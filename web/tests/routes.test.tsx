import { describe, expect, it } from "vitest";
import { App } from "../src/app";
import { useLocation } from "preact-iso";
import { EntriesRoute, groupByWeek } from "../src/routes/entries";
import { diffRows, groupLibrary } from "../src/routes/library";
import { flatten, groupSettings } from "../src/routes/settings";
import { codeFrom } from "../src/routes/pair";
import { weekFor } from "../src/routes/insights";
import { arr } from "../src/schema";
import { EntryPage, LibraryItem, Week } from "../src/types";
import blockToday from "../../tests/fixtures/today.json";
import { fixtures, json, mount, must, storedText, stubFetch, until } from "./helpers";

// The thread-start block's own dict (pinned by the engine's tests) plus the week strip the route adds: a day with
// everything on it: a location, weather, tasks and a reflection to carry forward.
const richToday = { ...blockToday, week: fixtures.today.week };

const base = {
  "/api/v1/today": fixtures.today,
  "/api/v1/library": fixtures.library,
  "/api/v1/entries": fixtures.entries,
  "/api/v1/entries/2026-10-14-evening": fixtures.entryEvening,
  "/api/v1/reflections": fixtures.reflections,
  "/api/v1/insights/weeks": fixtures.insightsWeeks,
  "/api/v1/library/gratitude": fixtures.libraryGratitude,
};

function visit(path: string) {
  window.history.replaceState(null, "", path);
}

