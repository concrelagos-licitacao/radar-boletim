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

# Portão de IA (Gemini): obra genérica (score=1) NÃO é gravada crua — passa ANTES
# pela IA, que lê o edital e só promove se confirmar concreto usinado ou brita/pedras.
# Custo zero: só modelos "flash" (camada gratuita). Requer GEMINI_API_KEY no ambiente.
IA_GATE_ATIVA     = os.getenv("IA_GATE_ATIVA", "true").lower() == "true"
IA_GATE_MAX       = int(os.getenv("IA_GATE_MAX", "80"))   # teto de chamadas Gemini por execução
IA_GATE_MIN_CHARS = 100                                   # texto mínimo confiável para triar
IA_GATE_PAUSA_S   = float(os.getenv("IA_GATE_PAUSA_S", "4.5"))  # pausa entre editais (free tier = 15 req/min)
ABA_TRIAGEM       = "Triagem IA"                          # cache das decisões da IA

# =========================================================================
# CONLICITAÇÃO — 4ª fonte (boletim .xlsx). Veredito do conselho: NÃO raspar a
# plataforma (sessão/login frágil); consumir o BOLETIM .xlsx que o ConLicitação
# entrega. O valor-add é o FILTRO (só PE de concreto usinado/brita) + toda a
# inteligência (geo, score, gate IA) que já roda no pipeline. Falha silenciosa.
# CONLICITACAO_XLSX_DIR = pasta onde o(s) .xlsx do boletim são deixados.
CONLICITACAO_ATIVO    = os.getenv("CONLICITACAO_ATIVO", "true").lower() == "true"
CONLICITACAO_XLSX_DIR = os.getenv("CONLICITACAO_XLSX_DIR", "")
ABA_CONLIC            = "ConLic Lidos"                     # idempotência: nc já gravados
ABA_CONLIC_INBOX      = "ConLic Inbox"                     # transporte automático (Apps Script)
# Mapa modalidade pelo PREFIXO do número do edital ConLicitação (ex.: "PE/0006/2025").
CONLIC_MODALIDADE_PREFIXO = {
    "PE": "Pregão Eletrônico",
    "DL": "Dispensa",
    "CR": "Concorrência",
    "PR": "Pregão Presencial",
    "SM": "Outros",
}
# Plataformas operacionais detectáveis no padrão "... https://(dominio) ..." do objeto.
CONLIC_ORIGEM_DOMINIOS = (
    ("licitar.digital", "Licitar Digital"),
    ("licitanet", "LICITANET"),
    ("bnccompras", "BNC"),
    ("bllcompras", "BLL"),
    ("jornaldolicitante", "Jornal do Licitante"),
    ("pncp.gov.br", "PNCP"),
)

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
    # -- portão de IA (obras genéricas confirmadas pela Gemini) --
    "ia_verificado",          # True quando a IA confirmou concreto/brita numa obra genérica (score=1→2)
    "ia_produto",             # produto que a IA identificou (ex: "concreto usinado fck 25")
    "ia_justificativa",       # 1 frase da IA explicando a relevância
    "ia_confianca",           # 0-100 confiança da IA
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
def buscar_editais_pncp(data_inicial: date, data_final: date) -> tuple[list[dict], bool]:
    """Consulta o endpoint público de contratações publicadas no PNCP.

    Pagina automaticamente e varre as modalidades em PNCP_MODALIDADES.
    Falhas de rede são logadas, mas o que já foi coletado é preservado.

    Retorna (coletados, integra). `integra` é True somente se TODAS as
    modalidades responderam de forma confiável: nenhuma teve a página 1
    falhada por timeout/throttling esgotado (payload None com 0 itens
    coletados até então). Esse flag é o sinal de integridade usado pelo
    watermark de cobertura — uma modalidade que esgotou retries por timeout
    é indistinguível de "0 editais legítimos" no total agregado, então
    precisamos do sinal por modalidade para não avançar o watermark sobre
    uma coleta PARCIAL (que perderia a modalidade que falhou).
    """
    coletados: list[dict] = []

    contagem_por_modalidade: dict[int, int] = {}
    modalidades_falhas: list[int] = []  # modalidades cuja pág. 1 falhou (timeout/throttling esgotado)
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
                # payload None = retries esgotados (timeout/throttling) nesta página.
                # Marca a modalidade como FALHA em QUALQUER página, não só na pág. 1:
                #   - pág. 1 falhada  => modalidade inteira perdida.
                #   - pág. >1 falhada => coleta PARCIAL da modalidade (já trouxe N páginas,
                #     mas as restantes ficaram de fora) — se o watermark avançasse aqui,
                #     perderíamos os editais das páginas não lidas para sempre.
                # Em ambos os casos a coleta da janela é INCOMPLETA => watermark NÃO avança;
                # a próxima rodada recupera o buraco (overlap garante que não se perde nada).
                # (HTTP 422 também retorna None na pág. 1, mas significa "sem dados na
                #  janela", que é legítimo; nesse caso paginamos só a pág. 1 e o
                #  falso-positivo conservador apenas adia o avanço do watermark um ciclo.)
                modalidades_falhas.append(modalidade)
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

    # Integridade: True só se TODAS as modalidades responderam (nenhuma com pág.1 falhada).
    integra = not modalidades_falhas
    if modalidades_falhas:
        logging.warning("PNCP COLETA PARCIAL — modalidades sem resposta confiável (timeout/throttling esgotado): %s. "
                        "Watermark NÃO será avançado para que a próxima rodada recupere o buraco.",
                        modalidades_falhas)
    return coletados, integra


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

        # Veto absoluto de ASFALTO para o ramo do concreto: "concreto betuminoso
        # usinado a quente (CBUQ)" contém "concreto ... usinado" e enganaria o
        # filtro — mas asfalto NÃO é nosso. (A brita segue avaliada normalmente:
        # vender brita PARA usina de asfalto é venda de brita.)
        _eh_asfalto = any(x in objeto_norm for x in
                          ("betuminoso", "cbuq", "asfaltic", "asfalto", "massa asfaltica"))

        # Regra 2 — CONCRETO USINADO (qualquer UF do órgão; a GEOGRAFIA decide depois
        # pelo LOCAL DA OBRA — assim órgãos estaduais/federais com sede longe não
        # são barrados aqui).
        if not _eh_asfalto:
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

    # Keywords score=3 separadas por material: item de BRITA só promove edital
    # em estado coberto por pedreira (brita é SÓ RJ) — senão driblaria a regra.
    kw_concreto3 = set(KEYWORDS_CONCRETO[3])
    kw_brita3 = set(KEYWORDS_BRITA[3])

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
                hit_concreto = next((k for k in kw_concreto3 if k in desc_norm), None)
                hit_brita = next((k for k in kw_brita3 if k in desc_norm), None)
                # Brita só vale em estado coberto por pedreira (RJ).
                if hit_brita and ed.get("uf") not in ESTADOS_BRITA:
                    hit_brita = None
                hit = hit_concreto or hit_brita
                if hit:
                    ed["score"] = 2
                    ed["score_label"] = SCORE_LABEL[2]
                    ed["itens_encontrados"] = desc_norm[:200]
                    if hit_brita and not hit_concreto:
                        ed["material"] = "brita"
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

    def _match(coord, material, valor, uf_local):
        """(tipo, filial, dist_km) se 'coord' está no raio para o material; senão None.

        REGRA DE NEGÓCIO: a filial precisa estar no MESMO ESTADO do edital/obra —
        edital de MG atendido por usina de SP NÃO vale (não participamos)."""
        if coord is None:
            return None
        if material == "concreto" and usinas:
            cand = [u for u in usinas if u.get("uf") == uf_local]
            if cand:
                d, f = _menor_distancia(coord, cand)
                if d is not None and d <= RAIO_USINA_KM:
                    return ("atendimento_usina", f, round(d, 2))
        if material == "brita" and pedreiras and (valor or 0) >= VALOR_MINIMO_PEDREIRA:
            cand = [p for p in pedreiras if p.get("uf") == uf_local]
            if cand:
                d, f = _menor_distancia(coord, cand)
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

        # A) qualifica pelo município do ÓRGÃO (filial do MESMO estado)
        res = _match(origem, material, valor, uf)

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
                r2 = _match(obra_coord, material, valor, key[1] if key else "")
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
        # REGRA: a filial precisa ser do MESMO estado do município (MG←SP não vale).
        if usinas and uf in ESTADOS_CONCRETO:
            cand = [u for u in usinas if u.get("uf") == uf]
            if cand:
                du, fu = _menor_distancia(origem, cand)
                if du is not None and du <= RAIO_USINA_KM:
                    atend[(mun, uf)] = {"filial": fu, "dist_km": round(du, 2), "tipo": "usina"}
                    continue
        if uf == "RJ" and pedreiras:
            candp = [p for p in pedreiras if p.get("uf") == uf]
            if candp:
                dp, fp = _menor_distancia(origem, candp)
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
        ed.get("ia_verificado", False),
        ed.get("ia_produto", ""),
        ed.get("ia_justificativa", ""),
        ed.get("ia_confianca", ""),
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
                        # Erro de cliente determinístico (ex.: 400 p/ modalidade não
                        # suportada neste endpoint) → re-tentar não adianta. Pula este
                        # uf/mod (o PNCP já cobre essa modalidade). 429 = rate limit (re-tenta).
                        if 400 <= resp.status_code < 500 and resp.status_code != 429:
                            logging.info("Compras.gov.br HTTP %s (uf=%s mod=%s) — modalidade não aceita aqui; pula (PNCP cobre).",
                                         resp.status_code, uf, mod)
                            payload = None
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


