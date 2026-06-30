# -*- coding: utf-8 -*-
"""BOLETIM GRATIS de licitacoes de concreto/brita (PNCP + Querido Diario + Licitar Digital).
Replica a ESTRUTURA do ConLicitacao de graca: 3 fontes -> filtro de perfil -> dedup por
IDENTIDADE (nao por texto truncado) -> grava a aba 'Boletim Licitacoes' do Hub. Guardrail
anti-falha-silenciosa: registra a saude (contagem por fonte/UF) e ALERTA quando uma fonte
zera/despenca vs. a rodada anterior, ou quando o PNCP trunca um estado. Nunca some em silencio."""
import os, re, time, json, hashlib, datetime, unicodedata
from math import radians, sin, cos, sqrt, asin
import requests
from dotenv import load_dotenv
load_dotenv()
import gspread

SHEET_ID = '1FjmN8EDKQRcBflL7VOp7MzB6PeKNO0hcXLUUAoLbBbg'
UFS = ['MG', 'SP', 'RJ', 'ES', 'PR', 'BA']
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36'}
def _n(s):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKD', str(s or '')).encode('ascii', 'ignore').decode().lower())
# Logica de SCORE do scraper.py (3=certo, 2=provavel c/ contexto, 1=obra generica-> descartada aqui)
KW3 = ("concreto usinado", "concreto pre-misturado", "concreto pre misturado", "concreto dosado",
       "concreto dosado em central", "central dosadora", "concreto preparado", "concreto comercializado",
       "fornecimento de concreto", "concreto bombeado", "concreto bombeavel",
       "concreto fck", "concreto estrutural", "concreto convencional", "concreto betonado",
       "brita", "britas", "brita graduada", "bgs", "brita 0", "brita 1", "brita 2", "brita 3", "brita 4",
       "brita corrida", "pedra britada", "pedras britadas", "pedrisco", "po de pedra", "bica corrida",
       "rachao", "racho", "pedregulho de cava", "pedregulho lavado", "cascalho", "agregado graudo", "agregados graudos",
       "pedra de mao", "pedra marroada", "seixo")
KW2_CONC = ("concreto", "concretagem", "concreto armado")
KW2_BRITA = ("agregado", "agregados")
CONTEXTO = ("fck", "mpa", "m3", "metro cubico", "metros cubicos", "usina", "usinado", "central",
            "dosado", "bombeado", "betoneira", "slump")
EXCL = ("tubo de concreto", "tubos de concreto", "manilha", "aduela", "poste de concreto", "postes de concreto",
        "bloco de concreto", "blocos de concreto", "bloco estrutural", "bloquete", "artefato de concreto",
        "artefatos de concreto", "pre-moldado", "pre moldado", "premoldado", "pre-fabricado", "pre fabricado",
        "prefabricado", "piso intertravado", "paver", "lajota", "meio-fio", "meio fio", "guia e sarjeta",
        "sarjeta", "cimento", "argamassa", "pavimentacao asfaltica", "asfalto", "cbuq", "massa asfaltica",
        "emulsao asfaltica", "concreto asfaltico", "concreto betuminoso", "agregado miudo")
HARD_EXCL = ("asfalt", "cbuq", "betumin", "massa asfaltica", "emulsao asfaltica")  # asfalto SEMPRE fora
def score(texto):
    t = _n(texto)
    if any(e in t for e in HARD_EXCL): return 0      # asfalto/CBUQ nunca entra, nem com 'concreto' junto
    if any(k in t for k in KW3): return 3            # sinal forte -> aceita (vence exclusao restante)
    if any(e in t for e in EXCL): return 0           # produto vizinho s/ sinal forte -> descarta
    if any(k in t for k in KW2_BRITA): return 2
    if any(k in t for k in KW2_CONC) and any(c in t for c in CONTEXTO): return 2
    return 0                                         # "concreto" sem contexto / obra generica -> descarta
def rel(t):
    return score(t) >= 2                             # mantem so CERTO + PROVAVEL (corta o ruido)
def norm(s):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKD', str(s or '')).encode('ascii', 'ignore').decode().upper().strip())
def iso(s):
    return str(s or '')[:10]

# ---------- GEO: distancia haversine ate usinas/pedreiras ----------
HAVERSINE_AJUSTE = float(os.environ.get('HAVERSINE_AJUSTE_FATOR', '1.0'))
_GEO_CACHE = {}
_geocoder_inst = [None]
_GEO_OFF = [False]      # disjuntor: desliga geocoding se Nominatim cair
_GEO_FALHAS = [0]       # falhas/timeouts consecutivos
_BASE_MUN = {}          # base local IBGE: (nome_norm, UF) -> (lat, lon)

