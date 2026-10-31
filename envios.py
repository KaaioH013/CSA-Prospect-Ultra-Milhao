#!/usr/bin/env python3
"""CSA Prospecção etapa 6: lotes de e-mails aprovados e leitura IMAP.

Somente Python stdlib. Nenhum envio sem --executar e confirmação exata.
Não cria campanhas em massa, não usa pixels e não contorna limites do provedor.
"""
from __future__ import annotations

import argparse
import configparser
import getpass
import hashlib
import imaplib
import os
import re
import smtplib
import sqlite3
import ssl
import sys
import time
from datetime import datetime, timedelta, timezone
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser, BytesHeaderParser
from email.utils import formataddr, getaddresses, make_msgid, parseaddr
from pathlib import Path
from typing import Callable

from contatos import EMAIL_RE
from gestor import agora, campanha, disponivel, lead, log_evento, preparar as preparar_gestor, bloquear
from pipeline import DB_DEFAULT, ROOT

CFG_DEFAULT = ROOT / "email_config.ini"
LIMITE_LOTE = 25
LIMITE_DIA = 40
INTERVALO_SEGUNDOS = 30
EMAILS_PESSOAIS = {"gmail.com", "hotmail.com", "outlook.com", "live.com", "yahoo.com", "yahoo.com.br", "icloud.com", "bol.com.br", "uol.com.br", "proton.me"}
CAIXAS_FUNCIONAIS = {"contato", "comercial", "vendas", "orcamento", "orcamentos", "atendimento", "info", "informacoes", "suporte", "sac", "office", "geral", "recepcao", "administrativo", "relacionamento", "marketing", "negocios", "business"}
OPT_OUT = re.compile(r"(?i)\b(?:n[aã]o\s+(?:quero\s+)?(?:mais\s+)?(?:receber|contat[oa]|me\s+contate)|remov[ae]\s+(?:meu\s+)?(?:email|e-mail|contato)|retir[ea]\s+(?:meu\s+)?(?:email|e-mail|contato)|descadastr\w*|cancele\s+(?:o\s+)?(?:envio|contato)|parem?\s+de\s+enviar)\b")
SCHEMA = """
CREATE TABLE IF NOT EXISTS lotes_email (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 campanha_id INTEGER NOT NULL REFERENCES campanhas(id),
 criado_em TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS envios_email (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 lote_id INTEGER NOT NULL REFERENCES lotes_email(id),
 lead_id INTEGER NOT NULL REFERENCES leads_campanha(id),
 dominio TEXT NOT NULL,
 destinatario_hash TEXT NOT NULL,
 conteudo_hash TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('preparado','enviando','enviado','incerto','cancelado')),
 criado_em TEXT NOT NULL,
 iniciado_em TEXT,
 enviado_em TEXT,
 message_id TEXT UNIQUE,
 erro_tipo TEXT,
 UNIQUE(lead_id),
 UNIQUE(dominio),
 UNIQUE(destinatario_hash)
);
CREATE INDEX IF NOT EXISTS ix_envios_lote ON envios_email(lote_id,status);
CREATE INDEX IF NOT EXISTS ix_envios_data ON envios_email(iniciado_em,status);
CREATE TABLE IF NOT EXISTS respostas_email (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 mensagem_hash TEXT NOT NULL UNIQUE,
 envio_id INTEGER NOT NULL REFERENCES envios_email(id),
 recebido_em TEXT NOT NULL,
 classificacao TEXT NOT NULL
);
"""


def preparar(db: Path):
    conn = preparar_gestor(db)
    conn.executescript(SCHEMA)
    return conn


def hash_email(email: str) -> str:
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()


def hash_conteudo(r) -> str:
    payload = "\x00".join([r["contato_valor"] or "", r["assunto"] or "", r["mensagem"] or "", r["diagnostico_assinatura"] or ""])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def email_funcional(email: str) -> bool:
    email = email.strip().lower()
    if not EMAIL_RE.fullmatch(email):
        return False
    local, dominio = email.rsplit("@", 1)
    # Inicialmente restrito a caixas funcionais para reduzir dados pessoais e risco.
    return dominio not in EMAILS_PESSOAIS and local in CAIXAS_FUNCIONAIS


