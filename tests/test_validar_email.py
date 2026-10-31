"""Autoteste sem mensagens ou conexões externas."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from io import StringIO
from contextlib import redirect_stdout
import validar_email

class EmailRealTestesOffline(unittest.TestCase):
    cfg={'de':'meuendereco@gmail.com','nome':'CSA','host':'smtp.gmail.com','porta':465,'modo':'ssl',
         'user':'meuendereco@gmail.com','imap_host':'imap.gmail.com','imap_porta':993,'imap_user':'meuendereco@gmail.com'}

    def test_autoteste_so_envia_para_proprio_remetente(self):
        m=validar_email.criar_msg_teste(self.cfg,'ABCD1234')
        self.assertEqual(str(m['From']),self.cfg['de'])
        self.assertEqual(str(m['To']),self.cfg['de'])
        self.assertIn('CSA VALIDACAO ABCD1234',str(m['Subject']))
        self.assertIn('Nenhuma empresa foi contatada',m.get_content())

    def test_sem_confirmacao_ninguem_recebe(self):
        with tempfile.TemporaryDirectory() as td:
            registro=Path(td)/'registro.json'
            with patch('builtins.input',return_value=''), patch.object(validar_email,'conectar_smtp') as smtp, redirect_stdout(StringIO()):
                validar_email.enviar_teste(self.cfg, registro)
            smtp.assert_not_called()
            self.assertFalse(registro.exists())

    def test_teste_pendente_impede_envio_acidental(self):
        with tempfile.TemporaryDirectory() as td:
            registro=Path(td)/'registro.json'
            registro.write_text('{"estado":"incerto","codigo":"001"}',encoding='utf-8')
            with patch.object(validar_email,'conectar_smtp') as smtp:
                with self.assertRaisesRegex(ValueError,'pendente'):
                    validar_email.enviar_teste(self.cfg, registro)
            smtp.assert_not_called()

if __name__=='__main__':
    unittest.main()
