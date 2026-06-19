"""app.py — Concrelagos · Histórico Comercial.

Site enxuto para a diretoria: apenas a aba Histórico, espelhando a planilha
comercial (PREGOES / GANHAS / ADITIVOS) via Google Sheets. Login por senha.
"""


from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
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


# ===== Conexão Google Sheets =====
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


# ===== UI Helpers =====
def _card(title: str, value: str, delta: str = "") -> str:
    return f"""
    <div class="cl-card">
        <div class="cl-card-title">{title}</div>
        <div class="cl-card-value">{value}</div>
        {f'<div class="cl-card-delta">{delta}</div>' if delta else ''}
    </div>
    """


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



# ===== App =====
def main() -> None:
    if not _check_login():
        return

    st.sidebar.markdown("### Painel")
    st.sidebar.caption("Concrelagos — Histórico")
    if st.sidebar.button("Recarregar dados", width='stretch'):
        st.cache_data.clear()
        st.rerun()
    if st.sidebar.button("Sair", width='stretch'):
        st.session_state["autenticado"] = False
        st.rerun()

    if LOGO_PATH.exists():
        st.image(str(LOGO_PATH), width=240)

    st.markdown(
        f"""
        <div class="cl-header-bar">
            <div>
                <div class="cl-header-title">{_TITULO_SOL}</div>
                <div class="cl-header-sub">Histórico comercial — pregões, contratos e aditivos</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    _aba_historico()


if __name__ == "__main__":
    main()
