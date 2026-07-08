# -*- coding: utf-8 -*-
"""E-mail = AVISO (decisao do dono 2026-07-07): o e-mail NAO mostra mais a tabela do boletim.
So notifica que o boletim do dia esta pronto e manda ver no site (fonte unica). Isso (a) mata a
fragilidade de montar HTML de tabela no caminho do sendmail e (b) acaba com a divergencia
e-mail<->site (o e-mail nao duplica dado; so linka). Blindado: qualquer erro de dado NUNCA
impede o e-mail de sair, e o envio nunca derruba o job (o .yml tem `|| echo`)."""
import os
import smtplib
import gspread
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import date

SHEET_ID = '1FjmN8EDKQRcBflL7VOp7MzB6PeKNO0hcXLUUAoLbBbg'
SITE_URL = os.environ.get('SITE_URL', 'https://concrelagos-licitacao.github.io/radar-boletim')

# 'or' (nao .get default) porque o workflow passa o secret VAZIO quando ele nao existe
EMAIL_TO   = os.environ.get('EMAIL_TO')   or 'licitacao.concrelagos@gmail.com'
EMAIL_FROM = os.environ.get('EMAIL_FROM') or 'licitacao.concrelagos@gmail.com'
EMAIL_PASS = os.environ.get('GMAIL_APP_PASSWORD', '')
EMAIL_CC   = os.environ.get('EMAIL_CC', '')

CORES_UF = {'MG': '#1565C0', 'SP': '#6A1B9A', 'RJ': '#00695C',
            'ES': '#E65100', 'PR': '#558B2F', 'BA': '#F9A825'}


def _cor(uf):
    return CORES_UF.get(str(uf).upper(), '#455A64')


def gerar_aviso(rows, hoje, truncou='', leitura_ok=True):
    """Monta o e-mail de AVISO (nao a tabela). `rows` = editais do boletim do dia (so pra contar
    e resumir por UF). `leitura_ok`=False quando NAO deu p/ ler a planilha (nao afirmar 'zero').
    Blindado: qualquer campo estranho nao lanca excecao."""
    total = len(rows or [])
    por_uf = {}
    for r in (rows or []):
        try:
            uf = str(r.get('UF', '')).upper().strip()
            if uf:
                por_uf[uf] = por_uf.get(uf, 0) + 1
        except Exception:
            continue
    badges = ' '.join(
        '<span style="background:%s;color:#fff;padding:3px 11px;border-radius:12px;font-size:13px;margin:2px;display:inline-block">%s %d</span>'
        % (_cor(uf), uf, n) for uf, n in sorted(por_uf.items(), key=lambda x: -x[1]))

    aviso_trunc = ''
    if truncou:
        aviso_trunc = (
            '<tr><td style="background:#FFF3E0;border-left:4px solid #FB8C00;padding:12px 28px">'
            '<span style="color:#E65100;font-size:13px;font-weight:bold">Cobertura parcial hoje:</span> '
            '<span style="color:#8D6E63;font-size:13px">o PNCP nao respondeu por completo em ' + str(truncou) +
            '. Pode haver editais desses estados fora da lista — confira direto no PNCP se for critico.</span>'
            '</td></tr>')

    frase = ('%d edital de concreto/brita no raio de atendimento hoje.' % total if total == 1
             else '%d editais de concreto/brita no raio de atendimento hoje.' % total)
    if total == 0:
        frase = 'Nenhum edital novo de concreto/brita no raio hoje. O historico segue no site.'
    if not leitura_ok:                       # leitura falhou -> nao mentir 'zero'; distinguir de zero-real
        frase = 'Boletim do dia pronto — nao consegui contar os editais agora. Confira direto no site.'

    return '''<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#F0F4F8;font-family:Arial,Helvetica,sans-serif">
<table width="100%%" cellpadding="0" cellspacing="0" style="background:#F0F4F8"><tr><td align="center" style="padding:26px 12px">
  <table width="560" cellpadding="0" cellspacing="0" style="max-width:560px;width:100%%">
    <tr><td style="background:#0D47A1;border-radius:10px 10px 0 0;padding:22px 28px">
      <span style="color:#fff;font-size:22px;font-weight:bold">Concrelagos</span>
      <span style="color:#90CAF9;font-size:14px;margin-left:12px">Radar de Licitacoes</span>
      <div style="color:#BBDEFB;font-size:12px;margin-top:4px">%s</div></td></tr>
    <tr><td style="background:#1565C0;padding:20px 28px">
      <div style="color:#fff;font-size:17px;font-weight:bold;line-height:1.4">%s</div>
      <div style="margin-top:10px">%s</div></td></tr>
    %s
    <tr><td style="background:#fff;padding:26px 28px;text-align:center">
      <a href="%s" style="background:#C28E2C;color:#fff;padding:13px 30px;border-radius:8px;font-size:15px;font-weight:bold;text-decoration:none;display:inline-block">Ver o boletim no site &rarr;</a>
      <div style="color:#6B7280;font-size:12px;margin-top:14px">O e-mail e so um aviso. Os editais, distancias, precos e a analise estao no site.</div>
    </td></tr>
    <tr><td style="background:#F8F9FA;border-radius:0 0 10px 10px;padding:16px 28px;border-top:1px solid #E6E8EC">
      <div style="color:#9CA3AF;font-size:11px;line-height:1.5"><b>Concrelagos Concreto S/A &middot; uso interno.</b><br>
      Dados publicos (PNCP &middot; Diario Oficial &middot; Licitar Digital), so Pregao Eletronico. Confira sempre o edital na fonte.</div>
    </td></tr>
  </table>
</td></tr></table></body></html>''' % (hoje.strftime('%d/%m/%Y'), frase, badges, aviso_trunc, SITE_URL)


