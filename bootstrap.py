"""
bootstrap.py — Popula a planilha Google Sheets "Concrelagos Hub" com as filiais
e cria a aba "Novas Licitações" vazia (header criado pelo scraper.py na 1ª execução).

GEOCODING: Nominatim (OpenStreetMap) — gratuito, rate-limit 1 req/s.
Cache em memória por endereço. Fallback para "Município, UF, Brasil" se o endereço
completo não resolver.

PROTEÇÕES:
- Só toca a planilha de ID definido em .env (GOOGLE_SHEETS_ID).
- Se a aba "Filiais" já tiver dados (linhas além do header), ABORTA — não sobrescreve.
- Cada erro de geocoding é logado e a linha vira coordenada (0, 0) com flag.

USO:
    python bootstrap.py            # roda tudo
    python bootstrap.py --dry-run  # geocodifica + monta tudo mas NÃO grava na planilha
    python bootstrap.py --force    # sobrescreve aba Filiais mesmo se tiver dados (cuidado)
"""
from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
import time
from pathlib import Path

import gspread
from dotenv import load_dotenv
from geopy.exc import GeocoderServiceError, GeocoderTimedOut
from geopy.geocoders import Nominatim
from gspread.exceptions import APIError, WorksheetNotFound

ROOT = Path(__file__).resolve().parent
CSV_PATH = ROOT / "dados" / "filiais.csv"
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

ABA_FILIAIS = "Filiais"
ABA_OUTPUT = "Novas Licitações"
HEADER_FILIAIS = [
    "nome", "sigla", "municipio", "uf",
    "latitude", "longitude", "tipo",
    "cnpj", "endereco_completo", "geocode_status", "fonte",
]

NOMINATIM_USER_AGENT = "concrelagos-intelligence-hub/1.0 (juridico@concrelagos.com.br)"
NOMINATIM_RATE_LIMIT_SEC = 1.1  # Política Nominatim: 1 req/s. Margem de segurança.


def _setup_logging() -> None:
    log_file = LOG_DIR / f"bootstrap_{time.strftime('%Y-%m-%d')}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def _validar_env() -> dict:
    obrig = ["GOOGLE_SHEETS_ID", "GOOGLE_SHEETS_CREDENTIALS_PATH"]
    faltando = [k for k in obrig if not os.getenv(k)]
    if faltando:
        logging.error("Variáveis de ambiente ausentes: %s", ", ".join(faltando))
        sys.exit(2)
    return {k: os.environ[k] for k in obrig}


def _carregar_csv() -> list[dict]:
    if not CSV_PATH.exists():
        logging.error("CSV não encontrado: %s", CSV_PATH)
        sys.exit(2)
    with CSV_PATH.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        linhas = list(reader)
    logging.info("CSV carregado: %d filiais.", len(linhas))
    return linhas


def _geocodificar(linhas: list[dict]) -> list[dict]:
    """
    Geocodifica cada linha via Nominatim. Cache simples por endereço.
    Rate-limit obrigatório de 1s entre requisições (política do OSM).
    Fallback: tenta endereço completo; se falhar, tenta "Município, UF, Brasil".
    """
    geocoder = Nominatim(user_agent=NOMINATIM_USER_AGENT, timeout=20)
    cache: dict[str, tuple[float, float, str]] = {}
    out: list[dict] = []
    falhas: list[str] = []

    def _query(addr: str) -> tuple[float, float] | None:
        try:
            time.sleep(NOMINATIM_RATE_LIMIT_SEC)
            loc = geocoder.geocode(addr, country_codes=["br"], language="pt-BR")
            if loc:
                return float(loc.latitude), float(loc.longitude)
        except (GeocoderServiceError, GeocoderTimedOut) as exc:
            logging.warning("Nominatim erro para '%s': %s", addr, exc)
        except Exception as exc:
            logging.error("Nominatim exceção inesperada para '%s': %s", addr, exc)
        return None

    for i, row in enumerate(linhas, start=1):
        end = row["endereco_completo"].strip()
        municipio = row.get("municipio", "").strip()
        uf = row.get("uf", "").strip()
        fallback = f"{municipio}, {uf}, Brasil" if municipio and uf else ""

        if not end and not fallback:
            out.append({**row, "latitude": "", "longitude": "", "geocode_status": "endereco_vazio"})
            falhas.append(row["nome"])
            continue

        cache_key = end or fallback
        if cache_key in cache:
            lat, lng, status = cache[cache_key]
            logging.info("[%d/%d] %s → cache (%.5f, %.5f)", i, len(linhas), row["nome"], lat, lng)
        else:
            result = _query(end) if end else None
            status = "ok"
            if result is None and fallback:
                logging.warning("[%d/%d] %s → endereço completo falhou, tentando '%s'", i, len(linhas), row["nome"], fallback)
                result = _query(fallback)
                status = "ok_municipio"
            if result is None:
                lat, lng, status = 0.0, 0.0, "nao_encontrado"
                falhas.append(row["nome"])
                logging.error("[%d/%d] %s → não encontrado", i, len(linhas), row["nome"])
            else:
                lat, lng = result
                logging.info("[%d/%d] %s → (%.5f, %.5f) [%s]", i, len(linhas), row["nome"], lat, lng, status)
            cache[cache_key] = (lat, lng, status)

        out.append({
            **row,
            "latitude": lat,
            "longitude": lng,
            "geocode_status": status,
        })

    logging.info("Geocoding concluído. %d filiais com falha:", len(falhas))
    for n in falhas:
        logging.info("  - %s", n)
    return out


