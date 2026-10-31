import tempfile
import unittest
import sys
import types
import xml.etree.ElementTree as ET
from unittest.mock import patch
from pathlib import Path
from pipeline import conectar, importar_csv, exportar_csv, site_normalizado, categoria, extrair_pbf, celula_excel


class PipelineTests(unittest.TestCase):
    def test_url(self):
        self.assertEqual(site_normalizado("HTTPS://WWW.Example.COM.BR/produtos")[0], "example.com.br")
        self.assertIsNone(site_normalizado("https://instagram.com/empresa"))
        self.assertIsNone(site_normalizado("http://127.0.0.1/admin"))
        self.assertEqual(site_normalizado("facebook.com/x; https://fabricante.com.br/")[0], "fabricante.com.br")

    def test_categoria(self):
        self.assertEqual(categoria({"shop": "industrial"}), "shop:industrial")
        self.assertIsNone(categoria({"amenity": "school"}))

    def test_csv_idempotente(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            entrada = root / "entrada.csv"
            db = root / "banco.sqlite3"
            saida = root / "saida.csv"
            entrada.write_text("nome;site;cidade;estado\nAcme;www.acme.com.br;Araraquara;SP\nAcme;https://acme.com.br/contato;Araraquara;SP\nOutra;instagram.com/outra;São Paulo;SP\n", encoding="utf-8")
            importar_csv(entrada, db)
            importar_csv(entrada, db)
            con = conectar(db)
            self.assertEqual(con.execute("select count(*) from websites").fetchone()[0], 1)
            self.assertEqual(con.execute("select count(*) from estabelecimentos").fetchone()[0], 1)
            con.close()
            exportar_csv(db, saida)
            texto = saida.read_text(encoding="utf-8-sig")
            self.assertIn("acme.com.br", texto)

    def test_protecao_csv(self):
        self.assertEqual(celula_excel("=cmd()"), "'=cmd()")

    def test_osm_xml_com_adaptador_simulado(self):
        # Testa o processamento via callbacks. Não substitui teste com um PBF real.
        class FakeTags:
            def __init__(self, d):
                self.d = d
            def get(self, k):
                return self.d.get(k)
            def __iter__(self):
                return (types.SimpleNamespace(k=k, v=v) for k, v in self.d.items())

        class FakeHandler:
            def apply_file(self, path, locations=False):
                root = ET.parse(path).getroot()
                for elem in root:
                    tags = {t.attrib["k"]: t.attrib["v"] for t in elem.findall("tag")}
                    obj = types.SimpleNamespace(id=elem.attrib["id"], tags=FakeTags(tags))
                    getattr(self, elem.tag)(obj)

        fake_osmium = types.SimpleNamespace(SimpleHandler=FakeHandler)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            arq = root / "fixture.osm"
            arq.write_text('''<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6" generator="tests">
 <node id="1" lat="-21" lon="-48"><tag k="name" v="Indústria A"/><tag k="office" v="company"/><tag k="website" v="https://empresa-a.com.br/"/></node>
 <node id="2" lat="-21" lon="-48"><tag k="name" v="Filial A"/><tag k="shop" v="yes"/><tag k="website" v="https://www.empresa-a.com.br/"/></node>
 <node id="3" lat="-21" lon="-48"><tag k="name" v="Escola B"/><tag k="amenity" v="school"/><tag k="website" v="https://escolab.com.br/"/></node>
</osm>''', encoding="utf-8")
            db = root / "banco.sqlite3"
            with patch.dict(sys.modules, {"osmium": fake_osmium}):
                extrair_pbf(arq, db)
            con = conectar(db)
            self.assertEqual(con.execute("select count(*) from websites").fetchone()[0], 1)
            self.assertEqual(con.execute("select count(*) from estabelecimentos").fetchone()[0], 2)
            con.close()


if __name__ == '__main__':
    unittest.main()
