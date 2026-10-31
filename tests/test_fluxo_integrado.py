"""Validação integrada, offline, com uma única empresa FICTÍCIA.

Percorre coleta CSV, auditoria HTML, diagnóstico por regras, descoberta de
contato público, gestor, aprovação, envio simulado e classificação da resposta.
Não acessa a Internet, NÃO usa dados reais e NÃO envia e-mails.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from email.message import EmailMessage

import pipeline
import auditor
import diagnosticos
import contatos
import gestor
import envios


class FluxoIntegrado(unittest.TestCase):
    def test_ciclo_de_ponta_a_ponta_sem_envio_externo(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            db = base / 'teste_integrado.sqlite3'
            csv = base / 'origem.csv'
            csv.write_text('nome;site;cidade;estado\nFabrica de Teste;industria-teste.example;Araraquara;SP\n', encoding='utf-8')
            pipeline.importar_csv(csv, db)

            # Site estático fictício, sem DNS real: os fatos vêm do parser normal.
            html = (b'<html><head><title>Fabrica de Teste</title></head><body>'
                    b'<h1>Produtos industriais</h1><a href="/contato">Contato</a>'
                    b'</body></html>')
            def resposta_fake(url, timeout, pausa, dominio):
                return ({'status': 200, 'url_final': url,
                         'headers': {'content-type': 'text/html'},
                         'body': html, 'truncado': False, 'tempo_ms': 38}, 'ausente_http_404')
            with patch('auditor.buscar_pagina', side_effect=resposta_fake):
                auditor.auditar(db, 1, 1, 5, 0, 30, None)
            with pipeline.conectar(db) as c:
                self.assertEqual(c.execute("SELECT status FROM auditorias").fetchone()[0], 'ok')
                self.assertGreater(c.execute("SELECT pontos_prioridade FROM auditorias").fetchone()[0], 0)

            results = diagnosticos.gerar(db, 1, 'regras', 'sem-modelo', 30, 0, False, None)
            self.assertEqual(results['gerados'], 1)
            def pagina_fake(url, dominio, timeout, pausa, cache):
                if url.rstrip('/').endswith('/contato'):
                    return ('<a href="mailto:comercial@industria-teste.example">Fale com vendas</a>', url)
                return ('<html><a href="/contato">Contato</a></html>', url)
            with patch('contatos.buscar_html', side_effect=pagina_fake):
                stats = contatos.coletar(db, 1, 1, 5, 0, 30, None)
            self.assertEqual(stats['com_contato'], 1)

            with envios.preparar(db) as c:
                cid = gestor.campanha(c, 'Teste Integrado')
                self.assertEqual(gestor.sincronizar(c, cid, 1, 0), 1)
                lead_id = c.execute('SELECT id FROM leads_campanha').fetchone()[0]
                gestor.aprovar(c, lead_id, True)
                lote, rows = envios.preparar_lote(c, cid, 1)
                self.assertEqual(len(rows), 1)
                capturados = []
                out = envios.executar_lote(c, lote, lambda *x: capturados.append(x), dormir=lambda _: None)
                self.assertEqual(out['enviados'], 1)
                self.assertEqual(len(capturados), 1)
                envio = c.execute('SELECT * FROM envios_email WHERE lote_id=?', (lote,)).fetchone()
                msg = EmailMessage()
                msg['From'] = 'comercial@industria-teste.example'
                msg['To'] = 'csa@example.test'
                msg['Subject'] = 'Re: Proposta'
                msg['Message-ID'] = '<resposta-e2e-001@example.test>'
                msg['In-Reply-To'] = envio['message_id']
                msg.set_content('Obrigado. Podemos conversar por e-mail.')
                self.assertEqual(envios.processar_resposta(c, msg.as_bytes()), 'resposta')
                self.assertEqual(gestor.lead(c, lead_id)['status'], 'respondeu')
                self.assertEqual(envios.processar_resposta(c, msg.as_bytes()), 'repetido')
                self.assertEqual(c.execute('SELECT COUNT(*) FROM respostas_email').fetchone()[0], 1)
                print('E2E OK: CSV -> auditoria -> diagnóstico -> contato -> revisão -> lote -> envio simulado -> resposta')


if __name__ == '__main__':
    unittest.main()
