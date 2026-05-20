#!/usr/bin/env python3
"""
dashboard_bko.py  —  Dashboard BKO Streamlit

Pipeline: Upload CSV -> Validar -> Processar -> Exibir relatorio

Uso: streamlit run dashboard_bko.py
     (ou via rodar_dashboard.bat)
"""

import os
import sys
import tempfile
import traceback
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st
from dotenv import load_dotenv
from fpdf import FPDF

load_dotenv()  # carrega .env em desenvolvimento local (no-op no Streamlit Cloud)

sys.path.insert(0, str(Path(__file__).parent))
import validador_tabela_ticket as vtk
import processador_relatorio_data as prd

# ---------------------------------------------------------------------------
# Configuracao da pagina
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="BKO | Dashboard de Tickets",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
[data-testid="stMetricValue"] { font-size: 1.8rem; font-weight: 700; }
[data-testid="stMetricLabel"] { font-size: 0.85rem; color: #666; }
.block-container { padding-top: 1.5rem; }
thead tr th { background-color: #1F3864 !important; color: white !important; }
</style>
""", unsafe_allow_html=True)

# Cores
COR_FECHADO   = "#2CA02C"
COR_PROCESSO  = "#FF7F0E"
COR_ABERTO    = "#1F77B4"
COR_ALERTA    = "#D62728"
COR_PRINCIPAL = "#1F3864"


# ---------------------------------------------------------------------------
# Pipeline com logs detalhados
# ---------------------------------------------------------------------------

def _log(msg: str):
    """Loga no terminal (visivel no Streamlit Cloud logs) com timestamp."""
    print(f"[BKO {datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def executar_pipeline(file_bytes: bytes, file_ext: str, status_ui) -> dict:
    """
    ETL completo em memoria com logs em cada etapa.
    status_ui: contexto st.status() para atualizacao visual em tempo real.
    """
    prd.AGORA = datetime.now()

    # ── Etapa 1: leitura e ETL ──────────────────────────────────────────
    status_ui.update(label="[1/6] Lendo arquivo e corrigindo encoding...")
    _log("INICIO pipeline")
    _log(f"Arquivo: {len(file_bytes):,} bytes | extensao: {file_ext}")

    suffix = f".{file_ext.lower()}"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = Path(tmp.name)

    try:
        df_raw, enc = vtk.read_any_file(tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)

    _log(f"Leitura OK | encoding={enc} | linhas={len(df_raw):,} | colunas={len(df_raw.columns)}")
    _log(f"Colunas encontradas: {list(df_raw.columns)}")

    # Valida colunas minimas obrigatorias
    COLUNAS_OBRIGATORIAS = {"ticket_id", "ticket_subject", "open_at", "status"}
    faltando = COLUNAS_OBRIGATORIAS - set(df_raw.columns)
    if faltando:
        msg = (
            f"Colunas nao encontradas: {faltando}\n\n"
            f"Colunas presentes no arquivo ({len(df_raw.columns)}): "
            f"{list(df_raw.columns)}\n\n"
            "Verifique se o arquivo usa separador ';' e foi gerado pelo sistema de tickets."
        )
        _log(f"ERRO validacao colunas: {msg}")
        raise ValueError(msg)

    # ── Etapa 2: limpeza ────────────────────────────────────────────────
    status_ui.update(label="[2/6] Limpando caracteres especiais...")
    _log("Iniciando limpeza de strings...")

    str_cols = df_raw.select_dtypes(include=["object", "str"]).columns
    df_raw[str_cols] = df_raw[str_cols].apply(lambda s: s.str.strip())

    if "ticket_subject" in df_raw.columns:
        df_raw["ticket_subject"] = vtk._fix_residual_chars(df_raw["ticket_subject"])

    df_raw, cols_criadas = vtk.split_datetime_columns(df_raw)
    avisos = vtk.validate(df_raw)
    _log(f"Limpeza OK | cols_criadas={cols_criadas} | avisos={len(avisos)}")

    # ── Etapa 3: parse de datetimes ─────────────────────────────────────
    status_ui.update(label="[3/6] Convertendo colunas de data e hora...")
    _log("Parse de datetimes...")

    df_raw = prd._parse_datetimes(df_raw)
    _log("Parse OK")

    # ── Etapa 4: consolidacao por ticket ────────────────────────────────
    status_ui.update(label="[4/6] Consolidando tickets (pode demorar)...")
    _log("Consolidando tickets por ticket_id...")

    df_geral = prd.consolidate(df_raw)
    _log(f"Consolidacao OK | tickets={len(df_geral):,} | status={df_geral['status'].value_counts().to_dict()}")

    # ── Etapa 5: analises ───────────────────────────────────────────────
    status_ui.update(label="[5/6] Calculando SLAs, funil e agrupamentos...")
    _log("Calculando analises...")

    df_sla_p   = prd.make_sla_em_processo(df_geral)
    _log(f"SLA em processo OK | {len(df_sla_p):,} tickets")

    df_sla_r   = prd.make_sla_resposta(df_geral)
    _log(f"SLA resposta OK | {len(df_sla_r):,} tickets")

    df_funil_m = prd.make_funil_mensal(df_geral)
    df_funil_s = prd.make_funil_semanal(df_geral)
    _log(f"Funil OK | {len(df_funil_m)} meses / {len(df_funil_s)} semanas")

    df_assunto = prd.make_por_assunto(df_geral)
    df_escrit  = prd.make_por_escritorio(df_geral)
    _log(f"Agrupamentos OK | {len(df_assunto)} assuntos / {len(df_escrit)} escritorios")

    # ── Etapa 6: gera XLSX ──────────────────────────────────────────────
    status_ui.update(label="[6/6] Gerando XLSX para download...")
    _log("Gerando XLSX...")

    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        xlsx_path = Path(tmp.name)

    prd.write_xlsx(str(xlsx_path), {
        "Aba_Geral":       df_geral,
        "SLA_Em_Processo": df_sla_p,
        "SLA_Resposta":    df_sla_r,
        "Funil_Mensal":    df_funil_m,
        "Funil_Semanal":   df_funil_s,
        "Por_Assunto":     df_assunto,
        "Por_Escritorio":  df_escrit,
    })
    xlsx_bytes = xlsx_path.read_bytes()
    xlsx_path.unlink(missing_ok=True)
    _log(f"XLSX OK | {len(xlsx_bytes):,} bytes")
    _log("PIPELINE CONCLUIDO")

    return {
        "enc":          enc,
        "avisos":       avisos,
        "cols_criadas": cols_criadas,
        "geral":        df_geral,
        "sla_p":        df_sla_p,
        "sla_r":        df_sla_r,
        "funil_m":      df_funil_m,
        "funil_s":      df_funil_s,
        "assunto":      df_assunto,
        "escrit":       df_escrit,
        "xlsx":         xlsx_bytes,
        "ref_time":     prd.AGORA.strftime("%d/%m/%Y %H:%M"),
    }


# ---------------------------------------------------------------------------
# Exportação: PDF e E-mail
# ---------------------------------------------------------------------------

def _trunc(s: str, n: int) -> str:
    """Trunca string para n chars (latin-1 safe) e adiciona '...' se necessario."""
    s = str(s).encode("latin-1", errors="replace").decode("latin-1")
    return s[: n - 3] + "..." if len(s) > n else s


class _RelatorioPDF(FPDF):
    """PDF com cabecalho azul e rodape com numero de pagina (a partir da pag. 2)."""

    def __init__(self, ref: str):
        super().__init__()
        self._ref = ref

    def header(self):
        if self.page_no() == 1:
            return
        self.set_fill_color(31, 56, 100)
        self.rect(0, 0, self.w, 12, "F")
        self.set_font("Helvetica", "B", 8)
        self.set_text_color(255, 255, 255)
        self.set_xy(15, 2)
        self.cell(130, 8, text="Relatorio BKO - Tickets", new_x="RIGHT", new_y="TOP")
        self.cell(0, 8, text=self._ref, align="R")
        self.set_xy(self.l_margin, self.t_margin)

    def footer(self):
        self.set_y(-12)
        self.set_draw_color(180, 180, 180)
        self.line(self.l_margin, self.get_y(),
                  self.w - self.r_margin, self.get_y())
        self.set_font("Helvetica", "I", 7)
        self.set_text_color(150, 150, 150)
        self.cell(0, 8, text=f"Pagina {self.page_no()}", align="C")


def _section_title(pdf, title: str) -> None:
    """Faixa colorida de titulo de secao."""
    pdf.set_fill_color(44, 62, 80)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 8, text=f"  {title}", fill=True,
             new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    pdf.set_text_color(0, 0, 0)


def _pdf_table_header(pdf, headers: list, widths: list,
                      aligns: list | None = None) -> None:
    if aligns is None:
        aligns = ["C"] * len(headers)
    pdf.set_fill_color(52, 73, 94)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 8)
    for h, w, a in zip(headers, widths, aligns):
        pdf.cell(w, 7, text=h, border=1, fill=True, align=a,
                 new_x="RIGHT", new_y="TOP")
    pdf.ln(7)


def _pdf_table_row(pdf, values: list, widths: list, idx: int,
                   aligns: list | None = None) -> None:
    if aligns is None:
        aligns = ["C"] * len(values)
    pdf.set_fill_color(245, 248, 252) if idx % 2 == 0 else pdf.set_fill_color(255, 255, 255)
    pdf.set_text_color(30, 30, 30)
    pdf.set_font("Helvetica", "", 8)
    for val, w, a in zip(values, widths, aligns):
        pdf.cell(w, 6, text=_trunc(str(val), 50), border="LRB",
                 fill=True, align=a, new_x="RIGHT", new_y="TOP")
    pdf.ln(6)


def _pdf_kpi_row(pdf, kpis: list) -> None:
    """
    Renderiza cards KPI com barra de acento colorida no topo.
    kpis = [(label, value, sub_text, color_hex), ...]
    """
    content_w = pdf.w - pdf.l_margin - pdf.r_margin
    gap  = 3
    cw   = content_w / len(kpis)
    card = cw - gap
    x0   = pdf.l_margin
    y0   = pdf.get_y()

    for i, (label, value, sub, color) in enumerate(kpis):
        r, g, b = int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)
        x = x0 + i * cw

        # Barra de acento (topo do card)
        pdf.set_fill_color(r, g, b)
        pdf.rect(x, y0, card, 3, "F")

        # Fundo do card
        pdf.set_fill_color(250, 251, 253)
        pdf.set_draw_color(210, 215, 220)
        pdf.rect(x, y0 + 3, card, 24, "FD")

        # Numero principal
        pdf.set_font("Helvetica", "B", 15)
        pdf.set_text_color(r, g, b)
        pdf.set_xy(x, y0 + 5)
        pdf.cell(card, 10, text=str(value), align="C",
                 new_x="RIGHT", new_y="TOP")

        # Texto secundario (ex: percentual)
        if sub:
            pdf.set_font("Helvetica", "", 7)
            pdf.set_text_color(110, 110, 110)
            pdf.set_xy(x, y0 + 15)
            pdf.cell(card, 5, text=str(sub), align="C",
                     new_x="RIGHT", new_y="TOP")

        # Label
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(70, 70, 70)
        pdf.set_xy(x, y0 + 21)
        pdf.cell(card, 6, text=label, align="C",
                 new_x="RIGHT", new_y="TOP")

    pdf.set_xy(x0, y0 + 30)
    pdf.set_text_color(0, 0, 0)
    pdf.set_draw_color(0, 0, 0)


def _pdf_add_charts(pdf, result: dict) -> None:
    """Adiciona graficos PNG ao PDF. Silencioso se kaleido nao estiver disponivel."""
    try:
        import plotly.io as pio

        df_m = result["funil_m"]
        fig1 = px.bar(
            df_m, y="periodo",
            x=["tickets_fechados", "em_processo"],
            barmode="group", orientation="h",
            title="Funil Mensal — Fechados e Em Processo",
            color_discrete_map={
                "tickets_fechados": "#2CA02C",
                "em_processo":      "#FF7F0E",
            },
        )
        fig1.update_layout(height=380, width=720, margin=dict(t=50, b=30),
                           legend_title_text="", plot_bgcolor="white",
                           paper_bgcolor="white")
        fig1.update_traces(textposition="outside", cliponaxis=False,
                           texttemplate="%{x}")

        df_e = result["escrit"].sort_values("total_tickets", ascending=True).tail(15)
        fig2 = px.bar(
            df_e, x="total_tickets", y="escritorio", orientation="h",
            title="Volume por Escritorio (Top 15)",
            color="pct_fechado",
            color_continuous_scale="RdYlGn", range_color=[0, 100],
            text="total_tickets",
        )
        fig2.update_layout(height=440, width=720, margin=dict(t=50, b=30),
                           plot_bgcolor="white", paper_bgcolor="white",
                           coloraxis_colorbar_title="% Fechado")
        fig2.update_traces(textposition="outside", cliponaxis=False)

        charts = [
            ("Funil Mensal de Tickets", fig1),
            ("Distribuicao por Escritorio", fig2),
        ]

        pdf.add_page()
        _section_title(pdf, "Graficos")

        for title_fig, fig in charts:
            img_bytes = pio.to_image(fig, format="png", scale=1.5)
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                tmp.write(img_bytes)
                tmp_path = tmp.name
            try:
                pdf.set_font("Helvetica", "B", 9)
                pdf.set_text_color(60, 60, 60)
                pdf.cell(0, 6, text=title_fig, new_x="LMARGIN", new_y="NEXT")
                pdf.image(tmp_path, w=180)
                pdf.ln(8)
            finally:
                Path(tmp_path).unlink(missing_ok=True)

    except Exception as exc:
        _log(f"Graficos no PDF ignorados: {exc}")


def _build_pdf(result: dict) -> bytes:
    """Gera PDF com capa, KPIs, resumo SLA, tabelas e graficos."""
    df_g   = result["geral"]
    ref    = result["ref_time"]
    total   = len(df_g)
    fech    = int((df_g["status"] == "closed").sum())
    proc    = int((df_g["status"] == "processing").sum())
    abertos = int((df_g["status"] == "open").sum())
    alert   = int((df_g["sla_alerta"] == "SIM").sum())

    sla_p_med  = df_g["tempo_processo_h"].mean()
    sla_p_max  = df_g["tempo_processo_h"].max()
    sla_r_med  = df_g["tempo_resposta_h"].mean()
    pct_alerta = alert / total * 100 if total else 0

    pdf = _RelatorioPDF(ref)
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.set_margins(15, 22, 15)

    # ─── Pagina 1: Capa + KPIs + Funil ────────────────────────────────────
    pdf.add_page()

    # Faixa de capa
    pdf.set_fill_color(31, 56, 100)
    pdf.rect(0, 0, pdf.w, 44, "F")
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 24)
    pdf.set_xy(15, 7)
    pdf.cell(0, 12, text="Relatorio BKO", new_x="LMARGIN", new_y="NEXT")
    pdf.set_xy(15, 20)
    pdf.set_font("Helvetica", "", 12)
    pdf.cell(0, 8, text="Dashboard de Tickets", new_x="LMARGIN", new_y="NEXT")
    pdf.set_xy(15, 31)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(160, 200, 255)
    pdf.cell(0, 8, text=f"Gerado em {ref}", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)

    pdf.set_xy(15, 52)

    # Cards KPI
    kpis = [
        ("Total de Tickets",  f"{total:,}",    "",                                    "1F3864"),
        ("Fechados",          f"{fech:,}",     f"{fech/total*100:.1f}% do total",     "2CA02C"),
        ("Em Processamento",  f"{proc:,}",     f"{proc/total*100:.1f}% do total",     "FF7F0E"),
        ("Abertos",           f"{abertos:,}",  f"{abertos/total*100:.1f}% do total",  "1F77B4"),
        ("SLA Alerta >24h",   f"{alert:,}",    f"{pct_alerta:.1f}% do total",         "D62728"),
    ]
    _pdf_kpi_row(pdf, kpis)
    pdf.ln(3)

    # Faixa de resumo SLA
    pdf.set_fill_color(240, 244, 248)
    pdf.set_draw_color(200, 210, 220)
    y_faixa = pdf.get_y()
    pdf.rect(pdf.l_margin, y_faixa,
             pdf.w - pdf.l_margin - pdf.r_margin, 10, "FD")
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(60, 60, 60)
    pdf.set_xy(pdf.l_margin + 4, y_faixa + 1)
    sla_items = [
        f"Proc. Medio: {sla_p_med:.1f}h"  if pd.notna(sla_p_med)  else "Proc. Medio: -",
        f"Proc. Maximo: {sla_p_max:.1f}h" if pd.notna(sla_p_max)  else "Proc. Max: -",
        f"Resp. Media: {sla_r_med:.1f}h"  if pd.notna(sla_r_med)  else "Resp. Media: -",
        f"Em Alerta: {pct_alerta:.1f}% dos tickets",
    ]
    pdf.cell(0, 8, text="   |   ".join(sla_items),
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_draw_color(0, 0, 0)
    pdf.ln(5)

    # Funil Mensal (sem coluna Abertos — so fechados e em_processo)
    _section_title(pdf, "Funil Mensal")
    heads_f = ["Periodo",  "Fechados", "Em Processo", "% Fechado", "SLA"]
    wids_f  = [45, 33, 38, 34, 30]
    alns_f  = ["C", "R",  "R",         "R",          "R"]
    cols_f  = ["periodo", "tickets_fechados", "em_processo",
               "pct_fechado", "tempo_medio_resposta_h"]
    _pdf_table_header(pdf, heads_f, wids_f, alns_f)
    for i, (_, row) in enumerate(result["funil_m"][cols_f].tail(12).iterrows()):
        resp = f"{row['tempo_medio_resposta_h']:.1f}" if pd.notna(row["tempo_medio_resposta_h"]) else "-"
        _pdf_table_row(pdf, [
            row["periodo"],
            f"{int(row['tickets_fechados']):,}",
            f"{int(row['em_processo']):,}",
            f"{row['pct_fechado']:.1f}%",
            resp,
        ], wids_f, i, alns_f)

    # ─── Pagina 2: Assuntos + Escritorios ─────────────────────────────────
    pdf.add_page()

    _section_title(pdf, "Top 10 Assuntos por Volume")
    heads_a = ["Assunto",  "Total", "Fechados", "Em Proc.", "% Fech.", "SLA"]
    wids_a  = [60, 20, 24, 26, 22, 28]
    alns_a  = ["L", "R",  "R",  "R",      "R",      "R"]
    _pdf_table_header(pdf, heads_a, wids_a, alns_a)
    for i, (_, row) in enumerate(result["assunto"].head(10).iterrows()):
        resp = f"{row['tempo_medio_resposta_h']:.1f}" if pd.notna(row.get("tempo_medio_resposta_h")) else "-"
        _pdf_table_row(pdf, [
            _trunc(row["assunto"], 34),
            f"{int(row['total']):,}",
            f"{int(row['fechados']):,}",
            f"{int(row['em_processo']):,}",
            f"{row['pct_fechado']:.1f}%",
            resp,
        ], wids_a, i, alns_a)

    pdf.ln(7)

    _section_title(pdf, "Top 10 Escritorios")
    heads_e = ["Escritorio", "Total", "Fechados", "Pendentes", "% Fech.", "SLA"]
    wids_e  = [54, 20, 24, 28, 22, 32]
    alns_e  = ["L", "R",  "R",  "R",       "R",      "R"]
    _pdf_table_header(pdf, heads_e, wids_e, alns_e)
    for i, (_, row) in enumerate(result["escrit"].head(10).iterrows()):
        resp = f"{row['tempo_medio_resposta_h']:.1f}" if pd.notna(row.get("tempo_medio_resposta_h")) else "-"
        _pdf_table_row(pdf, [
            _trunc(row["escritorio"], 30),
            f"{int(row['total_tickets']):,}",
            f"{int(row['fechados']):,}",
            f"{int(row['pendentes']):,}",
            f"{row['pct_fechado']:.1f}%",
            resp,
        ], wids_e, i, alns_e)

    # ─── Pagina 3: Graficos (se kaleido disponivel) ────────────────────────
    _pdf_add_charts(pdf, result)

    return bytes(pdf.output())


def _enviar_email(destinatario: str, xlsx_bytes: bytes, ref_time: str) -> None:
    """Envia XLSX por e-mail usando credenciais configuradas nos Streamlit Secrets."""
    import smtplib
    from email import encoders as enc_mod
    from email.mime.base import MIMEBase
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    # Prioridade: Streamlit Secrets (Cloud) → variáveis de ambiente do .env (local)
    cfg       = st.secrets.get("email", {})
    smtp_host = cfg.get("smtp_host") or os.getenv("EMAIL_SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(cfg.get("smtp_port") or os.getenv("EMAIL_SMTP_PORT", "587"))
    smtp_user = cfg.get("smtp_user") or os.getenv("EMAIL_SMTP_USER", "")
    smtp_pass = cfg.get("smtp_password") or os.getenv("EMAIL_SMTP_PASSWORD", "")
    smtp_from = cfg.get("smtp_from") or os.getenv("EMAIL_SMTP_FROM", smtp_user)

    if not smtp_user or not smtp_pass:
        raise ValueError(
            "Credenciais SMTP nao configuradas.\n"
            "Adicione no Streamlit Secrets (Settings > Secrets):\n\n"
            "[email]\n"
            "smtp_host     = \"smtp.gmail.com\"\n"
            "smtp_port     = 587\n"
            "smtp_user     = \"seu@email.com\"\n"
            "smtp_password = \"sua_senha_de_app\"\n"
            "smtp_from     = \"seu@email.com\""
        )

    msg            = MIMEMultipart()
    msg["From"]    = smtp_from
    msg["To"]      = destinatario
    msg["Subject"] = f"Relatorio BKO - Tickets ({ref_time})"

    body = (
        f"Ola,\n\n"
        f"Segue em anexo o relatorio de tickets BKO gerado em {ref_time}.\n\n"
        "O arquivo XLSX contem:\n"
        "  - Aba Geral: todos os tickets consolidados\n"
        "  - SLA Em Processo: tickets ativos por tempo em aberto\n"
        "  - SLA Resposta: tempo de resposta BO > BP\n"
        "  - Funil Mensal e Semanal\n"
        "  - Por Assunto e Por Escritorio\n\n"
        "Atenciosamente,\n"
        "Dashboard BKO"
    )
    msg.attach(MIMEText(body, "plain", "utf-8"))

    safe_ref = ref_time.replace("/", "").replace(":", "").replace(" ", "_")
    fname    = f"relatorio_bko_{safe_ref}.xlsx"
    part     = MIMEBase("application",
                        "vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    part.set_payload(xlsx_bytes)
    enc_mod.encode_base64(part)
    part.add_header("Content-Disposition", "attachment", filename=fname)
    msg.attach(part)

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.ehlo()
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.send_message(msg)

    _log(f"Email enviado para {destinatario}")


# ---------------------------------------------------------------------------
# Componentes visuais
# ---------------------------------------------------------------------------

def _fmt_h(h) -> str:
    """Formata horas como '2d 3h' ou '45min'."""
    if h is None or pd.isna(h):
        return "—"
    h = float(h)
    if h >= 24:
        d = int(h // 24)
        r = int(h % 24)
        return f"{d}d {r}h"
    if h >= 1:
        return f"{h:.1f}h"
    return f"{int(h * 60)}min"


def big_numbers(df: pd.DataFrame):
    total    = len(df)
    fechados = int((df["status"] == "closed").sum())
    em_proc  = int((df["status"] == "processing").sum())
    abertos  = int((df["status"] == "open").sum())
    alertas  = int((df["sla_alerta"] == "SIM").sum())

    def pct(n: int) -> str:
        return f"{n / total * 100:.1f}%" if total else "—"

    cards = [
        ("Total de Tickets", f"{total:,}",    "",            COR_PRINCIPAL),
        ("Fechados",         f"{fechados:,}", pct(fechados), COR_FECHADO),
        ("Em Processamento", f"{em_proc:,}",  pct(em_proc),  COR_PROCESSO),
        ("Abertos",          f"{abertos:,}",  pct(abertos),  COR_ABERTO),
        ("SLA Alerta >24h",  f"{alertas:,}",  pct(alertas),  COR_ALERTA),
    ]

    items = ""
    for label, value, delta, color in cards:
        delta_html = (
            f'<span class="bko-delta" style="color:{color};">&#x25B2; {delta}</span>'
            if delta else '<span class="bko-delta">&nbsp;</span>'
        )
        items += f"""
        <div class="bko-card" style="--c:{color}; border-color:{color}; box-shadow:0 5px 0 {color};">
            <p class="bko-label">{label}</p>
            <p class="bko-value" style="color:{color};">{value}</p>
            {delta_html}
        </div>"""

    st.markdown(f"""
    <style>
    .bko-grid {{
        display: grid;
        grid-template-columns: repeat(5, 1fr);
        gap: 18px;
        margin-bottom: 2rem;
    }}
    .bko-card {{
        border-radius: 0.75em;
        border: 2px solid;
        padding: 20px 22px 16px;
        background: #262730;
        cursor: default;
        transform: translateY(-3px);
        transition: transform 0.12s ease, box-shadow 0.12s ease;
    }}
    .bko-card:hover {{
        transform: translateY(-6px);
        box-shadow: 0 8px 0 var(--c) !important;
    }}
    .bko-card:active {{
        transform: translateY(2px);
        box-shadow: 0 1px 0 var(--c) !important;
    }}
    .bko-label {{
        font-size: 0.72rem;
        color: #aaaaaa;
        margin: 0 0 10px 0;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: .07em;
    }}
    .bko-value {{
        font-size: 2.2rem;
        font-weight: 800;
        margin: 0 0 8px 0;
        line-height: 1;
    }}
    .bko-delta {{
        font-size: 0.8rem;
        font-weight: 700;
        letter-spacing: .03em;
    }}
    </style>
    <div class="bko-grid">{items}</div>
    """, unsafe_allow_html=True)


# ---- Aba Geral ----

def tab_geral(df: pd.DataFrame):
    st.subheader(f"Todos os Tickets — {len(df):,} registros")

    col_cfg = {
        "ticket_id":           st.column_config.TextColumn("ID"),
        "ticket_subject":      st.column_config.TextColumn("Assunto", width="large"),
        "status":              st.column_config.TextColumn("Status"),
        "sla_alerta":          st.column_config.TextColumn("SLA Alerta"),
        "dt_abertura":         st.column_config.DatetimeColumn("Abertura",         format="DD/MM/YYYY HH:mm"),
        "dt_resposta":         st.column_config.DatetimeColumn("1a Resposta",      format="DD/MM/YYYY HH:mm"),
        "dt_ultima_atividade": st.column_config.DatetimeColumn("Ultima Atividade", format="DD/MM/YYYY HH:mm"),
        "tempo_processo_h":    st.column_config.NumberColumn("Processo (h)",  format="%.1f"),
        "tempo_resposta_h":    st.column_config.NumberColumn("Resposta (h)",  format="%.1f"),
        "n_mensagens":         st.column_config.NumberColumn("Mensagens"),
        "n_transferencias":    st.column_config.NumberColumn("Transferencias"),
        "analista":            st.column_config.TextColumn("Analista BO"),
        "consultor":           st.column_config.TextColumn("Consultor BP"),
        "escritorio":          st.column_config.TextColumn("Escritorio"),
        "mes_abertura":        st.column_config.TextColumn("Mes"),
        "semana_abertura":     st.column_config.TextColumn("Semana"),
    }
    st.dataframe(df, width='stretch', height=520, column_config=col_cfg)


# ---- SLA Em Processo ----

def tab_sla_processo(df: pd.DataFrame):
    st.subheader(f"Tickets Em Processo / Abertos — {len(df):,}")

    alertas  = int((df["sla_alerta"] == "SIM").sum())
    total    = len(df)
    max_h    = df["tempo_processo_h"].max()
    idx_max  = df["tempo_processo_h"].idxmax() if total else None
    id_antigo = str(df.loc[idx_max, "ticket_id"]) if idx_max is not None else "—"

    def pct(n: int) -> str:
        return f"{n / total * 100:.1f}% dos ativos" if total else "—"

    # faixas de tempo
    f_ok   = int((df["tempo_processo_h"] <= 10).sum())
    f_med  = int(((df["tempo_processo_h"] > 10) & (df["tempo_processo_h"] <= 24)).sum())
    f_crit = int((df["tempo_processo_h"] > 24).sum())

    cards = [
        ("Tickets Ativos",  f"{total:,}",   "",            COR_ALERTA),
        ("SLA Estourado",   f"{alertas:,}", pct(alertas),  COR_ALERTA),
        ("Mais Antigo",     _fmt_h(max_h),  f"#{id_antigo}", COR_ALERTA),
    ]

    faixas = [
        ("✅  Ate 10h",      f"{f_ok:,}",   pct(f_ok),   COR_FECHADO),
        ("⚠️  10h — 24h",   f"{f_med:,}",  pct(f_med),  COR_PROCESSO),
        ("🚨  Acima de 24h", f"{f_crit:,}", pct(f_crit), COR_ALERTA),
    ]

    def _card_html(label, value, sub, color, css_class):
        sub_html = (
            f'<span class="{css_class}-delta" style="color:{color};">{sub}</span>'
            if sub else f'<span class="{css_class}-delta">&nbsp;</span>'
        )
        return f"""
        <div class="{css_class}" style="--c:{color}; border-color:{color}; box-shadow:0 5px 0 {color};">
            <p class="{css_class}-label">{label}</p>
            <p class="{css_class}-value" style="color:{color};">{value}</p>
            {sub_html}
        </div>"""

    # stats para diagnóstico
    t_min = df["tempo_processo_h"].min()
    t_avg = df["tempo_processo_h"].mean()
    t_p50 = df["tempo_processo_h"].median()
    t_p90 = df["tempo_processo_h"].quantile(0.90)
    idx_min  = df["tempo_processo_h"].idxmin() if total else None
    id_novo  = str(df.loc[idx_min, "ticket_id"]) if idx_min is not None else "—"

    def _stat_color(h):
        if pd.isna(h):      return "#aaaaaa"
        if h <= 10:         return COR_FECHADO
        if h <= 24:         return COR_PROCESSO
        return COR_ALERTA

    stats = [
        ("🕐 Mais Recente",  _fmt_h(t_min), f"#{id_novo}", _stat_color(t_min)),
        ("📊 Media",         _fmt_h(t_avg), "",             _stat_color(t_avg)),
        ("📈 Mediana (P50)", _fmt_h(t_p50), "",             _stat_color(t_p50)),
        ("🔝 P90",           _fmt_h(t_p90), "",             _stat_color(t_p90)),
    ]

    stat_itens = "".join(f"""
        <div class="sla-stat">
            <span class="sla-stat-label">{lbl}</span>
            <span class="sla-stat-val" style="color:{cor};">{val}</span>
            <span class="sla-stat-sub">{sub}</span>
        </div>{"" if i == len(stats)-1 else '<div class="sla-stat-sep">|</div>'}
    """ for i, (lbl, val, sub, cor) in enumerate(stats))

    items  = "".join(_card_html(*c, "sla-card") for c in cards)
    fitens = "".join(_card_html(*f, "sla-card") for f in faixas)

    st.markdown(f"""
    <style>
    .sla-grid, .sla-fgrid {{
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        gap: 18px;
    }}
    .sla-grid  {{ margin-bottom: 0.6rem; }}
    .sla-fgrid {{ margin-bottom: 0.8rem; }}
    .sla-card {{
        border-radius: 0.75em;
        border: 2px solid;
        padding: 20px 22px 16px;
        background: #262730;
        cursor: default;
        transform: translateY(-3px);
        transition: transform 0.12s ease, box-shadow 0.12s ease;
    }}
    .sla-card:hover  {{ transform: translateY(-6px); box-shadow: 0 8px 0 var(--c) !important; }}
    .sla-card:active {{ transform: translateY(2px);  box-shadow: 0 1px 0 var(--c) !important; }}
    .sla-card-label {{
        font-size: 0.78rem; color: #aaaaaa; margin: 0 0 10px 0;
        font-weight: 700; text-transform: uppercase; letter-spacing: .06em;
    }}
    .sla-card-value {{ font-size: 2.2rem; font-weight: 800; margin: 0 0 8px 0; line-height: 1; }}
    .sla-card-delta {{ font-size: 0.8rem; font-weight: 700; letter-spacing: .03em; }}
    .sla-faixa-titulo {{
        font-size: 0.78rem; font-weight: 700; text-transform: uppercase;
        letter-spacing: .07em; color: #aaaaaa; margin: 1rem 0 0.6rem 0;
    }}
    /* stats bar */
    .sla-stats-bar {{
        display: flex;
        align-items: center;
        gap: 0;
        background: #1a1d24;
        border: 1px solid #3a3d4a;
        border-radius: 0.75em;
        padding: 14px 22px;
        margin-top: 0.8rem;
        flex-wrap: wrap;
    }}
    .sla-stat {{
        display: flex; flex-direction: column; align-items: center;
        flex: 1; min-width: 110px;
    }}
    .sla-stat-sep {{ color: #444; font-size: 1.4rem; padding: 0 4px; align-self: center; }}
    .sla-stat-label {{
        font-size: 0.68rem; color: #888; font-weight: 700;
        text-transform: uppercase; letter-spacing: .06em; margin-bottom: 4px;
    }}
    .sla-stat-val {{ font-size: 1.25rem; font-weight: 800; line-height: 1.1; }}
    .sla-stat-sub {{ font-size: 0.7rem; color: #888; margin-top: 2px; }}
    </style>
    <div class="sla-grid">{items}</div>
    <p class="sla-faixa-titulo">&#x23F1; Distribuicao por Tempo em Processo</p>
    <div class="sla-fgrid">{fitens}</div>
    <div class="sla-stats-bar">{stat_itens}</div>
    """, unsafe_allow_html=True)

    st.markdown("---")

    col_graf, col_escrit = st.columns(2)

    with col_graf:
        fig = px.histogram(
            df,
            x="tempo_processo_h",
            nbins=30,
            title="Distribuicao do Tempo em Processo (h)",
            labels={"tempo_processo_h": "Horas"},
            color_discrete_sequence=[COR_PROCESSO],
        )
        fig.add_vline(x=prd.SLA_ALERTA_H, line_dash="dash", line_color=COR_ALERTA,
                      annotation_text=f"{prd.SLA_ALERTA_H}h (alerta)", annotation_position="top right")
        fig.update_layout(margin=dict(t=40, b=20))
        st.plotly_chart(fig, width='stretch')

    with col_escrit:
        df_top = (
            df.groupby("escritorio", as_index=False)["ticket_id"]
            .count()
            .rename(columns={"ticket_id": "qtd"})
            .sort_values("qtd")
            .tail(20)
        )
        fig2 = px.bar(
            df_top, x="qtd", y="escritorio", orientation="h",
            title="Escritorios com Mais Tickets Ativos",
            labels={"qtd": "Qtd", "escritorio": ""},
            color_discrete_sequence=[COR_PRINCIPAL],
            text_auto=True,
        )
        fig2.update_traces(textposition="outside", cliponaxis=False)
        fig2.update_layout(margin=dict(t=40, b=20, r=60))
        st.plotly_chart(fig2, width='stretch')

    st.subheader("Tabela Detalhada")
    col_cfg = {
        "ticket_id":        st.column_config.TextColumn("ID"),
        "ticket_subject":   st.column_config.TextColumn("Assunto", width="large"),
        "status":           st.column_config.TextColumn("Status"),
        "sla_alerta":       st.column_config.TextColumn("SLA Alerta"),
        "dt_abertura":      st.column_config.DatetimeColumn("Abertura", format="DD/MM/YYYY HH:mm"),
        "tempo_processo_h": st.column_config.NumberColumn("Tempo (h)", format="%.1f"),
        "n_transferencias": st.column_config.NumberColumn("Transferencias"),
        "analista":         st.column_config.TextColumn("Analista BO"),
        "consultor":        st.column_config.TextColumn("Consultor BP"),
        "escritorio":       st.column_config.TextColumn("Escritorio"),
    }
    st.dataframe(df, width='stretch', height=420, column_config=col_cfg)


# ---- SLA Resposta ----

def tab_sla_resposta(df: pd.DataFrame):
    st.subheader(f"SLA Resposta BO -> BP — {len(df):,} tickets")

    desc = df["tempo_resposta_h"].describe(percentiles=[.50, .75, .90, .95])
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Media",   _fmt_h(desc["mean"]))
    c2.metric("Mediana", _fmt_h(desc["50%"]))
    c3.metric("P90",     _fmt_h(desc["90%"]))
    c4.metric("Maximo",  _fmt_h(desc["max"]))

    st.markdown("---")

    p95 = df["tempo_resposta_h"].quantile(0.95)
    col_hist, col_box = st.columns(2)

    with col_hist:
        fig_h = px.histogram(
            df[df["tempo_resposta_h"] <= p95],
            x="tempo_resposta_h",
            nbins=40,
            title="Distribuicao do Tempo de Resposta (ate P95)",
            labels={"tempo_resposta_h": "Horas"},
            color_discrete_sequence=[COR_PRINCIPAL],
        )
        fig_h.update_layout(margin=dict(t=40, b=20))
        st.plotly_chart(fig_h, width='stretch')

    with col_box:
        df_escrit_resp = df[df["escritorio"].ne("") & df["tempo_resposta_h"].notna()]
        top_escrit = (
            df_escrit_resp.groupby("escritorio")["tempo_resposta_h"]
            .median()
            .sort_values(ascending=False)
            .head(15)
            .index
        )
        fig_box = px.box(
            df_escrit_resp[df_escrit_resp["escritorio"].isin(top_escrit)],
            x="tempo_resposta_h",
            y="escritorio",
            orientation="h",
            title="Box-plot Resposta por Escritorio (Top 15 mediana)",
            labels={"tempo_resposta_h": "Horas", "escritorio": ""},
            color_discrete_sequence=[COR_PRINCIPAL],
        )
        fig_box.update_layout(margin=dict(t=40, b=20), height=420)
        st.plotly_chart(fig_box, width='stretch')

    st.subheader("Tabela Detalhada")
    col_cfg = {
        "ticket_id":        st.column_config.TextColumn("ID"),
        "ticket_subject":   st.column_config.TextColumn("Assunto", width="large"),
        "status":           st.column_config.TextColumn("Status"),
        "dt_abertura":      st.column_config.DatetimeColumn("Abertura",    format="DD/MM/YYYY HH:mm"),
        "dt_resposta":      st.column_config.DatetimeColumn("1a Resposta", format="DD/MM/YYYY HH:mm"),
        "tempo_resposta_h": st.column_config.NumberColumn("Resposta (h)", format="%.1f"),
        "analista":         st.column_config.TextColumn("Analista BO"),
        "consultor":        st.column_config.TextColumn("Consultor BP"),
        "escritorio":       st.column_config.TextColumn("Escritorio"),
    }
    st.dataframe(df, width='stretch', height=420, column_config=col_cfg)


# ---- Funil ----

def tab_funil(df_m: pd.DataFrame, df_s: pd.DataFrame):
    st.subheader("Funil Mensal")

    fig_m = px.bar(
        df_m,
        y="periodo",
        x=["tickets_fechados", "em_processo"],
        barmode="group",
        orientation="h",
        title="Tickets por Mes",
        labels={"periodo": "Mes", "value": "Tickets", "variable": ""},
        color_discrete_map={
            "tickets_fechados": COR_FECHADO,
            "em_processo":      COR_PROCESSO,
        },
        text_auto=True,
    )
    fig_m.update_traces(textposition="outside", cliponaxis=False)
    fig_m.update_layout(legend_title_text="", margin=dict(t=40, b=20, r=60))
    st.plotly_chart(fig_m, width='stretch')

    c1, c2 = st.columns(2)
    with c1:
        fig_rm = px.bar(
            df_m, x="periodo", y="tempo_medio_resposta_h",
            title="Tempo Medio de Resposta (h) por Mes",
            labels={"periodo": "Mes", "tempo_medio_resposta_h": "Horas"},
            color_discrete_sequence=[COR_PRINCIPAL],
            text_auto=".1f",
        )
        fig_rm.update_traces(textposition="outside", cliponaxis=False)
        fig_rm.update_layout(margin=dict(t=40, b=20))
        st.plotly_chart(fig_rm, width='stretch')

    with c2:
        fig_pm = px.bar(
            df_m, x="periodo", y="tempo_medio_processo_h",
            title="Tempo Medio em Processo (h) por Mes",
            labels={"periodo": "Mes", "tempo_medio_processo_h": "Horas"},
            color_discrete_sequence=[COR_PROCESSO],
            text_auto=".1f",
        )
        fig_pm.update_traces(textposition="outside", cliponaxis=False)
        fig_pm.update_layout(margin=dict(t=40, b=20))
        st.plotly_chart(fig_pm, width='stretch')

    st.subheader("Funil Semanal")
    fig_s = px.bar(
        df_s,
        y="periodo",
        x=["tickets_fechados", "em_processo"],
        barmode="group",
        orientation="h",
        title="Tickets por Semana",
        labels={"periodo": "Semana", "value": "Tickets", "variable": ""},
        color_discrete_map={
            "tickets_fechados": COR_FECHADO,
            "em_processo":      COR_PROCESSO,
        },
        text_auto=True,
    )
    fig_s.update_traces(textposition="outside", cliponaxis=False)
    fig_s.update_layout(legend_title_text="", margin=dict(t=40, b=20, r=60))
    st.plotly_chart(fig_s, width='stretch')

    st.subheader("Dados do Funil")
    c_esq, c_dir = st.columns(2)
    with c_esq:
        st.caption("Mensal")
        st.dataframe(df_m, width='stretch')
    with c_dir:
        st.caption("Semanal")
        st.dataframe(df_s, width='stretch')


# ---- Por Assunto ----

def tab_por_assunto(df: pd.DataFrame):
    st.subheader(f"Distribuicao por Assunto — {len(df)} assuntos")

    fig = px.bar(
        df.sort_values("total", ascending=True).tail(25),
        x="total",
        y="assunto",
        orientation="h",
        title="Top 25 Assuntos por Volume",
        labels={"total": "Qtd Tickets", "assunto": ""},
        color="pct_fechado",
        color_continuous_scale="RdYlGn",
        range_color=[0, 100],
        text="total",
    )
    fig.update_traces(textposition="outside", cliponaxis=False)
    fig.update_layout(height=600, coloraxis_colorbar_title="% Fechado",
                      margin=dict(t=40, b=20, r=60))
    st.plotly_chart(fig, width='stretch')

    st.subheader("Tabela Completa")
    col_cfg = {
        "assunto":                  st.column_config.TextColumn("Assunto", width="large"),
        "total":                    st.column_config.NumberColumn("Total"),
        "fechados":                 st.column_config.NumberColumn("Fechados"),
        "em_processo":              st.column_config.NumberColumn("Em Processo"),
        "com_sla_alerta":           st.column_config.NumberColumn("SLA Alerta"),
        "pct_fechado":              st.column_config.NumberColumn("% Fechado",  format="%.1f%%"),
        "tempo_medio_resposta_h":   st.column_config.NumberColumn("Resp. Media (h)", format="%.1f"),
        "tempo_medio_processo_h":   st.column_config.NumberColumn("Proc. Medio (h)", format="%.1f"),
        "n_transferencias_medio":   st.column_config.NumberColumn("Transfer. Medio", format="%.1f"),
    }
    st.dataframe(df, width='stretch', column_config=col_cfg)


# ---- Por Escritorio ----

def tab_por_escritorio(df: pd.DataFrame):
    st.subheader(f"Distribuicao por Escritorio — {len(df)} escritorios")

    c_vol, c_resp = st.columns(2)

    with c_vol:
        fig_v = px.bar(
            df.sort_values("total_tickets", ascending=True).tail(20),
            x="total_tickets",
            y="escritorio",
            orientation="h",
            title="Volume de Tickets por Escritorio",
            labels={"total_tickets": "Total", "escritorio": ""},
            color="pct_fechado",
            color_continuous_scale="RdYlGn",
            range_color=[0, 100],
            text="total_tickets",
        )
        fig_v.update_traces(textposition="outside", cliponaxis=False)
        fig_v.update_layout(coloraxis_colorbar_title="% Fechado",
                            margin=dict(t=40, b=20, r=60))
        st.plotly_chart(fig_v, width='stretch')

    with c_resp:
        fig_r = px.bar(
            df.sort_values("tempo_medio_resposta_h", ascending=True).tail(20),
            x="tempo_medio_resposta_h",
            y="escritorio",
            orientation="h",
            title="Tempo Medio de Resposta (h) por Escritorio",
            labels={"tempo_medio_resposta_h": "Horas", "escritorio": ""},
            color_discrete_sequence=[COR_PROCESSO],
            text_auto=".1f",
        )
        fig_r.update_traces(textposition="outside", cliponaxis=False)
        fig_r.update_layout(margin=dict(t=40, b=20, r=60))
        st.plotly_chart(fig_r, width='stretch')

    # Pendentes vs Fechados lado a lado
    df_status = df[["escritorio", "fechados", "pendentes"]].sort_values(
        "fechados", ascending=True
    ).tail(20)
    fig_comp = px.bar(
        df_status,
        x=["fechados", "pendentes"],
        y="escritorio",
        orientation="h",
        barmode="group",
        title="Fechados vs Pendentes por Escritorio (Top 20 fechados)",
        labels={"value": "Qtd", "escritorio": "", "variable": ""},
        color_discrete_map={"fechados": COR_FECHADO, "pendentes": COR_ALERTA},
        text_auto=True,
    )
    fig_comp.update_traces(textposition="outside", cliponaxis=False)
    fig_comp.update_layout(legend_title_text="", margin=dict(t=40, b=20, r=60))
    st.plotly_chart(fig_comp, width='stretch')

    st.subheader("Tabela Detalhada por Escritorio")
    col_cfg = {
        "escritorio":              st.column_config.TextColumn("Escritorio", width="medium"),
        "total_tickets":           st.column_config.NumberColumn("Total"),
        "fechados":                st.column_config.NumberColumn("Fechados"),
        "pct_fechado":             st.column_config.NumberColumn("% Fechado",  format="%.1f%%"),
        "pendentes":               st.column_config.NumberColumn("Pendentes"),
        "pct_pendente":            st.column_config.NumberColumn("% Pendente", format="%.1f%%"),
        "com_sla_alerta":          st.column_config.NumberColumn("SLA Alerta"),
        "tempo_medio_resposta_h":  st.column_config.NumberColumn("Resp. Media (h)",  format="%.1f"),
        "tempo_min_resposta_h":    st.column_config.NumberColumn("Resp. Min (h)",    format="%.1f"),
        "tempo_max_resposta_h":    st.column_config.NumberColumn("Resp. Max (h)",    format="%.1f"),
        "tempo_medio_processo_h":  st.column_config.NumberColumn("Proc. Medio (h)", format="%.1f"),
    }
    st.dataframe(df, width='stretch', column_config=col_cfg)


# ---------------------------------------------------------------------------
# Tela de boas-vindas
# ---------------------------------------------------------------------------

def tela_inicial():
    st.title("📊 Dashboard BKO — Tickets")
    st.markdown("---")

    c1, c2, c3 = st.columns(3)
    c1.info("**1. Carregar**\nEnvie o CSV exportado do sistema de tickets pela barra lateral.")
    c2.info("**2. Processar**\nO sistema executa automaticamente: Validar → ETL → Analisar.")
    c3.info("**3. Explorar**\nNavegue pelas abas e baixe o XLSX completo.")

    st.markdown("---")
    st.markdown("""
    #### Abas do Relatorio
    | Aba | Conteudo |
    |-----|----------|
    | 📋 Aba Geral | Um registro por ticket com todos os indicadores |
    | ⏱ SLA Em Processo | Tickets ativos ordenados por tempo em aberto |
    | 📨 SLA Resposta | Tempo de resposta BO → BP por ticket |
    | 📊 Funil | Mensal e semanal: abertos / em processo / fechados |
    | 🏷 Por Assunto | Volume e SLA por categoria do ticket |
    | 🏢 Por Escritorio | Fechados, pendentes e tempo de resposta por escritorio |

    #### Formato do arquivo CSV
    Separador `;` — encoding Windows-1252 ou UTF-8.
    Colunas esperadas: `ticket_id`, `ticket_subject`, `open_at`, `answered_at`,
    `status`, `sender`, `consultant`, `office` ...
    """)


# ---------------------------------------------------------------------------
# App principal
# ---------------------------------------------------------------------------

def main():
    # ── Sidebar — bloco único (evita double-context no Streamlit 1.57+) ─────
    with st.sidebar:
        st.markdown("## 📊 BKO Dashboard")
        st.markdown("---")

        uploaded = st.file_uploader(
            "Carregar arquivo",
            type=["csv", "xlsx", "xls"],
            help="CSV (separador ;) ou XLSX exportado do sistema de tickets",
        )

        if uploaded:
            st.success(f"Arquivo: **{uploaded.name}**")

        st.markdown("---")
        st.caption(f"SLA alerta configurado: > {prd.SLA_ALERTA_H}h")

        # Exportar — lê do session_state (disponível após pipeline concluir)
        if uploaded:
            _ck  = f"result_{uploaded.file_id}"
            _pk  = f"pdf_{uploaded.file_id}"
            _res = st.session_state.get(_ck)
            _pdf = st.session_state.get(_pk, b"")

            if _res is not None:
                st.markdown("---")
                st.markdown("#### 📥 Exportar")

                st.download_button(
                    label="📊 Baixar XLSX completo",
                    data=_res["xlsx"],
                    file_name=f"relatorio_bko_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    width='stretch',
                )

                if _pdf:
                    st.download_button(
                        label="📄 Baixar PDF",
                        data=_pdf,
                        file_name=f"relatorio_bko_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf",
                        mime="application/pdf",
                        width='stretch',
                    )
                else:
                    st.caption("PDF indisponivel — verifique os logs.")

                st.markdown("---")
                st.markdown("#### 📧 Enviar por E-mail")
                dest = st.text_input(
                    "Destinatario",
                    placeholder="email@exemplo.com",
                    key="email_dest",
                )
                if st.button("Enviar Relatorio", width='stretch', key="btn_email"):
                    if not dest or "@" not in dest:
                        st.warning("Informe um e-mail valido.")
                    else:
                        _ph = st.empty()
                        _ph.info(f"Enviando para {dest}...")
                        try:
                            _enviar_email(dest, _res["xlsx"], _res["ref_time"])
                            _ph.success(f"E-mail enviado para **{dest}**!")
                        except Exception as e_mail:
                            _ph.error(str(e_mail))

    # ── Sem arquivo — tela inicial ───────────────────────────────────────────
    if uploaded is None:
        tela_inicial()
        return

    # ── Com arquivo — pipeline ───────────────────────────────────────────────
    file_ext  = uploaded.name.rsplit(".", 1)[-1]
    cache_key = f"result_{uploaded.file_id}"
    if cache_key not in st.session_state:
        with st.status("Processando arquivo...", expanded=True) as status_ui:
            try:
                result = executar_pipeline(uploaded.getvalue(), file_ext, status_ui)
                status_ui.update(label="Processamento concluido!", state="complete", expanded=False)
                st.session_state[cache_key] = result
            except Exception as e:
                status_ui.update(label=f"Erro: {e}", state="error", expanded=True)
                _log(f"ERRO: {e}")
                _log(traceback.format_exc())
                st.error(f"Erro no processamento: {e}")
                st.exception(e)
                return

    result = st.session_state[cache_key]

    # Cabecalho
    st.title("📊 Dashboard BKO — Tickets")
    st.caption(
        f"Arquivo: **{uploaded.name}** | "
        f"Referencia: **{result['ref_time']}** | "
        f"Encoding: `{result['enc']}`"
    )

    # Avisos de qualidade
    if result["avisos"]:
        with st.expander(f"⚠️ {len(result['avisos'])} aviso(s) de qualidade dos dados", expanded=False):
            for av in result["avisos"]:
                st.warning(av)

    # PDF gerado fora do sidebar (evita conflito de contexto com st.spinner)
    pdf_key = f"pdf_{uploaded.file_id}"
    if pdf_key not in st.session_state:
        with st.spinner("Gerando PDF..."):
            try:
                st.session_state[pdf_key] = _build_pdf(result)
                _log("PDF gerado com sucesso")
            except Exception as e_pdf:
                _log(f"Erro ao gerar PDF: {e_pdf}")
                st.session_state[pdf_key] = b""
        st.rerun()  # atualiza sidebar para exibir botao PDF

    # Big numbers
    st.markdown("---")
    big_numbers(result["geral"])
    st.markdown("---")

    # Abas
    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
        "📋 Aba Geral",
        "⏱ SLA Em Processo",
        "📨 SLA Resposta",
        "📊 Funil",
        "🏷 Por Assunto",
        "🏢 Por Escritorio",
    ])

    with tab1:
        tab_geral(result["geral"])

    with tab2:
        tab_sla_processo(result["sla_p"])

    with tab3:
        tab_sla_resposta(result["sla_r"])

    with tab4:
        tab_funil(result["funil_m"], result["funil_s"])

    with tab5:
        tab_por_assunto(result["assunto"])

    with tab6:
        tab_por_escritorio(result["escrit"])


if __name__ == "__main__":
    main()
