import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "../src/app";
import { firstLine, progressOf, weekProse, weekTitle } from "../src/routes/insights";
import { healthLine } from "../src/routes/settings";
import { FIELDS, keyOf } from "../src/settingsModel";
import { Week } from "../src/types";
import { byText, click, fixtures, mount, must, stubApi, type, until } from "./helpers";

// WEB-UX, the pages builder's surfaces: Settings' section index and save bar, the defaults it says, Insights' prose
// figures. Layout itself (the save bar never covering the last row) is held in a browser: web/e2e/settings.spec.ts.

function visit(path: string) {
  window.history.replaceState(null, "", path);
}

const read = {
  "GET /api/v1/settings": fixtures.settings,
  "GET /api/v1/status": { now_local: "x", timezone: "x", last_tick: null, next: {}, warnings: [], recent_errors: [] },
  "GET /api/v1/doctor": [{ check: "config", status: "ok", detail: "loaded" }, { check: "agent", status: "fail", detail: "not found" }],
  "GET /api/v1/healthz": { ok: true, version: "1", code: "c", tick_last_at: null },
  "GET /api/v1/session": fixtures.session,
  "GET /api/v1/today": fixtures.today,
};

afterEach(() => {
  vi.restoreAllMocks();
  delete (Element.prototype as { scrollIntoView?: unknown }).scrollIntoView;
});

describe("Settings, the section index", () => {
  it("numbers the eight sections, jumps to one, and follows the scroll", async () => {
    visit("/settings");
    stubApi(read);
    const scrolled: string[] = [];
    // jsdom draws nothing, so it has no scrollIntoView: record what the page asks to bring into view
    Object.defineProperty(Element.prototype, "scrollIntoView", { configurable: true, writable: true, value: function (this: Element) { scrolled.push(this.id); } });
    const host = mount(<App />);
    const nav = await until(() => must(host.querySelector('nav[aria-label="Settings sections"]')));
    const links = [...nav.querySelectorAll<HTMLAnchorElement>("a")];
    await until(() => expect(links.map((a) => a.textContent)).toEqual([
      "01Ink and page", "02Place and time", "03Prompts", "04Reflections", "05In the chat", "06Programs", "07This device", "08Health(something to look at)",
    ]));   // Health carries a mark once the check has found something
    expect(links.map((a) => a.getAttribute("href"))).toEqual(links.map((a) => `#${must(host.querySelector(`section${a.getAttribute("href")}`)).id}`));
    expect(links[0]?.getAttribute("aria-current")).toBe("location");
    // a jump: the section comes to the top, its head takes the focus, the index marks it
    await click(links[7]);
    expect(scrolled).toEqual(["set-health"]);
    expect(document.activeElement?.id).toBe("set-health-h");
    expect(links[7]?.getAttribute("aria-current")).toBe("location");
    expect(links[0]?.hasAttribute("aria-current")).toBe(false);
    // the scroll: the last section whose head has passed the top fifth of the view is the one being read
    await new Promise((r) => setTimeout(r, 950));   // past the jump's own hold
    const tops: Record<string, number> = { "set-ink": -900, "set-place": -400, "set-prompts": 60, "set-reflections": 700 };
    vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (this: Element) {
      return { top: tops[this.id] ?? 2000, bottom: 0, left: 0, right: 0, width: 0, height: 0, x: 0, y: 0, toJSON: () => ({}) } as DOMRect;
    });
    document.dispatchEvent(new Event("scroll"));
    await until(() => expect(links[2]?.getAttribute("aria-current")).toBe("location"));
    expect(links[7]?.hasAttribute("aria-current")).toBe(false);
  });

  it("says at a glance what the health check found, how the browser is signed in, and her ink, each a way to its section", async () => {
    visit("/settings");
    stubApi(read);
    const host = mount(<App />);
    const glance = await until(() => must(host.querySelector('aside[aria-label="At a glance"]')));
    await until(() => expect(glance.textContent).toContain("1 not working"));
    await until(() => expect(glance.textContent).toContain("Paired with this browser"));
    expect(glance.textContent).toContain("Blue-black, a typewriter ribbon");
    expect([...glance.querySelectorAll("a")].map((a) => a.getAttribute("href"))).toEqual(["#set-health", "#set-device", "#set-ink"]);
    // choosing an ink is said there at once
    await click(must(host.querySelector<HTMLInputElement>('input[name="ink"][value="green"]')));
    expect(glance.textContent).toContain("Green, post-office ink");
  });
});

