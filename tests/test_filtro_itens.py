# -*- coding: utf-8 -*-
"""Oraculo de regressao OFFLINE (nao toca no PNCP). Prova que ler item-a-item:
  - Pedra Bonita/MG (item 'concreto usinado bombeavel')      -> ENTRA
  - Cajamar/SP (17 itens de mobiliario urbano)               -> FORA
  - drenagem c/ brita de berco de tubo (falso positivo)      -> FORA
Roda em segundos: `python tests/test_filtro_itens.py`."""
import json
import os
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(AQUI))
from filtro_concreto import edital_entra_por_itens, classificar_item, porte_m3  # noqa: E402

FIX = os.path.join(AQUI, 'fixtures')


def carrega(nome):
    with open(os.path.join(FIX, nome), encoding='utf-8') as f:
        return json.load(f)


CASOS = [
    ('pedra_bonita_nc93_MISTO_usinado.json', True,  'objeto dizia "artefatos/drenagem" mas tem concreto usinado no item'),
    ('cajamar_nc83_mobiliario.json',         False, '17 itens de mobiliario urbano, nenhum usinado/brita'),
    ('drenagem_brita_berco_FALSO_POSITIVO.json', False, 'brita e so berco de tubo -> nao pode entrar como concreto'),
]


def main():
    ok = True
    for arq, esperado, nota in CASOS:
        itens = carrega(arq)
        entra, item = edital_entra_por_itens(itens)
        marca = 'OK ' if entra == esperado else '*** FALHOU ***'
        if entra != esperado:
            ok = False
        print('[%s] %-42s entra=%-5s (esp %-5s)  %s' % (marca, arq[:42], entra, esperado, nota))
        if entra:
            desc = (item.get('descricao') if isinstance(item, dict) else str(item)) or ''
            print('        gatilho: %s  | porte: %s' % (desc[:55], porte_m3(item) or '(sem m3)'))
    # detalhe item-a-item do caso de drenagem (prova que nenhum item passa)
    print('\n  -- itens da drenagem (todos devem dar score 0) --')
    for it in carrega('drenagem_brita_berco_FALSO_POSITIVO.json'):
        print('     score=%d  %s' % (classificar_item(it['descricao']), it['descricao'][:60]))
    print('\nRESULTADO:', 'TODOS OS CASOS OK' if ok else '*** ALGUM CASO FALHOU ***')
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
