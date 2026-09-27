/* Staff on the map (director panel and the dashboard): photo in a circle with the full name above it; a click draws
   today's route. Refreshes every 30 s. opts: { el, av, json, people, center, list } */
window.spxStaffMap = function (opts) {
  const esc = window.surxonEsc || (s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])));
  const map = L.map(opts.el, { attributionControl: false });
  if (window.spxBasemap) spxBasemap(map); else L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', { maxZoom: 19 }).addTo(map);
  map.setView(opts.center, 12);
  const marks = {}; let trackLine = null, fitted = false;
  const icon = p => L.divIcon({ className: 'sm-pin', iconSize: [46, 46], iconAnchor: [23, 23],
    html: `<span class="sm-name ${p.stale ? 'stale' : ''}">${esc(p.name)}</span>`
        + `<span class="sm-av big ${p.stale ? 'stale' : ''}">${p.avatar ? `<img src="${opts.av + p.avatar}">` : esc(p.name.slice(0, 1))}</span>` });
  function draw(people) {
    const seen = new Set();
    people.forEach(p => {
      seen.add(p.key);
      const tip = `<b>${esc(p.name)}</b><br>${esc(p.role)}${p.field ? ' · ' + esc(p.field) : ''}<br>${p.at}${p.age_min > 1 ? ` (${p.age_min} daq oldin)` : ''}`;
      if (!marks[p.key]) marks[p.key] = L.marker([p.lat, p.lon], { icon: icon(p) }).bindTooltip(tip, { direction: 'bottom', offset: [0, 22] })
        .on('click', () => showTrack(p.key)).addTo(map);
      else { marks[p.key].setLatLng([p.lat, p.lon]).setIcon(icon(p)).setTooltipContent(tip); }
    });
    Object.keys(marks).forEach(k => { if (!seen.has(k)) { marks[k].remove(); delete marks[k]; } });
    if (!fitted && people.length) { map.fitBounds(people.map(p => [p.lat, p.lon]), { padding: [50, 50], maxZoom: 15 }); fitted = true; }
  }
  async function showTrack(key) {
    const r = await (await fetch(opts.json + '?iz=' + encodeURIComponent(key), { headers: { 'X-Requested-With': 'fetch' } })).json();
    if (trackLine) trackLine.remove();
    if (r.track && r.track.length > 1) {
      trackLine = L.layerGroup([L.polyline(r.track.map(t => [t[0], t[1]]), { color: '#38bdf8', weight: 4, opacity: .9 }),
        L.circleMarker([r.track[0][0], r.track[0][1]], { radius: 5, color: '#fff', fillColor: '#16a34a', fillOpacity: 1 }).bindTooltip('Boshlandi ' + r.track[0][2])]).addTo(map);
      map.fitBounds(r.track.map(t => [t[0], t[1]]), { padding: [40, 40] });
    } else if (marks[key]) map.setView(marks[key].getLatLng(), 16);
  }
  if (opts.list) opts.list.querySelectorAll('[data-key]').forEach(a => a.addEventListener('click', e => { e.preventDefault(); showTrack(a.dataset.key); window.scrollTo(0, 0); }));
  draw(opts.people || []);
  setInterval(async () => { try { const r = await (await fetch(opts.json, { headers: { 'X-Requested-With': 'fetch' } })).json(); draw(r.people); } catch (_) { } }, 30000);
  return map;
};