describe("Settings, the save bar", () => {
  it("rises with the count of changes, discards them, and stays last in the page so it never covers a row", async () => {
    visit("/settings");
    stubApi(read);
    const host = mount(<App />);
    const hours = await until(() => must(host.querySelector<HTMLInputElement>("#s-windows\\.collapse_hours")));
    const dock = must(host.querySelector(".save-dock"));
    expect(dock.getAttribute("data-shown")).toBe("false");
    expect(must(dock.querySelector(".save-bar")).getAttribute("aria-hidden")).toBe("true");   // hidden: out of the way of a reader
    // it is the form's last child: its height is the page's last padding, so at the bottom it sits under the last row
    expect(must(host.querySelector(".settings-form")).lastElementChild).toBe(dock);
    await click(host.querySelector('button[aria-label="More: Hours a prompt stays open"]'));
    expect(hours.value).toBe("6.5");   // a step of half an hour
    await click(host.querySelector('button[aria-label="Less: Minutes before annotating"]'));
    expect(must(host.querySelector<HTMLInputElement>("#s-windows\\.annotate_after_minutes")).value).toBe("15");   // unset: from its default, 20
    expect(dock.getAttribute("data-shown")).toBe("true");
    expect(dock.querySelector(".save-count")?.textContent).toBe("2 changes not saved");
    expect(dock.querySelector(".save-bar")?.hasAttribute("aria-hidden")).toBe(false);
    expect(hours.closest(".setting")?.classList.contains("changed")).toBe(true);
    await click(byText(host, "button", "Discard"));
    expect(hours.value).toBe("6");
    expect(dock.getAttribute("data-shown")).toBe("false");
  });

  it("says a moved value's default and puts it back; a flag left unset goes back to unset, not to a written value", async () => {
    visit("/settings");
    const { writes } = stubApi({ ...read, "PATCH /api/v1/settings": { written: { "windows.collapse_hours": "file" } } });
    const host = mount(<App />);
    const hours = await until(() => must(host.querySelector<HTMLInputElement>("#s-windows\\.collapse_hours")));
    await type(hours, "8");
    const row = must(hours.closest(".setting"));
    expect(row.querySelector(".default-hint")?.textContent).toBe("default 6 hoursUse default");
    await click(byText(row, "button", "Use default"));
    expect(hours.value).toBe("6");
    expect(row.querySelector(".default-hint")?.textContent).toBe("the default");
    // the day reflection is on in the file: off folds its latest time away; Use default writes it on again
    const day = must(host.querySelector("#s-reflections\\.day\\.enabled"));
    await click(day);
    expect(day.getAttribute("aria-checked")).toBe("false");
    expect(must(host.querySelector("#s-reflections\\.day\\.fallback_time")).closest(".set-fold")?.getAttribute("data-shut")).toBe("true");
    await click(byText(must(day.closest(".setting")), "button", "Use default"));
    expect(day.getAttribute("aria-checked")).toBe("true");
    expect(host.querySelector(".save-count")?.textContent).toBe("No changes");
    // an emptied box is left as it is: nothing is sent for it
    await type(hours, "");
    expect(row.textContent).toContain("Empty: left as it is");
    await type(must(host.querySelector("#s-today\\.max_chars")), "1500");
    await click(byText(host, "button", "Save"));
    await until(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]?.body).toEqual({ today: { max_chars: 1500 } });
  });
});

