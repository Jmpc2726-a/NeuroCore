/**
 * NeuroCore — Web Studio (app.js)
 *
 * Chat com streaming (SSE), visualizador de diffs, quadro de tarefas ligado
 * à API e configuração de conexão. Todo conteúdo vindo do usuário, do modelo
 * ou de serviços externos é escapado antes de entrar no DOM.
 */
(function () {
    'use strict';

    // ======================================================================
    // Utilidades
    // ======================================================================
    const API = '/api/v1';
    const API_BASE = window.location.protocol === 'file:' ? 'http://127.0.0.1:8000' : '';

    const store = {
        get(area, key) {
            try { return window[area].getItem(key); } catch (e) { return null; }
        },
        set(area, key, value) {
            try {
                if (value === null || value === undefined || value === '') window[area].removeItem(key);
                else window[area].setItem(key, value);
            } catch (e) { /* armazenamento indisponível */ }
        },
    };

    function escapeHtml(str) {
        return String(str == null ? '' : str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined && text !== null) node.textContent = text;
        return node;
    }

    function newId() {
        if (window.crypto && typeof window.crypto.randomUUID === 'function') return window.crypto.randomUUID();
        const bytes = new Uint8Array(16);
        (window.crypto || window.msCrypto).getRandomValues(bytes);
        return Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
    }

    function toast(message, type) {
        const container = document.getElementById('toast-container');
        if (!container) return;
        const node = el('div', 'toast' + (type ? ' ' + type : ''), message);
        node.setAttribute('role', type === 'error' ? 'alert' : 'status');
        container.appendChild(node);
        setTimeout(() => node.remove(), type === 'error' ? 7000 : 4000);
    }

    function truncate(text, max) {
        const s = String(text == null ? '' : text);
        return s.length > max ? s.slice(0, max) + '…' : s;
    }

    // ======================================================================
    // Estado
    // ======================================================================
    const PROVIDERS = {
        local: { label: 'Local (simulado)', models: [['simulated-local-v1', 'Simulado local']] },
        gemini: {
            label: 'Google Gemini',
            models: [['gemini-2.5-flash', 'Gemini 2.5 Flash'], ['gemini-2.5-pro', 'Gemini 2.5 Pro'], ['gemini-2.5-flash-lite', 'Gemini 2.5 Flash-Lite']],
        },
        openai: {
            label: 'OpenAI',
            models: [['gpt-4o', 'GPT-4o'], ['gpt-4o-mini', 'GPT-4o mini'], ['gpt-4.1', 'GPT-4.1'], ['gpt-4.1-mini', 'GPT-4.1 mini']],
        },
        anthropic: {
            label: 'Anthropic Claude',
            models: [['claude-opus-5-5', 'Claude Opus 5.5'], ['claude-sonnet-5-5', 'Claude Sonnet 5.5'], ['claude-haiku-5-5', 'Claude Haiku 5.5']],
        },
        openrouter: {
            label: 'OpenRouter',
            models: [['nvidia/nemotron-3-ultra-550b-a55b:free', 'Nemotron 3 Ultra (grátis)']],
            dynamic: true,
        },
    };
    const CUSTOM_MODEL = '__custom__';

    class AppState {
        constructor() {
            this.provider = store.get('localStorage', 'neurocore_provider') || 'local';
            if (!PROVIDERS[this.provider]) this.provider = 'local';
            this.model = store.get('localStorage', 'neurocore_model') || PROVIDERS[this.provider].models[0][0];
            if (this.provider === 'local') this.model = 'simulated-local-v1';

            // A chave fica na aba (sessionStorage) por padrão; só vai para o
            // localStorage se o usuário pedir para lembrar.
            this.rememberKey = store.get('localStorage', 'neurocore_remember_key') === '1' ||
                !!store.get('localStorage', 'neurocore_api_key');
            this.apiKey = (this.rememberKey ? store.get('localStorage', 'neurocore_api_key') : null) ||
                store.get('sessionStorage', 'neurocore_api_key') || '';
            this.accessToken = store.get('localStorage', 'neurocore_access_token') || '';

            this.sessionId = store.get('sessionStorage', 'neurocore_session_id') || newId();
            store.set('sessionStorage', 'neurocore_session_id', this.sessionId);

            this.diffViewMode = 'split';
            this.isStreaming = false;
            this.abortController = null;
            this.health = null;
            this.listeners = {};
        }

        on(event, cb) { (this.listeners[event] = this.listeners[event] || []).push(cb); }
        emit(event, data) { (this.listeners[event] || []).forEach(cb => cb(data)); }

        setConnection({ provider, model, apiKey, remember, accessToken }) {
            this.provider = PROVIDERS[provider] ? provider : 'local';
            this.model = model || PROVIDERS[this.provider].models[0][0];
            this.apiKey = apiKey || '';
            this.rememberKey = !!remember;
            store.set('localStorage', 'neurocore_provider', this.provider);
            store.set('localStorage', 'neurocore_model', this.model);
            store.set('localStorage', 'neurocore_remember_key', this.rememberKey ? '1' : null);
            if (this.rememberKey) {
                store.set('localStorage', 'neurocore_api_key', this.apiKey);
                store.set('sessionStorage', 'neurocore_api_key', null);
            } else {
                store.set('sessionStorage', 'neurocore_api_key', this.apiKey);
                store.set('localStorage', 'neurocore_api_key', null);
            }
            if (accessToken !== undefined) {
                this.accessToken = accessToken || '';
                store.set('localStorage', 'neurocore_access_token', this.accessToken);
            }
            this.emit('connectionChanged');
        }

        setDiffViewMode(mode) {
            this.diffViewMode = mode === 'unified' ? 'unified' : 'split';
        }

        newSession() {
            this.sessionId = newId();
            store.set('sessionStorage', 'neurocore_session_id', this.sessionId);
        }

        providerLabel() {
            return (PROVIDERS[this.provider] || PROVIDERS.local).label;
        }

        serverHasKey(provider) {
            const p = this.health && this.health.providers && this.health.providers[provider];
            return !!(p && p.configured);
        }

        /** Corpo comum das requisições ao agente. */
        agentPayload() {
            const body = { provider: this.provider, model: this.model };
            if (this.provider !== 'local' && this.apiKey) body.api_key = this.apiKey;
            return body;
        }

        authHeaders() {
            return this.accessToken ? { 'X-NeuroCore-Token': this.accessToken } : {};
        }
    }

    const state = new AppState();

    class ApiError extends Error {
        constructor(message, status) { super(message); this.status = status; }
    }

    async function readError(res) {
        let detail = '';
        try {
            const data = await res.json();
            if (typeof data.detail === 'string') detail = data.detail;
            else if (Array.isArray(data.detail)) detail = data.detail.map(d => d.msg).join('; ');
            else if (data.error) detail = data.error;
        } catch (e) { /* corpo não é JSON */ }
        if (res.status === 401) detail = detail || 'Token de acesso inválido ou ausente.';
        if (res.status === 429) detail = detail || 'Muitas requisições. Aguarde um pouco.';
        return new ApiError(detail || `O servidor respondeu com HTTP ${res.status}.`, res.status);
    }

    async function api(path, options = {}) {
        const headers = Object.assign({}, state.authHeaders(), options.headers || {});
        const init = { method: options.method || 'GET', headers, signal: options.signal };
        if (options.body !== undefined) {
            headers['Content-Type'] = 'application/json';
            init.body = JSON.stringify(options.body);
        }
        let res;
        try {
            res = await fetch(API_BASE + path, init);
        } catch (err) {
            if (err && err.name === 'AbortError') throw err;
            throw new ApiError('Não foi possível conectar ao servidor do NeuroCore.', 0);
        }
        if (options.raw) {
            if (!res.ok) throw await readError(res);
            return res;
        }
        if (!res.ok) {
            const error = await readError(res);
            if (error.status === 401) state.emit('unauthorized');
            throw error;
        }
        return res.status === 204 ? null : res.json();
    }

    /** Lê uma resposta text/event-stream e chama onEvent(tipo, dados). */
    async function readSSE(response, onEvent) {
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        const flush = (chunk) => {
            let event = 'message';
            const dataLines = [];
            chunk.split('\n').forEach(line => {
                if (line.startsWith('event:')) event = line.slice(6).trim();
                else if (line.startsWith('data:')) dataLines.push(line.slice(5).replace(/^ /, ''));
            });
            if (!dataLines.length) return;
            let data;
            try { data = JSON.parse(dataLines.join('\n')); } catch (e) { data = { raw: dataLines.join('\n') }; }
            onEvent(event, data);
        };
        for (;;) {
            const { value, done } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n');
            let idx;
            while ((idx = buffer.indexOf('\n\n')) !== -1) {
                flush(buffer.slice(0, idx));
                buffer = buffer.slice(idx + 2);
            }
        }
        if (buffer.trim()) flush(buffer);
    }

    // ======================================================================
    // Markdown seguro (escapa tudo e só então aplica a formatação)
    // ======================================================================
    const Markdown = {
        safeUrl(raw) {
            const url = raw.replace(/&amp;/g, '&');
            if (/^https?:\/\//i.test(url) || (/^\/(?!\/)/.test(url)) || url.startsWith('#')) return raw;
            return null;
        },

        inline(text) {
            const slots = [];
            const hold = (html) => { slots.push(html); return `\u0001${slots.length - 1}\u0001`; };
            let s = escapeHtml(text);

            s = s.replace(/`([^`\n]+)`/g, (m, code) => hold(`<code>${code}</code>`));
            s = s.replace(/\[([^\]\n]+)\]\(\s*([^)\s]+)\s*\)/g, (m, label, url) => {
                const safe = Markdown.safeUrl(url);
                if (!safe) return label;
                const external = /^https?:/i.test(safe);
                return hold(`<a href="${safe}"${external ? ' target="_blank" rel="noopener noreferrer"' : ''}>${label}</a>`);
            });
            s = s.replace(/(^|[\s(])(https?:\/\/[^\s<]+[^\s<.,;:!?)])/g, (m, pre, url) =>
                pre + hold(`<a href="${url}" target="_blank" rel="noopener noreferrer">${url}</a>`));
            s = s.replace(/\*\*([^*\n]+?)\*\*/g, '<strong>$1</strong>');
            s = s.replace(/__([^_\n]+?)__/g, '<strong>$1</strong>');
            s = s.replace(/(^|[^*\w])\*([^*\n]+?)\*(?!\*)/g, '$1<em>$2</em>');
            s = s.replace(/(^|\s)_([^_\n]+?)_(?=$|[\s.,;:!?)])/g, '$1<em>$2</em>');
            s = s.replace(/~~([^~\n]+?)~~/g, '<del>$1</del>');
            return s.replace(/\u0001(\d+)\u0001/g, (m, i) => slots[Number(i)]);
        },

        codeBlock(lang, code) {
            const label = lang ? escapeHtml(lang) : 'código';
            return `<div class="code-block"><div class="code-block-header"><span>${label}</span>` +
                `<button class="btn-copy" type="button" data-copy>Copiar</button></div>` +
                `<pre><code>${escapeHtml(code)}</code></pre></div>`;
        },

        render(src) {
            if (!src) return '';
            const codes = [];
            let text = String(src).replace(/\r\n/g, '\n');
            text = text.replace(/```[ \t]*([\w+#.-]*)[^\n]*\n([\s\S]*?)(?:\n```[ \t]*(?=\n|$)|$)/g, (m, lang, code) => {
                codes.push(Markdown.codeBlock(lang, code));
                return `\n\u0000${codes.length - 1}\u0000\n`;
            });

            const lines = text.split('\n');
            const out = [];
            let para = [];
            const flushPara = () => {
                if (para.length) out.push(`<p>${para.map(l => Markdown.inline(l)).join('<br>')}</p>`);
                para = [];
            };
            const isTableSep = (line) => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(line);
            const splitRow = (line) => line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(c => c.trim());

            for (let i = 0; i < lines.length; i++) {
                const line = lines[i];
                const code = line.match(/^\u0000(\d+)\u0000$/);
                if (code) { flushPara(); out.push(codes[Number(code[1])]); continue; }
                if (!line.trim()) { flushPara(); continue; }

                const heading = line.match(/^(#{1,6})\s+(.*)$/);
                if (heading) {
                    flushPara();
                    const level = Math.min(heading[1].length + 1, 4);
                    out.push(`<h${level}>${Markdown.inline(heading[2])}</h${level}>`);
                    continue;
                }
                if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) { flushPara(); out.push('<hr>'); continue; }

                if (line.includes('|') && i + 1 < lines.length && isTableSep(lines[i + 1])) {
                    flushPara();
                    const head = splitRow(line);
                    const rows = [];
                    i += 2;
                    while (i < lines.length && lines[i].includes('|') && lines[i].trim()) { rows.push(splitRow(lines[i])); i++; }
                    i--;
                    out.push('<table><thead><tr>' + head.map(c => `<th>${Markdown.inline(c)}</th>`).join('') + '</tr></thead><tbody>' +
                        rows.map(r => '<tr>' + r.map(c => `<td>${Markdown.inline(c)}</td>`).join('') + '</tr>').join('') +
                        '</tbody></table>');
                    continue;
                }

                const listMatch = line.match(/^\s*([-*+]|\d+[.)])\s+(.*)$/);
                if (listMatch) {
                    flushPara();
                    const ordered = /\d/.test(listMatch[1]);
                    const start = ordered ? parseInt(listMatch[1], 10) : 1;
                    const items = [];
                    while (i < lines.length) {
                        const m = lines[i].match(/^\s*([-*+]|\d+[.)])\s+(.*)$/);
                        if (!m || /\d/.test(m[1]) !== ordered) break;
                        items.push(m[2]);
                        i++;
                    }
                    i--;
                    const tag = ordered ? 'ol' : 'ul';
                    const startAttr = ordered && start !== 1 ? ` start="${start}"` : '';
                    out.push(`<${tag}${startAttr}>` + items.map(it => `<li>${Markdown.inline(it)}</li>`).join('') + `</${tag}>`);
                    continue;
                }

                if (/^>\s?/.test(line)) {
                    flushPara();
                    const quote = [];
                    while (i < lines.length && /^>\s?/.test(lines[i])) { quote.push(lines[i].replace(/^>\s?/, '')); i++; }
                    i--;
                    out.push(`<blockquote>${quote.map(q => Markdown.inline(q)).join('<br>')}</blockquote>`);
                    continue;
                }

                para.push(line);
            }
            flushPara();
            return out.join('');
        },
    };

    // ======================================================================
    // Diff (LCS linha a linha)
    // ======================================================================
    const MAX_DIFF_CELLS = 4000000;

    const DiffParser = {
        parse(oldText, newText) {
            const a = oldText ? oldText.replace(/\r\n/g, '\n').split('\n') : [];
            const b = newText ? newText.replace(/\r\n/g, '\n').split('\n') : [];

            // Prefixo e sufixo comuns reduzem muito o custo do LCS.
            let start = 0;
            while (start < a.length && start < b.length && a[start] === b[start]) start++;
            let endA = a.length, endB = b.length;
            while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) { endA--; endB--; }

            const result = [];
            for (let k = 0; k < start; k++) result.push({ type: 'unchanged', oldLineNum: k + 1, newLineNum: k + 1, content: a[k] });

            const midA = a.slice(start, endA), midB = b.slice(start, endB);
            const m = midA.length, n = midB.length;
            if ((m + 1) * (n + 1) > MAX_DIFF_CELLS) {
                throw new Error('Os textos são grandes demais para comparar no navegador.');
            }
            const w = n + 1;
            const dp = new Uint32Array((m + 1) * (n + 1));
            for (let i = m - 1; i >= 0; i--) {
                for (let j = n - 1; j >= 0; j--) {
                    dp[i * w + j] = midA[i] === midB[j] ? dp[(i + 1) * w + j + 1] + 1 : Math.max(dp[(i + 1) * w + j], dp[i * w + j + 1]);
                }
            }
            let i = 0, j = 0;
            while (i < m || j < n) {
                if (i < m && j < n && midA[i] === midB[j]) {
                    result.push({ type: 'unchanged', oldLineNum: start + i + 1, newLineNum: start + j + 1, content: midA[i] });
                    i++; j++;
                } else if (i < m && (j >= n || dp[(i + 1) * w + j] >= dp[i * w + j + 1])) {
                    result.push({ type: 'deleted', oldLineNum: start + i + 1, newLineNum: null, content: midA[i] });
                    i++;
                } else {
                    result.push({ type: 'added', oldLineNum: null, newLineNum: start + j + 1, content: midB[j] });
                    j++;
                }
            }
            for (let k = 0; k < a.length - endA; k++) {
                result.push({ type: 'unchanged', oldLineNum: endA + k + 1, newLineNum: endB + k + 1, content: a[endA + k] });
            }
            return result;
        },

        /** Reconstrói as duas versões a partir de um diff unificado (saída das ferramentas). */
        fromUnified(diffText) {
            const oldLines = [], newLines = [];
            String(diffText || '').split('\n').forEach(line => {
                if (line.startsWith('---') || line.startsWith('+++') || line.startsWith('@@')) return;
                if (line.startsWith('+')) newLines.push(line.slice(1));
                else if (line.startsWith('-')) oldLines.push(line.slice(1));
                else if (line.startsWith(' ')) { oldLines.push(line.slice(1)); newLines.push(line.slice(1)); }
            });
            return { oldText: oldLines.join('\n'), newText: newLines.join('\n') };
        },
    };

    class DiffViewer {
        constructor() {
            this.container = document.getElementById('diff-output-container');
            this.oldInput = document.getElementById('diff-original-input');
            this.newInput = document.getElementById('diff-modified-input');
            this.stats = document.getElementById('diff-stats');
            this.parsed = null;
            this.bind();
        }

        bind() {
            document.getElementById('btn-compute-diff').addEventListener('click', () => this.compute());
            document.getElementById('btn-clear-diff').addEventListener('click', () => {
                this.oldInput.value = '';
                this.newInput.value = '';
                this.parsed = null;
                this.stats.textContent = '';
                this.container.innerHTML = '<div class="empty-state">Cole as duas versões e clique em Comparar.</div>';
            });
            const split = document.getElementById('btn-diff-split');
            const unified = document.getElementById('btn-diff-unified');
            const setMode = (mode) => {
                state.setDiffViewMode(mode);
                split.classList.toggle('active', mode === 'split');
                unified.classList.toggle('active', mode === 'unified');
                split.setAttribute('aria-pressed', String(mode === 'split'));
                unified.setAttribute('aria-pressed', String(mode === 'unified'));
                this.render();
            };
            split.addEventListener('click', () => setMode('split'));
            unified.addEventListener('click', () => setMode('unified'));
            [this.oldInput, this.newInput].forEach(t => t.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); this.compute(); }
            }));
        }

        compute() {
            this.load(this.oldInput.value, this.newInput.value, false);
        }

        load(oldText, newText, fillInputs = true) {
            if (fillInputs) {
                this.oldInput.value = oldText;
                this.newInput.value = newText;
            }
            try {
                this.parsed = DiffParser.parse(oldText, newText);
            } catch (err) {
                this.parsed = null;
                this.container.innerHTML = `<div class="empty-state">${escapeHtml(err.message)}</div>`;
                return;
            }
            const added = this.parsed.filter(d => d.type === 'added').length;
            const removed = this.parsed.filter(d => d.type === 'deleted').length;
            this.stats.innerHTML = `<span class="add">+${added}</span> <span class="del">−${removed}</span>`;
            this.render();
        }

        render() {
            if (!this.parsed) return;
            if (!this.parsed.length) {
                this.container.innerHTML = '<div class="empty-state">Nada para comparar.</div>';
                return;
            }
            if (!this.parsed.some(d => d.type !== 'unchanged')) {
                this.container.innerHTML = '<div class="empty-state">As duas versões são idênticas.</div>';
                return;
            }
            this.container.innerHTML = state.diffViewMode === 'split' ? this.renderSplit(this.parsed) : this.renderUnified(this.parsed);
        }

        renderSplit(diffs) {
            const rows = [];
            let k = 0;
            while (k < diffs.length) {
                const d = diffs[k];
                if (d.type === 'unchanged') {
                    const c = escapeHtml(d.content);
                    rows.push(`<tr><td class="ln">${d.oldLineNum}</td><td>${c}</td><td class="ln">${d.newLineNum}</td><td>${c}</td></tr>`);
                    k++;
                    continue;
                }
                const dels = [], adds = [];
                while (k < diffs.length && diffs[k].type !== 'unchanged') {
                    (diffs[k].type === 'deleted' ? dels : adds).push(diffs[k]);
                    k++;
                }
                for (let r = 0; r < Math.max(dels.length, adds.length); r++) {
                    const l = dels[r], a = adds[r];
                    rows.push('<tr>' +
                        `<td class="ln">${l ? l.oldLineNum : ''}</td><td class="${l ? 'diff-line-removed' : ''}">${l ? '- ' + escapeHtml(l.content) : ''}</td>` +
                        `<td class="ln">${a ? a.newLineNum : ''}</td><td class="${a ? 'diff-line-added' : ''}">${a ? '+ ' + escapeHtml(a.content) : ''}</td>` +
                        '</tr>');
                }
            }
            return '<div class="diff-table-wrap"><table class="diff-table"><colgroup><col style="width:52px"><col><col style="width:52px"><col></colgroup>' +
                '<thead><tr><th colspan="2">Original</th><th colspan="2">Alterado</th></tr></thead>' +
                `<tbody>${rows.join('')}</tbody></table></div>`;
        }

        renderUnified(diffs) {
            const rows = diffs.map(d => {
                let cls = '', prefix = '  ';
                if (d.type === 'added') { cls = 'diff-line-added'; prefix = '+ '; }
                if (d.type === 'deleted') { cls = 'diff-line-removed'; prefix = '- '; }
                return `<tr class="${cls}"><td class="ln">${d.oldLineNum || ''}</td><td class="ln">${d.newLineNum || ''}</td><td>${prefix}${escapeHtml(d.content)}</td></tr>`;
            }).join('');
            return '<div class="diff-table-wrap"><table class="diff-table"><colgroup><col style="width:52px"><col style="width:52px"><col></colgroup>' +
                '<thead><tr><th>Antes</th><th>Depois</th><th>Linha</th></tr></thead>' +
                `<tbody>${rows}</tbody></table></div>`;
        }
    }

    // ======================================================================
    // Modais
    // ======================================================================
    const Modals = {
        lastFocus: null,
        open(id) {
            const modal = document.getElementById(id);
            if (!modal) return;
            this.lastFocus = document.activeElement;
            modal.classList.remove('hidden');
            const focusable = modal.querySelector('input:not(.hidden), select, textarea, button:not([data-close-modal])');
            if (focusable) setTimeout(() => focusable.focus(), 30);
        },
        close(modal) {
            if (typeof modal === 'string') modal = document.getElementById(modal);
            if (!modal) return;
            modal.classList.add('hidden');
            if (this.lastFocus && this.lastFocus.focus) this.lastFocus.focus();
        },
        init() {
            document.querySelectorAll('.modal-backdrop').forEach(modal => {
                modal.addEventListener('click', (e) => {
                    if (e.target === modal || e.target.closest('[data-close-modal]')) this.close(modal);
                });
            });
            document.addEventListener('keydown', (e) => {
                if (e.key !== 'Escape') return;
                const open = Array.from(document.querySelectorAll('.modal-backdrop:not(.hidden)')).pop();
                if (open) this.close(open);
            });
        },
    };

    // ======================================================================
    // Conexão (provedor, modelo, chave, token)
    // ======================================================================
    class ConnectionPanel {
        constructor() {
            this.form = document.getElementById('connection-form');
            this.provider = document.getElementById('modal-provider-select');
            this.model = document.getElementById('modal-model-select');
            this.customModel = document.getElementById('modal-model-custom');
            this.apiKey = document.getElementById('modal-api-key-input');
            this.remember = document.getElementById('modal-remember-key');
            this.token = document.getElementById('modal-access-token');
            this.keyGroup = document.getElementById('api-key-group');
            this.tokenGroup = document.getElementById('access-token-group');
            this.serverHint = document.getElementById('provider-server-hint');
            this.openRouterModels = null;
            this.bind();
            this.updateHeader();
            state.on('connectionChanged', () => this.updateHeader());
            state.on('healthChanged', () => this.updateHeader());
        }

        bind() {
            document.getElementById('btn-open-connection').addEventListener('click', () => this.open());
            this.provider.addEventListener('change', () => this.fillModels(this.provider.value, null));
            this.model.addEventListener('change', () => {
                this.customModel.classList.toggle('hidden', this.model.value !== CUSTOM_MODEL);
                if (this.model.value === CUSTOM_MODEL) this.customModel.focus();
            });
            document.getElementById('btn-toggle-key').addEventListener('click', (e) => {
                const show = this.apiKey.type === 'password';
                this.apiKey.type = show ? 'text' : 'password';
                e.currentTarget.textContent = show ? 'Ocultar' : 'Mostrar';
            });
            this.form.addEventListener('submit', (e) => {
                e.preventDefault();
                this.save();
            });
        }

        open() {
            this.provider.value = state.provider;
            this.fillModels(state.provider, state.model);
            this.apiKey.value = state.apiKey;
            this.apiKey.type = 'password';
            this.remember.checked = state.rememberKey;
            this.token.value = state.accessToken;
            this.tokenGroup.classList.toggle('hidden', !(state.health && state.health.auth_required) && !state.accessToken);
            Modals.open('connection-modal');
        }

        async fillModels(provider, selected) {
            const info = PROVIDERS[provider] || PROVIDERS.local;
            this.keyGroup.classList.toggle('hidden', provider === 'local');
            if (provider === 'local') {
                this.serverHint.textContent = 'Respostas simuladas, sem IA real. Útil para testar a interface.';
                this.serverHint.classList.remove('ok');
            } else if (state.serverHasKey(provider)) {
                this.serverHint.textContent = 'Este servidor já tem uma chave para este provedor; informar a sua é opcional.';
                this.serverHint.classList.add('ok');
            } else {
                this.serverHint.textContent = 'Informe sua chave de API abaixo para usar este provedor.';
                this.serverHint.classList.remove('ok');
            }

            let models = info.models.slice();
            if (info.dynamic) {
                this.setOptions([['', 'Carregando modelos...']], '');
                models = await this.loadOpenRouterModels(models);
                if (this.provider.value !== provider) return; // usuário trocou no meio
            }
            const value = selected || models[0][0];
            const known = models.some(m => m[0] === value);
            this.setOptions(models.concat([[CUSTOM_MODEL, 'Outro (digitar nome)...']]), known ? value : CUSTOM_MODEL);
            this.customModel.value = known ? '' : (value || '');
            this.customModel.classList.toggle('hidden', known);
        }

        setOptions(models, selected) {
            this.model.textContent = '';
            models.forEach(([value, label]) => {
                const opt = document.createElement('option');
                opt.value = value;
                opt.textContent = label;
                this.model.appendChild(opt);
            });
            this.model.value = selected;
        }

        async loadOpenRouterModels(defaults) {
            if (this.openRouterModels) return this.openRouterModels;
            try {
                const res = await fetch('https://openrouter.ai/api/v1/models');
                const data = await res.json();
                const list = (data && Array.isArray(data.data) ? data.data : [])
                    .filter(m => m && typeof m.id === 'string' && /^[\w.\-/:]+$/.test(m.id))
                    .map(m => {
                        const free = /:free$/.test(m.id);
                        return [m.id, truncate((m.name || m.id) + (free ? ' — grátis' : ''), 90), free];
                    })
                    .sort((x, y) => (y[2] - x[2]) || x[1].localeCompare(y[1]))
                    .map(m => [m[0], m[1]]);
                this.openRouterModels = list.length ? list : defaults;
            } catch (e) {
                toast('Não foi possível carregar a lista de modelos do OpenRouter.', 'warning');
                this.openRouterModels = defaults;
            }
            return this.openRouterModels;
        }

        save() {
            const provider = this.provider.value;
            let model = this.model.value === CUSTOM_MODEL ? this.customModel.value.trim() : this.model.value;
            if (!model) {
                toast('Escolha ou digite um modelo.', 'warning');
                return;
            }
            const apiKey = this.apiKey.value.trim();
            if (provider !== 'local' && !apiKey && !state.serverHasKey(provider)) {
                toast('Sem chave de API, as mensagens cairão no modo local simulado.', 'warning');
            }
            state.setConnection({
                provider,
                model,
                apiKey,
                remember: this.remember.checked,
                accessToken: this.token.value.trim(),
            });
            Modals.close('connection-modal');
            toast('Conexão salva.');
            checkHealth();
        }

        updateHeader() {
            const label = document.getElementById('active-provider-label');
            const dot = document.getElementById('status-dot');
            label.textContent = state.providerLabel() + (state.provider === 'local' ? '' : ' · ' + truncate(state.model, 28));
            dot.className = 'status-dot';
            if (!state.health) {
                dot.classList.add('offline');
                dot.parentElement.title = 'Servidor indisponível';
            } else if (state.provider === 'local' || state.apiKey || state.serverHasKey(state.provider)) {
                dot.classList.add('online');
                dot.parentElement.title = 'Conectado';
            } else {
                dot.classList.add('warning');
                dot.parentElement.title = 'Falta a chave de API deste provedor';
            }
        }
    }

    // ======================================================================
    // Chat
    // ======================================================================
    class ChatClient {
        constructor(diffViewer) {
            this.diffViewer = diffViewer;
            this.container = document.getElementById('chat-messages-container');
            this.input = document.getElementById('chat-prompt-input');
            this.form = document.getElementById('chat-form');
            this.sendBtn = document.getElementById('btn-send-prompt');
            this.stopBtn = document.getElementById('btn-stop');
            const welcome = document.getElementById('welcome-card');
            this.welcomeTemplate = welcome ? welcome.cloneNode(true) : null;
            this.bind();
            this.restoreHistory();
        }

        bind() {
            this.form.addEventListener('submit', (e) => {
                e.preventDefault();
                this.send(this.input.value);
            });
            this.input.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
                    e.preventDefault();
                    this.send(this.input.value);
                }
            });
            this.input.addEventListener('input', () => this.autoResize());
            this.stopBtn.addEventListener('click', () => {
                if (state.abortController) state.abortController.abort();
            });
            document.getElementById('btn-new-chat').addEventListener('click', () => this.newChat());
            this.container.addEventListener('click', (e) => {
                const chip = e.target.closest('.prompt-chip');
                if (chip) {
                    this.send(chip.getAttribute('data-prompt') || chip.textContent);
                    return;
                }
                const copy = e.target.closest('[data-copy]');
                if (copy) {
                    const pre = copy.closest('.code-block').querySelector('pre');
                    this.copy(pre ? pre.textContent : '', copy);
                    return;
                }
                const openDiff = e.target.closest('[data-open-diff]');
                if (openDiff && openDiff._diff) {
                    const { oldText, newText } = DiffParser.fromUnified(openDiff._diff);
                    this.diffViewer.load(oldText, newText, true);
                    switchView('view-diff');
                }
            });
        }

        async copy(text, button) {
            try {
                await navigator.clipboard.writeText(text);
                button.textContent = 'Copiado!';
            } catch (e) {
                button.textContent = 'Falhou';
            }
            setTimeout(() => { button.textContent = 'Copiar'; }, 1500);
        }

        autoResize() {
            this.input.style.height = 'auto';
            this.input.style.height = Math.min(this.input.scrollHeight, 200) + 'px';
        }

        nearBottom() {
            const c = this.container;
            return c.scrollHeight - c.scrollTop - c.clientHeight < 120;
        }

        scrollToBottom(force) {
            if (force || this.nearBottom()) this.container.scrollTop = this.container.scrollHeight;
        }

        removeWelcome() {
            const w = this.container.querySelector('#welcome-card');
            if (w) w.remove();
        }

        newChat() {
            if (state.abortController) state.abortController.abort();
            state.newSession();
            this.container.textContent = '';
            if (this.welcomeTemplate) this.container.appendChild(this.welcomeTemplate.cloneNode(true));
            this.input.value = '';
            this.autoResize();
            this.input.focus();
        }

        setStreaming(on) {
            state.isStreaming = on;
            this.sendBtn.disabled = on;
            this.stopBtn.classList.toggle('hidden', !on);
        }

        appendUser(text) {
            const msg = el('div', 'chat-message user');
            msg.appendChild(el('div', 'message-bubble', text));
            this.container.appendChild(msg);
        }

        createAssistant(metaText) {
            const msg = el('div', 'chat-message assistant');
            const meta = el('div', 'message-meta');
            meta.appendChild(el('span', '', 'NeuroCore'));
            const model = el('span', 'meta-model', metaText || '');
            meta.appendChild(model);
            const tools = el('div', 'tool-list');
            tools.style.display = 'contents';
            const bubble = el('div', 'message-bubble markdown-body');
            msg.append(meta, tools, bubble);
            this.container.appendChild(msg);
            return { msg, meta, model, tools, bubble };
        }

        addTypingIndicator(view) {
            const typing = el('div', 'typing-indicator');
            typing.setAttribute('aria-label', 'NeuroCore está pensando');
            for (let i = 0; i < 3; i++) typing.appendChild(el('span', 'typing-dot'));
            view.bubble.appendChild(typing);
            return typing;
        }

        addToolCard(view, name, args) {
            const card = el('details', 'tool-card');
            const summary = el('summary');
            summary.appendChild(el('span', 'tool-name', name));
            const argText = args && Object.keys(args).length ? JSON.stringify(args) : '';
            summary.appendChild(el('span', 'tool-args', truncate(argText, 160)));
            const status = el('span', 'tool-status running', 'executando');
            summary.appendChild(status);
            card.appendChild(summary);
            const output = el('div', 'tool-output', argText ? 'Argumentos: ' + JSON.stringify(args, null, 2) : 'Sem argumentos.');
            card.appendChild(output);
            view.tools.appendChild(card);
            return { card, status, output, summary };
        }

        finishToolCard(toolView, data) {
            const ok = data.status === 'success';
            toolView.status.className = 'tool-status ' + (ok ? 'success' : 'error');
            const ms = typeof data.execution_time_ms === 'number' ? ` · ${data.execution_time_ms} ms` : '';
            toolView.status.textContent = (ok ? 'ok' : 'erro') + ms;
            let text = '';
            if (!ok && data.error) text += 'Erro: ' + data.error + '\n\n';
            const out = data.output;
            if (out && typeof out === 'object' && ('stdout' in out || 'stderr' in out)) {
                text += `$ ${out.command || ''}\n${out.stdout || ''}${out.stderr ? '\n[stderr]\n' + out.stderr : ''}\n(exit ${out.exit_code})`;
            } else if (typeof out === 'string') {
                text += out;
            } else if (out !== undefined && out !== null && out !== '') {
                text += JSON.stringify(out, null, 2);
            }
            toolView.output.textContent = truncate(text || '(sem saída)', 20000);
        }

        addDiffButton(toolView, diff) {
            const btn = el('button', 'btn-copy', 'Ver diff');
            btn.type = 'button';
            btn.setAttribute('data-open-diff', '');
            btn._diff = diff;
            toolView.summary.insertBefore(btn, toolView.status);
        }

        addNotice(view, title, items) {
            // Um único aviso por resposta; novos itens são acrescentados sem repetir.
            if (!view.notice) {
                view.notice = el('details', 'notice');
                view.noticeTitle = el('summary', '', title);
                view.noticeList = el('ul');
                view.noticeItems = new Set();
                view.notice.append(view.noticeTitle, view.noticeList);
                view.msg.insertBefore(view.notice, view.bubble);
            }
            view.noticeTitle.textContent = title;
            (items || []).forEach(t => {
                if (view.noticeItems.has(t)) return;
                view.noticeItems.add(t);
                view.noticeList.appendChild(el('li', '', t));
            });
        }

        async send(rawText) {
            const text = String(rawText || '').trim();
            if (!text || state.isStreaming) return;
            this.input.value = '';
            this.autoResize();
            this.removeWelcome();
            this.appendUser(text);

            const view = this.createAssistant(state.provider === 'local' ? 'modo local' : state.model);
            let typing = this.addTypingIndicator(view);
            this.scrollToBottom(true);
            this.setStreaming(true);
            state.abortController = new AbortController();
            store.set('sessionStorage', 'neurocore_session_used', state.sessionId);

            let content = '';
            let renderPending = false;
            let finished = false;
            const toolViews = {};
            const renderContent = (final) => {
                renderPending = false;
                if (finished) return;
                if (final) finished = true;
                view.bubble.innerHTML = Markdown.render(content);
                if (!final) view.bubble.classList.add('streaming-cursor');
                else view.bubble.classList.remove('streaming-cursor');
                this.scrollToBottom(false);
            };
            const scheduleRender = () => {
                if (renderPending) return;
                renderPending = true;
                requestAnimationFrame(() => renderContent(false));
            };
            const dropTyping = () => { if (typing) { typing.remove(); typing = null; } };

            try {
                const res = await api(`${API}/chat/stream`, {
                    method: 'POST',
                    raw: true,
                    signal: state.abortController.signal,
                    body: Object.assign({ prompt: text, session_id: state.sessionId }, state.agentPayload()),
                });
                await readSSE(res, (event, data) => {
                    switch (event) {
                        case 'token':
                            dropTyping();
                            content += data.token || '';
                            scheduleRender();
                            break;
                        case 'tool_start':
                            dropTyping();
                            toolViews[data.tool_call_id] = this.addToolCard(view, data.name, data.args);
                            this.scrollToBottom(false);
                            break;
                        case 'diff': {
                            const last = Object.values(toolViews).pop();
                            if (last && data.diff) this.addDiffButton(last, data.diff);
                            break;
                        }
                        case 'tool_end': {
                            const tv = toolViews[data.tool_call_id];
                            if (tv) this.finishToolCard(tv, data);
                            if (!typing && !content) typing = this.addTypingIndicator(view);
                            break;
                        }
                        case 'fallback':
                            this.addNotice(view, `Aviso: resposta gerada por "${data.provider_used}" (fallback). Ver detalhes`, data.fallback_trace || []);
                            break;
                        case 'error':
                            content += `\n\n**Erro:** ${data.message || 'falha desconhecida'}`;
                            scheduleRender();
                            break;
                        case 'done':
                            if (data.model_used) view.model.textContent = data.provider_used === 'local' ? 'modo local' : data.model_used;
                            if (data.session_id && data.session_id !== state.sessionId) {
                                state.sessionId = data.session_id;
                                store.set('sessionStorage', 'neurocore_session_id', data.session_id);
                            }
                            break;
                        default:
                            break;
                    }
                });
                dropTyping();
                if (!content && !Object.keys(toolViews).length) content = '_Sem resposta do modelo._';
                renderContent(true);
            } catch (err) {
                dropTyping();
                if (err && err.name === 'AbortError') {
                    content += (content ? '\n\n' : '') + '_Resposta interrompida._';
                    renderContent(true);
                } else {
                    finished = true;
                    view.msg.classList.add('error');
                    view.bubble.classList.remove('streaming-cursor');
                    view.bubble.textContent = (err && err.message) || 'Erro inesperado.';
                    if (err && err.status === 401) state.emit('unauthorized');
                }
            } finally {
                state.abortController = null;
                this.setStreaming(false);
                this.scrollToBottom(false);
                this.input.focus();
            }
        }

        async restoreHistory() {
            // Só consulta o servidor se esta aba já conversou nesta sessão.
            if (store.get('sessionStorage', 'neurocore_session_used') !== state.sessionId) return;
            let session;
            try {
                session = await api(`${API}/sessions/${encodeURIComponent(state.sessionId)}`);
            } catch (e) {
                return; // sessão nova ou expirada
            }
            const messages = (session && session.messages) || [];
            if (!messages.some(m => m.role === 'user')) return;
            this.removeWelcome();
            let view = null;
            const pending = {};
            messages.forEach(m => {
                if (m.role === 'user') {
                    this.appendUser(m.content || '');
                    view = null;
                } else if (m.role === 'assistant') {
                    if (!view) view = this.createAssistant(session.model || '');
                    (m.tool_calls || []).forEach(tc => { pending[tc.id] = this.addToolCard(view, tc.name, tc.arguments); });
                    if (m.content) view.bubble.innerHTML = Markdown.render(m.content);
                } else if (m.role === 'tool' && pending[m.tool_call_id]) {
                    const tv = pending[m.tool_call_id];
                    tv.status.className = 'tool-status ' + (m.is_error ? 'error' : 'success');
                    tv.status.textContent = m.is_error ? 'erro' : 'ok';
                    tv.output.textContent = truncate(m.content || '', 20000);
                }
            });
            this.scrollToBottom(true);
        }
    }

    // ======================================================================
    // Tarefas (API /api/v1/tasks + stream de atividade)
    // ======================================================================
    const STATUSES = ['backlog', 'in_progress', 'completed', 'failed'];
    const PRIORITY_LABELS = { low: 'baixa', medium: 'média', high: 'alta', critical: 'crítica' };

    class KanbanManager {
        constructor() {
            this.tasks = [];
            this.pollTimer = null;
            this.eventSource = null;
            this.loaded = false;
            this.reloadTimer = null;
            this.bind();
        }

        bind() {
            document.getElementById('btn-new-task').addEventListener('click', () => Modals.open('new-task-modal'));
            document.getElementById('btn-refresh-tasks').addEventListener('click', () => this.load(true));
            document.getElementById('task-form').addEventListener('submit', (e) => {
                e.preventDefault();
                this.create();
            });
            document.querySelector('.kanban-board').addEventListener('click', (e) => {
                const btn = e.target.closest('button[data-action]');
                if (!btn) return;
                const id = btn.closest('.kanban-card').getAttribute('data-id');
                const action = btn.getAttribute('data-action');
                if (action === 'run') this.run(id, btn);
                else if (action === 'delete') this.remove(id);
                else if (action === 'result') this.showResult(id);
            });
        }

        activate() {
            if (!this.loaded) this.load();
            this.connectActivity();
        }

        async load(manual) {
            try {
                this.tasks = await api(`${API}/tasks`);
                this.loaded = true;
                this.render();
                if (manual) toast('Painel atualizado.');
            } catch (err) {
                toast('Não foi possível carregar as tarefas: ' + err.message, 'error');
            }
            this.schedulePoll();
        }

        scheduleReload() {
            clearTimeout(this.reloadTimer);
            this.reloadTimer = setTimeout(() => this.load(), 250);
        }

        schedulePoll() {
            clearTimeout(this.pollTimer);
            if (this.tasks.some(t => t.status === 'in_progress')) {
                this.pollTimer = setTimeout(() => this.load(), 2000);
            }
        }

        connectActivity() {
            if (this.eventSource || typeof EventSource === 'undefined') return;
            const status = document.getElementById('activity-status');
            const qs = state.accessToken ? '?token=' + encodeURIComponent(state.accessToken) : '';
            const es = new EventSource(API_BASE + `${API}/stream/activity` + qs);
            this.eventSource = es;
            es.onopen = () => { status.textContent = 'Atividade: ao vivo'; status.classList.add('live'); };
            es.onerror = () => { status.textContent = 'Atividade: reconectando...'; status.classList.remove('live'); };
            es.onmessage = (ev) => {
                let data;
                try { data = JSON.parse(ev.data); } catch (e) { return; }
                if (data && typeof data.event_type === 'string' && data.event_type.startsWith('task_')) this.scheduleReload();
            };
        }

        async create() {
            const title = document.getElementById('task-title-input');
            const prompt = document.getElementById('task-prompt-input');
            const priority = document.getElementById('task-priority-input');
            if (!title.value.trim() || !prompt.value.trim()) {
                toast('Preencha o título e a instrução.', 'warning');
                return;
            }
            const submit = document.getElementById('btn-submit-task');
            submit.disabled = true;
            try {
                await api(`${API}/tasks`, {
                    method: 'POST',
                    body: { title: title.value.trim(), prompt: prompt.value.trim(), priority: priority.value, status: 'backlog' },
                });
                title.value = '';
                prompt.value = '';
                priority.value = 'medium';
                Modals.close('new-task-modal');
                toast('Tarefa criada.');
                await this.load();
            } catch (err) {
                toast('Erro ao criar tarefa: ' + err.message, 'error');
            } finally {
                submit.disabled = false;
            }
        }

        async run(id, btn) {
            btn.disabled = true;
            try {
                await api(`${API}/tasks/${encodeURIComponent(id)}/run`, { method: 'POST', body: state.agentPayload() });
                toast('Tarefa enviada para execução.');
                await this.load();
            } catch (err) {
                toast('Erro ao executar: ' + err.message, 'error');
                btn.disabled = false;
            }
        }

        async remove(id) {
            const task = this.tasks.find(t => t.task_id === id);
            if (!window.confirm(`Excluir a tarefa "${task ? task.title : id}"?`)) return;
            try {
                await api(`${API}/tasks/${encodeURIComponent(id)}`, { method: 'DELETE' });
                await this.load();
            } catch (err) {
                toast('Erro ao excluir: ' + err.message, 'error');
            }
        }

        showResult(id) {
            const task = this.tasks.find(t => t.task_id === id);
            if (!task) return;
            document.getElementById('task-result-title').textContent = task.title;
            const body = document.getElementById('task-result-body');
            const md = `**Instrução:**\n\n${task.prompt}\n\n---\n\n**Resultado${task.provider_used ? ' (' + task.provider_used + ')' : ''}:**\n\n${task.result_summary || '_Sem resultado ainda._'}`;
            body.innerHTML = Markdown.render(md);
            Modals.open('task-result-modal');
        }

        cardFor(task) {
            const card = el('div', 'kanban-card');
            card.setAttribute('data-id', task.task_id);
            const top = el('div', 'kanban-card-top');
            top.appendChild(el('div', 'kanban-card-title', task.title));
            top.appendChild(el('span', 'priority ' + task.priority, PRIORITY_LABELS[task.priority] || task.priority));
            card.appendChild(top);
            card.appendChild(el('div', 'kanban-card-prompt', task.prompt || ''));

            const actions = el('div', 'kanban-card-actions');
            const addBtn = (label, action, cls) => {
                const b = el('button', cls, label);
                b.type = 'button';
                b.setAttribute('data-action', action);
                actions.appendChild(b);
                return b;
            };
            if (task.status === 'in_progress') {
                const running = addBtn('Executando...', 'noop', 'btn-secondary');
                running.disabled = true;
            } else {
                addBtn(task.status === 'backlog' ? 'Executar' : 'Executar de novo', 'run', 'btn-primary');
            }
            if (task.result_summary) addBtn('Ver resultado', 'result', 'btn-secondary');
            if (task.status !== 'in_progress') addBtn('Excluir', 'delete', 'btn-danger');
            card.appendChild(actions);
            return card;
        }

        render() {
            STATUSES.forEach(status => {
                const list = document.getElementById(`list-${status}`);
                const count = document.getElementById(`count-${status}`);
                const items = this.tasks.filter(t => t.status === status);
                count.textContent = String(items.length);
                list.textContent = '';
                if (!items.length) {
                    list.appendChild(el('div', 'kanban-empty', status === 'backlog' ? 'Nenhuma tarefa. Clique em "+ Criar tarefa".' : 'Vazio'));
                    return;
                }
                items.forEach(task => list.appendChild(this.cardFor(task)));
            });
        }
    }

    // ======================================================================
    // Navegação e saúde do servidor
    // ======================================================================
    let kanban = null;

    function switchView(viewId) {
        document.querySelectorAll('.tab-btn').forEach(tab => {
            const active = tab.getAttribute('data-view') === viewId;
            tab.classList.toggle('active', active);
            tab.setAttribute('aria-selected', String(active));
        });
        document.querySelectorAll('.view-section').forEach(view => {
            const active = view.id === viewId;
            view.classList.toggle('active', active);
            view.hidden = !active;
        });
        if (viewId === 'view-tasks' && kanban) kanban.activate();
        try { history.replaceState(null, '', '#' + viewId.replace('view-', '')); } catch (e) { /* ignore */ }
    }

    async function checkHealth() {
        try {
            state.health = await api(`${API}/health`);
        } catch (err) {
            state.health = null;
        }
        state.emit('healthChanged');
        const ws = document.getElementById('workspace-name');
        ws.textContent = state.health ? state.health.workspace_root : 'servidor offline';
        const info = document.getElementById('input-info');
        if (state.health && !state.health.dangerous_tools_enabled) {
            info.textContent = 'Modo seguro: comandos e escrita de arquivos estão desativados neste servidor.';
        }
        if (state.health && state.health.auth_required && !state.accessToken) {
            toast('Este servidor exige um token de acesso. Informe-o em Conexão.', 'warning');
        }
    }

    document.addEventListener('DOMContentLoaded', () => {
        Modals.init();

        document.querySelectorAll('.tab-btn').forEach(tab => {
            tab.addEventListener('click', () => switchView(tab.getAttribute('data-view')));
        });

        const diffViewer = new DiffViewer();
        const connection = new ConnectionPanel();
        kanban = new KanbanManager();
        const chat = new ChatClient(diffViewer);

        state.on('unauthorized', () => {
            toast('Token de acesso inválido ou ausente. Informe-o em Conexão.', 'error');
        });

        window.NeuroCore = {
            state,
            authHeaders: () => state.authHeaders(),
            renderMarkdown: (s) => Markdown.render(s),
            diffViewer,
            chat,
            connection,
        };

        diffViewer.load(
            "def run_server(port=8000):\n    print('Starting server on port', port)\n    return True",
            "def run_server(port: int = 8000, host: str = '127.0.0.1') -> bool:\n    \"\"\"Inicia o servidor FastAPI do NeuroCore.\"\"\"\n    logger.info(f'Starting NeuroCore server on {host}:{port}')\n    return True"
        );

        const hash = (window.location.hash || '').replace('#', '');
        if (['diff', 'tasks'].includes(hash)) switchView('view-' + hash);

        checkHealth();
        chat.input.focus();
    });
})();
