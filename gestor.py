#!/usr/bin/env python3
"""CSA Prospecção etapa 5: gestor local de campanhas (sem envio automático).

Operações: sincronizar contatos e diagnósticos prontos; revisar e aprovar;
registrar progresso comercial e histórico; registrar exclusões; exportar CSV.
Interface web local em http://127.0.0.1:8785, apenas Python stdlib.
"""
from __future__ import annotations

import argparse
import csv
import html
import secrets
import sqlite3
import sys
import urllib.parse
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from contatos import preparar as preparar_contatos
from pipeline import DB_DEFAULT, ROOT, celula_excel, site_normalizado
from contatos import EMAIL_RE

VERSAO = 1
STATUSES = ("revisao", "aprovado", "contatado", "respondeu", "reuniao", "proposta", "ganho", "perdido", "descartado", "bloqueado")
STATUS_LABEL = {
    "revisao": "Revisar", "aprovado": "Aprovado", "contatado": "Contato feito",
    "respondeu": "Respondeu", "reuniao": "Reunião", "proposta": "Proposta",
    "ganho": "Ganho", "perdido": "Perdido", "descartado": "Descartado", "bloqueado": "Não contatar",
}
TRANSITIONS = {
    "revisao": ("descartado",),
    "aprovado": ("contatado", "revisao", "descartado"),
    "contatado": ("respondeu", "perdido", "descartado"),
    "respondeu": ("reuniao", "proposta", "perdido", "descartado"),
    "reuniao": ("proposta", "perdido", "descartado"),
    "proposta": ("ganho", "perdido", "descartado"),
    "ganho": (), "perdido": ("revisao",), "descartado": ("revisao",), "bloqueado": (),
}
COLUMNS_MILESTONE = {
    "contatado": "primeiro_contato_em", "respondeu": "resposta_em",
    "reuniao": "reuniao_em", "proposta": "proposta_em", "ganho": "ganho_em",
}
SCHEMA = """
CREATE TABLE IF NOT EXISTS campanhas (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 nome TEXT NOT NULL UNIQUE,
 criada_em TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS leads_campanha (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 campanha_id INTEGER NOT NULL REFERENCES campanhas(id),
 dominio TEXT NOT NULL REFERENCES websites(dominio),
 empresa TEXT NOT NULL,
 cidade TEXT, estado TEXT,
 contato_tipo TEXT, contato_valor TEXT, contato_origem_url TEXT,
 diagnostico_assinatura TEXT NOT NULL,
 diagnostico_auditado_em TEXT NOT NULL,
 evidencia TEXT NOT NULL,
 assunto TEXT, mensagem TEXT,
 pontos_prioridade INTEGER NOT NULL DEFAULT 0,
 status TEXT NOT NULL DEFAULT 'revisao' CHECK(status IN
  ('revisao','aprovado','contatado','respondeu','reuniao','proposta','ganho','perdido','descartado','bloqueado')),
 anotacoes TEXT NOT NULL DEFAULT '',
 criado_em TEXT NOT NULL, atualizado_em TEXT NOT NULL,
 aprovado_em TEXT, primeiro_contato_em TEXT, resposta_em TEXT,
 reuniao_em TEXT, proposta_em TEXT, ganho_em TEXT,
 UNIQUE(campanha_id, dominio)
);
CREATE INDEX IF NOT EXISTS ix_leads_campanha_status
 ON leads_campanha(campanha_id,status,pontos_prioridade);
CREATE INDEX IF NOT EXISTS ix_leads_contato ON leads_campanha(contato_valor);
CREATE TABLE IF NOT EXISTS historico_comercial (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 lead_id INTEGER NOT NULL REFERENCES leads_campanha(id),
 registrado_em TEXT NOT NULL,
 evento TEXT NOT NULL,
 status_anterior TEXT,
 status_novo TEXT,
 observacao TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_historico_lead ON historico_comercial(lead_id,id);
"""


def agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def preparar(db: Path) -> sqlite3.Connection:
    # Reutiliza as tabelas anteriores; cria as novas sem apagar os dados existentes.
    conn = preparar_contatos(db)
    conn.executescript(SCHEMA)
    return conn


def campanha(conn: sqlite3.Connection, nome: str) -> int:
    nome = nome.strip()
    if not 2 <= len(nome) <= 100:
        raise ValueError("Nome da campanha deve ter de 2 a 100 caracteres")
    with conn:
        conn.execute("INSERT OR IGNORE INTO campanhas(nome,criada_em) VALUES(?,?)", (nome, agora()))
        row = conn.execute("SELECT id FROM campanhas WHERE nome=?", (nome,)).fetchone()
    return int(row["id"])


def log_evento(conn: sqlite3.Connection, lead_id: int, evento: str,
               anterior: str | None, novo: str | None, observacao: str = "") -> None:
    conn.execute("INSERT INTO historico_comercial(lead_id,registrado_em,evento,status_anterior,status_novo,observacao) "
                 "VALUES(?,?,?,?,?,?)", (lead_id, agora(), evento, anterior, novo, observacao[:2000]))


