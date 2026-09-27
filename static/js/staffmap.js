/* People and machines on one map (director panel and the dashboard): a person is a photo with the full name above it,
   a machine its icon with the code; a click draws today's route. Refreshes every 30 s.
   opts: { el, av, json, people, center, list, fleet: { json, machines }, fields: [{code, poly, active}] } */
window.spxStaffMap = function (opts) {
  const esc = window.surxonEsc || (s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])));
  const map = L.map(opts.el, { attributionControl: false });
  if (window.spxBasemap) spxBasemap(map); else L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', { maxZoom: 19 }).addTo(map);
  map.setView(opts.center, 12);
  const marks = {}, mmarks = {}; let trackLine = null, fitted = false;
  const fieldLayer = L.layerGroup().addTo(map), people = L.layerGroup().addTo(map), machines = L.layerGroup().addTo(map);
  if (opts.fleet) L.control.layers(null, { 'Odamlar': people, 'Texnika': machines }, { position: 'topleft', collapsed: false }).addTo(map);
  const KIND = { traktor: '🚜', kombayn: '🌾', mashina: '🚚', telashka: '🛒' };
  const COLOR = { field: '#22c55e', road: '#3b82f6', idle: '#f59e0b', parked: '#94a3b8', offline: '#475569', none: '#475569', off: '#94a3b8' };
  const micon = m => L.divIcon({ className: 'sm-pin', iconSize: [44, 44], iconAnchor: [22, 22],
    html: `<span class="sm-name ${['offline', 'none'].includes(m.state) ? 'stale' : ''}">${esc(m.code)}${m.operator ? ' · ' + esc(m.operator) : ''}</span>`
        + `<span class="fl-pin s-${m.state}">${KIND[m.kind] || '🚜'}</span>` });
  let pts = { p: [], m: [], f: [] };
  // where the work is: busy fields + fresh people + machines (stale positions and idle fields don't pull the view away)
  function fit(force) {
    const all = pts.f.concat(pts.p, pts.m);
    if ((force || !fitted) && all.length) { map.fitBounds(all, { padding: [50, 50], maxZoom: 16 }); fitted = true; }
  }
  window.spxDrawFields = function (fields) {
    fieldLayer.clearLayers(); pts.f = [];
    (fields || []).forEach(f => {
      if (!f.poly || f.poly.length < 3) return;
      L.polygon(f.poly, { color: f.active ? '#ffd23f' : '#ffffff', weight: f.active ? 3 : 1, opacity: f.active ? 1 : .5,
        fillColor: f.active ? '#ffd23f' : '#ffffff', fillOpacity: f.active ? .18 : .04, interactive: false }).addTo(fieldLayer)
        .bindTooltip(esc(f.code), { permanent: !!f.active, direction: 'center', className: 'sm-flbl' });
      if (f.active) pts.f.push(...f.poly);
    });
  };
  if (opts.fields) window.spxDrawFields(opts.fields);
  map._spxFit = fit;
  function drawMachines(ms) {
    const seen = new Set(); pts.m = [];
    (ms || []).filter(m => m.lat != null).forEach(m => {
      seen.add(String(m.id)); if (!['offline', 'none'].includes(m.state)) pts.m.push([m.lat, m.lon]);
      const tip = `<b>${esc(m.code)}</b>${m.operator ? ' · ' + esc(m.operator) : ''}<br>${esc(m.label)}${m.field ? ' · ' + esc(m.field) : ''}<br>${m.at}`;
      if (!mmarks[m.id]) mmarks[m.id] = L.marker([m.lat, m.lon], { icon: micon(m) }).bindTooltip(tip, { direction: 'bottom', offset: [0, 22] })
        .on('click', () => showMachine(m.id)).addTo(machines);
      else mmarks[m.id].setLatLng([m.lat, m.lon]).setIcon(micon(m)).setTooltipContent(tip);
    });
    Object.keys(mmarks).forEach(k => { if (!seen.has(k)) { mmarks[k].remove(); delete mmarks[k]; } });
  }
  async function showMachine(id) {
    const r = await (await fetch(opts.fleet.json + '?iz=' + id, { headers: { 'X-Requested-With': 'fetch' } })).json();
    if (trackLine) trackLine.remove();
    const all = [];
    trackLine = L.layerGroup((r.track || []).map(run => { all.push(...run.pts);
      return L.polyline(run.pts, { color: COLOR[run.kind] || '#fff', weight: run.kind === 'field' ? 5 : 3.5, opacity: .95,
        dashArray: run.kind === 'off' ? '4 6' : null }).bindTooltip(`${run.from}–${run.to}`); })).addTo(map);
    if (all.length > 1) map.fitBounds(all, { padding: [40, 40] }); else if (mmarks[id]) map.setView(mmarks[id].getLatLng(), 16);
  }
  const icon = p => L.divIcon({ className: 'sm-pin', iconSize: [46, 46], iconAnchor: [23, 23],
    html: `<span class="sm-name ${p.stale ? 'stale' : ''}">${esc(p.name)}</span>`
        + `<span class="sm-av big ${p.stale ? 'stale' : ''}">${p.avatar ? `<img src="${opts.av + p.avatar}">` : esc(p.name.slice(0, 1))}</span>` });
  function draw(list) {
    const seen = new Set(); pts.p = [];
    list.forEach(p => {
      seen.add(p.key); if (!p.stale) pts.p.push([p.lat, p.lon]);
      const tip = `<b>${esc(p.name)}</b><br>${esc(p.role)}${p.field ? ' · ' + esc(p.field) : ''}<br>${p.at}${p.age_min > 1 ? ` (${p.age_min} daq oldin)` : ''}`;
      if (!marks[p.key]) marks[p.key] = L.marker([p.lat, p.lon], { icon: icon(p) }).bindTooltip(tip, { direction: 'bottom', offset: [0, 22] })
        .on('click', () => showTrack(p.key)).addTo(people);
      else { marks[p.key].setLatLng([p.lat, p.lon]).setIcon(icon(p)).setTooltipContent(tip); }
    });
    Object.keys(marks).forEach(k => { if (!seen.has(k)) { marks[k].remove(); delete marks[k]; } });
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
  if (opts.list) {
    opts.list.querySelectorAll('[data-key]').forEach(a => a.addEventListener('click', e => { e.preventDefault(); showTrack(a.dataset.key); window.scrollTo(0, 0); }));
    opts.list.querySelectorAll('[data-machine]').forEach(a => a.addEventListener('click', e => { e.preventDefault(); showMachine(a.dataset.machine); window.scrollTo(0, 0); }));
  }
  draw(opts.people || []);
  if (opts.fleet) drawMachines(opts.fleet.machines);
  fit();                                   // people and machines together in view
  if (!fitted) { const any = (opts.people || []).map(p => [p.lat, p.lon]).concat(((opts.fleet || {}).machines || []).filter(m => m.lat != null).map(m => [m.lat, m.lon]));
    if (any.length) { map.fitBounds(any, { padding: [50, 50], maxZoom: 15 }); fitted = true; } }
  map._spxDraw = (ppl, ms) => { draw(ppl || []); if (opts.fleet) drawMachines(ms); };
  if (opts.noPoll) return map;
  setInterval(async () => {
    try { const r = await (await fetch(opts.json, { headers: { 'X-Requested-With': 'fetch' } })).json(); draw(r.people); } catch (_) { }
    if (opts.fleet) try { const r = await (await fetch(opts.fleet.json, { headers: { 'X-Requested-With': 'fetch' } })).json(); drawMachines(r.machines); } catch (_) { }
    fit();
  }, 30000);
  return map;
};
