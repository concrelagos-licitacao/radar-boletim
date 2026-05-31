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
import re
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
# Captura SEM corte de valor (default 0). O filtro de valor é feito na TELA (app.py),
# para nunca jogar fora uma licitação na coleta. Ajustável por env var se necessário.
VALOR_MINIMO_GERAL    = float(os.getenv("VALOR_MINIMO_GERAL",    "0"))
VALOR_MINIMO_PEDREIRA = float(os.getenv("VALOR_MINIMO_PEDREIRA", "0"))
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
# ⚠️ REGRA DE OURO: a Concrelagos vende SÓ DOIS PRODUTOS — concreto USINADO e BRITA.
# Cimento ensacado, postes/tubos/blocos/manilhas/artefatos pré-moldados, piso
# intertravado, meio-fio e pavimentação asfáltica NÃO são nossos — devem ser
# DESCARTADOS mesmo contendo a palavra "concreto" (ver KEYWORDS_EXCLUSAO).
#
# CONCRETO USINADO (concreto fresco entregue por caminhão-betoneira da usina).
KEYWORDS_CONCRETO: dict[int, tuple[str, ...]] = {
    3: (  # CERTO — termos que só existem em concreto usinado
        "concreto usinado",
        "concreto pre-misturado",
        "concreto pre misturado",
        "concreto pre-fabricado fck",   # raro, mas usinado
        "concreto dosado",
        "concreto dosado em central",
        "central dosadora",
        "concreto preparado",
        "concreto comercializado",
        "fornecimento de concreto",
        "concreto bombeado",
        "concreto bombeavel",
        "concreto usinado bombeado",
        "concreto fck",                 # spec técnica (ex: concreto fck 25 MPa)
        "concreto estrutural",
        "concreto convencional",
        "concreto betonado",
    ),
    2: (  # PROVÁVEL — "concreto" genérico; só conta com contexto de usinado (ver filtro)
        "concreto",
        "concretagem",
        "concreto armado",
    ),
    1: (  # POSSÍVEL — obras/materiais onde concreto pode estar escondido.
          # Passam pelo enriquecimento por itens (grátis) e podem ser lidos pela IA
          # sob demanda no card. Geo (70km de usina) limita o ruído.
        "materiais de construcao",
        "material de construcao",
        "execucao de obra",
        "obra de engenharia",
        "obras de engenharia",
        "empresa de engenharia",
        "construcao de",
        "reforma e ampliacao",
        "reforma de",
        "ampliacao de",
        "pavimentacao",            # asfáltica/CBUQ é barrada pela lista de exclusão
        "pavimento rigido",
        "drenagem",
        "saneamento",
        "infraestrutura",
        "recapeamento",
        "terraplenagem",
        "calcamento",
        "obras de arte",
        "reservatorio",
    ),
}
# BRITA e todas as variações/disfarces de nome (agregado graúdo de pedra britada).
KEYWORDS_BRITA: dict[int, tuple[str, ...]] = {
    3: (  # CERTO
        "brita",
        "britas",
        "brita graduada",
        "brita graduada simples",
        "bgs",
        "brita 0",
        "brita 1",
        "brita 2",
        "brita 3",
        "brita 4",
        "brita corrida",
        "pedra britada",
        "pedras britadas",
        "pedrisco",
        "po de pedra",
        "bica corrida",
        "rachao",                # "rachão" sem acento
        "racho",
        "pedregulho",
        "cascalho",
        "agregado graudo",
        "agregados graudos",
        "pedra de mao",
        "pedra marroada",
        "seixo",
    ),
    2: (  # PROVÁVEL
        "agregado",              # só score 2 (evita "agregado miúdo" = areia)
        "agregados",
    ),
    1: (  # POSSÍVEL — obras onde brita costuma se esconder (RJ). Itens/IA confirmam.
        "materiais de construcao",
        "material de construcao",
        "execucao de obra",
        "obra de engenharia",
        "obras de engenharia",
        "pavimentacao",
        "drenagem",
        "terraplenagem",
        "estrada",
        "recuperacao de estrada",
        "lastro",
        "sub-base",
        "base e sub-base",
        "cascalhamento",
        "construcao de",
    ),
}

