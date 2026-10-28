/*
 * The kill switch: serve this as /sw.js when the server goes back to a build that has no service worker.
 *
 * A 404 on /sw.js does not remove an installed worker (the browser keeps it when an update check fails), so a phone
 * that installed the PWA would keep answering from its precached shell, in front of the old API. A script that *does*
 * answer is the way out: the browser installs it in place of the worker, and it empties every cache of this origin,
 * unregisters itself and reloads the open pages from the network. Nothing here is the journal's own: the origin holds
 * no other caches. See DEPLOY.md, "PWA", Rollback.
 */
self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      for (const name of await caches.keys()) await caches.delete(name);
      await self.registration.unregister();
      const pages = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      for (const page of pages) page.navigate(page.url).catch(() => undefined);
    })(),
  );
});