# =========================================================================
# CONLICITAÇÃO — 4ª fonte (boletim .xlsx)
# =========================================================================
# _COLMAP: variações comuns de nome de coluna -> chave canônica usada aqui.
# TODO(conlic): o layout EXATO do .xlsx do boletim AINDA NÃO foi confirmado.
# Quando vier um .xlsx real do ConLicitação, ABRA-O e ajuste estas variações
# (adicione/remova nomes de coluna). Nada quebra se a coluna faltar — usamos
# sempre row.get(...) com default "". Confirme principalmente as colunas de
# DATAS (pode vir tudo num campo "datas" textual) e o "Nº ConLicitação".
_COLMAP = {
    # Confirmado contra o .xlsx real do boletim (abas 'Licitações'/'Acompanhamentos'):
    # colunas — Número ConLicitação, Código, Órgão, Endereço, Cidade, Estado, CEP,
    # Edital, Site 1, Site 2, Processo, Valor Estimado, Itens, Situação, Documento,
    # Abertura, Prazo, Objeto, Observação, Anexos, Atualizada em.
    "objeto": ("objeto", "descricao", "descrição", "objeto da licitacao", "objeto da licitação"),
    "edital": ("edital", "numero", "número", "numero do edital", "número do edital", "n edital", "nº edital"),
    "orgao": ("orgao", "órgão", "orgao/entidade", "órgão/entidade", "entidade", "comprador"),
    "cidade": ("cidade", "municipio", "município", "cidade/uf", "cidade - uf", "municipio/uf"),
    "uf": ("estado", "uf", "sigla uf"),
    # No .xlsx as datas vêm em COLUNAS separadas (Abertura/Prazo, já em datetime);
    # 'datas' é o fallback p/ o blob textual do HTML.
    "data_abertura": ("abertura", "data abertura", "data de abertura", "sessao", "sessão"),
    "data_encerramento": ("prazo", "encerramento", "data encerramento", "limite", "documento"),
    "datas": ("datas", "datas importantes"),
    "valor_estimado": ("valor estimado", "valor_estimado", "valor", "valor estimado (r$)", "valor (r$)"),
    "nc": ("numero conlicitacao", "número conlicitação", "nº conlicitacao", "nº conlicitação",
           "n conlicitacao", "nc", "controle"),  # NÃO 'codigo' (é o código do órgão)
    "status": ("situacao", "situação", "status"),
}


def _conlic_get(row: dict, canon: str) -> str:
    """Lê do dict de linha do .xlsx por chave canônica, tolerante a nomes de
    coluna variados (case/acentos/espacos). Nunca quebra: retorna "" se faltar."""
    # índice normalizado uma vez por linha seria mais rápido, mas o volume é baixo
    # (um boletim tem dezenas de linhas) — mantém simples e legível.
    norm_row = {}
    for k, v in row.items():
        nk = _normalize(str(k)).strip()
        norm_row[nk] = v
    for variante in _COLMAP.get(canon, ()):  # variantes já em minúsculo/sem acento esperado
        nv = _normalize(variante).strip()
        if nv in norm_row and norm_row[nv] not in (None, ""):
            return str(norm_row[nv]).strip()
    return ""


def _conlic_origem_plataforma(objeto: str) -> str:
    """Detecta a plataforma operacional pelo padrão '... https://(dominio) ...' no objeto."""
    s = (objeto or "").lower()
    if not s:
        return ""
    for dominio, rotulo in CONLIC_ORIGEM_DOMINIOS:
        if dominio in s:
            return rotulo
    return ""


def _conlic_parse_datas(texto: str) -> tuple[str, str]:
    """Best-effort: extrai (data_abertura, data_encerramento) ISO do texto de 'datas'.

    O boletim costuma trazer rótulos do tipo 'Abertura: 12/06/2026', 'Prazo: ...',
    'Documento: ...'. Pegamos a 1ª data dd/mm/aaaa após 'Abertura' como abertura e a
    1ª após 'Prazo'/'Encerr' como encerramento. Se nada casar, retorna ('', '').
    Nunca quebra.
    """
    if not texto:
        return "", ""
    t = str(texto)

    def _iso(m: str) -> str:
        try:
            d, mth, y = m.split("/")
            if len(y) == 2:
                y = "20" + y
            di, mi, yi = int(d), int(mth), int(y)
            if not (1 <= mi <= 12 and 1 <= di <= 31):
                return ""  # data malformada (ex.: 32/13/2026) — descarta
            return f"{yi:04d}-{mi:02d}-{di:02d}"
        except Exception:
            return ""

    def _data_apos(rotulos: tuple) -> str:
        for rot in rotulos:
            m = re.search(rot + r"[^0-9]{0,20}(\d{2}/\d{2}/\d{2,4})", t, re.IGNORECASE)
            if m:
                iso = _iso(m.group(1))
                if iso:
                    return iso
        return ""

    abertura = _data_apos((r"abertura", r"sess[aã]o", r"documento"))
    encerr = _data_apos((r"prazo", r"encerr", r"limite", r"propost"))
    # Fallback: se só houver UMA data no texto inteiro, usa-a como abertura.
    if not abertura and not encerr:
        m = re.search(r"(\d{2}/\d{2}/\d{2,4})", t)
        if m:
            abertura = _iso(m.group(1))
    return abertura, encerr


