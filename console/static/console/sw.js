/* Attnd console service worker.
 *
 * Watches background tasks (AI imports and updates) while any are running, so pages never have to
 * block on them: it polls /console/tasks/, sends every update to all open console tabs, and shows a
 * system notification when a task finishes while no console tab is visible (if allowed).
 * Pages ask it to start watching with postMessage({type: 'watch'}); it stops when nothing is running.
 * No caching: the console always talks to the live server.
 */
const FEED = '/console/tasks/';
const POLL_MS = 2500;
let polling = false;
let known = {};  // task id -> last status

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));

self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'watch') {
    event.waitUntil(watch());
  }
});

async function broadcast(message) {
  const clients = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
  clients.forEach((client) => client.postMessage(message));
  return clients;
}

async function fetchTasks() {
  const response = await fetch(FEED, { credentials: 'same-origin', headers: { Accept: 'application/json' } });
  if (!response.ok) throw new Error('HTTP ' + response.status);
  return response.json();
}

async function notifyFinished(task, clients) {
  if (clients.some((c) => c.visibilityState === 'visible')) return;
  if (!self.registration.showNotification || Notification.permission !== 'granted') return;
  const body = task.status === 'failed' ? (task.error || 'It didn’t finish.') : (task.summary || 'Done.');
  await self.registration.showNotification(task.title, {
    body, tag: 'attnd-task-' + task.id, icon: '/static/console/icons/icon-192.png', data: { url: task.url },
  });
}

async function watch() {
  if (polling) return;
  polling = true;
  try {
    // Keep going while something runs; stop after one quiet poll.
    for (;;) {
      let feed;
      try {
        feed = await fetchTasks();
      } catch (e) {
        await new Promise((r) => setTimeout(r, POLL_MS * 2));
        continue;
      }
      const clients = await broadcast({ type: 'tasks', tasks: feed.tasks, active: feed.active });
      for (const task of feed.tasks) {
        const before = known[task.id];
        if (before && before !== task.status && (task.status === 'done' || task.status === 'failed')) {
          await notifyFinished(task, clients);
        }
        known[task.id] = task.status;
      }
      if (!feed.active) break;
      await new Promise((r) => setTimeout(r, POLL_MS));
    }
  } finally {
    polling = false;
  }
}

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || '/console/';
  event.waitUntil((async () => {
    const clients = await self.clients.matchAll({ type: 'window' });
    const existing = clients.find((c) => c.url.includes('/console/'));
    if (existing) {
      await existing.focus();
      return existing.navigate(url);
    }
    return self.clients.openWindow(url);
  })());
});
