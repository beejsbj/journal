/**
 * What the page knows about being offline: whether the last read came from the service worker's kept copy (and when
 * that copy was taken), whether the browser says it has a network, and how to drop everything kept when the session
 * ends. The worker's own rules are `swCore.ts`; the headers it sets are the contract between the two.
 */
import { useEffect, useState } from "preact/hooks";
import { CACHED_AT, DATA_PREFIX, FROM_CACHE } from "./swCore";

/** The time a read was kept at, when the worker answered it from its copy; null for an answer from the server. */
export function keptAt(headers: Pick<Headers, "get">): number | null {
  if (headers.get(FROM_CACHE) !== "stale") return null;
  const at = Number(headers.get(CACHED_AT));
  return Number.isFinite(at) && at > 0 ? at : null;
}

const pad = (n: number) => String(n).padStart(2, "0");
const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "offline, as of 09:12" - with the day ("Tue 14 Oct 09:12") when the copy is not from today. Local time. */
export function ribbonText(at: number, now: number = Date.now()): string {
  const d = new Date(at);
  const n = new Date(now);
  const time = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  const today = d.getFullYear() === n.getFullYear() && d.getMonth() === n.getMonth() && d.getDate() === n.getDate();
  return `offline, as of ${today ? "" : `${DAYS[d.getDay()]} ${d.getDate()} ${MONTHS[d.getMonth()]} `}${time}`;
}

export const OFFLINE_WRITE = "You're offline; this will not be saved until you're back.";

let asOf: number | null = null;
const listeners = new Set<(at: number | null) => void>();
/** The kept-copy time of the latest answer to each read of the view on screen (a read answered by the server is absent). */
const stale = new Map<string, number>();

function publish(): void {
  const next = stale.size ? Math.min(...stale.values()) : null;   // the oldest copy on screen is what the ribbon admits to
  if (asOf === next) return;
  asOf = next;
  for (const l of [...listeners]) l(asOf);
}

/**
 * Record where the latest answer to the read `key` (its path) came from: the kept copy (a time) or the server (null).
 * A view reads more than once; a fresh answer to one read must not hide that another came from a copy, so the ribbon
 * stands while any read of the view is stale and shows the oldest time.
 */
export function noteRead(at: number | null, key = ""): void {
  if (at === null) stale.delete(key);
  else stale.set(key, at);
  publish();
}

/** A new view starts with no read noted, and so does the end of the session. */
export function forgetReads(): void {
  stale.clear();
  publish();
}

export function staleAsOf(): number | null {
  return asOf;
}

/** The time of the kept copy the view on screen was read from, or null. */
export function useStaleAsOf(): number | null {
  const [value, setValue] = useState(asOf);
  useEffect(() => {
    listeners.add(setValue);
    setValue(asOf);
    return () => {
      listeners.delete(setValue);
    };
  }, []);
  return value;
}

/** Whether the browser believes it has a network. A hint only: a write still tries, and says what happened. */
export function isOnline(): boolean {
  return typeof navigator === "undefined" || navigator.onLine !== false;
}

export function useOnline(): boolean {
  const [online, setOnline] = useState(isOnline());
  useEffect(() => {
    const up = () => setOnline(true);
    const down = () => setOnline(false);
    window.addEventListener("online", up);
    window.addEventListener("offline", down);
    setOnline(isOnline());
    return () => {
      window.removeEventListener("online", up);
      window.removeEventListener("offline", down);
    };
  }, []);
  return online;
}

/**
 * Forget every kept answer: this page's caches directly, and the worker by message (it may hold a request in flight).
 * Called when the session ends - an unpair, or any 401 - so nothing read under a session outlives it.
 */
export async function clearOfflineData(): Promise<void> {
  forgetReads();
  try {
    if (typeof caches !== "undefined") {
      for (const name of await caches.keys()) if (name.startsWith(DATA_PREFIX)) await caches.delete(name);
    }
  } catch {
    /* no Cache API on this page (insecure origin): nothing was kept */
  }
  try {
    if (typeof navigator !== "undefined" && "serviceWorker" in navigator) {
      navigator.serviceWorker.controller?.postMessage({ type: "CLEAR_DATA" });
    }
  } catch {
    /* no worker to tell */
  }
}
