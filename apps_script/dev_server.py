#!/usr/bin/env python3
"""
dev_server.py — pré-visualização local das páginas do Apps Script (sem Google).

Faz o papel do HtmlService: monta Index.html, Painel.html e Diretoria.html trocando
<?!= include('X') ?> pelo conteúdo de X.html, <?= urlGerencial ?> por /index e <?= urlPainel ?>
por /painel, e injeta um
google.script.run falso:
  getDados()           -> conteúdo de apps_script/.dev/dados.b64 (gzip+base64, como no Drive)
  getConfigRelatorio() -> destinatários/assunto de exemplo
  registrarSemana(r)   -> só registra no terminal

Para ter dados: gere o payload com o harness do Node (ver CLAUDE.md) e salve o resultado de
getDados() em apps_script/.dev/dados.b64 (a pasta está no .gitignore: tem nomes de pessoas).

Uso:
    python apps_script/dev_server.py            # http://localhost:8765
    PORT=9000 python apps_script/dev_server.py
"""

import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PASTA = Path(__file__).resolve().parent
DADOS = Path(os.getenv("BKO_DADOS", PASTA / ".dev" / "dados.b64"))
PORTA = int(os.getenv("PORT", "8765"))
PAGINAS = {"/index": "Index", "/painel": "Painel", "/diretoria": "Diretoria"}

STUB = """<script>
// google.script.run falso (dev_server.py): cada cadeia guarda seus próprios handlers
window.google = { script: { get run(){
  const h = { ok: () => {}, erro: e => console.error(e) };
  const cadeia = new Proxy({}, { get: (_, nome) => {
    if (nome === 'withSuccessHandler') return f => { h.ok = f; return cadeia; };
    if (nome === 'withFailureHandler') return f => { h.erro = f; return cadeia; };
    return (...args) => fetch('/api/' + nome, { method: 'POST', body: JSON.stringify(args) })
      .then(r => r.ok ? r.json() : r.text().then(t => Promise.reject(new Error(t))))
      .then(v => h.ok(v), e => h.erro(e));
  }});
  return cadeia;
}}};
</script>"""


def montar(nome: str) -> str:
    html = (PASTA / f"{nome}.html").read_text(encoding="utf-8")
    html = re.sub(r"<\?!=\s*include\('(\w+)'\)\s*\?>",
                  lambda m: (PASTA / f"{m.group(1)}.html").read_text(encoding="utf-8"), html)
    html = html.replace("<?= urlGerencial ?>", "/index").replace("<?= urlPainel ?>", "/painel")
    return html.replace("<head>", "<head>\n" + STUB, 1)


def api(nome: str, args: list):
    if nome == "getDados":
        return DADOS.read_text(encoding="utf-8").strip() if DADOS.exists() else ""
    if nome == "getConfigRelatorio":
        return {"destinatarios": "exemplo@w1consultoria.com.br",
                "assunto": "Relatório semanal de tickets · BackOffice · {semana}", "urlPlanilha": ""}
    if nome == "registrarSemana":
        print("registrarSemana:", json.dumps(args[0], ensure_ascii=False), flush=True)
        return "https://docs.google.com/spreadsheets/d/EXEMPLO/edit"
    raise KeyError(nome)


class Handler(BaseHTTPRequestHandler):
    def _enviar(self, codigo, corpo, tipo):
        dados = corpo.encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(dados)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(dados)

    def do_GET(self):
        caminho = self.path.split("?")[0]
        if caminho == "/":
            links = "".join(f'<li><a href="{p}">{n}</a></li>' for p, n in PAGINAS.items())
            aviso = "" if DADOS.exists() else f"<p><b>Sem dados:</b> salve o payload em {DADOS}</p>"
            return self._enviar(200, f"<!doctype html><meta charset=utf-8><h1>BKO · preview local</h1>{aviso}<ul>{links}</ul>",
                                "text/html; charset=utf-8")
        if caminho in PAGINAS:
            return self._enviar(200, montar(PAGINAS[caminho]), "text/html; charset=utf-8")
        self._enviar(404, "não encontrado", "text/plain; charset=utf-8")

    def do_POST(self):
        nome = self.path.removeprefix("/api/")
        args = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or "[]")
        try:
            self._enviar(200, json.dumps(api(nome, args), ensure_ascii=False), "application/json; charset=utf-8")
        except KeyError:
            self._enviar(404, f"função {nome} não existe no stub", "text/plain; charset=utf-8")

    def log_message(self, fmt, *args):
        if "/api/" not in self.path:
            super().log_message(fmt, *args)


if __name__ == "__main__":
    print(f"BKO preview em http://localhost:{PORTA}  (dados: {DADOS}{'' if DADOS.exists() else ' — ausente'})", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORTA), Handler).serve_forever()
