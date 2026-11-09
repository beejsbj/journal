import { describe, expect, it, vi } from "vitest";
import { nudge, openTarget, writeUrl } from "../src/swCore";
import { keyBytes, sameKey, support, turnOff, turnOn, type Device } from "../src/push";
import { Notifications } from "../src/routes/settings";
import { json, mount, must, stubFetch, tick, until } from "./helpers";

const KEY = "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4";
const INFO = { available: true, detail: "", public_key: KEY, when: "no-chat", active: true, subscriptions: 0 };

describe("what a push shows (the service worker)", () => {
  it("is the practice's title and due now, and a tap opens it to write", () => {
    const n = nudge({ title: "Morning journal", body: "due now", practice: "morning" });
    expect(n.title).toBe("Morning journal");
    expect(n.options.body).toBe("due now");
    expect(n.options.tag).toBe("practice:morning");
    expect(n.options.data).toEqual({ url: "/today?write=morning" });
  });

  it("says something plain when the payload is not what the server sends", () => {
    for (const data of [null, undefined, "text", 3, {}, { title: 5 }, { practice: ["x"] }]) {
      const n = nudge(data);
      expect(n.title, JSON.stringify(data)).toBe("Journal");
      expect(n.options.body).toBe("A practice is due");
      expect(n.options.data).toEqual({ url: "/today" });
    }
  });

  it("never takes an address from the payload: only a practice id, encoded", () => {
    const n = nudge({ title: "x", body: "due now", practice: "a/../b?c", url: "https://elsewhere.example/" });
    expect(n.options.data).toEqual({ url: "/today?write=a%2F..%2Fb%3Fc" });
    expect(writeUrl("evening")).toBe("/today?write=evening");
    expect(writeUrl(undefined)).toBe("/today");
    expect(writeUrl("")).toBe("/today");
  });
});

describe("a tap on the notification", () => {
  const ORIGIN = "https://journal.example";
  function clients(open: { url: string }[]) {
    const focused: string[] = [];
    const navigated: string[] = [];
    const opened: string[] = [];
    const windows = open.map((c) => ({
      url: c.url,
      focus: vi.fn(async () => { focused.push(c.url); return windows[0]; }),
      navigate: vi.fn(async (to: string) => { navigated.push(to); return null; }),
    }));
    return {
      api: {
        matchAll: vi.fn(async () => windows),
        openWindow: vi.fn(async (u: string) => { opened.push(u); return null; }),
      },
      focused, navigated, opened,
    };
  }

  it("brings an open page of the app forward and takes it there", async () => {
    const c = clients([{ url: `${ORIGIN}/entries` }]);
    await openTarget("/today?write=morning", c.api, ORIGIN);
    expect(c.navigated).toEqual([`${ORIGIN}/today?write=morning`]);
    expect(c.focused).toEqual([`${ORIGIN}/entries`]);
    expect(c.opened).toEqual([]);
  });

  it("opens the app when none is open", async () => {
    const c = clients([]);
    await openTarget("/today?write=morning", c.api, ORIGIN);
    expect(c.opened).toEqual([`${ORIGIN}/today?write=morning`]);
  });

  it("goes nowhere but this site", async () => {
    const c = clients([]);
    await openTarget("https://elsewhere.example/today", c.api, ORIGIN);
    await openTarget("//elsewhere.example/today", c.api, ORIGIN);
    await openTarget(42, c.api, ORIGIN);
    expect(c.opened).toEqual([`${ORIGIN}/today`, `${ORIGIN}/today`, `${ORIGIN}/today`]);
  });
});