describe("Today", () => {
  it("shows the date, the week, each practice with its state and the carry-forward line", async () => {
    visit("/");
    stubFetch({ ...base, "/api/v1/today": richToday });
    const host = mount(<App />);
    await until(() => must(host.querySelector("h1")));
    expect(host.querySelector("h1")?.textContent).toBe("Thursday 15 October 2026");
    expect(host.querySelector(".sub")?.textContent).toBe("09:12 IST · Hillside");
    expect(host.querySelector(".weather")?.textContent).toContain("14°C, light rain");
    expect(host.querySelectorAll(".week-strip a.day")).toHaveLength(7);
    const cards = [...host.querySelectorAll("li.practice")];
    expect(cards.map((c) => c.querySelector("h3")?.textContent)).toEqual(["Morning journal", "Evening journal"]);
    expect(cards[0]?.querySelector(".pill")?.textContent).toBe("answered 06:41");
    expect(cards[1]?.querySelector(".pill")?.textContent).toBe("at 23:00");
    expect(host.querySelector(".carry blockquote")?.textContent).toBe("What would it take to let the Tuesday feeling in?");
    expect(host.querySelector(".tasks")?.textContent).toContain("Call the bank");
  });

  it("still reads when the library cannot be fetched", async () => {
    visit("/");
    stubFetch({ ...base, "/api/v1/today": richToday, "/api/v1/library": () => json({ error: "engine-unavailable", detail: "x" }, 503) });
    const host = mount(<App />);
    await until(() => must(host.querySelector("li.practice")));
    expect(host.querySelector("li.practice h3")?.textContent).toBe("morning");
  });

  it("shows every prompt sent today in full, whatever its state, in her voice, in the order of the day", async () => {
    visit("/");
    stubFetch({ ...base, "/api/v1/today": fixtures.todayPrompts });
    const host = mount(<App />);
    await until(() => must(host.querySelector("li.practice .prompt")));
    const cards = [...host.querySelectorAll<HTMLElement>("li.practice")];
    expect(cards.map((c) => c.classList[2])).toEqual(["missed", "open", "answered", "upcoming"]);   // 06:00, 20:05, 21:30, 23:00
    expect(cards.map((c) => c.querySelector(".pill")?.textContent)).toEqual(
      ["came at 06:00", "waiting since 20:05", "answered 21:38", "at 23:00"]);   // a missed prompt says when it came, no more
    // the hour in the margin is left to the eye when the line under the title says the same time, and read out when it
    // says another (answered at 21:38, sent at 21:30)
    expect(cards.map((c) => c.querySelector(".practice-when")?.getAttribute("aria-hidden"))).toEqual(["true", "true", null, "true"]);
    expect(cards[2]?.querySelector(".practice-when time")?.textContent).toBe("21:30");
    const [missed, open, answered, upcoming] = cards as [HTMLElement, HTMLElement, HTMLElement, HTMLElement];
    // the whole message: the opener and every question, never folded on the web
    expect(missed.querySelector(".prompt .opener")?.textContent).toContain("Morning, Sam.");
    expect(missed.querySelectorAll(".prompt li")).toHaveLength(11);
    expect(missed.querySelector(".prompt details")).toBeNull();
    expect(missed.querySelector(".prompt li.asked")?.textContent).toContain("How did you sleep?");
    expect(missed.querySelector(".prompt-meta .who")?.textContent).toBe("Ada");
    expect(open.querySelector(".prompt .opener")).toBeNull();   // sent with the plain opener: nothing recorded, the questions stand
    expect(open.querySelectorAll(".prompt li").length).toBeGreaterThan(0);
    expect(answered.querySelector(".prompt .opener")?.textContent).toBe("Three small things tonight, Sam. The walk to the harbour counts.");
    expect(answered.querySelectorAll(".prompt li")).toHaveLength(2);
    // his reply under her message, in his own type, linked to the entry
    const reply = must(answered.querySelector<HTMLAnchorElement>("a.his-reply"));
    expect(reply.getAttribute("href")).toBe("/entries/2026-10-15-gratitude");
    expect(reply.querySelector(".who")?.textContent).toBe("Sam · 21:38");
    expect(reply.querySelector(".words")?.textContent).toContain("The ferry was late");
    expect(answered.querySelector(".prompt")?.contains(reply)).toBe(false);   // never inside her message
    expect(upcoming.querySelector(".prompt")).toBeNull();   // not sent yet: nothing to show
    expect([...missed.querySelectorAll("button")].map((b) => b.textContent)).toEqual(["Reply", "Send now"]);
  });

  it("credits OpenStreetMap under the place while it named it, quietly, and not otherwise", async () => {
    visit("/");
    stubFetch({ ...base, "/api/v1/today": { ...fixtures.todayPrompts, location_source: "openstreetmap" } });
    const host = mount(<App />);
    const credit = await until(() => must(host.querySelector(".today-head .credit")));
    expect(credit.textContent).toBe("Place name © OpenStreetMap contributors");
    expect(credit.querySelector("a")?.getAttribute("href")).toBe("https://www.openstreetmap.org/copyright");
  });

  it("shows no credit for a place typed by hand", async () => {
    visit("/");
    stubFetch({ ...base, "/api/v1/today": fixtures.todayPrompts });
    const host = mount(<App />);
    await until(() => must(host.querySelector(".today-head .sub")));
    expect(host.querySelector(".credit")).toBeNull();
  });

  it("draws a sent prompt's card without its message when the server could not build it", async () => {
    visit("/");
    const rows = fixtures.todayPrompts.practices.rows.map((r) => (r.id === "morning" ? { ...r, prompt: undefined } : r));
    stubFetch({ ...base, "/api/v1/today": { ...fixtures.todayPrompts, practices: { ...fixtures.todayPrompts.practices, rows } } });
    const host = mount(<App />);
    await until(() => must(host.querySelector("li.practice.answered .prompt")));
    const missed = must(host.querySelector<HTMLElement>("li.practice.missed"));
    expect(missed.querySelector(".pill")?.textContent).toBe("came at 06:00");
    expect(missed.querySelector(".prompt")).toBeNull();
    expect([...missed.querySelectorAll("button")].map((b) => b.textContent)).toEqual(["Reply", "Send now"]);
  });

  it("falls back to the pairing screen on a 401", async () => {
    visit("/");
    stubFetch({ "/api/v1/today": () => json({ error: "token", detail: "no token" }, 401), "/api/v1/library": () => json({ error: "token", detail: "" }, 401) });
    const host = mount(<App />);
    await until(() => must(host.querySelector(".pair-screen")));
    expect(host.querySelector("h1")?.textContent).toBe("Not paired");
    expect(host.querySelector(".nav")).toBeNull();
  });

  it("signed in by Tailscale, a refused read asks to retry instead of offering a pairing form", async () => {
    visit("/");
    localStorage.setItem("journal:via", "tailscale");
    let up = false;   // the server restarting: nothing answers until it is back
    const f = stubFetch({
      ...base,
      "/api/v1/today": () => (up ? fixtures.today : json({ error: "token", detail: "no token" }, 401)),
      "/api/v1/session": () => (up ? { via: "tailscale", login: "sam@example.org" } : json({ error: "engine-unavailable", detail: "" }, 503)),
    });
    const host = mount(<App />);
    await until(() => must(host.querySelector(".tailscale-lost")));
    expect(host.querySelector("h1")?.textContent).toBe("Couldn't confirm your Tailscale sign-in");
    await until(() => expect(host.querySelector('[role="alert"]')?.textContent).toContain("may be restarting"));
    expect(host.querySelector(".pair-fallback")?.hasAttribute("open")).toBe(false);   // pairing is folded away, not the screen
    up = true;
    must([...host.querySelectorAll<HTMLButtonElement>("button")].find((b) => b.textContent === "Retry")).click();
    await until(() => expect(host.querySelector("h1")?.textContent).toBe("Thursday 15 October 2026"));
    expect(f.calls).not.toContain("/api/v1/pair");
  });

  it("signed in by Tailscale, a server that refuses this device says so and opens the pairing fold", async () => {
    visit("/");
    localStorage.setItem("journal:via", "tailscale");
    stubFetch({ ...base, "/api/v1/today": () => json({ error: "token", detail: "no token" }, 401), "/api/v1/session": () => json({ error: "token", detail: "" }, 401) });
    const host = mount(<App />);
    await until(() => expect(host.querySelector("h1")?.textContent).toBe("This device isn't recognised by Tailscale any more"));
    expect(host.querySelector(".pair-fallback")?.hasAttribute("open")).toBe(true);
    expect(host.querySelector("input#pair-code")).not.toBeNull();
    expect(host.textContent).not.toContain("may be restarting");
  });

  it("remembers how the server last said this browser signs in, and only the word", async () => {
    visit("/");
    stubFetch({ ...base, "/api/v1/session": { via: "tailscale", login: "sam@example.org" } });
    mount(<App />);
    await until(() => expect(localStorage.getItem("journal:via")).toBe("tailscale"));
    expect(storedText(localStorage)).not.toContain("sam@example.org");
  });
});

