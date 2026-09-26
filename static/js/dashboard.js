/* Dashboard widgets: harvest chart, hand/combine donut, fields map, weather. */
(function () {
  'use strict';
  const fmt = n => Math.round(n).toLocaleString('ru-RU').replace(/,/g, ' ');
  const GREEN = '#1e9e4a', ORANGE = '#f59a23', NAVY = '#1b3a8a';

  // ---- harvest chart
  const seriesEl = document.getElementById('series-data');
  const canvas = document.getElementById('harvest-chart');
  if (seriesEl && canvas && window.Chart) {
    const all = JSON.parse(seriesEl.textContent);
    Chart.defaults.font.family = 'Roboto, system-ui, sans-serif';
    Chart.defaults.color = '#5a6f88';
    let chart;
    function draw(mode) {
      const s = all[mode];
      const hourly = mode === 'hourly';
      if (chart) chart.destroy();
      const datasets = hourly ? [
        { type: 'line', label: 'Qo‘l terimi', data: s.hand, borderColor: GREEN, backgroundColor: 'rgba(30,158,74,.15)', fill: true, tension: .35, pointRadius: 0 },
        { type: 'line', label: 'Kombayn', data: s.combine, borderColor: ORANGE, backgroundColor: 'rgba(245,154,35,.12)', fill: true, tension: .35, pointRadius: 0 }
      ] : [
        { type: 'line', label: 'Tarozi netto', data: s.net, borderColor: NAVY, backgroundColor: NAVY, tension: .3, pointRadius: 3, order: 0 },
        { type: 'bar', label: 'Qo‘l terimi', data: s.hand, backgroundColor: GREEN, stack: 'f', borderRadius: 2, order: 1 },
        { type: 'bar', label: 'Kombayn', data: s.combine, backgroundColor: ORANGE, stack: 'f', borderRadius: 2, order: 1 }
      ];
      chart = new Chart(canvas, {
        data: { labels: s.labels, datasets },
        options: {
          maintainAspectRatio: false, interaction: { mode: 'index', intersect: false },
          plugins: { legend: { position: 'bottom', labels: { usePointStyle: true, boxWidth: 8 } },
            tooltip: { callbacks: { label: c => `${c.dataset.label}: ${fmt(c.parsed.y)} kg` } } },
          scales: { x: { stacked: !hourly, grid: { display: false } },
            y: { stacked: !hourly, beginAtZero: true, ticks: { callback: v => fmt(v) }, grid: { color: '#edf2f8' } } }
        }
      });
    }
    draw('daily');
    document.querySelectorAll('[data-chart-tabs] button').forEach(b => b.addEventListener('click', () => {
      document.querySelectorAll('[data-chart-tabs] button').forEach(x => x.classList.toggle('on', x === b));
      draw(b.dataset.mode);
    }));
  }

  // ---- donut
  const donut = document.getElementById('type-donut');
  if (donut && window.Chart) {
    const hand = +donut.dataset.hand, comb = +donut.dataset.combine, empty = !(hand + comb);
    new Chart(donut, {
      type: 'doughnut',
      data: { labels: ['Qo‘l terimi', 'Kombayn'], datasets: [{ data: empty ? [1, 0] : [hand, comb], backgroundColor: empty ? ['#e3eaf3', '#e3eaf3'] : [GREEN, ORANGE], borderWidth: 0 }] },
      options: { cutout: '68%', maintainAspectRatio: false, plugins: { legend: { display: false }, tooltip: { enabled: !empty } } },
      plugins: [{ id: 'center', afterDraw(c) {
        const { ctx, chartArea: a } = c; const x = (a.left + a.right) / 2, y = (a.top + a.bottom) / 2;
        ctx.save(); ctx.textAlign = 'center'; ctx.fillStyle = '#0d2a4a'; ctx.font = '800 17px Roboto, sans-serif';
        ctx.fillText(donut.dataset.total + ' kg', x, y + 2); ctx.font = '12px Roboto, sans-serif'; ctx.fillStyle = '#6b7c93';
        ctx.fillText('Bugun', x, y + 20); ctx.restore(); } }]
    });
  }

  // ---- fields map (satellite imagery + polygons coloured by yield / brigade / state)
  const mapEl = document.getElementById('fields-map');
  const dataEl = document.getElementById('fields-data');
  if (mapEl && dataEl && window.L) {
    const fields = JSON.parse(dataEl.textContent || '[]');
    const c = (mapEl.dataset.center || '42.3167,59.6').split(',').map(Number);
    const map = L.map(mapEl, { zoomControl: true, attributionControl: true }).setView(c, 13);
    if (window.spxBasemap) spxBasemap(map);
    else L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', { maxZoom: 18 }).addTo(map);
    const yields = fields.filter(f => f.area).map(f => f.net / f.area);
    const maxY = Math.max(1, ...yields);
    const brigColors = ['#2f80ed', '#f28b1c', '#1e9e4a', '#6c4bd8', '#ee2f5b', '#13a5b5'];
    const brigs = [...new Set(fields.map(f => f.brigadier))];
    let layer = L.layerGroup().addTo(map);
    let legend;
    function colour(f, mode) {
      if (mode === 'picked') return '#9aa8b8';
      if (mode === 'brig') return brigColors[brigs.indexOf(f.brigadier) % brigColors.length];
      if (mode === 'state') return f.active ? '#2f80ed' : (f.net ? '#1e9e4a' : '#9aa8b8');
      const y = f.area ? f.net / f.area : 0;
      if (!f.net) return '#9aa8b8';
      return y >= maxY * .9 ? '#1e9e4a' : y >= maxY * .75 ? '#f28b1c' : '#ee2f5b';
    }
    let labelled = [];
    const readJson = id => { try { return JSON.parse((document.getElementById(id) || {}).textContent || '[]'); } catch (_) { return []; } };
    const picked = readJson('picked-data'), trips = readJson('picked-trips');
    const syncLabels = () => { const on = map.getZoom() >= 15; labelled.forEach(p => { const t = p.getTooltip(); if (!t) return; on ? p.openTooltip() : p.closeTooltip(); }); };
    map.on('zoomend', syncLabels);
    function draw(mode) {
      layer.clearLayers(); labelled = [];
      const bounds = [];
      fields.forEach(f => {
        if (!f.poly) return;
        const col = colour(f, mode);
        const p = L.polygon(f.poly, { color: col, weight: 2, fillColor: col, fillOpacity: .38 }).addTo(layer);
        const y = f.area ? Math.round(f.net / f.area) : 0;
        // labels only when zoomed in — at field-overview zoom they pile on top of each other
        p.bindTooltip(`<b>${f.name}</b><br>${f.area} ga${f.confirmed === false ? ' (xarita)' : ''}${f.net ? '<br>' + fmt(y) + ' kg/ga' + (f.confirmed === false ? ' ~' : '') : ''}`, { permanent: true, direction: 'center', className: 'map-label' });
        labelled.push(p);
        p.on('click', () => location.href = f.url);
        bounds.push(...f.poly);
      });
      const strip = document.getElementById('picked-strip');
      if (strip) strip.hidden = mode !== 'picked';
      if (mode === 'picked') {
        // where the cotton was weighed today (phone position of each weighing) + each trip's real photo under the map
        const esc = window.surxonEsc, dots = [];
        const show = lid => {
          dots.forEach(d => d.m.setStyle(lid && d.lid !== lid ? { radius: 4, fillOpacity: .25 } : { radius: lid ? 7 : 5, fillOpacity: .95 }));
          const pb = picked.filter(pt => !lid || pt.lid === lid).map(pt => [pt.lat, pt.lon]);
          if (pb.length) map.fitBounds(pb, { padding: [30, 30], maxZoom: 17 });
          strip && strip.querySelectorAll('.pt').forEach(b => b.classList.toggle('on', +b.dataset.lid === lid));
        };
        picked.forEach(pt => dots.push({ lid: pt.lid, m: L.circleMarker([pt.lat, pt.lon], { radius: 5, color: '#fff', weight: 1, fillColor: '#ffd23f', fillOpacity: .95 })
          .bindTooltip(`${esc(pt.field || '')} · ${esc(pt.trip || '')}<br>${fmt(pt.kg)} kg · ${pt.t}`).on('click', () => show(pt.lid)).addTo(layer) }));
        if (strip) {
          strip.innerHTML = trips.length ? trips.map(t => `<button type="button" class="pt" data-lid="${t.id}">
              ${t.thumb ? `<img src="${esc(t.thumb)}" alt="" loading="lazy">` : '<span class="noimg">rasm yo‘q</span>'}
              <div class="tx"><b>${esc(t.trailer)}</b> · ${esc(t.field || '')}<br>${fmt(t.kg)} kg · ${esc(t.t || '')}${t.pts ? '' : '<br><span style="color:#b45309">joylashuv yo‘q</span>'}</div></button>`).join('')
            : '<div class="empty-note">Bu kunda reys yo‘q.</div>';
          strip.querySelectorAll('.pt').forEach(b => b.addEventListener('click', () => { const lid = +b.dataset.lid; show(b.classList.contains('on') ? 0 : lid); }));
        }
        show(0);
      } else if (bounds.length) map.fitBounds(bounds, { padding: [12, 12] });
      syncLabels();
      if (legend) legend.remove();
      legend = L.control({ position: 'topright' });
      legend.onAdd = () => {
        const d = L.DomUtil.create('div', 'legend map-legend');
        const items = mode === 'brig' ? brigs.map((b, i) => [brigColors[i % brigColors.length], b || '—'])
          : mode === 'state' ? [['#2f80ed', 'Terim davom etmoqda'], ['#1e9e4a', 'Terim bo‘lgan'], ['#9aa8b8', 'Hali boshlanmagan']]
          : mode === 'picked' ? [['#ffd23f', picked.length ? `Tortish joyi (${picked.length} ta)` : 'Bu kunda joylashuvli tortish yo‘q']]
          : [['#1e9e4a', 'Yuqori hosil'], ['#f28b1c', 'O‘rtacha hosil'], ['#ee2f5b', 'Past hosil'], ['#9aa8b8', 'Ma’lumot yo‘q']];
        d.innerHTML = items.map(([col, t]) => `<div><i style="background:${col}"></i>${window.surxonEsc(t)}</div>`).join('');
        return d;
      };
      legend.addTo(map);
      if (!bounds.length) {
        const n = L.control({ position: 'bottomleft' });
        n.onAdd = () => { const d = L.DomUtil.create('div', 'map-legend'); d.innerHTML = 'Dala chegaralari hali chizilmagan. Dalalar → dala → “Xaritada chizish”.'; return d; };
        n.addTo(map);
      }
    }
    draw('yield');
    // trailers on the way to the punkt: an estimated position between where the trip was picked and the punkt
    (function transit() {
      const t = readJson('transit-data');
      if (!t || !t.stations) return;
      const lay = L.layerGroup().addTo(map), esc = window.surxonEsc;
      const skew = Date.parse(t.now.replace(' ', 'T')) - Date.now();
      t.stations.forEach(s => L.marker([s.lat, s.lon], { icon: L.divIcon({ className: 'tr-punkt', html: '🏭', iconSize: [30, 30] }) })
        .bindTooltip(esc(s.name), { permanent: true, direction: 'bottom', className: 'map-label' }).addTo(lay));
      const moving = (t.trips || []).filter(x => x.start && x.end && x.minutes);
      const items = moving.map(x => {
        L.polyline([x.start, x.end], { color: '#fbbf24', weight: 2.5, dashArray: '6 6', opacity: .9 }).addTo(lay);
        const m = L.marker(x.start, { zIndexOffset: 1000, icon: L.divIcon({ className: 'tr-tractor', html: '<span>🚜</span>', iconSize: [34, 34] }) })
          .bindTooltip('', { direction: 'top' }).addTo(lay);
        return { x, m, sent: Date.parse(x.sent_at.replace(' ', 'T')) };
      });
      function tick() {
        const now = Date.now() + skew;
        items.forEach(({ x, m, sent }) => {
          const f = Math.min(.97, Math.max(0, (now - sent) / 60000 / x.minutes));
          m.setLatLng([x.start[0] + (x.end[0] - x.start[0]) * f, x.start[1] + (x.end[1] - x.start[1]) * f]);
          const left = Math.max(0, Math.round(x.minutes - (now - sent) / 60000));
          m.setTooltipContent(`<b>${esc(x.trailer)}</b> · ${esc(x.field || '')} → ${esc(x.station || 'punkt')}<br>${fmt(x.kg)} kg · ≈ ${x.eta} da keladi${left ? ` (${left} daq)` : ''}`);
        });
      }
      tick(); if (items.length) setInterval(tick, 5000);
      // make sure the punkt and the moving trailers are in view together with the fields
      const b = map.getBounds();
      t.stations.forEach(s => b.extend([s.lat, s.lon]));
      moving.forEach(x => b.extend(x.start));
      if (t.stations.length) map.fitBounds(b, { padding: [20, 20] });
    })();
    document.querySelectorAll('[data-map-tabs] button').forEach(b => b.addEventListener('click', () => {
      document.querySelectorAll('[data-map-tabs] button').forEach(x => x.classList.toggle('on', x === b));
      draw(b.dataset.mode);
    }));
  }

  // ---- weather (open-meteo, no key; silently skipped offline)
  const w = document.querySelector('[data-weather]');
  if (w) {
    fetch(`https://api.open-meteo.com/v1/forecast?latitude=${w.dataset.lat}&longitude=${w.dataset.lon}&current=temperature_2m`)
      .then(r => r.json()).then(d => { const t = d.current && d.current.temperature_2m; if (t != null) w.querySelector('[data-temp]').textContent = Math.round(t) + '°C'; })
      .catch(() => { });
  }
})();
