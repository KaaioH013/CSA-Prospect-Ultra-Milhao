#!/usr/bin/env python3
"""Coletor independente de oportunidades de software no PNCP.

API oficial: https://pncp.gov.br/api/consulta/swagger-ui/index.html
Leitura apenas, sem credenciais, sem enviar propostas ou mensagens.
"""

from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sqlite3
import sys
import time
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
DB_DEFAULT = ROOT / "dados" / "licitacoes_pncp.sqlite3"
CSV_DEFAULT = ROOT / "saida" / "licitacoes_pncp.csv"
API_URL = "https://pncp.gov.br/api/consulta/v1/contratacoes/publicacao"
MODALIDADES_PADRAO = (4, 6, 8)  # Concorrência eletrônica, pregão eletrônico, dispensa
USER_AGENT = "CSA-Licitacoes-PNCP/0.1 (consulta publica)"
UFS = frozenset("AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO".split())

# Critérios de triagem, não substituem a leitura do termo de referência.
TERMOS = {
    "software": r"\bsoftwares?\b|\bsaas\b",
    "desenvolvimento": r"\bdesenvolviment[oa]\s+(?:de\s+)?(?:sistemas?\b|aplicativ\w*\b|aplicac\w*\b|soluc\w*\s+(?:digitais|tecnologicas)\b|plataformas?\b)",
    "sistema_informatizado": r"\bsistemas?\s+(?:informatizad\w*|de\s+informac\w*|de\s+gestao\b)",
    "licenciamento": r"\blicenciamento\s+(?:de\s+|do\s+|para\s+)?(?:uso\s+(?:de\s+)?)?(?:software\b|sistema\s+informatizad\w*\b)",
    "plataforma_digital": r"\bplataformas?\s+(?:digital\b|digitais|web\b|eletronica\b|eletronicas)\b",
    "portal_web": r"\b(?:portal|portais)\s+(?:web\b|digital\b|digitais|eletronico\b|eletronicos|institucional\b|institucionais)\b",
    "ti": r"\btecnologia\s+da\s+informacao\b|\bsoluc\w*\s+(?:de|em)\s+ti\b|\bservic\w*\s+de\s+ti\b",
    "sustentacao": r"\bsustentac\w*\s+(?:de\s+)?sistemas?\b",
    "implantacao": r"\bimplantac\w*\s+(?:de\s+|do\s+)?(?:sistema\s+informatizad\w*|software\b|plataforma\s+digital\b)",
    "inteligencia_artificial": r"\binteligencia\s+artificial\b|\baprendizado\s+de\s+maquina\b",
    "fabricacao_software": r"\bfabrica\s+de\s+software\b",
    "infraestrutura_digital": r"\bhospedagem\s+(?:de\s+)?(?:sites?\b|sistemas?\b|aplicac\w*\b)|\bcomputac\w*\s+em\s+nuvem\b",
}
PADROES = {chave: re.compile(padrao) for chave, padrao in TERMOS.items()}

SCHEMA = """
CREATE TABLE IF NOT EXISTS licitacoes_pncp (
  numero_controle TEXT PRIMARY KEY,
  objeto TEXT NOT NULL,
  orgao TEXT NOT NULL DEFAULT '',
  uf TEXT NOT NULL DEFAULT '',
  modalidade TEXT NOT NULL DEFAULT '',
  valor_estimado TEXT,
  data_publicacao TEXT NOT NULL DEFAULT '',
  data_encerramento_proposta TEXT NOT NULL DEFAULT '',
  situacao TEXT NOT NULL DEFAULT '',
  link_pncp TEXT NOT NULL DEFAULT '',
  termos_encontrados TEXT NOT NULL DEFAULT '',
  registro_original TEXT NOT NULL,
  primeiro_registro_utc TEXT NOT NULL,
  ultima_verificacao_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_licitacoes_publicacao ON licitacoes_pncp(data_publicacao);
CREATE INDEX IF NOT EXISTS idx_licitacoes_uf ON licitacoes_pncp(uf);
"""
COLUNAS_CSV = (
    "numero_controle", "orgao", "uf", "modalidade", "objeto", "valor_estimado",
    "data_publicacao", "data_encerramento_proposta", "situacao", "prazo_indicativo",
    "termos_encontrados", "link_pncp",
)


def normalizar(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in texto if not unicodedata.combining(c))


def classificar_objeto(objeto: str) -> list[str]:
    texto = normalizar(objeto)
    return [chave for chave, padrao in PADROES.items() if padrao.search(texto)]


def _texto(valor: object) -> str:
    return str(valor).strip() if valor is not None else ""