def _carregar_base_mun():
    if _BASE_MUN:
        return _BASE_MUN
    try:
        import csv
        with open('municipios_br.csv', encoding='utf-8') as f:
            for row in csv.DictReader(f):
                _BASE_MUN[(row['nome_norm'], row['uf'].upper())] = (float(row['lat']), float(row['lon']))
        print("  BASE MUNICIPIOS: %d carregados (geocoding local, sem rede)" % len(_BASE_MUN))
    except Exception as e:
        print("  BASE MUNICIPIOS indisponivel (%s) -- caira no Nominatim" % repr(e)[:50])
    return _BASE_MUN

def _haversine_km(p1, p2):
    lat1, lng1 = map(radians, p1)
    lat2, lng2 = map(radians, p2)
    dlat, dlng = lat2 - lat1, lng2 - lng1
    a = sin(dlat/2)**2 + cos(lat1)*cos(lat2)*sin(dlng/2)**2
    return 6371.0 * 2 * asin(sqrt(a)) * HAVERSINE_AJUSTE

def _geocode(municipio, uf):
    key = (_n(municipio).strip(), uf.upper())
    if key in _GEO_CACHE:
        return _GEO_CACHE[key]
    hit = _carregar_base_mun().get(key)   # 1o: base local IBGE (instantaneo, sem rede)
    if hit:
        _GEO_CACHE[key] = hit
        return hit
    if _GEO_OFF[0]:        # disjuntor aberto: nao bate mais no Nominatim
        return None
    try:
        from geopy.geocoders import Nominatim
        if _geocoder_inst[0] is None:
            _geocoder_inst[0] = Nominatim(user_agent='concrelagos-boletim/1.0', timeout=10)
        time.sleep(1.1)
        loc = _geocoder_inst[0].geocode('%s, %s, Brasil' % (municipio, uf), country_codes=['br'], timeout=10)
        _GEO_FALHAS[0] = 0
        if loc:
            coord = (float(loc.latitude), float(loc.longitude))
            _GEO_CACHE[key] = coord
            return coord
    except Exception as e:
        _GEO_FALHAS[0] += 1
        print("  GEO erro %s/%s: %s" % (municipio, uf, repr(e)[:60]))
        if _GEO_FALHAS[0] >= 5:
            _GEO_OFF[0] = True
            print("  GEO DESATIVADO: 5 falhas seguidas (Nominatim fora). Boletim segue sem distancia.")
    _GEO_CACHE[key] = None
    return None

def _carregar_filiais(gc):
    for sid in [os.environ.get('GOOGLE_SHEETS_ID', ''), SHEET_ID]:
        if not sid:
            continue
        try:
            rows = gc.open_by_key(sid).worksheet('Filiais').get_all_records()
            return [r for r in rows if r.get('latitude') and r.get('longitude')
                    and abs(float(r['latitude'])) > 0.01]
        except gspread.WorksheetNotFound:
            continue
        except Exception as e:
            print("  FILIAIS erro em %s...: %s" % (sid[:12], repr(e)[:60]))
    print("  FILIAIS: aba 'Filiais' nao encontrada. Crie no Hub com colunas:")
    print("    nome | municipio | uf | latitude | longitude | tipo")
    return []

def _enriquecer(r, filiais):
    mun, uf = r.get('municipio', ''), r.get('uf', '')
    if not mun or not uf or not filiais: return r
    coord = _geocode(mun, uf)
    if not coord: return r
    cands = [f for f in filiais if str(f.get('uf', '')).upper() == uf.upper()]
    if not cands: return r
    melhor_km, melhor_f = None, None
    for f in cands:
        try:
            km = _haversine_km(coord, (float(f['latitude']), float(f['longitude'])))
        except Exception: continue
        if melhor_km is None or km < melhor_km:
            melhor_km, melhor_f = km, f
    if melhor_f:
        r['distancia_km'] = round(melhor_km, 1)
        r['filial_proxima'] = '%s (%s/%s)' % (melhor_f.get('nome', ''), melhor_f.get('municipio', ''), uf)
        r['tipo_atendimento'] = melhor_f.get('tipo', 'usina')
    return r

AGORA = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
hoje = datetime.date.today()
HOJE_ISO = hoje.isoformat()
ini = hoje - datetime.timedelta(days=14)
registros = []      # cada um: dict fonte/uf/municipio/orgao/objeto/data_sessao/data_pub/numero/link/uid
PNCP_TRUNC = []     # UFs em que o PNCP truncou (integra=False) -> vira ALERTA

