# Etapa 7 | Validação de integração e autoteste controlado (31/10/2026)

**Status atual:** 72 testes automatizados locais aprovados, inclusive um teste integrado do
mesmo banco SQLite percorrendo CSV → auditoria HTML → diagnóstico por regras →
extração de contato → aprovação humana → lote → envio *simulado* → resposta
identificada pelo Message-ID. O teste integrado usa uma empresa `.example` e
conteúdo HTML fictício. **Não houve coleta externa nem envio de e-mails reais.**

O ambiente onde esta versão foi construída **não consegue consultar sites e
servidores SMTP/IMAP reais**. A autenticação e a entrega precisam ser testadas
no Windows do proprietário da conta, usando um endereço autorizado. Não
assuma que 72 testes locais comprovem entregabilidade ou a capacidade para
10 mil empresas.

## Instalar sem perder dados

1. Antes de atualizar, copie `dados/empresas.sqlite3` para um backup fora da pasta do projeto.
2. Copie os arquivos desta versão sobre o projeto anterior **SEM substituir nem apagar `dados/`**.
3. Execute `VALIDAR_TESTES_WINDOWS.bat`; não acessa contas nem envia e-mails.
4. Configure o SMTP/IMAP com `CONFIGURAR_EMAIL_WINDOWS.bat`. O exemplo do pacote
   é Gmail, mas substitua todos os parâmetros conforme seu provedor. A conta
   precisa ser sua ou autorizada; senha de aplicativo não deve ser enviada no chat.
5. Execute `VALIDAR_EMAIL_WINDOWS.bat`, opção 1. Esse teste somente autentica
   nos servidores SMTP e IMAP, sem enviar mensagens.
6. Se a conexão passar, abra novamente o menu, opção 2. Ele mostra o remetente,
   destinatário **idêntico ao remetente**, e só transmite **uma** mensagem após
   digitar a confirmação `TESTAR-MEU-EMAIL`. Nenhum lead é alterado.
7. Abra de novo o menu, opção 3, para conferir a presença da mensagem na INBOX
   via IMAP, sem marcá-la como lida. Se não for encontrada, verifique o spam e
   a entrega do provedor. Não repita o envio automaticamente: o registro fica
   em `dados/autoteste_email.json` para evitar duplicidade.

O autoteste não reutiliza a fila comercial e não é um disparador de campanhas.
Ele usa exclusivamente o próprio endereço que consta em `email_config.ini`.
**O SMTP aceitar uma mensagem não significa que ela foi entregue.** A validação
das respostas comerciais no funil ainda depende de um teste com remetente e
contato autorizado, feito posteriormente com um lead de teste.

Comandos equivalentes:

```powershell
python -m unittest discover -s tests -v
python validar_email.py conexao
python validar_email.py enviar-teste
python validar_email.py conferir
```

## Limites desta validação

- Coleta real com OpenStreetMap e auditoria de 10 mil sites: **não executadas**.
- Inferência real via Ollama: **não executada** neste ambiente.
- SMTP/IMAP reais: **não executados** neste ambiente.
- A versão 7 preserva a lógica das etapas 1 a 6. Não há envios comerciais automáticos.

---

# CSA Prospecção, Etapa 6: envio controlado por e-mail e acompanhamento IMAP

**Projeto acumulado das etapas 1 a 6.** A etapa 6 adiciona `envios.py`, os novos scripts Windows e a integração com a interface local. O envio é opcional. Nenhum código envia mensagens ao abrir o gestor, importar leads, preparar lotes ou fazer simulação.

## Primeiro uso, sem apagar o trabalho anterior

