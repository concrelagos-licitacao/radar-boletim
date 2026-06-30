"""
gerar_site.py - Gera o site estatico do Hub (GitHub Pages).

Roda no GitHub Actions depois do radar.py + boletim_email.py. Le a aba
'Boletim Licitacoes' do Hub Sheet e mantem um historico ACUMULADO em
docs/dados.json (merge + dedupe). Gera docs/index.html com filtros 100%
client-side (zero runtime no servidor).

PRIVACIDADE: a coluna FILIAL PROXIMA (nome/local da unidade) NAO entra no
site publico. So a DISTANCIA KM (generica) e exibida.

SECURITY: nenhuma credencial e logada ou embutida no site. O service_account
e lido do caminho passado por env; o site publicado contem apenas dados
publicos do PNCP.
"""
import os
import json
import shutil
from datetime import datetime, timezone, timedelta

import gspread

SHEET_ID = '1FjmN8EDKQRcBflL7VOp7MzB6PeKNO0hcXLUUAoLbBbg'
ABA = 'Boletim Licitacoes'
DOCS = 'docs'
DADOS_JSON = os.path.join(DOCS, 'dados.json')

# Coluna sensivel: nunca publicar
OMITIR = {'FILIAL PROXIMA'}


def _hoje_brt():
    return datetime.now(timezone(timedelta(hours=-3))).strftime('%Y-%m-%d')


def _chave(reg):
    """Chave de dedupe: numero+link, com fallback data+orgao+objeto."""
    num = (reg.get('NUMERO') or '').strip()
    link = (reg.get('LINK') or '').strip()
    if num or link:
        return ('NL', num, link)
    return ('DOO', (reg.get('DATA SESSAO') or '').strip(),
            (reg.get('ORGAO') or '').strip(),
            (reg.get('OBJETO') or '').strip()[:80])


def carregar_aba():
    creds = os.environ.get('GOOGLE_SHEETS_CREDENTIALS_PATH',
                           'credenciais/service_account.json')
    gc = gspread.service_account(filename=creds)
    valores = gc.open_by_key(SHEET_ID).worksheet(ABA).get_all_values()
    if len(valores) < 3:
        return []
    # linha 0 = banner, linha 1 = header, linha 2+ = dados
    header = [h.strip() for h in valores[1]]
    regs = []
    for linha in valores[2:]:
        if not any(c.strip() for c in linha):
            continue
        reg = {}
        for i, col in enumerate(header):
            if col in OMITIR:
                continue
            reg[col] = (linha[i].strip() if i < len(linha) else '')
        regs.append(reg)
    return regs


def merge_historico(novos, hoje):
    historico = []
    if os.path.exists(DADOS_JSON):
        try:
            with open(DADOS_JSON, encoding='utf-8') as f:
                historico = json.load(f)
        except Exception:
            historico = []

    vistos = {}
    for r in historico:
        vistos[_chave(r)] = r

    add = 0
    for r in novos:
        k = _chave(r)
        if k not in vistos:
            r['capturado_em'] = hoje
            vistos[k] = r
            add += 1
        else:
            # mantem capturado_em original, atualiza demais campos
            antigo_cap = vistos[k].get('capturado_em', hoje)
            vistos[k].update(r)
            vistos[k]['capturado_em'] = antigo_cap

    todos = list(vistos.values())
    # ordena por DATA SESSAO desc (strings dd/mm/aaaa) -> converte p/ ordenavel
    def _ord(r):
        d = (r.get('DATA SESSAO') or '').strip()
        p = d.split('/')
        if len(p) == 3:
            return '%s-%s-%s' % (p[2], p[1].zfill(2), p[0].zfill(2))
        return '0000-00-00'
    todos.sort(key=lambda r: (_ord(r), r.get('capturado_em', '')), reverse=True)
    return todos, add


