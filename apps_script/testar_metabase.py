#!/usr/bin/env python3
"""
testar_metabase.py — Diagnóstico da pergunta do Metabase antes de subir o Apps Script.

Lê METABASE_* do .env (raiz do projeto) e:
  1. autentica (chave de API ou e-mail/senha)
  2. mostra versão do Metabase, parâmetros e o SQL da pergunta
  3. roda a pergunta no período informado e salva o CSV em metabase_amostra.csv
  4. mostra colunas, total de linhas e o formato das datas

Uso:
    python apps_script/testar_metabase.py                          # últimos 7 dias
    python apps_script/testar_metabase.py 2026-09-01 2026-09-30    # período
    python apps_script/testar_metabase.py 01/09/2026 30/09/2026    # se o SQL esperar dd/mm/aaaa
"""

import csv
import io
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parent.parent
load_dotenv(RAIZ / ".env")

BASE = os.getenv("METABASE_URL", "").rstrip("/")
CARD = os.getenv("METABASE_CARD_ID", "")
SAIDA = RAIZ / "metabase_amostra.csv"


def http(method, path, headers=None, json_body=None, form=None, timeout=300):
    data, h = None, dict(headers or {})
    if json_body is not None:
        data = json.dumps(json_body).encode()
        h["Content-Type"] = "application/json"
    elif form is not None:
        data = urllib.parse.urlencode(form).encode()
        h["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(BASE + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def autenticar():
    key = os.getenv("METABASE_API_KEY", "").strip()
    if key:
        print("Autenticação: chave de API")
        return {"x-api-key": key}
    user, pwd = os.getenv("METABASE_USER", "").strip(), os.getenv("METABASE_PASSWORD", "")
    if not user or not pwd:
        sys.exit("Preencha METABASE_USER e METABASE_PASSWORD (ou METABASE_API_KEY) no .env")
    status, body = http("POST", "/api/session", json_body={"username": user, "password": pwd})
    if status != 200:
        sys.exit(f"Login falhou (HTTP {status}): {body[:300].decode(errors='replace')}\n"
                 "Se vocês entram com Google/SSO, login por senha não funciona: use uma chave de API.")
    print("Autenticação: e-mail/senha OK")
    return {"X-Metabase-Session": json.loads(body)["id"]}


def main():
    if not BASE or not CARD:
        sys.exit("Defina METABASE_URL e METABASE_CARD_ID no .env")
    hoje = date.today()
    start = sys.argv[1] if len(sys.argv) > 1 else (hoje - timedelta(days=7)).isoformat()
    final = sys.argv[2] if len(sys.argv) > 2 else hoje.isoformat()

    auth = autenticar()

    # 1. Versão
    st, body = http("GET", "/api/session/properties", headers=auth)
    if st == 200:
        v = json.loads(body).get("version", {})
        print(f"Versão do Metabase: {v.get('tag', '?')}")

    # 2. Pergunta: parâmetros e SQL
    st, body = http("GET", f"/api/card/{CARD}", headers=auth)
    if st != 200:
        sys.exit(f"Não consegui ler a pergunta {CARD} (HTTP {st}): {body[:300].decode(errors='replace')}")
    card = json.loads(body)
    dq = card.get("dataset_query", {})
    native = dq.get("native") or {}
    if not native and dq.get("stages"):           # formato novo (MBQL 5)
        native = dq["stages"][0]
    sql = native.get("query") or native.get("native") or ""
    tags = native.get("template-tags", {})
    if isinstance(tags, list):                    # MBQL 5: lista de tags
        tags = {t["name"]: t for t in tags}
    print(f"\nPergunta {CARD}: {card.get('name')}  (tipo: {dq.get('type') or dq.get('lib/type')})")
    print("Parâmetros:")
    for nome, t in tags.items():
        print(f"  - {nome}: tipo={t.get('type')} obrigatório={t.get('required', False)} padrão={t.get('default')!r}")
    print("\n──────── SQL ────────")
    print(sql)
    print("─────────────────────\n")

    # 3. Executa no período
    params = [
        {"id": tags[n].get("id"), "type": "string/=",
         "target": ["variable", ["template-tag", n]], "value": v}
        for n, v in (("start_date", start), ("final_date", final)) if n in tags
    ]
    print(f"Rodando com start_date={start!r} final_date={final!r} ...")
    st, body = http("POST", f"/api/card/{CARD}/query/csv", headers=auth,
                    form={"parameters": json.dumps(params), "format_rows": "false"})
    if st not in (200, 202):
        sys.exit(f"Consulta falhou (HTTP {st}): {body[:500].decode(errors='replace')}")
    texto = body.decode("utf-8-sig", errors="replace")
    if texto.lstrip().startswith("{") and '"error"' in texto[:500]:
        sys.exit(f"Erro do Metabase: {texto[:500]}")
    SAIDA.write_text(texto, encoding="utf-8")

    # 4. Resumo
    linhas = list(csv.reader(io.StringIO(texto)))
    header, dados = linhas[0], linhas[1:]
    print(f"Linhas: {len(dados):,}  |  tamanho: {len(body) / 1e6:.1f} MB  |  salvo em {SAIDA.name}")
    print(f"Colunas ({len(header)}): {header}")
    idx = {c.lower(): i for i, c in enumerate(header)}
    if "ticket_id" in idx:
        print(f"Tickets distintos: {len({r[idx['ticket_id']] for r in dados if len(r) > idx['ticket_id']}):,}")
    print("\nPrimeiras 3 linhas:")
    for r in dados[:3]:
        print("  " + json.dumps(dict(zip(header, r)), ensure_ascii=False))
    esperadas = ["ticket_id", "ticket_subject", "open_at", "status", "answered_at", "message_at",
                 "sender_id", "previous_sender_id", "sender", "consultant", "office"]
    faltando = [c for c in esperadas if c not in idx]
    print(f"\nColunas esperadas pelo Code.gs faltando: {faltando or 'nenhuma'}")


if __name__ == "__main__":
    main()