# 🚫 EXCLUSÕES — produtos VIZINHOS que NÃO são da Concrelagos. Se o objeto contém
# um destes termos e NÃO tem sinal forte (score 3) de usinado/brita, é descartado.
KEYWORDS_EXCLUSAO: tuple[str, ...] = (
    "tubo de concreto", "tubos de concreto", "manilha", "aduela",
    "poste de concreto", "postes de concreto", "poste",
    "bloco de concreto", "blocos de concreto", "bloco estrutural", "bloquete",
    "artefato de concreto", "artefatos de concreto",
    "pre-moldado", "pre moldado", "premoldado",
    "pre-fabricado", "pre fabricado", "prefabricado",
    "piso intertravado", "paver", "lajota",
    "meio-fio", "meio fio", "guia e sarjeta", "sarjeta",
    "cimento", "cimento portland", "saco de cimento",
    "argamassa",
    "pavimentacao asfaltica", "asfalto", "cbuq", "massa asfaltica",
    "emulsao asfaltica", "concreto asfaltico", "concreto betuminoso",
)
# Sinais de contexto que CONFIRMAM concreto usinado (promovem "concreto" genérico).
CONTEXTO_USINADO: tuple[str, ...] = (
    "fck", "mpa", "m3", "metro cubico", "metros cubicos", "usina", "usinado",
    "central", "dosado", "bombeado", "betoneira", "caminhao betoneira", "slump",
)

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
PNCP_PAUSA_S = float(os.getenv("PNCP_PAUSA_S", "0.8"))       # pausa entre requisições (evita throttling)
PNCP_MAX_PAGINAS = int(os.getenv("PNCP_MAX_PAGINAS", "5"))    # 5 páginas/modalidade na demo
# Códigos de modalidade conforme tabela de domínio do PNCP — varremos pregão,
# concorrência, dispensa, inexigibilidade e leilão, que cobrem o universo
# relevante para insumos de construção.
# A Concrelagos só participa de 3 modalidades:
#   6 = Pregão Eletrônico (PE)
#   5 = Pregão Presencial
#   8 = Dispensa de Licitação (DL)
# As demais (Concorrência, Inexigibilidade, Credenciamento, Leilão) são ignoradas —
# foco total no que a empresa realmente disputa (e runs mais rápidos/baratos).
PNCP_MODALIDADES = (6, 5, 8)

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
    # -- origem do edital (adicionado na Fase 10: multi-fonte) --
    "fonte",                  # API consultada: "PNCP", "COMPRASNET", "BLL"…
    "origem_plataforma",      # plataforma operacional do edital (ex: "LICITANET", "BLL", "ComprasNet")
    "local_obra",             # local da obra detectado no texto (quando difere da sede do órgão) ou "a confirmar"
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
    for i_mod, modalidade in enumerate(PNCP_MODALIDADES):
        if i_mod > 0:
            time.sleep(PNCP_PAUSA_S)  # pausa entre modalidades evita throttling do PNCP
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
            time.sleep(PNCP_PAUSA_S)  # pausa entre páginas evita throttling
        contagem_por_modalidade[modalidade] = total_modalidade

    logging.info("PNCP retornou %d editais (bruto) na janela %s a %s. Por modalidade: %s",
                 len(coletados), data_inicial, data_final, contagem_por_modalidade)
    if not coletados:
        logging.warning("PNCP retornou 0 editais brutos — possível problema de API, rede ou janela de datas vazia.")
    return coletados


def _pncp_get_com_retry(params: dict, modalidade: int, pagina: int) -> dict | None:
    """GET no PNCP com retry exponencial. Retorna payload JSON ou None se falhar.

    Importante: o PNCP costuma responder com CORPO VAZIO (que vira
    'Expecting value: line 1 column 1') quando recebe requisições rápidas demais
    (throttling). Tratamos corpo vazio como ERRO TRANSITÓRIO e tentamos de novo
    com backoff maior — senão modalidades inteiras (Dispensa, Concorrência…) somem.
    """
    # Mais paciência para vencer throttling (mínimo 3 tentativas)
    max_tent = max(PNCP_RETRY_COUNT, 3)
    for tentativa in range(max_tent + 1):
        transitorio = False
        try:
            resp = requests.get(PNCP_BASE_URL, params=params, timeout=PNCP_TIMEOUT_S)
            if resp.status_code == 204:  # No Content
                return {"data": [], "totalPaginas": 0}
            resp.raise_for_status()
            corpo = resp.text.strip()
            if not corpo:
                # Corpo vazio = quase sempre throttling → vale a pena tentar de novo
                transitorio = True
                logging.warning("PNCP corpo vazio (modalidade=%s pagina=%s tentativa=%s/%s) — provável throttling.",
                                modalidade, pagina, tentativa + 1, max_tent + 1)
            else:
                return resp.json()
        except requests.Timeout:
            transitorio = True
            logging.warning("PNCP timeout (modalidade=%s pagina=%s tentativa=%s/%s)",
                            modalidade, pagina, tentativa + 1, max_tent + 1)
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 422:
                logging.info("PNCP modalidade %s sem dados na janela (HTTP 422).", modalidade)
                return None
            logging.warning("PNCP HTTP erro (modalidade=%s pagina=%s tentativa=%s/%s): %s",
                            modalidade, pagina, tentativa + 1, max_tent + 1, e)
            if e.response is not None and e.response.status_code < 500 and e.response.status_code != 429:
                return None  # 4xx (exceto 429) não vale retry
            transitorio = True
        except ValueError as e:
            # JSONDecodeError (corpo inválido/vazio) → trata como transitório (throttling)
            transitorio = True
            logging.warning("PNCP corpo não-JSON (modalidade=%s pagina=%s tentativa=%s/%s): %s",
                            modalidade, pagina, tentativa + 1, max_tent + 1, e)
        except requests.RequestException as e:
            transitorio = True
            logging.warning("PNCP erro de rede (modalidade=%s pagina=%s tentativa=%s/%s): %s",
                            modalidade, pagina, tentativa + 1, max_tent + 1, e)
        if transitorio and tentativa < max_tent:
            time.sleep(1.5 * (tentativa + 1))  # backoff crescente: 1.5s, 3s, 4.5s…
    return None