describe("Settings, the defaults it says", () => {
  // config.DEFAULTS is the engine's; the page says them beside each field and must not drift from it
  const config = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "..", "..", "src", "journal", "config.py"), "utf-8");
  const defaults = config.slice(config.indexOf("DEFAULTS: dict"), config.indexOf("\n}\n", config.indexOf("DEFAULTS: dict")));
  /** The value of `"leaf":` inside the block `"parent": {` of DEFAULTS. */
  function engine(path: string[]): string {
    let text = defaults;
    for (const part of path.slice(0, -1)) {
      const at = text.indexOf(`"${part}": {`);
      if (at < 0) throw new Error(`no ${part}`);
      text = text.slice(at + part.length + 5);
    }
    const leaf = path[path.length - 1] as string;
    const m = new RegExp(`"${leaf}":\\s*([^,}\\n#]+)`).exec(text);
    if (!m?.[1]) throw new Error(`no ${path.join(".")}`);
    return m[1].trim();
  }
  it("holds every field's default equal to the engine's", () => {
    const said = FIELDS.filter((f) => f.def !== undefined);
    expect(said.length).toBe(13);
    for (const f of said) {
      const v = engine(f.path);
      const want = v === "True" ? true : v === "False" ? false : /^"/.test(v) ? v.slice(1, -1) : Number(v);
      expect([keyOf(f), f.def]).toEqual([keyOf(f), want]);
    }
  });
});

describe("Insights, the figures in words", () => {
  const week = (w: Partial<Week>): Week => ({ week_end: "2026-10-18", entries: 0, words: 0, answered: 0, missed: 0, program_progress: {}, review: null, reflections: [], ...w });
  const text = (w: Week, her: string | null) => {
    const host = mount(<p>{weekProse(w, her).map((s, i) => <span key={i}>{i > 0 && " "}{s}</span>)}</p>);
    return host.textContent;
  };
  it("says only what the counts say, and leaves out a clause with nothing to say", () => {
    expect(text(week({}), "Ada")).toBe("Nothing was written this week.");
    expect(text(week({ entries: 10, words: 2117, answered: 5, missed: 1, reflections: ["a", "b"], review: "r" }), "Ada"))
      .toBe("You wrote 2,117 words in 10 entries. You answered 5 of 6 prompts from Ada. Ada wrote 2 day reflections and her review of the week.");
    expect(text(week({ entries: 1, words: 1, answered: 3, missed: 0 }), null)).toBe("You wrote 1 word in 1 entry. You answered all 3 prompts.");
    expect(text(week({ entries: 2, answered: 0, missed: 2, review: "r" }), null)).toBe("2 entries this week. 2 prompts passed unanswered. She wrote her review of the week.");
  });
  it("titles a week by its days and month, and reads a program's line and her first line", () => {
    expect(weekTitle("2026-10-18")).toEqual({ nums: "12 – 18", month: "October" });
    expect(weekTitle("2026-10-04")).toEqual({ nums: "28 – 4", month: "September into October" });
    expect(progressOf("3 of 10 sessions done, 1 this week")).toEqual({ done: 3, total: 10 });
    expect(progressOf("started")).toBeNull();
    expect(firstLine("\n**What stood out** — the harbour.\n\nMore.")).toBe("What stood out — the harbour.");
    expect(healthLine({ ok: 3, warn: 0, fail: 0 })).toBe("All is well");
    expect(healthLine({ ok: 3, warn: 2, fail: 1 })).toBe("1 not working, 2 to look at");
  });
  it("opens the reflection a link names, in its week", async () => {
    visit("/insights#2026-10-14-reflection");
    stubApi({ "GET /api/v1/today": fixtures.today, "GET /api/v1/insights/weeks": fixtures.insightsWeeks, "GET /api/v1/reflections": fixtures.reflections, "GET /api/v1/library": fixtures.library });
    const host = mount(<App />);
    const slip = await until(() => must(host.querySelector('[id="2026-10-14-reflection"]')));
    expect(slip.getAttribute("data-open")).toBe("true");
    expect(slip.textContent).toContain("To carry forward");
    expect(slip.querySelector(".from a")?.getAttribute("href")).toBe("/entries/2026-10-14-evening");
  });
});