# orcamento de tempo POR FONTE: nenhuma fonte (ex: PNCP fora do ar) monopoliza o tempo das outras
PNCP_BUDGET_S    = float(os.environ.get('PNCP_BUDGET_S', '300'))     # 5 min
LICITAR_BUDGET_S = float(os.environ.get('LICITAR_BUDGET_S', '300'))  # 5 min
def _prazo(segundos):
    fim = time.monotonic() + segundos
    return lambda: time.monotonic() < fim

# ---------- 1) PNCP ----------
def pncp_get(url):
    for i in range(3):
        try: r = requests.get(url, timeout=20, headers=UA)
        except Exception: time.sleep(1.5*(i+1)); continue
        if r.status_code == 204: return {'data': [], 'totalPaginas': 0}
        if r.status_code != 200: time.sleep(1.5*(i+1)); continue
        try: j = r.json()
        except Exception: time.sleep(1.5*(i+1)); continue
        if not (j.get('data') or []) and (j.get('totalRegistros') or 0) > 0:
            time.sleep(1.8*(i+1)); continue
        return j
    return None
def coleta_pncp():
    n = 0
    ok_tempo = _prazo(PNCP_BUDGET_S)
    for uf in UFS:
        if not ok_tempo(): print("  PNCP: orcamento de tempo esgotado"); break
        pag, tot, ok = 1, 1, True
        while pag <= tot and pag <= 80:
            if not ok_tempo(): break
            url = ('https://pncp.gov.br/api/consulta/v1/contratacoes/publicacao'
                   '?dataInicial=%s&dataFinal=%s&codigoModalidadeContratacao=6&uf=%s&pagina=%d&tamanhoPagina=50'
                   % (ini.strftime('%Y%m%d'), hoje.strftime('%Y%m%d'), uf, pag))
            j = pncp_get(url)
            if j is None: ok = False; break          # falhou de vez -> integra do UF quebrou
            tot = j.get('totalPaginas') or tot
            data = j.get('data') or []
            for d in data:
                if not rel(d.get('objetoCompra')): continue
                uo = d.get('unidadeOrgao') or {}; oe = d.get('orgaoEntidade') or {}
                registros.append({'fonte': 'PNCP', 'uf': uo.get('ufSigla', uf), 'municipio': uo.get('municipioNome', ''),
                                  'orgao': oe.get('razaoSocial', ''), 'objeto': (d.get('objetoCompra') or '')[:300],
                                  'data_sessao': iso(d.get('dataEncerramentoProposta') or d.get('dataAberturaProposta')),
                                  'data_pub': iso(d.get('dataPublicacaoPncp')), 'numero': d.get('numeroControlePNCP', ''),
                                  'link': 'https://pncp.gov.br/app/editais',
                                  'valor': d.get('valorTotalEstimado') or '',
                                  'modalidade': d.get('modalidadeNome') or 'Pregao Eletronico',
                                  'uid': 'PNCP:' + str(d.get('numeroControlePNCP') or '')}); n += 1
            if not data and pag < tot:
                time.sleep(4); data = (pncp_get(url) or {}).get('data') or []
                if not data: ok = False; break  # retry unico falhou = throttle/truncou
            if not data: break
            pag += 1; time.sleep(1.0)
        if not ok: PNCP_TRUNC.append(uf)
        time.sleep(0.6)
    return n

# ---------- 2) Querido Diario ----------
def coleta_qd():
    n = 0
    try:
        params = {'querystring': '"concreto usinado" OR brita OR "pedras britadas"',
                  'published_since': ini.isoformat(), 'published_until': hoje.isoformat(),
                  'size': 200, 'sort_by': 'descending_date'}
        r = requests.get('https://api.queridodiario.ok.org.br/gazettes', params=params, timeout=50, headers=UA)
        if r.status_code != 200: print("  QD HTTP", r.status_code); return 0
        for g in (r.json() or {}).get('gazettes', []):
            if (g.get('state_code') or '').upper() not in UFS: continue
            exc = ' '.join(g.get('excerpts') or [])
            if not rel(exc): continue
            if not re.search(r'(pregao|preg[ao]o|licita|edital|tomada de pre|aviso)', exc, re.I): continue
            obj = re.sub(r'\s+', ' ', exc)[:300]
            registros.append({'fonte': 'QUERIDO_DIARIO', 'uf': (g.get('state_code') or '').upper(), 'municipio': g.get('territory_name', ''),
                              'orgao': (g.get('territory_name', '') + ' (Diario Oficial)'), 'objeto': obj,
                              'data_sessao': '', 'data_pub': iso(g.get('date')), 'numero': '',
                              'link': g.get('txt_url') or g.get('url') or '',
                              'uid': 'QD:' + hashlib.md5(norm(g.get('territory_name','') + obj).encode()).hexdigest()[:16]})
            n += 1
    except Exception as e:
        print("  QD erro:", repr(e)[:100])
    return n