def _rotular_origem(link_sistema_origem: str) -> str:
    """Detecta a plataforma operacional do edital a partir do link de origem.

    Espelha o comportamento do ConLicitação ("[LICITANET]", "BLL"…). Retorna
    rótulo curto ou "" se desconhecido.
    """
    s = (link_sistema_origem or "").lower()
    if not s:
        return ""
    regras = (
        ("licitanet", "LICITANET"),
        ("bllcompras", "BLL"),
        ("bll.org", "BLL"),
        ("portaldecompraspublicas", "Portal de Compras Públicas"),
        ("licitardigital", "Licitar Digital"),
        ("bnc.org", "BNC"),
        ("bionexo", "Bionexo"),
        ("comprasnet", "ComprasNet"),
        ("compras.gov", "Compras.gov.br"),
        ("comprasgovernamentais", "Compras.gov.br"),
        ("comprasbr", "ComprasBR"),
        ("publinexo", "Publinexo"),
        ("licitacoes-e", "Licitações-e (BB)"),
        ("bbmnet", "BBMNET"),
        ("gov.br", "Gov.br"),
    )
    for chave, rotulo in regras:
        if chave in s:
            return rotulo
    return ""


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
        "fonte": "PNCP",
        "origem_plataforma": _rotular_origem(item.get("linkSistemaOrigem") or ""),
        # campos internos para enriquecimento por itens (não gravados diretamente)
        "_cnpj": cnpj,
        "_ano_compra": str(ano) if ano else "",
        "_seq_compra": str(seq) if seq else "",
    }