def _abrir_planilha(creds_path: str, sheet_id: str) -> gspread.Spreadsheet:
    try:
        gc = gspread.service_account(filename=creds_path)
        sh = gc.open_by_key(sheet_id)
        logging.info("Planilha aberta: '%s' (ID %s).", sh.title, sheet_id)
        return sh
    except Exception as exc:
        logging.error("Falha ao abrir planilha %s via SA %s: %s", sheet_id, creds_path, exc)
        sys.exit(3)


def _aba_tem_dados(sh: gspread.Spreadsheet, nome: str) -> bool:
    try:
        ws = sh.worksheet(nome)
    except WorksheetNotFound:
        return False
    try:
        valores = ws.get_all_values()
    except APIError as exc:
        logging.error("Erro lendo aba '%s': %s", nome, exc)
        sys.exit(3)
    return len([l for l in valores if any(c.strip() for c in l)]) > 1


def _gravar_filiais(sh: gspread.Spreadsheet, filiais: list[dict], force: bool) -> None:
    if _aba_tem_dados(sh, ABA_FILIAIS) and not force:
        logging.error("ABORTANDO: aba '%s' já tem dados. Use --force para sobrescrever.", ABA_FILIAIS)
        sys.exit(4)
    try:
        ws = sh.worksheet(ABA_FILIAIS)
        logging.info("Aba '%s' encontrada — limpando e regravando.", ABA_FILIAIS)
        ws.clear()
    except WorksheetNotFound:
        logging.info("Aba '%s' não existe — criando.", ABA_FILIAIS)
        ws = sh.add_worksheet(title=ABA_FILIAIS, rows=len(filiais) + 5, cols=len(HEADER_FILIAIS))

    rows = [HEADER_FILIAIS]
    for f in filiais:
        rows.append([
            f.get("nome", ""),
            f.get("sigla", ""),
            f.get("municipio", ""),
            f.get("uf", ""),
            f.get("latitude", ""),
            f.get("longitude", ""),
            f.get("tipo", ""),
            f.get("cnpj", ""),
            f.get("endereco_completo", ""),
            f.get("geocode_status", ""),
            f.get("fonte", ""),
        ])

    # RAW (não USER_ENTERED) para evitar o Sheets interpretar "."/"," como
    # separador BR de milhares e quebrar as coordenadas (vimos -23.5525 virar -235525).
    ws.update(values=rows, range_name="A1", value_input_option="RAW")
    logging.info("Aba '%s' atualizada com %d linhas (incluindo header).", ABA_FILIAIS, len(rows))


def _criar_aba_output(sh: gspread.Spreadsheet) -> None:
    try:
        sh.worksheet(ABA_OUTPUT)
        logging.info("Aba '%s' já existe — mantida intacta.", ABA_OUTPUT)
    except WorksheetNotFound:
        sh.add_worksheet(title=ABA_OUTPUT, rows=100, cols=15)
        logging.info("Aba '%s' criada (vazia — header será gravado pelo scraper.py).", ABA_OUTPUT)


def _remover_pagina_default(sh: gspread.Spreadsheet) -> None:
    """Remove a aba padrão 'Página1'/'Sheet1' se estiver vazia. Não toca em nada com dados."""
    for nome_default in ("Página1", "Sheet1", "Página 1"):
        try:
            ws = sh.worksheet(nome_default)
        except WorksheetNotFound:
            continue
        if not any(any(c.strip() for c in row) for row in ws.get_all_values()):
            sh.del_worksheet(ws)
            logging.info("Aba padrão vazia '%s' removida.", nome_default)


def main() -> None:
    parser = argparse.ArgumentParser(description="Bootstrap da planilha Concrelagos Hub.")
    parser.add_argument("--dry-run", action="store_true", help="Geocodifica e monta tudo, mas NÃO grava na planilha.")
    parser.add_argument("--force", action="store_true", help="Sobrescreve aba Filiais mesmo se já tiver dados.")
    args = parser.parse_args()

    _setup_logging()
    load_dotenv()
    env = _validar_env()

    linhas = _carregar_csv()
    logging.info("Iniciando geocoding via Nominatim (OSM) — esperado ~%d segundos.", int(len(linhas) * NOMINATIM_RATE_LIMIT_SEC))
    filiais = _geocodificar(linhas)

    ok = sum(1 for f in filiais if f["geocode_status"] in ("ok", "ok_municipio"))
    logging.info("== Resumo geocoding ==")
    logging.info("  Total: %d", len(filiais))
    logging.info("  OK: %d", ok)
    logging.info("  Falhas: %d", len(filiais) - ok)

    if args.dry_run:
        logging.info("DRY-RUN: nada será gravado. Saída salva em logs/bootstrap_dryrun_filiais.csv para revisão.")
        out_path = LOG_DIR / "bootstrap_dryrun_filiais.csv"
        with out_path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=HEADER_FILIAIS + ["fonte"])
            w.writeheader()
            for fl in filiais:
                w.writerow({k: fl.get(k, "") for k in HEADER_FILIAIS + ["fonte"]})
        logging.info("CSV de revisão escrito em %s", out_path)
        return

    sh = _abrir_planilha(env["GOOGLE_SHEETS_CREDENTIALS_PATH"], env["GOOGLE_SHEETS_ID"])
    _gravar_filiais(sh, filiais, force=args.force)
    _criar_aba_output(sh)
    _remover_pagina_default(sh)
    logging.info("Bootstrap concluído com sucesso.")


if __name__ == "__main__":
    main()
