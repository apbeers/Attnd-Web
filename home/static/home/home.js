/* The home page and the console's first screen (Nov 21, 2026): the composer (typed examples, attach, speak), the
   chips (fill the composer, switch the phone's look), "See it live" (kinds, Grid | Map, Happening near <place> (an
   approximate position from the visitor's IP, no prompt) and anonymous counts of the page's own taps. Works without it. */
(function () {
  const calm = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const METRIC_URL = '/home/m/';
  const count = (name, detail) => {
    try {
      const body = JSON.stringify({ name, detail: detail || '' });
      if (!(navigator.sendBeacon && navigator.sendBeacon(METRIC_URL, new Blob([body], { type: 'application/json' })))) {
        fetch(METRIC_URL, { method: 'POST', body, keepalive: true, headers: { 'Content-Type': 'application/json' } });
      }
    } catch (e) { /* counts never get in the way */ }
  };
  document.addEventListener('click', (e) => {
    const el = e.target.closest('[data-metric]');
    if (el && el.dataset.metric !== 'submit') count(el.dataset.metric, el.dataset.detail);
  });

  // The visitor's time zone for the import (the event's own zone wins when the AI finds one).
  let tz = '';
  try { tz = Intl.DateTimeFormat().resolvedOptions().timeZone || ''; } catch (e) { /* none */ }
  document.querySelectorAll('[data-tz]').forEach((i) => { i.value = tz; });

  // Composer: typed placeholder examples (not with reduced motion), the attached file's name, speaking.
  document.querySelectorAll('[data-composer]').forEach((form) => {
    const text = form.querySelector('[data-composer-text]');
    const file = form.querySelector('[data-composer-file]');
    const name = form.querySelector('[data-composer-file-name]');
    const examples = (text.dataset.placeholders || '').split('|').filter(Boolean);
    if (!calm && examples.length > 1) {
      let i = 0, shown = 0, timer = null;
      const tick = () => {
        if (document.activeElement === text || text.value) { timer = setTimeout(tick, 1200); return; }
        const target = examples[i];
        shown += 1;
        text.setAttribute('placeholder', target.slice(0, shown));
        if (shown >= target.length) { i = (i + 1) % examples.length; shown = 0; timer = setTimeout(tick, 2200); }
        else timer = setTimeout(tick, 45);
      };
      timer = setTimeout(tick, 1500);
    }
    file?.addEventListener('change', () => { name.textContent = file.files[0] ? file.files[0].name : ''; });
    form.addEventListener('submit', (e) => {
      if (!text.value.trim() && !(file && file.files.length)) { e.preventDefault(); text.focus(); return; }
      count('submit');
      const go = form.querySelector('.composer__go');
      if (go) { go.disabled = true; go.textContent = 'Building…'; }
    });
    const Speech = window.SpeechRecognition || window.webkitSpeechRecognition;
    const mic = form.querySelector('[data-composer-mic]');
    if (Speech && mic) {
      mic.hidden = false;
      let rec = null;
      mic.addEventListener('click', () => {
        if (rec) { rec.stop(); return; }
        rec = new Speech();
        rec.lang = document.documentElement.lang || 'en-US';
        rec.interimResults = true;
        const before = text.value ? text.value.trim() + ' ' : '';
        rec.onresult = (ev) => { text.value = before + [...ev.results].map((r) => r[0].transcript).join(''); };
        rec.onend = () => { mic.classList.remove('is-listening'); rec = null; };
        mic.classList.add('is-listening');
        rec.start();
      });
    }
  });

  // Chips: fill the nearest composer (or open the file picker), and show that kind of event in the phone.
  const screens = document.querySelectorAll('[data-look-screen]');
  const showLook = (look) => screens.forEach((s) => s.classList.toggle('is-active', s.dataset.lookScreen === look));
  document.querySelectorAll('[data-chip]').forEach((chip) => {
    chip.addEventListener('mouseenter', () => showLook(chip.dataset.look));
    chip.addEventListener('focus', () => showLook(chip.dataset.look));
    if (chip.tagName !== 'BUTTON') return;
    chip.addEventListener('click', () => {
      showLook(chip.dataset.look);
      const form = chip.closest('section, header, .onb-hero')?.querySelector('[data-composer]') || document.querySelector('[data-composer]');
      if (!form) return;
      if (chip.hasAttribute('data-attach')) { form.querySelector('[data-composer-file]')?.click(); return; }
      const text = form.querySelector('[data-composer-text]');
      text.value = chip.dataset.prompt || '';
      text.focus();
      text.setSelectionRange(text.value.length, text.value.length);
    });
  });

  // Phones get "Open in Attnd": iPhone a Universal Link on www.attnd.app (the app, or the join page with the App Clip
  // card; never the attnd:// scheme, which made Safari show "the address is invalid" without the app); Android
  // Chrome's intent link. Everyone else keeps the join page (href).
  const ua = navigator.userAgent || '';
  const os = /Android/.test(ua) ? 'Android' : /iPhone|iPad|iPod/.test(ua) ? 'Ios' : '';
  // On the click, so cards added later (Happening near you) get it too.
  document.addEventListener('click', (e) => {
    const a = os && e.target.closest('[data-open-ios]');
    if (a && a.dataset['open' + os]) a.href = a.dataset['open' + os];
  });

  // QR codes counted once when they come into view (anonymous).
  if ('IntersectionObserver' in window) {
    const seen = new Set();
    const io = new IntersectionObserver((entries) => entries.forEach((en) => {
      const code = en.target.dataset.detail;
      if (en.isIntersecting && !seen.has(code)) { seen.add(code); count('qr_view', code); io.unobserve(en.target); }
    }), { threshold: 0.6 });
    document.querySelectorAll('[data-metric-view=qr_view]').forEach((img) => io.observe(img));
  }

  // Gallery: kind pills, Grid | Map, Happening near you.
  const gallery = document.querySelector('[data-gallery]');
  if (!gallery) return;
  const list = gallery.querySelector('[data-showcase-list]');
  const mapBox = gallery.querySelector('[data-gallery-map]');
  let kind = '';
  const press = (group, button) => group.querySelectorAll('.segmented__option').forEach((p) => {
    const on = p === button; p.classList.toggle('is-on', on); p.setAttribute('aria-pressed', String(on));
  });
  gallery.querySelectorAll('[data-kind-filter]').forEach((b) => b.addEventListener('click', () => {
    kind = b.dataset.kindFilter;
    press(b.parentElement, b);
    list?.querySelectorAll('.event-card').forEach((c) => { c.hidden = !!kind && !c.dataset.kind.startsWith(kind); });
    drawMap();
  }));
  // The Map view: OpenStreetMap tiles (Web Mercator, the zoom that fits every pin) under the pins, labels that
  // would collide hidden (the name is still the pin's accessible name and tooltip). P0 #6: the old view drew pins on
  // an empty grey box, which read as blank.
  const TILE = 256, W = 1000, H = 560;
  const merc = (lng, lat, z) => {
    const s = TILE * 2 ** z, r = Math.PI / 180;
    return [(lng + 180) / 360 * s, (1 - Math.log(Math.tan(lat * r) + 1 / Math.cos(lat * r)) / Math.PI) / 2 * s];
  };
  function drawMap() {
    if (!mapBox || mapBox.hidden) return;
    const svg = mapBox.querySelector('svg');
    svg.querySelectorAll('.gallery__map-pin, .gallery__map-tiles').forEach((p) => p.remove());
    const cards = [...gallery.querySelectorAll('.event-card')]
      .filter((c) => !c.hidden && c.dataset.lat && c.dataset.lng);
    const seen = new Set();
    const pts = cards.map((c) => [parseFloat(c.dataset.lng), parseFloat(c.dataset.lat), c])
      .filter(([lng, lat, c]) => Number.isFinite(lng) && Number.isFinite(lat) && !seen.has(c.dataset.code) && seen.add(c.dataset.code));
    if (!pts.length) return;
    let z = 12;
    for (; z > 1; z--) {  // the largest zoom where every pin fits with a margin
      const xy = pts.map(([lng, lat]) => merc(lng, lat, z));
      const w = Math.max(...xy.map((p) => p[0])) - Math.min(...xy.map((p) => p[0]));
      const h = Math.max(...xy.map((p) => p[1])) - Math.min(...xy.map((p) => p[1]));
      if (w <= W - 240 && h <= H - 120) break;
    }
    const xy = pts.map(([lng, lat]) => merc(lng, lat, z));
    const cx = (Math.max(...xy.map((p) => p[0])) + Math.min(...xy.map((p) => p[0]))) / 2;
    const cy = (Math.max(...xy.map((p) => p[1])) + Math.min(...xy.map((p) => p[1]))) / 2;
    const ox = cx - W / 2, oy = cy - H / 2;
    const ns = 'http://www.w3.org/2000/svg';
    const tiles = document.createElementNS(ns, 'g');
    tiles.setAttribute('class', 'gallery__map-tiles');
    const n = 2 ** z;
    for (let tx = Math.floor(ox / TILE); tx <= Math.floor((ox + W) / TILE); tx++) {
      for (let ty = Math.max(0, Math.floor(oy / TILE)); ty <= Math.min(n - 1, Math.floor((oy + H) / TILE)); ty++) {
        const img = document.createElementNS(ns, 'image');
        img.setAttribute('href', `https://tile.openstreetmap.org/${z}/${((tx % n) + n) % n}/${ty}.png`);
        img.setAttribute('x', tx * TILE - ox); img.setAttribute('y', ty * TILE - oy);
        img.setAttribute('width', TILE); img.setAttribute('height', TILE);
        img.addEventListener('error', () => img.remove());  // offline / blocked: the plain background stays
        tiles.appendChild(img);
      }
    }
    svg.querySelector('.gallery__map-bg').after(tiles);
    const dots = xy.map(([px, py]) => [px - ox - 12, py - oy - 12, px - ox + 12, py - oy + 12]);
    const boxes = [];
    const hits = (box, own) => [...boxes, ...dots.filter((d, j) => j !== own)]
      .some((b) => b[0] < box[2] && box[0] < b[2] && b[1] < box[3] && box[1] < b[3]);
    pts.forEach(([, , card], i) => {
      const x = xy[i][0] - ox, y = xy[i][1] - oy;
      const g = document.createElementNS(ns, 'g');
      g.setAttribute('class', 'gallery__map-pin');
      g.setAttribute('tabindex', '0');
      g.setAttribute('role', 'button');
      const name = card.querySelector('.event-card__name').textContent.trim();
      g.setAttribute('aria-label', name);
      const title = document.createElementNS(ns, 'title');
      title.textContent = name;
      const dot = document.createElementNS(ns, 'circle');
      dot.setAttribute('cx', x); dot.setAttribute('cy', y); dot.setAttribute('r', 11);
      dot.setAttribute('fill', getComputedStyle(card).getPropertyValue('--c1').trim() || '#431f86');
      g.append(title, dot);
      svg.appendChild(g);
      const label = document.createElementNS(ns, 'text');
      label.setAttribute('x', x + 16); label.setAttribute('y', y + 5); label.textContent = name;
      g.append(label);
      const box = [x + 14, y - 11, x + 18 + label.getComputedTextLength(), y + 9];  // the label's real extent
      if (box[2] > W || hits(box, i)) label.remove(); else boxes.push(box);
      const open = () => { setView('grid'); card.scrollIntoView({ behavior: calm ? 'auto' : 'smooth', block: 'center' }); card.querySelector('a, img')?.focus?.(); };
      g.addEventListener('click', open);
      g.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } });
    });
  }
  function setView(view) {
    const button = gallery.querySelector(`[data-view="${view}"]`);
    if (button) press(button.parentElement, button);
    if (list) list.hidden = view === 'map';
    gallery.querySelector('[data-near-list]')?.toggleAttribute('hidden', view === 'map');
    if (mapBox) mapBox.hidden = view !== 'map';
    drawMap();
  }
  gallery.querySelectorAll('[data-view]').forEach((b) => b.addEventListener('click', () => setView(b.dataset.view)));

  // Happening near <place>: an approximate position from the visitor's IP, asked by the BROWSER from Google's
  // Geolocation API (so Google sees the visitor's IP, not our server's; no permission prompt, no precise location),
  // at most once a day per browser (localStorage, rounded to 0.1°) and only when the page carries a key.
  // Anything failing keeps the page's own "Happening near <region>" cards.
  const near = gallery.querySelector('[data-near]');
  const key = gallery.dataset.geoKey;
  async function position() {
    try {
      const saved = JSON.parse(localStorage.getItem('attnd.home.geo') || 'null');
      if (saved && Date.now() - saved.at < 864e5) {
        if (saved.none) throw new Error('geolocate');  // Google couldn't place this visitor today: don't ask again
        if (typeof saved.lat === 'number') return saved;
      }
    } catch (e) { if (e.message === 'geolocate') throw e; /* no storage */ }
    const resp = await fetch(`https://www.googleapis.com/geolocation/v1/geolocate?key=${encodeURIComponent(key)}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ considerIp: true }),
      credentials: 'omit', referrerPolicy: 'strict-origin',
    });
    if (!resp.ok) {
      // 404 = Google can't place this address (Chrome logs the failed request anyway; there's no quiet way to ask).
      // Remembered for a day like a found position, so the visitor's later visits don't ask (or log it) again.
      if (resp.status === 404) {
        try { localStorage.setItem('attnd.home.geo', JSON.stringify({ none: true, at: Date.now() })); } catch (e) { /* no storage */ }
      }
      throw new Error('geolocate');
    }
    const d = await resp.json();
    const pos = { lat: Math.round(d.location.lat * 10) / 10, lng: Math.round(d.location.lng * 10) / 10, at: Date.now() };
    try { localStorage.setItem('attnd.home.geo', JSON.stringify(pos)); } catch (e) { /* no storage */ }
    return pos;
  }
  if (near && key && window.fetch) {
    position().then(async (pos) => {
      const url = `${gallery.dataset.nearUrl}?lat=${pos.lat.toFixed(1)}&lng=${pos.lng.toFixed(1)}`;
      const d = await (await fetch(url, { headers: { Accept: 'application/json' } })).json();
      if (!d.count) return;  // nothing near: keep the region's cards
      near.querySelector('[data-near-list]').innerHTML = d.html;
      near.querySelector('[data-near-title]').textContent = d.place ? `Happening near ${d.place}` : 'Happening near you';
      near.hidden = false;
    }).catch(() => { /* keep what the page had */ });
  }
})();
