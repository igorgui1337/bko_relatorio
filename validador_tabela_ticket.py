#!/usr/bin/env python3
"""
validador_tabela_ticket.py
ETL pipeline para arquivos CSV de tickets BKO.

Transformações realizadas:
  1. Detecta e corrige encoding (Windows-1252 / Latin-1 → UTF-8)
     Corrige caracteres como Ç, Ã, Õ, Ê, É em todas as colunas de texto
  2. Separa colunas de data+hora (C, D, E, F) criando novas colunas de hora:
       open_at        → open_at  +  hora_open
       answered_at    → answered_at  +  answered_hora
       message_at     → message_at   +  message_hora
       previous_message_at → previous_message_at + previous_hora
  3. Valida integridade do arquivo e gera relatório

Uso:
    python validador_tabela_ticket.py <arquivo.csv>
    python validador_tabela_ticket.py <arquivo.csv> <saida.csv>
"""

import sys
import pandas as pd
from pathlib import Path


# Mapeamento: coluna de data bruta → nome da nova coluna de hora
DATETIME_COLS = [
    ("open_at",             "hora_open"),
    ("answered_at",         "answered_hora"),
    ("message_at",          "message_hora"),
    ("previous_message_at", "previous_hora"),
]

# Encodings a tentar na ordem (Latin-1 cobre a maioria dos sistemas BR)
ENCODING_CANDIDATES = ["latin-1", "utf-8", "cp1252", "utf-8-sig"]

# Colunas obrigatórias mínimas para o arquivo ser considerado válido
REQUIRED_COLS = {"ticket_id", "ticket_subject", "open_at", "status"}


# ---------------------------------------------------------------------------
# Leitura com detecção de encoding
# ---------------------------------------------------------------------------

def _try_read(path: Path, encoding: str) -> pd.DataFrame | None:
    try:
        df = pd.read_csv(
            path,
            sep=";",
            encoding=encoding,
            dtype=str,
            keep_default_na=False,
        )
        # Aceita apenas se não houver coluna de substituição (sinal de encoding errado)
        garbled = sum(1 for c in df.columns if "�" in c or "?" in c)
        if garbled == 0:
            return df
    except (UnicodeDecodeError, Exception):
        pass
    return None


def read_csv_auto(path: Path) -> tuple[pd.DataFrame, str]:
    """Tenta ler o CSV com cada encoding candidato. Retorna (DataFrame, encoding_usado)."""
    for enc in ENCODING_CANDIDATES:
        df = _try_read(path, enc)
        if df is not None:
            return df, enc

    # Fallback: lê ignorando erros de decodificação
    df = pd.read_csv(path, sep=";", encoding="latin-1", dtype=str,
                     keep_default_na=False, on_bad_lines="skip")
    return df, "latin-1 (fallback)"


# ---------------------------------------------------------------------------
# Correção de encoding residual na coluna B (ticket_subject)
# ---------------------------------------------------------------------------

def _fix_residual_chars(series: pd.Series) -> pd.Series:
    """
    Correção de caracteres residuais para casos onde o arquivo sofreu
    double-encoding ou tem bytes mistos. Não necessária se o encoding
    foi detectado corretamente, mas serve como camada extra de segurança.
    """
    replacements = {
        "Ã§": "ç",  "Ã£": "ã",  "Ãµ": "õ",  "Ãª": "ê",  "Ã©": "é",
        "Ã\xa7": "ç", "Ã\xa3": "ã", "Ã\xb5": "õ",
        "Ã\xa9": "é", "Ã\xaa": "ê", "Ã\xa1": "á", "Ã\xad": "í",
        "Ã\xb3": "ó", "Ã\xba": "ú", "Ã\x87": "Ç", "Ã\x83": "Ã",
        "Ã\x95": "Õ",  "Ã\x8a": "Ê", "Ã\x89": "É",
        "â\x80\x99": "'",  "â\x80\x9c": '"',  "â\x80\x9d": '"',
    }
    for wrong, right in replacements.items():
        series = series.str.replace(wrong, right, regex=False)
    return series


# ---------------------------------------------------------------------------
# Divisão de colunas datetime
# ---------------------------------------------------------------------------

