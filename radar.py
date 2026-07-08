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
from filtro_concreto import edital_entra_por_itens, porte_m3   # item-a-item + porte (fixture-testado)

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
        "emulsao asfaltica", "concreto asfaltico", "concreto betuminoso", "agregado miudo",
        # falsos positivos observados (2026-07-04): 'agregad' de outros sentidos, servicos, saude
        "agregadora", "agregador de", "plataforma digital", "plano de assistencia", "assistencia medica",
        "plano de saude", "plano odontologico", "academia", "diario oficial", "comunicado", "art. 117")
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
# brita subset de KW3 (o resto de KW3 = concreto-usinado forte). Usado SO no caminho PNCP p/ mandar
# 'brita + contexto de berco/tubo' pro item-check em vez de aceitar direto pelo objeto -- o score() casa
# 'brita' (KW3) ANTES do EXCL, entao 'brita p/ assentamento de tubo' passaria. NAO mexe no score()/rel()
# global de proposito: Licitar/QD usam o mesmo gate e NAO tem rede de item-check (rebaixar la = MISS).
KW3_BRITA = ('brita', 'britas', 'brita graduada', 'bgs', 'brita 0', 'brita 1', 'brita 2', 'brita 3',
             'brita 4', 'brita corrida', 'pedra britada', 'pedras britadas', 'pedrisco', 'po de pedra',
             'bica corrida', 'rachao', 'racho', 'pedregulho de cava', 'pedregulho lavado', 'cascalho',
             'agregado graudo', 'agregados graudos', 'pedra de mao', 'pedra marroada', 'seixo')
def _so_brita_com_excl(o):
    t = _n(o)
    return (any(k in t for k in KW3_BRITA) and not any(k in t for k in KW3 if k not in KW3_BRITA)
            and any(e in t for e in EXCL))
def norm(s):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKD', str(s or '')).encode('ascii', 'ignore').decode().upper().strip())
def iso(s):
    return str(s or '')[:10]
def _pncp_link(nc):
    """numeroControlePNCP 'CNPJ-1-SEQ/ANO' -> URL real do edital no portal PNCP."""
    m = re.match(r'(\d{14})-\d+-(\d+)/(\d{4})', str(nc or ''))
    if m:
        return 'https://pncp.gov.br/app/editais/%s/%s/%d' % (m.group(1), m.group(3), int(m.group(2)))
    return 'https://pncp.gov.br/app/editais'

# ---------- GEO: distancia haversine ate usinas/pedreiras ----------
HAVERSINE_AJUSTE = float(os.environ.get('HAVERSINE_AJUSTE_FATOR', '1.0'))
_GEO_CACHE = {}
_geocoder_inst = [None]
_GEO_OFF = [False]      # disjuntor: desliga geocoding se Nominatim cair
_GEO_FALHAS = [0]       # falhas/timeouts consecutivos
_BASE_MUN = {}          # base local IBGE: (nome_norm, UF) -> (lat, lon)
_BASE_IBGE = {}         # (nome_norm, UF) -> codigo_ibge (territory_id do Querido Diario)

def _carregar_base_mun():
    if _BASE_MUN:
        return _BASE_MUN
    try:
        import csv
        with open('municipios_br.csv', encoding='utf-8') as f:
            for row in csv.DictReader(f):
                k = (row['nome_norm'], row['uf'].upper())
                _BASE_MUN[k] = (float(row['lat']), float(row['lon']))
                cib = (row.get('codigo_ibge') or '').strip()
                if cib:
                    _BASE_IBGE[k] = cib
        print("  BASE MUNICIPIOS: %d carregados (geocoding local, sem rede) | %d com IBGE" % (len(_BASE_MUN), len(_BASE_IBGE)))
    except Exception as e:
        print("  BASE MUNICIPIOS indisponivel (%s) -- caira no Nominatim" % repr(e)[:50])
    return _BASE_MUN

_CONECT = {'de', 'do', 'da', 'dos', 'das', 'e'}
def _cap(nome_norm):
    """Title-case com conectores em minusculo: 'santa maria do suacui' -> 'Santa Maria do Suacui'."""
    ps = nome_norm.split()
    return ' '.join(p if (i and p in _CONECT) else p.capitalize() for i, p in enumerate(ps))

_MUNIS_UF = {}
def _munis_por_uf(uf):
    """Lista [(nome_norm, canonical)] dos municipios de uma UF, do maior nome p/ o menor
    (casa 'santa maria do suacui' antes de 'santa maria'). Cacheado."""
    uf = uf.upper()
    if uf in _MUNIS_UF:
        return _MUNIS_UF[uf]
    base = _carregar_base_mun()
    lst = [(nn, _cap(nn)) for (nn, u) in base if u == uf]
    lst.sort(key=lambda x: len(x[0]), reverse=True)
    _MUNIS_UF[uf] = lst
    return lst

