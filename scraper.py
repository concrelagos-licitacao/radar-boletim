"""
Concrelagos Intelligence Hub — scraper.py
=========================================
Pipeline diário (Fases 1+2+3 do escopo arquitetural):
  1) Lê filiais (usinas e pedreiras) do Google Sheets.
  2) Busca editais publicados no PNCP na janela configurada.
  3) Filtra por palavra-chave, estado e valor mínimo.
  4) Qualifica por distância (Nominatim para geocoding + Haversine para distância)
     contra usinas (<= 70 km) e, para brita acima de R$ 200k, pedreiras (<= 700 km).
  5) Grava os qualificados na aba 'Novas Licitações' do mesmo Sheets.

Toda credencial vem do .env — nada hardcoded.

GEOCODING / DISTÂNCIA:
  - Geocoding via Nominatim (OpenStreetMap) — gratuito, rate-limit 1 req/s.
  - Distância via fórmula de Haversine (linha reta) — sem custo de API.
  - Como linha reta subestima distância de estrada, aplicamos
    HAVERSINE_AJUSTE_FATOR (padrão 1.0 = sem ajuste). Para ser mais
    conservador (descartar mais editais), aumente para 1.2-1.3.
"""

from __future__ import annotations

import csv
import logging
import os
import sys
import time
import unicodedata
from datetime import date, datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from math import asin, cos, radians, sin, sqrt
from pathlib import Path
from typing import Iterable
import smtplib

import gspread
import requests
from dotenv import load_dotenv
from geopy.exc import GeocoderServiceError, GeocoderTimedOut
from geopy.geocoders import Nominatim

# Carrega .env JÁ no import (antes das constantes lerem os.getenv)
load_dotenv()

# =========================================================================
# REGRAS DE NEGÓCIO
# =========================================================================
VALOR_MINIMO_GERAL    = float(os.getenv("VALOR_MINIMO_GERAL",    "50000"))
VALOR_MINIMO_PEDREIRA = float(os.getenv("VALOR_MINIMO_PEDREIRA", "80000"))
RAIO_USINA_KM = 70
RAIO_PEDREIRA_KM = 700

ESTADOS_CONCRETO = {"MG", "SP", "ES", "RJ", "PR", "BA"}
ESTADOS_BRITA    = {"RJ"}  # brita somente no Rio de Janeiro

# Palavras-chave organizadas por SCORE DE CONFIANÇA (3=CERTO, 2=PROVÁVEL, 1=POSSÍVEL).
# A filtragem percorre do score mais alto para o mais baixo e para no primeiro match.
#
# Score 3 — CERTO: o objeto/item fala explicitamente em compra do produto.
# Score 2 — PROVÁVEL: serviço que normalmente exige compra do produto, mas precisa
#            confirmar nos itens ou no edital completo.
# Score 1 — POSSÍVEL: edital genérico ("materiais de construção") — pode ou não
#            conter concreto usinado; editais deste grupo passam pelo enriquecimento
#            por itens (se PNCP_BUSCAR_ITENS=true) para promoção a score=2.
KEYWORDS_CONCRETO: dict[int, tuple[str, ...]] = {
    3: (
        "concreto usinado",
        "concreto pre-misturado",
        "concreto pre misturado",
        "concreto dosado",
        "concreto dosado em central",
        "concreto preparado",
        "concreto comercializado",
        "fornecimento de concreto",
        "concreto bombeado",        # muito comum em obras de médio porte
        "concreto fck",             # spec técnica explícita (ex: concreto fck 25 MPa)
    ),
    2: (
        "concreto",                 # "aquisição de concreto", "fornecimento de concreto" genérico
        "concreto armado",
        "concretagem",
        "concreto estrutural",
        "concreto para pavimentacao",
        "concreto magro",
    ),
    1: (
        "materiais de construcao",   # cobre "materiais de construção" sem acento
        "material de construcao",
    ),
}
# Brita: agregados de qualquer tipo, pedras, pedriscos.
KEYWORDS_BRITA: dict[int, tuple[str, ...]] = {
    3: (
        "brita",
        "pedrisco",
        "pedra britada",
        "pedras britadas",
        "rachao",               # "rachão" sem acento (pedra de mão)
        "pedregulho",
        "cascalho",
    ),
    2: (
        "agregado graudo",      # cobre "agregado graúdo" normalizado
        "agregados graudos",
        "agregado",             # só score 2 para não capturar "agregado miúdo" (areia)
        "agregados",
    ),
    1: (
        "materiais de construcao",  # pode conter brita — enriquecimento por itens vai confirmar
        "material de construcao",
    ),
}

SCORE_LABEL = {3: "CERTO", 2: "PROVÁVEL", 1: "POSSÍVEL"}

# Enriquecimento por itens: consulta o endpoint de itens do PNCP para editais
# com score=1 (genéricos), buscando keywords score=3 nas descrições dos itens.
# Ativado por PNCP_BUSCAR_ITENS=true (padrão false localmente, true em produção).
PNCP_BUSCAR_ITENS = os.getenv("PNCP_BUSCAR_ITENS", "false").lower() == "true"
PNCP_ITENS_MAX = int(os.getenv("PNCP_ITENS_MAX", "50"))  # máximo de editais score=1 a consultar
PNCP_ITENS_BASE_URL = "https://pncp.gov.br/api/pncp/v1/orgaos/{cnpj}/compras/{ano}/{seq}/itens"

PNCP_BASE_URL = "https://pncp.gov.br/api/consulta/v1/contratacoes/publicacao"
PNCP_TAMANHO_PAGINA = 50    # menor que 500 para evitar timeouts
PNCP_TIMEOUT_S = int(os.getenv("PNCP_TIMEOUT_S", "60"))
PNCP_RETRY_COUNT = int(os.getenv("PNCP_RETRY_COUNT", "1"))   # cada página = 1 + retry
PNCP_MAX_PAGINAS = int(os.getenv("PNCP_MAX_PAGINAS", "5"))    # 5 páginas/modalidade na demo
# Códigos de modalidade conforme tabela de domínio do PNCP — varremos pregão,
# concorrência, dispensa, inexigibilidade e leilão, que cobrem o universo
# relevante para insumos de construção.
PNCP_MODALIDADES = (6, 4, 8, 9, 1, 3)   # 3 = Concorrência Eletrônica (obras de grande porte)

