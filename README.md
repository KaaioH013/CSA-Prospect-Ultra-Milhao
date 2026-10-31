# CSA Prospecção

Pipeline local e experimental de prospecção B2B em **Python**, com coleta de websites, auditoria técnica, diagnósticos, identificação de contatos corporativos públicos, revisão humana e gestão de oportunidades. Usa **SQLite**, funciona no Windows e não requer APIs pagas para as funções principais.

> **Status:** protótipo para avaliação, não produção. Foram aprovados **91 testes automatizados offline** com dados controlados. **Não** foram validados 10 mil sites reais, inferências reais com provedores de IA, o SMTP/IMAP com provedores reais nem a entregabilidade de e-mails.
>
> **Segurança:** nenhum e-mail é transmitido durante a instalação, a execução dos testes, a coleta, a auditoria, a geração de diagnósticos ou a abertura do painel. O envio comercial precisa de conta configurada, aprovação individual e confirmação explícita de lote. **Não execute `ENVIAR_LOTE_WINDOWS.bat` para conhecer o projeto.**

## O que já existe

| Módulo | Arquivo | Função |
|---|---|---|
| Coleta | `pipeline.py` | Importa CSV ou extrai empresas com site da base OSM/Geofabrik, com deduplicação |
| Auditoria | `auditor.py` | Verifica sinais técnicos da homepage, incluindo HTTPS, metadados, HTML e contatos |
| Diagnósticos | `diagnosticos.py` + `cerebro_ia.py` | Regras, Ollama, OpenAI, APIs compatíveis, Gemini, Claude ou Codex CLI |
| Contatos | `contatos.py` | Procura canais corporativos públicos em até duas páginas internas adicionais |
| Gestor | `gestor.py` | Gestor web legado (mantido por compatibilidade) |
| Dashboard | `painel.py` + `ui/` | Interface responsiva, indicadores, processos, provedores, leads e histórico (Python stdlib, sem Node) |
| E-mail | `envios.py` | Prepara lotes, oferece envio por SMTP opcional e acompanha respostas por IMAP |
| Autoteste | `validar_email.py` | Valida conexão e, só após confirmação, teste para o próprio remetente |
| Seletor IA | `configurar_ia.py` | Menu para escolher provedor e modelo, com chave oculta e confirmação de custo |

## Requisitos

- Windows 10/11 com **Python 3.11 ou superior** instalado.
- Internet apenas para baixar a base OSM, instalar `osmium` e acessar websites (quando você optar por essas operações).
- IA é opcional: regras gratuitas por padrão. Para modelos locais, **Ollama** opcional; para APIs, uma chave do provedor; para login via ChatGPT, **Codex CLI** instalado e autenticado.
- Para fazer envios, conta de e-mail própria/autorizada configurada. O projeto **não** envia mensagens automaticamente.

## Teste rápido, sem gastar e sem cadastrar e-mail

1. Baixe e extraia o código em uma pasta, sem dados privados.
2. Execute `VALIDAR_TESTES_WINDOWS.bat`. Ele roda os testes offline, sem baixar bases, acessar provedores nem enviar mensagens.
3. Resultado esperado no ambiente atual: `Ran 91 tests` e `OK`.

Pelo terminal, na pasta do projeto:

```powershell
py -3 -m unittest discover -s tests -v
```

Para testar a importação **sem baixar centenas de MB**, use um CSV próprio com colunas `nome;site;cidade;estado`:

```powershell
py -3 pipeline.py importar-csv --arquivo exemplos\empresas_exemplo.csv
py -3 pipeline.py resumo
py -3 pipeline.py exportar
```

Os endereços `.example` do CSV incluído são fictícios, portanto **não devem ser auditados como sites reais**. Para testar auditoria online, importe sites públicos reais que você esteja autorizado a avaliar.

## Coleta e auditoria real (opcional)

`INICIAR_WINDOWS.bat` cria o ambiente virtual e, **mediante confirmação**, baixa a base OSM do Sudeste. A base é grande; quem quiser apenas conhecer a aplicação pode pular esse passo e usar `importar-csv`.

Depois de possuir empresas com sites reais no banco:

```powershell
py -3 auditor.py auditar --limite 100 --workers 4
py -3 auditor.py exportar
py -3 diagnosticos.py diagnosticar --modo regras --limite 30
py -3 diagnosticos.py exportar
py -3 contatos.py coletar --limite 100 --workers 2
py -3 contatos.py exportar
py -3 gestor.py sincronizar --campanha "Teste" --limite 50
py -3 gestor.py iniciar --porta 8785
```

