/* ===========================================================================
 * ConLicitação -> Concrelagos Intelligence Hub  —  BOOKMARKLET (zero IA)
 * ---------------------------------------------------------------------------
 * Roda na página do boletim ConLicitação (você já logado), lê o DOM e extrai
 * as licitações conforme o CONTRATO DE DADOS do Hub. Não usa XPath nem posição:
 * lê POR RÓTULO no innerText de cada card. Sempre copia o JSON para a área de
 * transferência (modo "colar"); opcionalmente faz POST para o Apps Script.
 *
 * Contrato (1 objeto por licitação), chaves lidas direto por
 * scraper._normalizar_conlicitacao(lic):
 *   objeto, nc, cidade, uf, edital, valor_estimado, orgao, datas,
 *   data_abertura, data_encerramento, status
 *
 * DOM real: cada licitação é <div class="card-body p-4">. O innerText traz os
 * rótulos em linhas próprias (Objeto:, Datas:, Órgão:, Cidade:, Edital:,
 * Valor Estimado:, Nº Conlicitação:). Há ruído de ícones ("info",
 * "content_copy", "location_on", "CAPAG X") e um rodapé "Atualizada em: ...".
 * =========================================================================== */

/* (A) ====================== VERSÃO LEGÍVEL (comentada) ====================== */

