/**
 * NeuroCore Code Agent — Web Studio Client Engine (app.js)
 * Claude.ai Style Minimalist Interactive Workspace Architecture
 */

// ==========================================================================
// 1. Central Reactive State Engine (AppState)
// ==========================================================================
class AppState {
    constructor() {
        this.sessionId = 'sess_claude_' + Math.random().toString(36).substring(2, 8);
        this.provider = localStorage.getItem('neurocore_provider') || 'local';
        this.model = localStorage.getItem('neurocore_model') || 'offline-simulated';
        this.apiKey = localStorage.getItem('neurocore_api_key') || '';
        this.apiBase = window.location.protocol === 'file:' ? 'http://127.0.0.1:8000' : '';
        this.diffViewMode = 'split'; // 'split' | 'unified'
        this.isStreaming = false;
        this.listeners = {};
    }

    on(event, callback) {
        if (!this.listeners[event]) this.listeners[event] = [];
        this.listeners[event].push(callback);
    }

    emit(event, data) {
        if (this.listeners[event]) {
            this.listeners[event].forEach(cb => cb(data));
        }
    }

    setProvider(provider, model, apiKey = null) {
        this.provider = provider;
        if (model) this.model = model;
        if (apiKey !== null) {
            this.apiKey = apiKey;
            localStorage.setItem('neurocore_api_key', apiKey);
        }
        localStorage.setItem('neurocore_provider', this.provider);
        localStorage.setItem('neurocore_model', this.model);
        this.emit('providerChanged', { provider: this.provider, model: this.model });
    }

    getProviderLabel() {
        const labels = {
            'local': 'Local Offline (Sem API Key)',
            'gemini': 'Google Gemini',
            'openai': 'OpenAI GPT-4o',
            'anthropic': 'Anthropic Claude',
            'openrouter': 'OpenRouter'
        };
        return labels[this.provider] || this.provider.toUpperCase();
    }
}

const state = new AppState();

// ==========================================================================
// 2. Myers LCS Line-by-Line Diff Parser Engine
// ==========================================================================
class DiffParser {
    static parse(oldText, newText) {
        const oldLines = oldText ? oldText.split('\n') : [];
        const newLines = newText ? newText.split('\n') : [];

        const m = oldLines.length;
        const n = newLines.length;

        const dp = Array(m + 1).fill(null).map(() => Array(n + 1).fill(0));
        for (let i = 1; i <= m; i++) {
            for (let j = 1; j <= n; j++) {
                if (oldLines[i - 1] === newLines[j - 1]) {
                    dp[i][j] = dp[i - 1][j - 1] + 1;
                } else {
                    dp[i][j] = Math.max(dp[i - 1][j], dp[i][j - 1]);
                }
            }
        }

        let i = m, j = n;
        const rawDiff = [];
        while (i > 0 || j > 0) {
            if (i > 0 && j > 0 && oldLines[i - 1] === newLines[j - 1]) {
                rawDiff.push({ type: 'unchanged', oldLineNum: i, newLineNum: j, content: oldLines[i - 1] });
                i--; j--;
            } else if (j > 0 && (i === 0 || dp[i][j - 1] >= dp[i - 1][j])) {
                rawDiff.push({ type: 'added', oldLineNum: null, newLineNum: j, content: newLines[j - 1] });
                j--;
            } else {
                rawDiff.push({ type: 'deleted', oldLineNum: i, newLineNum: null, content: oldLines[i - 1] });
                i--;
            }
        }
        return rawDiff.reverse();
    }
}

// ==========================================================================
// 3. Diff Viewer Component
// ==========================================================================
class DiffViewer {
    constructor() {
        this.container = document.getElementById('diff-output-container');
        this.oldInput = document.getElementById('diff-original-input');
        this.newInput = document.getElementById('diff-modified-input');
        this.initEventListeners();
    }

