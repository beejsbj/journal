/**
 * What the service worker keeps and what it never does, as plain functions over `fetch` and `CacheStorage` so the
 * rules are tested without a browser (`tests/swcore.test.ts`). `sw.ts` only wires them to the worker's events.
 *
 * The rules, in one allow-list:
 *  - the read views' answers (`today`, `entries`, an entry, `days`, `library`, `reflections`) are *network first*:
 *    the server's answer is used and kept when it is a 200; only when the network fails, takes longer than
 *    `NETWORK_TIMEOUT_MS` or the server answers 5xx does the kept copy stand in, marked so the page can say "offline,
 *    as of 09:12";
 *  - `/media/<name>` is kept only as a whole 200 no larger than `MEDIA_CAP_BYTES`, and a ranged request (what an
 *    audio element sends) is never answered from here;
 *  - everything else under `/api` is left alone: writes, `pair`, `logout`, `uploads`, `events` (the SSE stream),
 *    `settings`, `status`, `doctor`, `runs`, `insights`. An unlisted route is *not* cached: nothing is kept by default;
 *  - nothing that needs the session outlives it: a 401 from a kept route (even one that lands after the time limit,
 *    when the page was already shown the kept copy), and any `logout` request, empty the data caches (the page does the
 *    same on its side, `offline.ts`). An answer that was in flight when the caches were emptied is not stored when it
 *    lands: it was read under the session that has just ended.
 */

export const API = "/api/v1/";
export const DATA_CACHE = "journal-data-v1";
export const MEDIA_CACHE = "journal-media-v1";
/** Prefix of every cache that holds answers; the shell's precache (Workbox's own names) is not one of them. */
export const DATA_PREFIX = "journal-";

export const MAX_DATA_ENTRIES = 200;
export const MAX_MEDIA_ENTRIES = 20;
export const MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000;
export const MEDIA_CAP_BYTES = 2 * 1024 * 1024;
export const NETWORK_TIMEOUT_MS = 5000;

/** Response headers the worker sets on an answer taken from the cache (the page reads them: `offline.ts`). */
export const FROM_CACHE = "x-journal-cache";
export const CACHED_AT = "x-journal-cached-at";

export type Rule = "data" | "media" | "logout" | "never";

const DATA_PATHS = [
  /^today$/,
  /^days$/,
  /^entries$/,
  /^entries\/[^/]+$/,
  /^library$/,
  /^library\/[^/]+$/,
  /^reflections$/,
];

/** How the worker treats a request. `never` means: do not intercept, let the browser go to the network. */
export function classify(method: string, url: URL): Rule {
  if (!url.pathname.startsWith(API)) return "never";
  const rest = url.pathname.slice(API.length);
  if (rest === "logout") return "logout";   // any method: the data goes before the request does
  if (method.toUpperCase() !== "GET") return "never";
  if (/^media\/[^/]+$/.test(rest)) return "media";
  return DATA_PATHS.some((p) => p.test(rest)) ? "data" : "never";
}

/** What the worker needs from its environment; tests pass fakes. */
export interface Env {
  caches: CacheStorage;
  fetch: (request: Request) => Promise<Response>;
  now: () => number;
  /** Resolves after `ms`; the default is `setTimeout`. */
  sleep?: (ms: number) => Promise<void>;
  /** Tell the open pages something the worker learned after it had answered (`AUTH_LOST`: a late 401). The returned
   *  promise is awaited inside `defer`, so the worker is not stopped before the message is out. */
  notify?: (message: string) => void | Promise<void>;
  /** Keep working after the answer is sent (the worker's `waitUntil`); without it the answer waits for the store. */
  defer?: (work: Promise<unknown>) => void;
}

/**
 * Counts the times the caches were emptied. A request notes it before it goes to the network and stores its answer
 * only if it is unchanged when the answer lands (`store`): a read that was in flight across a logout or a 401 would
 * otherwise put his words back after the session that read them ended. Per worker instance, which is right: a
 * request does not outlive the instance that made it.
 */
let generation = 0;

/** Drop every cache that holds answers: the page's logout, a 401, and the `CLEAR_DATA` message end here. */
export async function clearData(caches: CacheStorage): Promise<void> {
  generation++;
  for (const name of await caches.keys()) {
    if (name.startsWith(DATA_PREFIX)) await caches.delete(name);
  }
}

async function stamp(res: Response, at: number, fromCache: boolean): Promise<Response> {
  const headers = new Headers(res.headers);
  headers.set(CACHED_AT, String(at));
  if (fromCache) headers.set(FROM_CACHE, "stale");
  return new Response(await res.arrayBuffer(), { status: res.status, statusText: res.statusText, headers });
}