(function () {
  "use strict";

  // ---- Configuração opcional (modo Apps Script automático) ----------------
  // Para ligar o envio automático, troque os dois placeholders abaixo.
  // Se SCRIPT_URL ficar com o placeholder, o bookmarklet só copia para o clipboard.
  var SCRIPT_URL = "COLE_AQUI_A_URL_DO_APPS_SCRIPT"; // ex.: https://script.google.com/macros/s/AKfy.../exec
  var TOKEN = "COLE_AQUI_O_TOKEN"; // mesmo token configurado no Apps Script

  // ---- Lista FIXA de rótulos do contrato (na ordem em que aparecem) -------
  // Usada para "fatiar" o innerText: o valor de um rótulo vai até o próximo rótulo.
  var ROTULOS = [
    "Objeto:",
    "Datas:",
    "Órgão:",
    "Cidade:",
    "Edital:",
    "Valor Estimado:",
    "Nº Conlicitação:"
  ];

  // Linhas puramente de ruído de ícones / UI que devem ser descartadas.
  function ehLinhaRuido(linha) {
    var l = linha.trim();
    if (l === "") return true;
    if (l === "info") return true;
    if (l === "content_copy") return true;
    if (l === "location_on") return true;
    if (l === "expand_more") return true;
    if (l === "expand_less") return true;
    if (/^CAPAG\b/i.test(l)) return true; // "CAPAG C", "CAPAG B" etc.
    return false;
  }

  // Remove sufixos de ícone colados no fim de um valor (ex.: "...Limeirainfo",
  // "...19025935content_copy", "...Município de Xinfo").
  function limparSufixoIcone(txt) {
    var s = String(txt == null ? "" : txt);
    // remove repetidamente sufixos conhecidos colados no fim
    var mudou = true;
    while (mudou) {
      mudou = false;
      var antes = s;
      s = s.replace(/(info|content_copy|location_on|expand_more|expand_less)\s*$/i, "");
      if (s !== antes) mudou = true;
    }
    return s.trim();
  }

  // Marcador da ÁREA DE BOTÕES (Ver mais informações / Ações / Ver itens / ...):
  // tudo a partir daqui é UI, não dado. Quando um campo (ex.: Edital sem Valor) absorve
  // essas linhas, paramos de coletar no 1º marcador.
  function ehParada(linha) {
    return /^(Ver mais informações|Ações|Ver itens|Baixar Edital|Resumo do Edital|Pergunte ao Edital|Gerenciar licitação|Anotações|Nenhum edital|BETA|mic|send)\b/i.test(String(linha).trim());
  }

  // Junta as linhas de um valor (já sem ruído) num texto único e limpo.
  function montarValor(linhas) {
    var limpas = [];
    for (var i = 0; i < linhas.length; i++) {
      if (ehParada(linhas[i])) break; // entrou na área de botões -> para
      if (ehLinhaRuido(linhas[i])) continue;
      limpas.push(linhas[i].trim());
    }
    var txt = limpas.join(" ").replace(/\s+/g, " ").trim();
    return limparSufixoIcone(txt);
  }

  // Dado o innerText de um card, devolve um mapa { "Objeto:": [linhas], ... }.
  // Estratégia: percorre as linhas; quando encontra um rótulo conhecido,
  // tudo até o próximo rótulo conhecido pertence ao valor dele.
  function fatiarPorRotulo(innerText) {
    var linhas = String(innerText || "").split(/\r?\n/);
    var mapa = {};
    var rotuloAtual = null;
    for (var i = 0; i < linhas.length; i++) {
      var bruta = linhas[i];
      var t = bruta.trim();
      // É um rótulo do contrato? Aceita rótulo SOZINHO na linha (valor nas linhas
      // seguintes, ex.: "Objeto:") OU rótulo + valor na MESMA linha (ex.:
      // "Nº Conlicitação: 19025935content_copy" — o ConLicitação põe o nº inline).
      var ehRotulo = false;
      for (var r = 0; r < ROTULOS.length; r++) {
        var rot = ROTULOS[r];
        if (t === rot) {
          rotuloAtual = rot;
          if (!mapa[rotuloAtual]) mapa[rotuloAtual] = [];
          ehRotulo = true;
          break;
        }
        if (t.indexOf(rot) === 0) { // rótulo + valor na mesma linha
          rotuloAtual = rot;
          if (!mapa[rotuloAtual]) mapa[rotuloAtual] = [];
          var resto = t.substring(rot.length);
          if (resto.trim() !== "") mapa[rotuloAtual].push(resto);
          ehRotulo = true;
          break;
        }
      }
      if (ehRotulo) continue;
      // Linha de valor: pertence ao último rótulo visto.
      if (rotuloAtual) mapa[rotuloAtual].push(bruta);
    }
    return mapa;
  }

  // Tira o rodapé "Atualizada em: ..." de dentro de um valor (cai no fim do card).
  function removerAtualizadaEm(txt) {
    return String(txt || "").replace(/Atualizada em:.*$/i, "").trim();
  }

  // Extrai só os dígitos do Nº ConLicitação.
  function soDigitos(txt) {
    var m = String(txt || "").match(/\d+/);
    return m ? m[0] : "";
  }

  // Converte um card (elemento DOM) num objeto do CONTRATO; "" se faltar nc.
  function cardParaObjeto(card) {
    var mapa = fatiarPorRotulo(card.innerText);

    var objeto = montarValor(mapa["Objeto:"] || []);
    objeto = removerAtualizadaEm(objeto); // por segurança, se cair junto

    var datas = montarValor(mapa["Datas:"] || []);
    var orgao = montarValor(mapa["Órgão:"] || []);
    var cidade = montarValor(mapa["Cidade:"] || []); // pode vir "Limeira - SP"
    var edital = montarValor(mapa["Edital:"] || []); // COM prefixo de modalidade
    // O valor é seguido pelos botões de ação (Ver itens, Baixar Edital, ...) que caem
    // no mesmo bloco; extrai SÓ o "R$ ..." para não poluir (senão o Hub lê valor=0).
    var valorBruto = montarValor(mapa["Valor Estimado:"] || []);
    var mValor = valorBruto.match(/R\$\s*[\d.,]+/);
    var valor = mValor ? mValor[0] : "";
    var ncBruto = montarValor(mapa["Nº Conlicitação:"] || []);
    var nc = soDigitos(ncBruto);

    if (!nc) return null; // sem nc não é licitação válida -> descarta

    // Monta o objeto EXATAMENTE com as chaves do contrato.
    return {
      objeto: objeto || "",
      nc: nc,
      cidade: cidade || "", // o Hub faz o split "Cidade - UF"
      uf: "", // a UF já vem dentro de "cidade"
      edital: edital || "",
      valor_estimado: valor || "",
      orgao: orgao || "",
      datas: datas || "", // bloco cru; o Hub extrai abertura/encerramento
      data_abertura: "",
      data_encerramento: "",
      status: "" // ANULADA/REVOGADA só apareceria se a página marcasse; o Hub filtra
    };
  }

  // ---- Seleção dos cards ---------------------------------------------------
  // Primário: div.card-body.p-4. Fallback robusto: qualquer elemento cujo
  // innerText contenha "Objeto:" E "Edital:" E "Conlicitação", pegando os
  // mais específicos (sem cards aninhados dentro de outro card selecionado).
  function coletarCards() {
    var primarios = Array.prototype.slice.call(
      document.querySelectorAll("div.card-body.p-4")
    );
    if (primarios.length > 0) return primarios;

    // Fallback: varre tudo e filtra por conteúdo.
    var todos = Array.prototype.slice.call(document.querySelectorAll("*"));
    var candidatos = todos.filter(function (el) {
      var t = el.innerText || "";
      return (
        t.indexOf("Objeto:") !== -1 &&
        t.indexOf("Edital:") !== -1 &&
        t.indexOf("Conlicitação") !== -1
      );
    });
    // Mantém só os mais específicos: descarta um candidato se algum OUTRO
    // candidato for descendente dele (ou seja, fica com as folhas).
    var especificos = candidatos.filter(function (el) {
      for (var j = 0; j < candidatos.length; j++) {
        if (candidatos[j] !== el && el.contains(candidatos[j])) {
          return false; // el contém outro candidato -> é "pai", descarta
        }
      }
      return true;
    });
    return especificos;
  }

  // ---- Clipboard (com fallback) -------------------------------------------
  function copiarParaClipboard(texto, aoTerminar) {
    function fallback() {
      try {
        var ta = document.createElement("textarea");
        ta.value = texto;
        ta.style.position = "fixed";
        ta.style.top = "-1000px";
        ta.style.left = "-1000px";
        document.body.appendChild(ta);
        ta.focus();
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
      } catch (e) {
        // se nem o fallback funcionar, segue o jogo (o usuário ainda vê o alert)
      }
      if (aoTerminar) aoTerminar();
    }
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(texto).then(
          function () {
            if (aoTerminar) aoTerminar();
          },
          function () {
            fallback();
          }
        );
      } else {
        fallback();
      }
    } catch (e) {
      fallback();
    }
  }

  // ---- Envio opcional ao Apps Script (text/plain evita preflight CORS) -----
  function enviarParaAppsScript(array) {
    if (!SCRIPT_URL || SCRIPT_URL === "COLE_AQUI_A_URL_DO_APPS_SCRIPT") return;
    try {
      fetch(SCRIPT_URL, {
        method: "POST",
        mode: "no-cors",
        headers: { "Content-Type": "text/plain;charset=utf-8" },
        body: JSON.stringify({ token: TOKEN, licitacoes: array })
      });
    } catch (e) {
      // no-cors não devolve resposta legível; falha silenciosa não atrapalha o clipboard
    }
  }

  // ---- Execução ------------------------------------------------------------
  try {
    var cards = coletarCards();
    var array = [];
    for (var i = 0; i < cards.length; i++) {
      var obj = cardParaObjeto(cards[i]);
      if (obj) array.push(obj);
    }

    var json = JSON.stringify(array);

    copiarParaClipboard(json, function () {
      enviarParaAppsScript(array);
      alert(array.length + " editais copiados! Cole no Hub e clique Processar.");
    });
  } catch (err) {
    alert("Erro ao extrair o boletim: " + (err && err.message ? err.message : err));
  }
})();


