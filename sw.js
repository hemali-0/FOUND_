// FOUND_ service worker: lets the site install as an app.
// Network-first, so users always get the newest version; cached files are only a fallback.
const CACHE = 'found-v3';
const SHELL = ['/static/css/style.css', '/static/js/app.js', '/static/icons/icon-192.png'];

self.addEventListener('install', e => e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL))));
self.addEventListener('activate', e => e.waitUntil(
  caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
));
self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET' || e.request.url.includes('/api/')) return;  // never cache data
  e.respondWith(fetch(e.request).catch(() => caches.match(e.request)));
});