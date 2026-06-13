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

# Sol da Concrelagos (SVG inline, dourado) — substitui o "O" de CONCRELAGOS nos títulos,
# como no logotipo oficial. Dimensionado como letra (0.95em) e alinhado à linha do texto.
_SOL_RAIOS = "".join(
    f'<line x1="12" y1="1.6" x2="12" y2="5.2" transform="rotate({a} 12 12)" />'
    for a in range(0, 360, 30)
)
_SOL_SVG = (
    '<svg viewBox="0 0 24 24" style="height:0.95em;width:0.95em;vertical-align:-0.1em;" '
    'xmlns="http://www.w3.org/2000/svg" aria-hidden="true">'
    '<circle cx="12" cy="12" r="4.7" fill="none" stroke="#C28E2C" stroke-width="2.6"/>'
    f'<g stroke="#C28E2C" stroke-width="2.3" stroke-linecap="round">{_SOL_RAIOS}</g>'
    '</svg>'
)
_TITULO_SOL = (
    f'CONCRELAG{_SOL_SVG}S '
    '<span style="opacity:0.88;font-size:0.82em;">INTELLIGENCE HUB</span>'
)

# ===== Styling corporativo =====
st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Michroma&family=Inter:wght@400;500;600;700&display=swap');
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
        --cl-grad-dark: linear-gradient(135deg, #343B43 0%, #23282E 100%);
        --cl-grad-gold: linear-gradient(135deg, #C28E2C 0%, #A9781F 100%);
        --cl-shadow-1: 0 1px 3px rgba(35,40,46,0.07), 0 1px 2px rgba(35,40,46,0.04);
        --cl-shadow-2: 0 10px 28px rgba(35,40,46,0.13), 0 3px 8px rgba(35,40,46,0.06);
        --cl-glow-gold: 0 4px 16px rgba(194,142,44,0.32);
        --cl-radius: 13px;
        --cl-font-display: 'Michroma', 'Segoe UI', sans-serif;  /* peso único 400, fonte larga */
        --cl-font-body: 'Inter', -apple-system, 'Segoe UI', sans-serif;
    }
    /* Fonte do corpo — NUNCA em span genérico (quebraria a fonte de ícones do
       Streamlit, fazendo ícones virarem texto tipo "double_arrow_right"). */
    .stApp, .stApp p, .stApp label, .stApp input, .stApp textarea,
    .stMarkdown { font-family: var(--cl-font-body); }
    /* Restaura a fonte dos ícones Material em qualquer contexto */
    [data-testid="stIconMaterial"], .material-symbols-rounded,
    [data-testid="stExpanderToggleIcon"], [data-testid^="stIcon"] {
        font-family: 'Material Symbols Rounded' !important;
    }
    .main { background-color: var(--cl-bg); }
    .stApp header { background-color: var(--cl-header); }
    .stApp header * { color: white !important; }
    .block-container { padding-top: 1.2rem; padding-bottom: 2rem; }
    /* Tipografia display (futurista — Michroma é larga e tem peso único 400) */
    h1, h2, h3, h4, h5 { color: var(--cl-primary); font-weight: 400;
        font-family: var(--cl-font-display); letter-spacing: 0.01em; }
    h1 { font-size: 1.5rem; } h2 { font-size: 1.25rem; } h3 { font-size: 1.05rem; }
    h4, h5 { font-size: 0.92rem; }
    .cl-serif { font-family: var(--cl-font-display); font-weight: 400; }
    .cl-card {
        background: var(--cl-card);
        border: 1px solid #ECEEF1;
        border-left: 4px solid var(--cl-accent);
        border-radius: var(--cl-radius);
        padding: 1.1rem 1.25rem;
        margin-bottom: 0.75rem;
        box-shadow: var(--cl-shadow-1);
        transition: transform 0.22s cubic-bezier(.2,.8,.2,1), box-shadow 0.22s ease, border-left-color 0.22s ease;
    }
    .cl-card:hover { transform: translateY(-5px) scale(1.015);
        box-shadow: var(--cl-shadow-2), var(--cl-glow-gold);
        border-left-color: var(--cl-accent-d); }
    .cl-card-title {
        font-size: 0.72rem;
        text-transform: uppercase;
        color: var(--cl-muted);
        margin-bottom: 0.3rem;
        letter-spacing: 0.09em;
        font-weight: 600;
    }
    .cl-card-value {
        font-size: 1.45rem;
        font-weight: 400;
        color: var(--cl-accent-d);
        line-height: 1.15;
        font-family: var(--cl-font-display);
        letter-spacing: 0;
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
    .cl-header-title { font-size: 1.18rem; font-weight: 400; }
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
       DESIGN SYSTEM — futurista, profissional (grafite + dourado)
       ========================================================= */
    .cl-edital-card { border:1px solid #ECEEF1; border-radius:var(--cl-radius); box-shadow:var(--cl-shadow-1); margin-bottom:1.1rem; overflow:hidden;
        transition: transform 0.22s cubic-bezier(.2,.8,.2,1), box-shadow 0.22s ease; }
    .cl-edital-card:hover { transform: translateY(-4px) scale(1.008);
        box-shadow: var(--cl-shadow-2), var(--cl-glow-gold); }
    .cl-edital-header { background:var(--cl-grad-dark) !important; padding:0.55rem 0.95rem !important; }
    .cl-hdr-left { display:flex; align-items:center; gap:0.45rem; }
    .cl-hdr-right { display:flex; align-items:center; gap:0.45rem; }
    .cl-edital-num { background:var(--cl-grad-gold) !important; color:#fff !important; min-width:26px !important; height:26px !important; width:auto !important; padding:0 0.5rem; border-radius:999px !important; font-size:0.8rem; font-family:var(--cl-font-display); font-weight:700; }
    .cl-hdr-icon { width:26px; height:26px; border-radius:50%; background:rgba(255,255,255,0.14); color:#fff; display:inline-flex; align-items:center; justify-content:center; font-size:0.82rem; transition: background 0.15s ease; }
    .cl-hdr-icon.on-fav  { background:#F59E0B; color:#fff; }
    .cl-hdr-icon.on-lido { background:var(--cl-accent); color:#fff; }
    .cl-edital-body { padding:0.95rem 1.15rem !important; }
    .cl-edital-objeto { color:#111827 !important; font-size:0.95rem; line-height:1.5; }
    .cl-edital-meta { gap:0.45rem 1.6rem !important; font-size:0.86rem !important; color:#374151 !important; }
    .cl-edital-meta b { color:#9CA3AF !important; font-weight:600 !important; text-transform:uppercase; font-size:0.72rem; letter-spacing:0.05em; }
    .cl-valor { color:var(--cl-accent-d); font-weight:700; font-size:1.08rem; font-family:var(--cl-font-display); }
    .cl-orgao { color:#3A4149; font-weight:600; }
    .cl-src-chip { display:inline-block; border:1px solid #E2E5E9; border-radius:999px; padding:0.1rem 0.55rem; font-size:0.68rem; font-weight:700; color:#374151; background:#F9FAFB; letter-spacing:0.04em; }
    .cl-origem-tag { background:#FBF3E3 !important; color:#8A6A1E !important; padding:0.1rem 0.5rem !important; border-radius:999px !important; }
    .cl-edital-actions { background:#FAFBFC !important; border-top:1px solid #EFF1F4 !important; padding:0.55rem 1.15rem !important; }
    .cl-edital-actions-label { font-weight:600 !important; }
    .cl-btn { border-radius:9px !important; transition: opacity 0.15s ease, transform 0.15s ease; }
    .cl-btn:hover { transform: translateY(-1px); }
    .cl-btn-primary  { background:var(--cl-grad-dark) !important; color:#fff !important; }
    .cl-btn-secondary{ background:#fff !important; color:#3A4149 !important; border:1px solid #CDD2D8 !important; }
    .cl-boletim-dia { background:#FBF3E3 !important; color:#8A6A1E !important; border-left:4px solid #C28E2C; border-radius:10px;
        font-family:var(--cl-font-display); font-weight:600; letter-spacing:0.01em; }
    .cl-header-bar { background:var(--cl-grad-dark) !important; border-radius:var(--cl-radius) !important;
        border-bottom:3px solid var(--cl-accent) !important; margin-top:0 !important;
        box-shadow: var(--cl-shadow-1), 0 14px 30px -18px rgba(194,142,44,0.45); }
    .cl-header-title { font-family:var(--cl-font-display); letter-spacing:0.02em; font-weight:400; }
    .cl-boletim-head { display:flex; align-items:baseline; gap:0.8rem; flex-wrap:wrap; border-bottom:2px solid #C28E2C; padding-bottom:0.5rem; margin:0.2rem 0 0.9rem 0; }
    .cl-boletim-head-title { font-size:1.15rem; font-weight:400; color:#3A4149; font-family:var(--cl-font-display); letter-spacing:0.02em; }
    .cl-boletim-head-sub { font-size:0.84rem; color:#6B7280; }
    /* Botões — pílulas grafite com tipografia display; primary = gradiente dourado c/ glow */
    .stButton button, [data-testid="stButton"] button, [data-testid="stBaseButton-secondary"] {
        border-radius:10px !important; font-weight:400 !important; font-size:0.72rem !important;
        font-family:var(--cl-font-display) !important; letter-spacing:0.03em !important;
        padding:0.32rem 0.75rem !important; min-height:0 !important;
        background:#3A4149 !important; border:1px solid #3A4149 !important;
        transition: transform 0.15s ease, box-shadow 0.15s ease, background 0.15s ease !important;
    }
    .stButton button *, [data-testid="stButton"] button * { color:#fff !important; }
    .stButton button:hover, [data-testid="stButton"] button:hover {
        background:#2E353D !important; border-color:#2E353D !important;
        transform: translateY(-1px); box-shadow: var(--cl-shadow-1); }
    [data-testid="stBaseButton-primary"], .stButton button[kind="primary"] {
        background:var(--cl-grad-gold) !important; border:1px solid #C28E2C !important;
        box-shadow: var(--cl-glow-gold) !important; }
    [data-testid="stBaseButton-primary"]:hover, .stButton button[kind="primary"]:hover {
        background:linear-gradient(135deg,#B5832700,#A9781F) #A9781F !important; border-color:#A9781F !important; }
    .stDownloadButton button, [data-testid="stDownloadButton"] button {
        background:var(--cl-grad-gold) !important; border:1px solid #C28E2C !important; border-radius:10px !important;
        font-weight:400 !important; font-size:0.72rem !important; font-family:var(--cl-font-display) !important;
        padding:0.32rem 0.75rem !important; box-shadow: var(--cl-glow-gold) !important;
    }
    .stDownloadButton button * { color:#fff !important; }
    /* Popover "Mais filtros" — contorno discreto */
    [data-testid="stPopover"] button { background:#fff !important; border:1px solid #CDD2D8 !important; box-shadow:none !important; }
    [data-testid="stPopover"] button * { color:#3A4149 !important; }
    .cl-total { color:#374151; font-size:0.95rem; padding-top:0.4rem; }
    /* Ritmo: cards juntos, ações coladas, expander limpo */
    .cl-edital-card { margin-bottom:0.35rem !important; }
    .cl-edital-actions { padding:0.5rem 1.1rem !important; }
    [data-testid="stExpander"] { border:1px solid #EFF1F4 !important; border-radius:12px !important; margin:0.1rem 0 0.15rem 0 !important; background:#fff; }
    [data-testid="stExpander"] summary { font-size:0.84rem !important; color:#3A4149 !important; font-weight:600; }
    [data-testid="stExpander"] summary:hover { color:var(--cl-accent-d) !important; }
    /* Inputs e selects — cantos suaves + foco dourado */
    [data-testid="stTextInput"] input, [data-baseweb="input"] input { font-family:var(--cl-font-body) !important; }
    [data-baseweb="input"], [data-baseweb="select"] > div { border-radius:10px !important; }
    [data-baseweb="input"]:focus-within, [data-baseweb="select"]:focus-within { border-color:var(--cl-accent) !important; }
    /* Sub-abas (Histórico) */
    [data-testid="stTabs"] button { font-family:var(--cl-font-display) !important; font-weight:600 !important; }
    [data-testid="stTabs"] button[aria-selected="true"] { color:var(--cl-accent-d) !important; }
    [data-baseweb="tab-highlight"] { background-color:var(--cl-accent) !important; }
    /* Métricas (Diário) */
    [data-testid="stMetricValue"] { font-family:var(--cl-font-display) !important; color:var(--cl-accent-d) !important; }
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
        f"""
        <div class="cl-header-bar" style="justify-content:center;text-align:center;">
            <div>
                <div class="cl-header-title">{_TITULO_SOL}</div>
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


@st.cache_data(ttl=300, show_spinner="Carregando triagem da IA...")
def _carregar_triagem_ia() -> pd.DataFrame:
    """Carrega a aba 'Triagem IA' (cache das decisões do portão Gemini).

    Deduplica pela ÚLTIMA linha por numero_controle_pncp — um 'verificado'/'negado'
    posterior sobrepõe um 'pendente' anterior (mesma lógica de scraper._carregar_triagem).
    Retorna DataFrame vazio se a aba estiver ausente/vazia ou em qualquer erro.
    """
    try:
        gc = _build_gspread_client()
        sh = gc.open_by_key(_get_sheet_id())
        ws = sh.worksheet("Triagem IA")
        vals = ws.get_all_values()
        if not vals or len(vals) < 2:
            return pd.DataFrame()
        header = [h.strip() for h in vals[0]]
        rows = [dict(zip(header, r)) for r in vals[1:] if any(c.strip() for c in r)]
        df = pd.DataFrame(rows)
        if df.empty:
            return pd.DataFrame()
        # Dedup pela ÚLTIMA ocorrência de cada numero_controle_pncp
        if "numero_controle_pncp" in df.columns:
            df = df.drop_duplicates(subset=["numero_controle_pncp"], keep="last")
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
                # 40 páginas: as exigências de habilitação ficam no meio/fim do edital
                with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
                    txt = "\n".join((p.extract_text() or "") for p in pdf.pages[:40]).strip()
                if len(txt) >= 100:
                    return txt
            else:
                # HTML → remove scripts/estilos/tags e normaliza espaços
                html = resp.text
                html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
                txt = re.sub(r"<[^>]+>", " ", html)
                txt = re.sub(r"\s+", " ", txt).strip()
                if len(txt) >= 200:
                    return txt[:40000]
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
        _dab = pd.to_datetime(df["data_abertura"], errors="coerce").dt.date
        df = df[(_dab >= filtros["dt_de"]) & (_dab <= filtros["dt_ate"])]
    if filtros.get("ocultar_lidos") and "numero_controle_pncp" in df.columns:
        lidos = _lidos_set()
        if lidos:
            df = df[~df["numero_controle_pncp"].isin(lidos)]
    return df


# ===== Abas =====
def _ir_boletim(conf=None, mat=None, extra=None) -> None:
    """Pré-aplica filtros e navega para o Boletim (usado pelos KPIs clicáveis)."""
    if conf is not None:
        st.session_state["f_conf"] = list(conf)
    if mat is not None:
        st.session_state["f_mat"] = list(mat)
    st.session_state["f_extra"] = extra
    st.session_state["pagina"] = "Boletim"
    st.rerun()


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

    _score_num = pd.to_numeric(ed.get("score", pd.Series(dtype="float")), errors="coerce")
    _rel = _score_num >= 2   # relevantes = CERTO + PROVÁVEL
    _TODOS_MAT = ["brita", "concreto"]

    # Aberto = pregão ainda não ocorreu (mesma regra do filtro "Abertas" do Boletim,
    # p/ os números do Dashboard baterem com o que o Boletim mostra ao clicar).
    _bcol = ed["data_encerramento"] if ("data_encerramento" in ed.columns and ed["data_encerramento"].notna().any()) else ed.get("data_abertura")
    if _bcol is not None:
        _bz = pd.to_datetime(_bcol, errors="coerce", utc=True)
        _ag = pd.Timestamp.now(tz="UTC")
        _aberto = _bz.isna() | (_bz >= _ag)
    else:
        _aberto = pd.Series(True, index=ed.index)

    # Balde 1 — Novas hoje (relevantes e abertas, pela data de execução do scraper)
    if "data_execucao" in ed.columns and ed["data_execucao"].notna().any():
        _hoje_mask = pd.to_datetime(ed["data_execucao"], errors="coerce").dt.normalize() == hoje
        novas_hoje = int((_hoje_mask & _rel & _aberto).sum())
    else:
        novas_hoje = 0

    # Balde 2 — CERTO (concreto/brita confirmado, aberto)
    certo = int(((_score_num == 3) & _aberto).sum())

    # Balde 3 — Brita (qualquer confiança, aberto)
    brita = int(((ed["material"] == "brita") & _aberto).sum()) if "material" in ed.columns else 0

    # Balde 4 — Vence ≤7 dias (relevantes; encerramento, fallback abertura)
    _base = ed["data_encerramento"] if ("data_encerramento" in ed.columns and ed["data_encerramento"].notna().any()) else ed.get("data_abertura")
    if _base is not None:
        _b = pd.to_datetime(_base, errors="coerce")
        vence_7d = int((((_b >= agora) & (_b <= agora + pd.Timedelta(days=7))) & _rel).sum())
    else:
        vence_7d = 0

    st.caption("Clique em **Ver** num card para abrir esses editais no Boletim, já filtrados.")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(_card("Novas hoje", f"{novas_hoje}"), unsafe_allow_html=True)
        if st.button("Ver →", key="kpi_novas", width='stretch'):
            _ir_boletim(conf=["CERTO", "PROVÁVEL"], mat=_TODOS_MAT, extra="hoje")
    with c2:
        st.markdown(_card("CERTO (concreto/brita)", f"{certo}"), unsafe_allow_html=True)
        if st.button("Ver →", key="kpi_certo", width='stretch'):
            _ir_boletim(conf=["CERTO"], mat=_TODOS_MAT, extra=None)
    with c3:
        st.markdown(_card("Brita", f"{brita}"), unsafe_allow_html=True)
        if st.button("Ver →", key="kpi_brita", width='stretch'):
            _ir_boletim(conf=["CERTO", "PROVÁVEL", "POSSÍVEL"], mat=["brita"], extra=None)
    with c4:
        st.markdown(_card("Vence ≤ 7 dias", f"{vence_7d}"), unsafe_allow_html=True)
        if st.button("Ver →", key="kpi_vence", width='stretch'):
            _ir_boletim(conf=["CERTO", "PROVÁVEL"], mat=_TODOS_MAT, extra="vence7")

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


def _secao_importar_conlicitacao() -> None:
    """Upload do boletim .xlsx do ConLicitação — qualquer pessoa do setor, qualquer máquina,
    via web. Aplica o filtro do Hub (só PE de concreto/brita) + geo, e grava no boletim.
    Reusa as funções do scraper; reimportar o mesmo boletim não duplica (gravar_em_sheets dedup)."""
    import io
    with st.expander("📥 Importar boletim do ConLicitação (.xlsx)"):
        st.caption("No ConLicitação, abra o boletim, clique em **Gerar .xlsx** e suba o arquivo aqui. "
                   "O Hub mantém só os **Pregões Eletrônicos de concreto usinado/brita**, aplica geografia "
                   "(filial no mesmo estado) e a inteligência. Funciona de qualquer máquina; "
                   "subir o mesmo boletim de novo não duplica.")
        up = st.file_uploader("Boletim .xlsx exportado do ConLicitação", type=["xlsx"], key="conlic_xlsx_up")
        if up is not None and st.button("Importar boletim", type="primary", key="conlic_import_btn"):
            try:
                import scraper as _sc
            except Exception as exc:
                st.error(f"Não consegui carregar o módulo de coleta: {exc}")
                return
            with st.spinner("Lendo o boletim, filtrando e qualificando…"):
                try:
                    rows = _sc._parse_boletim_xlsx(io.BytesIO(up.getvalue()))
                except ValueError:
                    st.error("Esse arquivo não parece um boletim do ConLicitação (colunas não reconhecidas).")
                    return
                except Exception as exc:
                    st.error(f"Não consegui ler o .xlsx: {exc}")
                    return
                editais = [e for e in (_sc._normalizar_conlicitacao(r) for r in rows) if e]
                editais = _sc._filtrar_pe_conlicitacao(editais)
                if not editais:
                    st.warning(f"{len(rows)} licitações no boletim, mas **nenhuma é Pregão Eletrônico de "
                               "concreto usinado/brita** — nada a importar (o resto é asfalto, material geral, "
                               "outra modalidade, etc.).")
                    return
                sid = _get_sheet_id()
                filiais = _sc.carregar_filiais(sid)
                qualificados = _sc.qualificar_por_distancia(editais, filiais)
                novos = _sc.gravar_em_sheets(qualificados, sid)
            cert = sum(1 for e in qualificados if int(e.get("score") or 0) >= 2)
            poss = len(qualificados) - cert
            st.success(
                f"Boletim importado: **{len(rows)}** licitações → **{len(editais)}** PE concreto/brita → "
                f"**{len(qualificados)}** atendidas por filial no mesmo estado → **{len(novos)} novas** no boletim "
                f"({cert} CERTO · {poss} POSSÍVEL)."
            )
            if novos:
                st.cache_data.clear()
                st.info("Clique em **Boletim** de novo (ou recarregue) para vê-las na lista.")


def _aba_editais(ed: pd.DataFrame) -> None:
    _secao_importar_conlicitacao()
    if ed.empty:
        st.subheader("Boletim de Licitações")
        st.info("Nenhuma licitação ainda. Importe um boletim do ConLicitação acima, "
                "ou rode `python scraper.py` para popular pelo PNCP.")
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
        if "f_mat" not in st.session_state:
            st.session_state["f_mat"] = mats
        st.session_state["f_mat"] = [m for m in st.session_state["f_mat"] if m in mats] or mats
        mat_sel = st.multiselect("Material", mats, key="f_mat")
        _score_op = {"CERTO": 3, "PROVÁVEL": 2, "POSSÍVEL": 1}
        # Padrão: só CERTO+PROVÁVEL (POSSÍVEL cru não chega mais). Os KPIs do
        # Dashboard pré-ajustam este filtro via session_state["f_conf"].
        if "f_conf" not in st.session_state:
            st.session_state["f_conf"] = ["CERTO", "PROVÁVEL"]
        st.session_state["f_conf"] = [l for l in st.session_state["f_conf"] if l in _score_op] or ["CERTO", "PROVÁVEL"]
        _sc_labels = st.multiselect("Confiança", list(_score_op), key="f_conf")
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

    # Filtro vindo de um card do Dashboard ("Novas hoje" / "Vence ≤ 7 dias")
    _extra = st.session_state.get("f_extra")
    if _extra:
        _ag = pd.Timestamp.now(tz="UTC")
        _lbl = ""
        if _extra == "hoje" and "data_execucao" in df.columns:
            _dx = pd.to_datetime(df["data_execucao"], errors="coerce").dt.date
            df = df[_dx == pd.Timestamp.now().date()]
            _lbl = "novas de hoje"
        elif _extra == "vence7":
            _bc = df["data_encerramento"] if ("data_encerramento" in df.columns and df["data_encerramento"].notna().any()) else df.get("data_abertura")
            if _bc is not None:
                _bb = pd.to_datetime(_bc, errors="coerce", utc=True)
                df = df[(_bb >= _ag) & (_bb <= _ag + pd.Timedelta(days=7))]
            _lbl = "vencem em ≤ 7 dias"
        if _lbl:
            _ex1, _ex2 = st.columns([4, 1])
            _ex1.info(f"Filtro do Dashboard: **{_lbl}**")
            if _ex2.button("Limpar filtro", key="limpar_extra", width='stretch'):
                st.session_state["f_extra"] = None
                st.rerun()

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
            st.caption("Excel indisponível")
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
        dist = pd.to_numeric(d.get("distancia_km"), errors="coerce")
        dist_str = f"{dist:.0f} km" if pd.notna(dist) else "—"
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


def _painel_saude_cobertura() -> None:
    """Painel de SAÚDE DA COBERTURA no topo do Diário — torna visíveis as duas
    falhas silenciosas: (1) coleta que voltou vazia (PNCP instável) e (2) obras
    genéricas (score=1) seguradas no portão da IA aguardando a cota do Gemini."""
    st.markdown("#### Saúde da cobertura")

    # ── (1) Alerta de coleta vazia (aba Execucoes) ──────────────────────────
    exec_df = _carregar_execucoes()  # @st.cache_data — chamar 2x não custa I/O extra
    if not exec_df.empty:
        # Última execução = maior data_execucao (com fallback para a última linha)
        if "data_execucao" in exec_df.columns and exec_df["data_execucao"].notna().any():
            ult_exec = exec_df.sort_values("data_execucao").iloc[-1]
        else:
            ult_exec = exec_df.iloc[-1]

        status_ult = str(ult_exec.get("status", "")).strip().lower()
        brutos_ult = pd.to_numeric(ult_exec.get("brutos", 0), errors="coerce")
        brutos_ult = 0 if pd.isna(brutos_ult) else int(brutos_ult)

        if status_ult == "alerta" or brutos_ult == 0:
            # Quantas das últimas 10 execuções voltaram vazias
            recentes = exec_df.copy()
            if "data_execucao" in recentes.columns:
                recentes = recentes.sort_values("data_execucao")
            recentes = recentes.tail(10)
            st_recentes = recentes.get("status", pd.Series(dtype=str)).astype(str).str.strip().str.lower()
            br_recentes = pd.to_numeric(
                recentes.get("brutos", pd.Series([0] * len(recentes), index=recentes.index)),
                errors="coerce",
            ).fillna(0)
            vazias = int(((st_recentes == "alerta") | (br_recentes == 0)).sum())

            st.error(
                "Última coleta voltou vazia (PNCP instável) — a janela pode não ter sido "
                "coberta. A próxima rodada re-tenta (janela de 3 dias da sobreposição).\n\n"
                f"**{vazias} das últimas {len(recentes)} execuções** voltaram vazias."
            )

    # ── (2) Card: obras aguardando confirmação da IA (aba Triagem IA) ────────
    triagem_df = _carregar_triagem_ia()
    n_pendentes = 0
    if not triagem_df.empty and "status" in triagem_df.columns:
        _st = triagem_df["status"].astype(str).str.strip().str.lower()
        n_pendentes = int((_st == "pendente").sum())

    cc1, cc2 = st.columns([1, 3])
    cc1.markdown(_card("Aguardando IA (cota)", f"{n_pendentes}"), unsafe_allow_html=True)
    cc2.caption(
        "Obras genéricas (score = 1) seguradas até a cota gratuita do Gemini voltar. "
        "Se esse número crescer, elas podem expirar antes de entrar no boletim — "
        "considerar um backfill manual."
    )

    st.markdown('<div class="cl-divider"></div>', unsafe_allow_html=True)


def _aba_diario(ed: pd.DataFrame, ultima: datetime | None) -> None:
    st.subheader("Diário de Execução")

    # ── Painel de Saúde da Cobertura (topo) ────────────────────────────────
    _painel_saude_cobertura()

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


# =========================================================================
# HISTÓRICO — espelho da planilha comercial (PREGOES / GANHAS / ADITIVOS)
# Site → planilha: formulário (append imediato). Planilha → site: cache 60s.
# =========================================================================
HISTORICO_SHEET_ID = "1FjmN8EDKQRcBflL7VOp7MzB6PeKNO0hcXLUUAoLbBbg"
_SA_EMAIL = "scraper-bot@concrelagos-hub.iam.gserviceaccount.com"


def _num_br_solto(v) -> float:
    """Parse tolerante p/ números da planilha comercial ('1.125', 'R$ 202.300,00')."""
    s = str(v or "").strip()
    s = s.replace("R$", "").replace(" ", "").replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0


@st.cache_data(ttl=60, show_spinner="Carregando histórico...")
def _carregar_historico() -> dict:
    """{titulo_aba: (header_original, header_limpo, linhas[dict])} da planilha comercial."""
    gc = _build_gspread_client()
    sh = gc.open_by_key(HISTORICO_SHEET_ID)
    out: dict = {}
    for ws in sh.worksheets():
        vals = ws.get_all_values()
        if not vals:
            out[ws.title] = ([], [], [])
            continue
        header_orig = vals[0]
        header_limpo = [" ".join(str(h).split()) for h in header_orig]
        linhas = []
        for r in vals[1:]:
            if not any(str(c).strip() for c in r):
                continue
            r = list(r) + [""] * (len(header_limpo) - len(r))
            linhas.append(dict(zip(header_limpo, r)))
        out[ws.title] = (header_orig, header_limpo, linhas)
    return out


def _adicionar_historico(aba_titulo: str, linha: list) -> bool:
    """Acrescenta um registro na aba da planilha comercial (site → planilha)."""
    try:
        gc = _build_gspread_client()
        ws = gc.open_by_key(HISTORICO_SHEET_ID).worksheet(aba_titulo)
        ws.append_row(linha, value_input_option="USER_ENTERED")
        return True
    except Exception as exc:
        if "403" in str(exc) or "permission" in str(exc).lower():
            st.error(
                "Sem permissão de ESCRITA na planilha comercial. Abra a planilha no Google Sheets "
                f"→ Compartilhar → adicione {_SA_EMAIL} como **Editor** (hoje está só como leitor). "
                "Depois disso o formulário grava normalmente."
            )
        else:
            st.error(f"Erro ao gravar na planilha: {exc}")
        return False


def _aba_historico() -> None:
    st.markdown(
        '<div class="cl-boletim-head">'
        '<span class="cl-boletim-head-title">Histórico</span>'
        '<span class="cl-boletim-head-sub">Planilha comercial · pregões, contratos e aditivos (sincronizada)</span>'
        '</div>',
        unsafe_allow_html=True,
    )
    try:
        dados = _carregar_historico()
    except Exception as exc:
        st.error(
            "Não consegui abrir a planilha comercial. Confirme que ela está compartilhada "
            f"com {_SA_EMAIL} (como Editor). Detalhe técnico: {exc}"
        )
        return
    if not dados:
        st.info("A planilha comercial está vazia.")
        return

    # KPIs (aba PREGOES)
    _preg_t = next((t for t in dados if "PREG" in t.upper()), None)
    if _preg_t:
        _, hdr, linhas = dados[_preg_t]
        dfp = pd.DataFrame(linhas)
        res = dfp.get("RESULTADO", pd.Series(dtype=str)).astype(str).str.upper().str.strip()
        vit = int(res.str.startswith("VIT").sum())
        der = int(res.str.startswith("DERROT").sum())
        col_vol = next((c for c in dfp.columns if "CONTRATADO" in c.upper()), None)
        vol = sum(_num_br_solto(v) for v in dfp[col_vol]) if col_vol is not None else 0
        k1, k2, k3, k4 = st.columns(4)
        k1.markdown(_card("Pregões registrados", f"{len(dfp)}"), unsafe_allow_html=True)
        k2.markdown(_card("Vitórias", f"{vit}"), unsafe_allow_html=True)
        k3.markdown(_card("Derrotas", f"{der}"), unsafe_allow_html=True)
        k4.markdown(_card("Volume contratado (m³)", f"{int(vol):,}".replace(",", ".")), unsafe_allow_html=True)
        st.markdown('<div class="cl-divider"></div>', unsafe_allow_html=True)

        # ----- Gráficos (Altair, cores oficiais das logos de cada empresa) -----
        import altair as alt
        dfp["_res2"] = res.apply(lambda x: "VITÓRIA" if x.startswith("VIT")
                                 else ("DERROTA" if x.startswith("DERROT") else "OUTROS"))
        dfp["_ano"] = pd.to_numeric(dfp.get("ANO"), errors="coerce")
        dfp["_vol"] = dfp[col_vol].apply(_num_br_solto) if col_vol is not None else 0.0
        col_valor = next((c for c in dfp.columns if "VALOR TOTAL" in c.upper()), None)
        dfp["_valor"] = dfp[col_valor].apply(_num_br_solto) if col_valor is not None else 0.0

        # Empresa → nome curto + cor da logo (Apolo vermelho, Outeiro âmbar,
        # Bangu vinho, IPEPAM laranja, Imboassica grafite, Concrelagos dourado)
        def _map_emp(nome: str) -> str:
            n = str(nome or "").upper()
            if "CONCRELAGOS" in n: return "Concrelagos"
            if "IMBOASSICA" in n: return "Pedreira Imboassica"
            if "APOLO" in n: return "Apolo"
            if "OUTEIRO" in n: return "Pedreira Outeiro"
            if "BANGU" in n: return "Pedreira Bangu"
            if "IPEPAM" in n: return "IPEPAM"
            return "Outras"
        dfp["_emp"] = dfp.get("EMPRESA", pd.Series([""] * len(dfp))).astype(str).map(_map_emp)
        _EMP_DOMINIO = ["Concrelagos", "Pedreira Imboassica", "Apolo",
                        "Pedreira Outeiro", "Pedreira Bangu", "IPEPAM", "Outras"]
        _EMP_CORES = ["#C28E2C", "#3A4149", "#D32F2F", "#F2A900", "#8E2430", "#F39C12", "#9AA0A6"]
        _esc_emp = alt.Scale(domain=_EMP_DOMINIO, range=_EMP_CORES)
        # Eixo numérico em PT-BR compacto ("200 mil", "1,2 mi")
        _LBL_PTBR = ("datum.value >= 1000000 ? replace(format(datum.value/1000000, '.1f'), '.', ',') + ' mi' : "
                     "datum.value >= 1000 ? format(datum.value/1000, '.0f') + ' mil' : format(datum.value, '.0f')")

        ga = dfp.dropna(subset=["_ano"]).copy()
        if not ga.empty:
            ga["_ano"] = ga["_ano"].astype(int)

        gcol1, gcol2 = st.columns([1, 1.6])
        with gcol1:
            st.markdown("##### Resultado geral")
            src = dfp["_res2"].value_counts().reset_index()
            src.columns = ["resultado", "qtd"]
            taxa = (vit / max(vit + der, 1)) * 100
            donut = alt.Chart(src).mark_arc(innerRadius=62, cornerRadius=4).encode(
                theta=alt.Theta("qtd:Q"),
                color=alt.Color("resultado:N",
                                scale=alt.Scale(domain=["VITÓRIA", "DERROTA", "OUTROS"],
                                                range=["#C28E2C", "#3A4149", "#D4D8DD"]),
                                legend=alt.Legend(orient="bottom", title=None)),
                tooltip=["resultado:N", "qtd:Q"],
            ).properties(height=250)
            _ctr = pd.DataFrame({"t": [f"{taxa:.0f}%"]})
            centro = alt.Chart(_ctr).mark_text(fontSize=30, fontWeight=700,
                                               color="#A9781F").encode(text="t:N")
            sub = alt.Chart(pd.DataFrame({"t": ["de vitória"]})).mark_text(
                dy=24, fontSize=12, color="#6B7280").encode(text="t:N")
            st.altair_chart(donut + centro + sub, width='stretch')
        with gcol2:
            st.markdown("##### Disputados × vitórias por ano")
            if not ga.empty:
                agg = ga.groupby("_ano").agg(
                    Disputados=("_res2", "size"),
                    Vitorias=("_res2", lambda s2: int((s2 == "VITÓRIA").sum())),
                ).reset_index().rename(columns={"Vitorias": "Vitórias"})
                longf = agg.melt("_ano", var_name="série", value_name="qtd")
                barras = alt.Chart(longf).mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
                    x=alt.X("_ano:O", title=None, axis=alt.Axis(labelAngle=0)),
                    xOffset="série:N",
                    y=alt.Y("qtd:Q", title=None),
                    color=alt.Color("série:N",
                                    scale=alt.Scale(domain=["Disputados", "Vitórias"],
                                                    range=["#3A4149", "#C28E2C"]),
                                    legend=alt.Legend(orient="bottom", title=None)),
                    tooltip=["_ano:O", "série:N", "qtd:Q"],
                ).properties(height=250)
                st.altair_chart(barras, width='stretch')

        gcol3, gcol4 = st.columns([1.3, 1])
        with gcol3:
            st.markdown("##### Volume contratado (m³) por ano — por empresa")
            if not ga.empty:
                va = ga.groupby(["_ano", "_emp"])["_vol"].sum().reset_index()
                vol_bar = alt.Chart(va).mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3).encode(
                    x=alt.X("_ano:O", title=None, axis=alt.Axis(labelAngle=0)),
                    y=alt.Y("_vol:Q", title=None, axis=alt.Axis(labelExpr=_LBL_PTBR)),
                    color=alt.Color("_emp:N", scale=_esc_emp,
                                    legend=alt.Legend(orient="bottom", title=None, columns=4)),
                    tooltip=[alt.Tooltip("_ano:O", title="ano"), alt.Tooltip("_emp:N", title="empresa"),
                             alt.Tooltip("_vol:Q", title="m³", format=",.0f")],
                ).properties(height=250)
                st.altair_chart(vol_bar, width='stretch')
        with gcol4:
            st.markdown("##### Top clientes por volume (m³)")
            if "CLIENTE" in dfp.columns:
                top = (dfp.assign(_cli=dfp["CLIENTE"].astype(str).str.strip())
                       .groupby("_cli")["_vol"].sum().nlargest(8).reset_index())
                top.columns = ["cliente", "vol"]
                hbar = alt.Chart(top).mark_bar(cornerRadiusEnd=4, color="#C28E2C").encode(
                    x=alt.X("vol:Q", title=None, axis=alt.Axis(labelExpr=_LBL_PTBR)),
                    y=alt.Y("cliente:N", sort="-x", title=None, axis=alt.Axis(labelLimit=190)),
                    tooltip=[alt.Tooltip("cliente:N"), alt.Tooltip("vol:Q", format=",.0f")],
                ).properties(height=250)
                st.altair_chart(hbar, width='stretch')

        st.markdown("##### Valor contratado (R$) por ano — por empresa")
        if not ga.empty:
            vr = ga.groupby(["_ano", "_emp"])["_valor"].sum().reset_index()
            val_bar = alt.Chart(vr).mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3).encode(
                x=alt.X("_ano:O", title=None, axis=alt.Axis(labelAngle=0)),
                y=alt.Y("_valor:Q", title=None,
                        axis=alt.Axis(labelExpr="'R$ ' + (" + _LBL_PTBR + ")")),
                color=alt.Color("_emp:N", scale=_esc_emp,
                                legend=alt.Legend(orient="bottom", title=None, columns=4)),
                tooltip=[alt.Tooltip("_ano:O", title="ano"), alt.Tooltip("_emp:N", title="empresa"),
                         alt.Tooltip("_valor:Q", title="R$", format=",.2f")],
            ).properties(height=250)
            st.altair_chart(val_bar, width='stretch')

    # ----- Contratos a vencer (aba GANHAS) — radar de renovação/aditivo -----
    _ganhas_t = next((t for t in dados if "GANHAS" in t.upper()), None)
    if _ganhas_t:
        _, _hg, _lg = dados[_ganhas_t]
        dfg = pd.DataFrame(_lg)
        if not dfg.empty and "VALIDADE DO CONTRATO" in dfg.columns:
            _vald = pd.to_datetime(dfg["VALIDADE DO CONTRATO"], errors="coerce", dayfirst=True)
            _hj = pd.Timestamp.now().normalize()
            _mk = _vald.notna() & (_vald >= _hj) & (_vald <= _hj + pd.Timedelta(days=90))
            venc = dfg[_mk].copy()
            if not venc.empty:
                st.markdown("##### Contratos a vencer (próximos 90 dias)")
                venc["_dias"] = (_vald[_mk] - _hj).dt.days
                for _, rv in venc.sort_values("_dias").iterrows():
                    st.warning(
                        f"**{rv.get('CLIENTE', '')}** — pregão {rv.get('Nº DO PREGÃO', '')} · "
                        f"vence em **{int(rv['_dias'])} dia(s)** ({rv.get('VALIDADE DO CONTRATO', '')}). "
                        "Avaliar aditivo/renovação."
                    )
        st.markdown('<div class="cl-divider"></div>', unsafe_allow_html=True)

    abas = list(dados.keys())
    tabs = st.tabs([t.strip() for t in abas])
    for tab, titulo in zip(tabs, abas):
        with tab:
            header_orig, header_limpo, linhas = dados[titulo]
            df = pd.DataFrame(linhas)
            _k = "".join(ch if ch.isalnum() else "_" for ch in titulo)
            busca = st.text_input("Buscar", key=f"hist_busca_{_k}",
                                  placeholder="cliente, nº do pregão, objeto…")
            if busca and not df.empty:
                mask = df.apply(lambda r: r.astype(str).str.contains(busca, case=False, na=False).any(), axis=1)
                df = df[mask]
            st.caption(f"{len(df)} registro(s) — mais recentes primeiro. Alterações feitas na planilha aparecem aqui em até 1 minuto.")
            st.dataframe(df.iloc[::-1], width='stretch', hide_index=True)

            with st.expander("Adicionar registro"):
                with st.form(key=f"hist_form_{_k}"):
                    valores: dict = {}
                    cols = st.columns(2)
                    for i, (orig, campo) in enumerate(zip(header_orig, header_limpo)):
                        if not campo:
                            continue
                        cu = campo.upper()
                        with cols[i % 2]:
                            if "DATA" in cu or "VALIDADE" in cu:
                                v = st.date_input(campo, value=None, format="DD/MM/YYYY",
                                                  key=f"hf_{_k}_{i}")
                                valores[campo] = v.strftime("%d/%m/%Y") if v else ""
                            elif cu == "RESULTADO":
                                valores[campo] = st.selectbox(
                                    campo, ["", "VITÓRIA", "DERROTA", "DESERTO", "FRACASSADO", "EM ANDAMENTO"],
                                    key=f"hf_{_k}_{i}")
                            elif "TIPO DE PREG" in cu:
                                valores[campo] = st.selectbox(
                                    campo, ["", "PE", "PRESENCIAL", "DL"], key=f"hf_{_k}_{i}")
                            elif cu == "EMPRESA":
                                valores[campo] = st.text_input(campo, value="CONCRELAGOS CONCRETO LTDA",
                                                               key=f"hf_{_k}_{i}")
                            else:
                                valores[campo] = st.text_input(campo, key=f"hf_{_k}_{i}")
                    enviar = st.form_submit_button("Salvar na planilha", type="primary")
                if enviar:
                    linha = [valores.get(c, "") for c in header_limpo]
                    if any(str(x).strip() for x in linha):
                        if _adicionar_historico(titulo, linha):
                            st.cache_data.clear()
                            st.success("Registro adicionado à planilha.")
                            st.rerun()
                    else:
                        st.warning("Preencha pelo menos um campo.")


# =========================================================================
# ANÁLISE DE EDITAIS — GEM do Gemini + agente interno, decisão e mensagens
# =========================================================================
FISCAL_WHATSAPP = "5522997570806"
_ANALISES_ABA = "Analises Editais"
_ANALISES_HEADER = ["numero_controle_pncp", "data", "status", "dados_json"]

# Agente "LICITAÇÕES" — o melhor analista de licitações do Brasil (prompt do GEM,
# aprimorado). Missão: análise exaustiva SEM deixar passar nada que inabilite.
_PROMPT_ANALISE = (
    "Você é o LICITAÇÕES, o melhor analista de licitações do Brasil — especialista sênior "
    "a serviço da Concrelagos (fornecedora de concreto usinado e brita). Sua função é analisar "
    "este edital com EXTREMA precisão para garantir que a empresa NÃO seja inabilitada. "
    "Nenhum detalhe pode passar.\n\n"
    "Regras de análise:\n"
    "- Para CADA documento de habilitação exigido, transcreva o TEXTO EXATO da exigência "
    "conforme consta no edital e o item/cláusula onde aparece.\n"
    "- Atenção extrema a: prazos, datas e validades de certidões, índices contábeis mínimos, "
    "atestados de capacidade técnica (quantitativos mínimos), registro em conselho (CREA etc.), "
    "visita técnica, amostras/laudos, garantia de proposta e tratamento ME/EPP (LC 123/2006).\n"
    "- Identifique a PLATAFORMA (site) onde ocorrerá a disputa e o link, se constar.\n"
    "- Extraia a logística completa: volume mínimo por entrega (m³), local de entrega, prazo de "
    "entrega, prazo de faturamento/pagamento, regras de emissão de nota fiscal e observações de "
    "fornecimento (dias/horários).\n"
    "- Se houver ambiguidade ou contradição no edital, formule o QUESTIONAMENTO a enviar ao órgão.\n\n"
    "Responda APENAS com JSON válido, sem markdown, exatamente neste formato:\n"
    '{"certame": {"orgao": "", "cidade_uf": "Cidade/UF", '
    '"limite_proposta": "DD/MM/AAAA às HH:MMh (horário de Brasília)", '
    '"inicio_disputa": "DD/MM/AAAA às HH:MMh (horário de Brasília)", "vigencia": "", '
    '"quantidade": "ex: 2.176 m³ de concreto usinado (FCK 15/20/25/30, convencional e bombeado)", '
    '"valor_maximo": "", "objeto": "resumo fiel em 1-3 frases", "local_entrega": "", '
    '"dias_horario": "", "volume_minimo": ""},\n'
    ' "plataforma": {"nome": "ex: Licitar Digital / BLL / ComprasNet", "link": "url ou vazio"},\n'
    ' "documentos": {\n'
    '   "constitutivos": [{"exigencia": "nome curto", "texto_exato": "transcrição literal do edital", "item_edital": "ex: 8.1.1"}],\n'
    '   "certidoes": [{"exigencia": "", "texto_exato": "", "item_edital": ""}],\n'
    '   "qualificacao_tecnica": [{"exigencia": "", "texto_exato": "", "item_edital": ""}],\n'
    '   "qualificacao_financeira": [{"exigencia": "", "texto_exato": "", "item_edital": ""}],\n'
    '   "outras": [{"exigencia": "", "texto_exato": "", "item_edital": ""}]},\n'
    ' "logistica": {"volume_minimo": "", "local_entrega": "", "prazo_entrega": "", '
    '"prazo_faturamento": "", "regras_nota_fiscal": "", "observacoes_fornecimento": ""},\n'
    ' "me_epp": "tratamento diferenciado ME/EPP aplicável (ou não) e impactos",\n'
    ' "riscos_inabilitacao": ["cada ponto que pode INABILITAR a empresa, em ordem de gravidade"],\n'
    ' "questionamentos_sugeridos": ["questionamentos a enviar ao órgão, se houver ambiguidade"]}\n'
    'Use "a confirmar no edital" quando o dado não constar; listas vazias [] quando não houver.\n\n'
    "Edital (texto extraído):\n"
)


def _gemini_gerar(prompt: str, qualidade: bool = True) -> str | None:
    """Gera texto no Gemini (só modelos flash gratuitos — custo zero).

    qualidade=True  → tenta os flash MAIS CAPAZES primeiro (2.5 → 2.0 → lite):
                      usado na análise de edital e no chat do agente.
    qualidade=False → lite primeiro (cota maior), p/ tarefas simples.
    """
    client = _gemini_client()
    if client is None:
        st.error("GEMINI_API_KEY não configurada (Settings → Secrets → [gemini] api_key).")
        return None
    _pagos = ("pro", "ultra", "exp", "thinking", "vision", "tts", "audio", "image", "live", "embedding", "preview")

    def _gratis(n: str) -> bool:
        n = n.lower()
        return "flash" in n and not any(p in n for p in _pagos)

    modelos: list = []
    try:
        for m in client.models.list():
            nome = (getattr(m, "name", "") or "").split("/")[-1]
            acts = getattr(m, "supported_actions", None) or []
            if nome and _gratis(nome) and ("generateContent" in acts or not acts):
                modelos.append(nome)
    except Exception:
        pass
    for c in ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-flash-latest",
              "gemini-2.0-flash-lite", "gemini-flash-lite-latest"]:
        if c not in modelos and _gratis(c):
            modelos.append(c)
    if qualidade:
        def _peso(m: str) -> int:
            if "2.5" in m and "lite" not in m: return 0
            if "lite" not in m: return 1
            return 2
        modelos.sort(key=_peso)
    else:
        modelos.sort(key=lambda m: 0 if "lite" in m else 1)

    ultimo = ""
    for modelo in modelos:
        try:
            r = client.models.generate_content(model=modelo, contents=prompt)
            t = (getattr(r, "text", "") or "").strip()
            if t:
                return t
        except Exception as exc:
            ultimo = str(exc)
            continue
    st.warning("IA sem cota disponível agora (camada gratuita). Tente novamente em alguns minutos.")
    if ultimo:
        st.caption(f"Detalhe: {ultimo[:140]}")
    return None


def _analisar_edital_ia(num_controle: str, link_pdf: str, link_pncp: str) -> dict | None:
    """Baixa o edital (PDF/HTML) e extrai o dossiê do certame com o agente LICITAÇÕES."""
    import json
    with st.spinner("Baixando o edital e analisando com o agente LICITAÇÕES..."):
        texto = st.session_state.get(f"txt_{num_controle}")
        if not texto:
            texto = _baixar_texto_edital(num_controle, link_pdf, link_pncp)
            st.session_state[f"txt_{num_controle}"] = texto or ""
        if not texto or len(texto) < 100:
            st.warning("Não consegui extrair o texto do edital (PDF escaneado ou sem download direto). "
                       "Coloque o PDF na pasta da licitação no Dropbox que eu leio de lá.")
            return None
        raw = _gemini_gerar(_PROMPT_ANALISE + texto[:40000], qualidade=True)
    if not raw:
        return None
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.rstrip("`").strip()
    try:
        return json.loads(raw)
    except Exception:
        st.warning("A IA respondeu em formato inesperado. Clique em Analisar novamente.")
        return None


def _salvar_analise(num_controle: str, status: str, dados: dict | None) -> None:
    """Registra a análise/decisão na aba 'Analises Editais' (auditoria). Falha silenciosa."""
    try:
        import json
        gc = _build_gspread_client()
        sh = gc.open_by_key(_get_sheet_id())
        ws = _get_or_create_worksheet(sh, _ANALISES_ABA, rows=2000, cols=len(_ANALISES_HEADER))
        vals = ws.get_all_values()
        if not vals:
            ws.append_row(_ANALISES_HEADER, value_input_option="USER_ENTERED")
        ws.append_row([
            num_controle, datetime.now().isoformat(timespec="seconds"), status,
            json.dumps(dados or {}, ensure_ascii=False)[:45000],
        ], value_input_option="USER_ENTERED")
    except Exception as exc:
        st.toast(f"Não consegui registrar a análise: {exc}", icon="⚠️")


_PROMPT_CHAT = (
    "Você é o LICITAÇÕES, o melhor analista de licitações do Brasil, especialista sênior da "
    "Concrelagos Concreto S/A (fornecedora de concreto usinado e brita). Você está em uma CONVERSA "
    "com a equipe jurídica sobre um edital específico (texto e análise abaixo).\n\n"
    "Como agir:\n"
    "- Responda em português, com precisão técnica e citando o ITEM do edital que fundamenta cada resposta.\n"
    "- Se pedirem MODELO/MINUTA de declaração (ME/EPP, inidoneidade, fato superveniente, menor aprendiz, "
    "proposta independente, etc.), gere a minuta COMPLETA e pronta para uso, no padrão de licitações públicas, "
    "com os dados da empresa que constarem e [CAMPOS ENTRE COLCHETES] para o que faltar.\n"
    "- Se pedirem questionamento ou impugnação, redija a minuta formal endereçada ao pregoeiro.\n"
    "- Quando a resposta depender de algo que não está no edital, diga claramente e sugira como confirmar.\n"
    "- Seja direto: nada de rodeios; rigor de quem não deixa a empresa ser inabilitada.\n"
)


def _chat_licitacoes(nc: str, link_pdf: str, link_pncp: str, analise: dict, historico: list) -> str | None:
    """Um turno do chat com o agente: contexto completo (edital + análise + docs da pasta)
    + transcrição da conversa → resposta do LICITAÇÕES (qualidade-first)."""
    import json
    texto = st.session_state.get(f"txt_{nc}")
    if not texto:
        texto = _baixar_texto_edital(nc, link_pdf, link_pncp)
        st.session_state[f"txt_{nc}"] = texto or ""
    docs_ctx = st.session_state.get(f"docsctx_{nc}", "")
    transcricao = "\n".join(
        ("USUÁRIO: " if m["role"] == "user" else "LICITAÇÕES: ") + m["content"]
        for m in historico[-12:]
    )
    prompt = (
        _PROMPT_CHAT
        + "\n=== ANÁLISE JÁ FEITA (JSON) ===\n" + json.dumps(analise or {}, ensure_ascii=False)[:6000]
        + "\n\n=== EDITAL (texto extraído) ===\n" + (texto or "(texto do edital indisponível)")[:40000]
        + (("\n\n=== DOCUMENTOS DA PASTA DA LICITAÇÃO ===\n" + docs_ctx[:20000]) if docs_ctx else "")
        + "\n\n=== CONVERSA ===\n" + transcricao
        + "\nLICITAÇÕES:"
    )
    return _gemini_gerar(prompt, qualidade=True)


def _g(dados: dict, chave: str) -> str:
    v = str((dados or {}).get(chave) or "").strip()
    return v if v else "a confirmar no edital"


# =========================================================================
# PASTA DO DROPBOX (08 - Licitações\LICITAÇÕES) + CHECAGEM DE HABILITAÇÃO
# Lê direto do disco quando a pasta existe (Dropbox sincronizado em todas as
# máquinas do setor). Sem pasta (ex.: hospedagem na nuvem) → upload manual.
# =========================================================================
def _licitacoes_dir() -> Path | None:
    candidatos = [
        Path(r"C:\Users\CONCRELAGOS\Dropbox\BIBLIOTECA JURIDICA - GERAL\CONCRELAGOS\08 - Licitações\LICITAÇÕES"),
        Path.home() / "Dropbox" / "BIBLIOTECA JURIDICA - GERAL" / "CONCRELAGOS" / "08 - Licitações" / "LICITAÇÕES",
    ]
    for p in candidatos:
        try:
            if p.exists():
                return p
        except OSError:
            continue
    return None


def _norm_txt(s) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return " ".join(s.lower().split())


def _achar_pasta_licitacao(base: Path, municipio: str, uf: str):
    """Melhor match de 'LICITAÇÃO - <MUNICÍPIO> <UF>' (tolerante a hífens/espaços)."""
    alvo = _norm_txt(municipio)
    if not alvo:
        return None
    melhor = None
    for p in base.iterdir():
        if not p.is_dir():
            continue
        n = _norm_txt(p.name)
        if alvo in n:
            if uf and _norm_txt(uf) in n.split():
                return p
            melhor = melhor or p
    return melhor


def _pdf_texto_bytes(dados_b: bytes, paginas: int = 6) -> str:
    import io
    import pdfplumber
    try:
        with pdfplumber.open(io.BytesIO(dados_b)) as pdf:
            return "\n".join((pg.extract_text() or "") for pg in pdf.pages[:paginas]).strip()
    except Exception:
        return ""


def _ler_docs_pasta(pasta: Path, max_arq: int = 15, max_chars: int = 28000):
    """Lê os PDFs da pasta (recursivo). Retorna (contexto_p_ia, nomes)."""
    try:
        pdfs = sorted(pasta.rglob("*.pdf"))[:max_arq]
    except OSError:
        return "", []
    partes, nomes, total = [], [], 0
    for f in pdfs:
        try:
            rel = str(f.relative_to(pasta))
        except ValueError:
            rel = f.name
        nomes.append(rel)
        if total >= max_chars:
            partes.append(f"--- ARQUIVO: {rel} --- (texto omitido por limite)")
            continue
        txt = _pdf_texto_bytes(f.read_bytes(), paginas=6)
        trecho = txt[: max(0, min(4000, max_chars - total))]
        total += len(trecho)
        partes.append(f"--- ARQUIVO: {rel} ---\n{trecho or '(sem texto extraível — possivelmente escaneado)'}")
    return "\n\n".join(partes), nomes


_PROMPT_HAB = (
    "Você é o LICITAÇÕES, o melhor analista de licitações do Brasil (Concrelagos — concreto usinado e brita). "
    "Sua tarefa: conferir, exigência por exigência, se os DOCUMENTOS DISPONÍVEIS cobrem as EXIGÊNCIAS DE "
    "HABILITAÇÃO do edital. Hoje é {HOJE}; a sessão do pregão é em {SESSAO}. Os NOMES dos arquivos costumam "
    "indicar o tipo e a validade (ex.: 'CERTIDAO FEDERAL - VENC 21-05-2024.pdf'): considere VENCIDO todo "
    "documento cuja validade termine antes da sessão. Não deixe passar NADA que possa inabilitar.\n"
    "REGRAS OBRIGATÓRIAS antes de concluir:\n"
    "1. LEIA O TEXTO DO EDITAL POR INTEIRO (fornecido abaixo) — baseie CADA conclusão no que o edital REALMENTE "
    "exige, não em suposição. As exigências extraídas são só um resumo; o edital manda.\n"
    "2. SEDE E FILIAL: muitos editais exigem certidões — em especial a CERTIDÃO DE FALÊNCIA/CONCORDATA/RECUPERAÇÃO "
    "JUDICIAL e a qualificação econômico-financeira — TANTO da FILIAL participante QUANTO da MATRIZ/SEDE (CNPJ raiz). "
    "Quando o edital pedir, cobre AS DUAS: marque 'faltante' se só a da filial OU só a da sede estiver presente, e "
    "deixe explícito na obs que falta a da sede (ou da filial).\n"
    "3. NÃO ACUSE discrepância de razão social, CNPJ ou 'erro material' (ex.: 'LTDA' vs 'S/A') A MENOS QUE o edital "
    "EXIJA correspondência exata E o documento claramente divirja do exigido. Na dúvida, use status 'verificar' com "
    "uma obs curta ('conferir se o edital aceita...'), NUNCA uma afirmação de erro. Razão social muda com o tempo "
    "(LTDA→S/A) e geralmente não inabilita — só vira problema se o edital for expresso.\n"
    "Responda APENAS com JSON válido, sem markdown:\n"
    '{"mapeamento": [{"item_edital": "8.1", "exigencia": "nome curto", '
    '"documento": "arquivo.pdf que atende, ou FALTANTE", "status": "ok|vencido|incompleto|faltante|verificar", '
    '"obs": "1 frase curta quando necessário"}],\n'
    ' "faltantes": [{"item_edital": "8.3", "exigencia": "ATA", "como_resolver": "onde/como obter ou gerar"}],\n'
    ' "habilitado_100": true|false,\n'
    ' "observacoes": ["alertas gerais (validades próximas, autenticação, assinatura, etc.)"]}\n'
)


def _verificar_habilitacao(d, analise: dict, docs_ctx: str, edital_txt: str = "") -> dict | None:
    import json
    data_sessao = _fmt_data(d.get("data_abertura"))
    docs_exig = (analise or {}).get("documentos") or {}
    prompt = (
        _PROMPT_HAB.replace("{HOJE}", datetime.now().strftime("%d/%m/%Y")).replace("{SESSAO}", str(data_sessao))
        + "\n=== TEXTO DO EDITAL (leia por inteiro — é a fonte da verdade) ===\n"
        + (edital_txt or "(texto do edital indisponível — baseie-se só nas exigências extraídas e seja conservador)")[:40000]
        + "\n\n=== EXIGÊNCIAS DO EDITAL (resumo extraído pela análise) ===\n"
        + json.dumps(docs_exig, ensure_ascii=False)[:12000]
        + "\n\n=== DOCUMENTOS DISPONÍVEIS ===\n" + (docs_ctx or "(nenhum documento carregado)")[:30000]
    )
    raw = _gemini_gerar(prompt, qualidade=True)
    if not raw:
        return None
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.rstrip("`").strip()
    try:
        return json.loads(raw)
    except Exception:
        st.warning("A IA respondeu em formato inesperado — clique em Verificar novamente.")
        return None


def _secao_habilitacao(nc: str, d, dados: dict) -> None:
    """Seção 'Habilitação': documentos da pasta do Dropbox (ou upload) + checagem 8.x."""
    st.markdown('<div class="cl-divider"></div>', unsafe_allow_html=True)
    st.markdown("##### Habilitação — documentos da pasta da licitação")
    base = _licitacoes_dir()

    if base:
        pastas = sorted(p.name for p in base.iterdir() if p.is_dir())
        sugestao = _achar_pasta_licitacao(base, str(d.get("municipio") or ""), str(d.get("uf") or ""))
        opcoes = ["(selecione a pasta)"] + pastas
        _pre = (pastas.index(sugestao.name) + 1) if (sugestao and sugestao.name in pastas) else 0
        esc = st.selectbox("Pasta da licitação (Dropbox)", opcoes, index=_pre, key=f"pasta_{nc}")
        if esc != "(selecione a pasta)":
            pasta_lic = base / esc
            subs = sorted(p.name for p in pasta_lic.iterdir() if p.is_dir())
            if subs:
                sub_esc = st.selectbox("Subpasta do pregão", ["(pasta inteira)"] + subs,
                                       index=1 if len(subs) == 1 else 0, key=f"sub_{nc}")
                alvo = pasta_lic / sub_esc if sub_esc != "(pasta inteira)" else pasta_lic
            else:
                alvo = pasta_lic
            fil_dir = base / "DOCUMENTAÇÃO FILIAIS"
            fil_esc = "(nenhuma)"
            if fil_dir.exists():
                fils = sorted(p.name for p in fil_dir.iterdir() if p.is_dir())
                fil_esc = st.selectbox("Certidões da filial (DOCUMENTAÇÃO FILIAIS)",
                                       ["(nenhuma)"] + fils, key=f"fil_{nc}")
            b1, b2 = st.columns([1.7, 1.3])
            with b1:
                if st.button("Carregar documentos da pasta", key=f"ld_{nc}", width='stretch'):
                    with st.spinner("Lendo os PDFs da pasta…"):
                        ctx, nomes = _ler_docs_pasta(alvo)
                        if fil_esc != "(nenhuma)":
                            ctx2, n2 = _ler_docs_pasta(fil_dir / fil_esc, max_arq=10, max_chars=12000)
                            ctx += f"\n\n=== CERTIDÕES DA FILIAL {fil_esc} ===\n" + ctx2
                            nomes += [f"FILIAL/{x}" for x in n2]
                    st.session_state[f"docsctx_{nc}"] = ctx
                    st.session_state[f"docsnomes_{nc}"] = nomes
            with b2:
                if st.button("Abrir pasta no Explorer", key=f"op_{nc}", width='stretch'):
                    try:
                        os.startfile(str(alvo))
                    except Exception:
                        st.toast("Não consegui abrir o Explorer.", icon="⚠️")
    else:
        st.info("Pasta do Dropbox não visível neste servidor — anexe os PDFs abaixo, ou use o app "
                "numa máquina do setor (com Dropbox) para leitura automática da pasta.")
        ups = st.file_uploader("Documentos de habilitação (PDF)", accept_multiple_files=True,
                               type=["pdf"], key=f"up_{nc}")
        if ups:
            partes, nomes, tot = [], [], 0
            for u in ups[:15]:
                t = _pdf_texto_bytes(u.getvalue(), paginas=6)
                trecho = t[:4000]
                tot += len(trecho)
                nomes.append(u.name)
                partes.append(f"--- ARQUIVO: {u.name} ---\n{trecho or '(sem texto extraível)'}")
                if tot > 28000:
                    break
            st.session_state[f"docsctx_{nc}"] = "\n\n".join(partes)
            st.session_state[f"docsnomes_{nc}"] = nomes

    nomes = st.session_state.get(f"docsnomes_{nc}") or []
    if nomes:
        st.caption(f"{len(nomes)} documento(s) carregado(s): " + " · ".join(nomes[:8])
                   + (" …" if len(nomes) > 8 else ""))
        if st.button("Verificar habilitação", type="primary", key=f"vh_{nc}"):
            with st.spinner("Conferindo exigência por exigência (8.1, 8.2, …)…"):
                vh = _verificar_habilitacao(d, dados, st.session_state.get(f"docsctx_{nc}", ""),
                                            st.session_state.get(f"txt_{nc}", ""))
            if vh:
                st.session_state[f"hab_{nc}"] = vh
        vh = st.session_state.get(f"hab_{nc}")
        if vh:
            if vh.get("habilitado_100"):
                st.success("**100% HABILITADOS** — todos os documentos cobrem as exigências do edital.")
            mapa = vh.get("mapeamento") or []
            if mapa:
                _ic = {"ok": "✓ OK", "vencido": "VENCIDO", "incompleto": "INCOMPLETO",
                       "faltante": "FALTANTE", "verificar": "VERIFICAR"}
                st.markdown("\n".join(
                    f"- **{m.get('item_edital', '?')} – {m.get('exigencia', '')}** → "
                    f"{m.get('documento', '')} **[{_ic.get(str(m.get('status', '')).lower(), m.get('status', ''))}]**"
                    + (f" — {m.get('obs')}" if m.get("obs") else "")
                    for m in mapa if isinstance(m, dict)
                ))
            falt = [f for f in (vh.get("faltantes") or []) if isinstance(f, dict)]
            if falt:
                st.error("**Documentos FALTANTES:**\n\n" + "\n".join(
                    f"- **{f.get('item_edital', '?')} – {f.get('exigencia', '')}** — {f.get('como_resolver', '')}"
                    for f in falt))
            obs = [str(o) for o in (vh.get("observacoes") or []) if str(o or "").strip()]
            if obs:
                st.info("\n".join(f"- {o}" for o in obs))


def _aba_analise(ed: pd.DataFrame) -> None:
    import urllib.parse

    st.markdown(
        '<div class="cl-boletim-head">'
        '<span class="cl-boletim-head-title">Análise de Editais</span>'
        '<span class="cl-boletim-head-sub">IA lê o edital · decisão · mensagens prontas (regional e fiscal)</span>'
        '</div>',
        unsafe_allow_html=True,
    )
    if ed.empty:
        st.info("Nenhum edital na base ainda.")
        return

    # Só editais ainda abertos (pregão não ocorrido), mais relevantes primeiro
    df = ed.copy()
    _b = df["data_encerramento"] if ("data_encerramento" in df.columns and df["data_encerramento"].notna().any()) else df.get("data_abertura")
    if _b is not None:
        _bz = pd.to_datetime(_b, errors="coerce", utc=True)
        df = df[_bz.isna() | (_bz >= pd.Timestamp.now(tz="UTC"))]
    if df.empty:
        st.info("Nenhum edital aberto no momento.")
        return
    df["_score_n"] = pd.to_numeric(df.get("score"), errors="coerce").fillna(0)
    df = df.sort_values(["_score_n", "valor_estimado"], ascending=[False, False])

    def _rotulo(i):
        r = df.loc[i]
        return (f"{r.get('numero_edital') or r.get('numero_controle_pncp')} · "
                f"{r.get('municipio')}/{r.get('uf')} · {str(r.get('objeto'))[:70]}")

    sel = st.selectbox("Edital para analisar", options=list(df.index), format_func=_rotulo)
    d = df.loc[sel]
    nc = str(d.get("numero_controle_pncp") or "")
    link_origem = str(d.get("link_sistema_origem") or "")
    link_pncp = str(d.get("link_pncp") or "")

    b1, b2 = st.columns([1.8, 3.2])
    with b1:
        analisar = st.button("Analisar com o agente LICITAÇÕES", type="primary", width='stretch')
    with b2:
        st.caption("Análise exaustiva: plataforma de disputa, documentos de habilitação com texto "
                   "exato, logística/faturamento e riscos de inabilitação.")
    if analisar:
        res = _analisar_edital_ia(nc, link_origem, link_pncp)
        if res:
            st.session_state[f"anl_{nc}"] = res
            _salvar_analise(nc, "analisado", res)

    dados = st.session_state.get(f"anl_{nc}")
    if not dados:
        st.caption("Selecione o edital e clique em Analisar — o agente lê o edital inteiro e "
                   "monta o dossiê de participação.")
        return

    # Compatibilidade: análise nova é aninhada (certame/documentos/...); antiga era plana.
    cert = dados.get("certame") if isinstance(dados.get("certame"), dict) else dados
    plat = dados.get("plataforma") or {}
    docs = dados.get("documentos") or {}
    logi = dados.get("logistica") or {}
    riscos = dados.get("riscos_inabilitacao") or ([dados.get("riscos")] if dados.get("riscos") else [])
    quests = dados.get("questionamentos_sugeridos") or []

    st.markdown("##### Dados do certame")
    st.markdown(
        f"**Órgão:** {_g(cert,'orgao')}  \n"
        f"**Limite p/ proposta:** {_g(cert,'limite_proposta')} · **Disputa:** {_g(cert,'inicio_disputa')}  \n"
        f"**Vigência:** {_g(cert,'vigencia')}  \n"
        f"**Quantidade:** {_g(cert,'quantidade')} · **Valor máximo:** {_g(cert,'valor_maximo')}  \n"
        f"**Local de entrega:** {_g(cert,'local_entrega')} · **Volume mínimo:** {_g(cert,'volume_minimo')}"
    )

    # Plataforma da disputa
    _pnome = str(plat.get("nome") or "").strip()
    _plink = str(plat.get("link") or "").strip()
    if _pnome or _plink:
        st.markdown("##### Plataforma da disputa")
        if _plink and _plink.lower().startswith("http"):
            st.markdown(f"**{_pnome or 'Plataforma'}** — [{_plink}]({_plink})")
        else:
            st.markdown(f"**{_pnome or 'a confirmar no edital'}**" + (f" — {_plink}" if _plink else ""))

    # Documentos de habilitação (texto exato por categoria)
    _CATS = [
        ("constitutivos", "Documentos constitutivos"),
        ("certidoes", "Certidões"),
        ("qualificacao_tecnica", "Qualificação técnica"),
        ("qualificacao_financeira", "Qualificação econômico-financeira"),
        ("outras", "Outras exigências"),
    ]
    if any(docs.get(k) for k, _ in _CATS):
        st.markdown("##### Documentos de habilitação")
        for k, rotulo in _CATS:
            itens = docs.get(k) or []
            if not itens:
                continue
            with st.expander(f"{rotulo} ({len(itens)})"):
                for it in itens:
                    if not isinstance(it, dict):
                        st.markdown(f"- {it}")
                        continue
                    _ex = str(it.get("exigencia") or "").strip() or "Exigência"
                    _item = str(it.get("item_edital") or "").strip()
                    _txt = str(it.get("texto_exato") or "").strip()
                    st.markdown(f"**{_ex}**" + (f" — item {_item}" if _item else ""))
                    if _txt:
                        st.markdown(f"> {_txt}")

    # Logística e faturamento
    if any(str(logi.get(k) or "").strip() for k in logi):
        st.markdown("##### Logística e faturamento")
        st.markdown(
            f"**Volume mínimo:** {_g(logi,'volume_minimo')} · **Prazo de entrega:** {_g(logi,'prazo_entrega')}  \n"
            f"**Local de entrega:** {_g(logi,'local_entrega')}  \n"
            f"**Faturamento:** {_g(logi,'prazo_faturamento')} · **Nota fiscal:** {_g(logi,'regras_nota_fiscal')}  \n"
            f"**Fornecimento:** {_g(logi,'observacoes_fornecimento')}"
        )

    _meepp = str(dados.get("me_epp") or "").strip()
    if _meepp:
        st.markdown(f"**ME/EPP:** {_meepp}")

    # Riscos de inabilitação — destaque máximo
    riscos = [str(r).strip() for r in riscos if str(r or "").strip()]
    if riscos:
        st.error("**Riscos de inabilitação — confira um a um:**\n\n" +
                 "\n".join(f"- {r}" for r in riscos))

    quests = [str(q).strip() for q in quests if str(q or "").strip()]
    if quests:
        st.info("**Questionamentos sugeridos ao órgão:**\n\n" +
                "\n".join(f"- {q}" for q in quests))

    # ----- Documentos da pasta + checagem de habilitação -----
    _secao_habilitacao(nc, d, dados)

    # ----- Chat com o agente LICITAÇÕES -----
    st.markdown('<div class="cl-divider"></div>', unsafe_allow_html=True)
    st.markdown("##### Converse com o LICITAÇÕES")
    st.caption("Pergunte sobre o edital, peça minutas de declarações que faltarem, "
               "questionamentos ou impugnações — ele responde com base no edital completo.")
    _ck = f"chat_{nc}"
    if _ck not in st.session_state:
        st.session_state[_ck] = []
    for _m in st.session_state[_ck]:
        with st.chat_message(_m["role"]):
            st.markdown(_m["content"])
    _perg = st.chat_input("Escreva sua pergunta ou pedido ao agente…", key=f"ci_{nc}")
    if _perg:
        st.session_state[_ck].append({"role": "user", "content": _perg})
        with st.chat_message("user"):
            st.markdown(_perg)
        with st.chat_message("assistant"):
            with st.spinner("O LICITAÇÕES está analisando…"):
                _resp = _chat_licitacoes(nc, link_origem, link_pncp, dados, st.session_state[_ck])
            st.markdown(_resp or "Sem cota de IA neste momento — tente novamente em alguns minutos.")
        if _resp:
            st.session_state[_ck].append({"role": "assistant", "content": _resp})

    st.markdown('<div class="cl-divider"></div>', unsafe_allow_html=True)
    dc1, dc2, _ = st.columns([1.2, 1.2, 3])
    estado_key = f"anl_status_{nc}"
    with dc1:
        if st.button("Edital aprovado", width='stretch'):
            st.session_state[estado_key] = "aprovado"
            _salvar_analise(nc, "aprovado", dados)
    with dc2:
        if st.button("Edital reprovado", width='stretch'):
            st.session_state[estado_key] = "reprovado"
            _salvar_analise(nc, "reprovado", dados)

    status = st.session_state.get(estado_key)
    if status == "reprovado":
        st.error("Edital marcado como REPROVADO (registrado na auditoria).")
        return
    if status != "aprovado":
        return

    st.success("Edital APROVADO — mensagens prontas abaixo.")
    st.markdown('<div class="cl-divider"></div>', unsafe_allow_html=True)

    # ----- Mensagem ao regional -----
    st.markdown("##### 1) Mensagem ao regional")
    nome_regional = st.text_input("Nome do regional", placeholder="ex: João")
    cidade_uf = _g(cert, "cidade_uf") if _g(cert, "cidade_uf") != "a confirmar no edital" else f"{d.get('municipio')}/{d.get('uf')}"
    msg_regional = (
        f"Bom dia {nome_regional or '(nome do regional)'}! Tudo bom?\n"
        f"Encontramos uma licitação no Município de {cidade_uf}, me informe se atendemos lá, por favor? "
        f"E se sim, qual a filial que irá participar, e também me informe o preço limite.\n"
        f"Dados do Certame:\n"
        f"1. Órgão solicitante: {_g(cert,'orgao')}.\n"
        f"2. Limite para Envio da Proposta: {_g(cert,'limite_proposta')}.\n"
        f"3. Início da Disputa (Lances): {_g(cert,'inicio_disputa')}.\n"
        f"4. Prazo de vigência da contratação: {_g(cert,'vigencia')}.\n"
        f"5. Quantidade Total Estimada: {_g(cert,'quantidade')}.\n"
        f"6. Valor Estimado (Preço Máximo): {_g(cert,'valor_maximo')}.\n"
        f"7. OBJETO: {_g(cert,'objeto')}.\n"
        f"8. Local de Entrega: {_g(cert,'local_entrega')}.\n"
        f"9. Dias e Horário de Entrega: {_g(cert,'dias_horario')}.\n"
        f"10. Volume Mínimo por Entrega: {_g(cert,'volume_minimo')}."
    )
    st.code(msg_regional, language=None)
    st.warning("Lembrete: anexar o PRINT DOS ITENS e o PDF DO EDITAL ao enviar a mensagem.")
    if link_origem or link_pncp:
        st.markdown(f"[Baixar edital]({link_origem or link_pncp})")
    st.caption("Filial definida? Peça o cadastro de fornecedor no Claude com /cadastros-concrelagos.")

    st.markdown('<div class="cl-divider"></div>', unsafe_allow_html=True)

    # ----- Mensagem ao fiscal (WhatsApp) -----
    st.markdown("##### 2) Documentos ao fiscal")
    filial_doc = st.text_input("Filial (para pedir a documentação)", placeholder="ex: Ubá")
    try:
        _data_preg = pd.to_datetime(d.get("data_abertura"), errors="coerce")
        data_pregao = _data_preg.strftime("%d/%m") if pd.notna(_data_preg) else "a definir"
    except Exception:
        data_pregao = "a definir"
    msg_fiscal = (
        f"Bom dia, tudo bem? Vamos participar de uma licitação dia {data_pregao} em {cidade_uf}. "
        f"Consegue me enviar essa documentação referente à filial de {filial_doc or '(filial)'}?\n"
        f"1. Certidão Conjunta de Débitos Relativos a Tributos Federais e à Dívida Ativa da União.\n"
        f"2. Certidão de Regularidade Estadual pertinente ao domicílio ou sede.\n"
        f"3. Certidão de Regularidade Municipal pertinente ao domicílio ou sede.\n"
        f"4. Certidão de Regularidade do FGTS.\n"
        f"5. Certidão Negativa de Débitos Trabalhistas (CNDT).\n"
        f"6. Certidão Negativa de Falência (emitida há no máximo 90 dias)."
    )
    st.code(msg_fiscal, language=None)
    _wa = f"https://wa.me/{FISCAL_WHATSAPP}?text={urllib.parse.quote(msg_fiscal)}"
    st.link_button("Abrir WhatsApp do fiscal com a mensagem pronta", _wa, width='stretch')
    st.caption("O WhatsApp abre com a mensagem preenchida — confira e aperte enviar.")


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
                <div class="cl-header-title">{_TITULO_SOL}</div>
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

    # Navegação por botões (controlável via código — permite que os KPIs do
    # Dashboard levem direto ao Boletim já filtrado). Ativo = dourado.
    _PAGINAS = ["Dashboard", "Boletim", "Mapa", "Histórico", "Análise", "Diário"]
    if "pagina" not in st.session_state:
        st.session_state["pagina"] = "Dashboard"
    _navc = st.columns(len(_PAGINAS))
    for _i, _nm in enumerate(_PAGINAS):
        if _navc[_i].button(_nm, key=f"nav_{_nm}", width='stretch',
                            type=("primary" if st.session_state["pagina"] == _nm else "secondary")):
            st.session_state["pagina"] = _nm
            st.session_state["f_extra"] = None   # nav manual zera o filtro one-shot do Dashboard
            st.rerun()
    st.markdown('<div style="margin-top:0.2rem;"></div>', unsafe_allow_html=True)

    _pg = st.session_state["pagina"]
    if _pg == "Dashboard":
        _aba_dashboard(ed, fil)
    elif _pg == "Boletim":
        _aba_editais(ed)
    elif _pg == "Mapa":
        _aba_mapa(ed, fil)
    elif _pg == "Histórico":
        _aba_historico()
    elif _pg == "Análise":
        _aba_analise(ed)
    else:
        _aba_diario(ed, ultima)


if __name__ == "__main__":
    main()
