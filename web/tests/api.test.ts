import { describe, expect, it } from "vitest";
import { ApiError, api, byTailscale, pair, query, session } from "../src/api";
import { isAuthLost } from "../src/auth";
import { fixtures, json, stubFetch } from "./helpers";

describe("the fixtures the server pins (tests/test_web_fixtures.py) parse", () => {
  it("every GET the app makes", async () => {
    stubFetch({
      "/api/v1/today": fixtures.today,
      "/api/v1/entries": fixtures.entries,
      "/api/v1/entries/2026-10-14-evening": fixtures.entryEvening,
      "/api/v1/entries/2026-10-15-fix-life-s01": fixtures.entrySession,
      "/api/v1/entries/2026-10-15-j": fixtures.entryNote,
      "/api/v1/reflections": fixtures.reflections,
      "/api/v1/insights/weeks": fixtures.insightsWeeks,
      "/api/v1/library": fixtures.library,
      "/api/v1/library/gratitude": fixtures.libraryGratitude,
      "/api/v1/settings": fixtures.settings,
    });
    expect((await api.today()).week).toHaveLength(7);
    expect((await api.entries({})).items).toHaveLength(3);
    for (const stem of ["2026-10-14-evening", "2026-10-15-fix-life-s01", "2026-10-15-j"]) {
      expect((await api.entry(stem)).stem).toBe(stem);
    }
    expect((await api.reflections({}))[0]?.sources).toEqual(["2026-10-14-evening"]);
    expect((await api.weeks({}))[0]?.week_end).toBe("2026-10-18");
    expect((await api.library()).map((p) => p.id)).toContain("gratitude");
    expect((await api.libraryOne("gratitude")).definition.sections[0]?.questions).toHaveLength(2);
    expect((await api.settings()).origins.location).toBe("file");
  });
});

describe("session", () => {
  it("parses the server's answer and tells a Tailscale sign-in apart", async () => {
    stubFetch({ "/api/v1/session": fixtures.session });
    const s = await session();
    expect(s).toEqual({ via: "token", login: null });
    expect(byTailscale(s)).toBe(false);
    stubFetch({ "/api/v1/session": { via: "tailscale", login: "sam@example.org" } });
    expect(byTailscale(await session())).toBe(true);
  });

  it("is null, and leaves the browser's pairing alone, when it cannot be told", async () => {
    stubFetch({ "/api/v1/session": () => json({ error: "token", detail: "" }, 401) });
    expect(await session()).toBeNull();
    expect(isAuthLost()).toBe(false);
    stubFetch({});   // an older server: 404
    expect(await session()).toBeNull();
  });

  it("reads the body of a refusal to its end, so the request is not left open", async () => {
    const refusal = json({ error: "token", detail: "" }, 401);
    stubFetch({ "/api/v1/session": () => refusal });
    expect(await session()).toBeNull();
    expect(refusal.bodyUsed).toBe(true);
  });
});

describe("unknown fields are dropped at the boundary", () => {
  it("in an entry document, its blocks and its prompt", async () => {
    const doc = structuredClone(fixtures.entryEvening) as Record<string, any>;
    doc.off_the_record = "/x not for the page";
    doc.path = "/server/path.md";
    doc.blocks[0].leak = "hidden";
    doc.prompt.private_note = "hidden";
    stubFetch({ "/api/v1/entries/x": doc });
    const got = (await api.entry("x")) as Record<string, any>;
    expect(got.off_the_record).toBeUndefined();
    expect(got.path).toBeUndefined();
    expect(got.blocks[0].leak).toBeUndefined();
    expect(got.prompt.private_note).toBeUndefined();
    expect(JSON.stringify(got)).not.toContain("not for the page");
  });

  it("in a library listing (the server's file path never reaches a view)", async () => {
    stubFetch({ "/api/v1/library/gratitude": fixtures.libraryGratitude });
    const one = await api.libraryOne("gratitude");
    expect(JSON.stringify(one)).not.toContain("/journal/state");
  });
});

describe("failures", () => {
  it("a 401 marks the browser unpaired and carries the server's line", async () => {
    stubFetch({ "/api/v1/today": () => json({ error: "token", detail: "no token" }, 401) });
    await expect(api.today()).rejects.toMatchObject({ status: 401, slug: "token", detail: "no token", unpaired: true });
    expect(isAuthLost()).toBe(true);
  });

  it("a network failure is an ApiError the view can say in words", async () => {
    const f = stubFetch({});
    f.fn.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    const err = await api.today().catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 0, slug: "network" });
  });

  it("an answer of the wrong shape is refused, naming where", async () => {
    stubFetch({ "/api/v1/today": { ...fixtures.today, week: "nope" } });
    await expect(api.today()).rejects.toMatchObject({ slug: "shape", detail: expect.stringContaining("week") });
  });

  it("an answer that is not JSON is refused", async () => {
    stubFetch({ "/api/v1/today": () => new Response("<html>", { status: 200 }) });
    await expect(api.today()).rejects.toMatchObject({ slug: "shape" });
  });

  it("pairing posts the code and clears the unpaired state", async () => {
    const f = stubFetch({ "/api/v1/pair": () => new Response(null, { status: 204 }) });
    await pair("abc");
    const init = f.fn.mock.calls[0]?.[1] as RequestInit;
    expect(init.method).toBe("POST");
    expect(init.body).toBe(JSON.stringify({ token: "abc" }));
    expect(isAuthLost()).toBe(false);
    stubFetch({ "/api/v1/pair": () => json({ error: "pairing", detail: "used or expired" }, 401) });
    await expect(pair("again")).rejects.toMatchObject({ slug: "pairing", detail: "used or expired" });
  });
});

describe("query", () => {
  it("leaves out what is empty", () => {
    expect(query({ q: "harbour", kind: undefined, audio: false, from: "" })).toBe("?q=harbour");
    expect(query({})).toBe("");
  });
});
