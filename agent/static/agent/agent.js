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
      if (step.seq <= lastSeq) return;
      lastSeq = step.seq;
      if (renderedSteps.has(step.id)) return;
      renderedSteps.add(step.id);
      renderStep(step);
      if (live && step.changed && step.event_id) refreshKeys([`event:${step.event_id}`]);
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
    syncChips();
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
    run = null;
    wasActive = false;
    setChatIds([]);
    store.remove('sessionStorage', 'attnd.agent.scroll');
    statusLine.textContent = '';
    confirmBox.hidden = true;
    stopButton.hidden = true;
    sendButton.hidden = false;
    input.placeholder = 'What should Attnd do?';
    input.focus();
  }
  $('[data-agent-new]').addEventListener('click', newChat);

  // A request from the history list: shown on its own (a new conversation that starts with it).
  async function show(id, isLive) {
    log.textContent = '';
    renderedSteps.clear();
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
        const data = await post(root.dataset.startUrl, { goal: text, page: location.pathname + location.search,
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
      (await fetchCards(step.component.name, step.component.items)).forEach((item) => box.appendChild(makeCard(item)));
    } catch (err) {
      box.className = 'agent__msg agent__msg--step is-failed';
      box.textContent = err.status === 404 ? 'This is no longer there.' : err.message;
    }
    foldOld();
    log.scrollTop = log.scrollHeight;
    return box;
  }

  function liveCards() { return [...log.querySelectorAll('.agent__card:not(.is-folded):not(.is-gone)')]; }

  // Long chats: the latest cards stay live; older ones fold into one line that re-fetches when tapped.
  function foldOld() {
    const cards = liveCards();
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

  log.addEventListener('click', (e) => {
    const card = e.target.closest('.agent__card');
    if (!card || card.classList.contains('is-folded') || card.classList.contains('is-gone')) return;
    const button = e.target.closest('[data-component-act]');
    if (button) { e.preventDefault(); act(card, button.dataset.componentAct); return; }
    const title = e.target.closest('.title-link');
    if (title && card.dataset.detail) { e.preventDefault(); pick(card); return; }
    if (e.target.closest('a, button, input, select, textarea, label, summary, details, form, [data-copy]')) return;
    if (card.dataset.detail && e.target.closest('.agent__card-body')) pick(card);
  });
  log.addEventListener('submit', (e) => {
    const form = e.target.closest('form[data-component-form]');
    const card = form && form.closest('.agent__card');
    if (!card) return;
    e.preventDefault();
    if (form.dataset.confirmAction) act(card, form.dataset.confirmAction, form);
    else submitForm(card, form, e.submitter);
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
