#!/usr/bin/env python3
"""Integra PNCP + API de Dados Abertos do Compras.gov.br para licitações de software.

Etapa 2: leitura pública (sem autenticação), deduplicação pelo número de controle
PNCP e exportação de uma visão consolidada. Não altera as tabelas do CRM.

Fonte oficial Compras.gov.br:
https://dadosabertos.compras.gov.br/swagger-ui/index.html
GET /modulo-contratacoes/1_consultarContratacoes_PNCP_14133
"""

from __future__ import annotations

import argparse
import csv
from datetime import date
import json
from pathlib import Path
import re
import sqlite3
import sys
import time
from typing import Any, Callable
from urllib.parse import urlencode

from licitacoes_pncp import (
    DB_DEFAULT,
    ROOT,
    _baixar_json,
    _seguro_para_csv,
    abrir_banco,
    converter_registro,
    prazo_indicativo,
    validar_periodo,
)

API_COMPRAS = (
    "https://dadosabertos.compras.gov.br/modulo-contratacoes/"
    "1_consultarContratacoes_PNCP_14133"
)
CSV_CONSOLIDADO = ROOT / "saida" / "licitacoes_multifonte.csv"

# ATENÇÃO: números da modalidade no Compras.gov.br não são os do PNCP.
# Compras.gov.br: 3=Concorrência, 5=Pregão, 6=Dispensa, conforme manual.
# PNCP: 4=Concorrência Eletrônica, 6=Pregão Eletrônico, 8=Dispensa.
MODALIDADES_COMPRAS = (3, 5, 6)
SCHEMA_COMPRAS = """
CREATE TABLE IF NOT EXISTS licitacoes_comprasgov (
 numero_controle TEXT PRIMARY KEY,
 id_compra TEXT NOT NULL DEFAULT '',
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
CREATE INDEX IF NOT EXISTS idx_licitacoes_comprasgov_publicacao
 ON licitacoes_comprasgov(data_publicacao);
CREATE TABLE IF NOT EXISTS licitacoes_comprasgov_exclusoes (
 numero_controle TEXT PRIMARY KEY,
 ultima_verificacao_utc TEXT NOT NULL
);
"""
COLUNAS = (
    "numero_controle", "fontes", "orgao", "uf", "modalidade", "objeto",
    "valor_estimado", "data_publicacao", "data_encerramento_proposta",
    "situacao", "prazo_indicativo", "termos_encontrados", "link_pncp",
)
CAMPOS_MERGE = (
    "objeto", "orgao", "uf", "modalidade", "valor_estimado", "data_publicacao",
    "data_encerramento_proposta", "situacao", "link_pncp", "termos_encontrados",
)


def _str(valor: object) -> str:
    return "" if valor is None else str(valor).strip()


def excluida(valor: object) -> bool:
    """Não tratar string 'false' como verdadeiro (bool('false') é True)."""
    return valor is True or valor == 1 or (
        isinstance(valor, str) and valor.strip().lower() in {"true", "1", "sim", "s"}
    )


def converter_compras(registro: dict[str, Any]) -> dict[str, Any] | None:
    """Adapta contrato aberto do Compras.gov.br ao mesmo classificador do PNCP.

    Exige numeroControlePNCP para dedupe exato, sem recorrer a aproximações
    inseguras de objeto/CNPJ/data.
    """
    if not isinstance(registro, dict) or excluida(registro.get("contratacaoExcluida")):
        return None
    pncp_id = _str(registro.get("numeroControlePNCP"))
    if not pncp_id:
        return None
    cnpj = re.sub(r"\D", "", _str(registro.get("orgaoEntidadeCnpj")))
    ano = registro.get("anoCompraPncp")
    sequencial = registro.get("sequencialCompraPncp")
    adaptado = {
        "numeroControlePNCP": pncp_id,
        "objetoCompra": registro.get("objetoCompra"),
        "orgaoEntidade": {
            "cnpj": cnpj,
            "razaoSocial": registro.get("orgaoEntidadeRazaoSocial"),
        },
        "unidadeOrgao": {
            "ufSigla": registro.get("unidadeOrgaoUfSigla"),
        },
        "anoCompra": ano,
        "sequencialCompra": sequencial,
        "modalidadeNome": registro.get("modalidadeNome"),
        "valorTotalEstimado": registro.get("valorTotalEstimado"),
        "dataPublicacaoPncp": registro.get("dataPublicacaoPncp"),
        "dataEncerramentoProposta": registro.get("dataEncerramentoPropostaPncp"),
        "situacaoCompraNome": registro.get("situacaoCompraNomePncp"),
    }
    item = converter_registro(adaptado)
    if item is None:
        return None
    item["id_compra"] = _str(registro.get("idCompra"))
    # Guardar a evidência original, nunca a estrutura adaptada.
    item["registro_original"] = json.dumps(registro, ensure_ascii=False, sort_keys=True)
    return item


