/* Vuma Field App service worker: app-shell caching and offline fallback. */
const CACHE_NAME = 'vuma-field-v1';
const APP_SHELL = [
    '/vuma/field/offline',
    '/vuma_field_app/static/src/css/field_app.css',
    '/vuma_field_app/static/src/js/field_app.js',
    '/vuma_field_app/static/img/icon.svg',
    '/vuma/field/manifest.json'
];

self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then((cache) => cache.addAll(APP_SHELL))
            .then(() => self.skipWaiting())
            .catch(() => self.skipWaiting())
    );
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((keys) => Promise.all(
            keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))
        )).then(() => self.clients.claim())
    );
});

self.addEventListener('fetch', (event) => {
    const request = event.request;
    if (request.method !== 'GET') {
        return;
    }
    const url = new URL(request.url);
    if (url.origin !== self.location.origin) {
        return;
    }
    if (request.mode === 'navigate') {
        event.respondWith(
            fetch(request).catch(() => caches.match('/vuma/field/offline'))
        );
        return;
    }
    if (url.pathname.startsWith('/vuma_field_app/static/')) {
        event.respondWith(
            caches.match(request).then((cached) => cached || fetch(request).then((response) => {
                const copy = response.clone();
                caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
                return response;
            }))
        );
    }
});