def pode_enviar(conn, r) -> tuple[bool, str]:
    if r["status"] != "aprovado":
        return False, "Lead não está aprovado"
    if r["contato_tipo"] != "email" or not email_funcional(r["contato_valor"] or ""):
        return False, "É necessário e-mail corporativo funcional"
    if not r["aprovado_em"]:
        return False, "Aprovação humana ausente"
    if not r["assunto"] or not r["mensagem"]:
        return False, "Mensagem incompleta"
    if "\n" in r["assunto"] or "\r" in r["assunto"]:
        return False, "Assunto contém quebra de linha"
    ok, motivo = disponivel(conn, r)
    if not ok:
        return False, motivo
    if conn.execute("SELECT 1 FROM envios_email WHERE dominio=? OR destinatario_hash=?", (r["dominio"], hash_email(r["contato_valor"]))).fetchone():
        return False, "Domínio ou destinatário já foi incluído em outro lote"
    return True, ""


def preparar_lote(conn, cid: int, limite: int = 20) -> tuple[int | None, list]:
    if not 1 <= limite <= LIMITE_LOTE:
        raise ValueError(f"Limite por lote: 1 a {LIMITE_LOTE}")
    selecionados = []
    with conn:
        for r in conn.execute("SELECT * FROM leads_campanha WHERE campanha_id=? AND status='aprovado' ORDER BY pontos_prioridade DESC,id", (cid,)).fetchall():
            if len(selecionados) >= limite:
                break
            if pode_enviar(conn, r)[0]:
                selecionados.append(r)
        if not selecionados:
            return None, []
        lote = conn.execute("INSERT INTO lotes_email(campanha_id,criado_em) VALUES(?,?)", (cid,agora())).lastrowid
        for r in selecionados:
            conn.execute("""INSERT INTO envios_email(lote_id,lead_id,dominio,destinatario_hash,conteudo_hash,status,criado_em)
                 VALUES(?,?,?,?,?,'preparado',?)""", (lote,r["id"],r["dominio"],hash_email(r["contato_valor"]),hash_conteudo(r),agora()))
            log_evento(conn, r["id"], "incluido_lote_email", r["status"], r["status"], f"Lote {lote}: aguarda envio explícito")
    return lote, selecionados


def detalhes_lote(conn, lote_id: int):
    rows = conn.execute("""SELECT e.*, l.empresa, l.contato_valor, l.assunto, l.mensagem, l.status lead_status,
          l.aprovado_em FROM envios_email e JOIN leads_campanha l ON l.id=e.lead_id
          WHERE e.lote_id=? ORDER BY e.id""", (lote_id,)).fetchall()
    if not rows:
        raise ValueError("Lote inexistente ou vazio")
    return rows


def limite_restante(conn, agora_dt: datetime | None = None) -> int:
    now = agora_dt or datetime.now(timezone.utc)
    inicio = now.replace(hour=0,minute=0,second=0,microsecond=0).isoformat(timespec="seconds")
    usados = conn.execute("SELECT COUNT(*) FROM envios_email WHERE iniciado_em >= ? AND status IN ('enviando','enviado','incerto')",(inicio,)).fetchone()[0]
    return max(0, LIMITE_DIA-int(usados))


def validar_preparado(conn, envio):
    r = lead(conn, envio["lead_id"])
    if envio["status"] != "preparado":
        return False, "Envio já processado"
    if r["status"] != "aprovado" or not r["aprovado_em"]:
        return False, "Aprovação alterada"
    if r["contato_tipo"] != "email" or not email_funcional(r["contato_valor"] or ""):
        return False, "E-mail indisponível ou não funcional"
    if hash_email(r["contato_valor"]) != envio["destinatario_hash"] or hash_conteudo(r) != envio["conteudo_hash"]:
        return False, "Rascunho ou destinatário alterado"
    return disponivel(conn, r)


def cancelar_pendente(conn, envio, motivo: str):
    with conn:
        conn.execute("UPDATE envios_email SET status='cancelado',erro_tipo=? WHERE id=? AND status='preparado'",(motivo[:120],envio["id"]))
        log_evento(conn, envio["lead_id"], "envio_cancelado", "aprovado", "aprovado", motivo[:200])


def corpo_email(mensagem: str) -> str:
    rodape = "\n\nCSA | Comunicação comercial. Se preferir não receber outros contatos, responda 'não contatar'."
    return mensagem.strip() + rodape


