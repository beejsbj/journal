/**
 * Whether this browser is paired, as far as the last answer showed. A 401 from any read flips it; pairing flips it
 * back. The store is the one piece of shared state the app has: views subscribe through `useAuthLost`.
 * Losing the session also drops every answer the service worker kept: nothing read under a session outlives it.
 */
import { useEffect, useState } from "preact/hooks";
import { clearOfflineData } from "./offline";

let lost = false;
const listeners = new Set<(lost: boolean) => void>();

export function setAuthLost(value: boolean): void {
  if (lost === value) return;
  lost = value;
  if (lost) void clearOfflineData();
  for (const l of [...listeners]) l(lost);
}

export function isAuthLost(): boolean {
  return lost;
}

export function useAuthLost(): boolean {
  const [value, setValue] = useState(lost);
  useEffect(() => {
    listeners.add(setValue);
    setValue(lost);
    return () => {
      listeners.delete(setValue);
    };
  }, []);
  return value;
}

/**
 * How the server last said this browser is signed in (`GET /session`'s `via`), kept across reloads so that, when a
 * read is refused, the page knows which screen helps. Under Tailscale identity there is nothing to pair: a refused
 * read means the identity could not be confirmed, and the pairing form would only mislead. Only the word is kept,
 * never the login. Unknown (null) until the server has said, and while it cannot (offline, an older server).
 */
export type Via = "tailscale" | "cookie" | "token";
const VIA_KEY = "journal:via";

export function lastVia(): Via | null {
  try {
    const v = localStorage.getItem(VIA_KEY);
    return v === "tailscale" || v === "cookie" || v === "token" ? v : null;
  } catch {
    return null;   // storage refused (a private window): the pairing screen, as before
  }
}

const viaListeners = new Set<(via: Via | null) => void>();

export function rememberVia(via: string | null | undefined): void {
  try {
    if (via === "tailscale" || via === "cookie" || via === "token") localStorage.setItem(VIA_KEY, via);
    else if (via === null) localStorage.removeItem(VIA_KEY);
  } catch {
    /* nothing kept: nothing to forget */
  }
  const now = lastVia();
  for (const l of [...viaListeners]) l(now);
}

/** `lastVia`, kept current: on a first visit the refused read can arrive before `/session` has answered. */
export function useLastVia(): Via | null {
  const [value, setValue] = useState(lastVia());
  useEffect(() => {
    viaListeners.add(setValue);
    setValue(lastVia());
    return () => {
      viaListeners.delete(setValue);
    };
  }, []);
  return value;
}