def consultar_compras(
    inicio: date,
    fim: date,
    modalidade: int,
    *,
    uf: str | None,
    tamanho_pagina: int,
    max_paginas: int,
    timeout: int,
    pausa: float = 0.7,
    transporte: Callable[..., dict] | None = None,
) -> list[dict[str, Any]]:
    """Paginação completa. Se falhar uma página, NÃO salva resultado parcial."""
    transporte = transporte or _baixar_json
    pagina = 1
    itens: list[dict[str, Any]] = []
    while True:
        params = {
            "dataPublicacaoPncpInicial": inicio.isoformat(),
            "dataPublicacaoPncpFinal": fim.isoformat(),
            "codigoModalidade": modalidade,
            "pagina": pagina,
            "tamanhoPagina": tamanho_pagina,
        }
        if uf:
            params["unidadeOrgaoUfSigla"] = uf
        url = API_COMPRAS + "?" + urlencode(params)
        resposta = transporte(url, timeout=timeout)
        if not isinstance(resposta, dict):
            raise RuntimeError("Compras.gov.br retornou estrutura JSON inválida")
        lote = resposta.get("resultado")
        total = resposta.get("totalPaginas")
        if not isinstance(lote, list) or any(not isinstance(x, dict) for x in lote):
            raise RuntimeError("Resposta Compras.gov.br sem lista de registros 'resultado'")
        if type(total) is not int or total < 0:
            raise RuntimeError("Resposta Compras.gov.br com 'totalPaginas' inválido")
        if total > 0 and pagina > total:
            raise RuntimeError("Paginação Compras.gov.br inconsistente")
        itens.extend(lote)
        if pagina >= total:
            break
        if pagina >= max_paginas:
            raise RuntimeError(
                f"Compras.gov.br, modalidade {modalidade}: {total} páginas excedem "
                f"--max-paginas={max_paginas}. Divida o intervalo ou aumente o limite. "
                "Nenhum registro da execução será gravado."
            )
        pagina += 1
        if pausa:
            time.sleep(pausa)
    return itens


def abrir_multifonte(caminho: Path) -> sqlite3.Connection:
    """Abre as mesmas tabelas PNCP da etapa 1, acrescentando tabela isolada."""
    con = abrir_banco(caminho)
    con.executescript(SCHEMA_COMPRAS)
    return con


def salvar_compras(
    caminho: Path, registros: list[dict], *, excluidos: list[str] | None = None
) -> tuple[int, int]:
    """Tudo em uma única transação; não mexe em licitacoes_pncp/CRM."""
    from datetime import datetime, timezone

    agora = datetime.now(timezone.utc).isoformat(timespec="seconds")
    novos = atualizados = 0
    con = abrir_multifonte(caminho)
    try:
        with con:
            for item in registros:
                numero = item["numero_controle"]
                antigo = con.execute(
                    "SELECT 1 FROM licitacoes_comprasgov WHERE numero_controle=?", (numero,)
                ).fetchone()
                valores = (numero, item["id_compra"]) + tuple(item[k] for k in CAMPOS_MERGE) + (
                    item["registro_original"], agora, agora,
                )
                con.execute(
                    """INSERT INTO licitacoes_comprasgov (
                      numero_controle,id_compra,objeto,orgao,uf,modalidade,
                      valor_estimado,data_publicacao,data_encerramento_proposta,
                      situacao,link_pncp,termos_encontrados,registro_original,
                      primeiro_registro_utc,ultima_verificacao_utc
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(numero_controle) DO UPDATE SET
                      id_compra=excluded.id_compra,
                      objeto=excluded.objeto,orgao=excluded.orgao,uf=excluded.uf,
                      modalidade=excluded.modalidade,valor_estimado=excluded.valor_estimado,
                      data_publicacao=excluded.data_publicacao,
                      data_encerramento_proposta=excluded.data_encerramento_proposta,
                      situacao=excluded.situacao,link_pncp=excluded.link_pncp,
                      termos_encontrados=excluded.termos_encontrados,
                      registro_original=excluded.registro_original,
                      ultima_verificacao_utc=excluded.ultima_verificacao_utc""",
                    valores,
                )
                con.execute(
                    "DELETE FROM licitacoes_comprasgov_exclusoes WHERE numero_controle=?", (numero,)
                )
                if antigo:
                    atualizados += 1
                else:
                    novos += 1
            # Exclusão oficial observada: registrar lápide e omitir do consolidado,
            # inclusive quando o PNCP ainda contém a versão antiga.
            for numero in set(excluidos or []):
                if not numero:
                    continue
                con.execute(
                    "INSERT INTO licitacoes_comprasgov_exclusoes "
                    "(numero_controle,ultima_verificacao_utc) VALUES (?,?) "
                    "ON CONFLICT(numero_controle) DO UPDATE SET "
                    "ultima_verificacao_utc=excluded.ultima_verificacao_utc",
                    (numero, agora),
                )
                con.execute(
                    "DELETE FROM licitacoes_comprasgov WHERE numero_controle=?", (numero,)
                )
    finally:
        con.close()
    return novos, atualizados


