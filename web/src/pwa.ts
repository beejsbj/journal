/**
 * Registering the service worker, and the "Update available" prompt. A new build is a new `sw.js`; the browser
 * installs it beside the running one, and it waits until the person says so (`apply`), because activating it reloads
 * the page. Only in a production build on a page that can have a worker (a secure origin).
 */
import { useEffect, useState } from "preact/hooks";
import { Workbox } from "workbox-window";
import { setAuthLost } from "./auth";
import { reloadViews } from "./events";

let apply: (() => void) | null = null;
const listeners = new Set<(a: (() => void) | null) => void>();

/** Say that an update is waiting (`apply` installs it and reloads), or that none is. Exported for the tests. */
export function offerUpdate(next: (() => void) | null): void {
  apply = next;
  for (const l of [...listeners]) l(apply);
}

/** A function that installs the waiting update and reloads, or null when none is waiting. */
export function useUpdate(): (() => void) | null {
  const [value, setValue] = useState<(() => void) | null>(() => apply);
  useEffect(() => {
    const l = (a: (() => void) | null) => setValue(() => a);
    listeners.add(l);
    l(apply);
    return () => {
      listeners.delete(l);
    };
  }, []);
  return value;
}

/** How often an installed app left open asks the server whether there is a new build. */
export const UPDATE_CHECK_MS = 30 * 60 * 1000;

/** What the worker tells the page on its own: the session ended while it was showing a kept copy (a late 401). */
export function onWorkerMessage(data: { type?: string } | null | undefined): void {
  if (data?.type === "AUTH_LOST") setAuthLost(true);
}

export function registerWorker(): void {
  if (!import.meta.env.PROD || typeof navigator === "undefined" || !("serviceWorker" in navigator)) return;
  navigator.serviceWorker.addEventListener("message", (event) => onWorkerMessage(event.data));
  // On the first visit the page renders and reads before the worker has installed and claimed it, so those first answers
  // bypassed it and were not kept: when it takes control for the first time, the views read again, through it.
  const controlledAtStart = !!navigator.serviceWorker.controller;
  navigator.serviceWorker.addEventListener("controllerchange", () => {
    if (!controlledAtStart) reloadViews();
  });
  const wb = new Workbox("/sw.js", { scope: "/" });
  wb.addEventListener("waiting", () => {
    offerUpdate(() => {
      wb.addEventListener("controlling", () => window.location.reload());
      wb.messageSkipWaiting();
    });
  });
  void wb.register().then((reg) => {
    if (!reg) return;
    setInterval(() => void reg.update().catch(() => undefined), UPDATE_CHECK_MS);
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") void reg.update().catch(() => undefined);
    });
  });
}