ABA_FILIAIS = "Filiais"
ABA_OUTPUT = "Novas Licitações"

# Notificações por e-mail (stdlib smtplib — sem dependência extra).
# Configure NOTIFICACAO_EMAIL_DE, NOTIFICACAO_EMAIL_SENHA (App Password Gmail)
# e NOTIFICACAO_EMAIL_PARA (separados por vírgula) no .env / GitHub Secrets.
NOTIFICACAO_EMAIL_DE    = ""   # preenchido em runtime via load_dotenv() / Secret
NOTIFICACAO_EMAIL_SENHA = ""
NOTIFICACAO_EMAIL_PARA  = ""
APP_URL = "https://concrelagos-intelligence-viynfmh4nlzrfjdktekn2f.streamlit.app/"

PROJECT_ROOT = Path(__file__).resolve().parent
LOG_DIR = PROJECT_ROOT / "logs"
OUTPUT_FALLBACK_DIR = PROJECT_ROOT / "output"

# Geocoding / distância (sem Google Maps)
NOMINATIM_USER_AGENT = "concrelagos-intelligence-hub/1.0 (juridico@concrelagos.com.br)"
NOMINATIM_RATE_LIMIT_SEC = 1.1  # política Nominatim
HAVERSINE_AJUSTE_FATOR = float(os.getenv("HAVERSINE_AJUSTE_FATOR", "1.0"))

OUTPUT_HEADER = [
    "data_execucao",
    "numero_controle_pncp",
    "numero_edital",          # ex: "PE/0006/2025" ou "DL/0264/2026"
    "modalidade",             # ex: "Pregão Eletrônico" ou "Dispensa"
    "orgao",
    "esfera",                 # F/E/M (Federal/Estadual/Municipal)
    "municipio",
    "uf",
    "objeto",
    "valor_estimado",
    "data_abertura",
    "data_encerramento",
    "material",
    "tipo_atendimento",
    "filial_mais_proxima",
    "distancia_km",
    "link_pncp",              # página oficial PNCP do edital
    "link_sistema_origem",    # link sistema do órgão (comprasnet, etc.)
    # -- campos de confiança (adicionados na Fase 6) --
    "score",                  # 1=POSSÍVEL / 2=PROVÁVEL / 3=CERTO
    "score_label",            # "POSSÍVEL" / "PROVÁVEL" / "CERTO"
    "keyword_trigger",        # keyword que disparou o match
    "itens_encontrados",      # item PNCP relevante encontrado (somente para score=1 enriquecido)
]

# Mapeamento de modalidade PNCP → sigla e nome
MODALIDADE_SIGLA = {
    1: "LE",   # Leilão Eletrônico
    2: "DA",   # Diálogo Competitivo
    3: "CO",   # Concorrência (Eletrônica)
    4: "CO",   # Concorrência (Presencial)
    5: "PE",   # Pregão (Presencial)
    6: "PE",   # Pregão Eletrônico
    7: "CP",   # Concurso
    8: "DL",   # Dispensa de Licitação
    9: "IL",   # Inexigibilidade
    10: "MR",  # Manifestação de Interesse
    11: "PC",  # Pré-qualificação
    12: "CC",  # Credenciamento
    13: "LI",  # Licitação Internacional
}
MODALIDADE_NOME = {
    1: "Leilão Eletrônico",
    2: "Diálogo Competitivo",
    3: "Concorrência Eletrônica",
    4: "Concorrência",
    5: "Pregão Presencial",
    6: "Pregão Eletrônico",
    7: "Concurso",
    8: "Dispensa de Licitação",
    9: "Inexigibilidade",
    10: "Manifestação de Interesse",
    11: "Pré-qualificação",
    12: "Credenciamento",
    13: "Licitação Internacional",
}
ESFERA_NOME = {"F": "Federal", "E": "Estadual", "M": "Municipal", "D": "Distrital"}


# =========================================================================
# LOGGING
# =========================================================================
def _configurar_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    arquivo_log = LOG_DIR / f"scraper_{date.today().isoformat()}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(arquivo_log, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


# =========================================================================
# UTILITÁRIOS
# =========================================================================
def _normalize(texto: str | None) -> str:
    if not texto:
        return ""
    nfkd = unicodedata.normalize("NFKD", str(texto))
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()


def _validar_env() -> None:
    obrigatorias = ("GOOGLE_SHEETS_ID", "GOOGLE_SHEETS_CREDENTIALS_PATH")
    faltando = [k for k in obrigatorias if not os.getenv(k)]
    if faltando:
        logging.error(
            "Variáveis de ambiente obrigatórias ausentes no .env: %s",
            ", ".join(faltando),
        )
        sys.exit(1)
    caminho_cred = os.environ["GOOGLE_SHEETS_CREDENTIALS_PATH"]
    if not Path(caminho_cred).is_file():
        logging.error("Arquivo de credencial não encontrado em %s", caminho_cred)
        sys.exit(1)


def _haversine_km(p1: tuple[float, float], p2: tuple[float, float]) -> float:
    """Distância em linha reta entre dois pontos (lat, lng) em quilômetros."""
    lat1, lng1 = map(radians, p1)
    lat2, lng2 = map(radians, p2)
    dlat = lat2 - lat1
    dlng = lng2 - lng1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlng / 2) ** 2
    c = 2 * asin(sqrt(a))
    R = 6371.0  # raio da Terra em km
    return R * c * HAVERSINE_AJUSTE_FATOR


