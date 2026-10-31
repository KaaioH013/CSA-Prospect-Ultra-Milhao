#!/usr/bin/env python3
"""CSA Prospecção: etapa 1, coleta local de sites públicos de empresas.

Fonte OSM: © OpenStreetMap contributors, licença ODbL.
https://www.openstreetmap.org/copyright
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import ipaddress
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
DB_DEFAULT = ROOT / "dados" / "empresas.sqlite3"
REGIONS = {
    "sudeste": "https://download.geofabrik.de/south-america/brazil/sudeste-latest.osm.pbf",
    "brasil": "https://download.geofabrik.de/south-america/brazil-latest.osm.pbf",
}
EXCLUDED_DOMAINS = {
    "facebook.com", "fb.com", "instagram.com", "whatsapp.com", "wa.me",
    "linktr.ee", "linktree.com", "youtube.com", "youtu.be", "tiktok.com",
    "twitter.com", "x.com", "linkedin.com", "google.com", "goo.gl",
    "maps.app.goo.gl", "ifood.com.br", "tripadvisor.com", "booking.com",
    "mercadolivre.com.br", "shopee.com.br", "olx.com.br",
}
BUSINESS_TAGS = (
    "shop", "office", "craft", "industrial", "amenity",
    "tourism", "healthcare", "leisure",
)
BUSINESS_LANDUSE = {"industrial", "commercial", "retail"}
BUSINESS_BUILDINGS = {"industrial", "commercial", "retail", "warehouse"}
NONBUSINESS = {
    ("office", "government"), ("office", "political_party"),
    ("amenity", "school"), ("amenity", "university"),
    ("amenity", "college"), ("amenity", "kindergarten"),
    ("amenity", "police"), ("amenity", "fire_station"),
    ("amenity", "townhall"), ("amenity", "courthouse"),
    ("amenity", "library"), ("amenity", "place_of_worship"),
    ("amenity", "public_building"), ("tourism", "attraction"),
    ("tourism", "museum"), ("leisure", "park"),
    ("leisure", "nature_reserve"),
}
SCHEMA = """
CREATE TABLE IF NOT EXISTS websites (
    dominio TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    primeiro_registro_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS estabelecimentos (
    id_fonte TEXT PRIMARY KEY,
    dominio TEXT NOT NULL REFERENCES websites(dominio),
    nome TEXT NOT NULL,
    cidade TEXT,
    estado TEXT,
    categoria TEXT,
    telefone TEXT,
    fonte TEXT NOT NULL,
    FOREIGN KEY (dominio) REFERENCES websites(dominio)
);
CREATE INDEX IF NOT EXISTS idx_estabelecimentos_dominio
ON estabelecimentos(dominio);
CREATE INDEX IF NOT EXISTS idx_estabelecimentos_estado
ON estabelecimentos(estado);
"""


def agora() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def site_normalizado(valor: str) -> tuple[str, str] | None:
    """Devolve (domínio, URL-raiz) de um site externo auditável."""
    if not valor:
        return None
    for entrada in re.split(r"[;\n\r]+", valor):
        entrada = entrada.strip().strip("\"'<> ")
        if not entrada:
            continue
        if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", entrada):
            entrada = "https://" + entrada
        try:
            p = urlsplit(entrada)
            if p.scheme.lower() not in ("http", "https") or p.username or p.password:
                continue
            host = (p.hostname or "").strip(".").lower().encode("idna").decode("ascii")
            if host.startswith("www."):
                host = host[4:]
            if len(host) > 253 or "." not in host:
                continue
            try:
                ipaddress.ip_address(host)
                continue
            except ValueError:
                pass
            labels = host.split(".")
            if not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", x) for x in labels):
                continue
            if len(labels[-1]) < 2:
                continue
            if any(host == d or host.endswith("." + d) for d in EXCLUDED_DOMAINS):
                continue
            return host, f"{p.scheme.lower()}://{host}/"
        except (UnicodeError, ValueError):
            continue
    return None


def categoria(tags: dict[str, str]) -> str | None:
    if tags.get("landuse") in BUSINESS_LANDUSE:
        return "landuse:" + tags["landuse"]
    if tags.get("building") in BUSINESS_BUILDINGS:
        return "building:" + tags["building"]
    for chave in BUSINESS_TAGS:
        valor = tags.get(chave, "").strip()
        if valor and valor != "no":
            if (chave, valor) in NONBUSINESS:
                return None
            return f"{chave}:{valor}"
    return None


def conectar(caminho: Path) -> sqlite3.Connection:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(caminho)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def gravar_lote(conn: sqlite3.Connection, lote: list[tuple]) -> None:
    if not lote:
        return
    data = agora()
    with conn:
        conn.executemany(
            "INSERT OR IGNORE INTO websites (dominio, url, primeiro_registro_utc) VALUES (?,?,?)",
            [(r[1], r[2], data) for r in lote],
        )
        conn.executemany(
            """INSERT INTO estabelecimentos
            (id_fonte, dominio, nome, cidade, estado, categoria, telefone, fonte)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(id_fonte) DO UPDATE SET
              dominio=excluded.dominio, nome=excluded.nome,
              cidade=excluded.cidade, estado=excluded.estado,
              categoria=excluded.categoria, telefone=excluded.telefone,
              fonte=excluded.fonte""",
            [(r[0], r[1], r[3], r[4], r[5], r[6], r[7], r[8]) for r in lote],
        )


def extrair_pbf(arquivo: Path, db: Path) -> None:
    try:
        import osmium
    except ImportError as exc:
        raise RuntimeError("Falta a dependência osmium. Execute: python -m pip install -r requirements.txt") from exc
    if not arquivo.is_file():
        raise FileNotFoundError(f"Arquivo PBF não encontrado: {arquivo}")
    conn = conectar(db)
    inicio = time.monotonic()

    class Extrator(osmium.SimpleHandler):
        def __init__(self) -> None:
            super().__init__()
            self.lote: list[tuple] = []
            self.validos = 0

        def processar(self, obj, tipo: str) -> None:
            # Cópia dos campos: as tags libosmium só existem durante o callback.
            site = obj.tags.get("website") or obj.tags.get("contact:website")
            if not site:
                return
            nome = obj.tags.get("name") or obj.tags.get("brand")
            if not nome:
                return
            tags = {tag.k: tag.v for tag in obj.tags}
            cat = categoria(tags)
            if not cat:
                return
            normalizado = site_normalizado(site)
            if not normalizado:
                return
            dominio, url = normalizado
            cidade = tags.get("addr:city", "").strip()
            estado = (tags.get("addr:state", "") or tags.get("is_in:state_code", "")).strip()
            telefone = (tags.get("contact:phone", "") or tags.get("phone", "")).strip()
            self.lote.append((f"osm:{tipo}:{obj.id}", dominio, url, nome.strip(),
                              cidade, estado, cat, telefone, "OpenStreetMap/Geofabrik ODbL"))
            self.validos += 1
            if len(self.lote) >= 1000:
                gravar_lote(conn, self.lote)
                self.lote.clear()
                if self.validos % 10000 == 0:
                    print(f"Processados {self.validos:,} registros com website válido...", flush=True)

        def node(self, obj) -> None:
            self.processar(obj, "node")

        def way(self, obj) -> None:
            self.processar(obj, "way")

        def relation(self, obj) -> None:
            self.processar(obj, "relation")

    extrator = Extrator()
    try:
        print(f"Lendo {arquivo.name} em fluxo, sem carregar tudo na RAM...", flush=True)
        extrator.apply_file(str(arquivo), locations=False)
        gravar_lote(conn, extrator.lote)
        print(f"Objetos comerciais com site aproveitável: {extrator.validos:,}")
        mostrar_resumo(conn)
        print(f"Tempo total: {time.monotonic() - inicio:.1f} s")
    finally:
        conn.close()


def importar_csv(arquivo: Path, db: Path) -> None:
    if not arquivo.is_file():
        raise FileNotFoundError(arquivo)
    conn = conectar(db)
    validos = 0
    lote: list[tuple] = []
    try:
        with arquivo.open("r", encoding="utf-8-sig", newline="") as f:
            trecho = f.read(4096)
            f.seek(0)
            try:
                dialect = csv.Sniffer().sniff(trecho, delimiters=",;\t")
                delim = dialect.delimiter
            except csv.Error:
                delim = ";"
            linhas = csv.DictReader(f, delimiter=delim)
            campos = {c.strip().lower() for c in (linhas.fieldnames or []) if c}
            if not (campos & {"site", "website", "url", "dominio"}):
                raise ValueError("CSV precisa conter coluna 'site', 'website', 'url' ou 'dominio'.")
            for row in linhas:
                r = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
                site = r.get("site") or r.get("website") or r.get("url") or r.get("dominio") or ""
                normalized = site_normalizado(site)
                if not normalized:
                    continue
                dominio, url = normalized
                nome = r.get("nome") or r.get("empresa") or r.get("razao_social") or dominio
                cidade = r.get("cidade", "")
                estado = r.get("estado") or r.get("uf", "")
                chave = f"{dominio}|{nome}|{cidade}|{estado}"
                id_fonte = "csv:" + hashlib.sha256(chave.encode("utf-8")).hexdigest()[:24]
                lote.append((id_fonte, dominio, url, nome, cidade, estado,
                             r.get("categoria", "importado:csv"), r.get("telefone", ""),
                             f"CSV:{arquivo.name}"))
                validos += 1
                if len(lote) >= 1000:
                    gravar_lote(conn, lote)
                    lote.clear()
        gravar_lote(conn, lote)
        print(f"Linhas de empresas com site válido importadas: {validos:,}")
        mostrar_resumo(conn)
    finally:
        conn.close()


def mostrar_resumo(conn: sqlite3.Connection) -> None:
    sites = conn.execute("SELECT COUNT(*) FROM websites").fetchone()[0]
    estabelecimentos = conn.execute("SELECT COUNT(*) FROM estabelecimentos").fetchone()[0]
    print(f"Domínios únicos: {sites:,} | Estabelecimentos: {estabelecimentos:,}")
    print("Atenção: estabelecimentos não são necessariamente empresas jurídicas únicas.")


def celula_excel(valor):
    """Evita execução de fórmulas em editores de CSV/planilhas."""
    if isinstance(valor, str) and valor.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + valor
    return valor


def exportar_csv(db: Path, destino: Path) -> None:
    conn = conectar(db)
    destino.parent.mkdir(parents=True, exist_ok=True)
    try:
        consulta = """
            SELECT w.dominio, w.url, e.nome, e.cidade, e.estado, e.categoria,
                   e.telefone, e.fonte,
                   (SELECT COUNT(*) FROM estabelecimentos x WHERE x.dominio=w.dominio) locais
            FROM websites w
            JOIN estabelecimentos e ON e.id_fonte = (
                SELECT MIN(x.id_fonte) FROM estabelecimentos x WHERE x.dominio=w.dominio
            )
            ORDER BY w.dominio
        """
        with destino.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow(["dominio", "url", "nome", "cidade", "estado",
                             "categoria", "telefone", "fonte", "qtd_estabelecimentos"])
            count = 0
            for row in conn.execute(consulta):
                writer.writerow([celula_excel(v) for v in row])
                count += 1
        print(f"Exportados {count:,} domínios únicos: {destino}")
        print("Dados OSM: © OpenStreetMap contributors, ODbL, openstreetmap.org/copyright")
    finally:
        conn.close()


def baixar(regiao: str, destino: Path) -> None:
    """Baixa com retomada simples e valida MD5 publicado pela Geofabrik."""
    url = REGIONS[regiao]
    destino.parent.mkdir(parents=True, exist_ok=True)
    if destino.exists():
        print(f"Arquivo já existe. Não baixei novamente: {destino}")
        return
    parcial = destino.with_suffix(destino.suffix + ".part")
    offset = parcial.stat().st_size if parcial.exists() else 0
    cabecalhos = {"User-Agent": "CSAProspeccao/1.0 (coleta de dados publicos OSM)"}
    if offset:
        cabecalhos["Range"] = f"bytes={offset}-"
    requisicao = urllib.request.Request(url, headers=cabecalhos)
    try:
        resposta = urllib.request.urlopen(requisicao, timeout=90)
    except urllib.error.HTTPError as exc:
        if exc.code == 416 and offset:
            # Pode ser um download completo que não foi renomeado.
            print("Download parcial já está completo segundo o servidor; verificando MD5...")
            _validar_md5(url, parcial)
            parcial.replace(destino)
            return
        raise
    with resposta:
        retomando = offset and resposta.status == 206
        modo = "ab" if retomando else "wb"
        total = int(resposta.headers.get("Content-Length", "0")) + (offset if retomando else 0)
        lidos = offset if retomando else 0
        ultima = 0.0
        print(f"Baixando {regiao.upper()} para {parcial.name}; origem: Geofabrik.", flush=True)
        with parcial.open(modo) as f:
            while True:
                bloco = resposta.read(1024 * 1024)
                if not bloco:
                    break
                f.write(bloco)
                lidos += len(bloco)
                if time.monotonic() - ultima > 4:
                    pct = f" ({lidos / total:.0%})" if total else ""
                    print(f"  {lidos / 1048576:.0f} MiB{pct}", flush=True)
                    ultima = time.monotonic()
    _validar_md5(url, parcial)
    parcial.replace(destino)
    print(f"Download íntegro: {destino}")


def _validar_md5(url: str, arquivo: Path) -> None:
    with urllib.request.urlopen(
        urllib.request.Request(url + ".md5", headers={"User-Agent": "CSAProspeccao/1.0"}), timeout=30
    ) as resp:
        resposta_md5 = resp.read().decode("ascii", errors="replace")
    match = re.search(r"\b[a-fA-F0-9]{32}\b", resposta_md5)
    if not match:
        raise RuntimeError("Não consegui interpretar o checksum MD5 da fonte. Arquivo .part preservado.")
    esperado = match.group(0).lower()
    md5 = hashlib.md5()
    with arquivo.open("rb") as f:
        for bloco in iter(lambda: f.read(4 * 1024 * 1024), b""):
            md5.update(bloco)
    if md5.hexdigest() != esperado:
        raise RuntimeError("MD5 divergente: download possivelmente incompleto. Arquivo .part preservado.")
    print("Integridade do arquivo confirmada pelo MD5 da fonte.")


def main() -> int:
    cli = argparse.ArgumentParser(description="CSA Prospecção | etapa 1 | custo de serviços: R$ 0")
    cli.add_argument("--db", type=Path, default=DB_DEFAULT, help="SQLite local")
    sub = cli.add_subparsers(dest="comando", required=True)
    b = sub.add_parser("baixar", help="Baixar base OSM da Geofabrik")
    b.add_argument("--regiao", choices=REGIONS, default="sudeste")
    b.add_argument("--destino", type=Path, help="Caminho do .osm.pbf")
    e = sub.add_parser("extrair", help="Extrair sites do arquivo .osm.pbf")
    e.add_argument("--arquivo", type=Path, required=True)
    i = sub.add_parser("importar-csv", help="Importar sua própria lista de empresas")
    i.add_argument("--arquivo", type=Path, required=True)
    x = sub.add_parser("exportar", help="Gerar CSV de sites únicos")
    x.add_argument("--arquivo", type=Path, default=ROOT / "saida" / "sites_unicos.csv")
    sub.add_parser("resumo", help="Estatísticas do banco SQLite")
    args = cli.parse_args()
    try:
        if args.comando == "baixar":
            baixar(args.regiao, args.destino or ROOT / "dados" / f"{args.regiao}-latest.osm.pbf")
        elif args.comando == "extrair":
            extrair_pbf(args.arquivo, args.db)
        elif args.comando == "importar-csv":
            importar_csv(args.arquivo, args.db)
        elif args.comando == "exportar":
            exportar_csv(args.db, args.arquivo)
        elif args.comando == "resumo":
            conn = conectar(args.db)
            try:
                mostrar_resumo(conn)
            finally:
                conn.close()
    except (RuntimeError, FileNotFoundError, ValueError, sqlite3.Error, urllib.error.URLError) as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
