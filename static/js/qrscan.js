/* Reusable in-page QR scanner: spxQrScan(video, msgEl, onText) → stop(). BarcodeDetector where the browser has it,
   otherwise the small jsQR decoder (iPhone). onText gets every decoded string; return true to stop scanning. */
window.spxQrScan = function (video, msg, onText, jsqrUrl) {
  let stream = null, stopped = false;
  const say = t => { if (msg) msg.textContent = t; };
  function stop() { stopped = true; if (stream) stream.getTracks().forEach(t => t.stop()); stream = null; }
  (async () => {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) { say('Kamera faqat https manzilda ishlaydi — raqamni yozing.'); return; }
    try { stream = await navigator.mediaDevices.getUserMedia({video: {facingMode: 'environment'}, audio: false}); }
    catch (e) { say('Kameraga ruxsat berilmadi — raqamni yozing.'); return; }
    video.srcObject = stream; await video.play(); say('QR kodni ramka ichiga keltiring.');
    let detect;
    if ('BarcodeDetector' in window) {
      const bd = new BarcodeDetector({formats: ['qr_code']});
      detect = async () => { const r = await bd.detect(video); return r[0] && r[0].rawValue; };
    } else {
      await new Promise((ok, bad) => { const s = document.createElement('script'); s.src = jsqrUrl; s.onload = ok; s.onerror = bad; document.head.appendChild(s); });
      const c = document.createElement('canvas'), ctx = c.getContext('2d', {willReadFrequently: true});
      detect = async () => {
        const w = video.videoWidth, h = video.videoHeight; if (!w) return null;
        const k = Math.min(1, 640 / w); c.width = w * k; c.height = h * k; ctx.drawImage(video, 0, 0, c.width, c.height);
        const r = jsQR(ctx.getImageData(0, 0, c.width, c.height).data, c.width, c.height); return r && r.data;
      };
    }
    let last = '', lastAt = 0;
    const loop = async () => {
      if (stopped) return;
      try { const t = await detect(); const now = Date.now();
        if (t && (t !== last || now - lastAt > 3000)) { last = t; lastAt = now; if (navigator.vibrate) navigator.vibrate(100); if (onText(t) === true) { stop(); return; } } } catch (e) {}
      setTimeout(loop, 250);
    };
    loop();
    window.addEventListener('pagehide', stop);
  })();
  return stop;
};
