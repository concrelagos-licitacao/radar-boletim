# Concrelagos Intelligence Hub — Deploy 24/7

Tempo total estimado: **15-20 minutos** de cliques (sem programar).

Esse guia coloca:
- **Scraper rodando 3x/dia automaticamente** (06h, 12h, 18h Brasília) no **GitHub Actions** — grátis.
- **Dashboard online 24/7** no **Streamlit Community Cloud** — grátis.

---

## Pré-requisitos

- [x] Conta no GitHub (tem)
- [x] Service Account JSON em `credenciais/service_account.json` (tem)
- [x] Planilha "Concrelagos Hub" compartilhada com a Service Account (tem)
- [x] `.env` local funcionando (tem)

---

## Parte 1: subir o código pro GitHub (5 min)

### 1.1 Criar repositório privado no GitHub

1. Abra https://github.com/new
2. Nome: `concrelagos-intelligence-hub` (ou outro)
3. **Visibility: Private** ✅
4. **NÃO** marque "Add a README", "Add .gitignore", nem "license" (já temos)
5. Clique **Create repository**
6. Anote a URL (algo como `https://github.com/SEU_USUARIO/concrelagos-intelligence-hub`)

### 1.2 Inicializar git localmente e fazer push

Abra **PowerShell** ou **Git Bash** dentro da pasta do projeto e rode:

```bash
cd "C:/Users/CONCRELAGOS/Dropbox/BIBLIOTECA JURIDICA - GERAL/CONCRELAGOS/12 - Equipe Jurídica/IGOR (ESTAGIÁRIO)/IGOR ESTAGIARIO JURÍDICO/LICITAÇÕES- PROJETO SITE"

git init
git branch -M main
git add .
git status   # confira: NENHUM arquivo de credenciais/, .env, ou EMPRESAS/*.pdf deve aparecer
git commit -m "feat: Concrelagos Intelligence Hub - infra completa (scraper + dashboard + deploy)"

# substitua SEU_USUARIO/SEU_REPO pelo que você criou
git remote add origin https://github.com/SEU_USUARIO/concrelagos-intelligence-hub.git
git push -u origin main
```

Se pedir autenticação, use seu usuário GitHub + um **Personal Access Token** (Settings → Developer Settings → Tokens → Generate, marque `repo`).

### 1.3 Validar que segredos NÃO subiram

Abra o repo no GitHub e confirme que **NÃO existe**:
- `.env`
- `credenciais/service_account.json`
- `EMPRESAS/*.pdf` (a pasta deve estar vazia ou nem existir no GitHub)

Se algum subiu por engano, **delete imediatamente do GitHub**, gere um novo Service Account JSON no GCP, e me avise.

---

## Parte 2: configurar GitHub Actions (scraper automático) (5 min)

### 2.1 Cadastrar GitHub Secrets

No seu repo no GitHub:

1. **Settings** → **Secrets and variables** → **Actions** → **New repository secret**
2. Adicione 2 secrets:

| Nome | Valor |
|---|---|
| `GOOGLE_SHEETS_ID` | `13R-89b7f0SOaYlrHziKTxhIipXi-VVuFaLF6sIQQtk4` |
| `SERVICE_ACCOUNT_JSON` | **Conteúdo INTEIRO** do arquivo `credenciais/service_account.json` (abra o arquivo, copie tudo, cole) |

### 2.2 Testar manualmente

1. **Actions** → "Scraper PNCP (Concrelagos)"
2. Botão **Run workflow** → branch `main` → **Run workflow**
3. Aguarde ~5-30 min (depende do humor do PNCP). Deve ficar verde ✅.
4. Veja a planilha "Concrelagos Hub" → aba "Novas Licitações" → novos editais aparecendo.

A partir daqui, ele roda **sozinho 3x/dia** (06h, 12h, 18h Brasília).

---

## Parte 3: dashboard online (Streamlit Cloud) (5 min)

### 3.1 Criar conta no Streamlit Cloud

1. Abra https://share.streamlit.io/
2. Clique **Sign up with GitHub** (usa sua conta GitHub)
3. Autorize o Streamlit a acessar seus repos privados

### 3.2 Deploy do app

1. **New app** → **Deploy from GitHub**
2. Selecione:
   - **Repository**: `SEU_USUARIO/concrelagos-intelligence-hub`
   - **Branch**: `main`
   - **Main file path**: `app.py`
3. **Advanced settings** → **Python version**: `3.11`
4. **Deploy!** (vai instalar dependências, demora ~3 min)

### 3.3 Configurar secrets do app

1. Quando o app subir (vai dar erro de credenciais — esperado), clique no **⋮** (menu) do app → **Settings** → **Secrets**
2. Cole o conteúdo abaixo, **substituindo as `...` pelos valores reais** do seu `credenciais/service_account.json`:

```toml
[auth]
password = "TROQUE_AQUI_POR_UMA_SENHA_FORTE"

[gcp]
sheets_id = "13R-89b7f0SOaYlrHziKTxhIipXi-VVuFaLF6sIQQtk4"

[gcp.service_account]
type = "service_account"
project_id = "concrelagos-hub"
private_key_id = "..."          # do JSON
private_key = """-----BEGIN PRIVATE KEY-----
... cole AQUI o conteúdo inteiro do private_key, mantendo as quebras de linha
-----END PRIVATE KEY-----
"""
client_email = "scraper-bot@concrelagos-hub.iam.gserviceaccount.com"
client_id = "..."              # do JSON
auth_uri = "https://accounts.google.com/o/oauth2/auth"
token_uri = "https://oauth2.googleapis.com/token"
auth_provider_x509_cert_url = "https://www.googleapis.com/oauth2/v1/certs"
client_x509_cert_url = "..."   # do JSON
universe_domain = "googleapis.com"
```

3. **Save** → o app reinicia em ~30s.

### 3.4 Acessar o site público

URL será algo como:
```
https://concrelagos-intelligence-hub.streamlit.app
```

Compartilhe com a diretoria. Login: a senha que você definiu em `[auth]password`.

---

## Limites do plano grátis

| Recurso | Limite | Estamos longe? |
|---|---|---|
| GitHub Actions (executa scraper) | 2000 min/mês (privado) | Sim: 3 runs × ~5min × 30 dias = 450min. Sobra 75%. |
| Streamlit Community Cloud | 1 app público OU 3 privados, 1GB RAM | OK |
| Nominatim (geocoder OSM) | 1 req/s | Já respeitamos (sleep 1.1s) |
| PNCP API | sem limite documentado | Usamos com retry 2x e timeout 60s |

---

## Manter o app sempre acordado (eliminar cold start)

O Streamlit Community Cloud hiberna o app após ~1h de inatividade. Duas opções gratuitas:

### Opção A — UptimeRobot (recomendado, 5 min de configuração)

1. Crie conta gratuita em **https://uptimerobot.com**
2. Clique **Add New Monitor**
3. Preencha:
   - **Monitor Type**: HTTP(s)
   - **Friendly Name**: Concrelagos Hub
   - **URL**: `https://concrelagos-intelligence-viynfmh4nlzrfjdktekn2f.streamlit.app/`
   - **Monitoring Interval**: 5 minutes
4. **Create Monitor**. Pronto.

O UptimeRobot pinga o app a cada 5 min gratuitamente. O app nunca dorme.
Bônus: você recebe e-mail de alerta se o app cair.

### Opção B — GitHub Actions (já está no repositório)

O arquivo `.github/workflows/keepalive.yml` pinga o app a cada 30 min durante o horário comercial (06h–20h Brasília). Basta fazer o push — já está ativado.

Para **desativar**: GitHub → Actions → "Keep-Alive Streamlit" → ⋯ → Disable workflow.

---

## Manutenção rotineira

- **Adicionar nova filial**: edite `dados/filiais.csv`, rode `python bootstrap.py --force` localmente, faça `git commit` e `git push`. O GitHub Actions pega na próxima execução.
- **Mudar palavras-chave**: edite `KEYWORDS_CONCRETO`/`KEYWORDS_BRITA` em `scraper.py`, commit, push.
- **Trocar senha do dashboard**: Streamlit Cloud → app → Settings → Secrets → mude `[auth] password`.
- **Pausar o scraper**: GitHub → repo → Actions → "Scraper PNCP" → ⋯ → Disable workflow.
- **Forçar execução agora**: GitHub → Actions → "Scraper PNCP" → Run workflow.

---

## Solução de problemas

| Sintoma | Causa provável | Como resolver |
|---|---|---|
| Streamlit mostra "Credenciais ausentes" | Secrets vazios ou TOML mal formado | Refaça Parte 3.3, atenção à indentação |
| GitHub Action falha em "Restaurar Service Account" | Secret `SERVICE_ACCOUNT_JSON` mal colado | Re-cole o JSON inteiro, sem quebras estranhas |
| 0 editais qualificados após várias execuções | PNCP fora do ar OU keywords muito restritas | Veja `logs/` artifact no GitHub Actions. Se PNCP retornou >100 brutos mas 0 filtrados, ajuste keywords. |
| App "dormindo" / lento na primeira visita | Streamlit Cloud hiberna apps sem acesso há horas | Normal. Carrega em ~30s na primeira visita. |
| GitHub Action passou de 2000min no mês | Estourou cota | Reduza para 1x/dia editando o cron em `.github/workflows/scraper.yml` |

---

## Próximos passos sugeridos (futuras versões)

- **Notificações push** quando edital aparecer (e-mail / WhatsApp via Twilio)
- **Resumo do edital com IA** (botão "Resumir" do ConLicitação) usando Anthropic API
- **Domínio customizado** (concrelagos-hub.com.br) — paga no Streamlit Cloud Teams ou usa CloudFlare em frente
- **Histórico em BigQuery** ao invés de só Sheets (escala melhor depois de meses)
- **Migração para Google Maps Distance Matrix** quando a diretoria autorizar o gasto (~R$ 50/mês para 10k requisições)