def _normalizar_conlicitacao(lic: dict) -> dict | None:
    """Converte uma licitação do boletim ConLicitação ao schema canônico do Hub.

    MURO ANTICORRUPÇÃO: descarta (retorna None) se faltar objeto OU cidade/uf OU nc,
    e descarta status terminal 'ANULADA'/'REVOGADA' (não é oportunidade viva).

    Espelha 1:1 as chaves de _extrair_edital / _normalizar_bll. _cnpj/_ano/_seq
    ficam "" (ConLic não traz PNCP estruturado) — só perde enriquecimento por itens.
    """
    objeto = str(lic.get("objeto") or "").strip()
    nc = str(lic.get("nc") or "").strip()
    cidade_raw = str(lic.get("cidade") or "").strip()
    uf_raw = str(lic.get("uf") or "").strip().upper()

    # split de 'Cidade - UF' (ou 'Cidade/UF') quando a UF não veio em coluna própria
    municipio = cidade_raw
    if not uf_raw and cidade_raw:
        m = re.split(r"\s*[-/]\s*", cidade_raw)
        if len(m) >= 2 and len(m[-1].strip()) == 2:
            municipio = " - ".join(m[:-1]).strip()
            uf_raw = m[-1].strip().upper()
    elif uf_raw and cidade_raw:
        # remove UF redundante do fim do nome da cidade, se houver
        municipio = re.sub(r"\s*[-/]\s*" + re.escape(uf_raw) + r"\s*$", "", cidade_raw, flags=re.IGNORECASE).strip()
    if len(uf_raw) > 2:
        uf_raw = uf_raw[:2]

    # MURO ANTICORRUPÇÃO — campos mínimos
    if not objeto or not municipio or not uf_raw or not nc:
        return None
    status = str(lic.get("status") or "").strip().upper()
    if any(t in status for t in ("ANULAD", "REVOGAD")):
        return None

    # modalidade pelo prefixo do número do edital (ex.: "PE/0006/2025")
    edital = str(lic.get("edital") or "").strip()
    prefixo = ""
    m = re.match(r"\s*([A-Za-z]{2})\s*/", edital)
    if m:
        prefixo = m.group(1).upper()
    modalidade = CONLIC_MODALIDADE_PREFIXO.get(prefixo, "Outros" if prefixo else "")

    # valor estimado tolerante a "R$ 1.234,56"
    valor_raw = lic.get("valor_estimado")
    valor = 0.0
    if valor_raw not in (None, ""):
        try:
            s = str(valor_raw).replace("R$", "").replace(" ", "").strip()
            # formato brasileiro: 1.234,56 -> 1234.56
            if "," in s:
                s = s.replace(".", "").replace(",", ".")
            valor = float(s)
        except (TypeError, ValueError):
            valor = 0.0

    # Datas: preferir as colunas separadas do .xlsx (Abertura/Prazo); fallback ao blob 'datas' do HTML
    data_abertura = _conlic_iso_data(lic.get("data_abertura"))
    data_encerramento = _conlic_iso_data(lic.get("data_encerramento"))
    if not data_abertura and not data_encerramento:
        data_abertura, data_encerramento = _conlic_parse_datas(str(lic.get("datas") or ""))

    return {
        "numero_controle_pncp": "CONLIC-" + nc,
        "numero_edital": edital,
        "modalidade": modalidade,
        "orgao": str(lic.get("orgao") or "").strip(),
        "esfera": "Municipal",  # best-effort: o boletim ConLic é majoritariamente municipal
        "municipio": municipio,
        "uf": uf_raw,
        "objeto": objeto,
        "valor_estimado": valor,
        "data_abertura": data_abertura,
        "data_encerramento": data_encerramento,
        "link_pncp": "",
        "link_sistema_origem": "",
        "fonte": "CONLICITACAO",
        "origem_plataforma": _conlic_origem_plataforma(objeto),
        "_cnpj": "",
        "_ano_compra": "",
        "_seq_compra": "",
        "itens_encontrados": "",
    }


def _filtrar_pe_conlicitacao(editais: list[dict]) -> list[dict]:
    """Mantém SÓ Pregão Eletrônico (prefixo PE/) e passa pelo filtro de keyword/estado/valor
    já existente (concreto score-3, brita-só-RJ, veto asfalto/CBUQ, exclusão tubo/bloco/pré-moldado).

    É AQUI que mora o valor-add da 4ª fonte: o boletim ConLicitação é genérico; nós só
    queremos PE de concreto usinado / brita. Retorna apenas os sobreviventes."""
    so_pe = [ed for ed in editais if ed.get("modalidade") == "Pregão Eletrônico"]
    if not so_pe:
        return []
    return filtrar_por_keyword_estado_valor(so_pe)


def _parse_boletim_xlsx(caminho: str) -> list[dict]:
    """Lê um .xlsx de boletim do ConLicitação e devolve list[dict] de linhas brutas
    (chaves canônicas: objeto, edital, orgao, cidade, uf, datas, valor_estimado, nc, status).

    TOLERANTE: usa pandas+openpyxl, mapeia nomes de coluna via _COLMAP, e NUNCA quebra
    se faltar coluna (row.get -> ""). Lança ValueError se o arquivo claramente NÃO é um
    boletim (sem nenhuma das colunas-âncora objeto/edital/nc) — o caller usa isso para
    LOG ALTO de 'sessão/arquivo inválido' em vez de silêncio.
    """
    import pandas as pd  # import tardio: pandas já é dependência; evita custo no import do módulo
    df = pd.read_excel(caminho, dtype=str, engine="openpyxl")
    df = df.fillna("")
    linhas: list[dict] = []
    registros = df.to_dict(orient="records")

    # Detecção de arquivo inválido: nenhuma âncora reconhecível em NENHUMA linha.
    def _tem_ancora(row: dict) -> bool:
        return bool(_conlic_get(row, "objeto") or _conlic_get(row, "edital") or _conlic_get(row, "nc"))

    if registros and not any(_tem_ancora(r) for r in registros):
        raise ValueError(
            "nenhuma coluna-âncora (objeto/edital/nc) reconhecida — "
            "não parece um boletim ConLicitação (ver _COLMAP/TODO)"
        )

    for row in registros:
        linhas.append({
            "objeto": _conlic_get(row, "objeto"),
            "edital": _conlic_get(row, "edital"),
            "orgao": _conlic_get(row, "orgao"),
            "cidade": _conlic_get(row, "cidade"),
            "uf": _conlic_get(row, "uf"),
            # colunas de data separadas do .xlsx (datetime); 'datas' é fallback do HTML
            "data_abertura": _conlic_get(row, "data_abertura"),
            "data_encerramento": _conlic_get(row, "data_encerramento"),
            "datas": _conlic_get(row, "datas"),
            "valor_estimado": _conlic_get(row, "valor_estimado"),
            "nc": _conlic_get(row, "nc"),
            "status": _conlic_get(row, "status"),
        })
    return linhas


def _conlic_iso_data(v) -> str:
    """Normaliza uma data do .xlsx para ISO (YYYY-MM-DD). Aceita '2026-06-17 09:29:00',
    'dd/mm/aaaa' e 'Não informado'/vazio (-> ''). Nunca quebra."""
    s = str(v or "").strip()
    if not s or "informad" in s.lower():
        return ""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return m.group(0)
    m = re.match(r"(\d{2})/(\d{2})/(\d{2,4})", s)
    if m:
        d, mth, y = m.group(1), m.group(2), m.group(3)
        if len(y) == 2:
            y = "20" + y
        if 1 <= int(mth) <= 12 and 1 <= int(d) <= 31:
            return f"{y}-{mth}-{d}"
    return ""