describe("this device's subscription", () => {
  it("turns the server's key into bytes", () => {
    const bytes = keyBytes(KEY);
    expect(bytes.length).toBe(65);
    expect(bytes[0]).toBe(4);
  });

  it("knows when a subscription was made with another key", () => {
    const sub = { options: { applicationServerKey: keyBytes(KEY).buffer } } as unknown as PushSubscription;
    expect(sameKey(sub, KEY)).toBe(true);
    expect(sameKey(sub, KEY.replace("BCV", "BDV"))).toBe(false);
    expect(sameKey({ options: { applicationServerKey: null } } as unknown as PushSubscription, KEY)).toBe(false);
  });

  it("is possible only where the browser has push; on an iPhone, once the app is on the Home Screen", () => {
    expect(support({ worker: true, push: true, notification: true, standalone: false, ios: false })).toBe("ok");
    expect(support({ worker: true, push: false, notification: false, standalone: false, ios: true })).toBe("install-first");
    expect(support({ worker: true, push: false, notification: false, standalone: true, ios: true })).toBe("unsupported");
    expect(support({ worker: false, push: false, notification: false, standalone: false, ios: false })).toBe("unsupported");
  });

  function device(permission: NotificationPermission, existing: { endpoint: string } | null = null) {
    const calls: string[] = [];
    const made = {
      endpoint: "https://fcm.googleapis.com/fcm/send/new",
      toJSON: () => ({ endpoint: "https://fcm.googleapis.com/fcm/send/new", keys: { p256dh: KEY, auth: "BTBZMqHH6r4Tts7J_aSIgg" } }),
      unsubscribe: vi.fn(async () => { calls.push("unsubscribe new"); return true; }),
    };
    const old = existing && { ...existing, unsubscribe: vi.fn(async () => { calls.push("unsubscribe old"); return true; }) };
    const dev: Device = {
      permission: () => permission,
      requestPermission: vi.fn(async () => { calls.push("ask"); return permission; }),
      registration: async () => ({
        pushManager: {
          getSubscription: async () => old as unknown as PushSubscription,
          subscribe: vi.fn(async (opts: PushSubscriptionOptionsInit) => {
            calls.push(`subscribe ${opts.userVisibleOnly} ${(opts.applicationServerKey as Uint8Array).length}`);
            return made as unknown as PushSubscription;
          }),
        },
      }) as unknown as ServiceWorkerRegistration,
    };
    return { dev, calls };
  }

  it("asks for permission, subscribes with the server's key, and gives the server the subscription", async () => {
    const posted: unknown[] = [];
    stubFetch({ "/api/v1/push/subscriptions": (_u: URL, init?: RequestInit) => { posted.push(JSON.parse(String(init?.body))); return json({ subscriptions: 1 }, 201); } });
    const { dev, calls } = device("granted");
    expect(await turnOn(KEY, dev)).toBe("on");
    expect(calls).toEqual(["ask", "subscribe true 65"]);
    expect(posted).toEqual([{ endpoint: "https://fcm.googleapis.com/fcm/send/new", keys: { p256dh: KEY, auth: "BTBZMqHH6r4Tts7J_aSIgg" } }]);
  });

  it("does nothing more when permission is refused", async () => {
    const f = stubFetch({});
    const { dev, calls } = device("denied");
    expect(await turnOn(KEY, dev)).toBe("denied");
    expect(calls).toEqual(["ask"]);
    expect(f.calls).toEqual([]);
  });

  it("replaces a subscription this device already had", async () => {
    stubFetch({ "/api/v1/push/subscriptions": () => json({ subscriptions: 1 }, 201) });
    const { dev, calls } = device("granted", { endpoint: "https://fcm.googleapis.com/fcm/send/old" });
    await turnOn(KEY, dev);
    expect(calls).toEqual(["ask", "unsubscribe old", "subscribe true 65"]);
  });

  it("turning off ends the browser's subscription and tells the server", async () => {
    const deleted: unknown[] = [];
    stubFetch({ "/api/v1/push/subscriptions": (_u: URL, init?: RequestInit) => { deleted.push([init?.method, JSON.parse(String(init?.body))]); return new Response(null, { status: 204 }); } });
    const { dev, calls } = device("granted", { endpoint: "https://fcm.googleapis.com/fcm/send/old" });
    await turnOff(dev);
    expect(calls).toEqual(["unsubscribe old"]);
    expect(deleted).toEqual([["DELETE", { endpoint: "https://fcm.googleapis.com/fcm/send/old" }]]);
  });
});

describe("Settings: notifications", () => {
  it("says the server cannot push, and why, when the extra is missing", async () => {
    stubFetch({ "/api/v1/push": { ...INFO, available: false, public_key: null, detail: "Web Push needs the optional extra journal[push]" } });
    const host = mount(<Notifications device={null} env={{ worker: true, push: true, notification: true, standalone: true, ios: false }} />);
    const text = await until(() => must(host.querySelector("#push-h")?.closest("section")?.textContent?.match(/journal\[push\].*/)?.[0]));
    expect(text).toContain("journal[push]");
    expect(host.querySelector("button")).toBeNull();
  });

  it("an older server with no push route says so, quietly", async () => {
    stubFetch({});
    const host = mount(<Notifications device={null} env={{ worker: true, push: true, notification: true, standalone: true, ios: false }} />);
    await until(() => must(host.textContent?.match(/does not offer notifications/)));
  });

  it("on an iPhone in Safari, says to add the app to the Home Screen first", async () => {
    stubFetch({ "/api/v1/push": INFO });
    const host = mount(<Notifications device={null} env={{ worker: true, push: false, notification: false, standalone: false, ios: true }} />);
    await until(() => must(host.textContent?.match(/Home Screen/)));
    expect(host.querySelector("button")).toBeNull();
  });

  it("turns on from a tap, and then offers to turn off", async () => {
    stubFetch({ "/api/v1/push": INFO, "/api/v1/push/subscriptions": () => json({ subscriptions: 1 }, 201) });
    let subscribed: PushSubscription | null = null;
    const made = {
      endpoint: "https://fcm.googleapis.com/fcm/send/new",
      options: { applicationServerKey: keyBytes(KEY).buffer },
      toJSON: () => ({ endpoint: "https://fcm.googleapis.com/fcm/send/new", keys: { p256dh: KEY, auth: "BTBZMqHH6r4Tts7J_aSIgg" } }),
      unsubscribe: async () => true,
    } as unknown as PushSubscription;
    const ask = vi.fn(async () => "granted" as NotificationPermission);
    const dev: Device = {
      permission: () => "default",
      requestPermission: ask,
      registration: async () => ({
        pushManager: {
          getSubscription: async () => subscribed,
          subscribe: async () => { subscribed = made; return made; },
        },
      }) as unknown as ServiceWorkerRegistration,
    };
    const host = mount(<Notifications device={dev} env={{ worker: true, push: true, notification: true, standalone: true, ios: false }} />);
    const on = await until(() => must([...host.querySelectorAll("button")].find((b) => b.textContent === "Turn on notifications")));
    expect(ask).not.toHaveBeenCalled();   // permission is asked from the tap, never on load
    on.click();
    await until(() => must([...host.querySelectorAll("button")].find((b) => b.textContent === "Turn off")));
    expect(ask).toHaveBeenCalledTimes(1);
    expect(host.textContent).toContain("On for this device");
    await tick();
  });

  it("says when this journal pushes nothing (a chat, push.when no-chat)", async () => {
    stubFetch({ "/api/v1/push": { ...INFO, active: false } });
    const host = mount(<Notifications device={null} env={{ worker: true, push: true, notification: true, standalone: true, ios: false }} />);
    await until(() => must(host.textContent?.match(/push\.when/)));
  });
});
