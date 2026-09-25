/* Solyarka screens: camera QR scanning, a fresh camera photo (no gallery), and saving that only says
   OLINDI / BERILDI after the server answered. Without internet: “HALI SAQLANMADI”, retry sends the same key. */
(function () {
  const csrf = () => (document.querySelector('meta[name=csrf-token]') || {}).content || '';
  let stream = null;

  async function openCam(video) {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) throw new Error('Kamera faqat https manzilda ishlaydi.');
    stopCam();
    stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment', width: { ideal: 1280 } }, audio: false });
    video.srcObject = stream;
    await video.play();
  }
  function stopCam() { if (stream) { stream.getTracks().forEach(t => t.stop()); stream = null; } }
  window.addEventListener('pagehide', stopCam);

  async function scanLoop(video, onText, isDone) {
    let detect;
    if ('BarcodeDetector' in window) {
      const bd = new BarcodeDetector({ formats: ['qr_code'] });
      detect = async () => { const r = await bd.detect(video); return r[0] && r[0].rawValue; };
    } else {
      await new Promise((ok, bad) => { if (window.jsQR) return ok(); const s = document.createElement('script');
        s.src = '/static/vendor/jsqr/jsQR.min.js'; s.onload = ok; s.onerror = bad; document.head.appendChild(s); });
      const c = document.createElement('canvas'), ctx = c.getContext('2d', { willReadFrequently: true });
      detect = async () => {
        const w = video.videoWidth, h = video.videoHeight; if (!w) return null;
        const k = Math.min(1, 640 / w); c.width = w * k; c.height = h * k;
        ctx.drawImage(video, 0, 0, c.width, c.height);
        const r = jsQR(ctx.getImageData(0, 0, c.width, c.height).data, c.width, c.height);
        return r && r.data;
      };
    }
    const loop = async () => { if (isDone()) return; try { const t = await detect(); if (t) await onText(t); } catch (_) { } setTimeout(loop, 250); };
    loop();
  }

  function capture(video) {
    const w = video.videoWidth, h = video.videoHeight;
    const k = Math.min(1, 1600 / Math.max(w, h));
    const c = document.createElement('canvas'); c.width = Math.round(w * k); c.height = Math.round(h * k);
    c.getContext('2d').drawImage(video, 0, 0, c.width, c.height);
    return new Promise(res => c.toBlob(b => res(b), 'image/jpeg', 0.85));
  }

  async function post(url, fd) {
    const r = await fetch(url, { method: 'POST', body: fd, credentials: 'same-origin',
      headers: { 'X-Requested-With': 'fetch', 'Accept': 'application/json', 'X-CSRF-Token': csrf() } });
    let data = {};
    try { data = await r.json(); } catch (_) { data = { ok: false, error: 'Server javobi tushunarsiz (' + r.status + ').' }; }
    return { status: r.status, data };
  }
  function uuid() { return (crypto.randomUUID ? crypto.randomUUID() : 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => { const r = Math.random() * 16 | 0; return (c === 'x' ? r : (r & 3 | 8)).toString(16); })); }
  function say(el, kind, text) { el.hidden = !text; el.className = 'hm-msg ' + (kind || ''); el.textContent = text || ''; }
  const OFFLINE = 'HALI SAQLANMADI — internet yo‘q. Ma’lumot telefonda turibdi; internet kelganda tugmani qayta bosing (ikki marta yozilmaydi).';

  window.Fuel = { openCam, stopCam, scanLoop, capture, post, uuid, say, OFFLINE };
})();