def _carregar_conlic_lidos(sheet_id: str) -> set:
    """Carrega o set de 'CONLIC-'+nc já processados (aba 'ConLic Lidos').
    Falha silenciosa → set() (degrada sem quebrar se não houver Sheets)."""
    lidos: set = set()
    try:
        creds_path = os.environ.get("GOOGLE_SHEETS_CREDENTIALS_PATH", "credenciais/service_account.json")
        gc = gspread.service_account(filename=creds_path)
        ws = gc.open_by_key(sheet_id).worksheet(ABA_CONLIC)
        for r in ws.get_all_records():
            nc = str(r.get("numero_controle_pncp", "")).strip()
            if nc:
                lidos.add(nc)
    except Exception:
        pass
    return lidos


def _gravar_conlic_lidos(sheet_id: str, linhas: list[list]) -> None:
    """Grava (append batched) os 'CONLIC-'+nc recém-processados na aba 'ConLic Lidos'.
    Cria a aba se faltar. Falha silenciosa (apenas log)."""
    if not linhas:
        return
    header = ["numero_controle_pncp", "data_leitura", "arquivo"]
    try:
        creds_path = os.environ.get("GOOGLE_SHEETS_CREDENTIALS_PATH", "credenciais/service_account.json")
        gc = gspread.service_account(filename=creds_path)
        sh = gc.open_by_key(sheet_id)
        try:
            ws = sh.worksheet(ABA_CONLIC)
        except gspread.exceptions.WorksheetNotFound:
            ws = sh.add_worksheet(title=ABA_CONLIC, rows=2000, cols=len(header))
            ws.append_row(header, value_input_option="USER_ENTERED")
        vals = ws.get_all_values()
        if not vals:
            ws.append_row(header, value_input_option="USER_ENTERED")
        ws.append_rows(linhas, value_input_option="USER_ENTERED")
    except Exception as exc:
        logging.warning("Não foi possível gravar 'ConLic Lidos': %s", exc)


# Estado de coleta exportado para main() decidir o status 'alerta'
# (arquivo novo presente mas parse=0 não pode virar '0 silencioso').
_CONLIC_ALERTA = {"arquivo_novo_sem_parse": False}


def _coletar_conlicitacao(sheet_id: str = "") -> list[dict]:
    """Coleta a 4ª fonte: varre os .xlsx NOVOS da pasta CONLICITACAO_XLSX_DIR,
    parseia, normaliza, FILTRA (só PE de concreto usinado/brita) e retorna.

    Idempotência: a aba 'ConLic Lidos' guarda os 'CONLIC-'+nc já gravados; itens já
    vistos são pulados. Falha silenciosa estilo _coletar_bll (try/except -> [] em erro).
    DETECÇÃO de arquivo inválido: parse que falha como boletim gera LOG ALTO (não silêncio).
    ALARME: arquivo novo presente mas parse=0 marca _CONLIC_ALERTA p/ status 'alerta'.
    """
    _CONLIC_ALERTA["arquivo_novo_sem_parse"] = False
    if not CONLICITACAO_ATIVO or not CONLICITACAO_XLSX_DIR:
        return []
    try:
        pasta = Path(CONLICITACAO_XLSX_DIR)
        if not pasta.is_dir():
            logging.warning("ConLicitação: CONLICITACAO_XLSX_DIR=%r não é uma pasta válida — pulando.",
                            CONLICITACAO_XLSX_DIR)
            return []

        arquivos = sorted(
            [p for p in pasta.glob("*.xlsx") if not p.name.startswith("~$")]
        )
        if not arquivos:
            return []

        lidos = _carregar_conlic_lidos(sheet_id) if sheet_id else set()
        coletados: list[dict] = []
        viu_arquivo_com_linhas = False
        novos_lidos: list[list] = []
        ts = datetime.now().isoformat(timespec="seconds")

        for caminho in arquivos:
            try:
                linhas = _parse_boletim_xlsx(str(caminho))
            except ValueError as exc:
                # arquivo presente mas NÃO é um boletim válido → LOG ALTO (não silencioso)
                logging.error("ConLicitação: arquivo %s NÃO parseou como boletim (sessão/arquivo inválido?): %s",
                              caminho.name, exc)
                continue
            except Exception as exc:
                logging.warning("ConLicitação: falha ao ler %s: %s — pulando arquivo.", caminho.name, exc)
                continue

            if linhas:
                viu_arquivo_com_linhas = True

            for lic in linhas:
                ed = _normalizar_conlicitacao(lic)
                if ed is None:
                    continue
                if ed["numero_controle_pncp"] in lidos:
                    continue  # idempotência: já gravado numa rodada anterior
                lidos.add(ed["numero_controle_pncp"])
                coletados.append(ed)

        # FILTRO (valor-add): só PE de concreto usinado / brita
        filtrados = _filtrar_pe_conlicitacao(coletados)

        # Idempotência: marca como lidos os que SOBREVIVERAM ao filtro (entram no funil).
        for ed in filtrados:
            novos_lidos.append([ed["numero_controle_pncp"], ts, "boletim"])
        if sheet_id and novos_lidos:
            _gravar_conlic_lidos(sheet_id, novos_lidos)

        # ALARME: havia arquivo novo COM linhas, mas nada sobreviveu → não é '0 silencioso'.
        # (0 após filtro pode ser legítimo — boletim sem concreto/brita —, mas se NEM o
        # parse bruto produziu candidatos, isso é suspeito de layout/coluna errados.)
        if viu_arquivo_com_linhas and not coletados:
            _CONLIC_ALERTA["arquivo_novo_sem_parse"] = True
            logging.warning("ConLicitação: arquivo(s) novo(s) presentes mas 0 licitações normalizadas "
                            "(layout/coluna do .xlsx pode ter mudado — ver _COLMAP/TODO).")

        if filtrados:
            logging.info("ConLicitação: %d licitações (PE concreto/brita) de %d arquivo(s) de boletim.",
                         len(filtrados), len(arquivos))
        return filtrados
    except Exception as exc:
        logging.warning("ConLicitação: erro inesperado na coleta (%s) — pulando fonte.", exc)
        return []