def _muni_de_orgao(orgao, uf):
    """Extrai o municipio do nome do orgao ('PREFEITURA MUNICIPAL DE X' -> 'X') e VALIDA
    contra o IBGE (so aceita se X existe como municipio da UF). Fail-open: '' se nao achar,
    para nao inventar cidade errada (o edital segue sem distancia, nunca some por chute)."""
    o = ' ' + _n(orgao).strip() + ' '
    if not o.strip():
        return ''
    base = _carregar_base_mun()
    # 1) tenta o trecho apos o ultimo conector "de/do/da/das/dos" (sem o sufixo /UF)
    tail = re.split(r'\s+d[eoa]s?\s+', o)[-1].strip(' /.-')
    tail = re.sub(r'[/-]\s*[a-z]{2}\s*$', '', tail).strip()
    if tail and (tail, uf.upper()) in base:
        return _cap(tail)
    # 2) fallback: procura qualquer municipio da UF como trecho do nome (maior primeiro)
    for nn, canon in _munis_por_uf(uf):
        if len(nn) >= 4 and (' ' + nn + ' ') in o:
            return canon
    return ''

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

def _coord(v):
    """Parseia coordenada tolerando virgula decimal br. NUNCA usar get_all_records p/ coord:
    ele le '-21,215816' como -21215816 (virgula = milhar) e a distancia explode p/ ~7000km."""
    s = str(v).strip().replace(',', '.')
    try:
        f = float(s)
        return f if abs(f) > 0.01 else None
    except (ValueError, TypeError):
        return None


def _carregar_filiais(gc):
    for sid in [os.environ.get('GOOGLE_SHEETS_ID', ''), SHEET_ID]:
        if not sid:
            continue
        try:
            # get_all_VALUES (nao records): preserva a virgula decimal das coords
            vals = gc.open_by_key(sid).worksheet('Filiais').get_all_values()
            if len(vals) < 2:
                continue
            hdr = {h.strip().lower(): i for i, h in enumerate(vals[0])}
            def cel(row, k):
                i = hdr.get(k)
                return row[i] if (i is not None and i < len(row)) else ''
            out = []
            for row in vals[1:]:
                lat, lon = _coord(cel(row, 'latitude')), _coord(cel(row, 'longitude'))
                if lat is None or lon is None:
                    continue
                out.append({'nome': cel(row, 'nome'), 'municipio': cel(row, 'municipio'),
                            'uf': cel(row, 'uf'), 'tipo': cel(row, 'tipo'),
                            'latitude': lat, 'longitude': lon})
            if out:
                return out
        except gspread.WorksheetNotFound:
            continue
        except Exception as e:
            print("  FILIAIS erro em %s...: %s" % (sid[:12], repr(e)[:60]))
    print("  FILIAIS: aba 'Filiais' nao encontrada. Crie no Hub com colunas:")
    print("    nome | municipio | uf | latitude | longitude | tipo")
    return []

# REGRA DE ATENDIMENTO: concreto entregue por USINA (<=70 km), brita por PEDREIRA (<=500 km).
RAIO_USINA_KM = float(os.environ.get('RAIO_USINA_KM', '70'))
RAIO_PEDREIRA_KM = float(os.environ.get('RAIO_PEDREIRA_KM', '400'))
_KW_BRITA = ('brita', 'pedra britada', 'pedras britadas', 'pedrisco', 'po de pedra', 'bica corrida',
             'agregado', 'rachao', 'racho', 'cascalho', 'seixo', 'pedregulho', 'bgs', 'pedra de mao')
_KW_CONC = ('concreto', 'usinado', 'concretagem', 'fck', 'dosado', 'bombeado', 'betonado', 'central dosadora')


def _material(obj):
    o = _n(obj)
    return any(k in o for k in _KW_CONC), any(k in o for k in _KW_BRITA)  # (concreto, brita)


def _min_dist(coord, filiais, tipo):
    melhor, mf = None, None
    for f in filiais:
        if tipo not in _n(f.get('tipo', '')):
            continue
        try:
            km = _haversine_km(coord, (float(f['latitude']), float(f['longitude'])))
        except Exception:
            continue
        if melhor is None or km < melhor:
            melhor, mf = km, f
    return melhor, mf


def _enriquecer(r, filiais):
    mun, uf = r.get('municipio', ''), r.get('uf', '')
    conc0, brita0 = _material(r.get('objeto', ''))
    # BRITA SO NO RJ (regra inviolavel): a checagem so depende da UF (sempre existe), vale mesmo sem
    # geocodificar o municipio. Sem isto, orgao estadual/federal c/ municipio nao-parseavel ('') furava
    # o gate mais abaixo e brita fora-RJ vazava pro boletim (via Licitar/PNCP-search).
    if brita0 and not conc0 and uf and uf.upper() != 'RJ':
        return None
    if not mun or not uf or not filiais:
        return r                                  # sem municipio/filiais -> nao da p/ validar raio, mantem
    coord = _geocode(mun, uf)
    if not coord:
        return r                                  # sem geo (raro c/ base IBGE) -> mantem sem distancia
    conc, brita = _material(r.get('objeto', ''))
    if not conc and not brita:
        conc = True                               # indefinido -> trata como concreto (raio mais restrito)
    opcoes = []
    if conc:
        d, f = _min_dist(coord, filiais, 'usina')
        if d is not None and d <= RAIO_USINA_KM:
            opcoes.append((d, f, 'usina'))
    # BRITA SO NO RJ (regra do usuario 2026-07-04): as pedreiras servem so o mercado do RJ.
    # Edital de brita fora do RJ nao entra pela pedreira (se for tb concreto perto de usina, entra por ali).
    if brita and uf.upper() == 'RJ':
        d, f = _min_dist(coord, filiais, 'pedreira')
        if d is not None and d <= RAIO_PEDREIRA_KM:
            opcoes.append((d, f, 'pedreira'))
    if not opcoes:
        return None                               # FORA do raio de atendimento -> some do boletim
    d, f, tp = min(opcoes, key=lambda x: x[0])
    r['distancia_km'] = round(d, 1)
    r['filial_proxima'] = '%s (%s/%s)' % (f.get('nome', ''), f.get('municipio', ''), f.get('uf', uf))
    r['tipo_atendimento'] = tp
    return r

