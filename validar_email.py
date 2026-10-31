#!/usr/bin/env python3
"""Autoteste REAL e estritamente unitário da conta de e-mail configurada.

- 'conexao': apenas autenticação SMTP e IMAP; sem envio.
- 'enviar-teste': uma única mensagem PARA A PRÓPRIA CONTA configurada.
- 'conferir': verifica recebimento IMAP, sem marcar e-mails como lidos.

Esta ferramenta NÃO usa o cadastro de leads, NÃO tem modo de envio em massa,
NÃO permite selecionar destinatários externos e NÃO altera o funil comercial.
"""
from __future__ import annotations

import argparse
import getpass
import imaplib
import json
import os
import secrets
import smtplib
import ssl
import sys
from datetime import datetime, timezone, timedelta
from email import policy
from email.message import EmailMessage
from email.parser import BytesHeaderParser
from email.utils import make_msgid
from pathlib import Path

from envios import CFG_DEFAULT, carregar_config
from pipeline import ROOT

REGISTRO = ROOT / 'dados' / 'autoteste_email.json'


def senha(tipo: str, reutilizar: str | None = None) -> str:
    key = 'CSA_SMTP_PASSWORD' if tipo == 'SMTP' else 'CSA_IMAP_PASSWORD'
    valor = os.environ.get(key) or reutilizar or getpass.getpass(f'Senha de aplicativo {tipo} (não será salva): ')
    if not valor:
        raise ValueError(f'Senha {tipo} vazia')
    return valor


def conectar_smtp(cfg, password):
    context = ssl.create_default_context()
    if cfg['modo'] == 'ssl':
        session = smtplib.SMTP_SSL(cfg['host'], cfg['porta'], timeout=30, context=context)
    else:
        session = smtplib.SMTP(cfg['host'], cfg['porta'], timeout=30)
    try:
        session.ehlo()
        if cfg['modo'] == 'starttls':
            session.starttls(context=context)
            session.ehlo()
        session.login(cfg['user'], password)
        return session
    except BaseException:
        session.close()
        raise


def conectar_imap(cfg, password):
    session = imaplib.IMAP4_SSL(cfg['imap_host'], cfg['imap_porta'], ssl_context=ssl.create_default_context(), timeout=30)
    try:
        session.login(cfg['imap_user'], password)
        kind, _ = session.select('INBOX', readonly=True)
        if kind != 'OK':
            raise RuntimeError('Não foi possível abrir INBOX em modo leitura')
        return session
    except BaseException:
        session.logout()
        raise


def verificar(cfg):
    ps = senha('SMTP')
    with conectar_smtp(cfg, ps):
        print('SMTP: autenticação e TLS OK. NENHUM e-mail enviado.')
    pi = senha('IMAP', ps if cfg['imap_user'] == cfg['user'] else None)
    with conectar_imap(cfg, pi):
        print('IMAP: autenticação e leitura da INBOX OK.')


def criar_msg_teste(cfg, codigo):
    conta = cfg['de']
    m = EmailMessage()
    m['From'] = conta
    m['To'] = conta  # Restrição proposital: somente para o próprio endereço.
    m['Subject'] = f'[CSA VALIDACAO {codigo}] Teste de entrega'
    m['Message-ID'] = make_msgid(domain=conta.split('@', 1)[1])
    m.set_content('Teste autorizado do sistema CSA Prospecção.\n'
                  'Esta é uma única mensagem enviada para o próprio remetente.\n'
                  'Nenhuma empresa foi contatada.\n')
    return m


