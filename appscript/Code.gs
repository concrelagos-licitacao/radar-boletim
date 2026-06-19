/**
 * Concrelagos — Histórico Comercial (Google Apps Script Web App)
 * ------------------------------------------------------------------
 * Dashboard SOMENTE LEITURA que espelha a planilha comercial
 * "CONTROLE - LICITAÇÕES" (abas PREGOES / GANHAS / ADITIVOS).
 *
 * Por que não precisa de senha:
 *   Implantar como Web App com  Executar como: EU (dono)  +  Acesso: Qualquer
 *   pessoa com o link. O script lê a planilha como dono (a planilha continua
 *   privada); quem abre o link NÃO faz login e NÃO vê a planilha, só o painel.
 *
 * É só leitura de propósito: como o link é público, um formulário de gravação
 * deixaria qualquer um escrever na planilha. Para gravar, use a planilha direto.
 */

var SHEET_ID  = '1FjmN8EDKQRcBflL7VOp7MzB6PeKNO0hcXLUUAoLbBbg';
var CACHE_SEG = 60;

// Empresas do grupo -> nome curto + cor da logo (igual ao site Streamlit)
var EMP_DOMINIO = ['Concrelagos', 'Pedreira Imboassica', 'Apolo', 'Pedreira Outeiro', 'Pedreira Bangu', 'IPEPAM', 'Outras'];
var EMP_CORES   = ['#C28E2C', '#3A4149', '#D32F2F', '#F2A900', '#8E2430', '#F39C12', '#9AA0A6'];

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('📊 Histórico')
    .addItem('Abrir dashboard', 'abrirDashboard')
    .addToUi();
}

function doGet(e) {
  return HtmlService.createHtmlOutputFromFile('Dashboard')
    .setTitle('Concrelagos — Histórico Comercial')
    .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL);
}

function abrirDashboard() {
  var html = HtmlService.createHtmlOutputFromFile('Dashboard')
    .setWidth(1500).setHeight(920);
  SpreadsheetApp.getUi().showModalDialog(html, 'Histórico Comercial');
}

// ---------- helpers ----------
function numBR(v) {
  if (v === null || v === undefined) return 0;
  var s = String(v).trim().replace(/R\$/g, '').replace(/\s/g, '');
  if (!s) return 0;
  s = s.replace(/\./g, '').replace(/,/g, '.');   // milhar BR vira nada; decimal vira ponto
  var n = parseFloat(s);
  return isNaN(n) ? 0 : n;
}
function findCol(header, sub) {            // por substring (tolerante a nome exato)
  sub = sub.toUpperCase();
  for (var i = 0; i < header.length; i++) {
    if (String(header[i]).toUpperCase().indexOf(sub) >= 0) return i;
  }
  return -1;
}
function mapEmp(nome) {
  var n = String(nome || '').toUpperCase();
  if (n.indexOf('CONCRELAGOS') >= 0) return 'Concrelagos';
  if (n.indexOf('IMBOASSICA')  >= 0) return 'Pedreira Imboassica';
  if (n.indexOf('APOLO')       >= 0) return 'Apolo';
  if (n.indexOf('OUTEIRO')     >= 0) return 'Pedreira Outeiro';
  if (n.indexOf('BANGU')       >= 0) return 'Pedreira Bangu';
  if (n.indexOf('IPEPAM')      >= 0) return 'IPEPAM';
  return 'Outras';
}
function parseDateBR(v) {
  if (!v) return null;
  if (Object.prototype.toString.call(v) === '[object Date]') return v;
  var m = String(v).trim().match(/(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{2,4})/);
  if (!m) return null;
  var yy = parseInt(m[3], 10); if (yy < 100) yy += 2000;
  var d = new Date(yy, parseInt(m[2], 10) - 1, parseInt(m[1], 10));
  d.setHours(0, 0, 0, 0);
  return isNaN(d.getTime()) ? null : d;
}

