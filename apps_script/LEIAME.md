# Dashboard BKO — Apps Script + Google Sites

Versão do dashboard para o Google Sites. Tem as mesmas métricas do Streamlit (`dashboard_bko.py`), com os dados vindo direto do Metabase.

```
A cada 30 min  → atualizarDados()      últimos 60 dias (pela data de abertura), ~20 s
Todo dia às 7h → atualizarHistorico()  anos antes de METABASE_DATA_INICIO (desde 2023): só os tickets aguardando o BO, ~3 min
Domingo às 5h  → cargaCompleta()       desde METABASE_DATA_INICIO (acerta o status dos tickets antigos), ~90 s
    (as três usam LockService: uma nunca grava por cima da outra; quem não pega a trava em 30 s pula a vez)
    → POST {METABASE_URL}/api/card/274/query/csv, um mês por vez (start_date/final_date)
    → consolidar_()  (mesma lógica do processador_relatorio_data.consolidate)
    → substitui os tickets da janela no JSON salvo e mantém os mais antigos
    → JSON no Drive (gzip + base64: ~1,6 MB para ~57 mil tickets)
Web app doGet() → Index.html (painel completo), Painel.html (?v=painel) ou Diretoria.html (?v=diretoria); os dados vêm por getDados()
```

**Limite da janela de 60 dias:** ela não enxerga mudanças de status em tickets abertos antes dela. Em out/2026, 4.841 dos 7.040 tickets não fechados tinham mais de 60 dias. Por isso a `cargaCompleta` roda todo domingo. Se precisar acertar antes, rode-a manualmente. O tempo em processo dos tickets não fechados é recalculado a cada execução, inclusive para os tickets antigos que foram mantidos.

## Métricas de transferência

Vêm das colunas `last_transference` e `transfer_*` da pergunta 274. O valor é o mesmo em todas as linhas do ticket e representa só a **última** troca de assunto.

- **Aba "Transferências":** total de transferidos, para outra área ou na mesma área, tempo da abertura até a transferência, volume por período, áreas de origem e de destino, principais fluxos entre assuntos e as transferências mais recentes.
- **Filtro na lateral:** Outra área / Mesma área / Sem transferência. Vale para todas as abas.
- **"Trocas de resp.":** é a métrica antiga do Streamlit (`n_transferencias`), que conta mudanças de remetente entre mensagens. Foi renomeada para não confundir com transferência de assunto.

| Arquivo | O que é |
|---|---|
| `Code.gs` | Busca no Metabase, consolida por ticket, grava no Drive e serve a página |
| `Departamentos.gs` | Mapeamento assunto → departamento no padrão do Titan (gerado do `Departamentos.xlsx`) |
| `Index.html` | Painel completo / gerencial (design W1; os filtros cruzados rodam no navegador) |
| `Diretoria.html` | Relatório de tickets em atraso no formato do Titan (`/exec?v=diretoria`) |
| `Painel.html` | Painel de tempo real: semana vigente (seg–dom), busca nos últimos 30 dias e relatório semanal por e-mail (`/exec?v=painel`) |
| `Regras.html` | Regras comuns às páginas: SLA de 1 dia útil, feriados, com quem está o ticket, transferência |
| `Relatorio.html` | Tabela por assunto no formato do Titan e cópia para e-mail/Excel (usada pelo Painel e pela Diretoria) |
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
2. Crie os arquivos `Code.gs`, `Departamentos.gs`, `Index.html`, `Painel.html`, `Diretoria.html`, `Regras.html` e `Relatorio.html` (HTML). Cole o conteúdo de cada um e substitua o `appsscript.json`.
3. Em Configurações do projeto > Propriedades do script, adicione:
   - `METABASE_URL`: `https://w1-inc.metabaseapp.com`
   - `METABASE_CARD_ID`: `274`
   - `METABASE_USER` e `METABASE_PASSWORD`, ou `METABASE_API_KEY`. A chave de API é melhor, porque não depende da senha de uma pessoa.
   - `METABASE_DATA_INICIO` (opcional): primeira data de abertura, no formato `yyyy-MM-dd`. O padrão é 1º de janeiro do ano atual; prefira fixar `2026-01-01`.
   - `RELATORIO_DESTINATARIOS`: e-mails do relatório semanal, separados por vírgula.
   - `RELATORIO_ASSUNTO` (opcional): assunto do e-mail; `{semana}` vira o período.
4. No editor, rode `testarConexao` e autorize. O log mostra as colunas recebidas.
5. Rode `atualizarDados`. Na primeira vez, sem dados salvos, ele faz a carga completa. Rode `atualizarHistorico` uma vez para trazer os atrasos antigos (~3 min). Depois rode `instalarGatilho`, que cria os gatilhos de 30 min, das 7h e de domingo.
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

## Relatório de tickets em atraso (`?v=diretoria`)

Relatório de controle para a diretoria, no **mesmo formato do relatório do Titan**: uma linha por assunto, com o departamento. O botão **"Ver painel completo"** abre o painel gerencial.

