/* “Mening kassam” two-step money forms: Tekshirish (server figures, nothing saved) → …tasdiqlash (saved once).
   Only a server answer counts as saved. Without internet the typed data stays on the page (and in this phone's
   storage), the screen says “HALI SAQLANMADI”, and a retry re-sends the same client_uuid, so it is stored once. */
(function () {
  const f = document.querySelector('form[data-hm]');
  if (!f) return;
  const q = s => f.querySelector(s);
  const csrf = () => (document.querySelector('meta[name=csrf-token]') || {}).content || '';
  const step1 = q('[data-hm-step1]'), review = q('[data-hm-review]'), acts = q('[data-hm-actions]'), msg = q('[data-hm-msg]');
  const okBtn = q('[data-hm-confirm]'), editBtn = q('[data-hm-edit]'), checkBtn = q('[data-hm-check]');
  const expect = q('input[name=expect]'), uuidI = q('input[name=client_uuid]');
  const KEY = 'hm:' + location.pathname;
  const okLabel = okBtn ? okBtn.innerHTML : '';

  function say(kind, text) { msg.hidden = !text; msg.className = 'hm-msg ' + (kind || ''); msg.textContent = text || ''; }
  function fields() { return [...f.querySelectorAll('input,select,textarea')].filter(i => i.name && i.name !== '_csrf' && i.type !== 'file'); }
  function saveDraft(pending) {
    try {
      const d = { __t: Date.now(), __pending: pending ? 1 : 0, __review: !review.hidden ? 1 : 0 };
      fields().forEach(i => { if (i.type === 'radio' || i.type === 'checkbox') { if (i.checked) d[i.name] = i.value; } else d[i.name] = i.value; });
      localStorage.setItem(KEY, JSON.stringify(d));
    } catch (_) { }
  }
  function clearDraft() { try { localStorage.removeItem(KEY); } catch (_) { } }
  function restore() {
    let d = null;
    try { d = JSON.parse(localStorage.getItem(KEY) || 'null'); } catch (_) { }
    if (!d || Date.now() - d.__t > 3 * 86400e3) return;
    fields().forEach(i => {
      if (!(i.name in d)) return;
      if (i.type === 'radio' || i.type === 'checkbox') i.checked = d[i.name] === i.value; else i.value = d[i.name];
    });
    document.dispatchEvent(new Event('hm:restored'));
    if (d.__pending) say('off', 'HALI SAQLANMADI: oldingi yuborish serverga yetgani noma’lum. “Tekshirish”ni bosing va qayta tasdiqlang — agar u saqlangan bo‘lsa, ikkinchi marta yozilmaydi.');
    else if (step1) say('info', 'Oldin yozilgan ma’lumot qaytarildi (hali saqlanmagan).');
  }
  setTimeout(restore, 0);   // after app.js gave the form a fresh client_uuid
  f.addEventListener('input', () => saveDraft(false));
  f.addEventListener('change', () => saveDraft(false));

  function body(withFiles) {
    document.dispatchEvent(new Event('hm:before'));
    const fd = new FormData(f);
    if (!withFiles) [...fd.keys()].forEach(k => { const v = fd.get(k); if (v instanceof File) fd.delete(k); });
    return fd;
  }
  async function post(url, fd) {
    const r = await fetch(url, { method: 'POST', body: fd, credentials: 'same-origin',
      headers: { 'X-Requested-With': 'fetch', 'Accept': 'application/json', 'X-CSRF-Token': csrf() } });
    let data = {};
    try { data = await r.json(); } catch (_) { data = { ok: false, error: 'Server javobi tushunarsiz (' + r.status + ').' }; }
    return { status: r.status, data };
  }
  function showReview(data) {
    review.innerHTML = data.html; expect.value = data.expect || '';
    if (step1) step1.hidden = true;
    review.hidden = false; acts.hidden = false; window.scrollTo(0, 0);
  }
  const offline = 'HALI SAQLANMADI — internet yo‘q. Yozganlaringiz telefonda turibdi; internet kelganda shu tugmani qayta bosing.';

  if (checkBtn) checkBtn.addEventListener('click', async () => {
    say('', '');
    if (!navigator.onLine) { say('off', 'Internet yo‘q — tekshirib bo‘lmaydi. Yozganlaringiz telefonda turibdi.'); return; }
    checkBtn.disabled = true;
    try {
      const { status, data } = await post(f.dataset.checkUrl, body(false));
      if (status === 401) { say('err', 'Sessiya tugagan — qayta kiring. Yozganlaringiz saqlanib turadi.'); return; }
      if (data.ok) showReview(data); else say('err', data.error || 'Xatolik');
    } catch (_) { say('off', 'Internet yo‘q — tekshirib bo‘lmaydi. Yozganlaringiz telefonda turibdi.'); }
    finally { checkBtn.disabled = false; }
  });

  if (editBtn) editBtn.addEventListener('click', () => { review.hidden = true; acts.hidden = true; if (step1) step1.hidden = false; say('', ''); });

  let busy = false;
  if (okBtn) okBtn.addEventListener('click', async () => {
    if (busy) return;
    busy = true; okBtn.disabled = true; okBtn.innerHTML = 'Saqlanmoqda…'; say('', '');
    saveDraft(true);
    try {
      const { status, data } = await post(f.dataset.confirmUrl, body(true));
      if (data.ok) { clearDraft(); say('ok', data.message || 'Saqlandi'); location.href = data.redirect || location.href; return; }
      if (status === 409 && data.stale) { showReview(data); saveDraft(false); say('warn', data.error); }
      else if (status === 401) say('err', 'Sessiya tugagan — qayta kiring. Hali saqlanmadi.');
      else { saveDraft(false); say('err', 'Saqlanmadi: ' + (data.error || 'xatolik')); }
    } catch (_) { say('off', offline); }
    busy = false; okBtn.disabled = false; okBtn.innerHTML = okLabel;
  });
  window.addEventListener('online', () => { if (!msg.hidden && msg.classList.contains('off')) say('info', 'Internet qaytdi — endi qayta bosing.'); });
})();