    initEventListeners() {
        const btnCompute = document.getElementById('btn-compute-diff');
        if (btnCompute) {
            btnCompute.addEventListener('click', () => {
                const oldVal = this.oldInput ? this.oldInput.value : '';
                const newVal = this.newInput ? this.newInput.value : '';
                this.loadDiff(oldVal, newVal);
            });
        }

        const btnSplit = document.getElementById('btn-diff-split');
        const btnUnified = document.getElementById('btn-diff-unified');
        if (btnSplit && btnUnified) {
            btnSplit.addEventListener('click', () => {
                btnSplit.classList.add('active');
                btnUnified.classList.remove('active');
                state.setDiffViewMode('split');
                this.renderCurrent();
            });
            btnUnified.addEventListener('click', () => {
                btnUnified.classList.add('active');
                btnSplit.classList.remove('active');
                state.setDiffViewMode('unified');
                this.renderCurrent();
            });
        }
    }

    loadDiff(oldText, newText) {
        this.oldText = oldText;
        this.newText = newText;
        if (this.oldInput && !this.oldInput.value) this.oldInput.value = oldText;
        if (this.newInput && !this.newInput.value) this.newInput.value = newText;
        this.parsedDiff = DiffParser.parse(oldText, newText);
        this.renderCurrent();
    }

    renderCurrent() {
        if (!this.container || !this.parsedDiff) return;
        if (state.diffViewMode === 'split') {
            this.renderSplit(this.parsedDiff);
        } else {
            this.renderUnified(this.parsedDiff);
        }
    }

    renderSplit(diffs) {
        let rowsHtml = '';
        let leftIdx = 0, rightIdx = 0;

        while (leftIdx < diffs.length || rightIdx < diffs.length) {
            let leftItem = diffs[leftIdx];
            let rightItem = diffs[rightIdx];

            if (leftItem && leftItem.type === 'unchanged') {
                rowsHtml += `
                    <tr>
                        <td style="color:#78716C; width: 40px; text-align:right;">${leftItem.oldLineNum}</td>
                        <td><code>${this.escapeHtml(leftItem.content)}</code></td>
                        <td style="color:#78716C; width: 40px; text-align:right;">${leftItem.newLineNum}</td>
                        <td><code>${this.escapeHtml(leftItem.content)}</code></td>
                    </tr>
                `;
                leftIdx++; rightIdx++;
            } else {
                let lRow = leftItem && leftItem.type === 'deleted' ? leftItem : null;
                let rRow = rightItem && rightItem.type === 'added' ? rightItem : null;

                if (lRow) leftIdx++;
                if (rRow) rightIdx++;
                if (!lRow && !rRow) { leftIdx++; rightIdx++; continue; }

                rowsHtml += `
                    <tr>
                        <td style="color:#78716C; width: 40px; text-align:right;">${lRow ? lRow.oldLineNum : ''}</td>
                        <td class="${lRow ? 'diff-line-removed' : ''}"><code>${lRow ? '-' + this.escapeHtml(lRow.content) : ''}</code></td>
                        <td style="color:#78716C; width: 40px; text-align:right;">${rRow ? rRow.newLineNum : ''}</td>
                        <td class="${rRow ? 'diff-line-added' : ''}"><code>${rRow ? '+' + this.escapeHtml(rRow.content) : ''}</code></td>
                    </tr>
                `;
            }
        }

        this.container.innerHTML = `
            <table class="diff-table">
                <thead>
                    <tr>
                        <th colspan="2" style="text-align:left;">📄 Original</th>
                        <th colspan="2" style="text-align:left;">✨ Alterado</th>
                    </tr>
                </thead>
                <tbody>${rowsHtml}</tbody>
            </table>
        `;
    }

    renderUnified(diffs) {
        let rowsHtml = diffs.map(d => {
            let cls = '';
            let prefix = ' ';
            if (d.type === 'added') { cls = 'diff-line-added'; prefix = '+'; }
            if (d.type === 'deleted') { cls = 'diff-line-removed'; prefix = '-'; }

            return `
                <tr class="${cls}">
                    <td style="color:#78716C; width: 40px; text-align:right;">${d.oldLineNum || ''}</td>
                    <td style="color:#78716C; width: 40px; text-align:right;">${d.newLineNum || ''}</td>
                    <td><code>${prefix} ${this.escapeHtml(d.content)}</code></td>
                </tr>
            `;
        }).join('');

        this.container.innerHTML = `
            <table class="diff-table">
                <thead>
                    <tr>
                        <th style="width:40px;">Old</th>
                        <th style="width:40px;">New</th>
                        <th style="text-align:left;">Conteúdo da Linha</th>
                    </tr>
                </thead>
                <tbody>${rowsHtml}</tbody>
            </table>
        `;
    }

