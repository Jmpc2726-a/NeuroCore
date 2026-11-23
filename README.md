# NeuroCore - Assistente de Código com IA

O NeuroCore é uma aplicação web que funciona como assistente de código: permite conversar com uma IA sobre um projeto, comparar versões de código lado a lado e acompanhar tarefas automatizadas em um único painel.

**Demonstração online:** https://neurocore-lpyp.onrender.com/

> A demonstração está hospedada no plano gratuito do Render. Após um período sem acessos, o servidor entra em modo de espera e o primeiro carregamento pode levar cerca de 1 minuto.

---

## Visão geral

A interface é organizada em três áreas principais:

| Aba | Descrição |
|-----|-----------|
| Conversa | Chat com o agente de código, com sugestões rápidas de comandos (ler arquivos de configuração, executar testes com pytest, buscar definições de funções com grep e criar scripts de teste). |
| Diffs de Código | Comparação entre a versão original e a versão modificada, usando o algoritmo Myers LCS, com visualização Split (lado a lado) e Unified (linha a linha). |
| Tarefas | Painel com as colunas Backlog, Em Execução, Concluído e Falhas, para criar tarefas com instruções para o agente e acompanhar o progresso. |

---

## Funcionalidades

- Chat com agente de código integrado a um workspace.
- Comparação visual de alterações de código com algoritmo Myers LCS.
- Dois modos de visualização de diffs: Split e Unified.
- Painel de tarefas com quatro estados (Backlog, Em Execução, Concluído e Falhas) e criação de novas tarefas por título e instrução.
- Seleção de provedor de IA e de modelo diretamente na interface.
- Modo Local Offline como padrão, sem necessidade de chave de API.
- Interface responsiva com tema Nature.

---

## Provedores de IA suportados

| Provedor | Modelo | Chave de API |
|----------|--------|--------------|
| Local Offline | offline-simulated | Não necessária |
| Google Gemini | gemini-2.5-flash | Necessária |
| OpenAI | gpt-4o | Necessária |
| Anthropic | claude-3-5-sonnet | Necessária |
| OpenRouter | Vários, incluindo modelos gratuitos | Necessária |

O modo Local Offline é o padrão e permite testar a interface sem configurar nenhum serviço externo. As respostas desse modo são simuladas e não representam um modelo de linguagem real.

---

## Como usar

1. Acesse a demonstração online.
2. Para usar o modo padrão, comece a conversar diretamente pela aba Conversa.
3. Para usar um modelo de IA real, abra a janela de configuração de conexão, escolha o provedor e o modelo, informe a sua chave de API e clique em Salvar Conexão.
4. Na aba Diffs de Código, cole a versão original e a versão modificada e gere a comparação visual.
5. Na aba Tarefas, crie uma nova tarefa com título e instrução e acompanhe o andamento nas colunas do painel.

Chaves de API com plano gratuito podem ser geradas no Google AI Studio (https://aistudio.google.com) e no OpenRouter (https://openrouter.ai). Confira os limites atuais em cada serviço.

---

## Segurança

- A chave de API informada na interface é usada para realizar as requisições ao provedor escolhido. Utilize chaves próprias, com limite de gasto configurado, e evite informar chaves sensíveis em instâncias públicas.
- Nunca inclua chaves de API no código-fonte ou no repositório. Em ambientes de desenvolvimento, mantenha-as em variáveis de ambiente ou em um arquivo `.env` listado no `.gitignore`.
- Caso uma chave tenha sido exposta, revogue-a no painel do provedor e gere uma nova.

---

## Tecnologias

- Frontend: HTML, CSS e JavaScript.
- Integração com APIs de modelos de linguagem (Gemini, OpenAI, Anthropic e OpenRouter).
- Hospedagem: Render.

---

## Roadmap

- Documentar em detalhes o funcionamento do modo Local Offline.
- Adicionar testes automatizados.
- Melhorar o tratamento de erros nas chamadas às APIs.
- Persistir o histórico de conversas.

---

## Sobre o projeto

Projeto pessoal desenvolvido com apoio de IA durante o aprendizado, com foco em programação, integração com APIs e arquitetura de aplicações web.
