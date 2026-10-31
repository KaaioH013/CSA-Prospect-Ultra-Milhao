# Provedores de IA: como escolher o cérebro

A CSA Prospecção usa **o mesmo banco, auditoria e validação de evidências** independentemente do modelo. O provedor de IA somente escolhe uma evidência pré-definida, o estilo e a pergunta final. **O Python escreve o rascunho com frases factuais aprovadas.** Nenhum provedor recebe o HTML completo, e-mails de leads ou a lista de contatos. A saída sempre passa pela mesma validação antes de entrar no SQLite.

## Modo simples no Windows

Abra `DIAGNOSTICAR_IA_WINDOWS.bat`. O menu oferece:

1. Regras Python sem IA e sem custos de API.
2. Ollama local (`qwen3:1.7b` ou outro modelo que você instalou).
3. OpenAI API (Responses API).
4. Endpoint compatível com OpenAI Chat Completions (OpenRouter, DeepSeek, Groq, LM Studio, Together, vLLM etc., quando o provedor suporta esse formato).
5. Anthropic Claude API.
6. Gemini API.
7. Codex CLI com login oficial na conta ChatGPT, se seu plano tiver acesso.

O menu pede o modelo, um limite pequeno por padrão (**3 diagnósticos**) e uma confirmação antes de usar serviços remotos. As chaves, quando necessárias, são solicitadas em campo oculto, permanecem apenas na memória do processo e não são gravadas no banco, CSV ou projeto. Também é possível fornecer a chave previamente como `LLM_API_KEY` ou variável própria do provedor.

## Exemplos no terminal

Os comandos abaixo apenas geram **rascunhos** e nunca enviam e-mails. **Cada exemplo pressupõe auditorias já registradas no banco.**

Sem IA, totalmente grátis:

```powershell
py -3 diagnosticos.py diagnosticar --modo regras --limite 20
```

IA local:

```powershell
ollama pull qwen3:1.7b
py -3 diagnosticos.py diagnosticar --modo ollama --modelo qwen3:1.7b --limite 20
```

OpenAI: `OPENAI_API_KEY` ou `LLM_API_KEY` no ambiente, sem salvar a chave em arquivo. O modelo é um exemplo, dependendo da disponibilidade na sua conta.

```powershell
py -3 diagnosticos.py diagnosticar --modo api --provedor openai --modelo gpt-4.1-mini --limite 3 --pedir-chave
```

Compatível com OpenAI: substitua a URL, o modelo e a chave conforme seu provedor.

```powershell
py -3 diagnosticos.py diagnosticar --modo api --provedor compativel --base-url https://api.deepseek.com/v1 --modelo deepseek-chat --limite 3 --pedir-chave
```

LM Studio rodando no seu PC, com endpoint local compatível e sem chave:

```powershell
py -3 diagnosticos.py diagnosticar --modo api --provedor compativel --base-url http://127.0.0.1:1234/v1 --modelo SEU_MODELO_CARREGADO --limite 3
```

Claude API:

```powershell
py -3 diagnosticos.py diagnosticar --modo api --provedor anthropic --modelo SEU_MODELO_CLAUDE --limite 3 --pedir-chave
```

Gemini API:

```powershell
py -3 diagnosticos.py diagnosticar --modo api --provedor gemini --modelo SEU_MODELO_GEMINI --limite 3 --pedir-chave
```

Codex CLI, aproveitando o login oficial na conta do ChatGPT:

```powershell
codex login
py -3 diagnosticos.py verificar-codex
py -3 diagnosticos.py diagnosticar --modo codex --limite 3
```

Se quiser um modelo específico autorizado pelo seu plano, passe `--modelo MODELO`. Sem essa opção, o Codex CLI utiliza o modelo padrão da sua configuração. O Codex roda com `--sandbox read-only`, `--ephemeral`, diretório temporário e JSON schema. Não copiamos tokens de autenticação ou arquivos internos de login. O Codex pode consumir a franquia do ChatGPT/Work/Codex e **não significa API ilimitada gratuita**. Em geral, o Codex é mais pesado e lento para esta classificação do que um modelo pequeno por API.

Para exportar diagnósticos depois de qualquer modo:

```powershell
py -3 diagnosticos.py exportar
```

## Economia e segurança

- Nunca processe 10 mil diagnósticos sem testar um lote pequeno primeiro.
- O sistema evita nova inferência quando a assinatura da auditoria, o modelo e o provedor continuam iguais. `--forcar` ignora esse cache e pode aumentar custos.
- Não há retries automáticos de API paga para evitar consumo duplicado em erros de rede ambíguos.
- APIs compatíveis são integradas pelo endpoint `/chat/completions`; nem todas aceitam `max_tokens`, prompts em formato de chat ou o mesmo nome de modelo. Adaptações específicas podem ser necessárias.
- OpenAI usa `/responses`; Anthropic usa `/messages`; Gemini usa `:generateContent`. As APIs podem mudar e os modelos variam de acordo com a conta.
- Endpoints remotos exigem HTTPS. HTTP só é permitido em `localhost`/`127.0.0.1`/`::1`. O sistema não segue redirecionamentos de API.
- A saída do LLM é tratada como não confiável, não executa comandos e não pode introduzir evidências que não constam na auditoria.
- A chave é enviada somente ao provedor escolhido. Confira a política de dados e os preços antes de utilizar APIs externas.
- `codex login` é autenticação oficial do **Codex CLI**, diferente de usar uma API key OpenAI. Ter assinatura ChatGPT não disponibiliza gratuitamente tokens em toda API externa. Para integração OAuth direta em apps, existe a documentação [Sign in with ChatGPT](https://developers.openai.com/siwc/quickstart), mas essa implementação ainda não está incluída aqui.

## Testes

```powershell
py -3 -m unittest discover -s tests -v
```

Os testes exercitam cada adaptador com **mocks**, sem sair para a internet e sem consumirem tokens. A inferência real com conta de usuário, um modelo específico ou uma instalação do Codex CLI ainda precisa ser validada no computador do operador.


## Claude CLI com login (sem API key no projeto)

Instale o Claude Code CLI conforme a documentação oficial, execute `claude auth login` no terminal e confira com `claude auth status`. No painel, em **Conexões de IA**, selecione **Claude CLI** e deixe o modelo em branco para usar o padrão da instalação. Alternativamente:

```powershell
py -3 diagnosticos.py verificar-claude
py -3 diagnosticos.py diagnosticar --modo claude --limite 3
```

O código usa `claude -p --output-format json --tools "" --max-turns 1`, em diretório temporário, sem disponibilizar o SQLite ao agente. Requer login compatível com a assinatura `claude.ai`; não copia nem lê tokens locais. O uso está sujeito aos limites da Anthropic.

## Dashboard de conexões

Abra `ABRIR_PAINEL_WINDOWS.bat`, entre em **Conexões de IA**, selecione o provedor, informe o ID exato do modelo e, se necessário, o endpoint HTTPS e chave da API. Para conexões por API, o botão **Verificar conexão** realiza **uma solicitação real** que pode ter custo. Para Codex/Claude, apenas consulta o estado da CLI, sem inferência. Para qualquer diagnóstico remoto o painel exige confirmação. A chave digitada não é armazenada.