/** Store a copy of `res` (the caller still hands `res` itself to the page), unless the caches were emptied since `gen`. */
async function store(env: Env, cacheName: string, max: number, request: Request, res: Response, gen: number): Promise<void> {
  if (gen !== generation) return;
  const copy = res.clone();
  const work = keep(env, cacheName, max, request, copy, gen).catch(() => undefined);   // a full or refusing cache is no error
  if (env.defer) env.defer(work);
  else await work;
}

async function keep(env: Env, cacheName: string, max: number, request: Request, res: Response, gen: number): Promise<void> {
  const cache = await env.caches.open(cacheName);
  const stamped = await stamp(res, env.now(), false);
  if (gen !== generation) return;
  await cache.delete(request.url);   // a new answer goes to the back of the line, so the oldest is the first key
  await cache.put(request.url, stamped);
  if (gen !== generation) {   // emptied while the put was under way
    await cache.delete(request.url);
    return;
  }
  const keys = await cache.keys();
  for (const old of keys.slice(0, Math.max(0, keys.length - max))) await cache.delete(old);
}

async function kept(env: Env, cacheName: string, request: Request): Promise<Response | undefined> {
  const cache = await env.caches.open(cacheName);
  const hit = await cache.match(request.url, { ignoreVary: true });
  if (!hit) return undefined;
  const at = Number(hit.headers.get(CACHED_AT));
  if (!Number.isFinite(at) || env.now() - at > MAX_AGE_MS) {
    await cache.delete(request.url);
    return undefined;
  }
  return stamp(hit, at, true);
}

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

/** Network first, the kept copy when the network cannot answer (see the header comment). */
export async function networkFirst(request: Request, env: Env): Promise<Response> {
  const gen = generation;
  const net = env.fetch(request);
  const gaveUp = Symbol("timeout");
  let res: Response | undefined;
  try {
    const first = await Promise.race([net, (env.sleep ?? sleep)(NETWORK_TIMEOUT_MS).then(() => gaveUp)]);
    if (first === gaveUp) {
      const copy = await kept(env, DATA_CACHE, request);
      if (copy) {   // slow, and there is something to show: show it, and keep the answer when it does come
        const late = net.then(async (r) => {
          if (r.status === 401) {   // the session ended while the kept copy was on screen: the page must hear of it
            await clearData(env.caches);
            await env.notify?.("AUTH_LOST");
          } else if (r.status === 200) {
            await store(env, DATA_CACHE, MAX_DATA_ENTRIES, request, r, gen);
          }
        });
        if (env.defer) env.defer(late.catch(() => undefined));
        else late.catch(() => undefined);
        return copy;
      }
      res = await net;         // nothing to show: keep waiting for the server
    } else {
      res = first as Response;
    }
  } catch {
    const copy = await kept(env, DATA_CACHE, request);
    if (copy) return copy;
    throw new TypeError("offline and nothing kept");   // the page sees a failed fetch, as without a worker
  }
  if (res.status === 401) {
    await clearData(env.caches);
    return res;
  }
  if (res.ok && res.status === 200) {
    await store(env, DATA_CACHE, MAX_DATA_ENTRIES, request, res, gen);
    return res;
  }
  if (res.status >= 500) {
    const copy = await kept(env, DATA_CACHE, request);
    if (copy) return copy;
  }
  return res;
}

/** Audio: whole and small only; a `Range` request is the browser's own player and goes straight to the server. */
export async function media(request: Request, env: Env): Promise<Response> {
  if (request.headers.has("range")) {
    const res = await env.fetch(request);
    if (res.status === 401) {   // the audio element's own request: the page's reads have not seen it
      await clearData(env.caches);
      await env.notify?.("AUTH_LOST");
    }
    return res;
  }
  const copy = await kept(env, MEDIA_CACHE, request);
  if (copy) return copy;
  const gen = generation;
  const res = await env.fetch(request);
  if (res.status === 401) {
    await clearData(env.caches);
    return res;
  }
  const size = Number(res.headers.get("content-length"));
  if (res.status === 200 && Number.isFinite(size) && size > 0 && size <= MEDIA_CAP_BYTES) {
    await store(env, MEDIA_CACHE, MAX_MEDIA_ENTRIES, request, res, gen);
  }
  return res;
}

/** The logout: the answers go first, so a failed request (offline) still leaves nothing behind, and again after it. */
export async function logout(request: Request, env: Env): Promise<Response> {
  await clearData(env.caches);
  try {
    return await env.fetch(request);
  } finally {
    await clearData(env.caches);   // a read begun while the logout was on its way carried the old cookie too
  }
}

/** The worker's answer to a request, or `undefined` when it does not handle it. */
export function respond(request: Request, env: Env): Promise<Response> | undefined {
  switch (classify(request.method, new URL(request.url))) {
    case "data":
      return networkFirst(request, env);
    case "media":
      return media(request, env);
    case "logout":
      return logout(request, env);
    default:
      return undefined;
  }
}
