# -*- coding: utf-8 -*-
"""INTELIGENCIA COMPETITIVA: le do PNCP os RESULTADOS/homologacoes de pregoes (quem GANHOU
e a que preco), pra depois calibrar o veredito e o "mapa de vazios" (cidades onde um
concorrente venceu no nosso raio).

DESCOBERTA (sonda ao vivo 2026-07-07, editais reais de concreto/brita em MG):
  - Endpoint de RESULTADOS por item (o que funciona):
      GET https://pncp.gov.br/api/pncp/v1/orgaos/{cnpj}/compras/{ano}/{seq}/itens/{numeroItem}/resultados
    Retorna 200 + LISTA (JSON) quando homologado; 204 (sem corpo) quando ainda NAO ha resultado.
  - Pre-check barato no nivel do item (evita bater no /resultados de item nao homologado):
      GET .../compras/{ano}/{seq}/itens?pagina=1&tamanhoPagina=50
    Cada item traz `temResultado` (bool) e `situacaoCompraItemNome` ('Em andamento' vs homologado).
  - Formato do VENCEDOR/VALOR dentro de cada resultado (campos usados):
      nomeRazaoSocialFornecedor  -> razao social do vencedor
      niFornecedor               -> CNPJ (ou CPF) do vencedor
      valorTotalHomologado       -> valor total homologado do item (R$)
      valorUnitarioHomologado    -> R$/unidade (ex.: R$/m3)
      quantidadeHomologada       -> quantidade homologada
      (unidade vem do proprio item na listagem: unidadeMedida, ex. 'METRO CUBICO (M3)')

REALIDADE (guardrail anti-invencao): a maioria dos editais RECENTES ainda esta 'Em andamento'
(temResultado=False -> /resultados devolve 204). So editais mais antigos (meses) costumam ter
homologacao. Quando nao ha resultado, as funcoes retornam None com seguranca -- nunca inventam dado.

SEM EFEITO COLATERAL NO IMPORT (igual filtro_concreto): so define funcoes; nada roda ao importar.
Reusa o PADRAO do radar.py (UA, _seq_de_nc, montagem de URL, retry) e IMPORTA o filtro_concreto
(nao duplica a classificacao de concreto/brita)."""
import re
import time

import requests

from filtro_concreto import classificar_item   # fonte unica: nao duplicar concreto/brita

# mesmo UA do radar.py (o PNCP filtra user-agent vazio)
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                    '(KHTML, like Gecko) Chrome/126.0 Safari/537.36'}

TIMEOUT_S = 40          # igual PNCP_TIMEOUT_S do radar
_RETRIES = 3            # PNCP e instavel: tenta de novo com backoff


def _seq_de_nc(nc):
    """numeroControlePNCP 'CNPJ-1-SEQ/ANO' -> (cnpj, ano, seq_int). None se nao casar.
    Mesmo parser do radar.py (_seq_de_nc): grupos = (cnpj, ano, seq)."""
    m = re.match(r'(\d+)-\d+-(\d+)/(\d+)', str(nc or ''))
    return (m.group(1), m.group(3), int(m.group(2))) if m else None


def _get_json(url):
    """1 GET com retry/backoff. Devolve:
      - objeto JSON (dict/list) em 200,
      - None em 204 (sem resultado -- NAO e erro, e 'ainda nao homologado'),
      - 'ERR' se todas as tentativas falharem (rede/timeout/status ruim/HTML de erro).
    O chamador distingue 'sem resultado' (None) de 'falha' ('ERR')."""
    for att in range(_RETRIES):
        try:
            r = requests.get(url, timeout=TIMEOUT_S, headers=UA)
        except Exception:
            time.sleep(1.2 * (att + 1))
            continue
        if r.status_code == 204:
            return None                      # homologacao ainda nao publicada
        if r.status_code != 200:
            time.sleep(1.2 * (att + 1))
            continue
        try:
            return r.json()
        except Exception:                    # sob carga o PNCP responde HTML de erro (nao-JSON)
            time.sleep(1.5 * (att + 1))
            continue
    return 'ERR'


def _itens_da_compra(cnpj, ano, seq):
    """Lista de itens da compra (com temResultado/situacao/unidadeMedida). [] em falha/vazio."""
    url = ('https://pncp.gov.br/api/pncp/v1/orgaos/%s/compras/%s/%d/itens'
           '?pagina=1&tamanhoPagina=50' % (cnpj, ano, seq))
    j = _get_json(url)
    return j if isinstance(j, list) else []


