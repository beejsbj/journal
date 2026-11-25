import { describe, expect, it } from "vitest";
import { App } from "../src/app";
import { logRows } from "../src/routes/day";
import { DayLog as DayLogShape } from "../src/types";
import { DayLog } from "../src/ui";
import { click, fixtures, mount, must, stubFetch, until } from "./helpers";

// A day as one log (issue #82): GET /days/{date}, pinned by tests/test_web_fixtures.py (tests/fixtures/api/day.json).

const base = {
  "/api/v1/today": fixtures.today,
  "/api/v1/library": fixtures.library,
  "/api/v1/entries": fixtures.entries,
  "/api/v1/days": fixtures.days,
  "/api/v1/days/2026-10-14": fixtures.day,
};

function visit(path: string) {
  window.history.replaceState(null, "", path);
}

describe("the day view", () => {
  it("reads the server's answer as it is", () => {
    const log = DayLogShape(fixtures.day);
    expect(log.date).toBe("2026-10-14");
    expect(log.lines.length).toBeGreaterThan(0);
  });

  it("shows the day's lines in time order, each hour a way into its entry, her replies folded", async () => {
    visit("/days/2026-10-14");
    stubFetch(base);
    const host = mount(<App />);
    await until(() => must(host.querySelector(".log-line")));
    expect(host.querySelector("h1")?.textContent).toBe("Wednesday 14 October 2026");
    const hours = [...host.querySelectorAll(".log-line .log-hour time")].map((t) => t.textContent);
    expect(hours).toEqual(["23:12", "23:40", "23:44", "23:50"]);
    const first = must(host.querySelector<HTMLAnchorElement>(".log-line .log-hour"));
    expect(first.getAttribute("href")).toBe("/entries/2026-10-14-evening#words:0");
    expect(first.getAttribute("aria-label")).toBe("23:12. Open in Evening journal");
    expect([...host.querySelectorAll(".log-entry a")].map((a) => a.textContent)).toEqual(["Evening journal"]);
    expect(host.querySelector(".log-line .log-source")?.textContent).toBe("voice");
    // her reply: hers, folded, and in her own voice when opened; never a line of his
    const fold = must(host.querySelector(".log-hers .disclosure-head"));
    expect(fold.getAttribute("aria-expanded")).toBe("false");
    expect(host.querySelector(".log-hers .disclosure-body")?.hasAttribute("inert")).toBe(true);
    expect([...host.querySelectorAll(".log-line .log-words")].map((w) => w.textContent).join(" ")).not.toContain("SECRETHERS");
    await click(fold);
    expect(fold.getAttribute("aria-expanded")).toBe("true");
    expect(host.querySelector(".log-hers .margin-note-body.her")?.textContent).toContain("The harbour sounds like");
    // the days either side
    expect(host.querySelector('.day-nav a[href="/days/2026-10-15"]')).not.toBeNull();
  });

  it("marks a line written after midnight with its weekday, and names each entry where the lines change", () => {
    const log = DayLogShape({
      ...fixtures.day,
      entries: [{ stem: "2026-10-14-j", kind: "j", practice: null, title: "Note — Wed 14 Oct 2026", emoji: "", time: "19:30" },
        ...fixtures.day.entries],
      lines: [
        { stem: "2026-10-14-j", key: "words:0", kind: "his", date: "2026-10-14", time: "19:30", text: "at dinner", source: "text", audio: null, reply_to: null },
        { stem: "2026-10-14-evening", key: "words:0", kind: "his", date: "2026-10-14", time: "23:12", text: "late", source: "text", audio: null, reply_to: null },
        { stem: "2026-10-14-evening", key: "her:1", kind: "hers", date: "2026-10-14", time: "23:13", text: "One." },
        { stem: "2026-10-14-evening", key: "her:2", kind: "hers", date: "2026-10-14", time: "23:14", text: "Two." },
        { stem: "2026-10-14-evening", key: "his:3", kind: "his", date: "2026-10-15", time: "00:20", text: "after midnight", source: "text", audio: null, reply_to: null },
      ],
    });
    const host = mount(<DayLog>{logRows(log)}</DayLog>);
    expect([...host.querySelectorAll(".log-entry a")].map((a) => a.textContent)).toEqual(["Note", "Evening journal"]);
    expect(host.querySelectorAll(".log-hers")).toHaveLength(1);   // one fold for her two replies in a row
    expect(host.querySelector(".log-hers-head")?.textContent).toBe("Ada · 2 replies");
    const all = [...host.querySelectorAll(".log-line")];
    const late = all[all.length - 1];
    expect(late?.querySelector(".log-day")?.textContent).toBe("Thu");
    expect(late?.querySelector(".log-hour")?.getAttribute("aria-label")).toBe("00:20, Thu. Open in Evening journal");
  });

  it("marks her reply after midnight with its weekday too", () => {
    const log = DayLogShape({
      ...fixtures.day,
      lines: [
        { stem: "2026-10-14-evening", key: "words:0", kind: "his", date: "2026-10-14", time: "23:12", text: "late", source: "text", audio: null, reply_to: null },
        { stem: "2026-10-14-evening", key: "her:1", kind: "hers", date: "2026-10-15", time: "00:21", text: "Still up." },
      ],
    });
    const host = mount(<DayLog>{logRows(log)}</DayLog>);
    expect(host.querySelector(".log-hers-head")?.textContent).toBe("Ada · Thu 00:21");
    expect(host.querySelector(".log-hers .margin-note .log-day")?.textContent).toBe("Thu ");
  });

  it("says so when nothing was written that day", async () => {
    visit("/days/2026-10-01");
    stubFetch({ ...base, "/api/v1/days/2026-10-01": { ...fixtures.day, date: "2026-10-01", weekday: "Thu", entries: [], lines: [], reflection: null, prev: null, next: "2026-10-14" } });
    const host = mount(<App />);
    await until(() => must(host.querySelector(".empty")));
    expect(host.querySelector(".empty")?.textContent).toBe("Nothing written this day.");
  });

  it("is reached from the Entries list's day headers and from Today", async () => {
    visit("/entries");
    stubFetch(base);
    const host = mount(<App />);
    await until(() => must(host.querySelector(".entry-row")));
    expect([...host.querySelectorAll(".day h3 a")].map((a) => a.getAttribute("href"))).toEqual(["/days/2026-10-15", "/days/2026-10-14"]);
    visit("/");
    const today = mount(<App />);
    await until(() => must(today.querySelector(".written-list")));
    expect(today.querySelector("a.whole-day")?.getAttribute("href")).toBe("/days/2026-10-15");
  });
});
