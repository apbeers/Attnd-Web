/* Attnd console — small vanilla helpers (no third-party JS). */
(function () {
  // Dismissible notices: <button data-dismiss> removes the .notice it sits in.
  document.addEventListener('click', e => {
    const button = e.target.closest('[data-dismiss]');
    if (button) button.closest('.notice')?.remove();
  });

  // No inline event handlers (the Content Security Policy blocks them); these data attributes replace them.
  // <button data-confirm="Delete this?">: asks first, and does nothing unless confirmed.
  document.addEventListener('click', e => {
    const el = e.target.closest('[data-confirm]');
    if (el && !window.confirm(el.dataset.confirm)) { e.preventDefault(); e.stopImmediatePropagation(); }
  }, true);
  // <select data-autosubmit>: submits its form when changed.
  document.addEventListener('change', e => {
    const el = e.target.closest('[data-autosubmit]');
    if (el && el.form) el.form.submit();
  });
  // <button data-copy="text">: copies the text, then says so.
  document.addEventListener('click', e => {
    const el = e.target.closest('[data-copy]');
    if (!el) return;
    navigator.clipboard.writeText(el.dataset.copy).then(() => { el.textContent = 'Copied'; }).catch(() => {});
  });

  // <div data-show-if="when:later"> is shown only while its form's `when` is "later" (radio, select or text);
  // data-show-if="urgent" only while the checkbox `urgent` is ticked. Also for forms added later (the Ask Attnd
  // chat calls window.attndShowIf(element)).
  function showIf(scope) {
    (scope || document).querySelectorAll('[data-show-if]').forEach(el => {
      const form = el.closest('form');
      if (!form) return;
      const [name, value] = el.dataset.showIf.split(':');
      const inputs = [...form.elements].filter(i => i.name === name);
      el.hidden = !inputs.some(i => (i.type === 'radio' || i.type === 'checkbox')
        ? i.checked && (value === undefined || i.value === value) : i.value === value);
    });
  }
  window.attndShowIf = showIf;
  document.addEventListener('change', e => { if (e.target.form) showIf(e.target.form); });
  showIf();

  // "Open in Attnd" on the QR card (<a data-open-app>): phones only, since people can't scan their own screen.
  // iPhone: the app's own scheme (attnd://join/<code>; Safari won't open a Universal Link to the domain it shows),
  // and if the page is still visible ~1.5 s later the app isn't installed: the join page (App Store / App Clip).
  // Android: Chrome's intent:// URL with the join link as its fallback. Desktop: hidden.
  const ua = navigator.userAgent || '';
  const phoneOs = /Android/i.test(ua) ? 'android'
    : (/iPhone|iPod|iPad/i.test(ua) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1)) ? 'ios' : '';
  function showOpenApp(scope) {
    if (!phoneOs) return;
    (scope || document).querySelectorAll('[data-open-app]').forEach((a) => {
      a.href = a.dataset[phoneOs] || a.dataset.fallback;
      a.hidden = false;
    });
  }
  window.attndOpenApp = showOpenApp;
  showOpenApp();
  document.addEventListener('click', (e) => {
    const a = e.target.closest('[data-open-app]');
    if (!a || phoneOs !== 'ios') return;
    e.preventDefault();
    let left = false;
    const away = () => { if (document.visibilityState === 'hidden') left = true; };
    document.addEventListener('visibilitychange', away);
    window.addEventListener('pagehide', away, { once: true });
    window.location.href = a.dataset.ios;
    setTimeout(() => {
      document.removeEventListener('visibilitychange', away);
      if (!left && document.visibilityState === 'visible') window.location.href = a.dataset.fallback;
    }, 1500);
  });

  // API-023: a changed colour theme applies at once (owner, Nov 21, 2026): after a save, a switch, Reset or a finished
  // Generate with AI, every <style data-event-theme="<id>"> on the page gets the new tokens and the compact card its
  // swatches, without a reload. Nothing changes visually while "Use custom theme on the website" is off.
  window.attndRefreshTheme = async function () {
    const style = document.querySelector('style[data-event-theme]');
    if (!style) return;
    const id = style.dataset.eventTheme;
    try {
      const r = await fetch(`/console/events/${id}/theme/css/`, { headers: { Accept: 'application/json' }, credentials: 'same-origin' });
      if (!r.ok) return;
      const d = await r.json();
      document.querySelectorAll(`style[data-event-theme="${id}"]`).forEach((s) => { if (s.textContent !== d.css) s.textContent = d.css; });
      document.querySelectorAll('.theme-swatches').forEach((list) => {
        list.querySelectorAll('.theme-swatch').forEach((li, i) => { if (d.swatches[i]) li.style.setProperty('--swatch', d.swatches[i]); });
      });
    } catch (e) { /* offline: the next page load has it */ }
  };

  // The Website tab's full screen preview: Desktop | Mobile. In the chat, Publish inside it closes the dialog first,
  // so the chat's confirm card is in view.
  document.addEventListener('click', (e) => {
    const dialogAction = e.target.closest && e.target.closest('[data-site-full] form button, [data-site-full] [data-act]');
    if (dialogAction && dialogAction.closest('.agent__card')) setTimeout(() => dialogAction.closest('dialog')?.close(), 0);
  }, true);
  document.addEventListener('click', (e) => {
    const b = e.target.closest && e.target.closest('[data-site-size]');
    if (!b) return;
    const dialog = b.closest('[data-site-full]');
    dialog.querySelectorAll('[data-site-size]').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
    dialog.querySelector('[data-site-stage]').dataset.siteStage = b.dataset.siteSize;
  });

  // Quick text edits in the full screen preview (batch 7 §6, no AI): "Edit text" asks the preview's bridge to make
  // text editable; each changed text comes back by postMessage and is saved through studio:text_edit as a new draft
  // version (Undo in the status line). Only messages from this dialog's own iframe are accepted.
  document.addEventListener('click', (e) => {
    const b = e.target.closest && e.target.closest('[data-site-edit-text]');
    if (!b) return;
    const dialog = b.closest('[data-site-full]');
    const frame = dialog && dialog.querySelector('iframe');
    if (!frame) return;
    const on = b.getAttribute('aria-pressed') !== 'true';
    b.setAttribute('aria-pressed', String(on));
    try { frame.contentWindow.postMessage({ type: 'attnd-studio-edit', on }, '*'); } catch (err) { /* not loaded */ }
    let status = dialog.querySelector('[data-site-edit-status]');
    if (!status) {
      status = document.createElement('span');
      status.className = 'small';
      status.setAttribute('data-site-edit-status', '');
      status.setAttribute('aria-live', 'polite');
      b.after(status);
    }
    status.textContent = on ? 'Click any text, type, then click outside it.' : '';
  });
  window.addEventListener('message', async (e) => {
    const data = e.data;
    if (!data || data.type !== 'attnd-studio-text') return;
    const dialog = Array.from(document.querySelectorAll('[data-site-full]')).find((d) => {
      const f = d.querySelector('iframe');
      return f && f.contentWindow === e.source;
    });
    if (!dialog) return;
    const button = dialog.querySelector('[data-site-edit-text]');
    const status = dialog.querySelector('[data-site-edit-status]');
    const csrf = document.querySelector('[name=csrfmiddlewaretoken]');
    const body = new FormData();
    body.set('page', data.page || 'index.html');
    body.set('old', data.old || '');
    body.set('new', data.new || '');
    if (status) status.textContent = 'Saving…';
    try {
      const r = await fetch(button.dataset.textUrl, { method: 'POST', body, credentials: 'same-origin',
        headers: { Accept: 'application/json', 'X-CSRFToken': csrf ? csrf.value : '' } });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || 'Not saved');
      if (status) {
        status.textContent = d.saved ? `✓ Saved as version ${d.number} · ` : '';
        if (d.saved && d.undo) {
          const undo = document.createElement('button');
          undo.type = 'button';
          undo.className = 'link-button';
          undo.textContent = 'Undo';
          undo.addEventListener('click', async () => {
            await fetch(d.undo, { method: 'POST', credentials: 'same-origin', headers: { 'X-CSRFToken': csrf ? csrf.value : '' } });
            status.textContent = 'Undone.';
            dialog.querySelector('iframe').src = dialog.querySelector('iframe').src;
          });
          status.appendChild(undo);
        }
      }
    } catch (err) {
      if (status) status.textContent = err.message;
    }
  });

  // Native dialogs: [data-dialog-open="#id"] opens <dialog id> as a modal (the browser traps focus and Esc closes);
  // [data-dialog-close] inside closes it; a click on the backdrop closes it too. Focus goes back to the opener.
  document.addEventListener('click', (e) => {
    const opener = e.target.closest('[data-dialog-open]');
    if (opener) {
      const dialog = document.querySelector(opener.dataset.dialogOpen);
      if (dialog && typeof dialog.showModal === 'function' && !dialog.open) {
        dialog._opener = opener;
        dialog.showModal();
        window.attndEnhance?.(dialog);
      }
      return;
    }
    const closer = e.target.closest('[data-dialog-close]');
    if (closer) { closer.closest('dialog')?.close(); return; }
    if (e.target.tagName === 'DIALOG' && e.target.open) {
      const box = e.target.getBoundingClientRect();
      if (e.clientX < box.left || e.clientX > box.right || e.clientY < box.top || e.clientY > box.bottom) e.target.close();
    }
  });
  document.addEventListener('close', (e) => { if (e.target.tagName === 'DIALOG') e.target._opener?.focus(); }, true);

  // Tabs: <div data-tabs> holds <button data-tab-target="#panel"> buttons. Clicking one selects it
  // (aria-selected) and shows its panel; the other buttons' panels get [hidden].
  // The choice survives a reload (P0 #5: confirming an action reloads the page; the tab stays where it was).
  const tabKey = (list) => `attnd.tabs.${location.pathname}.${[...document.querySelectorAll('[data-tabs]')].indexOf(list)}`;
  function selectPageTab(button, remember) {
    const list = button.closest('[data-tabs]');
    list.querySelectorAll('[data-tab-target]').forEach(b => {
      b.setAttribute('aria-selected', b === button ? 'true' : 'false');
      const panel = document.querySelector(b.dataset.tabTarget);
      if (panel) panel.hidden = b !== button;
    });
    if (remember) { try { sessionStorage.setItem(tabKey(list), button.dataset.tabTarget); } catch (err) { /* private mode */ } }
  }
  document.addEventListener('click', e => {
    const button = e.target.closest('[data-tab-target]');
    if (!button) return;
    selectPageTab(button, true);
  });
  window.attndRestoreTabs = (scope) => {
    (scope || document).querySelectorAll('[data-tabs]').forEach(list => {
      let target = null;
      try { target = sessionStorage.getItem(tabKey(list)); } catch (err) { /* private mode */ }
      const button = target && [...list.querySelectorAll('[data-tab-target]')].find(b => b.dataset.tabTarget === target);
      if (button && document.querySelector(target)) selectPageTab(button, false);
    });
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => window.attndRestoreTabs());
  else window.attndRestoreTabs();

  // Drag-to-reorder for lists: makeSortable(list, {handle: '.drag-handle', onEnd})
  window.makeSortable = function (list, opts) {
    let dragged = null;
    list.querySelectorAll(':scope > *').forEach(item => {
      const handle = opts.handle ? item.querySelector(opts.handle) : item;
      if (!handle) return;
      handle.addEventListener('pointerdown', () => { item.draggable = true; });
      item.addEventListener('dragstart', e => { dragged = item; item.classList.add('dragging'); e.dataTransfer.effectAllowed = 'move'; });
      item.addEventListener('dragend', () => {
        item.classList.remove('dragging'); item.draggable = false; dragged = null;
        if (opts.onEnd) opts.onEnd();
      });
    });
    list.addEventListener('dragover', e => {
      if (!dragged) return;
      e.preventDefault();
      const after = [...list.querySelectorAll(':scope > :not(.dragging)')].find(el => {
        const box = el.getBoundingClientRect();
        return e.clientY < box.top + box.height / 2;
      });
      after ? list.insertBefore(dragged, after) : list.appendChild(dragged);
    });
  };

  // ---------------------------------------------------------------------------
  // Draw the eye to what changed (owner, Nov 21, 2026): one helper for every place an action somewhere changes
  // something elsewhere. Faint and quick (~200–320 ms, ease-out, Web Animations API); nothing with reduced motion.
  //   attndReveal(el, 'slide', {from: 'left'|'right'})  a panel / tab / overlay level arriving from that side
  //   attndReveal(el, 'out')                             leaving to the right with a fade (the overlay's Back)
  //   attndReveal(el, 'in')                              a new card, step line or tray item fades up
  //   attndReveal(el, 'pulse')                           a row / card / number that was added, edited, restored
  //   attndReveal(el, 'glow', {delay})                   a form field the AI just filled (in sequence)
  //   attndReveal(el, 'collapse')                        a deleted row folds away (resolves when done; then remove it)
  //   attndReveal(el, 'scroll')                          scroll it into view, then pulse
  //   attndFlip(container, mutate)                       re-sorts: children glide from their old place (FLIP)
  // ---------------------------------------------------------------------------
  const calm = window.matchMedia('(prefers-reduced-motion: reduce)');
  const EASE = 'cubic-bezier(.2, .8, .2, 1)';
  function reveal(el, kind, opts) {
    opts = opts || {};
    if (!el || !el.animate || calm.matches) {
      if (kind === 'scroll' && el) el.scrollIntoView({ block: 'nearest' });
      return Promise.resolve();
    }
    const css = getComputedStyle(document.documentElement);
    const tint = (css.getPropertyValue('--color-focus') || 'rgba(67, 31, 134, .25)').trim();
    let frames, timing = { duration: 260, easing: EASE, delay: opts.delay || 0 };
    if (kind === 'slide') {
      const dx = opts.from === 'left' ? -18 : 18;
      frames = [{ opacity: 0, transform: `translateX(${dx}px)` }, { opacity: 1, transform: 'none' }];
    } else if (kind === 'out') {  // leaving to the right with a fade (the overlay's Back)
      frames = [{ opacity: 1, transform: 'none' }, { opacity: 0, transform: 'translateX(36px)' }];
      timing = { duration: 200, easing: 'cubic-bezier(.4, 0, .8, .4)', delay: 0, fill: 'forwards' };
    } else if (kind === 'in') {
      frames = [{ opacity: 0, transform: 'translateY(6px)' }, { opacity: 1, transform: 'none' }];
      timing.duration = 220;
    } else if (kind === 'pulse' || kind === 'glow') {
      frames = [{ boxShadow: `0 0 0 0 ${tint}` }, { boxShadow: `0 0 0 4px ${tint}`, offset: .35 }, { boxShadow: `0 0 0 0 transparent` }];
      timing = { duration: kind === 'glow' ? 700 : 900, easing: 'ease-out', delay: opts.delay || 0 };
    } else if (kind === 'collapse') {
      const h = el.getBoundingClientRect().height;
      el.style.overflow = 'hidden';
      frames = [{ opacity: 1, height: `${h}px` }, { opacity: 0, height: '0px', paddingTop: '0px', paddingBottom: '0px', marginTop: '0px', marginBottom: '0px' }];
      timing.duration = 240;
    } else if (kind === 'scroll') {
      el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
      return reveal(el, 'pulse', { delay: 180 });
    } else if (kind === 'flip') {
      const { dx = 0, dy = 0 } = opts;
      if (!dx && !dy) return Promise.resolve();
      frames = [{ transform: `translate(${dx}px, ${dy}px)` }, { transform: 'none' }];
      timing.duration = 280;
    } else return Promise.resolve();
    if (kind === 'slide' || kind === 'in') { el.style.opacity = ''; el.style.transform = ''; }  // (after an 'out')
    const anim = el.animate(frames, timing);
    return anim.finished.then(() => {
      if (kind === 'out') { try { anim.commitStyles(); } catch (e) { /* detached */ } anim.cancel(); }
    }).catch(() => {});
  }
  window.attndReveal = reveal;
  window.attndFlip = function (container, mutate, key) {
    key = key || ((el) => el.dataset.key || el.dataset.ids || el.dataset.id || '');
    const before = new Map([...container.children].map((el) => [key(el), el.getBoundingClientRect()]));
    mutate();
    if (calm.matches) return;
    [...container.children].forEach((el) => {
      const was = before.get(key(el));
      if (!key(el)) return;
      if (!was) { reveal(el, 'in'); return; }
      const now = el.getBoundingClientRect();
      reveal(el, 'flip', { dx: was.left - now.left, dy: was.top - now.top });
    });
  };

  // Minimal OpenStreetMap map: pinPicker(el, latInput, lngInput, [lat, lng], zoom, others). Tap to place the pin;
  // others = [{name, lat, lng}] (the event's other locations) as small muted pins with their names, framed to fit.
  // opts.readonly: a map to look at (the event map): no pin to place; opts.onOther(point, element) when a pin is tapped.
  window.pinPicker = function (el, latInput, lngInput, center, zoom, others, opts) {
    others = others || [];
    opts = opts || {};
    if (!latInput) { latInput = document.createElement('input'); lngInput = document.createElement('input'); }
    const TILE = 256;
    const project = (lat, lng, z) => {
      const s = TILE * Math.pow(2, z), r = Math.sin(lat * Math.PI / 180);
      return [(lng + 180) / 360 * s, (0.5 - Math.log((1 + r) / (1 - r)) / (4 * Math.PI)) * s];
    };
    const unproject = (x, y, z) => {
      const s = TILE * Math.pow(2, z), n = Math.PI - 2 * Math.PI * y / s;
      return [180 / Math.PI * Math.atan(0.5 * (Math.exp(n) - Math.exp(-n))), x / s * 360 - 180];
    };
    el.textContent = '';
    el.classList.add('pin-map--osm');
    const pinned = () => !isNaN(parseFloat(latInput.value)) && !isNaN(parseFloat(lngInput.value));
    // Frame every pin: the zoom where they all fit, centred on their middle.
    const points = others.map((o) => [o.lat, o.lng]).concat(pinned() ? [[+latInput.value, +lngInput.value]] : []);
    if (points.length > 1) {
      const w = el.clientWidth || 480, h = el.clientHeight || 260;
      for (let z = 18; z >= 2; z--) {
        const xy = points.map(([a, b]) => project(a, b, z));
        const xs = xy.map((p) => p[0]), ys = xy.map((p) => p[1]);
        if (Math.max(...xs) - Math.min(...xs) < w - 48 && Math.max(...ys) - Math.min(...ys) < h - 48) {
          zoom = z;
          center = unproject((Math.max(...xs) + Math.min(...xs)) / 2, (Math.max(...ys) + Math.min(...ys)) / 2, z);
          break;
        }
      }
    }
    let [cx, cy] = project(center[0], center[1], zoom);
    const layer = document.createElement('div'); layer.className = 'tiles'; el.appendChild(layer);
    const dots = others.map((o) => {
      const d = document.createElement('button');
      d.type = 'button'; d.className = 'pin-map__other'; d.title = o.name; d.setAttribute('aria-label', o.name);
      const label = document.createElement('span'); label.className = 'pin-map__label'; label.textContent = o.name;
      d.appendChild(label);
      if (o.hidden) d.classList.add('is-hidden');
      d.addEventListener('click', (e) => { e.stopPropagation(); opts.onOther?.(o, d); });
      el.appendChild(d);
      return d;
    });
    const pin = document.createElement('div'); pin.className = 'pin'; pin.innerHTML = el.dataset.pinSvg || '●'; el.appendChild(pin);
    el.insertAdjacentHTML('beforeend', '<div class="zoom"><button type="button" data-z="1" aria-label="Zoom in">+</button><button type="button" data-z="-1" aria-label="Zoom out">−</button></div><div class="credit">© OpenStreetMap contributors</div>');

    function render() {
      const w = el.clientWidth, h = el.clientHeight, max = Math.pow(2, zoom);
      const x0 = cx - w / 2, y0 = cy - h / 2;
      layer.innerHTML = '';
      for (let tx = Math.floor(x0 / TILE); tx <= Math.floor((x0 + w) / TILE); tx++) {
        for (let ty = Math.floor(y0 / TILE); ty <= Math.floor((y0 + h) / TILE); ty++) {
          if (ty < 0 || ty >= max) continue;
          const img = new Image(); img.className = 'tile'; img.alt = ''; img.onerror = () => img.remove();
          img.src = `https://tile.openstreetmap.org/${zoom}/${((tx % max) + max) % max}/${ty}.png`;
          img.style.left = (tx * TILE - x0) + 'px'; img.style.top = (ty * TILE - y0) + 'px';
          layer.appendChild(img);
        }
      }
      others.forEach((o, i) => {
        const [px, py] = project(o.lat, o.lng, zoom);
        dots[i].style.left = (px - x0) + 'px'; dots[i].style.top = (py - y0) + 'px';
      });
      if (pinned()) {
        const [px, py] = project(+latInput.value, +lngInput.value, zoom);
        pin.style.display = ''; pin.style.left = (px - x0) + 'px'; pin.style.top = (py - y0) + 'px';
      } else pin.style.display = 'none';
    }

    let drag = null;
    el.addEventListener('pointerdown', e => {
      if (e.target.closest('.zoom, .pin-map__other, .map-popup')) return;
      drag = {x: e.clientX, y: e.clientY, cx, cy, moved: false}; el.setPointerCapture(e.pointerId);
    });
    el.addEventListener('pointermove', e => {
      if (!drag) return;
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      if (Math.abs(dx) + Math.abs(dy) > 4) drag.moved = true;
      if (drag.moved) { cx = drag.cx - dx; cy = drag.cy - dy; render(); }
    });
    const setPin = (lat, lng) => {
      latInput.value = lat.toFixed(6); lngInput.value = lng.toFixed(6);
      [latInput, lngInput].forEach((i) => i.dispatchEvent(new Event('change', { bubbles: true })));
      el.dispatchEvent(new CustomEvent('attnd:pinned', { bubbles: true }));
      render();
    };
    el.addEventListener('pointerup', e => {
      if (drag && !drag.moved && !opts.readonly) {
        const box = el.getBoundingClientRect();
        const [lat, lng] = unproject(cx - box.width / 2 + (e.clientX - box.left), cy - box.height / 2 + (e.clientY - box.top), zoom);
        setPin(lat, lng);
      }
      drag = null;
    });
    el.querySelector('.zoom').addEventListener('click', e => {
      const dz = +e.target.dataset.z; if (!dz) return;
      const nz = Math.min(19, Math.max(2, zoom + dz));
      const [lat, lng] = unproject(cx, cy, zoom); zoom = nz; [cx, cy] = project(lat, lng, zoom); render();
    });
    [latInput, lngInput].forEach(i => i.addEventListener('change', render));
    (el.closest('form') || document).addEventListener('attnd:place', (e) => {
      zoom = Math.max(zoom, 16); [cx, cy] = project(e.detail.lat, e.detail.lng, zoom); render();
    });
    new ResizeObserver(() => render()).observe(el);
    render();
  };

  // ---------------------------------------------------------------------------
  // Forms with an address and coordinates (locations, event details), on pages and in the chat / canvas cards:
  // address autocomplete (Google Places when the browser key is set, else a Find button searching on the server:
  // Google Places or OpenStreetMap), the location map (this pin + the event's other locations) and
  // "Fill from this website?" for new or empty locations. attndEnhance(scope) is safe to call again.
  // ---------------------------------------------------------------------------
  const jsonData = (el, key, fallback) => { try { return JSON.parse(el.dataset[key] || ''); } catch (e) { return fallback; } };
  function field(form, name) { return form.querySelector(`[name="${name}"], [name$="-${name}"]`); }

  function addressFinder(input, lat, lng, form, url) {
    if (!input || !url || input.dataset.finder) return;
    input.dataset.finder = '1';
    const row = document.createElement('div');
    row.className = 'address-find';
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'button button--outline button--small';
    button.textContent = 'Find on the map';
    const list = document.createElement('ul');
    list.className = 'address-find__results';
    list.hidden = true;
    const status = document.createElement('span');
    status.className = 'small muted';
    status.setAttribute('role', 'status');
    row.append(button, status);
    input.after(row, list);
    async function find() {
      const q = input.value.trim();
      if (q.length < 3) { status.textContent = 'Type an address or place name first.'; return; }
      button.disabled = true; status.textContent = 'Searching…';
      try {
        const res = await fetch(`${url}?q=${encodeURIComponent(q)}`, { credentials: 'same-origin', headers: { Accept: 'application/json' } });
        const data = await res.json();
        list.textContent = '';
        (data.results || []).forEach((r) => {
          const li = document.createElement('li');
          const b = document.createElement('button');
          b.type = 'button'; b.className = 'link-button';
          const strong = document.createElement('strong'); strong.textContent = r.label;
          const small = document.createElement('span'); small.className = 'small muted'; small.textContent = r.address;
          b.append(strong, ' ', small);
          b.addEventListener('click', () => {
            input.value = r.address || r.label;
            if (lat && lng) {
              lat.value = (+r.lat).toFixed(7); lng.value = (+r.lng).toFixed(7);
              [input, lat, lng].forEach((i) => i.dispatchEvent(new Event('change', { bubbles: true })));
              form.dispatchEvent(new CustomEvent('attnd:place', { detail: { lat: +r.lat, lng: +r.lng } }));
            }
            list.hidden = true; list.textContent = ''; status.textContent = 'Pin set from the address.';
          });
          li.appendChild(b); list.appendChild(li);
        });
        list.hidden = !list.children.length;
        status.textContent = data.error || (list.children.length ? '' : 'Nothing found. Try adding the city.');
      } catch (e) { status.textContent = 'Couldn’t search right now.'; }
      finally { button.disabled = false; }
    }
    button.addEventListener('click', find);
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); find(); } });
  }

  function placesUrl(form) {
    const el = form.querySelector('[data-places-url]');
    if (el) return el.dataset.placesUrl;
    const m = (form.getAttribute('action') || location.pathname).match(/\/console\/events\/(\d+)\//);
    return m ? `/console/events/${m[1]}/places/` : '';
  }

  function enhanceMaps(form) {
    const address = field(form, 'address'), lat = field(form, 'latitude'), lng = field(form, 'longitude');
    if (!address || !lat || !lng || form.dataset.mapsReady) return;
    form.dataset.mapsReady = '1';
    const url = placesUrl(form);
    const fallback = () => addressFinder(address, lat, lng, form, url);
    if (window.attndMaps) attndMaps.addressAutocomplete(address, { lat, lng, form }).catch(fallback);
    else fallback();
    const el = form.querySelector('[data-pin-map]');
    if (!el) return;
    const center = jsonData(el, 'center', [39.8, -98.6]), zoom = +el.dataset.zoom || 4, others = jsonData(el, 'others', []);
    const empty = form.querySelector('[data-pin-empty]');
    el.addEventListener('attnd:pinned', () => empty?.remove());
    if (window.attndMaps) attndMaps.pinMap(el, lat, lng, center, zoom, others).catch(() => window.pinPicker(el, lat, lng, center, zoom, others));
    else window.pinPicker(el, lat, lng, center, zoom, others);
  }

  // "Fill from this website?": offered once a website is typed on a location with empty fields; fills only the empty
  // ones, never saves (the person reviews and saves), and the fill itself can be undone before saving.
  const FILLABLE = ['name', 'snippet', 'description', 'category', 'tags_text', 'address', 'latitude', 'longitude', 'image_url'];
  function enhanceFill(form) {
    const box = form.querySelector('[data-fill-site]');
    const site = field(form, 'website');
    if (!box || !site || box.dataset.ready) return;
    box.dataset.ready = '1';
    const go = box.querySelector('[data-fill-go]'), status = box.querySelector('[data-fill-status]');
    const empties = () => FILLABLE.map((n) => field(form, n)).filter((i) => i && !i.value.trim());
    const sync = () => { box.hidden = !(/^https?:\/\/\S+\.\S+/.test(site.value.trim()) && empties().length); };
    site.after(box);
    site.addEventListener('input', sync);
    site.addEventListener('change', sync);
    sync();
    go.addEventListener('click', async () => {
      const csrf = form.querySelector('[name=csrfmiddlewaretoken]');
      const body = new FormData();
      body.append('url', site.value.trim());
      go.disabled = true; status.textContent = 'Reading the website…';
      try {
        const res = await fetch(box.dataset.url, { method: 'POST', body, credentials: 'same-origin',
          headers: { 'X-CSRFToken': csrf ? csrf.value : '', Accept: 'application/json' } });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || 'Couldn’t read that website.');
        const before = [];
        Object.entries(data.fields || {}).forEach(([name, value]) => {
          const input = field(form, name);
          if (!input || input.value.trim()) return;  // only empty fields: never overwrite what's there
          before.push([input, input.value]);
          input.value = value;
          input.dispatchEvent(new Event('input', { bubbles: true }));
          input.dispatchEvent(new Event('change', { bubbles: true }));
          input.classList.add('is-ai-filled');
          input.closest('details')?.setAttribute('open', '');
          reveal(input, 'glow', { delay: before.length * 90 });
        });
        const lat = field(form, 'latitude'), lng = field(form, 'longitude');
        if (data.fields && data.fields.latitude != null && before.some(([i]) => i === lat)) {
          form.dispatchEvent(new CustomEvent('attnd:place', { detail: { lat: +lat.value, lng: +lng.value } }));
        }
        status.textContent = before.length
          ? `Filled ${before.length} field${before.length === 1 ? '' : 's'} from ${data.host}. Check them, then Save.`
          : `Nothing new found on ${data.host}.`;
        if (before.length) {
          const undo = document.createElement('button');
          undo.type = 'button'; undo.className = 'link-button small'; undo.textContent = 'Undo';
          undo.addEventListener('click', () => {
            before.forEach(([input, value]) => { input.value = value; input.dispatchEvent(new Event('change', { bubbles: true })); });
            status.textContent = 'Undone.'; undo.remove(); sync();
          });
          status.append(' ', undo);
        }
        go.hidden = true;
      } catch (e) { status.textContent = e.message; }
      finally { go.disabled = false; }
    });
  }

  // The event map (Overview, canvas, chat card): every location's pin, same maps as the location form; a tap opens a
  // small themed popup with brief details and View location (on the full-screen canvas it opens there).
  function mapPopup(el, point) {
    el.querySelector('.map-popup')?.remove();
    const pop = document.createElement('div');
    pop.className = 'map-popup';
    pop.setAttribute('role', 'dialog');
    pop.setAttribute('aria-label', point.name);
    const close = document.createElement('button');
    close.type = 'button'; close.className = 'map-popup__close'; close.setAttribute('aria-label', 'Close'); close.textContent = '×';
    close.addEventListener('click', () => reveal(pop, 'collapse').then(() => pop.remove()));
    const title = document.createElement('strong'); title.textContent = point.name;
    const meta = document.createElement('span'); meta.className = 'small muted';
    meta.textContent = [point.category, point.hidden ? 'Hidden' : '', point.snippet].filter(Boolean).join(' · ');
    const view = document.createElement('a');
    view.className = 'button button--primary button--small'; view.href = point.url; view.textContent = 'View location';
    pop.append(close, title, meta, view);
    el.appendChild(pop);
    reveal(pop, 'in');
    view.focus({ preventScroll: true });
  }
  function enhanceEventMaps(scope) {
    scope.querySelectorAll('[data-event-map]:not([data-ready])').forEach((el) => {
      el.dataset.ready = '1';
      const pins = jsonData(el, 'pins', []), center = jsonData(el, 'center', [39.8, -98.6]), zoom = +el.dataset.zoom || 15;
      const opts = { readonly: true, onOther: (p) => mapPopup(el, p) };
      if (window.attndMaps) attndMaps.pinMap(el, null, null, center, zoom, pins, opts).catch(() => window.pinPicker(el, null, null, center, zoom, pins, opts));
      else window.pinPicker(el, null, null, center, zoom, pins, opts);
    });
  }

  // Live numbers (Overview numbers, Metrics): refreshed every 60 s while the page is visible (paused when hidden), the
  // numbers that changed pulse, and "Updated 3:41 PM" says how fresh they are. Chat / canvas cards do the same in
  // agent.js (attndLiveSwap).
  const LIVE_MS = 60000;
  const clock = () => new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' }).format(new Date());
  window.attndLiveSwap = function (el, fresh) {
    const before = [...el.querySelectorAll('.stat__value, [data-live-number]')].map((n) => n.textContent.trim());
    if (fresh) el.innerHTML = fresh.innerHTML;
    [...el.querySelectorAll('.stat__value, [data-live-number]')].forEach((n, i) => {
      if (before[i] !== undefined && before[i] !== n.textContent.trim()) reveal(n.closest('div') || n, 'pulse');
    });
    el.querySelectorAll('[data-live-updated]').forEach((u) => { u.textContent = `Updated ${clock()} · refreshes every minute`; });
  };
  async function liveTick() {
    if (document.hidden) return;
    window.attndLiveCards?.();  // Ask Attnd's cards (agent.js), one batch request
    const mine = [...document.querySelectorAll('[data-live]')].filter((el) => !el.closest('.agent__card, .agent, .canvas'));
    if (!mine.length) return;
    try {
      const res = await fetch(location.href, { credentials: 'same-origin', headers: { Accept: 'text/html' } });
      if (!res.ok) return;
      const doc = new DOMParser().parseFromString(await res.text(), 'text/html');
      const count = {};
      mine.forEach((el) => {
        const key = el.dataset.live; const i = count[key] = (count[key] ?? -1) + 1;
        const fresh = [...doc.querySelectorAll(`[data-live="${key}"]`)].filter((x) => !x.closest('.agent__card, .agent, .canvas'))[i];
        if (fresh) window.attndLiveSwap(el, fresh);
      });
    } catch (e) { /* offline: try again next minute */ }
  }
  let liveTimer = null;
  const liveSync = () => {
    clearInterval(liveTimer);
    liveTimer = document.hidden ? null : setInterval(liveTick, LIVE_MS);
  };
  document.addEventListener('visibilitychange', () => { liveSync(); if (!document.hidden) liveTick(); });
  liveSync();

  // API-026: the QR card's live line ("Updated on 31 of 37 phones") and the preview phones' state, every 10 s while
  // the page is visible (one small JSON request per card; pages and chat / canvas cards alike).
  async function phonesTick() {
    if (document.hidden) return;
    for (const line of document.querySelectorAll('[data-phones-line]')) {
      try {
        const r = await fetch(line.dataset.phonesUrl, { credentials: 'same-origin', headers: { Accept: 'application/json' } });
        if (!r.ok) continue;
        const d = await r.json();
        if (line.textContent !== d.text) { line.textContent = d.text; reveal(line, 'pulse'); }
        const card = line.closest('.component') || document;
        (d.previews || []).forEach((p) => {
          const state = card.querySelector(`[data-preview-state="${CSS.escape(p.id)}"]`);
          if (state && state.textContent !== p.state) { state.textContent = p.state; reveal(state, 'pulse'); }
        });
      } catch (e) { /* offline: next time */ }
    }
  }
  setInterval(phonesTick, 10000);

  // §4: edit forms save as you type (a pause of ~1.5 s, or leaving a field): the same view as Save, answered as
  // JSON (console.middleware with X-Attnd-Component). "Saving… · Saved · Undo"; errors go under their field and
  // nothing invalid is saved; Cmd/Ctrl+S saves now; leaving with a save pending asks first. New things keep Add.
  const editTimers = new WeakMap();
  const editBusy = new Set();
  function editStatus(form, html) {
    const line = form.querySelector('[data-autosave-edit-status]');
    if (line) line.innerHTML = html;
  }
  function scheduleEdit(form, ms) {
    clearTimeout(editTimers.get(form));
    form.dataset.dirty = '1';
    editTimers.set(form, setTimeout(() => saveEdit(form), ms));
  }
  async function saveEdit(form) {
    clearTimeout(editTimers.get(form));
    if (editBusy.has(form)) { form.dataset.again = '1'; return; }
    if (!form.dataset.dirty) return;
    delete form.dataset.dirty;
    editBusy.add(form);
    editStatus(form, '<span class="spinner" aria-hidden="true"></span> Saving…');
    const csrf = form.querySelector('[name=csrfmiddlewaretoken]');
    try {
      const r = await fetch(form.getAttribute('action') || location.href, {
        method: 'POST', body: new FormData(form), credentials: 'same-origin',
        headers: { Accept: 'application/json', 'X-CSRFToken': csrf ? csrf.value : '',
                   'X-Attnd-Component': form.dataset.formComponent || '' } });
      const d = await r.json().catch(() => ({}));
      form.querySelectorAll('[data-autosave-error]').forEach((x) => x.remove());
      if (d.invalid && d.html) {
        const fresh = new DOMParser().parseFromString(d.html, 'text/html');
        const names = [];
        fresh.querySelectorAll('.field__error').forEach((err) => {
          const field = err.closest('.field');
          const input = field && field.querySelector('[name]');
          const mine = input && form.querySelector(`[name="${CSS.escape(input.name)}"]`);
          const box = mine && (mine.closest('.field') || mine.parentElement);
          if (!box) return;
          const note = err.cloneNode(true);
          note.dataset.autosaveError = '';
          box.appendChild(note);
          names.push(field.querySelector('.field__label')?.textContent.trim() || input.name);
        });
        editStatus(form, `<span class="tone-danger">Not saved: check ${names.join(', ') || 'the form'}.</span>`);
      } else if (d.saved) {
        const undo = d.undo ? ` · <button type="button" class="link-button small" data-autosave-undo="${d.undo}">Undo</button>` : '';
        editStatus(form, `<span class="status-mark status-mark--success" aria-hidden="true">✓</span> Saved${undo}`);
        document.dispatchEvent(new CustomEvent('attnd:saved', { detail: { form } }));
      } else {
        throw new Error();
      }
    } catch (e) {
      form.dataset.dirty = '1';
      editStatus(form, '<span class="tone-danger">Couldn\u2019t save. Trying again when you type.</span>');
    } finally {
      editBusy.delete(form);
      if (form.dataset.again) { delete form.dataset.again; saveEdit(form); }
    }
  }
  document.addEventListener('input', (e) => {
    const form = e.target.closest && e.target.closest('form[data-autosave-edit]');
    if (form && e.target.name && e.target.type !== 'file') scheduleEdit(form, 1500);
  });
  document.addEventListener('change', (e) => {
    const form = e.target.closest && e.target.closest('form[data-autosave-edit]');
    if (form && e.target.name) scheduleEdit(form, e.target.type === 'file' ? 50 : 300);
  });
  document.addEventListener('focusout', (e) => {
    const form = e.target.closest && e.target.closest('form[data-autosave-edit]');
    if (form && form.dataset.dirty) scheduleEdit(form, 150);
  });
  document.addEventListener('keydown', (e) => {
    if (!(e.metaKey || e.ctrlKey) || e.key.toLowerCase() !== 's') return;
    const form = document.activeElement?.closest?.('form[data-autosave-edit]')
      || document.querySelector('form[data-autosave-edit]');
    if (!form) return;
    e.preventDefault();
    form.dataset.dirty = '1';
    saveEdit(form);
  });
  window.addEventListener('beforeunload', (e) => {
    const pending = [...document.querySelectorAll('form[data-autosave-edit]')].some((f) => f.dataset.dirty || editBusy.has(f));
    if (pending) { e.preventDefault(); e.returnValue = ''; }
  });
  document.addEventListener('click', async (e) => {
    const b = e.target.closest && e.target.closest('[data-autosave-undo]');
    if (!b) return;
    const form = b.closest('form');
    const csrf = form?.querySelector('[name=csrfmiddlewaretoken]') || document.querySelector('[name=csrfmiddlewaretoken]');
    try {
      const r = await fetch(`/console/undo/${b.dataset.autosaveUndo}/`, { method: 'POST', credentials: 'same-origin',
        headers: { Accept: 'application/json', 'X-CSRFToken': csrf ? csrf.value : '' } });
      if (r.ok) location.reload();  // the form shows the values from before
    } catch (err) { /* keep the line */ }
  });

  // §4: the "N unpublished changes · Review · Discard · Publish" bar (event pages, the chat's canvas): fetched, so
  // pages don't pay for the diff; again after every save (attnd:saved) and every 30 s while visible.
  async function publishBars() {
    for (const slot of document.querySelectorAll('[data-publish-bar]')) {
      try {
        const r = await fetch(slot.dataset.publishBar, { credentials: 'same-origin', headers: { Accept: 'text/html' } });
        if (!r.ok) continue;
        const html = (await r.text()).trim();
        if (slot.dataset.html === html) continue;
        const was = !!slot.dataset.html;
        slot.dataset.html = html;
        slot.innerHTML = html;
        if (html) reveal(slot.firstElementChild, was ? 'pulse' : 'in');
      } catch (e) { /* next time */ }
    }
  }
  if (document.querySelector('[data-publish-bar]')) {
    publishBars();
    setInterval(() => { if (!document.hidden) publishBars(); }, 30000);
    document.addEventListener('attnd:saved', () => setTimeout(publishBars, 300));
  }
  window.attndPublishBars = publishBars;

  // App layout's tabs: at most 5 (iOS shows five, Home counts as one): "5 of 5 tabs", the rest disabled with a note.
  function tabLimit(list) {
    const max = Number(list.dataset.maxTabs || 5);
    const boxes = [...list.querySelectorAll('input[type=checkbox][name=tabs]:not([data-empty])')];  // empty tabs don't count
    const on = boxes.filter((b) => b.checked).length;
    boxes.forEach((b) => { b.disabled = !b.checked && on >= max; });
    const count = list.parentElement.querySelector('[data-tab-count]');
    if (count) count.textContent = `${on} of ${max} tabs besides Home${on >= max ? ` · ${count.dataset.note}` : ''}`;
  }
  const tabLimits = (scope) => (scope || document).querySelectorAll('[data-max-tabs]').forEach(tabLimit);
  document.addEventListener('change', (e) => {
    const list = e.target.closest && e.target.closest('[data-max-tabs]');
    if (list && e.target.name === 'tabs') tabLimit(list);
  });
  tabLimits();
  window.attndTabLimits = tabLimits;

  // The top bar's "Chat as default" switch: it flips (animated) before the page goes on.
  document.addEventListener('submit', (e) => {
    const form = e.target.closest && e.target.closest('.chat-switch-form');
    if (!form || form.dataset.flipped) return;
    e.preventDefault();
    const sw = form.querySelector('.chat-switch');
    sw.setAttribute('aria-checked', sw.getAttribute('aria-checked') === 'true' ? 'false' : 'true');
    form.dataset.flipped = '1';
    setTimeout(() => form.submit(), calm.matches ? 0 : 240);
  });

  // Forms that save as you change them (app layout, page and cards alike): a short pause after the last change, then
  // a JSON POST to the same view; nothing reloads. A [data-tab-sortable] list keeps its order in tab_order.
  async function autosave(form) {
    const status = form.closest('.component')?.querySelector('[data-autosave-status]');
    const list = form.querySelector('[data-tab-sortable]');
    const order = form.querySelector('input[name=tab_order]');
    if (list && order) order.value = [...list.querySelectorAll('[data-tab]')].map((li) => li.dataset.tab).join(',');
    syncSectionOrder(form);
    const csrf = form.querySelector('[name=csrfmiddlewaretoken]');
    if (status) status.textContent = 'Saving…';
    try {
      const r = await fetch(form.getAttribute('action') || location.href, { method: 'POST', body: new FormData(form),
        credentials: 'same-origin', headers: { Accept: 'application/json', 'X-CSRFToken': csrf ? csrf.value : '' } });
      const d = await r.json().catch(() => ({}));
      if (!r.ok || d.saved === false) throw new Error();
      const reason = form.querySelector('[data-layout-reason]');
      if (reason && d.resolved) {
        const p = String(d.resolved.resolved_preset || '');
        reason.textContent = `Currently using ${p.charAt(0).toUpperCase() + p.slice(1)}: ${d.resolved.reason}.`;
        reveal(reason, 'pulse');
      }
      // The Screens editor (API-024): the preview of the order just saved.
      const preview = d.preview && form.closest('[data-screen-block]')?.querySelector('[data-screen-preview]');
      if (preview) { preview.innerHTML = d.preview; reveal(preview, 'pulse'); }
      if (/\/theme\//.test(form.getAttribute('action') || '')) window.attndRefreshTheme();
      if (status) {
        status.textContent = 'Saved. Open apps update within a few seconds.';
        const undo = r.headers.get('X-Attnd-Undo');
        if (undo && /^\d+$/.test(undo)) {
          const b = document.createElement('button');
          b.type = 'button'; b.className = 'link-button'; b.textContent = 'Undo';
          b.addEventListener('click', async () => {
            b.disabled = true;
            const u = await fetch(`/console/undo/${undo}/`, { method: 'POST', credentials: 'same-origin',
              headers: { Accept: 'application/json', 'X-CSRFToken': csrf ? csrf.value : '' } }).catch(() => null);
            if (u && u.ok) location.reload(); else { b.disabled = false; status.textContent = 'Couldn’t undo that.'; }
          });
          status.append(' ', b);
        }
      }
    } catch (e) {
      if (status) status.textContent = 'Couldn’t save. Check your connection and try again.';
    }
  }
  document.addEventListener('change', (e) => {
    const form = e.target.closest && e.target.closest('form[data-autosave]');
    if (!form) return;
    clearTimeout(form._autosave);
    form._autosave = setTimeout(() => autosave(form), 400);
  });
  document.addEventListener('submit', (e) => {
    const form = e.target.closest && e.target.closest('form[data-autosave]');
    if (form) { e.preventDefault(); e.stopImmediatePropagation(); autosave(form); }
  }, true);
  // Screens editor (API-024): the section list's order goes into the form's hidden order; drag or ↑ / ↓ reorder it.
  function syncSectionOrder(form) {
    const list = form.querySelector('[data-section-sortable]');
    const order = form.querySelector('input[name=order]');
    if (list && order) order.value = [...list.querySelectorAll('[data-section]')].map((li) => li.dataset.section).join(',');
  }
  function enhanceSectionLists(scope) {
    scope.querySelectorAll('[data-section-sortable]:not([data-ready])').forEach((list) => {
      list.dataset.ready = '1';
      const form = list.closest('form');
      if (form) syncSectionOrder(form);
      window.makeSortable?.(list, { handle: '.drag-handle', onEnd: () => form && autosave(form) });
      list.addEventListener('click', (e) => {
        const button = e.target.closest('[data-move]');
        if (!button) return;
        const row = button.closest('[data-section]');
        const other = button.dataset.move === 'up' ? row.previousElementSibling : row.nextElementSibling;
        if (!other) return;
        button.dataset.move === 'up' ? list.insertBefore(row, other) : list.insertBefore(other, row);
        button.focus();
        reveal(row, 'pulse');
        if (form) autosave(form);
      });
      list.addEventListener('change', (e) => {
        const box = e.target.closest('input[name=on]');
        if (box) box.closest('[data-section]')?.classList.toggle('is-off', !box.checked);
      });
    });
  }
  function enhanceTabLists(scope) {
    scope.querySelectorAll('[data-tab-sortable]:not([data-ready])').forEach((list) => {
      list.dataset.ready = '1';
      const form = list.closest('form');
      const custom = form && form.querySelector('[name$="customize_tabs"]');
      const sync = () => list.querySelectorAll('input').forEach((i) => { i.disabled = custom ? !custom.checked : false; });
      custom?.addEventListener('change', sync);
      sync();
      window.makeSortable?.(list, { handle: '.drag-handle', onEnd: () => form && autosave(form) });
    });
  }

  // The browser's time zone for new events (hidden browser_tz; an untouched time zone select starts on it).
  function enhanceTimeZones(scope) {
    let tz = '';
    try { tz = Intl.DateTimeFormat().resolvedOptions().timeZone || ''; } catch (e) { return; }
    scope.querySelectorAll('input[name=browser_tz]').forEach((i) => { if (!i.value) i.value = tz; });
    scope.querySelectorAll('select[name$="timezone"][data-browser-tz]').forEach((sel) => {
      if ([...sel.options].some((o) => o.value === tz)) sel.value = tz;
    });
  }

  // Stage I7: a notification's lock-screen preview and its length counters follow the form as you type.
  function enhanceLockPreview(scope) {
    scope.querySelectorAll('[data-lock-preview]').forEach((box) => {
      const form = box.closest('form');
      if (!form || box.dataset.bound) return;
      box.dataset.bound = '1';
      const title = form.querySelector('[name=title]');
      const body = form.querySelector('[name=body]');
      const urgent = form.querySelector('[name=urgent]');
      const sync = () => {
        box.querySelector('[data-lock-title]').textContent = title?.value || 'Title';
        box.querySelector('[data-lock-body]').textContent = body?.value || 'Your message to attendees';
        box.classList.toggle('is-urgent', !!urgent?.checked);
        [[title, '[data-count-title]'], [body, '[data-count-body]']].forEach(([el, sel]) => {
          const out = box.querySelector(sel);
          if (el && out) {
            const max = parseInt(el.getAttribute('maxlength') || '0', 10);
            out.textContent = max ? `${el.value.length}/${max}` : String(el.value.length);
            out.classList.toggle('tone-danger', !!max && el.value.length > max - 10);
          }
        });
      };
      [title, body, urgent].forEach((el) => el?.addEventListener(el.type === 'checkbox' ? 'change' : 'input', sync));
      sync();
    });
  }

  // Colour theme card (API-023): a colour picker beside every exact hex field, both kept in sync, and the card's
  // preview follows what is typed (the saved theme is contrast-adjusted on the server; this is the raw choice).
  const HEX = /^#?([0-9a-f]{6})$/i;
  function hexRgb(h) { const m = HEX.exec(h || ''); if (!m) return null; const n = parseInt(m[1], 16); return [n >> 16, (n >> 8) & 255, n & 255]; }
  function lum(c) { return c.map((v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; })
    .reduce((a, v, i) => a + v * [0.2126, 0.7152, 0.0722][i], 0); }
  function mixHex(a, b, w) { return '#' + a.map((v, i) => Math.round(v * w + b[i] * (1 - w)).toString(16).padStart(2, '0')).join(''); }
  function updateThemePreview(form) {
    const preview = form.querySelector('[data-theme-preview]');
    if (!preview) return;
    preview.querySelectorAll('[data-preview-mode]').forEach((box) => {
      const mode = box.dataset.previewMode;
      const get = (role) => hexRgb(form.querySelector(`[name$="${mode}_${role}"]`)?.value);
      const p = get('primary'), s = get('secondary'), t = get('tertiary');
      const bg = hexRgb(getComputedStyle(box).getPropertyValue('--pv-bg').trim()) || [255, 255, 255];
      if (p) {
        const hex = mixHex(p, p, 1);
        box.style.setProperty('--pv-brand', hex);
        box.style.setProperty('--pv-text', hex);
        box.style.setProperty('--pv-on', lum(p) > 0.18 ? '#120f22' : '#ffffff');
      }
      if (s) { box.style.setProperty('--pv-soft', mixHex(s, bg, mode === 'dark' ? 0.24 : 0.14));
        box.style.setProperty('--pv-line', mixHex(s, bg, mode === 'dark' ? 0.45 : 0.34)); }
      if (t) box.style.setProperty('--pv-mid', mixHex(t, t, 1));
    });
  }
  function enhanceHexFields(scope) {
    scope.querySelectorAll('input[data-hex]:not([data-ready])').forEach((text) => {
      text.dataset.ready = '1';
      const wrap = document.createElement('div');
      wrap.className = 'hex-field';
      const pick = document.createElement('input');
      pick.type = 'color';
      const label = text.closest('.field')?.querySelector('.field__label')?.textContent.trim() || 'Colour';
      pick.setAttribute('aria-label', `Pick ${label}`);
      const m = HEX.exec(text.value);
      pick.value = m ? '#' + m[1].toLowerCase() : '#431f86';
      text.parentNode.insertBefore(wrap, text);
      wrap.append(pick, text);
      const form = text.closest('form');
      pick.addEventListener('input', () => { text.value = pick.value.toUpperCase(); form && updateThemePreview(form); });
      text.addEventListener('input', () => {
        const ok = HEX.exec(text.value.trim());
        if (ok) pick.value = '#' + ok[1].toLowerCase();
        form && updateThemePreview(form);
      });
    });
  }

  window.attndEnhance = function (scope) {
    window.attndTabLimits?.(scope);
    scope = scope || document;
    enhanceHexFields(scope);
    enhanceLockPreview(scope);
    enhanceTimeZones(scope);
    enhanceTabLists(scope);
    enhanceSectionLists(scope);
    scope.querySelectorAll('form').forEach((form) => { enhanceMaps(form); enhanceFill(form); });
    enhanceEventMaps(scope);
  };
  document.addEventListener('DOMContentLoaded', () => window.attndEnhance(document));  // after maps.js has loaded

  // ---------------------------------------------------------------------------
  // Background tasks (AI imports and updates)
  // Forms with data-background submit with fetch instead of navigating. The service worker (/console/sw.js)
  // watches running tasks and sends updates here; without one, this page polls the same feed itself.
  // A tray in the corner lists running and recently finished tasks.
  // ---------------------------------------------------------------------------
  const tray = document.getElementById('task-tray');
  if (tray) {
    const FEED = '/console/tasks/';
    const buttonsByTask = {};   // task id -> the button that started it
    let lastTasks = [];
    let fallbackTimer = null;
    const expanded = new Set();  // task ids whose steps are open
    const seenDone = new Set(JSON.parse(sessionStorage.getItem('attnd.tasksSeen') || '[]'));

    const escapeHtml = (text) => String(text || '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

    const onChatPage = !!document.querySelector('.assistant');  // /console/assistant/ (desktop and narrow)
    const shownState = new Map();  // task id -> status last drawn (new tasks fade in, finished ones pulse)
    function render(tasks) {
      lastTasks = tasks;
      const active = tasks.filter((t) => t.status === 'queued' || t.status === 'running');
      const toggle = tray.querySelector('.task-toggle');
      const list = tray.querySelector('.task-list');
      // The full-screen Ask Attnd chat shows work in the chat itself (task cards, working lines): no tray bubble there.
      tray.hidden = tasks.length === 0 || onChatPage;
      toggle.querySelector('.task-count').textContent = active.length
        ? `${active.length} running` : `${tasks.length} finished`;
      toggle.classList.toggle('is-active', active.length > 0);
      // Each task opens to its friendly steps ("Reading 3 web pages", "Found 12 locations…", "Waiting for your
      // review"), updated live by the same feed; a finished one links to its result.
      list.innerHTML = tasks.map((t) => {
        const running = t.status === 'queued' || t.status === 'running';
        const status = running ? `<span class="spinner"></span>${escapeHtml(t.progress || 'Working…')}`
          : t.status === 'failed' ? `<span class="tone-danger">${escapeHtml(t.error || 'Failed')}</span>`
          : escapeHtml(t.summary || 'Done');
        const open = expanded.has(t.id);
        const steps = (t.steps || []).map((st) => {
          const mark = st.state === 'running' ? '<span class="spinner" aria-hidden="true"></span>'
            : `<span class="status-mark status-mark--${{ done: 'success', failed: 'danger', waiting: 'warning' }[st.state] || 'info'}" aria-hidden="true">${{ done: '✓', failed: '×', waiting: '!' }[st.state] || '•'}</span>`;
          return `<li class="task-step task-step--${escapeHtml(st.state)}">${mark}<span>${escapeHtml(st.text)}</span></li>`;
        }).join('');
        const link = !running && t.url ? `<a class="button button--outline button--small task-result" href="${escapeHtml(t.url)}">${escapeHtml(t.result_label || 'Open')}</a>` : '';
        return `<li class="task-item task-${t.status}${open ? ' is-open' : ''}">
          <button type="button" class="task-head" aria-expanded="${open}" aria-controls="task-steps-${escapeHtml(t.id)}" data-task="${escapeHtml(t.id)}">
            <span class="task-title">${escapeHtml(t.title)}</span><span class="task-status">${status}</span>
            <span class="task-chevron" aria-hidden="true">›</span></button>
          <div class="task-body" id="task-steps-${escapeHtml(t.id)}"${open ? '' : ' hidden'}>
            ${steps ? `<ol class="task-steps">${steps}</ol>` : ''}${link}</div></li>`;
      }).join('');
      [...list.children].forEach((li, i) => {
        const t = tasks[i];
        if (!t || !tray.dataset.ready) return;
        const was = shownState.get(t.id);
        if (was === undefined) reveal(li, 'in');
        else if (was !== t.status && (t.status === 'done' || t.status === 'failed')) reveal(li, 'pulse');
      });
      tasks.forEach((t) => shownState.set(t.id, t.status));
      tasks.forEach((t) => {
        const button = buttonsByTask[t.id];
        if (button && t.status !== 'queued' && t.status !== 'running') {
          setBusy(button, false);
          const note = button.parentElement.querySelector('.task-inline-result') || document.createElement('span');
          note.className = 'task-inline-result';
          note.innerHTML = t.status === 'failed' ? `<span class="tone-danger">${escapeHtml(t.error)}</span>`
            : `${escapeHtml(t.summary)}${t.url ? ` · <a href="${escapeHtml(t.url)}">view</a>` : ''}`;
          button.after(note);
          delete buttonsByTask[t.id];
        }
        if ((t.status === 'done' || t.status === 'failed') && !seenDone.has(t.id) && tray.dataset.ready) {
          seenDone.add(t.id);
          if (t.status === 'done') window.attndRefreshTheme?.();
          sessionStorage.setItem('attnd.tasksSeen', JSON.stringify([...seenDone].slice(-50)));
          toast(t);
        } else if (t.status === 'done' || t.status === 'failed') {
          seenDone.add(t.id);
        }
      });
      renderChatLines(tasks);
      tray.dataset.ready = '1';
      if (!active.length && fallbackTimer) { clearInterval(fallbackTimer); fallbackTimer = null; }
    }

    // The chat column's lines (Ask Attnd panel, [data-agent-tasks]): every running task with the thinking ring and its
    // current friendly step, and the ones that finished while this page was open with their result link.
    const watchedHere = new Set();
    function renderChatLines(tasks) {
      const boxes = document.querySelectorAll('[data-agent-tasks]');
      if (!boxes.length) return;
      const lines = tasks.filter((t) => {
        if (document.querySelector(`[data-task-card="${CSS.escape(t.id)}"]`)) return false;  // it has its card in the chat
        const running = t.status === 'queued' || t.status === 'running';
        if (running) watchedHere.add(t.id);
        return running || watchedHere.has(t.id);
      }).slice(0, 3).map((t) => {
        const running = t.status === 'queued' || t.status === 'running';
        const step = (t.steps || []).filter((st) => st.state === 'running').pop();
        if (running) {
          return `<div class="agent__task"><span class="agent__thinking-spinner" aria-hidden="true"></span>
            <span>${escapeHtml(step ? step.text : (t.progress || `${t.title}…`))}</span></div>`;
        }
        if (t.status === 'failed') {
          return `<div class="agent__task agent__task--failed"><span class="status-mark status-mark--danger" aria-hidden="true">×</span>
            <span>${escapeHtml(t.title)}: ${escapeHtml(t.error || 'that didn’t work')}</span></div>`;
        }
        const link = t.url ? ` · <a href="${escapeHtml(t.url)}" data-task-result>${escapeHtml(t.result_label || 'Open')}</a>` : '';
        return `<div class="agent__task agent__task--done"><span class="status-mark status-mark--success" aria-hidden="true">✓</span>
          <span>${escapeHtml(t.summary || t.title)}${link}</span></div>`;
      }).join('');
      boxes.forEach((box) => { if (box.innerHTML !== lines) box.innerHTML = lines; });
    }
    document.addEventListener('click', (e) => {
      const link = e.target.closest('[data-agent-tasks] a[href]');
      if (link && window.attndCanvasOpen && window.attndCanvasOpen(link.getAttribute('href'), link)) e.preventDefault();
    });

    function toast(t) {
      const el = document.createElement('div');
      el.className = 'task-toast' + (t.status === 'failed' ? ' is-failed' : '');
      el.innerHTML = `<strong>${escapeHtml(t.title)}</strong><div>${escapeHtml(t.status === 'failed' ? t.error : t.summary)}</div>
        ${t.url ? `<a href="${escapeHtml(t.url)}">${escapeHtml(t.result_label || 'Open')}</a>` : ''}`;
      el.addEventListener('click', (e) => {
        const link = e.target.closest('a[href]');
        if (link && window.attndCanvasOpen && window.attndCanvasOpen(link.getAttribute('href'), link)) { e.preventDefault(); el.remove(); }
      });
      document.body.appendChild(el);
      setTimeout(() => el.classList.add('is-leaving'), 7000);
      setTimeout(() => el.remove(), 7600);
    }

    function setBusy(button, busy, label) {
      if (busy) {
        button.dataset.label = button.innerHTML;
        button.disabled = true;
        button.innerHTML = `<span class="spinner"></span> ${escapeHtml(label || 'Working…')}`;
      } else if (button.dataset.label) {
        button.disabled = false;
        button.innerHTML = button.dataset.label;
      }
    }

    async function poll() {
      try {
        const response = await fetch(FEED, { headers: { Accept: 'application/json' }, credentials: 'same-origin' });
        if (response.ok) render((await response.json()).tasks);
      } catch (e) { /* offline: try again next tick */ }
    }

    function watch() {
      if (navigator.serviceWorker && navigator.serviceWorker.controller) {
        navigator.serviceWorker.controller.postMessage({ type: 'watch' });
      } else if (!fallbackTimer) {  // 2.5 s, 10 s while the tab is hidden (stage H)
        const tick = () => { poll(); fallbackTimer = setTimeout(tick, document.hidden ? 10000 : 2500); };
        fallbackTimer = setTimeout(tick, 2500);
      }
      poll();
    }

    if ('serviceWorker' in navigator) {
      navigator.serviceWorker.register('/console/sw.js', { scope: '/console/' }).catch(() => {});
      navigator.serviceWorker.addEventListener('message', (e) => {
        if (e.data && e.data.type === 'tasks') render(e.data.tasks);
      });
    }
    const toggle = tray.querySelector('.task-toggle');
    toggle.setAttribute('aria-expanded', 'false');
    toggle.setAttribute('aria-controls', 'task-panel');
    tray.querySelector('.task-panel').id = 'task-panel';
    toggle.addEventListener('click', () => {
      const open = tray.classList.toggle('is-open');
      toggle.setAttribute('aria-expanded', String(open));
      if (open) {  // the newest task opens to its steps
        if (!expanded.size && lastTasks[0]) expanded.add(lastTasks[0].id);
        render(lastTasks);
        watch();
      }
    });
    tray.addEventListener('click', (e) => {
      const head = e.target.closest('[data-task]');
      if (head) {
        const id = head.dataset.task;
        if (expanded.has(id)) expanded.delete(id); else expanded.add(id);
        render(lastTasks);
        return;
      }
      // Full-screen Ask Attnd: a task's result opens on its canvas instead of leaving the chat.
      const link = e.target.closest('a[href]');
      if (link && window.attndCanvasOpen && window.attndCanvasOpen(link.getAttribute('href'), link)) {
        e.preventDefault();
        tray.classList.remove('is-open');
        toggle.setAttribute('aria-expanded', 'false');
      }
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && tray.classList.contains('is-open')) {
        tray.classList.remove('is-open');
        toggle.setAttribute('aria-expanded', 'false');
        toggle.focus();
      }
    });
    poll().then(() => { if (lastTasks.some((t) => t.status === 'queued' || t.status === 'running')) watch(); });

    document.addEventListener('submit', async (e) => {
      const form = e.target.closest('form[data-background]');
      if (!form) return;
      e.preventDefault();
      const button = e.submitter || form.querySelector('button[type=submit], button:not([type])');
      if (button) setBusy(button, true, form.dataset.busyLabel);
      form.querySelector('.task-inline-result')?.remove();
      try {
        const response = await fetch(form.action, { method: 'POST', body: new FormData(form, e.submitter),
                                                     headers: { Accept: 'application/json' }, credentials: 'same-origin' });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || !data.task) throw new Error(data.error || 'Something went wrong. Please try again.');
        if (button) buttonsByTask[data.task.id] = button;
        render([data.task, ...lastTasks.filter((t) => t.id !== data.task.id)]);
        // Inside an Ask Attnd card (chat or canvas) the chat draws the live task card; elsewhere the tray opens.
        const inChat = form.closest('.agent__card, [data-canvas]');
        document.dispatchEvent(new CustomEvent('attnd:task-started', { detail: { task: data.task, inChat: !!inChat } }));
        if (!onChatPage && (!inChat || !document.querySelector('[data-agent-log]'))) tray.classList.add('is-open');
        if (form.dataset.afterStart === 'reset') form.reset();
        if ('Notification' in window && Notification.permission === 'default') Notification.requestPermission();
        watch();
      } catch (err) {
        if (button) setBusy(button, false);
        const note = document.createElement('span');
        note.className = 'task-inline-result tone-danger';
        note.textContent = err.message;
        (button || form).after(note);
      }
    });
  }
})();