# ---------- 3) Licitar Digital ----------
def coleta_licitar():
    n = 0
    ok_tempo = _prazo(LICITAR_BUDGET_S)
    H = {**UA, 'Content-Type': 'application/json', 'Accept': 'application/json'}
    URL = 'https://manager-api.licitardigital.com.br/auction-notice/doSearchAuctionNotice'
    for uf in UFS:
        if not ok_tempo(): print("  Licitar: orcamento de tempo esgotado"); break
        for termo in ('concreto', 'brita'):
            offset, vazias = 0, 0
            while offset <= 600:
                if not ok_tempo(): break
                body = {'filter': {'search': termo, 'auctionType': 'E', 'state': uf}, 'offset': offset}
                try:
                    r = requests.post(URL, headers=H, data=json.dumps(body), timeout=40)
                except Exception: break
                if r.status_code not in (200, 201): break
                j = r.json(); data = j.get('data') or []
                if not data: break
                cnt = (j.get('meta') or {}).get('count', 0)
                fut_sessao = 0
                for it in data:
                    if it.get('auctionFinished') or it.get('auctionCanceled'): continue  # encerrada/cancelada fora
                    ds = iso(it.get('startDateTimeDispute'))
                    if ds and ds < HOJE_ISO: continue                                    # sessao ja passou -> fora
                    fut_sessao += 1
                    obj = it.get('simpleDescription') or ''
                    if not rel(obj): continue
                    registros.append({'fonte': 'LICITAR_DIGITAL', 'uf': uf, 'municipio': '',
                                      'orgao': it.get('organizationName', ''), 'objeto': obj[:300],
                                      'data_sessao': ds, 'data_pub': iso(it.get('dateTimeInsert')),
                                      'numero': it.get('auctionNumber', ''),
                                      'link': 'https://app.licitardigital.com.br/processo/%s' % it.get('id', ''),
                                      'uid': 'LIC:' + str(it.get('id') or '')})
                    n += 1
                vazias = vazias + 1 if fut_sessao == 0 else 0
                if vazias >= 2: break          # 2 paginas seguidas sem sessao futura = entrou no historico
                offset += 20
                if offset >= cnt: break
                time.sleep(0.4)
    return n

print("== BOLETIM GRATIS concreto/brita | janela %s a %s | UFs %s ==" % (ini, hoje, UFS))
c_pncp, c_qd, c_lic = coleta_pncp(), coleta_qd(), coleta_licitar()
print("PNCP:", c_pncp, "| Querido Diario:", c_qd, "| Licitar Digital:", c_lic)

# ---------- dedup POR IDENTIDADE (uid + texto COMPLETO; NUNCA por prefixo truncado) ----------
# precedencia: PNCP (origem legal) > Licitar > Querido Diario
ordem = {'PNCP': 0, 'LICITAR_DIGITAL': 1, 'QUERIDO_DIARIO': 2}
registros.sort(key=lambda x: ordem.get(x['fonte'], 9))
vistos_uid, vistos_txt, mirror, final = set(), set(), {}, []
for r in registros:
    uid = r.get('uid') or ''
    if uid and uid in vistos_uid: continue                 # mesma fonte relistando o mesmo edital
    ob = norm(r['objeto'])
    chave_txt = (norm(r['orgao']), ob)                      # mesmo orgao+objeto exatos
    if chave_txt in vistos_txt: continue
    if len(ob) >= 60 and ob in mirror and mirror[ob] != r['fonte']:
        continue                                           # mesmo edital ESPELHADO em outra fonte -> fica o de maior precedencia
    if uid: vistos_uid.add(uid)
    vistos_txt.add(chave_txt)
    if len(ob) >= 60: mirror.setdefault(ob, r['fonte'])
    final.append(r)
# so editais ainda disputaveis: sessao vazia (QD) ou hoje em diante
final = [r for r in final if (not r.get('data_sessao')) or r['data_sessao'] >= HOJE_ISO]
print("Total bruto:", len(registros), "| apos dedup+recencia:", len(final))