def _coletar_conlicitacao_inbox(sheet_id: str = "") -> list[dict]:
    """Coleta ConLicitação pelo caminho AUTOMÁTICO: a aba 'ConLic Inbox', onde o
    bookmarklet (via Apps Script) faz appendRow de cada licitação do boletim.

    Espelha _coletar_conlicitacao: lê as linhas com processado_em vazio, normaliza,
    aplica idempotência ('CONLIC-'+nc na aba 'ConLic Lidos') e FILTRA (só PE de
    concreto usinado/brita). Marca as linhas consumidas com timestamp em processado_em.
    Falha silenciosa estilo do arquivo (try/except -> [] em erro).
    ALARME: havia linha nova mas 0 normalizadas -> _CONLIC_ALERTA p/ status 'alerta'.
    """
    if not sheet_id:
        return []
    try:
        creds_path = os.environ.get("GOOGLE_SHEETS_CREDENTIALS_PATH", "credenciais/service_account.json")
        gc = gspread.service_account(filename=creds_path)
        ws = gc.open_by_key(sheet_id).worksheet(ABA_CONLIC_INBOX)
        registros = ws.get_all_records()
    except Exception:
        return []  # aba inexistente / qualquer erro -> falha silenciosa

    if not registros:
        return []

    campos = ("objeto", "nc", "cidade", "uf", "edital", "valor_estimado",
              "orgao", "datas", "data_abertura", "data_encerramento", "status")

    lidos = _carregar_conlic_lidos(sheet_id)
    coletados: list[dict] = []
    viu_linha_nova = False
    linhas_processadas: list[int] = []   # nº da linha na planilha (1-based, header = linha 1)

    for i, row in enumerate(registros, start=2):  # linha 1 = header
        if str(row.get("processado_em", "")).strip():
            continue  # já consumida numa rodada anterior
        viu_linha_nova = True
        linhas_processadas.append(i)
        lic = {c: row.get(c, "") for c in campos}
        ed = _normalizar_conlicitacao(lic)
        if ed is None:
            continue
        if ed["numero_controle_pncp"] in lidos:
            continue  # idempotência: já no funil
        lidos.add(ed["numero_controle_pncp"])
        coletados.append(ed)

    # FILTRO (valor-add): só PE de concreto usinado / brita
    filtrados = _filtrar_pe_conlicitacao(coletados)

    # Idempotência: marca como lidos os que SOBREVIVERAM ao filtro (entram no funil).
    ts = datetime.now().isoformat(timespec="seconds")
    novos_lidos = [[ed["numero_controle_pncp"], ts, "inbox"] for ed in filtrados]
    if novos_lidos:
        _gravar_conlic_lidos(sheet_id, novos_lidos)

    # Marca processado_em nas linhas consumidas (idempotência por 'ConLic Lidos' já
    # protege o funil; se a marcação falhar, apenas loga).
    if linhas_processadas:
        try:
            header = ws.row_values(1)
            col = header.index("processado_em") + 1  # 1-based
            for linha in linhas_processadas:
                ws.update_cell(linha, col, ts)
        except Exception as exc:
            logging.warning("ConLic Inbox: não foi possível marcar processado_em: %s", exc)

    # ALARME: linha(s) nova(s) presente(s) mas 0 normalizadas -> não é '0 silencioso'.
    if viu_linha_nova and not coletados:
        _CONLIC_ALERTA["arquivo_novo_sem_parse"] = True
        logging.warning("ConLic Inbox: linha(s) nova(s) na aba mas 0 licitações normalizadas "
                        "(contrato/colunas do appendRow podem ter mudado).")

    if filtrados:
        logging.info("ConLic Inbox: %d licitações (PE concreto/brita) de %d linha(s) nova(s).",
                     len(filtrados), len(linhas_processadas))
    return filtrados


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

# Marcador gravado em erro_msg quando a rodada foi um BACKFILL manual
# (override PNCP_DATA_INICIAL/FINAL). _ler_watermark ignora essas linhas para
# que um backfill histórico nunca avance o watermark até a data de hoje.
_BACKFILL_TAG = "backfill manual (override) — nao usar como watermark"

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


def _ler_watermark(sheet_id: str) -> date | None:
    """Deriva o WATERMARK de cobertura a partir da própria aba 'Execucoes'.

    Watermark = data até a qual a cobertura já foi CONFIRMADA íntegra. Custo
    zero: reusa a aba/header/cliente gspread que já existem — só LÊ, nunca
    escreve (a escrita acontece de graça no append de _gravar_execucao_sheets).

    Regra: pega o MAIOR `data_execucao` entre as linhas cuja coleta foi
    comprovadamente íntegra — status == "ok" E int(brutos) > 0. Linhas
    "alerta"/"erro" (vazias, parciais, com exceção) são ignoradas, então um
    gap se auto-cura: o watermark recua sozinho até o último sucesso real e a
    janela da próxima rodada cobre os dias perdidos.

    Retorna a DATA do último sucesso, ou None se não houver watermark ainda
    (primeira vez / aba inexistente / leitura falhou) — nesse caso o chamador
    cai no comportamento atual (JANELA_DIAS). Falha silenciosa.
    """
    try:
        creds_path = os.environ.get("GOOGLE_SHEETS_CREDENTIALS_PATH", "credenciais/service_account.json")
        gc = gspread.service_account(filename=creds_path)
        sh = gc.open_by_key(sheet_id)
        try:
            ws = sh.worksheet("Execucoes")
        except gspread.exceptions.WorksheetNotFound:
            return None  # primeira vez: aba ainda não existe
        registros = ws.get_all_records()  # dicts por header _EXECUCOES_HEADER
        melhor: date | None = None
        for reg in registros:
            if str(reg.get("status", "")).strip().lower() != "ok":
                continue
            # Ignora rodadas de BACKFILL manual: a data_execucao é HOJE, mas a janela
            # coberta é histórica — usá-la como watermark criaria um gap. (invariante 4)
            if str(reg.get("erro_msg", "")).strip() == _BACKFILL_TAG:
                continue
            try:
                if int(str(reg.get("brutos", "0")).strip() or "0") <= 0:
                    continue
            except (ValueError, TypeError):
                continue
            bruto_data = str(reg.get("data_execucao", "")).strip()
            if not bruto_data:
                continue
            try:
                # data_execucao é ISO datetime ("2026-06-12T08:30:00"); pegamos só a DATA
                d = datetime.fromisoformat(bruto_data).date()
            except ValueError:
                try:
                    d = date.fromisoformat(bruto_data[:10])
                except ValueError:
                    continue
            if melhor is None or d > melhor:
                melhor = d
        return melhor
    except Exception as exc:
        logging.warning("Não foi possível ler o watermark de 'Execucoes' (caindo em JANELA_DIAS): %s", exc)
        return None


# =========================================================================
# PORTÃO DE IA — obra genérica (score=1) só entra se a Gemini confirmar
# concreto usinado ou brita/pedras. Custo zero (flash-only). Cache em "Triagem IA".
# =========================================================================
# Exclui pagos (pro/ultra/exp/thinking) e modelos não-texto (tts/audio/image/...)
# que só desperdiçariam tentativas/cota na triagem.
_PAGOS_IA = ("pro", "ultra", "exp", "thinking", "vision", "tts",
             "audio", "image", "live", "embedding", "preview")


def _eh_modelo_gratuito(nome: str) -> bool:
    n = (nome or "").lower()
    return "flash" in n and not any(p in n for p in _PAGOS_IA)


def _gemini_client_scraper():
    """Cliente Gemini para o scraper (só env GEMINI_API_KEY; sem Streamlit)."""
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        return None
    try:
        from google import genai
        return genai.Client(api_key=key)
    except Exception as exc:
        logging.warning("Gemini indisponível (import/cliente): %s", exc)
        return None


def _modelos_gemini_gratuitos(client) -> list[str]:
    """Lista de modelos 'flash' gratuitos (descoberta dinâmica + candidatos conhecidos)."""
    modelos: list[str] = []
    try:
        for _m in client.models.list():
            _nome = (getattr(_m, "name", "") or "").split("/")[-1]
            _acts = getattr(_m, "supported_actions", None) or []
            if _nome and _eh_modelo_gratuito(_nome) and ("generateContent" in _acts or not _acts):
                modelos.append(_nome)
    except Exception:
        pass
    for _c in ["gemini-flash-lite-latest", "gemini-2.0-flash-lite", "gemini-2.5-flash",
               "gemini-2.0-flash", "gemini-flash-latest"]:
        if _c not in modelos and _eh_modelo_gratuito(_c):
            modelos.append(_c)
    # Prioriza modelos "lite" (limites grátis maiores / menos disputados → menos 429).
    modelos.sort(key=lambda m: 0 if "lite" in m else 1)
    return modelos


