#!/usr/bin/env python3
"""CSA Prospecção etapa 4: contatos corporativos publicados nos próprios sites.

Sem APIs, coleta sem inferência de e-mail, nenhuma mensagem enviada.
Apenas a homepage e até duas páginas de contato anunciadas pela própria homepage.
Robots.txt, SSRF e conexões de rede são protegidos pelo módulo auditor.py.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
import time
import urllib.parse
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

from auditor import (
    AuditoriaErro, BloqueioRede, RedirecionamentoExterno,
    USER_AGENT, buscar_robots, decodificar_html, redirecionar,
    requisitar, url_segura,
)
from pipeline import DB_DEFAULT, ROOT, celula_excel, conectar, site_normalizado

VERSION = 1
MAX_PAGE_BYTES = 512 * 1024
MAX_CONTACT_PAGES = 2
MAX_REDIRECTS = 4
CONTACT_KEYWORDS = (
    "contato", "contact", "fale-conosco", "fale_conosco", "faleconosco",
    "orcamento", "orçamento", "solicite-uma-proposta", "solicitar-proposta",
    "atendimento", "get-in-touch", "fale com", "entre em contato", "peça seu orçamento",
)
CORPORATE_LOCALPARTS = {
    "contato", "contatos", "contact", "comercial", "vendas", "sales",
    "atendimento", "sac", "info", "hello", "suporte", "support",
    "orcamento", "orcamentos", "faleconosco", "administrativo",
    "geral", "office", "negocios", "business", "propostas", "pedidos",
    "relacionamento", "customer", "customerservice",
}
PERSONAL_DOMAINS = {"gmail.com", "hotmail.com", "outlook.com", "yahoo.com", "yahoo.com.br", "icloud.com", "uol.com.br", "bol.com.br"}
INVALID_DOMAINS = {"example.com", "example.org", "example.net", "localhost", "test.com", "dominio.com.br", "seusite.com.br"}
EMAIL_RE = re.compile(r"(?<![\w.+-])([a-z0-9][a-z0-9._%+-]{0,63}@[a-z0-9.-]+\.[a-z]{2,24})(?![\w.-])", re.I)
PHONE_RE = re.compile(r"(?<!\d)(?:\+?55[\s.-]?)?\(?([1-9][0-9])\)?[\s.-]*(9?[2-9][0-9]{3})[\s.-]*([0-9]{4})(?!\d)")
SCHEMA = """
CREATE TABLE IF NOT EXISTS enriquecimentos (
 dominio TEXT PRIMARY KEY REFERENCES websites(dominio) ON DELETE CASCADE,
 versao INTEGER NOT NULL, processado_em TEXT NOT NULL, status TEXT NOT NULL,
 paginas_lidas INTEGER NOT NULL DEFAULT 0, contatos_encontrados INTEGER NOT NULL DEFAULT 0,
 erro TEXT
);
CREATE TABLE IF NOT EXISTS contatos_publicos (
 dominio TEXT NOT NULL REFERENCES websites(dominio) ON DELETE CASCADE,
 tipo TEXT NOT NULL, valor TEXT NOT NULL, origem_url TEXT NOT NULL,
 encontrado_em TEXT NOT NULL, confianca TEXT NOT NULL,
 PRIMARY KEY (dominio, tipo, valor)
);
CREATE INDEX IF NOT EXISTS idx_contatos_tipo ON contatos_publicos(tipo);
CREATE TABLE IF NOT EXISTS bloqueios_prospeccao (
 tipo TEXT NOT NULL CHECK(tipo IN ('dominio','email')),
 valor TEXT NOT NULL, motivo TEXT, registrado_em TEXT NOT NULL,
 PRIMARY KEY(tipo,valor)
);
"""


def agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def limpar_email(raw: str) -> str | None:
    value = urllib.parse.unquote(raw).strip().lower().strip(" <>.,;:\"'()")
    if len(value) > 254 or not EMAIL_RE.fullmatch(value):
        return None
    local, domain = value.rsplit("@", 1)
    if local not in CORPORATE_LOCALPARTS or domain in INVALID_DOMAINS or ".." in value:
        return None
    if domain.startswith("-") or any(part.startswith("-") or part.endswith("-") for part in domain.split(".")):
        return None
    return value


def normalizar_telefone(value: str) -> str | None:
    # Exige DDD brasileiro + 8 ou 9 dígitos. Não supõe DDD ausente.
    numbers = re.sub(r"\D", "", urllib.parse.unquote(value))
    if numbers.startswith("55") and len(numbers) in (12, 13):
        numbers = numbers[2:]
    if len(numbers) not in (10, 11):
        return None
    ddd, local = numbers[:2], numbers[2:]
    if ddd[0] == "0" or ddd[1] == "0" or local[0] not in "23456789":
        return None
    if len(local) == 9 and local[0] != "9":
        return None
    return "+55" + numbers


def whatsapp_link(link: str) -> str | None:
    try:
        p = urllib.parse.urlsplit(link)
        host = (p.hostname or "").lower()
        if p.scheme != "https" or p.username or p.password or p.port not in (None, 443):
            return None
        if host in ("wa.me", "www.wa.me"):
            digits = p.path.strip("/")
        elif host in ("api.whatsapp.com", "web.whatsapp.com") and p.path.rstrip("/") == "/send":
            digits = urllib.parse.parse_qs(p.query).get("phone", [""])[0]
        else:
            return None
        tel = normalizar_telefone(digits)
        return "https://wa.me/" + tel[1:] if tel else None
    except ValueError:
        return None


def link_contato(url: str, href: str, rotulo: str, dominio: str) -> str | None:
    try:
        novo = urllib.parse.urljoin(url, href.strip())
        scheme, host, port, _ = url_segura(novo, dominio)
        p = urllib.parse.urlsplit(novo)
        # Só páginas HTML plausíveis, sem queries nem fragmentos; não seguir fora do site.
        if p.query or p.username or p.password or not p.path or p.path == "/":
            return None
        if len(p.path) > 150 or p.path.count("/") > 4:
            return None
        if re.search(r"\.(?:pdf|zip|png|jpg|jpeg|gif|webp|js|css|xml|json|php\d*|aspx)$", p.path, re.I):
            return None
        alvo = urllib.parse.unquote(p.path).lower().replace("_", "-")
        label = rotulo.lower().strip()
        if not any(key in alvo or key in label for key in CONTACT_KEYWORDS):
            return None
        # Use o caminho anunciado pelo próprio site, não adivinhe páginas.
        return urllib.parse.urlunsplit((scheme, host, p.path.rstrip("/") + "/" if p.path.endswith("/") else p.path, "", ""))
    except (AuditoriaErro, ValueError, UnicodeError):
        return None


class ExtratorContatos(HTMLParser):
    def __init__(self, pagina_url: str, dominio: str):
        super().__init__(convert_charrefs=True)
        self.pagina_url = pagina_url
        self.dominio = dominio
        self.mailtos: set[str] = set()
        self.telefones: set[str] = set()
        self.whatsapps: set[str] = set()
        self.links: set[str] = set()
        self.texto: list[str] = []
        self._invisible = 0
        self._anchors: list[tuple[str, list[str]]] = []

    def handle_starttag(self, tag, attrs):
        a = {key.lower(): (value or "") for key, value in attrs if key}
        if tag in ("script", "style", "noscript", "svg"):
            self._invisible += 1
        if tag != "a":
            return
        href = a.get("href", "").strip()
        self._anchors.append((href, []))
        if href.lower().startswith("mailto:"):
            # Não processa query string que pode carregar corpo/assunto.
            value = urllib.parse.unquote(href[7:].split("?", 1)[0])
            for piece in re.split(r"[,;]", value):
                mail = limpar_email(piece)
                if mail:
                    self.mailtos.add(mail)
        elif href.lower().startswith("tel:"):
            phone = normalizar_telefone(href[4:].split("?", 1)[0])
            if phone:
                self.telefones.add(phone)
        elif href.startswith("https://"):
            wa = whatsapp_link(href)
            if wa:
                self.whatsapps.add(wa)

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg"):
            self._invisible = max(0, self._invisible - 1)
        if tag == "a" and self._anchors:
            href, pieces = self._anchors.pop()
            candidate = link_contato(self.pagina_url, href, " ".join(pieces), self.dominio)
            if candidate:
                self.links.add(candidate)

    def handle_data(self, data):
        if self._invisible:
            return
        if self._anchors:
            self._anchors[-1][1].append(data[:250])
        if data.strip():
            self.texto.append(data[:2000])

    def contatos(self) -> list[tuple[str, str, str]]:
        content = " ".join(self.texto)[:150000]
        emails = set(self.mailtos)
        emails.update(mail for m in EMAIL_RE.finditer(content) if (mail := limpar_email(m.group(1))))
        phones = set(self.telefones)
        for m in PHONE_RE.finditer(content):
            fone = normalizar_telefone("".join(m.groups()))
            if fone:
                phones.add(fone)
        result = []
        for mail in sorted(emails):
            domain = mail.rsplit("@", 1)[1]
            # A origem é o site. Domínios distintos e e-mail gratuito são dados de menor confiança.
            quality = "alta" if domain == self.dominio or domain.endswith("." + self.dominio) else "media"
            result.append(("email", mail, quality))
        result.extend(("telefone", phone, "media") for phone in sorted(phones))
        result.extend(("whatsapp", url, "media") for url in sorted(self.whatsapps))
        return result


def buscar_html(url: str, dominio: str, timeout: float, pausa: float,
                robots_cache: dict) -> tuple[str, str]:
    """Faz requisições sequenciais; robots de cada origem com cache apenas nesta execução."""
    for _ in range(MAX_REDIRECTS + 1):
        scheme, host, _, _ = url_segura(url, dominio)
        origin = f"{scheme}://{host}"
        if origin not in robots_cache:
            robots_cache[origin] = buscar_robots(url, timeout, dominio)
        robot, status, delay = robots_cache[origin]
        if status.startswith(("bloqueado", "inacessivel", "redirecionamento")):
            raise AuditoriaErro("robots.txt indisponível ou bloqueado: " + status)
        if robot is not None and not robot.can_fetch(USER_AGENT, url):
            raise AuditoriaErro("robots.txt proíbe acesso à página: " + url[:140])
        if delay > 30:
            raise AuditoriaErro("Crawl-delay acima do limite: acesso cancelado")
        time.sleep(max(pausa, float(delay)))
        r = requisitar(url, timeout, MAX_PAGE_BYTES, dominio)
        if r["status"] in (301, 302, 303, 307, 308) and r["headers"].get("location"):
            url = redirecionar(url, r["headers"]["location"], dominio)
            continue
        if r["status"] != 200:
            raise AuditoriaErro(f"HTTP {r['status']}")
        if r["headers"].get("content-encoding", "identity").lower() not in ("", "identity"):
            raise AuditoriaErro("Codificação HTTP não suportada")
        ctype = r["headers"].get("content-type", "").lower()
        if ctype and not any(x in ctype for x in ("text/html", "application/xhtml+xml")):
            raise AuditoriaErro("Conteúdo não é HTML")
        if r.get("truncado"):
            raise AuditoriaErro("HTML maior que o limite de 512 KiB")
        return decodificar_html(r["body"], ctype), url
    raise AuditoriaErro("Redirecionamentos demais")


def enriquecer_dominio(dominio: str, url: str, timeout: float,
                        pausa: float) -> dict:
    result = {"dominio": dominio, "status": "erro", "paginas_lidas": 0,
              "contatos": [], "erro": ""}
    if site_normalizado(url) is None or site_normalizado(url)[0] != dominio:
        result["erro"] = "URL não corresponde ao domínio"
        return result
    cache = {}
    try:
        html, url_final = buscar_html(url, dominio, timeout, pausa, cache)
        parser = ExtratorContatos(url_final, dominio)
        parser.feed(html)
        result["paginas_lidas"] += 1
        found = [(tipo, val, url_final, quality) for tipo, val, quality in parser.contatos()]
        urls = sorted(parser.links)
        for link in urls[:MAX_CONTACT_PAGES]:
            try:
                contact_html, contact_url = buscar_html(link, dominio, timeout, pausa, cache)
                sub = ExtratorContatos(contact_url, dominio)
                sub.feed(contact_html)
                result["paginas_lidas"] += 1
                found.extend((tipo, val, contact_url, quality) for tipo, val, quality in sub.contatos())
            except (AuditoriaErro, ValueError, UnicodeError) as exc:
                result["erro"] = (result["erro"] + " | " + str(exc)[:140]).strip(" |")[:300]
        # Evita múltiplas linhas para o mesmo contato em diferentes páginas.
        unique = {}
        for tipo, val, source, quality in found:
            k = (tipo, val)
            if k not in unique or (quality == "alta" and unique[k][3] != "alta"):
                unique[k] = (tipo, val, source, quality)
        result["contatos"] = list(unique.values())
        result["status"] = "ok" if result["contatos"] else "sem_contatos"
    except (AuditoriaErro, ValueError, UnicodeError, OSError) as exc:
        result["erro"] = str(exc)[:300]
        if isinstance(exc, (BloqueioRede, RedirecionamentoExterno)):
            result["status"] = "bloqueio_seguranca"
        elif "robots.txt" in str(exc).lower():
            result["status"] = "bloqueado_robots"
    return result


def preparar(db: Path) -> sqlite3.Connection:
    conn = conectar(db)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    # Permite usar a etapa 4 mesmo sem ter executado IA/auditoria ainda.
    from auditor import garantir_tabela
    from diagnosticos import SCHEMA as SCHEMA_DIAGNOSTICOS
    garantir_tabela(conn)
    conn.executescript(SCHEMA_DIAGNOSTICOS)
    return conn


def salvar(conn: sqlite3.Connection, result: dict) -> None:
    data = agora()
    domain = result["dominio"]
    # Uma coleta bloqueada não apaga a coleta anterior. Uma nova visita bem-sucedida a substitui.
    with conn:
        if result["status"] in ("ok", "sem_contatos"):
            conn.execute("DELETE FROM contatos_publicos WHERE dominio=?", (domain,))
            for typ, value, url, quality in result["contatos"]:
                if conn.execute("SELECT 1 FROM bloqueios_prospeccao WHERE (tipo='dominio' AND valor=?) OR (tipo='email' AND valor=?)", (domain, value)).fetchone():
                    continue
                conn.execute("INSERT INTO contatos_publicos VALUES (?,?,?,?,?,?)",
                             (domain, typ, value, url, data, quality))
        conn.execute("""INSERT INTO enriquecimentos (dominio,versao,processado_em,status,paginas_lidas,contatos_encontrados,erro)
                     VALUES (?,?,?,?,?,?,?) ON CONFLICT(dominio) DO UPDATE SET
                     versao=excluded.versao,processado_em=excluded.processado_em,
                     status=excluded.status,paginas_lidas=excluded.paginas_lidas,
                     contatos_encontrados=excluded.contatos_encontrados,erro=excluded.erro""",
                     (domain, VERSION, data, result["status"], result["paginas_lidas"],
                      len(result["contatos"]), result["erro"][:300]))


def coletar(db: Path, limite: int, workers: int, timeout: float, pausa: float,
            dias: int, uf: str | None) -> dict:
    conn = preparar(db)
    try:
        corte = (datetime.now(timezone.utc) - timedelta(days=dias)).isoformat(timespec="seconds")
        jobs = conn.execute("""SELECT w.dominio, COALESCE(a.url_final,w.url) AS url
          FROM websites w
          LEFT JOIN auditorias a ON a.dominio=w.dominio
          LEFT JOIN enriquecimentos c ON c.dominio=w.dominio
          WHERE (c.dominio IS NULL OR c.processado_em < ? OR c.versao <> ?)
            AND (a.dominio IS NULL OR a.status='ok')
            AND NOT EXISTS (SELECT 1 FROM bloqueios_prospeccao b WHERE b.tipo='dominio' AND b.valor=w.dominio)
            AND (? IS NULL OR EXISTS (SELECT 1 FROM estabelecimentos e WHERE e.dominio=w.dominio AND UPPER(e.estado)=?))
          ORDER BY COALESCE(a.pontos_prioridade,0) DESC,w.dominio LIMIT ?""",
          (corte, VERSION, uf, uf, limite)).fetchall()
        print(f"Selecionados: {len(jobs):,} sites. Até {MAX_CONTACT_PAGES} páginas adicionais por site.", flush=True)
        stats = {"processados": 0, "com_contato": 0, "sem_contato": 0, "erros": 0}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            iterator = iter(jobs)
            futures = {}
            for _ in range(min(len(jobs), workers * 2)):
                row = next(iterator)
                futures[pool.submit(enriquecer_dominio, row["dominio"], row["url"], timeout, pausa)] = row["dominio"]
            while futures:
                done, _ = wait(futures, return_when=FIRST_COMPLETED)
                for f in done:
                    domain = futures.pop(f)
                    try:
                        result = f.result()
                    except Exception as exc:
                        result = {"dominio": domain, "status": "erro", "paginas_lidas": 0,
                                  "contatos": [], "erro": f"Erro inesperado {type(exc).__name__}: {str(exc)[:160]}"}
                    salvar(conn, result)
                    stats["processados"] += 1
                    if result["status"] == "ok":
                        stats["com_contato"] += 1
                    elif result["status"] == "sem_contatos":
                        stats["sem_contato"] += 1
                    else:
                        stats["erros"] += 1
                    if stats["processados"] % 25 == 0 or stats["processados"] == len(jobs):
                        print(f"  {stats['processados']:,}/{len(jobs):,} | com contato: {stats['com_contato']:,} | "
                              f"sem contato: {stats['sem_contato']:,} | erros/bloqueios: {stats['erros']:,}", flush=True)
                    next_row = next(iterator, None)
                    if next_row is not None:
                        futures[pool.submit(enriquecer_dominio, next_row["dominio"], next_row["url"], timeout, pausa)] = next_row["dominio"]
        print("Enriquecimento concluído. Nenhuma mensagem enviada.")
        return stats
    finally:
        conn.close()


def exportar(db: Path, saida: Path, todos: bool = False) -> int:
    conn = preparar(db)
    try:
        saida.parent.mkdir(parents=True, exist_ok=True)
        sql = """SELECT c.dominio, COALESCE(e.nome,c.dominio) empresa,e.cidade,e.estado,
                w.url, c.tipo,c.valor,c.confianca,c.origem_url,c.encontrado_em,
                COALESCE(a.pontos_prioridade,0) pontos_prioridade,
                d.foco_evidencia, d.assunto, d.mensagem,
                CASE WHEN d.status='gerado' THEN 'sim' ELSE 'nao' END diagnostico_gerado,
                'sim' revisao_humana
                FROM contatos_publicos c
                JOIN websites w ON w.dominio=c.dominio
                JOIN enriquecimentos en ON en.dominio=c.dominio AND en.status='ok'
                LEFT JOIN auditorias a ON a.dominio=c.dominio
                LEFT JOIN diagnosticos d ON d.dominio=c.dominio AND d.status='gerado'
                LEFT JOIN estabelecimentos e ON e.id_fonte=(SELECT MIN(x.id_fonte) FROM estabelecimentos x WHERE x.dominio=c.dominio)
                WHERE NOT EXISTS (SELECT 1 FROM bloqueios_prospeccao b
                  WHERE (b.tipo='dominio' AND b.valor=c.dominio) OR (b.tipo='email' AND b.valor=c.valor))
                ORDER BY COALESCE(a.pontos_prioridade,0) DESC, c.dominio,
                  CASE c.tipo WHEN 'email' THEN 0 WHEN 'whatsapp' THEN 1 ELSE 2 END,
                  CASE c.confianca WHEN 'alta' THEN 0 ELSE 1 END, c.valor"""
        cols = ["dominio", "empresa", "cidade", "estado", "site", "tipo_contato", "contato",
                "confianca", "origem_url", "encontrado_em", "pontos_prioridade",
                "foco_evidencia", "assunto", "mensagem", "diagnostico_gerado", "revisao_humana"]
        written, seen = 0, set()
        with saida.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow(cols)
            # O banco pode vir somente da etapa 1: etapas 2/3 criam tabelas ao iniciar.
            for row in conn.execute(sql):
                if not todos and row["dominio"] in seen:
                    continue
                writer.writerow([celula_excel(row[k]) for k in row.keys()])
                seen.add(row["dominio"])
                written += 1
        print(f"Exportadas {written:,} linhas para {saida} (uma por domínio: {not todos}).")
        print("ATENÇÃO: contatos públicos não são consentimento nem verificação de entrega. Revise antes de usar.")
        return written
    finally:
        conn.close()


def resumo(db: Path) -> None:
    conn = preparar(db)
    try:
        print("SITES:", conn.execute("SELECT COUNT(*) FROM websites").fetchone()[0])
        for row in conn.execute("SELECT status,COUNT(*) n FROM enriquecimentos GROUP BY status ORDER BY n DESC"):
            print(f"  {row['status']}: {row['n']:,}")
        for row in conn.execute("SELECT tipo,COUNT(*) n FROM contatos_publicos GROUP BY tipo ORDER BY n DESC"):
            print(f"  contatos {row['tipo']}: {row['n']:,}")
        print("Domínios com contato:", conn.execute("SELECT COUNT(DISTINCT dominio) FROM contatos_publicos").fetchone()[0])
        print("Bloqueios ativos:", conn.execute("SELECT COUNT(*) FROM bloqueios_prospeccao").fetchone()[0])
    finally:
        conn.close()


def bloquear(db: Path, tipo: str, value: str, motivo: str):
    conn = preparar(db)
    try:
        if tipo == "dominio":
            normalizado = site_normalizado(value)
            if normalizado is None:
                raise ValueError("Domínio inválido")
            value = normalizado[0]
        else:
            value = value.strip().lower()
            if not EMAIL_RE.fullmatch(value):
                raise ValueError("E-mail inválido")
        with conn:
            conn.execute("INSERT OR REPLACE INTO bloqueios_prospeccao VALUES (?,?,?,?)",
                         (tipo, value, motivo[:240], agora()))
            if tipo == "dominio":
                conn.execute("DELETE FROM contatos_publicos WHERE dominio=?", (value,))
                conn.execute("DELETE FROM enriquecimentos WHERE dominio=?", (value,))
            else:
                conn.execute("DELETE FROM contatos_publicos WHERE tipo='email' AND valor=?", (value,))
        print(f"Bloqueio registrado: {tipo}={value}. Contatos correspondentes foram excluídos.")
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=DB_DEFAULT)
    sub = ap.add_subparsers(dest="comando", required=True)
    p = sub.add_parser("coletar", help="Encontra contatos em home e páginas internas de contato")
    p.add_argument("--limite", type=int, default=100)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--timeout", type=float, default=10)
    p.add_argument("--pausa", type=float, default=1.0)
    p.add_argument("--dias", type=int, default=30)
    p.add_argument("--uf", type=str)
    ex = sub.add_parser("exportar", help="Gera CSV para revisão, nunca envia e-mails")
    ex.add_argument("--arquivo", type=Path, default=ROOT / "saida" / "contatos_para_revisao.csv")
    ex.add_argument("--todos", action="store_true", help="Exporta todos os contatos por domínio")
    sub.add_parser("resumo")
    ban = sub.add_parser("bloquear", help="Insere domínio ou e-mail em lista de exclusão")
    g = ban.add_mutually_exclusive_group(required=True)
    g.add_argument("--dominio", type=str)
    g.add_argument("--email", type=str)
    ban.add_argument("--motivo", type=str, default="Solicitação de não contato")
    args = ap.parse_args()
    try:
        if args.comando == "coletar":
            if not (1 <= args.limite <= 100000 and 1 <= args.workers <= 8 and
                    2 <= args.timeout <= 60 and 0.5 <= args.pausa <= 60 and args.dias >= 0):
                ap.error("Limites: 1..100000, workers 1..8, timeout 2..60, pausa 0.5..60, dias >=0")
            uf = args.uf.upper() if args.uf else None
            if uf and not re.fullmatch(r"[A-Z]{2}", uf):
                ap.error("UF deve ter duas letras")
            coletar(args.db, args.limite, args.workers, args.timeout, args.pausa, args.dias, uf)
        elif args.comando == "exportar":
            exportar(args.db, args.arquivo, args.todos)
        elif args.comando == "bloquear":
            bloquear(args.db, "dominio" if args.dominio else "email", args.dominio or args.email, args.motivo)
        elif args.comando == "resumo":
            resumo(args.db)
        return 0
    except (sqlite3.Error, OSError, ValueError) as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
