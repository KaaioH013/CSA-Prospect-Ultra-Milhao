import csv
from datetime import date
from pathlib import Path
import sqlite3
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit

from licitacoes_pncp import (
    classificar_objeto, consultar_modalidade, converter_registro,
    exportar_csv, link_edital, main, prazo_indicativo,
    salvar_registros, validar_periodo, criar_parser,
)


def registro(objeto="Desenvolvimento de software para prefeitura", numero="00000000000000-1-000001/2026"):
    return {
        "numeroControlePNCP": numero,
        "objetoCompra": objeto,
        "orgaoEntidade": {"cnpj": "00000000000000", "razaoSocial": "Órgão de teste"},
        "unidadeOrgao": {"ufSigla": "SP"},
        "anoCompra": 2026,
        "sequencialCompra": 1,
        "modalidadeNome": "Pregão - Eletrônico",
        "valorTotalEstimado": 100000.25,
        "dataPublicacaoPncp": "2026-11-15T12:00:00",
        "dataEncerramentoProposta": "2026-11-20T18:00:00",
        "situacaoCompraNome": "Divulgada no PNCP",
    }


class LicitacoesTests(unittest.TestCase):
    def test_classificacao_e_falsos_positivos_basicos(self):
        self.assertIn("software", classificar_objeto("Aquisição de SOFTWARE"))
        self.assertIn("desenvolvimento", classificar_objeto("Desenvolvimento de aplicações para o município"))
        self.assertIn("sistema_informatizado", classificar_objeto("Sistema informatizado de gestão pública"))
        self.assertEqual(classificar_objeto("Manutenção do sistema de ar condicionado"), [])
        self.assertEqual(classificar_objeto("Sistema de tratamento de água"), [])
        self.assertEqual(classificar_objeto("Aquisição de computadores e monitores"), [])

    def test_converter_preserva_dados_e_link(self):
        item = converter_registro(registro())
        self.assertEqual(item["numero_controle"], "00000000000000-1-000001/2026")
        self.assertEqual(item["uf"], "SP")
        self.assertEqual(item["valor_estimado"], "100000.25")
        self.assertEqual(item["link_pncp"], "https://pncp.gov.br/app/editais/00000000000000/2026/1")
        self.assertIsNone(converter_registro(registro("Manutenção de sistema hidráulico")))
        self.assertEqual(link_edital({"anoCompra": 2026}), "")
        self.assertIsNone(converter_registro({"objetoCompra": "software"}))

    def test_paginacao_e_parametros(self):
        chamadas = []

        def fake_transport(url, timeout):
            params = parse_qs(urlsplit(url).query)
            chamadas.append(params)
            self.assertEqual(params["codigoModalidadeContratacao"], ["6"])
            self.assertEqual(params["uf"], ["SP"])
            self.assertEqual(params["tamanhoPagina"], ["50"])
            self.assertEqual(timeout, 10)
            pagina = int(params["pagina"][0])
            return {"data": [registro(numero=f"id-{pagina}")], "totalPaginas": 2}

        itens = consultar_modalidade(date(2026, 11, 14), date(2026, 11, 15), 6,
                                    uf="SP", tamanho_pagina=50, max_paginas=2,
                                    timeout=10, pausa=0, transporte=fake_transport)
        self.assertEqual(len(itens), 2)
        self.assertEqual([x["pagina"][0] for x in chamadas], ["1", "2"])
        self.assertEqual(chamadas[0]["dataInicial"], ["20261114"])

    def test_limite_paginas_falha_sem_silenciar_perdas(self):
        with self.assertRaisesRegex(RuntimeError, "Nenhum registro"):
            consultar_modalidade(date(2026, 11, 15), date(2026, 11, 15), 6,
                                uf=None, tamanho_pagina=50, max_paginas=1,
                                timeout=10, pausa=0,
                                transporte=lambda url, timeout: {"data": [registro()], "totalPaginas": 2})

    def test_resposta_malformada_falha(self):
        with self.assertRaisesRegex(RuntimeError, "totalPaginas"):
            consultar_modalidade(date(2026, 11, 15), date(2026, 11, 15), 6,
                                uf=None, tamanho_pagina=50, max_paginas=10,
                                timeout=10, pausa=0,
                                transporte=lambda url, timeout: {"data": [registro()]})

    def test_sqlite_idempotente_e_exportacao_csv_segura(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "dados" / "licitacoes.sqlite3"
            saida = Path(d) / "saida" / "resultado.csv"
            item = converter_registro(registro())
            self.assertEqual(salvar_registros(db, [item]), (1, 0))
            self.assertEqual(salvar_registros(db, [item]), (0, 1))
            malicioso = converter_registro(registro("=SOFTWARE", numero="id-malicioso"))
            self.assertIsNotNone(malicioso)
            salvar_registros(db, [malicioso])
            with sqlite3.connect(db) as con:
                self.assertEqual(con.execute("SELECT COUNT(*) FROM licitacoes_pncp").fetchone()[0], 2)
            self.assertEqual(exportar_csv(db, saida, hoje=date(2026, 11, 15)), 2)
            with saida.open(encoding="utf-8-sig", newline="") as f:
                linhas = list(csv.DictReader(f, delimiter=";"))
            self.assertTrue(any(x["objeto"] == "'=SOFTWARE" for x in linhas))
            self.assertEqual(exportar_csv(db, saida, somente_prazo_futuro=True,
                                         hoje=date(2026, 11, 15)), 2)

    def test_prazo_e_incerteza(self):
        hoje = date(2026, 11, 15)
        self.assertEqual(prazo_indicativo("", hoje), "nao_informado")
        self.assertEqual(prazo_indicativo("2026-11-14", hoje), "data_passada")
        self.assertEqual(prazo_indicativo("2026-11-15T00:01:00", hoje), "vence_hoje_conferir_hora")
        self.assertEqual(prazo_indicativo("2026-11-16", hoje), "data_futura_conferir_situacao")

    def test_argumentos_e_validacao_sem_rede(self):
        p = criar_parser()
        args = p.parse_args(["coletar", "--inicio", "2026-11-01", "--fim", "2026-11-15", "--uf", "sp"])
        self.assertEqual(args.uf, "SP")
        self.assertEqual(validar_periodo(args), (date(2026, 11, 1), date(2026, 11, 15)))
        self.assertEqual(main(["coletar", "--dias", "0"]), 1)
        self.assertEqual(main(["coletar", "--tamanho-pagina", "100"]), 1)
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(main(["exportar", "--banco", str(Path(d) / "ausente.db")]), 1)


if __name__ == "__main__":
    unittest.main()
