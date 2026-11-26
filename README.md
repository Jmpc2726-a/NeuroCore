# NeuroCore - Assistente de IA para programação e estudos

O NeuroCore é uma aplicação web que funciona como assistente de código e professor particular para o ENEM: conversa com uma IA sobre um projeto, entende imagens e documentos, desenha gráficos, gera imagens e materiais de estudo, compara versões de código e acompanha tarefas automatizadas, tudo numa interface no estilo dos grandes assistentes (histórico de conversas na barra lateral).



---

## Visão geral

A interface é organizada em três áreas principais:

| Aba | Descrição |
|-----|-----------|
| Conversa | Chat com o agente, com respostas em tempo real (streaming), anexos (imagens, PDF, Word, textos e código), cartões mostrando cada ferramenta usada, gráficos e imagens na própria conversa e histórico de conversas na barra lateral. |
| Diffs | Comparação entre a versão original e a modificada (LCS linha a linha), em modo lado a lado ou unificado. Alterações feitas pelo agente podem ser abertas aqui com o botão "Ver diff". |
| Tarefas | Quadro com as colunas Backlog, Em execução, Concluído e Falhas. As tarefas são executadas de verdade pelo agente, com atualização ao vivo, e ficam salvas em disco. |

---

## Funcionalidades

**Conversa**
- Histórico de conversas na barra lateral, agrupado por data, com busca, renomear e excluir. Cada navegador vê só as próprias conversas.
- Respostas em tempo real, com fórmulas matemáticas (LaTeX via KaTeX), realce de sintaxe nos códigos, tabelas e listas.
- Editar uma mensagem enviada, gerar a resposta de novo, copiar e ouvir a resposta em voz alta; ditado por voz na caixa de mensagem (navegadores compatíveis).
- Personalização: como o NeuroCore deve te chamar, estilo das respostas (conciso, detalhado, professor) e instruções personalizadas.
- Exportar a conversa em Markdown.

**Imagens e arquivos**
- Envie imagens pelo clipe, colando um print (Ctrl+V) ou arrastando para a conversa. Os modelos com visão (Gemini, GPT-4o, Claude) leem fotos de exercícios, prints de erro, gráficos e redações manuscritas.
- Envie PDF, Word (.docx), textos e código: o conteúdo é extraído e enviado ao modelo.
- As imagens são reprocessadas no servidor (removendo metadados como a localização GPS e corrigindo a rotação).
- Visualizador de imagens em tela cheia, com download.

**Ferramentas do agente**
- `create_chart`: gráficos de funções (com raízes, vértice, assíntotas e interseções calculados) e de dados (linhas, barras, dispersão, pizza, histograma).
- `calculator`: contas exatas, porcentagens, trigonometria (graus ou radianos), logaritmos e combinatória, com notação escolar (2x, x², 15% de 200, sen, tg).
- `generate_image`: gera ou edita imagens com OpenAI (gpt-image-1), Google Gemini ou OpenRouter.
- `enem_question`: questões reais do ENEM, com as figuras da questão mostradas ao aluno e enviadas ao modelo.
- `create_document`: listas de exercícios, resumos e planos de estudo em PDF, Word ou Markdown, inclusive com os gráficos e imagens da conversa.
- Leitura, busca e (em instalação local) edição de arquivos e execução de comandos no workspace.

**Outros**
- Comparação visual de alterações de código (lado a lado e unificado).
- Quadro de tarefas ligado à API, executando o agente em segundo plano.
- Seleção de provedor e modelo na interface (lista do OpenRouter carregada automaticamente).
- Modo Local (simulado) como padrão, sem chave de API: reconhece pedidos simples (ler arquivos, contas, gráficos, questões do ENEM) para testar a interface.
- Interface escura, responsiva e acessível (atalhos: Ctrl+Shift+O nova conversa, Ctrl+K buscar conversas, / foca a mensagem, Esc para a resposta).

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

