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
    try { if (!chatIds().length) brief(); } catch (e) { /* still starting up: the end of this file briefs */ }
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
  // While the conversation is restored on load (A21) nothing scrolls: the log is hidden, filled in one go, then its
  // scroll position is set once and it fades in.
  let restoring = null;  // promises of cards still loading, while restoring
  function stick() { if (!restoring) log.scrollTop = log.scrollHeight; }
  // Console links in the assistant's words become real links ("Change your password → [Account](/console/account/)"):
  // only same-site paths, built with the DOM (never HTML from the model).
  const LINK_RE = /\[([^\]\n]{1,80})\]\((\/(?!\/)[^)\s]{0,200})\)/g;
  function richText(el, text) {
    let at = 0;
    for (const m of text.matchAll(LINK_RE)) {
      el.append(text.slice(at, m.index));
      const a = document.createElement('a');
      a.href = m[2];
      a.textContent = m[1];
      a.className = 'agent__link';
      a.addEventListener('click', (e) => { if (canvasOn() && openUrl(m[2], a)) e.preventDefault(); });
      el.append(a);
      at = m.index + m[0].length;
    }
    el.append(text.slice(at));
  }
  function bubble(kind, text) {
    $('[data-agent-hello]')?.remove();
    const el = document.createElement('div');
    el.className = `agent__msg agent__msg--${kind}`;
    if (kind === 'assistant') richText(el, text); else el.textContent = text;
    log.appendChild(el);
    if (live && kind !== 'user') window.attndReveal?.(el, 'in');
    if (!thinking.hidden) log.appendChild(thinking);
    stick();
    return el;
  }
  // A step line: a small status mark in a tinted circle (✓ changed, + added, × failed, • looked), then the text.
  // (No coloured left-border brackets: owner, Nov 21, 2026.)
  function stepLine(text, state) {
    $('[data-agent-hello]')?.remove();
    const el = document.createElement('div');
    el.className = 'agent__msg agent__msg--step';
    const kind = state === 'failed' ? 'danger' : state === 'changed' ? (/^(Added|Created|Saved new|Scheduled|Sent)\b/.test(text) ? 'added' : 'success') : '';
    const mark = document.createElement('span');
    mark.className = `status-mark${kind ? ` status-mark--${kind}` : ''}`;
    mark.setAttribute('aria-hidden', 'true');
    mark.textContent = { danger: '×', added: '+', success: '✓' }[kind] || '•';
    const label = document.createElement('span');
    label.className = 'agent__step-text';
    label.textContent = text;
    el.append(mark, label);
    if (state === 'failed') el.classList.add('is-failed');
    if (state === 'changed') el.classList.add('is-changed');
    log.appendChild(el);
    if (live) window.attndReveal?.(el, 'in');
    if (!thinking.hidden) log.appendChild(thinking);
    stick();
    return el;
  }
  function renderStep(step) {
    if (step.kind === 'component') return showComponents(step);
    if (step.kind === 'note') return bubble('assistant', step.text);
    if (step.kind === 'question') return question(step);
    if (step.kind === 'reply') {
      if (echoedReply !== null && step.text === echoedReply) { echoedReply = null; return document.createComment('reply'); }
      return bubble('user', step.text);
    }
    const el = stepLine(step.text, !step.ok ? 'failed' : step.changed ? 'changed' : '');
    if (step.url && step.changed) {
      const link = document.createElement('a');
      link.href = step.url;
      link.textContent = 'Open';
      link.className = 'agent__open';
      link.addEventListener('click', (e) => { if (canvasOn() && openUrl(step.url)) e.preventDefault(); });
      el.append(link);
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
        const said = stepLine(data.message, 'changed');
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
    stick();
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
      if (restoring && el instanceof Promise) restoring.push(el);
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
    // agent-loader.js loads this file at once on the next page while a run is active (it reloads the page or needs
    // a reply); otherwise only when Ask Attnd is opened.
    store.set('sessionStorage', 'attnd.agent.active', run.active ? '1' : '');
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
    stick();
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
      chip.addEventListener('click', () => {
        if (/^talk to a person$/i.test(text.trim())) { talkToPerson(); return; }  // stage I18: no model call
        input.value = text; fitInput(); form.requestSubmit();
      });
      suggestBox.appendChild(chip);
    });
  }

  function statusText(r) {
    const cost = parseFloat(r.cost_usd || '0');
    const spend = cost >= 0.005 ? ` · $${cost.toFixed(2)}` : '';
    return ({ running: 'Working…', waiting_confirmation: 'Waiting for you to confirm', waiting_reply: 'Waiting for your answer',
              done: 'Done', failed: 'Didn’t finish', stopped: 'Stopped' }[r.status] || r.status) + spend;
  }

  // A finished run's answer is drawn once (a note with the same words isn't repeated as the summary).
  const sameText = (a, b) => (a || '').replace(/\s+/g, ' ').trim() === (b || '').replace(/\s+/g, ' ').trim();
  function answerOnce(text) {
    const last = [...log.children].filter((e) => e !== thinking && e.nodeType === 1).pop();
    if (last && last.classList.contains('agent__msg--assistant') && sameText(last.textContent, text.replace(LINK_RE, '$1'))) return last;
    return bubble('assistant', text);
  }
  // 👍 / 👎 under a finished answer (stage I): stored with the run for staff review.
  function addFeedback(msg, r) {
    if (!msg || msg.nextElementSibling?.matches?.('.agent__feedback') || r.buttons) return;
    const row = document.createElement('div');
    row.className = 'agent__feedback';
    row.setAttribute('role', 'group');
    row.setAttribute('aria-label', 'Was this helpful?');
    [['1', '👍', 'Helpful'], ['-1', '👎', 'Not helpful']].forEach(([value, mark, label]) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'agent__feedback-button';
      b.textContent = mark;
      b.setAttribute('aria-label', label);
      b.setAttribute('aria-pressed', String(String(r.feedback) === value));
      b.addEventListener('click', async () => {
        const on = b.getAttribute('aria-pressed') !== 'true';
        try {
          await post(runUrl(r.id, 'feedback/'), { value: on ? value : '' });
          row.querySelectorAll('button').forEach((x) => x.setAttribute('aria-pressed', 'false'));
          b.setAttribute('aria-pressed', String(on));
          window.attndReveal?.(b, 'pulse');
        } catch (e) { /* not important enough to show an error */ }
      });
      row.appendChild(b);
    });
    msg.after(row);
  }
  function finished() {
    if (run.summary) addFeedback(answerOnce(run.summary), run);
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
  // Backoff (stage H): 1.5 s while steps arrive, doubling to 6 s while nothing changes, 15 s in a hidden tab.
  let quietPolls = 0;
  const pollDelay = () => (document.hidden ? 15000 : quietPolls < 0 ? 100 : Math.min(1500 * 2 ** quietPolls, 6000));
  function schedule() { if (!timer) timer = setTimeout(poll, pollDelay()); }
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && timer) { clearTimeout(timer); timer = null; quietPolls = 0; poll(); }
  });
  function stopPolling() { clearTimeout(timer); timer = null; }
  async function poll() {
    timer = null;
    if (!run) return;
    const oldest = Math.min(...[...unfinished.values()].map((u) => u.seq - 1), lastSeq);
    const seqBefore = lastSeq;
    try {
      // Stage I14: the server holds the request until something happens (≤ 10 s), so the next poll can follow at once.
      const data = await get(runUrl(run.id, `?after=${oldest}${document.hidden ? '' : '&wait=10'}`));
      renderRun(data);
      live = true;
      quietPolls = data.waited ? -1 : (lastSeq === seqBefore ? Math.min(quietPolls + 1, 2) : 0);
    }
    catch (e) { statusLine.textContent = 'Reconnecting…'; schedule(); }
  }

  // ---- the conversation: every request of this chat stays in the log (and across page loads) ----
  const chatIds = () => { try { return JSON.parse(store.get('sessionStorage', 'attnd.agent.chat') || '[]'); } catch (e) { return []; } };
  const setChatIds = (ids) => store.set('sessionStorage', 'attnd.agent.chat', JSON.stringify(ids.slice(-60)));
  // The conversation id on the server (stage I4): the same chat on every device and after signing in again.
  const thread = () => store.get('sessionStorage', 'attnd.agent.thread') || '';
  const setThread = (t) => { if (t) store.set('sessionStorage', 'attnd.agent.thread', t); };
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
    store.remove('sessionStorage', 'attnd.agent.thread');
    store.set('sessionStorage', 'attnd.agent.fresh', '1');  // "+": this tab starts empty, not the latest chat
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
    appendData(await get(runUrl(id, '?after=0')), isLive);
  }
  function appendData(data, isLive) {
    lastSeq = 0;
    live = false;
    if (!data.run.buttons) bubble('user', data.run.goal);
    wasActive = data.run.active;
    renderRun(data);
    if (!data.run.active && data.run.summary) answerOnce(data.run.summary);
    if (!data.run.active && data.run.error && data.run.status !== 'done') bubble('error', data.run.error);
    live = isLive !== false;
  }

  // ---- sending -------------------------------------------------------------------------------
  // Sending: the typed text morphs into the new message bubble and moves from the composer up to its place in the
  // log (FLIP: the bubble is created at its final spot, then animated from where the text was), while the composer
  // clears at the same time. Reduced motion: a plain fade.
  let echoedReply = null;  // a reply already drawn by flyUp (its step from the server isn't drawn again)
  function flyUp(text) {
    const from = input.getBoundingClientRect();
    const inStyle = getComputedStyle(input);
    const el = bubble('user', text);
    const to = el.getBoundingClientRect();
    if (reduceMotionSend.matches || !el.animate || !from.width) {
      el.animate?.([{ opacity: 0 }, { opacity: 1 }], { duration: 180, easing: 'ease-out' });
      return el;
    }
    const elStyle = getComputedStyle(el);
    const dx = (from.left + parseFloat(inStyle.paddingLeft)) - (to.left + parseFloat(elStyle.paddingLeft));
    const dy = (from.top + parseFloat(inStyle.paddingTop)) - (to.top + parseFloat(elStyle.paddingTop));
    const sx = Math.min(1.25, Math.max(.6, from.width / Math.max(to.width, 1)));
    el.style.transformOrigin = 'left top';
    el.animate([
      { transform: `translate(${dx}px, ${dy}px) scale(${sx}, 1)`, backgroundColor: 'transparent', color: inStyle.color,
        borderRadius: '1.4rem', boxShadow: 'none' },
      { transform: 'none', backgroundColor: elStyle.backgroundColor, color: elStyle.color,
        borderRadius: elStyle.borderRadius },
    ], { duration: 320, easing: 'cubic-bezier(.2, .8, .2, 1)' });
    return el;
  }
  const reduceMotionSend = window.matchMedia('(prefers-reduced-motion: reduce)');

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text) { input.focus(); return; }
    const send = sendButton;
    send.disabled = true;
    const replying = run && run.status === 'waiting_reply';
    if (replying) echoedReply = text;
    const sent = flyUp(text);
    input.value = '';  // the composer clears while the bubble flies up
    fitInput();
    try {
      if (replying) {
        renderRun(await post(runUrl(run.id, 'reply/'), { text }));
        live = true;
      } else {
        if (!run || !run.active) { lastSeq = 0; }
        showSuggestions([]);
        const data = await post(root.dataset.startUrl, { goal: text, page: full ? fromPage : location.pathname + location.search,
                                                          event_id: eventId || '', attachments: attachments.map((a) => a.id).join(','),
                                                          target: target ? JSON.stringify({ component: target.component, ids: target.ids }) : '',
                                                          thread: thread() });
        setThread(data.run?.thread);
        store.remove('sessionStorage', 'attnd.agent.fresh');
        if (data.run && data.run.id) setChatIds(chatIds().filter((x) => x !== data.run.id).concat([data.run.id]));
        attachments = [];
        filesList.textContent = '';
        refreshButton.hidden = true;
        syncLinks();
        wasActive = true;
        live = true;
        renderRun(data);
      }
    } catch (err) {
      sent.remove();  // not sent: the text goes back into the composer
      echoedReply = null;
      if (!input.value) { input.value = text; fitInput(); }
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
        const line = stepLine(data.step.text, !data.step.ok ? 'failed' : data.step.changed ? 'changed' : '');
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
    if (file) attachFile(file);
  });
  // Stage I10: the command bar. Ctrl/⌘+K anywhere opens and focuses the chat; "/" at the start lists quick actions
  // that show their card at once (no model call).
  const COMMANDS = [
    { key: 'attention', label: 'What needs attention', component: 'attention', ids: () => ({}) },
    { key: 'ready', label: 'Is it ready to publish? (and Publish)', component: 'event_readiness', event: true },
    { key: 'location', label: 'Add a location', component: 'location_form', event: true },
    { key: 'item', label: 'Add a schedule item', component: 'schedule_item_form', event: true },
    { key: 'notify', label: 'Send a notification', component: 'notification_form', event: true },
    { key: 'numbers', label: 'Numbers (likes, busiest hour)', component: 'usage_answer', event: true },
    { key: 'new', label: 'New event', component: 'event_new_form', ids: () => ({}) },
  ];
  const menu = document.createElement('ul');
  menu.className = 'agent__commands';
  menu.id = 'agent-commands';
  menu.setAttribute('role', 'listbox');
  menu.hidden = true;
  form.before(menu);
  input.setAttribute('aria-controls', 'agent-commands');
  let choices = [];
  let chosen = 0;
  function drawMenu() {
    const q = input.value.startsWith('/') ? input.value.slice(1).trim().toLowerCase() : null;
    choices = q === null ? [] : COMMANDS.filter((c) => (!c.event || eventId)
      && (c.key.startsWith(q) || c.label.toLowerCase().includes(q)));
    menu.textContent = '';
    menu.hidden = !choices.length;
    input.setAttribute('aria-expanded', String(!menu.hidden));
    chosen = Math.min(chosen, Math.max(choices.length - 1, 0));
    choices.forEach((c, i) => {
      const li = document.createElement('li');
      li.setAttribute('role', 'option');
      li.id = `agent-cmd-${c.key}`;
      li.setAttribute('aria-selected', String(i === chosen));
      li.innerHTML = '<span class="mono"></span> <span></span>';
      li.children[0].textContent = `/${c.key}`;
      li.children[1].textContent = c.label;
      li.addEventListener('mousedown', (e) => { e.preventDefault(); runCommand(c); });
      menu.appendChild(li);
    });
    if (choices[chosen]) input.setAttribute('aria-activedescendant', `agent-cmd-${choices[chosen].key}`);
    else input.removeAttribute('aria-activedescendant');
  }
  async function runCommand(c) {
    input.value = '';
    fitInput();
    drawMenu();
    try {
      const ids = c.event ? { event_id: eventId } : c.ids();
      const [item] = await fetchCards(c.component, [ids]);
      if (!item) return;
      const box = cardBox();
      box.appendChild(makeCard(item));
      box.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
      box.querySelector('input:not([type=hidden]), textarea')?.focus();
    } catch (err) { bubble('error', err.message); }
  }
  input.addEventListener('input', () => { chosen = 0; drawMenu(); });
  input.addEventListener('keydown', (e) => {
    if (menu.hidden) return;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      chosen = (chosen + (e.key === 'ArrowDown' ? 1 : choices.length - 1)) % choices.length;
      drawMenu();
    } else if (e.key === 'Enter' && !e.shiftKey && choices[chosen]) {
      e.preventDefault();
      e.stopImmediatePropagation();
      runCommand(choices[chosen]);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      input.value = '';
      drawMenu();
    }
  }, true);
  document.addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && !e.altKey && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      if (!full && panel.hidden) open();
      input.focus();
      input.select();
    }
  });

  // Stage I15: on a phone, the full-screen chat shows the event's sections as chips above the composer; each shows
  // its cards in the chat, and the pages stay one tap away.
  (function phoneTabs() {
    if (!full || !eventId || !window.matchMedia('(max-width: 1023px)').matches) return;
    const row = document.createElement('div');
    row.className = 'agent__tabs-chips';
    row.setAttribute('role', 'group');
    row.setAttribute('aria-label', 'Event sections');
    [['Overview', 'event_overview'], ['Ready?', 'event_readiness'], ['Locations', 'location_row'],
     ['Schedule', 'schedule_item_row'], ['Notifications', 'notification_row'], ['Numbers', 'usage_answer']]
      .forEach(([label, name]) => {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'chip';
        b.textContent = label;
        b.addEventListener('click', async () => {
          try {
            const items = await fetchCards(name, [{ event_id: eventId }]);
            const box = cardBox();
            if (items.length) appendGrouped(box, items);
            else { const p = document.createElement('p'); p.className = 'small muted'; p.textContent = `No ${label.toLowerCase()} yet.`; box.appendChild(p); }
            box.scrollIntoView({ block: 'start', behavior: 'smooth' });
          } catch (err) { bubble('error', err.message); }
        });
        row.appendChild(b);
      });
    const pages = document.createElement('a');
    pages.className = 'chip';
    pages.href = `/console/events/${eventId}/?page=1`;
    pages.textContent = 'Pages ↗';
    row.appendChild(pages);
    suggestBox.before(row);
  })();

  // Stage I18: "Talk to a person": a short form in the chat; the message goes to the Attnd team by email with this
  // chat's run id and page (never anything secret), and the reply comes by email.
  function talkToPerson() {
    if (log.querySelector('.agent__contact')) { log.querySelector('.agent__contact textarea').focus(); return; }
    const box = document.createElement('form');
    box.className = 'agent__contact stack stack--tight';
    box.innerHTML = '<label class="small strong" for="agent-contact-text">Write to the Attnd team</label>'
      + '<textarea id="agent-contact-text" rows="3" maxlength="4000" required></textarea>'
      + '<div class="cluster"><button class="button button--primary button--small">Send</button>'
      + '<button type="button" class="button button--ghost button--small" data-cancel>Cancel</button></div>';
    const text = box.querySelector('textarea');
    text.placeholder = run?.error || run?.summary ? 'What went wrong, or what you need…' : 'What do you need help with?';
    box.querySelector('[data-cancel]').addEventListener('click', () => box.remove());
    box.addEventListener('submit', async (e) => {
      e.preventDefault();
      try {
        const data = await post(root.dataset.contactUrl, { message: text.value, run: run ? run.id : '',
                                                           page: full ? fromPage : location.pathname });
        box.remove();
        stepLine(data.text, 'changed');
      } catch (err) { bubble('error', err.message); }
    });
    log.appendChild(box);
    window.attndReveal?.(box, 'in');
    box.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    text.focus();
  }
  $('[data-agent-person]')?.addEventListener('click', talkToPerson);

  // Stage I8: speak instead of typing (the browser's own Web Speech API; hidden where it isn't available).
  (function voice() {
    const Rec = window.SpeechRecognition || window.webkitSpeechRecognition;
    const mic = $('[data-agent-mic]');
    if (!Rec || !mic) return;
    mic.hidden = false;
    let rec = null;
    let base = '';
    const stop = () => { rec?.stop(); };
    mic.addEventListener('click', () => {
      if (rec) { stop(); return; }
      rec = new Rec();
      rec.lang = document.documentElement.lang || navigator.language || 'en-US';
      rec.interimResults = true;
      rec.continuous = false;
      base = input.value ? input.value.replace(/\s*$/, ' ') : '';
      rec.onresult = (e) => {
        input.value = base + [...e.results].map((r) => r[0].transcript).join('');
        fitInput();
      };
      rec.onend = () => { rec = null; mic.setAttribute('aria-pressed', 'false'); mic.classList.remove('is-listening'); input.focus(); };
      rec.onerror = (e) => { if (e.error === 'not-allowed') bubble('error', 'Allow the microphone for this site to speak to Ask Attnd.'); };
      mic.setAttribute('aria-pressed', 'true');
      mic.classList.add('is-listening');
      try { rec.start(); } catch (e) { rec = null; }
    });
  })();

  // Stage I3: drop a file anywhere on the panel / full-screen chat, or paste one (a screenshot, a copied file).
  panel.addEventListener('dragover', (e) => {
    if ([...(e.dataTransfer?.types || [])].includes('Files')) { e.preventDefault(); panel.classList.add('is-dropping'); }
  });
  panel.addEventListener('dragleave', (e) => { if (!panel.contains(e.relatedTarget)) panel.classList.remove('is-dropping'); });
  panel.addEventListener('drop', (e) => {
    panel.classList.remove('is-dropping');
    const files = [...(e.dataTransfer?.files || [])];
    if (!files.length) return;
    e.preventDefault();
    files.slice(0, 5).forEach(attachFile);
    input.focus();
  });
  input.addEventListener('paste', (e) => {
    const files = [...(e.clipboardData?.files || [])];
    if (!files.length) return;
    e.preventDefault();
    files.slice(0, 5).forEach(attachFile);
  });
  async function attachFile(file) {
    const body = new FormData();
    body.append('file', file);
    try {
      const data = await post(root.dataset.attachUrl, body);
      attachments.push(data.attachment);
      const item = document.createElement('li');
      item.textContent = `Attached: ${data.attachment.name}`;
      filesList.appendChild(item);
      if (!input.value.trim()) input.placeholder = 'What should Attnd do with it? (e.g. “Add this to my event”)';
    } catch (err) { bubble('error', err.message); }
  }

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
    lastNext = data.next_offset ?? null;  // more after these (the canvas's "Show more")
    return data.items;
  }
  let lastNext = null;

  function setBody(card, html) {
    const body = document.createElement('div');
    body.className = 'agent__card-body';
    body.innerHTML = html;  // server-rendered component (our templates), see the note at the top
    card.querySelector('.agent__card-body')?.remove();
    card.prepend(body);
    window.attndShowIf?.(body);
    window.attndOpenApp?.(body);
    window.attndEnhance?.(body);  // address search, the location map, Fill from this website
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
    setBody(card, item.html);
    return card;
  }

  // Lists with groups (notifications: "Scheduled · 2" first, then "Sent · 5") get a small heading per group.
  const GROUPS = { scheduled: 'Scheduled', sent: 'Sent' };
  function appendGrouped(into, items) {
    const counts = {};
    items.forEach((i) => { if (i.group) counts[i.group] = (counts[i.group] || 0) + 1; });
    let current = null;
    items.forEach((item) => {
      if (item.group && item.group !== current && GROUPS[item.group]) {
        current = item.group;
        const h = document.createElement('h4');
        h.className = 'eyebrow agent__group';
        h.textContent = `${GROUPS[item.group]} · ${counts[item.group]}`;
        into.appendChild(h);
      }
      into.appendChild(makeCard(item));
    });
  }

  // Stage I: an empty chat opens with the "What needs attention" briefing under the greeting (not a run).
  var briefed = false;  // (var: open() may run before this line during start-up)
  async function brief() {
    const hello = $('[data-agent-hello]');
    if (briefed || !hello || log.querySelector('.agent__brief')) return;
    briefed = true;
    try {
      // Stage I17: new organizers get the tour (the card says show: false otherwise), everyone else the briefing.
      let [item] = root.dataset.firstRun ? await fetchCards('first_run', [{}]) : [null];
      if (!item || item.html.includes('data-tour-hidden')) [item] = await fetchCards('attention', [{}]);
      if (!item || !$('[data-agent-hello]')) return;
      const box = document.createElement('div');
      box.className = 'agent__component agent__brief';
      box.appendChild(makeCard(item));
      hello.after(box);
      window.attndReveal?.(box, 'in');
    } catch (e) { briefed = false; }
  }
  function cardBox() {
    $('[data-agent-hello]')?.remove();
    log.querySelector('.agent__brief')?.remove();
    const box = document.createElement('div');
    box.className = 'agent__component';
    log.appendChild(box);
    if (live) window.attndReveal?.(box, 'in');
    return box;
  }

  // Full screen: what the canvas can show goes to the canvas (a list → its tab, one item → the overlay) and the chat
  // keeps one line ("Showing 18 locations →", tap to show it again); no column of tiles, nothing for a tap's detail.
  const NOUNS = { location_row: ['location', 'locations'], schedule_item_row: ['schedule item', 'schedule items'],
                  notification_row: ['notification', 'notifications'], event_tile: ['event', 'events'] };
  const OVERLAY = new Set(['location_form', 'schedule_item_form', 'event_form', 'object_history', 'event_new_form']);
  function canvasNote(step, items) {
    const name = step.component.name;
    const n = (items || step.component.items).length;
    const noun = NOUNS[name];
    const what = noun ? `${n} ${noun[n === 1 ? 0 : 1]}` : (items && items[0] ? (items[0].label || items[0].title) : step.text);
    const line = document.createElement('button');
    line.type = 'button';
    line.className = 'agent__shown link-button small';
    line.textContent = `Showing ${what} →`;
    line.addEventListener('click', () => showOnCanvas(step, line));
    return line;
  }
  async function showOnCanvas(step, opener, items) {
    const name = step.component.name;
    items = items || await fetchCards(name, step.component.items);
    if (OVERLAY.has(name) && items.length === 1) {
      overlayOpener = opener || null;
      showOverlay(items[0].label || items[0].title, await withHistory(name, items[0].ids, items));
    } else {
      toCanvas(name, items);
    }
    return items;
  }

  async function showComponents(step) {
    if (step.component.fill) return fillStep(step);
    if (canvasOn()) {
      if (step.component.picked) return document.createComment('picked');  // its detail is already on the canvas
      const box = cardBox();
      box.classList.add('agent__note');
      try {
        const items = live ? await showOnCanvas(step) : null;
        box.appendChild(canvasNote(step, items));
      } catch (err) {
        box.replaceWith(stepLine(err.status === 404 ? 'This is no longer there.' : err.message, 'failed'));
      }
      stick();
      return box;
    }
    const box = cardBox();
    try {
      const items = await fetchCards(step.component.name, step.component.items);
      appendGrouped(box, items);
    } catch (err) {
      box.replaceWith(stepLine(err.status === 404 ? 'This is no longer there.' : err.message, 'failed'));
    }
    foldOld();
    stick();
    return box;
  }

  // ---- Add with AI (every form component) and fill_form ------------------------------------------------------
  // The sparkle button above a form opens the chat with a prompt for that thing and remembers the form; what the
  // person then says is sent with it, and the agent's fill_form puts values into that same form in front of them
  // (highlighted, in sequence). Nothing is saved until they press Save (or say "save it": submit).
  let target = null;  // {component, ids, form}
  const targetChip = $('[data-agent-target]');
  function setTarget(t) {
    target = t;
    if (!targetChip) return;
    targetChip.hidden = !t;
    if (t) targetChip.querySelector('[data-agent-target-text]').textContent = t.label;
  }
  targetChip?.querySelector('[data-agent-target-clear]')?.addEventListener('click', () => { setTarget(null); input.focus(); });
  document.addEventListener('click', (e) => {
    const b = e.target.closest('[data-add-with-ai]');
    if (!b) return;
    e.preventDefault();
    const form = b.closest('.ai-offer')?.nextElementSibling;
    let ids = {};
    try { ids = JSON.parse(b.dataset.aiIds || '{}'); } catch (err) { ids = {}; }
    setTarget({ component: b.dataset.aiComponent, ids, form: form && form.matches('form') ? form : null,
                label: `Filling: ${b.closest('.agent__card')?.dataset.title || document.title.split(' · ')[0]}` });
    if (!full) open(false);
    input.placeholder = b.dataset.aiPrompt;
    input.focus();
    window.attndReveal?.(input.closest('form') || input, 'pulse');
  });

  // Only forms in Ask Attnd's own cards (chat or canvas): never a page's form, whose buttons bypass the confirm card.
  const inCard = (f) => !!(f && f.closest('.agent__card') && (log.contains(f) || (canvas && canvas.contains(f))));
  function findForm(name, ids) {
    const want = JSON.stringify(Object.fromEntries(Object.entries(ids || {}).sort()));
    if (target && inCard(target.form) && document.contains(target.form) && target.component === name
        && target.form.dataset.formIds === want) return target.form;
    const all = [...document.querySelectorAll(`form[data-form-component="${name}"]`)]
      .filter((f) => f.dataset.formIds === want && inCard(f));
    return all.find((f) => f.offsetParent !== null) || all[0] || null;
  }
  function applyFill(form, values, submit) {
    let n = 0;
    Object.entries(values).forEach(([name, value]) => {
      const fields = form.querySelectorAll(`[name="${CSS.escape(name)}"]`);
      if (!fields.length) return;
      fields.forEach((el) => {
        if (el.type === 'checkbox') el.checked = /^(true|1|yes|on)$/i.test(value);
        else if (el.type === 'radio') el.checked = el.value === value;
        else el.value = value;
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
      });
      const el = fields[0];
      el.closest('details')?.setAttribute('open', '');  // Details for the assistant: open where it filled
      const box = el.closest('.field') || el;
      box.classList.add('is-ai-filled');
      window.attndReveal?.(box, 'glow', { delay: n * 90 });
      if (n === 0) box.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
      n += 1;
    });
    window.attndShowIf?.(form);
    // Never submit a form that always asks (its Send goes through the confirm card when the person presses it).
    if (submit && inCard(form) && !form.dataset.confirmAction) {
      const save = form.querySelector('button:not([type=button]):not([name])') || form.querySelector('button:not([type=button])');
      setTimeout(() => (save ? form.requestSubmit(save) : form.requestSubmit()), 300);
    }
    return n;
  }
  async function fillStep(step) {
    const { name, items, fill, submit } = step.component;
    const ids = items[0] || {};
    const line = stepLine(step.text, submit ? 'changed' : '');
    if (!live) return line;  // history: just the line
    let form = findForm(name, ids);
    try {
      if (!form) {  // not on screen yet: the canvas overlay (full screen) or a card in the chat
        if (canvasOn()) await openComponent(name, ids, null);
        else {
          const box = cardBox();
          (await fetchCards(name, [ids])).forEach((item) => box.appendChild(makeCard(item)));
        }
        form = findForm(name, ids);
      }
      if (form) applyFill(form, fill, submit);
    } catch (err) { bubble('error', err.message); }
    return line;
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
      const old = card.querySelector(".agent__card-body")?.innerHTML;
      setBody(card, item.html);
      if (old !== undefined && !sameHtml(old, item.html)) reveal(card, 'pulse');  // it changed: draw the eye
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
    // Canvas list sections of a changed event reload whole (new rows appear, deleted ones go)…
    const sections = canvas ? [...canvas.querySelectorAll('.canvas__section[data-parent]')].filter((sec) => {
      const parent = JSON.parse(sec.dataset.parent || '{}');
      return sec.dataset.component.endsWith('_row') || sec.dataset.component === 'event_tile'
        ? keys.some((k) => k === `event:${parent.event_id}` || (!parent.event_id && k.startsWith('event:'))) : false;
    }) : [];
    sections.forEach(reloadSection);
    // …and every other copy is fetched again on its own.
    liveCards().forEach((card) => {
      if (card === except || sections.some((sec) => sec.contains(card))) return;
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

  async function act(card, action, form, fields) {
    const body = form ? new FormData(form) : new FormData();
    if (fields) body.set('fields', fields);  // this button's own values (which member, which role)
    body.set('run', run ? run.id : '');  // none yet: the server starts a quiet one for the buttons
    body.set('component', card.dataset.component);
    body.set('ids', card.dataset.ids);
    body.set('action', action);
    body.set('thread', thread());
    try {
      const data = await post(root.dataset.actUrl, body);
      setThread(data.run?.thread);
      if (data.invalid) { setBody(card, data.items[0].html); return; }
      renderedSteps.add(data.step.id);
      if (!run || run.id !== data.run.id) setChatIds(chatIds().filter((x) => x !== data.run.id).concat([data.run.id]));
      stepLine(data.step.text, '');
      live = true;
      renderRun({ run: data.run, steps: [] });
    } catch (err) { bubble('error', err.message); }
  }

  async function submitForm(card, form, submitter) {
    const body = new FormData(form, submitter || undefined);
    card.classList.add('is-busy');
    try {
      // (getAttribute: an <input name="action"> shadows form.action)
      const response = await fetch(form.getAttribute('action'), { method: 'POST', body, credentials: 'same-origin',
        headers: { 'X-CSRFToken': csrf, 'X-Attnd-Component': card.dataset.component } });
      // The server answers JSON (console.middleware): errors → the component again; saved → what happened, the
      // Undo id and where the page would go. No HTML scraping.
      if (!(response.headers.get('Content-Type') || '').includes('json')) {
        throw new Error(response.status === 404 ? 'Not found, or you don’t have access to it.'
                                                : 'That didn’t work. Please try again.');
      }
      const data = await response.json();
      if (data.html) { setBody(card, data.html); return; }  // errors, in place
      if (!data.saved) throw new Error(data.error || 'Not saved. Open the page to see why.');
      const savedLine = stepLine((data.messages || []).join(' ') || 'Saved', 'changed');
      const ids0 = JSON.parse(card.dataset.ids);
      if (data.undo) addUndo(savedLine, data.undo, 'available', ids0.event_id);
      if (data.next) showNext(data.next);  // live ops: the drafted notification, prefilled (sending still asks)
      const keys = card.dataset.keys.split(' ');
      const made = (data.location || '').match(/\/console\/events\/(\d+)\/$/);
      if (card.dataset.component === 'event_new_form' && made) {  // an empty event was created: work on it here
        if (full) { location.assign(`${root.dataset.assistantUrl}?event=${made[1]}&from=${encodeURIComponent(`/console/events/${made[1]}/`)}`); return; }
      }
      const level = overlayStack.length ? overlayStack[overlayStack.length - 1] : null;
      const created = /_form$/.test(card.dataset.component) && !/"(location_id|item_id)"/.test(card.dataset.ids)
                      && !(submitter && submitter.name === 'save_add');
      if (level && level.el.contains(card) && created) {  // a new location / item: back to the list, which shows it
        refreshKeys(keys.length ? keys : [`event:${ids0.event_id}`], card);
        popOverlay();
        return;
      }
      await refresh(card);
      refreshKeys(keys, card);
      const ids = JSON.parse(card.dataset.ids);
      if (ids.event_id) pageChanged(ids.event_id);
    } catch (err) { bubble('error', err.message); }
    finally { card.classList.remove('is-busy'); }
  }

  async function showNext(next) {
    try {
      const [item] = await fetchCards(next.component, [next.ids]);
      if (!item) return;
      const box = cardBox();
      const card = makeCard(item);
      box.appendChild(card);
      const form = card.querySelector('form');
      if (form && next.fill) applyFill(form, Object.fromEntries(Object.entries(next.fill).map(([k, v]) => [k, String(v)])), false);
      card.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    } catch (e) { /* the notifications page still has it */ }
  }

  function onCardClick(e) {
    const card = e.target.closest('.agent__card');
    if (!card || card.classList.contains('is-folded') || card.classList.contains('is-gone')) return;
    const button = e.target.closest('[data-component-act]');
    if (button) { e.preventDefault(); act(card, button.dataset.componentAct, null, button.dataset.componentFields); return; }
    const title = e.target.closest('.title-link');
    if (title && card.dataset.detail) { e.preventDefault(); pick(card); return; }
    // Full screen: links to events, locations, the schedule and notifications open on the canvas, not as pages.
    const link = e.target.closest('a[href]');
    if (link && canvasOn() && !link.target && !link.hasAttribute('download') && openUrl(link.getAttribute('href'), link)) {
      e.preventDefault();
      return;
    }
    if (e.target.closest('a, button, input, select, textarea, label, summary, details, form, [data-copy], .drag-handle')) return;
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
  // Each tab holds sections, one per component (Notifications: the compose form, then every notification). A list section
  // remembers its parent ids ({event_id}) and reloads as a whole when something of that event changes, so new
  // rows appear and deleted ones go. Toolbars add things (Add location, Add schedule item…) in the overlay.
  const KINDS = { event_tile: 'Events', event_new_form: 'Events', event_overview: 'Overview', event_stats: 'Overview',
                  live_on_phone: 'Overview', needs_review: 'Overview', event_readiness: 'Overview', event_header: 'Overview', event_form: 'Overview',
                  object_history: 'History', location_row: 'Locations', location_form: 'Locations',
                  schedule_item_row: 'Schedule', schedule_item_form: 'Schedule', notification_row: 'Notifications',
                  notification_form: 'Notifications', event_team: 'Team', event_metrics: 'Metrics', usage_answer: 'Metrics',
                  event_map: 'Overview', event_lifecycle: 'Overview', sharing_kit: 'Overview', event_exports: 'Overview', event_rollover: 'Overview',
                  changeset_review: 'Review', app_layout: 'Settings', website_watching: 'Settings',
                  event_context_form: 'Settings', event_flyer: 'Settings', event_versions: 'History',
                  prospect_row: 'Prospects', prospect_detail: 'Prospects', prospect_run_row: 'Prospects',
                  prospect_settings_form: 'Prospects' };
  const ORDER = ['Events', 'Overview', 'Review', 'Locations', 'Schedule', 'Notifications', 'Team', 'Metrics', 'Settings',
                 'History', 'Prospects'];
  const SECTION_ORDER = ['event_tile', 'event_overview', 'event_readiness', 'event_lifecycle', 'sharing_kit', 'event_exports',
                         'notification_form', 'notification_row', 'app_layout', 'website_watching',
                         'event_context_form', 'event_flyer', 'object_history', 'event_versions',
                         'prospect_run_row', 'prospect_row'];
  const EMPTY = { location_row: 'No locations yet. Add stages, booths, food, anywhere attendees go.',
                  schedule_item_row: 'No schedule items yet.', notification_row: 'No notifications sent or scheduled yet.',
                  event_tile: 'No events yet.', prospect_row: 'No open prospects. Run a discovery from its settings.',
                  prospect_run_row: 'No discovery runs yet.' };
  const HEADINGS = { prospect_run_row: 'Latest runs', prospect_row: 'Open prospects, best fit first' };  // (notifications: the groups' own headings, Scheduled · N and Sent · N)
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const tabs = new Map();  // label -> {button, pane, label}
  let overlayStack = [];   // [{title, items, el}]
  let overlayOpener = null;

  function selectTab(label) {
    const before = [...tabs.values()].find((t) => !t.pane.hidden);
    tabs.forEach((t, key) => {
      const on = key === label;
      t.button.setAttribute('aria-selected', String(on));
      t.button.tabIndex = on ? 0 : -1;
      t.pane.hidden = !on;
    });
    if (overlayStack.length) closeOverlay(false, true);
    // The new panel arrives from its side of the tab order (never an instant cut), the tab itself pulses faintly.
    const now = tabs.get(label);
    if (now && before && before !== now) {
      const from = ORDER.indexOf(label) > ORDER.indexOf(before.label) ? 'right' : 'left';
      reveal(now.pane, 'slide', { from });
      reveal(now.button, 'pulse');
    }
  }
  const reveal = (el, kind, opts) => (window.attndReveal ? window.attndReveal(el, kind, opts) : Promise.resolve());

  function toolbarFor(label) {
    const add = (text, name, title) => ({ text, run: (b) => openComponent(name, { event_id: eventId }, title, b) });
    if (label === 'Locations' && eventId) return [add('Add location', 'location_form', 'New location')];
    if (label === 'Schedule' && eventId) return [add('Add schedule item', 'schedule_item_form', 'New schedule item')];
    return (TOOLBARS[label] || []).filter((x) => !x.needsEvent || eventId);
  }
  const TOOLBARS = {
    Prospects: [{ text: 'Discovery settings', plain: true, run: (b) => openComponent('prospect_settings_form', {}, 'Prospect settings', b) },
                { text: 'Export CSV', plain: true, run: () => { location.href = '/console/prospects/export.csv'; } }],
    Events: [{ text: 'New event', run: (b) => openComponent('event_new_form', {}, 'New event', b) }],
    Overview: [{ text: 'Edit details', needsEvent: true, run: (b) => openComponent('event_form', { event_id: eventId }, 'Details', b) },
               { text: 'History', needsEvent: true, plain: true, run: (b) => openComponent('object_history', { event_id: eventId }, 'History', b) }],
  };

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
    const tools = toolbarFor(label);
    if (tools.length) {
      const row = document.createElement('div');
      row.className = 'canvas__toolbar';
      tools.forEach((tool) => {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = `button button--${tool.plain ? 'outline' : 'primary'} button--small`;
        b.textContent = tool.plain ? tool.text : `+ ${tool.text}`;
        b.addEventListener('click', () => tool.run(b));
        row.appendChild(b);
      });
      pane.appendChild(row);
    }
    pane.dataset.tab = label;
    const grid = document.createElement('div');
    grid.className = 'canvas__grid';
    pane.appendChild(grid);
    const t = { button, pane, label, grid };
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

  function section(t, name) {
    let el = t.grid.querySelector(`:scope > [data-section="${name}"]`);
    if (el) return el;
    el = document.createElement('section');
    el.className = 'canvas__section';
    el.dataset.section = name;
    const rank = (n) => { const i = SECTION_ORDER.indexOf(n); return i < 0 ? 50 : i; };
    const after = [...t.grid.querySelectorAll(':scope > [data-section]')].find((s) => rank(s.dataset.section) > rank(name));
    t.grid.insertBefore(el, after || null);
    return el;
  }

  // "Show more" under a long list (the server sends 60 at a time).
  function moreButton(sec, next) {
    sec.querySelector(':scope > .canvas__more')?.remove();
    if (next == null) return;
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'button button--outline button--small canvas__more';
    b.dataset.moreOffset = next;
    b.textContent = 'Show more';
    sec.appendChild(b);
  }
  canvas?.addEventListener('click', async (e) => {
    const b = e.target.closest('[data-more-offset]');
    if (!b) return;
    const sec = b.closest('.canvas__section');
    b.disabled = true;
    try {
      const response = await fetch(`${root.dataset.componentsUrl}?component=${encodeURIComponent(sec.dataset.component)}`
        + `&items=${itemsParam([JSON.parse(sec.dataset.parent || '{}')])}&offset=${b.dataset.moreOffset}`,
        { credentials: 'same-origin', headers: { Accept: 'application/json' } });
      const data = await response.json();
      const wrap = sec.querySelector('.canvas__cards');
      const before = wrap.children.length;
      appendGrouped(wrap, data.items || []);
      [...wrap.children].slice(before).forEach((c) => reveal(c, 'in'));
      moreButton(sec, data.next_offset ?? null);
    } catch (err) { b.disabled = false; }
  });

  function fillSection(sec, name, items) {
    const next = lastNext;
    sec.textContent = '';
    if (HEADINGS[name]) {
      const h = document.createElement('h3');
      h.className = 'title-4 canvas__heading';
      h.textContent = HEADINGS[name];
      sec.appendChild(h);
    }
    const wrap = document.createElement('div');
    wrap.className = 'canvas__cards' + (name.endsWith('_row') ? ' canvas__cards--rows' : '');
    appendGrouped(wrap, items);
    if (!items.length) {
      const p = document.createElement('p');
      p.className = 'canvas__none muted';
      p.textContent = EMPTY[name] || 'Nothing here yet.';
      wrap.appendChild(p);
    }
    sec.appendChild(wrap);
    moreButton(sec, next);
    if (name === 'location_row' && items.length > 1 && window.makeSortable) sortable(wrap, sec);
    return wrap;
  }

  // Locations on the canvas can be dragged into the order attendees see (the same endpoint as the page).
  function sortable(wrap, sec) {
    wrap.dataset.sortable = '';
    window.makeSortable(wrap, {
      handle: '.drag-handle',
      async onEnd() {
        const parent = JSON.parse(sec.dataset.parent || '{}');
        const order = [...wrap.querySelectorAll(':scope > .agent__card')].map((c) => JSON.parse(c.dataset.ids).location_id);
        try {
          await post(root.dataset.reorderUrl.replace(/\/0\//, `/${parent.event_id}/`), { order: order.join(',') });
          stepLine('Saved the new order of the locations', 'changed');
        } catch (err) { bubble('error', err.message); }
      },
    });
  }

  function toCanvas(name, items, parent, quiet) {
    const t = ensureTab(KINDS[name] || 'More');
    const sec = section(t, name);
    if (parent) sec.dataset.parent = JSON.stringify(parent);
    else if (name.endsWith('_row') && items[0] && items[0].ids.event_id) sec.dataset.parent = JSON.stringify({ event_id: items[0].ids.event_id });
    else if (name === 'event_tile') sec.dataset.parent = '{}';
    sec.dataset.component = name;
    fillSection(sec, name, items);
    if (!quiet) selectTab(t.label);
    return sec;
  }

  // A whole list section again (new rows appear, deleted ones go): one request instead of one per card.
  // Reloading a list: rows glide to their new place (FLIP: moved, e.g. Scheduled → Sent), new rows fade in and pulse,
  // changed ones pulse, deleted ones collapse out first.
  const sameHtml = (a, b) => a.replace(/c[0-9a-f]{6}-/g, '') === b.replace(/c[0-9a-f]{6}-/g, '');
  async function reloadSection(sec) {
    const name = sec.dataset.component;
    let items;
    try { items = await fetchCards(name, [JSON.parse(sec.dataset.parent || '{}')]); }
    catch (err) { if (err.status === 404 || /none found/.test(err.message)) items = []; else return; }
    const wrap = sec.querySelector('.canvas__cards');
    const old = new Map([...(wrap ? wrap.querySelectorAll(':scope > .agent__card') : [])].map((c) => [c.dataset.ids, c]));
    const keep = new Set(items.map((i) => JSON.stringify(i.ids)));
    await Promise.all([...old].filter(([ids]) => !keep.has(ids)).map(([, c]) => reveal(c, 'collapse')));
    const html = new Map([...old].map(([ids, c]) => [ids, c.querySelector('.agent__card-body')?.innerHTML || '']));
    const rects = new Map([...old].filter(([ids]) => keep.has(ids)).map(([ids, c]) => [ids, c.getBoundingClientRect()]));
    fillSection(sec, name, items);
    const fresh = sec.querySelector('.canvas__cards');
    fresh?.querySelectorAll(':scope > .agent__card').forEach((c) => {
      const before = html.get(c.dataset.ids);
      const was = rects.get(c.dataset.ids);
      if (was) { const now = c.getBoundingClientRect(); reveal(c, 'flip', { dx: was.left - now.left, dy: was.top - now.top }); }
      if (before === undefined && old.size) { reveal(c, 'in'); reveal(c, 'pulse', { delay: 120 }); }
      else if (before !== undefined && !sameHtml(before, c.querySelector('.agent__card-body')?.innerHTML || '')) reveal(c, 'pulse');
    });
  }

  async function openDetail(card, opener) {
    const ids = JSON.parse(card.dataset.ids);
    if (card.dataset.component === 'event_tile' && ids.event_id !== eventId) {  // another event: full screen for it
      location.assign(`${root.dataset.assistantUrl}?event=${ids.event_id}&from=${encodeURIComponent(`/console/events/${ids.event_id}/`)}`);
      return;
    }
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
      showOverlay(card.dataset.title, await withHistory(card.dataset.detail, detailIds, items), true);
    } catch (err) { bubble('error', err.message); }
    finally { card.classList.remove('is-busy'); }
  }

  // An item's detail also shows its history when there is one (object_history), under the form.
  async function withHistory(name, ids, items) {
    if (!HISTORY_OF[name] || !ids[HISTORY_OF[name]]) return items;
    try { return items.concat(await fetchCards('object_history', [ids])); } catch (e) { return items; }
  }
  const HISTORY_OF = { location_form: 'location_id', schedule_item_form: 'item_id' };  // details with a history card

  // Open any component in the overlay (toolbar buttons, links inside cards, finished background tasks).
  async function openComponent(name, ids, title, opener) {
    try {
      const items = await withHistory(name, ids, await fetchCards(name, [ids]));
      if (!overlayStack.length) overlayOpener = opener || document.activeElement;
      showOverlay(title || items[0].label || items[0].title, items, overlayStack.length > 0);
      return true;
    } catch (err) { bubble('error', err.message); return false; }
  }

  // Console pages the canvas can show itself (links in cards, step links, the task tray): everything for events,
  // locations, the schedule and notifications stays in full screen. Anything else (Studio, the AI review, diagnostics…)
  // opens as a page.
  // The routes come from the server (agent/canvas.py ROUTES): path → component, ids and the canvas tab that lists it.
  const ROUTES = (() => {
    try { return JSON.parse(canvas?.dataset.routes || '[]').map(([p, name, tab]) => [new RegExp(p), name, tab]); }
    catch (e) { return []; }
  })();
  function openUrl(href, opener) {
    if (!canvasOn() || !href) return false;
    let url;
    try { url = new URL(href, location.origin); } catch (e) { return false; }
    if (url.origin !== location.origin || url.search) return false;
    for (const [pattern, name, tab] of ROUTES) {
      const m = url.pathname.match(pattern);
      if (!m) continue;
      const ids = Object.fromEntries(Object.entries(m.groups || {}).map(([k, v]) => [k, +v]));
      const sameEvent = !ids.event_id || ids.event_id === eventId;
      if (tab && sameEvent && tabs.has(tab) && !ids.location_id && !ids.item_id) { selectTab(tab); return true; }
      if (tab && !['event_overview', 'object_history', 'event_form'].includes(name)) return false;  // another event's list
      openComponent(name, ids, null, opener);
      return true;
    }
    return false;
  }
  window.attndCanvasOpen = openUrl;  // console.js: links in the task tray open here in full screen
  // The console's own links around the full-screen chat (top bar: Your events, New event) open on the canvas too.
  if (full) {
    document.addEventListener('click', (e) => {
      const link = e.target.closest('a[href]');
      if (!link || e.defaultPrevented || root.contains(link) || canvas?.contains(link) || link.closest('#task-tray, .task-toast')) return;
      if (e.metaKey || e.ctrlKey || e.shiftKey || link.target) return;
      if (openUrl(link.getAttribute('href'), link)) e.preventDefault();
    });
  }

  function showOverlay(title, items, push) {
    const overlay = canvas.querySelector('[data-canvas-overlay]');
    const body = overlay.querySelector('[data-overlay-body]');
    if (!push) { overlayStack = []; body.textContent = ''; }
    const level = document.createElement('div');
    level.className = 'canvas__level';
    items.forEach((item) => level.appendChild(makeCard(item)));
    const below = overlayStack[overlayStack.length - 1];
    overlayStack.push({ title, items, el: level });
    if (below) below.el.hidden = true;
    body.appendChild(level);
    const opening = overlay.hidden;
    overlay.classList.remove('is-leaving');
    overlay.hidden = false;
    if (!reduceMotion.matches) animate(opening ? overlay : level, opening ? 'is-entering' : 'is-entering');
    renderCrumbs();
    body.scrollTop = 0;
    overlay.querySelector('[data-overlay-title]').focus();
  }

  // The overlay's animations go through the console's one helper (console.js attndReveal): open slides in from the
  // right, Back slides out to the right, the level below comes back from the left.
  function animate(el, cls) {
    const kind = { 'is-entering': ['slide', { from: 'right' }], 'is-leaving': ['out', {}],
                   'is-returning': ['slide', { from: 'left' }] }[cls];
    return kind ? reveal(el, kind[0], kind[1]) : Promise.resolve();
  }

  function renderCrumbs() {
    const overlay = canvas.querySelector('[data-overlay-crumbs]').closest('[data-canvas-overlay]');
    const crumbs = overlay.querySelector('[data-overlay-crumbs]');
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
        b.addEventListener('click', () => popTo(i));
        li.appendChild(b);
      }
      crumbs.appendChild(li);
    });
    overlay.querySelector('[data-overlay-title]').textContent = trail[trail.length - 1];
  }

  // Back: the current level slides out to the right with a fade (the open animation in reverse); the level below
  // comes back. From the first level the whole overlay leaves the same way.
  let popping = false;
  async function popTo(depth) {
    if (popping || !overlayStack.length) return;
    if (depth <= 0) { await closeOverlay(true); return; }
    popping = true;
    const top = overlayStack[overlayStack.length - 1];
    await animate(top.el, 'is-leaving');
    overlayStack.splice(depth).forEach((l) => l.el.remove());
    const back = overlayStack[overlayStack.length - 1];
    back.el.hidden = false;
    animate(back.el, 'is-returning');
    renderCrumbs();
    canvas.querySelector('[data-overlay-title]').focus();
    popping = false;
  }
  function popOverlay() { popTo(overlayStack.length - 1); }

  async function closeOverlay(returnFocus, instant) {
    const overlay = canvas.querySelector('[data-canvas-overlay]');
    if (overlay.hidden) return;
    if (!instant) { popping = true; await animate(overlay, 'is-leaving'); popping = false; }
    overlayStack = [];
    overlay.hidden = true;
    overlay.classList.remove('is-leaving');
    overlay.querySelector('[data-overlay-body]').textContent = '';
    if (returnFocus && overlayOpener && document.contains(overlayOpener)) overlayOpener.focus();
    overlayOpener = null;
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

  // The event the page is about: its overview, locations, schedule and notifications (the compose form and every notification)
  // are on the canvas from the start; empty lists say so, and the toolbars add to them.
  // Staff: the Prospects tab (runs, then the open prospects by fit); absent for everyone else.
  async function preloadProspects() {
    if (!('staff' in root.dataset)) return;
    for (const name of ['prospect_run_row', 'prospect_row']) {
      let items = [];
      try { items = await fetchCards(name, [{}]); } catch (e) { /* none yet */ }
      toCanvas(name, items, {}, true);
    }
  }
  // The server drew the canvas (agent/canvas.py, A21): adopt its tabs, panes and cards as if toCanvas had made them.
  function adoptCanvas() {
    const bar = canvas.querySelector('[data-canvas-tabs]');
    if (!bar || !('ssr' in bar.dataset)) return false;
    bar.querySelectorAll('[role=tab]').forEach((button) => {
      const label = button.dataset.tabLabel;
      const pane = canvas.querySelector(`#${button.getAttribute('aria-controls')}`);
      const grid = pane.querySelector('.canvas__grid');
      const tools = toolbarFor(label);
      if (tools.length) {
        const row = document.createElement('div');
        row.className = 'canvas__toolbar';
        tools.forEach((tool) => {
          const b = document.createElement('button');
          b.type = 'button';
          b.className = `button button--${tool.plain ? 'outline' : 'primary'} button--small`;
          b.textContent = tool.plain ? tool.text : `+ ${tool.text}`;
          b.addEventListener('click', () => tool.run(b));
          row.appendChild(b);
        });
        pane.prepend(row);
      }
      button.addEventListener('click', () => selectTab(label));
      tabs.set(label, { button, pane, label, grid });
      pane.querySelectorAll('.canvas__section[data-component="location_row"]').forEach((sec) => {
        const wrap = sec.querySelector('.canvas__cards');
        if (wrap && wrap.querySelectorAll('.agent__card').length > 1 && window.makeSortable) sortable(wrap, sec);
      });
    });
    window.attndShowIf?.(canvas);
    window.attndOpenApp?.(canvas);
    window.attndEnhance?.(canvas);
    return true;
  }

  async function preloadCanvas() {
    if (!canvasOn()) return;
    if (adoptCanvas()) return;
    preloadProspects();
    if (!eventId) {
      try { toCanvas('event_tile', await fetchCards('event_tile', [{}]), {}, true); } catch (e) { toCanvas('event_tile', [], {}, true); }
      selectTab('Events');
      return;
    }
    // Every event and "New event" too, so the whole console is reachable from here.
    try { toCanvas('event_tile', await fetchCards('event_tile', [{}]), {}, true); } catch (e) { toCanvas('event_tile', [], {}, true); }
    const parent = { event_id: eventId };
    for (const name of ['event_overview', 'event_readiness', 'event_lifecycle', 'sharing_kit', 'event_exports', 'event_rollover', 'changeset_review',
                        'location_row', 'schedule_item_row', 'notification_form', 'notification_row',
                        'event_team', 'event_metrics', 'usage_answer', 'app_layout', 'website_watching', 'event_context_form',
                        'event_flyer', 'object_history', 'event_versions']) {
      let items = [];
      try { items = await fetchCards(name, [parent]); } catch (e) { /* none yet: the section says so */ }
      if (name === 'changeset_review' && !items.length) continue;  // the Review tab only while something waits
      toCanvas(name, items, parent, true);
      if (name === 'changeset_review') tabs.get('Review').button.textContent = `Review · ${items.length}`;
    }
    selectTab('Overview');
  }

  // Live numbers on cards (Overview numbers, Metrics): console.js's one live-numbers timer (60 s while visible,
  // paused when hidden) calls this; every visible live card comes back in one batch request.
  window.attndLiveCards = async () => {
    const cards = liveCards().filter((c) => c.offsetParent !== null && c.querySelector('[data-live]'));
    if (!cards.length) return;
    const q = cards.map((c) => ({ component: c.dataset.component, items: [JSON.parse(c.dataset.ids)] }));
    try {
      const response = await fetch(`${root.dataset.batchUrl}?q=${encodeURIComponent(JSON.stringify(q))}`,
                                   { credentials: 'same-origin', headers: { Accept: 'application/json' } });
      const data = await response.json();
      (data.results || []).forEach((r, i) => {
        const item = r.items && r.items[0];
        const body = cards[i].querySelector('.agent__card-body');
        if (!item || !body) return;
        const tmp = document.createElement('div');
        tmp.innerHTML = item.html;  // server-rendered component (our templates)
        const fresh = [...tmp.querySelectorAll('[data-live]')];
        body.querySelectorAll('[data-live]').forEach((el, j) => { if (fresh[j]) window.attndLiveSwap(el, fresh[j]); });
      });
    } catch (e) { /* offline: next minute */ }
  };

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

  // "Earlier · 12": older requests of a long conversation, loaded on demand above the first one.
  function earlierButton(older) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'link-button small agent__earlier';
    b.textContent = `Earlier · ${older.length}`;
    b.addEventListener('click', async () => {
      b.disabled = true;
      try {
        const data = await get(`${root.dataset.runsUrl}?chat=${older.join(',')}`);
        const keep = [...log.childNodes];
        log.textContent = '';
        (data.chat || []).forEach((c) => appendData(c, false));
        keep.forEach((n) => { if (n !== b) log.appendChild(n); });
        setChatIds([...(data.chat || []).map((c) => c.run.id), ...chatIds()]);
      } catch (e) { b.disabled = false; }
    });
    log.prepend(b);
  }

  // ---- on load: pick up a run that is still going -------------------------------------------
  (async () => {
    if (full) { root.classList.add('is-open'); preloadCanvas(); }
    else if (store.get('sessionStorage', 'attnd.agent.open') === '1') open(false);
    // One request for the whole conversation (its runs with their steps), drawn hidden, scrolled once, faded in.
    const ids = chatIds();
    if (ids.length) root.classList.add('is-restoring');
    try {
      // No ids in this tab (another device, signed in again): the latest conversation on the server (stage I4).
      const latest = ids.length || store.get('sessionStorage', 'attnd.agent.fresh') ? '' : `&latest=1&event=${eventId || ''}`;
      const data = await get(`${root.dataset.runsUrl}?chat=${ids.join(',')}${latest}`);
      const active = data.runs.find((r) => r.active);
      const chat = data.chat || [];
      setChatIds(chat.map((c) => c.run.id));
      setThread(data.thread || chat[chat.length - 1]?.run.thread);
      if (chat.length) {
        log.textContent = '';
        restoring = [];
        chat.forEach((c, i) => appendData(c, i === chat.length - 1 && c.run.active));
        await Promise.allSettled(restoring);
        restoring = null;
        const saved = parseInt(store.get('sessionStorage', 'attnd.agent.scroll') || '', 10);
        log.scrollTop = Number.isNaN(saved) ? log.scrollHeight : saved;
        if (data.earlier?.length) earlierButton(data.earlier);
      }
      if (active && active.status !== 'running') open(false);
    } catch (e) { /* offline: the panel still opens */ }
    restoring = null;
    if (root.classList.contains('is-restoring')) {
      root.classList.remove('is-restoring');
      root.classList.add('is-restored');  // one gentle fade-in (agent.css)
    }
  })();
  if (root.dataset.openOnLoad) { delete root.dataset.openOnLoad; if (panel.hidden) open(); }  // the click that loaded us
  if (!chatIds().length && (full || !panel.hidden)) brief();
  window.attndAgentReady = true;  // agent-loader.js: later clicks reach the handlers above directly
  if (window.attndAgentPending) { const b = window.attndAgentPending; window.attndAgentPending = null; b.click(); }
})();
