/* The one place the console checks a screen width in JavaScript (one responsive codebase, Nov 22, 2026): the three
   named breakpoints written at the top of console.css. attndScreen.is('phone' | 'medium' | 'wide'); attndScreen.on(fn)
   runs fn when the screen crosses one. Loaded first in <head> (no defer) so every script can ask.
   console/tests_breakpoints.py fails on a width check anywhere else, and on any other width. */
(function () {
  const QUERIES = { phone: '(max-width: 600px)', medium: '(min-width: 601px) and (max-width: 1023px)', wide: '(min-width: 1024px)' };
  const lists = Object.fromEntries(Object.entries(QUERIES).map(([name, query]) => [name, window.matchMedia(query)]));
  window.attndScreen = {
    is: (name) => !!lists[name] && lists[name].matches,
    on(fn) { Object.values(lists).forEach((list) => list.addEventListener('change', fn)); },
  };
})();
