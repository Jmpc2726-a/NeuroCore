/**
 * NeuroCore - Botão de download de documentos
 *
 * Observa o chat e transforma qualquer referência a um documento gerado
 * (link Markdown, link HTML, endereço completo, caminho errado como /mnt/data/x.pdf
 * ou só o nome do arquivo) em um botão que baixa de /api/v1/documents/<arquivo>.
 * Funciona independente de como o app.js desenha as mensagens.
 */
(function () {
  'use strict';

  var CONTAINER_ID = 'chat-messages-container';
  var API_BASE = '/api/v1/documents/';

  // Todo arquivo gerado pelo servidor tem o formato <titulo>-<6 hex>.<pdf|docx|md>.
  // Assim reconhecemos o link mesmo quando o modelo escreve o caminho errado
  // (por exemplo /mnt/data/arquivo.pdf ou sandbox:/mnt/data/arquivo.pdf).
  var NAME = '[a-z0-9-]{1,80}-[0-9a-f]{6}\\.(?:pdf|docx|md)';

  // Grupos: 1=rótulo, 2=nome (link Markdown) | 3=nome (caminho solto)
  var TEXT_RE = new RegExp(
    '\\[([^\\]]*)\\]\\(\\s*(?:[^\\s()]*\\/)?(' + NAME + ')(?:[?#][^\\s)]*)?\\s*\\)' +
    '|(?:sandbox:)?(?:[^\\s()\\[\\]]*\\/)?(' + NAME + ')',
    'gi'
  );
  var HREF_RE = new RegExp('(' + NAME + ')(?:[?#].*)?$', 'i');
  var QUICK_RE = /\.(?:pdf|docx|md)/i;

  var LABELS = { pdf: 'PDF', docx: 'Word', md: 'Markdown' };

  var ICON =
    '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>' +
    '<polyline points="14 2 14 8 20 8"/>' +
    '<line x1="12" y1="11" x2="12" y2="17"/>' +
    '<polyline points="9 14 12 17 15 14"/></svg>';

  var CSS =
    '.nc-doc-card{display:flex;align-items:center;gap:.8rem;margin:.75rem 0;' +
    'padding:.7rem 1rem;max-width:380px;background:var(--bg-surface,#fff);' +
    'border:1px solid var(--border-color,#D1FAE5);border-radius:var(--radius-md,10px);' +
    'color:var(--text-primary,#064E3B);text-decoration:none;cursor:pointer;' +
    'box-shadow:var(--shadow-sm,none);transition:border-color .15s ease,transform .15s ease}' +
    '.nc-doc-card:hover{border-color:var(--accent-terracotta,#10B981);transform:translateY(-1px)}' +
    '.nc-doc-icon{display:flex;align-items:center;justify-content:center;flex-shrink:0;' +
    'width:40px;height:40px;border-radius:var(--radius-sm,6px);' +
    'background:var(--bg-surface-hover,#ECFDF5);color:var(--accent-terracotta,#10B981)}' +
    '.nc-doc-info{display:flex;flex-direction:column;min-width:0}' +
    '.nc-doc-name{font-weight:600;font-size:.88rem;overflow:hidden;' +
    'text-overflow:ellipsis;white-space:nowrap}' +
    '.nc-doc-action{font-size:.78rem;color:var(--text-secondary,#047857)}' +
    '.nc-doc-card.nc-doc-error .nc-doc-action{color:var(--accent-rose,#EF4444)}';

  function injectStyle() {
    if (document.getElementById('nc-doc-style')) return;
    var style = document.createElement('style');
    style.id = 'nc-doc-style';
    style.textContent = CSS;
    document.head.appendChild(style);
  }

  function makeCard(name) {
    var ext = name.split('.').pop().toLowerCase();
    var a = document.createElement('a');
    a.className = 'nc-doc-card';
    a.href = API_BASE + name;
    a.setAttribute('download', name);
    a.setAttribute('data-nc-doc', name);

    var icon = document.createElement('span');
    icon.className = 'nc-doc-icon';
    icon.innerHTML = ICON; // constante interna, sem conteúdo do usuário

    var info = document.createElement('span');
    info.className = 'nc-doc-info';
    var title = document.createElement('span');
    title.className = 'nc-doc-name';
    title.textContent = name;
    var action = document.createElement('span');
    action.className = 'nc-doc-action';
    action.textContent = 'Baixar ' + (LABELS[ext] || ext.toUpperCase());

    info.appendChild(title);
    info.appendChild(action);
    a.appendChild(icon);
    a.appendChild(info);
    return a;
  }

  // Troca <a href="...x.pdf"> por um botão
  function processAnchors(root) {
    var anchors = root.querySelectorAll('a');
    for (var i = 0; i < anchors.length; i++) {
      var a = anchors[i];
      if (a.classList.contains('nc-doc-card')) continue;
      var m = HREF_RE.exec(a.getAttribute('href') || '');
      if (!m) continue;
      a.replaceWith(makeCard(m[1].toLowerCase()));
    }
  }

  // Troca links Markdown em texto puro e caminhos soltos por um botão
  function processTextNodes(root) {
    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode: function (node) {
        var p = node.parentElement;
        if (!p || p.closest('pre, script, style, a, .nc-doc-card')) {
          return NodeFilter.FILTER_REJECT;
        }
        return !QUICK_RE.test(node.nodeValue)
          ? NodeFilter.FILTER_REJECT
          : NodeFilter.FILTER_ACCEPT;
      }
    });

    var nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);

    nodes.forEach(function (node) {
      var text = node.nodeValue;
      var frag = document.createDocumentFragment();
      var last = 0;
      var found = false;
      var m;
      TEXT_RE.lastIndex = 0;
      while ((m = TEXT_RE.exec(text)) !== null) {
        found = true;
        var name = (m[2] || m[3]).toLowerCase();
        if (m.index > last) frag.appendChild(document.createTextNode(text.slice(last, m.index)));
        frag.appendChild(makeCard(name));
        last = m.index + m[0].length;
      }
      if (!found) return;
      if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
      node.replaceWith(frag);
    });
  }

  // Baixa o arquivo via fetch para tratar o caso de arquivo expirado (servidor reiniciado)
  function downloadFile(card) {
    var path = card.getAttribute('href');
    var name = card.getAttribute('data-nc-doc');
    var action = card.querySelector('.nc-doc-action');
    var original = action ? action.textContent : '';

    function setStatus(text, isError) {
      if (!action) return;
      action.textContent = text;
      card.classList.toggle('nc-doc-error', !!isError);
    }

    setStatus('Baixando...', false);
    fetch(path)
      .then(function (res) {
        if (!res.ok) throw new Error('HTTP ' + res.status);
        return res.blob();
      })
      .then(function (blob) {
        var url = URL.createObjectURL(blob);
        var link = document.createElement('a');
        link.href = url;
        link.download = name;
        document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(function () { URL.revokeObjectURL(url); }, 2000);
        setStatus(original, false);
      })
      .catch(function () {
        setStatus('Arquivo indisponível. Peça para gerar de novo.', true);
        setTimeout(function () { setStatus(original, false); }, 6000);
      });
  }

  document.addEventListener('click', function (event) {
    var card = event.target.closest && event.target.closest('.nc-doc-card');
    if (!card) return;
    event.preventDefault();
    downloadFile(card);
  });

  var observer = null;
  var scheduled = false;
  var OPTIONS = { childList: true, subtree: true, characterData: true };

  function run() {
    scheduled = false;
    var container = document.getElementById(CONTAINER_ID);
    if (!container) return;
    if (observer) observer.disconnect();
    try {
      processAnchors(container);
      processTextNodes(container);
    } finally {
      if (observer) observer.observe(container, OPTIONS);
    }
  }

  function schedule() {
    if (scheduled) return;
    scheduled = true;
    (window.requestAnimationFrame || window.setTimeout)(run, 16);
  }

  function init() {
    injectStyle();
    var container = document.getElementById(CONTAINER_ID);
    if (!container) return;
    observer = new MutationObserver(schedule);
    observer.observe(container, OPTIONS);
    run();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