# =========================================================================
# FASE 1 — CARGA DE FILIAIS DO GOOGLE SHEETS
# =========================================================================
def carregar_filiais(sheet_id: str) -> dict[str, list[dict]]:
    """Lê a aba 'Filiais' e retorna {'usinas': [...], 'pedreiras': [...]}.

    Colunas esperadas (header na linha 1):
      nome | sigla | municipio | uf | latitude | longitude | tipo
    Onde tipo ∈ {"usina", "pedreira"}.
    """
    try:
        gc = gspread.service_account(filename=os.environ["GOOGLE_SHEETS_CREDENTIALS_PATH"])
        planilha = gc.open_by_key(sheet_id)
        aba = planilha.worksheet(ABA_FILIAIS)
    except gspread.exceptions.SpreadsheetNotFound:
        logging.error("Planilha %s não encontrada (verifique o ID e o compartilhamento com a Service Account).", sheet_id)
        sys.exit(1)
    except gspread.exceptions.WorksheetNotFound:
        logging.error("Aba '%s' não existe na planilha.", ABA_FILIAIS)
        sys.exit(1)
    except gspread.exceptions.APIError as e:
        logging.error("Erro de API ao abrir a planilha: %s", e)
        sys.exit(1)

    # get_all_values mantém tudo como string (evita o gspread "numericise" automático
    # que transforma "-23,3112878" do locale BR em int -233112878).
    todas = aba.get_all_values()
    if not todas:
        logging.error("Aba '%s' está vazia.", ABA_FILIAIS)
        sys.exit(1)
    header = [h.strip() for h in todas[0]]
    linhas = [dict(zip(header, row)) for row in todas[1:]]

    usinas: list[dict] = []
    pedreiras: list[dict] = []

    def _parse_coord(s: str) -> float | None:
        if s is None:
            return None
        s = str(s).strip().replace(",", ".")
        if not s:
            return None
        # Detecta inteiros gigantes (sem ponto decimal salvo errado no Sheets)
        # ex: "-233112878" → insere ponto após o sinal e 2 dígitos: "-23.3112878"
        if "." not in s:
            try:
                n = int(s)
                # Se valor absoluto > 90, certamente é coordenada sem ponto
                if abs(n) > 90:
                    sign = "-" if n < 0 else ""
                    digits = str(abs(n))
                    # Coordenadas brasileiras: lat -33..5, lng -73..-34
                    # Insere ponto após 2 dígitos
                    if len(digits) > 2:
                        s = f"{sign}{digits[:2]}.{digits[2:]}"
                    else:
                        return None
            except ValueError:
                pass
        try:
            return float(s)
        except ValueError:
            return None

    for i, linha in enumerate(linhas, start=2):  # linha 1 = header
        lat = _parse_coord(linha.get("latitude", ""))
        lng = _parse_coord(linha.get("longitude", ""))
        if lat is None or lng is None:
            logging.warning("Filial linha %d ignorada: latitude/longitude inválidas (%r/%r).",
                            i, linha.get("latitude"), linha.get("longitude"))
            continue

        # Descarta linhas com coordenada (0, 0) — indicador de geocoding falho.
        if lat == 0.0 and lng == 0.0:
            logging.warning("Filial linha %d ignorada: coordenada (0,0) indica geocoding falho.", i)
            continue

        tipo = _normalize(linha.get("tipo"))
        filial = {
            "nome": linha.get("nome") or "",
            "sigla": linha.get("sigla") or "",
            "municipio": linha.get("municipio") or "",
            "uf": (linha.get("uf") or "").upper().strip(),
            "lat": lat,
            "lng": lng,
            "tipo": tipo,
        }
        if tipo == "usina":
            usinas.append(filial)
        elif tipo == "pedreira":
            pedreiras.append(filial)
        else:
            logging.warning("Filial linha %d ignorada: tipo desconhecido %r.", i, linha.get("tipo"))

    logging.info("Filiais carregadas: %d usinas, %d pedreiras.", len(usinas), len(pedreiras))
    if not usinas and not pedreiras:
        logging.error("Nenhuma filial válida na planilha — abortando.")
        sys.exit(1)
    return {"usinas": usinas, "pedreiras": pedreiras}


# =========================================================================
# FASE 2 — BUSCA NO PNCP
# =========================================================================
def buscar_editais_pncp(data_inicial: date, data_final: date) -> list[dict]:
    """Consulta o endpoint público de contratações publicadas no PNCP.

    Pagina automaticamente e varre as modalidades em PNCP_MODALIDADES.
    Falhas de rede são logadas, mas o que já foi coletado é preservado.
    """
    coletados: list[dict] = []

    contagem_por_modalidade: dict[int, int] = {}
    for modalidade in PNCP_MODALIDADES:
        pagina = 1
        total_modalidade = 0
        while True:
            params = {
                "dataInicial": data_inicial.strftime("%Y%m%d"),
                "dataFinal": data_final.strftime("%Y%m%d"),
                "codigoModalidadeContratacao": modalidade,
                "pagina": pagina,
                "tamanhoPagina": PNCP_TAMANHO_PAGINA,
            }
            payload = _pncp_get_com_retry(params, modalidade, pagina)
            if payload is None:
                break

            itens = payload.get("data") or []
            logging.info("PNCP modalidade=%d pagina=%d/%s: %d itens recebidos",
                         modalidade, pagina,
                         payload.get("totalPaginas", "?"), len(itens))
            for item in itens:
                try:
                    coletados.append(_extrair_edital(item))
                    total_modalidade += 1
                except (KeyError, TypeError) as e:
                    logging.debug("Edital descartado por estrutura incompleta: %s", e)
                    continue

            total_paginas = payload.get("totalPaginas") or 1
            if pagina >= total_paginas or not itens:
                break
            if pagina >= PNCP_MAX_PAGINAS:
                logging.info("PNCP_MAX_PAGINAS (%d) atingido para modalidade %d — interrompendo varredura.",
                             PNCP_MAX_PAGINAS, modalidade)
                break
            pagina += 1
        contagem_por_modalidade[modalidade] = total_modalidade

    logging.info("PNCP retornou %d editais (bruto) na janela %s a %s. Por modalidade: %s",
                 len(coletados), data_inicial, data_final, contagem_por_modalidade)
    if not coletados:
        logging.warning("PNCP retornou 0 editais brutos — possível problema de API, rede ou janela de datas vazia.")
    return coletados