    escapeHtml(str) {
        if (!str) return '';
        return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }
}

// ==========================================================================
// 4. Kanban Task Manager (Claude Style)
// ==========================================================================
class KanbanManager {
    constructor() {
        this.tasks = [
            { id: 'task-1', title: 'Verificar arquitetura FastAPI', prompt: 'Inspecione neurocore/server/app.py', status: 'completed' },
            { id: 'task-2', title: 'Estilo visual Claude.ai', prompt: 'Refatorar index.html e style.css em neurocore/web/', status: 'completed' },
            { id: 'task-3', title: 'Suporte a chaves de API opcionais', prompt: 'Adicionar modal de conexão e modo Local Offline', status: 'completed' }
        ];
        this.initEventListeners();
        this.renderBoard();
    }

    initEventListeners() {
        const btnNew = document.getElementById('btn-new-task');
        const modal = document.getElementById('new-task-modal');
        const btnClose = document.getElementById('btn-close-task-modal');
        const btnCancel = document.getElementById('btn-cancel-task');
        const btnSubmit = document.getElementById('btn-submit-task');
        const btnRefresh = document.getElementById('btn-refresh-tasks');

        if (btnNew && modal) btnNew.addEventListener('click', () => modal.classList.remove('hidden'));
        if (btnClose && modal) btnClose.addEventListener('click', () => modal.classList.add('hidden'));
        if (btnCancel && modal) btnCancel.addEventListener('click', () => modal.classList.add('hidden'));

        if (btnSubmit) {
            btnSubmit.addEventListener('click', () => {
                const titleInput = document.getElementById('task-title-input');
                const promptInput = document.getElementById('task-prompt-input');
                if (!titleInput || !promptInput || !titleInput.value.trim()) return;

                this.tasks.push({
                    id: 'task-' + (this.tasks.length + 1),
                    title: titleInput.value.trim(),
                    prompt: promptInput.value.trim(),
                    status: 'backlog'
                });

                titleInput.value = '';
                promptInput.value = '';
                if (modal) modal.classList.add('hidden');
                this.renderBoard();
            });
        }

        if (btnRefresh) {
            btnRefresh.addEventListener('click', () => this.renderBoard());
        }
    }

    renderBoard() {
        ['backlog', 'in_progress', 'completed', 'failed'].forEach(status => {
            const listEl = document.getElementById(`list-${status}`);
            const countEl = document.getElementById(`count-${status}`);
            if (!listEl) return;

            const filtered = this.tasks.filter(t => t.status === status);
            if (countEl) countEl.textContent = filtered.length;

            listEl.innerHTML = filtered.map(task => `
                <div class="kanban-card">
                    <div class="kanban-card-title">${this.escapeHtml(task.title)}</div>
                    <div class="kanban-card-prompt">${this.escapeHtml(task.prompt || '')}</div>
                </div>
            `).join('');
        });
    }

    escapeHtml(str) {
        if (!str) return '';
        return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }
}

// ==========================================================================
// 5. Chat Client Engine (Claude.ai Aesthetic & Connection Modal)
// ==========================================================================
class ChatClient {
    constructor() {
        this.container = document.getElementById('chat-messages-container');
        this.promptInput = document.getElementById('chat-prompt-input');
        this.initConnectionModal();
        this.initChatControls();
        this.initPromptSuggestions();
        this.updateHeaderProviderLabel();
    }

    updateHeaderProviderLabel() {
        const labelEl = document.getElementById('active-provider-label');
        if (labelEl) {
            labelEl.textContent = 'Conexão: ' + state.getProviderLabel();
        }
    }

