/**
 * Code.gs — Dashboard BKO (Google Apps Script + Google Sites)
 *
 * Fluxo:
 *   a cada 30 min       -> atualizarDados()      últimos JANELA_DIAS dias (por data de abertura)
 *   diário (7h)         -> atualizarHistorico()  anos antes de METABASE_DATA_INICIO, guardando só
 *                                                os tickets que estão aguardando o BO (atrasos antigos)
 *   domingo (5h)        -> cargaCompleta()       desde METABASE_DATA_INICIO (acerta status antigos)
 *   Atualizações usam LockService: uma execução nunca grava por cima de outra.
 *        -> baixa a pergunta do Metabase em CSV, um mês por requisição
 *           (filtro start_date/final_date da pergunta; cada mês tem ~10 MB por causa
 *            da coluna ticket_message, e o UrlFetch aceita no máximo 50 MB)
 *        -> consolida N mensagens por ticket (porta do processador_relatorio_data.consolidate)
 *        -> substitui no JSON salvo os tickets abertos dentro da janela e mantém os mais antigos
 *        -> grava o JSON (gzip + base64) no Drive
 *   doGet() -> serve Index.html com o JSON embutido (embed no Google Sites)
 *
 * Atenção: a janela não vê mudanças de status em tickets abertos antes dela (em out/2026,
 * ~70% dos não fechados tinham mais de 60 dias). Rode cargaCompleta() quando precisar
 * acertar esses tickets, ou agende-a (ver instalarGatilho).
 *
 * Configuração (Projeto > Configurações do projeto > Propriedades do script):
 *   METABASE_URL         https://w1-inc.metabaseapp.com
 *   METABASE_CARD_ID     274   (pergunta "Ticket x Productions")
 *   METABASE_DATA_INICIO opcional, yyyy-MM-dd — início da carga completa (padrão: 1º de janeiro do ano atual).
 *                        Use sempre um 1º de janeiro fixo (ex.: 2026-01-01).
 *   METABASE_HISTORICO_INICIO opcional, yyyy-MM-dd — primeiro ano do histórico (padrão: 2023-01-01)
 *   RELATORIO_DESTINATARIOS  e-mails do relatório semanal, separados por vírgula
 *   RELATORIO_ASSUNTO        opcional; {semana} vira o período (padrão: ASSUNTO_PADRAO)
 *   HISTORICO_SHEET_ID       criada sozinha: planilha com o histórico semanal
 *   METABASE_API_KEY     chave de API (Admin > Configurações > Autenticação > Chaves de API)
 *     — ou —
 *   METABASE_USER / METABASE_PASSWORD
 */

const SLA_ALERTA_H = 24;
const JANELA_DIAS = 60;
const HISTORICO_PADRAO = '2023-01-01';
// atualizarHistorico só começa um novo ano se ainda estiver abaixo disso (cada ano leva ~100 s;
// o Apps Script corta a execução em 6 min)
const HISTORICO_ORCAMENTO_MS = 150 * 1000;
const ARQUIVO_DADOS = 'bko_dashboard_dados.json.gz.b64';
// Sobe quando o formato do JSON muda; dados salvos em outra versão forçam carga completa.
const VERSAO_DADOS = 4;

// Com quem está o ticket não fechado, pela última mensagem
// TRANSFERENCIA: transferido de assunto depois da última mensagem (comparado com segundos,
// porque o BO costuma responder e transferir no mesmo minuto) — a vez é do novo departamento.
const FILA = { NENHUMA: 0, BO: 1, CONSULTOR: 2, TRANSFERENCIA: 3 };

// Ordem de exibição dos status. Status desconhecidos são anexados ao final.
const STATUS_BASE = ['open', 'processing', 'pending', 'closed'];

// Nomes aceitos para cada coluna (minúsculas). O primeiro que existir no CSV é usado.
const COLUNAS = {
  ticket_id:          ['ticket_id', 'ticket id', 'id'],
  ticket_subject:     ['ticket_subject', 'ticket subject', 'assunto'],
  open_at:            ['open_at', 'open at'],
  hora_open:          ['hora_open'],
  answered_at:        ['answered_at', 'answered at'],
  answered_hora:      ['answered_hora'],
  message_at:         ['message_at', 'message at'],
  message_hora:       ['message_hora'],
  status:             ['status'],
  sender_id:          ['sender_id', 'sender id'],
  previous_sender_id: ['previous_sender_id', 'previous sender id'],
  sender:             ['sender'],
  sender_type:        ['sender_type'],
  issuer_type:        ['issuer_type'],
  consultant:         ['consultant'],
  office:             ['office'],
  // Última transferência de assunto do ticket (mesmo valor em todas as linhas do ticket)
  last_transference:        ['last_transference'],
  transfer_from_department: ['transfer_from_department'],
  transfer_to_department:   ['transfer_to_department'],
  transfer_from_area:       ['transfer_from_area'],
  transfer_to_area:         ['transfer_to_area'],
  transfer_type:            ['transfer_type'],
};
const OBRIGATORIAS = ['ticket_id', 'ticket_subject', 'open_at', 'status'];


// ═══════════════════════════════ Web app ═══════════════════════════════════

/**
 * Serve só a página; os dados vêm depois por getDados(). Assim a tela abre na hora
 * em vez de esperar o Drive + a montagem de ~6 MB de HTML.
 *   (sem parâmetro)  -> Painel.html    PÁGINA PRINCIPAL: painel de tempo real + relatório semanal
 *   ?v=completo      -> Index.html     painel completo / gerencial (também ?v=gerencial)
 *   ?v=diretoria     -> Diretoria.html relatório de tickets em atraso (formato do Titan)
 *   ?v=painel        -> Painel.html    (mantido para links antigos)
 * Todas incluem Regras.html (SLA, fila, transferência); Painel e Diretoria incluem Relatorio.html.
 */
