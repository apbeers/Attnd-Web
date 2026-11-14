/* Google Maps in the console: address autocomplete and a draggable pin map.
 * Used only when GOOGLE_MAPS_BROWSER_KEY is set (a browser key restricted to our domains); otherwise
 * pages keep the plain address field and the OpenStreetMap pin picker in console.js.
 *
 * attndMaps.addressAutocomplete(input, {lat, lng})  – Places suggestions; picking one fills the address
 *                                                     and the coordinate fields
 * attndMaps.pinMap(el, latInput, lngInput, [lat, lng], zoom) – click or drag to place the pin
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
      script.onerror = () => reject(new Error('Google Maps did not load'));
      document.head.appendChild(script);
    });
    return loading;
  }

  const setValue = (input, value) => {
    if (!input) return;
    input.value = value;
    input.dispatchEvent(new Event('change', { bubbles: true }));
  };

  async function addressAutocomplete(input, coords) {
    if (!input) return;
    const maps = await load();
    const { PlaceAutocompleteElement } = await maps.importLibrary('places');
    const widget = new PlaceAutocompleteElement();
    widget.className = 'place-autocomplete';
    widget.setAttribute('placeholder', input.value || 'Search for an address or place');
    input.before(widget);
    input.classList.add('place-autocomplete-target');
    const choose = async (place) => {
      await place.fetchFields({ fields: ['formattedAddress', 'location', 'displayName'] });
      setValue(input, place.formattedAddress || place.displayName || '');
      if (place.location && coords) {
        setValue(coords.lat, place.location.lat().toFixed(7));
        setValue(coords.lng, place.location.lng().toFixed(7));
        document.dispatchEvent(new CustomEvent('attnd:place', { detail: { lat: place.location.lat(), lng: place.location.lng() } }));
      }
    };
    // Newer API: gmp-select with a prediction; older: gmp-placeselect with a place.
    widget.addEventListener('gmp-select', (e) => e.placePrediction && choose(e.placePrediction.toPlace()));
    widget.addEventListener('gmp-placeselect', (e) => e.place && choose(e.place));
  }

  async function pinMap(el, latInput, lngInput, center, zoom) {
    const maps = await load();
    const { Map } = await maps.importLibrary('maps');
    const { AdvancedMarkerElement } = await maps.importLibrary('marker');
    el.innerHTML = '';
    const has = latInput.value && lngInput.value;
    const map = new Map(el, { center: { lat: center[0], lng: center[1] }, zoom, mapId,
                              streetViewControl: false, mapTypeControl: false, fullscreenControl: false });
    const marker = new AdvancedMarkerElement({ map: has ? map : null, position: has ? { lat: +latInput.value, lng: +lngInput.value } : null,
                                               gmpDraggable: true, title: 'Drag to move' });
    const place = (lat, lng, pan) => {
      marker.position = { lat, lng };
      marker.map = map;
      setValue(latInput, lat.toFixed(7));
      setValue(lngInput, lng.toFixed(7));
      if (pan) { map.panTo({ lat, lng }); if (map.getZoom() < 15) map.setZoom(16); }
    };
    map.addListener('click', (e) => place(e.latLng.lat(), e.latLng.lng(), false));
    marker.addListener('dragend', () => {
      const p = marker.position;
      place(typeof p.lat === 'function' ? p.lat() : p.lat, typeof p.lng === 'function' ? p.lng() : p.lng, false);
    });
    document.addEventListener('attnd:place', (e) => place(e.detail.lat, e.detail.lng, true));
    const typed = () => { if (latInput.value && lngInput.value) place(+latInput.value, +lngInput.value, true); };
    latInput.addEventListener('input', typed);
    lngInput.addEventListener('input', typed);
  }

  window.attndMaps = { addressAutocomplete, pinMap, load };
})();
