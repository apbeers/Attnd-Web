// Phone usability check (batch 7 Track B): opens console pages in Chromium at 360, 768 and 1280 px and reports
//  - horizontal page scroll (the page wider than the window) with the elements that stick out,
//  - at 360 px: tap targets under 44 × 44 px (buttons, form controls, links that aren't inline in text),
//  - at 360 px: text inputs under 16px (iOS zooms into them on focus).
// Usage: node phone_check.cjs <base url> <session cookie value> <path> [<path> …]  → JSON on stdout.
// Needs the `playwright` package (NODE_PATH = `npm root -g`) and Chrome (CHROME_PATH or /usr/bin/google-chrome).
const { chromium } = require('playwright');

const [base, session, ...paths] = process.argv.slice(2);
const WIDTHS = [360, 768, 1280];

function inPage() {
  const W = window.innerWidth;
  // The gallery skips off-screen rows (content-visibility: auto); measure them all.
  document.querySelectorAll('.gallery-row').forEach((row) => { row.style.contentVisibility = 'visible'; });
  const out = { overflow: [], small: [], inputs: [] };
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const scrolls = (el) => { for (let p = el.parentElement; p; p = p.parentElement) {
    const o = getComputedStyle(p).overflowX; if (o === 'auto' || o === 'scroll' || o === 'hidden' || o === 'clip') return true; } return false; };
  const name = (el) => (el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') +
    (el.className && typeof el.className === 'string' ? '.' + el.className.trim().split(/\s+/).slice(0, 3).join('.') : '') +
    ' “' + (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().slice(0, 30) + '”');
  if (document.documentElement.scrollWidth > W + 1) {
    for (const el of document.body.querySelectorAll('*')) {
      if (!visible(el) || scrolls(el)) continue;
      const r = el.getBoundingClientRect();
      if (r.right > W + 1 && el.children.length === 0) out.overflow.push(name(el) + ' right=' + Math.round(r.right));
      if (out.overflow.length > 8) break;
    }
    if (!out.overflow.length) out.overflow.push('scrollWidth ' + document.documentElement.scrollWidth);
  }
  // Platform → Components: nothing may stick out of its fixed-width frame (scrollers inside the frame are fine).
  out.frames = [];
  for (const frame of document.querySelectorAll('[data-gallery-frame]')) {
    const fr = frame.getBoundingClientRect();
    const inner = (el) => { for (let p = el.parentElement; p && p !== frame; p = p.parentElement) {
      const o = getComputedStyle(p).overflowX; if (o === 'auto' || o === 'scroll' || o === 'hidden' || o === 'clip') return true; } return false; };
    for (const el of frame.querySelectorAll('*')) {
      if (el.children.length || !visible(el) || inner(el)) continue;
      const r = el.getBoundingClientRect();
      if (r.right > fr.right + 1) {
        out.frames.push(frame.closest('[data-gallery-component]').dataset.galleryComponent + ' @' + frame.dataset.galleryFrame + ': ' + name(el));
        break;
      }
    }
  }
  if (W <= 400) {
    const sel = 'button, input:not([type=hidden]), select, textarea, summary, [role=button], a.button, a.tabs__tab, ' +
      'a.sidenav__link, a.topbar__link, a.crumbs__back, a.chip, .badge--link';
    for (const el of document.querySelectorAll(sel)) {
      if (!visible(el) || el.closest('[hidden], .visually-hidden, .agent, dialog:not([open])')) continue;
      if (el.matches('.pin-map__other')) continue;  // a map dot: its ::before is the 44px hit area
      if (el.matches(':is(td, th) > input[type=checkbox]')) {  // dense tables: 24px (WCAG 2.5.8)
        const r = el.getBoundingClientRect();
        if (r.height < 24 - 0.5) out.small.push(name(el) + ` ${Math.round(r.width)}×${Math.round(r.height)}`);
        continue;
      }
      if (el.matches('input[type=checkbox], input[type=radio]')) {
        const label = el.closest('label') || (el.id && document.querySelector(`label[for="${el.id}"]`));  // the label is the target
        const r = (label || el).getBoundingClientRect();
        if (r.height < 44 - 0.5) out.small.push(name(label || el) + ` ${Math.round(r.width)}×${Math.round(r.height)}`);
        continue;
      }
      const r = el.getBoundingClientRect();
      if (r.height < 44 - 0.5 || r.width < 44 - 0.5) out.small.push(name(el) + ` ${Math.round(r.width)}×${Math.round(r.height)}`);
    }
    for (const el of document.querySelectorAll('input:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([type=color]), select, textarea')) {
      if (!visible(el)) continue;
      const size = parseFloat(getComputedStyle(el).fontSize);
      if (size < 16) out.inputs.push(name(el) + ' ' + size + 'px');
    }
  }
  return out;
}

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || '/usr/bin/google-chrome', args: ['--no-sandbox'] });
  const results = [];
  const url = new URL(base);
  for (const width of WIDTHS) {
    const context = await browser.newContext({ viewport: { width, height: 800 }, isMobile: width <= 400, hasTouch: width <= 400,
      deviceScaleFactor: 1, ignoreHTTPSErrors: true });
    await context.addCookies([{ name: 'sessionid', value: session, domain: url.hostname, path: '/' }]);
    const page = await context.newPage();
    for (const path of paths) {
      try {
        await page.goto(base + path, { waitUntil: 'load', timeout: 20000 });
        await page.waitForTimeout(150);
        const found = await page.evaluate(inPage);
        results.push({ path, width, ...found });
      } catch (error) {
        results.push({ path, width, error: String(error).slice(0, 200) });
      }
    }
    await context.close();
  }
  await browser.close();
  process.stdout.write(JSON.stringify(results));
})();