| Coluna | Regra |
|---|---|
| Abertos (mês atual) | Todos os tickets abertos no mês atual, inclusive os já fechados |
| Em processo (3 colunas) | Não fechados, abertos no mês atual e nos 2 anteriores, um mês por coluna |
| Tickets em dia | Aguardando o BO, ainda dentro de 1 dia útil |
| Em atraso · Sem resposta | Aguardando o BO há mais de 1 dia útil desde a última mensagem do consultor, de **qualquer mês** |
| Em atraso · Transferência | Transferido de assunto **depois** da última mensagem, e o novo departamento não respondeu em 1 dia útil |
| Resposta do consultor mais antiga | A mensagem de consultor pendente mais antiga do assunto |
| Últ. transferência de assunto | A transferência mais recente entre os tickets não fechados do assunto |

- **SLA de 1 dia útil:** vence no mesmo horário do próximo dia útil. Por exemplo, uma mensagem de sexta às 15h vence na segunda às 15h. Fins de semana e feriados nacionais não contam; Carnaval e Corpus Christi, que são ponto facultativo, contam como dia útil. A regra fica no `Regras.html` e vale também para o painel completo.
- **Atrasos antigos:** o `atualizarHistorico` guarda só os tickets anteriores a `METABASE_DATA_INICIO` que ainda aguardam o BO. Em out/2026 eram 30 tickets, de 2023 a 2025. No painel completo, esses tickets ficam fora do período padrão, que começa em `METABASE_DATA_INICIO`.
- **Linhas em vermelho:** têm tickets em atraso. O departamento "Teste" fica fora do relatório.
- **Botões:**
  - "Copiar tabela" copia em HTML, para colar no e-mail mantendo o formato, e em texto separado por tab, para o Excel.
  - "Imprimir / PDF" gera uma folha A4 na horizontal.
- **Teste sem publicar:** use **Implantar › Testar implantações** e abra a URL `/dev` com `?v=diretoria`.

## Painel de tempo real (`?v=painel`)

Painel para acompanhar a semana, com link para o painel completo. Pode ser colocado no Sites ou no Metabase com a URL `/exec?v=painel`. O web app já permite ser incorporado (`ALLOWALL`), mas quem abrir precisa estar logado com a conta Google da W1.

- **Atualização:**
  - os dados vêm do Metabase a cada 30 min;
  - o topo mostra a data e a hora da última atualização, e o ponto fica laranja se passar de 1h;
  - com a página aberta (por exemplo, numa TV), ela busca dados novos sozinha a cada 10 min.
- **Período:**
  - abre na **semana vigente, de segunda a domingo**;
  - ◀ ▶ trocam de semana, e "Buscar" aceita qualquer intervalo dentro dos **últimos 30 dias**;
  - **filtro de status** (Aberto, Em processo, Pendente, Fechado; dá para marcar mais de um; nenhum marcado = todos) vale para tudo na tela. O relatório semanal ignora o filtro e sai sempre completo;
  - abertos, fechados, 1ª resposta e transferidos usam a data de abertura;
  - "Agora" (aguardando BO, em dia e atrasos) é o retrato do momento, com o SLA de 1 dia útil.
  - **O painel só considera tickets abertos nos últimos 120 dias** (`JANELA_DIAS` no `Painel.html`), inclusive no relatório semanal; os atrasos mais antigos aparecem só no relatório de atrasos (`?v=diretoria`).
- **Conteúdo:** de cima para baixo:
  - a tabela **por assunto** (com o departamento ao lado, ordenada como no Titan: por departamento e, dentro dele, mais atrasos primeiro), com a última transferência de assunto;
  - os cartões do período e de agora;
  - os 10 atrasos mais antigos.
- **Contexto dos números:**
  - Os cartões do período comparam com o período anterior de mesmo tamanho, **medido no mesmo ponto do tempo**. A última atualização é deslocada para trás pelo mesmo número de dias, para os tickets antigos não ganharem dias a mais para serem respondidos ou fechados. O fechamento é aproximado pela última mensagem.
  - O cartão de abertos mostra os dias úteis e os feriados do período.
  - Os cartões de agora mostram a proporção sobre o total aguardando o BO.
  - Segue o princípio de dashboards "informação sem contexto é spam", da skill `revenue-centric-design`.
- **"Gerar relatório semanal":**
  1. Mostra uma prévia em HTML com:
     - o resumo da semana;
     - os números por departamento;
     - a situação por assunto, no formato do Titan;
     - o link para o painel completo.
  2. "Copiar e abrir e-mail" copia o relatório e abre um e-mail novo no Gmail, já com destinatários e assunto (`RELATORIO_DESTINATARIOS` e `RELATORIO_ASSUNTO`). É só colar (Ctrl+V) e enviar.
  3. Grava a semana na planilha **"BKO · Histórico semanal de tickets"**: uma linha por semana; gerar de novo atualiza a linha. A planilha é criada na primeira vez, e o id fica em `HISTORICO_SHEET_ID`.

## Observações

- "Agora" é o horário da última atualização. Os tickets abertos/em processo envelhecem a cada execução do gatilho, e não em tempo real.
- Viewers não precisam de acesso ao Metabase: o web app roda com a conta de quem implantou.
- Departamentos novos: edite o `Departamentos.xlsx` e regenere o `Departamentos.gs`. A coluna "Origem" diz se o mapeamento veio do Titan ou foi proposto. Assuntos sem mapeamento aparecem como "Sem Departamento".