def _baixar_texto_edital_scraper(num_controle: str, link_pdf: str, link_pncp: str) -> str:
    """Obtém o TEXTO do edital (PDF real via API de arquivos do PNCP → pdfplumber;
    fallback link_sistema_origem e página HTML). Retorna "" se nada utilizável."""
    import io, re
    import pdfplumber

    HDRS = {"User-Agent": "Mozilla/5.0 (compatible; ConcrelagosBot/1.0)"}
    urls: list[str] = []
    try:
        nc = (num_controle or "").strip()
        if "/" in nc and "-" in nc:
            esquerda, ano = nc.split("/", 1)
            partes = esquerda.split("-")
            cnpj, seq = partes[0], partes[-1]
            seq_int = str(int(seq))
            api = (f"https://pncp.gov.br/api/pncp/v1/orgaos/{cnpj}"
                   f"/compras/{ano}/{seq_int}/arquivos")
            r = requests.get(api, timeout=30, headers=HDRS)
            if r.status_code == 200:
                for arq in (r.json() or []):
                    u = arq.get("url") or arq.get("uri") or arq.get("link")
                    if u:
                        urls.append(u)
    except Exception:
        pass
    if link_pdf:
        urls.append(link_pdf)
    if link_pncp:
        urls.append(link_pncp)

    for u in urls:
        try:
            resp = requests.get(u, timeout=30, allow_redirects=True, headers=HDRS)
            resp.raise_for_status()
            ct = resp.headers.get("content-type", "").lower()
            eh_pdf = ("pdf" in ct) or resp.content[:5].startswith(b"%PDF") or u.lower().endswith(".pdf")
            if eh_pdf:
                with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
                    txt = "\n".join((p.extract_text() or "") for p in pdf.pages[:20]).strip()
                if len(txt) >= IA_GATE_MIN_CHARS:
                    return txt
            else:
                html = resp.text
                html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
                txt = re.sub(r"<[^>]+>", " ", html)
                txt = re.sub(r"\s+", " ", txt).strip()
                if len(txt) >= 200:
                    return txt[:12000]
        except Exception:
            continue
    return ""


_PROMPT_TRIAGEM = (
    "Você é um analista de licitações da Concrelagos, que vende SOMENTE dois produtos:\n"
    "(1) CONCRETO USINADO (concreto fresco dosado em central, entregue por betoneira) e\n"
    "(2) BRITA / PEDRAS britadas (agregado graúdo de pedreira: brita, pedrisco, pó de pedra, rachão, cascalho).\n"
    "Responda se o edital envolve COMPRA ou FORNECIMENTO de um desses dois produtos.\n"
    "NÃO é relevante: asfalto/CBUQ/massa asfáltica, cimento ensacado, artefatos de cimento "
    "(tubos, postes, blocos, manilhas, meio-fio, piso intertravado, pré-moldados), nem obra "
    "genérica sem fornecimento explícito de concreto usinado ou brita.\n"
    "Responda APENAS com JSON válido, sem markdown:\n"
    '{"relevante": true|false, "material": "concreto"|"brita"|null, '
    '"produto": "string curta ou vazio", "confianca": 0-100, "justificativa": "1 frase"}\n\n'
    "Edital (primeiros 10000 caracteres):\n"
)


def _triar_edital_ia(ed: dict, client, modelos: list[str]) -> dict | None:
    """Pergunta à Gemini se a obra genérica tem concreto usinado / brita.
    Retorna dict {relevante, material, produto, confianca, justificativa} OU
    None quando NÃO deu pra concluir (sem texto / 429-404 / JSON inválido) = pendente.
    """
    import json
    try:
        texto = _baixar_texto_edital_scraper(
            ed.get("numero_controle_pncp", ""),
            ed.get("link_sistema_origem", ""),
            ed.get("link_pncp", ""),
        )
        if not texto or len(texto) < IA_GATE_MIN_CHARS:
            return None  # PDF escaneado/indisponível → pendente

        prompt = _PROMPT_TRIAGEM + texto[:10000]
        response = None
        for _modelo in list(modelos):
            try:
                response = client.models.generate_content(model=_modelo, contents=prompt)
                # Sticky: o modelo que funcionou vai pro topo p/ as próximas chamadas
                # (evita repetir os 429 dos modelos sem cota a cada edital).
                if modelos and modelos[0] != _modelo and _modelo in modelos:
                    modelos.remove(_modelo); modelos.insert(0, _modelo)
                break
            except Exception as _exc:
                s = str(_exc)
                if any(t in s for t in ("429", "RESOURCE_EXHAUSTED", "404", "NOT_FOUND", "not found", "not supported")):
                    continue
                logging.warning("Gemini erro inesperado (%s): %s", _modelo, s[:160])
                continue
        if response is None:
            return None  # nenhum modelo respondeu (cota/erro) → pendente

        raw = (getattr(response, "text", "") or "").strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.rstrip("`").strip()
        dados = json.loads(raw)

        material = dados.get("material")
        if material not in ("concreto", "brita"):
            material = None
        relevante = bool(dados.get("relevante"))
        # Sem material nomeado (IA não confirmou concreto/brita) → NÃO promove:
        # evita gravar obra genérica como "relevante" sem produto real definido.
        if relevante and material is None:
            relevante = False
        # Guarda de brita: brita só vale no RJ (mesmo que a IA diga brita em outro estado).
        if material == "brita" and ed.get("uf") not in ESTADOS_BRITA:
            relevante = False
        try:
            conf = max(0, min(100, int(dados.get("confianca") or 0)))
        except (TypeError, ValueError):
            conf = 0
        return {
            "relevante": relevante,
            "material": material,
            "produto": str(dados.get("produto") or "").strip()[:120],
            "confianca": conf,
            "justificativa": str(dados.get("justificativa") or "").strip()[:200],
        }
    except json.JSONDecodeError:
        return None
    except Exception as exc:
        logging.warning("Triagem IA falhou (%s): %s", ed.get("numero_controle_pncp", "?"), exc)
        return None


_TRIAGEM_HEADER = [
    "numero_controle_pncp", "data_triagem", "relevante",
    "material", "produto", "confianca", "justificativa", "status",
]


def _carregar_triagem(sheet_id: str) -> dict:
    """Carrega o cache de decisões da IA (aba 'Triagem IA'). Falha silenciosa → {}.
    Mantém a ÚLTIMA linha por numero_controle (um 'verificado' sobrepõe um 'pendente')."""
    cache: dict = {}
    try:
        creds_path = os.environ.get("GOOGLE_SHEETS_CREDENTIALS_PATH", "credenciais/service_account.json")
        gc = gspread.service_account(filename=creds_path)
        ws = gc.open_by_key(sheet_id).worksheet(ABA_TRIAGEM)
        for r in ws.get_all_records():
            nc = str(r.get("numero_controle_pncp", "")).strip()
            if nc:
                cache[nc] = r
    except Exception:
        pass
    return cache