AGORA = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
hoje = datetime.date.today()
HOJE_ISO = hoje.isoformat()
# Janela 10 dias (era 14): SP tem ~65 paginas de 50 em 14 dias (PNCP trava tamanhoPagina em 50) e
# trunca sob instabilidade -> perdia editais nas paginas altas (ex: Cajamar nc 38, concreto usinado
# 562 m3 escondido em "obras de infraestrutura"). Menos dias = menos paginas = SP COMPLETA. Coletamos
# 7x/dia, entao 10 dias e folga de sobra p/ pegar todo edital novo; a historia acumulada nunca some.
ini = hoje - datetime.timedelta(days=int(os.environ.get('PNCP_JANELA_TOTAL_DIAS', '10')))
registros = []      # cada um: dict fonte/uf/municipio/orgao/objeto/data_sessao/data_pub/numero/link/uid
PNCP_TRUNC = []     # UFs em que o PNCP truncou (integra=False) -> vira ALERTA
QD_TRUNC   = []     # lotes/cidades do QD nao varridos (budget/falha) -> vira ALERTA (espelha PNCP_TRUNC)

# orcamento de tempo POR FONTE: nenhuma fonte (ex: PNCP fora do ar) monopoliza o tempo das outras.
# 600s (era 900): o deadline-guard do pncp_get ja impede o desperdicio numa consulta caida, e sobra
# tempo p/ o fallback coleta_pncp_search() (porta de busca) rodar quando o /api/consulta v1 trava.
PNCP_BUDGET_S    = float(os.environ.get('PNCP_BUDGET_S', '600'))
LICITAR_BUDGET_S = float(os.environ.get('LICITAR_BUDGET_S', '300'))  # 5 min
def _prazo(segundos):
    fim = time.monotonic() + segundos
    return lambda: time.monotonic() < fim

# ---------- 1) PNCP ----------
# A consulta do PNCP mata a conexao (~83s) quando o payload e grande (14d x UF numa tacada).
# Solucao: quebrar em JANELAS de 7 dias (payload menor COMPLETA) + retry de throttle
# ('pagina vazia com totalRegistros>0' = throttle, nao fim dos dados -- licao do comparativo.py).
PNCP_TIMEOUT_S = float(os.environ.get('PNCP_TIMEOUT_S', '40'))
PNCP_JANELA_DIAS = int(os.environ.get('PNCP_JANELA_DIAS', '5'))   # chunks menores (5d) = menos paginas/query = completa sob instabilidade

def pncp_get(url, ok_tempo=None):
    """1 pagina com retry. Trata timeout/pagina-de-erro e throttle (vazio c/ totalRegistros>0).
    ok_tempo: se dado, corta os retries quando o budget de tempo acabou -> uma pagina morta nao queima
    ~40s x5 e nao mata de fome as UFs seguintes (bug do backbone zerando: 'COLETOU 0 + TRUNCOU SP')."""
    last = None
    for att in range(5):
        if ok_tempo is not None and not ok_tempo(): return last   # budget acabou: cede tempo p/ as outras UFs
        try:
            r = requests.get(url, timeout=PNCP_TIMEOUT_S, headers=UA)
        except Exception:
            time.sleep(1.5 * (att + 1)); continue
        if r.status_code == 204:
            return {'data': [], 'totalRegistros': 0, 'totalPaginas': 0}
        if r.status_code != 200:
            time.sleep(1.5 * (att + 1)); continue
        try:
            j = r.json()
        except Exception:                      # sob carga o PNCP responde HTML de erro (nao-JSON)
            time.sleep(1.8 * (att + 1)); continue
        last = j
        if not (j.get('data') or []) and (j.get('totalRegistros') or 0) > 0:
            time.sleep(1.8 * (att + 1)); continue   # throttle: ha registros, mas a pagina veio vazia
        return j
    return last

def _janelas_pncp(dias_chunk):
    """Quebra [ini..hoje] em sub-janelas de N dias, sem sobreposicao (payload menor)."""
    js, fim = [], hoje
    while fim >= ini:
        comeco = max(ini, fim - datetime.timedelta(days=dias_chunk - 1))
        js.append((comeco, fim))
        fim = comeco - datetime.timedelta(days=1)
    return js

