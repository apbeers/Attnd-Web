// Prospects dashboard (staff): "select all" for the bulk actions. No inline handlers (CSP).
(function () {
  document.addEventListener('change', function (e) {
    const all = e.target.closest('[data-select-all]');
    if (!all) return;
    const form = all.closest('form');
    form.querySelectorAll('input[name="ids"]').forEach(function (box) { box.checked = all.checked; });
  });
})();
