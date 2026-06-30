import os
import smtplib
import gspread
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import date

SHEET_ID = '1FjmN8EDKQRcBflL7VOp7MzB6PeKNO0hcXLUUAoLbBbg'

# 'or' (nao .get default) porque o workflow passa o secret VAZIO quando ele nao existe
EMAIL_TO   = os.environ.get('EMAIL_TO')   or 'licitacao.concrelagos@gmail.com'
EMAIL_FROM = os.environ.get('EMAIL_FROM') or 'licitacao.concrelagos@gmail.com'
EMAIL_PASS = os.environ.get('GMAIL_APP_PASSWORD', '')
EMAIL_CC   = os.environ.get('EMAIL_CC', '')


CORES_UF = {
    'MG': '#1565C0', 'SP': '#6A1B9A', 'RJ': '#00695C',
    'ES': '#E65100', 'PR': '#558B2F', 'BA': '#F9A825',
}

def _cor(uf):
    return CORES_UF.get(str(uf).upper(), '#455A64')

def _truncar(txt, n=90):
    return (str(txt)[:n] + '…') if len(str(txt)) > n else str(txt)

def _badge_dist(km):
    try:
        v = float(km)
        if v <= 50:   return f'<span style="background:#2E7D32;color:#fff;padding:2px 7px;border-radius:10px;font-size:11px">{v:.0f} km</span>'
        if v <= 150:  return f'<span style="background:#F57F17;color:#fff;padding:2px 7px;border-radius:10px;font-size:11px">{v:.0f} km</span>'
        return f'<span style="background:#757575;color:#fff;padding:2px 7px;border-radius:10px;font-size:11px">{v:.0f} km</span>'
    except Exception:
        return ''


