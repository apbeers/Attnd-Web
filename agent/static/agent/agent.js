/* Ask Attnd: the console's AI agent panel (vanilla JS, no inline handlers; text only via textContent). */
(function () {
  const root = document.querySelector('[data-agent]');
  if (!root) return;
  const $ = (sel) => root.querySelector(sel);
  const launcher = $('[data-agent-open]');
  const panel = $('#agent-panel');
  const log = $('[data-agent-log]');
  const form = $('[data-agent-form]');
  const input = $('[data-agent-input]');
  const statusLine = $('[data-agent-status]');
  const confirmBox = $('[data-agent-confirm]');
  const confirmText = $('[data-agent-confirm-text]');
  const stopButton = $('[data-agent-stop]');
  const undoButton = $('[data-agent-undo]');
  const refreshButton = $('[data-agent-refresh]');
  const watchBox = $('[data-agent-watch]');
  const sendButton = $('[data-agent-send]');
  const linksRow = $('[data-agent-links]');
  const filesList = $('[data-agent-files]');
  const recentBox = $('#agent-recent');
  const busy = $('.agent__busy');
  const csrf = form.querySelector('input[name=csrfmiddlewaretoken]').value;
  const runUrl = (id, tail) => root.dataset.runUrl.replace(/\/0\/$/, `/${id}/`) + (tail || '');
  const eventId = root.dataset.eventId ? parseInt(root.dataset.eventId, 10) : null;

  // Per-viewer conveniences only; every read and write may throw (private mode, blocked storage).
  const store = {
    get(area, key) { try { return window[area].getItem(key); } catch (e) { return null; } },
    set(area, key, value) { try { window[area].setItem(key, value); } catch (e) { /* ignore */ } },
    remove(area, key) { try { window[area].removeItem(key); } catch (e) { /* ignore */ } },
  };

  let run = null;          // the run shown
  let lastSeq = 0;
  let timer = null;
  let live = false;        // steps arriving now (not history loaded with the page)
  let wasActive = false;
  let attachments = [];
  let dirty = false;

  // ---- open / close ----------------------------------------------------------
  function open(focus) {
    panel.hidden = false;
    root.classList.add('is-open');
    launcher.setAttribute('aria-expanded', 'true');
    store.set('sessionStorage', 'attnd.agent.open', '1');
    fitSheet();
    if (focus !== false) input.focus();
  }
  function close() {
    panel.hidden = true;
    root.classList.remove('is-open');
    launcher.setAttribute('aria-expanded', 'false');
    store.remove('sessionStorage', 'attnd.agent.open');
    fitSheet();
    launcher.focus();
  }
  launcher.addEventListener('click', () => (panel.hidden ? open() : close()));
  $('[data-agent-close]').addEventListener('click', close);
  panel.addEventListener('keydown', (e) => { if (e.key === 'Escape') { e.preventDefault(); close(); } });

  // ---- Background / Watch ------------------------------------------------------
  // Phones (the bottom sheet) always work in the background: the Watch switch is not shown there.
  const phone = window.matchMedia('(max-width: 600px)');
  watchBox.checked = store.get('localStorage', 'attnd.agent.watch') === '1';
  watchBox.addEventListener('change', () => { store.set('localStorage', 'attnd.agent.watch', watchBox.checked ? '1' : '0'); });
  const watching = () => watchBox.checked && !phone.matches;

  // ---- Phones: the sheet follows the visual viewport (keyboard open = composer right above it) ----------
  const vv = window.visualViewport;
  function fitSheet() {
    const html = document.documentElement;
    if (!phone.matches || panel.hidden) {
      html.classList.remove('agent-sheet-open');
      root.style.removeProperty('--agent-sheet-top');
      root.style.removeProperty('--agent-sheet-height');
      return;
    }
    html.classList.add('agent-sheet-open');
    const visible = vv ? vv.height : window.innerHeight;
    const top = vv ? vv.offsetTop : 0;
    // Keyboard closed: 85% of the screen. Open: everything above the keyboard (the log takes the rest).
    const keyboard = vv && vv.height < window.innerHeight * 0.8;
    const height = Math.round(keyboard ? visible : Math.min(visible, window.innerHeight * 0.85));
    root.style.setProperty('--agent-sheet-height', `${height}px`);
    root.style.setProperty('--agent-sheet-top', `${Math.round(top + visible - height)}px`);
  }
  if (vv) { vv.addEventListener('resize', fitSheet); vv.addEventListener('scroll', fitSheet); }
  window.addEventListener('resize', fitSheet);
  phone.addEventListener?.('change', fitSheet);

  // One-line composer that grows to three lines as you type.
  function fitInput() {
    input.style.height = 'auto';
    input.style.height = (input.scrollHeight + input.offsetHeight - input.clientHeight) + 'px';
  }
  input.addEventListener('input', fitInput);
  function syncLinks() { linksRow.hidden = undoButton.hidden && refreshButton.hidden; }

  // Unsaved input on the page: never reload over it.
  document.addEventListener('input', (e) => {
    if (e.target.closest('form') && !root.contains(e.target)) dirty = true;
  });
  document.addEventListener('submit', (e) => { if (!root.contains(e.target)) dirty = false; });

  // ---- talking to the server -----------------------------------------------------
  async function post(url, data) {
    const body = data instanceof FormData ? data : new FormData();
    if (!(data instanceof FormData)) Object.entries(data || {}).forEach(([k, v]) => body.append(k, v));
    const response = await fetch(url, { method: 'POST', body, credentials: 'same-origin',
                                        headers: { 'X-CSRFToken': csrf, Accept: 'application/json' } });
    const json = await response.json().catch(() => ({}));
    if (!response.ok) throw Object.assign(new Error(json.error || 'Something went wrong. Please try again.'), { data: json });
    return json;
  }
  async function get(url) {
    const response = await fetch(url, { credentials: 'same-origin', headers: { Accept: 'application/json' } });
    if (!response.ok) throw new Error('Could not reach Attnd.');
    return response.json();
  }

  // ---- rendering ---------------------------------------------------------------------
  function bubble(kind, text) {
    $('[data-agent-hello]')?.remove();
    const el = document.createElement('div');
    el.className = `agent__msg agent__msg--${kind}`;
    el.textContent = text;
    log.appendChild(el);
    log.scrollTop = log.scrollHeight;
    return el;
  }
  function renderStep(step) {
    if (step.kind === 'note') return bubble('assistant', step.text);
    if (step.kind === 'question') return bubble('assistant', step.text);
    if (step.kind === 'reply') return bubble('user', step.text);
    const el = bubble('step', step.text);
    el.classList.toggle('is-failed', !step.ok);
    el.classList.toggle('is-changed', step.changed);
    if (step.url && step.changed) {
      const link = document.createElement('a');
      link.href = step.url;
      link.textContent = 'Open';
      link.className = 'agent__open';
      el.append(' ', link);
    }
    return el;
  }

  function renderRun(data) {
    run = data.run;
    (data.steps || []).forEach((step) => {
      if (step.seq <= lastSeq) return;
      lastSeq = step.seq;
      renderStep(step);
      if (live && watching() && step.changed && step.url) watchNavigate(step.url);
    });
    const pending = run.pending;
    confirmBox.hidden = !pending;
    if (pending) {
      if (confirmBox.dataset.step !== String(pending.step)) {
        confirmBox.dataset.step = pending.step;
        confirmBox.dataset.hash = pending.hash;
        confirmText.textContent = pending.summary;
        if (!panel.hidden) { confirmBox.setAttribute('tabindex', '-1'); confirmBox.focus(); }
        if (panel.hidden) launcher.classList.add('needs-you');
      }
    } else {
      delete confirmBox.dataset.step;
    }
    const waiting = run.status === 'waiting_confirmation' || run.status === 'waiting_reply';
    launcher.classList.toggle('needs-you', waiting);
    busy.hidden = run.status !== 'running';
    stopButton.hidden = !run.active;
    sendButton.hidden = run.active && run.status === 'running';
    undoButton.hidden = !run.undo || (pending && pending.kind === 'undo');
    syncLinks();
    input.placeholder = run.status === 'waiting_reply' ? 'Your answer…' : 'What should Attnd do?';
    statusLine.textContent = statusText(run);
    if (!run.active && wasActive) finished();
    wasActive = run.active;
    if (run.active) schedule(); else stopPolling();
  }

  function statusText(r) {
    const cost = parseFloat(r.cost_usd || '0');
    const spend = cost >= 0.005 ? ` · $${cost.toFixed(2)}` : '';
    return ({ running: 'Working…', waiting_confirmation: 'Waiting for you to confirm', waiting_reply: 'Waiting for your answer',
              done: 'Done', failed: 'Didn’t finish', stopped: 'Stopped' }[r.status] || r.status) + spend;
  }

  function finished() {
    if (run.summary) bubble('assistant', run.summary);
    if (run.error && run.status !== 'done') bubble('error', run.error);
    const here = location.pathname;
    const touched = (eventId && run.changed_events.includes(eventId)) || run.changed_urls.includes(here);
    if (!touched || watching()) return;
    if (dirty) { refreshButton.hidden = false; syncLinks(); return; }
    store.set('sessionStorage', 'attnd.agent.open', panel.hidden ? '' : '1');
    location.reload();
  }

  function watchNavigate(url) {
    if (dirty) { refreshButton.hidden = false; syncLinks(); return; }
    store.set('sessionStorage', 'attnd.agent.open', '1');
    if (url === location.pathname) location.reload(); else location.assign(url);
  }
  refreshButton.addEventListener('click', () => { store.set('sessionStorage', 'attnd.agent.open', '1'); location.reload(); });

  // ---- polling ---------------------------------------------------------------------------
  function schedule() { if (!timer) timer = setTimeout(poll, 1500); }
  function stopPolling() { clearTimeout(timer); timer = null; }
  async function poll() {
    timer = null;
    if (!run) return;
    try { renderRun(await get(runUrl(run.id, `?after=${lastSeq}`))); live = true; }
    catch (e) { statusLine.textContent = 'Reconnecting…'; schedule(); }
  }

  async function show(id, isLive) {
    log.textContent = '';
    lastSeq = 0;
    live = false;
    const data = await get(runUrl(id, '?after=0'));
    bubble('user', data.run.goal);
    wasActive = data.run.active;
    renderRun(data);
    if (!data.run.active && data.run.summary) bubble('assistant', data.run.summary);
    if (!data.run.active && data.run.error && data.run.status !== 'done') bubble('error', data.run.error);
    live = isLive !== false;
  }

  // ---- sending -------------------------------------------------------------------------------
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text) { input.focus(); return; }
    const send = sendButton;
    send.disabled = true;
    try {
      if (run && run.status === 'waiting_reply') {
        renderRun(await post(runUrl(run.id, 'reply/'), { text }));
        live = true;
      } else {
        if (!run || !run.active) { log.textContent = ''; lastSeq = 0; }
        bubble('user', text);
        const data = await post(root.dataset.startUrl, { goal: text, page: location.pathname + location.search,
                                                          event_id: eventId || '', attachments: attachments.map((a) => a.id).join(',') });
        attachments = [];
        filesList.textContent = '';
        refreshButton.hidden = true;
        syncLinks();
        wasActive = true;
        live = true;
        renderRun(data);
      }
      input.value = '';
      fitInput();
    } catch (err) {
      bubble('error', err.message);
      if (err.data && err.data.run) { show(err.data.run.id); }
    } finally {
      send.disabled = false;
      input.focus();
    }
  });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); form.requestSubmit(); }
  });

  $('[data-agent-confirm-yes]').addEventListener('click', async () => {
    if (!run || !confirmBox.dataset.step) return;
    try { renderRun(await post(runUrl(run.id, 'confirm/'), { step: confirmBox.dataset.step, hash: confirmBox.dataset.hash })); live = true; poll(); }
    catch (err) { bubble('error', err.message); }
  });
  $('[data-agent-confirm-no]').addEventListener('click', async () => {
    if (!run || !confirmBox.dataset.step) return;
    try { renderRun(await post(runUrl(run.id, 'cancel/'), { step: confirmBox.dataset.step })); live = true; poll(); }
    catch (err) { bubble('error', err.message); }
  });
  stopButton.addEventListener('click', async () => {
    if (!run) return;
    try { renderRun(await post(runUrl(run.id, 'stop/'), {})); } catch (err) { bubble('error', err.message); }
  });
  undoButton.addEventListener('click', async () => {
    if (!run) return;
    try { renderRun(await post(runUrl(run.id, 'undo/'), {})); poll(); } catch (err) { bubble('error', err.message); }
  });

  $('[data-agent-file]').addEventListener('change', async (e) => {
    const file = e.target.files[0];
    e.target.value = '';
    if (!file) return;
    const body = new FormData();
    body.append('file', file);
    try {
      const data = await post(root.dataset.attachUrl, body);
      attachments.push(data.attachment);
      const item = document.createElement('li');
      item.textContent = `Attached: ${data.attachment.name}`;
      filesList.appendChild(item);
    } catch (err) { bubble('error', err.message); }
  });

  // ---- recent requests ---------------------------------------------------------------------
  $('[data-agent-history]').addEventListener('click', async (e) => {
    const button = e.currentTarget;
    const show_ = recentBox.hidden;
    recentBox.hidden = !show_;
    button.setAttribute('aria-expanded', String(show_));
    if (!show_) return;
    const list = $('[data-agent-recent]');
    list.textContent = '';
    try {
      const data = await get(root.dataset.runsUrl);
      if (!data.runs.length) { const li = document.createElement('li'); li.className = 'muted small'; li.textContent = 'Nothing yet.'; list.appendChild(li); }
      data.runs.forEach((r) => {
        const li = document.createElement('li');
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'link-button small';
        b.textContent = r.goal;
        b.addEventListener('click', () => { recentBox.hidden = true; button.setAttribute('aria-expanded', 'false'); show(r.id, r.active); });
        const meta = document.createElement('span');
        meta.className = 'muted small';
        meta.textContent = ` · ${r.status.replace('_', ' ')}`;
        li.append(b, meta);
        list.appendChild(li);
      });
      const spend = document.createElement('li');
      spend.className = 'muted small';
      spend.textContent = `Today: $${data.spent_today} of $${data.cap}`;
      list.appendChild(spend);
    } catch (err) { bubble('error', err.message); }
  });

  // ---- on load: pick up a run that is still going -------------------------------------------
  (async () => {
    if (store.get('sessionStorage', 'attnd.agent.open') === '1') open(false);
    try {
      const data = await get(root.dataset.runsUrl);
      const active = data.runs.find((r) => r.active);
      if (active) { await show(active.id, true); if (active.status !== 'running') open(false); }
    } catch (e) { /* offline: the panel still opens */ }
  })();
})();
