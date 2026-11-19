/* Diagnostics session page (staff): lanes timeline, filters, entry list and network waterfall.
   Vanilla JS, no libraries. Every string comes from an app, so it is only ever set with textContent. */
(function () {
  'use strict';
  const root = document.getElementById('diag-session');
  if (!root) return;

  const LEVEL_COLORS = ['#adb5bd', '#868e96', '#0b8bd6', '#431f86', '#f2b705', '#c62828', '#7a0b0b'];
  const LEVEL_BADGES = ['badge--muted', 'badge--muted', 'badge--info', 'badge--primary', 'badge--warning', 'badge--danger', 'badge--danger'];
  const AROUND_MS = 30000;
  const PAGE = 1500;

  const filtersEl = root.querySelector('[data-diag-filters]');
  const statusEl = root.querySelector('[data-diag-status]');
  const rowsEl = root.querySelector('[data-diag-rows]');
  const moreEl = root.querySelector('[data-diag-more]');
  const catsEl = root.querySelector('[data-diag-cats]');
  const canvas = root.querySelector('[data-diag-timeline] canvas');
  const waterfallEl = root.querySelector('[data-diag-waterfall]');
  const input = name => filtersEl.querySelector(`[data-filter="${name}"]`);

  let all = [], levels = [], categories = [], shown = [], limit = PAGE, selected = null, dots = [];
  const byReq = new Map();

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }
  const pad = (n, w = 2) => String(n).padStart(w, '0');
  function clock(ms) {
    const d = new Date(ms);
    return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}.${pad(d.getMilliseconds(), 3)}`;
  }
  const num = v => (typeof v === 'number' && isFinite(v) ? v : null);

  // ---- filters ---------------------------------------------------------------------------------------
  function activeCats() {
    return new Set([...catsEl.querySelectorAll('input:checked')].map(i => i.value));
  }
  function matches(e, f) {
    if (e.l < f.level || !f.cats.has(e.c)) return false;
    if (f.event && !e.e.startsWith(f.event)) return false;
    if (f.state && e.s !== f.state) return false;
    if (f.status && !(num(e.f.status) >= 400)) return false;
    if (f.duration && !(num(e.f.ms) > f.duration)) return false;
    if (f.around && selected && Math.abs(e.k - selected.k) > AROUND_MS) return false;
    if (f.text) {
      const hay = (e.m + ' ' + e.e + ' ' + JSON.stringify(e.f)).toLowerCase();
      if (!hay.includes(f.text)) return false;
    }
    return true;
  }
  function apply() {
    const f = {
      level: Number(input('level').value) || 0, cats: activeCats(), event: input('event').value.trim(),
      text: input('text').value.trim().toLowerCase(), duration: Number(input('duration').value) || 0,
      state: input('state').value, status: input('status').checked, around: input('around').checked,
    };
    shown = all.filter(e => matches(e, f));
    limit = PAGE;
    statusEl.textContent = `${shown.length} of ${all.length} entries shown` +
      (root.dataset.total && Number(root.dataset.total) > all.length ? ` (newest ${all.length} of ${root.dataset.total} loaded)` : '');
    drawTimeline();
    renderRows();
    renderWaterfall();
  }

  // ---- timeline (canvas: one lane per category) ------------------------------------------------------
  function drawTimeline() {
    const lanes = categories.filter(c => all.some(e => e.c === c));
    const laneH = 18, left = 90, top = 6, axis = 18;
    const width = canvas.parentElement.clientWidth || 800;
    const height = top + lanes.length * laneH + axis;
    const ratio = window.devicePixelRatio || 1;
    canvas.width = width * ratio; canvas.height = height * ratio; canvas.style.height = height + 'px';
    const ctx = canvas.getContext('2d');
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, width, height);
    dots = [];
    if (!all.length) return;
    const t0 = all[0].k, t1 = Math.max(all[all.length - 1].k, t0 + 1000);
    const x = k => left + (k - t0) / (t1 - t0) * (width - left - 10);
    ctx.font = '11px ui-monospace, Menlo, monospace';
    lanes.forEach((cat, i) => {
      const y = top + i * laneH;
      ctx.fillStyle = i % 2 ? '#f8f9fa' : '#fff';
      ctx.fillRect(left, y, width - left, laneH);
      ctx.fillStyle = '#6c757d';
      ctx.fillText(cat, 4, y + 13);
    });
    if (selected && input('around').checked) {
      ctx.fillStyle = 'rgba(67, 31, 134, .08)';
      ctx.fillRect(x(selected.k - AROUND_MS), top, x(selected.k + AROUND_MS) - x(selected.k - AROUND_MS), lanes.length * laneH);
    }
    const laneOf = new Map(lanes.map((c, i) => [c, i]));
    for (const e of shown) {
      const lane = laneOf.get(e.c);
      if (lane === undefined) continue;
      const cx = x(e.k), cy = top + lane * laneH + laneH / 2, r = e.l >= 5 ? 4 : 3;
      ctx.fillStyle = LEVEL_COLORS[e.l] || '#868e96';
      ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2); ctx.fill();
      dots.push([cx, cy, e]);
    }
    if (selected) {
      ctx.strokeStyle = '#431f86'; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(x(selected.k), top); ctx.lineTo(x(selected.k), top + lanes.length * laneH); ctx.stroke();
    }
    ctx.fillStyle = '#6c757d';
    const yAxis = top + lanes.length * laneH + 13;
    ctx.fillText(clock(t0), left, yAxis);
    const end = clock(t1);
    ctx.fillText(end, width - 10 - ctx.measureText(end).width, yAxis);
  }
  canvas.addEventListener('click', ev => {
    const rect = canvas.getBoundingClientRect();
    const px = ev.clientX - rect.left, py = ev.clientY - rect.top;
    let best = null, bestD = 64;
    for (const [cx, cy, e] of dots) {
      const d = (cx - px) ** 2 + (cy - py) ** 2;
      if (d < bestD) { bestD = d; best = e; }
    }
    if (best) select(best, true);
  });
  window.addEventListener('resize', () => drawTimeline());

  // ---- entry list ------------------------------------------------------------------------------------
  function row(e) {
    const tr = el('tr');
    tr.dataset.id = e.id;
    if (e.src !== 'app') tr.classList.add('diag-row--server');
    if (selected && e === selected) tr.classList.add('diag-row--selected');
    else if (selected && selected.r && e.r === selected.r) tr.classList.add('diag-row--linked');
    tr.append(el('td', 'mono small nowrap', clock(e.t)));
    const lv = el('td'); lv.append(el('span', 'badge ' + (LEVEL_BADGES[e.l] || 'badge--muted'), levels[e.l] || e.l)); tr.append(lv);
    tr.append(el('td', 'small', e.c + (e.s ? ' · ' + e.s : '')));
    tr.append(el('td', 'mono small', e.e));
    tr.append(el('td', 'small diag-msg', e.m));
    tr.addEventListener('click', () => select(e, false));
    return tr;
  }
  function detail(e) {
    const tr = el('tr', 'diag-detail');
    const td = el('td'); td.colSpan = 5;
    const meta = [`id ${e.id}`, e.src !== 'app' ? 'source ' + e.src : null, e.r ? 'req_id ' + e.r : null,
      e.n ? 'net ' + e.n : null, e.ev ? 'event ' + e.ev : null, e.seq !== null && e.seq !== undefined ? 'seq ' + e.seq : null,
      new Date(e.t).toISOString()].filter(Boolean).join(' · ');
    td.append(el('div', 'small muted', meta));
    td.append(el('pre', '', JSON.stringify(e.f, null, 2)));
    const actions = el('div', 'cluster cluster--tight');
    const around = el('button', 'button button--outline button--small', '±30 s around this');
    around.type = 'button';
    around.addEventListener('click', ev => { ev.stopPropagation(); input('around').checked = true; apply(); scrollTo(e); });
    actions.append(around);
    if (e.r) {
      const same = el('button', 'button button--ghost button--small', 'Only this request');
      same.type = 'button';
      same.addEventListener('click', ev => { ev.stopPropagation(); input('text').value = e.r; apply(); });
      actions.append(same);
    }
    td.append(actions);
    tr.append(td);
    return tr;
  }
  function renderRows() {
    rowsEl.replaceChildren();
    const frag = document.createDocumentFragment();
    for (const e of shown.slice(0, limit)) {
      frag.append(row(e));
      if (selected === e) frag.append(detail(e));
    }
    rowsEl.append(frag);
    moreEl.hidden = shown.length <= limit;
    moreEl.textContent = `Show more (${shown.length - limit} left)`;
  }
  moreEl.addEventListener('click', () => { limit += PAGE; renderRows(); });

  function scrollTo(e) {
    const tr = rowsEl.querySelector(`tr[data-id="${e.id}"]`);
    if (tr) tr.scrollIntoView({block: 'center', behavior: 'smooth'});
  }
  function select(e, scroll) {
    selected = selected === e && !scroll ? null : e;
    input('around').disabled = !selected;
    if (!selected) input('around').checked = false;
    const index = shown.indexOf(e);
    if (index >= limit) limit = index + 1;
    if (input('around').checked) apply(); else { drawTimeline(); renderRows(); renderWaterfall(); }
    if (scroll && selected) scrollTo(e);
  }

  // ---- network waterfall -----------------------------------------------------------------------------
  function renderWaterfall() {
    waterfallEl.replaceChildren();
    const reqs = shown.filter(e => e.src === 'app' && (e.e === 'net.request.finished' || e.e === 'net.request.failed'))
      .slice(-400).map(e => ({e, end: e.t, start: e.t - (num(e.f.ms) || 0)}));
    if (!reqs.length) { waterfallEl.append(el('p', 'muted small', 'No network requests in the current filter.')); return; }
    const t0 = Math.min(...reqs.map(r => r.start)), t1 = Math.max(...reqs.map(r => r.end), t0 + 1);
    const pct = v => (v - t0) / (t1 - t0) * 100;
    for (const r of reqs) {
      const status = num(r.e.f.status);
      const line = el('div', 'waterfall__row');
      line.title = r.e.m;
      line.append(el('div', 'waterfall__label',
        `${r.e.f.method || ''} ${r.e.f.path || r.e.e} ${status || (r.e.e === 'net.request.failed' ? 'failed' : '')} · ${num(r.e.f.ms) ?? '?'} ms`));
      const track = el('div', 'waterfall__track');
      const bar = el('div', 'waterfall__bar' + (r.e.e === 'net.request.failed' || status >= 500 ? ' waterfall__bar--error'
        : status >= 400 ? ' waterfall__bar--client' : status >= 300 ? ' waterfall__bar--redirect' : ''));
      bar.style.left = pct(r.start) + '%';
      bar.style.width = Math.max(0.2, pct(r.end) - pct(r.start)) + '%';
      track.append(bar);
      const server = (byReq.get(r.e.f.req_id) || []).find(s => s.src !== 'app');
      if (server && num(server.f.ms) !== null) {
        const mid = (r.start + r.end) / 2, half = server.f.ms / 2;
        const inner = el('div', 'waterfall__server');
        inner.style.left = pct(Math.max(r.start, mid - half)) + '%';
        inner.style.width = Math.max(0.1, pct(Math.min(r.end, mid + half)) - pct(Math.max(r.start, mid - half))) + '%';
        inner.title = `server ${server.f.ms} ms`;
        track.append(inner);
      }
      line.append(track);
      line.addEventListener('click', () => {
        document.querySelector('[data-tab-target="#diag-list"]')?.click();
        select(r.e, true);
      });
      waterfallEl.append(line);
    }
  }

  // ---- shortcuts from the summary panels -------------------------------------------------------------
  document.addEventListener('click', ev => {
    const filterBtn = ev.target.closest('[data-diag-filter-event]');
    if (filterBtn) { input('event').value = filterBtn.dataset.diagFilterEvent; apply(); scrollIntoList(); }
    const reqBtn = ev.target.closest('[data-diag-select-req]');
    if (reqBtn && reqBtn.dataset.diagSelectReq) {
      const e = all.find(x => x.r === reqBtn.dataset.diagSelectReq && x.src === 'app');
      if (e) { resetFilters(); select(e, true); }
    }
  });
  function scrollIntoList() { rowsEl.closest('.panel').scrollIntoView({behavior: 'smooth'}); }
  function resetFilters() {
    input('level').value = '0'; ['event', 'text', 'duration'].forEach(n => { input(n).value = ''; });
    input('state').value = ''; input('status').checked = false; input('around').checked = false;
    catsEl.querySelectorAll('input').forEach(i => { i.checked = true; });
    apply();
  }

  let timer = null;
  filtersEl.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(apply, 150); });
  filtersEl.addEventListener('change', apply);

  // ---- load --------------------------------------------------------------------------------------------
  fetch(root.dataset.entriesUrl, {credentials: 'same-origin', headers: {Accept: 'application/json'}})
    .then(r => { if (!r.ok) throw new Error(r.status); return r.json(); })
    .then(data => {
      all = data.entries; levels = data.levels; categories = data.categories;
      root.dataset.total = data.total;
      for (const e of all) {
        if (!e.r) continue;
        if (!byReq.has(e.r)) byReq.set(e.r, []);
        byReq.get(e.r).push(e);
      }
      const counts = new Map();
      all.forEach(e => counts.set(e.c, (counts.get(e.c) || 0) + 1));
      for (const cat of categories) {
        if (!counts.has(cat)) continue;
        const label = el('label');
        const box = el('input'); box.type = 'checkbox'; box.value = cat; box.checked = true;
        label.append(box, document.createTextNode(`${cat} (${counts.get(cat)})`));
        catsEl.append(label);
      }
      apply();
    })
    .catch(err => { statusEl.textContent = 'Could not load the entries (' + err.message + ').'; });
})();