def main():
    creds_path = os.environ.get('GOOGLE_SHEETS_CREDENTIALS_PATH', 'credenciais/service_account.json')
    # Blindagem: montar a mensagem NUNCA pode quebrar o envio. Se algo falhar ao ler o Sheet,
    # manda o aviso minimo (o site continua sendo a fonte).
    rows, truncou, leitura_ok = [], '', True
    try:
        gc = gspread.service_account(filename=creds_path)
        sh = gc.open_by_key(SHEET_ID)
        rows = sh.worksheet('Boletim Licitacoes').get_all_records(head=2)
        try:
            saude = sh.worksheet('Saude Boletim').get_all_values()
            if len(saude) > 1 and 'TRUNCOU' in saude[0]:
                ci = saude[0].index('TRUNCOU')
                if len(saude[-1]) > ci:
                    truncou = str(saude[-1][ci]).strip()
        except Exception as e:
            print('  (aviso de truncamento indisponivel: %s)' % repr(e)[:50])
    except Exception as e:
        leitura_ok = False   # leitura falhou: NAO afirmar 'zero editais' (mentira num dia que tinha)
        print('  (nao consegui ler o boletim p/ contar: %s) — enviando aviso minimo' % repr(e)[:60])

    hoje = date.today()
    try:
        html = gerar_aviso(rows, hoje, truncou, leitura_ok)
    except Exception as e:
        print('  (gerar_aviso falhou: %s) — aviso texto-puro' % repr(e)[:60])
        html = ('<p>Boletim de licitacoes de %s pronto. Veja em %s</p>'
                % (hoje.strftime('%d/%m/%Y'), SITE_URL))

    assunto = ('Radar Licitacoes %s — boletim pronto (ver no site)' % hoje.strftime('%d/%m') if not leitura_ok
               else 'Radar Licitacoes %s — %d edital(is) no raio' % (hoje.strftime('%d/%m'), len(rows)))

    if not EMAIL_PASS:
        print('GMAIL_APP_PASSWORD nao configurado. Assunto:', assunto, '| Destinatario:', EMAIL_TO)
        return

    msg = MIMEMultipart('alternative')
    msg['Subject'] = assunto
    msg['From'] = EMAIL_FROM
    msg['To'] = EMAIL_TO
    if EMAIL_CC:
        msg['Cc'] = EMAIL_CC
    msg.attach(MIMEText(html, 'html', 'utf-8'))

    destinatarios = [EMAIL_TO] + ([EMAIL_CC] if EMAIL_CC else [])
    with smtplib.SMTP('smtp.gmail.com', 587) as s:
        s.starttls()
        s.login(EMAIL_FROM, EMAIL_PASS)
        s.sendmail(EMAIL_FROM, destinatarios, msg.as_string())
    print('Aviso enviado para %s — %d editais no boletim.' % (EMAIL_TO, len(rows)))


if __name__ == '__main__':
    main()
