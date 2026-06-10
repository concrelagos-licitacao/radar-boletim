"""
app.py — Concrelagos Intelligence Hub (Fase 4)
Dashboard executivo de licitações públicas (PNCP) para diretoria.

Estrutura visual:
- Header corporativo com logo/nome + indicador de última atualização
- Login obrigatório (single-user, senha em st.secrets)
- Sidebar com filtros globais (UF, material, valor, distância, data)
- Abas principais:
    1. Visão Geral — KPIs em cards, ranking de oportunidades, gráficos
    2. Mapa — mapa interativo com usinas (azul), pedreiras (verde) e obras (vermelho)
    3. Editais — tabela paginada das licitações qualificadas
    4. Filiais — cards organizados por estado/tipo
    5. Diário — auditoria das execuções do scraper

Fonte de dados: Google Sheets ("Concrelagos Hub") abas "Filiais" e "Novas Licitações".
Cache de 5min via @st.cache_data para evitar bater na API a cada interação.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pydeck as pdk
import streamlit as st

# ===== Config inicial =====
_ICON = Path(__file__).resolve().parent / "assets" / "logo.png"
st.set_page_config(
    page_title="Concrelagos Intelligence Hub",
    page_icon=str(_ICON) if _ICON.exists() else None,
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Proteções anti-crawler: pede a Google/Bing pra NÃO indexar (mitigação extra
# além da senha. Streamlit Cloud não permite robots.txt custom, então usamos
# meta tags via st.markdown).
st.markdown(
    """
    <meta name="robots" content="noindex,nofollow,noarchive,nosnippet">
    <meta name="googlebot" content="noindex,nofollow,noarchive,nosnippet">
    <meta name="bingbot" content="noindex,nofollow,noarchive,nosnippet">
    """,
    unsafe_allow_html=True,
)

# Tenta carregar .env localmente; em produção usa st.secrets
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

ROOT = Path(__file__).resolve().parent
LOGO_PATH = ROOT / "assets" / "logo.png"

# ===== Styling corporativo =====
st.markdown(
    """
    <style>
    :root {
        --cl-primary: #3A4149;   /* grafite do logo Concrelagos */
        --cl-header:  #2E353D;    /* faixa escura (cabeçalho/login) */
        --cl-accent:  #C28E2C;    /* dourado/ocre do sol */
        --cl-accent-d:#A9781F;    /* dourado escuro (hover/valor) */
        --cl-bg:      #F7F8FA;
        --cl-card:    #FFFFFF;
        --cl-text:    #1F2937;
        --cl-muted:   #6B7280;
        --cl-success: #16A34A;
        --cl-danger:  #DC2626;
    }
    .main { background-color: var(--cl-bg); }
    .stApp header { background-color: var(--cl-header); }
    .stApp header * { color: white !important; }
    .block-container { padding-top: 1.2rem; padding-bottom: 2rem; }
    /* Títulos serifados (identidade Portfólio) */
    h1, h2, h3 { color: var(--cl-primary); font-weight: 700;
        font-family: Georgia, 'Times New Roman', serif; }
    .cl-serif { font-family: Georgia, 'Times New Roman', serif; }
    .cl-card {
        background: var(--cl-card);
        border: 1px solid #E5E7EB;
        border-left: 4px solid var(--cl-accent);
        border-radius: 8px;
        padding: 1.1rem 1.25rem;
        margin-bottom: 0.75rem;
        box-shadow: 0 1px 2px rgba(0,0,0,0.04);
    }
    .cl-card-title {
        font-size: 0.78rem;
        text-transform: uppercase;
        color: var(--cl-muted);
        margin-bottom: 0.3rem;
        letter-spacing: 0.04em;
    }
    .cl-card-value {
        font-size: 1.7rem;
        font-weight: 700;
        color: var(--cl-accent-d);
        line-height: 1.1;
        font-family: Georgia, 'Times New Roman', serif;
    }
    .cl-card-delta {
        font-size: 0.82rem;
        color: var(--cl-muted);
        margin-top: 0.25rem;
    }
    .cl-edital {
        background: var(--cl-card);
        border: 1px solid #E5E7EB;
        border-radius: 8px;
        padding: 1rem 1.25rem;
        margin-bottom: 0.6rem;
        transition: box-shadow 0.15s;
    }
    .cl-edital:hover { box-shadow: 0 4px 12px rgba(0,0,0,0.08); }

    /* Card estilo ConLicitação */
    .cl-edital-card {
        background: var(--cl-card);
        border: 1px solid #E5E7EB;
        border-radius: 8px;
        margin-bottom: 1rem;
        overflow: hidden;
        box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    }
    .cl-edital-header {
        background: var(--cl-primary);
        color: white;
        padding: 0.6rem 1rem;
        display: flex;
        justify-content: space-between;
        align-items: center;
    }
    .cl-edital-num {
        background: var(--cl-accent);
        color: var(--cl-primary);
        font-weight: 700;
        width: 28px; height: 28px;
        border-radius: 50%;
        display: flex; align-items: center; justify-content: center;
        font-size: 0.85rem;
    }
    .cl-edital-urgent {
        background: var(--cl-danger);
        color: white;
        font-weight: 700;
        font-size: 0.7rem;
        padding: 0.25rem 0.7rem;
        border-radius: 4px;
        letter-spacing: 0.06em;
    }
    .cl-score-3 {
        background: #DCFCE7; color: #15803D;
        font-weight: 700; font-size: 0.72rem;
        padding: 0.2rem 0.6rem; border-radius: 4px;
        letter-spacing: 0.04em;
    }
    .cl-score-2 {
        background: #FEF9C3; color: #854D0E;
        font-weight: 700; font-size: 0.72rem;
        padding: 0.2rem 0.6rem; border-radius: 4px;
        letter-spacing: 0.04em;
    }
    .cl-score-1 {
        background: #F3F4F6; color: #4B5563;
        font-weight: 700; font-size: 0.72rem;
        padding: 0.2rem 0.6rem; border-radius: 4px;
        letter-spacing: 0.04em;
    }
    .cl-edital-body {
        padding: 1rem 1.25rem;
    }
    .cl-edital-objeto {
        color: var(--cl-text);
        font-size: 0.95rem;
        margin-bottom: 0.8rem;
        line-height: 1.4;
    }
    .cl-edital-meta {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 0.4rem 1.25rem;
        font-size: 0.85rem;
        color: var(--cl-text);
    }
    .cl-edital-meta b { color: var(--cl-muted); font-weight: 500; }
    .cl-edital-actions {
        background: #FAFAFA;
        padding: 0.75rem 1.25rem;
        border-top: 1px solid #E5E7EB;
        display: flex; gap: 0.5rem; align-items: center; flex-wrap: wrap;
    }
    .cl-edital-actions-label {
        color: var(--cl-muted);
        font-size: 0.8rem;
        margin-right: 0.4rem;
    }
    .cl-btn {
        display: inline-flex;
        align-items: center;
        padding: 0.4rem 0.9rem;
        border-radius: 6px;
        font-size: 0.82rem;
        font-weight: 500;
        text-decoration: none;
        transition: opacity 0.15s;
    }
    .cl-btn:hover { opacity: 0.85; text-decoration: none; }
    .cl-btn-primary {
        background: #1E40AF;
        color: white !important;
    }
    .cl-btn-secondary {
        background: #3A4149;
        color: white !important;
    }
    .cl-tag {
        display: inline-block;
        font-size: 0.72rem;
        padding: 0.15rem 0.55rem;
        border-radius: 999px;
        margin-right: 0.4rem;
        font-weight: 500;
    }
    .cl-tag-usina    { background: #DBEAFE; color: #1E3A8A; }
    .cl-tag-pedreira { background: #DCFCE7; color: #166534; }
    .cl-tag-mg, .cl-tag-sp, .cl-tag-rj, .cl-tag-es, .cl-tag-pr, .cl-tag-ba {
        background: #F3F4F6; color: #1F2937;
    }
    .cl-divider { border-top: 1px solid #E5E7EB; margin: 1rem 0; }
    /* Resumo IA */
    .cl-ia-box {
        background: #F0F9FF; border: 1px solid #BAE6FD;
        border-radius: 6px; padding: 0.9rem 1rem; margin-top: 0.6rem;
    }
    .cl-ia-rec-participar { background:#DCFCE7;color:#15803D;font-weight:700;font-size:0.75rem;padding:0.2rem 0.6rem;border-radius:4px; }
    .cl-ia-rec-analisar   { background:#FEF9C3;color:#854D0E;font-weight:700;font-size:0.75rem;padding:0.2rem 0.6rem;border-radius:4px; }
    .cl-ia-rec-descartar  { background:#FEE2E2;color:#991B1B;font-weight:700;font-size:0.75rem;padding:0.2rem 0.6rem;border-radius:4px; }
    .cl-header-bar {
        background: linear-gradient(90deg, var(--cl-primary) 0%, #1E40AF 100%);
        color: white;
        padding: 1.2rem 1.5rem;
        border-radius: 8px;
        margin-bottom: 1.5rem;
        display: flex;
        justify-content: space-between;
        align-items: center;
    }
    .cl-header-title { font-size: 1.45rem; font-weight: 700; }
    .cl-header-sub   { font-size: 0.85rem; opacity: 0.85; }
    /* Cards lidos */
    .cl-edital-card-lido { opacity: 0.52; border-left: 4px solid #16A34A; }
    .cl-lido-badge {
        background: #DCFCE7; color: #15803D;
        font-size: 0.7rem; font-weight: 700;
        padding: 0.2rem 0.6rem; border-radius: 4px;
        letter-spacing: 0.04em;
    }
    /* Badges de origem da licitação */
    .cl-fonte-pncp       { background:#EFF6FF;color:#2E353D;font-size:0.62rem;font-weight:700;padding:0.12rem 0.4rem;border-radius:3px;letter-spacing:0.03em; }
    .cl-fonte-comprasnet { background:#FFF7ED;color:#C2410C;font-size:0.62rem;font-weight:700;padding:0.12rem 0.4rem;border-radius:3px;letter-spacing:0.03em; }
    .cl-fonte-bll        { background:#F5F3FF;color:#6D28D9;font-size:0.62rem;font-weight:700;padding:0.12rem 0.4rem;border-radius:3px;letter-spacing:0.03em; }
    /* Boletim (modelo ConLicitação) */
    .cl-boletim-dia {
        background: linear-gradient(90deg,#3A4149,#1E3A5F); color:#fff;
        font-weight:700; font-size:0.95rem; padding:0.5rem 0.9rem;
        border-radius:6px; margin:1.2rem 0 0.6rem 0;
    }
    .cl-fav-badge { color:#F59E0B; font-size:1rem; font-weight:700; }
    .cl-origem-tag {
        background:#FBF3E3; color:#8A6A1E; font-size:0.7rem; font-weight:700;
        padding:0.1rem 0.4rem; border-radius:3px;
    }

    /* =========================================================
       OVERRIDE VISUAL — modelo ConLicitação (tema claro + verde)
       ========================================================= */
    .cl-edital-card { border:1px solid #E6E8EB; border-radius:10px; box-shadow:0 2px 10px rgba(16,42,71,0.08); margin-bottom:1.1rem; }
    .cl-edital-header { background:#2D323B !important; padding:0.5rem 0.9rem !important; }
    .cl-hdr-left { display:flex; align-items:center; gap:0.45rem; }
    .cl-hdr-right { display:flex; align-items:center; gap:0.45rem; }
    .cl-edital-num { background:#C28E2C !important; color:#fff !important; min-width:26px !important; height:26px !important; width:auto !important; padding:0 0.45rem; border-radius:13px !important; font-size:0.82rem; }
    .cl-hdr-icon { width:26px; height:26px; border-radius:50%; background:rgba(255,255,255,0.16); color:#fff; display:inline-flex; align-items:center; justify-content:center; font-size:0.82rem; }
    .cl-hdr-icon.on-fav  { background:#F59E0B; color:#fff; }
    .cl-hdr-icon.on-lido { background:#C28E2C; color:#fff; }
    .cl-edital-body { padding:0.9rem 1.1rem !important; }
    .cl-edital-objeto { color:#111827 !important; font-size:0.95rem; }
    .cl-edital-meta { gap:0.45rem 1.6rem !important; font-size:0.87rem !important; color:#374151 !important; }
    .cl-edital-meta b { color:#9CA3AF !important; font-weight:600 !important; }
    .cl-valor { color:#A9781F; font-weight:800; font-size:1.05rem; }
    .cl-orgao { color:#3A4149; font-weight:600; }
    .cl-src-chip { display:inline-block; border:1px solid #D1D5DB; border-radius:6px; padding:0.08rem 0.5rem; font-size:0.7rem; font-weight:700; color:#374151; background:#F9FAFB; }
    .cl-origem-tag { background:#FBF3E3 !important; color:#8A6A1E !important; padding:0.1rem 0.45rem !important; border-radius:4px !important; }
    .cl-edital-actions { background:#F8FAFC !important; border-top:1px solid #EDF0F3 !important; padding:0.7rem 1.1rem !important; }
    .cl-edital-actions-label { font-weight:600 !important; }
    .cl-btn-primary  { background:#3A4149 !important; color:#fff !important; }
    .cl-btn-secondary{ background:#fff !important; color:#3A4149 !important; border:1px solid #3A4149 !important; }
    .cl-boletim-dia { background:#FBF3E3 !important; color:#8A6A1E !important; border-left:4px solid #C28E2C; border-radius:6px; }
    .cl-header-bar { background:var(--cl-header) !important; border-radius:0 !important; border-bottom:3px solid var(--cl-accent) !important; margin-top:0 !important; }
    .cl-header-title { font-family:Georgia,'Times New Roman',serif; letter-spacing:0.01em; }
    .cl-boletim-head { display:flex; align-items:baseline; gap:0.8rem; flex-wrap:wrap; border-bottom:2px solid #C28E2C; padding-bottom:0.5rem; margin:0.2rem 0 0.9rem 0; }
    .cl-boletim-head-title { font-size:1.4rem; font-weight:800; color:#3A4149; font-family:Georgia,'Times New Roman',serif; }
    .cl-boletim-head-sub { font-size:0.84rem; color:#6B7280; }
    /* Botões interativos do Streamlit como pílulas azuis pequenas (estilo ConLicitação) */
    .stButton button, [data-testid="stButton"] button, [data-testid="stBaseButton-secondary"] {
        border-radius:6px !important; font-weight:600 !important; font-size:0.78rem !important;
        padding:0.28rem 0.7rem !important; min-height:0 !important;
        background:#3A4149 !important; border:1px solid #3A4149 !important;
    }
    .stButton button *, [data-testid="stButton"] button * { color:#fff !important; }
    .stButton button:hover, [data-testid="stButton"] button:hover { background:#2E353D !important; border-color:#2E353D !important; }
    .stDownloadButton button, [data-testid="stDownloadButton"] button {
        background:#C28E2C !important; border:1px solid #C28E2C !important; border-radius:6px !important;
        font-weight:600 !important; font-size:0.78rem !important; padding:0.28rem 0.7rem !important;
    }
    .stDownloadButton button * { color:#fff !important; }
    /* Gatilho do popover "Mais filtros" — discreto, contorno azul */
    [data-testid="stPopover"] button { background:#fff !important; border:1px solid #3A4149 !important; }
    [data-testid="stPopover"] button * { color:#3A4149 !important; }
    /* Total de licitações */
    .cl-total { color:#374151; font-size:0.95rem; padding-top:0.4rem; }
    /* Ritmo: cards juntos, ações coladas, expander compacto */
    .cl-edital-card { margin-bottom:0.35rem !important; }
    .cl-edital-actions { padding:0.5rem 1.1rem !important; }
    [data-testid="stExpander"] { border:none !important; margin:0.1rem 0 0.15rem 0 !important; }
    [data-testid="stExpander"] summary { font-size:0.83rem !important; color:#3A4149 !important; }
    </style>
    """,
    unsafe_allow_html=True,
)


# ===== Login =====
def _check_login() -> bool:
    """Login simples por senha. Senha em st.secrets['auth']['password']."""
    if st.session_state.get("autenticado"):
        return True

    senha_correta = None
    try:
        senha_correta = st.secrets["auth"]["password"]
    except Exception:
        senha_correta = os.getenv("APP_PASSWORD", "concrelagos2026")
    if not senha_correta:
        senha_correta = "concrelagos2026"  # fallback dev

    if LOGO_PATH.exists():
        lc1, lc2, lc3 = st.columns([2, 3, 2])
        with lc2:
            st.image(str(LOGO_PATH), width='stretch')

    st.markdown(
        """
        <div class="cl-header-bar" style="justify-content:center;text-align:center;">
            <div>
                <div class="cl-header-title">Concrelagos Intelligence Hub</div>
                <div class="cl-header-sub">Rastreador autônomo de licitações públicas — acesso restrito</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    fc1, fc2, fc3 = st.columns([1, 1.5, 1])
    with fc2:
        with st.form("login"):
            st.markdown(
                "<div class='cl-serif' style='font-size:1.15rem;font-weight:700;"
                "color:#3A4149;margin-bottom:0.1rem;'>Acesso ao Sistema</div>",
                unsafe_allow_html=True,
            )
            st.caption("Sistema restrito — acesso autorizado apenas")
            senha = st.text_input("Senha de acesso", type="password", placeholder="Informe a senha")
            ok = st.form_submit_button("Entrar", type="primary", width='stretch')
    if ok:
        if senha == senha_correta:
            st.session_state["autenticado"] = True
            st.rerun()
        else:
            st.error("Senha incorreta.")
    return False


# ===== Acesso aos dados =====
def _parse_num_br(v) -> float | None:
    """Parse robusto: 'string' ou número. Trata vírgula BR, e detecta inteiros
    gigantes que vieram de coordenadas (-233112878 → -23.3112878)."""
    if v is None or v == "":
        return None
    s = str(v).strip().replace(",", ".")
    if not s:
        return None
    try:
        n = float(s)
    except ValueError:
        return None
    return n


def _parse_coord_serie(serie: pd.Series) -> pd.Series:
    """Aplica _parse_num_br + correção de coord sem decimal (>90 abs)."""
    def fix(v):
        n = _parse_num_br(v)
        if n is None:
            return None
        if abs(n) > 90:  # coordenada sem ponto decimal
            sign = -1 if n < 0 else 1
            digits = str(int(abs(n)))
            if len(digits) > 2:
                return sign * float(f"{digits[:2]}.{digits[2:]}")
            return None
        return n
    return serie.apply(fix)


def _parse_dist_serie(serie: pd.Series) -> pd.Series:
    """Distância: aplica parse + correção quando >1000 (= "40,82" virou 4082)."""
    def fix(v):
        n = _parse_num_br(v)
        if n is None:
            return None
        # Distância plausível: 0-1000 km. Se >5000, divide por 100.
        if n > 5000:
            return n / 100.0
        return n
    return serie.apply(fix)


def _build_gspread_client():
    """Retorna gspread client. Em LOCAL usa arquivo do .env; no Streamlit Cloud
    usa st.secrets['gcp']['service_account'] (dict)."""
    import gspread

    # Caminho 1: Streamlit Cloud (secrets em TOML)
    try:
        sa_info = dict(st.secrets["gcp"]["service_account"])
        return gspread.service_account_from_dict(sa_info)
    except Exception:
        pass

    # Caminho 2: arquivo local apontado por env var
    creds = os.environ.get("GOOGLE_SHEETS_CREDENTIALS_PATH")
    if creds and os.path.exists(creds):
        return gspread.service_account(filename=creds)

    st.error("Credenciais ausentes. Configure st.secrets['gcp']['service_account'] (Cloud) ou GOOGLE_SHEETS_CREDENTIALS_PATH (.env local).")
    st.stop()


def _gemini_client():
    """Retorna cliente Gemini configurado (novo SDK google-genai>=1.0).

    Usa gemini-2.0-flash — gratuito (15 req/min, 1M tokens/dia no tier Free).
    Chave obtida em https://aistudio.google.com/app/apikey (projeto concrelagos-hub).
    """
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        try:
            key = st.secrets["gemini"]["api_key"]
        except Exception:
            pass
    if not key:
        return None
    try:
        from google import genai
        return genai.Client(api_key=key)
    except ImportError:
        return None


def _get_or_create_worksheet(planilha, nome: str, rows: int = 500, cols: int = 20):
    """Abre ou cria uma aba no Google Sheets."""
    import gspread
    try:
        return planilha.worksheet(nome)
    except gspread.exceptions.WorksheetNotFound:
        return planilha.add_worksheet(title=nome, rows=rows, cols=cols)


def _get_sheet_id() -> str:
    sid = os.environ.get("GOOGLE_SHEETS_ID")
    if sid:
        return sid
    try:
        return st.secrets["gcp"]["sheets_id"]
    except Exception:
        st.error("GOOGLE_SHEETS_ID não configurado.")
        st.stop()


@st.cache_data(ttl=300, show_spinner="Carregando execuções...")
def _carregar_execucoes() -> pd.DataFrame:
    """Carrega a aba 'Execucoes' do Sheets com histórico de runs do scraper."""
    try:
        gc = _build_gspread_client()
        sh = gc.open_by_key(_get_sheet_id())
        ws = sh.worksheet("Execucoes")
        vals = ws.get_all_values()
        if not vals or len(vals) < 2:
            return pd.DataFrame()
        header = [h.strip() for h in vals[0]]
        rows = [dict(zip(header, r)) for r in vals[1:] if any(c.strip() for c in r)]
        df = pd.DataFrame(rows)
        if "data_execucao" in df.columns:
            df["data_execucao"] = pd.to_datetime(df["data_execucao"], errors="coerce")
        for col in ("brutos", "apos_keyword", "apos_geo", "novos", "tempo_s"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
        return df
    except Exception:
        return pd.DataFrame()


def _lidos_set() -> set:
    """Retorna o set de num_controle_pncp marcados como lidos (em session_state).
    Inicializa fazendo leitura do Sheets na primeira chamada da sessão."""
    if "lidos_set" not in st.session_state:
        try:
            gc = _build_gspread_client()
            sh = gc.open_by_key(_get_sheet_id())
            try:
                ws = sh.worksheet("Lidos")
                vals = ws.col_values(1)  # primeira coluna
                # Remove cabeçalho se existir
                lidos = {v.strip() for v in vals if v.strip() and v.strip() != "numero_controle_pncp"}
            except Exception:
                lidos = set()
        except Exception:
            lidos = set()
        st.session_state["lidos_set"] = lidos
    return st.session_state["lidos_set"]


def _marcar_lido(num_controle: str) -> None:
    """Adiciona num_controle à aba 'Lidos' do Sheets e ao session_state."""
    lidos = _lidos_set()
    if num_controle in lidos:
        return
    try:
        gc = _build_gspread_client()
        sh = gc.open_by_key(_get_sheet_id())
        ws = _get_or_create_worksheet(sh, "Lidos", rows=2000, cols=1)
        # Garante cabeçalho
        header = ws.acell("A1").value
        if not header or header.strip() != "numero_controle_pncp":
            ws.update("A1", [["numero_controle_pncp"]])
        ws.append_row([num_controle], value_input_option="USER_ENTERED")
        lidos.add(num_controle)
        st.session_state["lidos_set"] = lidos
    except Exception as exc:
        st.toast(f"Erro ao salvar no Sheets: {exc}", icon="⚠️")


def _marcar_lidos_bulk(nums: list) -> None:
    """Marca vários como lidos numa única gravação (1 append_rows) — para o botão
    'Marcar dia como lido', evitando N chamadas ao Sheets."""
    lidos = _lidos_set()
    novos = [str(n) for n in nums if str(n) and str(n) not in lidos]
    if not novos:
        return
    try:
        gc = _build_gspread_client()
        sh = gc.open_by_key(_get_sheet_id())
        ws = _get_or_create_worksheet(sh, "Lidos", rows=2000, cols=1)
        header = ws.acell("A1").value
        if not header or header.strip() != "numero_controle_pncp":
            ws.update("A1", [["numero_controle_pncp"]])
        ws.append_rows([[n] for n in novos], value_input_option="USER_ENTERED")
        lidos.update(novos)
        st.session_state["lidos_set"] = lidos
    except Exception as exc:
        st.toast(f"Erro ao marcar dia como lido: {exc}", icon="⚠️")


def _desmarcar_lido(num_controle: str) -> None:
    """Remove num_controle da aba 'Lidos' do Sheets e do session_state."""
    lidos = _lidos_set()
    if num_controle not in lidos:
        return
    try:
        gc = _build_gspread_client()
        sh = gc.open_by_key(_get_sheet_id())
        ws = _get_or_create_worksheet(sh, "Lidos", rows=2000, cols=1)
        cell = ws.find(num_controle)
        if cell:
            ws.delete_rows(cell.row)
        lidos.discard(num_controle)
        st.session_state["lidos_set"] = lidos
    except Exception as exc:
        st.toast(f"Erro ao remover do Sheets: {exc}", icon="⚠️")


def _favs_set() -> set:
    """Set de num_controle marcados como favoritos (estrela). Lê do Sheets 1x/sessão."""
    if "favs_set" not in st.session_state:
        try:
            gc = _build_gspread_client()
            sh = gc.open_by_key(_get_sheet_id())
            ws = sh.worksheet("Favoritos")
            vals = ws.col_values(1)
            favs = {v.strip() for v in vals if v.strip() and v.strip() != "numero_controle_pncp"}
        except Exception:
            favs = set()
        st.session_state["favs_set"] = favs
    return st.session_state["favs_set"]


def _marcar_fav(num_controle: str) -> None:
    favs = _favs_set()
    if num_controle in favs:
        return
    try:
        gc = _build_gspread_client()
        sh = gc.open_by_key(_get_sheet_id())
        ws = _get_or_create_worksheet(sh, "Favoritos", rows=2000, cols=1)
        header = ws.acell("A1").value
        if not header or header.strip() != "numero_controle_pncp":
            ws.update("A1", [["numero_controle_pncp"]])
        ws.append_row([num_controle], value_input_option="USER_ENTERED")
        favs.add(num_controle)
        st.session_state["favs_set"] = favs
    except Exception as exc:
        st.toast(f"Erro ao favoritar: {exc}", icon="⚠️")


def _desmarcar_fav(num_controle: str) -> None:
    favs = _favs_set()
    if num_controle not in favs:
        return
    try:
        gc = _build_gspread_client()
        sh = gc.open_by_key(_get_sheet_id())
        ws = _get_or_create_worksheet(sh, "Favoritos", rows=2000, cols=1)
        cell = ws.find(num_controle)
        if cell:
            ws.delete_rows(cell.row)
        favs.discard(num_controle)
        st.session_state["favs_set"] = favs
    except Exception as exc:
        st.toast(f"Erro ao desfavoritar: {exc}", icon="⚠️")


@st.cache_data(ttl=300, show_spinner="Carregando dados da planilha...")
def _carregar_dados() -> tuple[pd.DataFrame, pd.DataFrame, datetime | None]:
    """Carrega abas Filiais + Novas Licitações via get_all_values (strings puras,
    evita auto-numericise do gspread que quebra vírgula PT-BR)."""
    gc = _build_gspread_client()
    sheet_id = _get_sheet_id()
    sh = gc.open_by_key(sheet_id)

    def _read_as_df(ws_name: str) -> pd.DataFrame:
        try:
            ws = sh.worksheet(ws_name)
        except Exception:
            return pd.DataFrame()
        vals = ws.get_all_values()
        if not vals or len(vals) < 2:
            return pd.DataFrame()
        header = [h.strip() for h in vals[0]]
        rows = [dict(zip(header, r)) for r in vals[1:] if any(c.strip() for c in r)]
        return pd.DataFrame(rows)

    fil = _read_as_df("Filiais")
    ed = _read_as_df("Novas Licitações")

    # Normaliza Filiais
    if not fil.empty:
        if "latitude" in fil.columns:
            fil["latitude"] = _parse_coord_serie(fil["latitude"])
        if "longitude" in fil.columns:
            fil["longitude"] = _parse_coord_serie(fil["longitude"])

    # Normaliza Editais
    if not ed.empty:
        if "valor_estimado" in ed.columns:
            ed["valor_estimado"] = ed["valor_estimado"].apply(_parse_num_br)
        if "distancia_km" in ed.columns:
            ed["distancia_km"] = _parse_dist_serie(ed["distancia_km"])
        for col in ("data_execucao", "data_abertura", "data_encerramento"):
            if col in ed.columns:
                ed[col] = pd.to_datetime(ed[col], errors="coerce")
        # Garante colunas de score/confiança mesmo em planilhas antigas (backward compat)
        for col_default in ("score", "score_label", "keyword_trigger", "itens_encontrados"):
            if col_default not in ed.columns:
                ed[col_default] = ""

    ultima = ed["data_execucao"].max() if "data_execucao" in ed.columns and not ed.empty else None
    return fil, ed, ultima


# ===== Resumo com IA =====
_RESUMO_HEADER = [
    "numero_controle_pncp", "data_resumo", "produto_exato", "quantidade",
    "prazo_entrega", "local_entrega", "exigencias_tecnicas",
    "recomendacao", "justificativa",
]


def _buscar_resumo_cache(num_controle: str) -> dict | None:
    """Lê a aba 'Resumos IA' e retorna o dict do resumo se já existir."""
    try:
        gc = _build_gspread_client()
        sh = gc.open_by_key(_get_sheet_id())
        ws = _get_or_create_worksheet(sh, "Resumos IA", rows=500, cols=len(_RESUMO_HEADER))
        vals = ws.get_all_values()
        if len(vals) < 2:
            return None
        header = vals[0]
        for row in vals[1:]:
            d = dict(zip(header, row))
            if d.get("numero_controle_pncp") == num_controle:
                return d
    except Exception:
        pass
    return None


def _salvar_resumo_cache(num_controle: str, resumo: dict) -> None:
    """Salva o resumo na aba 'Resumos IA' do Sheets."""
    try:
        gc = _build_gspread_client()
        sh = gc.open_by_key(_get_sheet_id())
        ws = _get_or_create_worksheet(sh, "Resumos IA", rows=500, cols=len(_RESUMO_HEADER))
        vals = ws.get_all_values()
        # Garante header
        if not vals or not vals[0] or vals[0][0] != _RESUMO_HEADER[0]:
            ws.clear()
            ws.append_row(_RESUMO_HEADER, value_input_option="USER_ENTERED")
        linha = [
            num_controle,
            datetime.now().isoformat(timespec="seconds"),
            resumo.get("produto_exato", ""),
            resumo.get("quantidade", ""),
            resumo.get("prazo_entrega", ""),
            resumo.get("local_entrega", ""),
            "; ".join(resumo.get("exigencias_tecnicas") or []),
            resumo.get("recomendacao", ""),
            resumo.get("justificativa", ""),
        ]
        ws.append_row(linha, value_input_option="USER_ENTERED")
    except Exception as exc:
        st.warning(f"Não foi possível salvar o resumo no cache: {exc}")


def _baixar_texto_edital(num_controle: str, link_pdf: str, link_pncp: str) -> str:
    """Obtém o TEXTO do edital, priorizando o PDF real via API de arquivos do PNCP.

    Ordem de tentativa:
      1. API de arquivos do PNCP (a partir do numeroControlePNCP) → PDF de verdade.
      2. link_pdf (link_sistema_origem) — pode ser PDF direto.
      3. link_pncp — geralmente página HTML do portal (fallback).
    PDFs são lidos com pdfplumber; HTML tem as tags removidas antes de ir à IA.
    Retorna "" se nada utilizável for obtido.
    """
    import io, re
    import requests
    import pdfplumber

    HDRS = {"User-Agent": "Mozilla/5.0 (compatible; ConcrelagosBot/1.0)"}
    urls: list[str] = []

    # 1) API de arquivos do PNCP — numeroControlePNCP no formato "{cnpj}-{tipo}-{seq}/{ano}"
    try:
        nc = (num_controle or "").strip()
        if "/" in nc and "-" in nc:
            esquerda, ano = nc.split("/", 1)
            partes = esquerda.split("-")
            cnpj, seq = partes[0], partes[-1]
            seq_int = str(int(seq))  # remove zeros à esquerda
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

    # 2) e 3) fallbacks
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
                if len(txt) >= 100:
                    return txt
            else:
                # HTML → remove scripts/estilos/tags e normaliza espaços
                html = resp.text
                html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
                txt = re.sub(r"<[^>]+>", " ", html)
                txt = re.sub(r"\s+", " ", txt).strip()
                if len(txt) >= 200:
                    return txt[:12000]
        except Exception:
            continue
    return ""


def _resumir_edital(num_controle: str, link_pdf: str, link_pncp: str) -> dict | None:
    """Baixa o PDF do edital e gera um resumo estruturado via Gemini.

    Retorna dict com: produto_exato, quantidade, prazo_entrega, local_entrega,
    exigencias_tecnicas (list), recomendacao, justificativa.
    Retorna None em caso de erro.
    """
    import json

    # 1) Verifica cache
    cached = _buscar_resumo_cache(num_controle)
    if cached:
        exig_raw = cached.get("exigencias_tecnicas", "")
        cached["exigencias_tecnicas"] = [e.strip() for e in exig_raw.split(";") if e.strip()]
        return cached

    # 2) Precisa do cliente Gemini (gratuito)
    client = _gemini_client()
    if client is None:
        st.error("GEMINI_API_KEY não configurada. Obtenha grátis em aistudio.google.com/app/apikey e configure em Streamlit → Settings → Secrets → [gemini] api_key.")
        return None

    # 3) Obtém o texto do edital (PDF real via API de arquivos do PNCP; fallback p/ links)
    if not (link_pdf or link_pncp or num_controle):
        st.warning("Sem link disponível para baixar o edital.")
        return None

    texto_edital = _baixar_texto_edital(num_controle, link_pdf, link_pncp)

    if not texto_edital or len(texto_edital) < 100:
        st.warning(
            "Não consegui extrair o texto do edital (PDF escaneado ou portal sem download direto). "
            "Abra o link 'Baixar Edital' manualmente."
        )
        return None

    # 4) Chama Gemini — tenta modelos ATUAIS em ordem; pula automaticamente os
    #    indisponíveis (404/NOT_FOUND) e os sem cota (429), até um funcionar.
    prompt = f"""Analise este edital público brasileiro de fornecimento de concreto usinado ou brita.
Responda APENAS com JSON válido (sem markdown, sem explicação fora do JSON):
{{
  "produto_exato": "produto exato sendo comprado (ex: Concreto Usinado FCK 25 MPa)",
  "quantidade": "quantidade/volume (ex: 1.200 m³ ou 500 toneladas)",
  "prazo_entrega": "prazo e forma de entrega (ex: 180 dias corridos, parcelado conforme cronograma)",
  "local_entrega": "cidade/UF e endereço da obra (ex: Belo Horizonte/MG — Av. X nº Y)",
  "exigencias_tecnicas": ["lista de requisitos técnicos (FCK, slump, aditivos, normas ABNT, etc.)"],
  "recomendacao": "PARTICIPAR ou ANALISAR ou DESCARTAR",
  "justificativa": "1 frase explicando a recomendação"
}}

Edital (primeiros {min(len(texto_edital), 10000)} caracteres):
{texto_edital[:10000]}"""

    response = None
    # 🔒 TRAVA DE CUSTO ZERO: só usamos modelos da família "flash" (camada gratuita
    # do Gemini). Modelos "pro"/"ultra"/"exp" (que podem cobrar) são EXCLUÍDOS.
    # Sem billing ativado na conta, é impossível gerar fatura — no máximo dá erro de cota.
    _PAGOS = ("pro", "ultra", "exp", "thinking")
    def _eh_gratuito(nome: str) -> bool:
        n = nome.lower()
        return "flash" in n and "vision" not in n and not any(p in n for p in _PAGOS)

    # Descoberta dinâmica (à prova de renomeações do Google): pergunta à conta quais
    # modelos existem e mantém só os 'flash' gratuitos que suportam generateContent.
    _MODELOS_GEMINI = []
    try:
        for _m in client.models.list():
            _nome = (getattr(_m, "name", "") or "").split("/")[-1]
            _acts = getattr(_m, "supported_actions", None) or []
            if _nome and _eh_gratuito(_nome) and ("generateContent" in _acts or not _acts):
                _MODELOS_GEMINI.append(_nome)
    except Exception:
        pass
    # Reforço: candidatos conhecidos da camada gratuita (sem duplicar)
    for _c in ["gemini-2.0-flash", "gemini-2.5-flash", "gemini-flash-latest", "gemini-2.0-flash-001"]:
        if _c not in _MODELOS_GEMINI and _eh_gratuito(_c):
            _MODELOS_GEMINI.append(_c)
    _ultimo_erro = ""
    for _modelo in _MODELOS_GEMINI:
        try:
            response = client.models.generate_content(model=_modelo, contents=prompt)
            break
        except Exception as _exc:
            _exc_str = str(_exc)
            _ultimo_erro = _exc_str
            # Modelo indisponível (404) ou sem cota (429) → tenta o próximo
            if any(t in _exc_str for t in ("429", "RESOURCE_EXHAUSTED", "404", "NOT_FOUND", "not found", "not supported")):
                continue
            # Outro erro (chave inválida, rede) — mostra e para
            st.error(f"Erro ao chamar Gemini API ({_modelo}): {_exc}")
            return None

    if response is None:
        if "429" in _ultimo_erro or "RESOURCE_EXHAUSTED" in _ultimo_erro:
            st.warning(
                "**Cota gratuita do Gemini esgotada** por hoje. A cota renova às 00h UTC. "
                "Tente novamente mais tarde."
            )
        else:
            st.error(
                "Nenhum modelo Gemini disponível respondeu. Último erro: "
                f"{_ultimo_erro[:300]}"
            )
        return None

    # Extrai o texto com robustez: no SDK novo, response.text pode ser None ou
    # lançar exceção quando a resposta é bloqueada/cortada (finish_reason
    # SAFETY/MAX_TOKENS/RECITATION). Antes isso virava um "erro" genérico confuso.
    raw = ""
    try:
        raw = (response.text or "").strip()
    except Exception:
        raw = ""
    if not raw:
        motivo = None
        try:
            _cand = (getattr(response, "candidates", None) or [None])[0]
            _partes = getattr(getattr(_cand, "content", None), "parts", None) or []
            raw = "".join((getattr(p, "text", "") or "") for p in _partes).strip()
            motivo = getattr(_cand, "finish_reason", None)
        except Exception:
            pass
        if not raw:
            st.warning(
                f"A IA não retornou texto utilizável (motivo: {motivo or 'desconhecido'}). "
                "Costuma ser resposta cortada ou bloqueada — clique em IA novamente."
            )
            return None

    try:
        # Remove possível bloco markdown ```json ... ```
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.rstrip("`").strip()
        resumo = json.loads(raw)
    except json.JSONDecodeError as exc:
        st.warning(f"Gemini retornou JSON inválido: {exc}. Tente novamente.")
        return None
    except Exception as exc:
        st.error(f"Erro ao processar resposta do Gemini: {exc}")
        return None

    # 5) Salva no cache e retorna
    _salvar_resumo_cache(num_controle, resumo)
    return resumo


# ===== UI Helpers =====
def _card(title: str, value: str, delta: str = "") -> str:
    return f"""
    <div class="cl-card">
        <div class="cl-card-title">{title}</div>
        <div class="cl-card-value">{value}</div>
        {f'<div class="cl-card-delta">{delta}</div>' if delta else ''}
    </div>
    """


def _money(v: float) -> str:
    if pd.isna(v):
        return "—"
    if v >= 1_000_000:
        return f"R$ {v/1_000_000:.2f}M"
    if v >= 1_000:
        return f"R$ {v/1_000:.1f}k"
    return f"R$ {v:,.0f}".replace(",", ".")


# ===== Sidebar / Filtros =====
def _sidebar_acoes() -> None:
    """Barra lateral enxuta (recolhida por padrão) — sem filtros.
    No modelo ConLicitação os filtros ficam no topo do Boletim, não na lateral."""
    st.sidebar.markdown("### Painel")
    st.sidebar.caption("Concrelagos Intelligence Hub")
    if st.sidebar.button("Recarregar dados", width='stretch'):
        st.cache_data.clear()
        st.rerun()
    if st.sidebar.button("Sair", width='stretch'):
        st.session_state["autenticado"] = False
        st.rerun()


def _aplica_filtros(ed: pd.DataFrame, filtros: dict) -> pd.DataFrame:
    """Aplica os filtros presentes no dict (todas as chaves são opcionais)."""
    if ed.empty:
        return ed
    df = ed.copy()
    if "uf" in df.columns and filtros.get("ufs"):
        df = df[df["uf"].isin(filtros["ufs"])]
    if "material" in df.columns and filtros.get("materiais"):
        df = df[df["material"].isin(filtros["materiais"])]
    if "score" in df.columns and filtros.get("scores"):
        df["score"] = pd.to_numeric(df["score"], errors="coerce")
        df = df[df["score"].isin(filtros["scores"])]
    if "valor_estimado" in df.columns and filtros.get("valor_min") is not None:
        df = df[(df["valor_estimado"] >= filtros["valor_min"]) & (df["valor_estimado"] <= filtros["valor_max"])]
    if "distancia_km" in df.columns and filtros.get("dist_lim") is not None:
        df = df[df["distancia_km"] <= filtros["dist_lim"]]
    if filtros.get("dt_de") and filtros.get("dt_ate") and "data_abertura" in df.columns:
        df = df[(df["data_abertura"].dt.date >= filtros["dt_de"]) & (df["data_abertura"].dt.date <= filtros["dt_ate"])]
    if filtros.get("ocultar_lidos") and "numero_controle_pncp" in df.columns:
        lidos = _lidos_set()
        if lidos:
            df = df[~df["numero_controle_pncp"].isin(lidos)]
    return df


# ===== Abas =====
def _aba_dashboard(ed: pd.DataFrame, fil: pd.DataFrame) -> None:
    st.markdown(
        '<div class="cl-boletim-head">'
        '<span class="cl-boletim-head-title">Dashboard</span>'
        '<span class="cl-boletim-head-sub">Visão executiva · concreto usinado &amp; brita</span>'
        '</div>',
        unsafe_allow_html=True,
    )

    if ed.empty:
        st.info("Nenhum edital qualificado ainda. Rode `python scraper.py` para popular.")
        return

    agora = pd.Timestamp.now()
    hoje = agora.normalize()

    # Balde 1 — Novas hoje (pela data de execução do scraper; fallback abertura)
    if "data_execucao" in ed.columns and ed["data_execucao"].notna().any():
        novas_hoje = (pd.to_datetime(ed["data_execucao"], errors="coerce").dt.normalize() == hoje).sum()
    elif "data_abertura" in ed.columns:
        novas_hoje = (pd.to_datetime(ed["data_abertura"], errors="coerce").dt.normalize() == hoje).sum()
    else:
        novas_hoje = 0

    # Balde 2 — CERTO (concreto/brita confirmado)
    _score_num = pd.to_numeric(ed.get("score", pd.Series(dtype="float")), errors="coerce")
    certo = int((_score_num == 3).sum())

    # Balde 3 — Brita
    brita = int((ed["material"] == "brita").sum()) if "material" in ed.columns else 0

    # Balde 4 — Vence ≤7 dias (encerramento; fallback abertura)
    _base = ed["data_encerramento"] if ("data_encerramento" in ed.columns and ed["data_encerramento"].notna().any()) else ed.get("data_abertura")
    if _base is not None:
        _b = pd.to_datetime(_base, errors="coerce")
        vence_7d = int(((_b >= agora) & (_b <= agora + pd.Timedelta(days=7))).sum())
    else:
        vence_7d = 0

    c1, c2, c3, c4 = st.columns(4)
    c1.markdown(_card("Novas hoje", f"{int(novas_hoje)}"), unsafe_allow_html=True)
    c2.markdown(_card("CERTO (concreto/brita)", f"{certo}"), unsafe_allow_html=True)
    c3.markdown(_card("Brita", f"{brita}"), unsafe_allow_html=True)
    c4.markdown(_card("Vence ≤ 7 dias", f"{vence_7d}"), unsafe_allow_html=True)

    st.markdown('<div class="cl-divider"></div>', unsafe_allow_html=True)

    col_a, col_b = st.columns([1, 1.3])
    with col_a:
        st.markdown("##### Por estado")
        if "uf" in ed.columns:
            por_uf = ed.groupby("uf").size().reset_index(name="qtd").sort_values("qtd", ascending=False)
            st.bar_chart(por_uf.set_index("uf"), color="#C28E2C")
    with col_b:
        st.markdown("##### Top 5 oportunidades por valor")
        if "valor_estimado" in ed.columns:
            top = ed.nlargest(5, "valor_estimado")[["orgao", "municipio", "uf", "valor_estimado", "material"]].copy()
            top["valor_estimado"] = top["valor_estimado"].apply(_money)
            st.dataframe(top, width='stretch', hide_index=True)

    st.caption(f"Total na base: {len(ed)} editais · {int((ed.get('material') == 'concreto').sum())} concreto · {brita} brita. "
               "Veja a distribuição geográfica na aba **Mapa**.")


def _aba_mapa(ed: pd.DataFrame, fil: pd.DataFrame) -> None:
    st.subheader("Mapa de Cobertura")

    layers = []
    if not fil.empty and "latitude" in fil.columns:
        usinas = fil[(fil["tipo"] == "usina") & fil["latitude"].notna()].copy()
        pedreiras = fil[(fil["tipo"] == "pedreira") & fil["latitude"].notna()].copy()

        if not usinas.empty:
            usinas["color"] = [[30, 64, 175, 200]] * len(usinas)
            layers.append(pdk.Layer(
                "ScatterplotLayer", data=usinas,
                get_position=["longitude", "latitude"],
                get_fill_color="color", get_radius=8000,
                radius_min_pixels=4, radius_max_pixels=14, pickable=True,
            ))
        if not pedreiras.empty:
            pedreiras["color"] = [[22, 101, 52, 220]] * len(pedreiras)
            layers.append(pdk.Layer(
                "ScatterplotLayer", data=pedreiras,
                get_position=["longitude", "latitude"],
                get_fill_color="color", get_radius=10000,
                radius_min_pixels=5, radius_max_pixels=16, pickable=True,
            ))

    if not ed.empty and "latitude" not in ed.columns:
        pass  # editais geocodificados futuramente
    # Plotar editais qualificados (se houverem lat/lng)
    if not ed.empty and {"latitude", "longitude"}.issubset(ed.columns):
        ed_ok = ed.dropna(subset=["latitude", "longitude"]).copy()
        ed_ok["color"] = [[220, 38, 38, 200]] * len(ed_ok)
        layers.append(pdk.Layer(
            "ScatterplotLayer", data=ed_ok,
            get_position=["longitude", "latitude"],
            get_fill_color="color", get_radius=6000,
            radius_min_pixels=4, radius_max_pixels=12, pickable=True,
        ))

    if not layers:
        st.info("Sem pontos para exibir. Rode o bootstrap e o scraper.")
        return

    view = pdk.ViewState(latitude=-21.0, longitude=-43.0, zoom=5.2, pitch=0)
    deck = pdk.Deck(
        layers=layers, initial_view_state=view,
        map_style="light",
        tooltip={"html": "<b>{nome}</b><br/>{municipio}/{uf}<br/>{tipo}", "style": {"color": "white"}},
    )
    st.pydeck_chart(deck, width='stretch')

    leg_a, leg_b, leg_c = st.columns(3)
    leg_a.markdown('<span class="cl-tag cl-tag-usina">●  Usinas (raio 70 km)</span>', unsafe_allow_html=True)
    leg_b.markdown('<span class="cl-tag cl-tag-pedreira">●  Pedreiras (raio 700 km)</span>', unsafe_allow_html=True)
    leg_c.markdown('<span class="cl-tag" style="background:#FEE2E2;color:#991B1B;">●  Editais qualificados</span>', unsafe_allow_html=True)


def _eh_urgente(data_abertura) -> bool:
    """Edital é URGENTE se a data de abertura é até 7 dias do hoje."""
    if pd.isna(data_abertura):
        return False
    try:
        dt = pd.to_datetime(data_abertura)
    except Exception:
        return False
    delta = (dt - pd.Timestamp.now()).total_seconds() / 86400
    return -1 <= delta <= 7


def _fmt_data(v) -> str:
    if pd.isna(v) or not v:
        return "—"
    try:
        return pd.to_datetime(v).strftime("%d/%m/%Y %H:%M")
    except Exception:
        return str(v)


def _exportar_excel(df: pd.DataFrame) -> bytes:
    """Gera um .xlsx com as colunas mais úteis das licitações."""
    import io
    cols = [c for c in [
        "data_abertura", "orgao", "municipio", "uf", "objeto", "material",
        "valor_estimado", "score_label", "modalidade", "numero_edital",
        "distancia_km", "filial_mais_proxima", "origem_plataforma", "fonte",
        "link_pncp", "link_sistema_origem",
    ] if c in df.columns]
    out = df[cols].copy() if cols else df.copy()
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        out.to_excel(w, index=False, sheet_name="Licitações")
    return buf.getvalue()


def _aba_editais(ed: pd.DataFrame) -> None:
    if ed.empty:
        st.subheader("Boletim de Licitações")
        st.info("Nenhuma licitação ainda. Rode `python scraper.py` para popular.")
        return

    # ===== Cabeçalho do boletim (estilo ConLicitação) =====
    st.markdown(
        '<div class="cl-boletim-head">'
        '<span class="cl-boletim-head-title">Boletim de Licitações</span>'
        '<span class="cl-boletim-head-sub">Concreto usinado &amp; brita · Pregão Eletrônico · Pregão Presencial · Dispensa</span>'
        '</div>',
        unsafe_allow_html=True,
    )

    # Deep-link por estado: URL "?uf=MG" abre o Boletim já filtrado nesse estado
    # (usado pelos links dos e-mails por estado). Aceita 1 ou vários: ?uf=MG&uf=SP
    try:
        _uf_qp = [str(u).upper().strip() for u in st.query_params.get_all("uf") if str(u).strip()]
    except Exception:
        _uf_qp = []

    # ===== Filtros: UMA linha limpa + "Mais filtros" (modelo ConLicitação) =====
    fc1, fc2, fc3, fc4 = st.columns([1.1, 1, 2, 1.2])
    with fc1:
        ufs_disp = sorted(ed["uf"].dropna().unique().tolist()) if "uf" in ed.columns and not ed.empty else []
        _default_ufs = [u for u in _uf_qp if u in ufs_disp] or ufs_disp
        uf_sel = st.multiselect("Estados", ufs_disp, default=_default_ufs)
    with fc2:
        situacao = st.selectbox("Situação", ["Abertas", "Todas", "Encerradas"],
                                help="Abertas = pregão ainda não ocorreu (padrão). Encerrados ficam ocultos.")
    with fc3:
        busca = st.text_input("Buscar", placeholder="🔎 órgão, município ou objeto…")
    with fc4:
        ordem = st.selectbox("Ordenar por", ["Mais recente", "Maior valor", "Menor distância", "Data abertura"])

    # --- Demais filtros recolhidos (deixa o topo limpo) ---
    with st.popover("Mais filtros", width='content'):
        mats = sorted(ed["material"].dropna().unique().tolist()) if "material" in ed.columns and not ed.empty else ["concreto", "brita"]
        mat_sel = st.multiselect("Material", mats, default=mats)
        _score_op = {"CERTO": 3, "PROVÁVEL": 2, "POSSÍVEL": 1}
        # Padrão: só CERTO+PROVÁVEL. Obra genérica POSSÍVEL crua não chega mais
        # (passa pelo portão de IA no scraper); fica disponível só p/ linhas legadas.
        _sc_labels = st.multiselect("Confiança", list(_score_op), default=["CERTO", "PROVÁVEL"])
        score_sel = [_score_op[l] for l in _sc_labels]
        valor_min_sel = st.slider("Valor mínimo (R$)", 0, 2_000_000, 0, step=50_000, format="R$ %d")
        portais = sorted({
            str(p).strip() for p in (
                list(ed.get("origem_plataforma", pd.Series([], dtype=str)).dropna())
                + list(ed.get("fonte", pd.Series([], dtype=str)).dropna())
            ) if str(p).strip()
        })
        portal_sel = st.multiselect("Portal / origem", portais, default=portais)
        so_favoritas = st.checkbox("Só favoritas", value=False)
        modo = st.radio("Exibição", ["Cards", "Tabela"], horizontal=True)

    ed = _aplica_filtros(ed, {
        "ufs": uf_sel, "materiais": mat_sel, "scores": score_sel,
        "valor_min": float(valor_min_sel), "valor_max": float("inf"),
    })

    df = ed.copy()

    # Busca textual
    if busca:
        b = busca.lower()
        mask = (
            df.get("orgao", pd.Series([""] * len(df))).astype(str).str.lower().str.contains(b, na=False)
            | df.get("municipio", pd.Series([""] * len(df))).astype(str).str.lower().str.contains(b, na=False)
            | df.get("objeto", pd.Series([""] * len(df))).astype(str).str.lower().str.contains(b, na=False)
        )
        df = df[mask]

    # Situação: por padrão remove pregões que já aconteceram (encerrados).
    # Usa a data de encerramento (prazo) e, na falta, a de abertura/sessão.
    if situacao != "Todas":
        base = df["data_encerramento"] if "data_encerramento" in df.columns and df["data_encerramento"].notna().any() else df.get("data_abertura")
        if base is not None:
            b = pd.to_datetime(base, errors="coerce", utc=True)
            agora = pd.Timestamp.now(tz="UTC")
            if situacao == "Abertas":
                # mantém os ainda abertos; editais SEM data não são descartados.
                df = df[b.isna() | (b >= agora)]
            else:  # Encerradas
                df = df[b < agora]

    # Portal / origem
    if portal_sel and ("origem_plataforma" in df.columns or "fonte" in df.columns):
        op = df.get("origem_plataforma", pd.Series([""] * len(df))).astype(str)
        fo = df.get("fonte", pd.Series([""] * len(df))).astype(str)
        df = df[op.isin(portal_sel) | fo.isin(portal_sel)]

    # Só favoritas
    if so_favoritas and "numero_controle_pncp" in df.columns:
        favs = _favs_set()
        df = df[df["numero_controle_pncp"].isin(favs)] if favs else df.iloc[0:0]

    # Ordenação
    if ordem == "Maior valor" and "valor_estimado" in df.columns:
        df = df.sort_values("valor_estimado", ascending=False)
    elif ordem == "Menor distância" and "distancia_km" in df.columns:
        df = df.sort_values("distancia_km", ascending=True)
    elif ordem == "Data abertura" and "data_abertura" in df.columns:
        df = df.sort_values("data_abertura", ascending=True)
    else:  # Mais recente
        if "data_abertura" in df.columns:
            df = df.sort_values("data_abertura", ascending=False)
        elif "data_execucao" in df.columns:
            df = df.sort_values("data_execucao", ascending=False)

    # ----- Faixa compacta: total à esquerda + exportar à direita -----
    tcol, ecol1, ecol2 = st.columns([4, 1, 1])
    with tcol:
        st.markdown(
            f'<div class="cl-total">Total de <b>{len(df)}</b> licitação(ões)</div>',
            unsafe_allow_html=True,
        )
    with ecol1:
        try:
            st.download_button(
                "Excel", data=_exportar_excel(df),
                file_name=f"licitacoes_{datetime.now():%Y%m%d}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                width='stretch',
            )
        except Exception:
            pass
    with ecol2:
        st.download_button(
            "CSV", data=df.to_csv(index=False).encode("utf-8"),
            file_name=f"licitacoes_{datetime.now():%Y%m%d}.csv", mime="text/csv",
            width='stretch',
        )

    if modo == "Tabela":
        st.dataframe(df, width='stretch', hide_index=True)
        return

    # ----- Paginação ----- (páginas leves: 10 cards por vez)
    _POR_PAGINA = 10
    _chave_filtro = f"{busca}|{ordem}|{situacao}|{portal_sel}|{so_favoritas}"
    if st.session_state.get("_editais_chave_filtro") != _chave_filtro:
        st.session_state["_editais_chave_filtro"] = _chave_filtro
        st.session_state["page_editais"] = 0

    total = len(df)
    n_paginas = max(1, (total + _POR_PAGINA - 1) // _POR_PAGINA)
    pagina = min(st.session_state.get("page_editais", 0), n_paginas - 1)
    start = pagina * _POR_PAGINA
    end   = min(start + _POR_PAGINA, total)
    df_page = df.iloc[start:end]

    if total == 0:
        st.caption("Nenhuma licitação encontrada com esses filtros.")
    elif n_paginas > 1:
        st.caption(f"Mostrando {start + 1}–{end} · página {pagina + 1} de {n_paginas}")

    # Agrupamento por dia (seções tipo "boletim") só quando ordenado por data
    _agrupar_por_dia = ordem in ("Mais recente", "Data abertura")
    _dia_atual = None

    # Caixa de entrada: nº de não-lidas por dia (no df filtrado inteiro) para o
    # cabeçalho do dia e o botão "Marcar dia como lido".
    _nums_por_dia: dict = {}
    if _agrupar_por_dia and "numero_controle_pncp" in df.columns:
        for _, _r in df.iterrows():
            try:
                _dk = pd.to_datetime(_r.get("data_abertura"), errors="coerce")
                _dk = _dk.date() if pd.notna(_dk) else None
            except Exception:
                _dk = None
            _nums_por_dia.setdefault(_dk, []).append(str(_r.get("numero_controle_pncp") or ""))

    # ----- Cards estilo ConLicitação -----
    for idx, row in enumerate(df_page.itertuples(index=False), start=start + 1):
        d = row._asdict()

        # Cabeçalho de "boletim" (seção por dia)
        if _agrupar_por_dia:
            _dt = d.get("data_abertura")
            _dia = None
            try:
                if _dt is not None and not pd.isna(_dt):
                    _dia = pd.to_datetime(_dt).date()
            except Exception:
                _dia = None
            if _dia != _dia_atual:
                _dia_atual = _dia
                _label_dia = _dia.strftime("%d/%m/%Y") if _dia else "Sem data"
                _nums_dia = _nums_por_dia.get(_dia, [])
                _nao_lidas_dia = [n for n in _nums_dia if n and n not in _lidos_set()]
                _suf_dia = f" · {len(_nao_lidas_dia)} não lida(s)" if _nao_lidas_dia else " · tudo lido ✓"
                _hcol1, _hcol2 = st.columns([4, 1.2])
                with _hcol1:
                    st.markdown(
                        f'<div class="cl-boletim-dia">{_label_dia}{_suf_dia}</div>',
                        unsafe_allow_html=True,
                    )
                with _hcol2:
                    if _nao_lidas_dia and st.button("Marcar dia como lido",
                                                    key=f"diaread_{_label_dia}_{idx}",
                                                    width='stretch'):
                        _marcar_lidos_bulk(_nao_lidas_dia)
                        st.rerun()
        urgente = _eh_urgente(d.get("data_abertura"))
        objeto = (d.get("objeto") or "").strip() or "(sem descrição)"
        orgao = d.get("orgao") or "—"
        cidade = f"{d.get('municipio', '')} - {d.get('uf', '')}"
        num_edital = d.get("numero_edital") or d.get("numero_controle_pncp") or "—"
        modalidade = d.get("modalidade") or ""
        data_ab = _fmt_data(d.get("data_abertura"))
        valor = _money(d.get("valor_estimado", 0)) if d.get("valor_estimado") else "—"
        material = d.get("material") or ""
        dist = d.get("distancia_km")
        dist_str = f"{dist:.0f} km" if dist not in (None, "", float("nan")) and not pd.isna(dist) else "—"
        filial = d.get("filial_mais_proxima") or "—"
        link_pncp = d.get("link_pncp") or ""
        link_origem = d.get("link_sistema_origem") or ""
        tag_material_class = "cl-tag-pedreira" if material == "brita" else "cl-tag-usina"
        itens_enc = str(d.get("itens_encontrados") or "").strip()
        keyword_trig = str(d.get("keyword_trigger") or "").strip()

        # Estado "lido" e "favorito" — ícones no cabeçalho (estilo ConLicitação)
        num_controle = str(d.get("numero_controle_pncp") or "")
        ja_lido = num_controle in _lidos_set()
        ja_fav = num_controle in _favs_set()
        card_extra_class = "cl-edital-card-lido" if ja_lido else ""
        icones_hdr_html = (
            f'<span class="cl-hdr-icon {"on-fav" if ja_fav else ""}" title="Favorita">{"★" if ja_fav else "☆"}</span>'
            f'<span class="cl-hdr-icon {"on-lido" if ja_lido else ""}" title="Lida">{"✓" if ja_lido else "○"}</span>'
        )

        # Plataforma de origem: tag no objeto + chip na meta (igual ConLicitação)
        origem_plat = str(d.get("origem_plataforma") or "").strip()
        fonte_val = str(d.get("fonte") or "PNCP").strip()
        plataforma_label = origem_plat or fonte_val or "PNCP"
        origem_tag_html = (
            f'<span class="cl-origem-tag">[{origem_plat}]</span> ' if origem_plat else ""
        )
        src_chip_html = f'<span class="cl-src-chip">{plataforma_label}</span>'

        # Score de confiança
        try:
            score_val = int(d.get("score") or 0)
        except (ValueError, TypeError):
            score_val = 0
        _score_map = {3: ("cl-score-3", "CERTO"), 2: ("cl-score-2", "PROVÁVEL"), 1: ("cl-score-1", "POSSÍVEL")}
        score_cls, score_txt = _score_map.get(score_val, ("cl-score-1", ""))
        tag_score_html = f'<span class="{score_cls}">{score_txt}</span>' if score_val else ""

        # Selo "IA ✓" — obra genérica confirmada pelo portão de IA (score 1→2)
        _ia_verif = str(d.get("ia_verificado")).strip().lower() in ("true", "1", "sim", "verdadeiro")
        _ia_prod = str(d.get("ia_produto") or "").strip()
        tag_ia_html = (
            '<span class="cl-score-2" style="background:#FBF3E3;color:#8A6A1E;border:1px solid #C28E2C;">IA ✓</span>'
            if (score_val == 2 and _ia_verif) else ""
        )
        ia_prod_html = (
            f'<div style="font-size:0.8rem;color:#8A6A1E;margin-top:0.4rem;'
            f'background:#FBF3E3;padding:0.35rem 0.6rem;border-radius:4px;border-left:3px solid #C28E2C;">'
            f'<b>IA confirmou:</b> {_ia_prod[:180]}</div>'
            if (score_val == 2 and _ia_verif and _ia_prod) else ""
        )

        # Tag URGENTE (canto superior direito)
        tag_urgente_html = '<span class="cl-edital-urgent">URGENTE</span>' if urgente else ""

        # Badge de origem da licitação
        _fonte_val = str(d.get("fonte") or "PNCP").upper()
        _fonte_cls = {
            "PNCP": "cl-fonte-pncp",
            "COMPRASNET": "cl-fonte-comprasnet",
            "BLL": "cl-fonte-bll",
        }.get(_fonte_val, "cl-fonte-pncp")
        fonte_badge_html = f'<span class="{_fonte_cls}">{_fonte_val}</span>'

        # Linha de item encontrado (somente quando preenchido)
        item_enc_html = (
            f'<div style="font-size:0.8rem;color:#374151;margin-top:0.5rem;'
            f'background:#F0FDF4;padding:0.35rem 0.6rem;border-radius:4px;border-left:3px solid #16A34A;">'
            f'🔎 <b>Item encontrado:</b> {itens_enc[:180]}'
            f'</div>'
        ) if itens_enc else ""

        # Linha de keyword que disparou (discreta, abaixo do objeto)
        kw_html = (
            f'<div style="font-size:0.75rem;color:#9CA3AF;margin-bottom:0.3rem;">'
            f'Keyword: <em>{keyword_trig}</em>'
            f'</div>'
        ) if keyword_trig else ""

        # Local da obra (quando difere da sede do órgão, ou "a confirmar")
        _local_obra = str(d.get("local_obra") or "").strip()
        if _local_obra == "a confirmar":
            local_obra_html = ('<div style="font-size:0.8rem;color:#9A3412;margin-top:0.3rem;'
                               'background:#FFF7ED;padding:0.3rem 0.6rem;border-radius:4px;border-left:3px solid #EA580C;">'
                               '<b>Local da obra:</b> a confirmar (órgão estadual/federal — verifique no edital/IA)</div>')
        elif _local_obra:
            local_obra_html = (f'<div style="font-size:0.8rem;color:#8A6A1E;margin-top:0.3rem;'
                               f'background:#FBF3E3;padding:0.3rem 0.6rem;border-radius:4px;border-left:3px solid #C28E2C;">'
                               f'<b>Local da obra:</b> {_local_obra} (≠ sede do órgão)</div>')
        else:
            local_obra_html = ""

        # Botões
        botoes = []
        if link_origem:
            botoes.append(f'<a class="cl-btn cl-btn-primary" href="{link_origem}" target="_blank">Baixar Edital</a>')
        if link_pncp:
            botoes.append(f'<a class="cl-btn cl-btn-secondary" href="{link_pncp}" target="_blank">Ver no PNCP</a>')
        botoes_html = "".join(botoes) if botoes else '<span style="color:#9CA3AF;font-size:0.85rem;">Sem links disponíveis</span>'

        modal_suffix = f" · {modalidade}" if modalidade else ""
        html = (
            f'<div class="cl-edital-card {card_extra_class}">'
            f'<div class="cl-edital-header">'
            f'<div class="cl-hdr-left">'
            f'<div class="cl-edital-num">{idx}</div>'
            f'{icones_hdr_html}'
            f'</div>'
            f'<div class="cl-hdr-right">'
            f'{tag_urgente_html}{tag_ia_html}{tag_score_html}'
            f'</div>'
            f'</div>'
            f'<div class="cl-edital-body">'
            f'<div class="cl-edital-objeto"><b>Objeto:</b> {origem_tag_html}{objeto[:400]}</div>'
            f'{kw_html}'
            f'{item_enc_html}'
            f'{ia_prod_html}'
            f'{local_obra_html}'
            f'<div class="cl-edital-meta">'
            f'<div><b>Abertura:</b> {data_ab}</div>'
            f'<div><b>Órgão:</b> <span class="cl-orgao">{orgao}</span></div>'
            f'<div><b>Cidade:</b> {cidade}</div>'
            f'<div><b>Edital:</b> {num_edital}{modal_suffix}</div>'
            f'<div><b>Valor estimado:</b> <span class="cl-valor">{valor}</span></div>'
            f'<div><b>Origem:</b> {src_chip_html} · '
            f'<span class="cl-tag {tag_material_class}">{material}</span> '
            f'<span class="cl-tag" style="background:#F3F4F6;color:#1F2937;">{dist_str} · {filial}</span>'
            f'</div>'
            f'</div>'
            f'</div>'
            f'<div class="cl-edital-actions">'
            f'<span class="cl-edital-actions-label">Ações:</span>'
            f'{botoes_html}'
            f'</div>'
            f'</div>'
        )
        st.markdown(html, unsafe_allow_html=True)

        # ----- Ver mais informações (estilo ConLicitação) -----
        with st.expander("🔎 Ver mais informações da licitação"):
            _enc = _fmt_data(d.get("data_encerramento"))
            st.markdown(f"**Objeto completo:** {objeto}")
            cm1, cm2 = st.columns(2)
            cm1.markdown(f"**Abertura das propostas:** {data_ab}")
            cm2.markdown(f"**Encerramento:** {_enc or '—'}")
            cm1.markdown(f"**Modalidade:** {modalidade or '—'}")
            cm2.markdown(f"**Confiança:** {d.get('score_label') or '—'}")
            if keyword_trig:
                st.markdown(f"**Palavra-chave que casou:** _{keyword_trig}_")
            if itens_enc:
                st.markdown(f"**Item encontrado no edital:** {itens_enc[:300]}")
            _links = []
            if link_origem:
                _links.append(f"[Baixar edital (origem)]({link_origem})")
            if link_pncp:
                _links.append(f"[Ver no PNCP]({link_pncp})")
            if _links:
                st.markdown(" · ".join(_links))

        # ----- Ações: uma fileira compacta de botões pequenos (estilo ConLicitação) -----
        bcol1, bcol2, bcol3, _bspace = st.columns([1.2, 1.3, 1.1, 5])
        with bcol1:
            _lbl_lido = "✓ Lido" if ja_lido else "Lido"
            if st.button(_lbl_lido, key=f"lido_{idx}_{num_controle}", width='stretch',
                         help="Marcar/desmarcar como revisada"):
                _desmarcar_lido(num_controle) if ja_lido else _marcar_lido(num_controle)
                st.rerun()
        with bcol2:
            _lbl_fav = "★ Favorita" if ja_fav else "☆ Favoritar"
            if st.button(_lbl_fav, key=f"fav_{idx}_{num_controle}", width='stretch',
                         help="Marcar/desmarcar favorita"):
                _desmarcar_fav(num_controle) if ja_fav else _marcar_fav(num_controle)
                st.rerun()
        _ia_click = False
        with bcol3:
            _ia_click = st.button("IA", key=f"ia_{idx}_{num_edital}",
                                  width='stretch',
                                  help="Gemini lê o PDF e extrai produto, quantidade, prazo e recomendação")
        if _ia_click:
            with st.spinner("Baixando edital e consultando Gemini..."):
                resumo = _resumir_edital(
                    str(d.get("numero_controle_pncp") or ""),
                    link_origem, link_pncp,
                )
            if resumo:
                st.session_state[f"resumo_{num_edital}"] = resumo

        # Exibe resumo IA (se disponível)
        resumo_cached = st.session_state.get(f"resumo_{num_edital}")
        if resumo_cached:
            rec = str(resumo_cached.get("recomendacao") or "").upper()
            rec_class = {"PARTICIPAR": "cl-ia-rec-participar",
                         "ANALISAR":   "cl-ia-rec-analisar",
                         "DESCARTAR":  "cl-ia-rec-descartar"}.get(rec, "cl-ia-rec-analisar")
            exigs = resumo_cached.get("exigencias_tecnicas") or []
            exigs_str = " · ".join(exigs[:4]) if isinstance(exigs, list) else str(exigs)
            st.markdown(
                f'<div class="cl-ia-box">'
                f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.4rem;">'
                f'<span style="font-weight:700;font-size:0.88rem;color:#3A4149;">Análise IA</span>'
                f'<span class="{rec_class}">{rec}</span>'
                f'</div>'
                f'<div style="font-size:0.85rem;color:#1F2937;margin-bottom:0.25rem;">'
                f'<b>Produto:</b> {resumo_cached.get("produto_exato","—")} · '
                f'<b>Qtde:</b> {resumo_cached.get("quantidade","—")}</div>'
                f'<div style="font-size:0.82rem;color:#374151;margin-bottom:0.25rem;">'
                f'<b>Prazo:</b> {resumo_cached.get("prazo_entrega","—")} · '
                f'<b>Local:</b> {resumo_cached.get("local_entrega","—")}</div>'
                f'<div style="font-size:0.8rem;color:#6B7280;">'
                f'<b>Exigências:</b> {exigs_str or "—"}</div>'
                f'<div style="font-size:0.78rem;color:#374151;margin-top:0.3rem;font-style:italic;">'
                f'{resumo_cached.get("justificativa","")}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

        st.markdown('<div style="border-bottom:1px solid #EEF1F4;margin:0.15rem 0 0.7rem 0;"></div>', unsafe_allow_html=True)

    # ----- Navegação de páginas -----
    if n_paginas > 1:
        st.markdown('<div class="cl-divider"></div>', unsafe_allow_html=True)
        nav1, nav2, nav3 = st.columns([1, 2, 1])
        with nav1:
            if pagina > 0:
                if st.button("← Anterior", width='stretch'):
                    st.session_state["page_editais"] = pagina - 1
                    st.rerun()
        with nav2:
            st.markdown(
                f'<p style="text-align:center;color:#6B7280;font-size:0.85rem;margin:0.5rem 0;">'
                f'Página <b>{pagina + 1}</b> de <b>{n_paginas}</b></p>',
                unsafe_allow_html=True,
            )
        with nav3:
            if pagina < n_paginas - 1:
                if st.button("Próxima →", width='stretch'):
                    st.session_state["page_editais"] = pagina + 1
                    st.rerun()


def _aba_filiais(fil: pd.DataFrame) -> None:
    st.subheader("Cobertura Operacional")
    if fil.empty:
        st.info("Nenhuma filial cadastrada.")
        return

    c1, c2, c3 = st.columns(3)
    c1.markdown(_card("Usinas", f"{(fil['tipo']=='usina').sum()}"), unsafe_allow_html=True)
    c2.markdown(_card("Pedreiras", f"{(fil['tipo']=='pedreira').sum()}"), unsafe_allow_html=True)
    c3.markdown(_card("Estados", f"{fil['uf'].nunique()}"), unsafe_allow_html=True)

    st.markdown('<div class="cl-divider"></div>', unsafe_allow_html=True)

    for uf, grp in fil.groupby("uf"):
        st.markdown(f"##### {uf} — {len(grp)} unidades")
        cols = st.columns(3)
        for i, (_, row) in enumerate(grp.iterrows()):
            with cols[i % 3]:
                badge = "cl-tag-pedreira" if row["tipo"] == "pedreira" else "cl-tag-usina"
                st.markdown(
                    f'<div class="cl-card">'
                    f'<div class="cl-card-title">{row.get("sigla","")} · {row["municipio"]}</div>'
                    f'<div style="font-weight:600;color:var(--cl-primary);font-size:0.95rem;">{row["nome"]}</div>'
                    f'<div style="margin-top:0.4rem;"><span class="cl-tag {badge}">{row["tipo"]}</span></div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )


def _aba_diario(ed: pd.DataFrame, ultima: datetime | None) -> None:
    st.subheader("Diário de Execução")

    # ── Seção 1: Histórico de Runs (aba Execucoes) ─────────────────────────
    exec_df = _carregar_execucoes()

    if not exec_df.empty:
        ultima_exec = exec_df["data_execucao"].max() if "data_execucao" in exec_df.columns else None
        if ultima_exec and pd.notna(ultima_exec):
            st.success(f"Última execução do scraper: **{ultima_exec:%d/%m/%Y %H:%M}**")

        # KPIs da última execução
        ult = exec_df.sort_values("data_execucao").iloc[-1]
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Editais brutos",  int(ult["brutos"]        if "brutos"        in ult.index else 0))
        c2.metric("Após keywords",   int(ult["apos_keyword"]  if "apos_keyword"  in ult.index else 0))
        c3.metric("Após geo",        int(ult["apos_geo"]      if "apos_geo"      in ult.index else 0))
        c4.metric("Novos gravados",  int(ult["novos"]         if "novos"         in ult.index else 0))
        c5.metric("Tempo (s)", f"{float(ult['tempo_s'] if 'tempo_s' in ult.index else 0):.0f}s")

        st.markdown("##### Histórico de execuções (últimas 50)")
        # Formatar tabela para exibição
        disp = exec_df.sort_values("data_execucao", ascending=False).head(50).copy()
        if "data_execucao" in disp.columns:
            disp["data_execucao"] = disp["data_execucao"].dt.strftime("%d/%m/%Y %H:%M")
        disp = disp.rename(columns={
            "data_execucao": "Data/Hora",
            "status":        "Status",
            "brutos":        "Brutos",
            "apos_keyword":  "Keyword",
            "apos_geo":      "Geo",
            "novos":         "Novos",
            "tempo_s":       "Tempo(s)",
            "erro_msg":      "Erro",
        })
        # Exibe colunas relevantes na ordem certa
        cols_disp = [c for c in ["Data/Hora", "Status", "Brutos", "Keyword", "Geo", "Novos", "Tempo(s)", "Erro"] if c in disp.columns]
        st.dataframe(disp[cols_disp], width='stretch', hide_index=True)

        # Gráfico de funil ao longo do tempo
        if {"brutos", "apos_keyword", "apos_geo", "novos"}.issubset(exec_df.columns) and len(exec_df) > 1:
            st.markdown("##### Funil do scraper ao longo do tempo")
            funil_chart = exec_df.sort_values("data_execucao").set_index("data_execucao")[
                ["brutos", "apos_keyword", "apos_geo", "novos"]
            ]
            st.line_chart(funil_chart)
    else:
        if ultima:
            st.success(f"Última execução do scraper: **{ultima:%d/%m/%Y %H:%M}**")
        else:
            st.warning("Scraper ainda não foi executado.")
        st.info("Histórico de execuções não disponível ainda. Será preenchido na próxima execução do scraper.")

    # ── Seção 2: Editais por dia ────────────────────────────────────────────
    if ed.empty or "data_execucao" not in ed.columns:
        return

    st.markdown("---")
    por_dia = ed.groupby(ed["data_execucao"].dt.date).agg(
        editais=("numero_controle_pncp", "count"),
        valor=("valor_estimado", "sum"),
    ).reset_index().rename(columns={"data_execucao": "data"})
    por_dia["valor_fmt"] = por_dia["valor"].apply(_money)

    st.markdown("##### Editais qualificados por dia")
    st.bar_chart(por_dia.set_index("data")["editais"])
    st.markdown("##### Valor estimado por dia")
    st.dataframe(por_dia[["data", "editais", "valor_fmt"]], width='stretch', hide_index=True)


# ===== Main =====
def main() -> None:
    if not _check_login():
        return

    fil, ed, ultima = _carregar_dados()
    _sidebar_acoes()

    # Usa a data real da última execução do scraper (aba Execucoes),
    # com fallback para a data do último edital encontrado.
    exec_df = _carregar_execucoes()
    if not exec_df.empty and "data_execucao" in exec_df.columns:
        ultima_exec = exec_df["data_execucao"].max()
        ultima_str = ultima_exec.strftime("%d/%m/%Y %H:%M") if pd.notna(ultima_exec) else "—"
        _ult_row = exec_df.sort_values("data_execucao").iloc[-1]
        novos_ultima = int(_ult_row["novos"] if "novos" in _ult_row.index else 0)
        status_txt = f"{novos_ultima} novo(s)" if novos_ultima else "0 novos"
        sub_exec = f'<div class="cl-header-sub" style="font-size:0.75rem;opacity:0.75;">{status_txt} na última execução</div>'
    else:
        ultima_str = ultima.strftime("%d/%m/%Y %H:%M") if ultima else "—"
        sub_exec = ""

    if LOGO_PATH.exists():
        st.image(str(LOGO_PATH), width=240)

    st.markdown(
        f"""
        <div class="cl-header-bar">
            <div>
                <div class="cl-header-title">Concrelagos Intelligence Hub</div>
                <div class="cl-header-sub">Rastreador autônomo de licitações públicas — PNCP</div>
            </div>
            <div style="text-align:right;">
                <div class="cl-header-sub">Última varredura</div>
                <div style="font-weight:600;">{ultima_str}</div>
                {sub_exec}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Modelo ConLicitação: Dashboard (visão executiva) → Boletim (caixa de entrada)
    # → Mapa (preservado do site antigo) → Diário (saúde do scraper).
    tab0, tab1, tab2, tab3 = st.tabs(["Dashboard", "Boletim", "Mapa", "Diário"])
    with tab0:
        _aba_dashboard(ed, fil)
    with tab1:
        _aba_editais(ed)
    with tab2:
        _aba_mapa(ed, fil)
    with tab3:
        _aba_diario(ed, ultima)


if __name__ == "__main__":
    main()
