/* Map backgrounds for every map in the app. Free Esri imagery by default; when the server has a Google Map Tiles API
   key, a “Google sun’iy yo‘ldosh” layer is added (and chosen once a Google tile for this spot really loads). If Google fails (key limits, billing, no internet),
   the Esri layer stays and the contours keep working. Imagery is never live: the date is Google’s / Esri’s. */
(function () {
  const ESRI = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}';
  async function googleSession(key) {
    try {
      const c = JSON.parse(localStorage.getItem('spx-gtiles') || 'null');
      if (c && c.key === key.slice(-6) && c.expiry * 1000 > Date.now() + 3600e3) return c.session;
    } catch (_) { }
    const r = await fetch('https://tile.googleapis.com/v1/createSession?key=' + encodeURIComponent(key), {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mapType: 'satellite', language: 'uz-UZ', region: 'UZ' }) });
    if (!r.ok) throw new Error('Google ' + r.status);
    const d = await r.json();
    try { localStorage.setItem('spx-gtiles', JSON.stringify({ key: key.slice(-6), session: d.session, expiry: Number(d.expiry) })); } catch (_) { }
    return d.session;
  }
  window.spxBasemap = function (map, opts) {
    opts = opts || {};
    const esri = L.tileLayer(ESRI, { maxZoom: 19, attribution: 'Sun’iy yo‘ldosh: © Esri (surat sanasi noma’lum, jonli emas)' }).addTo(map);
    const osm = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '© OpenStreetMap' });
    const layers = { 'Sun’iy yo‘ldosh (Esri)': esri, 'Oddiy xarita': osm };
    const ctl = L.control.layers(layers, null, { position: 'topright' }).addTo(map);
    const key = (document.body.dataset.gmaps || '').trim();
    if (key) {
      googleSession(key).then(session => {
        const url = 'https://tile.googleapis.com/v1/2dtiles/{z}/{x}/{y}?session=' + session + '&key=' + encodeURIComponent(key);
        const g = L.tileLayer(url,
          { maxZoom: 21, maxNativeZoom: 20, attribution: 'Sun’iy yo‘ldosh: © Google (jonli emas)' });
        ctl.addBaseLayer(g, 'Google sun’iy yo‘ldosh');
        // switch only when Google really has a picture here (some villages have no close-up imagery → grey tiles)
        map.whenReady(() => {
          const z = Math.min(Math.round(map.getZoom()), 20), c = map.project(map.getCenter(), z).divideBy(256).floor();
          const img = new Image();
          img.onload = () => { if (map.hasLayer(esri)) { map.removeLayer(esri); g.addTo(map); } };
          img.src = L.Util.template(url, { x: c.x, y: c.y, z: z });
        });
        let errors = 0;
        g.on('tileerror', () => { if (++errors === 8 && map.hasLayer(g)) { map.removeLayer(g); esri.addTo(map);
          try { localStorage.removeItem('spx-gtiles'); } catch (_) { } } });
      }).catch(() => { /* Google unavailable: Esri stays */ });
    }
    return ctl;
  };
})();