// ---------- dados ----------
function getDados() {
  var cache = CacheService.getScriptCache();
  var hit = cache.get('hist');
  if (hit) { try { return JSON.parse(hit); } catch (e) {} }

  var ss = SpreadsheetApp.openById(SHEET_ID);
  var sheets = ss.getSheets();
  var tz = Session.getScriptTimeZone();
  var abas = [], pregT = null, ganhasT = null;

  for (var s = 0; s < sheets.length; s++) {
    var ws = sheets[s];
    var vals = ws.getDataRange().getValues();
    var header = vals.length ? vals[0].map(function (h) { return String(h).replace(/\s+/g, ' ').trim(); }) : [];
    var rows = [];
    for (var r = 1; r < vals.length; r++) {
      var row = vals[r], algum = false;
      for (var c = 0; c < row.length; c++) { if (String(row[c]).trim() !== '') { algum = true; break; } }
      if (!algum) continue;
      var norm = [];
      for (var c2 = 0; c2 < header.length; c2++) {
        var cell = row[c2];
        if (Object.prototype.toString.call(cell) === '[object Date]') {
          norm.push(Utilities.formatDate(cell, tz, 'dd/MM/yyyy'));
        } else {
          norm.push(cell === null || cell === undefined ? '' : String(cell));
        }
      }
      rows.push(norm);
    }
    var entry = { titulo: ws.getName().trim(), header: header, rows: rows };
    abas.push(entry);
    var TU = entry.titulo.toUpperCase();
    if (TU.indexOf('PREG') >= 0 && !pregT) pregT = entry;
    if (TU.indexOf('GANHAS') >= 0 && !ganhasT) ganhasT = entry;
  }

  var out = {
    ok: true, abas: abas, kpis: null, porAno: [],
    volAnoEmp: null, valAnoEmp: null, topClientes: [], vencendo: [],
    empDominio: EMP_DOMINIO, empCores: EMP_CORES
  };

  if (pregT) {
    var h = pregT.header, rows = pregT.rows;
    var iRes = findCol(h, 'RESULTADO'), iEmp = findCol(h, 'EMPRESA'),
        iAno = findCol(h, 'ANO'), iVol = findCol(h, 'CONTRATADO'),
        iVal = findCol(h, 'VALOR TOTAL'), iCli = findCol(h, 'CLIENTE');
    var vit = 0, der = 0, vol = 0, anoAgg = {}, volAE = {}, valAE = {}, cliAgg = {};
    for (var i = 0; i < rows.length; i++) {
      var rr = rows[i];
      var res = (iRes >= 0 ? String(rr[iRes]) : '').toUpperCase().trim();
      var isVit = res.indexOf('VIT') === 0;
      if (isVit) vit++; else if (res.indexOf('DERROT') === 0) der++;
      var vraw = iVol >= 0 ? numBR(rr[iVol]) : 0; vol += vraw;
      var valr = iVal >= 0 ? numBR(rr[iVal]) : 0;
      var ano = iAno >= 0 ? parseInt(String(rr[iAno]).replace(/\D/g, ''), 10) : NaN;
      var emp = mapEmp(iEmp >= 0 ? rr[iEmp] : '');
      if (!isNaN(ano)) {
        if (!anoAgg[ano]) anoAgg[ano] = { disp: 0, vit: 0 };
        anoAgg[ano].disp++; if (isVit) anoAgg[ano].vit++;
        if (!volAE[emp]) volAE[emp] = {}; volAE[emp][ano] = (volAE[emp][ano] || 0) + vraw;
        if (!valAE[emp]) valAE[emp] = {}; valAE[emp][ano] = (valAE[emp][ano] || 0) + valr;
      }
      if (iCli >= 0) { var cli = String(rr[iCli]).trim(); if (cli) cliAgg[cli] = (cliAgg[cli] || 0) + vraw; }
    }
    out.kpis = { pregoes: rows.length, vitorias: vit, derrotas: der, volume: Math.round(vol),
                 taxa: (vit / Math.max(vit + der, 1)) * 100 };
    var anos = Object.keys(anoAgg).map(Number).sort(function (a, b) { return a - b; });
    out.porAno = anos.map(function (a) { return { ano: a, disputados: anoAgg[a].disp, vitorias: anoAgg[a].vit }; });
    out.volAnoEmp = { anos: anos, datasets: EMP_DOMINIO.map(function (emp, idx) {
      return { empresa: emp, cor: EMP_CORES[idx], data: anos.map(function (a) { return Math.round((volAE[emp] && volAE[emp][a]) || 0); }) };
    }) };
    out.valAnoEmp = { anos: anos, datasets: EMP_DOMINIO.map(function (emp, idx) {
      return { empresa: emp, cor: EMP_CORES[idx], data: anos.map(function (a) { return Math.round((valAE[emp] && valAE[emp][a]) || 0); }) };
    }) };
    var cliArr = Object.keys(cliAgg).map(function (k) { return { cliente: k, vol: Math.round(cliAgg[k]) }; });
    cliArr.sort(function (a, b) { return b.vol - a.vol; });
    out.topClientes = cliArr.slice(0, 8);
  }

  if (ganhasT) {
    var hg = ganhasT.header, rg = ganhasT.rows;
    var iVald = findCol(hg, 'VALIDADE'), iCliG = findCol(hg, 'CLIENTE'), iPreg = findCol(hg, 'PREG');
    var hoje = new Date(); hoje.setHours(0, 0, 0, 0);
    var lim = new Date(hoje.getTime() + 90 * 86400000);
    for (var g = 0; g < rg.length; g++) {
      var d = parseDateBR(iVald >= 0 ? rg[g][iVald] : '');
      if (d && d >= hoje && d <= lim) {
        out.vencendo.push({
          cliente: iCliG >= 0 ? String(rg[g][iCliG]) : '',
          pregao: iPreg >= 0 ? String(rg[g][iPreg]) : '',
          validade: Utilities.formatDate(d, tz, 'dd/MM/yyyy'),
          dias: Math.round((d - hoje) / 86400000)
        });
      }
    }
    out.vencendo.sort(function (a, b) { return a.dias - b.dias; });
  }

  try { cache.put('hist', JSON.stringify(out), CACHE_SEG); } catch (e) { /* payload > 100KB: segue sem cache */ }
  return out;
}
