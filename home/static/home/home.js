/* The home page and the console's first screen (Nov 21, 2026): the composer (typed examples, attach, speak), the
   chips (fill the composer, switch the phone's look), "See it live" (kinds, Grid | Map, Happening near you with the
   browser's location only after a tap) and anonymous counts of the page's own taps. Everything works without it. */
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

  // Phones get "Open in Attnd" (iPhone: the app's own scheme, then the join page; Android: Chrome's intent link).
  const ua = navigator.userAgent || '';
  document.addEventListener('click', (e) => {
    const a = e.target.closest('[data-open-ios]');
    if (!a) return;
    if (/iPhone|iPad|iPod/.test(ua)) {
      e.preventDefault();
      const fallback = a.getAttribute('href');
      let left = false;
      const away = () => { if (document.visibilityState === 'hidden') left = true; };
      document.addEventListener('visibilitychange', away);
      window.location.href = a.dataset.openIos;
      setTimeout(() => { document.removeEventListener('visibilitychange', away); if (!left) window.location.href = fallback; }, 1500);
    } else if (/Android/.test(ua)) {
      e.preventDefault();
      window.location.href = a.dataset.openAndroid;
    }
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
  const press = (group, button) => group.querySelectorAll('.pill').forEach((p) => {
    const on = p === button; p.classList.toggle('is-on', on); p.setAttribute('aria-pressed', String(on));
  });
  gallery.querySelectorAll('[data-kind-filter]').forEach((b) => b.addEventListener('click', () => {
    kind = b.dataset.kindFilter;
    press(b.parentElement, b);
    list?.querySelectorAll('.event-card').forEach((c) => { c.hidden = !!kind && !c.dataset.kind.startsWith(kind); });
    drawMap();
  }));
  function drawMap() {
    if (!mapBox || mapBox.hidden) return;
    const svg = mapBox.querySelector('svg');
    svg.querySelectorAll('.gallery__map-pin').forEach((p) => p.remove());
    const cards = [...(list?.querySelectorAll('.event-card') || [])].filter((c) => !c.hidden && c.dataset.lat);
    if (!cards.length) return;
    const pts = cards.map((c) => [parseFloat(c.dataset.lng), parseFloat(c.dataset.lat), c]);
    const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
    const [x0, x1, y0, y1] = [Math.min(...xs) - 2, Math.max(...xs) + 2, Math.min(...ys) - 2, Math.max(...ys) + 2];
    const ns = 'http://www.w3.org/2000/svg';
    pts.forEach(([lng, lat, card]) => {
      const g = document.createElementNS(ns, 'g');
      g.setAttribute('class', 'gallery__map-pin');
      g.setAttribute('tabindex', '0');
      g.setAttribute('role', 'button');
      const name = card.querySelector('.event-card__name').textContent;
      g.setAttribute('aria-label', name);
      const x = 60 + (lng - x0) / (x1 - x0 || 1) * 880, y = 520 - (lat - y0) / (y1 - y0 || 1) * 480;
      const dot = document.createElementNS(ns, 'circle');
      dot.setAttribute('cx', x); dot.setAttribute('cy', y); dot.setAttribute('r', 11);
      dot.setAttribute('fill', getComputedStyle(card).getPropertyValue('--c1') || '#431f86');
      const label = document.createElementNS(ns, 'text');
      label.setAttribute('x', x + 16); label.setAttribute('y', y + 5); label.textContent = name;
      g.append(dot, label);
      const open = () => { setView('grid'); card.scrollIntoView({ behavior: calm ? 'auto' : 'smooth', block: 'center' }); card.querySelector('a, img')?.focus?.(); };
      g.addEventListener('click', open);
      g.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } });
      svg.appendChild(g);
    });
  }
  function setView(view) {
    const button = gallery.querySelector(`[data-view="${view}"]`);
    if (button) press(button.parentElement, button);
    if (list) list.hidden = view === 'map';
    if (mapBox) mapBox.hidden = view !== 'map';
    drawMap();
  }
  gallery.querySelectorAll('[data-view]').forEach((b) => b.addEventListener('click', () => setView(b.dataset.view)));

  // Happening near you: the browser's location only after the visitor taps "Use my location".
  const near = gallery.querySelector('[data-near]');
  const locate = gallery.querySelector('[data-near-locate]');
  if (near && locate && navigator.geolocation) {
    locate.hidden = false;
    near.hidden = false;
    locate.addEventListener('click', () => {
      locate.disabled = true;
      locate.textContent = 'Finding events near you…';
      navigator.geolocation.getCurrentPosition(async (pos) => {
        try {
          const url = `${gallery.dataset.nearUrl}?lat=${pos.coords.latitude.toFixed(2)}&lng=${pos.coords.longitude.toFixed(2)}`;
          const d = await (await fetch(url, { headers: { Accept: 'application/json' } })).json();
          near.querySelector('[data-near-list]').innerHTML = d.html || '';
          near.querySelector('[data-near-where]').textContent = d.count ? 'Near you.' : 'Nothing near you yet. Try one of these:';
        } catch (e) { /* keep what the page had */ }
        locate.hidden = true;
      }, () => { locate.disabled = false; locate.textContent = 'Use my location'; }, { maximumAge: 600000, timeout: 10000 });
    });
  }
})();
