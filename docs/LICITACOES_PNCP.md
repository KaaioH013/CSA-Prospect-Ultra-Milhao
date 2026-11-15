# Coletor de licitações de software: etapa 1, PNCP

**Status:** módulo experimental, independente do CRM e do coletor de empresas. O código usa apenas a biblioteca padrão do Python 3.11+; não requer OAuth, API key, IA ou `osmium`.

## O que faz

Consulta a **API pública de contratações por data de publicação** do Portal Nacional de Contratações Públicas (PNCP), percorre todas as páginas (até o limite configurado), classifica possíveis oportunidades de software por regras transparentes, deduplica pelo identificador oficial e guarda os registros em SQLite separado. Exporta CSV com link para o edital no PNCP.

Por padrão cobre **Brasil inteiro**, um dia de publicações e as modalidades 4 (concorrência eletrônica), 6 (pregão eletrônico) e 8 (dispensa). Outros códigos podem ser indicados em `--modalidades`. Uma contratação de software sem termos específicos no objeto pode não ser encontrada; uma contratação com termos semelhantes pode ser irrelevante. **É triagem preliminar, não habilitação ou análise jurídica.**

## Execução em Windows (PowerShell)

Na raiz do repositório:

```powershell
# Teste offline: não acessa Internet e não modifica dados reais
py -3 -m unittest discover -s tests -p test_licitacoes_pncp.py -v

# Consulta apenas (sem gravar no banco)
py -3 licitacoes_pncp.py coletar --dias 1 --simular

# Consulta real de um dia, todo o Brasil, e grava no SQLite independente
py -3 licitacoes_pncp.py coletar --dias 1

# Exemplo: últimos 7 dias, somente SP, pregão e dispensa
py -3 licitacoes_pncp.py coletar --dias 7 --uf SP --modalidades 6 8

# Exporta relatório CSV com todas as oportunidades guardadas
py -3 licitacoes_pncp.py exportar

# Exporta somente propostas com data de encerramento posterior a hoje
py -3 licitacoes_pncp.py exportar --somente-prazo-futuro
```

O SQLite fica em `dados/licitacoes_pncp.sqlite3` e o CSV em `saida/licitacoes_pncp.csv`. As duas pastas já são ignoradas pelo Git. Reexecuções atualizam registros existentes; nenhuma tabela anterior do projeto é alterada. O módulo não faz envio, cadastro, login, proposta ou inscrição.

## Limites importantes

- **Fonte:** `https://pncp.gov.br/api/consulta/v1/contratacoes/publicacao`. Os argumentos de consulta são `dataInicial`, `dataFinal`, `codigoModalidadeContratacao`, `pagina` e `tamanhoPagina`; `uf` é opcional.
- **Paginação:** máximo de 50 por página. O módulo aborta com erro se o total de páginas exceder `--max-paginas` (padrão 200). Ao encontrar muitas páginas, divida as consultas em intervalos menores. Erros de rede também abortam sem gravar registros parciais da execução.
- **Período:** até 31 dias por consulta (conservador). Consultar por publicação não garante que a oportunidade esteja aberta no momento da leitura. Compare os prazos com a data/hora oficial e cheque possíveis retificações e suspensão.
- **Documentos:** neste estágio são consultados metadados e objetos de contratação. **Não baixa PDFs ou anexos, não lê termos de referência e não fornece análise de elegibilidade.** Isso ficará para etapas seguintes.
- **Cobertura:** considera 3 modalidades por padrão, não todas. As regras de termos são heurísticas e podem ter falsos positivos/negativos. Empresas devem conferir documentos e requisitos na fonte oficial antes de apresentar proposta.
- **Disponibilidade:** sujeito a instabilidade, rate limits e mudança de contrato da API. O cliente aplica tentativas limitadas para erros transitórios, sem contornar bloqueios.
- **Privacidade/segurança:** não recebe credenciais e não faz scraping de páginas autenticadas. O CSV protege contra fórmulas iniciadas por `=`, `+`, `-` ou `@`.

## Referências oficiais

- Swagger da API de Consulta: https://pncp.gov.br/api/consulta/swagger-ui/index.html
- Manual de Integração do PNCP: https://pncp.gov.br/manual/pt-br/latest/singlehtml/

## Próximas etapas (ainda não implementadas)

2. Conector de outras fontes, priorizando Compras.gov.br, com deduplicação cruzada de fontes e validação de status.
3. Download controlado de documentos de editais e triagem por IA com evidências, critérios de experiência e riscos de habilitação.
