# BKO Tickets — Documentação Completa do Projeto

> **Repositório:** `igorgui1337/bko_relatorio` · Branch: `master`
> **Stack:** Python 3.10 · Streamlit 1.57 · Plotly · pandas · openpyxl · WeasyPrint

---

## Visão Geral

Sistema de análise de tickets de suporte BKO. Recebe um CSV exportado do sistema de tickets, executa ETL, gera indicadores e exibe um dashboard interativo com export HTML/PDF e envio por e-mail.

---

## Pipeline Completo

```
CSV Upload (Streamlit)
        │
        ▼
[1] validador_tabela_ticket.py
        │  - detecta encoding (latin-1, utf-8, cp1252, utf-8-sig)
        │  - separa colunas data+hora em duas colunas
        │  - valida colunas obrigatórias
        │  - corrige double-encoding (ex: Ã§ → ç)
        │  → DataFrame limpo + lista de avisos
        │
        ▼
[2] processador_relatorio_data.py
        │  - agrupa N linhas por ticket → 1 registro consolidado
        │  - calcula SLA (tempo_processo_h, tempo_resposta_h)
        │  - detecta alertas SLA > 24h
        │  - conta transferências de responsável
        │  - cruza com Departamentos.xlsx (LEFT JOIN por ticket_subject)
        │  → dict com 8 DataFrames + bytes do XLSX
        │
        ▼
[3] dashboard_bko.py
           - 7 abas de análise
           - export HTML interativo (Plotly JS)
           - export PDF (WeasyPrint / xhtml2pdf)
           - envio e-mail com XLSX + HTML em anexo
```

---

## Estrutura de Arquivos

```
bko_tickets/
├── dashboard_bko.py              # App principal Streamlit
├── processador_relatorio_data.py # ETL + geração de análises
├── validador_tabela_ticket.py    # Leitura e limpeza do CSV
├── Departamentos.xlsx            # DB de mapeamento assunto → departamento
├── requirements.txt              # Dependências Python
├── packages.txt                  # Dependências de sistema (Streamlit Cloud)
├── rodar_dashboard.bat           # Atalho para rodar localmente (Windows)
├── .streamlit/
│   └── secrets.toml              # Credenciais SMTP (nunca versionar)
└── .gitignore
```

---

## Arquivo 1 — `validador_tabela_ticket.py`

### Constantes

```python
DATETIME_COLS = [
    ("open_at",             "hora_open"),
    ("answered_at",         "answered_hora"),
    ("message_at",          "message_hora"),
    ("previous_message_at", "previous_hora"),
]
ENCODING_CANDIDATES = ["latin-1", "utf-8", "cp1252", "utf-8-sig"]
REQUIRED_COLS = {"ticket_id", "ticket_subject", "open_at", "status"}
```

### Funções

| Função | Assinatura | O que faz |
|--------|-----------|-----------|
| `read_csv_auto` | `(path) → (DataFrame, str)` | Tenta ler CSV com 4 encodings. Rejeita se colunas tiverem caracteres quebrados |
| `read_xlsx_file` | `(path, sheet=0) → (DataFrame, str)` | Lê primeira aba do XLSX, normaliza nomes de colunas |
| `read_any_file` | `(path) → (DataFrame, str)` | Roteador: detecta CSV ou XLSX pelo sufixo |
| `_fix_residual_chars` | `(series) → series` | Corrige double-encoding residual |
| `_detect_datetime_format` | `(series) → 'br'/'iso'/None` | Detecta formato BR ou ISO na coluna |
| `split_datetime_columns` | `(df) → (df, list[str])` | Separa colunas data+hora em duas colunas separadas |
| `validate` | `(df) → list[str]` | Retorna lista de avisos de qualidade (vazia = OK) |
| `process_file` | `(input, output=None) → str` | Pipeline completo: lê → corrige → divide → valida → salva CSV ETL |

### Colunas divididas pelo ETL

```
open_at              →  open_at       +  hora_open
answered_at          →  answered_at   +  answered_hora
message_at           →  message_at    +  message_hora
previous_message_at  →  previous_message_at  +  previous_hora
```

---

