import { describe, expect, it, vi } from "vitest";
import { render } from "preact";
import { LocationProvider } from "preact-iso";
import { App } from "../src/app";
import { useResource } from "../src/hooks";
import { IDLE_MS, backoff, useLive } from "../src/events";
import { isAuthLost } from "../src/auth";
import type { EntrySummary } from "../src/types";
import { kindLabel } from "../src/labels";
import { LibraryItem } from "../src/types";
import { arr } from "../src/schema";
import { fixtures, json, mount, must, stubFetch, until } from "./helpers";

// Regressions found in review: each was a failing probe before its fix.

function visit(path: string) {
  window.history.replaceState(null, "", path);
}

const base = {
  "/api/v1/today": fixtures.today,
  "/api/v1/library": fixtures.library,
  "/api/v1/entries": fixtures.entries,
  "/api/v1/reflections": fixtures.reflections,
  "/api/v1/insights/weeks": fixtures.insightsWeeks,
};

describe("Entries: a page of Load more never outlives its filters", () => {
  it("drops a held page when a filter changes under it", async () => {
    visit("/entries?q=harbour");
    const [evening, session] = fixtures.entries.items as [EntrySummary, EntrySummary];
    const note = { ...evening, stem: "2026-10-15-j", date: "2026-10-15", title: "A note" };
    let release: (r: Response) => void = () => {};
    const held = new Promise<Response>((r) => (release = r));
    const calls: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost:3000");
      calls.push(url.pathname + url.search);
      if (url.pathname.endsWith("/library")) return json(fixtures.library);
      if (url.pathname.endsWith("/entries")) {
        if (url.searchParams.get("cursor") === "40") {
          init?.signal?.addEventListener("abort", () => release(json({ error: "aborted", detail: "" }, 499)));
          return held;
        }
        if (url.searchParams.get("audio") === "1") return json({ items: [evening], next: null });
        return json({ items: [session], next: "40" });
      }
      return json({ error: "not-found", detail: "" }, 404);
    }));
    const host = mount(<App />);
    const more = await until(() => must(host.querySelector<HTMLElement>("button.wide")));
    more.click();
    await until(() => expect(calls.some((c) => c.includes("cursor=40"))).toBe(true));
    const audio = must([...host.querySelectorAll<HTMLElement>(".filter-chips .chip")].find((c) => c.textContent === "Has audio"));
    audio.click();
    await until(() => expect(window.location.search).toContain("audio=1"));
    await until(() => expect(host.querySelectorAll(".rows a").length).toBe(1));
    release(json({ items: [note], next: "80" }));
    await new Promise((r) => setTimeout(r, 50));
    const hrefs = [...host.querySelectorAll(".rows a")].map((a) => a.getAttribute("href"));
    expect(hrefs).toEqual([`/entries/${evening.stem}`]);
    expect(host.querySelector("button.wide")).toBeNull();
  });
});

describe("Insights", () => {
  it("survives a malformed percent-escape in the address", async () => {
    visit("/insights#%E0%A4%A");
    stubFetch(base);
    const host = mount(<App />);
    await until(() => must(host.querySelector(".week-card")));
  });

  it("reads each week's reflections for that week, so none is lost to a list cap", async () => {
    visit("/insights");
    const f = stubFetch({
      ...base,
      "/api/v1/reflections": (url: URL) =>
        url.searchParams.get("from") === "2026-10-12" && url.searchParams.get("to") === "2026-10-18" ? fixtures.reflections : [],
    });
    const host = mount(<App />);
    await until(() => expect(host.querySelector(".reflection summary")?.textContent).toContain("reflection"));
    expect(f.calls.some((c) => c.includes("from=2026-10-12") && c.includes("to=2026-10-18"))).toBe(true);
    expect(f.calls.every((c) => !c.includes("limit=200"))).toBe(true);
  });
});

describe("Pairing", () => {
  it("does not take a page's hash for a pairing code", async () => {
    visit("/insights#2026-10-14-reflection");
    const f = stubFetch({
      "/api/v1/today": () => json({ error: "token", detail: "no token" }, 401),
      "/api/v1/insights/weeks": () => json({ error: "token", detail: "no token" }, 401),
      "/api/v1/pair": () => json({ error: "pairing", detail: "unknown" }, 401),
    });
    const host = mount(<App />);
    await until(() => must(host.querySelector(".pair-screen")));
    await new Promise((r) => setTimeout(r, 50));
    expect(f.calls.some((c) => c.endsWith("/pair"))).toBe(false);
    expect(window.location.hash).toBe("#2026-10-14-reflection");
    expect(host.querySelector(".pair-screen .error-line")).toBeNull();
  });
});