# ---- ITEM-CHECK (backtest 2026-07-07, conselho): objeto e rotulo de conveniencia do municipio;
# a decisao de fornecer e sobre a LINHA/ITEM. Pedra Bonita/MG tinha objeto "artefatos de concreto,
# drenagem" (bate EXCL -> hoje DESCARTADO) mas item "CONCRETO USINADO BOMBEAVEL qt 300" -> miss real.
# SEGURO POR CONSTRUCAO: so abrimos itens de editais que HOJE JA SAO DESCARTADOS pelo objeto mas sao
# de "familia construcao" (podem esconder linha de usinado). So pode ADICIONAR um Pedra Bonita, nunca
# quebra caso que ja funciona nem mostra falso. Roda DEPOIS da listagem + raio (budget/teto proprios),
# so nos in-raio (punhado/dia), fallback = descartar (= comportamento de hoje). Classificacao item-a-item
# em filtro_concreto.py (fixture-testado: Pedra Bonita ENTRA, Cajamar/drenagem-berco FORA).
PNCP_ITENS = os.environ.get('PNCP_ITENS', '1') == '1'
PNCP_ITENS_MAX = int(os.environ.get('PNCP_ITENS_MAX', '40'))       # teto de aberturas/rodada
PNCP_ITENS_BUDGET_S = float(os.environ.get('PNCP_ITENS_BUDGET_S', '120'))  # tempo SEPARADO (nao rouba listagem)
_FAMILIA_CONSTRUCAO = ("concreto", "brita", "artefato", "drenagem", "pavimenta", "galeria", "aduela",
                       "tubo", "pedra", "agregado", "obra", "terraplen", "pluvial", "meio-fio", "meio fio",
                       "sarjeta", "guia", "calcamento", "bloquete", "usina")

def _familia_construcao(objeto):
    """Objeto que hoje e descartado pode esconder linha de usinado? So vale abrir itens se for
    de construcao (senao e fralda/medicina/etc -> nem toca no PNCP)."""
    t = _n(objeto)
    if any(e in t for e in HARD_EXCL): return False   # asfalto: nunca e nosso, nem gasta request
    return any(f in t for f in _FAMILIA_CONSTRUCAO)

def _seq_de_nc(nc):
    m = re.match(r'(\d+)-\d+-(\d+)/(\d+)', str(nc or ''))
    return (m.group(1), m.group(3), int(m.group(2))) if m else None

def pncp_itens_lista(nc):
    """Lista de itens de uma compra (1 request). None se falhar -> chamador faz fallback."""
    p = _seq_de_nc(nc)
    if not p: return None
    cnpj, ano, seq = p
    url = 'https://pncp.gov.br/api/pncp/v1/orgaos/%s/compras/%s/%d/itens?pagina=1&tamanhoPagina=50' % (cnpj, ano, seq)
    for att in range(3):
        try:
            r = requests.get(url, timeout=PNCP_TIMEOUT_S, headers=UA)
            if r.status_code == 200:
                j = r.json()
                return j if isinstance(j, list) else None
        except Exception:
            pass
        time.sleep(1.2 * (att + 1))
    return None

def resolver_pendentes(final):
    """Roda DEPOIS do raio, so nos candidatos 'pendente_item' que sobraram no raio (punhado).
    Abre os itens e confirma se ha linha de concreto usinado/brita PRODUTO. So ADICIONA:
    - confirmado -> mantem, enriquece o objeto com o item-gatilho (marca via_item).
    - nao confirmado / PNCP falhou / teto/tempo estourou -> DESCARTA (= comportamento de hoje).
    Budget e teto PROPRIOS: nunca rouba tempo da listagem (furo de carga do Contrario)."""
    pend = [r for r in final if r.get('pendente_item')]
    if not pend:
        for r in final: r.pop('pendente_item', None)
        return final, 0, 0
    ok_tempo = _prazo(PNCP_ITENS_BUDGET_S)
    abertos = confirmados = descartados = 0
    mantidos = []
    for r in final:
        if not r.get('pendente_item'):
            mantidos.append(r); continue
        if abertos >= PNCP_ITENS_MAX or not ok_tempo():
            descartados += 1; continue                      # fallback: nao deu p/ confirmar -> fora (= hoje)
        itens = pncp_itens_lista(r.get('numero', '')); abertos += 1
        if itens is None:
            descartados += 1; continue                      # PNCP falhou -> fallback = fora
        entra, item = edital_entra_por_itens(itens)
        if entra:
            desc0 = (item.get('descricao') if isinstance(item, dict) else str(item)) or ''
            ci, bi = _material(desc0)
            if bi and not ci and (r.get('uf') or '').upper() != 'RJ':
                descartados += 1; continue         # brita fora do RJ (regra inviolavel) -> nao entra pelo item
            desc = re.sub(r'\s+', ' ', desc0).strip()[:90]     # descricoes do PNCP sao longas/repetidas
            porte = porte_m3(item)      # '~300 m3' se for m3; '' senao. NUNCA vira R$ (teto, nao compra)
            # porte PRIMEIRO (sinal que importa), depois a descricao curta -- cabe nos 300 chars
            selo = (' | achado no item: %s (teto) — %s' % (porte, desc)) if porte else (' | achado no item: %s' % desc)
            r['objeto'] = (r.get('objeto', '')[:200] + selo)[:300]
            r['via_item'] = True
            r.pop('pendente_item', None)
            mantidos.append(r); confirmados += 1
        else:
            descartados += 1
    print("  Item-check: %d itens abertos -> %d confirmados (via item), %d descartados" %
          (abertos, confirmados, descartados))
    return mantidos, confirmados, descartados

PNCP_MAX_PAGINAS = int(os.environ.get('PNCP_MAX_PAGINAS', '60'))   # MG tem ~44 pag PE; teto 40 truncava
RADAR_ESTADO_PATH = 'radar_estado.json'   # estado leve entre as 7 coletas/dia (committado pelo Action)