describe("Entries", () => {
  it("groups by week and day, newest first, with indicators", async () => {
    visit("/entries");
    stubFetch(base);
    const host = mount(<App />);
    await until(() => must(host.querySelector(".entry-row")));
    expect(host.querySelector(".week-head")?.textContent).toBe("Week of 12 Oct");
    expect([...host.querySelectorAll(".day h3")].map((h) => h.textContent)).toEqual(["Thu 15 Oct", "Wed 14 Oct"]);
    const evening = must(host.querySelector('a[href="/entries/2026-10-14-evening"]'));
    expect(evening.querySelector(".title")?.textContent).toBe("Evening journal");
    expect(evening.querySelector(".first-line")?.textContent).toBe("First words, about the lighthouse.");
    expect(evening.querySelector('[aria-label="annotated"]')).not.toBeNull();
    expect(evening.querySelector('[aria-label="has a conversation"]')).not.toBeNull();
  });

  it("asks the server for the filters in the address", async () => {
    visit("/entries?kind=evening&q=harbour&audio=1&day=2026-10-14");
    const f = stubFetch(base);
    const host = mount(<App />);
    await until(() => must(host.querySelector(".entry-row")));
    const asked = f.calls.find((c) => c.startsWith("/api/v1/entries?"));
    expect(asked).toContain("kind=evening");
    expect(asked).toContain("q=harbour");
    expect(asked).toContain("audio=1");
    expect(asked).toContain("from=2026-10-14");
    expect(asked).toContain("to=2026-10-14");
    expect(host.querySelector(".active-filter")?.textContent).toContain("Wed 14 Oct");
  });

  it("loads the next page by cursor", async () => {
    visit("/entries");
    const first = { ...fixtures.entries, next: "3" };
    const f = stubFetch({
      ...base,
      "/api/v1/entries": (url: URL) =>
        url.searchParams.get("cursor") === "3"
          ? { items: [{ ...fixtures.entries.items[2], stem: "2026-10-13-morning", date: "2026-10-13", title: "Morning journal — Tue 13 Oct 2026" }], next: null }
          : first,
    });
    const host = mount(<App />);
    const more = await until(() => must(host.querySelector<HTMLElement>("button.wide")));
    more.click();
    await until(() => must(host.querySelector('a[href="/entries/2026-10-13-morning"]')));
    expect(host.querySelector("button.wide")).toBeNull();
    expect(f.calls.some((c) => c.includes("cursor=3"))).toBe(true);
  });

  it("says when nothing matches", async () => {
    visit("/entries?q=zzz");
    stubFetch({ ...base, "/api/v1/entries": { items: [], next: null } });
    const host = mount(<EntriesRoute />);
    await until(() => must(host.querySelector(".empty")));
    expect(host.querySelector(".empty")?.textContent).toBe("No entries match.");
  });
});

