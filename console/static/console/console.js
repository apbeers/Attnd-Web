/* Attnd console — small vanilla helpers (no third-party JS). */
(function () {
  // Dismissible alerts
  document.addEventListener('click', e => {
    const btn = e.target.closest('[data-dismiss="alert"]');
    if (btn) btn.closest('.alert').remove();
  });

  // Tabs: <button data-tab-target="#id">
  document.addEventListener('click', e => {
    const btn = e.target.closest('[data-tab-target]');
    if (!btn) return;
    const group = btn.closest('[data-tabs]');
    group.querySelectorAll('[data-tab-target]').forEach(b => b.classList.toggle('active', b === btn));
    const target = document.querySelector(btn.dataset.tabTarget);
    target.parentElement.querySelectorAll(':scope > .tab-pane').forEach(p => p.classList.toggle('active', p === target));
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
})();
