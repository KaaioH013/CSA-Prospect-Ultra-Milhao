import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from http.server import ThreadingHTTPServer

import painel
import gestor


class PainelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / 'test.sqlite3'
        with gestor.preparar(self.db):
            pass

    def tearDown(self):
        self.tmp.cleanup()

    def test_metricas_vazias(self):
        with gestor.preparar(self.db) as conn:
            result = painel.metrics(conn)
        self.assertEqual(result['counts']['empresas'], 0)
        self.assertEqual(result['recent_leads'], [])

    def test_limiter_e_allowlist(self):
        manager = painel.JobManager(self.db)
        for inp in (0, 10001, '-1'):
            with self.assertRaises(ValueError):
                manager._command('auditar', {'limite': inp})
        with self.assertRaises(ValueError):
            manager._command('cmd /c calc', {})
        with self.assertRaises(ValueError):
            manager._command('diagnosticar', {'provider':'openai','limite':1,'modelo':'test','api_key':''})
        cmd, env = manager._command('diagnosticar', {'provider':'regras','limite':1})
        self.assertEqual(cmd[4], 'diagnosticar')
        self.assertIn('--min-pontos',cmd)

    def test_cli_no_key_leak(self):
        manager = painel.JobManager(self.db)
        cmd, env = manager._command('diagnosticar', {'provider':'openai','modelo':'gpt-test','limite':1,'api_key':'FAKE_SECRET_VALUE'})
        self.assertNotIn('FAKE_SECRET_VALUE', ' '.join(cmd))
        self.assertEqual(env['LLM_API_KEY'], 'FAKE_SECRET_VALUE')
        codex_cmd, codex_env = manager._command('diagnosticar', {'provider':'codex','limite':1})
        self.assertNotIn('LLM_API_KEY',codex_env)

    def test_http_local_csrf(self):
        class Server(ThreadingHTTPServer): daemon_threads=True
        srv=Server(('127.0.0.1',0), painel.PanelHandler)
        srv.db=self.db; srv.csrf='test-secret'; srv.jobs=painel.JobManager(self.db)
        thread=threading.Thread(target=srv.serve_forever,daemon=True)
        thread.start()
        url=f'http://127.0.0.1:{srv.server_port}'
        try:
            with urllib.request.urlopen(url+'/api/metrics') as r:
                self.assertEqual(json.load(r)['counts']['empresas'],0)
            with urllib.request.urlopen(url+'/') as r:
                self.assertIn('CSA Prospect',r.read().decode())
            request=urllib.request.Request(url+'/api/jobs',data=b'{}',headers={'Content-Type':'application/json'},method='POST')
            with self.assertRaises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(request)
            self.assertEqual(e.exception.code,403)
            req=urllib.request.Request(url+'/api/jobs', data=json.dumps({'action':'diagnosticar','provider':'codex','limite':3}).encode(), headers={'X-CSA-CSRF':'test-secret','Content-Type':'application/json'},method='POST')
            with self.assertRaises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(req)
            self.assertEqual(e.exception.code,400)  # requires explicit cost confirmation
        finally:
            srv.shutdown();srv.server_close();thread.join(3)


if __name__=='__main__': unittest.main()