def _pncp_get_com_retry(params: dict, modalidade: int, pagina: int) -> dict | None:
    """GET no PNCP com retry exponencial. Retorna payload JSON ou None se falhar."""
    for tentativa in range(PNCP_RETRY_COUNT + 1):
        try:
            resp = requests.get(PNCP_BASE_URL, params=params, timeout=PNCP_TIMEOUT_S)
            if resp.status_code == 204:  # No Content
                return {"data": [], "totalPaginas": 0}
            resp.raise_for_status()
            return resp.json()
        except requests.Timeout:
            logging.warning("PNCP timeout (modalidade=%s pagina=%s tentativa=%s/%s)",
                            modalidade, pagina, tentativa + 1, PNCP_RETRY_COUNT + 1)
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 422:
                # 422 = Unprocessable: parâmetros inválidos para essa modalidade nessa janela
                logging.info("PNCP modalidade %s sem dados na janela (HTTP 422).", modalidade)
                return None
            logging.warning("PNCP HTTP erro (modalidade=%s pagina=%s tentativa=%s/%s): %s",
                            modalidade, pagina, tentativa + 1, PNCP_RETRY_COUNT + 1, e)
            if e.response is not None and e.response.status_code < 500:
                return None  # 4xx: não vale a pena retry
        except requests.RequestException as e:
            logging.warning("PNCP erro de rede (modalidade=%s pagina=%s tentativa=%s/%s): %s",
                            modalidade, pagina, tentativa + 1, PNCP_RETRY_COUNT + 1, e)
        except ValueError as e:
            logging.warning("PNCP JSON inválido (modalidade=%s pagina=%s): %s", modalidade, pagina, e)
            return None
        if tentativa < PNCP_RETRY_COUNT:
            time.sleep(2 ** tentativa)  # backoff: 1s, 2s
    return None


def _extrair_edital(item: dict) -> dict:
    unidade = item.get("unidadeOrgao") or {}
    orgao = item.get("orgaoEntidade") or {}
    valor = item.get("valorTotalEstimado")

    codigo_modalidade = item.get("modalidadeId") or item.get("codigoModalidadeContratacao")
    sigla_mod = MODALIDADE_SIGLA.get(int(codigo_modalidade), "") if codigo_modalidade else ""
    nome_mod = MODALIDADE_NOME.get(int(codigo_modalidade), "") if codigo_modalidade else ""

    ano = item.get("anoCompra") or ""
    seq = item.get("sequencialCompra") or ""
    numero_compra = item.get("numeroCompra") or ""

    # Edital formatado: "PE/0006/2025"
    numero_edital = ""
    if sigla_mod and numero_compra and ano:
        numero_edital = f"{sigla_mod}/{numero_compra}/{ano}"
    elif numero_compra and ano:
        numero_edital = f"{numero_compra}/{ano}"

    # Link oficial PNCP da página do edital (formato canônico)
    cnpj = orgao.get("cnpj") or ""
    link_pncp_pagina = ""
    if cnpj and ano and seq:
        link_pncp_pagina = f"https://pncp.gov.br/app/editais/{cnpj}/{ano}/{seq}"

    return {
        "numero_controle_pncp": item.get("numeroControlePNCP") or "",
        "numero_edital": numero_edital,
        "modalidade": nome_mod,
        "orgao": orgao.get("razaoSocial") or "",
        "esfera": ESFERA_NOME.get((orgao.get("esferaId") or "").upper(), orgao.get("esferaId") or ""),
        "municipio": unidade.get("municipioNome") or "",
        "uf": (unidade.get("ufSigla") or "").upper(),
        "objeto": item.get("objetoCompra") or "",
        "valor_estimado": float(valor) if valor is not None else 0.0,
        "data_abertura": item.get("dataAberturaProposta") or "",
        "data_encerramento": item.get("dataEncerramentoProposta") or "",
        "link_pncp": link_pncp_pagina,
        "link_sistema_origem": item.get("linkSistemaOrigem") or "",
        # campos internos para enriquecimento por itens (não gravados diretamente)
        "_cnpj": cnpj,
        "_ano_compra": str(ano) if ano else "",
        "_seq_compra": str(seq) if seq else "",
    }


# =========================================================================
# FASE 2 — FILTROS DE KEYWORD, ESTADO E VALOR
# =========================================================================
def filtrar_por_keyword_estado_valor(editais: list[dict]) -> list[dict]:
    """Filtra editais por keyword, estado e valor mínimo.

    Percorre KEYWORDS_CONCRETO e KEYWORDS_BRITA do score mais alto (3) para o mais
    baixo (1) e para no primeiro match, anotando `score` e `keyword_trigger`.
    """
    sobreviventes: list[dict] = []
    for ed in editais:
        if ed["valor_estimado"] < VALOR_MINIMO_GERAL:
            continue
        objeto_norm = _normalize(ed["objeto"])
        uf = ed["uf"]

        matched = False
        # Tenta concreto (score 3→1)
        if uf in ESTADOS_CONCRETO:
            for score in (3, 2, 1):
                hit = next((k for k in KEYWORDS_CONCRETO[score] if k in objeto_norm), None)
                if hit:
                    ed["material"] = "concreto"
                    ed["score"] = score
                    ed["score_label"] = SCORE_LABEL[score]
                    ed["keyword_trigger"] = hit
                    ed.setdefault("itens_encontrados", "")
                    sobreviventes.append(ed)
                    matched = True
                    break

        # Tenta brita (score 3→2→1; score=1 passa pelo enriquecimento de itens)
        if not matched and uf in ESTADOS_BRITA:
            for score in (3, 2, 1):
                hit = next((k for k in KEYWORDS_BRITA[score] if k in objeto_norm), None)
                if hit:
                    ed["material"] = "brita"
                    ed["score"] = score
                    ed["score_label"] = SCORE_LABEL[score]
                    ed["keyword_trigger"] = hit
                    ed.setdefault("itens_encontrados", "")
                    sobreviventes.append(ed)
                    break

    logging.info("%d editais após filtro keyword/estado/valor.", len(sobreviventes))
    return sobreviventes