Para analisar imagens, use um modelo com visão (todos os modelos padrão acima têm). A geração de imagens usa a chave do provedor escolhido quando ele for OpenAI, Gemini ou OpenRouter, ou as chaves configuradas no servidor.

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
| `NEUROCORE_MAX_UPLOAD_BYTES` | `15728640` (15 MB) | Tamanho máximo de cada arquivo enviado. |
| `NEUROCORE_MAX_ATTACHMENTS_PER_MESSAGE` | `10` | Anexos por mensagem. |
| `NEUROCORE_MAX_IMAGES_PER_REQUEST` | `8` | Quantas imagens recentes são reenviadas ao modelo a cada chamada. |
| `NEUROCORE_ENABLE_IMAGE_GENERATION` | `true` | Liga a ferramenta de geração de imagens. |
| `NEUROCORE_OPENAI_IMAGE_MODEL` / `NEUROCORE_GEMINI_IMAGE_MODEL` / `NEUROCORE_OPENROUTER_IMAGE_MODEL` | `gpt-image-1` / `gemini-2.5-flash-image` / `google/gemini-2.5-flash-image` | Modelos usados para gerar imagens. |
| `NEUROCORE_TIMEZONE` | `America/Sao_Paulo` | Fuso usado no prompt quando o navegador não informa o seu. |
| `NEUROCORE_MAX_CONTEXT_TOKENS` | `96000` | Tamanho máximo (estimado) do histórico enviado ao modelo. |

**Atenção no Render:** o disco das instâncias gratuitas é temporário. Conversas, anexos e tarefas ficam em `NEUROCORE_WORKSPACE` e somem a cada deploy ou reinício, a menos que você adicione um disco persistente e aponte `NEUROCORE_WORKSPACE` para ele.

Chaves de API com plano gratuito podem ser geradas no Google AI Studio (https://aistudio.google.com) e no OpenRouter (https://openrouter.ai). Confira os limites atuais em cada serviço.

---

## Segurança

- A chave de API informada na interface é usada apenas na requisição ao provedor escolhido: o servidor não a grava em disco nem a compartilha com outros visitantes. No navegador ela fica só na aba atual, a menos que você marque "Lembrar a chave neste navegador". Use chaves próprias, com limite de gasto.
- Em hospedagem pública (modo público), o agente não executa comandos nem altera arquivos do servidor; ele só pode ler, listar e buscar dentro do workspace.
- Arquivos sensíveis (`.env`, `.git`, histórico de sessões, anexos) ficam bloqueados para todas as ferramentas, e os comandos de shell rodam sem as variáveis de ambiente que contêm segredos.
- Cada navegador recebe um identificador aleatório: conversas, tarefas e anexos de uma pessoa não aparecem para outra.
- Anexos são validados pelo conteúdo (não pelo nome), imagens são reescritas (sem metadados, sem SVG) e servidas por links com identificadores aleatórios de 128 bits. A interface só exibe imagens do próprio servidor, o que impede que uma resposta manipulada use imagens externas para vazar dados.
- KaTeX e highlight.js vêm do jsDelivr em versões fixas, com verificação de integridade (SRI).
- O servidor envia cabeçalhos de segurança (CSP, `X-Frame-Options`, `nosniff`), limita o tamanho das requisições e a quantidade de mensagens por minuto, e não expõe detalhes de erros internos.
- Se o servidor tiver chaves próprias configuradas e for público, defina `NEUROCORE_ACCESS_TOKEN` para que só quem tem o token consiga usar essas chaves.
- Nunca inclua chaves de API no código-fonte ou no repositório. Em ambientes de desenvolvimento, mantenha-as em variáveis de ambiente ou em um arquivo `.env` listado no `.gitignore`.
- Caso uma chave tenha sido exposta, revogue-a no painel do provedor e gere uma nova.

---

## Tecnologias

- Backend: Python, FastAPI (REST + SSE), httpx, Pillow, matplotlib, pypdf e python-docx.
- Frontend: HTML, CSS e JavaScript puros, com KaTeX e highlight.js (CDN com SRI).
- Integração com APIs de modelos de linguagem (Gemini, OpenAI, Anthropic e OpenRouter).
- Testes: pytest.
- Hospedagem: Render.

---

## Roadmap

- Streaming token a token direto dos provedores (hoje a resposta é gerada inteira e depois transmitida em partes).
- Contas de usuário (login) para acessar o mesmo histórico em vários dispositivos.
- Busca na web com proteção contra acesso à rede interna do servidor.

---

## Sobre o projeto

Projeto pessoal desenvolvido com apoio de IA durante o aprendizado, com foco em programação, integração com APIs e arquitetura de aplicações web.