def mensagem_pronta(remetente: str, nome: str, destinatario: str, assunto: str, mensagem: str, message_id: str) -> EmailMessage:
    if "\r" in assunto or "\n" in assunto:
        raise ValueError("Assunto inválido")
    if not email_funcional(destinatario):
        raise ValueError("Destinatário não é endereço comercial funcional")
    m = EmailMessage()
    m["From"] = formataddr((nome, remetente))
    m["To"] = destinatario
    m["Reply-To"] = remetente
    m["Subject"] = assunto
    m["Message-ID"] = message_id
    m["Auto-Submitted"] = "no"
    m.set_content(corpo_email(mensagem), subtype="plain", charset="utf-8")
    return m


def carregar_config(caminho: Path):
    cfg = configparser.ConfigParser(interpolation=None)
    if not caminho.exists():
        raise ValueError(f"Configure primeiro: {caminho.name}. Use CONFIGURAR_EMAIL_WINDOWS.bat")
    cfg.read(caminho, encoding="utf-8")
    try:
        host = cfg["smtp"]["host"].strip()
        porta = cfg.getint("smtp", "porta")
        modo = cfg["smtp"]["seguranca"].lower().strip()
        de = cfg["smtp"]["email"].lower().strip()
        nome = cfg["smtp"].get("nome", "CSA").strip()
        user = cfg["smtp"].get("usuario", de).strip()
        imap_host = cfg["imap"]["host"].strip()
        imap_porta = cfg.getint("imap", "porta")
        imap_user = cfg["imap"].get("usuario", de).strip()
    except (KeyError,ValueError,configparser.Error) as exc:
        raise ValueError("email_config.ini incompleto ou inválido") from exc
    if not host or not imap_host or not EMAIL_RE.fullmatch(de) or modo not in {"ssl","starttls"}:
        raise ValueError("Informe SMTP/IMAP válidos, e-mail e SSL/STARTTLS")
    if not (1 <= porta <= 65535 and 1 <= imap_porta <= 65535):
        raise ValueError("Portas inválidas")
    return dict(host=host,porta=porta,modo=modo,de=de,nome=nome,user=user,
                imap_host=imap_host,imap_porta=imap_porta,imap_user=imap_user)


def enviar_smtp(config, senha, destinatario: str, assunto: str, mensagem: str, msg_id: str):
    m = mensagem_pronta(config["de"],config["nome"],destinatario,assunto,mensagem,msg_id)
    tls = ssl.create_default_context()
    if config["modo"] == "ssl":
        with smtplib.SMTP_SSL(config["host"],config["porta"],timeout=30,context=tls) as cliente:
            cliente.login(config["user"],senha)
            cliente.send_message(m,from_addr=config["de"],to_addrs=[destinatario])
    else:
        with smtplib.SMTP(config["host"],config["porta"],timeout=30) as cliente:
            cliente.ehlo()
            cliente.starttls(context=tls)
            cliente.ehlo()
            cliente.login(config["user"],senha)
            cliente.send_message(m,from_addr=config["de"],to_addrs=[destinatario])


def executar_lote(conn, lote_id: int, enviar_fn: Callable, dormir: Callable= time.sleep, max_emails: int = LIMITE_LOTE, dominio_msg: str = "csa.local") -> dict[str,int]:
    """Executa só envios preparados, nunca reenvia status incerto.

    enviar_fn(email,assunto,mensagem,message_id) realiza SMTP ou falso em testes.
    """
    if not 1 <= max_emails <= LIMITE_LOTE:
        raise ValueError("Limite por execução inválido")
    rows = detalhes_lote(conn,lote_id)
    salvos = 0
    stats = {"enviados":0,"cancelados":0,"incertos":0,"pendentes":0}
    for e in rows:
        if e["status"] != "preparado":
            continue
        if salvos >= max_emails or limite_restante(conn)==0:
            stats["pendentes"] += 1
            continue
        valido, motivo = validar_preparado(conn,e)
        if not valido:
            cancelar_pendente(conn,e,motivo)
            stats["cancelados"] += 1
            continue
        r=lead(conn,e["lead_id"])
        mid=make_msgid(domain=dominio_msg)
        # Persistir ANTES do SMTP. Se cair após o aceite remoto, fica "incerto".
        with conn:
            conn.execute("UPDATE envios_email SET status='enviando',iniciado_em=?,message_id=? WHERE id=? AND status='preparado'",(agora(),mid,e["id"]))
            log_evento(conn,r["id"],"envio_iniciado","aprovado","aprovado",f"Envio {e['id']} em andamento")
        salvos += 1
        try:
            enviar_fn(r["contato_valor"],r["assunto"],r["mensagem"],mid)
        except Exception as exc:
            # Uma exceção não garante que o provedor não recebeu a mensagem.
            with conn:
                conn.execute("UPDATE envios_email SET status='incerto',erro_tipo=? WHERE id=?",(type(exc).__name__[:80],e["id"]))
                log_evento(conn,r["id"],"envio_indeterminado","aprovado","aprovado","Confirme na caixa Enviados. Não reenvie automaticamente.")
            stats["incertos"] += 1
            # Erro no provedor: não insistir em enviar outros itens do lote.
            break
        else:
            t=agora()
            with conn:
                conn.execute("UPDATE envios_email SET status='enviado',enviado_em=? WHERE id=?",(t,e["id"]))
                conn.execute("""UPDATE leads_campanha SET status='contatado',primeiro_contato_em=COALESCE(primeiro_contato_em,?),atualizado_em=?
                    WHERE id=? AND status='aprovado'""",(t,t,r["id"]))
                log_evento(conn,r["id"],"enviado_smtp","aprovado","contatado",f"Envio {e['id']} aceito pelo servidor SMTP")
            stats["enviados"] += 1
            if salvos < max_emails and limite_restante(conn)>0:
                dormir(INTERVALO_SEGUNDOS)
    return stats