# =========================================================================
# FASE 2b — ENRIQUECIMENTO POR ITENS (somente editais score=1)
# =========================================================================
def _buscar_itens_edital(cnpj: str, ano: str, seq: str) -> list[str]:
    """Consulta o endpoint de itens de uma contratação PNCP.

    Retorna lista de descrições de itens (strings normalizadas).
    Falha silenciosa: retorna lista vazia se qualquer erro ocorrer.

    Endpoint: GET /api/pncp/v1/orgaos/{cnpj}/compras/{ano}/{seq}/itens
    """
    if not cnpj or not ano or not seq:
        return []
    url = PNCP_ITENS_BASE_URL.format(cnpj=cnpj, ano=ano, seq=seq)
    descricoes: list[str] = []
    pagina = 1
    while True:
        try:
            resp = requests.get(
                url,
                params={"pagina": pagina, "tamanhoPagina": 500},
                timeout=PNCP_TIMEOUT_S,
            )
            if resp.status_code in (404, 204):
                break
            resp.raise_for_status()
            payload = resp.json()
        except (requests.RequestException, ValueError) as exc:
            logging.debug("Items endpoint erro para %s/%s/%s p%d: %s", cnpj, ano, seq, pagina, exc)
            break

        itens = payload.get("data") or []
        for it in itens:
            desc = it.get("descricao") or it.get("descricaoItem") or ""
            if desc:
                descricoes.append(_normalize(desc))

        total_pag = payload.get("totalPaginas") or 1
        if pagina >= total_pag or not itens:
            break
        pagina += 1

    return descricoes


def enriquecer_com_itens(editais: list[dict]) -> list[dict]:
    """Para editais com score=1, consulta os itens PNCP buscando keywords score=3.

    Se encontrar, promove o edital a score=2 e preenche `itens_encontrados`.
    Processa no máximo PNCP_ITENS_MAX editais score=1 (os primeiros da lista).

    Ativado apenas quando PNCP_BUSCAR_ITENS=true.
    """
    if not PNCP_BUSCAR_ITENS:
        return editais

    # Todas as keywords score=3 de concreto + brita (para verificação nos itens)
    kw_diretas = set(KEYWORDS_CONCRETO[3]) | set(KEYWORDS_BRITA[3])

    candidatos = [ed for ed in editais if ed.get("score") == 1]
    if not candidatos:
        return editais

    limite = min(len(candidatos), PNCP_ITENS_MAX)
    logging.info("Enriquecimento por itens: consultando %d edital(is) score=1 (limite=%d).",
                 len(candidatos), limite)
    promovidos = 0

    for ed in candidatos[:limite]:
        cnpj = ed.get("_cnpj", "")
        ano = ed.get("_ano_compra", "")
        seq = ed.get("_seq_compra", "")
        descricoes = _buscar_itens_edital(cnpj, ano, seq)

        for desc_norm in descricoes:
            hit = next((k for k in kw_diretas if k in desc_norm), None)
            if hit:
                ed["score"] = 2
                ed["score_label"] = SCORE_LABEL[2]
                # Guarda trecho original (denormalizado se possível) para exibir no dashboard
                ed["itens_encontrados"] = desc_norm[:200]
                promovidos += 1
                logging.info("Edital %s promovido a score=2 via item '%s'.",
                             ed.get("numero_controle_pncp", "?"), hit)
                break  # basta 1 item para confirmar

    logging.info("Itens consultados para %d edital(is) score=1 — %d promovido(s) a score=2.",
                 limite, promovidos)
    return editais


# =========================================================================
# FASE 3 — QUALIFICAÇÃO GEOGRÁFICA (Nominatim + Haversine)
# =========================================================================
def qualificar_por_distancia(
    editais: list[dict],
    filiais: dict[str, list[dict]],
) -> list[dict]:
    """Mantém apenas editais dentro do raio de pelo menos uma filial.

    Geocoding: Nominatim (OSM), rate-limit 1s entre chamadas, cache por município|UF.
    Distância: fórmula de Haversine (linha reta) com fator de ajuste configurável.
    """
    geocoder = Nominatim(user_agent=NOMINATIM_USER_AGENT, timeout=20)
    geocode_cache: dict[str, tuple[float, float] | None] = {}
    qualificados: list[dict] = []

    usinas = filiais.get("usinas") or []
    pedreiras = filiais.get("pedreiras") or []

    for ed in editais:
        chave_geo = f"{_normalize(ed['municipio'])}|{ed['uf']}"

        if chave_geo in geocode_cache:
            origem = geocode_cache[chave_geo]
        else:
            origem = _geocodificar_municipio(geocoder, ed["municipio"], ed["uf"])
            geocode_cache[chave_geo] = origem

        if origem is None:
            logging.warning("Geocode não retornou coordenada para %s/%s — edital descartado.",
                            ed["municipio"], ed["uf"])
            continue

        # 1) Tenta usinas
        if usinas:
            distancia_km, mais_proxima = _menor_distancia(origem, usinas)
            if distancia_km is not None and distancia_km <= RAIO_USINA_KM:
                ed["tipo_atendimento"] = "atendimento_usina"
                ed["filial_mais_proxima"] = f"{mais_proxima['nome']} ({mais_proxima['municipio']}/{mais_proxima['uf']})"
                ed["distancia_km"] = round(distancia_km, 2)
                ed["latitude"] = origem[0]
                ed["longitude"] = origem[1]
                qualificados.append(ed)
                continue

        # 2) Tenta pedreiras (somente para brita acima do limiar)
        if (
            pedreiras
            and ed.get("material") == "brita"
            and ed["valor_estimado"] > VALOR_MINIMO_PEDREIRA
        ):
            distancia_km, mais_proxima = _menor_distancia(origem, pedreiras)
            if distancia_km is not None and distancia_km <= RAIO_PEDREIRA_KM:
                ed["tipo_atendimento"] = "atendimento_pedreira"
                ed["filial_mais_proxima"] = f"{mais_proxima['nome']} ({mais_proxima['municipio']}/{mais_proxima['uf']})"
                ed["distancia_km"] = round(distancia_km, 2)
                ed["latitude"] = origem[0]
                ed["longitude"] = origem[1]
                qualificados.append(ed)
                continue

        # Fora dos raios — descartado da memória.

    logging.info("%d editais qualificados após filtro geográfico.", len(qualificados))
    return qualificados


