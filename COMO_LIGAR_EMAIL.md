# Como ligar a notificação por e-mail (boletim por estado)

O sistema **já está pronto** para enviar um boletim por estado para
`licitacao.concrelagos@gmail.com`. Falta apenas informar **de qual conta**
enviar e a **senha de app** dessa conta. São 2 partes, ~10 minutos.

> Recomendado: usar a própria conta `licitacao.concrelagos@gmail.com` como
> remetente (envia para si mesma). Pode ser qualquer conta Gmail da empresa.

---

## Parte A — Gerar a "Senha de app" no Gmail (uma vez)

A senha de app é um código de 16 letras que permite o sistema enviar e-mail
**sem** expor a senha real da conta. Exige verificação em 2 etapas ligada.

1. Entre na conta Google que vai **enviar** (ex.: `licitacao.concrelagos@gmail.com`).
2. Acesse **https://myaccount.google.com/security**
3. Em **"Como você faz login no Google"**, ligue a **Verificação em duas etapas**
   (se já estiver ligada, pule).
4. Acesse **https://myaccount.google.com/apppasswords**
   - Em "Nome do app", escreva: `Concrelagos Hub`
   - Clique em **Criar**.
5. Aparece um código de **16 letras** (ex.: `abcd efgh ijkl mnop`).
   **Copie** (pode apagar os espaços; tanto faz).

> Guarde esse código com cuidado — ele dá permissão de enviar e-mail por essa conta.

---

## Parte B — Cadastrar nos Secrets do GitHub (uma vez)

1. Acesse:
   **https://github.com/juridicoconcrelagos/concrelagos-intelligence-hub/settings/secrets/actions**
   (logado na conta dona do repositório `juridicoconcrelagos`).
2. Para cada item abaixo, clique em **"New repository secret"**, preencha **Name**
   e **Secret**, e clique em **Add secret**:

   | Name (exatamente assim)   | Secret (valor)                                  |
   |---------------------------|--------------------------------------------------|
   | `NOTIFICACAO_EMAIL_DE`    | `licitacao.concrelagos@gmail.com` (a conta que envia) |
   | `NOTIFICACAO_EMAIL_SENHA` | a senha de app de 16 letras da Parte A           |

   > `NOTIFICACAO_EMAIL_PARA` **não é obrigatório** — o destino já é
   > `licitacao.concrelagos@gmail.com` por padrão. Só crie se quiser enviar
   > para outros e-mails (separados por vírgula).

   > Se um desses nomes **já existir**, clique nele e use **"Update"** para
   > corrigir o valor.

---

## Parte C — Testar

1. Acesse a aba **Actions** do repositório:
   **https://github.com/juridicoconcrelagos/concrelagos-intelligence-hub/actions**
2. Clique no workflow **"Scraper PNCP (Concrelagos)"** → botão **"Run workflow"**
   → **Run workflow** (deixe as datas em branco).
3. Aguarde alguns minutos. Se houver editais novos, chega **um e-mail por estado**
   em `licitacao.concrelagos@gmail.com`, cada um com botão **"Abrir boletim de XX"**.
   - Se **não houver editais novos** naquele momento, nenhum e-mail é enviado
     (isso é normal). O próximo envio acontece quando surgir edital novo, nas
     execuções automáticas (7×/dia).

---

## Resolução de problemas

- **Não chegou e-mail e havia editais novos:** confira no log do run (aba Actions)
  a linha de notificação. Erro de autenticação = a senha de app está errada
  (gere outra na Parte A). Confirme também que `NOTIFICACAO_EMAIL_DE` é a **mesma**
  conta onde você gerou a senha de app.
- **Caiu no spam:** marque como "não é spam" uma vez; os próximos chegam na caixa.
