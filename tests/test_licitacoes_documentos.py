"""Testes offline da etapa 3: origem oficial, PDF, evidência e IA sob opt-in."""
import base64
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from licitacoes_documentos import (
    DocumentoErro, MAX_PDF, analisar_local, baixar_documentos, baixar_oficial,
    conferir_pdf, decompor_numero, executar, extrair_evidencias, listar_documentos,
    montar_relatorio, normalizar, parser_cli, url_download, url_lista, _usar_provedor,
)

PDF_BASE64 = (
    "JVBERi0xLjMKJZOMi54gUmVwb3J0TGFiIEdlbmVyYXRlZCBQREYgZG9jdW1lbnQgKG9wZW5zb3VyY2UpCjEgMCBvYmoKPDwKL0Yx"
    "IDIgMCBSCj4+CmVuZG9iagoyIDAgb2JqCjw8Ci9CYXNlRm9udCAvSGVsdmV0aWNhIC9FbmNvZGluZyAvV2luQW5zaUVuY29kaW5n"
    "IC9OYW1lIC9GMSAvU3VidHlwZSAvVHlwZTEgL1R5cGUgL0ZvbnQKPj4KZW5kb2JqCjMgMCBvYmoKPDwKL0NvbnRlbnRzIDggMCBS"
    "IC9NZWRpYUJveCBbIDAgMCA2MTIgNzkyIF0gL1BhcmVudCA3IDAgUiAvUmVzb3VyY2VzIDw8Ci9Gb250IDEgMCBSIC9Qcm9jU2V0"
    "IFsgL1BERiAvVGV4dCAvSW1hZ2VCIC9JbWFnZUMgL0ltYWdlSSBdCj4+IC9Sb3RhdGUgMCAvVHJhbnMgPDwKCj4+IAogIC9UeXBl"
    "IC9QYWdlCj4+CmVuZG9iago0IDAgb2JqCjw8Ci9Db250ZW50cyA5IDAgUiAvTWVkaWFCb3ggWyAwIDAgNjEyIDc5MiBdIC9QYXJl"
    "bnQgNyAwIFIgL1Jlc291cmNlcyA8PAovRm9udCAxIDAgUiAvUHJvY1NldCBbIC9QREYgL1RleHQgL0ltYWdlQiAvSW1hZ2VDIC9J"
    "bWFnZUkgXQo+PiAvUm90YXRlIDAgL1RyYW5zIDw8Cgo+PiAKICAvVHlwZSAvUGFnZQo+PgplbmRvYmoKNSAwIG9iago8PAovUGFn"
    "ZU1vZGUgL1VzZU5vbmUgL1BhZ2VzIDcgMCBSIC9UeXBlIC9DYXRhbG9nCj4+CmVuZG9iago2IDAgb2JqCjw8Ci9BdXRob3IgKGFu"
    "b255bW91cykgL0NyZWF0aW9uRGF0ZSAoRDoyMDI2MTExNTE3NDQyNSswMCcwMCcpIC9DcmVhdG9yIChhbm9ueW1vdXMpIC9LZXl3"
    "b3JkcyAoKSAvTW9kRGF0ZSAoRDoyMDI2MTExNTE3NDQyNSswMCcwMCcpIC9Qcm9kdWNlciAoUmVwb3J0TGFiIFBERiBMaWJyYXJ5"
    "IC0gXChvcGVuc291cmNlXCkpIAogIC9TdWJqZWN0ICh1bnNwZWNpZmllZCkgL1RpdGxlICh1bnRpdGxlZCkgL1RyYXBwZWQgL0Zh"
    "bHNlCj4+CmVuZG9iago3IDAgb2JqCjw8Ci9Db3VudCAyIC9LaWRzIFsgMyAwIFIgNCAwIFIgXSAvVHlwZSAvUGFnZXMKPj4KZW5k"
    "b2JqCjggMCBvYmoKPDwKL0ZpbHRlciBbIC9BU0NJSTg1RGVjb2RlIC9GbGF0ZURlY29kZSBdIC9MZW5ndGggMjk1Cj4+CnN0cmVh"
    "bQpHYXMzLWI+LHIvJi1eRi9eWiRqSE0lVzFLJmorUjxQLDRoO2VtXmpkLUhLMSxRZTUjXisjZFdFIWciS0I9WW4qIitBNUBkW3JL"
    "M0FgbmE2UGdkPSIzcSlqRzg7IU0yMChaMj8/TC4rL1dpTW5HblE1Kkc4V1JYbktaJnI1cz8xLS5cL1UkNStoSVdNaj1kN09iI08l"
    "WGFXOWVOImYpI1A+cyw7TzNHQ3NmPWxhVmlvMidfbENTdUhAdWRZPmknRTlALHM6XCYzU1YwYjokQ0UlJzE6a0U5PiY/LV4+NlVy"
    "bz9WR3AjQz8jInJcbVJZXzhoIzdDPzlOZWsmSCpZOGBGT1A/Wzw+WUorXlxpMjkrPydXUGhBYG86JW1OZTReJlktYUI/MX4+ZW5k"
    "c3RyZWFtCmVuZG9iago5IDAgb2JqCjw8Ci9GaWx0ZXIgWyAvQVNDSUk4NURlY29kZSAvRmxhdGVEZWNvZGUgXSAvTGVuZ3RoIDI0"
    "Ngo+PgpzdHJlYW0KR2FzMkJiRE8vJSY0UT5CYEVnZCEwTzoqRlIsKEkmNWVuMjBJXD8mN09NVFI6VlpZcjEpaVk0QTNZWVNQaWgi"
    "JTZXLipyWjlgdDtJVTc9SidwXSpvNSQkWld1VV0rJk1CVkkzTFFRKD1WMCVYO1kyK2pHKjtLJmwjKWdSUWNPVlFWSG4xVCVhIWBt"
    "YzNdXnAvYFclQDtnUmEtXl5ZdSVCNkJqbVArRmxoPyE+S0xcRjFhcVxAXDM+Qkw/OiQkWydNV2NbRW1wMmdUQ1hjMSdca3BJWUcn"
    "Mz0jIm5VP0xYJlBNUW0vN1E8cTVPRWBvbWNyO2JfSH4+ZW5kc3RyZWFtCmVuZG9iagp4cmVmCjAgMTAKMDAwMDAwMDAwMCA2NTUz"
    "NSBmIAowMDAwMDAwMDYxIDAwMDAwIG4gCjAwMDAwMDAwOTIgMDAwMDAgbiAKMDAwMDAwMDE5OSAwMDAwMCBuIAowMDAwMDAwMzky"
    "IDAwMDAwIG4gCjAwMDAwMDA1ODUgMDAwMDAgbiAKMDAwMDAwMDY1MyAwMDAwMCBuIAowMDAwMDAwOTE0IDAwMDAwIG4gCjAwMDAw"
    "MDA5NzkgMDAwMDAgbiAKMDAwMDAwMTM2NCAwMDAwMCBuIAp0cmFpbGVyCjw8Ci9JRCAKWzw5Nzg1MGI3YTdjMWFiZDQ5MjI4YzJh"
    "ZWZjZTJiZmU4Nj48OTc4NTBiN2E3YzFhYmQ0OTIyOGMyYWVmY2UyYmZlODY+XQolIFJlcG9ydExhYiBnZW5lcmF0ZWQgUERGIGRv"
    "Y3VtZW50IC0tIGRpZ2VzdCAob3BlbnNvdXJjZSkKCi9JbmZvIDYgMCBSCi9Sb290IDUgMCBSCi9TaXplIDEwCj4+CnN0YXJ0eHJl"
    "ZgoxNzAwCiUlRU9GCg=="
)