describe("Entry", () => {
  it("renders the document, then the analysis tab", async () => {
    visit("/entries/2026-10-14-evening");
    stubFetch(base);
    const host = mount(<App />);
    await until(() => must(host.querySelector(".document")));
    expect(host.querySelector("h1")?.textContent).toBe("Evening journal");
    must(host.querySelector<HTMLElement>("#tab-analysis")).click();
    await until(() => must(host.querySelector(".analysis")));
    expect(host.querySelector(".analysis")?.textContent).toContain("Ada's annotations");
    expect(host.querySelector(".analysis")?.textContent).toContain("A note of hers.");
    await until(() => must(host.querySelector(".refl-title")));
    expect(host.querySelector(".analysis")?.textContent).toContain("To carry forward");
  });

  it("says not an engine entry on a 404", async () => {
    visit("/entries/2026-10-14");
    stubFetch(base);
    const host = mount(<App />);
    await until(() => must(host.querySelector(".empty")));
    expect(host.querySelector(".empty")?.textContent).toContain("Not an engine entry");
  });
});

describe("Library", () => {
  it("groups into Situational, Daily and Frameworks", () => {
    const items = arr(LibraryItem)(fixtures.library);
    const groups = groupLibrary([...items, { ...items[0]!, id: "prep", kind: "program", schedule: [] }, { ...items[0]!, id: "free", kind: "practice", schedule: [] }]);
    expect(groups.map((g) => g.group)).toEqual(["Situational", "Daily", "Frameworks"]);
    expect(groups[2]?.items.map((i) => i.id)).toEqual(["fix-life", "prep"]);   // the packaged program, then ours
    expect(groups[0]?.items.map((i) => i.id)).toContain("free");
  });

  it("reads a unified diff as rows: the file once, a gap per hunk, each line's mark apart from its words", () => {
    const rows = diffRows(fixtures.libraryMidweekDefault.diff.since ?? "");
    expect(rows[0]).toEqual({ kind: "file", mark: "", text: "midweek.md" });
    expect(rows[1]).toEqual({ kind: "hunk", mark: "", text: "⋯" });
    expect(rows.filter((r) => r.kind === "del").map((r) => r.text)).toEqual(["- Monday's intentions vs. where you are now — on track?"]);
    expect(rows.filter((r) => r.kind === "ins").map((r) => r.text)).toEqual(["- Monday's intentions vs. where you are now — aligned or drifted?"]);
    expect(rows.some((r) => r.text.startsWith("--- ") || r.text.startsWith("+++ "))).toBe(false);
    // a line "-- x" taken out reads "--- x" in a diff: it is a line, not a header
    expect(diffRows("--- a/f.md\n+++ b/f.md\n@@ -1 +1 @@\n--- x\n+-- y\n")).toEqual([
      { kind: "file", mark: "", text: "f.md" }, { kind: "hunk", mark: "", text: "⋯" },
      { kind: "del", mark: "-", text: "-- x" }, { kind: "ins", mark: "+", text: "-- y" },
    ]);
    // inside a hunk, "-- x" taken out and "++ y" put in read "--- x" / "+++ y": still lines, not a file's header
    expect(diffRows("--- a/f.md\n+++ b/f.md\n@@ -1,2 +1,2 @@\n a\n--- x\n+++ y\n--- a/g.md\n+++ b/g.md\n@@ -3 +3 @@\n-p\n+q\n")).toEqual([
      { kind: "file", mark: "", text: "f.md" }, { kind: "hunk", mark: "", text: "⋯" },
      { kind: "same", mark: "", text: "a" }, { kind: "del", mark: "-", text: "-- x" }, { kind: "ins", mark: "+", text: "++ y" },
      { kind: "file", mark: "", text: "g.md" }, { kind: "hunk", mark: "", text: "⋯" },
      { kind: "del", mark: "-", text: "p" }, { kind: "ins", mark: "+", text: "q" },
    ]);
  });

  it("lists cadence and source badges, and shows a definition's questions and file", async () => {
    visit("/library");
    stubFetch(base);
    const host = mount(<App />);
    await until(() => must(host.querySelector(".library-card")));
    const mine = must(host.querySelector('a[href="/library/gratitude"]'));
    expect(mine.querySelector(".meta")?.textContent).toBe("Daily 21:30");
    expect(mine.querySelector(".badge.source")?.textContent).toBe("yours");
    visit("/library/gratitude");
    const detail = mount(<App />);
    await until(() => must(detail.querySelector(".question-set")));
    expect(detail.querySelectorAll(".question-set li")).toHaveLength(2);
    expect(detail.querySelector(".raw pre")?.textContent).toContain("kind: practice");
  });
});