# =========================================================================
# FASE 2 — FILTROS DE KEYWORD, ESTADO E VALOR
# =========================================================================
def filtrar_por_keyword_estado_valor(editais: list[dict]) -> list[dict]:
    """Filtra editais para SÓ concreto usinado e brita (com exclusão de vizinhos).

    Regras:
      1. Se o objeto contém termo de EXCLUSÃO (pré-moldado, cimento, asfalto…) e NÃO
         tem sinal forte (score 3) de usinado/brita → descarta.
      2. Concreto: score 3 = termos de usinado explícitos; score 2 = "concreto"
         genérico SÓ se houver CONTEXTO_USINADO (fck, m³, central, bombeado…).
      3. Brita: score 3 = brita e variações; score 2 = "agregado(s)".
      4. "materiais de construção" → score 1 (enriquecimento por itens confirma).
    Valor: NÃO corta aqui (corte é só na tela). Mantido VALOR_MINIMO_GERAL=0.
    """
    sobreviventes: list[dict] = []
    descartados_exclusao = 0
    for ed in editais:
        if VALOR_MINIMO_GERAL and ed["valor_estimado"] < VALOR_MINIMO_GERAL:
            continue
        objeto_norm = _normalize(ed["objeto"])
        uf = ed["uf"]

        # Sinais fortes (score 3) de cada produto
        hit_concreto_forte = next((k for k in KEYWORDS_CONCRETO[3] if k in objeto_norm), None)
        hit_brita_forte = next((k for k in KEYWORDS_BRITA[3] if k in objeto_norm), None)
        tem_sinal_forte = bool(hit_concreto_forte or hit_brita_forte)

        # Regra 1 — exclusão de produtos vizinhos (postes, tubos, cimento, asfalto…)
        if not tem_sinal_forte:
            if any(x in objeto_norm for x in KEYWORDS_EXCLUSAO):
                descartados_exclusao += 1
                continue

        matched = False

        # Regra 2 — CONCRETO USINADO (qualquer UF do órgão; a GEOGRAFIA decide depois
        # pelo LOCAL DA OBRA — assim órgãos estaduais/federais com sede longe não
        # são barrados aqui).
        if True:
            hit, score = None, 0
            if hit_concreto_forte:
                hit, score = hit_concreto_forte, 3
            elif any(k in objeto_norm for k in KEYWORDS_CONCRETO[2]):
                hit = next(k for k in KEYWORDS_CONCRETO[2] if k in objeto_norm)
                # "concreto" com contexto de usinado = PROVÁVEL; sem contexto =
                # POSSÍVEL (score 1) para o enriquecimento por itens confirmar via PDF.
                score = 2 if any(c in objeto_norm for c in CONTEXTO_USINADO) else 1
            elif any(k in objeto_norm for k in KEYWORDS_CONCRETO[1]):
                hit, score = next(k for k in KEYWORDS_CONCRETO[1] if k in objeto_norm), 1
            if hit:
                ed["material"] = "concreto"
                ed["score"] = score
                ed["score_label"] = SCORE_LABEL[score]
                ed["keyword_trigger"] = hit
                ed.setdefault("itens_encontrados", "")
                sobreviventes.append(ed)
                matched = True

        # Regra 3 — BRITA (só nos estados de brita = RJ)
        if not matched and uf in ESTADOS_BRITA:
            hit, score = None, 0
            if hit_brita_forte:
                hit, score = hit_brita_forte, 3
            elif any(k in objeto_norm for k in KEYWORDS_BRITA[2]):
                hit, score = next(k for k in KEYWORDS_BRITA[2] if k in objeto_norm), 2
            elif any(k in objeto_norm for k in KEYWORDS_BRITA[1]):
                hit, score = next(k for k in KEYWORDS_BRITA[1] if k in objeto_norm), 1
            if hit:
                ed["material"] = "brita"
                ed["score"] = score
                ed["score_label"] = SCORE_LABEL[score]
                ed["keyword_trigger"] = hit
                ed.setdefault("itens_encontrados", "")
                sobreviventes.append(ed)

    logging.info(
        "%d editais após filtro keyword (concreto usinado + brita); %d descartados por exclusão.",
        len(sobreviventes), descartados_exclusao,
    )
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
            logging.warning("Items endpoint erro para %s/%s/%s p%d: %s", cnpj, ano, seq, pagina, exc)
            break

        # O endpoint de itens do PNCP pode responder como LISTA (array) OU como
        # objeto {"data": [...], "totalPaginas": N}. Tratar os dois formatos.
        if isinstance(payload, list):
            itens = payload
            total_pag = 1
        elif isinstance(payload, dict):
            itens = payload.get("data") or payload.get("itens") or []
            total_pag = payload.get("totalPaginas") or 1
        else:
            itens, total_pag = [], 1

        for it in itens:
            if not isinstance(it, dict):
                continue
            desc = it.get("descricao") or it.get("descricaoItem") or it.get("descricaoCompleta") or ""
            if desc:
                descricoes.append(_normalize(desc))

        if pagina >= total_pag or not itens:
            break
        pagina += 1
        time.sleep(0.2)  # pausa leve entre páginas de itens

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
        # Blindagem: um edital problemático NUNCA pode derrubar o pipeline inteiro.
        try:
            cnpj = ed.get("_cnpj", "")
            ano = ed.get("_ano_compra", "")
            seq = ed.get("_seq_compra", "")
            descricoes = _buscar_itens_edital(cnpj, ano, seq)

            for desc_norm in descricoes:
                hit = next((k for k in kw_diretas if k in desc_norm), None)
                if hit:
                    ed["score"] = 2
                    ed["score_label"] = SCORE_LABEL[2]
                    ed["itens_encontrados"] = desc_norm[:200]
                    promovidos += 1
                    logging.info("Edital %s promovido a score=2 via item '%s'.",
                                 ed.get("numero_controle_pncp", "?"), hit)
                    break  # basta 1 item para confirmar
        except Exception as exc:
            logging.warning("Enriquecimento falhou para %s (ignorado): %s",
                            ed.get("numero_controle_pncp", "?"), exc)
            continue

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

    coords_offline = _carregar_coords_offline()
    atend, regex_obra, nome2key = _municipios_atendidos(filiais)

    def _match(coord, material, valor):
        """(tipo, filial, dist_km) se 'coord' está no raio para o material; senão None."""
        if coord is None:
            return None
        if material == "concreto" and usinas:
            d, f = _menor_distancia(coord, usinas)
            if d is not None and d <= RAIO_USINA_KM:
                return ("atendimento_usina", f, round(d, 2))
        if material == "brita" and pedreiras and (valor or 0) >= VALOR_MINIMO_PEDREIRA:
            d, f = _menor_distancia(coord, pedreiras)
            if d is not None and d <= RAIO_PEDREIRA_KM:
                return ("atendimento_pedreira", f, round(d, 2))
        return None

    falhas_geo = 0
    por_obra = 0
    a_confirmar = 0
    for ed in editais:
        mun_norm = _normalize(ed["municipio"])
        uf = ed["uf"]
        chave_geo = f"{mun_norm}|{uf}"

        # Coordenada do município do ÓRGÃO: offline → Nominatim (fallback)
        origem = coords_offline.get((mun_norm, uf))
        if origem is None:
            if chave_geo in geocode_cache:
                origem = geocode_cache[chave_geo]
            else:
                origem = _geocodificar_municipio(geocoder, ed["municipio"], ed["uf"])
                geocode_cache[chave_geo] = origem

        material = ed.get("material")
        valor = ed.get("valor_estimado") or 0
        local_obra = ""
        obra_coord = None
        esf = str(ed.get("esfera", "")).strip().lower()
        eh_estad_fed = esf[:1] in ("e", "f")
        cobertos = ESTADOS_BRITA if material == "brita" else ESTADOS_CONCRETO

        # A) qualifica pelo município do ÓRGÃO
        res = _match(origem, material, valor)

        # B) qualifica pelo LOCAL DA OBRA citado no objeto — SÓ para órgãos
        #    Estaduais/Federais (DER, DNIT, saneamento...). Prefeitura (municipal)
        #    compra para o PRÓPRIO município, então casar uma obra distante no
        #    texto seria falso-positivo (ex.: órgão no PI casando "Santos/SP").
        if res is None and regex_obra is not None and eh_estad_fed:
            mm = regex_obra.search(_normalize(ed["objeto"]))
            if mm:
                nome_obra = mm.group(1) or mm.group(2)
                key = nome2key.get(nome_obra) if nome_obra else None
                obra_coord = coords_offline.get(key) if key else None
                r2 = _match(obra_coord, material, valor)
                if r2:
                    res = r2
                    local_obra = f"{key[0].title()}/{key[1]}"
                    # A OBRA vira a localização exibida (cidade/UF que de fato atendemos),
                    # não a sede do órgão.
                    ed["municipio"], ed["uf"] = key[0].title(), key[1]
                    por_obra += 1

        if res:
            # Trava final: só estados que cobrimos (concreto: MG/SP/ES/RJ/PR/BA; brita: RJ).
            if ed["uf"] not in cobertos:
                continue  # fora da nossa área de atuação — descarta
            tipo, f, dist = res
            ed["tipo_atendimento"] = tipo
            ed["filial_mais_proxima"] = f"{f['nome']} ({f['municipio']}/{f['uf']})"
            ed["distancia_km"] = dist
            ed["local_obra"] = local_obra
            # Mapa: prioriza a coord do LOCAL DA OBRA (qdo qualificou por ela);
            # senão usa a sede do órgão.
            coord_plot = obra_coord or origem
            if coord_plot:
                ed["latitude"], ed["longitude"] = coord_plot[0], coord_plot[1]
            qualificados.append(ed)
            continue

        # C) Estadual/Federal com sinal forte (score 3) e sem local detectável →
        #    "a confirmar", MAS só se a SEDE do órgão estiver num estado coberto
        #    (não deixa surgir UF que não atendemos).
        if eh_estad_fed and int(ed.get("score") or 0) >= 3 and uf in cobertos:
            ed["tipo_atendimento"] = "local_a_confirmar"
            ed["local_obra"] = "a confirmar"
            ed["filial_mais_proxima"] = ""
            ed["distancia_km"] = ""
            a_confirmar += 1
            qualificados.append(ed)
            continue

        # Fora do alcance — descartado.
        if origem is None:
            falhas_geo += 1

    logging.info(
        "%d qualificados (geo): %d por local-da-obra, %d 'a confirmar' (estad/fed), %d sem coordenada.",
        len(qualificados), por_obra, a_confirmar, falhas_geo,
    )
    return qualificados


