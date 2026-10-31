import csv
import json
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import unittest
from pathlib import Path
from unittest.mock import patch

from auditor import avaliar_html, salvar as salvar_auditoria
from diagnosticos import (
    FATOS, IAIndisponivel, dados_json, escolher_por_ollama, exportar, gerar,
    montar_rascunho, parse_flags, preparar, verificar_ollama,
)
from pipeline import conectar, gravar_lote


class DiagnosticoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / 'empresas.sqlite3'
        conn = conectar(self.db)
        gravar_lote(conn, [('csv:test-1', 'empresaexemplo.com.br', 'https://empresaexemplo.com.br/',
                           'Empresa Exemplo', 'Araraquara', 'SP', 'office:company', '', 'CSV')])
        conn.close()
        self.addCleanup(self.tmp.cleanup)

    def auditar_fake(self, body=b'<html lang="pt"><head><title>Site</title></head><body><h1>Ola</h1></body></html>',
                     timestamp='2026-10-31T12:00:00+00:00', status='ok'):
        row = {
            'dominio': 'empresaexemplo.com.br',
            'auditado_em': timestamp, 'status': status, 'url_final': 'https://empresaexemplo.com.br/',
            'http_status': 200, 'robots_status': 'lido', 'erro': None,
        }
        row.update(avaliar_html(body, 'text/html', 'https://empresaexemplo.com.br/', 120))
        conn = preparar(self.db)
        try:
            salvar_auditoria(conn, row)
        finally:
            conn.close()

    def test_somente_flags_conhecidos(self):
        self.assertEqual(parse_flags('["sem_h1","nao_existe","sem_h1",5]'), ['sem_h1'])
        self.assertEqual(parse_flags('not json'), [])
        self.assertEqual(parse_flags('{}'), [])

    def test_mensagem_ancorada_em_fato_e_hipotese(self):
        assunto, texto = montar_rascunho('Empresa Exemplo', 'empresaexemplo.com.br',
                                        'sem_h1', 'consultivo', 'sugestao')
        self.assertIn('Empresa Exemplo', assunto)
        self.assertIn(FATOS['sem_h1'][0], texto)
        self.assertIn('pode ter relação', texto)
        self.assertNotIn('queda de vendas', texto)
        self.assertIn('Equipe CSA', texto)

    def test_mensagem_limpa_crlf(self):
        _, texto = montar_rascunho('Empresa\nInjetada', 'empresaexemplo.com.br',
                                   'sem_h1', 'direto', 'conversa')
        self.assertIn('Empresa Injetada', texto)

    def test_nao_gera_para_site_sem_problema(self):
        self.auditar_fake(b'<html lang="pt"><head><title>ABC</title><meta name="viewport" content="width=device-width"><meta name="description" content="Descricao"></head><body><h1>Ola</h1></body></html>')
        stats = gerar(self.db, 30, 'regras', '', 10, 0, False, None)
        self.assertEqual(stats['gerados'], 0)
        conn = preparar(self.db)
        self.assertEqual(conn.execute('SELECT COUNT(*) FROM diagnosticos').fetchone()[0], 0)
        conn.close()

    def test_nao_gera_para_auditoria_com_erro(self):
        self.auditar_fake(status='erro')
        stats = gerar(self.db, 30, 'regras', '', 10, 0, False, None)
        self.assertEqual(stats['gerados'], 0)

    def test_modo_regras_salva_csv_e_cache(self):
        self.auditar_fake()
        result = gerar(self.db, 30, 'regras', '', 10, 2, False, 'SP')
        self.assertEqual(result['gerados'], 1)
        self.assertEqual(gerar(self.db, 30, 'regras', '', 10, 2, False, 'SP')['gerados'], 0)
        self.assertEqual(gerar(self.db, 30, 'regras', '', 10, 2, False, 'SP')['cache'], 1)
        destino = Path(self.tmp.name) / 'saidas' / 'diagnosticos.csv'
        self.assertEqual(exportar(self.db, destino), 1)
        with destino.open(encoding='utf-8-sig', newline='') as f:
            rows = list(csv.DictReader(f, delimiter=';'))
        self.assertEqual(rows[0]['revisao_humana_pendente'], '1')
        self.assertIn('sem_meta_description', rows[0]['evidencias_detectadas'])
        self.assertIn('Equipe CSA', rows[0]['mensagem_rascunho'])

    def test_auditoria_alterada_dispara_nova_geracao(self):
        self.auditar_fake(timestamp='2026-10-31T12:00:00+00:00')
        gerar(self.db, 1, 'regras', '', 10, 2, False, None)
        self.auditar_fake(timestamp='2026-10-31T13:00:00+00:00')
        stats = gerar(self.db, 1, 'regras', '', 10, 2, False, None)
        self.assertEqual(stats['gerados'], 1)

    def test_uf_filtrada(self):
        self.auditar_fake()
        self.assertEqual(gerar(self.db, 10, 'regras', '', 10, 2, False, 'RJ')['gerados'], 0)

    @patch('diagnosticos.dados_json')
    def test_modelo_seleciona_codigo_valido(self, mock_api):
        mock_api.return_value = {'response': json.dumps({
            'foco_codigo': 'sem_title', 'tom': 'direto', 'cta': 'diagnostico', 'justificativa': 'Relevante'})}
        r = escolher_por_ollama(['sem_title', 'sem_h1'], 'qwen3:1.7b', 30)
        self.assertEqual(r['foco_codigo'], 'sem_title')
        req = mock_api.call_args.args[1]
        self.assertEqual(req['model'], 'qwen3:1.7b')
        self.assertFalse(req['stream'])
        self.assertEqual(req['format']['properties']['foco_codigo']['enum'], ['sem_title', 'sem_h1'])
        self.assertNotIn('contato@', req['prompt'])

    @patch('diagnosticos.dados_json')
    def test_modelo_inventa_evidencia_rejeitado(self, mock_api):
        mock_api.return_value = {'response': json.dumps({
            'foco_codigo': 'problema_inventado', 'tom': 'direto', 'cta': 'diagnostico', 'justificativa': ''})}
        with self.assertRaises(IAIndisponivel):
            escolher_por_ollama(['sem_title'], 'qwen3:1.7b', 30)

    @patch('diagnosticos.dados_json')
    def test_modelo_json_invalido_rejeitado(self, mock_api):
        mock_api.return_value = {'response': 'TEXTO livre'}
        with self.assertRaises(IAIndisponivel):
            escolher_por_ollama(['sem_title'], 'qwen3:1.7b', 30)

    @patch('diagnosticos.dados_json')
    def test_verificacao_modelo_local(self, mock_api):
        mock_api.return_value = {'models': [{'name': 'qwen3:1.7b'}]}
        verificar_ollama('qwen3:1.7b')
        with self.assertRaises(IAIndisponivel):
            verificar_ollama('qwen3:4b')

    def test_endpoint_local_exclusivo(self):
        with self.assertRaises(IAIndisponivel):
            dados_json('https://api.externa.com/api/generate')

    def test_http_local_ollama_integracao_falsa_sem_internet(self):
        """Exercita conexão HTTP real com servidor local de teste, sem instalar modelo."""
        class Responder(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                if self.path != '/api/tags':
                    self.send_error(404)
                    return
                content = json.dumps({'models': [{'name': 'qwen3:1.7b'}]}).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(content)))
                self.end_headers()
                self.wfile.write(content)

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                assert payload['model'] == 'qwen3:1.7b'
                assert 'sem_meta_description' in payload['prompt']
                data = {'response': json.dumps({'foco_codigo': 'sem_meta_description',
                       'tom': 'consultivo', 'cta': 'sugestao', 'justificativa': 'Foco em busca.'})}
                content = json.dumps(data).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(content)))
                self.end_headers()
                self.wfile.write(content)

        server = ThreadingHTTPServer(('127.0.0.1', 0), Responder)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            self.auditar_fake()
            with patch('diagnosticos.BASE_LOCAL', f'http://127.0.0.1:{server.server_port}'):
                stats = gerar(self.db, 1, 'ollama', 'qwen3:1.7b', 10, 2, False, None)
            self.assertEqual(stats['gerados'], 1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    @patch('diagnosticos.verificar_ollama')
    @patch('diagnosticos.escolher_por_ollama')
    def test_integracao_ollama_mockada(self, mock_escolha, mock_verifica):
        self.auditar_fake()
        mock_escolha.return_value = {'foco_codigo': 'sem_meta_description',
                                     'tom': 'direto', 'cta': 'conversa', 'justificativa': 'Escolha'}
        stats = gerar(self.db, 10, 'ollama', 'qwen3:1.7b', 20, 2, False, None)
        self.assertEqual(stats['gerados'], 1)
        conn = preparar(self.db)
        row = conn.execute('SELECT * FROM diagnosticos').fetchone()
        self.assertEqual(row['modelo'], 'qwen3:1.7b')
        self.assertIn('meta description', row['mensagem'])
        self.assertEqual(row['revisao_humana'], 1)
        conn.close()

    @patch('diagnosticos.verificar_ollama')
    @patch('diagnosticos.escolher_por_ollama', side_effect=IAIndisponivel('timeout'))
    def test_falha_da_ia_nao_cria_rascunho_falso(self, mock_escolha, mock_verifica):
        self.auditar_fake()
        stats = gerar(self.db, 10, 'ollama', 'qwen3:1.7b', 20, 2, False, None)
        self.assertEqual(stats['erros'], 1)
        conn = preparar(self.db)
        row = conn.execute('SELECT status, mensagem FROM diagnosticos').fetchone()
        self.assertEqual(row['status'], 'erro')
        self.assertIsNone(row['mensagem'])
        conn.close()


if __name__ == '__main__':
    unittest.main()
