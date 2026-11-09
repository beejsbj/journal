/**
 * Web Push on this device: whether the browser can take notifications, and turning them on and off. The server keeps
 * one subscription per device (`POST`/`DELETE /api/v1/push/subscriptions`) and pushes a practice's title and "due now"
 * when its slot comes (`journal serve`, `serve/push.py`); the service worker shows it (`swCore.nudge`).
 *
 * Permission is asked only from a tap (`turnOn`), never on load: iOS refuses it otherwise, and a page that asks unbidden
 * is a page people block. On an iPhone or iPad push exists only in the installed app (Home Screen, iOS 16.4 or later).
 */
import { api } from "./api";

/** What the browser offers, read once (tests pass their own). */
export interface PushEnv {
  worker: boolean;
  push: boolean;
  notification: boolean;
  standalone: boolean;
  ios: boolean;
}

export type Support = "ok" | "install-first" | "unsupported";

export function browserEnv(): PushEnv {
  const nav = typeof navigator === "undefined" ? undefined : navigator;
  const win = typeof window === "undefined" ? undefined : window;
  const ua = nav?.userAgent ?? "";
  // iPadOS asks for the desktop site and says "Macintosh"; touch points tell it apart from a Mac
  const ios = /iPhone|iPad|iPod/.test(ua) || (/Macintosh/.test(ua) && (nav?.maxTouchPoints ?? 0) > 1);
  const standalone = !!win?.matchMedia?.("(display-mode: standalone)").matches
    || (nav as (Navigator & { standalone?: boolean }) | undefined)?.standalone === true;
  return {
    worker: !!nav && "serviceWorker" in nav,
    push: !!win && "PushManager" in win,
    notification: !!win && "Notification" in win,
    standalone,
    ios,
  };
}

export function support(env: PushEnv): Support {
  if (env.worker && env.push && env.notification) return "ok";
  // Safari on iOS has no PushManager until the app is on the Home Screen
  if (env.ios && !env.standalone) return "install-first";
  return "unsupported";
}

/** What `turnOn` and `turnOff` use of the browser (tests pass a fake). */
export interface Device {
  permission(): NotificationPermission;
  requestPermission(): Promise<NotificationPermission>;
  registration(): Promise<ServiceWorkerRegistration>;
}

/** This browser's device, or null when it cannot take notifications here. */
export function browserDevice(): Device | null {
  if (support(browserEnv()) !== "ok") return null;
  return {
    permission: () => Notification.permission,
    requestPermission: () => Notification.requestPermission(),
    registration: () => navigator.serviceWorker.ready,
  };
}

/** The server's public key (base64url, as `GET /push` gives it) as the bytes `subscribe` takes. */
export function keyBytes(b64url: string): Uint8Array {
  const b64 = b64url.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (b64url.length % 4)) % 4);
  const raw = atob(b64);
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

/** Whether a subscription was made with the server's current key (a new key leaves old subscriptions useless). */
export function sameKey(sub: PushSubscription, publicKey: string): boolean {
  const had = sub.options?.applicationServerKey;
  if (!had) return false;
  const a = new Uint8Array(had);
  const b = keyBytes(publicKey);
  return a.length === b.length && a.every((x, i) => x === b[i]);
}

/** This device's subscription, if it has one. */
export async function current(device: Device): Promise<PushSubscription | null> {
  return (await device.registration()).pushManager.getSubscription();
}

/** Ask for permission (call it from a tap), subscribe with the server's key and give the server the subscription.
 *  A subscription the device already had is replaced, so a key the server changed is picked up. */
export async function turnOn(publicKey: string, device: Device): Promise<"on" | "denied"> {
  const permission = await device.requestPermission();
  if (permission !== "granted") return "denied";
  const reg = await device.registration();
  const old = await reg.pushManager.getSubscription();
  if (old) await old.unsubscribe();
  const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(publicKey) as BufferSource });
  try {
    await api.subscribePush(sub.toJSON());
  } catch (e) {
    // the server did not keep it: no subscription in the browser either, or Settings would say On for nothing
    await sub.unsubscribe().catch(() => undefined);
    throw e;
  }
  // the one replaced, which the server may still count (it would wait for a push service's 410 to learn)
  if (old && old.endpoint !== sub.endpoint) await api.unsubscribePush(old.endpoint).catch(() => undefined);
  return "on";
}

/** End this device's subscription, in the browser and on the server. */
export async function turnOff(device: Device): Promise<void> {
  const sub = await current(device);
  if (!sub) return;
  const endpoint = sub.endpoint;
  await sub.unsubscribe();
  await api.unsubscribePush(endpoint);
}