def _geocodificar_municipio(geocoder: Nominatim, municipio: str, uf: str) -> tuple[float, float] | None:
    consulta = f"{municipio}, {uf}, Brasil"
    try:
        time.sleep(NOMINATIM_RATE_LIMIT_SEC)
        loc = geocoder.geocode(consulta, country_codes=["br"], language="pt-BR")
        if loc:
            return (float(loc.latitude), float(loc.longitude))
    except (GeocoderServiceError, GeocoderTimedOut) as exc:
        logging.warning("Nominatim erro para '%s': %s", consulta, exc)
    except Exception as exc:
        logging.error("Nominatim exceção inesperada para '%s': %s", consulta, exc)
    return None


def _menor_distancia(
    origem: tuple[float, float],
    destinos: Iterable[dict],
) -> tuple[float | None, dict | None]:
    """Calcula distância haversine de origem a cada destino. Retorna o mais próximo."""
    melhor_km: float | None = None
    melhor_destino: dict | None = None
    for d in destinos:
        coords = (d["lat"], d["lng"])
        km = _haversine_km(origem, coords)
        if melhor_km is None or km < melhor_km:
            melhor_km = km
            melhor_destino = d
    return melhor_km, melhor_destino


# =========================================================================
# FASE 1 — GRAVAÇÃO NA ABA 'Novas Licitações'
# =========================================================================
def gravar_em_sheets(qualificados: list[dict], sheet_id: str) -> list[dict]:
    """Grava editais qualificados na aba 'Novas Licitações'.

    Retorna a lista de editais efetivamente novos (anti-duplicata) para que
    main() possa enviar a notificação de e-mail.
    """
    if not qualificados:
        logging.info("Nada para gravar — 0 editais qualificados.")
        return []

    try:
        gc = gspread.service_account(filename=os.environ["GOOGLE_SHEETS_CREDENTIALS_PATH"])
        planilha = gc.open_by_key(sheet_id)
        try:
            aba = planilha.worksheet(ABA_OUTPUT)
        except gspread.exceptions.WorksheetNotFound:
            logging.info("Aba '%s' não existe — criando.", ABA_OUTPUT)
            aba = planilha.add_worksheet(title=ABA_OUTPUT, rows=1000, cols=len(OUTPUT_HEADER))

        valores_existentes = aba.get_all_values()
        # Considera "vazio" se nenhuma célula tem conteúdo real
        tem_conteudo = any(any(c.strip() for c in row) for row in valores_existentes)
        primeiro_eh_header = (
            tem_conteudo
            and valores_existentes
            and valores_existentes[0]
            and valores_existentes[0][0] == OUTPUT_HEADER[0]
        )
        if not primeiro_eh_header:
            # Reseta a aba e escreve o header certo
            aba.clear()
            aba.append_row(OUTPUT_HEADER, value_input_option="USER_ENTERED")
            ja_gravados: set[str] = set()
        else:
            header_atual = valores_existentes[0]
            # Migração incremental: adiciona colunas novas se o header antigo não as tem.
            colunas_faltando = [c for c in OUTPUT_HEADER if c not in header_atual]
            if colunas_faltando:
                nova_linha_header = header_atual + colunas_faltando
                # Atualiza a linha 1 (range A1:col_N onde N = len(nova_linha_header))
                aba.update("A1", [nova_linha_header], value_input_option="USER_ENTERED")
                logging.info("Header da aba '%s' atualizado com colunas novas: %s.",
                             ABA_OUTPUT, colunas_faltando)
            try:
                idx_pncp = header_atual.index("numero_controle_pncp")
                ja_gravados = {row[idx_pncp] for row in valores_existentes[1:] if len(row) > idx_pncp}
            except ValueError:
                ja_gravados = set()

        agora = datetime.now().isoformat(timespec="seconds")
        novos_editais = [
            ed for ed in qualificados
            if ed["numero_controle_pncp"] not in ja_gravados
        ]
        if not novos_editais:
            logging.info("Todos os qualificados já estavam na planilha (anti-duplicata).")
            return []

        novas_linhas = [_edital_para_linha(ed, agora) for ed in novos_editais]
        aba.append_rows(novas_linhas, value_input_option="USER_ENTERED")
        logging.info("Gravadas %d novas linhas na aba '%s'.", len(novas_linhas), ABA_OUTPUT)
        return novos_editais

    except gspread.exceptions.APIError as e:
        logging.error("Falha ao gravar no Sheets: %s — escrevendo fallback CSV.", e)
        _fallback_csv(qualificados)
    return []


