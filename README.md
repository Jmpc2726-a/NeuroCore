# NeuroCore - Assistente de Código com IA

O NeuroCore é uma aplicação web que funciona como assistente de código: permite conversar com uma IA sobre um projeto, comparar versões de código lado a lado e acompanhar tarefas automatizadas em um único painel.



---

## Visão geral

A interface é organizada em três áreas principais:

| Aba | Descrição |
|-----|-----------|
| Conversa | Chat com o agente, com respostas em tempo real (streaming), cartões mostrando cada ferramenta usada (ler arquivo, buscar, rodar comando) e histórico que sobrevive a um recarregamento da página. |
| Diffs | Comparação entre a versão original e a modificada (LCS linha a linha), em modo lado a lado ou unificado. Alterações feitas pelo agente podem ser abertas aqui com o botão "Ver diff". |
| Tarefas | Quadro com as colunas Backlog, Em execução, Concluído e Falhas. As tarefas são executadas de verdade pelo agente, com atualização ao vivo, e ficam salvas em disco. |

---

## Funcionalidades

- Chat com agente de código integrado a um workspace, com streaming e cartões de ferramentas.
- Busca de questões reais do ENEM e geração de documentos (PDF, Word, Markdown) para download.
- Comparação visual de alterações de código (lado a lado e unificado).
- Quadro de tarefas ligado à API, executando o agente em segundo plano.
- Seleção de provedor e modelo na interface (lista de modelos do OpenRouter carregada automaticamente).
- Modo Local (simulado) como padrão, sem necessidade de chave de API.
- Interface escura, responsiva e acessível (teclado, leitores de tela).

---

## Provedores de IA suportados

| Provedor | Modelo | Chave de API |
|----------|--------|--------------|
| Local (simulado) | simulated-local-v1 | Não necessária |
| Google Gemini | gemini-2.5-flash (padrão), gemini-2.5-pro | Necessária |
| OpenAI | gpt-4o (padrão), gpt-4.1 | Necessária |
| Anthropic | claude-opus-5-5 (padrão), claude-sonnet-5-5, claude-haiku-5-5 | Necessária |
| OpenRouter | Vários, incluindo modelos gratuitos | Necessária |

Também é possível digitar o nome de qualquer outro modelo do provedor ("Outro (digitar nome)...").

O modo Local Offline é o padrão e permite testar a interface sem configurar nenhum serviço externo. As respostas desse modo são simuladas e não representam um modelo de linguagem real.

---

## Como rodar

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn neurocore.server.app:app --host 127.0.0.1 --port 8000
```

Depois abra http://127.0.0.1:8000. Para rodar os testes: `python -m pytest`.

Para publicar (por exemplo no Render), use como comando de início:

```bash
uvicorn neurocore.server.app:app --host 0.0.0.0 --port $PORT
```

## Como usar

1. Para usar o modo padrão, comece a conversar diretamente pela aba Conversa.
2. Para usar um modelo de IA real, clique no botão de conexão (canto superior direito), escolha o provedor e o modelo, informe a sua chave de API e clique em Salvar conexão.
3. Na aba Diffs, cole a versão original e a modificada e clique em Comparar.
4. Na aba Tarefas, crie uma tarefa com título e instrução e clique em Executar; o resultado aparece em "Ver resultado".

## Configuração (variáveis de ambiente)

Copie `.env.example` para `.env` e ajuste. Principais opções:

| Variável | Padrão | Para que serve |
|----------|--------|----------------|
| `GEMINI_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY` | vazio | Chaves do servidor. Sem elas, cada usuário informa a própria chave na interface. |
| `NEUROCORE_WORKSPACE` | diretório atual | Pasta que o agente pode ler (e alterar, se permitido). |
| `NEUROCORE_ACCESS_TOKEN` | vazio | Se definido, a API exige este token (campo "Token de acesso" na interface). Recomendado em servidores públicos. |
| `NEUROCORE_PUBLIC_MODE` | `true` no Render, `false` local | Liga o modo público (desativa ferramentas perigosas e a documentação `/docs`). |
| `NEUROCORE_ENABLE_DANGEROUS_TOOLS` | `false` no modo público | Libera `run_command`, `write_file` e `replace_file_content`. |
| `NEUROCORE_CORS_ORIGINS` | `http://127.0.0.1:8000,http://localhost:8000` | Origens externas permitidas (separadas por vírgula). |
| `NEUROCORE_RATE_LIMIT_PER_MINUTE` | `30` | Mensagens/execuções por minuto por IP (`0` desliga). |
| `NEUROCORE_DEFAULT_PROVIDER` | `local` | Provedor padrão. |

Chaves de API com plano gratuito podem ser geradas no Google AI Studio (https://aistudio.google.com) e no OpenRouter (https://openrouter.ai). Confira os limites atuais em cada serviço.

---

## Segurança

- A chave de API informada na interface é usada apenas na requisição ao provedor escolhido: o servidor não a grava em disco nem a compartilha com outros visitantes. No navegador ela fica só na aba atual, a menos que você marque "Lembrar a chave neste navegador". Use chaves próprias, com limite de gasto.
- Em hospedagem pública (modo público), o agente não executa comandos nem altera arquivos do servidor; ele só pode ler, listar e buscar dentro do workspace.
- Arquivos sensíveis (`.env`, `.git`, histórico de sessões) ficam bloqueados para todas as ferramentas, e os comandos de shell rodam sem as variáveis de ambiente que contêm segredos.
- O servidor envia cabeçalhos de segurança (CSP, `X-Frame-Options`, `nosniff`), limita o tamanho das requisições e a quantidade de mensagens por minuto, e não expõe detalhes de erros internos.
- Se o servidor tiver chaves próprias configuradas e for público, defina `NEUROCORE_ACCESS_TOKEN` para que só quem tem o token consiga usar essas chaves.
- Nunca inclua chaves de API no código-fonte ou no repositório. Em ambientes de desenvolvimento, mantenha-as em variáveis de ambiente ou em um arquivo `.env` listado no `.gitignore`.
- Caso uma chave tenha sido exposta, revogue-a no painel do provedor e gere uma nova.

---

## Tecnologias

- Backend: Python, FastAPI (REST + SSE) e httpx.
- Frontend: HTML, CSS e JavaScript puros, sem dependências externas.
- Integração com APIs de modelos de linguagem (Gemini, OpenAI, Anthropic e OpenRouter).
- Testes: pytest.
- Hospedagem: Render.

---

## Roadmap

- Streaming token a token direto dos provedores (hoje a resposta é gerada inteira e depois transmitida em partes).
- Listar e retomar conversas antigas pela interface.
- Contas de usuário com chaves e histórico separados.

---

## Sobre o projeto

Projeto pessoal desenvolvido com apoio de IA durante o aprendizado, com foco em programação, integração com APIs e arquitetura de aplicações web.