def _ufs_rotacionadas():
    """Rotaciona a ordem das UFs a cada coleta. Se o orcamento de tempo estoura, a UF starvada
    hoje foi a primeira ontem -> ao longo das 7 coletas/dia toda UF pega a coleta 'inteira'
    varias vezes. Sem isso, a mesma UF (a ultima da lista) truncava sempre no mesmo lugar."""
    try:
        with open(RADAR_ESTADO_PATH, encoding='utf-8') as f:
            k = int(json.load(f).get('rot', 0))
    except Exception:
        k = 0
    try:
        with open(RADAR_ESTADO_PATH, 'w', encoding='utf-8') as f:
            json.dump({'rot': (k + 1) % len(UFS)}, f)
    except Exception:
        pass
    return UFS[k % len(UFS):] + UFS[:k % len(UFS)]

def coleta_pncp():
    n = 0
    ok_tempo = _prazo(PNCP_BUDGET_S)
    seen = set()
    ufs = _ufs_rotacionadas()
    for i, uf in enumerate(ufs):
        if not ok_tempo():
            print("  PNCP: orcamento de tempo esgotado")
            for u in ufs[i:]: PNCP_TRUNC.append(u + '(pulada-budget)')   # UF nao varrida -> GRITA no alerta (nao some em silencio)
            break
        uf_falhou = False
        n_uf = n
        pag_max_vista = 0   # maior 'totalPaginas' visto -> detecta truncamento estrutural (nao so falha de rede)
        pag_ok = 0          # ate onde realmente lemos
        for (d_ini, d_fim) in _janelas_pncp(PNCP_JANELA_DIAS):
            if not ok_tempo(): break
            pag, tot = 1, 1
            while pag <= tot and pag <= PNCP_MAX_PAGINAS:
                if not ok_tempo(): break
                url = ('https://pncp.gov.br/api/consulta/v1/contratacoes/publicacao'
                       '?dataInicial=%s&dataFinal=%s&codigoModalidadeContratacao=6&uf=%s&pagina=%d&tamanhoPagina=50'
                       % (d_ini.strftime('%Y%m%d'), d_fim.strftime('%Y%m%d'), uf, pag))
                j = pncp_get(url, ok_tempo)
                if j is None: uf_falhou = True; break     # janela quebrou de vez
                tot = j.get('totalPaginas') or tot
                pag_max_vista = max(pag_max_vista, tot)
                data = j.get('data') or []
                for d in data:
                    nc = str(d.get('numeroControlePNCP') or '')
                    if nc and nc in seen: continue
                    if nc: seen.add(nc)
                    objeto = d.get('objetoCompra') or ''
                    aceito = rel(objeto)
                    if aceito and _so_brita_com_excl(objeto):
                        aceito = False              # brita + contexto de berco/tubo -> confirma no item-check, nao aceita pelo objeto
                    # pendente = objeto descartado hoje, MAS de familia-construcao (pode esconder
                    # linha de usinado). So confirma abrindo os itens DEPOIS, e so se ficar no raio.
                    pendente = (not aceito) and PNCP_ITENS and _familia_construcao(objeto)
                    if not aceito and not pendente: continue
                    uo = d.get('unidadeOrgao') or {}; oe = d.get('orgaoEntidade') or {}
                    registros.append({'fonte': 'PNCP', 'uf': uo.get('ufSigla', uf), 'municipio': uo.get('municipioNome', ''),
                                      'orgao': oe.get('razaoSocial', ''), 'objeto': objeto[:300],
                                      'data_sessao': iso(d.get('dataEncerramentoProposta') or d.get('dataAberturaProposta')),
                                      'data_pub': iso(d.get('dataPublicacaoPncp')), 'numero': nc,
                                      'link': _pncp_link(nc),
                                      'valor': d.get('valorTotalEstimado') or '',
                                      'modalidade': d.get('modalidadeNome') or 'Pregao Eletronico',
                                      'pendente_item': pendente,   # resolver_pendentes() decide depois do raio
                                      'uid': 'PNCP:' + nc}); n += 1
                if not data: break
                pag_ok = max(pag_ok, pag)
                pag += 1; time.sleep(0.3)
            time.sleep(0.2)
        # TRUNCOU: (a) falhou de vez sem trazer nada (rede), OU (b) truncamento SEVERO -- lemos
        # menos de 80% das paginas disponiveis (teto/tempo). O limiar de 80% evita falso alarme
        # em UF grande onde as ultimas paginas quase nunca tem concreto; so avisa quando o buraco
        # e grande de verdade, pra diretoria nao ler 'cobertura parcial' todo dia (fadiga de alerta).
        severo = pag_max_vista > 0 and pag_ok > 0 and pag_ok < 0.8 * pag_max_vista
        if (uf_falhou and n == n_uf) or severo:
            PNCP_TRUNC.append('%s(%d/%d pag)' % (uf, pag_ok, pag_max_vista) if severo else uf)
        time.sleep(0.3)
    return n