_COORDS_OFFLINE: dict[tuple[str, str], tuple[float, float]] | None = None


def _carregar_coords_offline() -> dict[tuple[str, str], tuple[float, float]]:
    """Carrega coordenadas dos municípios brasileiros de dados/municipios_coords.csv.

    Base estática (IBGE, ~5.570 municípios) → geocoding instantâneo e SEM falha de
    rede. Evita que editais perto sejam descartados por erro/limite do Nominatim
    (Nominatim vira só fallback para nomes que não estiverem na base).
    Chave: (municipio_normalizado, UF). Cache em memória após a 1ª carga.
    """
    global _COORDS_OFFLINE
    if _COORDS_OFFLINE is not None:
        return _COORDS_OFFLINE
    coords: dict[tuple[str, str], tuple[float, float]] = {}
    arq = PROJECT_ROOT / "dados" / "municipios_coords.csv"
    try:
        with arq.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                try:
                    coords[(row["municipio_norm"], row["uf"])] = (
                        float(row["lat"]), float(row["lng"])
                    )
                except (ValueError, KeyError):
                    continue
        logging.info("Coordenadas offline carregadas: %d municípios.", len(coords))
    except FileNotFoundError:
        logging.warning("dados/municipios_coords.csv não encontrado — usando só Nominatim.")
    _COORDS_OFFLINE = coords
    return coords


# Cache (1×/execução) dos municípios dentro do raio das filiais + índice de nomes.
_MUN_ATENDIDOS: dict[tuple[str, str], dict] | None = None
_MUN_REGEX: "re.Pattern | None" = None
_MUN_NOME2KEY: dict[str, tuple[str, str]] = {}
# Nomes de municípios que também são palavra comum (evita falso-positivo no texto).
_MUN_BLOCKLIST = {
    "serra", "campos", "bonito", "alegre", "capela", "cristina", "patos",
    "boa vista", "santa rita", "bom jesus", "pirapora", "cataguases",
    "central", "vargem", "monte", "matias", "lagoa", "areia", "areias",
    "aluminio", "salvador", "cachoeira", "serrinha", "tocantins", "claudio",
    "castelo", "roque", "margarida", "vitoria", "uniao", "palmas", "pedra",
    "lavras", "carmo", "rosario", "bela vista", "cordeiro", "porto", "barra",
    "santos", "atalaia",  # sobrenome/nome comum → falso-positivo em texto
}


def _municipios_atendidos(filiais: dict[str, list[dict]]):
    """Pré-calcula os municípios dentro do raio das usinas/pedreiras e um regex
    para detectar o LOCAL DA OBRA citado no objeto do edital.

    Retorna (mapa, regex, nome2key):
      mapa[(municipio_norm, uf)] = {"filial", "dist_km", "tipo"}
      regex casa nomes de municípios atendidos no texto (limite de palavra)
      nome2key[municipio_norm] = (municipio_norm, uf)  # 1º atendido com aquele nome
    """
    global _MUN_ATENDIDOS, _MUN_REGEX, _MUN_NOME2KEY
    if _MUN_ATENDIDOS is not None:
        return _MUN_ATENDIDOS, _MUN_REGEX, _MUN_NOME2KEY

    coords = _carregar_coords_offline()
    usinas = filiais.get("usinas") or []
    pedreiras = filiais.get("pedreiras") or []
    atend: dict[tuple[str, str], dict] = {}
    for (mun, uf), origem in coords.items():
        # Só municípios nos estados que cobrimos (concreto) — evita casar obra
        # em estado fora da área e reduz o regex de busca no texto.
        if usinas and uf in ESTADOS_CONCRETO:
            du, fu = _menor_distancia(origem, usinas)
            if du is not None and du <= RAIO_USINA_KM:
                atend[(mun, uf)] = {"filial": fu, "dist_km": round(du, 2), "tipo": "usina"}
                continue
        if uf == "RJ" and pedreiras:
            dp, fp = _menor_distancia(origem, pedreiras)
            if dp is not None and dp <= RAIO_PEDREIRA_KM:
                atend[(mun, uf)] = {"filial": fp, "dist_km": round(dp, 2), "tipo": "pedreira"}

    nome2key: dict[str, tuple[str, str]] = {}
    nomes: list[str] = []
    for (mun, uf) in atend:
        nome2key.setdefault(mun, (mun, uf))
        if len(mun) >= 6 and mun not in _MUN_BLOCKLIST:
            nomes.append(mun)
    nomes.sort(key=len, reverse=True)  # casa o nome mais específico primeiro
    if nomes:
        alt = "|".join(re.escape(n) for n in nomes)
        # Exige CONTEXTO de local para evitar falso-positivo (ex.: "areias", "aluminio"):
        #  (1) "municipio de X" / "cidade de X" / "comarca de X" ...
        #  (2) "X/UF" ou "X - UF"  (UF dos nossos estados)
        ctx = (r"(?:municipios?\s+de|cidade\s+de|comarca\s+de|distrito\s+de|"
               r"localidade\s+de|na\s+cidade\s+de|sede\s+do\s+municipio\s+de)\s+(" + alt + r")\b")
        suf = r"\b(" + alt + r")\s*[/\-]\s*(?:mg|sp|rj|es|pr|ba)\b"
        regex = re.compile(ctx + "|" + suf)
    else:
        regex = None

    _MUN_ATENDIDOS, _MUN_REGEX, _MUN_NOME2KEY = atend, regex, nome2key
    logging.info("Municípios atendidos (raio das filiais): %d; nomes p/ busca no texto: %d.",
                 len(atend), len(nomes))
    return atend, regex, nome2key


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
        ed.get("fonte", "PNCP"),
        ed.get("origem_plataforma", ""),
        ed.get("local_obra", ""),
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
# FONTES ADICIONAIS — ComprasNet e BLL (Fase 10: multi-fonte)
# =========================================================================