def gerar_html(rows, hoje):
    total = len(rows)
    por_uf = {}
    for r in rows:
        uf = str(r.get('UF', '')).upper()
        por_uf[uf] = por_uf.get(uf, 0) + 1

    badges_uf = ' '.join(
        f'<span style="background:{_cor(uf)};color:#fff;padding:3px 10px;border-radius:12px;font-size:12px;margin:2px">{uf} {n}</span>'
        for uf, n in sorted(por_uf.items(), key=lambda x: -x[1])
    )

    linhas = ''
    for i, r in enumerate(rows):
        bg = '#ffffff' if i % 2 == 0 else '#F8F9FA'
        uf  = str(r.get('UF', ''))
        mun = str(r.get('MUNICIPIO', ''))
        org = _truncar(r.get('ORGAO', ''), 55)
        obj = _truncar(r.get('OBJETO', ''), 95)
        dt  = str(r.get('DATA SESSAO', ''))
        km  = _badge_dist(r.get('DISTANCIA KM', ''))
        fil = _truncar(r.get('FILIAL PROXIMA', ''), 30)
        lnk = str(r.get('LINK', ''))
        btn = f'<a href="{lnk}" style="background:#1565C0;color:#fff;padding:4px 10px;border-radius:6px;font-size:11px;text-decoration:none">Ver edital</a>' if lnk.startswith('http') else ''

        linhas += f'''
        <tr style="background:{bg}">
          <td style="padding:8px 10px;font-size:12px">
            <span style="background:{_cor(uf)};color:#fff;padding:2px 8px;border-radius:10px;font-size:11px">{uf}</span>
            <span style="color:#555;margin-left:4px">{mun}</span>
          </td>
          <td style="padding:8px 10px;font-size:12px;color:#333">{org}</td>
          <td style="padding:8px 10px;font-size:12px;color:#555">{obj}</td>
          <td style="padding:8px 10px;font-size:12px;text-align:center;white-space:nowrap">{dt}</td>
          <td style="padding:8px 10px;text-align:center">{km}<br><span style="font-size:10px;color:#888">{fil}</span></td>
          <td style="padding:8px 10px;text-align:center">{btn}</td>
        </tr>'''

    planilha_url = f'https://docs.google.com/spreadsheets/d/{SHEET_ID}'

    return f'''<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#F0F4F8;font-family:Arial,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#F0F4F8">
<tr><td align="center" style="padding:24px 12px">

  <table width="680" cellpadding="0" cellspacing="0" style="max-width:680px;width:100%">

    <!-- CABECALHO -->
    <tr>
      <td style="background:#0D47A1;border-radius:10px 10px 0 0;padding:22px 28px">
        <span style="color:#fff;font-size:22px;font-weight:bold">Concrelagos</span>
        <span style="color:#90CAF9;font-size:14px;margin-left:12px">Boletim de Licitações</span>
        <div style="color:#BBDEFB;font-size:12px;margin-top:4px">{hoje.strftime('%d/%m/%Y')} — atualizado às 07h</div>
      </td>
    </tr>

    <!-- RESUMO -->
    <tr>
      <td style="background:#1565C0;padding:14px 28px">
        <span style="color:#fff;font-size:26px;font-weight:bold">{total}</span>
        <span style="color:#90CAF9;font-size:14px;margin-left:8px">editais encontrados hoje</span>
        <div style="margin-top:8px">{badges_uf}</div>
      </td>
    </tr>

    <!-- TABELA -->
    <tr>
      <td style="background:#fff;padding:0">
        <table width="100%" cellpadding="0" cellspacing="0">
          <tr style="background:#E3F2FD">
            <th style="padding:10px;font-size:11px;color:#1565C0;text-align:left;border-bottom:2px solid #BBDEFB">UF / MUNICÍPIO</th>
            <th style="padding:10px;font-size:11px;color:#1565C0;text-align:left;border-bottom:2px solid #BBDEFB">ÓRGÃO</th>
            <th style="padding:10px;font-size:11px;color:#1565C0;text-align:left;border-bottom:2px solid #BBDEFB">OBJETO</th>
            <th style="padding:10px;font-size:11px;color:#1565C0;text-align:center;border-bottom:2px solid #BBDEFB;white-space:nowrap">DATA SESSÃO</th>
            <th style="padding:10px;font-size:11px;color:#1565C0;text-align:center;border-bottom:2px solid #BBDEFB">DISTÂNCIA</th>
            <th style="padding:10px;font-size:11px;color:#1565C0;text-align:center;border-bottom:2px solid #BBDEFB">EDITAL</th>
          </tr>
          {linhas}
        </table>
      </td>
    </tr>

    <!-- RODAPE -->
    <tr>
      <td style="background:#ECEFF1;border-radius:0 0 10px 10px;padding:14px 28px;text-align:center">
        <a href="{planilha_url}" style="background:#0D47A1;color:#fff;padding:8px 20px;border-radius:8px;font-size:13px;text-decoration:none">
          Abrir planilha completa
        </a>
        <div style="color:#90A4AE;font-size:11px;margin-top:10px">
          Boletim automático — Equipe Jurídica Concrelagos · licitacao@concrelagos.com.br
        </div>
      </td>
    </tr>

  </table>
</td></tr>
</table>
</body>
</html>'''


def main():
    creds_path = os.environ.get('GOOGLE_SHEETS_CREDENTIALS_PATH', 'credenciais/service_account.json')
    gc = gspread.service_account(filename=creds_path)
    # head=2: a linha 1 da aba e o banner; o cabecalho real esta na linha 2
    rows = gc.open_by_key(SHEET_ID).worksheet('Boletim Licitacoes').get_all_records(head=2)

    if not rows:
        print('Boletim vazio — e-mail nao enviado.')
        return

    hoje = date.today()
    html = gerar_html(rows, hoje)
    assunto = f'Boletim Licitações {hoje.strftime("%d/%m")} — {len(rows)} editais'

    if not EMAIL_PASS:
        print('GMAIL_APP_PASSWORD nao configurado.')
        print('Assunto:', assunto)
        print('Destinatario:', EMAIL_TO)
        print('HTML gerado com', len(rows), 'editais.')
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

    print(f'E-mail enviado para {EMAIL_TO} — {len(rows)} editais.')


if __name__ == '__main__':
    main()