def gerar_html(total, novos_hoje, hoje):
    atualizado = datetime.now(timezone(timedelta(hours=-3))).strftime('%d/%m/%Y %H:%M')
    return HTML_TEMPLATE.replace('{{ATUALIZADO}}', atualizado) \
                        .replace('{{TOTAL}}', str(total)) \
                        .replace('{{NOVOS}}', str(novos_hoje)) \
                        .replace('{{HOJE}}', hoje)


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Concrelagos - Radar de Licitacoes</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
  :root{
    --primary:#3A4149; --header:#2E353D; --accent:#C28E2C; --accent-d:#A9781F;
    --bg:#F7F8FA; --card:#FFFFFF; --text:#1F2937; --muted:#6B7280;
    --border:#ECEEF1; --radius:13px;
    --display:'Inter','Segoe UI',Arial,sans-serif; --body:'Inter','Segoe UI',Arial,sans-serif;
    --shadow1:0 1px 3px rgba(35,40,46,.07),0 1px 2px rgba(35,40,46,.04);
    --shadow2:0 10px 28px rgba(35,40,46,.13),0 3px 8px rgba(35,40,46,.06);
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--text);font-family:var(--body);font-size:14px}
  h1,h2,h3,h4{font-family:var(--display);font-weight:700;color:var(--primary);margin:0}
  .wrap{max-width:1500px;margin:0 auto;padding:18px 22px 60px}
  .hbar{background:#fff;border-bottom:3px solid var(--accent);border-radius:var(--radius);
    padding:18px 24px;display:flex;justify-content:space-between;align-items:center;gap:16px;
    box-shadow:var(--shadow1),0 14px 30px -18px rgba(194,142,44,.45)}
  .hbar img.logo{height:46px;width:auto;display:block}
  .hbar .ti{font-family:var(--display);font-size:1.15rem;color:var(--primary);letter-spacing:.02em}
  .hbar .s{color:var(--muted);font-size:.84rem;margin-top:3px}
  .updated{color:var(--muted);text-align:right;font-size:.78rem;white-space:nowrap}
  .cards{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:18px 0}
  .card{background:var(--card);border:1px solid var(--border);border-left:4px solid var(--accent);
    border-radius:var(--radius);padding:16px 18px;box-shadow:var(--shadow1);transition:transform .2s,box-shadow .2s}
  .card:hover{transform:translateY(-4px);box-shadow:var(--shadow2)}
  .card .lbl{font-size:.7rem;text-transform:uppercase;color:var(--muted);letter-spacing:.09em;font-weight:600}
  .card .val{font-size:1.6rem;font-family:var(--display);color:var(--accent-d);margin-top:6px}
  .panel{background:var(--card);border:1px solid var(--border);border-radius:var(--radius);padding:16px 18px;box-shadow:var(--shadow1)}
  .filtros{display:flex;gap:12px;flex-wrap:wrap;align-items:flex-end;margin-bottom:14px}
  .fcol{display:flex;flex-direction:column;gap:4px}
  .fcol label{font-size:.7rem;text-transform:uppercase;color:var(--muted);letter-spacing:.06em;font-weight:600}
  .fcol input,.fcol select{padding:9px 12px;border:1px solid #CDD2D8;border-radius:10px;font-family:var(--body);font-size:.9rem;background:#fff}
  .fcol input:focus,.fcol select:focus{outline:none;border-color:var(--accent)}
  #busca{min-width:280px}
  .rangeval{font-size:.8rem;color:var(--accent-d);font-weight:600}
  .count{color:#6B7280;font-size:.82rem;margin:4px 2px 10px}
  .tbl-wrap{overflow:auto;border:1px solid var(--border);border-radius:10px;max-height:68vh}
  table{border-collapse:collapse;width:100%;font-size:.82rem}
  thead th{position:sticky;top:0;background:var(--primary);color:#E7D9B6;text-align:left;
    padding:10px 11px;border-bottom:2px solid var(--accent);font-weight:600;font-size:.72rem;
    text-transform:uppercase;letter-spacing:.04em;white-space:nowrap}
  tbody td{padding:9px 11px;border-bottom:1px solid #F0F1F3;color:#374151;vertical-align:top}
  tbody tr:nth-child(even){background:#FAFBFC}
  tbody tr:hover{background:#FBF3E3}
  .uf{display:inline-block;background:var(--primary);color:#fff;border-radius:9px;padding:2px 9px;font-size:.72rem;font-weight:600}
  .km{display:inline-block;border-radius:10px;padding:2px 8px;font-size:.72rem;font-weight:600;color:#fff}
  .km.v{background:#2E7D32}.km.a{background:#E08A00}.km.c{background:#757575}
  .obj{max-width:420px;white-space:normal;line-height:1.35}
  .org{max-width:240px;white-space:normal;color:#4B5563}
  .btn{background:linear-gradient(135deg,#C28E2C,#A9781F);color:#fff;padding:5px 12px;border-radius:8px;
    font-size:.74rem;text-decoration:none;white-space:nowrap;display:inline-block}
  .btn:hover{box-shadow:0 4px 14px rgba(194,142,44,.4)}
  .novo{background:#2E7D32;color:#fff;border-radius:8px;padding:1px 7px;font-size:.64rem;font-weight:700;margin-left:6px}
  #load{position:fixed;inset:0;background:var(--bg);display:flex;flex-direction:column;align-items:center;justify-content:center;z-index:50}
  .spin{width:46px;height:46px;border:4px solid #E7D9B6;border-top-color:var(--accent);border-radius:50%;animation:r 1s linear infinite}
  @keyframes r{to{transform:rotate(360deg)}}
  #load p{margin-top:14px;color:var(--muted);font-family:var(--display);font-size:.8rem;letter-spacing:.05em}
  .err{background:#FEE2E2;border:1px solid #FCA5A5;color:#991B1B;padding:14px 18px;border-radius:10px;margin:20px 0}
  .foot{color:var(--muted);font-size:.74rem;text-align:center;margin-top:24px}
  @media(max-width:900px){.cards{grid-template-columns:repeat(2,1fr)}.obj{max-width:none}}
</style>
</head>
<body>
  <div id="load"><div class="spin"></div><p>Carregando radar...</p></div>

  <div class="wrap" id="app" style="display:none">
    <div class="hbar">
      <div style="display:flex;align-items:center;gap:16px">
        <img class="logo" src="logo.png" alt="Concrelagos" onerror="this.style.display='none'">
        <div>
          <div class="ti">Radar de Licitacoes</div>
          <div class="s">Concreto e brita - monitoramento PNCP, Diario Oficial e Licitar Digital</div>
        </div>
      </div>
      <div class="updated">Atualizado<br><b>{{ATUALIZADO}}</b></div>
    </div>

    <div id="erro"></div>

    <div class="cards">
      <div class="card"><div class="lbl">Editais no historico</div><div class="val">{{TOTAL}}</div></div>
      <div class="card"><div class="lbl">Novos hoje</div><div class="val">{{NOVOS}}</div></div>
      <div class="card"><div class="lbl">Exibindo agora</div><div class="val" id="kpiVis">-</div></div>
      <div class="card"><div class="lbl">Estados (UF)</div><div class="val" id="kpiUf">-</div></div>
    </div>

    <div class="panel">
      <div class="filtros">
        <div class="fcol" style="flex:1">
          <label>Buscar (objeto, orgao, municipio, numero)</label>
          <input id="busca" placeholder="Ex.: concreto usinado, prefeitura, brita...">
        </div>
        <div class="fcol">
          <label>UF</label>
          <select id="fuf"><option value="">Todas</option></select>
        </div>
        <div class="fcol">
          <label>Fonte</label>
          <select id="ffonte"><option value="">Todas</option></select>
        </div>
        <div class="fcol">
          <label>Distancia max: <span class="rangeval" id="kmval">qualquer</span></label>
          <input type="range" id="fkm" min="0" max="1000" step="25" value="1000">
        </div>
        <div class="fcol">
          <label>Periodo (sessao)</label>
          <select id="fper">
            <option value="">Tudo</option>
            <option value="fut">So futuros</option>
            <option value="7">Proximos 7 dias</option>
            <option value="30">Proximos 30 dias</option>
          </select>
        </div>
        <div class="fcol">
          <label>&nbsp;</label>
          <button class="btn" id="limpar" style="border:0;cursor:pointer;padding:9px 14px">Limpar filtros</button>
        </div>
      </div>
      <div class="count" id="count"></div>
      <div class="tbl-wrap">
        <table>
          <thead><tr>
            <th>Sessao</th><th>UF</th><th>Municipio</th><th>Orgao</th>
            <th>Objeto</th><th>Fonte</th><th>Dist.</th><th>Tipo</th><th>Edital</th>
          </tr></thead>
          <tbody id="tbody"></tbody>
        </table>
      </div>
    </div>

    <div class="foot">
      Concrelagos - Equipe Juridica - dados publicos (PNCP / Diario Oficial). Atualizado automaticamente todo dia as 7h.
    </div>
  </div>

<script>
var DADOS=[], HOJE="{{HOJE}}";
function esc(s){return s==null?'':String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
function g(r,k){return (r[k]==null?'':String(r[k])).trim();}

function parseBR(d){var p=String(d||'').split('/');if(p.length!==3)return null;
  var dt=new Date(+p[2],+p[1]-1,+p[0]);return isNaN(dt)?null:dt;}
function kmNum(r){var v=g(r,'DISTANCIA KM').replace(',','.');var n=parseFloat(v);return isNaN(n)?null:n;}
function kmCls(n){if(n==null)return '';if(n<=50)return 'v';if(n<=150)return 'a';return 'c';}

function popular(){
  var ufs={},fontes={};
  DADOS.forEach(function(r){var u=g(r,'UF');if(u)ufs[u]=1;var f=g(r,'FONTE');if(f)fontes[f]=1;});
  var su=document.getElementById('fuf');
  Object.keys(ufs).sort().forEach(function(u){var o=document.createElement('option');o.value=u;o.textContent=u;su.appendChild(o);});
  var sf=document.getElementById('ffonte');
  Object.keys(fontes).sort().forEach(function(f){var o=document.createElement('option');o.value=f;o.textContent=f;sf.appendChild(o);});
  document.getElementById('kpiUf').textContent=Object.keys(ufs).length;
}

function filtrar(){
  var q=document.getElementById('busca').value.toLowerCase().trim();
  var uf=document.getElementById('fuf').value;
  var fonte=document.getElementById('ffonte').value;
  var kmMax=+document.getElementById('fkm').value;
  var per=document.getElementById('fper').value;
  document.getElementById('kmval').textContent=(kmMax>=1000?'qualquer':kmMax+' km');
  var hoje=new Date();hoje.setHours(0,0,0,0);

  var out=DADOS.filter(function(r){
    if(uf && g(r,'UF')!==uf)return false;
    if(fonte && g(r,'FONTE')!==fonte)return false;
    if(kmMax<1000){var n=kmNum(r);if(n==null||n>kmMax)return false;}
    if(per){
      var dt=parseBR(g(r,'DATA SESSAO'));
      if(per==='fut'){if(!dt||dt<hoje)return false;}
      else{var lim=new Date(hoje);lim.setDate(lim.getDate()+(+per));if(!dt||dt<hoje||dt>lim)return false;}
    }
    if(q){
      var blob=(g(r,'OBJETO')+' '+g(r,'ORGAO')+' '+g(r,'MUNICIPIO')+' '+g(r,'NUMERO')+' '+g(r,'UF')).toLowerCase();
      if(blob.indexOf(q)<0)return false;
    }
    return true;
  });
  render(out);
}

function render(rows){
  var tb=document.getElementById('tbody');
  document.getElementById('count').textContent=rows.length+' edital(is) - de '+DADOS.length+' no historico';
  document.getElementById('kpiVis').textContent=rows.length;
  if(!rows.length){tb.innerHTML='<tr><td colspan="9" style="padding:24px;text-align:center;color:#9CA3AF">Nenhum edital com esses filtros.</td></tr>';return;}
  var h='';
  rows.forEach(function(r){
    var n=kmNum(r),km=(n==null?'-':'<span class="km '+kmCls(n)+'">'+Math.round(n)+' km</span>');
    var link=g(r,'LINK');
    var btn=(link.indexOf('http')===0)?'<a class="btn" href="'+esc(link)+'" target="_blank" rel="noopener">Abrir</a>':'-';
    var novo=(g(r,'capturado_em')===HOJE)?'<span class="novo">NOVO</span>':'';
    h+='<tr>'
      +'<td style="white-space:nowrap">'+esc(g(r,'DATA SESSAO'))+novo+'</td>'
      +'<td><span class="uf">'+esc(g(r,'UF'))+'</span></td>'
      +'<td>'+esc(g(r,'MUNICIPIO'))+'</td>'
      +'<td class="org">'+esc(g(r,'ORGAO'))+'</td>'
      +'<td class="obj">'+esc(g(r,'OBJETO'))+'</td>'
      +'<td>'+esc(g(r,'FONTE'))+'</td>'
      +'<td>'+km+'</td>'
      +'<td>'+esc(g(r,'TIPO'))+'</td>'
      +'<td>'+btn+'</td>'
      +'</tr>';
  });
  tb.innerHTML=h;
}

['busca','fuf','ffonte','fkm','fper'].forEach(function(id){
  document.getElementById(id).addEventListener('input',filtrar);
  document.getElementById(id).addEventListener('change',filtrar);
});
document.getElementById('limpar').addEventListener('click',function(){
  document.getElementById('busca').value='';document.getElementById('fuf').value='';
  document.getElementById('ffonte').value='';document.getElementById('fkm').value=1000;
  document.getElementById('fper').value='';filtrar();
});

fetch('dados.json?v='+Date.now()).then(function(r){return r.json();}).then(function(d){
  DADOS=d||[];
  document.getElementById('load').style.display='none';
  document.getElementById('app').style.display='block';
  popular();filtrar();
}).catch(function(e){
  document.getElementById('load').style.display='none';
  document.getElementById('app').style.display='block';
  document.getElementById('erro').innerHTML='<div class="err">Nao foi possivel carregar os dados (dados.json). Tente recarregar a pagina.</div>';
});
</script>
</body>
</html>"""


def main():
    os.makedirs(DOCS, exist_ok=True)
    hoje = _hoje_brt()

    novos = carregar_aba()
    print('Lidos %d editais da aba %s' % (len(novos), ABA))

    todos, add = merge_historico(novos, hoje)
    with open(DADOS_JSON, 'w', encoding='utf-8') as f:
        json.dump(todos, f, ensure_ascii=False, separators=(',', ':'))
    print('Historico: %d editais (%d novos hoje)' % (len(todos), add))

    novos_hoje = sum(1 for r in todos if r.get('capturado_em') == hoje)
    html = gerar_html(len(todos), novos_hoje, hoje)
    with open(os.path.join(DOCS, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(html)
    print('index.html gerado.')

    # logo (best-effort)
    for origem in ('assets/logo.png', os.path.join('assets', 'logo.png')):
        if os.path.exists(origem):
            shutil.copyfile(origem, os.path.join(DOCS, 'logo.png'))
            print('logo.png copiado.')
            break

    print('OK -> site em %s/' % DOCS)


if __name__ == '__main__':
    main()