def consolidar(caminho: Path) -> list[dict[str, Any]]:
    """Um resultado por numero_controle; prioriza campos PNCP preenchidos.

    Não deduplica por similaridade: duas licitações com objetos iguais podem ser
    processos distintos. Fonte múltipla só quando o identificador oficial coincide.
    """
    if not caminho.exists():
        raise FileNotFoundError(f"Banco inexistente: {caminho}")
    con = sqlite3.connect(caminho)
    con.row_factory = sqlite3.Row
    try:
        tabelas = {
            r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        registros: dict[str, dict[str, Any]] = {}
        bloqueados = set()
        if "licitacoes_comprasgov_exclusoes" in tabelas:
            bloqueados = {
                r[0] for r in con.execute(
                    "SELECT numero_controle FROM licitacoes_comprasgov_exclusoes"
                )
            }
        # PNCP é a fonte canônica dos campos quando preenchidos.
        for fonte, tabela in (
            ("Compras.gov.br", "licitacoes_comprasgov"),
            ("PNCP", "licitacoes_pncp"),
        ):
            if tabela not in tabelas:
                continue
            for row in con.execute(f"SELECT * FROM {tabela}"):
                item = dict(row)
                numero = _str(item.get("numero_controle"))
                if not numero or numero in bloqueados:
                    continue
                original = registros.get(numero)
                if original is None:
                    dados = {campo: item.get(campo) for campo in CAMPOS_MERGE}
                    dados["numero_controle"] = numero
                    dados["fontes"] = fonte
                    registros[numero] = dados
                else:
                    # PNCP consultado por último, sobrepõe somente valores presentes.
                    # Se uma fonte não informa prazo/status, aproveita a outra.
                    original["fontes"] = "PNCP + Compras.gov.br"
                    for campo in CAMPOS_MERGE:
                        valor = item.get(campo)
                        if valor is not None and _str(valor):
                            original[campo] = valor
        return sorted(
            registros.values(),
            key=lambda item: (_str(item.get("data_publicacao")), item["numero_controle"]),
            reverse=True,
        )
    finally:
        con.close()


def exportar_consolidado(
    caminho: Path,
    arquivo: Path,
    *,
    somente_prazo_futuro: bool = False,
    hoje: date | None = None,
) -> tuple[int, int]:
    itens = consolidar(caminho)
    hoje = hoje or date.today()
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    exportados = 0
    with arquivo.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUNAS, delimiter=";")
        writer.writeheader()
        for item in itens:
            prazo = prazo_indicativo(_str(item.get("data_encerramento_proposta")), hoje)
            if somente_prazo_futuro and prazo != "data_futura_conferir_situacao":
                continue
            item["prazo_indicativo"] = prazo
            writer.writerow({campo: _seguro_para_csv(item.get(campo)) for campo in COLUNAS})
            exportados += 1
    return len(itens), exportados


