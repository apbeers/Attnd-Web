/* Ask Attnd: the console's AI agent panel (vanilla JS, no inline handlers). Text goes in via textContent; the only
   HTML inserted is console components rendered by the server from our own templates for this person
   (agent/components.py), never anything the model wrote. */
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
  const thinking = $('[data-agent-thinking]');
  const stageLine = $('[data-agent-stage]');
  const suggestBox = $('[data-agent-suggest]');
  // Full screen (/console/assistant/): the chat on the left, the canvas on the right (assistant.html).
  const full = root.dataset.mode === 'full';
  const canvas = full ? document.querySelector('[data-canvas]') : null;
  const fromPage = root.dataset.from || '';
  const wide = window.matchMedia('(min-width: 1024px)');
  const canvasOn = () => full && canvas && wide.matches;  // narrower: the full page works like the panel
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
  const renderedSteps = new Set();  // step ids in the log (taps add steps the poll would bring again)
  const unfinished = new Map();     // step id -> {spot, seq}: steps still running, drawn when done

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
  $('[data-agent-close]')?.addEventListener('click', close);
  if (!full) panel.addEventListener('keydown', (e) => { if (e.key === 'Escape') { e.preventDefault(); close(); } });

  // ---- Full screen and back: the same conversation (sessionStorage), the same scroll position ----------------
  $('[data-agent-full]')?.addEventListener('click', (e) => {
    const url = new URL(root.dataset.assistantUrl, location.origin);
    if (eventId) url.searchParams.set('event', eventId);
    url.searchParams.set('from', location.pathname + location.search);
    e.currentTarget.href = url.pathname + url.search;
    store.set('sessionStorage', 'attnd.agent.scroll', String(log.scrollTop));
  });
  $('[data-agent-exit]')?.addEventListener('click', () => {
    store.set('sessionStorage', 'attnd.agent.open', '1');
    store.set('sessionStorage', 'attnd.agent.scroll', String(log.scrollTop));
  });

  // ---- Background / Watch ------------------------------------------------------
  // Phones (the bottom sheet) always work in the background: the Watch switch is not shown there.
  const phone = window.matchMedia('(max-width: 600px)');
  watchBox.checked = store.get('localStorage', 'attnd.agent.watch') === '1';
  watchBox.addEventListener('change', () => { store.set('localStorage', 'attnd.agent.watch', watchBox.checked ? '1' : '0'); });
  const watching = () => !full && watchBox.checked && !phone.matches;

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
    // The whole visible height: the full screen, or everything above the keyboard when it is open.
    const height = Math.round(visible);
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
    if (!thinking.hidden) log.appendChild(thinking);
    log.scrollTop = log.scrollHeight;
    return el;
  }
  function renderStep(step) {
    if (step.kind === 'component') return showComponents(step);
    if (step.kind === 'note') return bubble('assistant', step.text);
    if (step.kind === 'question') return question(step);
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
    if (step.undo) addUndo(el, step.undo.id, step.undo.state, step.event_id);
    return el;
  }

  // ---- Undo next to every change (console/undo.py: one recorded change, 24 h, then Redo) ----------------
  const undoUrl = (id) => root.dataset.undoUrl.replace(/\/0\/$/, `/${id}/`);
  function addUndo(el, id, state, changedEventId, redo) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'agent__undo';
    button.textContent = redo ? 'Redo' : 'Undo';
    button.dataset.undoId = id;
    if (state && state !== 'available') {
      button.disabled = true;
      button.textContent = state === 'used' ? 'Undone' : 'Undo expired';
    }
    button.addEventListener('click', async () => {
      button.disabled = true;
      try {
        const data = await post(undoUrl(id), {});
        const said = bubble('step', data.message);
        said.classList.add('is-changed');
        button.textContent = redo ? 'Redone' : 'Undone';
        if (data.redo) addUndo(said, data.redo, 'available', data.event_id, !redo);
        const eid = data.event_id || changedEventId;
        if (eid) { refreshKeys([`event:${eid}`]); pageChanged(eid); }
      } catch (err) {
        bubble('error', err.message);
        button.disabled = false;
      }
    });
    el.append(' ', button);
    return button;
  }

  // ask_user with options: tappable chips that send the answer (only while that question waits).
  function question(step) {
    const el = bubble('assistant', step.text);
    if (!(step.options || []).length) return el;
    const row = document.createElement('div');
    row.className = 'agent__chips';
    row.setAttribute('role', 'group');
    row.setAttribute('aria-label', 'Answers');
    step.options.forEach((option) => {
      const chip = document.createElement('button');
      chip.type = 'button';
      chip.className = 'agent__chip';
      chip.textContent = option;
      chip.dataset.questionStep = step.id;
      chip.addEventListener('click', () => answer(option, row));
      row.appendChild(chip);
    });
    log.appendChild(row);
    log.scrollTop = log.scrollHeight;
    return el;
  }
  async function answer(text, row) {
    if (!run || run.status !== 'waiting_reply') return;
    row.querySelectorAll('button').forEach((b) => { b.disabled = true; });
    try { renderRun(await post(runUrl(run.id, 'reply/'), { text })); live = true; poll(); }
    catch (err) { bubble('error', err.message); row.querySelectorAll('button').forEach((b) => { b.disabled = false; }); }
  }
  function syncChips() {
    log.querySelectorAll('[data-question-step]').forEach((chip) => {
      chip.disabled = !(run && run.status === 'waiting_reply');
    });
  }

  function renderRun(data) {
    run = data.run;
    (data.steps || []).forEach((step) => {
      // A step still running ("Opening …") is the thinking line's stage, not a log line yet: it keeps a place in
      // the log and is drawn there once it is done (polls ask again from the oldest unfinished step).
      const running = step.kind === 'tool' && /…$/.test(step.text || '') && step.confirm !== 'pending'
                      && data.run.status === 'running';
      if (step.seq <= lastSeq && !unfinished.has(step.id)) return;
      lastSeq = Math.max(lastSeq, step.seq);
      if (renderedSteps.has(step.id) && !unfinished.has(step.id)) return;
      if (running) {
        if (!unfinished.has(step.id)) {
          const spot = document.createComment(`step ${step.id}`);
          log.appendChild(spot);
          unfinished.set(step.id, { spot, seq: step.seq });
          renderedSteps.add(step.id);
        }
        return;
      }
      renderedSteps.add(step.id);
      const waiting = unfinished.get(step.id);
      const el = renderStep(step);
      if (waiting) { waiting.spot.replaceWith(el); unfinished.delete(step.id); }
      if (live && step.changed && step.event_id) refreshKeys([`event:${step.event_id}`]);
      if (live && watching() && step.changed && step.url) watchNavigate(step.url);
    });
    if (!data.run.active) unfinished.clear();
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
    setThinking(run.status === 'running', data.steps || []);
    showSuggestions(run.active ? [] : (run.suggestions || []));
    const waiting = run.status === 'waiting_confirmation' || run.status === 'waiting_reply';
    launcher.classList.toggle('needs-you', waiting);
    busy.hidden = run.status !== 'running';
    stopButton.hidden = !run.active;
    sendButton.hidden = run.active && run.status === 'running';
    undoButton.hidden = !run.undo || (pending && pending.kind === 'undo');
    syncLinks();
    input.placeholder = run.status === 'waiting_reply' ? 'Your answer…' : 'What should Attnd do?';
    syncChips();
    statusLine.textContent = statusText(run);
    if (!run.active && wasActive) finished();
    wasActive = run.active;
    if (run.active) schedule(); else stopPolling();
  }

  // ---- The thinking line: one line, a spinner and the current stage, cross-fading as stages change ----------
  let stageTimer = null;
  let lastStage = '';
  function setStage(text) {
    if (text === lastStage) return;
    lastStage = text;
    clearTimeout(stageTimer);
    stageLine.classList.add('is-leaving');
    stageTimer = setTimeout(() => { stageLine.textContent = text; stageLine.classList.remove('is-leaving'); }, 160);
  }
  function setThinking(on, steps) {
    thinking.hidden = !on;
    if (!on) { lastStage = ''; return; }
    log.appendChild(thinking);  // always the chat's last line
    const last = steps.length ? steps[steps.length - 1] : null;
    if (last) setStage(last.text && !/…$/.test(last.text) && last.kind !== 'component' ? 'Thinking…' : (last.stage || 'Working…'));
    else if (!lastStage) setStage('Thinking…');
    log.scrollTop = log.scrollHeight;
  }
  // Follow-ups after an answer: tapping one sends it.
  function showSuggestions(list) {
    suggestBox.textContent = '';
    suggestBox.hidden = !list.length;
    list.forEach((text) => {
      const chip = document.createElement('button');
      chip.type = 'button';
      chip.className = 'agent__chip';
      chip.textContent = text;
      chip.addEventListener('click', () => { input.value = text; fitInput(); form.requestSubmit(); });
      suggestBox.appendChild(chip);
    });
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
    if (full) {  // full screen: fresh copies on the canvas instead of a reload
      run.changed_events.forEach((id) => refreshKeys([`event:${id}`]));
      return;
    }
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
    const oldest = Math.min(...[...unfinished.values()].map((u) => u.seq - 1), lastSeq);
    try { renderRun(await get(runUrl(run.id, `?after=${oldest}`))); live = true; }
    catch (e) { statusLine.textContent = 'Reconnecting…'; schedule(); }
  }

  // ---- the conversation: every request of this chat stays in the log (and across page loads) ----
  const chatIds = () => { try { return JSON.parse(store.get('sessionStorage', 'attnd.agent.chat') || '[]'); } catch (e) { return []; } };
  const setChatIds = (ids) => store.set('sessionStorage', 'attnd.agent.chat', JSON.stringify(ids.slice(-20)));
  let scrollTimer = null;
  log.addEventListener('scroll', () => {
    clearTimeout(scrollTimer);
    scrollTimer = setTimeout(() => store.set('sessionStorage', 'attnd.agent.scroll', String(log.scrollTop)), 150);
  });
  window.addEventListener('pagehide', () => store.set('sessionStorage', 'attnd.agent.scroll', String(log.scrollTop)));

  function newChat() {
    stopPolling();
    log.textContent = '';
    lastSeq = 0;
    renderedSteps.clear();
    unfinished.clear();
    run = null;
    wasActive = false;
    setChatIds([]);
    store.remove('sessionStorage', 'attnd.agent.scroll');
    statusLine.textContent = '';
    confirmBox.hidden = true;
    stopButton.hidden = true;
    sendButton.hidden = false;
    input.placeholder = 'What should Attnd do?';
    showSuggestions([]);
    thinking.hidden = true;
    input.focus();
  }
  $('[data-agent-new]').addEventListener('click', newChat);

  // A request from the history list: shown on its own (a new conversation that starts with it).
  async function show(id, isLive) {
    log.textContent = '';
    renderedSteps.clear();
    unfinished.clear();
    setChatIds([id]);
    await appendRun(id, isLive);
  }

  async function appendRun(id, isLive) {
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
        if (!run || !run.active) { lastSeq = 0; }
        bubble('user', text);
        showSuggestions([]);
        const data = await post(root.dataset.startUrl, { goal: text, page: full ? fromPage : location.pathname + location.search,
                                                          event_id: eventId || '', attachments: attachments.map((a) => a.id).join(',') });
        if (data.run && data.run.id) setChatIds(chatIds().filter((x) => x !== data.run.id).concat([data.run.id]));
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
    try {
      const data = await post(runUrl(run.id, 'confirm/'), { step: confirmBox.dataset.step, hash: confirmBox.dataset.hash });
      renderRun(data);
      live = true;
      if (data.step) {  // a chat button (or Undo all) ran right away: its outcome, and fresh copies of what it touched
        const line = bubble('step', data.step.text);
        line.classList.toggle('is-failed', !data.step.ok);
        if (data.step.undo) addUndo(line, data.step.undo.id, data.step.undo.state, data.step.event_id);
        if (data.step.changed && data.step.event_id) { refreshKeys([`event:${data.step.event_id}`]); pageChanged(data.step.event_id); }
      }
      poll();
    } catch (err) { bubble('error', err.message); }
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

  // ---- console components in the chat (console/components, agent/components.py) ----------------------------------
  // Each card holds one component: HTML rendered by the server as this person, with its ids and the entities it
  // shows (data-keys "event:3 location:12"). Tapping the body shows its detail (no model call); buttons and forms
  // post to the same views as the page; always-ask ones wait for Confirm (agent:component_act → agent:confirm).
  const LIVE_CARDS = 10;
  const itemsParam = (items) => encodeURIComponent(JSON.stringify(items));

  async function fetchCards(name, items) {
    const response = await fetch(`${root.dataset.componentsUrl}?component=${encodeURIComponent(name)}&items=${itemsParam(items)}`,
                                 { credentials: 'same-origin', headers: { Accept: 'application/json' } });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw Object.assign(new Error(data.error || 'Could not show this.'), { status: response.status });
    return data.items;
  }

  function setBody(card, html) {
    const body = document.createElement('div');
    body.className = 'agent__card-body';
    body.innerHTML = html;  // server-rendered component (our templates), see the note at the top
    card.querySelector('.agent__card-body')?.remove();
    card.prepend(body);
    window.attndShowIf?.(body);
  }

  function makeCard(item) {
    const card = document.createElement('div');
    card.className = 'agent__card';
    card.dataset.component = item.component;
    card.dataset.ids = JSON.stringify(item.ids);
    card.dataset.keys = (item.keys || []).join(' ');
    card.dataset.title = item.label || item.title;
    if (item.detail) card.dataset.detail = item.detail;
    if (item.detail_ids) card.dataset.detailIds = JSON.stringify(item.detail_ids);
    const foot = document.createElement('div');
    foot.className = 'agent__card-foot';
    if (item.url) {
      const link = document.createElement('a');
      link.href = item.url;
      link.className = 'agent__open-page';
      // Wide screens: the chat stays open where it was over the new page. Phones: the sheet closes to show it.
      link.addEventListener('click', () => {
        store.set('sessionStorage', 'attnd.agent.scroll', String(log.scrollTop));
        if (phone.matches) store.remove('sessionStorage', 'attnd.agent.open');
      });
      link.textContent = 'Open page ↗';
      foot.appendChild(link);
    }
    card.appendChild(foot);
    setBody(card, item.html);
    return card;
  }

  function cardBox() {
    $('[data-agent-hello]')?.remove();
    const box = document.createElement('div');
    box.className = 'agent__component';
    log.appendChild(box);
    return box;
  }

  async function showComponents(step) {
    const box = cardBox();
    try {
      const items = await fetchCards(step.component.name, step.component.items);
      if (canvasOn() && step.component.picked) {
        // Something opened earlier: a tile that opens it again on the canvas.
        box.classList.add('agent__tiles');
        items.forEach((item) => box.appendChild(makeTile(item, null, 0, () => showOverlay(item.label || item.title, [item]))));
      } else if (canvasOn()) {
        // Full screen: compact tiles in the chat, the cards themselves on the canvas.
        box.classList.add('agent__tiles');
        const tab = toCanvas(step.component.name, items);
        items.forEach((item, i) => box.appendChild(makeTile(item, tab, i)));
      } else {
        items.forEach((item) => box.appendChild(makeCard(item)));
      }
    } catch (err) {
      box.className = 'agent__msg agent__msg--step is-failed';
      box.textContent = err.status === 404 ? 'This is no longer there.' : err.message;
    }
    foldOld();
    log.scrollTop = log.scrollHeight;
    return box;
  }

  function liveCards() {
    const scopes = [log, canvas].filter(Boolean);
    return scopes.flatMap((scope) => [...scope.querySelectorAll('.agent__card:not(.is-folded):not(.is-gone)')]);
  }

  // Long chats: the latest cards stay live; older ones fold into one line that re-fetches when tapped.
  function foldOld() {
    const cards = [...log.querySelectorAll('.agent__card:not(.is-folded):not(.is-gone)')];
    cards.slice(0, Math.max(0, cards.length - LIVE_CARDS)).forEach((card) => {
      card.classList.add('is-folded');
      card.querySelector('.agent__card-body')?.remove();
      const line = document.createElement('button');
      line.type = 'button';
      line.className = 'link-button small agent__folded';
      line.textContent = `▸ ${card.dataset.title}`;
      line.addEventListener('click', async () => {
        line.remove();
        card.classList.remove('is-folded');
        await refresh(card);
        foldOld();
      });
      card.prepend(line);
    });
  }

  async function refresh(card) {
    card.classList.add('is-busy');
    try {
      const [item] = await fetchCards(card.dataset.component, [JSON.parse(card.dataset.ids)]);
      setBody(card, item.html);
      card.dataset.keys = (item.keys || []).join(' ');
    } catch (err) {
      if (err.status === 404) {
        card.classList.add('is-gone');
        const note = document.createElement('p');
        note.className = 'small muted';
        note.textContent = `${card.dataset.title}: no longer there (deleted, or no access).`;
        card.querySelector('.agent__card-body')?.replaceChildren(note);
      }
    } finally { card.classList.remove('is-busy'); }
  }

  // Copies of something that changed (a chat button, the agent, a run) are fetched again.
  function refreshKeys(keys, except) {
    liveCards().forEach((card) => {
      if (card === except) return;
      const mine = card.dataset.keys.split(' ');
      if (keys.some((k) => mine.includes(k))) refresh(card);
    });
  }

  function pageChanged(changedEventId) {
    if (eventId && changedEventId === eventId) { refreshButton.hidden = false; syncLinks(); }
  }

  async function pick(card) {
    if (canvasOn()) return openDetail(card);
    if (!run) return;
    card.classList.add('is-busy');
    try {
      const data = await post(root.dataset.pickUrl, { run: run.id, component: card.dataset.component, ids: card.dataset.ids });
      renderedSteps.add(data.step.id);
      const box = cardBox();
      data.items.forEach((item) => box.appendChild(makeCard(item)));
      foldOld();
      box.scrollIntoView({ block: 'nearest' });
    } catch (err) { bubble('error', err.message); }
    finally { card.classList.remove('is-busy'); }
  }

  async function act(card, action, form) {
    if (!run) return;
    const body = form ? new FormData(form) : new FormData();
    body.set('run', run.id);
    body.set('component', card.dataset.component);
    body.set('ids', card.dataset.ids);
    body.set('action', action);
    try {
      const data = await post(root.dataset.actUrl, body);
      if (data.invalid) { setBody(card, data.items[0].html); return; }
      renderedSteps.add(data.step.id);
      bubble('step', data.step.text);
      live = true;
      renderRun({ run: data.run, steps: [] });
    } catch (err) { bubble('error', err.message); }
  }

  function notices(html) {
    const doc = new DOMParser().parseFromString(html, 'text/html');
    const found = [...doc.querySelectorAll('main .notice')];
    const undo = found.map((n) => n.dataset.undoId).find(Boolean);
    const texts = found.map((n) => (n.querySelector('.notice__text') || n).textContent.replace('×', '').trim()).filter(Boolean);
    return Object.assign(texts, { undo });
  }

  async function submitForm(card, form, submitter) {
    const body = new FormData(form, submitter || undefined);
    card.classList.add('is-busy');
    try {
      // (getAttribute: an <input name="action"> shadows form.action)
      const response = await fetch(form.getAttribute('action'), { method: 'POST', body, credentials: 'same-origin',
        headers: { 'X-CSRFToken': csrf, 'X-Attnd-Component': card.dataset.component } });
      if ((response.headers.get('Content-Type') || '').includes('json')) {
        const data = await response.json();
        if (data.html) { setBody(card, data.html); return; }  // errors, in place
      }
      if (!response.ok) throw new Error(response.status === 404 ? 'Not found, or you don’t have access to it.'
                                                              : 'That didn’t work. Please try again.');
      const said = notices(await response.text());
      if (!response.redirected) throw new Error(said.join(' ') || 'Not saved. Open the page to see why.');
      const savedLine = bubble('step', said.join(' ') || 'Saved ✓');
      savedLine.classList.add('is-changed');
      const ids0 = JSON.parse(card.dataset.ids);
      if (said.undo) addUndo(savedLine, said.undo, 'available', ids0.event_id);
      const keys = card.dataset.keys.split(' ');
      await refresh(card);
      refreshKeys(keys, card);
      const ids = JSON.parse(card.dataset.ids);
      if (ids.event_id) pageChanged(ids.event_id);
    } catch (err) { bubble('error', err.message); }
    finally { card.classList.remove('is-busy'); }
  }

  function onCardClick(e) {
    const card = e.target.closest('.agent__card');
    if (!card || card.classList.contains('is-folded') || card.classList.contains('is-gone')) return;
    const button = e.target.closest('[data-component-act]');
    if (button) { e.preventDefault(); act(card, button.dataset.componentAct); return; }
    const title = e.target.closest('.title-link');
    if (title && card.dataset.detail) { e.preventDefault(); pick(card); return; }
    if (e.target.closest('a, button, input, select, textarea, label, summary, details, form, [data-copy]')) return;
    if (card.dataset.detail && e.target.closest('.agent__card-body')) pick(card);
  }
  function onCardSubmit(e) {
    const form = e.target.closest('form[data-component-form]');
    const card = form && form.closest('.agent__card');
    if (!card) return;
    e.preventDefault();
    if (form.dataset.confirmAction) act(card, form.dataset.confirmAction, form);
    else submitForm(card, form, e.submitter);
  }
  [log, canvas].filter(Boolean).forEach((scope) => {
    scope.addEventListener('click', onCardClick);
    scope.addEventListener('submit', onCardSubmit);
  });

  // ---- Full screen: the canvas (tabs of what the chat showed) and the detail overlay ---------------------------
  const KINDS = { event_tile: 'Events', event_overview: 'Overview', event_stats: 'Overview', live_on_phone: 'Overview',
                  needs_review: 'Overview', event_header: 'Overview', location_row: 'Locations', location_form: 'Locations',
                  schedule_item_row: 'Schedule', schedule_item_form: 'Schedule', notification_row: 'Updates',
                  notification_form: 'Updates' };
  const ORDER = ['Events', 'Overview', 'Locations', 'Schedule', 'Updates'];
  const tabs = new Map();  // label -> {button, pane}
  let overlayStack = [];   // [{title, items}]
  let overlayOpener = null;

  function selectTab(label) {
    tabs.forEach((t, key) => {
      const on = key === label;
      t.button.setAttribute('aria-selected', String(on));
      t.button.tabIndex = on ? 0 : -1;
      t.pane.hidden = !on;
    });
    if (overlayStack.length) closeOverlay(false);
  }

  function ensureTab(label) {
    if (tabs.has(label)) return tabs.get(label);
    const bar = canvas.querySelector('[data-canvas-tabs]');
    const body = canvas.querySelector('[data-canvas-body]');
    canvas.querySelector('[data-canvas-empty]')?.remove();
    const id = `canvas-${label.toLowerCase()}`;
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'tabs__tab';
    button.id = `${id}-tab`;
    button.setAttribute('role', 'tab');
    button.setAttribute('aria-controls', id);
    button.textContent = label;
    button.addEventListener('click', () => selectTab(label));
    const pane = document.createElement('div');
    pane.className = 'canvas__pane';
    pane.id = id;
    pane.setAttribute('role', 'tabpanel');
    pane.setAttribute('aria-labelledby', button.id);
    pane.hidden = true;
    const t = { button, pane, label };
    tabs.set(label, t);
    const sorted = [...tabs.values()].sort((a, b) => ORDER.indexOf(a.label) - ORDER.indexOf(b.label));
    sorted.forEach((x) => { bar.appendChild(x.button); body.appendChild(x.pane); });
    bar.hidden = false;
    return t;
  }
  canvas?.querySelector('[data-canvas-tabs]').addEventListener('keydown', (e) => {
    if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
    const list = [...tabs.values()].sort((a, b) => ORDER.indexOf(a.label) - ORDER.indexOf(b.label));
    const at = list.findIndex((t) => t.button === document.activeElement);
    const next = list[(at + (e.key === 'ArrowRight' ? 1 : list.length - 1)) % list.length];
    if (next) { selectTab(next.label); next.button.focus(); }
  });

  function toCanvas(name, items) {
    const t = ensureTab(KINDS[name] || 'More');
    t.pane.textContent = '';
    const wrap = document.createElement('div');
    wrap.className = 'canvas__cards' + (name.endsWith('_row') ? ' canvas__cards--rows' : '');
    items.forEach((item) => wrap.appendChild(makeCard(item)));
    t.pane.appendChild(wrap);
    selectTab(t.label);
    return t;
  }

  function makeTile(item, tab, index, onOpen) {
    const tile = document.createElement('button');
    tile.type = 'button';
    tile.className = 'agent__tile';
    const mark = document.createElement('span');
    mark.className = 'agent__tile-mark';
    mark.textContent = (item.label || item.title || '?').trim().charAt(0).toUpperCase();
    const text = document.createElement('span');
    text.className = 'agent__tile-text';
    const strong = document.createElement('strong');
    strong.textContent = item.label || item.title;
    const small = document.createElement('span');
    small.textContent = item.title;
    text.append(strong, small);
    const arrow = document.createElement('span');
    arrow.className = 'agent__tile-arrow';
    arrow.setAttribute('aria-hidden', 'true');
    arrow.textContent = '›';
    tile.append(mark, text, arrow);
    tile.addEventListener('click', () => {
      if (onOpen) { overlayOpener = tile; onOpen(); return; }
      selectTab(tab.label);
      const card = tab.pane.querySelectorAll('.agent__card')[index];
      if (card && card.dataset.detail) openDetail(card, tile);
      else card?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    });
    return tile;
  }

  async function openDetail(card, opener) {
    const overlay = canvas.querySelector('[data-canvas-overlay]');
    const ids = JSON.parse(card.dataset.ids);
    const keep = card.dataset.detailIds ? JSON.parse(card.dataset.detailIds) : Object.keys(ids);
    const detailIds = Object.fromEntries(Object.entries(ids).filter(([k]) => keep.includes(k)));
    card.classList.add('is-busy');
    try {
      let items;
      if (run) {  // remembered in the run (told to the model with the next message)
        const data = await post(root.dataset.pickUrl, { run: run.id, component: card.dataset.component, ids: card.dataset.ids });
        renderedSteps.add(data.step.id);
        items = data.items;
      } else {
        items = await fetchCards(card.dataset.detail, [detailIds]);
      }
      if (!overlayStack.length) overlayOpener = opener || card;
      showOverlay(card.dataset.title, items, true);
    } catch (err) { bubble('error', err.message); }
    finally { card.classList.remove('is-busy'); }
  }

  function showOverlay(title, items, push) {
    const overlay = canvas.querySelector('[data-canvas-overlay]');
    if (!push) overlayStack = [];
    overlayStack.push({ title, items });
    renderOverlay();
    overlay.hidden = false;
    overlay.querySelector('[data-overlay-title]').focus();
  }

  function renderOverlay() {
    const overlay = canvas.querySelector('[data-canvas-overlay]');
    const crumbs = overlay.querySelector('[data-overlay-crumbs]');
    const body = overlay.querySelector('[data-overlay-body]');
    crumbs.textContent = '';
    const current = [...tabs.values()].find((t) => !t.pane.hidden);
    const trail = [current ? current.label : 'Canvas', ...overlayStack.map((l) => l.title)];
    trail.forEach((text, i) => {
      const li = document.createElement('li');
      if (i === trail.length - 1) {
        const span = document.createElement('span');
        span.setAttribute('aria-current', 'page');
        span.textContent = text;
        li.appendChild(span);
      } else {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'link-button';
        b.textContent = text;
        b.addEventListener('click', () => { overlayStack = overlayStack.slice(0, i); if (overlayStack.length) renderOverlay(); else closeOverlay(true); });
        li.appendChild(b);
      }
      crumbs.appendChild(li);
    });
    overlay.querySelector('[data-overlay-title]').textContent = trail[trail.length - 1];
    body.textContent = '';
    const level = overlayStack[overlayStack.length - 1];
    level.items.forEach((item) => body.appendChild(makeCard(item)));
  }

  function closeOverlay(returnFocus) {
    const overlay = canvas.querySelector('[data-canvas-overlay]');
    overlayStack = [];
    overlay.hidden = true;
    overlay.querySelector('[data-overlay-body]').textContent = '';
    if (returnFocus && overlayOpener && document.contains(overlayOpener)) overlayOpener.focus();
    overlayOpener = null;
  }
  function popOverlay() {
    overlayStack.pop();
    if (overlayStack.length) { renderOverlay(); canvas.querySelector('[data-overlay-title]').focus(); }
    else closeOverlay(true);
  }
  if (canvas) {
    const overlay = canvas.querySelector('[data-canvas-overlay]');
    overlay.querySelector('[data-overlay-back]').addEventListener('click', popOverlay);
    overlay.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') { e.preventDefault(); popOverlay(); return; }
      if (e.key !== 'Tab') return;  // keep focus inside the overlay while it is open
      const focusable = [...overlay.querySelectorAll('a[href], button:not([disabled]), input:not([type=hidden]):not([disabled]), select, textarea, [tabindex]:not([tabindex="-1"])')]
        .filter((el) => el.offsetParent !== null);
      if (!focusable.length) return;
      const first = focusable[0], last = focusable[focusable.length - 1];
      if (e.shiftKey && (document.activeElement === first || document.activeElement === overlay.querySelector('[data-overlay-title]'))) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    });
  }

  // The event the page is about: its overview, locations, schedule and updates are on the canvas from the start.
  async function preloadCanvas() {
    if (!canvasOn() || !eventId) return;
    for (const name of ['event_overview', 'location_row', 'schedule_item_row', 'notification_row']) {
      try { toCanvas(name, await fetchCards(name, [{ event_id: eventId }])); } catch (e) { /* none yet */ }
    }
    selectTab('Overview');
  }

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
    if (full) { root.classList.add('is-open'); preloadCanvas(); }
    else if (store.get('sessionStorage', 'attnd.agent.open') === '1') open(false);
    try {
      const data = await get(root.dataset.runsUrl);
      const active = data.runs.find((r) => r.active);
      let ids = chatIds().filter((id) => data.runs.some((r) => r.id === id));
      if (active && !ids.includes(active.id)) ids = ids.concat([active.id]);
      setChatIds(ids);
      if (ids.length) {
        log.textContent = '';
        for (const id of ids) {
          const r = data.runs.find((x) => x.id === id);
          await appendRun(id, id === ids[ids.length - 1] && r && r.active);
        }
        const saved = parseInt(store.get('sessionStorage', 'attnd.agent.scroll') || '', 10);
        if (!Number.isNaN(saved)) log.scrollTop = saved;
      }
      if (active && active.status !== 'running') open(false);
    } catch (e) { /* offline: the panel still opens */ }
  })();
})();
