# -*- coding: utf-8 -*-
"""Watchlist priorizada de alvos (read-only) — gera docs/alvos.json.

Norte (conselho 2026-07-07): o radar tem que CAPTURAR todo pregao de concreto/brita no raio,
com PRIORIDADE nos orgaos que ja fornecemos. Este script materializa esse alvo:
  - P1 = INCUMBENTES: orgaos que ja licitaram concreto/brita pra nos (historico.json::precos_por_orgao).
         'ativo' = comprou 2024+. Sao os de maior chance de reganhar + re-licitacao previsivel.
  - P2 = TODO O RAIO: municipios dentro do raio (usina <=70km / pedreira <=400km) das filiais que
         NAO estao em P1. Cobertura completa e obrigatoria (nao so incumbentes).

NAO grava em producao alem do docs/alvos.json (novo artefato). Roda:
    python alvos.py
"""
import csv
import json
import math
import os
import unicodedata

import gspread

SHEET_ID = os.environ.get('GOOGLE_SHEETS_ID', '1FjmN8EDKQRcBflL7VOp7MzB6PeKNO0hcXLUUAoLbBbg')
RAIO_USINA_KM = float(os.environ.get('RAIO_USINA_KM', '70'))
RAIO_PEDREIRA_KM = float(os.environ.get('RAIO_PEDREIRA_KM', '400'))
ANO_ATIVO = os.environ.get('ANO_ATIVO', '2024')   # comprou deste ano p/ frente = incumbente ativo


def _n(s):
    return ' '.join(unicodedata.normalize('NFKD', str(s or '')).encode('ascii', 'ignore').decode().lower().split())


def _hav(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    return 6371.0 * 2 * math.asin(math.sqrt(
        math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2))


def _f(v):
    try:
        return float(str(v).replace(',', '.'))
    except (ValueError, TypeError):
        return None


def carregar_filiais(gc):
    vals = gc.open_by_key(SHEET_ID).worksheet('Filiais').get_all_values()
    usinas, pedreiras = [], []
    for row in vals[1:]:
        if len(row) < 6:
            continue
        la, lo = _f(row[3]), _f(row[4])
        if la is None or lo is None:
            continue
        (pedreiras if 'pedreira' in _n(row[5]) else usinas).append((la, lo, row[0]))
    return usinas, pedreiras


def carregar_municipios():
    muns = []
    with open('municipios_br.csv', encoding='utf-8') as f:
        for r in csv.DictReader(f):
            la, lo = _f(r.get('lat')), _f(r.get('lon'))
            if la is None or lo is None:
                continue
            muns.append({'nome_norm': r['nome_norm'], 'uf': (r.get('uf') or '').upper(), 'lat': la, 'lon': lo,
                         'codigo_ibge': r.get('codigo_ibge', '')})
    return muns


def main():
    creds = os.environ.get('GOOGLE_SHEETS_CREDENTIALS_PATH', 'credenciais/service_account.json')
    gc = gspread.service_account(filename=creds)
    usinas, pedreiras = carregar_filiais(gc)
    muns = carregar_municipios()
    print('filiais: %d usinas, %d pedreiras | municipios BR: %d' % (len(usinas), len(pedreiras), len(muns)))

    # --- P1: incumbentes (do historico) ---
    try:
        hist = json.load(open('docs/historico.json', encoding='utf-8'))
    except Exception as e:
        print('  sem historico.json (%s) -> P1 vazio' % repr(e)[:50])
        hist = {}
    po = hist.get('precos_por_orgao') or {}
    p1 = {}
    for mk, v in po.items():
        ativo = str(v.get('ultimo_ano') or '') >= ANO_ATIVO
        p1[mk] = {
            'municipio': mk, 'cliente': v.get('cliente'), 'n_pregoes': v.get('n'),
            'preco_med': v.get('preco_med'), 'preco_p25': v.get('preco_p25'), 'preco_p75': v.get('preco_p75'),
            'volume_m3': v.get('volume'), 'ultimo_ano': v.get('ultimo_ano'),
            'prioridade': 1, 'incumbente': True, 'ativo': ativo,
        }

    # --- P2: todo o raio (haversine; rota real refina depois) menos P1 ---
    p2 = {}
    for m in muns:
        c = (m['lat'], m['lon'])
        d_us = min((_hav(c, (u[0], u[1])) for u in usinas), default=9e9)
        d_pd = min((_hav(c, (p[0], p[1])) for p in pedreiras), default=9e9)
        no_raio_concreto = d_us <= RAIO_USINA_KM
        no_raio_brita = (m['uf'] == 'RJ' and d_pd <= RAIO_PEDREIRA_KM)   # brita so RJ
        if not (no_raio_concreto or no_raio_brita):
            continue
        chave = m['nome_norm']
        if chave in p1:
            p1[chave]['codigo_ibge'] = m.get('codigo_ibge', '')
            p1[chave]['uf'] = m['uf']
            p1[chave]['dist_usina_km'] = round(d_us, 1) if no_raio_concreto else None
            continue
        p2[chave] = {
            'municipio': chave, 'uf': m['uf'], 'codigo_ibge': m.get('codigo_ibge', ''),
            'dist_usina_km': round(d_us, 1) if no_raio_concreto else None,
            'atende': 'concreto' if no_raio_concreto else 'brita',
            'prioridade': 2, 'incumbente': False,
        }

    out = {
        'gerado_por': 'alvos.py',
        'raio_usina_km': RAIO_USINA_KM, 'raio_pedreira_km': RAIO_PEDREIRA_KM,
        'total_p1_incumbentes': len(p1), 'p1_ativos': sum(1 for v in p1.values() if v.get('ativo')),
        'total_p2_raio': len(p2),
        'p1': sorted(p1.values(), key=lambda x: -(x.get('n_pregoes') or 0)),
        'p2': sorted(p2.values(), key=lambda x: (x.get('dist_usina_km') or 9e9)),
    }
    os.makedirs('docs', exist_ok=True)
    with open('docs/alvos.json', 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, separators=(',', ':'))
    print('OK -> docs/alvos.json | P1 incumbentes=%d (ativos %d) | P2 raio=%d' % (
        out['total_p1_incumbentes'], out['p1_ativos'], out['total_p2_raio']))


if __name__ == '__main__':
    main()
