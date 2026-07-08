# -*- coding: utf-8 -*-
"""Medicao de VAZAMENTO (read-only) — a metrica-norte PGL-r operacionalizada.

Norte do conselho: o melhor radar mede UMA coisa — pregoes de concreto/brita que a Concrelagos
CAPTUROU no raio (nao vazou). O gabarito e o boletim PAGO do ConLicitacao. A aba 'Backtest ConLic'
acumula, semana a semana, os editais PE concreto/brita que o ConLic listou, se estavam no nosso raio
(no_raio) e se o nosso radar pegou (radar_pegou).

Este script le essa aba e calcula a TAXA DE CAPTURA (dos que estavam no raio, quantos pegamos) +
o detalhe das perdas reais (no raio e nao pegamos) com a fonte. Gera docs/vazamento.json p/ o site
poder mostrar o placar. Nao promete "100%": mostra o numero medido, honesto.

    python vazamento.py
"""
import json
import os
import unicodedata

import gspread

SHEET_ID = os.environ.get('GOOGLE_SHEETS_ID', '1FjmN8EDKQRcBflL7VOp7MzB6PeKNO0hcXLUUAoLbBbg')


def _n(s):
    return ' '.join(unicodedata.normalize('NFKD', str(s or '')).encode('ascii', 'ignore').decode().lower().split())


def main():
    creds = os.environ.get('GOOGLE_SHEETS_CREDENTIALS_PATH', 'credenciais/service_account.json')
    gc = gspread.service_account(filename=creds)
    try:
        vals = gc.open_by_key(SHEET_ID).worksheet('Backtest ConLic').get_all_values()
    except Exception as e:
        print('sem aba Backtest ConLic (%s) — nada a medir ainda' % repr(e)[:60])
        return
    if len(vals) < 2:
        print('Backtest ConLic vazia — rode o backtest semanal primeiro (ler os boletins do ConLic).')
        return
    hdr = {h.strip().lower(): i for i, h in enumerate(vals[0])}

    def cel(row, k):
        i = hdr.get(k)
        return (row[i].strip() if i is not None and i < len(row) else '')

    total = no_raio = pegou = 0
    perdas = []
    por_boletim = {}
    for row in vals[1:]:
        cid = cel(row, 'cidade')
        if not cid:
            continue
        total += 1
        bol = cel(row, 'boletim') or '?'
        d = por_boletim.setdefault(bol, {'total': 0, 'no_raio': 0, 'pegou': 0})
        d['total'] += 1
        raio = _n(cel(row, 'no_raio'))
        dentro = raio.startswith('dentro')
        if dentro:
            no_raio += 1
            d['no_raio'] += 1
            pego = _n(cel(row, 'radar_pegou')) in ('sim', 'sim (apos item-check)') or 'sim' in _n(cel(row, 'radar_pegou'))
            if pego:
                pegou += 1
                d['pegou'] += 1
            else:
                perdas.append({'cidade': cid, 'uf': cel(row, 'uf'), 'material': cel(row, 'material'),
                               'edital': cel(row, 'edital'), 'dist_km': cel(row, 'dist_km'),
                               'boletim': bol, 'fonte': cel(row, 'fonte_no_radar'),
                               'obs': cel(row, 'situacao')})

    taxa = round(100.0 * pegou / no_raio, 1) if no_raio else None
    out = {
        'gerado_por': 'vazamento.py',
        'total_conlic_concreto_brita': total,
        'no_raio': no_raio, 'capturados_no_raio': pegou,
        'taxa_captura_pct': taxa,
        'perdas_reais': perdas,           # no raio e NAO pegamos = vazamento real, com a fonte
        'por_boletim': por_boletim,
    }
    os.makedirs('docs', exist_ok=True)
    with open('docs/vazamento.json', 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, separators=(',', ':'))

    print('=== VAZAMENTO (gabarito = boletim pago ConLic) ===')
    print('ConLic concreto/brita PE medidos: %d | no nosso raio: %d' % (total, no_raio))
    if taxa is not None:
        print('TAXA DE CAPTURA no raio: %s%% (%d de %d)' % (taxa, pegou, no_raio))
    print('Perdas reais (no raio, nao pegamos): %d' % len(perdas))
    for p in perdas[:15]:
        print('  - %s/%s %s (%s) | dist %s | fonte=%s | %s' % (
            p['cidade'], p['uf'], p['material'], p['edital'], p['dist_km'], p['fonte'] or '?', p['obs'][:40]))
    print('OK -> docs/vazamento.json')


if __name__ == '__main__':
    main()