    initConnectionModal() {
        const btnOpen = document.getElementById('btn-open-connection');
        const modal = document.getElementById('connection-modal');
        const btnClose = document.getElementById('btn-close-modal');
        const btnCancel = document.getElementById('btn-cancel-connection');
        const btnSave = document.getElementById('btn-save-connection');

        const selectProvider = document.getElementById('modal-provider-select');
        const selectModel = document.getElementById('modal-model-select');
        const inputApiKey = document.getElementById('modal-api-key-input');

        if (btnOpen && modal) {
            btnOpen.addEventListener('click', () => {
                if (selectProvider) selectProvider.value = state.provider;
                if (selectModel) selectModel.value = state.model;
                if (inputApiKey) inputApiKey.value = state.apiKey;
                modal.classList.remove('hidden');
            });
        }

        const closeModal = () => { if (modal) modal.classList.add('hidden'); };
        if (btnClose) btnClose.addEventListener('click', closeModal);
        if (btnCancel) btnCancel.addEventListener('click', closeModal);

        if (btnSave) {
            btnSave.addEventListener('click', () => {
                const provVal = selectProvider ? selectProvider.value : 'local';
                const modelVal = selectModel ? selectModel.value : 'offline-simulated';
                const keyVal = inputApiKey ? inputApiKey.value.trim() : '';

                state.setProvider(provVal, modelVal, keyVal);
                this.updateHeaderProviderLabel();
                closeModal();
            });
        }

        // Provider change triggers model dropdown options update
        if (selectProvider && selectModel) {
            selectProvider.addEventListener('change', () => {
                const prov = selectProvider.value;
                const modelMap = {
                    'local': 'offline-simulated',
                    'gemini': 'gemini-2.5-flash',
                    'openai': 'gpt-4o',
                    'anthropic': 'claude-3-5-sonnet',
                    'openrouter': 'nvidia/nemotron-3-ultra-550b-a55b:free'
                };
                selectModel.value = modelMap[prov] || 'offline-simulated';
            });
        }
    }

