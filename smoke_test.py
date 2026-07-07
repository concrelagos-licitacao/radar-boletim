# -*- coding: utf-8 -*-
"""Smoke-test — trava de seguranca ANTES de cada commit (conselho 2026-07-07).

Um erro de f-string no HTML embutido, ou um dado quebrado, derruba a geracao do site OU o envio
do e-mail pra diretoria em silencio. Este teste pega isso antes. Rapido, sem rede. Roda:
    python smoke_test.py
Sai 0 se tudo ok; !=0 se algo quebrou (bloqueia o commit).

Valida:
  1. Todos os .py do pipeline: `ast.parse` (sintaxe).
  2. docs/dados.json: JSON valido; nenhum edital pedreira fora do RJ (regra brita-so-RJ).
  3. docs/index.html: extrai o <script> e roda `node --check` (JS valido).
  4. docs/historico.json e docs/alvos.json (se existirem): JSON valido.
  5. boletim_email: a funcao de e-mail roda sem excecao com 0/1/edital-sem-NUMERO
     (protege o envio das 08:15 de quebrar por dado de borda).
"""
import ast
import json
import os
import re
import subprocess
import sys
import tempfile

FALHAS = []


def check(nome, cond, detalhe=''):
    print(('  OK  ' if cond else '  FALHOU ') + nome + ((' -> ' + detalhe) if detalhe and not cond else ''))
    if not cond:
        FALHAS.append(nome + ((': ' + detalhe) if detalhe else ''))


def t_sintaxe():
    print('[1] sintaxe dos .py do pipeline')
    for f in ('radar.py', 'gerar_site.py', 'boletim_email.py', 'sync_filiais.py', 'filtro_concreto.py', 'alvos.py'):
        if not os.path.exists(f):
            continue
        try:
            ast.parse(open(f, encoding='utf-8').read())
            check(f, True)
        except SyntaxError as e:
            check(f, False, 'linha %s: %s' % (e.lineno, e.msg))


def t_dados_json():
    print('[2] docs/dados.json')
    if not os.path.exists('docs/dados.json'):
        check('dados.json existe', False, 'arquivo ausente')
        return
    try:
        d = json.load(open('docs/dados.json', encoding='utf-8'))
    except Exception as e:
        check('dados.json parseavel', False, repr(e)[:80])
        return
    check('dados.json parseavel (%d editais)' % len(d), True)
    fora = [r for r in d if str(r.get('TIPO', '')).strip().lower() == 'pedreira'
            and str(r.get('UF', '')).strip().upper() not in ('RJ', '')]
    check('nenhum pedreira fora do RJ', not fora, '%d fora: %s' % (len(fora), [r.get('MUNICIPIO') for r in fora[:3]]))


def t_index_js():
    print('[3] docs/index.html -> JS valido (node --check)')
    if not os.path.exists('docs/index.html'):
        check('index.html existe', False, 'arquivo ausente')
        return
    html = open('docs/index.html', encoding='utf-8').read()
    scripts = re.findall(r'<script\b[^>]*>(.*?)</script>', html, re.S | re.I)
    js = '\n;\n'.join(s for s in scripts if s.strip() and 'src=' not in s[:0])
    if not js.strip():
        check('tem JS embutido', True, '(nenhum script inline)')
        return
    node = None
    for cand in ('node', 'node.exe'):
        try:
            subprocess.run([cand, '--version'], capture_output=True, timeout=10)
            node = cand
            break
        except Exception:
            continue
    if not node:
        check('node disponivel', True, '(node ausente -> pulo o check de JS)')
        return
    with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as tf:
        tf.write(js)
        caminho = tf.name
    try:
        r = subprocess.run([node, '--check', caminho], capture_output=True, text=True, timeout=30)
        check('JS embutido valido', r.returncode == 0, (r.stderr or '')[:160])
    finally:
        os.unlink(caminho)


def t_json_extras():
    print('[4] docs/historico.json e docs/alvos.json')
    for f in ('docs/historico.json', 'docs/alvos.json'):
        if not os.path.exists(f):
            continue
        try:
            json.load(open(f, encoding='utf-8'))
            check(f + ' parseavel', True)
        except Exception as e:
            check(f + ' parseavel', False, repr(e)[:80])


def t_email():
    print('[5] boletim_email: constroi mensagem sem excecao (0/1/sem-NUMERO)')
    try:
        import importlib
        be = importlib.import_module('boletim_email')
    except Exception as e:
        check('import boletim_email', False, repr(e)[:100])
        return
    from datetime import date
    casos = {
        '0 editais': [],
        '1 edital': [{'UF': 'MG', 'MUNICIPIO': 'Muriae', 'ORGAO': 'PREFEITURA', 'OBJETO': 'concreto usinado',
                      'DATA SESSAO': '2026-07-20', 'DISTANCIA KM': '30', 'NUMERO': '123', 'LINK': 'https://x'}],
        '1 sem NUMERO': [{'UF': 'MG', 'MUNICIPIO': 'X', 'ORGAO': 'Y', 'OBJETO': 'brita', 'DATA SESSAO': '',
                          'DISTANCIA KM': '', 'NUMERO': '', 'LINK': ''}],
    }
    # tenta a funcao publica de montagem (gerar_html hoje; apos refactor, o nome pode mudar)
    fn = getattr(be, 'gerar_html', None) or getattr(be, 'gerar_aviso', None) or getattr(be, 'montar_email', None)
    if not fn:
        check('funcao de montagem do e-mail existe', False, 'nao achei gerar_html/gerar_aviso/montar_email')
        return
    for nome, rows in casos.items():
        try:
            out = fn(rows, date(2026, 7, 20))
            check('e-mail %s' % nome, isinstance(out, str) and len(out) > 0)
        except TypeError:
            try:
                out = fn(rows)   # assinatura de 1 arg
                check('e-mail %s' % nome, isinstance(out, str) and len(out) > 0)
            except Exception as e:
                check('e-mail %s' % nome, False, repr(e)[:100])
        except Exception as e:
            check('e-mail %s' % nome, False, repr(e)[:100])


def main():
    for t in (t_sintaxe, t_dados_json, t_index_js, t_json_extras, t_email):
        try:
            t()
        except Exception as e:
            FALHAS.append('%s crashou: %s' % (t.__name__, repr(e)[:120]))
            print('  FALHOU %s crashou: %s' % (t.__name__, repr(e)[:120]))
    print()
    if FALHAS:
        print('SMOKE-TEST FALHOU (%d):' % len(FALHAS))
        for f in FALHAS:
            print('  - ' + f)
        sys.exit(1)
    print('SMOKE-TEST OK — seguro pra commitar.')
    sys.exit(0)


if __name__ == '__main__':
    main()