def mensagem_ids(s: str):
    return re.findall(r"<[^<>\s]{1,250}>", s or "")


def texto_novo(msg) -> str:
    try:
        part = msg.get_body(preferencelist=("plain",))
        if part is None or part.get_content_type() != "text/plain":
            return ""
        texto = part.get_content()
    except Exception:
        return ""
    linhas=[]
    for l in texto.splitlines():
        st=l.strip()
        if st.startswith(">") or re.match(r"(?i)^(em .*escreveu:|on .*wrote:|from:|de:|-----original message-----)",st):
            break
        linhas.append(l)
        if len("\n".join(linhas)) > 1500:
            break
    return "\n".join(linhas)[:1500]


def processar_resposta(conn, raw: bytes) -> str:
    """Aceita só resposta no thread com remetente igual ao destinatário enviado."""
    if len(raw)>500_000:
        return "ignorado"
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    if msg.get("Auto-Submitted", "no").lower().strip() != "no" or msg.get("Precedence", "").lower().strip() in {"bulk","list","junk"}:
        return "automatico"
    mid = str(msg.get("Message-ID", "")).strip()
    if not mid:
        return "ignorado"
    hashed = hashlib.sha256(mid.encode("utf-8")).hexdigest()
    if conn.execute("SELECT 1 FROM respostas_email WHERE mensagem_hash=?", (hashed,)).fetchone():
        return "repetido"
    refs = set(mensagem_ids(str(msg.get("In-Reply-To", ""))+" "+str(msg.get("References", ""))))
    if not refs:
        return "ignorado"
    emails = [addr.strip().lower() for _, addr in getaddresses(msg.get_all("From",[]))]
    if len(emails) != 1 or not EMAIL_RE.fullmatch(emails[0]):
        return "ignorado"
    placeholders=",".join("?" for _ in refs)
    matches=conn.execute(f"""SELECT e.* FROM envios_email e
        WHERE e.message_id IN ({placeholders}) AND e.status='enviado'""",tuple(refs)).fetchall()
    for e in matches:
        r=lead(conn,e["lead_id"])
        if r["contato_tipo"] != "email" or not r["contato_valor"] or r["contato_valor"].lower()!=emails[0]:
            continue
        if hash_email(emails[0]) != e["destinatario_hash"]:
            continue
        oposicao=bool(OPT_OUT.search(texto_novo(msg)))
        t=agora()
        with conn:
            conn.execute("INSERT OR IGNORE INTO respostas_email(mensagem_hash,envio_id,recebido_em,classificacao) VALUES(?,?,?,?)",
                         (hashed,e["id"],t,"nao_contatar" if oposicao else "resposta"))
            if oposicao:
                bloquear(conn,"email",emails[0],"Solicitação explícita em resposta de e-mail")
                return "exclusao"
            anterior=r["status"]
            destino="respondeu" if anterior=="contatado" else anterior
            conn.execute("UPDATE leads_campanha SET status=?,resposta_em=COALESCE(resposta_em,?),atualizado_em=? WHERE id=?",(destino,t,t,r["id"]))
            log_evento(conn,r["id"],"resposta_imap",anterior,destino,"Resposta confirmada pelo identificador da conversa")
        return "resposta"
    return "ignorado"