describe("Live events", () => {
  it("opens a new stream after the browser gives up on a refused one, and tells the views", async () => {
    const made: FakeSource[] = [];
    class FakeSource {
      static CLOSED = 2;
      readyState = 0;
      onopen: (() => void) | null = null;
      onerror: (() => void) | null = null;
      closed = false;
      constructor(public url: string) {
        made.push(this);
      }
      addEventListener() {}
      close() {
        this.closed = true;
        this.readyState = 2;
      }
    }
    vi.stubGlobal("EventSource", FakeSource);
    const reload = vi.fn();
    function Listener() {
      useLive(["tick"], reload);
      return null;
    }
    mount(<Listener />);
    await until(() => expect(made).toHaveLength(1));
    vi.useFakeTimers();
    try {
      made[0]!.readyState = 2;   // a 502 or 401: the browser fails the connection and does not retry
      made[0]!.onerror?.();
      expect(made[0]!.closed).toBe(true);
      expect(made).toHaveLength(1);
      vi.advanceTimersByTime(backoff(1));
      expect(made).toHaveLength(2);
      made[1]!.readyState = 1;
      made[1]!.onopen?.();
      expect(reload).toHaveBeenCalledTimes(1);   // whatever it announced meanwhile was missed: read again
    } finally {
      vi.useRealTimers();
    }
  });

  it("reads again after the browser reopened a dropped stream by itself", async () => {
    const made: { onopen: (() => void) | null; onerror: (() => void) | null; readyState: number }[] = [];
    class FakeSource {
      static CLOSED = 2;
      readyState = 1;
      onopen: (() => void) | null = null;
      onerror: (() => void) | null = null;
      constructor() {
        made.push(this);
      }
      addEventListener() {}
      close() {}
    }
    vi.stubGlobal("EventSource", FakeSource);
    const reload = vi.fn();
    function Listener() {
      useLive(["tick"], reload);
      return null;
    }
    mount(<Listener />);
    await until(() => expect(made).toHaveLength(1));
    made[0]!.onopen?.();
    expect(reload).not.toHaveBeenCalled();     // the first open is no reconnect
    made[0]!.readyState = 0;                   // dropped: the browser is retrying the same stream
    made[0]!.onerror?.();
    made[0]!.readyState = 1;
    made[0]!.onopen?.();
    expect(made).toHaveLength(1);
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("keeps one stream across view changes and closes it only after an idle while", async () => {
    const made: { closed: boolean }[] = [];
    class FakeSource {
      static CLOSED = 2;
      readyState = 1;
      closed = false;
      onopen = null;
      onerror = null;
      constructor() {
        made.push(this);
      }
      addEventListener() {}
      close() {
        this.closed = true;
      }
    }
    vi.stubGlobal("EventSource", FakeSource);
    function Listener() {
      useLive(["tick"], () => {});
      return null;
    }
    const first = mount(<Listener />);
    await until(() => expect(made).toHaveLength(1));
    vi.useFakeTimers();
    try {
      // A navigation: the old route unmounts a moment before the new one mounts. The server holds a closed stream's
      // slot for up to a ping interval, so the stream must not be closed and opened again for it.
      render(null, first);
      vi.advanceTimersByTime(IDLE_MS / 2);
      mount(<Listener />);
      vi.advanceTimersByTime(200);   // the new route's effect runs after paint
      vi.advanceTimersByTime(IDLE_MS * 2);
      expect(made).toHaveLength(1);
      expect(made[0]!.closed).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });

  it("closes the stream when nobody has listened for a while", async () => {
    const made: { closed: boolean }[] = [];
    class FakeSource {
      static CLOSED = 2;
      readyState = 1;
      closed = false;
      onopen = null;
      onerror = null;
      constructor() {
        made.push(this);
      }
      addEventListener() {}
      close() {
        this.closed = true;
      }
    }
    vi.stubGlobal("EventSource", FakeSource);
    function Listener() {
      useLive(["tick"], () => {});
      return null;
    }
    const host = mount(<Listener />);
    await until(() => expect(made).toHaveLength(1));
    vi.useFakeTimers();
    try {
      render(null, host);
      vi.advanceTimersByTime(IDLE_MS - 1);
      expect(made[0]!.closed).toBe(false);
      vi.advanceTimersByTime(2);
      expect(made[0]!.closed).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });

  it("backs off 1 s, doubling to 30 s", () => {
    expect([1, 2, 3, 9].map(backoff)).toEqual([1000, 2000, 4000, 30000]);
  });
});

describe("Today links", () => {
  it("opens each slot's own entry and the reflection by its real stem", async () => {
    visit("/");
    const data = {
      ...fixtures.today,
      practices: {
        rows: [
          { id: "evening", state: "answered", sent: "08:00", answered: "08:10", stem: "2026-10-15-evening" },
          { id: "evening", state: "open", sent: "09:00", stem: "2026-10-15-evening-2" },
        ],
        open: ["evening"],
      },
      entries: [
        { stem: "2026-10-15-evening", first_line: "first slot" },
        { stem: "2026-10-15-evening-2", first_line: "second slot" },
      ],
      reflection: { date: "2026-10-14", asked: "What?", stem: "2026-10-14-reflection-2" },
    };
    stubFetch({ ...base, "/api/v1/today": data });
    const host = mount(<App />);
    await until(() => expect(host.querySelectorAll("a.open-entry")).toHaveLength(2));
    expect([...host.querySelectorAll("a.open-entry")].map((a) => a.getAttribute("href"))).toEqual([
      "/entries/2026-10-15-evening",
      "/entries/2026-10-15-evening-2",
    ]);
    expect(host.querySelector(".carry a")?.getAttribute("href")).toBe("/insights#2026-10-14-reflection-2");
  });
});

describe("Entries search", () => {
  it("keeps a filter chosen while the search is still waiting to fire", async () => {
    visit("/entries");
    stubFetch(base);
    const host = mount(<App />);
    const box = await until(() => must(host.querySelector<HTMLInputElement>("input[type=search]")));
    box.value = "harbour";
    box.dispatchEvent(new Event("input", { bubbles: true }));
    const audio = await until(() => must([...host.querySelectorAll<HTMLElement>(".filter-chips .chip")].find((c) => c.textContent === "Has audio")));
    audio.click();
    await new Promise((r) => setTimeout(r, 600));
    expect(window.location.search).toContain("audio=1");
    expect(window.location.search).toContain("q=harbour");
  });
});

describe("Library", () => {
  it("lists programs under Frameworks", async () => {
    visit("/library");
    stubFetch(base);
    const host = mount(<App />);
    await until(() => must(host.querySelector("#g-Frameworks")));
    expect(host.querySelector('a[href="/library/fix-life"]')?.textContent).toContain("sessions");
  });
});

describe("Pairing retry", () => {
  it("offers Retry for a code the server could not take at the moment, though the address no longer holds it", async () => {
    visit("/pair#one-time-code");
    let tries = 0;
    const f = stubFetch({
      "/api/v1/pair": () => (++tries === 1 ? json({ error: "engine-unavailable", detail: "restarting" }, 503) : new Response(null, { status: 204 })),
      ...base,
    });
    const host = mount(<App />);
    const retry = await until(() => must(host.querySelector<HTMLElement>(".error-line button")));
    expect(window.location.hash).toBe("");
    retry.click();
    await until(() => expect(f.calls.filter((c) => c.endsWith("/pair"))).toHaveLength(2));
    await until(() => expect(window.location.pathname).toBe("/"));
  });
});

describe("Labels", () => {
  it("names the second note or slot of a day like the first", () => {
    const library = arr(LibraryItem)(fixtures.library);
    expect(kindLabel("j-2", library)).toBe("Note");
    expect(kindLabel("evening-2", library)).toBe(kindLabel("evening", library));
    expect(kindLabel("unknown-2", library)).toBe("unknown-2");
  });
});

describe("Entry of a program session", () => {
  it("lists the phases' questions it asked", async () => {
    visit("/entries/2026-10-15-fix-life-s01");
    stubFetch({ ...base, "/api/v1/entries/2026-10-15-fix-life-s01": fixtures.entrySession });
    const host = mount(<App />);
    await until(() => must(host.querySelector(".questions summary")));
    expect(host.querySelector(".questions")?.textContent).toContain("Phase 1");
    expect(host.querySelector(".phase-divider")?.textContent).toMatch(/^Phase 1 · \S/);   // the divider names the phase
  });
});

describe("useResource", () => {
  it("never shows the answer to other inputs, not even for one render", async () => {
    const seen: string[] = [];
    function Probe({ id }: { id: string }) {
      const [res] = useResource(async () => id, [id]);
      seen.push(`${id}:${res.state === "ready" ? res.data : res.state}`);
      return null;
    }
    const host = mount(<Probe id="a" />);
    await until(() => expect(seen).toContain("a:a"));
    seen.length = 0;
    render(<LocationProvider><Probe id="b" /></LocationProvider>, host);
    await until(() => expect(seen).toContain("b:b"));
    expect(seen).not.toContain("b:a");
    expect(seen[0]).toBe("b:loading");
  });
});

describe("Live events and the session", () => {
  it("probes an authenticated read when the stream is refused, so a 401 shows the pairing screen", async () => {
    const made: { readyState: number; onerror: (() => void) | null }[] = [];
    class FakeSource {
      static CLOSED = 2;
      readyState = 0;
      onopen: (() => void) | null = null;
      onerror: (() => void) | null = null;
      constructor(public url: string) {
        made.push(this);
      }
      addEventListener() {}
      close() {
        this.readyState = 2;
      }
    }
    vi.stubGlobal("EventSource", FakeSource);
    const f = stubFetch({ "/api/v1/status": () => json({ error: "token", detail: "no token" }, 401) });
    function Listener() {
      useLive(["tick"], () => {});
      return null;
    }
    mount(<Listener />);
    await until(() => expect(made).toHaveLength(1));
    expect(isAuthLost()).toBe(false);
    made[0]!.readyState = 2;   // the browser gave up: a 401 looks exactly like a 502 from here
    made[0]!.onerror?.();
    await until(() => expect(isAuthLost()).toBe(true));
    expect(f.calls).toContain("/api/v1/status");
  });
});

describe("Library of situational definitions", () => {
  it("shows a situational definition with its session and phase counts, and a program's phases without questions", async () => {
    visit("/library");
    stubFetch(base);
    const host = mount(<App />);
    await until(() => must(host.querySelector('a[href="/library/conversation-prep"]')));
    expect(must(host.querySelector('a[href="/library/conversation-prep"]')).querySelector(".meta")?.textContent).toMatch(
      /^On demand · 1 session · \d+ phases?$/,
    );
    const detail = {
      definition: { ...fixtures.libraryGratitude.definition, id: "fix-life", kind: "program", schedule: [], sections: [], opener: { plain: null } },
      raw: "---\nkind: program\n---\n",
      problems: [],
      mtime: null,
      sessions: [
        { number: 1, id: "s01", title: "Origin", raw: "", phases: [
          { number: 1, title: "Intro", type: "intro", questions: [] },
          { number: 2, title: "Excavation", type: "questions", questions: [{ id: "q1", text: "Where did it start?", pinned: false }] },
        ] },
      ],
    };
    visit("/library/fix-life");
    stubFetch({ ...base, "/api/v1/library/fix-life": detail });
    const page = mount(<App />);
    await until(() => must(page.querySelector(".session-set")));
    expect(page.querySelectorAll(".session-set .phase-line")).toHaveLength(2);   // the phase with no question is listed too
    expect(page.querySelector(".session-set")?.textContent).toContain("Where did it start?");
  });
});

describe("Settings and a tick from before version stamps", () => {
  it("says the last tick ran, with an unknown version, not that it has not run", async () => {
    visit("/settings");
    stubFetch({
      ...base,
      "/api/v1/settings": fixtures.settings,
      "/api/v1/healthz": { ok: true, version: "2.0.0", code: "aaa111", tick_last_at: "2026-10-15T03:30:00+00:00" },
      "/api/v1/status": { now_local: "x", timezone: "y", last_tick: { at: "2026-10-15T03:30:00+00:00" }, next: {}, warnings: [], recent_errors: [] },
      "/api/v1/doctor": [],
    });
    const host = mount(<App />);
    await until(() => must(host.querySelector(".warning")));
    expect(host.textContent).not.toContain("has not run yet");
    expect(host.textContent).toContain("ran before versions were stamped");
    expect(host.querySelector(".warning")?.textContent).toContain("predates version stamps");
  });
});
