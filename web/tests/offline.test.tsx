import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api } from "../src/api";
import { setAuthLost } from "../src/auth";
import { OfflineRibbon } from "../src/components/OfflineRibbon";
import {
  OFFLINE_WRITE, clearOfflineData, forgetReads, keptAt, noteRead, ribbonText, staleAsOf,
} from "../src/offline";
import { CACHED_AT, DATA_CACHE, FROM_CACHE } from "../src/swCore";
import { fixtures, mount, until } from "./helpers";

afterEach(() => forgetReads());

const at = (h: number, m: number, day = 15) => new Date(2026, 9, day, h, m).getTime();

describe("the offline ribbon", () => {
  it("says when the copy was kept, in local time", () => {
    expect(ribbonText(at(9, 12), at(11, 0))).toBe("offline, as of 09:12");
    expect(ribbonText(at(0, 5), at(23, 0))).toBe("offline, as of 00:05");
  });

  it("adds the day when the copy is not from today", () => {
    expect(ribbonText(at(21, 40, 14), at(9, 0, 15))).toBe("offline, as of Wed 14 Oct 21:40");
  });

  it("reads the worker's headers, and only a marked answer counts as kept", () => {
    const stale = new Headers({ [FROM_CACHE]: "stale", [CACHED_AT]: "1760000000000" });
    expect(keptAt(stale)).toBe(1760000000000);
    expect(keptAt(new Headers({ [CACHED_AT]: "1760000000000" }))).toBeNull();
    expect(keptAt(new Headers({ [FROM_CACHE]: "stale", [CACHED_AT]: "soon" }))).toBeNull();
    expect(keptAt(new Headers())).toBeNull();
  });

  it("is shown while a read came from the kept copy and gone when the server answers again", async () => {
    const host = mount(<OfflineRibbon />);
    expect(host.querySelector(".ribbon")).toBeNull();
    noteRead(at(9, 12));
    await until(() => expect(host.querySelector(".ribbon")?.textContent).toMatch(/^offline, as of (\w{3} \d+ \w{3} )?\d\d:\d\d$/));
    noteRead(null);
    await until(() => expect(host.querySelector(".ribbon")).toBeNull());
  });

  it("stays while any read of the view came from a copy, with the oldest time; a fresh answer to another read does not hide it", () => {
    noteRead(at(9, 12), "/today");
    noteRead(null, "/runs");                       // a fresh answer to a different read
    expect(staleAsOf()).toBe(at(9, 12));
    noteRead(at(8, 0), "/days");
    expect(staleAsOf()).toBe(at(8, 0));            // the oldest copy on screen
    noteRead(null, "/days");
    expect(staleAsOf()).toBe(at(9, 12));
    noteRead(null, "/today");                      // the same read answered by the server again
    expect(staleAsOf()).toBeNull();
    noteRead(at(9, 12), "/today");
    forgetReads();                                 // a new view
    expect(staleAsOf()).toBeNull();
  });

  it("is set by a read the worker answered from its copy, and cleared by one from the server", async () => {
    const stale = { [FROM_CACHE]: "stale", [CACHED_AT]: String(at(9, 12)) };
    const answer = (headers: Record<string, string>) =>
      new Response(JSON.stringify(fixtures.today), { status: 200, headers: { "content-type": "application/json", ...headers } });
    vi.stubGlobal("fetch", vi.fn(async () => answer(stale)));
    await api.today();
    expect(staleAsOf()).toBe(at(9, 12));
    vi.stubGlobal("fetch", vi.fn(async () => answer({})));
    await api.today();
    expect(staleAsOf()).toBeNull();
  });
});

describe("a write while offline", () => {
  it("fails with the offline line, and the page keeps what was typed (the composer's draft)", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));
    const online = vi.spyOn(navigator, "onLine", "get").mockReturnValue(false);
    try {
      await expect(api.createEntry({ text: "x" })).rejects.toMatchObject({ slug: "offline", detail: OFFLINE_WRITE });
      await expect(api.today()).rejects.toMatchObject({ slug: "offline" });
    } finally {
      online.mockRestore();
    }
  });

  it("is a plain network failure when the browser believes it is online", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));
    const err = await api.createEntry({ text: "x" }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).slug).toBe("network");
  });
});

describe("nothing kept outlives the session", () => {
  it("a 401, an unpair, drops every cache that holds answers and tells the worker", async () => {
    const deleted: string[] = [];
    const names = [DATA_CACHE, "journal-media-v1", "workbox-precache-v2-x"];
    vi.stubGlobal("caches", { keys: async () => names, delete: async (n: string) => { deleted.push(n); return true; } });
    const post = vi.fn();
    Object.defineProperty(navigator, "serviceWorker", { value: { controller: { postMessage: post } }, configurable: true });
    try {
      noteRead(at(9, 12));
      await clearOfflineData();
      expect(deleted).toEqual([DATA_CACHE, "journal-media-v1"]);
      expect(post).toHaveBeenCalledWith({ type: "CLEAR_DATA" });
      expect(staleAsOf()).toBeNull();
    } finally {
      Reflect.deleteProperty(navigator, "serviceWorker");
    }
  });

  it("is what losing the session does", async () => {
    const deleted: string[] = [];
    vi.stubGlobal("caches", { keys: async () => [DATA_CACHE], delete: async (n: string) => { deleted.push(n); return true; } });
    setAuthLost(true);
    await until(() => expect(deleted).toEqual([DATA_CACHE]));
  });
});
