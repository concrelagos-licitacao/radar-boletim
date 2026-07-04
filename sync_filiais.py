# -*- coding: utf-8 -*-
"""
sync_filiais.py — sincroniza a aba 'Filiais' (base do radar) a partir da planilha
de ALVARAS que o usuario mantem (fonte de verdade das filiais).

Decisao do conselho (2026-07-03): Opcao C (hibrida, "nunca some silencioso"):
  - Planilha de alvaras = LISTA autoritativa de quais filiais existem.
  - Coordenadas vem de: (1) cache da aba 'Filiais' atual (curada, correta), depois
    (2) municipios_br.csv por municipio+UF. Municipio novo sem coord confiavel ->
    aba 'Filiais_PENDENTES' (NUNCA descartado em silencio).
  - So as 7 empresas que participam de licitacao entram (Concrelagos + 6 pedreiras).
  - dedupe por (municipio_oficial, UF, tipo) -> Itaperuna pode ter usina E pedreira.

Uso:
  python sync_filiais.py --dry-run   # so imprime, nao escreve nada
  python sync_filiais.py             # escreve abas 'Filiais' e 'Filiais_PENDENTES'
"""
import os, sys, csv, unicodedata
import gspread

CRED = os.environ.get('GOOGLE_SHEETS_CREDENTIALS_PATH', 'credenciais/service_account.json')
HUB_ID = os.environ.get('GOOGLE_SHEETS_ID', '1FjmN8EDKQRcBflL7VOp7MzB6PeKNO0hcXLUUAoLbBbg')
ALV_ID = '1QOiGyMwmvNhl_Fag4IBepbjDiusYMSsJQ57ZwbM8hic'
DRY = '--dry-run' in sys.argv

# ---- as 7 empresas que licitam: nome na planilha -> tipo de atendimento ----
EMPRESAS = {
    'CONCRELAGOS CONCRETO S/A': 'usina',
    'INDUSTRIA E COMERCIO APOLO LTDA': 'pedreira',
    'PEDREIRA OUTEIRO INDUSTRIA E COMERCIO DE PEDRAS LTDA': 'pedreira',
    'PEDREIRA BANGU LTDA': 'pedreira',
    'PEDREIRA BELA VISTA': 'pedreira',
    'PEDREIRA IMBOASSICA LTDA': 'pedreira',
    'IPEPAM INDUSTRIA DE PEDRAS PADUA MIRACEMA LTDA': 'pedreira',
}

def norm(s):
    s = unicodedata.normalize('NFKD', s or '').encode('ascii', 'ignore').decode()
    return ' '.join(s.upper().split())

# Usinas Concrelagos que a base curada tinha ERRADO como 'pedreira' (puxavam brita p/ SP).
# Pedreira REAL so nas 6 empresas (Apolo/Outeiro/Bangu/Bela Vista/Imboassica/Ipepam), N/centro RJ + ES.
_FORCA_USINA = {norm(m) for m in ('Volta Redonda', 'Itaborai', 'Rio das Ostras', 'Italva')}

# ---- mapa de apelidos: string crua normalizada -> (municipio oficial, UF) ----
# Resolve bairros (Gardenia Azul, Itaquera...), sufixos de unidade (2, II, NOVA),
# sufixos de estado (/MG) e nomes baguncados. UF explicita mata o homonimo.
ALIAS = {
    'ABAETE/MG': ('Abaete', 'MG'),
    'ADITIBRAS - DUQUE DE CAXIAS': ('Duque de Caxias', 'RJ'),
    'BANGU': ('Rio de Janeiro', 'RJ'),               # bairro do Rio (usina Concrelagos)
    'BOM DESPACHO/MG': ('Bom Despacho', 'MG'),
    'BOM JESUS DO ITABAPONA': ('Bom Jesus do Itabapoana', 'RJ'),  # typo na planilha
    'CAMPOS - 28 DE MARCO': ('Campos dos Goytacazes', 'RJ'),
    'CHACARA RIO-PETROPOLIS': ('Petropolis', 'RJ'),
    'CONTAGEM 2': ('Contagem', 'MG'),
    'GARDENIA AZUL': ('Rio de Janeiro', 'RJ'),        # bairro do Rio
    'ITAPERUNA - MATRIZ': ('Itaperuna', 'RJ'),        # pedreira Apolo matriz
    'ITAQUERA': ('Sao Paulo', 'SP'),                  # bairro de SP
    'JUIZ DE FORA 2': ('Juiz de Fora', 'MG'),
    'PARA DE MINAS II': ('Para de Minas', 'MG'),
    'RESENDE 2': ('Resende', 'RJ'),
    'SANTA CRUZ': ('Rio de Janeiro', 'RJ'),           # bairro do Rio (NAO Santa Cruz/RN)
    'SERRA - NOVA': ('Serra', 'ES'),
    'TAQUARA': ('Rio de Janeiro', 'RJ'),              # bairro (Jacarepagua/RJ)
    'UBA/MG II': ('Uba', 'MG'),
    'VILA VELHA - NOVA': ('Vila Velha', 'ES'),
    # variantes de sufixo comuns (por seguranca, alem da base cache):
    'ITAPERUNA': ('Itaperuna', 'RJ'),
}

