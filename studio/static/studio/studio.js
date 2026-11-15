/* Attnd Studio editor (console). Vanilla JS, no framework. Data from the server is only ever inserted with
   textContent / DOM properties (never innerHTML), except the app preview, which is our own escaped template. */
(function () {
  'use strict';
  var root = document.querySelector('[data-studio]');
  if (!root) return;

  var $ = function (sel, scope) { return (scope || root).querySelector(sel); };
  var csrf = $('input[name=csrfmiddlewaretoken]').value;
  var state = JSON.parse(document.getElementById('studio-state-json').textContent);
  var origin = root.dataset.sitesOrigin;
  var frame = $('[data-preview-frame]');
  var selection = null, selecting = false, runPreview = '', lastRev = -1, pollTimer = null;
  var code = { files: null, current: null, dirty: false, number: null };

  function url(template, id) { return template.replace(/\/0\/(?=[a-z-]*\/?$)/, '/' + id + '/'); }
  function el(tag, cls, text) { var n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }
  function post(target, data) {
    var body = data instanceof FormData ? data : new FormData();
    if (!(data instanceof FormData)) Object.keys(data || {}).forEach(function (k) { body.append(k, data[k]); });
    return fetch(target, { method: 'POST', body: body, credentials: 'same-origin',
                           headers: { 'X-CSRFToken': csrf, Accept: 'application/json' } })
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (j) { j._status = r.status; return j; }); });
  }
  function getJSON(target) {
    return fetch(target, { credentials: 'same-origin', headers: { Accept: 'application/json' } }).then(function (r) { return r.json(); });
  }
  function formPost(action, fields) {  // a real form submit, for actions that redirect with a message
    var form = el('form'); form.method = 'post'; form.action = action; form.hidden = true;
    fields = Object.assign({ csrfmiddlewaretoken: csrf }, fields || {});
    Object.keys(fields).forEach(function (k) { var i = el('input'); i.type = 'hidden'; i.name = k; i.value = fields[k]; form.append(i); });
    document.body.append(form); form.submit();
  }

  // ---------------------------------------------------------------- preview
  var pageSelect = $('[data-page-select]');
  function pagePath() { return pageSelect.value || ''; }
  function renderPages() {
    var current = pageSelect.value;
    pageSelect.replaceChildren();
    (state.pages.length ? state.pages : ['index.html']).forEach(function (p) {
      var value = p === 'index.html' ? '' : p.replace(/\.html$/, '');
      var o = el('option', '', p === 'index.html' ? 'Home (index.html)' : p); o.value = value; pageSelect.append(o);
    });
    if ([].some.call(pageSelect.options, function (o) { return o.value === current; })) pageSelect.value = current;
    $('[data-empty-site]').hidden = state.pages.length > 0;
  }
  function previewBase() { return runPreview || state.preview; }
  function loadFrame() {
    if (!frame || !previewBase()) return;
    frame.src = previewBase() + pagePath();
    $('[data-open-preview]').href = state.preview + pagePath();
    setSelecting(false);
  }
  pageSelect.addEventListener('change', loadFrame);
  $('[data-reload]').addEventListener('click', loadFrame);
  root.querySelectorAll('[data-width]').forEach(function (b) {
    b.addEventListener('click', function () {
      root.querySelectorAll('[data-width]').forEach(function (x) { x.setAttribute('aria-pressed', String(x === b)); });
      if (frame) frame.style.width = b.dataset.width === '0' ? '100%' : b.dataset.width + 'px';
    });
  });
  if (frame) frame.style.width = '390px';

  // Element selection through the preview's bridge (postMessage; the sandboxed frame's origin is "null").
  var selectBtn = $('[data-select-toggle]');
  function setSelecting(on) {
    selecting = on; selectBtn.setAttribute('aria-pressed', String(on));
    if (frame && frame.contentWindow) frame.contentWindow.postMessage({ type: 'attnd-studio-select', on: on }, '*');
  }
  selectBtn.addEventListener('click', function () { setSelecting(!selecting); });
  window.addEventListener('message', function (e) {
    if (!frame || e.source !== frame.contentWindow || !e.data || typeof e.data !== 'object') return;
    if (e.data.type === 'attnd-studio-selected') {
      selecting = false; selectBtn.setAttribute('aria-pressed', 'false');
      selection = { page: String(e.data.page || pagePath()), selector: String(e.data.selector || ''), tag: String(e.data.tag || ''),
                    text: String(e.data.text || ''), html: String(e.data.html || '') };
      $('[data-selection-label]').textContent = selection.tag + (selection.text ? ' “' + selection.text.slice(0, 50) + '”' : '');
      $('[data-selection]').hidden = false;
      $('#studio-prompt').focus();
    } else if (e.data.type === 'attnd-studio-select-cancelled') {
      setSelecting(false);
    }
  });
  $('[data-clear-selection]').addEventListener('click', function () { selection = null; $('[data-selection]').hidden = true; });

  $('[data-preview-link]').addEventListener('click', function (e) {
    var b = e.currentTarget;
    post(root.dataset.previewLinkUrl).then(function (j) {
      if (!j.url) return;
      navigator.clipboard.writeText(j.url).then(function () { b.textContent = 'Copied (valid ' + j.days + ' days)'; },
        function () { window.prompt('Private preview link', j.url); });
    });
  });

  // ---------------------------------------------------------------- chat + runs
  var log = $('[data-chat-log]'), runPanel = $('[data-run-panel]'), steps = $('[data-run-steps]');
  var chatForm = $('[data-chat-form]'), prompt = $('#studio-prompt'), sendBtn = $('[data-chat-send]'), chatError = $('[data-chat-error]');
  function addMessage(role, text) {
    var li = el('li', 'studio-msg studio-msg--' + role);
    li.append(el('div', 'studio-msg__text', text));
    log.append(li); li.scrollIntoView({ block: 'nearest' });
  }
  root.querySelectorAll('[data-idea]').forEach(function (b) {
    b.addEventListener('click', function () { prompt.value = b.dataset.idea; prompt.focus(); });
  });
  function send(text) {
    chatError.hidden = true;
    var data = { text: text };
    if (selection) data.selection = JSON.stringify(selection);
    sendBtn.disabled = true;
    return post(root.dataset.chatUrl, data).then(function (j) {
      if (!j.run) { sendBtn.disabled = false; chatError.textContent = j.error || 'Something went wrong.'; chatError.hidden = false; return; }
      addMessage('user', text);
      prompt.value = ''; selection = null; $('[data-selection]').hidden = true;
      watchRun(j.run.id);
    }).catch(function () { sendBtn.disabled = false; chatError.textContent = 'Could not reach Attnd. Try again.'; chatError.hidden = false; });
  }
  chatForm.addEventListener('submit', function (e) { e.preventDefault(); if (prompt.value.trim()) send(prompt.value.trim()); });
  prompt.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); chatForm.requestSubmit(); }
  });

  function watchRun(id) {
    runPanel.hidden = false; sendBtn.disabled = true; steps.replaceChildren(); lastRev = -1;
    clearTimeout(pollTimer);
    (function poll() {
      getJSON(url(root.dataset.runUrl, id)).then(function (j) {
        var run = j.run;
        steps.replaceChildren.apply(steps, run.steps.map(function (s) { return el('li', '', s.text); }));
        if (run.status === 'queued' || run.status === 'running') {
          if (run.preview && run.files_rev !== lastRev) {
            var first = lastRev === -1; lastRev = run.files_rev; runPreview = run.preview;
            if (!first || run.files_rev > 0) loadFrame();
          }
          pollTimer = setTimeout(poll, 1500);
          return;
        }
        runPanel.hidden = true; sendBtn.disabled = false; runPreview = '';
        addMessage('assistant', run.status === 'done' ? run.reply : 'Sorry, that didn\'t work: ' + run.error);
        refresh();
      }).catch(function () { pollTimer = setTimeout(poll, 4000); });
    })();
  }

  function refresh() {
    return getJSON(root.dataset.stateUrl).then(function (s) {
      state = s; renderAll(); loadFrame(); code.files = null;
      if (!$('#studio-code').hidden) loadCode();
      refreshApp();
    });
  }

  // ---------------------------------------------------------------- code
  var fileList = $('[data-file-list]'), editor = $('[data-code-editor]'), saveBtn = $('[data-save-code]'), codeStatus = $('[data-code-status]');
  function loadCode() {
    return getJSON(root.dataset.codeUrl).then(function (j) {
      code.files = j.files; code.number = j.number; code.dirty = false;
      if (!code.current || !(code.current in code.files)) code.current = Object.keys(code.files).sort()[0] || null;
      renderCode();
    });
  }
  function renderCode() {
    fileList.replaceChildren();
    Object.keys(code.files || {}).sort().forEach(function (p) {
      var li = el('li'); var b = el('button', 'button button--ghost button--small mono', p); b.type = 'button';
      if (p === code.current) b.setAttribute('aria-current', 'true');
      b.addEventListener('click', function () { code.current = p; renderCode(); });
      li.append(b); fileList.append(li);
    });
    if (!Object.keys(code.files || {}).length) fileList.append(el('li', 'muted', 'No files yet.'));
    $('[data-code-path]').textContent = code.current || '—';
    editor.disabled = !code.current;
    editor.value = code.current ? code.files[code.current] : '';
    saveBtn.disabled = !code.dirty;
  }
  editor.addEventListener('input', function () {
    if (!code.current) return;
    code.files[code.current] = editor.value; code.dirty = true; saveBtn.disabled = false; codeStatus.textContent = 'Unsaved changes';
  });
  editor.addEventListener('keydown', function (e) {  // Tab inserts two spaces
    if (e.key === 'Tab' && !e.shiftKey) { e.preventDefault(); editor.setRangeText('  ', editor.selectionStart, editor.selectionEnd, 'end'); editor.dispatchEvent(new Event('input')); }
  });
  $('[data-add-file]').addEventListener('click', function () {
    var input = $('[data-new-file]'), p = input.value.trim().toLowerCase();
    if (!p || !code.files) return;
    if (!(p in code.files)) code.files[p] = p.endsWith('.html') ? '<!doctype html>\n<html lang="en">\n<head>\n  <meta charset="utf-8">\n  <meta name="viewport" content="width=device-width, initial-scale=1">\n  <title></title>\n  <link rel="stylesheet" href="styles.css">\n</head>\n<body>\n\n</body>\n</html>\n' : '';
    code.current = p; code.dirty = true; input.value = ''; renderCode();
  });
  $('[data-delete-file]').addEventListener('click', function () {
    if (!code.current || !window.confirm('Delete ' + code.current + '? (Saved as a new version; you can undo.)')) return;
    delete code.files[code.current]; code.current = null; code.dirty = true; renderCode();
  });
  saveBtn.addEventListener('click', function () {
    saveBtn.disabled = true; codeStatus.textContent = 'Saving…';
    post(root.dataset.codeUrl, { files: JSON.stringify(code.files), summary: $('[data-code-summary]').value }).then(function (j) {
      if (j.error) { codeStatus.textContent = j.error; saveBtn.disabled = false; return; }
      code.dirty = false; $('[data-code-summary]').value = '';
      codeStatus.textContent = j.saved ? 'Saved as version ' + j.number : 'No changes';
      refresh();
    });
  });
  document.querySelector('[data-tab-target="#studio-code"]').addEventListener('click', function () { if (!code.files) loadCode(); });
  window.addEventListener('beforeunload', function (e) { if (code.dirty) { e.preventDefault(); e.returnValue = ''; } });

  // ---------------------------------------------------------------- assets + flyer
  var grid = $('[data-asset-grid]'), uploadStatus = $('[data-upload-status]');
  function thumb(a) {
    var box = el('div', 'studio-thumb');
    if ((a.kind === 'image' || a.kind === 'svg') && a.url) { var img = el('img'); img.src = a.url; img.alt = a.alt || ''; img.loading = 'lazy'; box.append(img); }
    else box.append(el('span', 'studio-thumb__kind', a.kind.toUpperCase()));
    return box;
  }
  function assetUrl(id) { return root.dataset.assetUrl.replace('00000000-0000-0000-0000-000000000000', id); }
  function renderAssets() {
    grid.replaceChildren();
    if (!state.assets.length) grid.append(el('p', 'muted small', 'No files yet.'));
    state.assets.forEach(function (a) {
      var card = el('div', 'studio-asset panel');
      card.append(thumb(a));
      card.append(el('div', 'small strong truncate', a.name));
      card.append(el('div', 'small muted', [a.kind, a.size, Math.round(a.bytes / 1024) + ' KB', a.generated ? 'AI' : ''].filter(Boolean).join(' · ')));
      var desc = el('textarea', 'small'); desc.rows = 2; desc.value = a.description; desc.placeholder = 'Description (the AI fills this in)';
      desc.setAttribute('aria-label', 'Description of ' + a.name);
      var tags = el('input', 'small'); tags.value = (a.tags || []).join(', '); tags.placeholder = 'tags, comma separated';
      tags.setAttribute('aria-label', 'Tags of ' + a.name);
      var row = el('div', 'cluster cluster--tight');
      var save = el('button', 'button button--secondary button--small', 'Save'); save.type = 'button';
      save.addEventListener('click', function () { post(assetUrl(a.id), { description: desc.value, tags: tags.value }).then(function (j) { if (j.state) { state = j.state; renderAll(); } }); });
      var del = el('button', 'button button--ghost button--small tone-danger', 'Delete'); del.type = 'button';
      del.addEventListener('click', function () {
        if (!window.confirm('Delete ' + a.name + '? Pages that use it will show a broken image.')) return;
        post(assetUrl(a.id), { action: 'delete' }).then(function (j) { if (j.error) window.alert(j.error); if (j.state) { state = j.state; renderAll(); } });
      });
      var copy = el('button', 'button button--ghost button--small', 'Copy path'); copy.type = 'button';
      copy.addEventListener('click', function () { navigator.clipboard.writeText(a.path).then(function () { copy.textContent = 'Copied'; }); });
      row.append(save, copy, del);
      card.append(desc, tags, row);
      grid.append(card);
    });
  }
  function upload(files) {
    if (!files.length) return;
    var data = new FormData(); [].forEach.call(files, function (f) { data.append('files', f, f.name || 'pasted.png'); });
    uploadStatus.textContent = 'Uploading ' + files.length + ' file' + (files.length > 1 ? 's' : '') + '…';
    post(root.dataset.assetsUrl, data).then(function (j) {
      uploadStatus.textContent = (j.added && j.added.length ? j.added.length + ' added. ' : '') + (j.errors || []).join(' ') + (j.error || '');
      if (j.state) { state = j.state; renderAll(); setTimeout(refresh, 8000); }  // descriptions arrive a little later
    });
  }
  var drop = $('[data-drop]'), fileInput = $('[data-file-input]');
  drop.addEventListener('click', function (e) { if (e.target !== fileInput) fileInput.click(); });
  drop.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fileInput.click(); } });
  fileInput.addEventListener('change', function () { upload(fileInput.files); fileInput.value = ''; });
  ['dragenter', 'dragover'].forEach(function (t) { drop.addEventListener(t, function (e) { e.preventDefault(); drop.classList.add('is-over'); }); });
  ['dragleave', 'drop'].forEach(function (t) { drop.addEventListener(t, function () { drop.classList.remove('is-over'); }); });
  drop.addEventListener('drop', function (e) { e.preventDefault(); upload(e.dataTransfer.files); });
  document.addEventListener('paste', function (e) {
    if ($('#studio-assets').hidden || !e.clipboardData || !e.clipboardData.files.length) return;
    e.preventDefault(); upload(e.clipboardData.files);
  });

  function renderFlyer() {
    var current = $('[data-flyer-current]'), choices = $('[data-flyer-choices]');
    var chosen = state.assets.filter(function (a) { return a.id === state.flyer; })[0];
    current.replaceChildren(chosen ? thumb(chosen) : el('span', 'muted small', 'None chosen yet.'));
    if (chosen) current.append(el('div', 'small', chosen.name));
    choices.replaceChildren();
    var list = state.assets.filter(function (a) { return a.kind === 'image' || a.kind === 'pdf'; })
      .sort(function (a, b) { return (b.generated ? 1 : 0) - (a.generated ? 1 : 0); });
    if (!list.length) choices.append(el('p', 'muted small', 'Upload images in Assets or generate flyer art.'));
    list.forEach(function (a) {
      var card = el('div', 'studio-asset panel'); card.append(thumb(a), el('div', 'small truncate', a.name));
      var b = el('button', 'button button--' + (a.id === state.flyer ? 'primary' : 'secondary') + ' button--small',
                 a.id === state.flyer ? 'The flyer' : 'Use as flyer');
      b.type = 'button'; b.disabled = a.id === state.flyer;
      b.addEventListener('click', function () { post(root.dataset.flyerUrl, { asset_id: a.id }).then(function (j) { if (j.state) { state = j.state; renderAll(); } }); });
      card.append(b); choices.append(card);
    });
  }
  $('[data-flyer-generate]').addEventListener('submit', function (e) {
    e.preventDefault();
    var text = $('#flyer-prompt').value.trim();
    send('Make portrait flyer art for the event with generate_image (purpose flyer) and set it as the flyer with set_flyer.'
         + (text ? ' Art direction: ' + text : '')).then(function () { $('#flyer-prompt').value = ''; });
  });

  // ---------------------------------------------------------------- versions
  function renderVersions() {
    var list = $('[data-version-list]'); list.replaceChildren();
    state.versions.forEach(function (v) {
      var li = el('li', 'cluster');
      var main = el('div', 'grow');
      var title = el('div'); title.append(el('strong', '', 'Version ' + v.number + ' '));
      if (v.number === state.draft) title.append(el('span', 'badge badge--neutral', 'Draft'));
      if (v.number === state.published) title.append(el('span', 'badge badge--live', 'Live'));
      main.append(title, el('div', 'small', v.summary || '—'),
                  el('div', 'small muted', v.source + ' · ' + v.by + ' · ' + new Date(v.at).toLocaleString() + ' · ' + v.pages + ' page' + (v.pages === 1 ? '' : 's')));
      var actions = el('div', 'cluster cluster--tight');
      var preview = el('a', 'button button--ghost button--small', 'Preview'); preview.href = url(root.dataset.versionPreviewUrl, v.id);
      preview.target = '_blank'; preview.rel = 'noopener'; actions.append(preview);
      if (v.number !== state.draft) {
        var restore = el('button', 'button button--secondary button--small', 'Restore'); restore.type = 'button';
        restore.addEventListener('click', function () { formPost(url(root.dataset.restoreUrl, v.id)); });
        actions.append(restore);
      }
      if (root.dataset.canPublish && v.number !== state.published) {
        var pub = el('button', 'button button--outline button--small', 'Publish this version'); pub.type = 'button';
        pub.addEventListener('click', function () { if (window.confirm('Make version ' + v.number + ' live?')) formPost(url(root.dataset.publishVersionUrl, v.id)); });
        actions.append(pub);
      }
      li.append(main, actions); list.append(li);
    });
  }

  // ---------------------------------------------------------------- app look
  function refreshApp() {
    getJSON(root.dataset.appPreviewUrl).then(function (j) {
      $('[data-app-preview]').innerHTML = j.html;  // our own server-rendered, escaped template
      var info = $('[data-app-look]'); info.replaceChildren();
      info.append(el('div', '', 'Accent colour: ' + (j.accent || 'Attnd purple')));
      if (j.logo) { var img = el('img', 'studio-logo'); img.src = j.logo; img.alt = 'Logo'; info.append(el('div', '', 'Logo:'), img); }
    });
  }

  function renderAll() { renderPages(); renderAssets(); renderFlyer(); renderVersions(); }
  renderAll();
  loadFrame();
  refreshApp();
  if (root.dataset.activeRun) watchRun(root.dataset.activeRun);
  else log.lastElementChild && log.lastElementChild.scrollIntoView({ block: 'nearest' });
})();
