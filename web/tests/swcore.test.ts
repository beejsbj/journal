import { beforeEach, describe, expect, it } from "vitest";
import {
  CACHED_AT, DATA_CACHE, FROM_CACHE, MAX_AGE_MS, MAX_DATA_ENTRIES, MEDIA_CACHE, MEDIA_CAP_BYTES, clearData, classify,
  respond, type Env,
} from "../src/swCore";

/** An in-memory `CacheStorage`: enough of the Cache API for the worker's rules (keys in insertion order). */
class FakeCache {
  entries = new Map<string, Response>();
  async match(key: RequestInfo | URL) {
    return this.entries.get(typeof key === "string" ? key : (key as Request).url)?.clone();
  }
  async put(key: RequestInfo | URL, res: Response) {
    this.entries.set(typeof key === "string" ? key : (key as Request).url, res);
  }
  async delete(key: RequestInfo | URL) {
    return this.entries.delete(typeof key === "string" ? key : (key as Request).url);
  }
  async keys() {
    return [...this.entries.keys()].map((u) => new Request(u));
  }
}
class FakeStorage {
  stores = new Map<string, FakeCache>();
  async open(name: string) {
    if (!this.stores.has(name)) this.stores.set(name, new FakeCache());
    return this.stores.get(name) as unknown as Cache;
  }
  async keys() {
    return [...this.stores.keys()];
  }
  async delete(name: string) {
    return this.stores.delete(name);
  }
  peek(name: string): string[] {
    return [...(this.stores.get(name)?.entries.keys() ?? [])];
  }
}

