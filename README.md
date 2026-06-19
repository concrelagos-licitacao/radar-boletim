# Concrelagos — Histórico Comercial (Google Apps Script)

Dashboard **somente leitura**, hospedado no Google Apps Script, que espelha a planilha
comercial **CONTROLE - LICITAÇÕES** (abas `PREGOES` / `GANHAS` / `ADITIVOS`) para a diretoria.
Sem senha, sem servidor externo — roda dentro do Google.

## Arquivos
- `appscript/Code.gs` — backend: lê a planilha (`SpreadsheetApp`), calcula KPIs/gráficos, cache de 60s.
- `appscript/Dashboard.html` — painel: KPIs, gráficos (Chart.js), contratos a vencer e abas com busca.

> Estes arquivos são **colados no editor do Apps Script** (não há deploy por este repositório).

## Como publicar (sem senha)
1. Abra a planilha **CONTROLE - LICITAÇÕES** → **Extensões → Apps Script**.
2. Substitua o `Code.gs` pelo conteúdo de `appscript/Code.gs`.
3. **+ → HTML**, nomeie **`Dashboard`** (sem `.html`), cole `appscript/Dashboard.html`. Salve.
4. **Implantar → Nova implantação → App da Web**:
   - **Executar como:** Eu
   - **Quem pode acessar:** **Qualquer pessoa** (é isto que dispensa o login)
5. Autorize quando o Google pedir. Copie a **URL do app da Web** → é o link da diretoria.

Para atualizar depois de mexer no código: **Implantar → Gerenciar implantações → editar (lápis) → Nova versão**.

## Notas
- **Somente leitura** de propósito: como o link é público, não há formulário de gravação
  (lançamentos são feitos direto na planilha). A planilha continua privada — o script lê como dono.
- Spreadsheet ID no `Code.gs`: `1FjmN8EDKQRcBflL7VOp7MzB6PeKNO0hcXLUUAoLbBbg`. Se `openById`
  falhar, abra a planilha e **Arquivo → Salvar como Planilha Google**, e troque o ID.

---
> Histórico: este repositório já foi um rastreador PNCP completo (scraper + ConLicitação + IA + site
> Streamlit). Esse projeto inteiro está preservado na tag git **`projeto-completo-pre-corte`**
> (`git checkout projeto-completo-pre-corte`).