1. Feche os scripts e faça uma cópia de `dados/empresas.sqlite3`. Nunca sobrescreva a pasta `dados/` ao atualizar. Se esta for a primeira instalação, execute as etapas 1 a 5 antes.
2. Extraia o ZIP **CSA_Prospeccao_Etapa_6.zip** e copie os arquivos de código para sua pasta `CSA_Prospeccao`. O ZIP não inclui nenhum banco de dados de prospecção.
3. Execute `ABRIR_GESTOR_WINDOWS.bat`, revise os diagnósticos e marque a confirmação individual de cada lead a ser abordado. Só se qualificam e-mails corporativos funcionais, por exemplo `contato@empresa.com.br` ou `comercial@empresa.com.br`.
4. Execute `CONFIGURAR_EMAIL_WINDOWS.bat` e edite `email_config.ini`: servidor, porta, remetente e usuário SMTP/IMAP. **Não salve senha no arquivo.** O script solicitará a senha de aplicativo ao conectar. Para contas Google, consulte https://support.google.com/accounts/answer/185833?hl=pt-BR . Senhas de aplicativo podem não estar disponíveis em algumas contas; o sistema não implementa OAuth2 nesta versão.
5. Execute `PREPARAR_LOTE_WINDOWS.bat`: ele seleciona até 20 contatos já aprovados, exibe todos e registra um lote. **Nenhum e-mail é enviado.**
6. Execute `ENVIAR_LOTE_WINDOWS.bat`, informe o número do lote e confira a simulação. Para realmente transmitir, digite a confirmação exata exibida na tela. A senha é solicitada sem ser salva em disco. A conta de e-mail precisa ser sua ou estar expressamente autorizada para uso comercial.
7. Depois, execute `VERIFICAR_RESPOSTAS_WINDOWS.bat` para localizar respostas vinculadas às mensagens pelo `In-Reply-To` ou `References`. A caixa INBOX é lida sem marcar mensagens como lidas. Respostas válidas atualizam o funil; pedidos explícitos de não contato entram na lista de exclusão. `RESUMO_EMAIL_WINDOWS.bat` mostra os totais.

As etapas anteriores continuam disponíveis. O painel web permanece em `http://127.0.0.1:8785`; não há servidor público nem agendamentos automáticos.

## Comandos CLI opcionais

```powershell
python envios.py preparar --campanha "CSA Geral" --limite 20
python envios.py enviar --lote 1
# A próxima linha ENVIA de verdade somente após aprovação individual e confirmação exata:
python envios.py enviar --lote 1 --executar --confirmar ENVIAR-1
python envios.py respostas --dias 30 --limite 500
python envios.py resumo
python -m unittest discover -s tests -v
```

Use `.venv\Scripts\python.exe` no lugar de `python` se seu Python estiver apenas no ambiente virtual. Com `--db caminho\banco.sqlite3` você pode testar em uma cópia do banco. Com `--config caminho\email_config.ini` pode escolher outra conta de e-mail. A configuração não exige pacotes Python adicionais.

## Salvaguardas, métricas e comportamento em falhas

- **Dupla aprovação:** revisão por lead no gestor + confirmação textual do lote para transmissão. O envio começa em simulação. A plataforma **não automatiza follow-ups**; prioriza contato relevante, não disparos indiscriminados.
- **Limites conservadores:** máximo de 25 endereços por lote, 40 tentativas por dia no banco e intervalo mínimo de 30 segundos entre mensagens dentro de uma execução. São limites internos; o provedor pode ter limites mais baixos. Esses números NÃO significam que o domínio está preparado para enviar esse volume.
- **Sem repetição:** um mesmo domínio e e-mail entram apenas uma vez no sistema de lotes. Uma resposta SMTP incerta é marcada como `incerto` e **não é reenviada**. Em caso de erro, verifique a pasta Enviados; o programa interrompe o restante da execução.
- **Condições verificadas novamente antes da transmissão:** rascunho idêntico ao aprovado, contato ainda existente, auditoria e diagnóstico atualizados, exclusão não registrada. Alterações ou bloqueios cancelam o envio pendente.
- **Envio sem rastreamento invasivo:** mensagem de texto simples, sem pixels, sem anexos, sem URLs de rastreamento e com opção de não contato por resposta. Não mede abertura ou clique.
- **Identificação de respostas:** para evitar vincular conversas erradas, o sistema exige identificador da conversa (`In-Reply-To`/`References`) e remetente igual ao destinatário original. Respostas sem essas informações não são classificadas automaticamente. Respostas automáticas são ignoradas. O conteúdo da resposta não é armazenado no banco; ficam data, identificação derivada por hash e classificação.
- **Opt-out:** pedidos claros de remoção são bloqueados automaticamente, limpando o contato do gestor. Pedidos ambíguos ainda exigem revisão da caixa manualmente.
- **Métricas:** `enviado` significa *aceito pelo servidor SMTP*, não prova entrega na caixa de entrada, leitura ou interesse. `incerto` exige verificação humana. Respostas por outros canais devem ser registradas manualmente.
- **Proteção de senha:** a senha SMTP/IMAP é digitada no prompt ou fornecida por variável de ambiente `CSA_SMTP_PASSWORD` / `CSA_IMAP_PASSWORD`; nunca fica salva no arquivo de configuração. O arquivo `email_config.ini` também é ignorado pelo Git.
- **Execução local:** não há serviço externo de envio incluído. O custo de API é R$ 0, mas o provedor de correio, domínio, energia, manutenção e disponibilidade podem ter custos.

