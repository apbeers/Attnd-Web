/* Google Maps in the console: address autocomplete and the location map (pin + the event's other locations).
 * Used only when GOOGLE_MAPS_BROWSER_KEY is set (a browser key restricted to our domains); otherwise console.js keeps
 * the plain address field with a Find button (server search) and its OpenStreetMap map. console.js attndEnhance
 * calls these for every form on the page and in the chat / canvas cards.
 *
 * attndMaps.addressAutocomplete(input, {lat, lng, form}) – Places suggestions; picking one fills the address and the
 *                                                          coordinate fields (and moves that form's map)
 * attndMaps.pinMap(el, latInput, lngInput, [lat, lng], zoom, others) – tap or drag to place the pin; others =
 *                                                          [{name, lat, lng}] drawn as small muted pins, framed to fit
 */
(function () {
  const config = document.getElementById('maps-config');
  if (!config || !config.dataset.key) return;
  const key = config.dataset.key;
  const mapId = config.dataset.mapId || 'DEMO_MAP_ID';
  let loading = null;

  function load() {
    if (loading) return loading;
    loading = new Promise((resolve, reject) => {
      window.__attndMapsReady = () => resolve(window.google.maps);
      const script = document.createElement('script');
      script.src = 'https://maps.googleapis.com/maps/api/js?key=' + encodeURIComponent(key) +
        '&v=weekly&loading=async&libraries=places,marker&callback=__attndMapsReady';
      script.async = true;
      script.onerror = () => { loading = null; reject(new Error('Google Maps did not load')); };
      document.head.appendChild(script);
    });
    return loading;
  }

  // Google's Maps JS (and its billing) starts only when a map or an address field is actually on screen (or the
  // field is focused), never just because a console page has a hidden form.
  function whenVisible(el) {
    return new Promise((resolve) => {
      if (!('IntersectionObserver' in window)) { resolve(); return; }
      let done = false;
      const go = () => { if (done) return; done = true; io.disconnect(); el.removeEventListener('focusin', go); resolve(); };
      const io = new IntersectionObserver((entries) => entries.some((e) => e.isIntersecting) && go(), { rootMargin: '200px' });
      io.observe(el);
      el.addEventListener('focusin', go);
    });
  }

  const setValue = (input, value) => {
    if (!input) return;
    input.value = value;
    input.dispatchEvent(new Event('change', { bubbles: true }));
  };
  // The console's theme (data-theme on <html>, else the system's): the map follows it.
  const dark = () => {
    const t = document.documentElement.dataset.theme;
    return t ? t === 'dark' : window.matchMedia('(prefers-color-scheme: dark)').matches;
  };

  async function addressAutocomplete(input, coords) {
    if (!input || input.dataset.autocomplete) return;
    input.dataset.autocomplete = '1';
    await whenVisible(input);
    const maps = await load();
    // PlaceAutocompleteElement groups a person's typing and their pick into one session token by itself (billed per
    // session, not per keystroke); it has no minimum-characters option.
    const { PlaceAutocompleteElement } = await maps.importLibrary('places');
    const widget = new PlaceAutocompleteElement();
    widget.className = 'place-autocomplete';
    widget.setAttribute('placeholder', input.value || 'Search for an address or place');
    if (dark()) widget.style.colorScheme = 'dark';
    input.before(widget);
    input.classList.add('place-autocomplete-target');
    const scope = (coords && coords.form) || document;
    const choose = async (place) => {
      await place.fetchFields({ fields: ['formattedAddress', 'location', 'displayName'] });
      setValue(input, place.formattedAddress || place.displayName || '');
      if (place.location && coords) {
        setValue(coords.lat, place.location.lat().toFixed(7));
        setValue(coords.lng, place.location.lng().toFixed(7));
        scope.dispatchEvent(new CustomEvent('attnd:place', { detail: { lat: place.location.lat(), lng: place.location.lng() } }));
      }
    };
    // Newer API: gmp-select with a prediction; older: gmp-placeselect with a place.
    widget.addEventListener('gmp-select', (e) => e.placePrediction && choose(e.placePrediction.toPlace()));
    widget.addEventListener('gmp-placeselect', (e) => e.place && choose(e.place));
  }

  async function pinMap(el, latInput, lngInput, center, zoom, others, opts) {
    opts = opts || {};
    if (!latInput) { latInput = document.createElement('input'); lngInput = document.createElement('input'); }
    await whenVisible(el);
    const maps = await load();
    const { Map } = await maps.importLibrary('maps');
    const { AdvancedMarkerElement, PinElement } = await maps.importLibrary('marker');
    el.innerHTML = '';
    el.classList.add('pin-map--google');
    const has = latInput.value && lngInput.value;
    const map = new Map(el, { center: { lat: center[0], lng: center[1] }, zoom, mapId,
                              colorScheme: dark() ? 'DARK' : 'LIGHT',
                              streetViewControl: false, mapTypeControl: false, fullscreenControl: false,
                              clickableIcons: false });
    const css = getComputedStyle(document.documentElement);
    const token = (name, fallback) => (css.getPropertyValue(name) || fallback).trim();
    (others || []).forEach((o) => {
      const pin = new PinElement({ scale: 0.6, background: token('--map-other', '#9b93ad'), borderColor: token('--map-other-line', '#7d7590'),
                                   glyphColor: token('--map-other-line', '#7d7590') });
      const m = new AdvancedMarkerElement({ map, position: { lat: o.lat, lng: o.lng }, content: pin.element, title: o.name, zIndex: 1,
                                             gmpClickable: !!opts.onOther });
      if (opts.onOther) m.addListener('click', () => opts.onOther(o, m));
    });
    const main = new PinElement({ background: token('--color-brand', '#431f86'), borderColor: token('--color-brand-line', '#2e1560'),
                                  glyphColor: '#fff' });
    const marker = new AdvancedMarkerElement({ map: has ? map : null, position: has ? { lat: +latInput.value, lng: +lngInput.value } : null,
                                               content: main.element, gmpDraggable: !opts.readonly, title: 'Drag to move', zIndex: 10 });
    if ((others || []).length) {  // frame this place and the others
      const bounds = new maps.LatLngBounds();
      others.forEach((o) => bounds.extend({ lat: o.lat, lng: o.lng }));
      if (has) bounds.extend({ lat: +latInput.value, lng: +lngInput.value });
      map.fitBounds(bounds, 40);
      maps.event.addListenerOnce(map, 'idle', () => { if (map.getZoom() > 18) map.setZoom(18); });
    }
    const place = (lat, lng, pan) => {
      marker.position = { lat, lng };
      marker.map = map;
      setValue(latInput, lat.toFixed(7));
      setValue(lngInput, lng.toFixed(7));
      if (pan) { map.panTo({ lat, lng }); if (map.getZoom() < 15) map.setZoom(16); }
      el.dispatchEvent(new CustomEvent('attnd:pinned', { bubbles: true }));
    };
    if (!opts.readonly) map.addListener('click', (e) => place(e.latLng.lat(), e.latLng.lng(), false));
    marker.addListener('dragend', () => {
      const p = marker.position;
      place(typeof p.lat === 'function' ? p.lat() : p.lat, typeof p.lng === 'function' ? p.lng() : p.lng, false);
    });
    (el.closest('form') || document).addEventListener('attnd:place', (e) => place(e.detail.lat, e.detail.lng, true));
    const typed = () => { if (latInput.value && lngInput.value) place(+latInput.value, +lngInput.value, true); };
    latInput.addEventListener('input', typed);
    lngInput.addEventListener('input', typed);
  }

  window.attndMaps = { addressAutocomplete, pinMap, load };
})();
