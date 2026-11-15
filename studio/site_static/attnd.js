/* Attnd data library for Studio sites: live event data as custom elements, plus spam-protected forms.
   Served at <site>/_attnd/attnd.js; reads <site>/_attnd/data.json (relative to the page's <base>).
   Light DOM (so the site's CSS styles it), classes attnd-<element>__<part>, zero-specificity defaults. */
(function () {
  'use strict';
  if (window.Attnd) return;

  var DATA_URL = '_attnd/data.json';
  var REFRESH_MS = 120000;
  var data = null, etag = null, loading = null;
  var listeners = [];

  function load() {
    if (loading) return loading;
    var headers = etag ? { 'If-None-Match': etag } : {};
    loading = fetch(DATA_URL, { headers: headers, credentials: 'omit' }).then(function (r) {
      if (r.status === 304) return data;
      if (!r.ok) throw new Error('data ' + r.status);
      etag = r.headers.get('ETag');
      return r.json().then(function (json) { data = json; listeners.forEach(function (fn) { fn(data); }); return data; });
    }).catch(function () { return data; }).finally(function () { loading = null; });
    return loading;
  }
  function onData(fn) { listeners.push(fn); if (data) fn(data); else load(); }
  setInterval(function () { if (!document.hidden) load(); }, REFRESH_MS);
  document.addEventListener('visibilitychange', function () { if (!document.hidden) load(); });

  // ---- formatting in the event's time zone ----
  function tz() { return (data && data.event && data.event.timezone) || 'UTC'; }
  function fmt(iso, opts) {
    if (!iso) return '';
    try { return new Intl.DateTimeFormat(undefined, Object.assign({ timeZone: tz() }, opts)).format(new Date(iso)); }
    catch (e) { return iso; }
  }
  function time(iso) { return fmt(iso, { hour: 'numeric', minute: '2-digit' }); }
  function day(iso) { return fmt(iso, { weekday: 'long', month: 'long', day: 'numeric' }); }
  function dayKey(iso) { return fmt(iso, { year: 'numeric', month: '2-digit', day: '2-digit' }); }
  function isoDayKey(yyyy_mm_dd) {  // "2026-11-21" -> the same key format as dayKey()
    var parts = (yyyy_mm_dd || '').split('-');
    if (parts.length !== 3) return '';
    return fmt(new Date(Date.UTC(+parts[0], +parts[1] - 1, +parts[2], 12)).toISOString(),
      { year: 'numeric', month: '2-digit', day: '2-digit' });
  }
  function range(a, b) { return b ? time(a) + ' – ' + time(b) : time(a); }
  function ended(item) {
    var end = item.end_time || item.start_time;
    return end && new Date(end).getTime() < Date.now();
  }

  // ---- tiny DOM helpers (text only: never innerHTML with data) ----
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null && text !== '') n.textContent = text;
    return n;
  }
  function attr(node, name, fallback) { var v = node.getAttribute(name); return v == null ? fallback : v; }
  function hasTag(obj, tag) { return !tag || (obj.tags || []).indexOf(tag.toLowerCase()) !== -1; }
  function limit(list, node) { var n = parseInt(attr(node, 'limit', '0'), 10); return n > 0 ? list.slice(0, n) : list; }
  function empty(node, cls, text) { node.replaceChildren(el('p', cls + '__empty', text)); }

  // ---- default styles (zero specificity via :where, so the site's CSS always wins) ----
  var css = ':where(attnd-schedule,attnd-lineup,attnd-locations,attnd-map,attnd-updates,attnd-countdown,attnd-app-badge){display:block;font-family:var(--attnd-font,inherit);color:var(--attnd-text,inherit)}' +
    ':where(attnd-event){display:inline}' +
    ':where(.attnd-schedule__day){margin:1.5em 0 .5em;font-size:1.25em}' +
    ':where(.attnd-schedule__list,.attnd-updates__list,.attnd-locations__list){list-style:none;margin:0;padding:0}' +
    ':where(.attnd-schedule__item,.attnd-updates__item){display:grid;grid-template-columns:7.5em 1fr;gap:.25em 1em;padding:.75em 0;border-bottom:1px solid var(--attnd-border,rgba(127,127,127,.3))}' +
    ':where(.attnd-schedule__time){font-weight:700;color:var(--attnd-accent,inherit)}' +
    ':where(.attnd-schedule__name,.attnd-lineup__name,.attnd-locations__name,.attnd-updates__title){font-weight:700}' +
    ':where(.attnd-schedule__place,.attnd-lineup__meta,.attnd-locations__snippet,.attnd-updates__time,.attnd-schedule__desc){color:var(--attnd-muted,inherit);opacity:.85}' +
    ':where(.attnd-schedule__item--ended){opacity:.55}' +
    ':where(.attnd-lineup__grid,.attnd-locations__list){display:grid;grid-template-columns:repeat(auto-fill,minmax(14em,1fr));gap:1em}' +
    ':where(.attnd-lineup__card,.attnd-locations__card){padding:1em;border-radius:var(--attnd-radius,12px);background:var(--attnd-surface,rgba(127,127,127,.08))}' +
    ':where(.attnd-locations__img){display:block;width:100%;aspect-ratio:4/3;object-fit:cover;border-radius:calc(var(--attnd-radius,12px) * .75);margin-bottom:.6em}' +
    ':where(.attnd-locations__group-title){margin:1.25em 0 .5em}' +
    ':where(.attnd-map__frame){position:relative;overflow:hidden;border-radius:var(--attnd-radius,12px);background:#dde3ea;touch-action:none}' +
    ':where(.attnd-map__tile){position:absolute;width:256px;height:256px;user-select:none}' +
    ':where(.attnd-map__pin){position:absolute;width:28px;height:28px;margin:-28px 0 0 -14px;padding:0;border:0;background:none;cursor:pointer;color:var(--attnd-accent,#431f86)}' +
    ':where(.attnd-map__credit){position:absolute;right:4px;bottom:2px;font-size:11px;background:rgba(255,255,255,.8);color:#333;padding:0 4px}' +
    ':where(.attnd-map__zoom){position:absolute;top:8px;right:8px;display:flex;flex-direction:column;gap:4px}' +
    ':where(.attnd-map__zoom button){width:34px;height:34px;font-size:20px;border:0;border-radius:8px;background:#fff;color:#111;cursor:pointer}' +
    ':where(.attnd-map__label){margin:.75em 0 0;font-weight:700}' +
    ':where(.attnd-countdown__units){display:flex;gap:1em;flex-wrap:wrap}' +
    ':where(.attnd-countdown__unit){display:flex;flex-direction:column;align-items:center;min-width:3.5em}' +
    ':where(.attnd-countdown__value){font-size:2.5em;font-weight:800;line-height:1}' +
    ':where(.attnd-app-badge){display:flex;flex-wrap:wrap;gap:1em;align-items:center}' +
    ':where(.attnd-app-badge__store){display:inline-flex;align-items:center;gap:.5em;padding:.7em 1.2em;border-radius:999px;background:var(--attnd-text,#111);color:var(--attnd-surface,#fff);font-weight:700;text-decoration:none;min-height:44px}' +
    ':where(.attnd-app-badge__qr){width:120px;height:120px;background:#fff;padding:6px;border-radius:8px}' +
    ':where(.attnd-app-badge__code){font-family:ui-monospace,monospace;font-weight:700;letter-spacing:.08em}';
  var style = document.createElement('style');
  style.id = 'attnd-elements';
  style.textContent = css;
  (document.head || document.documentElement).prepend(style);

  // ---- elements ----
  function define(name, render) {
    if (customElements.get(name)) return;
    customElements.define(name, class extends HTMLElement {
      connectedCallback() {
        var self = this;
        if (!this._bound) { this._bound = true; onData(function (d) { if (self.isConnected) render(self, d); }); }
      }
    });
  }

  function scheduleItems(node, d) {
    var tag = attr(node, 'tag', ''), loc = attr(node, 'location', ''), wantDay = attr(node, 'day', '');
    var showPast = node.hasAttribute('show-past');
    var key = wantDay ? isoDayKey(wantDay) : '';
    return d.schedule.filter(function (i) {
      return hasTag(i, tag) && (!loc || (i.location_name || '').toLowerCase() === loc.toLowerCase())
        && (!key || dayKey(i.start_time) === key) && (showPast || !ended(i));
    });
  }

  define('attnd-schedule', function (node, d) {
    var items = limit(scheduleItems(node, d), node);
    if (!items.length) return empty(node, 'attnd-schedule', attr(node, 'empty', 'The schedule is coming soon.'));
    var by = attr(node, 'group-by', 'day');
    var groups = [], index = {};
    items.forEach(function (i) {
      var k = by === 'location' ? (i.location_name || 'General') : by === 'none' ? '' : day(i.start_time);
      if (!(k in index)) { index[k] = groups.length; groups.push({ title: k, items: [] }); }
      groups[index[k]].items.push(i);
    });
    var out = [];
    groups.forEach(function (g) {
      if (g.title) out.push(el('h3', 'attnd-schedule__day', g.title));
      var ul = el('ul', 'attnd-schedule__list');
      g.items.forEach(function (i) {
        var li = el('li', 'attnd-schedule__item' + (ended(i) ? ' attnd-schedule__item--ended' : ''));
        li.append(el('span', 'attnd-schedule__time', range(i.start_time, i.end_time)));
        var body = el('div', 'attnd-schedule__body');
        body.append(el('div', 'attnd-schedule__name', i.name));
        if (i.location_name && by !== 'location') body.append(el('div', 'attnd-schedule__place', i.location_name));
        if (i.description && node.hasAttribute('descriptions')) body.append(el('p', 'attnd-schedule__desc', i.description));
        li.append(body);
        ul.append(li);
      });
      out.push(ul);
    });
    node.replaceChildren.apply(node, out);
  });

  define('attnd-lineup', function (node, d) {
    var items = limit(scheduleItems(node, d), node);
    if (!items.length) return empty(node, 'attnd-lineup', attr(node, 'empty', 'The lineup is coming soon.'));
    var grid = el('div', 'attnd-lineup__grid');
    items.forEach(function (i) {
      var card = el('article', 'attnd-lineup__card');
      card.append(el('h3', 'attnd-lineup__name', i.name));
      card.append(el('p', 'attnd-lineup__meta', [day(i.start_time), time(i.start_time), i.location_name].filter(Boolean).join(' · ')));
      if (i.description) card.append(el('p', 'attnd-lineup__desc', i.description));
      grid.append(card);
    });
    node.replaceChildren(grid);
  });

  function locationsFor(node, d) {
    var cat = attr(node, 'category', ''), tag = attr(node, 'tag', '');
    return d.locations.filter(function (l) {
      return hasTag(l, tag) && (!cat || (l.category || '').toLowerCase() === cat.toLowerCase());
    });
  }

  define('attnd-locations', function (node, d) {
    var list = limit(locationsFor(node, d), node);
    if (!list.length) return empty(node, 'attnd-locations', attr(node, 'empty', 'Locations are coming soon.'));
    var groups = [], index = {};
    list.forEach(function (l) {
      var k = attr(node, 'category', '') ? '' : (l.category || 'More');
      if (!(k in index)) { index[k] = groups.length; groups.push({ title: k, items: [] }); }
      groups[index[k]].items.push(l);
    });
    var out = [];
    groups.forEach(function (g) {
      var section = el('section', 'attnd-locations__group');
      if (g.title && groups.length > 1) section.append(el('h3', 'attnd-locations__group-title', g.title));
      var ul = el('ul', 'attnd-locations__list');
      g.items.forEach(function (l) {
        var li = el('li', 'attnd-locations__card');
        if (node.hasAttribute('images') && l.image_url) {
          var img = el('img', 'attnd-locations__img'); img.src = l.image_url; img.alt = ''; img.loading = 'lazy';
          li.append(img);
        }
        li.append(el('div', 'attnd-locations__name', l.name));
        if (l.snippet) li.append(el('div', 'attnd-locations__snippet', l.snippet));
        if (l.address) li.append(el('div', 'attnd-locations__address', l.address));
        ul.append(li);
      });
      section.append(ul);
      out.push(section);
    });
    node.replaceChildren.apply(node, out);
  });

  define('attnd-updates', function (node, d) {
    var list = limit(d.updates, node);
    if (!list.length) return empty(node, 'attnd-updates', attr(node, 'empty', 'No updates yet.'));
    var ul = el('ul', 'attnd-updates__list');
    list.forEach(function (u) {
      var li = el('li', 'attnd-updates__item' + (u.priority === 'urgent' ? ' attnd-updates__item--urgent' : ''));
      li.append(el('span', 'attnd-updates__time', fmt(u.sent_at, { weekday: 'short', hour: 'numeric', minute: '2-digit' })));
      var body = el('div', 'attnd-updates__body');
      body.append(el('div', 'attnd-updates__title', u.title));
      body.append(el('p', 'attnd-updates__text', u.body));
      li.append(body);
      ul.append(li);
    });
    node.replaceChildren(ul);
  });

  define('attnd-event', function (node, d) {
    var e = d.event, field = attr(node, 'field', 'name'), text = '';
    if (field === 'name') text = e.name;
    else if (field === 'dates') {
      var a = day(e.start_time), b = e.end_time ? day(e.end_time) : '';
      text = b && b !== a ? a + ' – ' + b : a;
    } else if (field === 'time') text = range(e.start_time, e.end_time);
    else if (field === 'address') text = e.address;
    else if (field === 'description') text = e.description;
    else if (field === 'code') text = e.code;
    node.textContent = text || '';
  });

  define('attnd-countdown', function (node, d) {
    var target = attr(node, 'to', '') || d.event.start_time;
    var endAt = d.event.end_time;
    function tick() {
      var now = Date.now(), t = new Date(target).getTime();
      if (isNaN(t)) { node.textContent = ''; return; }
      if (now >= t) {
        var over = endAt && now > new Date(endAt).getTime();
        node.replaceChildren(el('p', 'attnd-countdown__now', over ? attr(node, 'ended', 'That\'s a wrap — thanks for coming!')
          : attr(node, 'now', 'Happening now')));
        return;
      }
      var s = Math.floor((t - now) / 1000);
      var units = [['days', Math.floor(s / 86400)], ['hours', Math.floor(s / 3600) % 24], ['minutes', Math.floor(s / 60) % 60]];
      var wrap = el('div', 'attnd-countdown__units');
      wrap.setAttribute('role', 'timer');
      units.forEach(function (u) {
        var box = el('div', 'attnd-countdown__unit');
        box.append(el('span', 'attnd-countdown__value', String(u[1])), el('span', 'attnd-countdown__label', u[0]));
        wrap.append(box);
      });
      node.replaceChildren(wrap);
    }
    tick();
    clearInterval(node._timer);
    node._timer = setInterval(tick, 30000);
  });

  define('attnd-app-badge', function (node, d) {
    var out = [];
    var ios = el('a', 'attnd-app-badge__store attnd-app-badge__store--ios', 'Download on the App Store');
    ios.href = d.app.ios; ios.rel = 'noopener';
    var android = el('a', 'attnd-app-badge__store attnd-app-badge__store--android', 'Get it on Google Play');
    android.href = d.app.android; android.rel = 'noopener';
    out.push(ios, android);
    if (node.hasAttribute('qr')) {
      var link = el('a', 'attnd-app-badge__qr-link'); link.href = d.app.join_url;
      var img = el('img', 'attnd-app-badge__qr'); img.src = '_attnd/qr.svg'; img.alt = 'QR code: open ' + d.event.name + ' in the Attnd app';
      img.width = 120; img.height = 120;
      link.append(img); out.push(link);
    }
    if (node.hasAttribute('code')) out.push(el('span', 'attnd-app-badge__code', 'Event code ' + d.app.code));
    node.replaceChildren.apply(node, out);
  });

  // ---- map: OpenStreetMap tiles, pins, list (no API key) ----
  var TILE = 256;
  function project(lat, lng, z) {
    var s = TILE * Math.pow(2, z), r = Math.sin(lat * Math.PI / 180);
    return [(lng + 180) / 360 * s, (0.5 - Math.log((1 + r) / (1 - r)) / (4 * Math.PI)) * s];
  }
  var PIN = '<svg viewBox="0 0 24 24" width="28" height="28" aria-hidden="true"><path fill="currentColor" d="M12 22s-7-6.5-7-12a7 7 0 0 1 14 0c0 5.5-7 12-7 12z"/><circle cx="12" cy="10" r="2.6" fill="#fff"/></svg>';

  define('attnd-map', function (node, d) {
    var pins = locationsFor(node, d).filter(function (l) {
      return l.latitude != null && l.longitude != null && !(l.latitude === 0 && l.longitude === 0);
    });
    if (!pins.length && d.event.latitude != null) pins = [{ name: d.event.name, latitude: d.event.latitude, longitude: d.event.longitude, address: d.event.address }];
    if (!pins.length) return empty(node, 'attnd-map', attr(node, 'empty', 'The map is coming soon.'));
    var height = parseInt(attr(node, 'height', '420'), 10) || 420;
    var frame = el('div', 'attnd-map__frame'); frame.style.height = height + 'px';
    frame.setAttribute('role', 'region'); frame.setAttribute('aria-label', 'Map of ' + d.event.name);
    var layer = el('div'), label = el('p', 'attnd-map__label');
    label.setAttribute('aria-live', 'polite');
    frame.append(layer);
    var credit = el('div', 'attnd-map__credit'); credit.innerHTML = '© <a href="https://www.openstreetmap.org/copyright" rel="noopener">OpenStreetMap</a>';
    var zoomBox = el('div', 'attnd-map__zoom');
    var zin = el('button', '', '+'), zout = el('button', '', '−');
    zin.type = zout.type = 'button'; zin.setAttribute('aria-label', 'Zoom in'); zout.setAttribute('aria-label', 'Zoom out');
    zoomBox.append(zin, zout);
    frame.append(credit, zoomBox);
    var list = el('ul', 'attnd-locations__list attnd-map__list');
    pins.forEach(function (p) {
      var li = el('li', 'attnd-locations__card');
      li.append(el('div', 'attnd-locations__name', p.name));
      if (p.address) li.append(el('div', 'attnd-locations__address', p.address));
      list.append(li);
    });
    node.replaceChildren(frame, label, list);

    var lats = pins.map(function (p) { return p.latitude; }), lngs = pins.map(function (p) { return p.longitude; });
    var zoom = 17, cx, cy;
    function fit() {
      var w = frame.clientWidth || 600;
      for (zoom = 18; zoom > 3; zoom--) {
        var a = project(Math.max.apply(null, lats), Math.min.apply(null, lngs), zoom);
        var b = project(Math.min.apply(null, lats), Math.max.apply(null, lngs), zoom);
        if (b[0] - a[0] < w - 60 && b[1] - a[1] < height - 80) break;
      }
      var c = project((Math.max.apply(null, lats) + Math.min.apply(null, lats)) / 2, (Math.max.apply(null, lngs) + Math.min.apply(null, lngs)) / 2, zoom);
      cx = c[0]; cy = c[1];
    }
    function draw() {
      var w = frame.clientWidth, h = frame.clientHeight, max = Math.pow(2, zoom), x0 = cx - w / 2, y0 = cy - h / 2;
      layer.replaceChildren();
      for (var tx = Math.floor(x0 / TILE); tx <= Math.floor((x0 + w) / TILE); tx++) {
        for (var ty = Math.floor(y0 / TILE); ty <= Math.floor((y0 + h) / TILE); ty++) {
          if (ty < 0 || ty >= max) continue;
          var img = el('img', 'attnd-map__tile'); img.alt = ''; img.draggable = false;
          img.src = 'https://tile.openstreetmap.org/' + zoom + '/' + (((tx % max) + max) % max) + '/' + ty + '.png';
          img.style.left = (tx * TILE - x0) + 'px'; img.style.top = (ty * TILE - y0) + 'px';
          layer.append(img);
        }
      }
      pins.forEach(function (p) {
        var xy = project(p.latitude, p.longitude, zoom);
        var b = el('button', 'attnd-map__pin'); b.type = 'button'; b.innerHTML = PIN;
        b.setAttribute('aria-label', p.name);
        b.style.left = (xy[0] - x0) + 'px'; b.style.top = (xy[1] - y0) + 'px';
        b.addEventListener('click', function () { label.textContent = p.name + (p.address ? ' — ' + p.address : ''); });
        layer.append(b);
      });
    }
    var drag = null;
    frame.addEventListener('pointerdown', function (e) {
      if (e.target.closest('button,a')) return;
      drag = { x: e.clientX, y: e.clientY, cx: cx, cy: cy }; frame.setPointerCapture(e.pointerId);
    });
    frame.addEventListener('pointermove', function (e) {
      if (!drag) return; cx = drag.cx - (e.clientX - drag.x); cy = drag.cy - (e.clientY - drag.y); draw();
    });
    frame.addEventListener('pointerup', function () { drag = null; });
    function zoomBy(dz) {
      var nz = Math.min(19, Math.max(3, zoom + dz)); if (nz === zoom) return;
      var f = Math.pow(2, nz - zoom); cx *= f; cy *= f; zoom = nz; draw();
    }
    zin.addEventListener('click', function () { zoomBy(1); });
    zout.addEventListener('click', function () { zoomBy(-1); });
    fit(); draw();
    if (!node._resize) { node._resize = true; window.addEventListener('resize', function () { draw(); }); }
  });

  // ---- forms: <form data-attnd-form="name"> ----
  var loadedAt = Date.now();
  function prepare(form) {
    if (form._attnd) return; form._attnd = true;
    var name = form.getAttribute('data-attnd-form');
    if (!form.getAttribute('action')) form.setAttribute('action', '_attnd/forms/' + name);
    form.setAttribute('method', 'post');
    var hp = document.createElement('div');
    hp.setAttribute('aria-hidden', 'true');
    hp.style.cssText = 'position:absolute;left:-10000px;width:1px;height:1px;overflow:hidden';
    hp.innerHTML = '<label>Leave this empty<input name="_hp" tabindex="-1" autocomplete="off"></label>';
    form.append(hp);
    var ts = document.createElement('input'); ts.type = 'hidden'; ts.name = '_ts'; ts.value = String(loadedAt); form.append(ts);
    var page = document.createElement('input'); page.type = 'hidden'; page.name = '_page'; page.value = location.pathname; form.append(page);
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      var button = form.querySelector('[type=submit],button:not([type])');
      var status = form.querySelector('[data-attnd-form-status]');
      if (!status) { status = el('p', 'attnd-form__status'); status.setAttribute('data-attnd-form-status', ''); form.append(status); }
      status.setAttribute('role', 'status');
      if (button) button.disabled = true;
      fetch(form.getAttribute('action'), { method: 'POST', body: new FormData(form), headers: { Accept: 'application/json' }, credentials: 'omit' })
        .then(function (r) { return r.json().catch(function () { return {}; }).then(function (j) { return [r, j]; }); })
        .then(function (res) {
          if (res[0].ok) {
            status.textContent = form.getAttribute('data-success') || 'Thanks! We got it.';
            form.reset(); form.classList.add('attnd-form--sent');
          } else {
            status.textContent = res[1].error || 'Sorry, that didn\'t go through. Please try again.';
          }
        })
        .catch(function () { status.textContent = 'Sorry, that didn\'t go through. Please try again.'; })
        .finally(function () { if (button) button.disabled = false; });
    });
  }
  function scanForms() { document.querySelectorAll('form[data-attnd-form]').forEach(prepare); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', scanForms); else scanForms();
  new MutationObserver(scanForms).observe(document.documentElement, { childList: true, subtree: true });

  window.Attnd = { load: load, onData: onData, get data() { return data; } };
})();