const PAGINA_PRINCIPAL = ['Painel', 'Painel de Tickets · Tempo real'];
const PAGINAS = {
  painel:    PAGINA_PRINCIPAL,
  completo:  ['Index', 'Tickets · BackOffice · Painel completo'],
  gerencial: ['Index', 'Tickets · BackOffice · Painel completo'],
  diretoria: ['Diretoria', 'Relatório de Tickets em Atraso · BackOffice'],
};
function doGet(e) {
  const v = e && e.parameter && e.parameter.v;
  const [arquivo, titulo] = Object.prototype.hasOwnProperty.call(PAGINAS, v) ? PAGINAS[v] : PAGINA_PRINCIPAL;
  const url = ScriptApp.getService().getUrl();
  return montarPagina_(arquivo, {
    URL_PAINEL: url,                        // link "voltar ao painel"
    URL_COMPLETO: url + '?v=completo',      // link "ver painel completo"
  })
    .setTitle(titulo)
    .addMetaTag('viewport', 'width=device-width, initial-scale=1')
    .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL);
}

/**
 * Diagnóstico: monta cada página exatamente como o doGet entrega e passa cada <script> pelo
 * verificador de sintaxe do V8 do Google. Se um bloco falha, testa apagar cada linha sozinha
 * e mostra as linhas cuja remoção conserta o bloco (só código, nenhum dado). Não altera nada.
 */
function diagnosticoPagina() {
  const NL = String.fromCharCode(10);
  const url = ScriptApp.getService().getUrl();
  const compila = c => { try { new Function(c); return ''; } catch (e) { return e.message; } };
  for (const arquivo of ['Painel', 'Index', 'Diretoria']) {
    const html = montarPagina_(arquivo, { URL_PAINEL: url, URL_COMPLETO: url + '?v=completo' }).getContent();
    console.log(`== ${arquivo}: ${html.split(NL).length} linhas | ${html.length} caracteres`);
    const re = /<script[^>]*>([\s\S]*?)<\/script>/g;
    let m, bloco = 0;
    while ((m = re.exec(html))) {
      bloco++;
      const inicio = html.slice(0, m.index).split(NL).length;
      const erro = compila(m[1]);
      if (!erro) continue;
      console.log(`   ERRO no <script> nº ${bloco} (linha ${inicio}): ${erro}`);
      const ls = m[1].split(NL);
      let achou = 0;
      for (let i = 0; i < ls.length && achou < 8; i++) {
        if (!ls[i].trim()) continue;
        const sem = ls.slice(0, i).concat([''], ls.slice(i + 1)).join(NL);
        const e2 = compila(sem);
        if (!e2 || e2 !== erro) {
          achou++;
          console.log(`   linha ${inicio + i} (sem ela: ${e2 || 'compila'}):`);
          for (let k = Math.max(0, i - 2); k <= Math.min(ls.length - 1, i + 2); k++)
            console.log(`      ${k === i ? '>>' : '  '} ${inicio + k}: ${JSON.stringify(ls[k]).slice(0, 300)}`);
        }
      }
      if (!achou) console.log('   nenhuma linha isolada explica o erro');
    }
    console.log(`   ${bloco} blocos <script> verificados`);
  }
}

/** Conteúdo de outro arquivo HTML do projeto (marcador @@INCLUDE:Nome@@ nas páginas). */
function include(nome) {
  return HtmlService.createHtmlOutputFromFile(nome).getContent();
}

/**
 * Monta a página sem o motor de template do HtmlService: lê o arquivo, encaixa os marcadores
 * @@INCLUDE:Nome@@ e troca @@URL_...@@ (usados só em atributos HTML, valores escapados).
 * Por que não os scriptlets do HtmlService: ele escapa esses marcadores ao ler o arquivo
 * (aparecem como texto na página) e interpreta as barras invertidas do JavaScript (a sequência
 * barra+n virava quebra de linha real -> SyntaxError). Regra: as páginas não usam barra invertida no JS.
 * É a mesma montagem do dev_server.py, então o preview local fica igual ao Google.
 */
function montarPagina_(arquivo, vars) {
  const escapar = t => String(t).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const html = HtmlService.createHtmlOutputFromFile(arquivo).getContent()
    .replace(/@@INCLUDE:(\w+)@@/g, (_, nome) => include(nome))
    .replace(/@@(URL_\w+)@@/g, (_, chave) => escapar(vars[chave] == null ? '' : vars[chave]));
  return HtmlService.createHtmlOutput(html);
}


// ═══════════════════════════════ Rotinas ═══════════════════════════════════

/** Gatilho diário: últimos JANELA_DIAS dias. Sem dados salvos, faz a carga completa. */
function atualizarDados() {
  const salvo = JSON.parse(lerDados_());
  if (!salvo || salvo.versao !== VERSAO_DADOS) return cargaCompleta();
  const d = new Date(Date.now() - 3 * 3600000);
  d.setUTCDate(d.getUTCDate() - JANELA_DIAS);
  atualizar_(d.toISOString().slice(0, 10));
}

/** Manual: recarrega tudo desde METABASE_DATA_INICIO. */
function cargaCompleta() {
  atualizar_(inicioCarga_());
}

/** Primeira data de abertura da carga completa (METABASE_DATA_INICIO ou 1º de janeiro do ano atual). */
function inicioCarga_() {
  return PropertiesService.getScriptProperties().getProperty('METABASE_DATA_INICIO')
    || fmtDt_(Date.now()).slice(0, 4) + '-01-01';
}