def carregar():
    gc = gspread.service_account(filename=CRED)
    hub = gc.open_by_key(HUB_ID)
    base_rows = hub.worksheet('Filiais').get_all_values()
    alv_rows = gc.open_by_key(ALV_ID).worksheets()[0].get_all_values()
    return gc, hub, base_rows, alv_rows

def main():
    gc, hub, base_rows, alv_rows = carregar()

    # cache de coords da base atual: (municipio_norm, uf) -> (lat, lon, nome)
    # base guarda coord como numero (virgula = so display pt-BR); converto p/ float
    def _f(x):
        try:
            return float(str(x).replace(',', '.').strip())
        except (ValueError, AttributeError):
            return None
    cache = {}
    for r in base_rows[1:]:
        if len(r) < 6:
            continue
        nome, mun, uf, lat, lon, tipo = (r + ['']*6)[:6]
        la, lo = _f(lat), _f(lon)
        if la is not None and lo is not None and abs(la) > 0.01:
            cache[(norm(mun), norm(uf))] = (la, lo, nome.strip())
    # municipio_norm -> set(UF) presentes na base curada (fonte confiavel p/ desambiguar UF)
    cache_ufs = {}
    for (mn, uf) in cache:
        cache_ufs.setdefault(mn, set()).add(uf)

    # csv IBGE: (municipio_norm, uf) -> (lat, lon)  e  municipio_norm -> set(uf)
    csv_coord = {}
    csv_ufs = {}
    with open('municipios_br.csv', encoding='utf-8') as f:
        rd = csv.reader(f)
        for row in rd:
            if len(row) < 4:
                continue
            nome, uf, lat, lon = row[0], row[1], row[2], row[3]
            if not lat or lat == 'lat':
                continue
            try:
                csv_coord[(norm(nome), norm(uf))] = (float(lat), float(lon))
            except ValueError:
                continue
            csv_ufs.setdefault(norm(nome), set()).add(norm(uf))

    # UNIAO (nunca remove): comeca com a base atual intacta, so ACRESCENTA o que faltar.
    # Remocao de filial e sempre manual -- alvara so adiciona. Garante "nunca perder filial".
    base_final = []
    base_keys = set()
    for r in base_rows[1:]:
        if len(r) < 6:
            continue
        nome, mun, uf, lat, lon, tipo = (r + ['']*6)[:6]
        la, lo = _f(lat), _f(lon)
        if not mun.strip() or la is None or lo is None or abs(la) <= 0.01:
            continue
        # correcao: usinas Concrelagos que estavam mal-classificadas como pedreira (puxavam
        # brita p/ SP). Pedreira REAL so nas 6 empresas do RJ/ES. Ver feedback do usuario.
        if 'pedreira' in norm(tipo) and norm(mun) in _FORCA_USINA:
            tipo = 'usina'
        base_final.append([nome, mun, uf, la, lo, tipo])   # coord como float (numerico)
        base_keys.add((norm(mun), norm(uf), norm(tipo)))

    # percorre alvaras -> resolve
    vistos = set()          # (municipio_norm, uf, tipo)
    filiais = []            # linhas resolvidas (do alvara)
    novos = []              # subconjunto de 'filiais' que NAO existe na base atual
    pendentes = []          # nao resolvidas -> aba PENDENTES
    total_entrada = 0

    for r in alv_rows[1:]:
        if len(r) < 3:
            continue
        emp = r[0].strip(); mun_raw = r[1].strip(); sigla = r[2].strip()
        if emp not in EMPRESAS or not mun_raw:
            continue
        tipo = EMPRESAS[emp]
        nraw = norm(mun_raw)

        # 1) apelido explicito?
        if nraw in ALIAS:
            mun_of, uf = ALIAS[nraw]
        else:
            mun_of = mun_raw
            # 2) base curada manda: se o municipio existe la em 1 UF so, usa esse UF
            base_ufs = cache_ufs.get(nraw, set())
            csv_ufs_m = csv_ufs.get(nraw, set())
            if len(base_ufs) == 1:
                uf = next(iter(base_ufs))
            elif len(csv_ufs_m) == 1:
                uf = next(iter(csv_ufs_m))
            else:
                uf = ''   # UF desconhecida ou ambigua -> PENDENTES

        key = (norm(mun_of), norm(uf), norm(tipo))
        if key in vistos:
            continue
        vistos.add(key)
        total_entrada += 1

        # resolve coord: cache -> csv -> pendente
        coord = None; origem = ''
        if norm(uf):
            if (norm(mun_of), norm(uf)) in cache:
                lat, lon, _ = cache[(norm(mun_of), norm(uf))]; coord = (lat, lon); origem = 'cache'
            elif (norm(mun_of), norm(uf)) in csv_coord:
                lat, lon = csv_coord[(norm(mun_of), norm(uf))]; coord = (lat, lon); origem = 'csv'

        if coord:
            nome_fil = '%s%s' % (mun_of, '' if tipo == 'usina' else ' (pedreira)')
            row = [nome_fil, mun_of, uf, coord[0], coord[1], tipo]
            filiais.append(row)
            if key not in base_keys:      # so conta como NOVO se ainda nao existe na base
                novos.append(row)
        else:
            motivo = 'UF ambigua/desconhecida' if not norm(uf) else 'sem coord em cache/csv'
            pendentes.append([emp[:30], mun_raw, sigla, tipo, motivo])

    final = base_final + novos     # UNIAO: base intacta + so os novos

    # ---- invariante do Executor: nada some em silencio ----
    print('=== SYNC FILIAIS (%s) ===' % ('DRY-RUN' if DRY else 'ESCRITA'))
    print('Base atual (preservada 100%%, nada removido): %d' % len(base_final))
    print('Filiais distintas das 7 empresas no alvara: %d' % total_entrada)
    print('   -> ja existiam na base: %d' % (len(filiais) - len(novos)))
    print('   -> NOVAS a acrescentar: %d' % len(novos))
    print('Pendentes (sem coord, vao pra revisao manual): %d' % len(pendentes))
    print('TOTAL final da aba Filiais: %d' % len(final))
    ok = len(filiais) + len(pendentes) == total_entrada
    print('INVARIANTE resolvidas+pendentes==entrada: %s' % ('OK' if ok else 'FALHOU'))
    if novos:
        print('\n--- NOVAS filiais (base nao tinha) ---')
        for f in novos:
            print('   [%s] %-26s %s (%s,%s)' % (f[5], f[1], f[2], f[3], f[4]))
    if pendentes:
        print('\n--- PENDENTES (corrija na planilha ou no ALIAS) ---')
        for p in pendentes:
            print('   [%s] %-28s sigla=%-10s -> %s' % (p[3], p[1], p[2], p[4]))

    if DRY:
        print('\n[dry-run] nada foi escrito.')
        return

    # escreve aba Filiais (UNIAO) + Filiais_PENDENTES
    def ws(nome, cols):
        try:
            return hub.worksheet(nome)
        except gspread.WorksheetNotFound:
            return hub.add_worksheet(title=nome, rows=500, cols=cols)
    wf = ws('Filiais', 6)
    wf.clear()
    wf.update([['nome', 'municipio', 'uf', 'latitude', 'longitude', 'tipo']] + final)
    wp = ws('Filiais_PENDENTES', 5)
    wp.clear()
    wp.update([['empresa', 'municipio_planilha', 'sigla', 'tipo', 'motivo']] + pendentes)
    print('\nEscrito: Filiais (%d, base %d + novas %d) + PENDENTES (%d).'
          % (len(final), len(base_final), len(novos), len(pendentes)))

if __name__ == '__main__':
    main()