def link_edital(registro: dict) -> str:
    orgao = registro.get("orgaoEntidade") or {}
    cnpj = re.sub(r"\W", "", _texto(orgao.get("cnpj")))
    ano = _texto(registro.get("anoCompra"))
    sequencial = _texto(registro.get("sequencialCompra"))
    if not (cnpj and ano.isdigit() and sequencial.isdigit()):
        return ""
    return f"https://pncp.gov.br/app/editais/{cnpj}/{int(ano)}/{int(sequencial)}"


def converter_registro(registro: dict) -> dict | None:
    if not isinstance(registro, dict):
        return None
    numero = _texto(registro.get("numeroControlePNCP"))
    objeto = _texto(registro.get("objetoCompra"))
    if not numero or not objeto:
        return None
    termos = classificar_objeto(objeto)
    if not termos:
        return None
    orgao = registro.get("orgaoEntidade") or {}
    unidade = registro.get("unidadeOrgao") or {}
    if not isinstance(orgao, dict):
        orgao = {}
    if not isinstance(unidade, dict):
        unidade = {}
    valor = registro.get("valorTotalEstimado")
    return {
        "numero_controle": numero,
        "objeto": objeto,
        "orgao": _texto(orgao.get("razaoSocial")),
        "uf": _texto(unidade.get("ufSigla") or unidade.get("ufNome")),
        "modalidade": _texto(registro.get("modalidadeNome")),
        "valor_estimado": None if valor is None else _texto(valor),
        "data_publicacao": _texto(registro.get("dataPublicacaoPncp")),
        "data_encerramento_proposta": _texto(registro.get("dataEncerramentoProposta")),
        "situacao": _texto(registro.get("situacaoCompraNome")),
        "link_pncp": link_edital(registro),
        "termos_encontrados": ",".join(termos),
        "registro_original": json.dumps(registro, ensure_ascii=False, sort_keys=True),
    }


def prazo_indicativo(data_encerramento: str, hoje: date) -> str:
    """Não equivale à confirmação de disponibilidade no portal."""
    if not data_encerramento:
        return "nao_informado"
    try:
        dia = date.fromisoformat(data_encerramento[:10])
    except ValueError:
        return "invalido"
    if dia < hoje:
        return "data_passada"
    if dia == hoje:
        return "vence_hoje_conferir_hora"
    return "data_futura_conferir_situacao"


def _baixar_json(url: str, *, timeout: int, tentativas: int = 3) -> dict:
    for tentativa in range(1, tentativas + 1):
        req = Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
        try:
            with urlopen(req, timeout=timeout) as resposta:
                if resposta.status == 204:
                    return {"data": [], "totalPaginas": 0}
                if resposta.status != 200:
                    raise RuntimeError(f"PNCP respondeu HTTP {resposta.status}")
                corpo = resposta.read(16 * 1024 * 1024 + 1)
                if len(corpo) > 16 * 1024 * 1024:
                    raise RuntimeError("Resposta PNCP maior que 16 MiB")
                decodificado = json.loads(corpo.decode("utf-8-sig"))
                if not isinstance(decodificado, dict):
                    raise RuntimeError("Formato JSON inesperado: objeto principal ausente")
                return decodificado
        except HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or tentativa == tentativas:
                raise RuntimeError(f"PNCP HTTP {exc.code}, consulta interrompida: {url}") from exc
            retry_after = exc.headers.get("Retry-After", "")
            espera = min(15, float(retry_after)) if retry_after.isdigit() else 2 ** (tentativa - 1)
            time.sleep(espera)
        except (URLError, TimeoutError) as exc:
            if tentativa == tentativas:
                raise RuntimeError(f"Falha de rede consultando PNCP: {exc}") from exc
            time.sleep(2 ** (tentativa - 1))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise RuntimeError("O PNCP não devolveu JSON válido") from exc
    raise RuntimeError("Falha inesperada de consulta")