describe("Insights", () => {
  it("steps through weeks and shows the day reflections", async () => {
    visit("/insights");
    stubFetch(base);
    const host = mount(<App />);
    await until(() => must(host.querySelector(".week-card")));
    expect(host.querySelector(".stepper strong")?.textContent).toBe("12 – 18 Oct");
    expect(host.querySelector(".counts")?.textContent).toContain("Words32");
    await until(() => expect(host.querySelector(".reflection summary")?.textContent).toBe("Ada's reflection — Wed 14 Oct 2026"));
    must(host.querySelector<HTMLElement>(".stepper button")).click();
    await until(() => expect(host.querySelector(".stepper strong")?.textContent).toBe("5 – 11 Oct"));
    await until(() => expect(host.querySelector(".week-card")?.textContent).toContain("No day reflections this week."));
  });

  it("finds the week a reflection link names", () => {
    const weeks = fixtures.insightsWeeks.map((w) => Week(w));
    expect(weekFor(weeks, "2026-10-14-reflection")).toBe(0);
    expect(weekFor(weeks, "nope")).toBe(-1);
  });
});

describe("Settings", () => {
  it("flattens the settings tree to dotted keys, grouped by top-level key with their origin", async () => {
    expect(flatten({ a: { b: 1, c: [1, 2] }, d: "x", e: {} })).toEqual([["a.b", "1"], ["a.c", "[1,2]"], ["d", "x"], ["e", "{}"]]);
    const groups = groupSettings(fixtures.settings.settings as never);
    expect(groups.find((g) => g.key === "windows")?.rows).toEqual([["windows.collapse_hours", "6"], ["windows.grace_hours", "2"]]);
    visit("/settings");
    stubFetch({
      ...base,
      "/api/v1/settings": fixtures.settings,
      "/api/v1/healthz": { ok: true, version: "2.0.0", code: "aaa111", tick_last_at: "2026-10-15T03:30:00+00:00" },
      "/api/v1/status": { now_local: "2026-10-15T09:12+05:30", timezone: "Asia/Kolkata", last_tick: { at: "2026-10-15T03:30:00+00:00", version: "2.0.0", code: "bbb222" }, next: { morning: "Fri 16 Oct 06:00 IST" }, warnings: [], recent_errors: [] },
      "/api/v1/doctor": [{ check: "config", status: "ok", detail: "loaded" }, { check: "audio transcription", status: "warn", detail: "no backend" }],
      "/api/v1/session": fixtures.session,
    });
    const host = mount(<App />);
    await until(() => must(host.querySelector(".settings-group")));
    const windows = [...host.querySelectorAll(".settings-group")].find((g) => g.querySelector(".key")?.textContent === "windows");
    expect(windows?.querySelector(".origin")?.textContent).toBe("config file");
    await until(() => must(host.querySelector(".warning")));
    expect(host.querySelector(".warning")?.textContent).toContain("different code");
    await until(() => must(host.querySelector("table.doctor")));
    expect(host.querySelectorAll("table.doctor tbody tr")).toHaveLength(2);
    expect(host.textContent).toContain("Paired as localhost:3000");
  });

  it("signed in by Tailscale: says so, and offers no Unpair", async () => {
    visit("/settings");
    stubFetch({ ...base, "/api/v1/settings": fixtures.settings, "/api/v1/session": { via: "tailscale", login: "sam@example.org" }, "/api/v1/healthz": { ok: true, version: "2.0.0", code: "a", tick_last_at: null }, "/api/v1/status": { now_local: "x", timezone: "y", last_tick: null, next: {}, warnings: [], recent_errors: [] }, "/api/v1/doctor": [] });
    const host = mount(<App />);
    await until(() => expect(host.textContent).toContain("Signed in via Tailscale as sam@example.org"));
    await until(() => must(host.querySelector(".settings-group")));
    expect([...host.querySelectorAll("button")].find((b) => b.textContent === "Unpair")).toBeUndefined();
    expect(host.textContent).not.toContain("Paired as");
  });

  it("does not claim a pairing it has not seen when the server cannot say how this browser is signed in", async () => {
    visit("/settings");
    stubFetch({ ...base, "/api/v1/settings": fixtures.settings, "/api/v1/session": () => json({ error: "engine-unavailable", detail: "" }, 503), "/api/v1/healthz": { ok: true, version: "2.0.0", code: "a", tick_last_at: null }, "/api/v1/status": { now_local: "x", timezone: "y", last_tick: null, next: {}, warnings: [], recent_errors: [] }, "/api/v1/doctor": [] });
    const host = mount(<App />);
    await until(() => expect(host.textContent).toContain("Couldn't confirm how this browser is signed in"));
    expect(host.textContent).not.toContain("Paired as");
    expect([...host.querySelectorAll("button")].find((b) => b.textContent === "Unpair")).toBeUndefined();   // not known to be paired
    expect([...host.querySelectorAll("button")].find((b) => b.textContent === "Check again")).toBeDefined();
  });

  it("unpairs: logout, then the pairing screen", async () => {
    visit("/settings");
    const f = stubFetch({ ...base, "/api/v1/session": fixtures.session, "/api/v1/settings": fixtures.settings, "/api/v1/healthz": { ok: true, version: "2.0.0", code: "a", tick_last_at: null }, "/api/v1/status": { now_local: "x", timezone: "y", last_tick: null, next: {}, warnings: [], recent_errors: [] }, "/api/v1/doctor": [], "/api/v1/logout": () => new Response(null, { status: 204 }) });
    const host = mount(<App />);
    const button = await until(() => must([...host.querySelectorAll<HTMLElement>("button")].find((b) => b.textContent === "Unpair")));
    button.click();
    await until(() => must(host.querySelector(".pair-screen"), "pair"));
    expect(f.calls).toContain("/api/v1/logout");
  });
});