def split_datetime_columns(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """
    Para cada par (data, hora):
    - Se a coluna hora já existe → pula (arquivo já processado)
    - Se a coluna data contém data+hora combinadas → divide e insere coluna hora
    Retorna (df_modificado, lista_de_colunas_criadas)
    """
    created = []
    for date_col, hora_col in DATETIME_COLS:
        if date_col not in df.columns:
            continue
        if hora_col in df.columns:
            continue  # já separado (arquivo de exemplo final)

        # Testa se o padrão combinado existe: "DD/MM/YYYY HH:MM"
        sample = df[date_col].dropna().head(20)
        has_time = sample.str.contains(r"\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}", regex=True, na=False).any()
        if not has_time:
            continue  # coluna já contém só data ou está vazia

        parts = df[date_col].str.strip().str.split(r"\s+", n=1, expand=True)
        df[date_col] = parts[0].str.strip()
        time_vals = parts[1].str.strip() if 1 in parts.columns else pd.Series([""] * len(df), index=df.index)

        pos = df.columns.get_loc(date_col) + 1
        df.insert(pos, hora_col, time_vals.fillna(""))
        created.append(hora_col)

    return df, created


# ---------------------------------------------------------------------------
# Validação do arquivo transformado
# ---------------------------------------------------------------------------

def validate(df: pd.DataFrame) -> list[str]:
    """Retorna lista de avisos de qualidade. Lista vazia = tudo OK."""
    warnings = []

    missing_required = REQUIRED_COLS - set(df.columns)
    if missing_required:
        warnings.append(f"Colunas obrigatórias ausentes: {missing_required}")

    if "ticket_id" in df.columns:
        nulls = df["ticket_id"].eq("").sum()
        if nulls:
            warnings.append(f"ticket_id vazio em {nulls} linhas")

    if "open_at" in df.columns:
        date_pattern = r"^\d{2}/\d{2}/\d{4}$"
        invalids = df["open_at"].str.strip().str.match(date_pattern, na=False)
        bad = (~invalids & df["open_at"].ne("")).sum()
        if bad:
            warnings.append(f"open_at com formato de data inválido em {bad} linhas")

    if "status" in df.columns:
        known = {"open", "closed", "pending", "processing"}
        unexpected = set(df["status"].str.lower().dropna().unique()) - known - {""}
        if unexpected:
            warnings.append(f"Valores inesperados em 'status': {unexpected}")

    # Linhas com dados em colunas Unnamed → desalinhamento de colunas
    unnamed_with_data = [c for c in df.columns if str(c).startswith("Unnamed") and df[c].ne("").any()]
    for col in unnamed_with_data:
        n = df[col].ne("").sum()
        warnings.append(f"Coluna extra '{col}' tem {n} linhas com dados (possível desalinhamento)")

    # Checa encoding residual na coluna ticket_subject
    # Cobre: ASCII + Latin-1 Supplement (ª, º, ê, etc.) + Latin Extended-A/B
    if "ticket_subject" in df.columns:
        valid_pattern = r"[^\x00-\x7F\x80-ɏ\s]"
        garbled = df["ticket_subject"].str.contains(valid_pattern, regex=True, na=False)
        if garbled.any():
            warnings.append(
                f"ticket_subject pode ter caracteres residuais em {garbled.sum()} linhas"
            )

    return warnings


# ---------------------------------------------------------------------------
# Pipeline principal
# ---------------------------------------------------------------------------

def process_file(input_path: str, output_path: str | None = None) -> str:
    """
    Executa o ETL completo. Retorna o caminho do arquivo de saída.
    """
    input_path = Path(input_path)
    if not input_path.exists():
        raise FileNotFoundError(f"Arquivo não encontrado: {input_path}")

    print(f"\n{'='*60}")
    print(f"  VALIDADOR / ETL DE TICKETS BKO")
    print(f"{'='*60}")
    print(f"  Arquivo:  {input_path.name}")

    # 1. Leitura com detecção de encoding
    df, enc = read_csv_auto(input_path)
    print(f"  Encoding: {enc}")
    print(f"  Linhas:   {len(df):,}  |  Colunas: {len(df.columns)}")

    # 2. Correção de caracteres residuais na coluna ticket_subject (coluna B)
    if "ticket_subject" in df.columns:
        df["ticket_subject"] = _fix_residual_chars(df["ticket_subject"])

    # 3. Colunas sem nome (Unnamed) surgem de `;` extras no final do header
    #    Se todas as células estiverem vazias → pode remover
    #    Se houver dados → são linhas desalinhadas (reporta, mas mantém)
    unnamed_cols = [c for c in df.columns if str(c).startswith("Unnamed")]
    truly_empty = [c for c in unnamed_cols if df[c].eq("").all()]
    if truly_empty:
        df.drop(columns=truly_empty, inplace=True)
        print(f"  Colunas vazias removidas: {truly_empty}")

    # 4. Divisão das colunas de data/hora (C, D, E, F)
    df, created_cols = split_datetime_columns(df)
    if created_cols:
        print(f"  Colunas criadas: {created_cols}")
    else:
        print("  Colunas de hora: já separadas (nenhuma alteração necessária)")

    # 5. Validação
    warnings = validate(df)
    if warnings:
        print("\n  [!] AVISOS DE QUALIDADE:")
        for w in warnings:
            print(f"      - {w}")
    else:
        print("  Validação: OK (sem problemas detectados)")

    # 6. Saída
    if output_path is None:
        output_path = input_path.parent / (input_path.stem + "_ETL.csv")
    output_path = Path(output_path)

    # utf-8-sig adiciona BOM → compatível com Excel no Windows
    df.to_csv(output_path, sep=";", index=False, encoding="utf-8-sig")

    print(f"\n  Arquivo salvo: {output_path.name}")
    print(f"  Colunas finais ({len(df.columns)}):")
    for i, col in enumerate(df.columns, 1):
        print(f"    {i:>2}. {col}")
    print(f"{'='*60}\n")

    return str(output_path)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        print("Uso: python validador_tabela_ticket.py <arquivo.csv> [saida.csv]")
        sys.exit(1)

    inp = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else None

    try:
        process_file(inp, out)
    except FileNotFoundError as e:
        print(f"ERRO: {e}")
        sys.exit(1)