# Compras.gov.br Dados Abertos — API OFICIAL gratuita, sem login. Endpoint de
# contratações 14.133 (espelho do PNCP), FILTRÁVEL POR UF → redundância confiável
# que recupera o que a varredura direta do PNCP perde por throttling/limite de página.
COMPRASGOV_URL = "https://dadosabertos.compras.gov.br/modulo-contratacoes/1_consultarContratacoes_PNCP_14133"
COMPRASGOV_UFS = ("MG", "SP", "RJ", "ES", "PR", "BA")   # estados com filiais
COMPRASGOV_MODALIDADES = (6, 5, 8)  # Pregão Eletrônico, Pregão Presencial, Dispensa

# BLL — endpoint JSON interno do portal (não documentado oficialmente).
# Cobre ~5.000 municípios em SP/MG/PR/RJ que usam a plataforma BLL.
BLL_BUSCA_URL = "https://bll.org.br/api/oportunidades/busca"
# Palavras-chave enviadas diretamente à BLL (buscam no título/objeto do edital)
BLL_KEYWORDS = ["concreto", "brita", "material de construcao"]


def _normalizar_comprasnet(item: dict) -> dict:
    """Normaliza um registro da API Compras.gov.br (contratações 14.133) ao schema interno.

    Schema real (dadosabertos.compras.gov.br/modulo-contratacoes/...14133):
    objetoCompra, unidadeOrgaoUfSigla, unidadeOrgaoMunicipioNome, orgaoEntidadeRazaoSocial,
    orgaoEntidadeEsferaId, modalidadeNome, valorTotalEstimado, numeroControlePNCP,
    orgaoEntidadeCnpj, anoCompraPncp, sequencialCompraPncp, dataAbertura/EncerramentoPropostaPncp.
    """
    cnpj = str(item.get("orgaoEntidadeCnpj") or "")
    ano = str(item.get("anoCompraPncp") or "")
    seq = str(item.get("sequencialCompraPncp") or "")
    try:
        valor = float(item.get("valorTotalEstimado") or 0)
    except (TypeError, ValueError):
        valor = 0.0
    esfera_id = (item.get("orgaoEntidadeEsferaId") or "").upper()
    link_pncp = f"https://pncp.gov.br/app/editais/{cnpj}/{ano}/{seq}" if (cnpj and ano and seq) else ""
    return {
        "numero_controle_pncp": item.get("numeroControlePNCP") or "",
        "numero_edital": str(item.get("numeroCompra") or ""),
        "modalidade": item.get("modalidadeNome") or "",
        "orgao": item.get("orgaoEntidadeRazaoSocial") or "",
        "esfera": ESFERA_NOME.get(esfera_id, esfera_id),
        "municipio": item.get("unidadeOrgaoMunicipioNome") or "",
        "uf": (item.get("unidadeOrgaoUfSigla") or "").upper(),
        "objeto": item.get("objetoCompra") or "",
        "valor_estimado": valor,
        "data_abertura": item.get("dataAberturaPropostaPncp") or "",
        "data_encerramento": item.get("dataEncerramentoPropostaPncp") or "",
        "link_pncp": link_pncp,
        "link_sistema_origem": "",
        "fonte": "COMPRASGOV",
        "origem_plataforma": "Compras.gov.br",
        "_cnpj": cnpj,
        "_ano_compra": ano,
        "_seq_compra": seq,
    }