# A condição de atualidade garante que a evidência técnica e a versão do diagnóstico
# ainda coincidem. A extração de contatos deve estar vigente e não bloqueada.
CANDIDATOS = """
SELECT w.dominio, COALESCE(e.nome,w.dominio) empresa, e.cidade, e.estado,
 c.tipo contato_tipo, c.valor contato_valor, c.origem_url contato_origem_url,
 d.assinatura diagnostico_assinatura, d.auditado_em diagnostico_auditado_em,
 d.foco_evidencia evidencia, d.assunto, d.mensagem, a.pontos_prioridade
FROM websites w
JOIN auditorias a ON a.dominio=w.dominio AND a.status='ok'
JOIN diagnosticos d ON d.dominio=w.dominio AND d.status='gerado'
  AND d.auditado_em=a.auditado_em
JOIN enriquecimentos en ON en.dominio=w.dominio AND en.status='ok'
JOIN contatos_publicos c ON c.dominio=w.dominio
LEFT JOIN estabelecimentos e ON e.id_fonte=(SELECT MIN(x.id_fonte) FROM estabelecimentos x WHERE x.dominio=w.dominio)
WHERE a.pontos_prioridade >= ?
 AND TRIM(COALESCE(d.mensagem,'')) != '' AND TRIM(COALESCE(d.foco_evidencia,'')) != ''
 AND NOT EXISTS (SELECT 1 FROM bloqueios_prospeccao b WHERE
    (b.tipo='dominio' AND b.valor=w.dominio) OR
    (b.tipo='email' AND c.tipo='email' AND b.valor=c.valor))
 AND NOT EXISTS (SELECT 1 FROM leads_campanha l WHERE l.campanha_id=? AND l.dominio=w.dominio)
 AND (? IS NULL OR EXISTS (SELECT 1 FROM estabelecimentos x
        WHERE x.dominio=w.dominio AND UPPER(x.estado)=?))
ORDER BY a.pontos_prioridade DESC, w.dominio,
 CASE c.tipo WHEN 'email' THEN 0 WHEN 'whatsapp' THEN 1 ELSE 2 END,
 CASE c.confianca WHEN 'alta' THEN 0 ELSE 1 END, c.valor
"""


def sincronizar(conn: sqlite3.Connection, cid: int, limite: int = 100, min_pontos: int = 3,
               uf: str | None = None) -> int:
    if not 1 <= limite <= 10000:
        raise ValueError("Limite entre 1 e 10.000")
    if not 0 <= min_pontos <= 20:
        raise ValueError("Pontos mínimos entre 0 e 20")
    if uf and (len(uf) != 2 or not uf.isalpha()):
        raise ValueError("UF inválida")
    uf = uf.upper() if uf else None
    # Percorremos contatos ordenados; primeiro registro por domínio é o melhor canal.
    # Limite aplicado após deduplicação para não desperdiçar entradas.
    novos, vistos = 0, set()
    with conn:
        for r in conn.execute(CANDIDATOS, (min_pontos, cid, uf, uf)):
            if r["dominio"] in vistos:
                continue
            vistos.add(r["dominio"])
            t = agora()
            cur = conn.execute("""INSERT OR IGNORE INTO leads_campanha
              (campanha_id,dominio,empresa,cidade,estado,contato_tipo,contato_valor,
               contato_origem_url,diagnostico_assinatura,diagnostico_auditado_em,
               evidencia,assunto,mensagem,pontos_prioridade,status,criado_em,atualizado_em)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,'revisao',?,?)""",
              (cid, r["dominio"], r["empresa"], r["cidade"], r["estado"],
               r["contato_tipo"], r["contato_valor"], r["contato_origem_url"],
               r["diagnostico_assinatura"], r["diagnostico_auditado_em"],
               r["evidencia"], r["assunto"], r["mensagem"], r["pontos_prioridade"], t, t))
            if cur.rowcount:
                novos += 1
                log_evento(conn, cur.lastrowid, "importado", None, "revisao", "Contato e diagnóstico importados")
            if novos >= limite:
                break
    return novos


def lead(conn: sqlite3.Connection, lid: int) -> sqlite3.Row:
    r = conn.execute("SELECT * FROM leads_campanha WHERE id=?", (lid,)).fetchone()
    if r is None:
        raise ValueError("Lead não encontrado")
    return r


def disponivel(conn: sqlite3.Connection, r: sqlite3.Row) -> tuple[bool, str]:
    if not r["contato_valor"]:
        return False, "Contato removido ou indisponível"
    if conn.execute("""SELECT 1 FROM bloqueios_prospeccao WHERE
      (tipo='dominio' AND valor=?) OR (tipo='email' AND valor=?) LIMIT 1""",
       (r["dominio"], r["contato_valor"])).fetchone():
        return False, "Contato consta na lista de exclusão"
    # O contato precisa continuar na tabela de origem, não basta ter sido armazenado no CRM.
    if not conn.execute("""SELECT 1 FROM contatos_publicos c
      JOIN enriquecimentos en ON en.dominio=c.dominio AND en.status='ok'
      WHERE c.dominio=? AND c.tipo=? AND c.valor=?""",
      (r["dominio"], r["contato_tipo"], r["contato_valor"])).fetchone():
        return False, "Contato já não está na base de origem"
    d = conn.execute("""SELECT d.assinatura, d.auditado_em, a.auditado_em atual,
      a.status status_auditoria FROM diagnosticos d JOIN auditorias a ON a.dominio=d.dominio
      WHERE d.dominio=? AND d.status='gerado'""", (r["dominio"],)).fetchone()
    if not d or d["assinatura"] != r["diagnostico_assinatura"] or \
        d["auditado_em"] != r["diagnostico_auditado_em"] or \
        d["auditado_em"] != d["atual"] or d["status_auditoria"] != "ok":
        return False, "Diagnóstico mudou: revise a auditoria e atualize o rascunho"
    return True, ""


