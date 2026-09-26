/* Which field is the phone in? Field contours are [[lat, lon], …]. Shared by the tally's home map and the new-trip form. */
(function () {
  const R = 6371000, rad = d => d * Math.PI / 180;
  function inside(p, poly) {
    let c = false;
    for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
      const [yi, xi] = poly[i], [yj, xj] = poly[j];
      if (((yi > p[0]) !== (yj > p[0])) && (p[1] < (xj - xi) * (p[0] - yi) / (yj - yi) + xi)) c = !c;
    }
    return c;
  }
  function dist(p, poly) {              // metres from the point to the contour edge
    const k = Math.cos(rad(p[0])), xy = q => [rad(q[1]) * k * R, rad(q[0]) * R], P = xy(p);
    let best = Infinity;
    for (let i = 0; i < poly.length; i++) {
      const A = xy(poly[i]), B = xy(poly[(i + 1) % poly.length]);
      const dx = B[0] - A[0], dy = B[1] - A[1], L = dx * dx + dy * dy;
      let t = L ? ((P[0] - A[0]) * dx + (P[1] - A[1]) * dy) / L : 0; t = Math.max(0, Math.min(1, t));
      best = Math.min(best, Math.hypot(P[0] - A[0] - t * dx, P[1] - A[1] - t * dy));
    }
    return best;
  }
  // {field, metres (0 = inside)} or {field: null, nearest, metres} when farther than `limit`
  function locateField(p, fields, limit) {
    const hit = fields.find(f => inside(p, f.poly));
    if (hit) return { field: hit, metres: 0 };
    let best = null;
    fields.forEach(f => { const m = dist(p, f.poly); if (!best || m < best.m) best = { f, m }; });
    if (!best) return { field: null, nearest: null, metres: null };
    return best.m <= (limit || 500) ? { field: best.f, metres: Math.round(best.m) } : { field: null, nearest: best.f, metres: Math.round(best.m) };
  }
  window.spxFieldGeo = { inside, dist, locateField };
})();