### Antes de colocar um domínio em operação

Confirme titularidade/autorização da conta de envio, SPF/DKIM/DMARC no domínio, política do provedor e fluxo de atendimento das respostas. Preferencialmente use uma caixa comercial dedicada. Uma infraestrutura gratuita ou pessoal não garante entregabilidade e pode ter políticas anti-spam restritivas. Valide a base legal e a proporcionalidade para cada campanha; endereço público não equivale a consentimento. Consulte a orientação da ANPD sobre legítimo interesse e expectativas do titular em https://www.gov.br/anpd/pt-br/centrais-de-conteudo/materiais-educativos-e-publicacoes/guia_orientativo_hipoteses_legais_tratamento_de_dados_pessoais_legitimo_interesse .

### O que foi testado e o que não foi

Foram executados testes automáticos com SQLite temporário, contatos sintéticos, SMTP simulado e IMAP simulado, incluindo bloqueios, duplicidade, falha de rede, resposta e opt-out. A interface local também é testada pelas etapas anteriores. **Não houve envio de e-mail real, acesso a caixa IMAP real, coleta real de 10 mil empresas ou validação de entregabilidade.** O primeiro uso real deve ser feito com uma conta controlada e destinatário de teste autorizado.

---

## Documentação das etapas anteriores

# CSA Prospecção | Etapas 1, 2 e 3

Projeto local em Python para coletar estabelecimentos com sites públicos, auditar a homepage e gerar **rascunhos comerciais para revisão** usando IA instalada no próprio computador. Banco único em SQLite. **Sem APIs pagas; não há envio de e-mails.**

## ETAPA 3 | Inteligência local e rascunhos comerciais

**Se o banco da etapa 2 já existir:** NÃO substitua nem apague a pasta `dados/`. Copie somente os arquivos novos `diagnosticos.py`, `PREPARAR_IA_WINDOWS.bat` e `DIAGNOSTICAR_IA_WINDOWS.bat` para dentro da pasta atual do projeto. Os arquivos anteriores continuam iguais. **Faça uma cópia do SQLite antes de executar qualquer script**, principalmente se tiver dados próprios. O arquivo `dados/empresas.sqlite3` não é incluído neste ZIP.

### Instalação sem assinatura

1. Execute a coleta da etapa 1 e a auditoria da etapa 2. A etapa 3 precisa de `auditorias` com `status=ok` e problemas técnicos detectados.
2. Instale o **Ollama para Windows** pelo site oficial: https://ollama.com/download/windows . Não escolha modo cloud nem assine uma API: o script só se conecta a `127.0.0.1:11434`.
3. Execute `PREPARAR_IA_WINDOWS.bat`. Ele **pergunta antes de baixar** o modelo `qwen3:1.7b` (aprox. 1,4 GB, quantizado). Ou, no terminal, execute `ollama pull qwen3:1.7b`.
4. Execute `DIAGNOSTICAR_IA_WINDOWS.bat`. Ele processa **até 30 sites com evidências novas**, grava cada rascunho no SQLite e exporta `saida/diagnosticos.csv`.
5. Abra o CSV e **revise cada constatação no site real** antes de fazer qualquer contato. Nenhuma mensagem é enviada.

