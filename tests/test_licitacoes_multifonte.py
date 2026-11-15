"""Testes offline, sem chamadas de rede nem alterações no CRM."""
from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
import sqlite3
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from licitacoes_pncp import converter_registro, salvar_registros
from licitacoes_multifonte import (
    MODALIDADES_COMPRAS, _validar_args, consultar_compras, consolidar,
    converter_compras, criar_parser, excluida, executar_compras,
    exportar_consolidado, main, salvar_compras,
)


def compra(objeto="Desenvolvimento de software para secretaria", numero="12345678000100-1-000001/2026"):
    return {
        "idCompra": "idCompra-123", "numeroControlePNCP": numero,
        "orgaoEntidadeCnpj": "12345678000100",
        "orgaoEntidadeRazaoSocial": "Prefeitura Exemplo",
        "unidadeOrgaoUfSigla": "SP", "anoCompraPncp": 2026,
        "sequencialCompraPncp": 1, "modalidadeNome": "Pregão Eletrônico",
        "valorTotalEstimado": 72000.35,
        "dataPublicacaoPncp": "2026-11-15T12:00:00",
        "dataEncerramentoPropostaPncp": "2026-11-27T17:00:00",
        "situacaoCompraNomePncp": "Divulgada no PNCP",
        "objetoCompra": objeto,
        "contratacaoExcluida": False,
    }


def pncp_registro(objeto="Software PNCP", numero="12345678000100-1-000001/2026"):
    return {
        "numeroControlePNCP": numero, "objetoCompra": objeto,
        "orgaoEntidade": {"cnpj": "12345678000100", "razaoSocial": "Prefeitura Exemplo"},
        "unidadeOrgao": {"ufSigla": "SP"}, "anoCompra": 2026,
        "sequencialCompra": 1, "modalidadeNome": "Pregão - Eletrônico",
        "valorTotalEstimado": None, "dataPublicacaoPncp": "2026-11-15",
        "dataEncerramentoProposta": "", "situacaoCompraNome": "",
    }