NUMERO = "12345678000100-1-000001/2026"


def pdf_bytes():
    return base64.b64decode(PDF_BASE64)


def api_fake(url: str, *, limite: int):
    if url.endswith("/arquivos"):
        return json.dumps({"documentos": [
            {"sequencialDocumento": 2, "titulo": "Anexo irrelevante", "tipoDocumentoNome": "Outros"},
            {"sequencialDocumento": 1, "titulo": "Edital de licitacao", "tipoDocumentoNome": "Edital"},
        ]}).encode()
    if url.endswith("/1"):
        return pdf_bytes()
    return b"PK\x03\x04arquivo.zip"


class DocumentosTests(unittest.TestCase):
    def test_identificador_e_url_oficial(self):
        self.assertEqual(decompor_numero(NUMERO), ("12345678000100", 2026, 1))
        self.assertEqual(url_lista(NUMERO),
            "https://pncp.gov.br/api/pncp/v1/orgaos/12345678000100/compras/2026/1/arquivos")
        self.assertTrue(url_download(NUMERO, 2).endswith("/arquivos/2"))
        with self.assertRaises(ValueError):
            decompor_numero("http://127.0.0.1/private")
        with self.assertRaises(ValueError):
            url_download(NUMERO, -1)

    def test_url_externa_bloqueada_antes_da_rede(self):
        for url in (
            "http://pncp.gov.br/api/pncp/v1/orgaos/12345678000100/compras/2026/1/arquivos",
            "https://pncp.gov.br.evil.example/api/pncp/v1/orgaos/12345678000100/compras/2026/1/arquivos",
            "https://pncp.gov.br@evil.example/api/pncp/v1/orgaos/12345678000100/compras/2026/1/arquivos",
            "https://pncp.gov.br:8443/api/pncp/v1/orgaos/12345678000100/compras/2026/1/arquivos",
            "https://pncp.gov.br/api/pncp/v1/orgaos/12345678000100/compras/2026/1/arquivos?url=127.0.0.1",
        ):
            with self.subTest(url=url), self.assertRaises(DocumentoErro):
                baixar_oficial(url, limite=100)

    def test_lista_ordenada_sem_seguir_url_externa(self):
        docs = listar_documentos(NUMERO, transporte=api_fake)
        self.assertEqual([x["sequencial"] for x in docs], [1, 2])
        self.assertTrue(all(x["url_oficial"].startswith("https://pncp.gov.br/api/pncp/") for x in docs))

    def test_lista_invalida_falha(self):
        with self.assertRaises(DocumentoErro):
            listar_documentos(NUMERO, transporte=lambda *args, **kwargs: b'{"data": []}')

    def test_pdf_real_duas_paginas_e_trechos(self):
        paginas = conferir_pdf(pdf_bytes())
        self.assertEqual(len(paginas), 2)
        self.assertIn("Atestado de capacidade tecnica", paginas[0]["texto"])
        evidencias = extrair_evidencias(paginas, documento="edital.pdf")
        cats = {t for e in evidencias for t in e["categorias"]}
        self.assertEqual(cats, {"tecnico", "atestado", "prazo", "risco"})
        self.assertTrue(any(e["pagina"] == 2 and "multa" in e["trecho"] for e in evidencias))

    def test_pdf_invalido_nao_e_scan_automatico(self):
        with self.assertRaises(DocumentoErro):
            conferir_pdf(b"PK\x03\x04arquivo.zip")
        with self.assertRaises(DocumentoErro):
            conferir_pdf(b"%PDF-1.5" + b"x" * (MAX_PDF + 1))
        from pypdf import PdfWriter
        stream = __import__('io').BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        writer.write(stream)
        paginas = conferir_pdf(stream.getvalue())
        self.assertEqual(paginas[0]["texto"], "")

    def test_baixa_apenas_pdf_legitimo_e_relata_outros(self):
        with tempfile.TemporaryDirectory() as temp:
            docs, falhas = baixar_documentos(NUMERO, limite=2, destino_raiz=Path(temp), transporte=api_fake)
            self.assertEqual(len(docs), 1)
            self.assertEqual(len(falhas), 1)
            self.assertTrue(Path(docs[0]["arquivo"]).is_file())
            self.assertEqual(docs[0]["paginas"][0]["pagina"], 1)
            self.assertIn("não é PDF", falhas[0]["erro"])

    def test_indicios_nao_sao_habilitacao(self):
        docs = [{"nome": "edital.pdf", "sha256": "a" * 64, "url": "",
                 "paginas": conferir_pdf(pdf_bytes())}]
        r = montar_relatorio(NUMERO, docs, [])
        self.assertEqual(r["status"], "triagem_preliminar_sem_conclusao_de_habilitacao")
        self.assertTrue(r["evidencias"])
        self.assertEqual(r["ia"]["analises"], [])

    def test_optin_ia_obrigatorio_sem_invocar_api(self):
        with tempfile.TemporaryDirectory() as temp:
            arq = Path(temp) / "edital.pdf"
            arq.write_bytes(pdf_bytes())
            args = parser_cli().parse_args(["analisar", "--numero", NUMERO,
                "--pdf", str(arq), "--ia", "openai", "--modelo", "modelo-teste",
                "--diretorio-relatorios", str(Path(temp) / "saida")])
            with self.assertRaisesRegex(DocumentoErro, "--autorizar-envio-ia"):
                executar(args)
            self.assertFalse((Path(temp) / "saida").exists())

    def test_ia_rejeita_id_inventado_e_categoria_trocada(self):
        evidencias = [{"id": "E001", "pagina": 1, "categorias": ["atestado"],
                       "trecho": "Atestado de capacidade tecnica compativel"}]
        resposta = {"choices": [{"message": {"content": json.dumps({"analises": [
            {"categoria": "atestado", "evidencia_id": "E999", "observacao": "Falso", "verificacao": "Falso"},
            {"categoria": "prazo", "evidencia_id": "E001", "observacao": "Falso", "verificacao": "Falso"},
            {"categoria": "atestado", "evidencia_id": "E001", "observacao": "Confirmar natureza e experiência", "verificacao": "Validar atestado"},
        ]})}}]}
        with patch.dict(os.environ, {"OPENAI_API_KEY": "CHAVE_TESTE"}):
            saida = _usar_provedor(evidencias, provedor="openai", modelo="modelo-teste",
                                  transport=lambda url, headers, payload, timeout: resposta)
        self.assertEqual(len(saida["analises"]), 1)
        self.assertEqual(saida["analises"][0]["evidencia_id"], "E001")

    def test_ia_sem_chave_bloqueia(self):
        evidencias = [{"id": "E001", "pagina": 1, "categorias": ["tecnico"], "trecho": "Software"}]
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            with self.assertRaisesRegex(DocumentoErro, "Chave ausente"):
                _usar_provedor(evidencias, provedor="openai", modelo="teste")

    def test_analise_local_gera_json_md_e_nao_toca_crm(self):
        with tempfile.TemporaryDirectory() as temp:
            arq = Path(temp) / "edital.pdf"
            arq.write_bytes(pdf_bytes())
            args = parser_cli().parse_args(["analisar", "--numero", NUMERO,
                "--pdf", str(arq), "--diretorio-relatorios", str(Path(temp) / "saida")])
            r = executar(args)
            self.assertTrue(r["evidencias"])
            target = Path(temp) / "saida" / (NUMERO.replace("/", "_") + ".json")
            self.assertTrue(target.is_file())
            texto = target.read_text(encoding="utf-8")
            self.assertIn('"pagina": 2', texto)
            self.assertFalse((Path(temp) / "dados").exists())

    def test_analise_via_api_mock_sem_rede(self):
        with tempfile.TemporaryDirectory() as temp:
            args = parser_cli().parse_args(["analisar", "--numero", NUMERO,
                "--diretorio-relatorios", str(Path(temp) / "saida"),
                "--diretorio-documentos", str(Path(temp) / "dados")])
            r = executar(args, transporte=api_fake)
            self.assertEqual(len(r["documentos"]), 1)
            self.assertEqual(len(r["falhas_documentos"]), 1)

    def test_pagina_branca_indicada_ao_usuario(self):
        from pypdf import PdfWriter
        import io, hashlib
        b = io.BytesIO()
        w = PdfWriter()
        w.add_blank_page(width=612,height=792)
        w.write(b)
        d=[{"nome":"escaneado.pdf","paginas":conferir_pdf(b.getvalue()),
            "sha256":hashlib.sha256(b.getvalue()).hexdigest(),"url":""}]
        evidencias, cobertura = analisar_local(d)
        self.assertEqual(evidencias, [])
        self.assertEqual(cobertura[0]["status"], "texto_indisponivel_possivel_scan")


if __name__ == "__main__":
    unittest.main()
