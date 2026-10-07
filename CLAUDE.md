# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Streamlit dashboard for BKO support-ticket analysis (Portuguese codebase/UI). A user uploads a ticket export (CSV `;`-separated or XLSX); the app runs ETL, consolidates rows per ticket, computes SLA/funnel/grouping analyses, and offers XLSX/HTML export plus SMTP e-mail. `PROJETO.md` is the detailed reference (function tables, column schemas, result dict shape) — consult it before large changes and keep it in sync.

## Commands

```bash
pip install -r requirements.txt
streamlit run dashboard_bko.py          # or rodar_dashboard.bat (expects venv at C:\bko_env)

# Standalone CLI pipeline (same steps, without the dashboard)
python validador_tabela_ticket.py <export.csv|xlsx> [out_ETL.csv]
python processador_relatorio_data.py <arquivo_ETL.csv> [relatorio.xlsx]
```

No test suite, linter, or build step exists. Verify changes by running the app (or the CLI scripts) against a real export CSV; sample `ticket_x_productions_*.csv` files may be present locally but data files are gitignored (except `Departamentos.xlsx`).

## Architecture

Three modules form a linear pipeline:

1. `validador_tabela_ticket.py` (imported as `vtk`) — encoding auto-detection (latin-1/utf-8/cp1252/utf-8-sig), double-encoding repair (`_fix_residual_chars`), splitting combined date+time columns (`open_at` → `open_at` + `hora_open`, etc., BR or ISO formats), validation warnings.
2. `processador_relatorio_data.py` (imported as `prd`) — `_parse_datetimes` rebuilds `dt_*` columns from the split pairs; `consolidate()` is the core step that collapses N message rows per `ticket_id` into one record (status priority `closed > processing > open`, SLA hours, transfer counts, last analyst/consultor); `make_*` functions build each analysis DataFrame; `write_xlsx` writes formatted sheets with openpyxl charts.
3. `dashboard_bko.py` — Streamlit UI. `executar_pipeline()` re-implements the orchestration of `vtk` + `prd` in memory (it does **not** call `prd.process()`), additionally merging `Departamentos.xlsx` (LEFT JOIN on `ticket_subject`, fallback `"Sem Departamento"`) and producing `Por_Departamento`. A change to the pipeline steps usually needs to be made in both `executar_pipeline()` and `prd.process()`.

Key details:
- `prd.AGORA` is a module-level timestamp used as "now" for open tickets' SLA; the dashboard reassigns it at the start of each pipeline run. `SLA_ALERTA_H = 24` drives alerts.
- Results are cached in `st.session_state` keyed by `uploaded.file_id` (`result_*`, `pdf_*`, `html_*`); invalidating requires changing/clearing these keys.
- Exports: `_build_html_export` (self-contained dark-theme HTML with Plotly via CDN — the primary export, meant to be printed to PDF from the browser) and `_build_pdf` (WeasyPrint → xhtml2pdf fallback; plus an fpdf2-based path). The PDF is still generated but its download button is hidden.
- Chart image rendering uses kaleido; WeasyPrint needs the system libs in `packages.txt` (Streamlit Cloud deployment).
- SMTP config: `st.secrets["email"]` (`.streamlit/secrets.toml`) first, then `EMAIL_SMTP_*` env vars (`.env` via python-dotenv). See the `.example` files.
- Status colors are shared conventions: fechado `#2CA02C`, processo `#FF7F0E`, aberto `#1F77B4`, alerta `#D62728`, principal `#1F3864` (duplicated in both `dashboard_bko.py` and `processador_relatorio_data.py`).
- Sidebar widgets live in a single `with st.sidebar:` block and PDF generation runs outside it, deliberately, to avoid Streamlit 1.57+ context conflicts.