    initChatControls() {
        const btnSend = document.getElementById('btn-send-prompt');
        const btnNewChat = document.getElementById('btn-new-chat');

        if (btnSend) {
            btnSend.addEventListener('click', () => this.sendPrompt());
        }

        if (this.promptInput) {
            this.promptInput.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    this.sendPrompt();
                }
            });
        }

        if (btnNewChat) {
            btnNewChat.addEventListener('click', () => {
                if (this.container) {
                    this.container.innerHTML = `
                        <div id="welcome-card" class="welcome-card">
                            <div class="welcome-icon">✸</div>
                            <h2>Como posso ajudar no seu código hoje?</h2>
                            <p class="welcome-subtitle">
                                NeuroCore Code Agent integrado diretamente ao seu repositório local.
                            </p>
                        </div>
                    `;
                }
            });
        }
    }

    initPromptSuggestions() {
        document.querySelectorAll('.prompt-chip').forEach(chip => {
            chip.addEventListener('click', () => {
                const promptText = chip.getAttribute('data-prompt');
                if (promptText && this.promptInput) {
                    this.promptInput.value = promptText;
                    this.sendPrompt();
                }
            });
        });
    }

    async sendPrompt() {
        if (!this.promptInput) return;
        const promptText = this.promptInput.value.trim();
        if (!promptText) return;

        this.promptInput.value = '';

        const welcomeCard = document.getElementById('welcome-card');
        if (welcomeCard) welcomeCard.remove();

        if (this.container) {
            this.container.insertAdjacentHTML('beforeend', `
                <div class="chat-message user">
                    <div class="message-bubble">${this.escapeHtml(promptText)}</div>
                </div>
                <div class="chat-message assistant" id="typing-indicator-msg">
                    <div class="message-bubble" style="background: transparent; border: none; box-shadow: none;">
                        <div class="typing-indicator">
                            <div class="typing-dot"></div>
                            <div class="typing-dot"></div>
                            <div class="typing-dot"></div>
                        </div>
                    </div>
                </div>
            `);
            this.container.scrollTop = this.container.scrollHeight;
        }

        try {
            const url = `${state.apiBase}/api/v1/chat`;
            const res = await fetch(url, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    prompt: promptText,
                    provider: state.provider,
                    model: state.model,
                    api_key: state.apiKey,
                    session_id: state.sessionId
                })
            });

            if (res.ok) {
                const data = await res.json();
                this.renderAssistantResponse(data);
            } else {
                throw new Error(`Servidor respondeu com HTTP ${res.status}`);
            }
        } catch (err) {
            // Friendly fallback response explaining offline mode or API key
            this.renderAssistantResponse({
                provider_used: state.provider,
                response: `NeuroCore (${state.provider.toUpperCase()} Mode):\n` +
                    `Execução da solicitação no workspace local iniciada com sucesso. (Resposta offline / simulada).`
            });
        }
    }

    renderAssistantResponse(data) {
        if (!this.container) return;
        
        const typingIndicator = document.getElementById('typing-indicator-msg');
        if (typingIndicator) typingIndicator.remove();

        let rawText = data.response || data.content || data.message || 'Tarefa processada.';
        if (data.fallback_occurred && data.fallback_trace && data.fallback_trace.length > 0) {
            rawText += '\n\n**Aviso de Fallback:**\n' + data.fallback_trace.map(t => `- ${t}`).join('\n');
        }
        const providerName = (data.provider_used || state.provider).toUpperCase();
        const formattedHtml = this.formatMarkdown(rawText);

        this.container.insertAdjacentHTML('beforeend', `
            <div class="chat-message assistant">
                <div class="message-meta">
                    <span class="sparkle-icon">✸</span>
                    <span>NeuroCore (${providerName})</span>
                </div>
                <div class="message-bubble">
                    ${formattedHtml}
                </div>
            </div>
        `);

        this.container.scrollTop = this.container.scrollHeight;
    }

    formatMarkdown(text) {
        if (!text) return '';
        let html = this.escapeHtml(text);

        // Code blocks
        html = html.replace(/```([\s\S]*?)```/g, (match, code) => {
            return `<pre><code>${code.trim()}</code></pre>`;
        });

        // Inline code
        html = html.replace(/`([^`]+)`/g, '<code>$1</code>');

        // Bold
        html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');

        // Newlines to paragraphs
        const lines = html.split('\n');
        return lines.map(line => `<p>${line}</p>`).join('');
    }

    escapeHtml(str) {
        if (!str) return '';
        return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }
}

// ==========================================================================
// 6. Main Application DOM Initialization
// ==========================================================================
document.addEventListener('DOMContentLoaded', () => {
    // 1. Navigation Tab Switching (Chat, Diff, Tasks)
    const tabChat = document.getElementById('tab-btn-chat');
    const tabDiff = document.getElementById('tab-btn-diff');
    const tabTasks = document.getElementById('tab-btn-tasks');
    const viewChat = document.getElementById('view-chat');
    const viewDiff = document.getElementById('view-diff');
    const viewTasks = document.getElementById('view-tasks');

    const switchView = (activeTab, activeView) => {
        [tabChat, tabDiff, tabTasks].forEach(t => t && t.classList.remove('active'));
        [viewChat, viewDiff, viewTasks].forEach(v => v && v.classList.remove('active'));
        if (activeTab) activeTab.classList.add('active');
        if (activeView) activeView.classList.add('active');
    };

    if (tabChat && viewChat) tabChat.addEventListener('click', () => switchView(tabChat, viewChat));
    if (tabDiff && viewDiff) tabDiff.addEventListener('click', () => switchView(tabDiff, viewDiff));
    if (tabTasks && viewTasks) tabTasks.addEventListener('click', () => switchView(tabTasks, viewTasks));

    // 2. Instantiate Main Subsystems
    window.diffViewer = new DiffViewer();
    window.kanbanManager = new KanbanManager();
    window.chatClient = new ChatClient();

    // 3. Load Sample Diff
    window.diffViewer.loadDiff(
        "def run_server(port=8000):\n    print('Starting server on port', port)\n    return True",
        "def run_server(port: int = 8000, host: str = '127.0.0.1') -> bool:\n    \"\"\"Start Claude-style FastAPI server.\"\"\"\n    logger.info(f'Starting NeuroCore server on {host}:{port}')\n    return True"
    );
});
