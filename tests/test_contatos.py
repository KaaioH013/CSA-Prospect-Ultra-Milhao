"""Testes offline. Nenhuma requisição a sites públicos."""
import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.robotparser import RobotFileParser

import contatos
from auditor import AuditoriaErro, BloqueioRede
from pipeline import gravar_lote


class ContatoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / 'empresas.sqlite3'
        self.conn = contatos.preparar(self.db)
        self.addCleanup(self.conn.close)
        gravar_lote(self.conn, [
            ('csv:1', 'empresa.com.br', 'https://empresa.com.br/', 'Empresa Alfa', 'Campinas', 'SP', 'office:company', '', 'CSV'),
            ('csv:2', 'outra.com.br', 'https://outra.com.br/', 'Empresa Beta', 'Santos', 'SP', 'office:company', '', 'CSV'),
        ])

    def test_email_corporativo_publico_e_falsos(self):
        self.assertEqual(contatos.limpar_email('COMERCIAL@EMPRESA.COM.BR'), 'comercial@empresa.com.br')
        for bad in ('joao.silva@empresa.com.br', 'noreply@empresa.com.br',
                    'contato@example.com', 'foo@localhost', 'contato@@empresa.com.br',
                    'contato@empresa..com.br'):
            self.assertIsNone(contatos.limpar_email(bad), bad)

    def test_telefones_brasileiros_e_whatsapp(self):
        self.assertEqual(contatos.normalizar_telefone('(16) 99999-1234'), '+5516999991234')
        self.assertEqual(contatos.normalizar_telefone('16 3333-4444'), '+551633334444')
        self.assertEqual(contatos.whatsapp_link('https://api.whatsapp.com/send?phone=5516999991234&text=olá'),
                         'https://wa.me/5516999991234')
        self.assertIsNone(contatos.normalizar_telefone('3333-4444'))
        self.assertIsNone(contatos.whatsapp_link('http://wa.me/5516999991234'))

    def test_html_extrai_apenas_canais_comerciais(self):
        html = '''<html><body><script>var a='comercial@injetado.com';</script>
        <p>Envie para contato@empresa.com.br ou João: joao@empresa.com.br.</p>
        <a href="mailto:vendas@empresa.com.br?subject=Oi">Vendas</a>
        <a href="tel:+55 16 3333-4444">Ligue</a>
        <a href="https://wa.me/5516999991234?text=olá">WhatsApp</a>
        <a href="/contato">Fale com a gente</a>
        <a href="https://outra.com.br/contato">Outro site</a></body></html>'''
        parser = contatos.ExtratorContatos('https://empresa.com.br/', 'empresa.com.br')
        parser.feed(html)
        got = {(x[0], x[1]) for x in parser.contatos()}
        self.assertIn(('email', 'vendas@empresa.com.br'), got)
        self.assertIn(('email', 'contato@empresa.com.br'), got)
        self.assertIn(('telefone', '+551633334444'), got)
        self.assertIn(('whatsapp', 'https://wa.me/5516999991234'), got)
        self.assertNotIn(('email', 'comercial@injetado.com'), got)
        self.assertNotIn(('email', 'joao@empresa.com.br'), got)
        self.assertEqual(parser.links, {'https://empresa.com.br/contato'})

    def test_link_contato_ssrf_porta_e_dominios_externos(self):
        for href in ('http://127.0.0.1/contato', 'https://outra.com.br/contato',
                     'http://empresa.com.br:8000/contato', '/painel?redirect=/contato',
                     'javascript:alert(1)', '/arquivo.pdf'):
            self.assertIsNone(contatos.link_contato('https://empresa.com.br/', href, 'Entre em contato', 'empresa.com.br'), href)
        self.assertEqual(contatos.link_contato('https://empresa.com.br/', '/contato', '', 'empresa.com.br'),
                         'https://empresa.com.br/contato')

    @patch.object(contatos, 'buscar_html')
    def test_fluxo_prioriza_e_maximo_duas_paginas(self, mock_fetch):
        homepage = '''<a href="/contato">Contato</a><a href="/atendimento">Atendimento</a>
                       <a href="/orcamento">Orçamento</a><a href="/privacidade">Privacidade</a>'''
        def fake(url, dom, timeout, pausa, cache):
            if url == 'https://empresa.com.br/':
                return homepage, url
            return '<a href="mailto:comercial@empresa.com.br">Email</a>', url
        mock_fetch.side_effect = fake
        result = contatos.enriquecer_dominio('empresa.com.br', 'https://empresa.com.br/', 10, 0.5)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['paginas_lidas'], 3)
        self.assertEqual(len(result['contatos']), 1)
        self.assertEqual(mock_fetch.call_count, 3)

    @patch.object(contatos, 'buscar_html')
    def test_robots_bloqueado_nunca_inventa_contato(self, mock_fetch):
        mock_fetch.side_effect = AuditoriaErro('robots.txt proíbe acesso à página inicial')
        result = contatos.enriquecer_dominio('empresa.com.br', 'https://empresa.com.br/', 10, 0.5)
        self.assertEqual(result['status'], 'bloqueado_robots')
        self.assertEqual(result['contatos'], [])

    @patch.object(contatos, 'buscar_robots')
    @patch.object(contatos, 'requisitar')
    @patch.object(contatos.time, 'sleep')
    def test_respeita_disallow_sem_chamar_html(self, sleep, request, robots):
        rp = RobotFileParser()
        rp.parse(['User-agent: *', 'Disallow: /contato'])
        robots.return_value = (rp, 'lido', 0)
        with self.assertRaises(AuditoriaErro):
            contatos.buscar_html('https://empresa.com.br/contato', 'empresa.com.br', 4, 0.5, {})
        request.assert_not_called()

    @patch.object(contatos, 'buscar_robots')
    @patch.object(contatos, 'requisitar')
    @patch.object(contatos.time, 'sleep')
    def test_http_e_redirecionamento_externo_bloqueados(self, sleep, request, robots):
        robots.return_value = (None, 'ausente_http_404', 0)
        request.return_value = {'status': 302, 'headers': {'location': 'http://127.0.0.1/admin'}}
        with self.assertRaises(BloqueioRede):
            contatos.buscar_html('https://empresa.com.br/', 'empresa.com.br', 3, 1, {})

    def test_sqlite_exportacao_cache_e_bloqueio(self):
        res = {'dominio': 'empresa.com.br', 'status': 'ok', 'paginas_lidas': 2,
               'contatos': [
                   ('email', 'contato@empresa.com.br', 'https://empresa.com.br/contato', 'alta'),
                   ('email', 'vendas@empresa.com.br', 'https://empresa.com.br/contato', 'alta'),
                   ('telefone', '+551633334444', 'https://empresa.com.br/contato', 'media'),
               ], 'erro': ''}
        contatos.salvar(self.conn, res)
        contatos.salvar(self.conn, res)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM contatos_publicos').fetchone()[0], 3)
        out = Path(self.tmp.name) / 'output.csv'
        self.assertEqual(contatos.exportar(self.db, out), 1)
        with out.open(encoding='utf-8-sig', newline='') as f:
            rows = list(csv.DictReader(f, delimiter=';'))
        self.assertEqual(rows[0]['contato'], 'contato@empresa.com.br')
        self.assertEqual(rows[0]['revisao_humana'], 'sim')
        self.assertEqual(contatos.exportar(self.db, out, todos=True), 3)
        contatos.bloquear(self.db, 'email', 'contato@empresa.com.br', 'pedido')
        self.assertEqual(contatos.exportar(self.db, out, todos=True), 2)
        # Contato opt-out não reaparece na próxima visita.
        contatos.salvar(self.conn, res)
        self.assertEqual(contatos.exportar(self.db, out, todos=True), 2)
        contatos.bloquear(self.db, 'dominio', 'empresa.com.br', 'pedido')
        self.assertEqual(contatos.exportar(self.db, out, todos=True), 0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM contatos_publicos').fetchone()[0], 0)

    def test_coleta_seleciona_pendentes_ignora_bloqueios_e_cache(self):
        def resposta(dominio, url, timeout, pausa):
            return {'dominio': dominio, 'status': 'sem_contatos', 'paginas_lidas': 1,
                    'contatos': [], 'erro': ''}
        with patch.object(contatos, 'enriquecer_dominio', side_effect=resposta) as fake:
            stats = contatos.coletar(self.db, 1, 1, 2, 0.5, 30, None)
            self.assertEqual(stats['processados'], 1)
            stats = contatos.coletar(self.db, 1, 1, 2, 0.5, 30, None)
            self.assertEqual(stats['processados'], 1)  # segunda empresa
            stats = contatos.coletar(self.db, 10, 1, 2, 0.5, 30, None)
            self.assertEqual(stats['processados'], 0)

if __name__ == '__main__':
    unittest.main()
