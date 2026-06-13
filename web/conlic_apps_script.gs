/**
 * ============================================================================
 *  ConLicitação Inbox — Google Apps Script (Web App)
 *  Concrelagos Intelligence Hub — 4ª fonte (boletim ConLicitação)
 * ============================================================================
 *
 *  O QUE ESTE SCRIPT FAZ
 *  ---------------------
 *  Recebe um POST (vindo do bookmarklet que você roda na tela do boletim
 *  ConLicitação) e grava cada licitação numa linha da aba "ConLic Inbox" do
 *  seu Google Sheet. Depois o scraper.py lê as linhas com processado_em vazio,
 *  processa, e marca processado_em — fechando o transporte automático.
 *
 *  COMO INSTALAR (passo a passo, faça UMA vez)
 *  -------------------------------------------
 *   1) Abra https://script.google.com  ->  Novo projeto.
 *   2) Apague o conteúdo do Code.gs e COLE este arquivo inteiro.
 *   3) Troque os DOIS placeholders no topo do código:
 *        - TOKEN     -> invente uma senha secreta (ex.: 32 caracteres aleatórios).
 *        - SHEET_ID  -> o ID da sua planilha. Ele está na URL do Sheet:
 *                       https://docs.google.com/spreadsheets/d/ESTE_PEDACO/edit
 *   4) Salve (ícone do disquete).
 *   5) Implantar  ->  Nova implantação.
 *        - Engrenagem (Tipo)  ->  "App da Web".
 *        - "Executar como"     ->  Eu (você mesmo).
 *        - "Quem pode acessar" ->  "Qualquer pessoa".
 *        - Implantar. Autorize o acesso quando o Google pedir.
 *   6) Copie a "URL do app da Web" (termina em /exec).
 *   7) No bookmarklet, cole essa URL no campo SCRIPT_URL e cole o MESMO TOKEN
 *      no campo de token. Pronto: clicar no bookmarklet envia o boletim pra cá.
 *
 *  DICA DE TESTE: depois de implantar, clique no bookmarklet uma vez e confira
 *  se as linhas aparecem na aba "ConLic Inbox". Se aparecer {ok:false,error:"token"},
 *  o token do bookmarklet não bate com o TOKEN aqui.
 *
 *  SEGURANÇA: nunca compartilhe a URL /exec junto com o TOKEN. Quem tiver os dois
 *  pode gravar na sua aba. Se vazar, gere um TOKEN novo e re-implante.
 * ============================================================================
 */

// >>> TROQUE ESTE VALOR <<< invente uma senha secreta e use a MESMA no bookmarklet.
const TOKEN = "COLE_AQUI_UM_TOKEN_SECRETO";

// >>> TROQUE ESTE VALOR <<< ID da planilha (o trecho da URL entre /d/ e /edit).
const SHEET_ID = "COLE_AQUI_O_ID_DA_PLANILHA";

// Nome da aba de destino. NÃO mude sem alinhar com o scraper.py.
const ABA = "ConLic Inbox";

// Header EXATO do CONTRATO (ordem fixa). É a 1ª linha da aba.
const HEADER = [
  "recebido_em",
  "nc",
  "edital",
  "orgao",
  "cidade",
  "uf",
  "datas",
  "data_abertura",
  "data_encerramento",
  "valor_estimado",
  "objeto",
  "status",
  "processado_em"
];

/**
 * doPost — ponto de entrada do Web App.
 * Recebe { token, licitacoes: [ {<objeto do contrato>}, ... ] } em JSON.
 */
function doPost(e) {
  try {
    // 1) parse do corpo do POST
    //    Guarda contra POST sem corpo (e/e.postData/contents ausentes): o bookmarklet
    //    manda text/plain, mas um "ping" ou requisição malformada pode vir sem body.
    var body;
    var corpo = (e && e.postData && e.postData.contents) ? e.postData.contents : "";
    if (!corpo) {
      return _json({ ok: false, error: "json", detalhe: "corpo vazio" });
    }
    try {
      body = JSON.parse(corpo);
    } catch (err) {
      return _json({ ok: false, error: "json", detalhe: String(err) });
    }

    // 2) validação do token — se inválido, NÃO grava.
    if (!body || body.token !== TOKEN) {
      return _json({ ok: false, error: "token" });
    }

    var licitacoes = (body && body.licitacoes) || [];
    if (!Array.isArray(licitacoes) || licitacoes.length === 0) {
      return _json({ ok: true, gravadas: 0 });
    }

    // 3) abre a planilha por ID e pega/cria a aba "ConLic Inbox"
    var ss = SpreadsheetApp.openById(SHEET_ID);
    var sh = ss.getSheetByName(ABA);
    if (!sh) {
      sh = ss.insertSheet(ABA);
      sh.getRange(1, 1, 1, HEADER.length).setValues([HEADER]);
    } else if (sh.getLastRow() === 0 || _a1Vazio(sh)) {
      // aba existe mas está vazia (ou A1 em branco / header nunca escrito) — escreve o header.
      // NUNCA sobrescreve uma aba que já tem dados; só inicializa se estiver vazia.
      sh.getRange(1, 1, 1, HEADER.length).setValues([HEADER]);
    }

    // 4) monta as linhas na ORDEM do header e grava em lote
    var agora = new Date().toISOString(); // TIMESTAMP_ISO
    var linhas = [];
    for (var i = 0; i < licitacoes.length; i++) {
      var lic = licitacoes[i] || {};
      linhas.push([
        agora,                       // recebido_em
        _s(lic.nc),                  // nc
        _s(lic.edital),              // edital (COM prefixo de modalidade)
        _s(lic.orgao),               // orgao
        _s(lic.cidade),             // cidade (pode vir "Cidade - UF")
        _s(lic.uf),                  // uf (pode vir "")
        _s(lic.datas),               // datas (blob cru)
        _s(lic.data_abertura),       // data_abertura (opcional, geralmente "")
        _s(lic.data_encerramento),   // data_encerramento (opcional, geralmente "")
        _s(lic.valor_estimado),      // valor_estimado ("R$ ..." ou "")
        _s(lic.objeto),              // objeto
        _s(lic.status),              // status ("" salvo ANULADA/REVOGADA)
        ""                           // processado_em (vazio — o scraper preenche)
      ]);
    }

    if (linhas.length > 0) {
      var primeira = sh.getLastRow() + 1;
      sh.getRange(primeira, 1, linhas.length, HEADER.length).setValues(linhas);
    }

    // 5) resposta de sucesso
    return _json({ ok: true, gravadas: linhas.length });
  } catch (err) {
    // erro inesperado — devolve diagnóstico sem vazar segredos
    return _json({ ok: false, error: "interno", detalhe: String(err) });
  }
}

/**
 * doGet — opcional. Permite abrir a URL /exec no navegador só para um "ping"
 * rápido e confirmar que a implantação está no ar (NÃO grava nada).
 */
function doGet() {
  return _json({ ok: true, servico: "ConLic Inbox", aba: ABA });
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// Converte qualquer campo ausente/null/undefined em "" e força string.
function _s(v) {
  return (v === null || v === undefined) ? "" : String(v);
}

// true se a célula A1 estiver em branco (aba sem header escrito ainda).
function _a1Vazio(sh) {
  return String(sh.getRange(1, 1).getValue() || "").trim() === "";
}

// Resposta JSON padronizada do ContentService.
function _json(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}