/**
 * Gatilho diário: traz os atrasos antigos (tickets abertos antes de METABASE_DATA_INICIO que
 * ainda aguardam o BO), para o relatório considerar atrasos de qualquer mês.
 * Carregar esses anos inteiros deixaria os dados enormes; por isso só ficam os pendentes do BO.
 * Processa um ano por vez em rodízio, enquanto couber no tempo (HISTORICO_ORCAMENTO_MS).
 */
function atualizarHistorico() {
  comTrava_('atualizarHistorico', historicoSemTrava_);
}

function historicoSemTrava_() {
  const t0 = Date.now();
  const props = PropertiesService.getScriptProperties();
  const inicio = inicioCarga_();
  const anoIni = +(props.getProperty('METABASE_HISTORICO_INICIO') || HISTORICO_PADRAO).slice(0, 4);
  const anos = [];
  for (let a = anoIni; a < +inicio.slice(0, 4); a++) anos.push(a);
  if (!anos.length) { console.log(`Sem anos de histórico antes de ${inicio}`); return; }

  const salvo = JSON.parse(lerDados_());
  if (!salvo || salvo.versao !== VERSAO_DADOS) { console.log('Rode atualizarDados antes do histórico.'); return; }
  // Mantém o horário da última atualização: o histórico não deve "adiantar o relógio" dos tickets recentes
  const agora = parseDt_(salvo.geradoEm);
  const base = decodificar_(salvo);
  const mb = conectarMetabase_();

  let i = Math.max(0, anos.indexOf(+props.getProperty('HISTORICO_PROXIMO_ANO')));
  for (let feitos = 0; feitos < anos.length && Date.now() - t0 < HISTORICO_ORCAMENTO_MS; feitos++, i++) {
    const ano = anos[i % anos.length];
    const grupos = new Map();
    for (const [ini, fim] of meses_(`${ano}-01-01`, `${ano}-12-31`)) agrupar_(baixarMetabaseCsv_(mb, ini, fim), grupos);
    for (const [tid, rec] of base) if (rec.abertura && rec.abertura.slice(0, 4) === String(ano)) base.delete(tid);
    const pendentes = consolidar_(grupos, agora).filter(aguardandoBO_);
    pendentes.forEach(rec => base.set(rec.tid, rec));
    console.log(`Histórico ${ano}: ${grupos.size} tickets lidos, ${pendentes.length} aguardando o BO | ${(Date.now() - t0) / 1000}s`);
  }
  props.setProperty('HISTORICO_PROXIMO_ANO', String(anos[i % anos.length]));

  recalcularEspera_(base, agora);
  salvarDados_(JSON.stringify(codificar_(base, agora)));
  console.log(`OK histórico | ${(Date.now() - t0) / 1000}s`);
}

/** Rode uma vez: agenda atualizarDados (30 min), atualizarHistorico (7h) e cargaCompleta (domingo 5h). */
function instalarGatilho() {
  ScriptApp.getProjectTriggers()
    .filter(tr => ['atualizarDados', 'cargaCompleta', 'atualizarHistorico'].indexOf(tr.getHandlerFunction()) >= 0)
    .forEach(tr => ScriptApp.deleteTrigger(tr));
  ScriptApp.newTrigger('atualizarDados').timeBased().everyMinutes(30).create();
  ScriptApp.newTrigger('atualizarHistorico').timeBased().everyDays(1).atHour(7).inTimezone('America/Sao_Paulo').create();
  ScriptApp.newTrigger('cargaCompleta').timeBased().onWeekDay(ScriptApp.WeekDay.SUNDAY).atHour(5).inTimezone('America/Sao_Paulo').create();
  console.log('Gatilhos instalados: atualizarDados a cada 30 min, atualizarHistorico diário às 7h, cargaCompleta domingo às 5h');
}

/** Diagnóstico: colunas e 3 primeiras linhas do mês atual. */
function testarConexao() {
  const hoje = fmtDt_(Date.now()).slice(0, 10);
  const linhas = baixarMetabaseCsv_(conectarMetabase_(), hoje.slice(0, 8) + '01', hoje);
  console.log('Colunas: ' + JSON.stringify(linhas[0]));
  // ticket_message fica fora do log: traz dados de clientes (telefone, e-mail)
  const iMsg = linhas[0].indexOf('ticket_message');
  linhas.slice(1, 4).forEach(l => console.log(JSON.stringify(l.filter((_, i) => i !== iMsg))));
  console.log(`Total: ${linhas.length - 1} linhas`);
}

/**
 * Baixa os tickets abertos de `desde` até hoje e substitui essa faixa nos dados salvos.
 * Tickets abertos antes de `desde` são mantidos como estavam (só o tempo em processo
 * dos não fechados é recalculado).
 */
function atualizar_(desde) {
  comTrava_(`atualizar desde ${desde}`, () => atualizarSemTrava_(desde));
}

/**
 * Roda fn com a trava do script. Com gatilho a cada 30 min, uma atualização pode começar
 * enquanto o histórico (~3 min) ainda grava; quem não conseguir a trava em 30 s desiste.
 */
function comTrava_(nome, fn) {
  const trava = LockService.getScriptLock();
  if (!trava.tryLock(30000)) { console.log(`${nome}: outra atualização em andamento, pulando esta.`); return; }
  try { return fn(); } finally { trava.releaseLock(); }
}

