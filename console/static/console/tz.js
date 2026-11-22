/* The browser's time zone for the server (console/session_expiry.py): a sign-in lasts until the next local
   midnight in this zone. Only the zone name (e.g. "America/Chicago"), nothing else. */
(function () {
  try {
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    if (!zone || !/^[A-Za-z0-9_+\-\/]{1,64}$/.test(zone)) return;
    const secure = location.protocol === 'https:' ? '; Secure' : '';
    document.cookie = `attnd_tz=${encodeURIComponent(zone)}; Path=/; Max-Age=31536000; SameSite=Lax${secure}`;
  } catch (e) { /* no Intl: the server uses its default zone */ }
})();
