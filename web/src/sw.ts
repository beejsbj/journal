/**
 * The service worker (built by `vite-plugin-pwa`, `injectManifest`): Workbox precaches the shell, versioned by the
 * build's content hashes; the rules for the API are `swCore.ts`. It is served from `/` with `Cache-Control: no-cache`,
 * so its scope is the whole site and a new build is noticed at the next load.
 *
 * Typed by hand: the project compiles with the DOM library, and the worker library would clash with it.
 */
import { cleanupOutdatedCaches, createHandlerBoundToURL, precacheAndRoute } from "workbox-precaching";
import { NavigationRoute, registerRoute } from "workbox-routing";
import { clearData, respond } from "./swCore";

interface Scope {
  __WB_MANIFEST: Array<string | { url: string; revision: string | null }>;
  addEventListener(type: string, listener: (event: any) => void): void;
  skipWaiting(): Promise<void>;
  clients: { claim(): Promise<void>; matchAll(): Promise<Array<{ postMessage(message: unknown): void }>> };
  caches: CacheStorage;
}
declare const self: Scope;

precacheAndRoute(self.__WB_MANIFEST);
cleanupOutdatedCaches();

// Every page that is not the API answers the precached shell; the client routes it (`/entries/...`, `/pair`).
registerRoute(new NavigationRoute(createHandlerBoundToURL("/index.html"), { denylist: [/^\/api\//, /^\/healthz/] }));

self.addEventListener(
  "fetch",
  (event: { request: Request; respondWith(r: Promise<Response>): void; waitUntil(p: Promise<unknown>): void }) => {
    const answer = respond(event.request, {
      caches: self.caches,
      fetch: (request) => fetch(request),
      now: () => Date.now(),
      defer: (work) => event.waitUntil(work),
      notify: (type) => self.clients.matchAll().then((all) => all.forEach((c) => c.postMessage({ type }))),
    });
    if (answer) event.respondWith(answer);
  },
);

// A new worker waits until the person taps "Update available" (pwa.ts), so a reload never surprises a draft.
self.addEventListener("message", (event: { data?: { type?: string }; waitUntil(p: Promise<unknown>): void }) => {
  if (event.data?.type === "SKIP_WAITING") void self.skipWaiting();
  if (event.data?.type === "CLEAR_DATA") event.waitUntil(clearData(self.caches));
});

self.addEventListener("activate", (event: { waitUntil(p: Promise<unknown>): void }) => {
  event.waitUntil(self.clients.claim());
});