## Arquivo 2 — `processador_relatorio_data.py`

### Constantes

```python
SLA_ALERTA_H = 24          # horas — acima disso gera alerta
AGORA = datetime.now()     # referência para tickets ainda abertos
HEADER_COLOR  = "1F3864"   # azul escuro (cabeçalho XLSX)
OPEN_COLOR    = "FFF2CC"   # amarelo claro
PROC_COLOR    = "DEEBF7"   # azul claro
CLOSED_COLOR  = "E2EFDA"   # verde claro
ALERT_COLOR   = "FCE4D6"   # laranja
```

### Funções de carregamento e consolidação

| Função | O que faz |
|--------|-----------|
| `load_csv(path)` | Lê CSV ETL, cria colunas `dt_abertura`, `dt_resposta`, `dt_mensagem`, `dt_anterior` |
| `_parse_datetimes(df)` | Combina pares (data_col + hora_col) em colunas datetime |
| `_resolve_status(series)` | Prioridade: `closed > processing > open` |
| `_count_transfers(group)` | Conta linhas onde `previous_sender_id ≠ "" AND ≠ sender_id` |
| `consolidate(df)` | **Função central** — agrega N linhas por `ticket_id` em 1 registro |

### Lógica de SLA

```python
# Tempo em processo
if status in ("open", "processing"):
    tempo_processo_h = (AGORA - dt_abertura).total_seconds() / 3600
else:
    tempo_processo_h = (dt_ultima_msg - dt_abertura).total_seconds() / 3600

# Tempo de resposta BO → BP
tempo_resposta_h = (dt_resposta - dt_abertura).total_seconds() / 3600

# Alerta
sla_alerta = "SIM" if tempo_processo_h > 24 and status != "closed" else ""
```

### Campos do registro consolidado (`df_geral`)

```
ticket_id             string
ticket_subject        string
status                open | processing | closed
dt_abertura           datetime
dt_resposta           datetime | NaT
dt_ultima_atividade   datetime | NaT
tempo_processo_h      float (horas)
tempo_resposta_h      float (horas) | None
sla_alerta            "SIM" | ""
n_mensagens           int
n_transferencias      int
analista              string (último responsável BO)
analista_id           string
consultor             string (último responsável BP)
consultor_id          string
escritorio            string
mes_abertura          "YYYY-MM"
semana_abertura       "YYYY-SNN"
departamento          string (após merge com Departamentos.xlsx)
```

### Funções analíticas

| Função | Agrupamento | Colunas principais da saída |
|--------|-------------|----------------------------|
| `make_sla_em_processo(df)` | Por ticket (filtro: open/processing) | ticket_id, tempo_processo_h, sla_alerta, n_transferencias |
| `make_sla_resposta(df)` | Por ticket (filtro: tem resposta) | ticket_id, dt_resposta, tempo_resposta_h |
| `make_funil_mensal(df)` | mes_abertura | tickets_abertos, tickets_fechados, em_processo, pct_fechado |
| `make_funil_semanal(df)` | semana_abertura | idem funil mensal |
| `make_por_assunto(df)` | ticket_subject | total, fechados, em_processo, pct_fechado, tempo_medio_resposta_h |
| `make_por_escritorio(df)` | escritorio | total_tickets, fechados, pendentes, pct_fechado, pct_pendente |
| `make_por_departamento(df)` | departamento | total_tickets, fechados, em_processo, pct_fechado, n_transferencias_total |
| `load_departamentos(path)` | — | Lê Departamentos.xlsx → mapeamento ticket_subject → departamento |

### Funções de escrita XLSX

| Função | O que faz |
|--------|-----------|
| `write_xlsx(path, sheets)` | Escreve dict de DataFrames em abas do XLSX com formatação |
| `_apply_header(ws)` | Cabeçalho azul escuro + fonte branca bold |
| `_autowidth(ws, df)` | Auto-ajusta largura das colunas (max 55 chars) |
| `_color_status_rows(ws, df)` | Pinta linhas por status (amarelo/azul/verde) e SLA (laranja) |
| `_sanitize_df(df)` | Converte datetime → string, remove chars ilegais de células Excel |

### Abas geradas no XLSX

