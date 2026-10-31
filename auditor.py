#!/usr/bin/env python3
"""CSA Prospecção: etapa 2, auditoria responsável e econômica de homepages.

Somente biblioteca padrão do Python; nenhum serviço de IA/API pago.
Respeita robots.txt, evita redes privadas, limita paralelismo e grava checkpoints.
Não mede Core Web Vitals, experiência visual nem renderização JavaScript.
"""
from __future__ import annotations

import argparse
import csv
import http.client
import ipaddress
import json
import re
import socket
import sqlite3
import ssl
import sys
import time
import urllib.parse
import urllib.robotparser
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from pipeline import DB_DEFAULT, ROOT, celula_excel, conectar, site_normalizado

USER_AGENT = "CSAProspeccaoAuditor/0.2 (+https://www.openstreetmap.org/copyright)"
MAX_HTML_BYTES = 768 * 1024
MAX_ROBOTS_BYTES = 512 * 1024
MAX_REDIRECTS = 5
MAX_ISSUE_SCORE = 20
GENERIC_EMAIL_PREFIX = {
    "contato", "contact", "comercial", "vendas", "sales", "atendimento", "sac",
    "info", "hello", "suporte", "support", "orcamento", "orcamentos",
    "faleconosco", "administrativo", "geral", "office",
}
SCHEMA_AUDITORIA = """
CREATE TABLE IF NOT EXISTS auditorias (
  dominio TEXT PRIMARY KEY REFERENCES websites(dominio) ON DELETE CASCADE,
  auditado_em TEXT NOT NULL,
  status TEXT NOT NULL,
  url_final TEXT,
  http_status INTEGER,
  robots_status TEXT,
  titulo TEXT,
  descricao TEXT,
  viewport TEXT,
  idioma TEXT,
  h1_count INTEGER,
  imagens INTEGER,
  imagens_sem_alt INTEGER,
  canonical TEXT,
  noindex INTEGER,
  formulario_contato INTEGER,
  emails_genericos_json TEXT NOT NULL DEFAULT '[]',
  telefone_publico_json TEXT NOT NULL DEFAULT '[]',
  tempo_cabecalhos_ms INTEGER,
  tamanho_html_bytes INTEGER,
  problemas_json TEXT NOT NULL DEFAULT '[]',
  pontos_prioridade INTEGER NOT NULL DEFAULT 0,
  erro TEXT
);
CREATE INDEX IF NOT EXISTS idx_auditorias_status ON auditorias(status);
CREATE INDEX IF NOT EXISTS idx_auditorias_prioridade ON auditorias(pontos_prioridade);
"""
COLUMNS = (
    "dominio", "auditado_em", "status", "url_final", "http_status", "robots_status",
    "titulo", "descricao", "viewport", "idioma", "h1_count", "imagens",
    "imagens_sem_alt", "canonical", "noindex", "formulario_contato",
    "emails_genericos_json", "telefone_publico_json", "tempo_cabecalhos_ms",
    "tamanho_html_bytes", "problemas_json", "pontos_prioridade", "erro",
)


class AuditoriaErro(Exception):
    pass


class BloqueioRede(AuditoriaErro):
    pass


class RedirecionamentoExterno(AuditoriaErro):
    pass


class ConexaoHTTP(http.client.HTTPConnection):
    """Liga no IP público já verificado, evitando nova resolução após o filtro."""
    def __init__(self, host: str, ip: str, port: int, timeout: float):
        super().__init__(host, port=port, timeout=timeout)
        self._ip_seguro = ip

    def connect(self):
        self.sock = socket.create_connection((self._ip_seguro, self.port), self.timeout)