def _coletar_comprasnet(data_inicial: date, data_final: date) -> list[dict]:
    """Coleta contratações 14.133 do Compras.gov.br Dados Abertos (oficial, grátis),
    filtrando pelos estados das filiais (COMPRASGOV_UFS).

    É um espelho do PNCP, porém UF-filtrável e estável → **recupera o que a varredura
    direta do PNCP perde por limite de página/throttling**. A dedup por número de
    controle (em main) junta com o PNCP sem duplicar. Falha silenciosa por UF/modalidade.
    """
    coletados: list[dict] = []
    di = data_inicial.strftime("%Y-%m-%d")
    df = data_final.strftime("%Y-%m-%d")
    for uf in COMPRASGOV_UFS:
        for mod in COMPRASGOV_MODALIDADES:
            pagina = 1
            while True:
                # Retry por página: o Compras.gov.br dá timeout transitório (45s).
                # Antes, 1 timeout abortava TODA a paginação do UF/mod → perdia editais.
                payload = None
                for tentativa in range(1, 4):
                    try:
                        resp = requests.get(COMPRASGOV_URL, params={
                            "pagina": pagina, "tamanhoPagina": 100,
                            "unidadeOrgaoUfSigla": uf, "codigoModalidade": mod,
                            "dataPublicacaoPncpInicial": di, "dataPublicacaoPncpFinal": df,
                        }, timeout=PNCP_TIMEOUT_S, headers={"Accept": "application/json"})
                        if resp.status_code in (204, 404):
                            payload = {"resultado": []}
                            break
                        resp.raise_for_status()
                        payload = resp.json()
                        break
                    except (requests.RequestException, ValueError) as exc:
                        if tentativa >= 3:
                            logging.warning("Compras.gov.br erro (uf=%s mod=%s p=%d) após %d tentativas: %s — desiste deste uf/mod.",
                                            uf, mod, pagina, tentativa, exc)
                        else:
                            time.sleep(PNCP_PAUSA_S * tentativa * 2)
                if payload is None:
                    break  # esgotou as tentativas — aborta este uf/mod (paginação é sequencial)
                itens = payload.get("resultado") or []
                if not itens:
                    break
                for it in itens:
                    try:
                        coletados.append(_normalizar_comprasnet(it))
                    except Exception:
                        continue
                total_pags = int(payload.get("totalPaginas") or 1)
                if pagina >= total_pags or pagina >= PNCP_MAX_PAGINAS:
                    break
                pagina += 1
                time.sleep(PNCP_PAUSA_S)
    if coletados:
        logging.info("Compras.gov.br (14.133): %d editais brutos (%s a %s, %d UFs).",
                     len(coletados), di, df, len(COMPRASGOV_UFS))
    return coletados


def _normalizar_bll(item: dict) -> dict:
    """Converte um registro da API interna da BLL para o schema interno do scraper."""
    valor_raw = item.get("valorEstimado") or item.get("valor") or 0
    try:
        valor = float(str(valor_raw).replace(",", ".").replace("R$", "").replace(" ", ""))
    except (TypeError, ValueError):
        valor = 0.0

    municipio_raw = item.get("municipio") or item.get("cidade") or ""
    uf_raw = (item.get("uf") or item.get("estado") or "").upper()
    if len(uf_raw) > 2:
        uf_raw = uf_raw[:2]

    id_bll = str(item.get("id") or item.get("codigo") or item.get("numeroEdital") or "")
    link = item.get("link") or item.get("url") or item.get("linkEdital") or ""

    return {
        "numero_controle_pncp": f"BLL-{id_bll}" if id_bll else "",
        "numero_edital": item.get("numeroEdital") or item.get("numero") or "",
        "modalidade": item.get("modalidade") or item.get("tipoLicitacao") or "BLL",
        "orgao": item.get("orgao") or item.get("nomeOrgao") or item.get("entidade") or "",
        "esfera": item.get("esfera") or "M",  # BLL cobre principalmente municípios
        "municipio": municipio_raw,
        "uf": uf_raw,
        "objeto": item.get("objeto") or item.get("descricao") or item.get("titulo") or "",
        "valor_estimado": valor,
        "data_abertura": item.get("dataAbertura") or item.get("dataPublicacao") or "",
        "data_encerramento": item.get("dataEncerramento") or item.get("dataFim") or "",
        "link_pncp": "",
        "link_sistema_origem": link,
        "fonte": "BLL",
        "origem_plataforma": "BLL",
        "_cnpj": "",
        "_ano_compra": "",
        "_seq_compra": "",
    }


def _coletar_bll(data_inicial: date, data_final: date) -> list[dict]:
    """Coleta editais da BLL via endpoint JSON interno do portal.

    Busca por palavras-chave relevantes (concreto, brita) na janela de datas.
    Falha silenciosa: se o endpoint estiver indisponível ou bloqueado,
    loga WARNING e retorna lista vazia.
    """
    coletados: list[dict] = []
    fmt_data = lambda d: d.strftime("%d/%m/%Y")
    di = fmt_data(data_inicial)
    df = fmt_data(data_final)

    for kw in BLL_KEYWORDS:
        pagina = 1
        while True:
            try:
                resp = requests.get(
                    BLL_BUSCA_URL,
                    params={
                        "palavraChave": kw,
                        "dataInicio": di,
                        "dataFim": df,
                        "pagina": pagina,
                        "itensPorPagina": 50,
                    },
                    headers={
                        "User-Agent": "Mozilla/5.0 (compatible; ConcrelagosBot/1.0)",
                        "Accept": "application/json",
                    },
                    timeout=30,
                )
                if resp.status_code in (401, 403, 404):
                    logging.warning(
                        "BLL API retornou HTTP %d (keyword=%r) — endpoint pode ter mudado. "
                        "Pulando fonte BLL.", resp.status_code, kw
                    )
                    return coletados  # falha silenciosa completa
                resp.raise_for_status()
                payload = resp.json()
            except requests.RequestException as exc:
                logging.warning("BLL API erro de rede (keyword=%r p=%d): %s — pulando BLL.", kw, pagina, exc)
                return coletados
            except ValueError as exc:
                logging.warning("BLL JSON inválido (keyword=%r p=%d): %s", kw, pagina, exc)
                break

            itens = (
                payload.get("itens") or
                payload.get("data") or
                payload.get("result") or
                (payload if isinstance(payload, list) else [])
            )
            if not itens:
                break

            for item in itens:
                try:
                    coletados.append(_normalizar_bll(item))
                except Exception as exc:
                    logging.debug("BLL: edital ignorado por estrutura incompleta: %s", exc)

            total_pags = payload.get("totalPaginas") or payload.get("totalPages") or 1
            if pagina >= int(total_pags) or pagina >= PNCP_MAX_PAGINAS:
                break
            pagina += 1
            time.sleep(0.5)

    if coletados:
        logging.info("BLL retornou %d editais brutos na janela %s a %s.", len(coletados), di, df)
    return coletados


