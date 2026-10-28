import { render, type ComponentChild } from "preact";
import { LocationProvider } from "preact-iso";
import { afterEach, vi } from "vitest";
import { setAuthLost } from "../src/auth";
import { closeEvents } from "../src/events";

import days from "../../tests/fixtures/api/days.json";
import entries from "../../tests/fixtures/api/entries.json";
import entryEvening from "../../tests/fixtures/api/entry-evening.json";
import entryNote from "../../tests/fixtures/api/entry-note.json";
import entrySession from "../../tests/fixtures/api/entry-session.json";
import insightsWeeks from "../../tests/fixtures/api/insights-weeks.json";
import library from "../../tests/fixtures/api/library.json";
import libraryFixLife from "../../tests/fixtures/api/library-fix-life.json";
import libraryGratitude from "../../tests/fixtures/api/library-gratitude.json";
import libraryMidweekDefault from "../../tests/fixtures/api/library-midweek-default.json";
import libraryMidweekEdited from "../../tests/fixtures/api/library-midweek-edited.json";
import libraryValidate from "../../tests/fixtures/api/library-validate.json";
import reflections from "../../tests/fixtures/api/reflections.json";
import runOne from "../../tests/fixtures/api/run-one.json";
import runs from "../../tests/fixtures/api/runs.json";
import session from "../../tests/fixtures/api/session.json";
import settings from "../../tests/fixtures/api/settings.json";
import today from "../../tests/fixtures/api/today.json";
import todayPrompts from "../../tests/fixtures/api/today-prompts.json";

/** A working `localStorage`: newer Node versions put a global of their own (without methods unless given a file) in
 *  front of jsdom's, so the tests install a plain in-memory one when the one they find cannot store. */
class MemoryStorage implements Storage {
  private items = new Map<string, string>();
  get length(): number { return this.items.size; }
  clear(): void { this.items.clear(); }
  getItem(key: string): string | null { return this.items.get(key) ?? null; }
  key(i: number): string | null { return [...this.items.keys()][i] ?? null; }
  removeItem(key: string): void { this.items.delete(key); }
  setItem(key: string, value: string): void { this.items.set(key, String(value)); }
  [name: string]: unknown;
}
if (typeof (globalThis as { localStorage?: Storage }).localStorage?.setItem !== "function") {
  const store = new MemoryStorage();
  Object.defineProperty(globalThis, "localStorage", { value: store, configurable: true, writable: true });
  if (typeof window !== "undefined" && window !== globalThis) Object.defineProperty(window, "localStorage", { value: store, configurable: true });
}

/** Everything a storage holds, as one string (to check that something is not kept). */
export function storedText(s: Storage): string {
  const out: string[] = [];
  for (let i = 0; i < s.length; i++) {
    const k = s.key(i);
    if (k !== null) out.push(`${k}=${s.getItem(k) ?? ""}`);
  }
  return out.join("\n");
}

/** The server's answers, as pinned by tests/test_web_fixtures.py. */
export const fixtures = {
  days, entries, entryEvening, entryNote, entrySession, insightsWeeks, library, libraryFixLife, libraryGratitude, libraryMidweekDefault,
  libraryMidweekEdited, libraryValidate, reflections, runOne, runs, session, settings, today, todayPrompts,
} as const;

const mounted: HTMLElement[] = [];

export function mount(ui: ComponentChild): HTMLElement {
  const host = document.createElement("div");
  document.body.appendChild(host);
  mounted.push(host);
  render(<LocationProvider>{ui}</LocationProvider>, host);
  return host;
}

afterEach(() => {
  for (const host of mounted.splice(0)) {
    render(null, host);
    host.remove();
  }
  setAuthLost(false);
  closeEvents();
  sessionStorage.clear();
  localStorage.clear();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

/** Poll until `check` stops throwing (the views load in the background). */
export async function until<T>(check: () => T, ms = 2000): Promise<T> {
  const end = Date.now() + ms;
  for (;;) {
    try {
      return check();
    } catch (e) {
      if (Date.now() > end) throw e;
      await new Promise((r) => setTimeout(r, 10));
    }
  }
}

export function must<T>(value: T | null | undefined, what = "element"): T {
  if (value === null || value === undefined) throw new Error(`missing ${what}`);
  return value;
}

type Answer = unknown | ((url: URL, init?: RequestInit) => Response | unknown);

/** Replace `fetch` with a table of `/path` → JSON (or a function making the answer). Unlisted paths answer 404. */
export function stubFetch(table: Record<string, Answer>) {
  const calls: string[] = [];
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost:3000");
    calls.push(url.pathname + url.search);
    const hit = table[url.pathname + url.search] ?? table[url.pathname];
    if (hit === undefined) return json({ error: "not-found", detail: "no such thing" }, 404);
    const body = typeof hit === "function" ? (hit as (u: URL, i?: RequestInit) => unknown)(url, init) : hit;
    return body instanceof Response ? body : json(body);
  });
  vi.stubGlobal("fetch", fn);
  return { fn, calls };
}

export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

/** Let the views re-render: state changes are applied a moment after the event that caused them. */
export const tick = (): Promise<void> => new Promise((r) => setTimeout(r, 0));

/** Type into a field the way a person does: set the value, tell the framework, let it draw. */
export async function type(el: Element | null | undefined, value: string): Promise<void> {
  const field = must(el, "field") as HTMLInputElement | HTMLTextAreaElement;
  field.value = value;
  field.dispatchEvent(new Event("input", { bubbles: true }));
  await tick();
}

/** Choose an option of a `<select>`. */
export async function choose(el: Element | null | undefined, value: string): Promise<void> {
  const field = must(el, "select") as HTMLSelectElement;
  field.value = value;
  field.dispatchEvent(new Event("change", { bubbles: true }));
  await tick();
}

export async function click(el: Element | null | undefined): Promise<void> {
  (must(el, "button") as HTMLElement).click();
  await tick();
}

/** The button or link whose text is `label`. */
export function byText(host: ParentNode, selector: string, label: string | RegExp): HTMLElement | undefined {
  return [...host.querySelectorAll<HTMLElement>(selector)].find((e) =>
    typeof label === "string" ? e.textContent?.trim() === label : label.test(e.textContent ?? ""),
  );
}

/** A write the stub saw: its method, path and parsed JSON body. */
export interface Sent {
  method: string;
  path: string;
  body: unknown;
}

/**
 * `stubFetch` for a test that writes: `answers` maps "METHOD /path" to a body (or a function making a Response), and
 * every request is recorded in `sent` with its body. Unlisted writes answer 404, as unlisted reads do.
 */
export function stubApi(answers: Record<string, unknown | ((sent: Sent) => Response | unknown)>) {
  const sent: Sent[] = [];
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost:3000");
    const method = (init?.method ?? "GET").toUpperCase();
    let body: unknown = undefined;
    if (typeof init?.body === "string") body = JSON.parse(init.body);
    else if (init?.body instanceof FormData) body = init.body;
    const record: Sent = { method, path: url.pathname + url.search, body };
    sent.push(record);
    const hit = answers[`${method} ${url.pathname + url.search}`] ?? answers[`${method} ${url.pathname}`];
    if (hit === undefined) return json({ error: "not-found", detail: "no such thing" }, 404);
    const out = typeof hit === "function" ? (hit as (s: Sent) => unknown)(record) : hit;
    return out instanceof Response ? out : out === null ? new Response(null, { status: 204 }) : json(out, method === "POST" ? 201 : 200);
  });
  vi.stubGlobal("fetch", fn);
  return { sent, fn, writes: () => sent.filter((s) => s.method !== "GET") };
}