# ---------- 1b) PNCP porta alternativa: /api/search (index ElasticSearch do frontend pncp.gov.br) ----------
# Fallback AUTOMATICO quando o /api/consulta v1 cai/trunca. Verificado ao vivo 2026-07-08: no MESMO
# minuto em que o consulta deu ReadTimeout, a search respondeu 200 (SP concreto PE = 99 resultados).
# MESMA base, MESMO numero_controle_pncp -> o dedup por uid 'PNCP:'+nc casa direto e pncp_itens_lista()
# (m3) aponta pro mesmo backend UP. Regras: status=recebendo_proposta e OBRIGATORIO (sem ele -> HTTP 400);
# modalidades=6 = PE (mesmo codigo do consulta); UMA UF por request (multi-UF retorna 0); q= busca
# textual server-side que inclui os ITENS -> pega concreto escondido em objeto vago (classe Cajamar).
UA_SEARCH = dict(UA, **{'Accept': 'application/json, text/plain, */*', 'Referer': 'https://pncp.gov.br/app/editais'})
PNCP_SEARCH_Q = ('concreto', 'brita')
PNCP_SEARCH_TAMPAG = 50

def _pncp_search_get(uf, q, pagina, ok_tempo=None):
    url = ('https://pncp.gov.br/api/search/?tipos_documento=edital&ordenacao=-data'
           '&pagina=%d&tam_pagina=%d&status=recebendo_proposta&ufs=%s&modalidades=6&q=%s'
           % (pagina, PNCP_SEARCH_TAMPAG, uf, requests.utils.quote(q)))
    for att in range(3):
        if ok_tempo is not None and not ok_tempo(): return None
        try:
            r = requests.get(url, timeout=PNCP_TIMEOUT_S, headers=UA_SEARCH)
            if r.status_code == 200: return r.json()
            time.sleep(1.2 * (att + 1))
        except Exception:
            time.sleep(1.2 * (att + 1))
    return None

def coleta_pncp_search():
    """Porta de busca do PNCP -- so como FALLBACK do coleta_pncp (consulta v1). Espelha o shape do
    registro do coleta_pncp; a classificacao reusa rel()/_familia_construcao (o MESMO gate) e o
    item-check confirma os pendentes in-raio depois. Dedup final por uid 'PNCP:'+nc."""
    n = 0
    ok_tempo = _prazo(PNCP_BUDGET_S)
    seen = set(r.get('numero') for r in registros if r.get('fonte') == 'PNCP' and r.get('numero'))
    for uf in _ufs_rotacionadas():
        if not ok_tempo(): print("  PNCP-search: orcamento de tempo esgotado"); break
        for q in PNCP_SEARCH_Q:
            if not ok_tempo(): break
            pag, tot = 1, 1
            while pag <= tot and pag <= PNCP_MAX_PAGINAS:
                if not ok_tempo(): break
                j = _pncp_search_get(uf, q, pag, ok_tempo)
                if j is None: break
                total = j.get('total') or 0
                tot = (total + PNCP_SEARCH_TAMPAG - 1) // PNCP_SEARCH_TAMPAG if total else 1
                items = j.get('items') or []
                if not items: break
                for it in items:
                    nc = str(it.get('numero_controle_pncp') or '')
                    if not nc or nc in seen: continue
                    seen.add(nc)
                    objeto = it.get('description') or it.get('title') or ''
                    aceito = rel(objeto)
                    if aceito and _so_brita_com_excl(objeto):
                        aceito = False              # brita + contexto de berco/tubo -> item-check (mesmo gate do consulta)
                    pendente = (not aceito) and PNCP_ITENS and _familia_construcao(objeto)
                    if not aceito and not pendente: continue
                    registros.append({'fonte': 'PNCP', 'uf': it.get('uf', uf),
                                      'municipio': it.get('municipio_nome', ''),
                                      'orgao': it.get('orgao_nome', ''), 'objeto': objeto[:300],
                                      'data_sessao': iso(it.get('data_fim_vigencia')),
                                      'data_pub': iso(it.get('data_publicacao_pncp')), 'numero': nc,
                                      'link': _pncp_link(nc), 'valor': it.get('valor_global') or '',
                                      'modalidade': it.get('modalidade_licitacao_nome') or 'Pregao Eletronico',
                                      'pendente_item': pendente, 'uid': 'PNCP:' + nc}); n += 1
                pag += 1; time.sleep(0.3)
    return n

# ---------- 2) Querido Diario ----------
QD_URL = 'https://api.queridodiario.ok.org.br/gazettes'
QD_QUERY = '"concreto usinado" OR brita OR "pedras britadas" OR "concreto bombeavel" OR "concreto dosado" OR "pedra britada"'
QD_BATCH = int(os.environ.get('QD_BATCH', '40'))       # territory_ids por request (URL nao pode ser gigante)
_RAIO_IBGE = [None]

