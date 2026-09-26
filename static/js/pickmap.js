/* “Which part of the field(s) did this trip pick”: the trip's field and its neighbours, split into cells over
   satellite imagery. Tap cells, or walk with the phone and cells under it are marked. When cells of two fields are
   marked, the split (e.g. D-16 80 % · D-17 20 %) is shown — the system shares the trip's kg and pay the same way. */
(function () {
  const el = document.getElementById('pk-map'), dataEl = document.getElementById('pk-data'), out = document.getElementById('pk-cells');
  if (!el || !dataEl || !out || !window.L) return;
  const d = JSON.parse(dataEl.textContent || '{}');
  const chosen = new Set(d.chosen || []), done = new Set(), info = {}, fieldOf = {};
  const fmt = n => String(Math.round(n)).replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
  const map = L.map(el, { zoomControl: true, attributionControl: false, scrollWheelZoom: false });
  if (window.spxBasemap) spxBasemap(map); else L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', { maxZoom: 19 }).addTo(map);
  const style = k => chosen.has(k) ? { color: '#052e16', weight: 1, fillColor: '#22c55e', fillOpacity: .66 }
    : done.has(k) ? { color: '#a16207', weight: .6, fillColor: '#facc15', fillOpacity: .5 }
    : { color: '#ffffff', weight: .6, opacity: .55, fillColor: '#ffffff', fillOpacity: .04 };
  const shapes = {}, cellsAll = [];
  let main = null;
  d.fields.forEach(f => {
    info[f.id] = f; f.done.forEach(k => done.add(k));
    L.polygon(f.poly, { color: f.main ? '#ffffff' : '#e2e8f0', weight: f.main ? 3 : 1.8, dashArray: f.main ? null : '6 5', fill: false })
      .bindTooltip(f.code, { permanent: true, direction: 'center', className: 'map-label' }).addTo(map);
    if (f.main) main = f;
    f.cells.forEach(c => {
      fieldOf[c.k] = f.id; cellsAll.push(c);
      shapes[c.k] = L.polygon(c.poly, style(c.k)).addTo(map).on('click', () => { chosen.has(c.k) ? chosen.delete(c.k) : chosen.add(c.k); paint(c.k); });
    });
  });
  map.fitBounds(main ? main.poly : d.fields[0].poly, { padding: [26, 26] });
  function paint(k) { if (k && shapes[k]) shapes[k].setStyle(style(k)); sum(); }
  function sum() {
    out.value = [...chosen].join(',');
    const per = {};
    chosen.forEach(k => { const f = fieldOf[k]; if (f) per[f] = (per[f] || 0) + info[f].cell_ha; });
    const ha = Object.values(per).reduce((a, b) => a + b, 0), box = document.getElementById('pk-sum');
    if (!chosen.size) { box.innerHTML = '<span style="color:#b45309">Hali belgilanmagan — kataklarni bosing yoki “Yurib belgilash”</span>'; return; }
    let html = `Belgilandi ≈ <b>${ha.toFixed(2).replace('.', ',')} ga</b> · ${fmt(d.kg)} kg → <b class="pk-cha">${(d.kg / 100 / ha).toFixed(1).replace('.', ',')} s/ga</b>`;
    const ids = Object.keys(per);
    if (ids.length > 1) {
      html += '<div class="pk-split">Ikki dalaga bo‘linadi: ' + ids.sort((a, b) => per[b] - per[a]).map(id =>
        `<span><b>${info[id].code}</b> ${Math.round(per[id] / ha * 100)}% · ${fmt(d.kg * per[id] / ha)} kg</span>`).join('') + '</div>';
    }
    if (main) {
      
      html += `<div class="tiny">${d.round}-terim: ${main.code} dalaning ${Math.round(new Set([...chosen, ...done].filter(k => fieldOf[k] === main.id)).size / main.n * 100)}% i terildi (${main.area || '—'} ga)</div>`;
    }
    box.innerHTML = html;
  }
  document.getElementById('pk-clear').addEventListener('click', () => { const ks = [...chosen]; chosen.clear(); ks.forEach(k => shapes[k].setStyle(style(k))); sum(); });
  let watch = null, me = null;
  const inCell = (p, poly) => { const la = poly.map(x => x[0]), lo = poly.map(x => x[1]);
    return p[0] >= Math.min(...la) && p[0] <= Math.max(...la) && p[1] >= Math.min(...lo) && p[1] <= Math.max(...lo); };
  const btn = document.getElementById('pk-walk');
  btn.addEventListener('click', () => {
    if (watch !== null) { navigator.geolocation.clearWatch(watch); watch = null; btn.textContent = '🚶 Yurib belgilash'; btn.classList.remove('btn-green'); return; }
    if (!navigator.geolocation) return alert('Bu telefonda joylashuv yo‘q.');
    btn.textContent = '⏹ To‘xtatish'; btn.classList.add('btn-green');
    watch = navigator.geolocation.watchPosition(pos => {
      const p = [pos.coords.latitude, pos.coords.longitude];
      if (!me) me = L.circleMarker(p, { radius: 7, color: '#fff', weight: 2, fillColor: '#2563eb', fillOpacity: 1 }).addTo(map); else me.setLatLng(p);
      if (pos.coords.accuracy > 25) return;
      const c = cellsAll.find(c => c.poly.length === 4 && inCell(p, c.poly));
      if (c && !chosen.has(c.k)) { chosen.add(c.k); paint(c.k); }
    }, () => {}, { enableHighAccuracy: true, maximumAge: 3000, timeout: 20000 });
  });
  setTimeout(() => map.invalidateSize(), 60);
  sum();
})();