porfonte = {}
for r in final: porfonte[r['fonte']] = porfonte.get(r['fonte'], 0) + 1
poruf = {}
for r in final: poruf[r['uf']] = poruf.get(r['uf'], 0) + 1
print("Por fonte:", porfonte, "| Por UF:", poruf)

# ---------- GUARDRAIL: compara com a rodada anterior e gera ALERTA ----------
gc = gspread.service_account(filename=os.environ['GOOGLE_SHEETS_CREDENTIALS_PATH'])
sh = gc.open_by_key(SHEET_ID)
filiais = _carregar_filiais(gc)
print("Filiais carregadas:", len(filiais))
def aba(nome, cols):
    try: return sh.worksheet(nome)
    except gspread.WorksheetNotFound: return sh.add_worksheet(title=nome, rows=2000, cols=cols)

raw = {'PNCP': c_pncp, 'LICITAR_DIGITAL': c_lic, 'QUERIDO_DIARIO': c_qd}  # coletado (saude da fonte), antes do dedup
ws_saude = aba('Saude Boletim', 9)
prev = ws_saude.get_all_values()
prev_raw = {}
if len(prev) > 1:
    try: prev_raw = json.loads(prev[-1][2] or '{}')
    except Exception: prev_raw = {}

alertas = []
for f in ('PNCP', 'LICITAR_DIGITAL', 'QUERIDO_DIARIO'):
    atual, antes = raw.get(f, 0), prev_raw.get(f, 0)
    if atual == 0 and antes > 0: alertas.append("FONTE %s COLETOU 0 (antes %d)" % (f, antes))
    elif antes >= 10 and atual < antes * 0.5: alertas.append("FONTE %s DESPENCOU %d->%d" % (f, antes, atual))
if PNCP_TRUNC: alertas.append("PNCP TRUNCOU (dados incompletos): " + ", ".join(PNCP_TRUNC))
alerta_txt = " | ".join(alertas) if alertas else "OK - todas as fontes saudaveis"

# log de saude (append) -> baseline pra proxima rodada (compara RAW = a fonte respondeu?)
if not prev:
    ws_saude.append_row(['QUANDO', 'TOTAL_UNICO', 'RAW_POR_FONTE(json)', 'POR_UF(json)', 'PNCP_raw', 'LICITAR_raw', 'QD_raw', 'TRUNCOU', 'ALERTA'])
ws_saude.append_row([AGORA, len(final), json.dumps(raw), json.dumps(poruf),
                     c_pncp, c_lic, c_qd, ", ".join(PNCP_TRUNC), alerta_txt])

# ---------- enriquece com distancia geo ----------
if filiais:
    uniq_mun = len(set((r.get('municipio',''), r.get('uf','')) for r in final if r.get('municipio')))
    print("Geocodificando %d municipios unicos..." % uniq_mun)
    final = [_enriquecer(r, filiais) for r in final]

# ---------- grava o BOLETIM (mesma estrutura do ConLic) ----------
final.sort(key=lambda r: (r.get('data_sessao') or '9999', r['uf']))
FONTE_LBL = {'PNCP': 'PNCP', 'LICITAR_DIGITAL': 'Licitar Digital', 'QUERIDO_DIARIO': 'Diario Oficial'}
header = ['DATA SESSAO', 'UF', 'MUNICIPIO', 'ORGAO', 'OBJETO', 'FONTE', 'PUBLICADO', 'NUMERO', 'DISTANCIA KM', 'FILIAL PROXIMA', 'TIPO', 'LINK', 'VALOR', 'MODALIDADE']
linhas = [[r.get('data_sessao',''), r['uf'], r.get('municipio',''), r['orgao'], r['objeto'],
           FONTE_LBL.get(r['fonte'], r['fonte']), r.get('data_pub',''), str(r.get('numero','')),
           str(r.get('distancia_km','')), r.get('filial_proxima',''), r.get('tipo_atendimento',''),
           r.get('link',''), str(r.get('valor','')), r.get('modalidade','')]
          for r in final]
banner = "BOLETIM %s | %d editais | PNCP %d  Licitar %d  Diario %d | %s" % (
    AGORA, len(final), porfonte.get('PNCP',0), porfonte.get('LICITAR_DIGITAL',0), porfonte.get('QUERIDO_DIARIO',0),
    ("*** ALERTA: " + alerta_txt + " ***") if alertas else alerta_txt)

ws = aba('Boletim Licitacoes', 14)
ws.clear()
ws.update(values=[[banner], header] + linhas, range_name='A1')
print("\nOK -> aba 'Boletim Licitacoes' (%d editais) | %s" % (len(final), alerta_txt))