def _deduplicar_fontes(editais: list[dict]) -> list[dict]:
    """Remove duplicatas entre fontes, priorizando PNCP quando o mesmo edital aparecer em múltiplas fontes."""
    vistos: set[str] = set()
    resultado: list[dict] = []
    for ed in editais:
        chave = (
            ed.get("numero_controle_pncp") or
            ed.get("link_sistema_origem") or
            (ed.get("objeto", "")[:60] + "|" + ed.get("uf", ""))
        )
        if chave and chave not in vistos:
            vistos.add(chave)
            resultado.append(ed)
    return resultado


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
    # Destino padrão: caixa oficial das licitações. Usa o padrão quando a env var
    # está ausente OU vazia (o Actions seta a var vazia se o Secret não existir).
    destinatarios_raw = (os.getenv("NOTIFICACAO_EMAIL_PARA") or "").strip() or "licitacao.concrelagos@gmail.com"

    # Só remetente + senha (App Password) precisam ser configurados; o destino já tem padrão.
    if not (remetente and senha):
        logging.info("Notificação por e-mail desativada (faltam NOTIFICACAO_EMAIL_DE/SENHA).")
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

    def _card_html(ed: dict) -> str:
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
        return (
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

    def _html_uf(uf: str, eds: list[dict]) -> str:
        """Boletim de UM estado, com botão que abre o Boletim já filtrado nesse UF."""
        cards = "".join(_card_html(ed) for ed in eds)
        link_estado = f"{APP_URL}?uf={uf}"
        return (
            '<!DOCTYPE html><html><body style="font-family:Arial,sans-serif;background:#F7F8FA;margin:0;padding:20px;">'
            '<div style="max-width:620px;margin:0 auto;background:white;border-radius:8px;overflow:hidden;'
            'box-shadow:0 2px 8px rgba(0,0,0,0.1);">'
            '<div style="background:#0E2A47;color:white;padding:20px 24px;">'
            f'<h2 style="margin:0;font-size:18px;">🏗️ Concrelagos · Licitações {uf}</h2>'
            f'<p style="margin:6px 0 0;opacity:0.85;font-size:14px;">'
            f'{len(eds)} novo(s) edital(is) qualificado(s) em {uf}</p>'
            '</div>'
            # Botão de acesso direto ao boletim do estado (topo, bem visível)
            f'<div style="text-align:center;padding:16px 24px 4px;">'
            f'<a href="{link_estado}" style="display:inline-block;background:#C5A572;color:#0E2A47;'
            f'font-weight:700;font-size:14px;text-decoration:none;padding:11px 22px;border-radius:6px;">'
            f'📋 Abrir boletim de {uf} →</a></div>'
            + cards +
            f'<div style="background:#F3F4F6;padding:14px 24px;text-align:center;'
            f'font-size:12px;color:#6B7280;border-top:1px solid #E5E7EB;">'
            f'<a href="{link_estado}" style="color:#0E2A47;font-weight:600;">Ver boletim de {uf}</a> '
            f'— análise com IA, filtros e exportação.</div>'
            '</div></body></html>'
        )

    # Agrupa os novos editais POR ESTADO → um e-mail (boletim) por UF.
    por_uf: dict[str, list[dict]] = {}
    for ed in novos:
        uf = str(ed.get("uf") or "??").upper().strip() or "??"
        por_uf.setdefault(uf, []).append(ed)

    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as srv:
            srv.ehlo()
            srv.starttls()
            srv.login(remetente, senha)
            for uf, eds in sorted(por_uf.items()):
                msg = MIMEMultipart("alternative")
                msg["Subject"] = f"🏗️ {uf} · {len(eds)} novo(s) edital(is) — Concrelagos Hub"
                msg["From"] = remetente
                msg["To"] = ", ".join(destinatarios)
                msg.attach(MIMEText(_html_uf(uf, eds), "html", "utf-8"))
                srv.sendmail(remetente, destinatarios, msg.as_string())
        logging.info("Notificações por estado enviadas para %s: %s",
                     destinatarios, {uf: len(e) for uf, e in sorted(por_uf.items())})
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
        # ---- PNCP (fonte primária) ----
        brutos_pncp = buscar_editais_pncp(data_inicial, data_final)

        # ---- ComprasNet (fonte secundária: federal/estadual pré-migração) ----
        brutos_cn = _coletar_comprasnet(data_inicial, data_final)

        # ---- BLL (fonte terciária: municípios SP/MG/PR/RJ) ----
        brutos_bll = _coletar_bll(data_inicial, data_final)

        # Consolida e deduplica — PNCP tem precedência
        brutos = _deduplicar_fontes(brutos_pncp + brutos_cn + brutos_bll)
        logging.info(
            "Multi-fonte: PNCP=%d + ComprasNet=%d + BLL=%d -> deduplicado=%d editais brutos",
            len(brutos_pncp), len(brutos_cn), len(brutos_bll), len(brutos),
        )

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
        "FUNIL: brutos=%d -> keyword=%d -> geo=%d -> novos=%d | tempo=%.1fs",
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
