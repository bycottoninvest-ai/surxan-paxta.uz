/* SURXON PAXTA — shared UI behaviour: navigation, search, photo previews, reason dialogs,
   double-submit protection and the offline queue for field forms. */
(function () {
  'use strict';
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const csrf = () => ($('meta[name=csrf-token]') || {}).content || '';
  const uuid = () => (crypto.randomUUID ? crypto.randomUUID() :
    'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => { const r = Math.random() * 16 | 0; return (c === 'x' ? r : (r & 3 | 8)).toString(16); }));

  // ---- navigation drawer / dropdowns
  document.addEventListener('click', e => {
    const open = e.target.closest('[data-open-nav]');
    if (open) { e.preventDefault(); document.body.classList.add('nav-open'); return; }
    if (e.target.closest('[data-close-nav]')) { document.body.classList.remove('nav-open'); return; }
    const tog = e.target.closest('[data-toggle]');
    $$('.dropdown.open').forEach(d => { if (d.contains(e.target)) return; if (!tog || d !== $(tog.dataset.toggle)) d.classList.remove('open'); });
    if (tog && !e.target.closest('.dropdown')) { const t = $(tog.dataset.toggle); t.classList.toggle('open'); if (t.classList.contains('collapse') && t.classList.contains('open')) t.scrollIntoView({ block: 'nearest', behavior: 'smooth' }); }
    if (!e.target.closest('[data-search]')) $$('.search-results').forEach(r => r.classList.remove('open'));
  });

  // ---- live clock
  const clock = $('[data-clock]');
  if (clock) setInterval(() => { const d = new Date(); clock.textContent = d.toTimeString().slice(0, 5); }, 15000);

  // ---- global search
  $$('[data-search]').forEach(box => {
    const input = $('input', box), out = $('.search-results', box);
    let t;
    input.addEventListener('input', () => {
      clearTimeout(t);
      const q = input.value.trim();
      if (q.length < 2) { out.classList.remove('open'); return; }
      t = setTimeout(async () => {
        try {
          const r = await fetch('/qidiruv?format=json&q=' + encodeURIComponent(q), { headers: { 'X-Requested-With': 'fetch' } });
          const data = await r.json();
          out.innerHTML = data.results.length ? data.results.map(x =>
            `<a href="${x.url}"><span class="t">${esc(x.type)}</span><span><b>${esc(x.label)}</b><br><span class="small muted">${esc(x.sub || '')}</span></span></a>`).join('')
            : '<div class="empty">Hech narsa topilmadi</div>';
          out.classList.add('open');
        } catch (_) { /* offline */ }
      }, 220);
    });
    input.addEventListener('keydown', e => { if (e.key === 'Enter') location.href = '/qidiruv?q=' + encodeURIComponent(input.value); });
  });
  function esc(s) { return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
  window.surxonEsc = esc;

  // ---- image previews
  document.addEventListener('change', e => {
    const input = e.target.closest('input[type=file][data-preview]');
    if (!input) return;
    const box = input.parentElement.querySelector('.previews');
    if (!box) return;
    box.innerHTML = '';
    Array.from(input.files).slice(0, 12).forEach(f => { const img = new Image(); img.src = URL.createObjectURL(f); box.appendChild(img); });
  });

  // ---- lightbox
  document.addEventListener('click', e => {
    const img = e.target.closest('img[data-full]');
    const lb = $('#lightbox');
    if (img && lb) { $('img', lb).src = img.dataset.full; $('.cap', lb).textContent = img.dataset.cap || ''; lb.classList.add('open'); return; }
    if (e.target.closest('#lightbox')) lb.classList.remove('open');
  });

  // ---- reason dialog for void / correction actions
  document.addEventListener('click', e => {
    const b = e.target.closest('[data-reason-form]');
    const dlg = $('#reason-dialog');
    if (b && dlg) {
      const f = $('form', dlg);
      f.action = b.dataset.reasonForm;
      $('[data-title]', dlg).textContent = b.dataset.title || 'Sabab';
      f.reset();
      dlg.showModal();
    }
    if (e.target.closest('[data-close-dialog]')) e.target.closest('dialog').close();
  });

  // ---- confirmations
  document.addEventListener('submit', e => {
    const f = e.target;
    if (f.dataset.confirm && !confirm(f.dataset.confirm)) { e.preventDefault(); return; }
  }, true);

  // ---- idempotency keys + double-submit protection on every POST form
  function arm(form) { $$('[data-uuid]', form).forEach(i => { i.value = uuid(); }); }
  $$('form').forEach(arm);
  document.addEventListener('submit', e => {
    const f = e.target;
    // getAttribute: a field named 'method'/'action' inside the form would shadow f.method / f.action
    if ((f.getAttribute('method') || '').toLowerCase() !== 'post' || e.defaultPrevented) return;
    if (f.dataset.offline) return; // handled below
    const btn = f.querySelector('button:not([type=button])');
    if (f.dataset.busy) { e.preventDefault(); return; }
    f.dataset.busy = '1';
    // the clicked button's name=value (e.g. role=archive, action=test_…) must still be sent after it is disabled:
    // disabled controls are left out of the submitted form, so carry it in a hidden field
    const sub = e.submitter;
    if (sub && sub.name) {
      const h = document.createElement('input');
      h.type = 'hidden'; h.name = sub.name; h.value = sub.value; h.dataset.submitter = '1';
      f.appendChild(h);
    }
    if (btn) { btn.disabled = true; btn.dataset.label = btn.innerHTML; btn.innerHTML = 'Saqlanmoqda…'; }
  });
  window.addEventListener('pageshow', () => $$('form[data-busy]').forEach(f => { delete f.dataset.busy; $$('input[data-submitter]', f).forEach(i => i.remove()); const b = f.querySelector('button[disabled]'); if (b) { b.disabled = false; if (b.dataset.label) b.innerHTML = b.dataset.label; } }));

  // =================================================================== offline queue
  // Field forms marked data-offline are sent with fetch. If the network is down the
  // whole form (including photos) is stored in IndexedDB and replayed later with the
  // same client_uuid, so the server stores it exactly once.
  const DB = 'surxon-offline', STORE = 'queue';
  function idb() {
    return new Promise((res, rej) => {
      const r = indexedDB.open(DB, 1);
      r.onupgradeneeded = () => r.result.createObjectStore(STORE, { keyPath: 'id' });
      r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error);
    });
  }
  async function qAll() { const db = await idb(); return new Promise(res => { const t = db.transaction(STORE).objectStore(STORE).getAll(); t.onsuccess = () => res(t.result || []); t.onerror = () => res([]); }); }
  async function qPut(item) { const db = await idb(); return new Promise(res => { const tx = db.transaction(STORE, 'readwrite'); tx.objectStore(STORE).put(item); tx.oncomplete = res; }); }
  async function qDel(id) { const db = await idb(); return new Promise(res => { const tx = db.transaction(STORE, 'readwrite'); tx.objectStore(STORE).delete(id); tx.oncomplete = res; }); }

  const syncEl = $('#sync-state');
  const userId = document.body.dataset.user || '';
  async function showSync(extra) {
    if (!syncEl) return;
    let n = 0;
    try { n = (await qAll()).filter(x => x.user === userId).length; } catch (_) { }
    syncEl.className = 'sync show';
    if (!navigator.onLine) { syncEl.classList.add('offline'); syncEl.textContent = 'Internet yo‘q' + (n ? ` · navbatda ${n} ta` : ''); }
    else if (n) { syncEl.classList.add('pending'); syncEl.textContent = `Yuborilmoqda: ${n} ta`; }
    else if (extra) { syncEl.classList.add('ok'); syncEl.textContent = extra; setTimeout(() => syncEl.classList.remove('show'), 4000); }
    else syncEl.classList.remove('show');
  }

  function formToEntries(form) {
    const fd = new FormData(form);
    const entries = [];
    for (const [k, v] of fd.entries()) {
      if (v instanceof File) { if (v.size) entries.push([k, v, v.name]); } else entries.push([k, v]);
    }
    return entries;
  }
  function entriesToFD(entries) {
    const fd = new FormData();
    entries.forEach(([k, v, name]) => name ? fd.append(k, v, name) : fd.append(k, v));
    return fd;
  }
  // A weak signal is worse than none: the request hangs. After SEND_TIMEOUT the entry goes to the queue instead
  // (if the server did get it after all, the replay with the same client_uuid is recognised and not stored twice).
  const SEND_TIMEOUT = 15000;
  async function send(action, entries) {
    const ctl = window.AbortController ? new AbortController() : null;
    const timer = ctl ? setTimeout(() => ctl.abort(), SEND_TIMEOUT) : null;
    let r;
    try {
      r = await fetch(action, { method: 'POST', body: entriesToFD(entries), credentials: 'same-origin', signal: ctl ? ctl.signal : undefined,
        headers: { 'X-Requested-With': 'fetch', 'Accept': 'application/json', 'X-CSRF-Token': csrf() } });
    } finally { if (timer) clearTimeout(timer); }
    let data = {};
    try { data = await r.json(); } catch (_) { }
    return { status: r.status, data };
  }

  let flushing = false;
  async function flush() {
    if (flushing || !navigator.onLine) return showSync();
    flushing = true;
    let sent = 0, failed = [];
    try {
      for (const item of (await qAll()).sort((a, b) => a.at - b.at)) {     // in the order they were entered
        if (item.user !== userId) continue;   // never replay another person's entries
        try {
          const { status, data } = await send(item.action, item.entries);
          if (status === 401) break;             // session expired: keep items, ask to log in
          if (data.ok || status === 422 || status === 403 || status === 400) {
            await qDel(item.id);
            if (data.ok) sent++; else failed.push(`${item.label}: ${data.error || status}`);
          }
        } catch (_) { break; }                   // still offline
      }
    } finally { flushing = false; }
    if (failed.length) alert('Navbatdagi ayrim yozuvlar saqlanmadi:\n' + failed.join('\n'));
    showSync(sent ? `${sent} ta yozuv yuborildi ✓` : '');
    if (sent && document.querySelector('[data-refresh-on-sync]')) setTimeout(() => location.reload(), 800);
  }
  window.addEventListener('online', flush);
  window.addEventListener('offline', () => showSync());
  if (window.indexedDB) { flush(); setInterval(flush, 30000); }

  document.addEventListener('submit', async e => {
    const form = e.target;
    if (!form.dataset.offline || e.defaultPrevented) return;
    e.preventDefault();
    if (form.dataset.busy) return;
    if (form.dataset.validate && window[form.dataset.validate] && !window[form.dataset.validate](form)) return;
    form.dataset.busy = '1';
    const btn = form.querySelector('button:not([type=button])');
    const label = btn ? btn.innerHTML : '';
    if (btn) { btn.disabled = true; btn.innerHTML = 'Saqlanmoqda…'; }
    const entries = formToEntries(form);
    const actionUrl = new URL(form.getAttribute('action') || location.href, location.href).href;
    const item = { id: uuid(), user: userId, action: actionUrl, entries, label: form.dataset.offline, at: Date.now() };
    const done = () => { delete form.dataset.busy; if (btn) { btn.disabled = false; btn.innerHTML = label; } };
    try {
      const { status, data } = await send(actionUrl, entries);
      if (status === 401) {        // logged out: keep the entry, it is sent after logging in again
        await qPut(item);
        alert('Sessiya tugagan. Yozuv telefonda saqlandi — qayta kiring, kirgandan keyin o‘zi yuboriladi.');
        location.href = '/login'; return;
      }
      if (!data.ok) { toast(data.error || 'Xatolik', 'error'); form.dispatchEvent(new CustomEvent('failed', { detail: data })); done(); return; }
      if (form.dataset.after === 'reset') {
        toast(data.message, 'success');
        form.reset(); arm(form); form.dispatchEvent(new CustomEvent('saved', { detail: data }));
        $$('.previews', form).forEach(p => p.innerHTML = '');
        done();
      } else { location.href = data.redirect || location.href; }
    } catch (_) {
      await qPut(item);
      toast('Internet yo‘q — yozuv NAVBATDA: serverga hali yetmagan. Internet kelganda o‘zi yuboriladi.', 'warn');
      form.reset(); arm(form); form.dispatchEvent(new CustomEvent('queued', { detail: { entries } }));
      $$('.previews', form).forEach(p => p.innerHTML = '');
      done(); showSync();
    }
  });

  function toast(msg, kind) {
    let box = $('#toast');
    if (!box) { box = document.createElement('div'); box.id = 'toast'; box.style.cssText = 'position:fixed;left:50%;transform:translateX(-50%);top:12px;z-index:120;max-width:92vw;width:460px'; document.body.appendChild(box); }
    const el = document.createElement('div');
    el.className = 'flash ' + (kind || 'info');
    el.style.boxShadow = '0 10px 30px rgba(7,31,64,.2)';
    el.textContent = msg;
    box.appendChild(el);
    setTimeout(() => el.remove(), kind === 'error' ? 7000 : 4000);
  }
  window.surxonToast = toast;
  window.surxonQueue = { all: async () => (await qAll()).filter(x => x.user === userId), flush };

  // ---- geolocation for photo forms (only if the user allows it)
  $$('form[data-geo]').forEach(f => {
    if (!navigator.geolocation) return;
    navigator.geolocation.getCurrentPosition(p => {
      const lat = f.querySelector('[name=lat]'), lon = f.querySelector('[name=lon]');
      if (lat) lat.value = p.coords.latitude.toFixed(6);
      if (lon) lon.value = p.coords.longitude.toFixed(6);
    }, () => { }, { timeout: 8000, maximumAge: 600000 });
  });

  // ---- logout clears private cached pages
  document.addEventListener('click', e => {
    if (e.target.closest('[data-logout]') && 'caches' in window) caches.keys().then(k => k.forEach(n => n.startsWith('surxon-private') && caches.delete(n)));
  });

  // ---- money inputs: whole so‘m, shown with spaces while typing (the server strips them)
  document.addEventListener('input', e => {
    const el = e.target.closest('[data-money]');
    if (!el) return;
    const digits = el.value.replace(/\D/g, '');
    el.value = digits ? Number(digits).toLocaleString('ru-RU').replace(/\u00a0/g, ' ') : '';
  });

  if ('serviceWorker' in navigator) navigator.serviceWorker.register('/service-worker.js').catch(() => { });
})();

// ---- nakladnoy PDF: share the FILE to the phone's share sheet (Telegram etc.), fall back to download.
// The PDF is fetched in advance: iOS Safari only opens the share sheet if share() runs straight from the tap.
(function () {
  const btns = document.querySelectorAll('[data-pdf-share]');
  if (!btns.length) return;
  btns.forEach(btn => {
    const url = btn.dataset.pdfShare, name = btn.dataset.pdfName || 'nakladnoy.pdf';
    let file = null;
    const canFiles = typeof navigator.canShare === 'function' && typeof File === 'function';
    if (canFiles) {
      fetch(url, { credentials: 'same-origin' }).then(r => r.ok ? r.blob() : null).then(b => {
        if (!b) return;
        const f = new File([b], name, { type: 'application/pdf' });
        if (navigator.canShare({ files: [f] })) { file = f; btn.hidden = false; }
      }).catch(() => {});
    }
    btn.addEventListener('click', () => {
      if (file) {
        navigator.share({ files: [file], title: name }).catch(err => {
          if (err && err.name !== 'AbortError') location.href = url + '?download=1';
        });
      } else {
        location.href = url + '?download=1';
      }
    });
  });
})();

// ---- open trip: brigade first, then only that brigade's fields (fields without a fixed brigade stay available)
(function () {
  document.querySelectorAll('[data-brig-select]').forEach(sel => {
    const form = sel.closest('form'), fields = form && form.querySelector('[data-brig-fields]');
    if (!fields) return;
    const apply = () => {
      const b = sel.value;
      let first = null;
      fields.querySelectorAll('option').forEach(o => {
        const ok = !b || !o.dataset.brig || o.dataset.brig === b;
        o.hidden = !ok; o.disabled = !ok;
        if (ok && !first && o.value) first = o;
      });
      if (fields.selectedOptions[0] && fields.selectedOptions[0].disabled) fields.value = first ? first.value : '';
    };
    sel.addEventListener('change', apply); apply();
  });
})();
