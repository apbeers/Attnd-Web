/* Attnd Studio preview bridge: lets the console editor select an element on the previewed page.
   Only served on preview URLs. Talks to the parent console with postMessage; ignores anyone else. */
(function () {
  'use strict';
  var CONSOLE = __CONSOLE_ORIGINS__;
  if (window.parent === window) return;
  var selecting = false, hovered = null;
  var box = document.createElement('div');
  box.style.cssText = 'position:fixed;z-index:2147483647;pointer-events:none;border:2px solid #7b4fd6;' +
    'background:rgba(123,79,214,.12);border-radius:3px;display:none;transition:all .05s';
  document.documentElement.appendChild(box);

  function post(message) { CONSOLE.forEach(function (origin) { try { window.parent.postMessage(message, origin); } catch (e) {} }); }

  function selector(node) {
    var parts = [];
    while (node && node.nodeType === 1 && node !== document.body && parts.length < 6) {
      var part = node.tagName.toLowerCase();
      if (node.id) { parts.unshift(part + '#' + node.id); break; }
      var cls = (node.getAttribute('class') || '').trim().split(/\s+/).filter(Boolean).slice(0, 2);
      if (cls.length) part += '.' + cls.join('.');
      var parent = node.parentElement;
      if (parent) {
        var same = Array.prototype.filter.call(parent.children, function (c) { return c.tagName === node.tagName; });
        if (same.length > 1) part += ':nth-of-type(' + (same.indexOf(node) + 1) + ')';
      }
      parts.unshift(part);
      node = parent;
    }
    return parts.join(' > ');
  }

  function outline(node) {
    if (!node) { box.style.display = 'none'; return; }
    var r = node.getBoundingClientRect();
    box.style.display = 'block';
    box.style.left = r.left + 'px'; box.style.top = r.top + 'px';
    box.style.width = r.width + 'px'; box.style.height = r.height + 'px';
  }

  document.addEventListener('mouseover', function (e) {
    if (!selecting) return;
    hovered = e.target.closest('[data-attnd-host-footer]') ? null : e.target;
    outline(hovered);
  }, true);
  document.addEventListener('click', function (e) {
    if (!selecting) return;
    e.preventDefault(); e.stopPropagation();
    var node = e.target;
    if (node.closest('[data-attnd-host-footer]')) return;
    selecting = false; outline(null);
    post({ type: 'attnd-studio-selected', page: location.pathname.split('/_preview/')[1] ? location.pathname.split('/').slice(4).join('/') : '',
           selector: selector(node), tag: node.tagName.toLowerCase(),
           text: (node.innerText || '').trim().slice(0, 600), html: node.outerHTML.slice(0, 3000) });
  }, true);
  document.addEventListener('keydown', function (e) {
    if (selecting && e.key === 'Escape') { selecting = false; outline(null); post({ type: 'attnd-studio-select-cancelled' }); }
  });
  window.addEventListener('message', function (e) {
    if (e.source !== window.parent || CONSOLE.indexOf(e.origin) === -1 || !e.data) return;
    if (e.data.type === 'attnd-studio-select') { selecting = !!e.data.on; if (!selecting) outline(null); }
  });
  post({ type: 'attnd-studio-ready', page: location.pathname });
})();