describe("Pairing", () => {
  it("reads the code from the link, takes it out of the address and goes to Today", async () => {
    visit("/pair#one-time-code");
    const f = stubFetch({ ...base, "/api/v1/pair": () => new Response(null, { status: 204 }) });
    const host = mount(<App />);
    await until(() => expect(host.querySelector("h1")?.textContent).toBe("Thursday 15 October 2026"));
    expect(f.fn.mock.calls.find((c) => String(c[0]).endsWith("/pair"))?.[1]?.body).toBe(JSON.stringify({ token: "one-time-code" }));
    expect(window.location.hash).toBe("");
    expect(window.location.pathname).toBe("/");
  });

  it("signed in by Tailscale: the pairing page goes to Today and spends no code", async () => {
    visit("/pair#one-time-code");
    const f = stubFetch({ ...base, "/api/v1/session": { via: "tailscale", login: "sam@example.org" }, "/api/v1/pair": () => new Response(null, { status: 204 }) });
    const host = mount(<App />);
    await until(() => expect(host.querySelector("h1")?.textContent).toBe("Thursday 15 October 2026"));
    expect(f.calls).not.toContain("/api/v1/pair");
    expect(window.location.hash).toBe("");
    expect(window.location.pathname).toBe("/");
  });

  it("says the link is spent and offers a field for another", async () => {
    visit("/pair#spent");
    stubFetch({ "/api/v1/pair": () => json({ error: "pairing", detail: "this pairing link is unknown, used or expired: run `journal serve pair` again" }, 401) });
    const host = mount(<App />);
    await until(() => must(host.querySelector('[role="alert"]')));
    expect(host.querySelector('[role="alert"]')?.textContent).toContain("used or expired");
    expect(host.querySelector("input#pair-code")).not.toBeNull();
    expect(window.location.hash).toBe("");
  });

  it("accepts a whole pasted link or a bare code", () => {
    expect(codeFrom(" https://journal.example.org/pair#abc123 ")).toBe("abc123");
    expect(codeFrom("abc123")).toBe("abc123");
    expect(codeFrom("")).toBe("");
  });
});

describe("pure helpers", () => {
  it("groupByWeek keeps order and splits weeks", () => {
    const page = EntryPage(fixtures.entries);
    const earlier = { ...page.items[2]!, stem: "2026-10-08-evening", date: "2026-10-08" };
    const weeks = groupByWeek([...page.items, earlier]);
    expect(weeks.map((w) => w.week)).toEqual(["2026-10-12", "2026-10-05"]);
    expect(weeks[0]?.days.map((d) => d.date)).toEqual(["2026-10-15", "2026-10-14"]);
  });

  it("the location is not read by the tests above", () => {
    function Probe() { return <p>{useLocation().path}</p>; }
    visit("/library");
    const host = mount(<Probe />);
    expect(host.textContent).toBe("/library");
  });
});
