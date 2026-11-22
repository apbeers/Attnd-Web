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
  const base = svg.viewBox.baseVal;
  const home = { x: base.x, y: base.y, w: base.width, h: base.height };
  let view = { ...home };
  const apply = () => svg.setAttribute('viewBox', `${view.x} ${view.y} ${view.w} ${view.h}`);
  const zoom = (k, cx, cy) => {
    if (!k) { view = { ...home }; apply(); return; }
    cx = cx ?? view.x + view.w / 2; cy = cy ?? view.y + view.h / 2;
    view = { x: cx - (cx - view.x) / k, y: cy - (cy - view.y) / k, w: view.w / k, h: view.h / k };
    apply();
  };
  document.querySelectorAll('[data-arch-zoom]').forEach((b) => b.addEventListener('click', () => zoom(+b.dataset.archZoom)));
  svg.addEventListener('wheel', (e) => {
    e.preventDefault();
    const r = svg.getBoundingClientRect();
    zoom(e.deltaY < 0 ? 1.15 : 0.87, view.x + (e.clientX - r.left) / r.width * view.w, view.y + (e.clientY - r.top) / r.height * view.h);
  }, { passive: false });
  let drag = null;
  svg.addEventListener('pointerdown', (e) => { if (e.target.closest('.arch-node')) return; drag = { x: e.clientX, y: e.clientY, v: { ...view } }; svg.setPointerCapture(e.pointerId); });
  svg.addEventListener('pointermove', (e) => {
    if (!drag) return;
    const r = svg.getBoundingClientRect();
    view.x = drag.v.x - (e.clientX - drag.x) / r.width * view.w;
    view.y = drag.v.y - (e.clientY - drag.y) / r.height * view.h;
    apply();
  });
  svg.addEventListener('pointerup', () => { drag = null; });

  const nodeEls = [...svg.querySelectorAll('.arch-node')];
  const edgeEls = [...svg.querySelectorAll('.arch-edges path')];
  const el = (tag, text, cls) => { const x = document.createElement(tag); if (text) x.textContent = text; if (cls) x.className = cls; return x; };

  function open(id) {
    const n = nodes[id];
    if (!n) return;
    nodeEls.forEach((g) => g.classList.toggle('is-picked', g.dataset.node === id));
    edgeEls.forEach((p) => p.classList.toggle('is-on', p.dataset.from === id || p.dataset.to === id));
    panelEl.replaceChildren();
    panelEl.append(el('h2', n.label, 'title-4'), el('p', n.description, 'small'));
    if (n.status) panelEl.append(el('p', `Status: ${n.status.state} · ${n.status.text}`, 'small strong'));
    if (n.docs) { const a = el('a', 'Docs ↗'); a.href = n.docs; a.target = '_blank'; a.rel = 'noopener'; panelEl.append(a); }
    const list = (title, items, fn) => {
      if (!items.length) return;
      panelEl.append(el('h3', title, 'eyebrow'));
      const ul = el('ul', '', 'small'); items.forEach((i) => ul.append(fn(i))); panelEl.append(ul);
    };
    list('Code', n.code, (c) => { const li = el('li'); if (c.url) { const a = el('a', c.path, 'mono'); a.href = c.url; a.target = '_blank'; a.rel = 'noopener'; li.append(a); } else li.append(el('span', c.path, 'mono')); return li; });
    list('Env vars', Object.entries(n.env), ([k, set]) => el('li', `${set ? '✓' : '✗ missing'} ${k}`, set ? 'mono' : 'mono tone-warning'));
    list('Hosts', n.hosts, (h) => el('li', h, 'mono'));
    list('Connects to', n.edges, (e) => el('li', `→ ${nodes[e.to] ? nodes[e.to].label : e.to}: ${e.label} (${e.protocol}${e.auth ? ', ' + e.auth : ''})`));
  }
  nodeEls.forEach((g) => {
    g.addEventListener('click', () => open(g.dataset.node));
    g.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(g.dataset.node); } });
  });
  document.querySelectorAll('[data-arch-open]').forEach((b) => b.addEventListener('click', () => open(b.dataset.archOpen)));

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
    copy.setAttribute('viewBox', `${home.x} ${home.y} ${home.w} ${home.h}`);
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
      const c = document.createElement('canvas'); c.width = home.w * 2; c.height = home.h * 2;
      const ctx = c.getContext('2d'); ctx.fillStyle = getComputedStyle(document.body).backgroundColor; ctx.fillRect(0, 0, c.width, c.height);
      ctx.drawImage(img, 0, 0, c.width, c.height);
      c.toBlob((blob) => download(blob, 'attnd-architecture.png'));
    };
    img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(text);
  }));
})();
