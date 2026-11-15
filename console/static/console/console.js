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

  // Tabs: <div data-tabs> holds <button data-tab-target="#panel"> buttons. Clicking one selects it
  // (aria-selected) and shows its panel; the other buttons' panels get [hidden].
  document.addEventListener('click', e => {
    const button = e.target.closest('[data-tab-target]');
    if (!button) return;
    button.closest('[data-tabs]').querySelectorAll('[data-tab-target]').forEach(b => {
      b.setAttribute('aria-selected', b === button ? 'true' : 'false');
      const panel = document.querySelector(b.dataset.tabTarget);
      if (panel) panel.hidden = b !== button;
    });
  });

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

  // Minimal OpenStreetMap pin picker: pinPicker(el, latInput, lngInput, [lat, lng], zoom)
  window.pinPicker = function (el, latInput, lngInput, center, zoom) {
    const TILE = 256;
    const project = (lat, lng, z) => {
      const s = TILE * Math.pow(2, z), r = Math.sin(lat * Math.PI / 180);
      return [(lng + 180) / 360 * s, (0.5 - Math.log((1 + r) / (1 - r)) / (4 * Math.PI)) * s];
    };
    const unproject = (x, y, z) => {
      const s = TILE * Math.pow(2, z), n = Math.PI - 2 * Math.PI * y / s;
      return [180 / Math.PI * Math.atan(0.5 * (Math.exp(n) - Math.exp(-n))), x / s * 360 - 180];
    };
    let [cx, cy] = project(center[0], center[1], zoom);
    const layer = document.createElement('div'); el.appendChild(layer);
    const pin = document.createElement('div'); pin.className = 'pin'; pin.innerHTML = el.dataset.pinSvg || '●'; el.appendChild(pin);
    el.insertAdjacentHTML('beforeend', '<div class="zoom"><button type="button" data-z="1">+</button><button type="button" data-z="-1">−</button></div><div class="credit">© OpenStreetMap contributors</div>');

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
      const lat = parseFloat(latInput.value), lng = parseFloat(lngInput.value);
      if (!isNaN(lat) && !isNaN(lng)) {
        const [px, py] = project(lat, lng, zoom);
        pin.style.display = ''; pin.style.left = (px - x0) + 'px'; pin.style.top = (py - y0) + 'px';
      } else pin.style.display = 'none';
    }

    let drag = null;
    el.addEventListener('pointerdown', e => {
      if (e.target.closest('.zoom')) return;
      drag = {x: e.clientX, y: e.clientY, cx, cy, moved: false}; el.setPointerCapture(e.pointerId);
    });
    el.addEventListener('pointermove', e => {
      if (!drag) return;
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      if (Math.abs(dx) + Math.abs(dy) > 4) drag.moved = true;
      if (drag.moved) { cx = drag.cx - dx; cy = drag.cy - dy; render(); }
    });
    el.addEventListener('pointerup', e => {
      if (drag && !drag.moved) {
        const box = el.getBoundingClientRect();
        const [lat, lng] = unproject(cx - box.width / 2 + (e.clientX - box.left), cy - box.height / 2 + (e.clientY - box.top), zoom);
        latInput.value = lat.toFixed(6); lngInput.value = lng.toFixed(6); render();
      }
      drag = null;
    });
    el.querySelector('.zoom').addEventListener('click', e => {
      const dz = +e.target.dataset.z; if (!dz) return;
      const nz = Math.min(19, Math.max(2, zoom + dz));
      const [lat, lng] = unproject(cx, cy, zoom); zoom = nz; [cx, cy] = project(lat, lng, zoom); render();
    });
    [latInput, lngInput].forEach(i => i.addEventListener('change', render));
    window.addEventListener('resize', render);
    render();
  };

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
    const seenDone = new Set(JSON.parse(sessionStorage.getItem('attnd.tasksSeen') || '[]'));

    const escapeHtml = (text) => String(text || '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

    function render(tasks) {
      lastTasks = tasks;
      const active = tasks.filter((t) => t.status === 'queued' || t.status === 'running');
      const toggle = tray.querySelector('.task-toggle');
      const list = tray.querySelector('.task-list');
      tray.hidden = tasks.length === 0;
      toggle.querySelector('.task-count').textContent = active.length
        ? `${active.length} running` : `${tasks.length} finished`;
      toggle.classList.toggle('is-active', active.length > 0);
      list.innerHTML = tasks.map((t) => {
        const running = t.status === 'queued' || t.status === 'running';
        const status = running ? `<span class="spinner"></span>${escapeHtml(t.progress || 'Working…')}`
          : t.status === 'failed' ? `<span class="tone-danger">${escapeHtml(t.error || 'Failed')}</span>`
          : escapeHtml(t.summary || 'Done');
        const link = !running && t.url ? `<a href="${escapeHtml(t.url)}">Open</a>` : '';
        return `<li class="task-item task-${t.status}"><div class="task-title">${escapeHtml(t.title)}</div>
                <div class="task-status">${status} ${link}</div></li>`;
      }).join('');
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
          sessionStorage.setItem('attnd.tasksSeen', JSON.stringify([...seenDone].slice(-50)));
          toast(t);
        } else if (t.status === 'done' || t.status === 'failed') {
          seenDone.add(t.id);
        }
      });
      tray.dataset.ready = '1';
      if (!active.length && fallbackTimer) { clearInterval(fallbackTimer); fallbackTimer = null; }
    }

    function toast(t) {
      const el = document.createElement('div');
      el.className = 'task-toast' + (t.status === 'failed' ? ' is-failed' : '');
      el.innerHTML = `<strong>${escapeHtml(t.title)}</strong><div>${escapeHtml(t.status === 'failed' ? t.error : t.summary)}</div>
        ${t.url ? `<a href="${escapeHtml(t.url)}">Open</a>` : ''}`;
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
      } else if (!fallbackTimer) {
        fallbackTimer = setInterval(poll, 2500);
      }
      poll();
    }

    if ('serviceWorker' in navigator) {
      navigator.serviceWorker.register('/console/sw.js', { scope: '/console/' }).catch(() => {});
      navigator.serviceWorker.addEventListener('message', (e) => {
        if (e.data && e.data.type === 'tasks') render(e.data.tasks);
      });
    }
    tray.querySelector('.task-toggle').addEventListener('click', () => tray.classList.toggle('is-open'));
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
        tray.classList.add('is-open');
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