def executar_compras(args: argparse.Namespace) -> tuple[int, int, int]:
    inicio, fim = validar_periodo(args)
    candidatos: dict[str, dict] = {}
    consultados = 0
    sem_id = 0
    excluidos: set[str] = set()
    for modalidade in args.modalidades:
        lote = consultar_compras(
            inicio, fim, modalidade, uf=args.uf, tamanho_pagina=args.tamanho_pagina,
            max_paginas=args.max_paginas, timeout=args.timeout,
        )
        consultados += len(lote)
        for r in lote:
            if excluida(r.get("contratacaoExcluida")):
                numero_excluido = _str(r.get("numeroControlePNCP"))
                if numero_excluido:
                    excluidos.add(numero_excluido)
                    candidatos.pop(numero_excluido, None)
                continue
            if not _str(r.get("numeroControlePNCP")):
                sem_id += 1
                continue
            item = converter_compras(r)
            if item is not None and item["numero_controle"] not in excluidos:
                candidatos[item["numero_controle"]] = item
        print(f"Compras.gov.br modalidade {modalidade}: {len(lote)} registros.", file=sys.stderr)
    print(
        f"Recebidos {consultados}, candidatos de software {len(candidatos)}, "
        f"sem ID PNCP {sem_id}, excluídos com ID {len(excluidos)}.", file=sys.stderr,
    )
    if args.simular:
        print("Simulação: nada foi gravado.")
        return consultados, len(candidatos), 0
    novos, atualizados = salvar_compras(
        args.banco, list(candidatos.values()), excluidos=list(excluidos)
    )
    print(f"Compras.gov.br gravado: {novos} novos, {atualizados} atualizados.")
    return consultados, novos, atualizados


def _validar_args(args: argparse.Namespace) -> None:
    if args.dias < 1 or args.dias > 31:
        raise ValueError("--dias deve estar entre 1 e 31")
    if not 1 <= args.tamanho_pagina <= 500:
        raise ValueError("--tamanho-pagina deve estar entre 1 e 500")
    if not 1 <= args.max_paginas <= 10000:
        raise ValueError("--max-paginas deve estar entre 1 e 10000")
    if not 1 <= args.timeout <= 120:
        raise ValueError("--timeout deve estar entre 1 e 120")
    if any(m < 1 or m > 99 for m in args.modalidades):
        raise ValueError("--modalidades inválidas")
    validar_periodo(args)


def criar_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Importar contratações do Compras.gov.br e consolidar com o PNCP"
    )
    comandos = p.add_subparsers(dest="comando", required=True)
    c = comandos.add_parser("coletar", help="Consultar API Compras.gov.br e atualizar banco comum")
    c.add_argument("--dias", type=int, default=1)
    c.add_argument("--inicio", help="AAAA-MM-DD (use com --fim)")
    c.add_argument("--fim", help="AAAA-MM-DD (use com --inicio)")
    c.add_argument("--modalidades", type=int, nargs="+", default=list(MODALIDADES_COMPRAS),
                   help="Códigos Compras.gov.br, padrão 3 5 6 (NÃO são códigos PNCP)")
    c.add_argument("--uf", type=str.upper, choices=sorted(
        "AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO".split()
    ))
    c.add_argument("--tamanho-pagina", type=int, default=200)
    c.add_argument("--max-paginas", type=int, default=200)
    c.add_argument("--timeout", type=int, default=25)
    c.add_argument("--banco", type=Path, default=DB_DEFAULT)
    c.add_argument("--simular", action="store_true", help="Consulta sem gravar")
    e = comandos.add_parser("exportar", help="CSV unificado dos bancos PNCP + Compras.gov.br")
    e.add_argument("--banco", type=Path, default=DB_DEFAULT)
    e.add_argument("--arquivo", type=Path, default=CSV_CONSOLIDADO)
    e.add_argument("--somente-prazo-futuro", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = criar_parser().parse_args(argv)
    try:
        if args.comando == "coletar":
            _validar_args(args)
            executar_compras(args)
        else:
            encontrados, exportados = exportar_consolidado(
                args.banco, args.arquivo,
                somente_prazo_futuro=args.somente_prazo_futuro,
            )
            print(
                f"Consolidado: {encontrados} únicos entre as fontes; "
                f"{exportados} exportados para {args.arquivo}."
            )
        return 0
    except (RuntimeError, ValueError, OSError, sqlite3.Error) as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())