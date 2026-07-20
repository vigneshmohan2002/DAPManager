// Only immutable, explicitly versioned presentation assets are cached here.
// Application HTML, authentication, API responses, and audio stay on network.
const STATIC_VERSION = "3";
const CACHE_NAME = `dapmanager-static-v${STATIC_VERSION}`;
const CACHEABLE_PATHS = new Set([
    "/static/tailwind.css",
    "/static/qrcode.min.js",
    "/static/pwa-register.js",
    "/static/manifest.json",
    "/static/manifest-player.webmanifest",
    "/static/manifest-satellite.webmanifest",
    "/static/icons/icon-192.png",
    "/static/icons/icon-512.png",
    "/static/icons/icon-maskable-512.png",
    "/static/icons/apple-touch-icon-180.png",
    "/static/icons/favicon-32.png",
]);
const PRECACHE_URLS = Array.from(
    CACHEABLE_PATHS,
    (path) => `${path}?v=${STATIC_VERSION}`,
);

function isCacheableRequest(request) {
    if (
        request.method !== "GET"
        || request.headers.has("Range")
        || request.headers.has("Authorization")
    ) {
        return false;
    }
    const url = new URL(request.url);
    if (url.origin !== self.location.origin) {
        return false;
    }
    if (
        request.mode === "navigate"
        || request.destination === "document"
        || url.pathname.startsWith("/api/")
        || url.pathname === "/auth"
    ) {
        return false;
    }
    return (
        url.search === `?v=${STATIC_VERSION}`
        && CACHEABLE_PATHS.has(url.pathname)
    );
}

function isCacheableResponse(response) {
    if (!response.ok || response.type !== "basic") {
        return false;
    }
    const contentType = (response.headers.get("Content-Type") || "").toLowerCase();
    return !contentType.includes("text/html");
}

self.addEventListener("install", (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then((cache) => cache.addAll(PRECACHE_URLS))
            .then(() => self.skipWaiting()),
    );
});

self.addEventListener("activate", (event) => {
    event.waitUntil(
        caches.keys()
            .then((names) => Promise.all(
                names
                    .filter((name) => (
                        name.startsWith("dapmanager-static-v")
                        && name !== CACHE_NAME
                    ))
                    .map((name) => caches.delete(name)),
            ))
            .then(() => self.clients.claim()),
    );
});

self.addEventListener("fetch", (event) => {
    if (!isCacheableRequest(event.request)) {
        return;
    }
    event.respondWith((async () => {
        const cached = await caches.match(event.request);
        if (cached) {
            return cached;
        }
        const response = await fetch(event.request);
        if (isCacheableResponse(response)) {
            const cache = await caches.open(CACHE_NAME);
            await cache.put(event.request, response.clone());
        }
        return response;
    })());
});