def editar(conn: sqlite3.Connection, lid: int, assunto: str, mensagem: str, anotacoes: str) -> None:
    r = lead(conn, lid)
    if r["status"] not in ("revisao", "aprovado"):
        raise ValueError("Só rascunhos em revisão ou aprovados podem ser editados")
    assunto, mensagem, anotacoes = assunto.strip(), mensagem.strip(), anotacoes.strip()
    if not (2 <= len(assunto) <= 250 and 10 <= len(mensagem) <= 5000):
        raise ValueError("Assunto: 2 a 250 caracteres; mensagem: 10 a 5.000 caracteres")
    if len(anotacoes) > 2000:
        raise ValueError("Anotação acima de 2.000 caracteres")
    with conn:
        conn.execute("""UPDATE leads_campanha SET assunto=?,mensagem=?,anotacoes=?,
          status='revisao',aprovado_em=NULL,atualizado_em=? WHERE id=?""",
          (assunto, mensagem, anotacoes, agora(), lid))
        log_evento(conn, lid, "rascunho_editado", r["status"], "revisao", "Rascunho salvo para revisão")


def atualizar_rascunho(conn: sqlite3.Connection, lid: int) -> None:
    r = lead(conn, lid)
    if r["status"] != "revisao":
        raise ValueError("Só é possível atualizar um rascunho em revisão")
    d = conn.execute("""SELECT d.assinatura,d.auditado_em,d.assunto,d.mensagem,
      d.foco_evidencia,a.auditado_em atual,a.status status_auditoria
      FROM diagnosticos d JOIN auditorias a ON a.dominio=d.dominio
      WHERE d.dominio=? AND d.status='gerado'""", (r["dominio"],)).fetchone()
    if not d or d["auditado_em"] != d["atual"] or d["status_auditoria"] != "ok":
        raise ValueError("Gere diagnóstico atualizado antes")
    with conn:
        conn.execute("""UPDATE leads_campanha SET diagnostico_assinatura=?,
          diagnostico_auditado_em=?,evidencia=?,assunto=?,mensagem=?,atualizado_em=?
          WHERE id=?""", (d["assinatura"], d["auditado_em"], d["foco_evidencia"],
                          d["assunto"], d["mensagem"], agora(), lid))
        log_evento(conn, lid, "rascunho_atualizado", "revisao", "revisao", "Rascunho e evidência atualizados")


def aprovar(conn: sqlite3.Connection, lid: int, conferi: bool) -> None:
    r = lead(conn, lid)
    if r["status"] != "revisao":
        raise ValueError("Aprovação só é permitida para itens em revisão")
    if not conferi:
        raise ValueError("Marque a confirmação de revisão humana")
    if not r["assunto"] or not r["mensagem"]:
        raise ValueError("Preencha o assunto e a mensagem antes de aprovar")
    ok, motivo = disponivel(conn, r)
    if not ok:
        raise ValueError(motivo)
    with conn:
        conn.execute("UPDATE leads_campanha SET status='aprovado',aprovado_em=?,atualizado_em=? WHERE id=?",
                     (agora(), agora(), lid))
        log_evento(conn, lid, "aprovado_manualmente", "revisao", "aprovado", "Evidências e contato conferidos")


def transicao(conn: sqlite3.Connection, lid: int, destino: str, nota: str = "") -> None:
    r = lead(conn, lid)
    if destino not in TRANSITIONS[r["status"]]:
        raise ValueError(f"Transição inválida: {r['status']} -> {destino}")
    if len(nota) > 2000:
        raise ValueError("Observação acima de 2.000 caracteres")
    if destino == "contatado":
        ok, motivo = disponivel(conn, r)
        if not ok:
            raise ValueError(motivo)
    t = agora()
    with conn:
        conn.execute("UPDATE leads_campanha SET status=?,atualizado_em=? WHERE id=?", (destino,t,lid))
        if destino in COLUMNS_MILESTONE:
            col = COLUMNS_MILESTONE[destino]
            conn.execute(f"UPDATE leads_campanha SET {col}=COALESCE({col}, ?) WHERE id=?", (t,lid))
        log_evento(conn, lid, "alteracao_status", r["status"], destino, nota.strip())


def bloquear(conn: sqlite3.Connection, tipo: str, valor: str, motivo: str = "") -> int:
    if tipo not in ("dominio", "email"):
        raise ValueError("Tipo de bloqueio deve ser dominio ou email")
    if tipo == "dominio":
        par = site_normalizado(valor)
        if par is None:
            raise ValueError("Domínio inválido")
        valor = par[0]
    else:
        valor = valor.strip().lower()
        if not EMAIL_RE.fullmatch(valor):
            raise ValueError("E-mail inválido")
    motivo = motivo.strip()[:240]
    t = agora()
    with conn:
        conn.execute("""INSERT INTO bloqueios_prospeccao(tipo,valor,motivo,registrado_em)
          VALUES(?,?,?,?) ON CONFLICT(tipo,valor) DO UPDATE SET
          motivo=excluded.motivo,registrado_em=excluded.registrado_em""", (tipo,valor,motivo,t))
        if tipo == "dominio":
            conn.execute("DELETE FROM contatos_publicos WHERE dominio=?", (valor,))
            conn.execute("DELETE FROM enriquecimentos WHERE dominio=?", (valor,))
            affected = conn.execute("SELECT id,status FROM leads_campanha WHERE dominio=?", (valor,)).fetchall()
        else:
            conn.execute("DELETE FROM contatos_publicos WHERE tipo='email' AND valor=?", (valor,))
            affected = conn.execute("SELECT id,status FROM leads_campanha WHERE contato_tipo='email' AND contato_valor=?", (valor,)).fetchall()
        # Cancela automaticamente lotes ainda não enviados. Envios com resultado
        # incerto ou já aceitos pelo SMTP nunca são reexecutados.
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='envios_email'").fetchone() and affected:
            for item in affected:
                conn.execute("UPDATE envios_email SET status='cancelado',erro_tipo='Exclusão comercial' WHERE lead_id=? AND status='preparado'", (item["id"],))
        for r in affected:
            conn.execute("""UPDATE leads_campanha SET status='bloqueado',
              contato_valor=NULL,contato_origem_url=NULL,assunto=NULL,mensagem=NULL,
              anotacoes='',atualizado_em=? WHERE id=?""", (t,r["id"]))
            log_evento(conn, r["id"], "exclusao", r["status"], "bloqueado", "Bloqueio de prospecção registrado")
    return len(affected)