def _resultado_do_item(cnpj, ano, seq, numero_item):
    """1o resultado homologado de UM item, normalizado, ou None.
    None cobre os 3 casos seguros: 204 (nao homologado), 'ERR' (falha), lista vazia."""
    url = ('https://pncp.gov.br/api/pncp/v1/orgaos/%s/compras/%s/%d/itens/%s/resultados'
           % (cnpj, ano, seq, numero_item))
    j = _get_json(url)
    if not isinstance(j, list) or not j:
        return None
    # pega o resultado nao-cancelado de melhor classificacao (ordemClassificacaoSrp=1 = vencedor)
    validos = [x for x in j if isinstance(x, dict) and not x.get('dataCancelamento')]
    if not validos:
        return None
    validos.sort(key=lambda x: (x.get('ordemClassificacaoSrp') or 9,
                                x.get('sequencialResultado') or 9))
    return validos[0]


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def resultado_da_compra(numero_controle_pncp):
    """PUBLICA. Dado um numeroControlePNCP ('CNPJ-1-SEQ/ANO'), devolve o resultado do
    PRIMEIRO item de concreto/brita (classificar_item >= 3) que ja esteja HOMOLOGADO:

        {vencedor, cnpj_vencedor, valor_homologado, unidade, quantidade}

    Ou None se: nc invalido, PNCP falhou, nenhum item de concreto/brita, ou nenhum item
    homologado ainda (o caso comum em editais recentes). NUNCA inventa dado.

    - valor_homologado = valorTotalHomologado (R$) do item vencedor.
    - unidade          = unidadeMedida do item (ex. 'METRO CUBICO (M3)').
    - quantidade       = quantidadeHomologada (fallback: quantidade do item).
    Campo extra util (valor_unitario) tambem retornado quando disponivel."""
    p = _seq_de_nc(numero_controle_pncp)
    if not p:
        return None
    cnpj, ano, seq = p
    itens = _itens_da_compra(cnpj, ano, seq)
    if not itens:
        return None
    for it in itens:
        if not isinstance(it, dict):
            continue
        desc = it.get('descricao') or ''
        if classificar_item(desc) < 3:          # so itens de concreto usinado / brita-produto
            continue
        if not it.get('temResultado'):          # pre-check barato: nao homologado -> nem bate no /resultados
            continue
        res = _resultado_do_item(cnpj, ano, seq, it.get('numeroItem'))
        if not res:
            continue
        return {
            'vencedor': res.get('nomeRazaoSocialFornecedor') or '',
            'cnpj_vencedor': res.get('niFornecedor') or '',
            'valor_homologado': _num(res.get('valorTotalHomologado')),
            'valor_unitario': _num(res.get('valorUnitarioHomologado')),
            'unidade': it.get('unidadeMedida') or '',
            'quantidade': _num(res.get('quantidadeHomologada'))
                          if _num(res.get('quantidadeHomologada')) is not None
                          else _num(it.get('quantidade')),
            'item_descricao': re.sub(r'<[^>]+>', ' ', desc).strip()[:90],  # limpa HTML embutido
            'numero_item': it.get('numeroItem'),
            'numero_controle_pncp': str(numero_controle_pncp),
        }
    return None


def todos_resultados_da_compra(numero_controle_pncp):
    """Variante: TODOS os itens de concreto/brita ja homologados (para o mapa de vazios,
    onde varios lotes/itens podem ter vencedores distintos). Lista (possivelmente vazia)."""
    p = _seq_de_nc(numero_controle_pncp)
    if not p:
        return []
    cnpj, ano, seq = p
    out = []
    for it in _itens_da_compra(cnpj, ano, seq):
        if not isinstance(it, dict):
            continue
        if classificar_item(it.get('descricao') or '') < 3:
            continue
        if not it.get('temResultado'):
            continue
        res = _resultado_do_item(cnpj, ano, seq, it.get('numeroItem'))
        if not res:
            continue
        out.append({
            'vencedor': res.get('nomeRazaoSocialFornecedor') or '',
            'cnpj_vencedor': res.get('niFornecedor') or '',
            'valor_homologado': _num(res.get('valorTotalHomologado')),
            'valor_unitario': _num(res.get('valorUnitarioHomologado')),
            'unidade': it.get('unidadeMedida') or '',
            'quantidade': _num(res.get('quantidadeHomologada')),
            'item_descricao': re.sub(r'<[^>]+>', ' ', it.get('descricao') or '').strip()[:90],
            'numero_item': it.get('numeroItem'),
        })
    return out
