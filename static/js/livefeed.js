/* Jonli kuzatuv: tap a card → the photo(s) big, or the video player (the video downloads only now). */
(function () {
  const MEDIA = window.SPX_MEDIA || '/media/';
  let box = null;
  function close() { if (box) { box.querySelectorAll('video').forEach(v => { v.pause(); v.removeAttribute('src'); v.load(); }); box.remove(); box = null; } }
  function open(list, i) {
    close();
    box = document.createElement('div'); box.className = 'lv-box';
    const m = list[i];
    let inner = '';
    if (m.kind === 'video') {
      inner = m.path ? `<video controls autoplay playsinline preload="none" ${m.thumb ? `poster="${MEDIA}${m.thumb}"` : ''} src="${MEDIA}${m.path}"></video>`
                     : `<div class="lv-note">${(m.note || 'Video serverga yuklanmagan — asli Telegram arxivida.').replace(/</g, '&lt;')}</div>`;
    } else inner = `<img src="${MEDIA}${m.path || m.thumb}" alt="">`;
    box.innerHTML = `<button class="lv-x" aria-label="Yopish">✕</button>${list.length > 1 ? `<button class="lv-prev">‹</button><button class="lv-next">›</button><span class="lv-cnt">${i + 1} / ${list.length}</span>` : ''}${inner}`;
    box.addEventListener('click', e => {
      if (e.target.closest('.lv-prev')) return open(list, (i - 1 + list.length) % list.length);
      if (e.target.closest('.lv-next')) return open(list, (i + 1) % list.length);
      if (e.target === box || e.target.closest('.lv-x')) close();
    });
    document.body.appendChild(box);
  }
  document.addEventListener('click', e => {
    const card = e.target.closest('.lv-card');
    if (!card) return;
    e.preventDefault();
    try { open(JSON.parse(card.dataset.lv), 0); } catch (_) { }
  });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') close(); });
})();
