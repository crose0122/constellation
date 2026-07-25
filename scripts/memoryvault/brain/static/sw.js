/* Minimal service worker: makes the Constellation installable as an app.
   Network-first everything — the library lives on the LAN, staleness is
   worse than a spinner. */
self.addEventListener("install", (e) => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(clients.claim()));
self.addEventListener("fetch", (e) => { /* passthrough */ });
