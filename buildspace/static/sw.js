// Copyright 2026 Reality Checkpoint
// SPDX-License-Identifier: Apache-2.0
// Minimal service worker for Buildspace PWA installability.
//
// Strategy:
//   - Cache the static app shell on install (index + CSS + JS + icons + CDN libs).
//   - /api/* and /ws always go to the network — never cache live data.
//   - Static assets: cache-first with network revalidation.
//   - Navigation requests: network-first, fall back to cached index.

const CACHE = "buildspace-v8";
const SHELL = [
  "/",
  "/static/app.css",
  "/static/app.js",
  "/static/manifest.json",
  "/static/icons/icon-180.png",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);

  // Never touch live endpoints.
  if (url.pathname.startsWith("/api/") || url.pathname === "/ws") return;

  // Navigation (loading the app shell): network-first so updates land, but
  // fall back to cached / if offline.
  if (req.mode === "navigate") {
    event.respondWith(
      fetch(req)
        .then((r) => {
          const copy = r.clone();
          caches.open(CACHE).then((cache) => cache.put("/", copy));
          return r;
        })
        .catch(() => caches.match("/")),
    );
    return;
  }

  // Static assets: cache-first, update cache in the background.
  if (url.origin === location.origin && url.pathname.startsWith("/static/")) {
    event.respondWith(
      caches.match(req).then((cached) => {
        const fetched = fetch(req)
          .then((r) => {
            const copy = r.clone();
            caches.open(CACHE).then((cache) => cache.put(req, copy));
            return r;
          })
          .catch(() => cached);
        return cached || fetched;
      }),
    );
  }
});
