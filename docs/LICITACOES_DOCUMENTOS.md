# Licitações de software, etapa 3: análise documental PNCP

**Status:** integração experimental. Os testes usam PDFs fictícios e respostas simuladas; não foi validado download real de anexos nem resultado de provedores de IA. A ferramenta não comprova habilitação jurídica/técnica.

## O que foi implementado

`licitacoes_documentos.py` utiliza um número de controle PNCP obtido no CSV da etapa 2 e consulta a listagem de documentos de uma contratação e o download por `sequencialDocumento`. Os endpoints são documentados no [manual oficial](https://pncp.gov.br/manual/pt-br/latest/singlehtml/), usando `https://pncp.gov.br/api/pncp` como URL-base.

O código prioriza edital e termo de referência, tenta até 3 PDFs por padrão (máximo 6), extrai texto com `pypdf` e aponta indícios nas categorias técnica, atestado, prazo e risco. Todos os indícios possuem **trecho do documento e número da página**. PDFs inacessíveis ou incompatíveis aparecem em `falhas_documentos` e não são ignorados silenciosamente. PDFs digitalizados não passam por OCR e precisam de revisão.

## Instalação e execução (Windows PowerShell)

Na raiz da branch da etapa 3, com Python 3.11+:

```powershell
py -3 -m pip install -r requirements-licitacoes-documentos.txt
py -3 -m unittest discover -s tests -p "test_licitacoes_documentos.py" -v
```

Substitua o número fictício abaixo pelo número de controle PNCP de uma licitação real:

```powershell
# Apenas lista anexos
py -3 licitacoes_documentos.py listar-documentos --numero "12345678000100-1-000001/2026"

# Tenta baixar PDFs oficiais e produz relatório local
py -3 licitacoes_documentos.py analisar --numero "12345678000100-1-000001/2026"

# Se o portal fornecer PDF por outra URL, baixe-o manualmente e use arquivo local
py -3 licitacoes_documentos.py analisar --numero "12345678000100-1-000001/2026" --pdf "C:\editais\edital.pdf" --pdf "C:\editais\termo.pdf"
```

Os PDFs oficiais baixados são salvos em `dados/licitacoes_documentos/`. Relatórios completos saem em `saida/licitacoes_analises/` nos formatos JSON e Markdown. Essas pastas já são ignoradas pelo Git. Não modifica CRM, bancos existentes nem coletor da etapa 2.

## IA opcional, com autorização explícita

**Por padrão nenhum trecho é transmitido para serviços de IA.** Para incluir a interpretação de IA, configure sua chave de API no seu terminal, informe o modelo disponível no seu provedor e utilize `--autorizar-envio-ia`:

```powershell
$env:ANTHROPIC_API_KEY = "CHAVE_GERENCIADA_LOCALMENTE"
py -3 licitacoes_documentos.py analisar --numero "12345678000100-1-000001/2026" --pdf "C:\editais\edital.pdf" --ia anthropic --modelo "MODELO_DA_SUA_CONTA" --autorizar-envio-ia

# Também aceita --ia openai, com OPENAI_API_KEY e um modelo da conta OpenAI.
```

O provedor recebe até **40 trechos** com seus IDs e páginas, não o PDF inteiro. Pode haver cobrança e retenção de dados pelo provedor. A resposta é aceita somente se referenciar evidências realmente extraídas e respeitar suas categorias. Mesmo assim, toda observação da IA é **hipótese para revisão**, e nunca confirmação de exigência, de equivalência de atestados, de prazo vigente ou de habilitação.

## Limitações e cuidados

- Apenas PDFs (até 12 MiB e 100 páginas). Não abre ZIP, DOCX ou anexos escaneados por OCR.
- Download estritamente pelo domínio/endpoints oficiais PNCP, sem seguir redirecionamentos para hosts desconhecidos. Caso o PNCP redirecione arquivos, use `--pdf` após baixá-los manualmente.
- Apenas os primeiros 3 (máximo 6) documentos prioritários são tentados; não representa leitura integral de todos os anexos.
- Páginas em imagens, tabelas mal extraídas, erros de OCR, textos interrompidos ou versões retificadas podem gerar lacunas. **Ausência de evidência não significa ausência de exigência.**
- Verifique nos portais oficiais a data/hora da sessão, suspensões, retificações, critérios completos, exigências de experiência e compatibilidade dos atestados da empresa. Revise com profissional técnico/jurídico antes de propor preço ou participar.
- Nenhum login, envio de mensagem, proposta ou inscrição é executado por esta extensão.

## Documentação oficial

- [Consultar documentos da contratação](https://pncp.gov.br/manual/pt-br/latest/contratacao/consultar_todos_documentos_de_uma_contratacao.html)
- [Baixar documento](https://pncp.gov.br/manual/pt-br/latest/contratacao/baixar_documento_de_uma_contratacao.html)
