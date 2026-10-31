import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from auditor import (
    BloqueioRede, RedirecionamentoExterno, avaliar_html, auditar,
    buscar_pagina, buscar_robots, executar_auditoria, exportar,
    garantir_tabela, redirecionar, salvar, selecionar_pendentes, url_segura,
)
from pipeline import conectar, importar_csv


class AuditorTests(unittest.TestCase):
    def test_seguranca_urls(self):
        self.assertEqual(url_segura('https://www.exemplo.com.br/abc', 'exemplo.com.br')[1], 'www.exemplo.com.br')
        for url in ('http://127.0.0.1/', 'http://localhost/', 'http://admin:pass@site.com/',
                    'http://site.com:8080/', 'file:///etc/passwd'):
            with self.subTest(url=url):
                with self.assertRaises(BloqueioRede):
                    url_segura(url)
        with self.assertRaises(RedirecionamentoExterno):
            redirecionar('https://exemplo.com/', 'https://outraempresa.com/', 'exemplo.com')

    def test_seguranca_dns_sem_rede_privada(self):
        from auditor import ips_publicos
        import socket
        fake = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))]
        with patch('auditor.socket.getaddrinfo', return_value=fake):
            with self.assertRaises(BloqueioRede):
                ips_publicos('site.com', 443)

    def test_html_analise_factual(self):
        html = b'''<html lang="pt-BR"><head><title>Exemplo | Industria</title>
          <meta name="description" content="Fabricamos componentes industriais">
          <meta name="viewport" content="width=device-width,initial-scale=1">
          <link rel="canonical" href="https://empresa.com.br/">
          </head><body><h1>Fabricacao industrial</h1>
          <img src="logo.png" alt=""><img src="peca.png">
          <a href="mailto:comercial@empresa.com.br">Orcamentos</a>
          <a href="mailto:joao.silva@empresa.com.br">Joao</a>
          <a href="tel:+55 16 3333-4444">Ligar</a>
          <a href="/contato">Contato</a></body></html>'''
        resultado = avaliar_html(html, 'text/html; charset=utf-8', 'https://empresa.com.br/', 150)
        self.assertEqual(resultado['titulo'], 'Exemplo | Industria')
        self.assertEqual(resultado['h1_count'], 1)
        self.assertEqual(resultado['imagens_sem_alt'], 1)
        self.assertEqual(json.loads(resultado['problemas_json']), ['imagens_sem_alt'])
        self.assertEqual(json.loads(resultado['emails_genericos_json']), ['comercial@empresa.com.br'])
        self.assertEqual(resultado['formulario_contato'], 1)

    def test_html_noindex_e_nao_https(self):
        r = avaliar_html(b'<html><head><meta name="robots" content="noindex"></head></html>',
                          'text/html', 'http://empresa.com/', 800)
        self.assertIn('noindex_na_home', json.loads(r['problemas_json']))
        self.assertIn('sem_https', json.loads(r['problemas_json']))
        self.assertGreaterEqual(r['pontos_prioridade'], 8)

    def test_robots_proibe_auditoria(self):
        with patch('auditor.requisitar', return_value={
            'status': 200, 'headers': {}, 'body': b'User-agent: *\nDisallow: /\n',
            'truncado': False,
        }):
            with self.assertRaisesRegex(Exception, 'proíbe'):
                buscar_pagina('https://empresa.com/', 2.0, 0, 'empresa.com')

    def test_robots_503_bloqueia_sem_buscar_html(self):
        with patch('auditor.requisitar', return_value={
            'status': 503, 'headers': {}, 'body': b'', 'truncado': False,
        }) as req:
            with self.assertRaisesRegex(Exception, 'Robots.txt'):
                buscar_pagina('https://empresa.com/', 2.0, 0, 'empresa.com')
            self.assertEqual(req.call_count, 1)

    def test_robots_404_libera(self):
        def fake_req(url, *_):
            if url.endswith('/robots.txt'):
                return {'status': 404, 'headers': {}, 'body': b'', 'truncado': False}
            return {'status': 200, 'headers': {'content-type': 'text/html'},
                    'body': b'<h1>Empresa</h1>', 'truncado': False, 'tempo_ms': 20}
        with patch('auditor.requisitar', side_effect=fake_req):
            resp, robots = buscar_pagina('https://empresa.com/', 2.0, 0, 'empresa.com')
            self.assertEqual(resp['status'], 200)
            self.assertEqual(robots, 'ausente_http_404')

    def test_https_indisponivel_tenta_http_com_robots_novo(self):
        calls = []
        def fake_page(url, timeout, pausa, dominio):
            calls.append(url)
            if url.startswith('https:'):
                raise ExceptionNoRobots('Robots.txt não permite auditoria: inacessivel:SSL error')
            return ({'url_final': url, 'status': 200, 'headers': {'content-type': 'text/html'},
                     'body': b'<html><title>Empresa</title></html>',
                     'truncado': False, 'tempo_ms': 30}, 'ausente_http_404')
        with patch('auditor.buscar_pagina', side_effect=fake_page):
            row = executar_auditoria('empresa.com', 'https://empresa.com/', 1, 0)
        self.assertEqual(len(calls), 2)
        self.assertEqual(row['status'], 'ok')
        self.assertIn('sem_https', json.loads(row['problemas_json']))

    def test_http_local_interno_sem_expor_rede_privada_em_producao(self):
        # Exercita HTTP real em servidor local SOMENTE ao substituir connect no teste.
        # Em produção, o bloqueio DNS de IP privado permanece obrigatório.
        import http.server
        import socket
        import threading
        from auditor import ConexaoHTTP

        class Fixture(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == '/robots.txt':
                    data = b'User-agent: *\nAllow: /\n'
                else:
                    data = b'<html lang="pt-BR"><head><title>Empresa de teste</title></head><body><h1>Oi</h1></body></html>'
                self.send_response(200)
                self.send_header('Content-Type', 'text/html' if self.path != '/robots.txt' else 'text/plain')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            def log_message(self, *_args):
                pass

        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Fixture)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            def connect_test(self):
                self.sock = socket.create_connection(('127.0.0.1', server.server_port), self.timeout)
            with patch('auditor.ips_publicos', return_value=['127.0.0.1']), \
                 patch.object(ConexaoHTTP, 'connect', connect_test):
                result, status = buscar_pagina('http://testeempresa.com.br/', 2, 0, 'testeempresa.com.br')
            self.assertEqual(result['status'], 200)
            self.assertEqual(status, 'lido')
            parsed = avaliar_html(result['body'], result['headers']['content-type'], result['url_final'], result['tempo_ms'])
            self.assertEqual(parsed['titulo'], 'Empresa de teste')
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)

    def test_banco_checkpoint_reauditoria_e_csv(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            entrada = root / 'entrada.csv'
            db = root / 'dados.sqlite3'
            destino = root / 'auditorias.csv'
            entrada.write_text('nome;site;estado\nEmpresa A;empresa-a.com.br;SP\nEmpresa B;empresa-b.com.br;MG\n', encoding='utf-8')
            importar_csv(entrada, db)
            conn = conectar(db)
            garantir_tabela(conn)
            pendentes = selecionar_pendentes(conn, 100, 30, 'SP')
            self.assertEqual([x[0] for x in pendentes], ['empresa-a.com.br'])
            conn.close()
            def simular(site, url, timeout, pausa):
                return {
                    'dominio': site, 'auditado_em': '2026-10-31T00:00:00+00:00',
                    'status': 'ok', 'url_final': url, 'http_status': 200, 'titulo': 'Indústria',
                    'problemas_json': '["sem_meta_description"]', 'pontos_prioridade': 2,
                    'emails_genericos_json': '[]', 'telefone_publico_json': '[]',
                }
            with patch('auditor.executar_auditoria', side_effect=simular):
                auditar(db, 2, 2, 5, 0, 30, None)
            con = conectar(db)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM auditorias').fetchone()[0], 2)
            self.assertEqual(len(selecionar_pendentes(con, 10, 30)), 0)
            con.close()
            exportar(db, destino)
            with destino.open(encoding='utf-8-sig', newline='') as f:
                linhas = list(csv.DictReader(f, delimiter=';'))
            self.assertEqual(len(linhas), 2)
            self.assertIn('problemas_detectados', linhas[0])


# Mantém a subclasse exata usada na captura pela auditoria.
from auditor import AuditoriaErro
class ExceptionNoRobots(AuditoriaErro):
    pass


if __name__ == '__main__':
    unittest.main()