def listar(conn: sqlite3.Connection, cid: int, status: str = "", busca: str = "", pagina: int = 1,
           tamanho: int = 25) -> tuple[list[sqlite3.Row], int]:
    filtro = "l.campanha_id=?"
    params: list[Any] = [cid]
    if status:
        if status not in STATUSES:
            raise ValueError("Status inválido")
        filtro += " AND l.status=?"
        params.append(status)
    if busca.strip():
        filtro += " AND (l.empresa LIKE ? OR l.dominio LIKE ?)"
        s = f"%{busca.strip()[:80].replace('%','').replace('_','')}%"
        params += [s,s]
    total = conn.execute("SELECT COUNT(*) FROM leads_campanha l WHERE " + filtro, params).fetchone()[0]
    rows = conn.execute("SELECT l.* FROM leads_campanha l WHERE " + filtro +
        " ORDER BY CASE l.status WHEN 'revisao' THEN 0 WHEN 'aprovado' THEN 1 ELSE 2 END,"
        " l.pontos_prioridade DESC,l.id LIMIT ? OFFSET ?", [*params, tamanho, (max(pagina,1)-1)*tamanho]).fetchall()
    return rows, total


def metricas(conn: sqlite3.Connection, cid: int) -> dict[str, int]:
    row = conn.execute("""SELECT COUNT(*) total,
      SUM(CASE WHEN aprovado_em IS NOT NULL THEN 1 ELSE 0 END) aprovados,
      SUM(CASE WHEN primeiro_contato_em IS NOT NULL THEN 1 ELSE 0 END) contatados,
      SUM(CASE WHEN resposta_em IS NOT NULL THEN 1 ELSE 0 END) respostas,
      SUM(CASE WHEN reuniao_em IS NOT NULL THEN 1 ELSE 0 END) reunioes,
      SUM(CASE WHEN proposta_em IS NOT NULL THEN 1 ELSE 0 END) propostas,
      SUM(CASE WHEN ganho_em IS NOT NULL THEN 1 ELSE 0 END) ganhos,
      SUM(CASE WHEN status='bloqueado' THEN 1 ELSE 0 END) bloqueados
      FROM leads_campanha WHERE campanha_id=?""", (cid,)).fetchone()
    return {k:int(row[k] or 0) for k in row.keys()}


def exportar(conn: sqlite3.Connection, cid: int, arquivo: Path) -> int:
    """Somente aprovados, ainda atuais, com contato válido e sem exclusões."""
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    rows = conn.execute("SELECT * FROM leads_campanha WHERE campanha_id=? AND status='aprovado' ORDER BY pontos_prioridade DESC,id", (cid,))
    cols = ("id","empresa","dominio","estado","cidade","contato_tipo","contato_valor","contato_origem_url",
            "pontos_prioridade","evidencia","assunto","mensagem","aprovado_em")
    count = 0
    with arquivo.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh, delimiter=";")
        writer.writerow(cols)
        for r in rows:
            if disponivel(conn, r)[0]:
                writer.writerow([celula_excel(r[k]) for k in cols])
                count += 1
    return count