def _edital_para_linha(ed: dict, ts: str) -> list:
    return [
        ts,
        ed.get("numero_controle_pncp", ""),
        ed.get("numero_edital", ""),
        ed.get("modalidade", ""),
        ed.get("orgao", ""),
        ed.get("esfera", ""),
        ed.get("municipio", ""),
        ed.get("uf", ""),
        ed.get("objeto", ""),
        ed.get("valor_estimado", 0.0),
        ed.get("data_abertura", ""),
        ed.get("data_encerramento", ""),
        ed.get("material", ""),
        ed.get("tipo_atendimento", ""),
        ed.get("filial_mais_proxima", ""),
        ed.get("distancia_km", ""),
        ed.get("link_pncp", ""),
        ed.get("link_sistema_origem", ""),
        ed.get("score", ""),
        ed.get("score_label", ""),
        ed.get("keyword_trigger", ""),
        ed.get("itens_encontrados", ""),
    ]


def _fallback_csv(qualificados: list[dict]) -> None:
    OUTPUT_FALLBACK_DIR.mkdir(parents=True, exist_ok=True)
    arq = OUTPUT_FALLBACK_DIR / f"novas_licitacoes_fallback_{datetime.now():%Y%m%dT%H%M%S}.csv"
    ts = datetime.now().isoformat(timespec="seconds")
    with arq.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(OUTPUT_HEADER)
        for ed in qualificados:
            w.writerow(_edital_para_linha(ed, ts))
    logging.error("Fallback gravado em %s — verifique conectividade e re-execute.", arq)


