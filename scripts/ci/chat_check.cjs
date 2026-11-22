// One chat on every screen (Nov 22, 2026): drives Ask Attnd on an emulated iPhone and on a 1440 × 900 desktop.
// Phone: the launcher on an event page opens the full-screen chat page (no bottom sheet), sending keeps the bubble in
// view and the conversation (thread) unchanged, a shorter viewport (the keyboard) keeps the composer visible with
// nothing on top of it, history opens over the log and closes on a pick, closing and reopening keeps the
// conversation. Desktop: the floating panel and the full-screen page still open.
// Usage: node chat_check.cjs <base url> <session cookie> <event id> [<screenshot dir> <label>] → JSON on stdout.
// Needs the `playwright` package (NODE_PATH = `npm root -g`) and Chrome (CHROME_PATH or /usr/bin/google-chrome).
const { chromium, devices } = require('playwright');

const [base, session, eventId, shots, label] = process.argv.slice(2);
const problems = [];
const notes = [];
const check = (ok, text) => { if (!ok) problems.push(text); };

async function snap(page, name) {
  if (!shots) return;
  await page.screenshot({ path: `${shots}/${label || 'run'}-${name}.png` });
}

// Is the composer visible, inside the viewport, and the topmost thing at its centre? (No bleed-through.)
async function composerClear(page) {
  return page.evaluate(() => {
    const form = document.querySelector('[data-agent-form]');
    if (!form) return { ok: false, why: 'no composer' };
    const r = form.getBoundingClientRect();
    const vh = (window.visualViewport ? window.visualViewport.height : window.innerHeight);
    const pts = [[r.left + 12, r.top + r.height / 2], [r.left + r.width / 2, r.top + r.height / 2], [r.right - 12, r.top + r.height / 2]];
    for (const [x, y] of pts) {
      const el = document.elementFromPoint(x, y);
      if (!el || !form.contains(el)) return { ok: false, why: `covered by ${el ? el.className || el.tagName : 'nothing'} at ${Math.round(x)},${Math.round(y)}` };
    }
    const log = document.querySelector('[data-agent-log]').getBoundingClientRect();
    return { ok: r.bottom <= vh + 1 && r.top >= 0 && log.bottom <= r.top + 1 && log.height > 40,
             why: `composer ${Math.round(r.top)}–${Math.round(r.bottom)}, viewport ${Math.round(vh)}, log ${Math.round(log.top)}–${Math.round(log.bottom)}`,
             scrollY: window.scrollY };
  });
}