function atualizarSemTrava_(desde) {
  const t0 = Date.now();
  const agora = Date.now();
  const hoje = fmtDt_(agora).slice(0, 10);
  const mb = conectarMetabase_();

  // O filtro da pergunta é pela data de abertura, então cada ticket cai inteiro num único mês.
  const grupos = new Map();
  for (const [ini, fim] of meses_(desde, hoje)) {
    const t1 = Date.now();
    const linhas = baixarMetabaseCsv_(mb, ini, fim);
    agrupar_(linhas, grupos);
    console.log(`${ini}..${fim}: ${linhas.length - 1} linhas em ${(Date.now() - t1) / 1000}s`);
  }

  const base = decodificar_(JSON.parse(lerDados_()));
  let mantidos = 0;
  for (const [tid, rec] of base) {
    if (rec.abertura && rec.abertura.slice(0, 10) >= desde) base.delete(tid);   // faixa refeita
    else mantidos++;
  }
  for (const rec of consolidar_(grupos, agora)) base.set(rec.tid, rec);
  recalcularEspera_(base, agora);

  const payload = codificar_(base, agora);
  salvarDados_(JSON.stringify(payload));
  console.log(`OK desde ${desde}: ${grupos.size} tickets novos/atualizados + ${mantidos} mantidos = ${payload.t.length} | ${(Date.now() - t0) / 1000}s`);
}


// ═══════════════════════════════ Metabase ══════════════════════════════════

/** Autentica e lê os parâmetros (template tags) da pergunta. */
function conectarMetabase_() {
  const props = PropertiesService.getScriptProperties();
  const base = (props.getProperty('METABASE_URL') || '').replace(/\/+$/, '');
  const card = props.getProperty('METABASE_CARD_ID');
  if (!base || !card) throw new Error('Defina METABASE_URL e METABASE_CARD_ID nas propriedades do script.');
  const headers = authHeaders_(base, props);

  const resp = UrlFetchApp.fetch(`${base}/api/card/${card}`, { headers, muteHttpExceptions: true });
  if (resp.getResponseCode() !== 200) {
    throw new Error(`Não consegui ler a pergunta ${card} (HTTP ${resp.getResponseCode()})`);
  }
  const dq = JSON.parse(resp.getContentText()).dataset_query || {};
  let tags = ((dq.stages && dq.stages[0]) || dq.native || {})['template-tags'] || {};
  if (!Array.isArray(tags)) tags = Object.values(tags);
  return { base, card, headers, tags };
}

/** CSV de um período (yyyy-MM-dd, pela data de abertura). */
function baixarMetabaseCsv_(mb, inicio, fim) {
  const valores = { start_date: inicio, final_date: fim };
  const parameters = mb.tags.filter(t => t.name in valores).map(t => ({
    id: t.id, type: 'string/=', target: ['variable', ['template-tag', t.name]], value: valores[t.name],
  }));
  if (parameters.length < 2) throw new Error('A pergunta não tem os parâmetros start_date e final_date.');

  const resp = UrlFetchApp.fetch(`${mb.base}/api/card/${mb.card}/query/csv`, {
    method: 'post',
    headers: mb.headers,
    payload: { parameters: JSON.stringify(parameters), format_rows: 'false' },
    muteHttpExceptions: true,
  });
  const code = resp.getResponseCode();
  if (code !== 200 && code !== 202) {
    throw new Error(`Metabase HTTP ${code}: ${resp.getContentText().slice(0, 300)}`);
  }
  return Utilities.parseCsv(resp.getContentText('UTF-8'));
}

function authHeaders_(base, props) {
  const apiKey = props.getProperty('METABASE_API_KEY');
  if (apiKey) return { 'x-api-key': apiKey };

  const user = props.getProperty('METABASE_USER');
  const pass = props.getProperty('METABASE_PASSWORD');
  if (!user || !pass) throw new Error('Defina METABASE_API_KEY (ou METABASE_USER e METABASE_PASSWORD).');

  const resp = UrlFetchApp.fetch(`${base}/api/session`, {
    method: 'post',
    contentType: 'application/json',
    payload: JSON.stringify({ username: user, password: pass }),
    muteHttpExceptions: true,
  });
  if (resp.getResponseCode() !== 200) {
    throw new Error(`Login Metabase falhou (HTTP ${resp.getResponseCode()})`);
  }
  return { 'X-Metabase-Session': JSON.parse(resp.getContentText()).id };
}


// ═══════════════════════════════ Consolidação ══════════════════════════════
// Porta de processador_relatorio_data.consolidate(): um registro por ticket.

