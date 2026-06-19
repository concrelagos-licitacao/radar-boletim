# Concrelagos — Histórico Comercial

Site enxuto para a diretoria visualizar o **histórico comercial de licitações** (pregões,
contratos ganhos e aditivos), espelhando a planilha Google "CONTROLE - LICITAÇÕES".

- **App:** `app.py` (Streamlit, página única: aba **Histórico**).
- **Dados:** planilha comercial no Google Sheets (abas `PREGOES`, `GANHAS`, `ADITIVOS`),
  lida via conta de serviço. Edições na planilha aparecem no site em até ~1 min (cache 60s).
- **Acesso:** login por senha (`st.secrets["auth"]["password"]` no Streamlit Cloud, ou
  `APP_PASSWORD` no `.env` local).

## Rodar local

```bash
pip install -r requirements.txt
streamlit run app.py
```

Precisa de credenciais Google: `GOOGLE_SHEETS_CREDENTIALS_PATH` apontando para o JSON da
conta de serviço (`credenciais/service_account.json`), que deve ter acesso de leitura à
planilha comercial. Para o formulário "Adicionar registro" gravar, a conta precisa ser
**Editor** da planilha.

## Deploy (Streamlit Community Cloud)

Repositório → `app.py` → Python 3.11. Em **Secrets**, configure `[auth] password` e
`[gcp.service_account]` (conteúdo do JSON). O `.github/workflows/keepalive.yml` mantém o
app acordado.

> Histórico do projeto completo (scraper PNCP, ConLicitação, IA) preservado na tag git
> `projeto-completo-pre-corte` — recuperável com `git checkout projeto-completo-pre-corte`.