def safe(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def render_page(conn: sqlite3.Connection, cid: int, status: str = "", busca: str = "", pagina: int = 1,
                flash: str = "", csrf: str = "") -> str:
    campanhas = conn.execute("SELECT * FROM campanhas ORDER BY id DESC").fetchall()
    if not campanhas:
        return html_base("CSA | Campanhas", "<h1>CSA Prospecção</h1><p>Crie uma campanha pelo terminal ou formulário.</p>", csrf)
    if cid not in [r["id"] for r in campanhas]:
        cid = campanhas[0]["id"]
    cur = next(r for r in campanhas if r["id"] == cid)
    rows, total = listar(conn, cid, status, busca, pagina)
    met = metricas(conn,cid)
    painel = "".join(f'<div class="metric"><small>{safe(label)}</small><strong>{met[key]:,}</strong></div>'
       for key,label in (("total","Na fila"),("aprovados","Aprovações históricas"),("contatados","Contatos realizados"),
                         ("respostas","Responderam"),("reunioes","Reuniões"),("propostas","Propostas"),("ganhos","Vendas")))
    conversao = (met["respostas"] / met["contatados"] * 100) if met["contatados"] else 0
    options = "".join(f'<option value="{c["id"]}" {"selected" if c["id"]==cid else ""}>{safe(c["nome"])}</option>' for c in campanhas)
    statuses = '<option value="">Todos os status</option>'+"".join(
        f'<option value="{s}" {"selected" if s==status else ""}>{safe(label)}</option>' for s,label in STATUS_LABEL.items())
    nav = f'''<header><h1>CSA <span>Prospecção</span></h1><p>Gestor local de oportunidades | Lotes SMTP exigem confirmação manual</p></header>
    <section class="card"><form action="/" method="get" class="row">
      <label>Campanha<select name="campanha">{options}</select></label>
      <label>Status<select name="status">{statuses}</select></label>
      <label>Pesquisar<input name="q" value="{safe(busca)}" placeholder="Empresa ou domínio" maxlength="80"></label>
      <button type="submit">Filtrar</button></form></section>
    <section class="metrics">{painel}</section><p class="hint">Respostas / contatos realizados: <b>{conversao:.1f}%</b>. Indicadores baseados nos estágios registrados manualmente.</p>'''
    envio_stats = ''
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='envios_email'").fetchone():
        counts = {r[0]:r[1] for r in conn.execute('''SELECT e.status, COUNT(*) FROM envios_email e
          JOIN lotes_email l ON l.id=e.lote_id WHERE l.campanha_id=? GROUP BY e.status''',(cid,))}
        envio_stats = (f'<p class="hint">E-mails desta campanha: <b>{counts.get("preparado",0)}</b> preparados, '
           f'<b>{counts.get("enviado",0)}</b> aceitos pelo SMTP, '
           f'<b>{counts.get("incerto",0)+counts.get("enviando",0)}</b> pendentes de conferência após falha.</p>')
    actions = envio_stats + '''<section class="card"><h2>Envio comercial (etapa 6)</h2>
      <p>Depois de aprovar os leads, execute <code>PREPARAR_LOTE_WINDOWS.bat</code>, confira o lote e use
      <code>ENVIAR_LOTE_WINDOWS.bat</code> para confirmar cada envio. Para acompanhar respostas, execute
      <code>VERIFICAR_RESPOSTAS_WINDOWS.bat</code>. O painel não dispara mensagens sozinho.</p>
      </section>''' + f'''<section class="card"><h2>Preparar fila</h2>
      <form action="/acao" method="post" class="row">
      <input type="hidden" name="csrf" value="{safe(csrf)}">
      <input type="hidden" name="acao" value="sincronizar">
      <input type="hidden" name="campanha_id" value="{cid}">
      <label>Até quantos leads?<input name="limite" type="number" min="1" max="10000" value="100"></label>
      <label>Prioridade mínima<input name="min_pontos" type="number" min="0" max="20" value="3"></label>
      <label>UF (opcional)<input name="uf" maxlength="2" placeholder="SP"></label>
      <button type="submit">Adicionar à fila</button></form>
      <form action="/acao" method="post" class="row smallrow">
       <input type="hidden" name="csrf" value="{safe(csrf)}"><input type="hidden" name="acao" value="criar_campanha">
       <label>Nova campanha<input name="nome" placeholder="Ex.: Oficinas do interior" maxlength="100" required></label>
       <button class="secondary" type="submit">Criar campanha</button>
      </form>
      <p class="hint">Só importa domínios com contato e diagnóstico atualizados. Importação não envia mensagens.</p>
      </section>'''
    links = f'<p><a href="/exportar?campanha={cid}&csrf={safe(csrf)}">Exportar CSV de aprovados válidos</a> · <a href="/historico?campanha={cid}">Histórico recente</a></p>'
    cards=[]
    for r in rows:
        ok,motivo = disponivel(conn,r)
        status_now=r["status"]
        opts = "".join(f'<option value="{s}">{safe(STATUS_LABEL[s])}</option>' for s in TRANSITIONS[status_now])
        warning = f'<p class="alert">{safe(motivo)}</p>' if not ok and status_now not in ("bloqueado","descartado","ganho") else ""
        h=f'''<article class="lead card"><div class="leadhead"><h3>{safe(r["empresa"])}</h3><span class="pill">{safe(STATUS_LABEL[status_now])}</span></div>
        <div class="muted"><b>{safe(r["dominio"])}</b> | {safe(r["cidade"])} / {safe(r["estado"])} | Prioridade técnica: {int(r["pontos_prioridade"])}</div>
        <p><b>Contato:</b> {safe(r["contato_tipo"])} {safe(r["contato_valor"])} <small>(não verificado)</small></p>
        <p><b>Evidência:</b> {safe(r["evidencia"])}</p>{warning}'''
        if status_now in ("revisao","aprovado"):
            h+=f'''<details><summary>Editar rascunho e observações</summary><form action="/acao" method="post" class="stack">
            <input type="hidden" name="csrf" value="{safe(csrf)}"><input type="hidden" name="acao" value="editar">
            <input type="hidden" name="id" value="{r["id"]}"><input type="hidden" name="campanha_id" value="{cid}">
            <label>Assunto<input name="assunto" maxlength="250" value="{safe(r["assunto"])}" required></label>
            <label>Mensagem<textarea name="mensagem" maxlength="5000" rows="5" required>{safe(r["mensagem"])}</textarea></label>
            <label>Anotações<textarea name="anotacoes" maxlength="2000" rows="2">{safe(r["anotacoes"])}</textarea></label>
            <button class="secondary" type="submit">Salvar alterações e voltar à revisão</button></form></details>'''
        if status_now == "revisao":
            h+=f'''<div class="row actionrow"><form action="/acao" method="post">
               <input type="hidden" name="csrf" value="{safe(csrf)}"><input type="hidden" name="acao" value="aprovar">
               <input type="hidden" name="id" value="{r["id"]}"><input type="hidden" name="campanha_id" value="{cid}">
               <label class="confirm"><input type="checkbox" name="conferi" value="1" required> Conferi manualmente o site, o contato e o texto</label>
               <button type="submit" {'disabled' if not ok else ''}>Aprovar para contato manual</button></form>
               <form action="/acao" method="post">
                <input type="hidden" name="csrf" value="{safe(csrf)}"><input type="hidden" name="acao" value="atualizar">
                <input type="hidden" name="id" value="{r["id"]}"><input type="hidden" name="campanha_id" value="{cid}">
                <button class="secondary" type="submit">Atualizar rascunho da auditoria</button></form></div>'''
        if opts:
            h+=f'''<form action="/acao" method="post" class="row actionrow">
              <input type="hidden" name="csrf" value="{safe(csrf)}"><input type="hidden" name="acao" value="status">
              <input type="hidden" name="id" value="{r["id"]}"><input type="hidden" name="campanha_id" value="{cid}">
              <label>Registrar andamento<select name="destino">{opts}</select></label>
              <label>Observação<input name="nota" maxlength="2000" placeholder="Ex.: falou com recepção"></label>
              <button class="secondary" type="submit">Registrar</button></form>'''
        if status_now != "bloqueado" and r["contato_valor"]:
            h+=f'''<form action="/acao" method="post" class="row actionrow">
              <input type="hidden" name="csrf" value="{safe(csrf)}"><input type="hidden" name="acao" value="bloquear">
              <input type="hidden" name="id" value="{r["id"]}"><input type="hidden" name="campanha_id" value="{cid}">
              <label>Exclusão<select name="tipo"><option value="dominio">Todo domínio</option>{'<option value="email">Somente e-mail</option>' if r["contato_tipo"]=='email' else ''}</select></label>
              <label>Motivo<input name="motivo" maxlength="240" placeholder="Solicitou não contato"></label>
              <label class="confirm"><input type="checkbox" name="confirmar_bloqueio" value="1" required> Confirmo o bloqueio</label>
              <button class="danger" type="submit">Não contatar</button></form>'''
        h+='</article>'
        cards.append(h)
    pager = ''
    if total > 25:
        page_count = (total+24)//25
        links_pg=[]
        for label, nextpage in (("Anterior",max(1,pagina-1)),("Próxima",min(page_count,pagina+1))):
            url='/?'+urllib.parse.urlencode({"campanha":cid,"status":status,"q":busca,"pagina":nextpage})
            links_pg.append(f'<a href="{safe(url)}">{label}</a>')
        pager=f'<p class="pages">Página {pagina} de {page_count} | '+ ' · '.join(links_pg)+'</p>'
    body = nav+(f'<div class="notice">{safe(flash)}</div>' if flash else '')+actions+links+f'<h2>Fila: {safe(cur["nome"])} <small>({total} encontrados)</small></h2>'+(''.join(cards) or '<div class="card">Sem leads nesta seleção. Execute a coleta, auditoria, diagnóstico e enriquecimento, depois sincronize.</div>')+pager
    body += '<footer>Dados OSM: © OpenStreetMap contributors (ODbL). Contato público não significa consentimento. Envios exigem comando e confirmação humana.</footer>'
    return html_base("CSA | Prospecção",body,csrf)


def html_base(title: str, content: str, csrf: str = "") -> str:
    # Interface offline: sem frameworks, CDN, javascript externo ou rastreadores.
    return f'''<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>{safe(title)}</title><style>
      :root{{--bg:#f3f5f8;--ink:#192333;--muted:#596879;--accent:#1766bd;--line:#dce2e9}}
      *{{box-sizing:border-box}}body{{font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif;color:var(--ink);background:var(--bg);max-width:1120px;margin:0 auto;padding:24px}}
      header{{margin-bottom:20px}}h1{{font-size:27px;margin:0}}h1 span{{color:var(--accent)}}header p,.muted,.hint,small{{color:var(--muted)}}h2{{margin:26px 0 12px}}h3{{font-size:18px;margin:0}}
      .card,.metric{{background:white;border:1px solid var(--line);border-radius:12px;padding:18px;margin:12px 0}}
      .metrics{{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px}}.metric{{margin:0;padding:14px}}.metric small,.metric strong{{display:block}}.metric strong{{font-size:24px}}
      .row{{display:flex;align-items:end;gap:12px;flex-wrap:wrap}}.row label{{flex:1;min-width:120px}}.stack{{display:grid;gap:10px}}label{{display:block;font-weight:600;font-size:13px}}
      input,select,textarea{{margin-top:4px;width:100%;border:1px solid #cad4df;border-radius:7px;padding:9px;font:inherit;color:var(--ink);background:white}}textarea{{resize:vertical}}
      input[type=checkbox]{{width:auto;margin:0 8px 0 0}}button{{border:0;background:var(--accent);color:white;padding:10px 14px;border-radius:7px;font-weight:650;cursor:pointer;white-space:nowrap}}
      button.secondary{{color:var(--accent);background:#e8f0fb}}button.danger{{background:#9b3131}}button:disabled{{opacity:.5;cursor:not-allowed}}
      .leadhead{{display:flex;justify-content:space-between;gap:12px;align-items:center}}.pill{{border-radius:22px;background:#e9eef4;padding:4px 12px;white-space:nowrap;font-size:12px;font-weight:650}}
      details{{margin-top:12px}}summary{{cursor:pointer;font-weight:650;color:var(--accent)}}details form{{margin-top:14px}}.lead p{{margin:9px 0;overflow-wrap:anywhere}}.lead .actionrow{{margin-top:15px;border-top:1px solid var(--line);padding-top:12px}}.confirm{{display:block;min-width:290px;margin-bottom:8px}}
      .alert{{background:#fff0eb;border-radius:7px;padding:10px;color:#912b22}}.notice{{background:#e6f3ff;border:1px solid #a5c8f4;padding:12px;border-radius:8px}}
      a{{color:var(--accent);text-decoration:none}}a:hover{{text-decoration:underline}}.pages,footer{{margin:28px 0;color:var(--muted)}}
      @media(max-width:600px){{body{{padding:12px}}.card{{padding:12px}}.row{{align-items:stretch}}.row label{{min-width:100%}}button{{width:100%}}}}
      </style></head><body>{content}</body></html>'''


def historico_html(conn: sqlite3.Connection, cid: int) -> str:
    rows = conn.execute("""SELECT h.registrado_em,h.evento,h.status_anterior,h.status_novo,
        h.observacao,l.empresa,l.dominio FROM historico_comercial h JOIN leads_campanha l ON l.id=h.lead_id
        WHERE l.campanha_id=? ORDER BY h.id DESC LIMIT 200""", (cid,)).fetchall()
    content = f'<h1>Histórico comercial</h1><p><a href="/?campanha={cid}">Voltar ao gestor</a></p>'
    for r in rows:
        content += f'<article class="card"><b>{safe(r["empresa"])}</b> ({safe(r["dominio"])})<p>{safe(r["registrado_em"])} · {safe(r["evento"])} · {safe(r["status_anterior"])} → {safe(r["status_novo"])}</p><p>{safe(r["observacao"])}</p></article>'
    return html_base("CSA | Histórico",content)


class ServidorLocal(BaseHTTPRequestHandler):
    db: Path = DB_DEFAULT
    csrf: str = ""

    def _enviar(self, data: bytes, content_type: str = "text/html; charset=utf-8", filename: str | None = None,
                codigo: int = 200) -> None:
        self.send_response(codigo)
        self.send_header("Content-Type",content_type)
        self.send_header("Content-Length",str(len(data)))
        self.send_header("Cache-Control","no-store")
        self.send_header("X-Content-Type-Options","nosniff")
        self.send_header("X-Frame-Options","DENY")
        self.send_header("Referrer-Policy","no-referrer")
        self.send_header("Content-Security-Policy","default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
        if filename:
            self.send_header("Content-Disposition",f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(data)

    def _trusted(self) -> bool:
        return self.headers.get("Host","").lower() in ("127.0.0.1:8785","localhost:8785",f"127.0.0.1:{self.server.server_port}",f"localhost:{self.server.server_port}")

    def _open(self) -> sqlite3.Connection:
        return preparar(self.db)

    def _redirecionar(self, cid: int, mensagem: str) -> None:
        url = "/?"+urllib.parse.urlencode({"campanha":cid,"info":mensagem[:160]})
        self.send_response(303)
        self.send_header("Location",url)
        self.send_header("Content-Length","0")
        self.send_header("Cache-Control","no-store")
        self.end_headers()

    def do_GET(self) -> None:
        if not self._trusted():
            return self._enviar(b"Invalid host", "text/plain", codigo=403)
        u = urllib.parse.urlsplit(self.path)
        q = urllib.parse.parse_qs(u.query)
        def val(key: str, default: str = "") -> str:
            return q.get(key,[default])[0]
        try:
            with self._open() as conn:
                camps=conn.execute("SELECT id FROM campanhas ORDER BY id DESC").fetchall()
                cid=int(val("campanha", str(camps[0]["id"] if camps else 0)))
                if not camps:
                    cid=campanha(conn,"CSA Geral")
                if u.path == "/":
                    page=render_page(conn,cid,val("status"),val("q"),max(1,int(val("pagina","1"))),val("info")[:160],self.csrf)
                    return self._enviar(page.encode("utf-8"))
                if u.path == "/historico":
                    return self._enviar(historico_html(conn,cid).encode("utf-8"))
                if u.path == "/exportar":
                    # Exportação por GET requer token para reduzir risco de vazamento por páginas externas.
                    if not secrets.compare_digest(val("csrf"),self.csrf):
                        return self._enviar(b"Forbidden", "text/plain", codigo=403)
                    import io
                    out = io.StringIO(newline="")
                    writer = csv.writer(out,delimiter=";")
                    cols = ("id","empresa","dominio","estado","cidade","contato_tipo","contato_valor","contato_origem_url","pontos_prioridade","evidencia","assunto","mensagem","aprovado_em")
                    writer.writerow(cols)
                    for r in conn.execute("SELECT * FROM leads_campanha WHERE campanha_id=? AND status='aprovado' ORDER BY pontos_prioridade DESC,id",(cid,)).fetchall():
                        if disponivel(conn,r)[0]:
                            writer.writerow([celula_excel(r[k]) for k in cols])
                    data=("\ufeff"+out.getvalue()).encode("utf-8")
                    return self._enviar(data,"text/csv; charset=utf-8",f"csa_aprovados_campanha_{cid}.csv")
                return self._enviar(b"Not found", "text/plain", codigo=404)
        except (ValueError, sqlite3.Error) as exc:
            return self._enviar(safe(str(exc)).encode("utf-8"),"text/plain; charset=utf-8",codigo=400)

    def do_POST(self) -> None:
        if not self._trusted() or self.path != "/acao":
            return self._enviar(b"Forbidden", "text/plain", codigo=403)
        origin = self.headers.get("Origin", "")
        if origin and origin not in (f"http://127.0.0.1:{self.server.server_port}",f"http://localhost:{self.server.server_port}"):
            return self._enviar(b"Forbidden", "text/plain", codigo=403)
        try:
            n=int(self.headers.get("Content-Length","0"))
            if n <= 0 or n > 20000:
                return self._enviar(b"Request too large", "text/plain", codigo=413)
            data=urllib.parse.parse_qs(self.rfile.read(n).decode("utf-8",errors="replace"),keep_blank_values=True)
            def val(key: str) -> str:
                return data.get(key,[""])[0]
            if not secrets.compare_digest(val("csrf"),self.csrf):
                return self._enviar(b"Forbidden", "text/plain", codigo=403)
            act=val("acao")
            cid=int(val("campanha_id") or 0)
            with self._open() as conn:
                if act=="criar_campanha":
                    cid=campanha(conn,val("nome"))
                    note="Campanha criada"
                elif act=="sincronizar":
                    n=sincronizar(conn,cid,int(val("limite")),int(val("min_pontos")),val("uf"))
                    note=f"{n} novos leads adicionados à revisão"
                elif act in ("editar","aprovar","status","bloquear","atualizar"):
                    lid=int(val("id"))
                    r=lead(conn,lid)
                    if r["campanha_id"]!=cid:
                        raise ValueError("Campanha incorreta")
                    if act=="editar":
                        editar(conn,lid,val("assunto"),val("mensagem"),val("anotacoes"))
                        note="Rascunho salvo; precisa de nova aprovação"
                    elif act=="aprovar":
                        aprovar(conn,lid,val("conferi")=="1")
                        note="Lead aprovado para contato manual"
                    elif act=="status":
                        transicao(conn,lid,val("destino"),val("nota"))
                        note="Andamento registrado"
                    elif act=="atualizar":
                        atualizar_rascunho(conn,lid)
                        note="Rascunho atualizado; faça nova revisão"
                    else:
                        if val("confirmar_bloqueio") != "1":
                            raise ValueError("Confirme o bloqueio")
                        tipo=val("tipo")
                        if tipo=="email" and r["contato_tipo"]!="email":
                            raise ValueError("Este contato não é e-mail")
                        valor=r["dominio"] if tipo=="dominio" else r["contato_valor"]
                        total=bloquear(conn,tipo,valor,val("motivo"))
                        note=f"Exclusão registrada em {total} leads"
                else:
                    raise ValueError("Ação desconhecida")
            return self._redirecionar(cid,note)
        except (ValueError,sqlite3.Error,UnicodeError) as exc:
            return self._redirecionar(cid if 'cid' in locals() else 0, "Erro: "+str(exc)[:150])

    def log_message(self, fmt, *args):
        # Acesso localhost sem logar contatos/URLs em terminal.
        pass


def main() -> int:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db",type=Path,default=DB_DEFAULT)
    sub=ap.add_subparsers(dest="comando",required=True)
    p=sub.add_parser("iniciar",help="Abre interface web local")
    p.add_argument("--porta",type=int,default=8785)
    p=sub.add_parser("sincronizar",help="Importa leads elegíveis para revisão")
    p.add_argument("--campanha",default="CSA Geral")
    p.add_argument("--limite",type=int,default=100)
    p.add_argument("--min-pontos",type=int,default=3)
    p.add_argument("--uf")
    p=sub.add_parser("resumo",help="Indicadores da campanha")
    p.add_argument("--campanha",default="CSA Geral")
    p=sub.add_parser("exportar",help="Exporta apenas leads aprovados, ainda válidos")
    p.add_argument("--campanha",default="CSA Geral")
    p.add_argument("--arquivo",type=Path,default=ROOT/"saida"/"leads_aprovados.csv")
    p=sub.add_parser("bloquear",help="Registra domínio ou e-mail na lista de exclusão")
    p.add_argument("--dominio")
    p.add_argument("--email")
    p.add_argument("--motivo",default="Solicitação de não contato")
    args=ap.parse_args()
    try:
        with preparar(args.db) as conn:
            if args.comando=="bloquear":
                if bool(args.dominio)==bool(args.email):
                    raise ValueError("Informe --dominio ou --email")
                total=bloquear(conn,"dominio" if args.dominio else "email",args.dominio or args.email,args.motivo)
                print(f"Bloqueio registrado. Leads afetados: {total}.")
            elif args.comando !="iniciar":
                cid=campanha(conn,args.campanha)
                if args.comando=="sincronizar":
                    n=sincronizar(conn,cid,args.limite,args.min_pontos,args.uf)
                    print(f"{n} leads adicionados à revisão. Nenhuma mensagem enviada.")
                elif args.comando=="resumo":
                    print(f"Campanha: {args.campanha}")
                    for k,v in metricas(conn,cid).items():
                        print(f"{k}: {v}")
                elif args.comando=="exportar":
                    n=exportar(conn,cid,args.arquivo)
                    print(f"{n} leads aprovados exportados em {args.arquivo}. Nenhuma mensagem enviada.")
        if args.comando=="iniciar":
            if not 1024<=args.porta<=65535:
                raise ValueError("Porta deve estar entre 1024 e 65535")
            handler=type("CSAServidor",(ServidorLocal,), {"db":args.db,"csrf":secrets.token_urlsafe(32)})
            with HTTPServer(("127.0.0.1",args.porta),handler) as srv:
                print(f"Gestor CSA aberto em http://127.0.0.1:{args.porta}",flush=True)
                print("Funciona só neste PC. Para encerrar, Ctrl+C. Nenhum e-mail é enviado.",flush=True)
                webbrowser.open(f"http://127.0.0.1:{args.porta}/")
                srv.serve_forever()
    except (ValueError,sqlite3.Error,OSError) as exc:
        print(f"Erro: {exc}",file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nGestor encerrado")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