/** Acrescenta as linhas de um CSV ao mapa ticket_id -> mensagens. */
function agrupar_(linhas, grupos) {
  if (linhas.length < 2) return grupos;
  const header = linhas[0].map(h => String(h).trim().toLowerCase());
  const col = {};
  for (const [nome, aliases] of Object.entries(COLUNAS)) {
    col[nome] = aliases.map(a => header.indexOf(a)).find(i => i >= 0);
    if (col[nome] === undefined) col[nome] = -1;
  }
  const faltando = OBRIGATORIAS.filter(c => col[c] < 0);
  if (faltando.length) {
    throw new Error(`Colunas não encontradas: ${faltando.join(', ')}. Recebidas: ${header.join(', ')}`);
  }
  const v = (row, nome) => (col[nome] >= 0 && row[col[nome]] != null ? String(row[col[nome]]).trim() : '');

  for (let i = 1; i < linhas.length; i++) {
    const row = linhas[i];
    const tid = normId_(v(row, 'ticket_id'));
    if (!tid) continue;
    const r = {
      subject:  v(row, 'ticket_subject'),
      status:   v(row, 'status').toLowerCase(),
      abertura: parseDt_(v(row, 'open_at'), v(row, 'hora_open')),
      resposta: parseDt_(v(row, 'answered_at'), v(row, 'answered_hora')),
      mensagem: parseDt_(v(row, 'message_at'), v(row, 'message_hora')),
      msgBruta: v(row, 'message_at'),   // com segundos: desempata mensagens do mesmo minuto
      sid:      normId_(v(row, 'sender_id')),
      psid:     normId_(v(row, 'previous_sender_id')),
      sender:   v(row, 'sender'),
      tipo:     v(row, 'sender_type'),
      emissor:  v(row, 'issuer_type'),
      consult:  v(row, 'consultant'),
      office:   v(row, 'office'),
    };
    const tDt = v(row, 'last_transference');
    if (tDt) {
      r.transf = {
        dt:       fmtDt_(parseDt_(tDt)),
        bruta:    tDt,                  // com segundos, para comparar com a última mensagem
        de:       v(row, 'transfer_from_department'),
        para:     v(row, 'transfer_to_department'),
        areaDe:   v(row, 'transfer_from_area'),
        areaPara: v(row, 'transfer_to_area'),
        tipo:     v(row, 'transfer_type'),
      };
    }
    if (!grupos.has(tid)) grupos.set(tid, []);
    grupos.get(tid).push(r);
  }
  return grupos;
}

/** Mensagens agrupadas -> lista de registros consolidados (um por ticket). */
function consolidar_(grupos, agora) {
  const out = [];
  for (const [tid, grp] of grupos) {
    // ordena por data da mensagem, sem data no fim (igual ao sort_values na_position="last")
    grp.sort((a, b) => ((a.mensagem ?? Infinity) - (b.mensagem ?? Infinity))
      || (a.msgBruta < b.msgBruta ? -1 : a.msgBruta > b.msgBruta ? 1 : 0));   // mesmo minuto: pelos segundos

    // answered_at do Metabase é it.updated_at (última atualização), não a 1ª resposta.
    // 1ª resposta = primeira mensagem do BackOffice (sender_type = Admin) que responde ao consultor:
    //   ticket aberto pelo consultor -> 1ª msg do BO, contada da abertura;
    //   ticket aberto pelo próprio BO (issuer_type = Admin) -> 1ª msg do BO DEPOIS da 1ª msg do
    //   consultor, contada dessa msg do consultor (senão a abertura do BO entraria como 0 min);
    //   sem msg do consultor -> sem tempo de resposta.
    const temTipo = grp.some(r => r.tipo);
    const doBO = grp.filter(r => r.tipo === 'Admin');
    const abertura = minimo_(grp.map(r => r.abertura));
    const abertoPeloBO = primeiroNaoVazio_(grp.map(r => r.emissor)) === 'Admin';
    let resposta = null, inicioResposta = abertura;
    if (!temTipo) {
      resposta = minimo_(grp.map(r => r.resposta));
    } else if (!abertoPeloBO) {
      resposta = minimo_(doBO.map(r => r.mensagem));
    } else {
      const iCons = grp.findIndex(r => r.tipo === 'Consultant' && r.mensagem != null);
      const resp = iCons >= 0 ? grp.slice(iCons + 1).find(r => r.tipo === 'Admin' && r.mensagem != null) : null;
      inicioResposta = iCons >= 0 ? grp[iCons].mensagem : null;
      resposta = resp ? resp.mensagem : null;
    }
    let ultima = maximo_(grp.map(r => r.mensagem));
    if (ultima == null) ultima = minimo_(grp.map(r => r.resposta));

    const status = resolverStatus_(grp.map(r => r.status));
    // Não fechado: transferido depois da última mensagem -> vez do novo departamento;
    // senão, se a última mensagem é do BO, a vez é do consultor; senão, do BO.
    const ultimaMsg = grp.filter(r => r.mensagem != null).pop();
    const transf = (grp.find(r => r.transf) || {}).transf || null;
    const fila = status === 'closed' ? FILA.NENHUMA
      : (transf && transf.bruta > ((ultimaMsg && ultimaMsg.msgBruta) || '')) ? FILA.TRANSFERENCIA
      : (ultimaMsg && ultimaMsg.tipo === 'Admin') ? FILA.CONSULTOR : FILA.BO;

    let tProc = null;
    if (abertura != null) {
      const fim = (status === 'open' || status === 'processing') ? agora : (ultima ?? abertura);
      tProc = round1_((fim - abertura) / 3600000);
    }
    // Em horas úteis (sem sábados, domingos e feriados nacionais), coerente com o SLA de 1 dia útil
    const tResp = (inicioResposta != null && resposta != null && resposta >= inicioResposta)
      ? round1_(horasUteis_(inicioResposta, resposta)) : null;

    out.push({
      tid:        Number(tid) || tid,
      assunto:    primeiroNaoVazio_(grp.map(r => r.subject)),
      status,
      abertura:   fmtDt_(abertura),
      resposta:   fmtDt_(resposta),
      ultima:     fmtDt_(ultima),
      tProc,
      tResp,
      nMsg:       grp.length,
      nTrocas:    grp.filter(r => r.psid !== '' && r.psid !== r.sid).length,
      analista:   ultimoNaoVazio_((temTipo ? doBO : grp).map(r => r.sender)),
      consultor:  ultimoNaoVazio_(grp.map(r => r.consult)),
      escritorio: ultimoNaoVazio_(grp.map(r => r.office)),
      fila,
      transf:     transf && { dt: transf.dt, de: transf.de, para: transf.para, areaDe: transf.areaDe, areaPara: transf.areaPara, tipo: transf.tipo },
    });
  }
  return out;
}