async function phone(browser) {
  const url = new URL(base);
  const context = await browser.newContext({ ...devices['iPhone 15 Pro'], ignoreHTTPSErrors: true });
  await context.addCookies([{ name: 'sessionid', value: session, domain: url.hostname, path: '/' }]);
  const page = await context.newPage();
  page.on('pageerror', (e) => problems.push(`phone page error: ${e.message}`));
  await page.goto(`${base}/console/events/${eventId}/locations/`, { waitUntil: 'load' });
  await snap(page, 'phone-1-page');
  await page.click('[data-agent-open]');
  await page.waitForSelector('[data-agent-input]:visible', { timeout: 10000 });
  await page.waitForTimeout(600);
  notes.push(`phone opened at ${new URL(page.url()).pathname}`);
  check(new URL(page.url()).pathname.startsWith('/console/assistant/'), `phone: the launcher should open the chat page, got ${page.url()}`);
  await snap(page, 'phone-2-open');
  let clear = await composerClear(page);
  check(clear.ok, `phone open: ${clear.why}`);

  // Send: the bubble appears at once and stays; the answer lands in the same conversation.
  const threadBefore = await page.evaluate(() => sessionStorage.getItem('attnd.agent.thread'));
  await page.fill('[data-agent-input]', 'Which locations have no schedule?');
  await page.click('[data-agent-send]');
  await page.waitForTimeout(150);
  const bubble = await page.evaluate(() => {
    const b = [...document.querySelectorAll('.agent__msg--user')].pop();
    if (!b) return null;
    const r = b.getBoundingClientRect();
    const log = document.querySelector('[data-agent-log]').getBoundingClientRect();
    return { text: b.textContent, inView: r.top >= log.top - 1 && r.bottom <= log.bottom + 1 };
  });
  check(bubble && /no schedule/.test(bubble.text), 'phone: the sent bubble did not appear');
  check(bubble && bubble.inView, 'phone: the sent bubble is out of view');
  await page.waitForSelector('.agent__msg--assistant', { timeout: 15000 });
  await page.waitForTimeout(800);
  await snap(page, 'phone-3-sent');
  const after = await page.evaluate(() => ({ thread: sessionStorage.getItem('attnd.agent.thread'), path: location.pathname,
    users: [...document.querySelectorAll('.agent__msg--user')].map((b) => b.textContent) }));
  check(after.path.startsWith('/console/assistant/'), 'phone: navigated away after sending');
  check(after.users.some((t) => /no schedule/.test(t)), 'phone: the request is gone from the log after the answer');
  check(!threadBefore || threadBefore === after.thread, `phone: the thread changed ${threadBefore} → ${after.thread}`);

  // The keyboard: a shorter viewport keeps the composer right above it, nothing over it, the page doesn't scroll.
  const size = page.viewportSize();
  await page.focus('[data-agent-input]');
  await page.setViewportSize({ width: size.width, height: Math.round(size.height * 0.55) });
  await page.waitForTimeout(400);
  clear = await composerClear(page);
  check(clear.ok, `phone keyboard: ${clear.why}`);
  check(!clear.scrollY, `phone keyboard: the page scrolled (${clear.scrollY})`);
  await snap(page, 'phone-4-keyboard');
  await page.setViewportSize(size);
  await page.waitForTimeout(300);

  // History: over the log (not in its flow), closed by a pick; the conversation shown stays the person's.
  const logBox = await page.locator('[data-agent-log]').boundingBox();
  await page.click('[data-agent-history]');
  await page.waitForSelector('[data-agent-recent] button', { timeout: 10000 });
  await page.waitForTimeout(200);
  const logBox2 = await page.locator('[data-agent-log]').boundingBox();
  check(Math.abs(logBox.y - logBox2.y) < 2 && Math.abs(logBox.height - logBox2.height) < 2,
        `phone history: the log moved (${Math.round(logBox.y)}/${Math.round(logBox.height)} → ${Math.round(logBox2.y)}/${Math.round(logBox2.height)})`);
  await snap(page, 'phone-5-history');
  await page.keyboard.press('Escape');
  await page.waitForTimeout(150);
  check(await page.locator('#agent-recent').isHidden(), 'phone history: Esc did not close it');
  await page.click('[data-agent-history]');
  await page.waitForSelector('[data-agent-recent] button');
  await page.locator('[data-agent-recent] button').first().click();
  await page.waitForTimeout(800);
  check(await page.locator('#agent-recent').isHidden(), 'phone history: a pick did not close it');

  // Close and reopen: the same conversation.
  await page.click('[data-agent-exit]');
  await page.waitForLoadState('load');
  await page.waitForTimeout(300);
  notes.push(`phone exit to ${new URL(page.url()).pathname}`);
  await page.click('[data-agent-open]');
  await page.waitForSelector('[data-agent-input]:visible');
  await page.waitForTimeout(800);
  const again = await page.evaluate(() => [...document.querySelectorAll('.agent__msg--user')].map((b) => b.textContent));
  check(again.some((t) => /no schedule/.test(t)), 'phone reopen: the conversation is gone');
  clear = await composerClear(page);
  check(clear.ok, `phone reopen: ${clear.why}`);
  await snap(page, 'phone-6-reopen');

  // A second request stays in the same conversation, below the first.
  await page.fill('[data-agent-input]', 'Thanks, and the schedule?');
  await page.click('[data-agent-send]');
  await page.waitForTimeout(1500);
  const second = await page.evaluate(() => ({ thread: sessionStorage.getItem('attnd.agent.thread'),
    users: [...document.querySelectorAll('.agent__msg--user')].map((b) => b.textContent) }));
  check(second.users.some((t) => /no schedule/.test(t)) && second.users.some((t) => /and the schedule/.test(t)),
        `phone second send: the log lost the conversation (${JSON.stringify(second.users)})`);
  check(second.thread === after.thread, `phone second send: the thread changed ${after.thread} → ${second.thread}`);
  await snap(page, 'phone-7-second');
  await context.close();
}

async function desktop(browser) {
  const url = new URL(base);
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, ignoreHTTPSErrors: true });
  await context.addCookies([{ name: 'sessionid', value: session, domain: url.hostname, path: '/' }]);
  const page = await context.newPage();
  page.on('pageerror', (e) => problems.push(`desktop page error: ${e.message}`));
  await page.goto(`${base}/console/events/${eventId}/locations/`, { waitUntil: 'load' });
  await page.click('[data-agent-open]');
  await page.waitForSelector('#agent-panel:visible', { timeout: 10000 });
  await page.waitForTimeout(900);
  check(new URL(page.url()).pathname.endsWith('/locations/'), 'desktop: the launcher should open the floating panel');
  await snap(page, 'desktop-1-panel');
  await page.goto(`${base}/console/assistant/?event=${eventId}`, { waitUntil: 'load' });
  await page.waitForTimeout(1200);
  await snap(page, 'desktop-2-full');
  await context.close();
}

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || '/usr/bin/google-chrome', args: ['--no-sandbox'] });
  try { await phone(browser); } catch (e) { problems.push(`phone: ${String(e).slice(0, 300)}`); }
  try { await desktop(browser); } catch (e) { problems.push(`desktop: ${String(e).slice(0, 300)}`); }
  await browser.close();
  process.stdout.write(JSON.stringify({ problems, notes }));
})();