Use comandos no terminal dentro da pasta do projeto para aumentar a escala:

```powershell
python diagnosticos.py verificar-ollama
python diagnosticos.py diagnosticar --limite 1000 --min-pontos 3
python diagnosticos.py exportar
python diagnosticos.py resumo
```

Para usar um modelo local de maior capacidade (download separadamente):

```powershell
ollama pull qwen3:4b
python diagnosticos.py diagnosticar --limite 1000 --modelo qwen3:4b --min-pontos 4
```

Para não instalar modelo nenhum, há um **modo baseado em regras**, que não é IA:

```powershell
python diagnosticos.py diagnosticar --limite 1000 --modo regras
python diagnosticos.py exportar
```

`--uf SP` limita os registros a empresas cujo endereço foi cadastrado em SP. `--forcar` refaz os rascunhos da configuração atual; sem isso, resultados gerados com a mesma auditoria/modelo são ignorados. Quando o site for reavaliado ou quando trocar o modelo/modo, a saída será atualizada. Erros individuais são registrados e podem ser tentados de novo.

### O que a IA realmente faz

- Recebe **apenas códigos de achados técnicos e suas descrições padronizadas**, sem HTML original, nomes de funcionários nem e-mails pessoais. Os metadados externos não são enviados ao modelo, para evitar instruções maliciosas embutidas em páginas.
- Escolhe **uma evidência principal**, tom (direto ou consultivo) e pergunta final. O texto final usa **somente descrições técnicas mapeadas no Python**, o nome comercial e o domínio cadastrados. A IA não inventa fatos no corpo da mensagem.
- Guarda no banco o código da evidência, a justificativa interna da IA, o rascunho, modelo e referência à auditoria. Toda mensagem sai marcada como `revisao_humana_pendente=1`.
- Não diagnostica perda de faturamento, notas no Google, problemas visuais de celular, SEO completo ou conversão, porque as auditorias atuais **não medem essas coisas**.

Este mecanismo é intencionalmente conservador: **não transforma hipótese em fato comercial**. A IA é usada na priorização e no estilo, não para afirmar resultados inexistentes. A qualidade textual e a utilidade comercial precisam de validação com leads reais.

### Custos e capacidade

**R$ 0 em APIs e assinaturas** após instalar o Ollama e baixar os pesos. Há custo de energia, uso de hardware, internet e tempo. O modelo pequeno `qwen3:1.7b` ocupa cerca de 1,4 GB no download, mas precisa de RAM adicional para rodar. Funciona em CPU sem GPU dedicada, porém pode ser bastante lento. **Não foi medido tempo para 10 mil gerações em CPU**; por isso o padrão é selecionar primeiro os sites com melhor evidência e rodar um lote de 30. As 10 mil auditorias são tarefa da etapa 2, não necessariamente 10 mil chamadas de IA.

Não há scraper de e-mails adicionais, enriquecimento de contatos nem disparo automático nesta etapa. Prospecção requer análise jurídica, inclusive LGPD, opt-out e avaliação de reputação de domínio. Atribua dados derivados do OSM conforme a ODbL.

### Requisitos e validação

Python 3.11+ (biblioteca padrão para auditoria e diagnóstico), Ollama local para o modo IA. A extração OSM continua dependendo de `osmium` da etapa 1. Execute todos os testes com `python -m unittest discover -s tests -v`. Os testes da IA usam uma resposta **simulada de Ollama**, portanto validam a integração e proteção contra saídas inválidas, mas **não medem precisão real do modelo**.

---

## Instalação no Windows

- **Para atualizar o código da etapa 2 (se necessário):** descompacte este ZIP e copie `auditor.py` e `AUDITAR_WINDOWS.bat` para dentro da sua pasta `CSA_Prospeccao` atual. Mantenha a pasta `dados/` com o banco existente. Não precisa baixar a base OSM novamente nem reinstalar as dependências para fazer auditorias.
- **Se ainda NÃO executou a etapa 1:** extraia o projeto completo e rode `INICIAR_WINDOWS.bat` uma vez, com Python 3.11 ou superior. Ele instala `osmium`, solicita autorização antes do download da região Sudeste e gera `dados/empresas.sqlite3`. O arquivo de download pode ser grande.
- Com a base pronta, execute `AUDITAR_WINDOWS.bat`. Ele audita **até 100 domínios pendentes**, salva cada resultado no mesmo SQLite e gera `saida/auditorias.csv`. Fechar o programa não apaga auditorias já gravadas.