/** Tempo em processo dos não fechados anda com o relógio, inclusive nos tickets mantidos. */
function recalcularEspera_(base, agora) {
  for (const rec of base.values()) {
    if ((rec.status === 'open' || rec.status === 'processing') && rec.abertura) {
      rec.tProc = round1_((agora - parseDt_(rec.abertura)) / 3600000);
    }
  }
}

/**
 * Aguardando o BO: não fechado e (última mensagem do consultor, ou transferido de assunto
 * depois da última mensagem). Mesma regra de refBO() no Regras.html.
 */
function aguardandoBO_(rec) {
  return rec.status !== 'closed' && (rec.fila === FILA.BO || rec.fila === FILA.TRANSFERENCIA);
}

/** closed > processing > open — o status mais crítico vence (igual ao Python). */
function resolverStatus_(lista) {
  const set = new Set(lista);
  for (const p of ['closed', 'processing', 'open']) if (set.has(p)) return p;
  return lista[lista.length - 1] || '';
}


// ═══════════════════════════════ Formato do JSON ═══════════════════════════
// Textos repetidos viram índices em listas para manter o JSON pequeno.
// Linha de ticket:
//   [0 ticket_id, 1 assunto, 2 status, 3 abertura, 4 primeira_resposta_BO (ao consultor), 5 ultima_mensagem,
//    6 tempo_processo_h (corridas), 7 tempo_resposta_h (horas úteis), 8 n_mensagens, 9 trocas_de_responsavel,
//    10 analista (último remetente Admin), 11 consultor, 12 escritorio, 13 fila (FILA),
//    — só quando o ticket foi transferido de assunto —
//    14 data_transferencia, 15 assunto_origem, 16 assunto_destino, 17 area_origem, 18 area_destino, 19 tipo]

function codificar_(base, agora) {
  const dic = { assuntos: new Dicionario_(), analistas: new Dicionario_(), consultores: new Dicionario_(),
                escritorios: new Dicionario_(), departamentos: new Dicionario_(), areas: new Dicionario_(),
                tiposTransf: new Dicionario_() };
  const statusOrdem = STATUS_BASE.slice();
  const t = [];
  for (const r of base.values()) {
    if (r.status && statusOrdem.indexOf(r.status) < 0) statusOrdem.push(r.status);
    const row = [r.tid, dic.assuntos.idx(r.assunto), statusOrdem.indexOf(r.status), r.abertura, r.resposta, r.ultima,
                 r.tProc, r.tResp, r.nMsg, r.nTrocas, dic.analistas.idx(r.analista), dic.consultores.idx(r.consultor),
                 dic.escritorios.idx(r.escritorio), r.fila];
    if (r.transf) {
      row.push(r.transf.dt, dic.assuntos.idx(r.transf.de), dic.assuntos.idx(r.transf.para),
               dic.areas.idx(r.transf.areaDe), dic.areas.idx(r.transf.areaPara), dic.tiposTransf.idx(r.transf.tipo));
    }
    t.push(row);
  }
  return {
    versao: VERSAO_DADOS,
    geradoEm: fmtDt_(agora),
    inicio: inicioCarga_(),
    slaH: SLA_ALERTA_H,
    statusOrdem,
    assuntos: dic.assuntos.lista,
    analistas: dic.analistas.lista,
    consultores: dic.consultores.lista,
    escritorios: dic.escritorios.lista,
    deptPorAssunto: dic.assuntos.lista.map(a => dic.departamentos.idx(DEPARTAMENTOS[a] || 'Sem Departamento')),
    departamentos: dic.departamentos.lista,
    areas: dic.areas.lista,
    tiposTransf: dic.tiposTransf.lista,
    t,
  };
}

/** Inverso de codificar_: payload salvo -> Map ticket_id -> registro. */
function decodificar_(p) {
  const base = new Map();
  if (!p || !p.t || p.versao !== VERSAO_DADOS) return base;
  const at = (lista, i) => (i >= 0 ? lista[i] : '');
  for (const x of p.t) {
    base.set(x[0], {
      tid: x[0], assunto: at(p.assuntos, x[1]), status: p.statusOrdem[x[2]] || '',
      abertura: x[3], resposta: x[4], ultima: x[5], tProc: x[6], tResp: x[7], nMsg: x[8], nTrocas: x[9],
      analista: at(p.analistas, x[10]), consultor: at(p.consultores, x[11]), escritorio: at(p.escritorios, x[12]),
      fila: x[13],
      transf: x.length > 14 ? {
        dt: x[14], de: at(p.assuntos, x[15]), para: at(p.assuntos, x[16]),
        areaDe: at(p.areas, x[17]), areaPara: at(p.areas, x[18]), tipo: at(p.tiposTransf, x[19]),
      } : null,
    });
  }
  return base;
}

/** Valor -> índice numa lista. '' vira -1. */
function Dicionario_() { this.lista = []; this.map = new Map(); }
Dicionario_.prototype.idx = function (s) {
  if (!s) return -1;
  if (!this.map.has(s)) { this.map.set(s, this.lista.length); this.lista.push(s); }
  return this.map.get(s);
};


// ═══════════════════════════════ Helpers ═══════════════════════════════════

/** "1 407 109" / "1.407.109" / "1407109.0" -> "1407109" */
function normId_(s) {
  if (!s) return '';
  if (/^\d+\.0+$/.test(s)) return s.replace(/\.0+$/, '');
  return s.replace(/[\s. ](?=\d{3}(\D|$))/g, '').trim();
}

