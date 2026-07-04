# RUNBOOK — Radar de Licitações Concrelagos

Guia de manutenção pra QUALQUER pessoa tocar o sistema (não depender do Igor).
Conselho 2026-07-04: ponto único de falha é o maior risco pós-entrega.

## O que é / onde roda
- **Coleta:** `radar.py` roda no **GitHub Actions** (grátis) da conta `concrelagos-licitacao`,
  repo **`radar-boletim`**, 7x/dia. Puxa PNCP + Querido Diário + Licitar Digital (só Pregão
  Eletrônico), filtra concreto/brita + raio de filial, grava a aba `Boletim Licitacoes` do
  Hub Sheet (ID `1FjmN8EDKQRcBflL7VOp7MzB6PeKNO0hcXLUUAoLbBbg`).
- **Filiais:** `sync_filiais.py` (roda antes do radar) lê a planilha de alvarás do Igor
  (`1QOiGyMwmvNhl...`), filtra as 7 empresas que licitam, escreve a aba `Filiais`.
- **Site:** `gerar_site.py` gera `docs/` (HTML+JSON) publicado no **GitHub Pages** —
  https://concrelagos-licitacao.github.io/radar-boletim/ (zero-runtime, grátis).
- **E-mail:** `boletim_email.py` manda o digest 1x/dia (08:15) pra `licitacao.concrelagos@gmail.com`.
- Tudo orquestrado por `.github/workflows/radar-boletim.yml`.

## Secrets (GitHub → Settings → Secrets and variables → Actions)
- `GOOGLE_CREDENTIALS_JSON` — service account do Google Sheets (conta de serviço).
- `GOOGLE_SHEETS_ID` — ID do Hub Sheet.
- `GMAIL_APP_PASSWORD`, `EMAIL_FROM`, `EMAIL_TO`, `EMAIL_CC` — envio do e-mail.
- `GEMINI_API_KEY` — resumo de edital por IA (opcional; se faltar, site funciona sem resumo).
NUNCA commitar secret. A conta de serviço precisa ter acesso de edição às 2 planilhas.

## Como saber que quebrou
- Site com o selo amarelo **"Dados podem estar DESATUALIZADOS"** no topo = coleta parou.
- Nenhum e-mail às 08:15 = workflow falhou.
- GitHub → aba **Actions** → run vermelho. Abra o run e veja o passo que falhou.

## Consertos comuns
- **Push do site rejeitado (non-fast-forward):** alguém deu push junto. Rode o workflow de novo
  (Actions → Boletim Licitacoes → Run workflow). Não empurre commits enquanto um run roda.
- **PNCP: 0 / TRUNCOU:** a API consulta do PNCP está sobrecarregada (servidor deles). Normal em
  alguns horários; as 7 coletas/dia compensam. Não é bug nosso.
- **Gemini 429:** cota grátis estourou. Resumo IA fica vazio; o resto funciona. Sem ação.
- **Filial nova não aparece:** confira a aba `Filiais_PENDENTES` do Hub (município que não
  geocodou) e o mapa `ALIAS` em `sync_filiais.py`.

## Rodar na mão (se o CI cair)
Na pasta do projeto, com Python 3.11 + `pip install -r requirements.txt` e o
`credenciais/service_account.json` no lugar:
```
python sync_filiais.py     # atualiza filiais
python radar.py            # coleta editais -> Boletim Licitacoes
python gerar_site.py       # gera docs/ (site)
```

## Backtest vs ConLicitação (prova de cobertura — semanal)
`backtest_conlic.py` compara o boletim do ConLic com o radar. Precisa do login do ConLic
(feito pelo Igor). Resultado acumula na aba `Backtest ConLic`. Rodar semanalmente por 2-3
semanas antes de decidir cancelar o ConLic. É PILOTO — não afirmar "100%", dizer "X/Y no piloto".
