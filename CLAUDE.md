# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

BKO support-ticket dashboard (Portuguese codebase/UI), running as a **Google Apps Script web app embedded in Google Sites**. Data comes straight from Metabase (Cloud, question **274 "Ticket x Productions"**, native SQL, one row per ticket message). `apps_script/LEIAME.md` holds the deployment steps and data notes. Keep it in sync.

`legado_streamlit/` is the retired Streamlit version (manual CSV upload). Do not extend it. Its response-time and analyst metrics are known to be wrong (see its README).

## Commands

There is no build, lint, or test suite. Code is deployed by pasting the files into the Apps Script editor (see LEIAME). Local tooling:

```bash
pip install python-dotenv pandas
python apps_script/testar_metabase.py 2026-09-01 2026-09-30   # reads METABASE_* from .env; prints params, SQL, columns; saves metabase_amostra.csv
node -e "new Function(require('fs').readFileSync('apps_script/Code.gs','utf8'))"   # syntax check
```

To verify `Code.gs` changes, run it in Node with a `vm` context that stubs `Utilities` (parseCsv, gzip/ungzip, base64, newBlob), `PropertiesService`, `CacheService`, `DriveApp` and `UrlFetchApp`. `UrlFetchApp` can call the real Metabase through a child process. Then compare the output against an independent pandas computation on `metabase_amostra.csv`. To preview `Index.html`, inject a fake `google.script.run` whose `getDados()` returns the gzip+base64 payload, and serve the file locally.

## Architecture

`apps_script/Code.gs` (server) → JSON in Drive → `apps_script/Index.html` (client renders everything, cross-filtering in the browser).

- **Fetch:** `conectarMetabase_` logs in (`METABASE_API_KEY` or user/password from Script Properties) and reads the card's template tags. `baixarMetabaseCsv_` POSTs `/api/card/274/query/csv` with `start_date`/`final_date` (text, `yyyy-MM-dd`, filtering on ticket **open date**). It fetches **one month per request**, because the `ticket_message` column makes a month ~10 MB and UrlFetch caps responses at 50 MB.
- **Incremental update:** `atualizarDados()` (daily trigger, 6h) refetches the last `JANELA_DIAS` (60) days and replaces those tickets in the stored payload, keeping older ones. `cargaCompleta()` refetches everything since `METABASE_DATA_INICIO`. Because the filter is on open date, a ticket always lands entirely in one month or window. Older open tickets only get status changes via `cargaCompleta`.
- **Consolidation** (`agrupar_` → `consolidar_`, ported from the legacy `processador_relatorio_data.consolidate`): N message rows become one record per ticket. Status priority is `closed > processing > open`. Key business rules:
  - First response = first message with `sender_type = Admin`. Do **not** use `answered_at`: it is `it.updated_at` in the SQL.
  - `fila`: for non-closed tickets, a last message from Admin means "aguardando consultor"; otherwise "aguardando BO". The 24h SLA alert applies only to "aguardando BO", measured from the last message.
  - Analyst = last Admin sender.
  - Subject transfers come from the `transfer_*`/`last_transference` columns (constant per ticket, last transfer only). "Trocas de resp." is the old sender-change count, a different metric.
- **Payload format** (`codificar_`/`decodificar_`): strings are dictionary-encoded into index lists. Each ticket is a positional array, and the field order is documented above `codificar_`. Transfer fields are appended only when present. Bump `VERSAO_DADOS` in **both** `Code.gs` and `Index.html` whenever the row layout changes. A stored payload with another version forces a full reload.
- **Storage/serving:** the payload is gzip + base64 in a Drive file (`DADOS_FILE_ID` property), also cached in `CacheService` in 90 KB slices. `doGet` serves only the HTML shell. The page calls `google.script.run.getDados()` and decompresses with `DecompressionStream`.
- **Departments:** `Departamentos.gs` maps subject → department and is generated from `Departamentos.xlsx` (repo root). Unmapped subjects become "Sem Departamento".
- **UI conventions in `Index.html`:** W1 palette as CSS tokens with light/dark themes. Every visual derives from `filtrados()` (sidebar state plus click-to-filter). Use `tabela()`/`hbars()`/`barChart()` for new visuals. Long lists use the `select.limite` + `limitar(k, lista)` pattern. Lists over the ~57k tickets cap at 500 rows instead of "Todos".
- **Privacy:** `ticket_message` contains client personal data. Never log it (see `testarConexao`).