/**
 * Converte data (+ hora opcional) para epoch ms. Aceita:
 *   "2026-04-01T07:07:00Z" / "...-03:00"  (instante absoluto)
 *   "2026-04-01 07:07[:ss]"               (horário de Brasília)
 *   "01/04/2026 07:07" ou "01/04/2026" + hora "07:07"
 */
function parseDt_(data, hora) {
  if (!data) return null;
  let s = hora ? `${data} ${hora}` : data;
  s = s.replace(/\s+/g, ' ').trim();

  if (/[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})$/.test(s) && /^\d{4}-/.test(s)) {
    const d = new Date(s.replace(' ', 'T'));
    return isNaN(d) ? null : d.getTime();
  }
  let m = s.match(/^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/);
  if (m) return localMs_(+m[1], +m[2], +m[3], +(m[4] || 0), +(m[5] || 0));
  m = s.match(/^(\d{2})\/(\d{2})\/(\d{4})(?: (\d{1,2}):(\d{2}))?/);
  if (m) return localMs_(+m[3], +m[2], +m[1], +(m[4] || 0), +(m[5] || 0));
  return null;
}

/**
 * Data/hora de Brasília -> epoch ms. Brasília é UTC-3 fixo (sem horário de verão
 * desde 2019); conta direta porque Utilities.parseDate é lento em ~165 mil linhas.
 */
function localMs_(y, mo, d, h, mi) {
  return Date.UTC(y, mo - 1, d, h + 3, mi);
}

/** Pares [início, fim] mês a mês entre duas datas yyyy-MM-dd. */
function meses_(inicio, fim) {
  const out = [];
  let [y, m] = inicio.split('-').map(Number);
  let ini = inicio;
  while (ini <= fim) {
    const ultimo = new Date(Date.UTC(y, m, 0)).toISOString().slice(0, 10);
    out.push([ini, ultimo < fim ? ultimo : fim]);
    m++; if (m > 12) { m = 1; y++; }
    ini = `${y}-${String(m).padStart(2, '0')}-01`;
  }
  return out;
}

/** epoch ms -> "yyyy-MM-dd HH:mm" em Brasília (UTC-3, ver localMs_). */
function fmtDt_(ms) {
  return ms == null ? '' : new Date(ms - 3 * 3600000).toISOString().slice(0, 16).replace('T', ' ');
}
function round1_(x) { return Math.round(x * 10) / 10; }

// ── Dias úteis (mesma regra do Regras.html: feriados nacionais fixos + Sexta-feira Santa) ──
const FERIADOS_FIXOS = ['01-01', '04-21', '05-01', '09-07', '10-12', '11-02', '11-15', '11-20', '12-25'];
const _feriados = {};
function feriados_(ano) {
  if (_feriados[ano]) return _feriados[ano];
  const a = ano % 19, b = Math.floor(ano / 100), c = ano % 100, d = Math.floor(b / 4), e = b % 4;
  const f = Math.floor((b + 8) / 25), g = Math.floor((b - f + 1) / 3), h = (19 * a + b - d - g + 15) % 30;
  const i = Math.floor(c / 4), k = c % 4, l = (32 + 2 * e + 2 * i - h - k) % 7, m = Math.floor((a + 11 * h + 22 * l) / 451);
  const mes = Math.floor((h + l - 7 * m + 114) / 31), dia = ((h + l - 7 * m + 114) % 31) + 1;
  const sexta = new Date(Date.UTC(ano, mes - 1, dia - 2)).toISOString().slice(0, 10);
  return _feriados[ano] = new Set(FERIADOS_FIXOS.map(md => `${ano}-${md}`).concat([sexta]));
}
const DIA_MS = 86400000, BRT_MS = 3 * 3600000;   // Brasília = UTC-3 (ver localMs_)
function diaUtilMs_(inicioDiaLocal) {
  const iso = new Date(inicioDiaLocal).toISOString().slice(0, 10), wd = new Date(inicioDiaLocal).getUTCDay();
  return wd !== 0 && wd !== 6 && !feriados_(+iso.slice(0, 4)).has(iso);
}
/** Horas entre dois instantes (epoch ms), descontando as horas de sábados, domingos e feriados. */
function horasUteis_(ini, fim) {
  const a = ini - BRT_MS, b = fim - BRT_MS;   // relógio de Brasília
  let ms = 0;
  for (let dia = Math.floor(a / DIA_MS) * DIA_MS; dia < b; dia += DIA_MS) {
    if (diaUtilMs_(dia)) ms += Math.max(0, Math.min(b, dia + DIA_MS) - Math.max(a, dia));
  }
  return ms / 3600000;
}
function minimo_(arr) { const f = arr.filter(x => x != null); return f.length ? Math.min.apply(null, f) : null; }
function maximo_(arr) { const f = arr.filter(x => x != null); return f.length ? Math.max.apply(null, f) : null; }
function primeiroNaoVazio_(arr) { return arr.find(x => x) || ''; }
function ultimoNaoVazio_(arr) { for (let i = arr.length - 1; i >= 0; i--) if (arr[i]) return arr[i]; return ''; }


// ═══════════════════════════════ Relatório semanal ═════════════════════════

const ASSUNTO_PADRAO = 'Relatório semanal de tickets · BackOffice · {semana}';
const PLANILHA_NOME = 'BKO · Histórico semanal de tickets';
const PLANILHA_COLUNAS = ['Semana (início)', 'Semana (fim)', 'Gerado em', 'Abertos', 'Fechados', '% fechados',
  '1ª resposta mediana (h)', 'Transferidos', 'Aguardando BO', 'Atraso sem resposta', 'Atraso transferência'];