class ConexaoHTTPS(http.client.HTTPSConnection):
    def __init__(self, host: str, ip: str, port: int, timeout: float):
        super().__init__(host, port=port, timeout=timeout, context=ssl.create_default_context())
        self._ip_seguro = ip

    def connect(self):
        sock = socket.create_connection((self._ip_seguro, self.port), self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


def url_segura(url: str, host_base: str | None = None) -> tuple[str, str, int, str]:
    """Somente http(s), portas padrão, hostname público, sem credenciais."""
    p = urllib.parse.urlsplit(url)
    scheme = p.scheme.lower()
    host = (p.hostname or "").lower().rstrip(".")
    if scheme not in ("https", "http") or not host or p.username or p.password:
        raise BloqueioRede("URL com protocolo ou credenciais não permitidos")
    try:
        port = p.port or (443 if scheme == "https" else 80)
    except ValueError as exc:
        raise BloqueioRede("Porta inválida") from exc
    if port != (443 if scheme == "https" else 80):
        raise BloqueioRede("Apenas portas HTTP/HTTPS padrão são permitidas")
    if site_normalizado(f"{scheme}://{host}") is None:
        raise BloqueioRede("Host inválido, endereço IP ou domínio não auditável")
    if host_base is not None and host.removeprefix("www.") != host_base.removeprefix("www."):
        raise RedirecionamentoExterno(f"Redirecionamento para domínio diferente: {host[:120]}")
    path = p.path or "/"
    if len(path) > 2048:
        raise BloqueioRede("URL excessivamente longa")
    return scheme, host, port, path + ("?" + p.query if p.query else "")


def ips_publicos(host: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise AuditoriaErro("Falha de DNS") from exc
    ips = list(dict.fromkeys(item[4][0] for item in infos))
    if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
        raise BloqueioRede("DNS retornou endereço privado, reservado ou inválido")
    return ips


def requisitar(url: str, timeout: float, limite_bytes: int, host_base: str) -> dict[str, Any]:
    scheme, host, port, path = url_segura(url, host_base)
    ips = ips_publicos(host, port)
    erro = None
    for ip in ips[:3]:
        con = (ConexaoHTTPS if scheme == "https" else ConexaoHTTP)(host, ip, port, timeout)
        try:
            inicio = time.monotonic()
            con.request("GET", path, headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,text/plain;q=0.8,*/*;q=0.1",
                "Accept-Encoding": "identity",
                "Connection": "close",
            })
            resp = con.getresponse()
            espera_ms = round((time.monotonic() - inicio) * 1000)
            cab = {k.lower(): v for k, v in resp.getheaders()}
            # Página limitada e só HTML, robôs são texto; nunca baixar multimídia grande.
            content_type = cab.get("content-type", "").lower()
            if limite_bytes == MAX_HTML_BYTES and content_type and not any(x in content_type for x in ("text/html", "application/xhtml+xml")):
                corpo = b""
            else:
                corpo = resp.read(limite_bytes + 1)
            return {
                "status": resp.status, "headers": cab,
                "body": corpo[:limite_bytes], "truncado": len(corpo) > limite_bytes,
                "tempo_ms": espera_ms, "url": url,
            }
        except (OSError, TimeoutError, ssl.SSLError, http.client.HTTPException) as exc:
            erro = exc
        finally:
            con.close()
    raise AuditoriaErro(f"Falha de conexão: {str(erro)[:160] if erro else 'sem resposta'}")


def redirecionar(url: str, location: str, host_base: str) -> str:
    destino = urllib.parse.urljoin(url, location)
    url_segura(destino, host_base)
    return destino


def buscar_robots(url: str, timeout: float, host_base: str) -> tuple[urllib.robotparser.RobotFileParser | None, str, float]:
    """Retorna parser, status, crawl_delay. Indisponibilidade técnica bloqueia."""
    p = urllib.parse.urlsplit(url)
    target = f"{p.scheme}://{p.netloc}/robots.txt"
    for _ in range(MAX_REDIRECTS + 1):
        try:
            r = requisitar(target, timeout, MAX_ROBOTS_BYTES, host_base)
        except (AuditoriaErro, ValueError) as exc:
            return None, "inacessivel:" + str(exc)[:100], 0
        if r["status"] in (301, 302, 303, 307, 308) and r["headers"].get("location"):
            try:
                target = redirecionar(target, r["headers"]["location"], host_base)
            except AuditoriaErro as exc:
                return None, "redirecionamento_robots_bloqueado:" + str(exc)[:75], 0
            continue
        if r["status"] in (401, 403, 429) or r["status"] >= 500:
            return None, f"bloqueado_http_{r['status']}", 0
        if 400 <= r["status"] < 500:
            return None, f"ausente_http_{r['status']}", 0
        if r["status"] != 200 or r["truncado"]:
            return None, "inacessivel_ou_grande", 0
        robot = urllib.robotparser.RobotFileParser()
        robot.parse(r["body"].decode("utf-8-sig", "replace").splitlines())
        delay = robot.crawl_delay(USER_AGENT) or robot.crawl_delay("*") or 0
        try:
            delay = float(delay)
        except (ValueError, TypeError):
            delay = 0
        return robot, "lido", max(0.0, delay)
    return None, "redirecionamentos_robots_excedidos", 0


def buscar_pagina(url: str, timeout: float, pausa: float, host_base: str) -> tuple[dict[str, Any], str]:
    robots_cache: dict[str, tuple[urllib.robotparser.RobotFileParser | None, str, float]] = {}
    ultimo_robots = ""
    for i in range(MAX_REDIRECTS + 1):
        origem = urllib.parse.urlsplit(url)
        key = f"{origem.scheme}://{origem.netloc}"
        if key not in robots_cache:
            robots_cache[key] = buscar_robots(url, timeout, host_base)
        parser, status_robots, delay = robots_cache[key]
        ultimo_robots = status_robots
        if status_robots.startswith(("bloqueado", "inacessivel", "redirecionamento", "redirecionamentos")):
            raise AuditoriaErro("Robots.txt não permite auditoria: " + status_robots)
        if parser is not None and not parser.can_fetch(USER_AGENT, url):
            raise AuditoriaErro("Robots.txt proíbe acesso à página inicial")
        if delay > 30:
            raise AuditoriaErro("Crawl-delay acima de 30 segundos; auditoria ignorada")
        time.sleep(max(pausa, delay))
        r = requisitar(url, timeout, MAX_HTML_BYTES, host_base)
        r["url_final"] = url
        if r["status"] in (301, 302, 303, 307, 308) and r["headers"].get("location"):
            if i == MAX_REDIRECTS:
                raise AuditoriaErro("Redirecionamentos demais")
            url = redirecionar(url, r["headers"]["location"], host_base)
            continue
        return r, ultimo_robots
    raise AuditoriaErro("Redirecionamentos demais")


class LeitorHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self._in_title = False
        self.description = ""
        self.viewport = ""
        self.lang = ""
        self.h1 = 0
        self.img = 0
        self.img_missing_alt = 0
        self.canonical = ""
        self.noindex = False
        self.form = 0
        self.generic_emails: set[str] = set()
        self.telefones: set[str] = set()
        self.links_contato = 0
        self.jsonld = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        tag = tag.lower()
        a = {k.lower(): (v or "") for k, v in attrs if k}
        if tag == "html":
            self.lang = a.get("lang", "")[:30]
        elif tag == "title":
            self._in_title = True
        elif tag == "h1":
            self.h1 += 1
        elif tag == "img":
            self.img += 1
            if "alt" not in a:
                self.img_missing_alt += 1
        elif tag == "form":
            self.form += 1
        elif tag == "script" and a.get("type", "").lower() == "application/ld+json":
            self.jsonld = True
        elif tag == "meta":
            name = (a.get("name") or a.get("property") or "").lower()
            if name == "description" and not self.description:
                self.description = a.get("content", "")[:500]
            elif name == "viewport":
                self.viewport = a.get("content", "")[:300]
            elif name in ("robots", "googlebot") and "noindex" in a.get("content", "").lower():
                self.noindex = True
        elif tag == "link" and "canonical" in a.get("rel", "").lower().split():
            self.canonical = a.get("href", "")[:500]
        elif tag == "a":
            href = a.get("href", "")
            if href.startswith("mailto:"):
                mail = urllib.parse.unquote(href[7:].split("?")[0]).strip().lower()
                if re.fullmatch(r"[a-z0-9][a-z0-9._%+-]{0,63}@[a-z0-9.-]+\.[a-z]{2,}", mail) and mail.split("@", 1)[0] in GENERIC_EMAIL_PREFIX:
                    self.generic_emails.add(mail)
            elif href.startswith("tel:"):
                fone = urllib.parse.unquote(href[4:])[:40]
                if re.fullmatch(r"[+0-9() .-]{8,40}", fone):
                    self.telefones.add(fone.strip())
            elif "contato" in href.lower() or "contact" in href.lower():
                self.links_contato += 1

    def handle_endtag(self, tag: str):
        if tag.lower() == "title":
            self._in_title = False

    def handle_data(self, data: str):
        if self._in_title:
            self.title_parts.append(data)


def decodificar_html(body: bytes, content_type: str) -> str:
    enc = re.search(r"charset\s*=\s*['\"]?([\w-]+)", content_type, re.I)
    encoding = enc.group(1) if enc else "utf-8"
    try:
        return body.decode(encoding, "replace")
    except (LookupError, ValueError):
        return body.decode("utf-8", "replace")


def avaliar_html(body: bytes, content_type: str, url_final: str, tempo_ms: int, truncated: bool = False) -> dict[str, Any]:
    parser = LeitorHTML()
    try:
        parser.feed(decodificar_html(body, content_type))
        parser.close()
    except (ValueError, AssertionError):
        # HTML corrompido não deve derrubar o processamento dos outros sites.
        pass
    titulo = " ".join(" ".join(parser.title_parts).split())[:500]
    problemas: list[str] = []
    pontos = 0

    def flag(chave: str, peso: int):
        nonlocal pontos
        problemas.append(chave)
        pontos += peso

    if url_final.startswith("http://"):
        flag("sem_https", 4)
    if not titulo:
        flag("sem_title", 3)
    if not parser.description.strip():
        flag("sem_meta_description", 2)
    if not parser.viewport:
        flag("sem_meta_viewport", 3)
    if not parser.lang:
        flag("sem_lang_html", 1)
    if parser.h1 == 0:
        flag("sem_h1", 2)
    if parser.img_missing_alt:
        flag("imagens_sem_alt", 1)
    if parser.noindex:
        flag("noindex_na_home", 4)
    if truncated:
        flag("html_maior_768kb", 1)
    # Sem contato ou canonical não implica obrigatoriamente falha: registrar somente como dado.
    return {
        "titulo": titulo,
        "descricao": parser.description.strip(),
        "viewport": parser.viewport.strip(),
        "idioma": parser.lang,
        "h1_count": parser.h1,
        "imagens": parser.img,
        "imagens_sem_alt": parser.img_missing_alt,
        "canonical": parser.canonical,
        "noindex": int(parser.noindex),
        "formulario_contato": int(bool(parser.form or parser.links_contato)),
        "emails_genericos_json": json.dumps(sorted(parser.generic_emails)[:10], ensure_ascii=False),
        "telefone_publico_json": json.dumps(sorted(parser.telefones)[:10], ensure_ascii=False),
        "problemas_json": json.dumps(problemas, ensure_ascii=False),
        "pontos_prioridade": min(pontos, MAX_ISSUE_SCORE),
    }


def executar_auditoria(dominio: str, url: str, timeout: float, pausa: float) -> dict[str, Any]:
    row: dict[str, Any] = dict.fromkeys(COLUMNS)
    row.update({
        "dominio": dominio, "auditado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "erro", "pontos_prioridade": 0,
        "emails_genericos_json": "[]", "telefone_publico_json": "[]", "problemas_json": "[]",
    })
    normalizado = site_normalizado(url)
    if not normalizado or normalizado[0] != dominio:
        row["erro"] = "Endereço não corresponde ao domínio cadastrado"
        return row
    # Etapa 1 já guarda domínio-raiz. Não seguir redirects para outros domínios.
    tentativas = [url]
    if url.startswith("https://"):
        tentativas.append("http://" + urllib.parse.urlsplit(url).netloc + "/")
    for idx, tentativa in enumerate(tentativas):
        try:
            resposta, status_robots = buscar_pagina(tentativa, timeout, pausa, dominio)
            row.update({
                "url_final": resposta["url_final"], "http_status": resposta["status"],
                "robots_status": status_robots, "tempo_cabecalhos_ms": resposta["tempo_ms"],
                "tamanho_html_bytes": len(resposta["body"]),
            })
            if resposta["status"] >= 400:
                row["status"] = "http_erro"
                row["erro"] = f"HTTP {resposta['status']}"
                return row
            ct = resposta["headers"].get("content-type", "")
            if ct and not any(x in ct.lower() for x in ("text/html", "application/xhtml+xml")):
                row["status"] = "nao_html"
                return row
            if resposta["headers"].get("content-encoding", "identity").lower() not in ("", "identity"):
                row["status"] = "codificacao_nao_suportada"
                return row
            row.update(avaliar_html(resposta["body"], ct, resposta["url_final"], resposta["tempo_ms"], resposta["truncado"]))
            if "noindex" in resposta["headers"].get("x-robots-tag", "").lower() and not row["noindex"]:
                row["noindex"] = 1
                flags = json.loads(row["problemas_json"])
                flags.append("noindex_cabecalho_http")
                row["problemas_json"] = json.dumps(flags, ensure_ascii=False)
                row["pontos_prioridade"] = min(row["pontos_prioridade"] + 4, MAX_ISSUE_SCORE)
            row["status"] = "ok"
            return row
        except RedirecionamentoExterno as exc:
            row["status"] = "redirecionamento_externo"
            row["erro"] = str(exc)[:300]
            return row
        except BloqueioRede as exc:
            row["status"] = "bloqueio_seguranca"
            row["erro"] = str(exc)[:300]
            return row
        except AuditoriaErro as exc:
            msg = str(exc)
            if "Robots.txt" in msg:
                # Se a origem HTTPS não responde, testar a origem HTTP separada
                # com SEU PRÓPRIO robots.txt. Nunca contornar regra Disallow/403/429.
                if idx == 0 and len(tentativas) > 1 and (
                    "inacessivel:" in msg or "bloqueado_http_5" in msg
                ):
                    continue
                row["status"] = "bloqueado_robots"
                row["robots_status"] = msg[:300]
                row["erro"] = msg[:300]
                return row
            row["erro"] = msg[:300]
            if idx < len(tentativas) - 1:
                continue
    return row


def garantir_tabela(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_AUDITORIA)


def selecionar_pendentes(conn: sqlite3.Connection, limite: int, dias: int, uf: str | None = None) -> list[tuple[str, str]]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=dias)).isoformat(timespec="seconds")
    return conn.execute("""
       SELECT w.dominio, w.url FROM websites w
       LEFT JOIN auditorias a ON a.dominio = w.dominio
       WHERE (a.dominio IS NULL OR a.auditado_em < ?)
         AND (? IS NULL OR EXISTS (SELECT 1 FROM estabelecimentos e
                                  WHERE e.dominio = w.dominio AND UPPER(e.estado) = ?))
       ORDER BY CASE WHEN a.dominio IS NULL THEN 0 ELSE 1 END, w.dominio
       LIMIT ?
    """, (cutoff, uf, uf, limite)).fetchall()


def salvar(conn: sqlite3.Connection, linha: dict[str, Any]) -> None:
    placeholders = ",".join("?" for _ in COLUMNS)
    updates = ",".join(f"{c}=excluded.{c}" for c in COLUMNS if c != "dominio")
    with conn:
        conn.execute(f"INSERT INTO auditorias ({','.join(COLUMNS)}) VALUES ({placeholders}) "
                     f"ON CONFLICT(dominio) DO UPDATE SET {updates}",
                     [linha.get(k) for k in COLUMNS])


def auditar(db: Path, limite: int, workers: int, timeout: float, pausa: float, dias: int, uf: str | None) -> None:
    conn = conectar(db)
    try:
        garantir_tabela(conn)
        total_sites = conn.execute("SELECT COUNT(*) FROM websites").fetchone()[0]
        if not total_sites:
            print("Banco sem sites. Execute INICIAR_WINDOWS.bat ou importe um CSV antes.")
            return
        pendentes = selecionar_pendentes(conn, limite, dias, uf)
        print(f"Domínios no banco: {total_sites:,}. Selecionados para auditoria: {len(pendentes):,}.", flush=True)
        if not pendentes:
            print("Sem sites pendentes. Use --dias 0 para refazer auditorias existentes.")
            return
        inicio = time.monotonic()
        concluidos = 0
        ok = 0
        # Fila limitada: não cria 10 mil tarefas simultâneas, economizando RAM.
        with ThreadPoolExecutor(max_workers=workers) as pool:
            cursor = iter(pendentes)
            tarefas = {}
            for _ in range(min(workers * 3, len(pendentes))):
                d, u = next(cursor)
                tarefas[pool.submit(executar_auditoria, d, u, timeout, pausa)] = d
            while tarefas:
                finalizadas, _ = wait(tarefas, return_when=FIRST_COMPLETED)
                for futuro in finalizadas:
                    dominio = tarefas.pop(futuro)
                    try:
                        row = futuro.result()
                    except Exception as exc:
                        row = dict.fromkeys(COLUMNS)
                        row.update({"dominio": dominio, "auditado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                    "status": "erro", "erro": f"Erro inesperado: {type(exc).__name__}: {str(exc)[:160]}",
                                    "pontos_prioridade": 0, "emails_genericos_json": "[]",
                                    "telefone_publico_json": "[]", "problemas_json": "[]"})
                    salvar(conn, row)
                    concluidos += 1
                    ok += row["status"] == "ok"
                    if concluidos % 10 == 0 or concluidos == len(pendentes):
                        print(f"  {concluidos:,}/{len(pendentes):,} concluídos | {ok:,} HTML analisados | "
                              f"{time.monotonic() - inicio:.0f}s", flush=True)
                    prox = next(cursor, None)
                    if prox is not None:
                        d, u = prox
                        tarefas[pool.submit(executar_auditoria, d, u, timeout, pausa)] = d
        print(f"Finalizado: {concluidos:,} tentativas, {ok:,} sites HTML analisados. "
              f"Tempo {time.monotonic() - inicio:.1f}s.")
    finally:
        conn.close()


def exportar(db: Path, saida: Path):
    conn = conectar(db)
    try:
        garantir_tabela(conn)
        saida.parent.mkdir(parents=True, exist_ok=True)
        sql = """SELECT a.dominio, e.nome, e.cidade, e.estado, e.categoria,
                        a.url_final, a.auditado_em, a.status, a.http_status,
                        a.titulo, a.descricao, a.viewport, a.idioma,
                        a.h1_count, a.imagens, a.imagens_sem_alt,
                        a.canonical, a.noindex, a.formulario_contato,
                        a.emails_genericos_json, a.telefone_publico_json,
                        a.tempo_cabecalhos_ms, a.tamanho_html_bytes,
                        a.problemas_json, a.pontos_prioridade, a.robots_status, a.erro
                 FROM auditorias a
                 LEFT JOIN estabelecimentos e ON e.id_fonte = (
                    SELECT MIN(x.id_fonte) FROM estabelecimentos x WHERE x.dominio=a.dominio
                 )
                 ORDER BY CASE WHEN a.status='ok' THEN 0 ELSE 1 END,
                          a.pontos_prioridade DESC, a.dominio"""
        campos = ["dominio", "empresa", "cidade", "estado", "categoria", "url_final", "auditado_em",
                  "status", "http_status", "titulo", "descricao", "viewport", "idioma", "h1_count",
                  "imagens", "imagens_sem_alt", "canonical", "noindex", "tem_contato_ou_formulario",
                  "emails_genericos", "telefones_publicos", "tempo_cabecalhos_ms", "html_bytes",
                  "problemas_detectados", "prioridade", "robots_status", "erro"]
        with saida.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow(campos)
            n = 0
            for linha in conn.execute(sql):
                writer.writerow([celula_excel(valor) for valor in linha])
                n += 1
        print(f"Exportadas {n:,} auditorias para {saida}")
        print("Origem da base OSM: © OpenStreetMap contributors, licença ODbL.")
    finally:
        conn.close()


def resumo(db: Path):
    conn = conectar(db)
    try:
        garantir_tabela(conn)
        for status, n in conn.execute("SELECT status, COUNT(*) FROM auditorias GROUP BY status ORDER BY COUNT(*) DESC"):
            print(f"{status}: {n:,}")
        total = conn.execute("SELECT COUNT(*) FROM auditorias").fetchone()[0]
        print(f"Total de domínios tentados: {total:,}")
    finally:
        conn.close()


def main() -> int:
    cli = argparse.ArgumentParser(description="CSA | Auditor técnico de homepages | Sem APIs pagas")
    cli.add_argument("--db", type=Path, default=DB_DEFAULT, help="Banco da etapa 1")
    sub = cli.add_subparsers(dest="comando", required=True)
    a = sub.add_parser("auditar", help="Auditar sites pendentes com checkpoint SQLite")
    a.add_argument("--limite", type=int, default=100, help="Até 100 sites por execução, por padrão")
    a.add_argument("--workers", type=int, default=8, help="Concorrência 1..16 (padrão 8)")
    a.add_argument("--timeout", type=float, default=10, help="Timeout em segundos (padrão 10)")
    a.add_argument("--pausa", type=float, default=0.5, help="Pausa mínima entre robots e página, segundos")
    a.add_argument("--dias", type=int, default=30, help="Refazer somente após N dias (0 força reauditoria)")
    a.add_argument("--uf", type=str, help="Restringir estado, como SP")
    e = sub.add_parser("exportar", help="Gerar CSV para revisão, sem IA")
    e.add_argument("--arquivo", type=Path, default=ROOT / "saida" / "auditorias.csv")
    sub.add_parser("resumo", help="Totais por status")
    args = cli.parse_args()
    try:
        if args.comando == "auditar":
            if not (1 <= args.limite <= 100000 and 1 <= args.workers <= 16 and 1 <= args.timeout <= 40 and 0 <= args.pausa <= 30 and 0 <= args.dias <= 3650):
                cli.error("Parâmetros fora dos limites permitidos")
            if args.uf and not re.fullmatch(r"[A-Za-z]{2}", args.uf):
                cli.error("UF precisa ter duas letras")
            auditar(args.db, args.limite, args.workers, args.timeout, args.pausa, args.dias,
                    args.uf.upper() if args.uf else None)
        elif args.comando == "exportar":
            exportar(args.db, args.arquivo)
        else:
            resumo(args.db)
    except (sqlite3.Error, OSError) as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