# =========================================================================
# NOTIFICAÇÕES POR E-MAIL
# =========================================================================
def enviar_notificacao_email(novos: list[dict]) -> None:
    """Envia e-mail HTML com os editais recém-qualificados.

    Usa smtplib (stdlib) + Gmail SMTP. Falha silenciosa: loga o erro e segue.
    Requer as env vars NOTIFICACAO_EMAIL_DE, NOTIFICACAO_EMAIL_SENHA e
    NOTIFICACAO_EMAIL_PARA configuradas.
    """
    remetente = os.getenv("NOTIFICACAO_EMAIL_DE", "").strip()
    senha = os.getenv("NOTIFICACAO_EMAIL_SENHA", "").strip()
    destinatarios_raw = os.getenv("NOTIFICACAO_EMAIL_PARA", "").strip()

    if not (remetente and senha and destinatarios_raw):
        logging.info("Notificação por e-mail desativada (variáveis NOTIFICACAO_EMAIL_* não configuradas).")
        return
    if not novos:
        return

    destinatarios = [d.strip() for d in destinatarios_raw.split(",") if d.strip()]
    n = len(novos)

    # ---- Monta o HTML ----
    _SCORE_STYLE = {
        3: ("background:#DCFCE7;color:#15803D", "✅ CERTO"),
        2: ("background:#FEF9C3;color:#854D0E", "⚠️ PROVÁVEL"),
        1: ("background:#F3F4F6;color:#4B5563",  "🔍 POSSÍVEL"),
    }

    def _fmt_valor(v) -> str:
        try:
            v = float(v)
        except (TypeError, ValueError):
            return "—"
        if v >= 1_000_000:
            return f"R$ {v/1_000_000:.2f}M"
        if v >= 1_000:
            return f"R$ {v/1_000:.1f}k"
        return f"R$ {v:,.0f}"

    cards_html = ""
    for ed in novos:
        score_val = ed.get("score") or 0
        try:
            score_val = int(score_val)
        except (ValueError, TypeError):
            score_val = 0
        score_style, score_txt = _SCORE_STYLE.get(score_val, ("background:#F3F4F6;color:#4B5563", ""))
        objeto = str(ed.get("objeto") or "").strip()[:140]
        orgao  = str(ed.get("orgao") or "").strip()
        cidade = f"{ed.get('municipio', '')} / {ed.get('uf', '')}"
        valor  = _fmt_valor(ed.get("valor_estimado"))
        link   = str(ed.get("link_pncp") or ed.get("link_sistema_origem") or "").strip()
        link_html = (
            f'<a href="{link}" style="color:#1E40AF;font-size:13px;text-decoration:none;">🔍 Ver no PNCP →</a>'
            if link else ""
        )
        cards_html += (
            f'<div style="border:1px solid #E5E7EB;border-left:4px solid #0E2A47;'
            f'margin:12px 20px;padding:14px 16px;border-radius:6px;">'
            f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">'
            f'<span style="{score_style};font-weight:700;font-size:11px;padding:3px 8px;border-radius:3px;">{score_txt}</span>'
            f'<span style="font-weight:700;color:#0E2A47;font-size:15px;">{valor}</span>'
            f'</div>'
            f'<div style="font-size:13px;color:#1F2937;margin-bottom:4px;">{objeto}</div>'
            f'<div style="font-size:12px;color:#6B7280;margin-bottom:8px;">{orgao} · 📍 {cidade}</div>'
            f'{link_html}'
            f'</div>'
        )

    html = (
        '<!DOCTYPE html><html><body style="font-family:Arial,sans-serif;background:#F7F8FA;margin:0;padding:20px;">'
        '<div style="max-width:620px;margin:0 auto;background:white;border-radius:8px;overflow:hidden;'
        'box-shadow:0 2px 8px rgba(0,0,0,0.1);">'
        '<div style="background:#0E2A47;color:white;padding:20px 24px;">'
        '<h2 style="margin:0;font-size:18px;">🏗️ Concrelagos Intelligence Hub</h2>'
        f'<p style="margin:6px 0 0;opacity:0.85;font-size:14px;">'
        f'{n} novo(s) edital(is) qualificado(s) encontrado(s)</p>'
        '</div>'
        + cards_html +
        f'<div style="background:#F3F4F6;padding:14px 24px;text-align:center;'
        f'font-size:12px;color:#6B7280;border-top:1px solid #E5E7EB;">'
        f'Acesse o <a href="{APP_URL}" style="color:#0E2A47;font-weight:600;">dashboard</a> '
        f'para análise completa, filtros e pipeline de oportunidades.</div>'
        '</div></body></html>'
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🏗️ [{n}] novo(s) edital(is) qualificado(s) — Concrelagos Hub"
    msg["From"] = remetente
    msg["To"] = ", ".join(destinatarios)
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as srv:
            srv.ehlo()
            srv.starttls()
            srv.login(remetente, senha)
            srv.sendmail(remetente, destinatarios, msg.as_string())
        logging.info("Notificação enviada para %s (%d edital(is)).", destinatarios, n)
    except smtplib.SMTPAuthenticationError:
        logging.error("Notificação: falha de autenticação SMTP. Verifique NOTIFICACAO_EMAIL_SENHA (use App Password).")
    except Exception as exc:
        logging.error("Notificação: erro ao enviar e-mail: %s", exc)


# =========================================================================
# LOG DE EXECUÇÕES
# =========================================================================
_EXECUCOES_HEADER = [
    "data_execucao", "status", "brutos", "apos_keyword",
    "apos_geo", "novos", "tempo_s", "erro_msg",
]

def _gravar_execucao_sheets(sheet_id: str, dados: dict) -> None:
    """Grava uma linha de auditoria na aba 'Execucoes' do Sheets.

    Cria a aba automaticamente se não existir. Falha silenciosa.
    """
    try:
        creds_path = os.environ.get("GOOGLE_SHEETS_CREDENTIALS_PATH", "credenciais/service_account.json")
        gc = gspread.service_account(filename=creds_path)
        sh = gc.open_by_key(sheet_id)
        try:
            ws = sh.worksheet("Execucoes")
        except gspread.exceptions.WorksheetNotFound:
            ws = sh.add_worksheet(title="Execucoes", rows=1000, cols=len(_EXECUCOES_HEADER))
            ws.append_row(_EXECUCOES_HEADER, value_input_option="USER_ENTERED")
        # Garante header se aba estava vazia
        vals = ws.get_all_values()
        if not vals or vals[0] != _EXECUCOES_HEADER:
            ws.clear()
            ws.append_row(_EXECUCOES_HEADER, value_input_option="USER_ENTERED")
        linha = [str(dados.get(col, "")) for col in _EXECUCOES_HEADER]
        ws.append_row(linha, value_input_option="USER_ENTERED")
        logging.info("Execução gravada na aba 'Execucoes' do Sheets.")
    except Exception as exc:
        logging.warning("Não foi possível gravar execução no Sheets: %s", exc)


# =========================================================================
# ORQUESTRADOR
# =========================================================================
def main() -> None:
    _configurar_logging()
    load_dotenv()
    _validar_env()

    # Janela: aceita PNCP_DATA_INICIAL/PNCP_DATA_FINAL como override (formato YYYY-MM-DD)
    # ou cai no padrão JANELA_DIAS (D-N até hoje).
    di_str = os.getenv("PNCP_DATA_INICIAL", "").strip()
    df_str = os.getenv("PNCP_DATA_FINAL", "").strip()
    if di_str and df_str:
        data_inicial = date.fromisoformat(di_str)
        data_final = date.fromisoformat(df_str)
        logging.info("Janela FIXA configurada via .env: %s a %s.", data_inicial, data_final)
    else:
        janela = int(os.getenv("JANELA_DIAS", "1"))
        hoje = date.today()
        data_inicial = hoje - timedelta(days=janela)
        data_final = hoje
        logging.info("Janela relativa (JANELA_DIAS=%d): %s a %s.", janela, data_inicial, data_final)

    sheet_id = os.environ["GOOGLE_SHEETS_ID"]
    filiais = carregar_filiais(sheet_id)

    t0 = time.time()
    erro_execucao = ""
    novos: list[dict] = []
    qualificados: list[dict] = []
    pre: list[dict] = []
    brutos: list[dict] = []
    try:
        brutos = buscar_editais_pncp(data_inicial, data_final)
        pre = filtrar_por_keyword_estado_valor(brutos)
        pre = enriquecer_com_itens(pre)   # promove score=1 → score=2 via endpoint de itens
        qualificados = qualificar_por_distancia(pre, filiais)
        novos = gravar_em_sheets(qualificados, sheet_id)
    except Exception as exc:
        erro_execucao = str(exc)
        logging.error("Erro inesperado na execução principal: %s", exc)

    tempo_s = round(time.time() - t0, 1)

    # Log do funil completo — mostra onde os editais são descartados
    logging.info(
        "FUNIL: brutos=%d → keyword=%d → geo=%d → novos=%d | tempo=%.1fs",
        len(brutos), len(pre), len(qualificados), len(novos), tempo_s,
    )
    if len(brutos) > 0 and len(pre) == 0:
        logging.warning("FUNIL: todos os editais descartados pelo filtro de keyword/estado/valor. "
                        "Verifique KEYWORDS_*, ESTADOS_* e VALOR_MINIMO_*.")
    if len(pre) > 0 and len(qualificados) == 0:
        logging.warning("FUNIL: editais passaram pelo keyword mas todos descartados pela qualificação geográfica. "
                        "Verifique se as filiais têm coordenadas válidas no Sheets.")

    # Grava execução na aba "Execucoes" do Sheets (auditoria permanente)
    _gravar_execucao_sheets(sheet_id, {
        "data_execucao": datetime.now().isoformat(timespec="seconds"),
        "status": "erro" if erro_execucao else "ok",
        "brutos": len(brutos),
        "apos_keyword": len(pre),
        "apos_geo": len(qualificados),
        "novos": len(novos),
        "tempo_s": tempo_s,
        "erro_msg": erro_execucao,
    })

    # Notificação por e-mail com os editais realmente novos (anti-duplicata aplicado)
    enviar_notificacao_email(novos)

    logging.info("Execução concluída — %d editais qualificados, %d novos.", len(qualificados), len(novos))


if __name__ == "__main__":
    main()