## 10 mil auditorias sem assinatura

Abra o terminal **na pasta do projeto**:

```powershell
python auditor.py auditar --limite 10000 --workers 8 --timeout 10 --pausa 0.5
python auditor.py exportar
python auditor.py resumo
```

Use `.venv\Scripts\python.exe` no lugar de `python` se sua instalação do Windows exigir. Para restringir a São Paulo:

```powershell
python auditor.py auditar --limite 10000 --uf SP
```

O processo seleciona **domínios ainda não auditados** ou analisados há mais de 30 dias (padrão), não 10 mil empresas necessariamente diferentes. Para reauditar antes do prazo:

```powershell
python auditor.py auditar --limite 100 --dias 0
```

É possível rodar várias vezes. O fluxo não cria uma nova linha para o mesmo domínio e **não repete análises recentes** sem você solicitar. O número de domínios efetivamente disponíveis depende da qualidade e da cobertura da base coletada.

## O que é verificado

Quando a página inicial pode ser obtida como HTML estático:

- HTTPS e códigos HTTP, com verificação de certificado TLS no acesso HTTPS;
- `<title>`, meta description, meta viewport, atributo de idioma e presença de H1;
- imagens com atributo `alt` ausente (não considera `alt=""` um erro automático);
- meta robots e cabeçalho `X-Robots-Tag: noindex`;
- canonical informado, presença de formulário ou link de contato;
- e-mails **genéricos corporativos** e telefones publicados em links `mailto:`/`tel:` na página inicial;
- duração de espera até os **cabeçalhos** da resposta e bytes de HTML transferidos, limitados a 768 KiB;
- classificação por quantidade/peso de **indícios de problemas na homepage** para inspeção posterior.

O campo `pontos_prioridade` vai de 0 a 20: **maior significa mais indícios**, não maior chance comprovada de compra nem nota de SEO. As evidências individuais ficam na coluna `problemas_detectados` do CSV.

## Proteções e limites

