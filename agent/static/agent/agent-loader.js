/* Ask Attnd loads lazily (stage H): agent.js (~1,500 lines) is fetched only when the panel is opened, or at once on
   the full-screen page, when the panel was open on the previous page, or while a run is active (it refreshes the page
   when the run ends). Hovering or focusing the launcher starts the download early. */
(function () {
  const root = document.querySelector('[data-agent]');
  if (!root || !root.dataset.agentSrc) return;
  const get = (key) => { try { return sessionStorage.getItem(key); } catch (e) { return null; } };
  let loading = false;
  const load = () => {
    if (loading) return;
    loading = true;
    const script = document.createElement('script');
    script.src = root.dataset.agentSrc;
    document.body.appendChild(script);
  };
  window.attndAgentLoad = load;
  if (root.dataset.mode === 'full' || get('attnd.agent.open') === '1' || get('attnd.agent.active') === '1') {
    load();
    return;
  }
  const launcher = root.querySelector('[data-agent-open]');
  if (!launcher) return;
  const early = () => load();
  launcher.addEventListener('pointerenter', early, { once: true });
  launcher.addEventListener('focus', early, { once: true });
  launcher.addEventListener('click', (e) => {
    if (window.attndAgentReady) return;
    e.stopImmediatePropagation();
    root.dataset.openOnLoad = '1';
    load();
  });
  // "Add with AI" on a page form before agent.js is here: load it, then replay the click.
  document.addEventListener('click', (e) => {
    const b = e.target.closest('[data-add-with-ai]');
    if (!b || window.attndAgentReady) return;
    e.preventDefault();
    window.attndAgentPending = b;
    load();
  });
})();