```
Aba_Geral         Um registro por ticket com todos os indicadores
SLA_Em_Processo   Tickets abertos/em processo ordenados por urgência
SLA_Resposta      Tempo de resposta BO → BP por ticket
Funil_Mensal      Abertos/fechados/em processo por mês
Funil_Semanal     Abertos/fechados/em processo por semana
Por_Assunto       Agrupamento por assunto do ticket
Por_Escritorio    Agrupamento por escritório
```

---

## Arquivo 3 — `dashboard_bko.py`

### Cores do sistema

```python
COR_FECHADO   = "#2CA02C"   # verde
COR_PROCESSO  = "#FF7F0E"   # laranja
COR_ABERTO    = "#1F77B4"   # azul
COR_ALERTA    = "#D62728"   # vermelho
COR_PRINCIPAL = "#1F3864"   # azul escuro (cabeçalhos)
```

### Função `executar_pipeline(file_bytes, filename)`

Pipeline completo executado no Streamlit. Retorna:

```python
result = {
    "enc":      str,        # encoding detectado
    "avisos":   list[str],  # avisos de qualidade dos dados
    "geral":    DataFrame,  # todos os tickets consolidados
    "sla_p":    DataFrame,  # SLA Em Processo
    "sla_r":    DataFrame,  # SLA Resposta
    "funil_m":  DataFrame,  # Funil Mensal
    "funil_s":  DataFrame,  # Funil Semanal
    "assunto":  DataFrame,  # Por Assunto
    "escrit":   DataFrame,  # Por Escritório
    "depto":    DataFrame,  # Por Departamento
    "xlsx":     bytes,      # XLSX completo em memória
    "ref_time": str,        # "DD/MM/YYYY HH:MM"
}
```

### Cache `st.session_state`

```python
f"result_{file.file_id}"  →  result dict
f"pdf_{file.file_id}"     →  bytes do PDF
f"html_{file.file_id}"    →  bytes do HTML export (UTF-8)
```

### Abas do dashboard (7 tabs)

| Tab | Função | Conteúdo |
|-----|--------|----------|
| 📋 Aba Geral | `tab_aba_geral(df)` | Tabela completa com todos os tickets |
| ⏱ SLA Em Processo | `tab_sla_processo(df)` | 3 cards vermelhos + top-20 tickets em espera (barras coloridas) |
| 📨 SLA Resposta | `tab_sla_resposta(df)` | 4 cards KPI + faixas ≤10h/10-24h/>24h + tabela |
| 📊 Funil | `tab_funil(df_m, df_s)` | Barras agrupadas mensal e semanal |
| 🏷 Por Assunto | `tab_por_assunto(df)` | Top-25 horizontal + tabela |
| 🏢 Por Escritório | `tab_por_escritorio(df)` | Comparativos + tabela |
| 🏬 Por Departamento | `tab_por_departamento(df)` | Volume + SLA + "Transferências: dados em tratamento" |

### Componentes de KPI

```python
# big_numbers(df) — renderiza via st.markdown(HTML)
# 5 cards com box-shadow 3D, fundo #262730
# Valores: Total, Fechados, Em Processamento, Abertos, SLA Alerta
```

### Funções de Export

| Função | Assinatura | O que faz |
|--------|-----------|-----------|
| `_build_html_export` | `(result) → str` | HTML completo, tema escuro Streamlit, gráficos Plotly interativos via CDN, CSS `@media print` |
| `_build_pdf` | `(result) → bytes` | PDF via WeasyPrint (Linux) com KPIs, tabelas e gráficos PNG base64 |
| `_html_to_pdf` | `(html) → bytes` | Tenta WeasyPrint → fallback xhtml2pdf → RuntimeError |
| `_fig_to_b64` | `(fig) → str` | Exporta Plotly figure → PNG base64 (usa kaleido) |
| `_enviar_email` | `(dest, xlsx, ref, html=None)` | Envia SMTP com XLSX + HTML como anexos |

### Funções auxiliares

| Função | O que faz |
|--------|-----------|
| `_fmt_h(h)` | `459.2` → `"19d 3h"` ou `"45min"` |
| `_trunc(s, n)` | Trunca string para n chars |
| `_log(msg)` | Print com timestamp para debug |

