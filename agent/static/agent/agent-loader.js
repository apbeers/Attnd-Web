/* Ask Attnd loads lazily (stage H): agent.js (~2,000 lines) is fetched only when the panel is opened, or at once on
   the full-screen page, when the panel was open on the previous page, or while a run is active (it refreshes the page
   when the run ends). Hovering or focusing the launcher starts the download early.

   One chat (Nov 22, 2026): phones (≤ 600px) never get the floating panel. Everything that opens the chat there (the
   launcher, Ctrl/⌘+K, Add with AI) goes to the full-screen chat page through attndOpenChat, with ?event= and ?from=
   like its Full screen button; the conversation follows through sessionStorage and the server thread. agent.js calls
   the same function, so there is one way in. Don't add phone-only chat code paths. */
(function () {
  const root = document.querySelector('[data-agent]');
  if (!root || !root.dataset.agentSrc) return;
  const get = (key) => { try { return sessionStorage.getItem(key); } catch (e) { return null; } };
  const full = root.dataset.mode === 'full';
  const phone = () => window.attndScreen.is('phone');  // console/static/console/screen.js
  let loading = false;
  const load = () => {
    if (loading) return;
    loading = true;
    const script = document.createElement('script');
    script.src = root.dataset.agentSrc;
    document.body.appendChild(script);
  };
  window.attndAgentLoad = load;

  // The full-screen chat for this page: ?event= (the page's event) and ?from= (this page, where Exit comes back to);
  // `ai` = the form an Add with AI click is about ({component, ids, prompt, label}).
  function chatUrl(ai) {
    const url = new URL(root.dataset.assistantUrl, location.origin);
    if (root.dataset.eventId) url.searchParams.set('event', root.dataset.eventId);
    url.searchParams.set('from', location.pathname + location.search);
    if (ai) url.searchParams.set('ai', JSON.stringify(ai));
    return url.pathname + url.search;
  }
  // Opens the chat as the full-screen page when this screen is a phone; false = use the floating panel.
  window.attndOpenChat = (ai) => {
    if (full || !phone()) return false;
    location.assign(chatUrl(ai));
    return true;
  };
  window.attndAiTarget = (b) => {
    let ids = {};
    try { ids = JSON.parse(b.dataset.aiIds || '{}'); } catch (err) { ids = {}; }
    return { component: b.dataset.aiComponent, ids, prompt: b.dataset.aiPrompt || '',
             label: `${b.textContent.trim().startsWith('Edit') ? 'Editing' : 'Filling in'} ${b.closest('.agent__card')?.dataset.title || document.title.split(' · ')[0].split(' — ')[0]} · tell Attnd what to change` };
  };

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
  // On a phone the panel never opens here, so nothing to load (agent.js loads on the chat page itself).
  if (full || (!phone() && (get('attnd.agent.open') === '1' || get('attnd.agent.active') === '1'))) {
    load();
    return;
  }
  const launcher = root.querySelector('[data-agent-open]');
  if (!launcher) return;
  const early = () => { if (!phone()) load(); };
  launcher.addEventListener('pointerenter', early, { once: true });
  launcher.addEventListener('focus', early, { once: true });
  launcher.addEventListener('click', (e) => {
    if (window.attndAgentReady) return;
    e.stopImmediatePropagation();
    if (window.attndOpenChat()) return;
    root.dataset.openOnLoad = '1';
    load();
  });
  // Ctrl/⌘+K before agent.js is here: load it and open the chat (agent.js handles the key itself afterwards).
  document.addEventListener('keydown', (e) => {
    if (window.attndAgentReady || !(e.metaKey || e.ctrlKey) || e.altKey || e.key.toLowerCase() !== 'k') return;
    e.preventDefault();
    if (window.attndOpenChat()) return;
    root.dataset.openOnLoad = '1';
    load();
  });
  // "Add with AI" on a page form before agent.js is here: load it, then replay the click (phones: the chat page).
  document.addEventListener('click', (e) => {
    const b = e.target.closest('[data-add-with-ai]');
    if (!b || window.attndAgentReady) return;
    e.preventDefault();
    if (window.attndOpenChat(window.attndAiTarget(b))) return;
    window.attndAgentPending = b;
    load();
  });
})();
