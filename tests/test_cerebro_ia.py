"""Testes sem internet, API paga ou login real."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cerebro_ia import (ProvedorIndisponivel, _json_saida, _respostas_openai,
                       executar_api, executar_codex, validar_url_api, verificar_codex)
from diagnosticos import IAIndisponivel, escolher_por_api, escolher_por_codex

VALIDO = {"foco_codigo": "sem_h1", "tom": "consultivo", "cta": "sugestao",
          "justificativa": "Conferir estrutura"}


class ProvedoresTests(unittest.TestCase):
    def test_validar_url_api(self):
        self.assertEqual(validar_url_api('https://example.net/v1/'), 'https://example.net/v1')
        self.assertEqual(validar_url_api('http://localhost:11434/v1'), 'http://localhost:11434/v1')
        for bad in ('http://example.com/v1', 'file:///etc/passwd',
                    'https://usuario:senha@example.org/v1',
                    'https://example.org/v1?api_key=segredo', 'https://example.org/v1#abc'):
            with self.subTest(bad=bad), self.assertRaises(ProvedorIndisponivel):
                validar_url_api(bad)

    def test_modelo_json_estrito(self):
        self.assertEqual(_json_saida(json.dumps(VALIDO)), VALIDO)
        self.assertEqual(_json_saida('```json\n' + json.dumps(VALIDO) + '\n```'), VALIDO)
        for s in ('oi, tente depois', '{", "a"', '[1,2]', 'Texto: ' + json.dumps(VALIDO)):
            with self.subTest(value=s), self.assertRaises(ProvedorIndisponivel):
                _json_saida(s)

    def test_openai_responses(self):
        data = {'output': [{'type': 'reasoning'}, {'type': 'message', 'content': [
            {'type': 'output_text', 'text': json.dumps(VALIDO)}]}]}
        self.assertEqual(json.loads(_respostas_openai(data)), VALIDO)

    @patch.dict(os.environ, {'LLM_API_KEY': 'chave_temporaria'}, clear=True)
    @patch('cerebro_ia._post')
    def test_openai_api_key_em_header_sem_log(self, post):
        post.return_value = {'output': [{'type': 'message', 'content': [
            {'type': 'output_text', 'text': json.dumps(VALIDO)}]}]}
        result = executar_api('openai', 'modelo-teste', 'códigos: sem_h1', 10)
        self.assertEqual(result, VALIDO)
        url, payload, headers, timeout = post.call_args.args
        self.assertEqual(url, 'https://api.openai.com/v1/responses')
        self.assertFalse(payload['store'])
        self.assertEqual(headers['Authorization'], 'Bearer chave_temporaria')
        self.assertNotIn('chave_temporaria', url)
        self.assertEqual(timeout, 10)

    @patch.dict(os.environ, {'LLM_API_KEY': 'abc'}, clear=True)
    @patch('cerebro_ia._post')
    def test_openai_compativel_api(self, post):
        post.return_value = {'choices': [{'message': {'content': json.dumps(VALIDO)}}]}
        self.assertEqual(executar_api('compativel', 'modelo', 'dados', 10,
                                     'https://api.exemplo.net/v1'), VALIDO)
        self.assertEqual(post.call_args.args[0], 'https://api.exemplo.net/v1/chat/completions')

    @patch.dict(os.environ, {}, clear=True)
    @patch('cerebro_ia._post')
    def test_local_compat_sem_chave(self, post):
        post.return_value = {'choices': [{'message': {'content': json.dumps(VALIDO)}}]}
        executar_api('compativel', 'modelo', 'dados', 10, 'http://127.0.0.1:1234/v1')
        self.assertEqual(post.call_args.args[2], {})

    @patch.dict(os.environ, {'LLM_API_KEY': 'abc'}, clear=True)
    @patch('cerebro_ia._post')
    def test_anthropic_nativo(self, post):
        post.return_value = {'content': [{'type': 'text', 'text': json.dumps(VALIDO)}]}
        self.assertEqual(executar_api('anthropic', 'claude-teste', 'dados', 10), VALIDO)
        self.assertEqual(post.call_args.args[0], 'https://api.anthropic.com/v1/messages')
        self.assertEqual(post.call_args.args[2]['x-api-key'], 'abc')

    @patch.dict(os.environ, {'LLM_API_KEY': 'abc'}, clear=True)
    @patch('cerebro_ia._post')
    def test_gemini_nativo_header(self, post):
        post.return_value = {'candidates': [{'content': {'parts': [{'text': json.dumps(VALIDO)}]}}]}
        self.assertEqual(executar_api('gemini', 'gemini-teste', 'dados', 10), VALIDO)
        self.assertTrue(post.call_args.args[0].endswith('/models/gemini-teste:generateContent'))
        self.assertEqual(post.call_args.args[2]['x-goog-api-key'], 'abc')
        self.assertNotIn('abc', post.call_args.args[0])

    def test_http_real_local_sem_api_externa(self):
        """Atravessa HTTP real para servidor de teste loopback."""
        class ServidorFake(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def do_POST(self):
                assert self.path == '/v1/chat/completions'
                content = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                assert content['model'] == 'teste-local'
                data = json.dumps({'choices': [{'message': {'content': json.dumps(VALIDO)}}]}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        server = ThreadingHTTPServer(('127.0.0.1', 0), ServidorFake)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        try:
            with patch.dict(os.environ, {}, clear=True):
                result = executar_api('compativel', 'teste-local', 'somente dados', 4,
                                     'http://127.0.0.1:' + str(server.server_port) + '/v1')
            self.assertEqual(result, VALIDO)
        finally:
            server.shutdown()
            server.server_close()
            t.join(timeout=3)

    @patch.dict(os.environ, {}, clear=True)
    def test_api_remota_precisa_chave(self):
        with self.assertRaises(ProvedorIndisponivel):
            executar_api('compativel', 'modelo', 'dados', 10, 'https://exemplo.org/v1')

    @patch('diagnosticos.executar_api')
    def test_modelo_fora_flags_rejeitado(self, mock_api):
        mock_api.return_value = dict(VALIDO, foco_codigo='inventado')
        with self.assertRaises(IAIndisponivel):
            escolher_por_api(['sem_h1'], 'openai', 'modelo', 10)

    @patch('cerebro_ia.shutil.which', return_value='codex')
    @patch('cerebro_ia.subprocess.run')
    def test_codex_login_nao_usa_api_key(self, run, which):
        run.return_value = SimpleNamespace(returncode=0, stdout='Logged in using ChatGPT', stderr='')
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'segredo', 'CODEX_API_KEY': 'segredo'}, clear=True):
            verificar_codex()
        args = run.call_args
        self.assertEqual(args.args[0], ['codex', 'login', 'status'])
        self.assertNotIn('OPENAI_API_KEY', args.kwargs['env'])
        self.assertNotIn('CODEX_API_KEY', args.kwargs['env'])

    @patch('cerebro_ia.shutil.which', return_value='codex')
    @patch('cerebro_ia.subprocess.run')
    def test_codex_rejeita_login_por_chave(self, run, which):
        run.return_value = SimpleNamespace(returncode=0, stdout='Logged in with API key', stderr='')
        with self.assertRaises(ProvedorIndisponivel):
            verificar_codex()

    @patch('cerebro_ia.verificar_codex')
    @patch('cerebro_ia.subprocess.run')
    def test_codex_exec_schema_sem_permissoes_gravacao(self, run, verify):
        def fake_run(cmd, **kwargs):
            self.assertIn('read-only', cmd)
            self.assertIn('--ephemeral', cmd)
            self.assertEqual(cmd[-1], '-')
            self.assertNotIn('--dangerously-bypass-approvals-and-sandbox', cmd)
            dest = Path(cmd[cmd.index('--output-last-message') + 1])
            dest.write_text(json.dumps(VALIDO), encoding='utf-8')
            return SimpleNamespace(returncode=0, stderr='', stdout='')
        run.side_effect = fake_run
        self.assertEqual(executar_codex('', 'dados', 20), VALIDO)

    @patch('diagnosticos.executar_codex', return_value=dict(VALIDO, foco_codigo='falso'))
    def test_codex_sem_foco_permitido(self, run):
        with self.assertRaises(IAIndisponivel):
            escolher_por_codex(['sem_h1'], '', 10)


if __name__ == '__main__':
    unittest.main()
