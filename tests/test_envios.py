"""Testes offline da etapa 6: nenhum SMTP ou IMAP externo é acessado."""
import email
import io
import os
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

import envios
import gestor
from pipeline import gravar_lote


class EnviosTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db=Path(temp.name)/'test.sqlite3'
        self.conn=envios.preparar(self.db)
        self.addCleanup(self.conn.close)
        gravar_lote(self.conn,[
            ('csv:1','alfaindustrial.com.br','https://alfaindustrial.com.br/','Empresa Alfa','Araraquara','SP','office:company','','CSV'),
            ('csv:2','betaindustrial.com.br','https://betaindustrial.com.br/','Empresa Beta','Araraquara','SP','office:company','','CSV'),
            ('csv:3','gamaindustrial.com.br','https://gamaindustrial.com.br/','Empresa Gama','Araraquara','SP','office:company','','CSV')])
        self.cid=gestor.campanha(self.conn,'Teste envio')
        for dom in ('alfaindustrial.com.br','betaindustrial.com.br','gamaindustrial.com.br'):
            self.conn.execute('''INSERT INTO auditorias(dominio,auditado_em,status,pontos_prioridade) VALUES(?,?,?,?)''',(dom,'2026-10-31T11:00:00+00:00','ok',7))
            self.conn.execute('''INSERT INTO diagnosticos(dominio,auditado_em,assinatura,modo,modelo,versao,processado_em,status,foco_evidencia,assunto,mensagem,revisao_humana)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',(dom,'2026-10-31T11:00:00+00:00','s-'+dom,'regras','nenhum',1,'2026-10-31T11:05:00+00:00','gerado',
                'Ausência de description','Uma sugestão para '+dom,'Olá! Nossa equipe analisou alguns pontos públicos do seu site.',1))
            self.conn.execute('''INSERT INTO enriquecimentos(dominio,versao,processado_em,status,paginas_lidas,contatos_encontrados,erro) VALUES(?,?,?,?,?,?,?)''',(dom,1,'2026-10-31T11:05:00+00:00','ok',2,1,''))
            self.conn.execute('''INSERT INTO contatos_publicos(dominio,tipo,valor,origem_url,encontrado_em,confianca) VALUES(?,?,?,?,?,?)''',(
                dom,'email','comercial@'+dom,'https://'+dom+'/contato','2026-10-31T11:05:00+00:00','alta'))
        self.conn.commit()
        self.assertEqual(gestor.sincronizar(self.conn,self.cid,10,3),3)
        self.ids=[r[0] for r in self.conn.execute('SELECT id FROM leads_campanha ORDER BY id')]
        for lid in self.ids:
            gestor.aprovar(self.conn,lid,True)

    def lote(self,n=2):
        return envios.preparar_lote(self.conn,self.cid,n)[0]

    def envia(self,lote):
        enviados=[]
        resultado=envios.executar_lote(self.conn,lote,lambda *args: enviados.append(args),dormir=lambda _:None)
        return resultado,enviados

    def resposta(self, envio, texto='Obrigado, podemos conversar.'):
        row=self.conn.execute('SELECT l.contato_valor FROM leads_campanha l JOIN envios_email e ON e.lead_id=l.id WHERE e.id=?',(envio['id'],)).fetchone()
        msg=EmailMessage()
        msg['From']=row['contato_valor']
        msg['To']='csacomercial@exemplo.com'
        msg['Subject']='Re: Uma sugestão'
        msg['Message-ID']='<resposta-321@example.net>'
        msg['In-Reply-To']=envio['message_id']
        msg.set_content(texto)
        return msg.as_bytes()

    def test_fluxo_preparar_e_enviar_uma_vez(self):
        lote=self.lote(2)
        a=self.conn.execute('SELECT COUNT(*) FROM envios_email WHERE status="preparado"').fetchone()[0]
        self.assertEqual(a,2)
        result,sent=self.envia(lote)
        self.assertEqual(result['enviados'],2)
        self.assertEqual(len(sent),2)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM leads_campanha WHERE status="contatado"').fetchone()[0],2)
        second,env=self.envia(lote)
        self.assertEqual(second['enviados'],0)
        self.assertEqual(env,[])
        self.assertEqual(envios.preparar_lote(self.conn,self.cid,10)[0],self.lote_restante())

    def lote_restante(self):
        return self.conn.execute('SELECT MAX(id) FROM lotes_email').fetchone()[0]

    def test_lote_sem_novos_na_mesma_campanha(self):
        l1=self.lote(3)
        l2,rows=envios.preparar_lote(self.conn,self.cid,3)
        self.assertIsNone(l2)
        self.assertEqual(rows,[])

    def test_apenas_email_corporativo_funcional(self):
        for addr in ('carlos@gmail.com','diretor@empresa.com','qualquer@outlook.com','xxx@empresa.com','sem-arroba'):
            self.assertFalse(envios.email_funcional(addr),addr)
        for addr in ('comercial@empresa.com.br','contato@empresa.com','orcamentos@teste.net'):
            self.assertTrue(envios.email_funcional(addr),addr)

    def test_mensagem_texto_sem_pixel_com_cancelamento(self):
        m=envios.mensagem_pronta('csa@dominio.com','CSA','contato@empresa.com','Diagnóstico','Olá, uma análise.', '<abc@dominio.com>')
        self.assertEqual(m.get_content_type(),'text/plain')
        self.assertIn("não contatar",m.get_content())
        self.assertNotIn('<img',m.as_string())
        self.assertEqual(m['Reply-To'],'csa@dominio.com')
        with self.assertRaises(ValueError):
            envios.mensagem_pronta('a@b.com','CSA','contato@empresa.com','Assunto\nBCC: outro@dominio.com','Mensagem','<a@b.com>')

    def test_rascunho_editado_cancela_preparado(self):
        lote=self.lote(1)
        lid=self.ids[0]
        gestor.editar(self.conn,lid,'Outro assunto','Mensagem diferente para a empresa.','')
        stats,sent=self.envia(lote)
        self.assertEqual(stats['cancelados'],1)
        self.assertEqual(len(sent),0)

    def test_bloqueio_cancela_envio(self):
        lote=self.lote(1)
        gestor.bloquear(self.conn,'email','comercial@alfaindustrial.com.br','Não contatar')
        stats,sent=self.envia(lote)
        self.assertEqual(stats['cancelados'],0)  # bloqueio já cancelou antes do envio
        self.assertEqual(self.conn.execute('SELECT status FROM envios_email LIMIT 1').fetchone()[0],'cancelado')
        self.assertFalse(sent)

    def test_falha_ambigua_nao_reenvia_e_nao_continua(self):
        lote=self.lote(2)
        def falhar(*args):
            raise smtplib.SMTPServerDisconnected('Servidor fechou')
        import smtplib
        stats=envios.executar_lote(self.conn,lote,falhar,dormir=lambda _:None)
        self.assertEqual(stats['incertos'],1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM envios_email WHERE status="incerto"').fetchone()[0],1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM envios_email WHERE status="preparado"').fetchone()[0],1)
        stats2,sent=self.envia(lote)
        self.assertEqual(stats2['enviados'],1)
        self.assertEqual(len(sent),1)

    def test_resposta_valida_altera_funil_e_ignora_repetida(self):
        lote=self.lote(1)
        self.envia(lote)
        envio=self.conn.execute('SELECT * FROM envios_email WHERE lote_id=?',(lote,)).fetchone()
        resposta=self.resposta(envio)
        self.assertEqual(envios.processar_resposta(self.conn,resposta),'resposta')
        self.assertEqual(gestor.lead(self.conn,envio['lead_id'])['status'],'respondeu')
        self.assertEqual(envios.processar_resposta(self.conn,resposta),'repetido')

    def test_pedido_de_exclusao_bloqueia_e_nao_guarda_corpo(self):
        lote=self.lote(1)
        self.envia(lote)
        envio=self.conn.execute('SELECT * FROM envios_email WHERE lote_id=?',(lote,)).fetchone()
        resposta=self.resposta(envio,'Por favor, não quero receber outras mensagens. Obrigado.')
        self.assertEqual(envios.processar_resposta(self.conn,resposta),'exclusao')
        self.assertEqual(gestor.lead(self.conn,envio['lead_id'])['status'],'bloqueado')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM bloqueios_prospeccao WHERE tipo="email"').fetchone()[0],1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM respostas_email').fetchone()[0],1)

    def test_ignora_resposta_de_outro_email_e_resposta_sem_thread(self):
        lote=self.lote(1)
        self.envia(lote)
        envio=self.conn.execute('SELECT * FROM envios_email WHERE lote_id=?',(lote,)).fetchone()
        msg=email.message_from_bytes(self.resposta(envio),policy=email.policy.default)
        msg.replace_header('From','comercial@intruso.com')
        self.assertEqual(envios.processar_resposta(self.conn,msg.as_bytes()),'ignorado')
        msg.replace_header('From','comercial@alfaindustrial.com.br')
        del msg['In-Reply-To']
        self.assertEqual(envios.processar_resposta(self.conn,msg.as_bytes()),'ignorado')

    def test_ignora_automatico_e_aspas(self):
        lote=self.lote(1)
        self.envia(lote)
        envio=self.conn.execute('SELECT * FROM envios_email WHERE lote_id=?',(lote,)).fetchone()
        msg=email.message_from_bytes(self.resposta(envio,'Obrigado pela análise.\n\n> Não quero receber mais contatos.'),policy=email.policy.default)
        self.assertEqual(envios.processar_resposta(self.conn,msg.as_bytes()),'resposta')
        msg.replace_header('Message-ID','<outra@example.net>')
        msg['Auto-Submitted']='auto-replied'
        self.assertEqual(envios.processar_resposta(self.conn,msg.as_bytes()),'automatico')

    def test_cota_diaria(self):
        lote=self.lote(2)
        self.conn.execute("UPDATE envios_email SET status='incerto',iniciado_em=? WHERE id=(SELECT MIN(id) FROM envios_email)",(envios.agora(),))
        self.assertEqual(envios.limite_restante(self.conn),envios.LIMITE_DIA-1)

    def test_config_valida_sem_senhas(self):
        f=Path(self.db.parent)/'email_config.ini'
        f.write_text('''[smtp]\nhost=smtp.exemplo.com\nporta=465\nseguranca=ssl\nemail=csa@example.com\nnome=CSA\n[imap]\nhost=imap.exemplo.com\nporta=993\n''',encoding='utf-8')
        cfg=envios.carregar_config(f)
        self.assertEqual(cfg['de'],'csa@example.com')
        self.assertNotIn('senha',cfg)
        self.assertNotIn('password',cfg)

    def test_mock_do_imap_resposta_real(self):
        lote=self.lote(1)
        self.envia(lote)
        envio=self.conn.execute('SELECT * FROM envios_email WHERE lote_id=?',(lote,)).fetchone()
        raw=self.resposta(envio)
        class MockIMAP:
            def __init__(self,*args,**kw):pass
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def login(self,*a):return ('OK',[b''])
            def select(self,*a,**k):return ('OK',[b'1'])
            def uid(self,cmd,*args):
                if cmd=='SEARCH':return ('OK',[b'1'])
                return ('OK',[(b'1 (BODY[] {100})',raw),b')'])
        with patch.object(envios.imaplib,'IMAP4_SSL',MockIMAP):
            stats=envios.ler_imap(self.conn,dict(imap_host='imap.example.com',imap_porta=993,imap_user='csa@example.com'),'fake')
        self.assertEqual(stats['resposta'],1)
        self.assertEqual(stats['vistos'],1)

if __name__=='__main__':
    unittest.main()