def consultar_modalidade(
    inicio: date, fim: date, modalidade: int, *, uf: str | None,
    tamanho_pagina: int, max_paginas: int, timeout: int, pausa: float = 0.2,
    transporte=None,
) -> list[dict]:
    """Obtém todas as páginas ou falha explicitamente, sem resultado parcial silencioso."""
    transporte = transporte or _baixar_json
    dados: list[dict] = []
    pagina = 1
    while True:
        params = {
            "dataInicial": inicio.strftime("%Y%m%d"),
            "dataFinal": fim.strftime("%Y%m%d"),
            "codigoModalidadeContratacao": modalidade,
            "pagina": pagina,
            "tamanhoPagina": tamanho_pagina,
        }
        if uf:
            params["uf"] = uf
        url = f"{API_URL}?{urlencode(params)}"
        resposta = transporte(url, timeout=timeout)
        lote = resposta.get("data")
        if not isinstance(lote, list):
            raise RuntimeError("Resposta PNCP sem lista 'data'; coleta cancelada")
        total = resposta.get("totalPaginas")
        if isinstance(total, bool) or not isinstance(total, int) or total < 0:
            raise RuntimeError("Resposta PNCP sem totalPaginas válido; coleta cancelada")
        if any(not isinstance(x, dict) for x in lote):
            raise RuntimeError("Resposta PNCP contém registro inesperado")
        dados.extend(lote)
        if pagina >= total:
            break
        if pagina >= max_paginas:
            raise RuntimeError(
                f"Modalidade {modalidade}: existem {total} páginas, limite {max_paginas}. "
                "Reduza o intervalo, aumente --max-paginas ou divida as consultas. "
                "Nenhum registro desta execução foi gravado."
            )
        pagina += 1
        if pausa:
            time.sleep(pausa)
    return dados


def abrir_banco(caminho: Path) -> sqlite3.Connection:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(caminho)
    con.executescript(SCHEMA)
    return con


def salvar_registros(caminho: Path, registros: list[dict]) -> tuple[int, int]:
    """Operação transacional; dados comerciais já existentes não são tocados."""
    con = abrir_banco(caminho)
    agora = datetime.now(timezone.utc).isoformat(timespec="seconds")
    novos = 0
    atualizados = 0
    try:
        with con:
            for item in registros:
                existe = con.execute(
                    "SELECT 1 FROM licitacoes_pncp WHERE numero_controle=?", (item["numero_controle"],)
                ).fetchone() is not None
                con.execute(
                    """INSERT INTO licitacoes_pncp (
                         numero_controle, objeto, orgao, uf, modalidade, valor_estimado,
                         data_publicacao, data_encerramento_proposta, situacao,
                         link_pncp, termos_encontrados, registro_original,
                         primeiro_registro_utc, ultima_verificacao_utc
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(numero_controle) DO UPDATE SET
                         objeto=excluded.objeto, orgao=excluded.orgao, uf=excluded.uf,
                         modalidade=excluded.modalidade, valor_estimado=excluded.valor_estimado,
                         data_publicacao=excluded.data_publicacao,
                         data_encerramento_proposta=excluded.data_encerramento_proposta,
                         situacao=excluded.situacao, link_pncp=excluded.link_pncp,
                         termos_encontrados=excluded.termos_encontrados,
                         registro_original=excluded.registro_original,
                         ultima_verificacao_utc=excluded.ultima_verificacao_utc""",
                    tuple(item[c] for c in (
                        "numero_controle", "objeto", "orgao", "uf", "modalidade",
                        "valor_estimado", "data_publicacao", "data_encerramento_proposta",
                        "situacao", "link_pncp", "termos_encontrados", "registro_original",
                    )) + (agora, agora),
                )
                if existe:
                    atualizados += 1
                else:
                    novos += 1
    finally:
        con.close()
    return novos, atualizados


def _seguro_para_csv(valor: object) -> str:
    """Evita CSV formula injection ao abrir em planilhas."""
    texto = _texto(valor)
    if texto.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + texto
    return texto


def exportar_csv(caminho_db: Path, destino: Path, *, somente_prazo_futuro: bool = False,
                hoje: date | None = None) -> int:
    if not caminho_db.exists():
        raise FileNotFoundError(f"Banco inexistente: {caminho_db}. Execute 'coletar' primeiro.")
    hoje = hoje or date.today()
    destino.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(caminho_db)
    count = 0
    try:
        rows = con.execute("SELECT * FROM licitacoes_pncp ORDER BY data_publicacao DESC, numero_controle")
        campos = [desc[0] for desc in rows.description]
        with destino.open("w", encoding="utf-8-sig", newline="") as arquivo:
            writer = csv.DictWriter(arquivo, fieldnames=COLUNAS_CSV, delimiter=";")
            writer.writeheader()
            for row in rows:
                item = dict(zip(campos, row))
                prazo = prazo_indicativo(item["data_encerramento_proposta"], hoje)
                if somente_prazo_futuro and prazo != "data_futura_conferir_situacao":
                    continue
                item["prazo_indicativo"] = prazo
                writer.writerow({campo: _seguro_para_csv(item.get(campo, "")) for campo in COLUNAS_CSV})
                count += 1
    finally:
        con.close()
    return count


