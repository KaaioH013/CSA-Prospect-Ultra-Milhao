"""Etapa 5: testes integrados offline, sem e-mails enviados nem Internet."""
import csv
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import HTTPServer
from pathlib import Path

import gestor
from pipeline import gravar_lote


class GestorTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.db = Path(self.t.name)/'dados.sqlite3'
        self.conn = gestor.preparar(self.db)
        self.addCleanup(self.conn.close)
        gravar_lote(self.conn, [
            ('csv:1','empresa.com.br','https://empresa.com.br/','Empresa Alfa','Araraquara','SP','office:company','','CSV'),
            ('csv:2','beta.com.br','https://beta.com.br/','Empresa Beta','São Carlos','SP','office:company','','CSV'),
        ])
        self.cid = gestor.campanha(self.conn,'Campanha teste')
        for dom, pts in [('empresa.com.br',8),('beta.com.br',5)]:
            self.conn.execute('''INSERT INTO auditorias(dominio,auditado_em,status,pontos_prioridade)
              VALUES(?,?,?,?)''',(dom,'2026-10-31T11:00:00+00:00','ok',pts))
            self.conn.execute('''INSERT INTO diagnosticos(dominio,auditado_em,assinatura,modo,modelo,
              versao,processado_em,status,foco_evidencia,assunto,mensagem,revisao_humana)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
              (dom,'2026-10-31T11:00:00+00:00','signature-'+dom,'regras','sem-modelo',1,
               '2026-10-31T11:01:00+00:00','gerado','Faltou metadado no HTML',
               'Diagnóstico para '+dom,'Olá, encontramos um possível ponto de melhoria no site da sua empresa.',1))
            self.conn.execute('''INSERT INTO enriquecimentos(dominio,versao,processado_em,status,
               paginas_lidas,contatos_encontrados,erro) VALUES(?,?,?,?,?,?,?)''',
               (dom,1,'2026-10-31T11:05:00+00:00','ok',2,2,''))
            self.conn.execute('''INSERT INTO contatos_publicos(dominio,tipo,valor,origem_url,
               encontrado_em,confianca) VALUES(?,?,?,?,?,?)''',
               (dom,'email','comercial@'+dom,'https://'+dom+'/contato',
                '2026-10-31T11:05:00+00:00','alta'))
            self.conn.execute('''INSERT INTO contatos_publicos(dominio,tipo,valor,origem_url,
               encontrado_em,confianca) VALUES(?,?,?,?,?,?)''',
               (dom,'telefone','+551633334444','https://'+dom+'/contato',
                '2026-10-31T11:05:00+00:00','media'))
        self.conn.commit()

    def sync(self):
        gestor.sincronizar(self.conn,self.cid,10,3,'SP')
        return self.conn.execute("SELECT id FROM leads_campanha WHERE dominio='empresa.com.br' AND campanha_id=?",(self.cid,)).fetchone()[0]

    def test_importa_e_prioriza_email_sem_duplicar(self):
        self.assertEqual(gestor.sincronizar(self.conn,self.cid,10,3,'SP'),2)
        self.assertEqual(gestor.sincronizar(self.conn,self.cid,10,3,'SP'),0)
        rows=self.conn.execute('SELECT * FROM leads_campanha ORDER BY pontos_prioridade DESC').fetchall()
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0]['contato_tipo'],'email')
        self.assertEqual(rows[0]['status'],'revisao')
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM historico_comercial WHERE evento='importado'").fetchone()[0],2)

    def test_aprovacao_exige_conferencia_e_documento_valido(self):
        lid=self.sync()
        with self.assertRaisesRegex(ValueError,'confirmação'):
            gestor.aprovar(self.conn,lid,False)
        gestor.aprovar(self.conn,lid,True)
        self.assertEqual(gestor.lead(self.conn,lid)['status'],'aprovado')
        self.assertEqual(gestor.metricas(self.conn,self.cid)['aprovados'],1)
        out=Path(self.t.name)/'export.csv'
        self.assertEqual(gestor.exportar(self.conn,self.cid,out),1)
        with out.open(encoding='utf-8-sig',newline='') as fh:
            exported=list(csv.DictReader(fh,delimiter=';'))
        self.assertEqual(exported[0]['dominio'],'empresa.com.br')
        self.assertEqual(exported[0]['contato_valor'],'comercial@empresa.com.br')

    def test_editando_aprovado_volta_para_revisao(self):
        lid=self.sync()
        gestor.aprovar(self.conn,lid,True)
        gestor.editar(self.conn,lid,'Assunto revisado','Mensagem revisada e conferível','Anotação comercial')
        self.assertEqual(gestor.lead(self.conn,lid)['status'],'revisao')
        self.assertIsNone(gestor.lead(self.conn,lid)['aprovado_em'])
        self.assertEqual(gestor.exportar(self.conn,self.cid,Path(self.t.name)/'out.csv'),0)

    def test_diagnostico_alterado_bloqueia_aprovacao_e_exportacao(self):
        lid=self.sync()
        self.conn.execute("UPDATE auditorias SET auditado_em=? WHERE dominio='empresa.com.br'",('2026-11-01T11:00:00+00:00',))
        self.conn.commit()
        with self.assertRaisesRegex(ValueError,'Diagnóstico'):
            gestor.aprovar(self.conn,lid,True)
        self.conn.execute("UPDATE auditorias SET auditado_em=? WHERE dominio='empresa.com.br'",('2026-10-31T11:00:00+00:00',))
        self.conn.commit()
        gestor.aprovar(self.conn,lid,True)
        self.conn.execute("UPDATE diagnosticos SET assinatura='nova-assinatura' WHERE dominio='empresa.com.br'")
        self.conn.commit()
        self.assertEqual(gestor.exportar(self.conn,self.cid,Path(self.t.name)/'out.csv'),0)

    def test_atualizar_rascunho_apos_nova_auditoria(self):
        lid=self.sync()
        self.conn.execute("UPDATE auditorias SET auditado_em=? WHERE dominio='empresa.com.br'",('2026-11-01T11:00:00+00:00',))
        self.conn.execute("UPDATE diagnosticos SET assinatura=?,auditado_em=?,mensagem=? WHERE dominio='empresa.com.br'",(
            'atual','2026-11-01T11:00:00+00:00','Mensagem nova com evidência técnica revisada'))
        self.conn.commit()
        gestor.atualizar_rascunho(self.conn,lid)
        gestor.aprovar(self.conn,lid,True)
        self.assertEqual(gestor.lead(self.conn,lid)['diagnostico_assinatura'],'atual')

    def test_historico_estagios_metricas(self):
        lid=self.sync()
        gestor.aprovar(self.conn,lid,True)
        for status in ('contatado','respondeu','reuniao','proposta','ganho'):
            gestor.transicao(self.conn,lid,status,'Etapa registrada manualmente')
        with self.assertRaises(ValueError):
            gestor.transicao(self.conn,lid,'revisao')
        met=gestor.metricas(self.conn,self.cid)
        for key in ('contatados','respostas','reunioes','propostas','ganhos'):
            self.assertEqual(met[key],1,key)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM historico_comercial WHERE lead_id=?',(lid,)).fetchone()[0],7)

    def test_nao_pula_etapas_comerciais_criticas(self):
        lid=self.sync()
        with self.assertRaises(ValueError):
            gestor.transicao(self.conn,lid,'contatado')
        gestor.aprovar(self.conn,lid,True)
        with self.assertRaises(ValueError):
            gestor.transicao(self.conn,lid,'ganho')

    def test_bloqueio_de_dominio_redige_dados_e_impede_exportacao(self):
        lid=self.sync()
        gestor.aprovar(self.conn,lid,True)
        self.assertEqual(gestor.bloquear(self.conn,'dominio','https://empresa.com.br/','Não contatar'),1)
        r=gestor.lead(self.conn,lid)
        self.assertEqual(r['status'],'bloqueado')
        self.assertIsNone(r['contato_valor'])
        self.assertIsNone(r['mensagem'])
        self.assertEqual(gestor.exportar(self.conn,self.cid,Path(self.t.name)/'x.csv'),0)
        self.assertFalse(self.conn.execute("SELECT 1 FROM contatos_publicos WHERE dominio='empresa.com.br'").fetchone())
        other=gestor.campanha(self.conn,'Outro segmento')
        self.assertEqual(gestor.sincronizar(self.conn,other,10),1)  # apenas beta

    def test_bloqueio_email_deixa_telefone_outro_dominio_livre(self):
        lid=self.sync()
        self.assertEqual(gestor.bloquear(self.conn,'email','comercial@empresa.com.br'),1)
        self.assertEqual(gestor.lead(self.conn,lid)['status'],'bloqueado')
        other=gestor.campanha(self.conn,'Campanha B')
        self.assertEqual(gestor.sincronizar(self.conn,other,10),2)
        r=self.conn.execute('SELECT contato_tipo FROM leads_campanha WHERE campanha_id=? AND dominio=?',(other,'empresa.com.br')).fetchone()
        self.assertEqual(r['contato_tipo'],'telefone')

    def test_bloqueio_externo_pela_etapa4_impede_aprovacao(self):
        lid=self.sync()
        self.conn.execute("INSERT INTO bloqueios_prospeccao(tipo,valor,motivo,registrado_em) VALUES('email','comercial@empresa.com.br','teste','agora')")
        self.conn.commit()
        with self.assertRaises(ValueError):
            gestor.aprovar(self.conn,lid,True)

    def test_html_escapado_sem_execucao_scripts(self):
        lid=self.sync()
        gestor.editar(self.conn,lid,'Assunto test','<script>alert(1)</script> Mensagem segura','teste')
        page=gestor.render_page(self.conn,self.cid,csrf='token')
        self.assertIn('&lt;script&gt;',page)
        self.assertNotIn('<script>alert(1)</script>',page)

    def test_filtro_paginacao_e_excel_csv_injection(self):
        self.sync()
        self.assertEqual(gestor.listar(self.conn,self.cid,'revisao','Alfa')[1],1)
        self.assertEqual(gestor.listar(self.conn,self.cid,'revisao','Inexistente')[1],0)
        lid=self.conn.execute("SELECT id FROM leads_campanha WHERE dominio='empresa.com.br'").fetchone()[0]
        gestor.editar(self.conn,lid,'=HYPERLINK("bad")','Mensagem de teste com dados suficientes','')
        gestor.aprovar(self.conn,lid,True)
        dest=Path(self.t.name)/'csv.csv'
        gestor.exportar(self.conn,self.cid,dest)
        with dest.open(encoding='utf-8-sig') as f:
            content=f.read()
        self.assertIn("'=HYPERLINK",content)

    def test_interface_web_local_csrf_e_post_restrito(self):
        from gestor import ServidorLocal
        handler=type('TestLocal',(ServidorLocal,),{'db':self.db,'csrf':'token_de_teste_123'})
        srv=HTTPServer(('127.0.0.1',0),handler)
        self.addCleanup(srv.server_close)
        thread=threading.Thread(target=srv.serve_forever,daemon=True)
        thread.start()
        self.addCleanup(lambda: (srv.shutdown(),thread.join(timeout=2)))
        base=f'http://127.0.0.1:{srv.server_port}'
        # GET precisa renderizar a interface e nunca alterar o DB.
        with urllib.request.urlopen(base+'/') as r:
            body=r.read().decode()
        self.assertIn('Gestor local de oportunidades',body)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM leads_campanha').fetchone()[0],0)
        form=urllib.parse.urlencode({'acao':'sincronizar','campanha_id':self.cid,
            'limite':1,'min_pontos':3,'csrf':'TOKEN_INVALIDO'}).encode()
        with self.assertRaises(urllib.error.HTTPError) as c:
            urllib.request.urlopen(urllib.request.Request(base+'/acao',data=form))
        self.assertEqual(c.exception.code,403)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM leads_campanha').fetchone()[0],0)
        form=urllib.parse.urlencode({'acao':'sincronizar','campanha_id':self.cid,
            'limite':1,'min_pontos':3,'csrf':'token_de_teste_123'}).encode()
        with urllib.request.urlopen(urllib.request.Request(base+'/acao',data=form)) as r:
            self.assertEqual(r.status,200) # segue 303 para a interface
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM leads_campanha').fetchone()[0],1)
        with self.assertRaises(urllib.error.HTTPError) as c:
            urllib.request.urlopen(urllib.request.Request(base+'/',headers={'Host':'evil.test'}))
        self.assertEqual(c.exception.code,403)


if __name__=='__main__':
    unittest.main()
