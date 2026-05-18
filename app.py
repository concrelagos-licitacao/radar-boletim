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
st.set_page_config(
    page_title="Concrelagos Intelligence Hub",
    page_icon="🏗️",
    layout="wide",
    initial_sidebar_state="expanded",
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

# ===== Styling corporativo =====
st.markdown(
    """
    <style>
    :root {
        --cl-primary: #0E2A47;
        --cl-accent:  #C5A572;
        --cl-bg:      #F7F8FA;
        --cl-card:    #FFFFFF;
        --cl-text:    #1F2937;
        --cl-muted:   #6B7280;
        --cl-success: #16A34A;
        --cl-danger:  #DC2626;
    }
    .main { background-color: var(--cl-bg); }
    .stApp header { background-color: var(--cl-primary); }
    .stApp header * { color: white !important; }
    .block-container { padding-top: 1.5rem; padding-bottom: 2rem; }
    h1, h2, h3 { color: var(--cl-primary); font-weight: 600; }
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
        font-size: 1.65rem;
        font-weight: 700;
        color: var(--cl-primary);
        line-height: 1.1;
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
        background: #0E2A47;
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

    st.markdown(
        """
        <div class="cl-header-bar">
            <div>
                <div class="cl-header-title">🏗️ Concrelagos Intelligence Hub</div>
                <div class="cl-header-sub">Rastreador autônomo de licitações públicas — acesso restrito</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.form("login"):
        senha = st.text_input("Senha de acesso", type="password", placeholder="Informe a senha")
        ok = st.form_submit_button("Entrar", type="primary", use_container_width=True)
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


def _get_sheet_id() -> str:
    sid = os.environ.get("GOOGLE_SHEETS_ID")
    if sid:
        return sid
    try:
        return st.secrets["gcp"]["sheets_id"]
    except Exception:
        st.error("GOOGLE_SHEETS_ID não configurado.")
        st.stop()


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
def _sidebar_filtros(ed: pd.DataFrame, fil: pd.DataFrame) -> dict:
    st.sidebar.title("🔎 Filtros")
    st.sidebar.caption("Aplicados a todas as abas")

    ufs = sorted(set(ed["uf"].dropna().tolist() if "uf" in ed.columns and not ed.empty else []) | set(fil["uf"].dropna().tolist()))
    uf_sel = st.sidebar.multiselect("UF", ufs, default=ufs)

    materiais = sorted(set(ed["material"].dropna().tolist())) if "material" in ed.columns and not ed.empty else ["concreto", "brita"]
    mat_sel = st.sidebar.multiselect("Material", materiais, default=materiais)

    # Filtro de confiança (score) — só aparece se a coluna existir
    score_opcoes = {"CERTO (3)": 3, "PROVÁVEL (2)": 2, "POSSÍVEL (1)": 1}
    if "score_label" in ed.columns and not ed.empty:
        scores_presentes = sorted(ed["score"].dropna().unique().tolist(), reverse=True)
        score_labels_presentes = [k for k, v in score_opcoes.items() if v in scores_presentes]
        score_sel_labels = st.sidebar.multiselect(
            "Confiança", score_labels_presentes, default=score_labels_presentes,
            help="CERTO = keyword exata no objeto · PROVÁVEL = keyword indireta · POSSÍVEL = edital genérico (pode conter o produto)",
        )
        score_sel = [score_opcoes[l] for l in score_sel_labels]
    else:
        score_sel = []

    valor_min = float(ed["valor_estimado"].min()) if not ed.empty and "valor_estimado" in ed.columns else 180_000.0
    valor_max = float(ed["valor_estimado"].max()) if not ed.empty and "valor_estimado" in ed.columns else 10_000_000.0
    valor_range = st.sidebar.slider(
        "Valor estimado (R$)",
        min_value=180_000.0,
        max_value=max(valor_max, 1_000_000.0),
        value=(180_000.0, max(valor_max, 1_000_000.0)),
        step=50_000.0,
        format="R$ %.0f",
    )

    dist_max = float(ed["distancia_km"].max()) if not ed.empty and "distancia_km" in ed.columns else 700.0
    dist_lim = st.sidebar.slider("Distância máxima (km)", 0, max(int(dist_max), 700), max(int(dist_max), 700))

    hoje = datetime.now().date()
    # Padrão amplo: inclui editais do último ano para não cortar dados históricos.
    # Quando a abertura do edital for futura, ainda assim mostra.
    dt_de = st.sidebar.date_input("De", value=hoje - timedelta(days=365))
    dt_ate = st.sidebar.date_input("Até", value=hoje + timedelta(days=180))

    st.sidebar.markdown('<div class="cl-divider"></div>', unsafe_allow_html=True)
    if st.sidebar.button("🔄 Recarregar dados", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    if st.sidebar.button("Sair", use_container_width=True):
        st.session_state["autenticado"] = False
        st.rerun()

    return {
        "ufs": uf_sel,
        "materiais": mat_sel,
        "scores": score_sel,
        "valor_min": valor_range[0],
        "valor_max": valor_range[1],
        "dist_lim": dist_lim,
        "dt_de": dt_de,
        "dt_ate": dt_ate,
    }


def _aplica_filtros(ed: pd.DataFrame, filtros: dict) -> pd.DataFrame:
    if ed.empty:
        return ed
    df = ed.copy()
    if "uf" in df.columns and filtros["ufs"]:
        df = df[df["uf"].isin(filtros["ufs"])]
    if "material" in df.columns and filtros["materiais"]:
        df = df[df["material"].isin(filtros["materiais"])]
    if "score" in df.columns and filtros.get("scores"):
        df["score"] = pd.to_numeric(df["score"], errors="coerce")
        df = df[df["score"].isin(filtros["scores"])]
    if "valor_estimado" in df.columns:
        df = df[(df["valor_estimado"] >= filtros["valor_min"]) & (df["valor_estimado"] <= filtros["valor_max"])]
    if "distancia_km" in df.columns:
        df = df[df["distancia_km"] <= filtros["dist_lim"]]
    if "data_abertura" in df.columns:
        df = df[(df["data_abertura"].dt.date >= filtros["dt_de"]) & (df["data_abertura"].dt.date <= filtros["dt_ate"])]
    return df


# ===== Abas =====
def _aba_visao_geral(ed: pd.DataFrame, fil: pd.DataFrame) -> None:
    st.subheader("Visão Geral")

    if ed.empty:
        st.info("Nenhum edital qualificado ainda. Rode `python scraper.py` para popular.")
        return

    total = len(ed)
    valor_total = ed["valor_estimado"].sum() if "valor_estimado" in ed.columns else 0
    concreto = (ed["material"] == "concreto").sum() if "material" in ed.columns else 0
    brita = (ed["material"] == "brita").sum() if "material" in ed.columns else 0
    proximos_7d = (
        (ed["data_abertura"] >= pd.Timestamp.now()) &
        (ed["data_abertura"] <= pd.Timestamp.now() + pd.Timedelta(days=7))
    ).sum() if "data_abertura" in ed.columns else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.markdown(_card("Editais qualificados", f"{total}"), unsafe_allow_html=True)
    c2.markdown(_card("Valor total estimado", _money(valor_total)), unsafe_allow_html=True)
    c3.markdown(_card("Concreto / Brita", f"{concreto} / {brita}"), unsafe_allow_html=True)
    c4.markdown(_card("Abertura próximos 7d", f"{proximos_7d}"), unsafe_allow_html=True)

    st.markdown('<div class="cl-divider"></div>', unsafe_allow_html=True)

    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown("##### Distribuição por UF")
        if "uf" in ed.columns:
            por_uf = ed.groupby("uf").size().reset_index(name="qtd").sort_values("qtd", ascending=False)
            st.bar_chart(por_uf.set_index("uf"))
    with col_b:
        st.markdown("##### Top 5 oportunidades por valor")
        if "valor_estimado" in ed.columns:
            top = ed.nlargest(5, "valor_estimado")[["orgao", "municipio", "uf", "valor_estimado", "material"]]
            top["valor_estimado"] = top["valor_estimado"].apply(_money)
            st.dataframe(top, use_container_width=True, hide_index=True)


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
    st.pydeck_chart(deck, use_container_width=True)

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


def _aba_editais(ed: pd.DataFrame) -> None:
    st.subheader("Editais Qualificados")

    if ed.empty:
        st.info("Nenhum edital qualificado ainda. Rode `python scraper.py` para popular.")
        return

    # ----- Filtros adicionais -----
    col_a, col_b, col_c = st.columns([2, 1, 1])
    with col_a:
        busca = st.text_input("🔎 Buscar por órgão, município ou objeto", placeholder="ex: prefeitura, concreto…")
    with col_b:
        ordem = st.selectbox("Ordenar por", ["Mais recente", "Maior valor", "Menor distância", "Data abertura"])
    with col_c:
        modo = st.radio("Modo", ["Cards", "Tabela"], horizontal=True, label_visibility="collapsed")

    df = ed.copy()
    if busca:
        b = busca.lower()
        mask = (
            df.get("orgao", pd.Series([""] * len(df))).astype(str).str.lower().str.contains(b, na=False)
            | df.get("municipio", pd.Series([""] * len(df))).astype(str).str.lower().str.contains(b, na=False)
            | df.get("objeto", pd.Series([""] * len(df))).astype(str).str.lower().str.contains(b, na=False)
        )
        df = df[mask]

    if ordem == "Maior valor" and "valor_estimado" in df.columns:
        df = df.sort_values("valor_estimado", ascending=False)
    elif ordem == "Menor distância" and "distancia_km" in df.columns:
        df = df.sort_values("distancia_km", ascending=True)
    elif ordem == "Data abertura" and "data_abertura" in df.columns:
        df = df.sort_values("data_abertura", ascending=True)
    else:  # Mais recente
        if "data_execucao" in df.columns:
            df = df.sort_values("data_execucao", ascending=False)

    st.caption(f"**{len(df)}** editais encontrados")

    if modo == "Tabela":
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.download_button(
            "📥 Baixar CSV", data=df.to_csv(index=False).encode("utf-8"),
            file_name=f"editais_{datetime.now():%Y%m%d}.csv", mime="text/csv",
        )
        return

    # ----- Cards estilo ConLicitação -----
    for idx, row in enumerate(df.itertuples(index=False), start=1):
        d = row._asdict()
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

        # Score de confiança
        try:
            score_val = int(d.get("score") or 0)
        except (ValueError, TypeError):
            score_val = 0
        _score_map = {3: ("cl-score-3", "✅ CERTO"), 2: ("cl-score-2", "⚠️ PROVÁVEL"), 1: ("cl-score-1", "🔍 POSSÍVEL")}
        score_cls, score_txt = _score_map.get(score_val, ("cl-score-1", ""))
        tag_score_html = f'<span class="{score_cls}">{score_txt}</span>' if score_val else ""

        # Tag URGENTE (canto superior direito)
        tag_urgente_html = '<span class="cl-edital-urgent">URGENTE</span>' if urgente else ""

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

        # Botões
        botoes = []
        if link_origem:
            botoes.append(f'<a class="cl-btn cl-btn-primary" href="{link_origem}" target="_blank">📥 Baixar Edital</a>')
        if link_pncp:
            botoes.append(f'<a class="cl-btn cl-btn-secondary" href="{link_pncp}" target="_blank">🔍 Ver no PNCP</a>')
        botoes_html = "".join(botoes) if botoes else '<span style="color:#9CA3AF;font-size:0.85rem;">Sem links disponíveis</span>'

        modal_suffix = f" · {modalidade}" if modalidade else ""
        html = (
            f'<div class="cl-edital-card">'
            f'<div class="cl-edital-header">'
            f'<div style="display:flex;align-items:center;gap:0.5rem;">'
            f'<div class="cl-edital-num">{idx}</div>'
            f'{tag_score_html}'
            f'</div>'
            f'{tag_urgente_html}'
            f'</div>'
            f'<div class="cl-edital-body">'
            f'<div class="cl-edital-objeto"><b>Objeto:</b> {objeto[:400]}</div>'
            f'{kw_html}'
            f'{item_enc_html}'
            f'<div class="cl-edital-meta">'
            f'<div><b>Datas:</b> Documento: {data_ab}</div>'
            f'<div><b>Órgão:</b> <span style="color:#1E40AF;">{orgao}</span></div>'
            f'<div><b>Cidade:</b> 📍 {cidade}</div>'
            f'<div><b>Edital:</b> {num_edital}{modal_suffix}</div>'
            f'<div><b>Valor:</b> <span style="color:var(--cl-primary);font-weight:700;">{valor}</span> · '
            f'<span class="cl-tag {tag_material_class}">{material}</span> '
            f'<span class="cl-tag" style="background:#F3F4F6;color:#1F2937;">{dist_str} de {filial}</span>'
            f'</div>'
            f'</div>'
            f'</div>'
            f'<div class="cl-edital-actions">'
            f'<div class="cl-edital-actions-label">Ações:</div>'
            f'{botoes_html}'
            f'</div>'
            f'</div>'
        )
        st.markdown(html, unsafe_allow_html=True)


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
    if ultima:
        st.success(f"Última execução do scraper: **{ultima:%d/%m/%Y %H:%M}**")
    else:
        st.warning("Scraper ainda não foi executado.")

    if ed.empty or "data_execucao" not in ed.columns:
        st.info("Sem histórico para exibir.")
        return

    por_dia = ed.groupby(ed["data_execucao"].dt.date).agg(
        editais=("numero_controle_pncp", "count"),
        valor=("valor_estimado", "sum"),
    ).reset_index().rename(columns={"data_execucao": "data"})
    por_dia["valor_fmt"] = por_dia["valor"].apply(_money)

    st.markdown("##### Editais por dia")
    st.bar_chart(por_dia.set_index("data")["editais"])
    st.markdown("##### Valor agregado por dia")
    st.dataframe(por_dia[["data", "editais", "valor_fmt"]], use_container_width=True, hide_index=True)


# ===== Main =====
def main() -> None:
    if not _check_login():
        return

    fil, ed, ultima = _carregar_dados()
    filtros = _sidebar_filtros(ed, fil)
    ed_f = _aplica_filtros(ed, filtros)

    ultima_str = ultima.strftime("%d/%m/%Y %H:%M") if ultima else "—"
    st.markdown(
        f"""
        <div class="cl-header-bar">
            <div>
                <div class="cl-header-title">🏗️ Concrelagos Intelligence Hub</div>
                <div class="cl-header-sub">Rastreador autônomo de licitações públicas — PNCP</div>
            </div>
            <div style="text-align:right;">
                <div class="cl-header-sub">Última varredura</div>
                <div style="font-weight:600;">{ultima_str}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "📊 Visão Geral", "🗺️ Mapa", "📋 Editais", "🏭 Filiais", "📅 Diário",
    ])
    with tab1:
        _aba_visao_geral(ed_f, fil)
    with tab2:
        _aba_mapa(ed_f, fil)
    with tab3:
        _aba_editais(ed_f)
    with tab4:
        _aba_filiais(fil)
    with tab5:
        _aba_diario(ed_f, ultima)


if __name__ == "__main__":
    main()
