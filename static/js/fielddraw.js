/* Draw or edit a field contour on a Leaflet map: tap to add corners, drag a corner to move it, tap a small middle dot
   to add a corner between two, select a corner and “O‘chirish” to remove it. The area is computed live on the WGS84
   ellipsoid (same formula as the server). Coordinates are stored as [lat, lon] with 7 decimals (~1 cm). */
(function () {
  const A = 6378137.0, F = 1 / 298.257223563, E2 = F * (2 - F);
  function areaHa(pts) {
    if (!pts || pts.length < 3) return 0;
    const lat0 = pts.reduce((s, p) => s + p[0], 0) / pts.length * Math.PI / 180;
    const lon0 = pts.reduce((s, p) => s + p[1], 0) / pts.length * Math.PI / 180;
    const m0 = A * (1 - E2) / Math.pow(1 - E2 * Math.sin(lat0) ** 2, 1.5);
    const xy = pts.map(p => { const la = p[0] * Math.PI / 180, lo = p[1] * Math.PI / 180;
      const n = A / Math.sqrt(1 - E2 * Math.sin(la) ** 2); return [(lo - lon0) * n * Math.cos(la), (la - lat0) * m0]; });
    let s = 0;
    for (let i = 0; i < xy.length; i++) { const a = xy[i], b = xy[(i + 1) % xy.length]; s += a[0] * b[1] - b[0] * a[1]; }
    return Math.abs(s) / 2 / 10000;
  }
  const r7 = v => Math.round(v * 1e7) / 1e7;
  const vIcon = sel => L.divIcon({ className: 'fd-v' + (sel ? ' fd-sel' : ''), iconSize: [18, 18] });
  const mIcon = L.divIcon({ className: 'fd-m', iconSize: [12, 12] });

  window.spxEditor = function (map, opts) {
    let pts = (opts.points || []).map(p => [p[0], p[1]]);
    if (pts.length > 1 && pts[0][0] === pts[pts.length - 1][0] && pts[0][1] === pts[pts.length - 1][1]) pts.pop();
    const poly = L.polygon(pts, { color: '#1e9eff', weight: 3, fillOpacity: .25 }).addTo(map);
    const handles = L.layerGroup().addTo(map);
    let on = false, sel = -1;
    const changed = () => { poly.setLatLngs(pts); if (opts.onChange) opts.onChange(pts.slice(), areaHa(pts)); if (on) redraw(); };
    function redraw() {
      handles.clearLayers();
      pts.forEach((p, i) => {
        const m = L.marker(p, { draggable: true, icon: vIcon(i === sel), zIndexOffset: 1000 }).addTo(handles);
        m.on('drag', e => { pts[i] = [r7(e.latlng.lat), r7(e.latlng.lng)]; poly.setLatLngs(pts); });
        m.on('dragend', changed);
        m.on('click', ev => { L.DomEvent.stopPropagation(ev); sel = sel === i ? -1 : i; redraw(); if (opts.onSelect) opts.onSelect(sel); });
      });
      if (pts.length >= 2) pts.forEach((p, i) => {
        if (pts.length < 3 && i === pts.length - 1) return;
        const q = pts[(i + 1) % pts.length];
        const mid = L.marker([(p[0] + q[0]) / 2, (p[1] + q[1]) / 2], { icon: mIcon, draggable: true }).addTo(handles);
        const add = ll => { pts.splice(i + 1, 0, [r7(ll.lat), r7(ll.lng)]); sel = -1; changed(); };
        mid.on('click', ev => { L.DomEvent.stopPropagation(ev); add(mid.getLatLng()); });
        mid.on('dragend', () => add(mid.getLatLng()));
      });
    }
    function onClick(e) { pts.push([r7(e.latlng.lat), r7(e.latlng.lng)]); sel = -1; changed(); }
    const api = {
      start() { if (on) return; on = true; map.on('click', onClick); redraw(); map.getContainer().classList.add('fd-drawing'); },
      stop() { on = false; map.off('click', onClick); handles.clearLayers(); map.getContainer().classList.remove('fd-drawing'); },
      undo() { if (pts.length) { pts.pop(); sel = -1; changed(); } },
      removeSelected() { if (sel >= 0) { pts.splice(sel, 1); sel = -1; changed(); } },
      clear() { pts = []; sel = -1; changed(); },
      points: () => pts.slice(), area: () => areaHa(pts), layer: poly,
    };
    if (opts.onChange) opts.onChange(pts.slice(), areaHa(pts));
    return api;
  };
  window.spxAreaHa = areaHa;
})();