/* (B) === BOOKMARKLET (uma linha, arraste para a barra de favoritos) ===

javascript:(function(){var SCRIPT_URL="COLE_AQUI_A_URL_DO_APPS_SCRIPT",TOKEN="COLE_AQUI_O_TOKEN",ROTULOS=["Objeto:","Datas:","Órgão:","Cidade:","Edital:","Valor Estimado:","Nº Conlicitação:"];function R(l){var t=l.trim();if(t===""||t==="info"||t==="content_copy"||t==="location_on"||t==="expand_more"||t==="expand_less")return true;if(/^CAPAG\b/i.test(t))return true;return false}function S(x){var s=String(x==null?"":x),m=true;while(m){m=false;var a=s;s=s.replace(/(info|content_copy|location_on|expand_more|expand_less)\s*$/i,"");if(s!==a)m=true}return s.trim()}function P(l){return /^(Ver mais informações|Ações|Ver itens|Baixar Edital|Resumo do Edital|Pergunte ao Edital|Gerenciar licitação|Anotações|Nenhum edital|BETA|mic|send)\b/i.test(String(l).trim())}function V(ls){var c=[];for(var i=0;i<ls.length;i++){if(P(ls[i]))break;if(R(ls[i]))continue;c.push(ls[i].trim())}return S(c.join(" ").replace(/\s+/g," ").trim())}function F(it){var ls=String(it||"").split(/\r?\n/),mp={},ra=null;for(var i=0;i<ls.length;i++){var t=ls[i].trim(),er=false;for(var r=0;r<ROTULOS.length;r++){var ro=ROTULOS[r];if(t===ro){ra=ro;if(!mp[ra])mp[ra]=[];er=true;break}if(t.indexOf(ro)===0){ra=ro;if(!mp[ra])mp[ra]=[];var re=t.substring(ro.length);if(re.trim()!=="")mp[ra].push(re);er=true;break}}if(er)continue;if(ra)mp[ra].push(ls[i])}return mp}function AE(x){return String(x||"").replace(/Atualizada em:.*$/i,"").trim()}function D(x){var m=String(x||"").match(/\d+/);return m?m[0]:""}function C(card){var mp=F(card.innerText),o=AE(V(mp["Objeto:"]||[])),da=V(mp["Datas:"]||[]),og=V(mp["Órgão:"]||[]),ci=V(mp["Cidade:"]||[]),ed=V(mp["Edital:"]||[]),va=(V(mp["Valor Estimado:"]||[]).match(/R\$\s*[\d.,]+/)||[])[0]||"",nc=D(V(mp["Nº Conlicitação:"]||[]));if(!nc)return null;return{objeto:o||"",nc:nc,cidade:ci||"",uf:"",edital:ed||"",valor_estimado:va||"",orgao:og||"",datas:da||"",data_abertura:"",data_encerramento:"",status:""}}function CO(){var p=Array.prototype.slice.call(document.querySelectorAll("div.card-body.p-4"));if(p.length>0)return p;var all=Array.prototype.slice.call(document.querySelectorAll("*")),cd=all.filter(function(e){var t=e.innerText||"";return t.indexOf("Objeto:")!==-1&&t.indexOf("Edital:")!==-1&&t.indexOf("Conlicitação")!==-1});return cd.filter(function(e){for(var j=0;j<cd.length;j++){if(cd[j]!==e&&e.contains(cd[j]))return false}return true})}function CP(tx,cb){function fb(){try{var ta=document.createElement("textarea");ta.value=tx;ta.style.position="fixed";ta.style.top="-1000px";ta.style.left="-1000px";document.body.appendChild(ta);ta.focus();ta.select();document.execCommand("copy");document.body.removeChild(ta)}catch(e){}if(cb)cb()}try{if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(tx).then(function(){if(cb)cb()},function(){fb()})}else{fb()}}catch(e){fb()}}function EN(a){if(!SCRIPT_URL||SCRIPT_URL==="COLE_AQUI_A_URL_DO_APPS_SCRIPT")return;try{fetch(SCRIPT_URL,{method:"POST",mode:"no-cors",headers:{"Content-Type":"text/plain;charset=utf-8"},body:JSON.stringify({token:TOKEN,licitacoes:a})})}catch(e){}}try{var cs=CO(),ar=[];for(var i=0;i<cs.length;i++){var ob=C(cs[i]);if(ob)ar.push(ob)}var js=JSON.stringify(ar);CP(js,function(){EN(ar);alert(ar.length+" editais copiados! Cole no Hub e clique Processar.")})}catch(err){alert("Erro ao extrair o boletim: "+(err&&err.message?err.message:err))}})();

=========================================================================== */