def enviar_teste(cfg, registro: Path = REGISTRO):
    # Não repetir automaticamente após resultado ambíguo ou teste anterior pendente.
    if registro.exists():
        try:
            anterior = json.loads(registro.read_text(encoding='utf-8'))
            if anterior.get('estado') != 'recebido':
                raise ValueError('Existe autoteste pendente/incerto. Confira sua caixa e o arquivo dados/autoteste_email.json. Não repetir automaticamente.')
        except json.JSONDecodeError:
            raise ValueError('Registro de teste ilegível. Inspecione antes de fazer novo envio.')
    print(f'O teste vai enviar EXATAMENTE UMA mensagem de {cfg["de"]} PARA {cfg["de"]}.')
    print('Nenhum outro destinatário pode ser selecionado. Nenhum lead será alterado.')
    if input('Digite TESTAR-MEU-EMAIL para autorizar: ').strip() != 'TESTAR-MEU-EMAIL':
        print('Cancelado. Nenhum e-mail enviado.')
        return
    ps = senha('SMTP')
    codigo = secrets.token_hex(6).upper()
    msg = criar_msg_teste(cfg, codigo)
    registro.parent.mkdir(parents=True, exist_ok=True)
    registro.write_text(json.dumps({'codigo': codigo, 'message_id': str(msg['Message-ID']),
                                    'estado': 'incerto', 'criado_utc': datetime.now(timezone.utc).isoformat()},indent=2), encoding='utf-8')
    try:
        with conectar_smtp(cfg, ps) as smtp:
            smtp.send_message(msg, from_addr=cfg['de'], to_addrs=[cfg['de']])
    except Exception:
        print('Não foi possível confirmar o aceite pelo SMTP. Pode ter sido enviado; NÃO repita sem conferir.', file=sys.stderr)
        raise
    registro.write_text(json.dumps({'codigo': codigo, 'message_id': str(msg['Message-ID']),
                                    'estado': 'smtp_aceitou', 'criado_utc': datetime.now(timezone.utc).isoformat()},indent=2), encoding='utf-8')
    print(f'SMTP aceitou UM e-mail para a sua própria conta. Código: {codigo}.')
    print('Isso NÃO comprova entrega. Use a opção de conferência IMAP.')


def conferir(cfg, registro: Path = REGISTRO):
    if not registro.exists():
        raise ValueError('Primeiro execute o envio de teste; nenhum registro encontrado.')
    data = json.loads(registro.read_text(encoding='utf-8'))
    codigo = data.get('codigo', '')
    if not codigo:
        raise ValueError('Registro incompleto')
    pi = senha('IMAP')
    with conectar_imap(cfg, pi) as box:
        inicio = (datetime.now(timezone.utc) - timedelta(days=7)).strftime('%d-%b-%Y')
        typ, payload = box.uid('SEARCH', None, 'SINCE', inicio)
        if typ != 'OK':
            raise RuntimeError('Busca IMAP indisponível')
        uids = (payload[0] or b'').split()[-500:]
        for uid in reversed(uids):
            typ, rows = box.uid('FETCH', uid, '(BODY.PEEK[HEADER.FIELDS (SUBJECT MESSAGE-ID)])')
            if typ != 'OK':
                continue
            cab = next((x[1] for x in rows if isinstance(x, tuple) and isinstance(x[1], bytes)), None)
            if not cab:
                continue
            h = BytesHeaderParser(policy=policy.default).parsebytes(cab)
            if f'[CSA VALIDACAO {codigo}]' not in str(h.get('Subject', '')):
                continue
            if str(h.get('Message-ID', '')).strip() != data.get('message_id'):
                continue
            data['estado'] = 'recebido'
            data['recebido_utc'] = datetime.now(timezone.utc).isoformat()
            registro.write_text(json.dumps(data, indent=2), encoding='utf-8')
            print('SUCESSO: mensagem recebida na INBOX pelo IMAP sem alterar o status de leitura.')
            return True
    print('Ainda não encontrada na INBOX. Confira também a pasta Spam e as regras do provedor.')
    print('SMTP aceito não garante entrega ou ausência de filtro anti-spam.')
    return False


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('acao', choices=('conexao', 'enviar-teste', 'conferir'))
    p.add_argument('--config', type=Path, default=CFG_DEFAULT)
    args = p.parse_args()
    try:
        cfg = carregar_config(args.config)
        if cfg['de'] == 'exemplo@seudominio.com.br':
            raise ValueError('Substitua o remetente de exemplo no email_config.ini antes de conectar.')
        if args.acao == 'conexao':
            verificar(cfg)
        elif args.acao == 'enviar-teste':
            enviar_teste(cfg)
        else:
            conferir(cfg)
        return 0
    except (ValueError, OSError, RuntimeError, smtplib.SMTPException, imaplib.IMAP4.error) as e:
        print(f'Falha no autoteste: {type(e).__name__}: {e}',file=sys.stderr)
        return 1

if __name__ == '__main__':
    raise SystemExit(main())