def validar_periodo(args: argparse.Namespace) -> tuple[date, date]:
    if bool(args.inicio) != bool(args.fim):
        raise ValueError("Use --inicio e --fim juntos (AAAA-MM-DD)")
    if args.inicio:
        inicio = date.fromisoformat(args.inicio)
        fim = date.fromisoformat(args.fim)
    else:
        fim = date.today()
        inicio = fim - timedelta(days=args.dias - 1)
    if inicio > fim:
        raise ValueError("A data inicial precisa ser anterior ou igual à final")
    if (fim - inicio).days > 30:
        raise ValueError("Intervalo máximo de 31 dias. Divida por períodos menores.")
    if fim > date.today():
        raise ValueError("A data final não pode estar no futuro para consulta por publicação")
    return inicio, fim


def executar_coleta(args: argparse.Namespace) -> tuple[int, int, int]:
    inicio, fim = validar_periodo(args)
    encontrados: dict[str, dict] = {}
    consultados = 0
    for modalidade in args.modalidades:
        registros = consultar_modalidade(
            inicio, fim, modalidade, uf=args.uf, tamanho_pagina=args.tamanho_pagina,
            max_paginas=args.max_paginas, timeout=args.timeout,
        )
        consultados += len(registros)
        for registro in registros:
            item = converter_registro(registro)
            if item:
                encontrados[item["numero_controle"]] = item
        print(f"Modalidade {modalidade}: {len(registros)} contratações consultadas.", file=sys.stderr)
    lista = list(encontrados.values())
    if args.simular:
        print(f"Simulação: {consultados} registros, {len(lista)} candidatos de software. Nada gravado.")
        return consultados, len(lista), 0
    novos, atualizados = salvar_registros(args.banco, lista)
    print(f"Concluído: {consultados} registros, {len(lista)} candidatos, {novos} novos, {atualizados} atualizados.")
    return consultados, novos, atualizados


def criar_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Coletar licitações de software da API pública do PNCP")
    subs = p.add_subparsers(dest="comando", required=True)
    c = subs.add_parser("coletar", help="Consultar API e atualizar SQLite separado da prospecção")
    c.add_argument("--dias", type=int, default=1, help="Dias corridos até hoje (padrão: 1)")
    c.add_argument("--inicio", help="Início AAAA-MM-DD, junto com --fim")
    c.add_argument("--fim", help="Fim AAAA-MM-DD, junto com --inicio")
    c.add_argument("--modalidades", type=int, nargs="+", default=list(MODALIDADES_PADRAO),
                   help="Códigos PNCP, padrão: 4 6 8")
    c.add_argument("--uf", type=str.upper, choices=sorted(UFS), help="UF; padrão: Brasil inteiro")
    c.add_argument("--tamanho-pagina", type=int, default=50, help="PNCP permite de 10 a 50")
    c.add_argument("--max-paginas", type=int, default=200, help="Limite de segurança por modalidade")
    c.add_argument("--timeout", type=int, default=25)
    c.add_argument("--banco", type=Path, default=DB_DEFAULT)
    c.add_argument("--simular", action="store_true", help="Consulta API, sem alterar banco")
    e = subs.add_parser("exportar", help="Exportar candidatos do banco para CSV")
    e.add_argument("--banco", type=Path, default=DB_DEFAULT)
    e.add_argument("--arquivo", type=Path, default=CSV_DEFAULT)
    e.add_argument("--somente-prazo-futuro", action="store_true",
                   help="Filtra por data posterior a hoje; situação oficial deve ser conferida")
    return p


def main(argv: list[str] | None = None) -> int:
    p = criar_parser()
    args = p.parse_args(argv)
    try:
        if args.comando == "coletar":
            if args.dias < 1 or args.dias > 31:
                raise ValueError("--dias deve estar entre 1 e 31")
            if not 10 <= args.tamanho_pagina <= 50:
                raise ValueError("--tamanho-pagina deve estar entre 10 e 50")
            if args.max_paginas < 1 or args.max_paginas > 10000:
                raise ValueError("--max-paginas deve estar entre 1 e 10000")
            if args.timeout < 1 or args.timeout > 120:
                raise ValueError("--timeout deve estar entre 1 e 120")
            if any(m < 1 or m > 30 for m in args.modalidades):
                raise ValueError("--modalidades inválidas: informe códigos inteiros entre 1 e 30")
            executar_coleta(args)
        else:
            qtd = exportar_csv(args.banco, args.arquivo, somente_prazo_futuro=args.somente_prazo_futuro)
            print(f"Exportados {qtd} candidatos para {args.arquivo}")
        return 0
    except (RuntimeError, OSError, ValueError, sqlite3.Error) as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
