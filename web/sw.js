/* Blanco OS — service worker.
   App shell cache-first, API network-first with a stale fallback so the deck
   still renders offline. Bump VERSION on any app-shell change; `activate`
   deletes every other cache. */
const VERSION = 'blanco-os-v17';
const APP_SHELL = [
  '/', '/index.html', '/notepad.html', '/manifest.json', '/icon.svg', '/favicon.ico',
  '/icons/icon-192.png', '/icons/icon-512.png', '/icons/icon-maskable-512.png',
];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(VERSION)
      // One bad URL must not fail the whole install, so each entry is
      // fetched on its own and failures are swallowed.
      .then(c => Promise.all(APP_SHELL.map(u => c.add(u).catch(() => {}))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== VERSION).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

/* Only full 200 same-origin basic responses are worth storing. Caching a 206
   (audio/video range request) poisons the cache and breaks media playback;
   caching opaque cross-origin responses just burns quota. */
function cacheable(res){
  return res && res.status === 200 && res.type === 'basic';
}

function putLater(req, res){
  const copy = res.clone();
  caches.open(VERSION).then(c => c.put(req, copy)).catch(() => {});
}

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;              // timer/task POSTs pass through

  const url = new URL(req.url);
  // Google Fonts, the YouTube IFrame API and googlevideo media segments are all
  // cross-origin — let the network own them entirely.
  if (url.origin !== self.location.origin) return;
  // EventSource isn't a fetch, but be explicit: never buffer the pulse stream.
  if (url.pathname.startsWith('/api/stream')) return;

  // API: network first, fall back to the last good response.
  if (url.pathname.startsWith('/api/')){
    e.respondWith(
      fetch(req)
        .then(res => { if (cacheable(res)) putLater(req, res); return res; })
        .catch(() => caches.match(req).then(hit => hit || Response.json(
          {offline: true}, {status: 503, headers: {'x-blanco-offline': '1'}})))
    );
    return;
  }

  // The detached notepad window is its own navigation; falling back to the
  // deck's shell would open the whole OS in a 520px popup.
  if (req.mode === 'navigate' && url.pathname === '/notepad.html'){
    e.respondWith(
      fetch(req)
        .then(res => { if (cacheable(res)) putLater('/notepad.html', res); return res; })
        .catch(() => caches.match('/notepad.html'))
    );
    return;
  }

  // Navigations always resolve to the app shell so a cold offline load renders.
  if (req.mode === 'navigate'){
    e.respondWith(
      fetch(req)
        .then(res => { if (cacheable(res)) putLater('/index.html', res); return res; })
        .catch(() => caches.match('/index.html').then(hit => hit || caches.match('/')))
    );
    return;
  }

  // Static assets: cache first, fill on miss.
  e.respondWith(
    caches.match(req).then(hit => hit || fetch(req).then(res => {
      if (cacheable(res)) putLater(req, res);
      return res;
    }))
  );
});