def cabecalho_corresponde(conn, raw: bytes) -> bool:
    """Evita baixar corpos de mensagens sem relação com os envios da CSA."""
    try:
        h = BytesHeaderParser(policy=policy.default).parsebytes(raw)
        if h.get('Auto-Submitted', 'no').lower().strip() != 'no' or h.get('Precedence','').lower().strip() in {'bulk','list','junk'}:
            return False
        ids = set(mensagem_ids(str(h.get('In-Reply-To',''))+' '+str(h.get('References',''))))
        if not ids:
            return False
        email_origem = [addr.strip().lower() for _,addr in getaddresses(h.get_all('From',[]))]
        if len(email_origem)!=1 or not EMAIL_RE.fullmatch(email_origem[0]):
            return False
        placeholders=','.join('?' for _ in ids)
        query = 'SELECT 1 FROM envios_email e JOIN leads_campanha l ON l.id=e.lead_id WHERE e.message_id IN (' + placeholders + ") AND e.status='enviado' AND l.contato_valor=? LIMIT 1"
        r=conn.execute(query,(*ids,email_origem[0])).fetchone()
        return r is not None
    except (ValueError,TypeError):
        return False


def ler_imap(conn, config, senha: str, dias: int = 30, limite: int = 500) -> dict:
    if not 1<=dias<=90 or not 1<=limite<=1000:
        raise ValueError("dias: 1–90 e limite: 1–1000")
    inicio=(datetime.now(timezone.utc)-timedelta(days=dias)).strftime("%d-%b-%Y")
    stats={"resposta":0,"exclusao":0,"automatico":0,"ignorado":0,"repetido":0,"vistos":0}
    with imaplib.IMAP4_SSL(config["imap_host"],config["imap_porta"],ssl_context=ssl.create_default_context(),timeout=30) as box:
        box.login(config["imap_user"],senha)
        typ,_=box.select("INBOX",readonly=True)
        if typ != "OK":
            raise RuntimeError("Não foi possível abrir a caixa INBOX")
        typ, data = box.uid("SEARCH",None,"SINCE",inicio)
        if typ != "OK":
            raise RuntimeError("Busca IMAP falhou")
        uids = (data[0] or b"").split()
        # Ler primeiro os e-mails mais novos; nenhum deles será marcado como lido.
        for uid in reversed(uids[-limite:]):
            # Só o cabeçalho é lido inicialmente. Economiza transferência de mensagens irrelevantes.
            typ, parts=box.uid("FETCH",uid,"(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID FROM IN-REPLY-TO REFERENCES AUTO-SUBMITTED PRECEDENCE)])")
            if typ != "OK":
                continue
            cabecalho=next((x[1] for x in parts if isinstance(x,tuple) and isinstance(x[1],bytes)),None)
            stats["vistos"]+=1
            if cabecalho is None or not cabecalho_corresponde(conn,cabecalho):
                stats["ignorado"]+=1
                continue
            typ, parts=box.uid("FETCH",uid,"(BODY.PEEK[])")
            if typ != "OK":
                continue
            raw=next((x[1] for x in parts if isinstance(x,tuple) and isinstance(x[1],bytes)),None)
            if raw is None:
                continue
            result=processar_resposta(conn,raw)
            stats[result]+=1
    return stats


def resumo(conn) -> list[tuple[str,int]]:
    return [(r[0],r[1]) for r in conn.execute("SELECT status,COUNT(*) FROM envios_email GROUP BY status ORDER BY status")]