- Antes de acessar uma página, o auditor consulta `/robots.txt`, respeita `Disallow`, `Crawl-delay` de até 30s e bloqueia quando há indisponibilidade técnica, 401/403/429 ou erro 5xx. Em caso de `Crawl-delay` acima de 30s, o site fica sem auditoria. Conforme [RFC 9309](https://www.rfc-editor.org/rfc/rfc9309.html), ausência com 404 pode permitir leitura.
- Faz no máximo **8 conexões simultâneas** por padrão (configurável até 16). Pausa mínima de 0,5 s entre robots e página; acessa apenas a homepage e o robots, com redirecionamentos controlados.
- Aceita somente domínios e IPs públicos com HTTP(S) nas portas padrão, verifica DNS antes de conectar, fixa a conexão ao IP validado e **não segue redirecionamentos para domínios diferentes**. Evita acessos a redes locais. Não permite requisições arbitrárias para serviços internos.
- Não renderiza JavaScript, não testa visualmente o layout, não faz navegação mobile nem mede Lighthouse, PageSpeed, Core Web Vitals ou conversões. A métrica `tempo_cabecalhos_ms` **não equivale à velocidade real do site**.
- Algumas páginas bloqueiam crawlers, não apresentam HTML útil ou enviam conteúdo com codificação não suportada. Esses casos recebem status distinto; **não são classificados automaticamente como sites ruins**.
- A ferramenta respeita critérios de uso responsável. Antes de prospectar, confirme evidências relevantes manualmente, avalie a LGPD e políticas de contato. **Não há disparo de mensagens.**

## Custos

**R$ 0 em licenças, APIs e tokens na etapa 2**. Seu custo real envolve eletricidade, tráfego e tempo de máquina. Quantidade de dados e duração dependem dos sites e da rede. Auditar 10 mil domínios pode envolver aproximadamente 20 mil requisições HTTP (robots + página), além de redirecionamentos e novas tentativas; não existe uma garantia de concluir 10 mil análises HTML válidas.

## Estrutura de arquivos

```text
CSA_Prospeccao/
    pipeline.py                # etapa 1, coleta, deduplicação, CSV
    auditor.py                 # etapa 2, auditoria e relatório CSV
    INICIAR_WINDOWS.bat        # etapa 1, instalação e download OSM
    AUDITAR_WINDOWS.bat        # etapa 2, até 100 sites e exportação
    diagnosticos.py            # etapa 3, IA local e rascunhos
    PREPARAR_IA_WINDOWS.bat    # instalação opcional do modelo local
    DIAGNOSTICAR_IA_WINDOWS.bat # lote de até 30 rascunhos de IA
    requirements.txt           # osmium exigido apenas pela extração OSM
    dados/empresas.sqlite3     # criado localmente, não incluído no ZIP
    saida/auditorias.csv       # gerado localmente, não incluído no ZIP
    saida/diagnosticos.csv     # gerado localmente, não incluído no ZIP
    tests/                     # testes automatizados
```

## Importar outra lista sem baixar OSM

```powershell
python pipeline.py importar-csv --arquivo empresas.csv
python auditor.py auditar --limite 10000
python auditor.py exportar
```

O CSV deve conter pelo menos `site` (aceita também `website`, `url` ou `dominio`), opcionalmente `nome`, `cidade`, `estado`, `categoria` e `telefone`. O sistema elimina domínios duplicados.

## Testes

```powershell
python -m unittest discover -s tests -v
```

Os testes usam dados sintéticos e um servidor HTTP local de teste. Isso valida o pipeline, o banco, bloqueios e a extração de HTML, **mas não demonstra que foram auditados 10 mil sites reais**. O desempenho em produção e a cobertura devem ser avaliados no seu PC.

## Fonte dos dados da etapa 1

Dados OSM: © OpenStreetMap contributors, disponibilizados sob ODbL. Atribuição e obrigações de licença devem ser respeitadas ao redistribuir bases derivadas.

- https://download.geofabrik.de/south-america/brazil/sudeste.html
- https://www.openstreetmap.org/copyright

Este ZIP substitui os anteriores em novas instalações e **não inclui o banco de dados existente**. Ao usar em pasta já instalada, preserve `dados/` e faça backup antes de qualquer mudança.

---

# Etapa 4: enriquecimento de contatos comerciais públicos

Arquivo novo: `contatos.py`. Atalho Windows: `ENRIQUECER_CONTATOS_WINDOWS.bat`.
Não usa API externa, proxy, CAPTCHA, login, motores de busca ou serviços pagos.

**Atualização sem perda de banco:** preserve `dados/` e `saida/` e copie os arquivos da etapa 4 por cima do projeto da etapa 3. O SQLite cria as tabelas adicionais automaticamente. Faça backup de `dados/empresas.sqlite3` antes de atualizar. Não é necessário baixar o OSM novamente.

**No Windows:** rode `ENRIQUECER_CONTATOS_WINDOWS.bat` para processar até 100 domínios e exportar um CSV para revisão.

**Para escalar:**

```powershell
python contatos.py coletar --limite 10000 --workers 4 --pausa 1.0 --timeout 10
python contatos.py exportar
python contatos.py resumo
```

O arquivo `saida/contatos_para_revisao.csv` contém até um contato por domínio, priorizando e-mail, depois WhatsApp e telefone. Para registrar todos os canais públicos:

```powershell
python contatos.py exportar --todos --arquivo saida/todos_contatos.csv
```

Regras: lê **apenas a página inicial e até duas páginas de contato explicitamente ligadas nela**, sem navegar em links externos. Procura e-mails de caixas genéricas de negócio (ex.: `comercial@...`, `contato@...`), telefones e links de WhatsApp publicados pela empresa. Não coleta e-mails de pessoas identificadas pelo nome nem tenta adivinhar endereços. Registra a página de origem, data e confiança (maior quando o e-mail pertence ao domínio). Detectar um padrão de e-mail **não garante que a caixa exista**: a verificação é apenas sintática, sem validação SMTP/MX. Também não garante que o telefone pertença ao setor comercial.

As páginas respeitam `robots.txt`, bloqueios de segurança e redirecionamentos internos. A coleta é pausada por origem, usa no máximo 4 trabalhadores por padrão, leitura limitada a 512 KiB por página e limita as páginas extras por domínio. Há cache de 30 dias, SQLite com gravação por domínio e retomada após interrupção. Um site com problemas de conexão ou bloqueio é registrado sem inventar contato. Como o crawler não renderiza JavaScript, sites que dependem dele podem aparecer como `sem_contatos`.

**Lista de exclusão:** quando receber pedido de não contato, remova os dados e impeça a recolheita pelo comando:

```powershell
python contatos.py bloquear --dominio empresa.com.br --motivo "Solicitou não contato"
python contatos.py bloquear --email contato@empresa.com.br --motivo "Solicitou não contato"
```

`contatos.py` **não envia mensagens**. Contato publicado na web não equivale a consentimento, nem torna por si só o uso irrestrito desses dados lícito. Antes de campanhas, avalie base legal, transparência, minimização, opt-out e direitos dos titulares conforme LGPD e orientação da ANPD: https://www.gov.br/anpd/pt-br/centrais-de-conteudo/materiais-educativos-e-publicacoes/guia_orientativo_hipoteses_legais_tratamento_de_dados_pessoais_legitimo_interesse . Dados coletados do OSM seguem ODbL: https://www.openstreetmap.org/copyright . Protocolo de robots: https://www.rfc-editor.org/rfc/rfc9309.html .

**Limites reais:** para 10 mil domínios, o programa pode fazer até aproximadamente 30 mil requisições HTML, além de robots e redirects. Não significa 10 mil contatos aproveitáveis. Não medimos a cobertura, velocidade, entregabilidade, conformidade comercial ou conversão em empresas reais. Custos de serviço: R$ 0; custos de energia, rede e hardware não são zero.

---

# Etapa 5: gestor local de campanhas e oportunidades

Novo `gestor.py` e `ABRIR_GESTOR_WINDOWS.bat`. Esta é uma **fila de revisão e gestão de oportunidades**, não um disparador de e-mails ou WhatsApp. A interface funciona no próprio computador, com Python 3.11+ e SQLite, sem instalar Node, CRM ou bibliotecas extras. O único pacote Python de terceiros do projeto é `osmium`, necessário apenas na coleta da etapa 1.

## Atualização sem perder trabalho

1. Faça cópia de segurança do banco `dados/empresas.sqlite3` (antes, feche qualquer script do projeto). **Não exclua `dados/` ou `saida/`.**
2. Extraia este ZIP e copie os arquivos do projeto para sua pasta `CSA_Prospeccao` existente. Confirme substituir **os arquivos de código**, não a pasta de dados.
3. Execute `ABRIR_GESTOR_WINDOWS.bat`. O navegador abre em `http://127.0.0.1:8785`, disponível **somente no próprio PC**. Use a tela para criar campanhas, importar leads, revisar e registrar andamento.
4. Para aparecerem leads, execute antes as etapas 1–4: coleta de sites, auditoria, diagnósticos e enriquecimento de contatos. Depois pressione **Adicionar à fila** no gestor.

As tabelas `campanhas`, `leads_campanha` e `historico_comercial` são criadas no SQLite existente. O instalador **não apaga nem recria** os dados anteriores. Os arquivos `dados/*.sqlite3` e `saida/*.csv` não estão no ZIP distribuído.

## Fluxo de trabalho

1. **Adicionar à fila:** seleciona os domínios com auditoria `ok`, diagnóstico atualizado, contato comercial obtido e sem exclusão. Prioriza pontuação técnica, e-mail corporativo e confiança da origem. Um domínio entra no máximo uma vez por campanha, mesmo que existam várias páginas, contatos ou estabelecimentos associados.
2. **Revisar:** confira manualmente o site, a evidência e o contato. Edite assunto, mensagem e observações conforme necessário. O texto não é automaticamente enviado.
3. **Aprovar:** marque a caixa de confirmação e clique em **Aprovar para contato manual**. O programa bloqueia aprovação de diagnósticos desatualizados, contatos apagados e empresas que constem em exclusão.
4. **Registrar andamento:** após fazer contato **fora do sistema**, marque `Contato feito`; depois, conforme os fatos, `Respondeu`, `Reunião`, `Proposta`, `Ganho` ou `Perdido`. É possível descartar oportunidades. O histórico preserva cada alteração de status e data.
5. **Exportar:** a opção da tela e o comando CLI exportam **apenas contatos aprovados, atuais e não bloqueados**. Essa exportação não marca ninguém como contatado e pode conter a mesma linha novamente enquanto o status for `Aprovado`.
6. **Não contatar:** domínio e e-mail podem entrar na lista de exclusão. O contato é removido do inventário, o lead correspondente é bloqueado e as cópias do contato, assunto e mensagem são apagadas do gestor. O histórico do evento é mantido sem o contato. Antes de registrar observações, evite dados pessoais desnecessários.

## Comandos opcionais

No terminal, na pasta `CSA_Prospeccao`:

```powershell
python gestor.py sincronizar --campanha "CSA Geral" --limite 10000 --min-pontos 3 --uf SP
python gestor.py iniciar --porta 8785
python gestor.py resumo --campanha "CSA Geral"
python gestor.py exportar --campanha "CSA Geral" --arquivo saida/leads_aprovados.csv
python gestor.py bloquear --dominio exemplo.com.br --motivo "Solicitou não receber contatos"
python gestor.py bloquear --email contato@exemplo.com.br --motivo "Solicitou não receber contatos"
```

No Windows, use `.venv\Scripts\python.exe` se necessário. A interface abre diretamente na campanha mais recente. É possível criar múltiplas campanhas e filtrar por nome, domínio e etapa do funil.

## Indicadores

O gestor mostra total na fila, aprovações históricas, contatos realizados, respostas, reuniões, propostas e vendas, além da taxa de respostas sobre contatos. Os indicadores dependem do **registro manual correto**. A entrada na fila não conta como contato. Uma venda é registrada como sucesso somente quando a etapa `Ganho` for atingida, não quando a IA gerar uma mensagem.

## Segurança e limitações

- A interface escuta **somente `127.0.0.1`**. Não abra portas, não publique em domínio nem exponha a pasta `dados` em redes. A interface local não oferece autenticação para múltiplos usuários.
- Formulários usam token contra requisições externas, verificação de origem, escape do conteúdo HTML e limite de tamanho; a exportação protege contra fórmulas maliciosas em planilhas.
- Nenhum script da etapa 5 conecta ao Gmail, WhatsApp, SMTP ou ferramentas de disparo. **Nada é enviado automaticamente.** Os contatos não são testados quanto à entregabilidade, e a auditoria técnica não comprova impacto comercial.
- A lista de exclusão protege este sistema, não outras ferramentas externas. Mesmo dados de contato públicos requerem avaliação de base legal, transparência, finalidade, direitos dos titulares e canal de oposição conforme LGPD.
- O painel trabalha em uma única máquina. Não é um sistema multiusuário, não sincroniza respostas de caixas de e-mail e não mede abertura ou clique. O usuário registra esses eventos manualmente.
- Atenção: se uma auditoria ou diagnóstico mudar depois da aprovação, o registro antigo é **excluído do CSV exportado** até o rascunho ser atualizado e novamente aprovado.
- Fonte de estabelecimento OSM: © OpenStreetMap contributors, ODbL.

### Validação

```powershell
python -m unittest discover -s tests -v
```

Inclui testes integrados offline para deduplicação, priorização do canal, revisão, estados comerciais, histórico, bloqueio, exportação segura e proteção da interface local. Nenhum desses testes comprova que foram encontrados 10 mil leads reais nem que houve venda.
