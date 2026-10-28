/**
 * One shared `EventSource` on `/api/v1/events`, opened while any view listens. The browser reconnects a dropped
 * stream by itself, and a refused one is reopened here with a backoff; a view that wants fresh data on an event calls
 * its own `reload`. Where `EventSource` is missing (tests) it is a no-op.
 *
 * The stream outlives a view change: a route unmounts (no listener left) a moment before the next one mounts, and the
 * server only notices a closed stream at its next ping (up to 25 s) while holding one of its few slots, so closing
 * and reopening on every navigation would fill them. The stream is closed after `IDLE_MS` with nobody listening.
 */
import { useEffect, useRef } from "preact/hooks";
import { BASE, api } from "./api";

export type EventName = "entries-changed" | "ledger-changed" | "runs-changed" | "library-changed" | "tick";
const NAMES: EventName[] = ["entries-changed", "ledger-changed", "runs-changed", "library-changed", "tick"];

/** `reconnected`: the stream came back after a failure, so anything it announced meanwhile was missed. */
type Handler = (name: EventName | "reconnected") => void;
const handlers = new Set<Handler>();
let source: EventSource | null = null;
let retry: ReturnType<typeof setTimeout> | null = null;
let failures = 0;
let idle: ReturnType<typeof setTimeout> | null = null;   // closes the stream once nobody has listened for IDLE_MS
let gap = false;   // the stream broke at some point, whoever reopened it: events in between were missed

/** How long the stream stays open with no listener: longer than a route change, short enough to free the slot. */
export const IDLE_MS = 5000;

/** Wait before the next attempt: 1 s, doubling to 30 s. Exported for the test's timers. */
export const backoff = (failed: number): number => Math.min(30_000, 1000 * 2 ** Math.max(0, failed - 1));

function open(): void {
  if (source || retry || typeof EventSource === "undefined") return;
  const es = new EventSource(`${BASE}/events`);
  source = es;
  for (const name of NAMES) es.addEventListener(name, () => handlers.forEach((h) => h(name)));
  es.onopen = () => {
    if (gap) handlers.forEach((h) => h("reconnected"));
    gap = false;
    failures = 0;
  };
  // The browser retries a dropped connection itself, but a non-200 answer (a 502 while the unit restarts, a 401) closes
  // the stream for good: open a new one after a pause for as long as anyone listens.
  es.onerror = () => {
    gap = true;   // also when the browser is retrying by itself (readyState CONNECTING): the next open reads again
    if (es.readyState !== EventSource.CLOSED || source !== es) return;
    es.close();
    source = null;
    failures += 1;
    // A refused stream may be a 401 (the session ended while a view stays open); the stream itself cannot say, and no
    // other request is made on an idle page. Any authenticated read does: `get` marks the browser unpaired on a 401.
    void api.status().catch(() => undefined);
    if (handlers.size === 0) return;
    retry = setTimeout(() => {
      retry = null;
      if (handlers.size > 0) open();
    }, backoff(failures));
  };
}

/** Close the stream and forget its state: what the idle timer does, and what a test resets between cases. */
export function closeEvents(): void {
  if (idle) clearTimeout(idle);
  idle = null;
  source?.close();
  source = null;
  if (retry) clearTimeout(retry);
  retry = null;
  failures = 0;
  gap = false;
}

// A connection that comes back is a gap closed: views read again, and a stream waiting out its backoff opens now.
if (typeof window !== "undefined") {
  window.addEventListener("online", () => {
    if (handlers.size === 0) return;
    if (retry) clearTimeout(retry);
    retry = null;
    open();
    handlers.forEach((h) => h("reconnected"));
  });
}

/** Make every view that listens read again, as after a reconnect (the worker has just taken control: see `pwa.ts`). */
export function reloadViews(): void {
  handlers.forEach((h) => h("reconnected"));
}

function subscribe(h: Handler): () => void {
  handlers.add(h);
  if (idle) clearTimeout(idle);
  idle = null;
  open();
  return () => {
    handlers.delete(h);
    if (handlers.size === 0 && !idle) idle = setTimeout(closeEvents, IDLE_MS);
  };
}

/** Call `reload` when any of `names` arrives. */
export function useLive(names: EventName[], reload: () => void): void {
  const want = useRef(names);
  want.current = names;
  const cb = useRef(reload);
  cb.current = reload;
  useEffect(() => subscribe((n) => (n === "reconnected" || want.current.includes(n)) && cb.current()), []);
}