def cli() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db",type=Path,default=DB_DEFAULT)
    parser.add_argument("--config",type=Path,default=CFG_DEFAULT)
    sub=parser.add_subparsers(dest="comando",required=True)
    prep=sub.add_parser("preparar",help="Prepara até 25 contatos já aprovados; não envia")
    prep.add_argument("--campanha",default="CSA Geral")
    prep.add_argument("--limite",type=int,default=20)
    env=sub.add_parser("enviar",help="Exibe lote; envio só com --executar e confirmação exata")
    env.add_argument("--lote",type=int,required=True)
    env.add_argument("--executar",action="store_true")
    env.add_argument("--confirmar",default="")
    sub.add_parser("assistente",help="Prévia interativa e confirmação de um lote no Windows")
    resp=sub.add_parser("respostas",help="Sincroniza respostas via IMAP sem marcar como lidas")
    resp.add_argument("--dias",type=int,default=30)
    resp.add_argument("--limite",type=int,default=500)
    sub.add_parser("resumo",help="Resumo do envio por status")
    args=parser.parse_args()
    try:
        with preparar(args.db) as conn:
            if args.comando=="preparar":
                cid=campanha(conn,args.campanha)
                lote,rows=preparar_lote(conn,cid,args.limite)
                if lote is None:
                    print("Nenhum contato elegível: revise e aprove e-mails funcionais no gestor.")
                else:
                    print(f"Lote {lote} preparado ({len(rows)} e-mails). NADA enviado.")
                    for r in rows:
                        print(f"  {r['id']}: {r['empresa']} | {r['contato_valor']} | {r['assunto'][:90]}")
                    print(f"Para conferir: python envios.py enviar --lote {lote}")
            elif args.comando in ("enviar", "assistente"):
                if args.comando == "assistente":
                    resposta=input("Número do lote a conferir: ").strip()
                    if not resposta.isdecimal() or int(resposta)<=0:
                        raise ValueError("Número de lote inválido")
                    args.lote=int(resposta)
                    args.executar=False
                    args.confirmar=""
                rows=detalhes_lote(conn,args.lote)
                print(f"Lote {args.lote} | {limite_restante(conn)} envios restantes hoje (limite geral {LIMITE_DIA}/dia).")
                for r in rows:
                    print(f" [{r['status']}] {r['empresa']} | {r['contato_valor'] or '[sem contato]'} | {r['assunto'] or ''}")
                if args.comando == "assistente":
                    print("SIMULAÇÃO acima, nada enviado. Para enviar, confirme o lote e os destinatários.")
                    args.confirmar=input(f"Digite ENVIAR-{args.lote} para transmitir (Enter cancela): ").strip()
                    args.executar=(args.confirmar == f"ENVIAR-{args.lote}")
                if not args.executar:
                    print("SIMULAÇÃO encerrada: nada enviado.")
                else:
                    if args.confirmar != f"ENVIAR-{args.lote}":
                        raise ValueError(f"Confirmação incorreta. Use --confirmar ENVIAR-{args.lote}")
                    config=carregar_config(args.config)
                    if config["de"] in {"exemplo@seudominio.com.br",""}:
                        raise ValueError("Preencha endereço real no email_config.ini")
                    senha=os.environ.get("CSA_SMTP_PASSWORD") or getpass.getpass("Senha de aplicativo SMTP (não será salva): ")
                    if not senha:
                        raise ValueError("Senha SMTP vazia")
                    def real_email(dest,assunto,mensagem,mid):
                        enviar_smtp(config,senha,dest,assunto,mensagem,mid)
                    print("Envio real iniciado. Nenhuma repetição automática após falha.",flush=True)
                    stats=executar_lote(conn,args.lote,real_email,dominio_msg=config["de"].split("@",1)[1])
                    print("Resultado:",stats)
                    if stats["incertos"]:
                        print("ATENÇÃO: status incerto. Confira a caixa Enviados antes de qualquer ação.")
            elif args.comando=="respostas":
                config=carregar_config(args.config)
                senha=os.environ.get("CSA_IMAP_PASSWORD") or getpass.getpass("Senha de aplicativo IMAP (não será salva): ")
                if not senha:
                    raise ValueError("Senha IMAP vazia")
                print("Lendo respostas sem alterar mensagens na caixa de e-mail...")
                print(ler_imap(conn,config,senha,args.dias,args.limite))
            elif args.comando=="resumo":
                print("Status dos envios:")
                for status,count in resumo(conn):
                    print(f"{status}: {count}")
                print("Respostas confirmadas:",conn.execute("SELECT COUNT(*) FROM respostas_email WHERE classificacao='resposta'").fetchone()[0])
                print("Pedidos de exclusão:",conn.execute("SELECT COUNT(*) FROM respostas_email WHERE classificacao='nao_contatar'").fetchone()[0])
    except (ValueError,OSError,RuntimeError,configparser.Error,imaplib.IMAP4.error,sqlite3.Error) as exc:  # type: ignore[name-defined]
        print(f"Erro: {exc}",file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrompido. Consulte o status antes de reiniciar")
        return 130
    return 0

if __name__=="__main__":
    raise SystemExit(cli())