def _gravar_triagem(sheet_id: str, linhas: list[list]) -> None:
    """Grava (append) as decisões novas da IA na aba 'Triagem IA' (1 chamada batched)."""
    if not linhas:
        return
    try:
        creds_path = os.environ.get("GOOGLE_SHEETS_CREDENTIALS_PATH", "credenciais/service_account.json")
        gc = gspread.service_account(filename=creds_path)
        sh = gc.open_by_key(sheet_id)
        try:
            ws = sh.worksheet(ABA_TRIAGEM)
        except gspread.exceptions.WorksheetNotFound:
            ws = sh.add_worksheet(title=ABA_TRIAGEM, rows=2000, cols=len(_TRIAGEM_HEADER))
            ws.append_row(_TRIAGEM_HEADER, value_input_option="USER_ENTERED")
        vals = ws.get_all_values()
        if not vals or vals[0] != _TRIAGEM_HEADER:
            if not vals:
                ws.append_row(_TRIAGEM_HEADER, value_input_option="USER_ENTERED")
        ws.append_rows(linhas, value_input_option="USER_ENTERED")
    except Exception as exc:
        logging.warning("Não foi possível gravar Triagem IA: %s", exc)


def _promover_da_ia(ed: dict, material, produto, justificativa, confianca) -> None:
    """Promove uma obra genérica confirmada pela IA: score 1 → 2 + campos ia_*."""
    ed["score"] = 2
    ed["score_label"] = SCORE_LABEL[2]
    if material in ("concreto", "brita"):
        ed["material"] = material
    ed["ia_verificado"] = True
    ed["ia_produto"] = produto or ""
    ed["ia_justificativa"] = justificativa or ""
    ed["ia_confianca"] = confianca if confianca != "" else ""


def aplicar_gate_ia(qualificados: list[dict], sheet_id: str) -> list[dict]:
    """PORTÃO DE IA. Obras genéricas (score=1) só passam se a Gemini confirmar
    concreto usinado / brita. score>=2 (keyword) sempre bypassa. Rodado APÓS a geo,
    então a IA só lê editais geograficamente relevantes. Regra: POSSÍVEL não
    confirmada NUNCA é gravada; pendentes ficam no cache para re-tentar depois."""
    if not IA_GATE_ATIVA:
        return qualificados

    score1 = [ed for ed in qualificados if int(ed.get("score") or 0) == 1]
    resto = [ed for ed in qualificados if int(ed.get("score") or 0) != 1]
    if not score1:
        return qualificados

    cache = _carregar_triagem(sheet_id)
    ts = datetime.now().isoformat(timespec="seconds")
    novas_linhas: list[list] = []
    promovidos: list[dict] = []
    cache_hit = chamadas = negados = pendentes = 0

    client = _gemini_client_scraper()
    if client is None:
        logging.warning("IA-GATE: GEMINI_API_KEY ausente — %d obra(s) genérica(s) NÃO gravadas "
                        "(pendentes p/ próxima execução com a chave).", len(score1))
        for ed in score1:
            nc = ed.get("numero_controle_pncp", "")
            if nc and nc not in cache:
                novas_linhas.append([nc, ts, "", "", "", "", "", "pendente"])
        _gravar_triagem(sheet_id, novas_linhas)
        return resto

    # 1) resolve pelo cache; só não-cacheados (ou 'pendente') consomem Gemini
    a_triar: list[dict] = []
    for ed in score1:
        nc = ed.get("numero_controle_pncp", "")
        c = cache.get(nc)
        if c and str(c.get("status")) == "verificado":
            _promover_da_ia(ed, c.get("material"), c.get("produto"),
                            c.get("justificativa"), c.get("confianca", ""))
            promovidos.append(ed); cache_hit += 1
        elif c and str(c.get("status")) == "negado":
            cache_hit += 1  # descarta (não entra)
        else:
            a_triar.append(ed)

    # 2) ordena por valor desc e aplica teto IA_GATE_MAX (excedente vira pendente)
    a_triar.sort(key=lambda e: float(e.get("valor_estimado") or 0), reverse=True)
    modelos = _modelos_gemini_gratuitos(client)
    for i, ed in enumerate(a_triar):
        nc = ed.get("numero_controle_pncp", "")
        if i >= IA_GATE_MAX:
            novas_linhas.append([nc, ts, "", "", "", "", "", "pendente"]); pendentes += 1
            continue
        v = _triar_edital_ia(ed, client, modelos)
        chamadas += 1
        time.sleep(IA_GATE_PAUSA_S)   # ritma p/ ficar abaixo do limite por minuto (free tier)
        if v is None:
            novas_linhas.append([nc, ts, "", "", "", "", "", "pendente"]); pendentes += 1
        elif v["relevante"]:
            _promover_da_ia(ed, v["material"], v["produto"], v["justificativa"], v["confianca"])
            promovidos.append(ed)
            novas_linhas.append([nc, ts, True, v["material"] or "", v["produto"],
                                 v["confianca"], v["justificativa"], "verificado"])
        else:
            novas_linhas.append([nc, ts, False, v["material"] or "", v["produto"],
                                 v["confianca"], v["justificativa"], "negado"]); negados += 1

    _gravar_triagem(sheet_id, novas_linhas)
    logging.info("IA-GATE: score1=%d -> cache_hit=%d, chamadas=%d, promovidos=%d, negados=%d, pendentes=%d",
                 len(score1), cache_hit, chamadas, len(promovidos), negados, pendentes)
    return resto + promovidos


