/* Homepage "try it": import an event from its website (POST /scrape/, then poll the job). */
(function () {
  const $ = id => document.getElementById(id);
  const form = $('scrape-form'), loading = $('scrape-loading'), success = $('scrape-success'), error = $('scrape-error');
  const input = $('event-url'), button = $('scrape-button');
  if (!form || !button) return;  // invite-only: the homepage shows a notice instead of the form
  const show = (...els) => els.forEach(el => { el.hidden = false; });
  const hide = (...els) => els.forEach(el => { el.hidden = true; });

  function fail(message) {
    hide(loading);
    $('error-message').textContent = message || 'Something went wrong. Please try again.';
    show(form, error);
    button.disabled = false;
  }

  async function run() {
    const url = input.value.trim();
    if (!url) { input.focus(); return; }
    hide(form, success, error); show(loading); button.disabled = true;
    try {
      const start = await fetch('/scrape/', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({url, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone}),
      });
      let data = await start.json().catch(() => ({error: 'Unexpected response from the server.'}));
      if (data.error) return fail(data.error);

      // The import runs in the background; poll until it finishes (up to ~5 minutes).
      const deadline = Date.now() + 5 * 60 * 1000;
      while (true) {
        await new Promise(r => setTimeout(r, 2500));
        const status = await (await fetch(data.status_url)).json();
        if (status.progress) $('scrape-progress').textContent = status.progress;
        if (status.status === 'done') { data = status; break; }
        if (status.status === 'failed') return fail(status.error);
        if (Date.now() > deadline) return fail('This is taking longer than expected. Please try again in a moment.');
      }

      hide(loading);
      $('result-code').textContent = data.event_code;
      $('result-qr').src = data.qr_code;
      const locs = data.location_count === 1 ? '1 location' : `${data.location_count} locations`;
      $('result-summary').textContent = `“${data.event_name}” — ${locs} imported.`;
      $('claim-account-button').href = data.claim_url;
      show(success);
      button.disabled = false;
    } catch (e) {
      fail('Network error — please check your connection and try again.');
    }
  }

  button.addEventListener('click', run);
  input.addEventListener('keydown', e => { if (e.key === 'Enter') run(); });
  $('scrape-again-button').addEventListener('click', () => { hide(success, error); show(form); input.select(); });
})();
