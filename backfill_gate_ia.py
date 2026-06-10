"""
Backfill único — re-tria as obras genéricas (score=1 / POSSÍVEL) JÁ existentes na
planilha pelo MESMO portão de IA do scraper.

Resultado por linha score=1:
  - CONFIRMADA pela IA (concreto usinado / brita)  -> promovida (score=2 + campos ia_*)
  - NEGADA pela IA                                  -> REMOVIDA da planilha
  - PENDENTE (sem texto / cota / erro)              -> mantida (re-tentar rodando de novo)

Linhas score>=2 (CERTO/PROVÁVEL) não são tocadas.

Pré-requisito: GEMINI_API_KEY no ambiente (.env local ou variável). Re-executável:
o cache "Triagem IA" evita re-trabalho; rode de novo se sobrarem pendentes (cota).

Uso:  python backfill_gate_ia.py
"""
import os
import sys
import csv
from datetime import datetime

PROJ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJ)
os.chdir(PROJ)

from dotenv import load_dotenv
load_dotenv()
import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

import gspread
import scraper as s

ABA = s.ABA_OUTPUT  # "Novas Licitações"


def _to_float(v):
    try:
        return float(str(v).replace(",", ".").replace("R$", "").strip() or 0)
    except (TypeError, ValueError):
        return 0.0


def main():
    if not os.environ.get("GEMINI_API_KEY", "").strip():
        print("ERRO: GEMINI_API_KEY ausente no ambiente. Adicione no .env e rode de novo.")
        sys.exit(1)

    # Backfill processa TUDO (sem o teto de produção)
    s.IA_GATE_MAX = 100000

    sheet_id = os.environ["GOOGLE_SHEETS_ID"]
    gc = gspread.service_account(filename=os.environ["GOOGLE_SHEETS_CREDENTIALS_PATH"])
    ws = gc.open_by_key(sheet_id).worksheet(ABA)
    vals = ws.get_all_values()
    if not vals:
        print("Planilha vazia."); return
    header = vals[0]
    # Garante as colunas novas no header (migração incremental)
    novo_header = header + [c for c in s.OUTPUT_HEADER if c not in header]
    idx = {c: novo_header.index(c) for c in novo_header}

    def get(row, name):
        i = idx.get(name)
        return row[i] if (i is not None and i < len(row)) else ""

    # 1) Backup CSV de segurança (estado atual)
    backup = os.path.join(PROJ, f".backup_novas_licitacoes_{datetime.now():%Y%m%d_%H%M%S}.csv")
    with open(backup, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(vals)
    print(f"Backup salvo em: {backup}  ({len(vals)-1} linhas)")

    # 2) Coleta os editais score=1
    score1 = []
    for row in vals[1:]:
        try:
            sc = int(float(get(row, "score") or 0))
        except (TypeError, ValueError):
            sc = 0
        if sc != 1:
            continue
        score1.append({
            "numero_controle_pncp": get(row, "numero_controle_pncp"),
            "score": 1,
            "uf": get(row, "uf"),
            "objeto": get(row, "objeto"),
            "valor_estimado": _to_float(get(row, "valor_estimado")),
            "material": get(row, "material") or "concreto",
            "link_sistema_origem": get(row, "link_sistema_origem"),
            "link_pncp": get(row, "link_pncp"),
        })
    print(f"Obras genéricas (score=1) a re-triar: {len(score1)}")
    if not score1:
        print("Nada a fazer."); return

    # 3) Roda o MESMO portão de IA (grava cache 'Triagem IA' + muta os promovidos)
    s.aplicar_gate_ia(score1, sheet_id)
    cache = s._carregar_triagem(sheet_id)

    # nc -> edital promovido (já com score=2 + ia_*)
    promovidos = {e["numero_controle_pncp"]: e for e in score1
                  if int(e.get("score") or 0) == 2 and e.get("ia_verificado")}

    # 4) Reconstrói a planilha: mantém score!=1; promove confirmadas; remove negadas; mantém pendentes
    novas_linhas = [novo_header]
    promov = neg = pend = mantidas = 0
    for row in vals[1:]:
        row = list(row) + [""] * (len(novo_header) - len(row))
        try:
            sc = int(float(get(row, "score") or 0))
        except (TypeError, ValueError):
            sc = 0
        if sc != 1:
            novas_linhas.append(row); mantidas += 1; continue
        nc = get(row, "numero_controle_pncp")
        if nc in promovidos:
            e = promovidos[nc]
            row[idx["score"]] = 2
            row[idx["score_label"]] = s.SCORE_LABEL[2]
            if e.get("material"):
                row[idx["material"]] = e["material"]
            row[idx["ia_verificado"]] = True
            row[idx["ia_produto"]] = e.get("ia_produto", "")
            row[idx["ia_justificativa"]] = e.get("ia_justificativa", "")
            row[idx["ia_confianca"]] = e.get("ia_confianca", "")
            novas_linhas.append(row); promov += 1
        elif str(cache.get(nc, {}).get("status")) == "negado":
            neg += 1  # descarta (não acrescenta)
        else:
            novas_linhas.append(row); pend += 1  # pendente → mantém

    # 5) Regrava a aba (clear + update)
    ws.clear()
    ws.update("A1", novas_linhas, value_input_option="USER_ENTERED")
    print(f"OK. Promovidas={promov} | Removidas(negadas)={neg} | Pendentes(mantidas)={pend} | score!=1 mantidas={mantidas}")
    print(f"Total final na planilha: {len(novas_linhas)-1} (antes: {len(vals)-1}).")
    if pend:
        print(f"-> {pend} pendentes (cota/sem texto). Rode de novo mais tarde para tentar promovê-las.")


if __name__ == "__main__":
    main()