Abra `http://127.0.0.1:8785` no navegador do próprio computador. O painel é local e não deve ser exposto à Internet sem autenticação, autorização e revisão de segurança.

### Dashboard completo (recomendado)

No Windows, abra **`ABRIR_PAINEL_WINDOWS.bat`**. No Linux/macOS:

```bash
python painel.py --porta 8785
```

Acesse `http://127.0.0.1:8785`. A interface é responsiva, roda **somente em localhost** e não precisa de Node.js nem hospedagem.

Seções: Visão geral com KPIs e funil, Operações com auditoria/diagnósticos/contatos/importação CSV/sincronização e interrupção, Empresas e leads com revisão/aprovação/bloqueios, Conexões de IA e Histórico. A importação CSV via painel aceita até 2 MB e precisa das colunas `nome;site` (opcionais `cidade;estado`). Os CSVs enviados e dados coletados ficam em `dados/` fora do Git.

As credenciais de API são solicitadas somente no momento da operação e não são gravadas no banco, `.env` ou `localStorage`. Atualizar a página apaga a chave digitada. Se optar por Codex ou Claude CLI, o login é realizado **no terminal** pela ferramenta oficial, não pelo dashboard.

O painel **nunca envia campanhas de e-mail**. Para testar a conta de e-mail, use os scripts de validação separados após revisão. A UI expõe API apenas em loopback, com proteção de origem e token CSRF. Não faça port forwarding nem exponha ao público.

### Escolha o cérebro da IA

Execute **`DIAGNOSTICAR_IA_WINDOWS.bat`**, que abre o menu de provedores (inclui o modo gratuito por regras). Instale [Ollama](https://ollama.com/download/windows) e execute `PREPARAR_IA_WINDOWS.bat` apenas se quiser o modelo local.

Também há opções para **OpenAI API, provedores compatíveis com OpenAI, Gemini, Claude API, Codex CLI e Claude CLI com login na sua conta ChatGPT**. O script aceita `--modo`, `--provedor`, `--modelo` e `--base-url`. Consulte o guia com comandos de cada provedor: **[Configurando as IAs](docs/PROVEDORES_IA.md)**.

**Custos:** chamadas de API podem ser cobradas à parte, Codex CLI consome os limites do plano ChatGPT e Claude CLI consome os limites da assinatura correspondente. A assinatura do ChatGPT não equivale a uma API key gratuita. O menu pede autorização explícita para o lote remoto e usa limite inicial de 3 registros. Não são enviadas campanhas de e-mail.

## Onde ficam seus dados

- `dados/`: SQLite, downloads OSM, registros e informações locais.
- `saida/`: CSVs gerados com empresas, diagnósticos e contatos.
- `email_config.ini`: configuração local do remetente; **não grave senhas** nesse arquivo.

Esses caminhos estão no `.gitignore` e **não devem ser enviados ao GitHub, nem incluídos em issues ou screenshots**. O repositório inclui somente um exemplo sem dados reais (`email_config.exemplo.ini`). Senhas para SMTP/IMAP são solicitadas no terminal ou via variáveis de ambiente.

## Boas práticas e limites

- Respeite os termos de cada fonte, o `robots.txt`, limites de requisição e exigências da **LGPD**. Um e-mail público não significa consentimento para receber campanhas.
- A auditoria atual verifica sinais técnicos limitados. Não substitui Lighthouse, auditoria visual completa nem consultoria de SEO ou acessibilidade.
- Rascunhos comerciais exigem revisão humana; não afirme perdas financeiras ou falhas que não foram comprovadas.
- O processamento pode consumir RAM, CPU, disco, banda e energia, apesar de não haver custo de API.
- Dados do OpenStreetMap: © OpenStreetMap contributors, licenciados sob ODbL: https://www.openstreetmap.org/copyright . Cumpra requisitos de atribuição e compartilhamento da base quando aplicáveis.

## Contribuições e testes

```powershell
py -3 -m unittest discover -s tests -v
```

Os testes também executam no GitHub Actions a cada push e pull request. Os provedores externos e o login real do Codex não são exercitados pela suíte offline. Para histórico técnico, comandos adicionais e restrições de cada módulo, consulte [documentação completa](docs/HISTORICO_E_DOCUMENTACAO_COMPLETA.md).

**Licenciamento do código:** este repositório não inclui uma licença open source explícita. Ele é público para inspeção e avaliação; para redistribuição ou uso além do permitido por lei, peça autorização ao titular do projeto. Essa condição não substitui a licença própria dos dados OSM.
