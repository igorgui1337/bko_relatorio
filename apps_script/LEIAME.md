# Dashboard BKO — Apps Script + Google Sites

Versão do dashboard para o Google Sites. Tem as mesmas métricas do Streamlit (`dashboard_bko.py`), com os dados vindo direto do Metabase.

```
Todo dia às 6h → atualizarDados()  últimos 60 dias (pela data de abertura), ~20 s
Manual         → cargaCompleta()   desde METABASE_DATA_INICIO, ~80 s
    → POST {METABASE_URL}/api/card/274/query/csv, um mês por vez (start_date/final_date)
    → consolidar_()  (mesma lógica do processador_relatorio_data.consolidate)
    → substitui os tickets da janela no JSON salvo e mantém os mais antigos
    → JSON no Drive (gzip + base64: ~1,6 MB para ~57 mil tickets)
Web app doGet() → Index.html com o JSON embutido → incorporado no Google Sites
```

**Limite da janela de 60 dias:** ela não enxerga mudanças de status em tickets abertos antes dela. Em out/2026, 4.841 dos 7.040 tickets não fechados tinham mais de 60 dias. Quando precisar acertar esses tickets, rode `cargaCompleta` manualmente. O tempo em processo dos tickets não fechados é recalculado a cada execução, inclusive para os tickets antigos que foram mantidos.

## Métricas de transferência

Vêm das colunas `last_transference` e `transfer_*` da pergunta 274. O valor é o mesmo em todas as linhas do ticket e representa só a **última** troca de assunto.

- **Aba "Transferências":** total de transferidos, para outra área ou na mesma área, tempo da abertura até a transferência, volume por período, áreas de origem e de destino, principais fluxos entre assuntos e as transferências mais recentes.
- **Filtro na lateral:** Outra área / Mesma área / Sem transferência. Vale para todas as abas.
- **"Trocas de resp.":** é a métrica antiga do Streamlit (`n_transferencias`), que conta mudanças de remetente entre mensagens. Foi renomeada para não confundir com transferência de assunto.

| Arquivo | O que é |
|---|---|
| `Code.gs` | Busca no Metabase, consolida por ticket, grava no Drive e serve a página |
| `Departamentos.gs` | Mapeamento assunto → departamento (gerado do `Departamentos.xlsx`) |
| `Index.html` | Dashboard (design W1 do relatório de exemplo; os filtros cruzados rodam no navegador) |
| `appsscript.json` | Manifesto: fuso de São Paulo, escopos e configuração do web app |

## Fonte: Metabase

A fonte é a pergunta **274 "Ticket x Productions"** em https://w1-inc.metabaseapp.com (Metabase Cloud v1.63). É uma pergunta em SQL que devolve uma linha por mensagem.

- **Parâmetros:** `start_date` e `final_date`, de texto, no formato `yyyy-MM-dd`, aplicados como `::date` sobre a data de abertura. Sem eles, a pergunta devolve a base inteira.
- **Volume:** cada mês tem ~20 mil linhas e ~10 MB, por causa da coluna `ticket_message`. O UrlFetch aceita no máximo 50 MB, por isso o `Code.gs` busca mês a mês. Jan–out/2026 leva ~77 s, bem abaixo do limite de 6 min do Apps Script.
- **Datas:** chegam já em Brasília (o SQL subtrai 3h), em ISO sem fuso.

Para diagnosticar fora do Google, com o login no `.env` da raiz:
```bash
python apps_script/testar_metabase.py 2026-09-01 2026-09-30
```
Ele mostra a versão, os parâmetros, o SQL, as colunas e as primeiras linhas, e salva o resultado em `metabase_amostra.csv`.

## Implantação

1. Em script.google.com, crie um projeto novo. Em Configurações do projeto, marque "Mostrar arquivo de manifesto appsscript.json".
2. Crie os arquivos `Code.gs`, `Departamentos.gs` e `Index.html` (HTML). Cole o conteúdo de cada um e substitua o `appsscript.json`.
3. Em Configurações do projeto > Propriedades do script, adicione:
   - `METABASE_URL`: `https://w1-inc.metabaseapp.com`
   - `METABASE_CARD_ID`: `274`
   - `METABASE_USER` e `METABASE_PASSWORD`, ou `METABASE_API_KEY`. A chave de API é melhor, porque não depende da senha de uma pessoa.
   - `METABASE_DATA_INICIO` (opcional): primeira data de abertura, no formato `yyyy-MM-dd`. O padrão é 1º de janeiro do ano atual.
4. No editor, rode `testarConexao` e autorize. O log mostra as colunas recebidas.
5. Rode `atualizarDados`. Na primeira vez, sem dados salvos, ele faz a carga completa. Depois rode `instalarGatilho`, que cria o gatilho diário das 6h.
6. Em Implantar > Nova implantação > App da Web, use "Executar como: eu" e "Quem pode acessar: qualquer pessoa no domínio". Copie a URL `/exec`.
7. No Google Sites, vá em Inserir > Incorporar > Por URL, cole a URL `/exec` e ajuste a altura do bloco.

Quando mudar `Index.html` ou `Code.gs`, faça uma nova versão em Implantar > Gerenciar implantações > Editar > Nova versão. Assim a URL continua a mesma.

## Teste local (sem Google)

`Code.gs` roda no Node, e os resultados foram conferidos ticket a ticket contra o `consolidate()` do Python, sem nenhuma diferença:
- CSV de maio/2026: 8.963 tickets.
- Pergunta 274 de setembro/2026: 6.881 tickets.

O fluxo completo também foi rodado contra o Metabase real:
- Carga completa: 57.141 tickets em 82 s.
- Incremental de 60 dias logo depois: 20 s, com resultado idêntico ao da carga completa (0 diferenças). Para ver a página fora do Google, substitua `<?!= dados ?>` no `Index.html` pelo JSON gerado e abra o arquivo no navegador.

## Observações

- "Agora" é o horário da última atualização. Os tickets abertos/em processo envelhecem a cada execução do gatilho, e não em tempo real.
- Viewers não precisam de acesso ao Metabase: o web app roda com a conta de quem implantou.
- Departamentos novos: edite `Departamentos.gs`. Assuntos sem mapeamento aparecem como "Sem Departamento".