const ORIGIN = "https://host.example:8790";   // any origin, any port: the rules never look at either
const req = (path: string, init?: RequestInit) => new Request(ORIGIN + path, init);
const json = (body: unknown, status = 200, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", ...headers } });

let storage: FakeStorage;
let clock: number;
let online: boolean;
let answers: Map<string, () => Response>;
let sent: string[];
let env: Env;

beforeEach(() => {
  storage = new FakeStorage();
  clock = Date.UTC(2026, 9, 15, 9, 12);
  online = true;
  answers = new Map();
  sent = [];
  env = {
    caches: storage as unknown as CacheStorage,
    now: () => clock,
    fetch: async (r) => {
      sent.push(`${r.method} ${new URL(r.url).pathname}${new URL(r.url).search}`);
      if (!online) throw new TypeError("Failed to fetch");
      const make = answers.get(new URL(r.url).pathname + new URL(r.url).search) ?? answers.get(new URL(r.url).pathname);
      return make ? make() : json({ error: "not-found" }, 404);
    },
    sleep: () => new Promise(() => undefined),   // the network limit never fires unless a test says so
  };
});

describe("what the worker handles", () => {
  const rule = (method: string, path: string) => classify(method, new URL(ORIGIN + path));

  it("keeps the read views, on any origin", () => {
    for (const path of [
      "/api/v1/today", "/api/v1/entries", "/api/v1/entries?q=lighthouse&limit=20", "/api/v1/entries/2026-10-14-evening",
      "/api/v1/days", "/api/v1/library", "/api/v1/library/gratitude", "/api/v1/reflections?week=2026-10-12",
    ]) {
      expect(rule("GET", path), path).toBe("data");
    }
    expect(classify("GET", new URL("http://localhost:8790/api/v1/today"))).toBe("data");
  });

  it("never keeps a write, whatever its path", () => {
    for (const method of ["POST", "PUT", "PATCH", "DELETE"]) {
      for (const path of ["/api/v1/today", "/api/v1/entries", "/api/v1/entries/x/blocks", "/api/v1/library/x", "/api/v1/runs"]) {
        expect(rule(method, path), `${method} ${path}`).toBe("never");
      }
    }
  });

  it("never touches pairing, the event stream, uploads or the routes that are not read views", () => {
    for (const path of [
      "/api/v1/pair", "/api/v1/events", "/api/v1/uploads", "/api/v1/settings", "/api/v1/status", "/api/v1/doctor",
      "/api/v1/runs", "/api/v1/runs/abc", "/api/v1/insights/weeks", "/api/v1/healthz", "/api/v1/entries/x/meta",
      "/api/v1/library/x/send", "/api/v1/nothing-we-know", "/healthz",
    ]) {
      expect(rule("GET", path), path).toBe("never");
    }
    expect(respond(req("/api/v1/events"), env)).toBeUndefined();
    expect(respond(req("/api/v1/pair", { method: "POST", body: "{}" }), env)).toBeUndefined();
  });

  it("leaves the shell's files and the page's own routes to the precache", () => {
    for (const path of ["/", "/entries", "/assets/index-abc.js", "/sw.js", "/manifest.webmanifest", "/icons/icon-192.png"]) {
      expect(rule("GET", path), path).toBe("never");
    }
  });

  it("sees logout as its own rule, and audio as media", () => {
    expect(rule("POST", "/api/v1/logout")).toBe("logout");
    expect(rule("GET", "/api/v1/media/2026-10-14-evening.webm")).toBe("media");
    expect(rule("POST", "/api/v1/media/x.webm")).toBe("never");
  });
});

describe("network first, the kept copy when the network cannot answer", () => {
  it("answers from the server and keeps the answer", async () => {
    answers.set("/api/v1/today", () => json({ day: "2026-10-15" }));
    const res = await respond(req("/api/v1/today"), env)!;
    expect(await res.json()).toEqual({ day: "2026-10-15" });
    expect(res.headers.get(FROM_CACHE)).toBeNull();
    expect(storage.peek(DATA_CACHE)).toEqual([ORIGIN + "/api/v1/today"]);
  });

  it("uses the kept copy offline, marked, with the time it was kept", async () => {
    answers.set("/api/v1/entries/x", () => json({ stem: "x" }));
    await respond(req("/api/v1/entries/x"), env)!;
    online = false;
    clock += 3600_000;
    const res = await respond(req("/api/v1/entries/x"), env)!;
    expect(await res.json()).toEqual({ stem: "x" });
    expect(res.headers.get(FROM_CACHE)).toBe("stale");
    expect(Number(res.headers.get(CACHED_AT))).toBe(Date.UTC(2026, 9, 15, 9, 12));
  });

  it("fails as it would without a worker when nothing was kept", async () => {
    online = false;
    await expect(respond(req("/api/v1/today"), env)!).rejects.toBeInstanceOf(TypeError);
  });

  it("falls back to the kept copy when the server is down (5xx), not on a 404", async () => {
    answers.set("/api/v1/today", () => json({ day: "d" }));
    await respond(req("/api/v1/today"), env)!;
    answers.set("/api/v1/today", () => json({ error: "bad-gateway" }, 502));
    const res = await respond(req("/api/v1/today"), env)!;
    expect(res.headers.get(FROM_CACHE)).toBe("stale");
    answers.set("/api/v1/today", () => json({ error: "not-found" }, 404));
    const gone = await respond(req("/api/v1/today"), env)!;
    expect(gone.status).toBe(404);
    expect(gone.headers.get(FROM_CACHE)).toBeNull();
  });

  it("does not keep an error", async () => {
    answers.set("/api/v1/entries/missing", () => json({ error: "not-found" }, 404));
    answers.set("/api/v1/today", () => json({ error: "bad-gateway" }, 502));
    await respond(req("/api/v1/entries/missing"), env)!;
    await respond(req("/api/v1/today"), env)!;
    expect(storage.peek(DATA_CACHE)).toEqual([]);
  });

  it("shows the kept copy when the server is slow, and keeps the answer when it comes", async () => {
    answers.set("/api/v1/today", () => json({ day: "old" }));
    await respond(req("/api/v1/today"), env)!;
    let release!: () => void;
    const slow = new Promise<void>((r) => (release = r));
    const slowEnv: Env = {
      ...env,
      sleep: async () => undefined,   // the limit passes at once
      fetch: async () => {
        await slow;
        return json({ day: "new" });
      },
    };
    const res = await respond(req("/api/v1/today"), slowEnv)!;
    expect(res.headers.get(FROM_CACHE)).toBe("stale");
    expect(await res.json()).toEqual({ day: "old" });
    release();
    await new Promise((r) => setTimeout(r, 10));
    const fresh = await (await (await storage.open(DATA_CACHE)).match(ORIGIN + "/api/v1/today"))!.json();
    expect(fresh).toEqual({ day: "new" });
  });

  it("keeps waiting for the server when the limit passes and nothing is kept", async () => {
    const slowEnv: Env = {
      ...env,
      sleep: async () => undefined,
      fetch: async () => {
        await new Promise((r) => setTimeout(r, 5));
        return json({ day: "only" });
      },
    };
    expect(await (await respond(req("/api/v1/today"), slowEnv)!).json()).toEqual({ day: "only" });
  });

  it("drops a copy older than seven days instead of showing it", async () => {
    answers.set("/api/v1/today", () => json({ day: "d" }));
    await respond(req("/api/v1/today"), env)!;
    online = false;
    clock += MAX_AGE_MS + 1;
    await expect(respond(req("/api/v1/today"), env)!).rejects.toBeInstanceOf(TypeError);
    expect(storage.peek(DATA_CACHE)).toEqual([]);
  });

  it("keeps the last 200 answers, oldest out first", async () => {
    answers.set("/api/v1/entries/x", () => json({}));
    for (let i = 0; i < MAX_DATA_ENTRIES + 3; i++) await respond(req(`/api/v1/entries/e${i}`), { ...env, fetch: async () => json({ i }) })!;
    const kept = storage.peek(DATA_CACHE);
    expect(kept).toHaveLength(MAX_DATA_ENTRIES);
    expect(kept[0]).toBe(ORIGIN + "/api/v1/entries/e3");
    expect(kept.at(-1)).toBe(ORIGIN + `/api/v1/entries/e${MAX_DATA_ENTRIES + 2}`);
  });

  it("defers the keeping to the worker's waitUntil when it has one", async () => {
    answers.set("/api/v1/today", () => json({ day: "d" }));
    const work: Promise<unknown>[] = [];
    await respond(req("/api/v1/today"), { ...env, defer: (p) => work.push(p) })!;
    await Promise.all(work);
    expect(storage.peek(DATA_CACHE)).toHaveLength(1);
  });
});

describe("nothing outlives the session", () => {
  it("a 401 on a read view empties the data caches and is passed on", async () => {
    answers.set("/api/v1/today", () => json({ day: "d" }));
    await respond(req("/api/v1/today"), env)!;
    await (await storage.open(MEDIA_CACHE)).put(ORIGIN + "/api/v1/media/a.webm", new Response("x"));
    answers.set("/api/v1/entries/x", () => json({ error: "token" }, 401));
    const res = await respond(req("/api/v1/entries/x"), env)!;
    expect(res.status).toBe(401);
    expect(await storage.keys()).toEqual([]);
  });

  it("a logout request empties them before it is sent, even when it then fails", async () => {
    answers.set("/api/v1/today", () => json({ day: "d" }));
    await respond(req("/api/v1/today"), env)!;
    online = false;
    await expect(respond(req("/api/v1/logout", { method: "POST" }), env)!).rejects.toBeInstanceOf(TypeError);
    expect(await storage.keys()).toEqual([]);
    expect(sent.at(-1)).toBe("POST /api/v1/logout");
  });

  it("a 401 that comes after the time limit still empties the caches, and tells the page", async () => {
    answers.set("/api/v1/today", () => json({ day: "old" }));
    await respond(req("/api/v1/today"), env)!;
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    const told: string[] = [];
    const slowEnv: Env = {
      ...env,
      sleep: async () => undefined,   // the limit passes at once; the kept copy is shown
      notify: (m) => void told.push(m),
      fetch: async () => {
        await gate;
        return json({ error: "token" }, 401);
      },
    };
    const res = await respond(req("/api/v1/today"), slowEnv)!;
    expect(res.headers.get(FROM_CACHE)).toBe("stale");
    release();
    await new Promise((r) => setTimeout(r, 10));
    expect(await storage.keys()).toEqual([]);
    expect(told).toEqual(["AUTH_LOST"]);
  });

  it("a late 401 is told to the page before the worker may stop (the notice is awaited inside the deferred work)", async () => {
    answers.set("/api/v1/today", () => json({ day: "old" }));
    await respond(req("/api/v1/today"), env)!;
    const work: Promise<unknown>[] = [];
    let told = false;
    const slowEnv: Env = {
      ...env,
      sleep: async () => undefined,
      defer: (p) => work.push(p),
      notify: () => new Promise<void>((r) => setTimeout(() => ((told = true), r()), 15)),
      fetch: async () => {
        await new Promise((r) => setTimeout(r, 5));
        return json({ error: "token" }, 401);
      },
    };
    await respond(req("/api/v1/today"), slowEnv)!;
    await Promise.all(work);
    expect(told).toBe(true);
  });

  it("a 401 on the audio element's ranged request empties the caches and tells the page too", async () => {
    answers.set("/api/v1/today", () => json({ day: "d" }));
    await respond(req("/api/v1/today"), env)!;
    answers.set("/api/v1/media/a.webm", () => json({ error: "token" }, 401));
    const told: string[] = [];
    const res = await respond(req("/api/v1/media/a.webm", { headers: { Range: "bytes=0-" } }), { ...env, notify: (m) => void told.push(m) })!;
    expect(res.status).toBe(401);
    expect(await storage.keys()).toEqual([]);
    expect(told).toEqual(["AUTH_LOST"]);
  });

  it("a read in flight across a logout is not kept when it lands", async () => {
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    const slowEnv: Env = {
      ...env,
      fetch: async (r) => {
        if (r.url.endsWith("/logout")) return new Response(null, { status: 204 });
        await gate;
        return json({ entry: "his words" });
      },
    };
    const reading = respond(req("/api/v1/entries/x"), slowEnv)!;
    await respond(req("/api/v1/logout", { method: "POST" }), slowEnv)!;
    release();
    expect((await reading).status).toBe(200);   // the page that asked still gets its answer
    await new Promise((r) => setTimeout(r, 10));
    expect(storage.peek(DATA_CACHE)).toEqual([]);
  });

  it("a slow read that lands after a logout is not kept either, nor is audio", async () => {
    answers.set("/api/v1/today", () => json({ day: "old" }));
    await respond(req("/api/v1/today"), env)!;
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    const slowEnv: Env = {
      ...env,
      sleep: async () => undefined,
      fetch: async (r) => {
        if (r.url.endsWith("/logout")) return new Response(null, { status: 204 });
        await gate;
        return r.url.includes("/media/") ? new Response("abc", { headers: { "content-length": "3" } }) : json({ day: "new" });
      },
    };
    await respond(req("/api/v1/today"), slowEnv)!;
    const audio = respond(req("/api/v1/media/a.webm"), slowEnv)!;
    await respond(req("/api/v1/logout", { method: "POST" }), slowEnv)!;
    release();
    await audio;
    await new Promise((r) => setTimeout(r, 10));
    expect(storage.peek(DATA_CACHE)).toEqual([]);
    expect(storage.peek(MEDIA_CACHE)).toEqual([]);
  });

  it("clears only the caches that hold answers, not the shell's precache", async () => {
    await storage.open("workbox-precache-v2-https://host.example/");
    await storage.open(DATA_CACHE);
    await clearData(storage as unknown as CacheStorage);
    expect(await storage.keys()).toEqual(["workbox-precache-v2-https://host.example/"]);
  });
});

describe("audio", () => {
  const big = () => new Response("a".repeat(10), { status: 200, headers: { "content-length": String(MEDIA_CAP_BYTES + 1) } });
  const small = () => new Response("abc", { status: 200, headers: { "content-length": "3", "content-type": "audio/webm" } });

  it("keeps a small whole file and serves it again offline", async () => {
    answers.set("/api/v1/media/a.webm", small);
    await respond(req("/api/v1/media/a.webm"), env)!;
    expect(storage.peek(MEDIA_CACHE)).toHaveLength(1);
    online = false;
    expect(await (await respond(req("/api/v1/media/a.webm"), env)!).text()).toBe("abc");
  });

  it("never keeps audio over the cap", async () => {
    answers.set("/api/v1/media/long.webm", big);
    await respond(req("/api/v1/media/long.webm"), env)!;
    expect(storage.peek(MEDIA_CACHE)).toEqual([]);
  });

  it("never keeps a ranged answer or one of unknown size, and a ranged request is not answered from a copy", async () => {
    answers.set("/api/v1/media/a.webm", () => new Response("ab", { status: 206, headers: { "content-length": "2" } }));
    await respond(req("/api/v1/media/a.webm", { headers: { Range: "bytes=0-" } }), env)!;
    answers.set("/api/v1/media/b.webm", () => new Response("abc", { status: 200 }));
    await respond(req("/api/v1/media/b.webm"), env)!;
    expect(storage.peek(MEDIA_CACHE)).toEqual([]);
    answers.set("/api/v1/media/c.webm", small);
    await respond(req("/api/v1/media/c.webm"), env)!;
    answers.set("/api/v1/media/c.webm", () => new Response("fresh", { status: 206, headers: { "content-length": "5" } }));
    expect(await (await respond(req("/api/v1/media/c.webm", { headers: { Range: "bytes=0-" } }), env)!).text()).toBe("fresh");
  });
});