class TestComprasGov(unittest.TestCase):
    def test_modalidades_nao_confundidas_com_pncp(self):
        self.assertEqual(MODALIDADES_COMPRAS, (3, 5, 6))

    def test_adaptador_classifica_pelo_mesmo_motor(self):
        item = converter_compras(compra())
        self.assertIsNotNone(item)
        self.assertIn("software", item["termos_encontrados"])
        self.assertEqual(item["numero_controle"], "12345678000100-1-000001/2026")
        self.assertEqual(item["data_encerramento_proposta"], "2026-11-27T17:00:00")
        self.assertEqual(item["link_pncp"], "https://pncp.gov.br/app/editais/12345678000100/2026/1")
        self.assertEqual(item["id_compra"], "idCompra-123")
        self.assertIn('"idCompra": "idCompra-123"', item["registro_original"])

    def test_descarta_objetos_nao_relacionados_e_registros_sem_id(self):
        self.assertIsNone(converter_compras(compra("Alimentos para cozinha")))
        sem_id = compra()
        sem_id.pop("numeroControlePNCP")
        self.assertIsNone(converter_compras(sem_id))
        self.assertIsNone(converter_compras({"contratacaoExcluida": True}))

    def test_exclusao_booleano_e_texto(self):
        for v in (True, 1, "true", "1", "sim"):
            self.assertTrue(excluida(v))
        for v in (False, 0, "false", "0", None):
            self.assertFalse(excluida(v))

    def test_paginacao_parametros_oficiais(self):
        chamadas = []

        def fake(url, timeout):
            params = parse_qs(urlsplit(url).query)
            chamadas.append(params)
            self.assertEqual(timeout, 11)
            self.assertEqual(params["codigoModalidade"], ["5"])
            self.assertEqual(params["dataPublicacaoPncpInicial"], ["2026-11-01"])
            self.assertEqual(params["dataPublicacaoPncpFinal"], ["2026-11-15"])
            self.assertEqual(params["unidadeOrgaoUfSigla"], ["SP"])
            self.assertEqual(params["tamanhoPagina"], ["500"])
            return {"resultado": [compra(numero=f"id-{params['pagina'][0]}")], "totalPaginas": 2}

        resultado = consultar_compras(
            date(2026, 11, 1), date(2026, 11, 15), 5, uf="SP", tamanho_pagina=500,
            max_paginas=2, timeout=11, pausa=0, transporte=fake,
        )
        self.assertEqual(len(resultado), 2)
        self.assertEqual([x["pagina"][0] for x in chamadas], ["1", "2"])

    def test_rejeita_paginacao_incompleta(self):
        with self.assertRaisesRegex(RuntimeError, "Nenhum registro"):
            consultar_compras(
                date(2026, 11, 15), date(2026, 11, 15), 5, uf=None,
                tamanho_pagina=500, max_paginas=1, timeout=10, pausa=0,
                transporte=lambda url, timeout: {"resultado": [compra()], "totalPaginas": 2},
            )

    def test_rejeita_resposta_invalida(self):
        for retorno in ({"data": [], "totalPaginas": 1},
                        {"resultado": [], "totalPaginas": "1"},
                        {"resultado": "", "totalPaginas": 1}):
            with self.subTest(retorno=retorno), self.assertRaises(RuntimeError):
                consultar_compras(
                    date(2026, 11, 15), date(2026, 11, 15), 5, uf=None,
                    tamanho_pagina=50, max_paginas=5, timeout=10, pausa=0,
                    transporte=lambda url, timeout: retorno,
                )

    def test_idempotencia_e_banco_pncp_preservado(self):
        with tempfile.TemporaryDirectory() as p:
            db = Path(p) / "dados" / "banco.sqlite3"
            item = converter_compras(compra())
            self.assertEqual(salvar_compras(db, [item]), (1, 0))
            self.assertEqual(salvar_compras(db, [item]), (0, 1))
            with sqlite3.connect(db) as con:
                self.assertEqual(con.execute("SELECT COUNT(*) FROM licitacoes_comprasgov").fetchone()[0], 1)
                self.assertEqual(con.execute("SELECT COUNT(*) FROM licitacoes_pncp").fetchone()[0], 0)

    def test_consolidacao_duas_fontes_um_id_e_precedencia(self):
        with tempfile.TemporaryDirectory() as p:
            db = Path(p) / "dados" / "banco.sqlite3"
            c = converter_compras(compra())
            salvar_compras(db, [c])
            item = converter_registro(pncp_registro())
            salvar_registros(db, [item])
            unicos = consolidar(db)
            self.assertEqual(len(unicos), 1)
            self.assertEqual(unicos[0]["fontes"], "PNCP + Compras.gov.br")
            self.assertEqual(unicos[0]["objeto"], "Software PNCP")
            self.assertEqual(unicos[0]["valor_estimado"], "72000.35")
            self.assertEqual(unicos[0]["data_encerramento_proposta"], "2026-11-27T17:00:00")
            self.assertEqual(unicos[0]["situacao"], "Divulgada no PNCP")

    def test_dois_ids_diferentes_nao_faz_dedupe_por_semelhanca(self):
        with tempfile.TemporaryDirectory() as p:
            db = Path(p) / "dados" / "banco.sqlite3"
            a = converter_compras(compra(numero="numero-1"))
            b = converter_compras(compra(numero="numero-2"))
            salvar_compras(db, [a, b])
            self.assertEqual(len(consolidar(db)), 2)

    def test_apenas_pncp_antigo_exporta(self):
        with tempfile.TemporaryDirectory() as p:
            db = Path(p) / "dados" / "banco.sqlite3"
            salvar_registros(db, [converter_registro(pncp_registro())])
            unico = consolidar(db)
            self.assertEqual(unico[0]["fontes"], "PNCP")

    def test_exportacao_csv_protege_formula_e_filtro_data(self):
        with tempfile.TemporaryDirectory() as p:
            db = Path(p) / "dados" / "banco.sqlite3"
            destino = Path(p) / "saida.csv"
            salvar_compras(db, [converter_compras(compra(objeto="=SOFTWARE"))])
            self.assertEqual(exportar_consolidado(db, destino, hoje=date(2026, 11, 15)), (1, 1))
            with destino.open("r", encoding="utf-8-sig", newline="") as f:
                rows = list(csv.DictReader(f, delimiter=";"))
            self.assertEqual(rows[0]["objeto"], "'=SOFTWARE")
            self.assertEqual(rows[0]["fontes"], "Compras.gov.br")
            self.assertEqual(exportar_consolidado(db, destino, somente_prazo_futuro=True,
                                                   hoje=date(2026, 11, 30)), (1, 0))

    def test_simulacao_nao_grava(self):
        with tempfile.TemporaryDirectory() as p:
            db = Path(p) / "dados" / "banco.sqlite3"
            args = criar_parser().parse_args([
                "coletar", "--dias", "1", "--banco", str(db), "--simular",
            ])
            with patch("licitacoes_multifonte.consultar_compras", return_value=[compra()]):
                self.assertEqual(executar_compras(args), (3, 1, 0))
            self.assertFalse(db.exists())

    def test_falha_na_segunda_modalidade_nao_modifica_banco(self):
        with tempfile.TemporaryDirectory() as p:
            db = Path(p) / "dados" / "banco.sqlite3"
            args = criar_parser().parse_args(["coletar", "--dias", "1", "--banco", str(db)])
            with patch("licitacoes_multifonte.consultar_compras",
                       side_effect=[[compra()], RuntimeError("falha na API")]):
                with self.assertRaisesRegex(RuntimeError, "falha na API"):
                    executar_compras(args)
            self.assertFalse(db.exists())

    def test_cli_validacao_sem_rede(self):
        self.assertEqual(main(["coletar", "--tamanho-pagina", "501"]), 1)
        self.assertEqual(main(["coletar", "--dias", "0"]), 1)
        self.assertEqual(main(["exportar", "--banco", "/arquivo/naoexiste.sqlite"]), 1)
        args = criar_parser().parse_args(["coletar", "--uf", "sp", "--modalidades", "5", "6"])
        _validar_args(args)
        self.assertEqual(args.uf, "SP")
        self.assertEqual(args.modalidades, [5, 6])


if __name__ == "__main__":
    unittest.main()

class TestExclusoes(unittest.TestCase):
    def test_tombstone_compras_oculta_pncp_ate_reaparecer(self):
        from licitacoes_multifonte import consolidar, salvar_compras, converter_compras
        from licitacoes_pncp import converter_registro, salvar_registros
        with tempfile.TemporaryDirectory() as p:
            db = Path(p) / 'dados' / 'licitacoes.sqlite3'
            pitem = converter_registro(pncp_registro())
            citem = converter_compras(compra())
            salvar_registros(db, [pitem])
            salvar_compras(db, [citem])
            self.assertEqual(len(consolidar(db)), 1)
            salvar_compras(db, [], excluidos=[citem['numero_controle']])
            self.assertEqual(len(consolidar(db)), 0)
            # A reativação oficial remove a marca de exclusão.
            salvar_compras(db, [citem])
            self.assertEqual(len(consolidar(db)), 1)