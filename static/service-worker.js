/* SURXON PAXTA service worker.
   Public static assets are cached. Of the logged-in pages only the tally's field pages (/dala…) are kept, so the
   work can go on without internet; they are deleted on logout / login, so the next person on a shared phone never
   sees them. API responses and photos are never stored.
   Offline data entry is handled by the IndexedDB queue in app.js, not by this cache. */
const VERSION = 'surxon-static-v4';
const PAGES = 'surxon-field-pages';        // the tally's own field pages, so they open without internet
const ASSETS = [
  '/static/css/app.css', '/static/js/app.js', '/static/icons.svg',
  '/static/img/logo-light.png', '/static/img/logo-dark.png', '/static/img/hero-field.jpg',
  '/static/img/sidebar-cotton.jpg', '/static/img/trailer.jpg', '/static/img/icon-192.png', '/offline'
];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(VERSION).then(c => c.addAll(ASSETS)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  // removes the old v1 cache that stored private pages
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== VERSION && k !== PAGES).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;
  if (url.pathname.startsWith('/static/')) {
    // network first for static files (a new version is used right after an update); the cache is only the offline
    // fallback. Files are versioned (?v=…), so a cached copy of an old version is never picked for a new page.
    e.respondWith(caches.open(VERSION).then(async cache => {
      try {
        const res = await fetch(req);
        if (res.ok) cache.put(req, res.clone());
        return res;
      } catch (_) {
        return (await cache.match(req)) || (await cache.match(req, { ignoreSearch: true })) || Response.error();
      }
    }));
    return;
  }
  if (req.mode === 'navigate' && (url.pathname === '/logout' || url.pathname === '/login')) {
    // leaving / changing user: forget the cached field pages of the previous person
    e.respondWith(caches.delete(PAGES).then(() => fetch(req)).catch(() => caches.match('/offline')));
    return;
  }
  if ((req.mode === 'navigate' || req.headers.get('X-SPX-Prefetch')) && /^\/dala(\/|$)/.test(url.pathname) && !url.pathname.endsWith('.pdf')) {
    // field pages: network first; the last copy opens when there is no internet (entries go to the offline queue)
    e.respondWith(caches.open(PAGES).then(async cache => {
      try {
        const res = await fetch(req);
        if (res.ok && !res.redirected) cache.put(url.pathname, res.clone());
        return res;
      } catch (_) {
        return (await cache.match(url.pathname)) || (await caches.match('/offline'));
      }
    }));
    return;
  }
  if (req.mode === 'navigate') {
    // pages always come from the network; if it's down show the offline page
    e.respondWith(fetch(req).catch(() => caches.match('/offline')));
  }
});