# =========================================================================
# ORQUESTRADOR
# =========================================================================
def main() -> None:
    _configurar_logging()
    load_dotenv()
    _validar_env()

    sheet_id = os.environ["GOOGLE_SHEETS_ID"]

    # Janela de coleta. Três modos, nesta ordem de precedência:
    #   (a) OVERRIDE MANUAL — PNCP_DATA_INICIAL/PNCP_DATA_FINAL (backfill via
    #       workflow_dispatch). Vence tudo; o watermark é ignorado e NÃO é
    #       avançado por um backfill manual (override_manual=True desliga o avanço).
    #   (b) WATERMARK DE COBERTURA — janela = [watermark - overlap, hoje], com
    #       PISO=JANELA_DIAS e CAP=PNCP_JANELA_MAX_DIAS. Cada rodada cobre desde
    #       o último sucesso confirmado, então rodada vazia/falha não perde edital.
    #   (c) PISO PURO (primeira vez, sem watermark) — comportamento atual: [hoje-JANELA_DIAS, hoje].
    di_str = os.getenv("PNCP_DATA_INICIAL", "").strip()
    df_str = os.getenv("PNCP_DATA_FINAL", "").strip()
    janela_piso = int(os.getenv("JANELA_DIAS", "1"))          # PISO: janela mínima
    janela_max = int(os.getenv("PNCP_JANELA_MAX_DIAS", "30")) # CAP: janela máxima
    overlap_dias = 1                                          # sobreposição de segurança
    override_manual = bool(di_str and df_str)

    if override_manual:
        # (a) OVERRIDE MANUAL — vence o watermark; não avança o watermark depois.
        data_inicial = date.fromisoformat(di_str)
        data_final = date.fromisoformat(df_str)
        logging.info("Janela FIXA (override manual via .env): %s a %s. Watermark IGNORADO e NÃO será avançado.",
                     data_inicial, data_final)
    else:
        hoje = date.today()
        data_final = hoje
        piso_inicial = hoje - timedelta(days=janela_piso)      # janela mínima (PISO)
        cap_inicial = hoje - timedelta(days=janela_max)        # janela máxima (CAP)
        watermark = _ler_watermark(sheet_id)
        if watermark is None:
            # (c) PISO PURO — primeira vez / sem watermark: comportamento atual.
            data_inicial = piso_inicial
            logging.info("Janela relativa SEM watermark (primeira vez) — fonte=PISO (JANELA_DIAS=%d): %s a %s.",
                         janela_piso, data_inicial, data_final)
        else:
            # (b) WATERMARK — começa 1 dia antes do último sucesso confirmado (overlap).
            base = watermark - timedelta(days=overlap_dias)
            # PISO: nunca menor que JANELA_DIAS (se watermark for muito recente).
            data_inicial = min(base, piso_inicial)
            fonte = "watermark" if data_inicial == base else "piso"
            # CAP: nunca maior que PNCP_JANELA_MAX_DIAS — se o buraco for maior,
            # cobre só o cap; o resto fica para a próxima rodada (watermark não avança
            # além do que foi de fato coletado, então o buraco persiste e se auto-cura).
            if data_inicial < cap_inicial:
                logging.warning(
                    "Janela de cobertura (%s a %s, %d dias) excede CAP de %d dias "
                    "(buraco desde watermark=%s maior que o teto). Cobrindo só o CAP; "
                    "o restante fica para a próxima rodada.",
                    data_inicial, data_final, (data_final - data_inicial).days,
                    janela_max, watermark,
                )
                data_inicial = cap_inicial
                fonte = "cap"
            logging.info(
                "Janela de COBERTURA — fonte=%s | watermark=%s overlap=%dd piso=%dd cap=%dd | janela efetiva: %s a %s (%d dias).",
                fonte, watermark, overlap_dias, janela_piso, janela_max,
                data_inicial, data_final, (data_final - data_inicial).days,
            )

    filiais = carregar_filiais(sheet_id)

    t0 = time.time()
    erro_execucao = ""
    novos: list[dict] = []
    qualificados: list[dict] = []
    pre: list[dict] = []
    brutos: list[dict] = []
    pncp_integra = False  # só vira True se a coleta PNCP foi íntegra (todas as modalidades responderam)
    try:
        # ---- PNCP (fonte primária) ----
        brutos_pncp, pncp_integra = buscar_editais_pncp(data_inicial, data_final)

        # ---- ComprasNet (fonte secundária: federal/estadual pré-migração) ----
        brutos_cn = _coletar_comprasnet(data_inicial, data_final)

        # ---- BLL (fonte terciária: municípios SP/MG/PR/RJ) ----
        brutos_bll = _coletar_bll(data_inicial, data_final)

        # ---- ConLicitação (4ª fonte: boletim .xlsx; já FILTRADA p/ PE concreto/brita) ----
        brutos_conlic = _coletar_conlicitacao(sheet_id)
        # ---- ConLic Inbox (mesma 4ª fonte, caminho automático via Apps Script) ----
        brutos_conlic_inbox = _coletar_conlicitacao_inbox(sheet_id)

        # Consolida e deduplica — PNCP tem precedência (vem 1º no concat; ConLic por último).
        brutos = _deduplicar_fontes(brutos_pncp + brutos_cn + brutos_bll + brutos_conlic + brutos_conlic_inbox)
        logging.info(
            "Multi-fonte: PNCP=%d + ComprasNet=%d + BLL=%d + ConLic=%d + ConLicInbox=%d -> deduplicado=%d editais brutos",
            len(brutos_pncp), len(brutos_cn), len(brutos_bll), len(brutos_conlic), len(brutos_conlic_inbox), len(brutos),
        )

        pre = filtrar_por_keyword_estado_valor(brutos)
        pre = enriquecer_com_itens(pre)   # promove score=1 → score=2 via endpoint de itens
        qualificados = qualificar_por_distancia(pre, filiais)
        # PORTÃO DE IA: obra genérica (score=1) só passa se a Gemini confirmar concreto/brita.
        qualificados = aplicar_gate_ia(qualificados, sheet_id)
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

    # Decide o status da execução. ESTE status é a fonte do WATERMARK:
    # _ler_watermark() só considera linhas com status=="ok" E brutos>0. Logo,
    # rebaixar uma coleta PARCIAL para "alerta" aqui é exatamente o que impede o
    # watermark de avançar sobre ela (invariante de GUARDA DE FALHA PARCIAL).
    #
    # Integridade da coleta (sinal de NÃO avançar o watermark), em ordem:
    #   - exceção na execução  -> "erro"
    #   - 0 brutos no total     -> "alerta" (coleta vazia / throttling)
    #   - PNCP parcial          -> "alerta" (alguma modalidade não respondeu)
    #   - backfill manual        -> nunca avança o watermark (status "ok" não é base
    #                               de janela relativa; e _ler_watermark usa só a DATA,
    #                               que num backfill é a de HOJE — porém a guarda real
    #                               é não confiar em data de edital; ainda assim, por
    #                               segurança explícita, marcamos integra=False abaixo).
    coleta_integra = pncp_integra and not override_manual
    if not erro_execucao and len(brutos) == 0:
        erro_execucao = "coleta vazia (0 brutos) — possivel throttling/instabilidade das APIs"
        status_exec = "alerta"
    elif not erro_execucao and not coleta_integra:
        # brutos>0 mas coleta PARCIAL: alguma modalidade PNCP falhou (timeout/throttling)
        # OU é um backfill manual. Em ambos os casos NÃO deixamos virar base de watermark.
        if override_manual:
            status_exec = "ok"  # backfill é coleta válida; mas watermark não usa override
            # Marca a linha como backfill para que _ler_watermark a IGNORE: data_execucao
            # de um backfill é HOJE, mas a janela coberta é histórica — se virasse
            # watermark, criaria um gap entre a janela do backfill e hoje. (invariante 4)
            erro_execucao = _BACKFILL_TAG
            logging.info("Backfill manual concluído — status 'ok', marcado como backfill; o watermark NÃO é avançado por backfill.")
        else:
            erro_execucao = "coleta PARCIAL — alguma modalidade PNCP nao respondeu (timeout/throttling); watermark nao avanca"
            status_exec = "alerta"
            logging.warning("Coleta PARCIAL gravada como 'alerta' — watermark NÃO avança; próxima rodada recupera o buraco.")
    else:
        status_exec = "erro" if erro_execucao else "ok"

    # ALARME ConLicitação: arquivo novo de boletim presente, mas 0 licitações
    # normalizadas (layout/coluna do .xlsx mudou) → rebaixa 'ok' para 'alerta'.
    # NÃO mexe em coleta 'erro'/'alerta' já decidida e NÃO segura o watermark do PNCP
    # (coerente com BLL/ComprasNet: a 4ª fonte é auxiliar). É só um sinal de saúde.
    if status_exec == "ok" and _CONLIC_ALERTA.get("arquivo_novo_sem_parse"):
        status_exec = "alerta"
        if not erro_execucao:
            erro_execucao = ("ConLicitação: boletim .xlsx novo presente mas 0 licitações "
                             "parseadas (layout/coluna pode ter mudado — ver _COLMAP/TODO)")
        logging.warning("Status rebaixado para 'alerta' por ConLicitação (boletim novo sem parse).")

    # Grava execução na aba "Execucoes" do Sheets (auditoria permanente)
    _gravar_execucao_sheets(sheet_id, {
        "data_execucao": datetime.now().isoformat(timespec="seconds"),
        "status": status_exec,
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