def _raio_ibge_codes():
    """Codigos IBGE (territory_id do QD) das cidades no raio -> varredura EXAUSTIVA cidade-a-cidade.
    haversine e um SUPERSET do raio de rota real (rota >= reta), entao cobre toda cidade que o filtro
    final manteria -- zero cidade cega. Cacheado. Retorna [] se nao der (cai na busca global)."""
    if _RAIO_IBGE[0] is not None:
        return _RAIO_IBGE[0]
    _carregar_base_mun()                                # popula _BASE_MUN e _BASE_IBGE
    try:
        gc = gspread.service_account(filename=os.environ.get('GOOGLE_SHEETS_CREDENTIALS_PATH', 'credenciais/service_account.json'))
        filiais = _carregar_filiais(gc)
    except Exception as e:
        print("  QD-IBGE: sem filiais (%s) -> busca global" % repr(e)[:50])
        _RAIO_IBGE[0] = []
        return []
    usinas = [(f['latitude'], f['longitude']) for f in filiais if 'pedreira' not in _n(f.get('tipo', ''))]
    pedreiras = [(f['latitude'], f['longitude']) for f in filiais if 'pedreira' in _n(f.get('tipo', ''))]
    cods = set()
    for (nome, uf), (la, lo) in _BASE_MUN.items():
        cib = _BASE_IBGE.get((nome, uf))
        if not cib:
            continue
        c = (la, lo)
        dentro = any(_haversine_km(c, u) <= RAIO_USINA_KM for u in usinas)
        if not dentro and uf == 'RJ':                   # brita so RJ
            dentro = any(_haversine_km(c, p) <= RAIO_PEDREIRA_KM for p in pedreiras)
        if dentro:
            cods.add(cib)
    _RAIO_IBGE[0] = sorted(cods)
    print("  QD-IBGE: %d cidades no raio p/ varrer (usinas %d, pedreiras %d)" % (len(cods), len(usinas), len(pedreiras)))
    return _RAIO_IBGE[0]

def _qd_ingest(gazettes):
    """Filtra e adiciona as gazetas do QD (mesma logica de sempre: sinal forte + termo de licitacao)."""
    n = 0
    for g in gazettes or []:
        exc = ' '.join(g.get('excerpts') or [])
        # QD e RUIDOSO (texto integral) -> exige SINAL FORTE (score 3), senao entra lixo administrativo.
        if score(exc) < 3:
            continue
        if not re.search(r'(pregao|preg[ao]o|licita|edital|tomada de pre|aviso)', exc, re.I):
            continue
        obj = re.sub(r'\s+', ' ', exc)[:300]
        uid = 'QD:' + hashlib.md5(norm(g.get('territory_name', '') + obj).encode()).hexdigest()[:16]
        if any(r.get('uid') == uid for r in registros):
            continue
        registros.append({'fonte': 'QUERIDO_DIARIO', 'uf': (g.get('state_code') or '').upper(),
                          'municipio': g.get('territory_name', ''),
                          'orgao': (g.get('territory_name', '') + ' (Diario Oficial)'), 'objeto': obj,
                          'data_sessao': '', 'data_pub': iso(g.get('date')), 'numero': '',
                          'link': g.get('txt_url') or g.get('url') or '', 'uid': uid})
        n += 1
    return n

