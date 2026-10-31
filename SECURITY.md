# Segurança e privacidade

**Nunca publique:** `dados/`, `saida/`, `email_config.ini`, `.env`, bancos SQLite, bases OSM baixadas, leads, listas de e-mails reais, senhas de aplicativo ou tokens. O `.gitignore` evita commits acidentais comuns, mas não substitui conferência manual antes do push.

Este é um protótipo experimental; o painel local não tem autenticação para exposição na internet. Execute em `127.0.0.1` e evite compartilhar o ambiente com usuários não autorizados.

O envio de e-mails exige decisão humana, uso autorizado da conta, revisão de cada destinatário e respeito à LGPD e às políticas dos provedores. O código usa testes falsos/mocks no CI, sem contato real.

Antes de abrir issue pública com um problema de segurança, remova dados pessoais, senhas, URLs privadas e endereços de clientes. Não publique vulnerabilidades exploráveis nem credenciais em issues públicas.

## Provedores de IA

Chaves de API devem ser fornecidas pelo ambiente ou pelo seletor com campo oculto. Nunca comite tokens, credenciais do Codex, arquivos locais de autenticação ou URLs contendo segredo. O projeto não acessa os tokens do Codex: a autenticação é administrada pelo CLI oficial. Ao escolher provedores externos, somente códigos de evidência e descrições fixas são enviados. APIs pagas podem gerar cobrança mesmo em operações de teste.


### Dashboard web local

`painel.py` vincula somente a `127.0.0.1`, valida Host/Origin e exige `X-CSA-CSRF` nas mutações. Não tem login multiusuário, não pode ser publicado diretamente na Internet. Os processos Python são disparados por lista explícita de ações, sem shell e sem aceitar comandos arbitrários. A interface nunca chama SMTP. A importação via HTTP impõe limite de 2 MiB e salva com nome aleatório dentro de `dados/importacoes/`.

API keys fornecidas pelo painel são passadas por ambiente ao subprocesso solicitado e não persistidas. Para testes de API, são passadas diretamente ao adaptador. Nunca use screenshots com chaves visíveis. As CLIs são executadas no contexto local e mantêm seus próprios logins oficiais.
