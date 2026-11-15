/* Passkeys (WebAuthn) for the sign-in, second-step, enrollment and Account pages. Vanilla JS, no inline
   handlers (CSP). Server side: console/views_passkeys.py + console/webauthn.py.

   Markup hooks (all hidden until this script finds WebAuthn support):
   [data-passkey-ui]                  shown when the browser supports passkeys
   [data-passkey-fallback]            hidden when it does (text for browsers without passkeys)
   [data-passkey-signin]              sign-in page: data-options-url, data-verify-url, data-next;
                                      autofill (conditional mediation) + a [data-passkey-button]
   [data-passkey-second-step]         same, with mode "second_step"
   [data-passkey-register]            button: data-options-url, data-verify-url, data-next,
                                      optional data-name-input="#id" (the passkey's name)
   [data-passkey-prompt]              banner shown only with a built-in (platform) authenticator
   [data-autoprompt]                  on [data-passkey-signin]: open the passkey sheet on load when this
                                      browser used a passkey here before (localStorage attnd.passkey.used)
   [data-password-details]            <details open> around the password form: folded ("Use a password
                                      instead") when this browser used a passkey here before
   [data-passkey-collapse]            <details open> closed when passkeys work ("Other options")
   a[data-passkey-immediate]          link to the sign-in page: data-options-url, data-verify-url, data-next;
                                      tries WebAuthn immediate UI mode on click where supported
   [data-apple-hint] / [data-other-hint]  shown / hidden on Apple platforms (authenticator setup link)
   [data-passkey-error]               where messages go (role="alert") inside the nearest [data-passkey-scope]
*/
(function () {
  'use strict';
  const supported = !!(window.PublicKeyCredential && navigator.credentials && navigator.credentials.create);

  // ---- base64url <-> ArrayBuffer -------------------------------------------------------------------
  function toB64url(buffer) {
    const bytes = new Uint8Array(buffer);
    let text = '';
    for (let i = 0; i < bytes.length; i++) text += String.fromCharCode(bytes[i]);
    return btoa(text).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  }
  function fromB64url(text) {
    const b64 = text.replace(/-/g, '+').replace(/_/g, '/') + '==='.slice((text.length + 3) % 4);
    const raw = atob(b64);
    const bytes = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
    return bytes.buffer;
  }

  function creationOptions(json) {
    const o = Object.assign({}, json);
    o.challenge = fromB64url(json.challenge);
    o.user = Object.assign({}, json.user, { id: fromB64url(json.user.id) });
    o.excludeCredentials = (json.excludeCredentials || []).map(c => Object.assign({}, c, { id: fromB64url(c.id) }));
    return o;
  }
  function requestOptions(json) {
    const o = Object.assign({}, json);
    o.challenge = fromB64url(json.challenge);
    o.allowCredentials = (json.allowCredentials || []).map(c => Object.assign({}, c, { id: fromB64url(c.id) }));
    return o;
  }
  // Serialized by hand rather than with credential.toJSON(), which some browsers and password-manager
  // extensions don't implement (or implement differently).
  function registrationJSON(cred) {
    const r = cred.response;
    return {
      id: cred.id, rawId: toB64url(cred.rawId), type: cred.type,
      authenticatorAttachment: cred.authenticatorAttachment || null,
      response: {
        clientDataJSON: toB64url(r.clientDataJSON),
        attestationObject: toB64url(r.attestationObject),
        transports: typeof r.getTransports === 'function' ? r.getTransports() : [],
      },
    };
  }
  function assertionJSON(cred) {
    const r = cred.response;
    return {
      id: cred.id, rawId: toB64url(cred.rawId), type: cred.type,
      authenticatorAttachment: cred.authenticatorAttachment || null,
      response: {
        clientDataJSON: toB64url(r.clientDataJSON),
        authenticatorData: toB64url(r.authenticatorData),
        signature: toB64url(r.signature),
        userHandle: r.userHandle ? toB64url(r.userHandle) : null,
      },
    };
  }

  // ---- server calls --------------------------------------------------------------------------------
  function csrfToken() {
    const input = document.querySelector('input[name="csrfmiddlewaretoken"]');
    return input ? input.value : '';
  }
  async function post(url, body) {
    const response = await fetch(url, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
      body: JSON.stringify(body || {}),
    });
    let data = {};
    try { data = await response.json(); } catch (e) { /* not JSON */ }
    if (!response.ok) throw new Error(data.error || 'Something went wrong. Please try again.');
    return data;
  }

  function showError(el, text) {
    const scope = el.closest('[data-passkey-scope]') || document;
    const box = scope.querySelector('[data-passkey-error]');
    if (!box) return;
    box.textContent = text || '';
    box.hidden = !text;
  }
  function friendly(err, creating) {
    if (!err) return '';
    if (err.name === 'NotAllowedError') return 'The passkey request was cancelled or timed out.';
    if (err.name === 'InvalidStateError' && creating) return 'This device already has a passkey for your account.';
    if (err.name === 'SecurityError') return 'Passkeys don’t work on this address. Use the site’s usual address.';
    if (err.name === 'NotSupportedError') return 'This device can’t make a passkey that works here.';
    return err.message || 'Something went wrong. Please try again.';
  }

  // ---- remembered on this device ----------------------------------------------------------------
  // Set after a passkey sign-in or after adding a passkey in this browser, so the sign-in page can open
  // the passkey sheet at once next time. Storage may be off (private mode, blocked): then nothing is kept.
  const REMEMBER_KEY = 'attnd.passkey.used';
  const TRIED_KEY = 'attnd.passkey.autoprompted';
  function storageGet(store, key) { try { return window[store].getItem(key); } catch (e) { return null; } }
  function storageSet(store, key, value) {
    try { if (value === null) window[store].removeItem(key); else window[store].setItem(key, value); } catch (e) { /* off */ }
  }
  function remember() { storageSet('localStorage', REMEMBER_KEY, '1'); }

  async function clientCapabilities() {
    if (typeof PublicKeyCredential.getClientCapabilities !== 'function') return {};
    try { return (await PublicKeyCredential.getClientCapabilities()) || {}; } catch (e) { return {}; }
  }

  // ---- sign in / second step ----------------------------------------------------------------------
  // Only one WebAuthn request can be pending: the autofill (conditional) request is aborted, and its
  // promise allowed to settle, before a modal or immediate request starts (WebKit loses the click's user
  // activation otherwise, bug 258642). Each request gets its own single-use challenge; the server keeps a
  // few outstanding per session (webauthn.MAX_OUTSTANDING), so abandoned ones do no harm.
  function setUpSignIn(root, mode) {
    const optionsUrl = root.dataset.optionsUrl, verifyUrl = root.dataset.verifyUrl, next = root.dataset.next || '';
    const button = root.querySelector('[data-passkey-button]');
    let autofill = null; // { controller, done } of the pending autofill request
    let autoFailed = false; // the sheet opened on load didn't sign in

    async function finish(credential) {
      const result = await post(verifyUrl, { mode, next, credential: assertionJSON(credential) });
      remember();
      window.location.assign(result.redirect);
    }

    async function request(extra) {
      const options = await post(optionsUrl, { mode });
      const credential = await navigator.credentials.get(Object.assign({ publicKey: requestOptions(options) }, extra));
      if (credential) await finish(credential);
      return !!credential;
    }

    async function stopAutofill() {
      if (!autofill) return;
      const pending = autofill;
      autofill = null;
      pending.controller.abort();
      await pending.done;
    }

    function startAutofill() {
      if (mode !== 'signin' || autofill || typeof PublicKeyCredential.isConditionalMediationAvailable !== 'function') return;
      const controller = new AbortController();
      const done = (async () => {
        try {
          if (!(await PublicKeyCredential.isConditionalMediationAvailable()) || controller.signal.aborted) return;
          await request({ mediation: 'conditional', signal: controller.signal });
        } catch (err) {
          if (err.name === 'AbortError' || err.name === 'NotAllowedError' || controller.signal.aborted) return;
          showError(root, friendly(err, false));
        } finally {
          if (autofill && autofill.controller === controller) autofill = null;
        }
      })();
      autofill = { controller, done };
    }

    // A browser that used a passkey here before: the passkey is the main way in, the password folds away.
    function passkeyFirst() {
      const details = document.querySelector('[data-password-details]');
      if (details) {
        details.open = false;
        const summary = details.querySelector('summary');
        if (summary) summary.hidden = false;
        const form = details.querySelector('form');
        if (form) form.addEventListener('submit', () => {
          if (autoFailed) storageSet('localStorage', REMEMBER_KEY, null); // went back to the password
        });
      }
      const or = root.querySelector('[data-passkey-or]');
      if (or) or.hidden = true;
      const label = root.querySelector('[data-passkey-button-label]');
      if (label) label.textContent = 'Sign in with your passkey';
      if (button) button.classList.add('button--large');
    }

    // On load: open the passkey sheet without a click (Chrome, Edge and Firefox allow a modal request;
    // Safari refuses it without a user gesture, with NotAllowedError, and the button stays on top).
    // Once per tab session, so a cancelled sheet doesn't come back on every reload.
    async function autoPrompt() {
      storageSet('sessionStorage', TRIED_KEY, '1');
      try {
        if (await request({})) return true;
      } catch (err) {
        if (err.name !== 'NotAllowedError' && err.name !== 'AbortError') showError(root, friendly(err, false));
      }
      autoFailed = true;
      return false;
    }

    if (button) {
      button.addEventListener('click', async () => {
        await stopAutofill();
        showError(root, '');
        button.disabled = true;
        try {
          await request({});
        } catch (err) {
          showError(root, friendly(err, false));
          startAutofill();
        } finally {
          button.disabled = false;
        }
      });
    }

    if (mode === 'signin' && storageGet('localStorage', REMEMBER_KEY)) {
      passkeyFirst();
      if (root.hasAttribute('data-autoprompt') && !storageGet('sessionStorage', TRIED_KEY)) {
        autoPrompt().then(signedIn => { if (!signedIn) startAutofill(); });
        return;
      }
    }
    startAutofill();
  }

  // ---- immediate sign-in from a "Sign in" link (public site) ------------------------------------------
  // WebAuthn immediate UI mode (Chrome 149+, PublicKeyCredential.getClientCapabilities().immediateGet):
  // navigator.credentials.get({publicKey, uiMode: 'immediate'}) shows the passkey sheet only when this
  // device holds a passkey for the site and otherwise rejects at once with NotAllowedError. It needs a
  // user gesture, so it runs from the click on the link; on any failure the link is simply followed to the
  // sign-in page. Browsers without the capability just follow the link (an unknown uiMode would be
  // ignored and open the full modal, so it is never sent to them).
  function setUpImmediateLinks(links, capabilities) {
    if (!capabilities.immediateGet) return;
    links.forEach(link => {
      link.addEventListener('click', async event => {
        if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        const go = () => window.location.assign(link.href);
        try {
          const options = await post(link.dataset.optionsUrl, { mode: 'signin' });
          const credential = await navigator.credentials.get({ publicKey: requestOptions(options), uiMode: 'immediate' });
          if (!credential) return go();
          const result = await post(link.dataset.verifyUrl, { mode: 'signin', next: link.dataset.next || '', credential: assertionJSON(credential) });
          remember();
          window.location.assign(result.redirect);
        } catch (err) {
          go(); // no passkey on this device, dismissed, or anything else: the normal sign-in page
        }
      });
    });
  }

  // ---- adding a passkey --------------------------------------------------------------------------
  function setUpRegister(button) {
    button.addEventListener('click', async () => {
      showError(button, '');
      const nameInput = button.dataset.nameInput ? document.querySelector(button.dataset.nameInput) : null;
      button.disabled = true;
      try {
        const options = await post(button.dataset.optionsUrl, {});
        const credential = await navigator.credentials.create({ publicKey: creationOptions(options) });
        if (!credential) return;
        const result = await post(button.dataset.verifyUrl, {
          next: button.dataset.next || '', name: nameInput ? nameInput.value : '',
          credential: registrationJSON(credential),
        });
        remember();
        window.location.assign(result.redirect);
      } catch (err) {
        showError(button, friendly(err, true));
      } finally {
        button.disabled = false;
      }
    });
  }

  // ---- page set-up ---------------------------------------------------------------------------------
  // Authenticator setup link: Apple platforms open it in Apple Passwords (iPadOS reports itself as a Mac).
  if (/Macintosh|iPhone|iPad|iPod/.test(navigator.userAgent || '')) {
    document.querySelectorAll('[data-apple-hint]').forEach(el => { el.hidden = false; });
    document.querySelectorAll('[data-other-hint]').forEach(el => { el.hidden = true; });
  }
  document.querySelectorAll('[data-passkey-fallback]').forEach(el => { el.hidden = supported; });
  if (!supported) return;
  document.querySelectorAll('[data-passkey-ui]').forEach(el => { el.hidden = false; });
  document.querySelectorAll('[data-passkey-collapse]').forEach(el => { el.open = false; });
  const immediateLinks = document.querySelectorAll('a[data-passkey-immediate]');
  if (immediateLinks.length) clientCapabilities().then(caps => setUpImmediateLinks(immediateLinks, caps));
  document.querySelectorAll('[data-passkey-signin]').forEach(el => setUpSignIn(el, 'signin'));
  document.querySelectorAll('[data-passkey-second-step]').forEach(el => setUpSignIn(el, 'second_step'));
  document.querySelectorAll('[data-passkey-register]').forEach(setUpRegister);
  const prompt = document.querySelector('[data-passkey-prompt]');
  if (prompt && typeof PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable === 'function') {
    PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable()
      .then(available => { prompt.hidden = !available; })
      .catch(() => {});
  }
})();
