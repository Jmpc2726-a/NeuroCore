/**
 * NeuroCore — Web Studio (app.js)
 *
 * Interface no estilo dos grandes assistentes: barra lateral com o histórico
 * de conversas, chat com streaming, anexos (imagens e documentos), galeria e
 * visualizador de imagens, fórmulas (KaTeX), realce de código (highlight.js),
 * ditado e leitura em voz alta, edição e regeneração de mensagens, diffs e
 * quadro de tarefas.
 *
 * Segurança: todo conteúdo vindo do usuário, do modelo ou de serviços
 * externos é escapado antes de entrar no DOM; imagens só são exibidas a
 * partir do próprio servidor (/api/v1/files/<id>).
 */
(function () {
    'use strict';

    // ======================================================================
    // Utilidades
    // ======================================================================
    const API = '/api/v1';
    const API_BASE = window.location.protocol === 'file:' ? 'http://127.0.0.1:8000' : '';
    const FILE_ID_RE = /^[a-f0-9]{32}$/;

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

    function $(id) {
        return document.getElementById(id);
    }

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
        window.crypto.getRandomValues(bytes);
        return Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
    }

    function toast(message, type) {
        const container = $('toast-container');
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

    function formatBytes(bytes) {
        if (!bytes && bytes !== 0) return '';
        if (bytes < 1024) return bytes + ' B';
        if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(0) + ' KB';
        return (bytes / (1024 * 1024)).toFixed(1).replace('.', ',') + ' MB';
    }

    function debounce(fn, ms) {
        let timer = null;
        return (...args) => {
            clearTimeout(timer);
            timer = setTimeout(() => fn(...args), ms);
        };
    }

    function slugify(text) {
        return String(text || '')
            .normalize('NFKD').replace(/[̀-ͯ]/g, '')
            .replace(/[^a-zA-Z0-9]+/g, '-').replace(/^-+|-+$/g, '').toLowerCase().slice(0, 50) || 'conversa';
    }

    function fileUrl(id, download) {
        return FILE_ID_RE.test(id || '') ? `${API_BASE}${API}/files/${id}${download ? '?download=1' : ''}` : '';
    }

    async function copyText(text) {
        try {
            await navigator.clipboard.writeText(text);
            return true;
        } catch (e) {
            const area = el('textarea');
            area.value = text;
            area.style.position = 'fixed';
            area.style.opacity = '0';
            document.body.appendChild(area);
            area.select();
            let ok = false;
            try { ok = document.execCommand('copy'); } catch (err) { ok = false; }
            area.remove();
            return ok;
        }
    }

    const ICONS = {
        copy: '<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
        check: '<path d="M20 6 9 17l-5-5"/>',
        refresh: '<path d="M21 12a9 9 0 1 1-2.64-6.36"/><path d="M21 3v6h-6"/>',
        edit: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/>',
        speaker: '<path d="M11 5 6 9H2v6h4l5 4V5z"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14"/>',
        download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5"/><path d="M12 15V3"/>',
        trash: '<path d="M3 6h18"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>',
        file: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M16 13H8"/><path d="M16 17H8"/>',
        logo: null,
    };

    function icon(name, size) {
        const s = size || 16;
        if (name === 'logo') {
            return `<svg width="${s}" height="${s}" viewBox="0 0 32 32" fill="none" aria-hidden="true"><path d="M9 22V10l14 12V10" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
        }
        return `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ''}</svg>`;
    }

    function iconButton(name, label, extraClass) {
        const b = el('button', 'icon-btn' + (extraClass ? ' ' + extraClass : ''));
        b.type = 'button';
        b.title = label;
        b.setAttribute('aria-label', label);
        b.innerHTML = icon(name); // ícone fixo, sem conteúdo do usuário
        return b;
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
    const DEFAULT_SETTINGS = { userName: '', style: 'balanced', instructions: '', enterSends: true };

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

            // Identificador aleatório deste navegador: separa as conversas de cada pessoa.
            let clientId = store.get('localStorage', 'neurocore_client_id');
            if (!clientId || !/^[A-Za-z0-9_-]{16,128}$/.test(clientId)) {
                clientId = newId();
                store.set('localStorage', 'neurocore_client_id', clientId);
            }
            this.clientId = clientId;

            this.sessionId = store.get('sessionStorage', 'neurocore_session_id') || newId();
            store.set('sessionStorage', 'neurocore_session_id', this.sessionId);

            this.settings = Object.assign({}, DEFAULT_SETTINGS);
            try {
                const saved = JSON.parse(store.get('localStorage', 'neurocore_settings') || '{}');
                if (saved && typeof saved === 'object') Object.assign(this.settings, saved);
            } catch (e) { /* configurações inválidas: usa o padrão */ }

            this.limits = { maxUploadBytes: 15 * 1024 * 1024, maxAttachments: 10 };
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

        saveSettings(values) {
            this.settings = Object.assign({}, this.settings, values);
            store.set('localStorage', 'neurocore_settings', JSON.stringify(this.settings));
            this.emit('settingsChanged');
        }

        setDiffViewMode(mode) {
            this.diffViewMode = mode === 'unified' ? 'unified' : 'split';
        }

        setSession(id, used) {
            this.sessionId = id;
            store.set('sessionStorage', 'neurocore_session_id', id);
            store.set('sessionStorage', 'neurocore_session_used', used ? id : null);
        }

        markSessionUsed() {
            store.set('sessionStorage', 'neurocore_session_used', this.sessionId);
        }

        sessionUsed() {
            return store.get('sessionStorage', 'neurocore_session_used') === this.sessionId;
        }

        providerLabel() {
            return (PROVIDERS[this.provider] || PROVIDERS.local).label;
        }

        serverHasKey(provider) {
            const p = this.health && this.health.providers && this.health.providers[provider];
            return !!(p && p.configured);
        }

        /** Corpo comum das requisições ao agente (provedor + preferências). */
        agentPayload() {
            const body = { provider: this.provider, model: this.model };
            if (this.provider !== 'local' && this.apiKey) body.api_key = this.apiKey;
            const s = this.settings;
            if (s.userName) body.user_name = s.userName.slice(0, 80);
            if (s.style && s.style !== 'balanced') body.response_style = s.style;
            if (s.instructions) body.custom_instructions = s.instructions.slice(0, 4000);
            try {
                const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
                if (tz && tz.length <= 64) body.timezone = tz;
            } catch (e) { /* sem fuso */ }
            return body;
        }

        authHeaders() {
            const headers = { 'X-Client-Id': this.clientId };
            if (this.accessToken) headers['X-NeuroCore-Token'] = this.accessToken;
            return headers;
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
            else if (Array.isArray(data.detail)) detail = data.detail.map(d => String(d.msg || '').replace(/^Value error, /, '')).join('; ');
            else if (data.error) detail = data.error;
        } catch (e) { /* corpo não é JSON */ }
        if (res.status === 401) detail = detail || 'Token de acesso inválido ou ausente.';
        if (res.status === 413) detail = detail || 'Arquivo ou mensagem grande demais.';
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
        if (!res.ok) {
            const error = await readError(res);
            if (error.status === 401) state.emit('unauthorized');
            throw error;
        }
        if (options.raw) return res;
        return res.status === 204 ? null : res.json();
    }

    async function uploadFile(blob, name, signal) {
        const headers = Object.assign({}, state.authHeaders(), {
            'Content-Type': blob.type || 'application/octet-stream',
            'X-Filename': encodeURIComponent(name || 'arquivo'),
        });
        let res;
        try {
            res = await fetch(`${API_BASE}${API}/uploads`, { method: 'POST', headers, body: blob, signal });
        } catch (err) {
            if (err && err.name === 'AbortError') throw err;
            throw new ApiError('Falha de conexão ao enviar o arquivo.', 0);
        }
        if (!res.ok) throw await readError(res);
        return res.json();
    }

    /** Reduz fotos grandes no navegador antes do envio (o servidor também valida). */
    async function prepareImage(file) {
        try {
            if (!/^image\/(jpeg|png|webp)$/.test(file.type) || typeof createImageBitmap !== 'function') return file;
            if (file.size < 1.5 * 1024 * 1024) return file;
            const bitmap = await createImageBitmap(file);
            const scale = Math.min(1, 2048 / Math.max(bitmap.width, bitmap.height));
            const width = Math.max(1, Math.round(bitmap.width * scale));
            const height = Math.max(1, Math.round(bitmap.height * scale));
            const canvas = document.createElement('canvas');
            canvas.width = width;
            canvas.height = height;
            canvas.getContext('2d').drawImage(bitmap, 0, 0, width, height);
            if (bitmap.close) bitmap.close();
            const type = file.type === 'image/png' ? 'image/png' : 'image/jpeg';
            const blob = await new Promise(resolve => canvas.toBlob(resolve, type, 0.9));
            return blob && blob.size < file.size ? blob : file;
        } catch (e) {
            return file;
        }
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
    // Markdown seguro + fórmulas (KaTeX) + realce de código (highlight.js)
    // ======================================================================
    const Markdown = {
        hlCache: new Map(),
        mathCache: new Map(),

        remember(map, key, value) {
            if (map.size > 400) map.delete(map.keys().next().value);
            map.set(key, value);
            return value;
        },

        safeUrl(raw) {
            const url = raw.replace(/&amp;/g, '&');
            if (/^https?:\/\//i.test(url) || /^\/(?!\/)/.test(url) || url.startsWith('#')) return raw;
            return null;
        },

        renderMath(tex, display) {
            const key = (display ? 'D:' : 'I:') + tex;
            if (this.mathCache.has(key)) return this.mathCache.get(key);
            let html = null;
            if (window.katex && typeof window.katex.renderToString === 'function') {
                try {
                    html = window.katex.renderToString(tex, { displayMode: display, throwOnError: false, strict: 'ignore', trust: false });
                } catch (e) {
                    html = null;
                }
            }
            if (!html) {
                html = display ? `<div class="math-fallback">${escapeHtml(tex)}</div>` : `<code class="math-fallback">${escapeHtml(tex)}</code>`;
            } else if (display) {
                html = `<div class="math-block">${html}</div>`;
            }
            return this.remember(this.mathCache, key, html);
        },

        highlight(code, lang) {
            const key = lang + '\u0000' + code;
            if (this.hlCache.has(key)) return this.hlCache.get(key);
            let html = null;
            let label = lang;
            const hljs = window.hljs;
            if (hljs) {
                try {
                    if (lang && hljs.getLanguage(lang)) {
                        html = hljs.highlight(code, { language: lang, ignoreIllegals: true }).value;
                    } else if (!lang && code.length <= 4000) {
                        const auto = hljs.highlightAuto(code);
                        if (auto.relevance >= 5) {
                            html = auto.value;
                            label = auto.language || '';
                        }
                    }
                } catch (e) {
                    html = null;
                }
            }
            if (html === null) html = escapeHtml(code);
            return this.remember(this.hlCache, key, { html, label });
        },

        codeBlock(lang, code) {
            const { html, label } = this.highlight(code, (lang || '').toLowerCase());
            return `<div class="code-block"><div class="code-block-header"><span>${escapeHtml(label || 'código')}</span>` +
                `<button class="btn-copy" type="button" data-copy>Copiar</button></div>` +
                `<pre><code class="hljs">${html}</code></pre></div>`;
        },

        inline(text, slots) {
            const hold = (html) => { slots.push(html); return `\u0001${slots.length - 1}\u0001`; };
            let s = escapeHtml(text);

            // Imagens: só as servidas pelo próprio NeuroCore (evita rastreamento por URLs externas).
            s = s.replace(/!\[([^\]\n]*)\]\(\s*([^)\s]+)\s*\)/g, (m, alt, url) => {
                const match = url.match(/^\/api\/v1\/files\/([a-f0-9]{32})$/);
                if (match) {
                    return hold(`<img class="md-image" src="${fileUrl(match[1])}" alt="${alt}" loading="lazy" data-lightbox data-name="${alt}">`);
                }
                const safe = Markdown.safeUrl(url);
                if (safe && /^https?:/i.test(safe)) {
                    return hold(`<a href="${safe}" target="_blank" rel="noopener noreferrer">[imagem: ${alt || 'link'}]</a>`);
                }
                return alt ? `[imagem: ${alt}]` : '';
            });
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

        renderList(items, slots) {
            let html = '';
            const stack = [];
            items.forEach(item => {
                while (stack.length && item.indent < stack[stack.length - 1].indent) {
                    html += `</li></${stack.pop().tag}>`;
                }
                const top = stack[stack.length - 1];
                if (!top || item.indent > top.indent) {
                    const tag = item.ordered ? 'ol' : 'ul';
                    const start = item.ordered && item.num !== 1 && !isNaN(item.num) ? ` start="${item.num}"` : '';
                    html += `<${tag}${start}><li>`;
                    stack.push({ indent: item.indent, tag });
                } else {
                    html += '</li><li>';
                }
                html += item.text.split('\n').map(t => this.inline(t, slots)).join('<br>');
            });
            while (stack.length) html += `</li></${stack.pop().tag}>`;
            return html;
        },

        render(src) {
            if (!src) return '';
            const blocks = [];
            const slots = [];
            let text = String(src).replace(/\r\n/g, '\n').replace(/[\u0000-\u0002]/g, '');
            const block = (html) => { blocks.push(html); return `\n\u0000${blocks.length - 1}\u0000\n`; };
            const inlineHold = (html) => { slots.push(html); return `\u0001${slots.length - 1}\u0001`; };

            // 1) Blocos de código (cercas ```), inclusive o último ainda aberto durante o streaming.
            text = text.replace(/```[ \t]*([\w+#.-]*)[^\n]*\n([\s\S]*?)(?:\n```[ \t]*(?=\n|$)|$)/g,
                (m, lang, code) => block(this.codeBlock(lang, code)));
            // 2) Fórmulas em destaque: $$...$$ e \[...\]
            text = text.replace(/\$\$([\s\S]+?)\$\$/g, (m, tex) => block(this.renderMath(tex.trim(), true)));
            text = text.replace(/\\\[([\s\S]+?)\\\]/g, (m, tex) => block(this.renderMath(tex.trim(), true)));
            // 3) Código em linha (antes das fórmulas, para "$" dentro de código não virar fórmula)
            text = text.replace(/`([^`\n]+)`/g, (m, code) => inlineHold(`<code>${escapeHtml(code)}</code>`));
            // 4) Fórmulas em linha: \(...\) e $...$ (não confunde com "R$ 10")
            text = text.replace(/\\\(([^\n]+?)\\\)/g, (m, tex) => inlineHold(this.renderMath(tex.trim(), false)));
            text = text.replace(/(^|[^\\$\w])\$(?![\s$])([^$\n]*?[^\s\\$])\$(?![\d$\w])/g,
                (m, pre, tex) => pre + inlineHold(this.renderMath(tex, false)));

            const lines = text.split('\n');
            const out = [];
            let para = [];
            const flushPara = () => {
                if (para.length) out.push(`<p>${para.map(l => this.inline(l, slots)).join('<br>')}</p>`);
                para = [];
            };
            const isTableSep = (line) => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(line);
            const splitRow = (line) => line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(c => c.trim());
            const listRe = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;

            for (let i = 0; i < lines.length; i++) {
                const line = lines[i];
                const blockMatch = line.match(/^\u0000(\d+)\u0000$/);
                if (blockMatch) { flushPara(); out.push(blocks[Number(blockMatch[1])]); continue; }
                if (!line.trim()) { flushPara(); continue; }

                const heading = line.match(/^(#{1,6})\s+(.*)$/);
                if (heading) {
                    flushPara();
                    const level = Math.min(heading[1].length + 1, 4);
                    out.push(`<h${level}>${this.inline(heading[2], slots)}</h${level}>`);
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
                    out.push('<table><thead><tr>' + head.map(c => `<th>${this.inline(c, slots)}</th>`).join('') + '</tr></thead><tbody>' +
                        rows.map(r => '<tr>' + r.map(c => `<td>${this.inline(c, slots)}</td>`).join('') + '</tr>').join('') +
                        '</tbody></table>');
                    continue;
                }

                if (listRe.test(line)) {
                    flushPara();
                    const items = [];
                    while (i < lines.length) {
                        const m = lines[i].match(listRe);
                        if (m) {
                            items.push({
                                indent: m[1].replace(/\t/g, '    ').length,
                                ordered: /\d/.test(m[2]),
                                num: parseInt(m[2], 10),
                                text: m[3],
                            });
                            i++;
                        } else if (items.length && /^\s{2,}\S/.test(lines[i]) && !/^\u0000/.test(lines[i])) {
                            items[items.length - 1].text += '\n' + lines[i].trim();
                            i++;
                        } else {
                            break;
                        }
                    }
                    i--;
                    out.push(this.renderList(items, slots));
                    continue;
                }

                if (/^>\s?/.test(line)) {
                    flushPara();
                    const quote = [];
                    while (i < lines.length && /^>\s?/.test(lines[i])) { quote.push(lines[i].replace(/^>\s?/, '')); i++; }
                    i--;
                    out.push(`<blockquote>${quote.map(q => this.inline(q, slots)).join('<br>')}</blockquote>`);
                    continue;
                }

                para.push(line);
            }
            flushPara();
            return out.join('').replace(/\u0001(\d+)\u0001/g, (m, idx) => slots[Number(idx)] || '');
        },

        /** Texto puro de um bloco renderizado (para copiar ou ler em voz alta). */
        plainText(node) {
            const clone = node.cloneNode(true);
            clone.querySelectorAll('.katex-mathml, .code-block-header, .btn-copy').forEach(n => n.remove());
            return clone.innerText || clone.textContent || '';
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
            let start = 0;
            while (start < a.length && start < b.length && a[start] === b[start]) start++;
            let endA = a.length, endB = b.length;
            while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) { endA--; endB--; }

            const result = [];
            for (let k = 0; k < start; k++) result.push({ type: 'unchanged', oldLineNum: k + 1, newLineNum: k + 1, content: a[k] });

            const midA = a.slice(start, endA), midB = b.slice(start, endB);
            const m = midA.length, n = midB.length;
            if ((m + 1) * (n + 1) > MAX_DIFF_CELLS) throw new Error('Os textos são grandes demais para comparar no navegador.');
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
            this.container = $('diff-output-container');
            this.oldInput = $('diff-original-input');
            this.newInput = $('diff-modified-input');
            this.stats = $('diff-stats');
            this.parsed = null;
            this.bind();
        }

        bind() {
            $('btn-compute-diff').addEventListener('click', () => this.load(this.oldInput.value, this.newInput.value, false));
            $('btn-clear-diff').addEventListener('click', () => {
                this.oldInput.value = '';
                this.newInput.value = '';
                this.parsed = null;
                this.stats.textContent = '';
                this.container.innerHTML = '<div class="empty-state">Cole as duas versões e clique em Comparar.</div>';
            });
            const split = $('btn-diff-split');
            const unified = $('btn-diff-unified');
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
                if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                    e.preventDefault();
                    this.load(this.oldInput.value, this.newInput.value, false);
                }
            }));
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
    // Modais, confirmação e visualizador de imagens
    // ======================================================================
    const Modals = {
        lastFocus: null,
        anyOpen() {
            return !!document.querySelector('.modal-backdrop:not(.hidden)') || !$('lightbox').classList.contains('hidden');
        },
        open(id) {
            const modal = $(id);
            if (!modal) return;
            this.lastFocus = document.activeElement;
            modal.classList.remove('hidden');
            const focusable = modal.querySelector('input:not(.hidden):not([type="checkbox"]), select, textarea, button:not([data-close-modal])');
            if (focusable) setTimeout(() => focusable.focus(), 30);
        },
        close(modal) {
            if (typeof modal === 'string') modal = $(modal);
            if (!modal || modal.classList.contains('hidden')) return;
            modal.classList.add('hidden');
            if (modal.id === 'confirm-modal' && Confirm.resolve) Confirm.finish(false);
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
                if (!$('lightbox').classList.contains('hidden')) { Lightbox.close(); return; }
                const open = Array.from(document.querySelectorAll('.modal-backdrop:not(.hidden)')).pop();
                if (open) this.close(open);
            });
        },
    };

    const Confirm = {
        resolve: null,
        ask(message, { title = 'Confirmar', confirmText = 'Confirmar' } = {}) {
            $('confirm-title').textContent = title;
            $('confirm-message').textContent = message;
            $('confirm-ok').textContent = confirmText;
            Modals.open('confirm-modal');
            setTimeout(() => $('confirm-cancel').focus(), 40);
            return new Promise(resolve => { this.resolve = resolve; });
        },
        finish(value) {
            const resolve = this.resolve;
            this.resolve = null;
            if (resolve) resolve(value);
        },
        init() {
            $('confirm-ok').addEventListener('click', () => {
                const resolve = this.resolve;
                this.resolve = null;
                Modals.close('confirm-modal');
                if (resolve) resolve(true);
            });
            $('confirm-cancel').addEventListener('click', () => Modals.close('confirm-modal'));
        },
    };

    const Lightbox = {
        items: [],
        index: 0,
        open(items, index) {
            this.items = items;
            this.index = Math.max(0, Math.min(index, items.length - 1));
            this.lastFocus = document.activeElement;
            $('lightbox').classList.remove('hidden');
            this.show();
            $('lightbox-close').focus();
        },
        show() {
            const item = this.items[this.index];
            if (!item) return;
            const img = $('lightbox-image');
            img.src = item.src;
            img.alt = item.name || 'Imagem';
            $('lightbox-name').textContent = item.name || '';
            const link = $('lightbox-download');
            link.href = item.src + (item.src.includes('?') ? '&' : '?') + 'download=1';
            link.setAttribute('download', item.name || 'imagem');
            const multiple = this.items.length > 1;
            $('lightbox-prev').hidden = !multiple;
            $('lightbox-next').hidden = !multiple;
        },
        step(delta) {
            if (this.items.length < 2) return;
            this.index = (this.index + delta + this.items.length) % this.items.length;
            this.show();
        },
        close() {
            $('lightbox').classList.add('hidden');
            $('lightbox-image').removeAttribute('src');
            if (this.lastFocus && this.lastFocus.focus) this.lastFocus.focus();
        },
        init() {
            $('lightbox-close').addEventListener('click', () => this.close());
            $('lightbox-prev').addEventListener('click', () => this.step(-1));
            $('lightbox-next').addEventListener('click', () => this.step(1));
            $('lightbox').addEventListener('click', (e) => { if (e.target === $('lightbox')) this.close(); });
            document.addEventListener('keydown', (e) => {
                if ($('lightbox').classList.contains('hidden')) return;
                if (e.key === 'ArrowLeft') this.step(-1);
                if (e.key === 'ArrowRight') this.step(1);
            });
            document.addEventListener('click', (e) => {
                const img = e.target.closest && e.target.closest('img[data-lightbox]');
                if (!img) return;
                e.preventDefault();
                const scope = img.closest('.chat-message, .modal-body') || document;
                const imgs = Array.from(scope.querySelectorAll('img[data-lightbox]'));
                this.open(imgs.map(i => ({ src: i.getAttribute('src'), name: i.dataset.name || i.alt })), imgs.indexOf(img));
            });
        },
    };

    // ======================================================================
    // Conexão (provedor, modelo, chave, token)
    // ======================================================================
    class ConnectionPanel {
        constructor() {
            this.form = $('connection-form');
            this.provider = $('modal-provider-select');
            this.model = $('modal-model-select');
            this.customModel = $('modal-model-custom');
            this.apiKey = $('modal-api-key-input');
            this.remember = $('modal-remember-key');
            this.token = $('modal-access-token');
            this.keyGroup = $('api-key-group');
            this.tokenGroup = $('access-token-group');
            this.serverHint = $('provider-server-hint');
            this.openRouterModels = null;
            this.bind();
            this.updateHeader();
            state.on('connectionChanged', () => this.updateHeader());
            state.on('healthChanged', () => this.updateHeader());
        }

        bind() {
            $('btn-open-connection').addEventListener('click', () => this.open());
            this.provider.addEventListener('change', () => this.fillModels(this.provider.value, null));
            this.model.addEventListener('change', () => {
                this.customModel.classList.toggle('hidden', this.model.value !== CUSTOM_MODEL);
                if (this.model.value === CUSTOM_MODEL) this.customModel.focus();
            });
            $('btn-toggle-key').addEventListener('click', (e) => {
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
                if (this.provider.value !== provider) return;
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
                        const vision = m.architecture && Array.isArray(m.architecture.input_modalities) &&
                            m.architecture.input_modalities.includes('image');
                        const tags = [free ? 'grátis' : '', vision ? 'imagens' : ''].filter(Boolean).join(', ');
                        return [m.id, truncate((m.name || m.id) + (tags ? ` — ${tags}` : ''), 90), free];
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
            const model = this.model.value === CUSTOM_MODEL ? this.customModel.value.trim() : this.model.value;
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
            const label = $('active-provider-label');
            const dot = $('status-dot');
            label.textContent = state.provider === 'local' ? 'Local (simulado)' : `${state.providerLabel()} · ${truncate(state.model, 30)}`;
            dot.className = 'status-dot';
            const pill = $('btn-open-connection');
            if (!state.health) {
                dot.classList.add('offline');
                pill.title = 'Servidor indisponível';
            } else if (state.provider === 'local' || state.apiKey || state.serverHasKey(state.provider)) {
                dot.classList.add('online');
                pill.title = 'Conectado — clique para trocar provedor ou modelo';
            } else {
                dot.classList.add('warning');
                pill.title = 'Falta a chave de API deste provedor';
            }
        }
    }

    // ======================================================================
    // Personalização
    // ======================================================================
    class SettingsPanel {
        constructor(onDeleteAll) {
            this.onDeleteAll = onDeleteAll;
            this.form = $('settings-form');
            this.name = $('settings-name');
            this.style = $('settings-style');
            this.instructions = $('settings-instructions');
            this.enterSends = $('settings-enter-sends');
            this.count = $('settings-instructions-count');
            $('btn-open-settings').addEventListener('click', () => this.open());
            this.instructions.addEventListener('input', () => { this.count.textContent = String(this.instructions.value.length); });
            this.form.addEventListener('submit', (e) => {
                e.preventDefault();
                this.save();
            });
            $('btn-delete-all').addEventListener('click', () => this.deleteAll());
            this.renderUser();
            state.on('settingsChanged', () => this.renderUser());
        }

        open() {
            const s = state.settings;
            this.name.value = s.userName || '';
            this.style.value = s.style || 'balanced';
            this.instructions.value = s.instructions || '';
            this.enterSends.checked = s.enterSends !== false;
            this.count.textContent = String(this.instructions.value.length);
            Modals.open('settings-modal');
        }

        save() {
            state.saveSettings({
                userName: this.name.value.trim().slice(0, 80),
                style: this.style.value,
                instructions: this.instructions.value.trim().slice(0, 4000),
                enterSends: this.enterSends.checked,
            });
            Modals.close('settings-modal');
            toast('Preferências salvas.');
        }

        async deleteAll() {
            const ok = await Confirm.ask('Todas as suas conversas e anexos serão apagados deste servidor. Essa ação não pode ser desfeita.', {
                title: 'Apagar todas as conversas?', confirmText: 'Apagar tudo',
            });
            if (!ok) return;
            try {
                const res = await api(`${API}/sessions`, { method: 'DELETE' });
                Modals.close('settings-modal');
                toast(`${res.count} conversa(s) apagada(s).`);
                this.onDeleteAll();
            } catch (err) {
                toast('Não foi possível apagar: ' + err.message, 'error');
            }
        }

        renderUser() {
            const name = (state.settings.userName || '').trim();
            $('user-display-name').textContent = name || 'Personalizar';
            const avatar = $('user-avatar');
            if (name) {
                avatar.textContent = name.charAt(0).toUpperCase();
            } else {
                avatar.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/></svg>';
            }
            const input = $('chat-prompt-input');
            if (input) input.dataset.enterSends = state.settings.enterSends === false ? '0' : '1';
            const info = $('input-info');
            if (info && !info.dataset.locked) {
                info.textContent = state.settings.enterSends === false ? 'Ctrl+Enter envia' : 'Enter envia · Shift+Enter quebra linha';
            }
            updateWelcome();
        }
    }

    function updateWelcome() {
        const title = $('welcome-title');
        if (!title) return;
        const name = (state.settings.userName || '').trim().split(/\s+/)[0];
        const hour = new Date().getHours();
        const greeting = hour >= 5 && hour < 12 ? 'Bom dia' : hour >= 12 && hour < 18 ? 'Boa tarde' : 'Boa noite';
        title.textContent = name ? `${greeting}, ${name}. Como posso ajudar?` : 'Como posso ajudar hoje?';
    }

    // ======================================================================
    // Barra lateral (histórico de conversas)
    // ======================================================================
    class Sidebar {
        constructor() {
            this.shell = $('app-shell');
            this.list = $('conversation-list');
            this.search = $('conversation-search');
            this.backdrop = $('sidebar-backdrop');
            this.sessions = [];
            this.chat = null;
            this.mq = window.matchMedia('(max-width: 900px)');
            if (!this.mq.matches && store.get('localStorage', 'neurocore_sidebar') === 'collapsed') {
                this.shell.classList.add('sidebar-collapsed');
            }
            $('btn-toggle-sidebar').addEventListener('click', () => this.toggle());
            $('btn-collapse-sidebar').addEventListener('click', () => this.toggle());
            this.backdrop.addEventListener('click', () => this.close());
            this.search.addEventListener('input', debounce(() => this.render(), 120));
            this.list.addEventListener('click', (e) => this.onListClick(e));
            const onChange = () => this.close();
            if (this.mq.addEventListener) this.mq.addEventListener('change', onChange);
        }

        isMobile() { return this.mq.matches; }

        toggle() {
            if (this.isMobile()) {
                if (this.shell.classList.contains('sidebar-open')) this.close();
                else this.openDrawer();
            } else {
                const collapsed = this.shell.classList.toggle('sidebar-collapsed');
                store.set('localStorage', 'neurocore_sidebar', collapsed ? 'collapsed' : null);
            }
        }

        openDrawer() {
            this.shell.classList.add('sidebar-open');
            this.backdrop.hidden = false;
        }

        close() {
            this.shell.classList.remove('sidebar-open');
            this.backdrop.hidden = true;
        }

        focusSearch() {
            if (this.isMobile()) this.openDrawer();
            else if (this.shell.classList.contains('sidebar-collapsed')) this.toggle();
            this.search.focus();
            this.search.select();
        }

        async load() {
            try {
                const sessions = await api(`${API}/sessions`);
                this.sessions = (sessions || []).filter(s => s.message_count > 0);
            } catch (err) {
                if (!this.sessions.length) this.sessions = [];
            }
            this.render();
        }

        groupLabel(iso) {
            const date = new Date(iso);
            if (isNaN(date.getTime())) return 'Mais antigas';
            const startOfDay = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
            const days = Math.round((startOfDay(new Date()) - startOfDay(date)) / 86400000);
            if (days <= 0) return 'Hoje';
            if (days === 1) return 'Ontem';
            if (days <= 7) return 'Últimos 7 dias';
            if (days <= 30) return 'Últimos 30 dias';
            return date.toLocaleDateString('pt-BR', { month: 'long', year: 'numeric' });
        }

        render() {
            const query = this.search.value.trim().toLowerCase();
            const items = this.sessions.filter(s => !query || (s.title || '').toLowerCase().includes(query));
            this.list.textContent = '';
            if (!items.length) {
                this.list.appendChild(el('div', 'conversation-empty', query ? 'Nenhuma conversa encontrada.' : 'Suas conversas aparecem aqui.'));
                return;
            }
            let lastGroup = null;
            items.forEach(s => {
                const group = this.groupLabel(s.updated_at);
                if (group !== lastGroup) {
                    this.list.appendChild(el('div', 'conv-group-title', group));
                    lastGroup = group;
                }
                const item = el('div', 'conv-item' + (s.session_id === state.sessionId ? ' active' : ''));
                item.dataset.id = s.session_id;
                const open = el('button', 'conv-open', s.title || 'Nova conversa');
                open.type = 'button';
                open.title = s.title || '';
                open.dataset.action = 'open';
                const actions = el('div', 'conv-actions');
                const rename = iconButton('edit', 'Renomear');
                rename.dataset.action = 'rename';
                const del = iconButton('trash', 'Excluir');
                del.dataset.action = 'delete';
                actions.append(rename, del);
                item.append(open, actions);
                this.list.appendChild(item);
            });
        }

        setActive(id) {
            this.list.querySelectorAll('.conv-item').forEach(item => item.classList.toggle('active', item.dataset.id === id));
        }

        upsert(id, title) {
            const now = new Date().toISOString();
            const existing = this.sessions.find(s => s.session_id === id);
            if (existing) {
                if (title) existing.title = title;
                existing.updated_at = now;
                this.sessions = [existing].concat(this.sessions.filter(s => s !== existing));
            } else {
                this.sessions.unshift({ session_id: id, title: title || 'Nova conversa', updated_at: now, message_count: 1 });
            }
            this.render();
        }

        onListClick(e) {
            const btn = e.target.closest('button[data-action]');
            if (!btn) return;
            const item = btn.closest('.conv-item');
            const id = item && item.dataset.id;
            if (!id) return;
            const action = btn.dataset.action;
            if (action === 'open') {
                this.chat.openConversation(id);
                if (this.isMobile()) this.close();
            } else if (action === 'rename') {
                this.startRename(item, id);
            } else if (action === 'delete') {
                this.remove(id);
            }
        }

        startRename(item, id) {
            const session = this.sessions.find(s => s.session_id === id);
            const input = el('input', 'conv-rename-input');
            input.value = session ? session.title : '';
            input.maxLength = 120;
            input.setAttribute('aria-label', 'Novo título');
            item.textContent = '';
            item.appendChild(input);
            input.focus();
            input.select();
            let done = false;
            const finish = async (save) => {
                if (done) return;
                done = true;
                const title = input.value.trim();
                if (save && title && session && title !== session.title) {
                    try {
                        await api(`${API}/sessions/${encodeURIComponent(id)}`, { method: 'PATCH', body: { title } });
                        session.title = title;
                        if (id === state.sessionId && this.chat) this.chat.setTitle(title);
                    } catch (err) {
                        toast('Não foi possível renomear: ' + err.message, 'error');
                    }
                }
                this.render();
            };
            input.addEventListener('keydown', (ev) => {
                if (ev.key === 'Enter') { ev.preventDefault(); finish(true); }
                if (ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); finish(false); }
            });
            input.addEventListener('blur', () => finish(true));
        }

        async remove(id) {
            const session = this.sessions.find(s => s.session_id === id);
            const ok = await Confirm.ask(`A conversa "${truncate(session ? session.title : '', 60)}" e seus anexos serão apagados.`, {
                title: 'Excluir conversa?', confirmText: 'Excluir',
            });
            if (!ok) return;
            try {
                await api(`${API}/sessions/${encodeURIComponent(id)}`, { method: 'DELETE' });
                this.sessions = this.sessions.filter(s => s.session_id !== id);
                this.render();
                if (id === state.sessionId && this.chat) this.chat.newChat();
                toast('Conversa excluída.');
            } catch (err) {
                toast('Não foi possível excluir: ' + err.message, 'error');
            }
        }
    }

    // ======================================================================
    // Chat
    // ======================================================================
    const SOURCE_LABELS = { chart: 'Gráfico', generated: 'Imagem gerada', enem: 'Figura do ENEM', upload: 'Imagem' };

    class ChatClient {
        constructor(diffViewer, sidebar) {
            this.diffViewer = diffViewer;
            this.sidebar = sidebar;
            this.scroller = $('chat-scroll');
            this.container = $('chat-messages-container');
            this.input = $('chat-prompt-input');
            this.form = $('chat-form');
            this.sendBtn = $('btn-send-prompt');
            this.stopBtn = $('btn-stop');
            this.attachBtn = $('btn-attach');
            this.fileInput = $('file-input');
            this.tray = $('attachment-tray');
            this.voiceBtn = $('btn-voice');
            this.dropOverlay = $('drop-overlay');
            this.scrollBtn = $('btn-scroll-bottom');
            this.titleEl = $('conversation-title');
            const welcome = $('welcome-card');
            this.welcomeTemplate = welcome ? welcome.cloneNode(true) : null;
            this.pending = [];
            this.recognition = null;
            this.speakingButton = null;
            this.bind();
            this.initVoice();
            this.initDragDrop();
            this.setTitle('');
        }

        // ---------------- Eventos ----------------
        bind() {
            this.form.addEventListener('submit', (e) => {
                e.preventDefault();
                this.send(this.input.value);
            });
            this.input.addEventListener('keydown', (e) => {
                if (e.key !== 'Enter' || e.isComposing) return;
                const enterSends = this.input.dataset.enterSends !== '0';
                if ((enterSends && !e.shiftKey) || (!enterSends && (e.ctrlKey || e.metaKey))) {
                    e.preventDefault();
                    this.send(this.input.value);
                }
            });
            this.input.addEventListener('input', () => this.autoResize());
            this.input.addEventListener('paste', (e) => {
                const files = Array.from((e.clipboardData && e.clipboardData.files) || []);
                if (files.length) {
                    e.preventDefault();
                    this.addFiles(files.map((f, i) => (f.name && f.name !== 'image.png') ? f :
                        new File([f], `print-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}${i ? '-' + i : ''}.png`, { type: f.type })));
                }
            });
            this.stopBtn.addEventListener('click', () => this.stop());
            this.attachBtn.addEventListener('click', () => this.fileInput.click());
            this.fileInput.addEventListener('change', () => {
                this.addFiles(this.fileInput.files);
                this.fileInput.value = '';
            });
            $('btn-new-chat').addEventListener('click', () => this.newChat());
            $('btn-new-chat-header').addEventListener('click', () => this.newChat());
            $('btn-export-chat').addEventListener('click', () => this.exportConversation());
            this.scroller.addEventListener('scroll', () => this.updateScrollButton());
            this.scrollBtn.addEventListener('click', () => this.scrollToBottom(true, true));

            this.container.addEventListener('click', (e) => {
                const chip = e.target.closest('.prompt-chip');
                if (chip) {
                    if (chip.dataset.action === 'attach') this.fileInput.click();
                    else if (chip.dataset.fill) {
                        this.input.value = chip.dataset.fill;
                        this.autoResize();
                        this.input.focus();
                        this.input.setSelectionRange(this.input.value.length, this.input.value.length);
                    } else {
                        this.send(chip.dataset.prompt || chip.textContent);
                    }
                    return;
                }
                const copy = e.target.closest('[data-copy]');
                if (copy) {
                    const pre = copy.closest('.code-block').querySelector('pre');
                    copyText(pre ? pre.textContent : '').then(ok => {
                        copy.textContent = ok ? 'Copiado!' : 'Falhou';
                        setTimeout(() => { copy.textContent = 'Copiar'; }, 1500);
                    });
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

        stop() {
            if (state.abortController) state.abortController.abort();
        }

        autoResize() {
            this.input.style.height = 'auto';
            this.input.style.height = Math.min(this.input.scrollHeight, 220) + 'px';
        }

        nearBottom() {
            const s = this.scroller;
            return s.scrollHeight - s.scrollTop - s.clientHeight < 140;
        }

        scrollToBottom(force, smooth) {
            if (force || this.nearBottom()) {
                this.scroller.scrollTo({ top: this.scroller.scrollHeight, behavior: smooth ? 'smooth' : 'auto' });
            }
            this.updateScrollButton();
        }

        updateScrollButton() {
            const s = this.scroller;
            this.scrollBtn.hidden = s.scrollHeight - s.scrollTop - s.clientHeight < 240;
        }

        setTitle(title) {
            const text = title || '';
            this.titleEl.textContent = text;
            this.titleEl.title = text;
            document.title = text ? `${text} — NeuroCore` : 'NeuroCore — Assistente de IA';
        }

        setStreaming(on) {
            state.isStreaming = on;
            this.sendBtn.hidden = on;
            this.stopBtn.hidden = !on;
            this.updateSendState();
        }

        updateSendState() {
            const uploading = this.pending.some(p => p.status === 'uploading');
            this.sendBtn.disabled = state.isStreaming || uploading;
            this.sendBtn.title = uploading ? 'Aguarde o envio dos anexos' : 'Enviar mensagem';
        }

        // ---------------- Anexos na caixa de mensagem ----------------
        initDragDrop() {
            const view = $('view-chat');
            const hasFiles = (e) => e.dataTransfer && Array.from(e.dataTransfer.types || []).includes('Files');
            let depth = 0;
            view.addEventListener('dragenter', (e) => {
                if (!hasFiles(e)) return;
                e.preventDefault();
                depth++;
                this.dropOverlay.hidden = false;
            });
            view.addEventListener('dragover', (e) => {
                if (!hasFiles(e)) return;
                e.preventDefault();
                e.dataTransfer.dropEffect = 'copy';
            });
            view.addEventListener('dragleave', (e) => {
                if (!hasFiles(e)) return;
                depth = Math.max(0, depth - 1);
                if (!depth) this.dropOverlay.hidden = true;
            });
            view.addEventListener('drop', (e) => {
                if (!hasFiles(e)) return;
                e.preventDefault();
                depth = 0;
                this.dropOverlay.hidden = true;
                this.addFiles(e.dataTransfer.files);
            });
            // Evita que o navegador abra o arquivo se ele for solto fora da conversa.
            window.addEventListener('dragover', (e) => { if (hasFiles(e)) e.preventDefault(); });
            window.addEventListener('drop', (e) => { if (hasFiles(e)) e.preventDefault(); });
        }

        addFiles(fileList) {
            const files = Array.from(fileList || []);
            if (!files.length) return;
            if (!this.isChatVisible()) switchView('view-chat');
            const max = state.limits.maxAttachments;
            for (const file of files) {
                if (this.pending.length >= max) {
                    toast(`Máximo de ${max} anexos por mensagem.`, 'warning');
                    break;
                }
                const isImage = /^image\//.test(file.type);
                if (!isImage && file.size > state.limits.maxUploadBytes) {
                    toast(`"${truncate(file.name, 40)}" é grande demais (máximo ${formatBytes(state.limits.maxUploadBytes)}).`, 'error');
                    continue;
                }
                const item = {
                    localId: newId(),
                    file,
                    name: file.name || 'arquivo',
                    isImage,
                    status: 'uploading',
                    previewUrl: isImage ? URL.createObjectURL(file) : null,
                    att: null,
                    error: '',
                    controller: new AbortController(),
                };
                this.pending.push(item);
                item.promise = this.uploadItem(item);
            }
            this.renderTray();
            this.input.focus();
        }

        isChatVisible() {
            return $('view-chat').classList.contains('active');
        }

        async uploadItem(item) {
            try {
                const blob = item.isImage ? await prepareImage(item.file) : item.file;
                if (blob.size > state.limits.maxUploadBytes) {
                    throw new Error(`Arquivo grande demais (máximo ${formatBytes(state.limits.maxUploadBytes)}).`);
                }
                item.att = await uploadFile(blob, item.name, item.controller.signal);
                item.status = 'ready';
            } catch (err) {
                if (err && err.name === 'AbortError') return;
                item.status = 'error';
                item.error = (err && err.message) || 'Falha no envio.';
                toast(`"${truncate(item.name, 40)}": ${item.error}`, 'error');
            }
            this.renderTray();
        }

        removeItem(localId) {
            const item = this.pending.find(p => p.localId === localId);
            if (!item) return;
            if (item.status === 'uploading') item.controller.abort();
            if (item.previewUrl) URL.revokeObjectURL(item.previewUrl);
            this.pending = this.pending.filter(p => p !== item);
            this.renderTray();
        }

        clearTray() {
            this.pending.forEach(p => { if (p.previewUrl) URL.revokeObjectURL(p.previewUrl); });
            this.pending = [];
            this.renderTray();
        }

        renderTray() {
            this.tray.textContent = '';
            this.tray.hidden = !this.pending.length;
            this.pending.forEach(item => {
                const node = el('div', 'tray-item' + (item.isImage ? ' image' : '') + (item.status === 'error' ? ' error' : ''));
                node.title = item.status === 'error' ? `${item.name}: ${item.error}` : item.name;
                if (item.isImage) {
                    const img = el('img');
                    img.src = item.previewUrl;
                    img.alt = item.name;
                    node.appendChild(img);
                } else {
                    const iconBox = el('span', 'doc-chip-icon');
                    iconBox.innerHTML = icon('file');
                    const text = el('span', 'doc-chip-text');
                    text.appendChild(el('strong', '', item.name));
                    const meta = item.att && item.att.kind === 'document'
                        ? (item.att.pages ? `${item.att.pages} pág. · ` : '') + formatBytes(item.att.size_bytes)
                        : formatBytes(item.file.size);
                    text.appendChild(el('span', '', meta));
                    node.append(iconBox, text);
                }
                if (item.status === 'uploading') {
                    const status = el('div', 'tray-status');
                    status.appendChild(el('div', 'tray-spinner'));
                    node.appendChild(status);
                } else if (item.status === 'error') {
                    node.appendChild(el('div', 'tray-status', 'Erro'));
                }
                const remove = el('button', 'tray-remove', '✕');
                remove.type = 'button';
                remove.setAttribute('aria-label', `Remover ${item.name}`);
                remove.addEventListener('click', () => this.removeItem(item.localId));
                node.appendChild(remove);
                this.tray.appendChild(node);
            });
            this.updateSendState();
        }

        // ---------------- Voz ----------------
        initVoice() {
            const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
            if (!SR) return;
            this.voiceBtn.hidden = false;
            this.voiceBtn.addEventListener('click', () => {
                if (this.recognition) this.recognition.stop();
                else this.startVoice(SR);
            });
        }

        startVoice(SR) {
            let rec;
            try {
                rec = new SR();
            } catch (e) {
                toast('Não foi possível iniciar o ditado por voz.', 'warning');
                return;
            }
            rec.lang = 'pt-BR';
            rec.interimResults = true;
            rec.continuous = true;
            const base = this.input.value ? this.input.value.replace(/\s*$/, ' ') : '';
            let finalText = '';
            rec.onresult = (e) => {
                let interim = '';
                for (let i = e.resultIndex; i < e.results.length; i++) {
                    const r = e.results[i];
                    if (r.isFinal) finalText += r[0].transcript;
                    else interim += r[0].transcript;
                }
                this.input.value = base + finalText + interim;
                this.autoResize();
            };
            rec.onerror = (e) => {
                if (e.error === 'not-allowed' || e.error === 'service-not-allowed') toast('Permita o uso do microfone para ditar.', 'warning');
                else if (e.error !== 'aborted' && e.error !== 'no-speech') toast('O ditado por voz falhou.', 'warning');
            };
            rec.onend = () => {
                this.recognition = null;
                this.voiceBtn.setAttribute('aria-pressed', 'false');
                this.voiceBtn.title = 'Ditar por voz';
            };
            try {
                rec.start();
            } catch (e) {
                return;
            }
            this.recognition = rec;
            this.voiceBtn.setAttribute('aria-pressed', 'true');
            this.voiceBtn.title = 'Parar ditado';
        }

        speak(view, button) {
            if (!('speechSynthesis' in window)) {
                toast('Seu navegador não suporta leitura em voz alta.', 'warning');
                return;
            }
            const synth = window.speechSynthesis;
            if (this.speakingButton === button) {
                synth.cancel();
                return;
            }
            synth.cancel();
            const text = Markdown.plainText(view.bubble).trim();
            if (!text) return;
            const utter = new SpeechSynthesisUtterance(text.slice(0, 30000));
            utter.lang = 'pt-BR';
            const voice = synth.getVoices().find(v => (v.lang || '').toLowerCase().startsWith('pt'));
            if (voice) utter.voice = voice;
            utter.rate = 1.05;
            const reset = () => {
                button.classList.remove('speaking');
                if (this.speakingButton === button) this.speakingButton = null;
            };
            utter.onend = reset;
            utter.onerror = reset;
            if (this.speakingButton) this.speakingButton.classList.remove('speaking');
            this.speakingButton = button;
            button.classList.add('speaking');
            synth.speak(utter);
        }

        // ---------------- Renderização de mensagens ----------------
        removeWelcome() {
            const w = this.container.querySelector('#welcome-card');
            if (w) w.remove();
        }

        showWelcome() {
            this.container.textContent = '';
            if (this.welcomeTemplate) this.container.appendChild(this.welcomeTemplate.cloneNode(true));
            updateWelcome();
        }

        newChat() {
            if (state.abortController) state.abortController.abort();
            if (this.recognition) this.recognition.stop();
            if ('speechSynthesis' in window) window.speechSynthesis.cancel();
            state.setSession(newId(), false);
            this.showWelcome();
            this.setTitle('');
            this.clearTray();
            this.input.value = '';
            this.autoResize();
            if (this.sidebar) {
                this.sidebar.setActive(null);
                if (this.sidebar.isMobile()) this.sidebar.close();
            }
            switchView('view-chat');
            this.input.focus();
            this.updateScrollButton();
        }

        attachmentNodes(attachments) {
            const wrap = el('div', 'message-attachments');
            (attachments || []).forEach(att => {
                if (!att || !FILE_ID_RE.test(att.id || '')) return;
                if (att.kind === 'image') {
                    const img = el('img', 'attachment-thumb');
                    img.src = fileUrl(att.id);
                    img.alt = att.name || 'Imagem anexada';
                    img.loading = 'lazy';
                    img.dataset.lightbox = '';
                    img.dataset.name = att.name || '';
                    wrap.appendChild(img);
                } else {
                    const chip = el('span', 'doc-chip');
                    const iconBox = el('span', 'doc-chip-icon');
                    iconBox.innerHTML = icon('file');
                    const text = el('span', 'doc-chip-text');
                    text.appendChild(el('strong', '', att.name || 'documento'));
                    const meta = [att.pages ? `${att.pages} página(s)` : '', att.text_chars ? `${att.text_chars.toLocaleString('pt-BR')} caracteres` : '']
                        .filter(Boolean).join(' · ');
                    text.appendChild(el('span', '', meta || 'Documento'));
                    chip.append(iconBox, text);
                    wrap.appendChild(chip);
                }
            });
            return wrap;
        }

        appendUser(text, attachments, messageId) {
            const msg = el('div', 'chat-message user');
            if (messageId) msg.dataset.messageId = messageId;
            msg._text = text || '';
            msg._attachments = attachments || [];
            if (attachments && attachments.length) msg.appendChild(this.attachmentNodes(attachments));
            if (text) msg.appendChild(el('div', 'message-bubble', text));
            const actions = el('div', 'message-actions');
            const copy = iconButton('copy', 'Copiar mensagem');
            copy.addEventListener('click', () => this.flashCopy(copy, msg._text));
            const edit = iconButton('edit', 'Editar e reenviar');
            edit.addEventListener('click', () => this.startEdit(msg));
            actions.append(copy, edit);
            msg.appendChild(actions);
            this.container.appendChild(msg);
            return msg;
        }

        createAssistant(metaText) {
            const msg = el('div', 'chat-message assistant');
            const avatar = el('div', 'assistant-avatar');
            avatar.innerHTML = icon('logo', 16);
            const body = el('div', 'assistant-body');
            const meta = el('div', 'message-meta');
            meta.appendChild(el('span', '', 'NeuroCore'));
            const model = el('span', 'meta-model', metaText || '');
            meta.appendChild(model);
            const tools = el('div', 'tool-list');
            tools.style.display = 'contents';
            const bubble = el('div', 'message-bubble markdown-body');
            body.append(meta, tools, bubble);
            msg.append(avatar, body);
            this.container.appendChild(msg);
            return { msg, body, meta, model, tools, bubble, raw: '', gallery: null, userEl: null, request: null };
        }

        addActions(view) {
            if (view.actions) return;
            const actions = el('div', 'message-actions');
            const copy = iconButton('copy', 'Copiar resposta');
            copy.addEventListener('click', () => this.flashCopy(copy, view.raw || Markdown.plainText(view.bubble)));
            const speak = iconButton('speaker', 'Ouvir resposta');
            speak.addEventListener('click', () => this.speak(view, speak));
            const regen = iconButton('refresh', 'Gerar a resposta de novo');
            regen.classList.add('regen');
            regen.addEventListener('click', () => this.regenerate(view));
            actions.append(copy, speak, regen);
            view.body.appendChild(actions);
            view.actions = actions;
            view.msg._view = view;
        }

        refreshLastMarkers() {
            const assistants = Array.from(this.container.querySelectorAll('.chat-message.assistant'));
            assistants.forEach((m, i) => {
                const last = i === assistants.length - 1;
                m.classList.toggle('last', last);
                const regen = m.querySelector('.regen');
                if (regen) regen.hidden = !last;
            });
        }

        async flashCopy(button, text) {
            const ok = await copyText(text || '');
            button.innerHTML = icon(ok ? 'check' : 'copy');
            button.classList.toggle('done', ok);
            setTimeout(() => {
                button.innerHTML = icon('copy');
                button.classList.remove('done');
            }, 1500);
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

        addGallery(view, attachments) {
            const images = (attachments || []).filter(a => a && a.kind === 'image' && FILE_ID_RE.test(a.id || ''));
            if (!images.length) return;
            if (!view.gallery) {
                view.gallery = el('div', 'message-images');
                view.body.insertBefore(view.gallery, view.bubble);
            }
            images.forEach(att => {
                const item = el('figure', 'gallery-item');
                const img = el('img');
                img.src = fileUrl(att.id);
                img.alt = att.name || 'Imagem';
                img.loading = 'lazy';
                img.dataset.lightbox = '';
                img.dataset.name = att.name || '';
                if (att.width && att.height) {
                    img.width = att.width;
                    img.height = att.height;
                }
                const actions = el('div', 'gallery-actions');
                const download = el('a', 'icon-btn');
                download.href = fileUrl(att.id, true);
                download.setAttribute('download', att.name || 'imagem');
                download.title = 'Baixar';
                download.setAttribute('aria-label', 'Baixar imagem');
                download.innerHTML = icon('download');
                actions.appendChild(download);
                const caption = el('figcaption', 'gallery-caption', `${SOURCE_LABELS[att.source] || 'Imagem'} · ${att.name || ''}`);
                item.append(img, actions, caption);
                view.gallery.appendChild(item);
            });
            view.gallery.classList.toggle('single', view.gallery.children.length === 1);
        }

        addDiffButton(toolView, diff) {
            const btn = el('button', 'btn-copy', 'Ver diff');
            btn.type = 'button';
            btn.setAttribute('data-open-diff', '');
            btn._diff = diff;
            toolView.summary.insertBefore(btn, toolView.status);
        }

        addNotice(view, title, items) {
            if (!view.notice) {
                view.notice = el('details', 'notice');
                view.noticeTitle = el('summary', '', title);
                view.noticeList = el('ul');
                view.noticeItems = new Set();
                view.notice.append(view.noticeTitle, view.noticeList);
                view.body.insertBefore(view.notice, view.bubble);
            }
            view.noticeTitle.textContent = title;
            (items || []).forEach(t => {
                if (view.noticeItems.has(t)) return;
                view.noticeItems.add(t);
                view.noticeList.appendChild(el('li', '', t));
            });
        }

        removeFrom(node) {
            while (node) {
                const next = node.nextElementSibling;
                node.remove();
                node = next;
            }
        }

        // ---------------- Envio e streaming ----------------
        async send(rawText, options = {}) {
            const text = String(rawText || '').trim();
            if (state.isStreaming) return;
            if (!options.attachments && this.pending.some(p => p.status === 'uploading')) {
                this.sendBtn.disabled = true;
                await Promise.allSettled(this.pending.map(p => p.promise));
            }
            const attachments = options.attachments ||
                this.pending.filter(p => p.status === 'ready' && p.att).map(p => p.att);
            if (!text && !attachments.length) {
                if (this.pending.some(p => p.status === 'error')) toast('Remova os anexos com erro ou envie de novo.', 'warning');
                return;
            }
            if (this.recognition) this.recognition.stop();
            if (!options.attachments) this.clearTray();
            if (!options.keepInput) {
                this.input.value = '';
                this.autoResize();
            }
            switchView('view-chat');
            this.removeWelcome();

            const userEl = this.appendUser(text, attachments);
            const view = this.createAssistant(state.provider === 'local' ? 'modo local' : state.model);
            view.userEl = userEl;
            view.request = { text, attachments };
            let typing = this.addTypingIndicator(view);
            this.scrollToBottom(true);
            this.setStreaming(true);
            state.abortController = new AbortController();
            state.markSessionUsed();

            let renderPending = false;
            let finished = false;
            const toolViews = {};
            const renderContent = (final) => {
                renderPending = false;
                if (finished) return;
                if (final) finished = true;
                view.bubble.innerHTML = Markdown.render(view.raw);
                view.bubble.classList.toggle('streaming-cursor', !final);
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
                    body: Object.assign({
                        prompt: text,
                        session_id: state.sessionId,
                        attachments: attachments.map(a => a.id),
                    }, state.agentPayload()),
                });
                await readSSE(res, (event, data) => {
                    switch (event) {
                        case 'connected':
                            if (data.user_message_id) userEl.dataset.messageId = data.user_message_id;
                            if (data.title) {
                                this.setTitle(data.title);
                                if (this.sidebar) {
                                    this.sidebar.upsert(state.sessionId, data.title);
                                    this.sidebar.setActive(state.sessionId);
                                }
                            }
                            break;
                        case 'token':
                            dropTyping();
                            view.raw += data.token || '';
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
                            this.addGallery(view, data.attachments);
                            if (!typing && !view.raw) typing = this.addTypingIndicator(view);
                            this.scrollToBottom(false);
                            break;
                        }
                        case 'fallback':
                            this.addNotice(view, `Aviso: resposta gerada por "${data.provider_used}" (fallback). Ver detalhes`, data.fallback_trace || []);
                            break;
                        case 'error':
                            view.raw += `\n\n**Erro:** ${data.message || 'falha desconhecida'}`;
                            scheduleRender();
                            break;
                        case 'done':
                            if (data.model_used) view.model.textContent = data.provider_used === 'local' ? 'modo local' : data.model_used;
                            break;
                        default:
                            break;
                    }
                });
                dropTyping();
                if (!view.raw && !Object.keys(toolViews).length) view.raw = '_Sem resposta do modelo._';
                renderContent(true);
            } catch (err) {
                dropTyping();
                if (err && err.name === 'AbortError') {
                    view.raw += (view.raw ? '\n\n' : '') + '_Resposta interrompida._';
                    renderContent(true);
                } else {
                    finished = true;
                    view.msg.classList.add('error');
                    view.bubble.classList.remove('streaming-cursor');
                    view.bubble.textContent = (err && err.message) || 'Erro inesperado.';
                    view.raw = view.bubble.textContent;
                }
            } finally {
                state.abortController = null;
                this.addActions(view);
                this.setStreaming(false);
                this.refreshLastMarkers();
                this.scrollToBottom(false);
                if (this.sidebar) this.sidebar.load();
                if (window.matchMedia('(hover: hover)').matches) this.input.focus();
            }
        }

        async rewindTo(userEl) {
            const id = userEl && userEl.dataset.messageId;
            if (!id) throw new Error('Esta mensagem ainda não foi registrada; tente de novo em instantes.');
            await api(`${API}/sessions/${encodeURIComponent(state.sessionId)}/rewind`, { method: 'POST', body: { message_id: id } });
            this.removeFrom(userEl);
        }

        async regenerate(view) {
            if (state.isStreaming || !view.userEl || !view.request) return;
            try {
                await this.rewindTo(view.userEl);
            } catch (err) {
                toast('Não foi possível gerar de novo: ' + err.message, 'error');
                return;
            }
            this.send(view.request.text, { attachments: view.request.attachments, keepInput: true });
        }

        startEdit(userEl) {
            if (state.isStreaming) return;
            const bubble = userEl.querySelector('.message-bubble');
            const actions = userEl.querySelector('.message-actions');
            const box = el('div', 'edit-box');
            const area = el('textarea');
            area.value = userEl._text || '';
            area.setAttribute('aria-label', 'Editar mensagem');
            const buttons = el('div', 'edit-box-actions');
            const cancel = el('button', 'btn-secondary', 'Cancelar');
            cancel.type = 'button';
            const save = el('button', 'btn-primary', 'Enviar');
            save.type = 'button';
            buttons.append(cancel, save);
            box.append(area, buttons);
            if (bubble) bubble.hidden = true;
            if (actions) actions.hidden = true;
            userEl.appendChild(box);
            area.focus();
            area.setSelectionRange(area.value.length, area.value.length);
            const restore = () => {
                box.remove();
                if (bubble) bubble.hidden = false;
                if (actions) actions.hidden = false;
            };
            cancel.addEventListener('click', restore);
            area.addEventListener('keydown', (e) => {
                if (e.key === 'Escape') { e.stopPropagation(); restore(); }
                if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); save.click(); }
            });
            save.addEventListener('click', async () => {
                const newText = area.value.trim();
                if (!newText && !(userEl._attachments || []).length) return;
                save.disabled = true;
                try {
                    await this.rewindTo(userEl);
                } catch (err) {
                    save.disabled = false;
                    toast('Não foi possível editar: ' + err.message, 'error');
                    return;
                }
                this.send(newText, { attachments: userEl._attachments || [], keepInput: true });
            });
        }

        // ---------------- Histórico ----------------
        async openConversation(id) {
            if (state.isStreaming) this.stop();
            state.setSession(id, true);
            if (this.sidebar) this.sidebar.setActive(id);
            switchView('view-chat');
            this.container.textContent = '';
            let session;
            try {
                session = await api(`${API}/sessions/${encodeURIComponent(id)}`);
            } catch (err) {
                toast('Não foi possível abrir a conversa: ' + err.message, 'error');
                this.newChat();
                return;
            }
            this.renderHistory(session);
        }

        renderHistory(session) {
            this.container.textContent = '';
            const messages = (session && session.messages) || [];
            if (!messages.some(m => m.role === 'user')) {
                this.showWelcome();
                this.setTitle('');
                return;
            }
            this.setTitle(session.title || '');
            let view = null;
            let lastUser = null;
            const pending = {};
            messages.forEach(m => {
                if (m.role === 'user') {
                    if (view) this.addActions(view);
                    const userEl = this.appendUser(m.content || '', m.attachments || [], m.id);
                    lastUser = { el: userEl, request: { text: m.content || '', attachments: m.attachments || [] } };
                    view = null;
                } else if (m.role === 'assistant') {
                    if (!view) {
                        view = this.createAssistant('');
                        if (lastUser) {
                            view.userEl = lastUser.el;
                            view.request = lastUser.request;
                        }
                    }
                    (m.tool_calls || []).forEach(tc => { pending[tc.id] = this.addToolCard(view, tc.name, tc.arguments); });
                    if (m.content) {
                        view.raw += (view.raw ? '\n\n' : '') + m.content;
                        view.bubble.innerHTML = Markdown.render(view.raw);
                    }
                } else if (m.role === 'tool') {
                    const tv = pending[m.tool_call_id];
                    if (tv) {
                        tv.status.className = 'tool-status ' + (m.is_error ? 'error' : 'success');
                        tv.status.textContent = m.is_error ? 'erro' : 'ok';
                        tv.output.textContent = truncate(m.content || '', 20000);
                    }
                    if (view) this.addGallery(view, m.attachments);
                }
            });
            if (view) this.addActions(view);
            this.refreshLastMarkers();
            requestAnimationFrame(() => this.scrollToBottom(true));
        }

        async restoreCurrent() {
            if (!state.sessionUsed()) return;
            try {
                const session = await api(`${API}/sessions/${encodeURIComponent(state.sessionId)}`);
                this.renderHistory(session);
                if (this.sidebar) this.sidebar.setActive(state.sessionId);
            } catch (e) {
                state.setSession(newId(), false);
            }
        }

        async exportConversation() {
            if (!state.sessionUsed()) {
                toast('Ainda não há mensagens para exportar.', 'warning');
                return;
            }
            let session;
            try {
                session = await api(`${API}/sessions/${encodeURIComponent(state.sessionId)}`);
            } catch (err) {
                toast('Não foi possível exportar: ' + err.message, 'error');
                return;
            }
            const title = session.title || 'Conversa';
            const origin = window.location.origin;
            const lines = [`# ${title}`, '', `_Exportado do NeuroCore em ${new Date().toLocaleString('pt-BR')}_`, ''];
            let assistantOpen = false;
            (session.messages || []).forEach(m => {
                if (m.role === 'user') {
                    assistantOpen = false;
                    lines.push('## Você', '');
                    if (m.content) lines.push(m.content, '');
                    (m.attachments || []).forEach(a => {
                        lines.push(a.kind === 'image' ? `![${a.name}](${origin}${API}/files/${a.id})` : `- Anexo: ${a.name}`);
                    });
                    lines.push('');
                } else if (m.role === 'assistant' && m.content) {
                    if (!assistantOpen) lines.push('## NeuroCore', '');
                    assistantOpen = true;
                    lines.push(m.content, '');
                } else if (m.role === 'tool') {
                    (m.attachments || []).filter(a => a.kind === 'image').forEach(a => {
                        if (!assistantOpen) lines.push('## NeuroCore', '');
                        assistantOpen = true;
                        lines.push(`![${a.name}](${origin}${API}/files/${a.id})`, '');
                    });
                }
            });
            const blob = new Blob([lines.join('\n')], { type: 'text/markdown;charset=utf-8' });
            const url = URL.createObjectURL(blob);
            const link = el('a');
            link.href = url;
            link.download = `${slugify(title)}.md`;
            document.body.appendChild(link);
            link.click();
            link.remove();
            setTimeout(() => URL.revokeObjectURL(url), 2000);
            toast('Conversa exportada.');
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
            $('btn-new-task').addEventListener('click', () => Modals.open('new-task-modal'));
            $('btn-refresh-tasks').addEventListener('click', () => this.load(true));
            $('task-form').addEventListener('submit', (e) => {
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
            const status = $('activity-status');
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
            const title = $('task-title-input');
            const prompt = $('task-prompt-input');
            const priority = $('task-priority-input');
            if (!title.value.trim() || !prompt.value.trim()) {
                toast('Preencha o título e a instrução.', 'warning');
                return;
            }
            const submit = $('btn-submit-task');
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
            const payload = state.agentPayload();
            try {
                await api(`${API}/tasks/${encodeURIComponent(id)}/run`, {
                    method: 'POST',
                    body: { provider: payload.provider, model: payload.model, api_key: payload.api_key },
                });
                toast('Tarefa enviada para execução.');
                await this.load();
            } catch (err) {
                toast('Erro ao executar: ' + err.message, 'error');
                btn.disabled = false;
            }
        }

        async remove(id) {
            const task = this.tasks.find(t => t.task_id === id);
            const ok = await Confirm.ask(`A tarefa "${truncate(task ? task.title : id, 60)}" será excluída.`, {
                title: 'Excluir tarefa?', confirmText: 'Excluir',
            });
            if (!ok) return;
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
            $('task-result-title').textContent = task.title;
            const body = $('task-result-body');
            const md = `**Instrução:**\n\n${task.prompt}\n\n---\n\n**Resultado${task.provider_used ? ' (' + task.provider_used + ')' : ''}:**\n\n${task.result_summary || '_Sem resultado ainda._'}`;
            body.innerHTML = Markdown.render(md);
            const images = (task.result_attachments || []).filter(a => a && a.kind === 'image' && FILE_ID_RE.test(a.id || ''));
            if (images.length) {
                const gallery = el('div', 'message-images');
                images.forEach(att => {
                    const item = el('figure', 'gallery-item');
                    const img = el('img');
                    img.src = fileUrl(att.id);
                    img.alt = att.name || 'Imagem';
                    img.dataset.lightbox = '';
                    img.dataset.name = att.name || '';
                    item.appendChild(img);
                    gallery.appendChild(item);
                });
                body.appendChild(gallery);
            }
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
                addBtn('Executando...', 'noop', 'btn-secondary').disabled = true;
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
                const list = $(`list-${status}`);
                const count = $(`count-${status}`);
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
    // Navegação, saúde do servidor e inicialização
    // ======================================================================
    let kanban = null;

    function switchView(viewId) {
        document.querySelectorAll('.nav-item[data-view]').forEach(tab => {
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
        const hash = viewId === 'view-chat' ? '' : '#' + viewId.replace('view-', '');
        try { history.replaceState(null, '', window.location.pathname + hash); } catch (e) { /* ignore */ }
    }

    async function checkHealth() {
        try {
            state.health = await api(`${API}/health`);
        } catch (err) {
            state.health = null;
        }
        const health = state.health;
        if (health && health.limits) {
            state.limits.maxUploadBytes = health.limits.max_upload_bytes || state.limits.maxUploadBytes;
            state.limits.maxAttachments = health.limits.max_attachments_per_message || state.limits.maxAttachments;
        }
        state.emit('healthChanged');
        $('workspace-name').textContent = health ? `workspace: ${health.workspace_root}` : 'servidor offline';
        const info = $('input-info');
        if (health && !health.dangerous_tools_enabled) {
            info.dataset.locked = '1';
            info.textContent = 'Modo seguro: sem comandos nem edição de arquivos no servidor';
        }
        if (health && health.auth_required && !state.accessToken) {
            toast('Este servidor exige um token de acesso. Informe-o em Conexão.', 'warning');
        }
    }

    function isTyping(target) {
        return target && (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName));
    }

    document.addEventListener('DOMContentLoaded', () => {
        Modals.init();
        Confirm.init();
        Lightbox.init();

        const diffViewer = new DiffViewer();
        const connection = new ConnectionPanel();
        kanban = new KanbanManager();
        const sidebar = new Sidebar();
        const chat = new ChatClient(diffViewer, sidebar);
        sidebar.chat = chat;
        const settings = new SettingsPanel(() => {
            sidebar.sessions = [];
            sidebar.render();
            chat.newChat();
        });

        document.querySelectorAll('.nav-item[data-view]').forEach(tab => {
            tab.addEventListener('click', () => {
                switchView(tab.getAttribute('data-view'));
                if (sidebar.isMobile()) sidebar.close();
            });
        });

        state.on('unauthorized', () => {
            toast('Token de acesso inválido ou ausente. Informe-o em Conexão.', 'error');
        });

        document.addEventListener('keydown', (e) => {
            const mod = e.ctrlKey || e.metaKey;
            if (mod && e.shiftKey && (e.key === 'O' || e.key === 'o')) {
                e.preventDefault();
                chat.newChat();
            } else if (mod && !e.shiftKey && (e.key === 'k' || e.key === 'K')) {
                e.preventDefault();
                sidebar.focusSearch();
            } else if (e.key === 'Escape' && state.isStreaming && !Modals.anyOpen()) {
                chat.stop();
            } else if (e.key === '/' && !mod && !isTyping(e.target) && !Modals.anyOpen()) {
                e.preventDefault();
                switchView('view-chat');
                chat.input.focus();
            }
        });

        window.NeuroCore = {
            state,
            authHeaders: () => state.authHeaders(),
            renderMarkdown: (s) => Markdown.render(s),
            diffViewer,
            chat,
            sidebar,
            connection,
            settings,
        };

        diffViewer.load(
            "def run_server(port=8000):\n    print('Starting server on port', port)\n    return True",
            "def run_server(port: int = 8000, host: str = '127.0.0.1') -> bool:\n    \"\"\"Inicia o servidor FastAPI do NeuroCore.\"\"\"\n    logger.info(f'Starting NeuroCore server on {host}:{port}')\n    return True"
        );

        const hash = (window.location.hash || '').replace('#', '');
        if (['diff', 'tasks'].includes(hash)) switchView('view-' + hash);

        updateWelcome();
        checkHealth().then(() => {
            sidebar.load();
            chat.restoreCurrent();
        });
        chat.input.focus();
    });
})();