def coleta_qd():
    """Querido Diario EXAUSTIVO por IBGE: varre CADA cidade do raio (territory_ids) em vez de uma
    busca global de texto -- fecha o interior <20k que o PNCP nao ve ate 2027. Meta: zero cidade
    cega. Fallback: se nao houver codigos de raio (sem filiais/IBGE), usa a busca global antiga."""
    n = 0
    cods = _raio_ibge_codes()
    if not cods:
        try:
            r = requests.get(QD_URL, params={'querystring': QD_QUERY, 'published_since': ini.isoformat(),
                             'published_until': hoje.isoformat(), 'size': 200, 'sort_by': 'descending_date'},
                             timeout=50, headers=UA)
            if r.status_code == 200:
                gz = [g for g in (r.json() or {}).get('gazettes', []) if (g.get('state_code') or '').upper() in UFS]
                n = _qd_ingest(gz)
        except Exception as e:
            print("  QD erro (global):", repr(e)[:80])
        return n
    ok_tempo = _prazo(float(os.environ.get('QD_BUDGET_S', '180')))
    lotes = 0
    total = (len(cods) + QD_BATCH - 1) // QD_BATCH       # lotes planejados -> mede a cobertura
    falhos = 0
    for i in range(0, len(cods), QD_BATCH):
        if not ok_tempo():
            print("  QD-IBGE: orcamento de tempo esgotado no lote %d" % lotes)
            QD_TRUNC.append('budget: %d/%d lotes varridos' % (lotes, total)); break   # GRITA: varredura parcial
        lote = cods[i:i + QD_BATCH]
        params = [('querystring', QD_QUERY), ('published_since', ini.isoformat()),
                  ('published_until', hoje.isoformat()), ('size', 200), ('sort_by', 'descending_date')]
        params += [('territory_ids', c) for c in lote]
        lote_ok = False
        for att in range(3):
            try:
                r = requests.get(QD_URL, params=params, timeout=25, headers=UA)   # 25s (era 50): lote travado nao come o budget
                if r.status_code == 200:
                    n += _qd_ingest((r.json() or {}).get('gazettes', []))
                    lote_ok = True; break
                time.sleep(1.2 * (att + 1))
            except Exception:
                time.sleep(1.2 * (att + 1))
        if not lote_ok: falhos += 1                       # lote que falhou 3x -> cidades daquele lote ficaram cegas
        lotes += 1
        time.sleep(0.3)
    if falhos: QD_TRUNC.append('%d lote(s) sem resposta' % falhos)
    print("  QD-IBGE: %d lotes varridos -> %d editais no raio%s" % (lotes, n, (' [TRUNCOU: %s]' % '; '.join(QD_TRUNC)) if QD_TRUNC else ''))
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
                    registros.append({'fonte': 'LICITAR_DIGITAL', 'uf': uf,
                                      'municipio': _muni_de_orgao(it.get('organizationName', ''), uf),
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
c_pncp = coleta_pncp()
if c_pncp == 0 or PNCP_TRUNC:      # consulta v1 caiu/truncou -> abre a porta alternativa (mesma base, backend de busca)
    c_sea = coleta_pncp_search()
    print("  PNCP-search (fallback consulta v1): +%d editais pela porta de busca" % c_sea)
    c_pncp += c_sea
c_qd, c_lic = coleta_qd(), coleta_licitar()
print("PNCP:", c_pncp, "| Querido Diario:", c_qd, "| Licitar Digital:", c_lic)

# ---------- dedup POR IDENTIDADE (uid + texto COMPLETO; NUNCA por prefixo truncado) ----------
# precedencia: PNCP (origem legal) > Licitar > Querido Diario
ordem = {'PNCP': 0, 'LICITAR_DIGITAL': 1, 'QUERIDO_DIARIO': 2}
registros.sort(key=lambda x: ordem.get(x['fonte'], 9))
# DEDUP QD x PNCP por MUNICIPIO (conselho unanime 2026-07-08): o dedup por texto exato NUNCA casa
# o QD "texto solto" (excerpt do diario) com o objeto estruturado do PNCP -> quando o PNCP voltar
# (hoje deu 0), o MESMO municipio apareceria 2x (1 PNCP + 1 QD) = boletim duplicado, mata a
# credibilidade do veredito. Regra: o QD so vale onde o PNCP NAO viu (municipio sem edital PNCP) --
# que e justamente o valor do QD (interior <20k que o PNCP nao cobre ate 2027). Onde ha PNCP, ele
# e a fonte autoritativa; o QD daquele municipio e descartado.
# chave (municipio, UF): nome de cidade NAO e unico (Rio Claro SP/RJ, Mesquita MG/RJ, Valenca BA/RJ,
# Cantagalo MG-PR-RJ) -> a chave so-nome apagava o QD do homonimo do RJ-core. E ignora o PNCP
# 'pendente_item' (ainda nao confirmado): senao um objeto vago do PNCP pre-suprime o QD e, se o
# item-check depois descartar, a cidade fica CEGA (sem nenhuma fonte).
mun_pncp = set((_n(r.get('municipio', '')), (r.get('uf') or '').upper()) for r in registros
               if r.get('fonte') == 'PNCP' and r.get('municipio') and not r.get('pendente_item'))
vistos_uid, vistos_txt, mirror, final = set(), set(), {}, []
n_qd_sup = 0
for r in registros:
    if r.get('fonte') == 'QUERIDO_DIARIO' and (_n(r.get('municipio', '')), (r.get('uf') or '').upper()) in mun_pncp:
        n_qd_sup += 1; continue                            # PNCP ja cobre esse municipio+UF -> nao duplica
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
print("Total bruto:", len(registros), "| apos dedup+recencia:", len(final),
      "| QD suprimido (municipio ja no PNCP):", n_qd_sup)

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
if QD_TRUNC: alertas.append("QD VARRIDO PARCIAL (cidade cega possivel): " + ", ".join(QD_TRUNC))
alerta_txt = " | ".join(alertas) if alertas else "OK - todas as fontes saudaveis"

# log de saude -> baseline pra proxima rodada (compara RAW = a fonte respondeu?).
# BUG corrigido 2026-07-07: append_row SEM table_range deslizava as colunas a cada rodada ->
# aba sem cabecalho, dados espalhados a direita -> diario.json lia vazio e o health-check ficava
# CEGO justo quando devia gritar. Fix: (1) auto-reparo do cabecalho; (2) append ancorado em 'A1'.
HDR_SAUDE = ['QUANDO', 'TOTAL_UNICO', 'RAW_POR_FONTE(json)', 'POR_UF(json)', 'PNCP_raw', 'LICITAR_raw', 'QD_raw', 'TRUNCOU', 'ALERTA']
if (not prev) or (not prev[0]) or (prev[0][0].strip().upper() != 'QUANDO'):
    ws_saude.clear()                                  # cabecalho ausente/corrompido -> reescreve limpo
    ws_saude.update(values=[HDR_SAUDE], range_name='A1')
ws_saude.append_row([AGORA, len(final), json.dumps(raw), json.dumps(poruf),
                     c_pncp, c_lic, c_qd, ", ".join(PNCP_TRUNC + QD_TRUNC), alerta_txt],
                    table_range='A1', value_input_option='USER_ENTERED')   # ancora em A1 -> nao desliza

# ---------- enriquece com distancia geo + REGRA DE RAIO (concreto<=70km usina, brita<=300km pedreira) ----------
if filiais:
    uniq_mun = len(set((r.get('municipio',''), r.get('uf','')) for r in final if r.get('municipio')))
    print("Geocodificando %d municipios unicos..." % uniq_mun)
    antes = len(final)
    final = [x for x in (_enriquecer(r, filiais) for r in final) if x is not None]
    print("Regra de raio: %d dentro do raio de atendimento (%d removidos fora)" % (len(final), antes - len(final)))
    # item-check SO nos pendentes in-raio (objeto descartado mas familia-construcao): confirma
    # concreto usinado/brita escondido nos itens (ex: Pedra Bonita). So adiciona; fallback = fora.
    final, _conf, _desc = resolver_pendentes(final)

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
