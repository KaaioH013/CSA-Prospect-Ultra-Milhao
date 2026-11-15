# Licitações de software, etapa 2: PNCP + Compras.gov.br

**Estado:** integração experimental. A leitura dos endpoints públicos ainda não foi testada ao vivo neste ambiente. Os testes automatizados usam respostas JSON locais, não dependem de contas e não enviam propostas.

## Fontes oficiais

- PNCP: `https://pncp.gov.br/api/consulta/v1/contratacoes/publicacao` (etapa 1, `licitacoes_pncp.py`).
- Compras.gov.br: `https://dadosabertos.compras.gov.br/modulo-contratacoes/1_consultarContratacoes_PNCP_14133` (etapa 2, `licitacoes_multifonte.py`).
- Documentação: [Swagger Compras.gov.br](https://dadosabertos.compras.gov.br/swagger-ui/index.html) e [Manual da API](https://manual-api-compras.readthedocs.io/pt-br/latest/versao-impressao.html).

**Códigos de modalidade diferentes:** PNCP utiliza, por padrão, `4 6 8` (concorrência eletrônica, pregão eletrônico, dispensa). A consulta Compras.gov.br utiliza `3 5 6` (concorrência, pregão, dispensa), conforme tabela do próprio Compras.gov.br. Não copie os códigos de um endpoint para outro.

## Usar no Windows

Execute a partir da raiz do projeto (Python 3.11+):

```powershell
# Verificação offline: teste as duas etapas sem Internet ou custos de API
py -3 -m unittest discover -s tests -p "test_licitacoes*.py" -v

# Consultar as duas APIs por data de publicação, sem gravar nada
py -3 licitacoes_pncp.py coletar --dias 1 --simular
py -3 licitacoes_multifonte.py coletar --dias 1 --simular

# Coleta real de um dia, Brasil inteiro, com deduplicação por número PNCP
py -3 licitacoes_pncp.py coletar --dias 1
py -3 licitacoes_multifonte.py coletar --dias 1

# CSV consolidado de todas as licitações únicas já armazenadas
py -3 licitacoes_multifonte.py exportar

# SP apenas, último período de sete dias, por fonte
py -3 licitacoes_pncp.py coletar --dias 7 --uf SP --modalidades 4 6 8
py -3 licitacoes_multifonte.py coletar --dias 7 --uf SP --modalidades 3 5 6
py -3 licitacoes_multifonte.py exportar --somente-prazo-futuro
```

### Armazenamento

As duas fontes usam o mesmo banco `dados/licitacoes_pncp.sqlite3`, preservando a tabela `licitacoes_pncp` da etapa 1 e criando **somente** `licitacoes_comprasgov` e `licitacoes_comprasgov_exclusoes` para a segunda fonte. O CSV unificado fica em `saida/licitacoes_multifonte.csv`.

- Uma única linha por `numeroControlePNCP` em caso de duplicatas entre fontes.
- Campo `fontes`: `PNCP`, `Compras.gov.br` ou `PNCP + Compras.gov.br`.
- Campos preenchidos no PNCP têm precedência; valores ausentes podem ser completados pelo Compras.gov.br.
- Se o Compras.gov.br marcar explicitamente um registro como excluído, ele deixa de aparecer no CSV, mesmo que o PNCP ainda mantenha um registro antigo. A exclusão é registrada em tabela própria, que pode ser revertida se o registro voltar ativo numa nova consulta.
- Sem número de controle PNCP, o registro não entra no consolidado, para evitar falsos agrupamentos. Isso pode gerar perdas de cobertura, reportadas na execução.
- O CSV protege contra fórmulas de planilha em campos de texto.

## Limites e riscos

- **Não indica licitação aberta nem habilitação confirmada.** Os filtros de data consideram a data de publicação, e `--somente-prazo-futuro` só verifica uma data, não o status atual, retificações ou a hora da sessão.
- O Compras.gov.br contém registros associados ao PNCP da administração federal. Não equivale a uma segunda cobertura integral de licitações municipais/estaduais. Cruzar ambas as fontes aumenta a possibilidade de enriquecimento, mas pode não aumentar muito o total de oportunidades únicas.
- O sistema **não lê PDFs, anexos, termos de referência ou exigências de atestados** nesta etapa. Isso exige outro módulo de extração e análise, com links oficiais e revisão humana.
- Se ocorrer erro ou estourar `--max-paginas`, o coletor da respectiva fonte **não salva parcialmente** os registros novos dessa execução. Como cada coletor grava sua própria tabela, uma execução PNCP bem-sucedida não é revertida se, depois, o Compras.gov.br falhar.
- Registros já armazenados podem ficar desatualizados caso sejam cancelados fora da janela de publicação consultada. Revalide os status diretamente nas páginas oficiais antes de qualquer proposta.
- Não existe agendamento automático, integração com o dashboard legado ou publicação na branch `main` nesta etapa.
- Sem autenticação, sem API paga, sem automação de cadastro/propostas. A API oficial do Compras.gov.br indica limite geral de **100 chamadas por minuto por IP**; o código inclui intervalos entre páginas e tentativas limitadas para erros 429/5xx (via transporte compartilhado).

## Próxima etapa prevista (não implementada)

Leitura de documentos e análise das exigências de participação, prazos e atestados equivalentes para serviços de software, com evidência do edital e revisão humana obrigatória.