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
  // Back from the full-screen chat (Chat as default switched off, ?chat=closed): the panel stays closed and its
  // launcher pulses a few times so people know Ask Attnd is still here (with reduced motion: a short tooltip).
  const params = new URLSearchParams(location.search);
  if (params.get('chat') === 'closed') {
    try { sessionStorage.removeItem('attnd.agent.open'); sessionStorage.removeItem('attnd.agent.active'); } catch (e) { /* no storage */ }
    params.delete('chat');
    history.replaceState(null, '', location.pathname + (params.toString() ? `?${params}` : '') + location.hash);
    const pulse = () => {
      const button = root.querySelector('[data-agent-open]');
      if (!button) return;
      if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
        const tip = document.createElement('span');
        tip.className = 'agent__still-here';
        tip.setAttribute('role', 'status');
        tip.textContent = 'Ask Attnd is still here';
        button.after(tip);
        setTimeout(() => tip.remove(), 4000);
        return;
      }
      [0, 900, 1800].forEach((ms) => setTimeout(() => {
        button.classList.remove('is-pulsing');
        void button.offsetWidth;  // restart the animation
        button.classList.add('is-pulsing');
      }, ms));
      setTimeout(() => button.classList.remove('is-pulsing'), 2800);
    };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', pulse); else pulse();
  }
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
  // Ctrl/⌘+K before agent.js is here: load it and open the chat (agent.js handles the key itself afterwards).
  document.addEventListener('keydown', (e) => {
    if (window.attndAgentReady || !(e.metaKey || e.ctrlKey) || e.altKey || e.key.toLowerCase() !== 'k') return;
    e.preventDefault();
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