---

## Arquivo de Referência — `Departamentos.xlsx`

```
Colunas: ticket_subject | Departamento
Linhas:  48 mapeamentos de categoria → departamento
JOIN:    LEFT JOIN em df_geral.ticket_subject
Fallback: tickets sem match → "Sem Departamento"
```

### Departamentos existentes

```
CS/CX                        (maior volume — ~5.000 tickets)
Operações
Financeiro
Capital
Treinamento & Desenvolvimento
Sem Departamento             (fallback automático)
```

---

## Configuração de E-mail

```toml
# .streamlit/secrets.toml
[email]
smtp_host     = "smtp.gmail.com"
smtp_port     = 587
smtp_user     = "seu@email.com"
smtp_password = "senha_de_app_gmail"
smtp_from     = "seu@email.com"
```

Também aceita variáveis de ambiente (`.env` para desenvolvimento local):
```
EMAIL_SMTP_HOST, EMAIL_SMTP_PORT, EMAIL_SMTP_USER,
EMAIL_SMTP_PASSWORD, EMAIL_SMTP_FROM
```

---

## Dependências

### `requirements.txt`
```
streamlit>=1.35.0
plotly>=5.18.0
pandas>=2.0.0
openpyxl>=3.1.0
fpdf2>=2.7.0
kaleido
weasyprint>=60.0
python-dotenv>=1.0.0
```

### `packages.txt` (Streamlit Cloud — Debian Trixie)
```
libpango-1.0-0
libharfbuzz0b
libpangoft2-1.0-0
libpangocairo-1.0-0
libcairo2
libgdk-pixbuf-2.0-0
shared-mime-info
fonts-liberation
```

---

## Como Rodar Localmente

```bash
# 1. Criar ambiente virtual
python -m venv bko_env
bko_env\Scripts\activate        # Windows
source bko_env/bin/activate     # Linux/Mac

# 2. Instalar dependências
pip install -r requirements.txt

# 3. Configurar e-mail (opcional)
cp .env.example .env
# editar .env com credenciais SMTP

# 4. Rodar
streamlit run dashboard_bko.py
# ou no Windows:
rodar_dashboard.bat
```

---

## Replicar em React — Mapeamento

### Backend (API REST necessária)

| Endpoint | Método | Equivale a |
|----------|--------|-----------|
| `/upload` | POST (multipart) | `process_file()` + `executar_pipeline()` |
| `/result/:id` | GET | Retorna `result` como JSON |
| `/export/xlsx/:id` | GET | Retorna bytes do XLSX |
| `/export/html/:id` | GET | Retorna HTML gerado |
| `/email` | POST | `_enviar_email()` |

### Frontend React — Componentes

| Componente | Equivale a |
|------------|-----------|
| `<FileUpload>` | `st.file_uploader` + `executar_pipeline` |
| `<BigNumbers>` | `big_numbers(df)` — 5 cards KPI |
| `<TabGeral>` | `tab_aba_geral` |
| `<TabSLAProcesso>` | `tab_sla_processo` |
| `<TabSLAResposta>` | `tab_sla_resposta` |
| `<TabFunil>` | `tab_funil` |
| `<TabPorAssunto>` | `tab_por_assunto` |
| `<TabPorEscritorio>` | `tab_por_escritorio` |
| `<TabPorDepartamento>` | `tab_por_departamento` |
| `<ExportHTML>` | `_build_html_export` |
| `<SendEmail>` | `_enviar_email` |

### Bibliotecas React sugeridas

```
recharts ou plotly.js    → gráficos
tanstack/react-table     → tabelas
react-dropzone           → upload
axios                    → chamadas à API
dayjs                    → manipulação de datas
```

---

## Status Atual do Projeto

- Dashboard funcional no Streamlit Cloud
- Export HTML interativo com gráficos Plotly (recomendado para PDF)
- Export PDF via WeasyPrint no Cloud (backup)
- Envio de e-mail com XLSX + HTML em anexo
- Análise por departamento via Departamentos.xlsx
- Transferências por departamento marcadas como "dados em tratamento"