/** Chamada pelo Painel: destinatários e assunto do e-mail semanal. */
function getConfigRelatorio() {
  const props = PropertiesService.getScriptProperties();
  const id = props.getProperty('HISTORICO_SHEET_ID');
  return {
    destinatarios: props.getProperty('RELATORIO_DESTINATARIOS') || '',
    assunto: props.getProperty('RELATORIO_ASSUNTO') || ASSUNTO_PADRAO,
    urlPlanilha: id ? `https://docs.google.com/spreadsheets/d/${id}/edit` : '',
  };
}

/**
 * Chamada pelo Painel ao gerar o relatório: grava (ou atualiza) a linha da semana no Sheets.
 * resumo: {inicio, fim, abertos, fechados, pctFechados, respMedianaH, transferidos,
 *          aguardandoBO, atrasoResposta, atrasoTransferencia}
 */
function registrarSemana(resumo) {
  const props = PropertiesService.getScriptProperties();
  let planilha = null;
  const id = props.getProperty('HISTORICO_SHEET_ID');
  if (id) { try { planilha = SpreadsheetApp.openById(id); } catch (e) { /* apagada: recria */ } }
  if (!planilha) {
    planilha = SpreadsheetApp.create(PLANILHA_NOME);
    props.setProperty('HISTORICO_SHEET_ID', planilha.getId());
    const aba = planilha.getSheets()[0];
    aba.setName('Semanas');
    aba.getRange(1, 1, 1, PLANILHA_COLUNAS.length).setValues([PLANILHA_COLUNAS]).setFontWeight('bold');
    aba.setFrozenRows(1);
  }
  const aba = planilha.getSheets()[0];
  const linha = [resumo.inicio, resumo.fim, fmtDt_(Date.now()), resumo.abertos, resumo.fechados, resumo.pctFechados,
    resumo.respMedianaH, resumo.transferidos, resumo.aguardandoBO, resumo.atrasoResposta, resumo.atrasoTransferencia];
  // Upsert pela semana: gerar o relatório de novo atualiza a linha em vez de duplicar
  const inicios = aba.getLastRow() > 1 ? aba.getRange(2, 1, aba.getLastRow() - 1, 1).getDisplayValues().map(r => r[0]) : [];
  const i = inicios.indexOf(resumo.inicio);
  const n = i >= 0 ? i + 2 : aba.getLastRow() + 1;
  aba.getRange(n, 1, 1, 2).setNumberFormat('@');   // datas como texto yyyy-MM-dd
  aba.getRange(n, 1, 1, linha.length).setValues([linha]);
  return planilha.getUrl();
}


// ═══════════════════════════════ Armazenamento ═════════════════════════════
// JSON comprimido (gzip) em base64: ~6 MB de JSON viram ~1,6 MB no Drive.
// Uma cópia fica no CacheService (fatias de 90 KB, limite de 100 KB por chave)
// para as visitas não precisarem ler o Drive.

const CACHE_PREFIXO = 'dados_v1_';
const CACHE_FATIA = 90000;
const CACHE_SEGUNDOS = 21600;   // máximo do CacheService (6 h)

/** Chamada pela página (google.script.run): devolve o JSON em gzip + base64, ou ''. */
function getDados() {
  return lerCache_() || guardarCache_(lerArquivo_());
}

function salvarDados_(json) {
  const gz = Utilities.gzip(Utilities.newBlob(json, 'application/json'));
  const conteudo = Utilities.base64Encode(gz.getBytes());
  const props = PropertiesService.getScriptProperties();
  const id = props.getProperty('DADOS_FILE_ID');
  let salvo = false;
  if (id) {
    try { DriveApp.getFileById(id).setContent(conteudo); salvo = true; } catch (e) { /* arquivo apagado: recria */ }
  }
  if (!salvo) {
    const f = DriveApp.createFile(ARQUIVO_DADOS, conteudo, MimeType.PLAIN_TEXT);
    props.setProperty('DADOS_FILE_ID', f.getId());
  }
  guardarCache_(conteudo);
}

/** JSON salvo (texto), ou 'null' se ainda não houver dados. */
function lerDados_() {
  const conteudo = lerArquivo_();
  if (!conteudo) return 'null';
  const gz = Utilities.newBlob(Utilities.base64Decode(conteudo), 'application/x-gzip');
  return Utilities.ungzip(gz).getDataAsString('UTF-8');
}

function lerArquivo_() {
  const id = PropertiesService.getScriptProperties().getProperty('DADOS_FILE_ID');
  if (!id) return '';
  try { return DriveApp.getFileById(id).getBlob().getDataAsString() || ''; } catch (e) { return ''; }
}

function guardarCache_(conteudo) {
  if (!conteudo) return '';
  const fatias = {};
  const n = Math.ceil(conteudo.length / CACHE_FATIA);
  for (let i = 0; i < n; i++) fatias[CACHE_PREFIXO + i] = conteudo.slice(i * CACHE_FATIA, (i + 1) * CACHE_FATIA);
  fatias[CACHE_PREFIXO + 'n'] = String(n);
  try { CacheService.getScriptCache().putAll(fatias, CACHE_SEGUNDOS); } catch (e) { console.log('Cache não gravado: ' + e); }
  return conteudo;
}

function lerCache_() {
  const cache = CacheService.getScriptCache();
  const n = Number(cache.get(CACHE_PREFIXO + 'n'));
  if (!n) return '';
  const chaves = Array.from({ length: n }, (_, i) => CACHE_PREFIXO + i);
  const fatias = cache.getAll(chaves);
  if (chaves.some(k => fatias[k] == null)) return '';   // alguma fatia expirou
  return chaves.map(k => fatias[k]).join('');
}
