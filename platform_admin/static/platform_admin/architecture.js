/* Platform → Architecture: pan / zoom, search, layer toggles, node side panel, flows step by step, SVG / PNG export.
   No libraries. Reduced motion: flows show as a numbered list without the step animation. */
(function () {
  'use strict';
  const svg = document.querySelector('[data-arch-svg]');
  if (!svg) return;
  const panelEl = document.querySelector('[data-arch-panel]');
  const nodes = JSON.parse(document.getElementById('arch-panel-data').textContent);
  const flows = JSON.parse(document.getElementById('arch-flows-data').textContent);
  const calm = window.matchMedia('(prefers-reduced-motion: reduce)');
  const canvas = document.querySelector('[data-arch-canvas]');
  const panelBody = document.querySelector('[data-arch-panel-body]');
  // The whole diagram (its natural size). The view always has the canvas's aspect, so 1 unit = 1 px at "1:1" and
  // the labels stay readable; it opens at 1:1 from the top left (or fitted when the canvas is wider than the diagram).
  const full = { w: +svg.dataset.width, h: +svg.dataset.height };
  const MIN = 0.2, MAX = 3;
  let view = { x: 0, y: 0, w: full.w, h: full.h };
  const box = () => canvas.getBoundingClientRect();
  const scale = () => box().width / view.w;  // px per unit
  const apply = () => svg.setAttribute('viewBox', `${view.x} ${view.y} ${view.w} ${view.h}`);
  function setScale(k, cx, cy) {
    const r = box();
    if (!view.w) return;
    if (!r.width || !r.height) return;
    k = Math.min(MAX, Math.max(MIN, k));
    cx = cx ?? view.x + view.w / 2; cy = cy ?? view.y + view.h / 2;
    const fx = (cx - view.x) / view.w, fy = (cy - view.y) / view.h;
    view.w = r.width / k; view.h = r.height / k;
    view.x = cx - fx * view.w; view.y = cy - fy * view.h;
    apply();
  }
  function fit() {
    const r = box();
    if (!r.width || !r.height) return;
    const k = Math.min(r.width / full.w, r.height / full.h);
    view = { w: r.width / k, h: r.height / k, x: 0, y: 0 };
    view.x = (full.w - view.w) / 2; view.y = (full.h - view.h) / 2;
    apply();
  }
  function actual() {
    const r = box();
    if (!r.width || !r.height) return;
    view = { x: 0, y: 0, w: r.width, h: r.height };
    apply();
  }
  function start() {
    const r = box();
    if (!r.width || !r.height) return false;
    const k = Math.min(1.25, Math.max(0.8, r.width / full.w));
    view = { x: 0, y: 0, w: r.width / k, h: r.height / k };
    apply();
    return true;
  }
  let started = start();
  new ResizeObserver(() => { if (!started) started = start(); else setScale(scale() || 1); }).observe(canvas);
  document.querySelectorAll('[data-arch-zoom]').forEach((b) => b.addEventListener('click', () => {
    const k = +b.dataset.archZoom;
    if (!k) fit(); else if (b.dataset.archFit) actual(); else setScale(scale() * k);
  }));
  // Scroll pans (so the page never gets stuck); Ctrl / ⌘ + scroll and trackpad pinch (ctrlKey) zoom at the pointer.
  canvas.addEventListener('wheel', (e) => {
    e.preventDefault();
    const r = box(), k = scale();
    if (!k) return;
    if (e.ctrlKey || e.metaKey) {
      setScale(k * Math.exp(-e.deltaY * 0.0025), view.x + (e.clientX - r.left) / k, view.y + (e.clientY - r.top) / k);
    } else {
      const dx = e.shiftKey && !e.deltaX ? e.deltaY : e.deltaX, dy = e.shiftKey && !e.deltaX ? 0 : e.deltaY;
      view.x += dx / k; view.y += dy / k; apply();
    }
  }, { passive: false });
  let drag = null;
  svg.addEventListener('pointerdown', (e) => {
    if (e.button !== 0 || e.target.closest('.arch-node')) return;
    drag = { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y, k: scale() };
    svg.setPointerCapture(e.pointerId); canvas.classList.add('is-dragging');
  });
  svg.addEventListener('pointermove', (e) => {
    if (!drag) return;
    view.x = drag.vx - (e.clientX - drag.x) / drag.k;
    view.y = drag.vy - (e.clientY - drag.y) / drag.k;
    apply();
  });
  const end = () => { drag = null; canvas.classList.remove('is-dragging'); };
  svg.addEventListener('pointerup', end); svg.addEventListener('pointercancel', end);
  // Keyboard: arrows pan, + / − zoom, 0 fits (when the canvas or a box has focus).
  canvas.addEventListener('keydown', (e) => {
    const k = scale(), step = 80 / k;
    const moves = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
    if (moves[e.key] && !e.target.closest('input')) { e.preventDefault(); view.x += moves[e.key][0]; view.y += moves[e.key][1]; apply(); }
    else if (e.key === '+' || e.key === '=') setScale(k * 1.25);
    else if (e.key === '-') setScale(k * 0.8);
    else if (e.key === '0') fit();
  });
  // Bring a box into view (flows, the list, search) without changing the zoom.
  function reveal(id) {
    if (!box().width) return;  // phones: the list and the panel instead of the canvas
    const g = svg.querySelector(`.arch-node[data-node="${CSS.escape(id)}"] rect`);
    if (!g) return;
    const x = +g.getAttribute('x'), y = +g.getAttribute('y'), w = +g.getAttribute('width'), h = +g.getAttribute('height');
    const panelW = panelEl.hidden ? 0 : panelEl.getBoundingClientRect().width / scale();
    const visW = view.w - panelW;
    if (x < view.x || x + w > view.x + visW || y < view.y || y + h > view.y + view.h) {
      view.x = x + w / 2 - visW / 2; view.y = y + h / 2 - view.h / 2; apply();
    }
  }

  const nodeEls = [...svg.querySelectorAll('.arch-node')];
  const edgeEls = [...svg.querySelectorAll('.arch-edges path')];
  const el = (tag, text, cls) => { const x = document.createElement(tag); if (text) x.textContent = text; if (cls) x.className = cls; return x; };

  function open(id) {
    const n = nodes[id];
    if (!n) return;
    nodeEls.forEach((g) => g.classList.toggle('is-picked', g.dataset.node === id));
    edgeEls.forEach((p) => p.classList.toggle('is-on', p.dataset.from === id || p.dataset.to === id));
    panelEl.hidden = false;
    panelBody.replaceChildren();
    panelBody.append(el('h2', n.label, 'title-4'), el('p', n.description, 'small'));
    if (n.status) panelBody.append(el('p', `Status: ${n.status.state} · ${n.status.text}`, 'small strong'));
    if (n.docs) { const a = el('a', 'Docs ↗'); a.href = n.docs; a.target = '_blank'; a.rel = 'noopener'; panelBody.append(a); }
    const list = (title, items, fn) => {
      if (!items.length) return;
      panelBody.append(el('h3', title, 'eyebrow'));
      const ul = el('ul', '', 'small'); items.forEach((i) => ul.append(fn(i))); panelBody.append(ul);
    };
    list('Code', n.code, (c) => { const li = el('li'); if (c.url) { const a = el('a', c.path, 'mono'); a.href = c.url; a.target = '_blank'; a.rel = 'noopener'; li.append(a); } else li.append(el('span', c.path, 'mono')); return li; });
    list('Env vars', Object.entries(n.env), ([k, set]) => el('li', `${set ? '✓' : '✗ missing'} ${k}`, set ? 'mono' : 'mono tone-warning'));
    list('Hosts', n.hosts, (h) => el('li', h, 'mono'));
    list('Connects to', n.edges, (e) => el('li', `→ ${nodes[e.to] ? nodes[e.to].label : e.to}: ${e.label} (${e.protocol}${e.auth ? ', ' + e.auth : ''})`));
    reveal(id);
  }
  function close() {
    panelEl.hidden = true;
    nodeEls.forEach((g) => g.classList.remove('is-picked'));
    edgeEls.forEach((p) => p.classList.remove('is-on'));
  }
  document.querySelector('[data-arch-close]')?.addEventListener('click', close);
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !panelEl.hidden) close(); });
  nodeEls.forEach((g) => {
    g.addEventListener('click', () => open(g.dataset.node));
    g.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(g.dataset.node); } });
  });
  document.querySelectorAll('[data-arch-open]').forEach((b) => b.addEventListener('click', () => {
    open(b.dataset.archOpen);
    if (window.attndScreen?.is('phone')) panelEl.scrollIntoView({ block: 'nearest' });
  }));

  // Search + layer toggles
  const search = document.querySelector('[data-arch-search]');
  const layers = [...document.querySelectorAll('[data-arch-layer]')];
  function filter() {
    const q = (search?.value || '').trim().toLowerCase();
    const off = new Set(layers.filter((c) => !c.checked).map((c) => c.dataset.archLayer));
    const hidden = new Set();
    nodeEls.forEach((g) => {
      const tags = g.dataset.tags.split(' ').filter(Boolean);
      const hide = tags.length && tags.every((t) => off.has(t));
      g.classList.toggle('is-hidden', hide);
      if (hide) hidden.add(g.dataset.node);
      const n = nodes[g.dataset.node];
      const text = [n.label, n.description, ...Object.keys(n.env), ...n.hosts, ...n.code.map((c) => c.path)].join(' ').toLowerCase();
      g.classList.toggle('is-dim', !!q && !text.includes(q));
    });
    edgeEls.forEach((p) => p.classList.toggle('is-hidden', hidden.has(p.dataset.from) || hidden.has(p.dataset.to)));
    const first = q && nodeEls.find((g) => !g.classList.contains('is-dim') && !g.classList.contains('is-hidden'));
    if (first) reveal(first.dataset.node);
  }
  search?.addEventListener('input', filter);
  layers.forEach((c) => c.addEventListener('change', filter));

  // Flows
  const stepsEl = document.querySelector('[data-arch-steps]');
  const next = document.querySelector('[data-arch-next]');
  let flow = null, at = -1, timer = null;
  function show(i) {
    at = i;
    const [id] = flow.steps[i];
    nodeEls.forEach((g) => g.classList.toggle('is-step', g.dataset.node === id));
    [...stepsEl.children].forEach((li, j) => li.classList.toggle('is-current', j === i));
    const prev = i > 0 ? flow.steps[i - 1][0] : null;
    edgeEls.forEach((p) => p.classList.toggle('is-on', prev && ((p.dataset.from === prev && p.dataset.to === id) || (p.dataset.from === id && p.dataset.to === prev))));
    open(id);
  }
  document.querySelectorAll('[data-arch-flow]').forEach((b) => b.addEventListener('click', () => {
    flow = flows.find((f) => f.id === b.dataset.archFlow);
    clearInterval(timer);
    stepsEl.replaceChildren(...flow.steps.map(([id, text]) => el('li', `${nodes[id] ? nodes[id].label : id}: ${text}`)));
    next.hidden = false;
    show(0);
    if (!calm.matches) timer = setInterval(() => { if (at < flow.steps.length - 1) show(at + 1); else clearInterval(timer); }, 1800);
  }));
  next?.addEventListener('click', () => { clearInterval(timer); if (flow) show((at + 1) % flow.steps.length); });

  // Export
  function svgText() {
    const copy = svg.cloneNode(true);
    copy.setAttribute('viewBox', `0 0 ${full.w} ${full.h}`);
    copy.setAttribute('width', full.w); copy.setAttribute('height', full.h);
    const css = getComputedStyle(document.documentElement);
    const v = (n) => css.getPropertyValue(n).trim();
    const style = document.createElementNS('http://www.w3.org/2000/svg', 'style');
    style.textContent = `text{font:600 13px system-ui,sans-serif;fill:${v('--color-text')}} .arch-col{fill:${v('--color-muted')}} path{fill:none;stroke:${v('--color-border')}} rect{fill:${v('--color-surface-2')};stroke:${v('--color-border')}}`;
    copy.prepend(style);
    return new XMLSerializer().serializeToString(copy);
  }
  function download(blob, name) { const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 1000); }
  document.querySelectorAll('[data-arch-export]').forEach((b) => b.addEventListener('click', () => {
    const text = svgText();
    if (b.dataset.archExport === 'svg') { download(new Blob([text], { type: 'image/svg+xml' }), 'attnd-architecture.svg'); return; }
    const img = new Image();
    img.onload = () => {
      const c = document.createElement('canvas'); c.width = full.w * 2; c.height = full.h * 2;
      const ctx = c.getContext('2d'); ctx.fillStyle = getComputedStyle(document.body).backgroundColor; ctx.fillRect(0, 0, c.width, c.height);
      ctx.drawImage(img, 0, 0, c.width, c.height);
      c.toBlob((blob) => download(blob, 'attnd-architecture.png'));
    };
    img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(text);
  }));
})();
