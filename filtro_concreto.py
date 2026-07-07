# -*- coding: utf-8 -*-
"""Classificador de concreto/brita — fonte unica de verdade (objeto E item).

Nasceu do backtest 2026-07-07 (conselho): a decisao de fornecer e sobre a LINHA/ITEM,
nao sobre o resumo (objetoCompra). Um edital de objeto "artefatos de concreto, drenagem"
(Pedra Bonita/MG) escondia no item 5 "CONCRETO USINADO BOMBEAVEL qt 300" -> miss real.
Ler item-a-item pega isso SEM o falso positivo do texto-embolado (drenagem lista brita p/
berco de tubo). Chave do conserto: no nivel do ITEM, o contexto de pre-moldado/drenagem/
berco e testado ANTES de 'brita' (no score() do objeto a ordem era KW3 antes de EXCL, o que
fazia 'brita de berco' entrar como concreto)."""
import re
import unicodedata


def _n(s):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKD', str(s or '')).encode('ascii', 'ignore').decode().lower())


# ---- listas (espelham radar.py; este modulo passa a ser a fonte unica quando o radar importar) ----
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
        "agregadora", "agregador de", "plataforma digital", "plano de assistencia", "assistencia medica",
        "plano de saude", "plano odontologico", "academia", "diario oficial", "comunicado", "art. 117")
HARD_EXCL = ("asfalt", "cbuq", "betumin", "massa asfaltica", "emulsao asfaltica")


def score(texto):
    """Score do OBJETO/resumo (mantido identico ao radar). 3=certo, 2=provavel c/ contexto, 0=fora."""
    t = _n(texto)
    if any(e in t for e in HARD_EXCL):
        return 0
    if any(k in t for k in KW3):
        return 3
    if any(e in t for e in EXCL):
        return 0
    if any(k in t for k in KW2_BRITA):
        return 2
    if any(k in t for k in KW2_CONC) and any(c in t for c in CONTEXTO):
        return 2
    return 0


def rel(t):
    return score(t) >= 2


# ---- classificador de ITEM (linha do edital) ----
# Contexto que denuncia material ACESSORIO (pre-moldado, drenagem, berco/assentamento de tubo):
# testado ANTES de 'brita' p/ NAO deixar 'brita de berco de tubo' entrar como concreto (furo
# estrutural do score() do objeto, apontado pelo conselho). So sobra brita/concreto como PRODUTO.
_ITEM_EXCL_CTX = EXCL + (
    "assentamento", "berco", "lastro", "galeria", "colchao drenante", "dreno", "drenagem",
    "reaterro", "regularizacao do fundo", "envelopamento", "guia", "bloco", "banco", "banqueta",
    "lixeira", "floreira", "bebedouro", "mesa", "paraciclo", "bicicletario", "mobiliario")


def classificar_item(descricao):
    """Score de UM item isolado. Exige-se >=3 (produto explicito) p/ admitir o edital.
    Ordem: HARD_EXCL -> contexto acessorio/pre-moldado -> KW3 (concreto usinado/brita produto)."""
    t = _n(descricao)
    if any(e in t for e in HARD_EXCL):
        return 0
    if any(e in t for e in _ITEM_EXCL_CTX):
        return 0                       # pre-moldado/drenagem/berco/mobiliario -> nunca via brita acessoria
    if any(k in t for k in KW3):
        return 3                       # concreto usinado/bombeavel/fck OU brita como PRODUTO
    if any(k in t for k in KW2_CONC) and any(c in t for c in CONTEXTO):
        return 3                       # 'concreto ... fck 25 mpa m3' explicito
    return 0


def edital_entra_por_itens(itens):
    """True + o ITEM-GATILHO (dict completo: descricao/quantidade/unidade) se ALGUM item e
    concreto usinado/brita PRODUTO (score>=3). O dict permite ao chamador mostrar o PORTE
    (ex: '~300 m3') sem inventar valor. `itens` = lista de dicts com 'descricao'."""
    for it in itens or []:
        d = it.get('descricao') if isinstance(it, dict) else it
        if classificar_item(d) >= 3:
            return True, (it if isinstance(it, dict) else {'descricao': d})
    return False, None


def porte_m3(item):
    """String de porte a partir do item-gatilho: '~300 m3' se a unidade for m3; senao ''.
    NUNCA inventa valor em R$ (decisao do conselho: quantidade de registro de precos e TETO,
    nao compra); serve so como PORTE grosseiro p/ priorizar."""
    if not isinstance(item, dict):
        return ''
    q = item.get('quantidade')
    u = _n(item.get('unidadeMedida') or '')
    try:
        q = float(q)
    except (TypeError, ValueError):
        return ''
    if q <= 0 or u not in ('m3', 'm³', 'mc', 'metro cubico', 'metros cubicos'):
        return ''
    return '~%s m3' % (int(q) if q == int(q) else round(q, 1))
