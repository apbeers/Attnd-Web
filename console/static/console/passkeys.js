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

  // ---- sign in / second step ----------------------------------------------------------------------
  function setUpSignIn(root, mode) {
    const optionsUrl = root.dataset.optionsUrl, verifyUrl = root.dataset.verifyUrl, next = root.dataset.next || '';
    const button = root.querySelector('[data-passkey-button]');
    let autofill = null; // AbortController of the pending autofill request

    async function run(mediation) {
      const options = await post(optionsUrl, { mode });
      const publicKey = requestOptions(options);
      const request = { publicKey };
      if (mediation === 'conditional') {
        autofill = new AbortController();
        request.mediation = 'conditional';
        request.signal = autofill.signal;
      }
      const credential = await navigator.credentials.get(request);
      if (!credential) return;
      const result = await post(verifyUrl, { mode, next, credential: assertionJSON(credential) });
      window.location.assign(result.redirect);
    }

    async function startAutofill() {
      if (mode !== 'signin' || typeof PublicKeyCredential.isConditionalMediationAvailable !== 'function') return;
      try {
        if (!(await PublicKeyCredential.isConditionalMediationAvailable())) return;
        await run('conditional');
      } catch (err) {
        if (err.name === 'AbortError' || err.name === 'NotAllowedError') return; // replaced or dismissed
        showError(root, friendly(err, false));
      }
    }

    if (button) {
      button.addEventListener('click', async () => {
        if (autofill) { autofill.abort(); autofill = null; }
        showError(root, '');
        button.disabled = true;
        try {
          await run('modal');
        } catch (err) {
          showError(root, friendly(err, false));
          startAutofill();
        } finally {
          button.disabled = false;
        }
      });
    }
    startAutofill();
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
        window.location.assign(result.redirect);
      } catch (err) {
        showError(button, friendly(err, true));
      } finally {
        button.disabled = false;
      }
    });
  }

  // ---- page set-up ---------------------------------------------------------------------------------
  document.querySelectorAll('[data-passkey-fallback]').forEach(el => { el.hidden = supported; });
  if (!supported) return;
  document.querySelectorAll('[data-passkey-ui]').forEach(el => { el.hidden = false; });
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
