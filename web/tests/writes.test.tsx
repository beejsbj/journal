import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "../src/app";
import { loadDraft } from "../src/drafts";
import { closeEvents } from "../src/events";
import { json, fixtures, byText, choose, click, mount, must, storedText, stubApi, tick, type, until } from "./helpers";

// Everything that writes: the composer and the recorder, the entry's meta sheet, Today's buttons, the run screen,
// the program cards, settings and the "+" sheet. The server is a stub that records what it was sent.

function visit(path: string) {
  window.history.replaceState(null, "", path);
}

const reads = {
  "GET /api/v1/today": fixtures.today,
  "GET /api/v1/library": fixtures.library,
  "GET /api/v1/runs": [],
  "GET /api/v1/entries/2026-10-14-evening": fixtures.entryEvening,
  "GET /api/v1/reflections": fixtures.reflections,
};

afterEach(() => {
  vi.useRealTimers();
});

describe("The composer on an entry", () => {
  it("appends a block of his words, with a client id, and clears the draft", async () => {
    visit("/entries/2026-10-14-evening");
    const { writes } = stubApi({ ...reads, "POST /api/v1/entries/2026-10-14-evening/blocks": { key: "words:2" } });
    const host = mount(<App />);
    const box = await until(() => must(host.querySelector<HTMLTextAreaElement>(".composer textarea")));
    expect(host.querySelector(".document .composer")).toBeNull();   // no editor on any block: the composer is below the document
    await type(box, "One more thing, about the sea.");
    expect(loadDraft("entry:2026-10-14-evening")).toBe("One more thing, about the sea.");
    await click(byText(host, "button", "Add to the entry"));
    await until(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]?.body).toMatchObject({ text: "One more thing, about the sea.", client_id: expect.stringMatching(/^[\w.-]{1,64}$/) });
    await until(() => expect(host.querySelector<HTMLTextAreaElement>(".composer textarea")?.value).toBe(""));
    expect(loadDraft("entry:2026-10-14-evening")).toBe("");
  });

  it("keeps the text and the error when the write fails, and a retry is the same write", async () => {
    visit("/entries/2026-10-14-evening");
    let tries = 0;
    const { writes } = stubApi({
      ...reads,
      "POST /api/v1/entries/2026-10-14-evening/blocks": () => {
        tries += 1;
        return tries === 1 ? json({ error: "busy", detail: "The journal is busy; try again in a moment." }, 423) : { key: "words:2" };
      },
    });
    const host = mount(<App />);
    const box = await until(() => must(host.querySelector<HTMLTextAreaElement>(".composer textarea")));
    await type(box, "Words that must not be lost.");
    await click(byText(host, "button", "Add to the entry"));
    const err = await until(() => must(host.querySelector(".composer .error-line")));
    expect(err.textContent).toContain("busy");
    expect(box.value).toBe("Words that must not be lost.");
    await click(byText(err, "button", "Retry"));
    await until(() => expect(writes()).toHaveLength(2));
    const [first, second] = writes().map((w) => (w.body as { client_id: string }).client_id);
    expect(second).toBe(first);   // a retry after a lost answer cannot make two blocks
    await until(() => expect(box.value).toBe(""));
  });

  it("offline: says nothing is saved until he is back, keeps the draft, and the write fails with the same line", async () => {
    visit("/entries/2026-10-14-evening");
    stubApi({ ...reads, "POST /api/v1/entries/2026-10-14-evening/blocks": { key: "words:2" } });
    const inner = globalThis.fetch;
    vi.stubGlobal("fetch", (input: RequestInfo | URL, init?: RequestInit) =>
      init?.method === "POST" ? Promise.reject(new TypeError("Failed to fetch")) : inner(input, init));
    const online = vi.spyOn(navigator, "onLine", "get").mockReturnValue(false);
    try {
      const host = mount(<App />);
      const box = await until(() => must(host.querySelector<HTMLTextAreaElement>(".composer textarea")));
      expect(must(host.querySelector(".composer .offline-note")).textContent).toContain("You're offline; this will not be saved until you're back.");
      await type(box, "Written on the train.");
      await click(byText(host, "button", "Add to the entry"));
      const err = await until(() => must(host.querySelector(".composer .error-line")));
      expect(err.textContent).toContain("You're offline; this will not be saved until you're back.");
      expect(box.value).toBe("Written on the train.");
      expect(loadDraft("entry:2026-10-14-evening")).toBe("Written on the train.");
    } finally {
      online.mockRestore();
    }
  });

  it("refuses /x on the page's own terms: the server's 422 is shown and nothing is dropped", async () => {
    visit("/entries/2026-10-14-evening");
    stubApi({
      ...reads,
      "POST /api/v1/entries/2026-10-14-evening/blocks": json({ error: "off-the-record", detail: "this starts with the off-the-record command, so it is not saved; what you typed is still in your browser" }, 422),
    });
    const host = mount(<App />);
    const box = await until(() => must(host.querySelector<HTMLTextAreaElement>(".composer textarea")));
    await type(box, "/x not for the journal");
    await click(byText(host, "button", "Add to the entry"));
    const err = await until(() => must(host.querySelector(".composer .error-line")));
    expect(err.textContent).toContain("not saved");
    expect(byText(err, "button", "Retry")).toBeUndefined();   // sending the same words again would be refused again
    expect(box.value).toBe("/x not for the journal");
  });

  it("has no composer on the companion's reflection", async () => {
    visit("/entries/2026-10-14-reflection");
    stubApi({ ...reads, "GET /api/v1/entries/2026-10-14-reflection": { ...fixtures.entryEvening, stem: "2026-10-14-reflection", kind: "reflection" } });
    const host = mount(<App />);
    await until(() => must(host.querySelector(".document")));
    expect(host.querySelector(".composer")).toBeNull();
    expect(host.querySelector(".composer-closed")?.textContent).toContain("companion's reflection");
  });

  it("edits nothing: a program session sends its answers through the run", async () => {
    visit("/entries/2026-10-15-fix-life-s01");
    stubApi({ ...reads, "GET /api/v1/entries/2026-10-15-fix-life-s01": fixtures.entrySession });
    const host = mount(<App />);
    await until(() => must(host.querySelector(".document")));
    expect(host.querySelector(".composer")).toBeNull();
    expect(host.querySelector(".composer-closed a")?.getAttribute("href")).toBe("/runs/2026-10-15-a1f3");
  });
});

/** A recorder that produces one chunk when stopped, and a microphone that can be released. */
function fakeMicrophone() {
  const stopped = vi.fn();
  class FakeRecorder {
    static isTypeSupported = () => true;
    state = "inactive";
    mimeType = "audio/webm;codecs=opus";
    ondataavailable: ((e: { data: Blob }) => void) | null = null;
    onstop: (() => void) | null = null;
    constructor(public stream: unknown, public options?: { mimeType?: string }) {}
    start() {
      this.state = "recording";
    }
    stop() {
      this.state = "inactive";
      this.ondataavailable?.({ data: new Blob(["voice"], { type: this.mimeType }) });
      this.onstop?.();
    }
  }
  vi.stubGlobal("MediaRecorder", FakeRecorder);
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: { getUserMedia: async () => ({ getTracks: () => [{ stop: stopped }] }) },
  });
  return { stopped };
}

describe("Voice", () => {
  it("records, uploads the recording, then posts a block that names the upload", async () => {
    visit("/entries/2026-10-14-evening");
    const mic = fakeMicrophone();
    const { sent } = stubApi({
      ...reads,
      "POST /api/v1/uploads": { upload: "a".repeat(32), bytes: 5, mime: "audio/webm" },
      "POST /api/v1/entries/2026-10-14-evening/blocks": { key: "words:2" },
    });
    const host = mount(<App />);
    await until(() => must(host.querySelector(".composer")));
    await click(host.querySelector('[aria-label="Record a voice note"]'));
    await until(() => must(host.querySelector('[aria-label="Stop recording"]')));
    await click(host.querySelector('[aria-label="Stop recording"]'));
    await until(() => expect(host.textContent).toContain("Voice note"));
    expect(mic.stopped).toHaveBeenCalled();   // the microphone is released the moment recording stops
    await click(byText(host, "button", "Add to the entry"));
    await until(() => expect(sent.filter((s) => s.method === "POST")).toHaveLength(2));
    const [upload, block] = sent.filter((s) => s.method === "POST");
    expect(upload?.path).toBe("/api/v1/uploads");
    expect(upload?.body).toBeInstanceOf(FormData);
    const file = (upload?.body as FormData).get("file") as File;
    expect(file.type).toBe("audio/webm;codecs=opus");
    expect(file.name).toBe("recording.webm");
    expect(block?.path).toBe("/api/v1/entries/2026-10-14-evening/blocks");
    expect(block?.body).toMatchObject({ upload: "a".repeat(32) });
    expect(block?.body).not.toHaveProperty("text");
  });

  it("does not upload twice when the block fails: the retry reuses the upload", async () => {
    visit("/entries/2026-10-14-evening");
    fakeMicrophone();
    let tries = 0;
    const { sent } = stubApi({
      ...reads,
      "POST /api/v1/uploads": { upload: "b".repeat(32), bytes: 5, mime: "audio/webm" },
      "POST /api/v1/entries/2026-10-14-evening/blocks": () => (++tries === 1 ? json({ error: "busy", detail: "busy" }, 423) : { key: "words:2" }),
    });
    const host = mount(<App />);
    await until(() => must(host.querySelector(".composer")));
    await click(host.querySelector('[aria-label="Record a voice note"]'));
    await click(await until(() => must(host.querySelector('[aria-label="Stop recording"]'))));
    await until(() => expect(host.textContent).toContain("Voice note"));
    await click(byText(host, "button", "Add to the entry"));
    const err = await until(() => must(host.querySelector(".composer .error-line")));
    await click(byText(err, "button", "Retry"));
    await until(() => expect(sent.filter((s) => s.path.endsWith("/blocks"))).toHaveLength(2));
    expect(sent.filter((s) => s.path === "/api/v1/uploads")).toHaveLength(1);
  });

  it("uploads the kept recording again when the server dropped it (404 after an hour), under the same write id", async () => {
    visit("/entries/2026-10-14-evening");
    fakeMicrophone();
    let ups = 0;
    let tries = 0;
    const { sent } = stubApi({
      ...reads,
      "POST /api/v1/uploads": () => ({ upload: String(++ups).repeat(32), bytes: 5, mime: "audio/webm" }),
      "POST /api/v1/entries/2026-10-14-evening/blocks": () =>
        ++tries === 1 ? json({ error: "busy", detail: "busy" }, 423)
          : tries === 2 ? json({ error: "not-found", detail: "that upload is unknown, used or older than an hour" }, 404)
          : { key: "words:2" },
    });
    const host = mount(<App />);
    await until(() => must(host.querySelector(".composer")));
    await click(host.querySelector('[aria-label="Record a voice note"]'));
    await click(await until(() => must(host.querySelector('[aria-label="Stop recording"]'))));
    await until(() => expect(host.textContent).toContain("Voice note"));
    await click(byText(host, "button", "Add to the entry"));
    const err = await until(() => must(host.querySelector(".composer .error-line")));
    await click(byText(err, "button", "Retry"));     // the stored upload is gone: this must not fail for ever
    await until(() => expect(sent.filter((s) => s.path.endsWith("/blocks"))).toHaveLength(3));
    const posts = sent.filter((s) => s.path.endsWith("/blocks"));
    expect(sent.filter((s) => s.path === "/api/v1/uploads")).toHaveLength(2);
    expect(posts[2]?.body).toMatchObject({ upload: "2".repeat(32), client_id: (posts[0]?.body as { client_id: string }).client_id });
    await until(() => expect(host.querySelector(".composer .error-line")).toBeNull());
  });

  it("says so when the page cannot record", async () => {
    visit("/entries/2026-10-14-evening");
    vi.stubGlobal("MediaRecorder", undefined);
    stubApi(reads);
    const host = mount(<App />);
    await until(() => must(host.querySelector(".composer")));
    expect(host.querySelector(".recorder")).toBeNull();
    expect(host.querySelector(".composer-actions")?.textContent).toContain("Recording needs a secure page");
  });
});

describe("An entry that is his own page", () => {
  it("offers no composer and no meta sheet where the server would refuse a write", async () => {
    visit("/entries/2026-10-14-evening");
    stubApi({ ...reads, "GET /api/v1/entries/2026-10-14-evening": { ...fixtures.entryEvening, writable: false } });
    const host = mount(<App />);
    await until(() => must(host.querySelector(".page-head")));
    expect(host.querySelector(".composer")).toBeNull();
    expect(byText(host, "button", "Mood, people, star")).toBeUndefined();
    expect(host.querySelector(".composer-closed")?.textContent).toContain("This page is yours");
  });
});

describe("Mood, people and a star", () => {
  it("patches the front matter with the entry's mtime and reloads", async () => {
    visit("/entries/2026-10-14-evening");
    const { writes } = stubApi({ ...reads, "PATCH /api/v1/entries/2026-10-14-evening/meta": { meta: { mood: ["calm"], starred: true }, mtime: 1760000001 } });
    const host = mount(<App />);
    await until(() => must(host.querySelector(".page-head")));
    await click(byText(host, "button", "Mood, people, star"));
    const sheet = await until(() => must(host.querySelector('[role="dialog"]')));
    await type(sheet.querySelector("#tag-Mood"), "calm");
    sheet.querySelector("#tag-Mood")?.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    await tick();
    await click(sheet.querySelector('input[type="checkbox"]'));
    await click(byText(sheet, "button", "Save"));
    await until(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]?.body).toEqual({ mood: ["calm"], starred: true, mtime: 1760000000 });   // people untouched: not sent
    await until(() => expect(host.querySelector('[role="dialog"]')).toBeNull());
  });

  it("sends only the star when only the star changed: a mood and people he did not touch are never sent", async () => {
    visit("/entries/2026-10-14-evening");
    const withLists = { ...fixtures.entryEvening, meta: { mood: ["calm", "tired"], people: ["Sam"] } };
    const { writes } = stubApi({ ...reads, "GET /api/v1/entries/2026-10-14-evening": withLists, "PATCH /api/v1/entries/2026-10-14-evening/meta": { meta: { starred: true }, mtime: 1760000001 } });
    const host = mount(<App />);
    await until(() => must(host.querySelector(".page-head")));
    await click(byText(host, "button", "Mood, people, star"));
    const sheet = await until(() => must(host.querySelector('[role="dialog"]')));
    expect((byText(sheet, "button", "Save") as HTMLButtonElement).disabled).toBe(true);   // nothing changed yet
    await click(sheet.querySelector('input[type="checkbox"]'));
    await click(byText(sheet, "button", "Save"));
    await until(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]?.body).toEqual({ starred: true, mtime: 1760000000 });
  });

  it("shows the server's own line for a 409 that is not a moved file, with no reload offer", async () => {
    visit("/entries/2026-10-14-evening");
    stubApi({ ...reads, "PATCH /api/v1/entries/2026-10-14-evening/meta": json({ error: "not-writable", detail: "the entry has no front matter" }, 409) });
    const host = mount(<App />);
    await until(() => must(host.querySelector(".page-head")));
    await click(byText(host, "button", "Mood, people, star"));
    const sheet = await until(() => must(host.querySelector('[role="dialog"]')));
    await click(sheet.querySelector('input[type="checkbox"]'));
    await click(byText(sheet, "button", "Save"));
    const err = await until(() => must(sheet.querySelector(".error-line")));
    expect(err.textContent).toContain("no front matter");
    expect(err.textContent).not.toContain("Reload the entry");
  });

  it("offers a reload when the entry changed since it was read (409)", async () => {
    visit("/entries/2026-10-14-evening");
    stubApi({ ...reads, "PATCH /api/v1/entries/2026-10-14-evening/meta": json({ error: "mtime", detail: "the entry changed since you read it; reload it" }, 409) });
    const host = mount(<App />);
    await until(() => must(host.querySelector(".page-head")));
    await click(byText(host, "button", "Mood, people, star"));
    const sheet = await until(() => must(host.querySelector('[role="dialog"]')));
    await click(sheet.querySelector('input[type="checkbox"]'));
    await click(byText(sheet, "button", "Save"));
    const err = await until(() => must(sheet.querySelector(".error-line")));
    expect(byText(err, "button", "Reload the entry")).toBeDefined();
  });
});

describe("Today", () => {
  const open = {
    ...fixtures.today,
    practices: { rows: [{ id: "morning", state: "open", sent: "06:00", stem: "2026-10-15-morning" }, ...fixtures.today.practices.rows], open: ["morning"] },
  };

  it("replies to a waiting prompt as a new entry of that practice", async () => {
    visit("/");
    const { writes } = stubApi({ ...reads, "GET /api/v1/today": open, "POST /api/v1/entries": { stem: "2026-10-15-morning", path: "x", rule: "web" } });
    const host = mount(<App />);
    const card = await until(() => must(host.querySelector("li.practice.open")));
    await click(byText(card, "button", "Reply"));
    await type(card.querySelector("textarea"), "Slept badly, but the light is good.");
    await click(byText(card, "button", "Reply"));
    await until(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]?.body).toMatchObject({ practice: "morning", text: "Slept badly, but the light is good." });
    await until(() => expect(card.textContent).toContain("Saved."));
    expect(card.querySelector(".saved a")?.getAttribute("href")).toBe("/entries/2026-10-15-morning");
  });

  it("sends a practice now (spawned), says it is on its way, and clears on the ledger event", async () => {
    visit("/");
    const { writes } = stubApi({ ...reads, "POST /api/v1/library/gratitude/send": json({ spawned: true, log: "/x" }, 202) });
    const host = mount(<App />);
    const card = await until(() => must(host.querySelector("li.practice.upcoming")));
    await click(byText(card, "button", "Send now"));
    await until(() => expect(card.textContent).toContain("On its way"));
    expect(writes()[0]?.path).toBe("/api/v1/library/gratitude/send");
  });

  it("shows the error of a send that cannot start", async () => {
    visit("/");
    stubApi({ ...reads, "POST /api/v1/library/gratitude/send": json({ error: "engine-unavailable", detail: "cannot start it: OSError" }, 503) });
    const host = mount(<App />);
    const card = await until(() => must(host.querySelector("li.practice.upcoming")));
    await click(byText(card, "button", "Send now"));
    await until(() => expect(card.querySelector(".error-line")?.textContent).toContain("cannot start"));
  });

  const running = { ...fixtures.runs[0] };
  const withRun = { ...reads, "GET /api/v1/runs": [running] };

  it("shows a card for the run in progress, with Continue and Pause, and the empty state without one", async () => {
    visit("/");
    const { writes } = stubApi({ ...withRun, "POST /api/v1/runs/2026-10-15-run1/pause": json({ spawned: true, log: "/x" }, 202) });
    const host = mount(<App />);
    const card = await until(() => must(host.querySelector("li.run")));
    expect(card.textContent).toContain("session 1/10");
    expect(card.querySelector("a.button")?.getAttribute("href")).toBe("/runs/2026-10-15-run1");
    expect(card.querySelector('[role="progressbar"]')).not.toBeNull();
    await click(byText(card, "button", "Pause"));
    await until(() => expect(writes()).toHaveLength(1));
    await until(() => expect(card.textContent).toContain("Pausing…"));
  });

  it("offers Resume for a run on a break", async () => {
    visit("/");
    stubApi({ ...reads, "GET /api/v1/runs": [{ ...running, status: "paused" }] });
    const host = mount(<App />);
    const card = await until(() => must(host.querySelector("li.run")));
    expect(card.textContent).toContain("On a break");
    expect(byText(card, "button", "Resume")).toBeDefined();
  });

  it("says no program is running, and points at the Library", async () => {
    visit("/");
    stubApi(reads);
    const host = mount(<App />);
    await until(() => expect(host.textContent).toContain("No program running"));
    expect(byText(host, ".empty a", /Library/)?.getAttribute("href")).toBe("/library");
  });

  it("lists the questions still to come today, with their times", async () => {
    visit("/");
    const scheduled = [
      { session: 10, n: 1, of: 2, offset: "+2h", local: "2026-10-15T12:00", status: "pending", text: "q" },
      { session: 10, n: 2, of: 2, offset: "+5h", local: "2026-10-15T15:30", status: "pending", text: "q" },
      { session: 10, n: 3, of: 3, offset: "+1d", local: "2026-10-16T09:00", status: "pending", text: "tomorrow" },
      { session: 10, n: 4, of: 4, offset: "-1h", local: "2026-10-15T08:00", status: "sent", text: "sent already" },
    ];
    stubApi({ ...reads, "GET /api/v1/runs": [{ ...running, scheduled }] });
    const host = mount(<App />);
    await until(() => must(host.querySelector(".interrupts")));
    expect([...host.querySelectorAll(".interrupts li")].map((li) => li.querySelector("time")?.textContent)).toEqual(["12:00", "15:30"]);
  });

  it("lists session 10's questions on the day they go out, when the run is already completed, and can stop them", async () => {
    visit("/");
    const scheduled = [
      { session: 10, n: 1, of: 2, offset: "+1d", local: "2026-10-15T09:00", status: "pending", text: "q" },
      { session: 10, n: 2, of: 2, offset: "+1d", local: "2026-10-15T18:00", status: "pending", text: "q" },
    ];
    const { writes } = stubApi({
      ...reads,
      "GET /api/v1/runs": [{ ...running, status: "completed", scheduled }],
      "POST /api/v1/runs/2026-10-15-run1/abandon": json({ spawned: true, log: "/x" }, 202),
    });
    const host = mount(<App />);
    await until(() => must(host.querySelector(".interrupts")));
    expect([...host.querySelectorAll(".interrupts li")].map((li) => li.querySelector("time")?.textContent)).toEqual(["09:00", "18:00"]);
    expect(host.textContent).not.toContain("No program running");
    const card = must(host.querySelector("li.run"));
    expect(card.textContent).toContain("still on their way");
    await click(byText(card, "button", "Stop the questions"));
    await click(byText(card, "button", "Cancel them"));
    await until(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]?.path).toBe("/api/v1/runs/2026-10-15-run1/abandon");
  });

  it("shows no questions of an abandoned or expired run", async () => {
    visit("/");
    const scheduled = [{ session: 10, n: 1, of: 1, offset: "+1d", local: "2026-10-15T09:00", status: "pending", text: "q" }];
    stubApi({ ...reads, "GET /api/v1/runs": [{ ...running, status: "abandoned", scheduled }] });
    const host = mount(<App />);
    await until(() => expect(host.textContent).toContain("No program running"));
    expect(host.querySelector(".interrupts")).toBeNull();
  });

  it("falls back to the one-line summary when the runs cannot be read", async () => {
    visit("/");
    stubApi({ ...reads, "GET /api/v1/today": { ...fixtures.today, program: { text: "fix-life: session 1" } }, "GET /api/v1/runs": () => json({ error: "x", detail: "x" }, 500) });
    const host = mount(<App />);
    await until(() => expect(host.querySelector(".card.program")?.textContent).toBe("fix-life: session 1"));
  });
});

describe("The run screen", () => {
  const run = fixtures.runOne;
  const base = { ...reads, "GET /api/v1/runs/2026-10-15-run1": run };
  const spawned = () => json({ spawned: true, log: "/x" }, 202);

  it("shows the open phase and appends an answer to the session's entry, naming the phase", async () => {
    visit("/runs/2026-10-15-run1");
    const { writes } = stubApi({ ...base, "POST /api/v1/entries/2026-10-15-fix-life-s01-2/blocks": { key: "words:0", stem: "2026-10-15-fix-life-s01-2", advance: "settle" } });
    const host = mount(<App />);
    await until(() => must(host.querySelector(".phase")));
    expect(host.querySelector(".phase h2")?.textContent).toBe("Current Reality Excavation");
    expect(host.querySelector(".phase-strip")?.getAttribute("aria-label")).toBe("Phase 1 of 6");
    expect(host.querySelector(".phase")?.textContent).toContain("What's the dull, persistent dissatisfaction");
    await type(host.querySelector(".phase textarea"), "The calendar is always full.");
    await click(byText(host, "button", "Add to my answer"));
    await until(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]?.body).toMatchObject({ text: "The calendar is always full.", phase: 1 });
    await until(() => expect(host.querySelector(".phase .saved")?.textContent ?? "").toContain("next phase follows"));
  });

  it("says what Next does when the program waits for it", async () => {
    visit("/runs/2026-10-15-run1");
    stubApi({ ...base, "POST /api/v1/entries/2026-10-15-fix-life-s01-2/blocks": { key: "words:0", advance: "next" } });
    const host = mount(<App />);
    await until(() => must(host.querySelector(".phase textarea")));
    await type(host.querySelector(".phase textarea"), "x");
    await click(byText(host, "button", "Add to my answer"));
    await until(() => expect(host.querySelector(".phase .saved")?.textContent ?? "").toContain("Press Next"));
  });

  it("offers exactly the controls the server lists, and binds Next to the phase it was reading", async () => {
    visit("/runs/2026-10-15-run1");
    const { writes } = stubApi({ ...base, "POST /api/v1/runs/2026-10-15-run1/next": spawned });
    const host = mount(<App />);
    await until(() => must(host.querySelector(".controls")));
    expect([...host.querySelectorAll(".controls button")].map((b) => b.textContent)).toEqual(["Next", "Skip", "Pause", "Abandon"]);
    await click(byText(host, ".controls button", "Next"));
    await until(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]?.body).toEqual({ expect: "1:1:1" });
    await until(() => expect(host.querySelector(".controls")?.textContent).toContain("Moving on…"));
  });

  it("reads the run again when it has moved on (409)", async () => {
    visit("/runs/2026-10-15-run1");
    let reads2 = 0;
    stubApi({
      ...reads,
      "GET /api/v1/runs/2026-10-15-run1": () => { reads2 += 1; return run; },
      "POST /api/v1/runs/2026-10-15-run1/skip": json({ error: "moved-on", detail: "the run is not at that phase any more; reload it", where: "1:2:1" }, 409),
    });
    const host = mount(<App />);
    await until(() => must(host.querySelector(".controls")));
    await click(byText(host, ".controls button", "Skip"));
    await until(() => expect(host.querySelector(".controls .error-line")?.textContent).toContain("has moved on"));
    await until(() => expect(reads2).toBeGreaterThan(1));
  });

  it("keeps the words he had typed when the run moves to another phase, and offers them back", async () => {
    visit("/runs/2026-10-15-run1");
    let reads2 = 0;
    const later = { ...run, current: { session: 1, phase: 2 }, where: "1:2:1", phase: { ...run.phase, phase: 2, title: "Second phase" } };
    stubApi({
      ...reads,
      "GET /api/v1/runs/2026-10-15-run1": () => (++reads2 === 1 ? run : later),
      "POST /api/v1/runs/2026-10-15-run1/skip": json({ error: "moved-on", detail: "the run is not at that phase any more; reload it", where: "1:2:1" }, 409),
    });
    const host = mount(<App />);
    const box = await until(() => must(host.querySelector<HTMLTextAreaElement>(".composer textarea")));
    await type(box, "Half of an answer");
    await click(byText(host, ".controls button", "Skip"));
    await until(() => expect(host.querySelector(".phase h2")?.textContent).toBe("Second phase"));
    const kept = await until(() => must(host.querySelector(".composer-kept")));
    expect(kept.textContent).toContain("unsent words are kept");
    expect(host.querySelector<HTMLTextAreaElement>(".composer textarea")?.value).toBe("");
    await click(byText(kept, "button", "Put them back in the box"));
    expect(host.querySelector<HTMLTextAreaElement>(".composer textarea")?.value).toBe("Half of an answer");
    expect(host.querySelector(".composer-kept")).toBeNull();
  });

  it("asks before abandoning", async () => {
    visit("/runs/2026-10-15-run1");
    const { writes } = stubApi({ ...base, "POST /api/v1/runs/2026-10-15-run1/abandon": spawned });
    const host = mount(<App />);
    await until(() => must(host.querySelector(".controls")));
    await click(byText(host, ".controls button", "Abandon"));
    expect(writes()).toHaveLength(0);
    await click(byText(host, ".controls .confirm button", "Abandon"));
    await until(() => expect(writes()).toHaveLength(1));
  });

  it("closes the composer while the run is on a break, and offers Resume", async () => {
    visit("/runs/2026-10-15-run1");
    stubApi({ ...reads, "GET /api/v1/runs/2026-10-15-run1": { ...run, status: "paused", keyboard: ["resume", "abandon"] } });
    const host = mount(<App />);
    await until(() => must(host.querySelector(".controls")));
    expect(host.querySelector(".phase .composer")).toBeNull();
    expect(host.querySelector(".composer-closed")?.textContent).toContain("On a break");
    expect([...host.querySelectorAll(".controls button")].map((b) => b.textContent)).toEqual(["Resume", "Abandon"]);
  });

  it("shows a phase that was already sent as written, and a reflect phase's text when it has one", async () => {
    visit("/runs/2026-10-15-run1");
    stubApi({ ...reads, "GET /api/v1/runs/2026-10-15-run1": { ...run, phase: { ...run.phase, type: "reflect", sent_text: "You described a calendar that is always full." } } });
    const host = mount(<App />);
    await until(() => must(host.querySelector(".phase-sent")));
    expect(host.querySelector(".phase-sent")?.textContent).toContain("always full");
    expect(host.querySelector(".phase-sent")?.textContent).toContain("Written by the companion");   // her words never go unlabelled
  });

  it("compiles insights for a completed run that has none", async () => {
    visit("/runs/2026-10-15-run1");
    const { writes } = stubApi({
      ...reads,
      "GET /api/v1/runs/2026-10-15-run1": { ...run, status: "completed", phase: null, current: null, keyboard: [], insights_entry: null },
      "POST /api/v1/runs/2026-10-15-run1/insights": spawned,
    });
    const host = mount(<App />);
    await click(await until(() => must(byText(host, "button", "Compile insights"))));
    await until(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]?.path).toBe("/api/v1/runs/2026-10-15-run1/insights");
  });
});

describe("The Library", () => {
  const withGratitude = { ...reads, "GET /api/v1/library/gratitude": fixtures.libraryGratitude };

  it("sends a practice now", async () => {
    visit("/library/gratitude");
    const { writes } = stubApi({ ...withGratitude, "POST /api/v1/library/gratitude/send": json({ spawned: true, log: "/x" }, 202) });
    const host = mount(<App />);
    await click(await until(() => must(byText(host, "button", "Send now"))));
    await until(() => expect(writes()).toHaveLength(1));
  });

  it("turns a practice off by saving the file with enabled false, against the mtime it read", async () => {
    visit("/library/gratitude");
    const { writes } = stubApi({ ...withGratitude, "PUT /api/v1/library/gratitude": fixtures.libraryGratitude });
    const host = mount(<App />);
    await click(await until(() => must(byText(host, "button", "Turn off"))));
    await until(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]?.body).toMatchObject({ mtime: 1760000000, definition: { id: "gratitude", enabled: false } });
  });

  it("turns a built-in off in one save, in place, claiming no file of his", async () => {
    visit("/library/morning");
    const builtin = { ...fixtures.libraryGratitude, definition: { ...fixtures.libraryGratitude.definition, id: "morning", source: "builtin" }, mtime: 1700000000 };
    const edited = { ...fixtures.libraryGratitude, definition: { ...fixtures.libraryGratitude.definition, id: "morning", source: "fork" }, mtime: 1760000005 };
    const { writes } = stubApi({ ...reads, "GET /api/v1/library/morning": builtin, "PUT /api/v1/library/morning": edited });
    const host = mount(<App />);
    await click(await until(() => must(byText(host, "button", "Turn off"))));
    await until(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]?.method).toBe("PUT");
    expect(writes()[0]?.body).toMatchObject({ definition: { id: "morning", enabled: false } });
    expect(writes()[0]?.body).not.toHaveProperty("mtime");
  });

  it("offers Edit on a built-in, with no fork in the way", async () => {
    visit("/library/morning");
    const builtin = { ...fixtures.libraryGratitude, definition: { ...fixtures.libraryGratitude.definition, id: "morning", source: "builtin" } };
    stubApi({ ...reads, "GET /api/v1/library/morning": builtin });
    const host = mount(<App />);
    const edit = await until(() => must(byText(host, "a.button", "Edit")));
    expect(edit.getAttribute("href")).toBe("/library/morning/edit");
    expect(byText(host, "button", /Fork/)).toBeUndefined();
    expect(host.querySelector(".edited-note")).toBeNull();
  });

  it("says a built-in is edited, that its default has changed since, and resets it only after asking", async () => {
    visit("/library/midweek");
    const { writes } = stubApi({ ...reads, "GET /api/v1/library/midweek": fixtures.libraryMidweekEdited, "DELETE /api/v1/library/midweek": null });
    const host = mount(<App />);
    const note = await until(() => must(host.querySelector(".edited-note")));
    expect(host.querySelector(".eyebrow")?.textContent).toContain("edited");
    expect(note.textContent).toContain("Your version is used in place of the built-in");
    expect(note.querySelector(".default-changed")?.textContent).toContain("The default has changed since you edited it.");
    expect(byText(note, "a.button", "See what changed")?.getAttribute("href")).toBe("/library/midweek/default");
    await click(byText(note, "button", "Reset to default"));
    expect(writes()).toHaveLength(0);
    expect(note.querySelector(".confirm")?.textContent).toContain("Replace your version with the default?");
    await click(byText(note, ".confirm button", "Reset to default"));
    await until(() => expect(writes().map((w) => `${w.method} ${w.path}`)).toEqual(["DELETE /api/v1/library/midweek"]));
    await until(() => expect(host.querySelector(".edited-note .confirm")).toBeNull());   // the page is read again
  });

  it("shows the default beside his edit: what changed since, his version against it, and Reset", async () => {
    visit("/library/midweek/default");
    const { writes } = stubApi({ ...reads, "GET /api/v1/library/midweek/default": fixtures.libraryMidweekDefault, "DELETE /api/v1/library/midweek": null });
    const host = mount(<App />);
    const since = await until(() => must(host.querySelector('[aria-labelledby="since-h"] .diff')));
    expect(since.querySelector(".diff-line.ins")?.textContent).toContain("aligned or drifted?");
    expect(since.querySelector(".diff-line.del")?.textContent).toContain("on track?");
    const yours = must(byText(host, ".diff-section summary", "Your version against the default")).parentElement;
    expect(yours?.querySelector(".diff-line.ins")?.textContent).toContain("Where has your energy gone this week?");
    expect(host.querySelector(".eyebrow")?.textContent).toContain("The default");
    expect(host.querySelectorAll(".question-set li").length).toBeGreaterThan(0);   // the default's own questions
    await click(byText(host, "button", "Reset to default"));
    await click(byText(host, ".confirm button", "Reset to default"));
    await until(() => expect(writes().map((w) => w.method)).toEqual(["DELETE"]));
    await until(() => expect(window.location.pathname).toBe("/library/midweek"));
  });

  it("marks an edited built-in whose default changed on its Library card", async () => {
    visit("/library");
    const lib = fixtures.library.map((i) => (i.id === "midweek" ? { ...i, source: "fork", default_changed: true } : i));
    stubApi({ ...reads, "GET /api/v1/library": lib });
    const host = mount(<App />);
    const card = await until(() => must(host.querySelector('a[href="/library/midweek"]')));
    expect(card.querySelector(".badge.source")?.textContent).toBe("edited");
    expect(card.querySelector(".badge.changed")?.textContent).toBe("default changed");
  });

  it("offers Edit on a program too", async () => {
    visit("/library/fix-life");
    stubApi({ ...reads, "GET /api/v1/library/fix-life": fixtures.libraryFixLife });
    const host = mount(<App />);
    await until(() => expect(byText(host, "a.button", "Edit")?.getAttribute("href")).toBe("/library/fix-life/edit"));
  });

  it("starts a program, and carries on with a run that is going", async () => {
    visit("/library/fix-life");
    const program = {
      ...fixtures.libraryGratitude,
      definition: { ...fixtures.libraryGratitude.definition, id: "fix-life", kind: "program", schedule: [], sections: [] },
      sessions: [{ number: 1, title: "S1", phases: [{ number: 1, title: "P1", type: "ask", questions: [] }] }],
    };
    const { writes } = stubApi({ ...reads, "GET /api/v1/library/fix-life": program, "POST /api/v1/runs": json({ spawned: true, log: "/x" }, 202) });
    const host = mount(<App />);
    await click(await until(() => must(byText(host, "button", "Start"))));
    await until(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]?.body).toEqual({ program: "fix-life" });
    closeEvents();
    stubApi({ ...reads, "GET /api/v1/library/fix-life": program, "GET /api/v1/runs": fixtures.runs });
    const again = mount(<App />);
    await until(() => expect(byText(again, "a.button", "Continue")?.getAttribute("href")).toBe("/runs/2026-10-15-run1"));
  });

  it("names the run in progress when a start is refused", async () => {
    visit("/library/fix-life");
    const program = {
      ...fixtures.libraryGratitude,
      definition: { ...fixtures.libraryGratitude.definition, id: "fix-life", kind: "program", schedule: [], sections: [] },
      sessions: [{ number: 1, title: "S1", phases: [] }],
    };
    stubApi({ ...reads, "GET /api/v1/library/fix-life": program, "POST /api/v1/runs": json({ error: "active-run", detail: "this program has a run in progress", run: "r9" }, 409) });
    const host = mount(<App />);
    await click(await until(() => must(byText(host, "button", "Start"))));
    await until(() => expect(host.querySelector('a[href="/runs/r9"]')).not.toBeNull());
  });

  it("links to the other program's unfinished run when one program at a time refuses a start", async () => {
    visit("/library/my-program");
    const program = {
      ...fixtures.libraryGratitude,
      definition: { ...fixtures.libraryGratitude.definition, id: "my-program", kind: "program", schedule: [], sections: [] },
      sessions: [{ number: 1, title: "S1", phases: [] }],
    };
    const detail = "Fix your entire life in 1 day isn't finished (session 3 of 10 is next: Three), and only one program runs at a time. Finish it or abandon it first.";
    stubApi({ ...reads, "GET /api/v1/library/my-program": program, "POST /api/v1/runs": json({ error: "program-in-progress", detail, run: "r7", program: "fix-life" }, 409) });
    const host = mount(<App />);
    await click(await until(() => must(byText(host, "button", "Start"))));
    const link = await until(() => must(host.querySelector('a[href="/runs/r7"]')));
    expect(link.textContent).toBe("Carry on with it");
    expect(link.closest("p")?.textContent).toContain("only one program runs at a time");
  });

  it("draws the progress of a running program on its card", async () => {
    visit("/library");
    const lib = fixtures.library.map((i) => (i.id === "fix-life" ? { ...i, active_run: "2026-10-15-run1" } : i));
    stubApi({ ...reads, "GET /api/v1/library": lib, "GET /api/v1/runs": fixtures.runs });
    const host = mount(<App />);
    await until(() => must(host.querySelector('a[href="/library/fix-life"] [role="progressbar"]')));
    expect(host.querySelector('a[href="/library/fix-life"]')?.textContent).toContain("0 of 10 sessions");
  });
});

describe("Settings", () => {
  const settings = {
    settings: { location: "Hillside", windows: { collapse_hours: 6 }, conversations: { quiet_minutes: 45 }, timezone: "Asia/Kolkata" },
    origins: { location: "file", windows: "file", conversations: "hermes" },
  };
  const read = {
    "GET /api/v1/settings": settings,
    "GET /api/v1/status": { now_local: "x", timezone: "x", last_tick: null, next: {}, warnings: [], recent_errors: [] },
    "GET /api/v1/doctor": [],
    "GET /api/v1/healthz": { ok: true, version: "1", code: "c", tick_last_at: null },
  };

  it("sends only what changed, nested, and reads the settings again", async () => {
    visit("/settings");
    const { writes } = stubApi({ ...read, "PATCH /api/v1/settings": { written: { "windows.collapse_hours": "file", "today.enabled": "file" } } });
    const host = mount(<App />);
    const hours = await until(() => must(host.querySelector<HTMLInputElement>("#s-windows\\.collapse_hours")));
    expect(hours.value).toBe("6");
    await type(hours, "8");
    await choose(host.querySelector("#s-today\\.enabled"), "off");
    await click(byText(host, "button", "Save settings"));
    await until(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]?.body).toEqual({ windows: { collapse_hours: 8 }, today: { enabled: false } });
    await until(() => expect(host.querySelector(".settings-form .saved")?.textContent).toContain("windows.collapse_hours (config file)"));
  });

  it("shows which layer each field is in", async () => {
    visit("/settings");
    stubApi(read);
    const host = mount(<App />);
    const hours = await until(() => must(host.querySelector<HTMLInputElement>("#s-windows\\.collapse_hours")));
    expect(hours.closest(".field")?.textContent).toContain("In the config file.");
    const cache = must(host.querySelector<HTMLInputElement>("#s-today\\.max_chars"));
    expect(cache.closest(".field")?.textContent).toContain("default applies");
  });

  it("locks what Hermes owns, with the line that changes it", async () => {
    visit("/settings");
    stubApi(read);
    const host = mount(<App />);
    const quiet = await until(() => must(host.querySelector<HTMLInputElement>("#s-conversations\\.quiet_minutes")));
    expect(quiet.disabled).toBe(true);
    expect(quiet.closest(".field")?.textContent).toContain("hermes config set plugins.entries.journal.settings.conversations.quiet_minutes");
  });

  it("will not send a value outside the range, and shows the server's reason for one it refuses", async () => {
    visit("/settings");
    stubApi({ ...read, "PATCH /api/v1/settings": json({ error: "invalid", detail: "a value is not allowed; nothing was saved", problems: ["windows.collapse_hours: 5 is not an allowed value"] }, 422) });
    const host = mount(<App />);
    const hours = await until(() => must(host.querySelector<HTMLInputElement>("#s-windows\\.collapse_hours")));
    await type(hours, "100");
    expect(byText(host, "button", "Save settings")?.hasAttribute("disabled")).toBe(true);
    expect(hours.closest(".field")?.textContent).toContain("At most 72");
    await type(hours, "5");
    await click(byText(host, "button", "Save settings"));
    await until(() => expect(host.querySelector(".settings-form .error-line")?.textContent).toContain("is not an allowed value"));
  });
});

describe("Use my location", () => {
  const settings = fixtures.settings;
  const read = {
    "GET /api/v1/settings": settings,
    "GET /api/v1/status": { now_local: "x", timezone: "x", last_tick: null, next: {}, warnings: [], recent_errors: [] },
    "GET /api/v1/doctor": [],
    "GET /api/v1/healthz": { ok: true, version: "1", code: "c", tick_last_at: null },
  };
  const geo = (fn: (ok: PositionCallback, fail: PositionErrorCallback) => void) =>
    Object.defineProperty(navigator, "geolocation", { configurable: true, value: { getCurrentPosition: fn } });
  afterEach(() => {
    delete (navigator as unknown as Record<string, unknown>).geolocation;
  });

  it("sends the position once and says where the journal now is", async () => {
    geo((ok) => ok({ coords: { latitude: 9.9812, longitude: 76.2834 } } as GeolocationPosition));
    visit("/settings");
    let located = false;
    const after = { ...settings, settings: { ...settings.settings, location: "Kochi, Kerala, India", timezone: "Asia/Kolkata", coordinates: { lat: 9.97, lon: 76.25, place: "Kochi, Kerala, India", source: "openstreetmap" } } };
    const { writes } = stubApi({
      ...read,
      "GET /api/v1/settings": () => (located ? after : settings),
      "POST /api/v1/settings/location": () => {
        located = true;
        return json({ written: { location: "file", timezone: "file", coordinates: "file" }, location: "Kochi, Kerala, India", timezone: "Asia/Kolkata", local_time_now: "Thu 21:40" });
      },
    });
    const host = mount(<App />);
    await click(await until(() => must(byText(host, "button", "Use my location"))));
    await until(() => expect(host.querySelector(".locate .saved")?.textContent).toBe("Now in Kochi, Kerala, India · Asia/Kolkata · Thu 21:40 there."));
    expect(writes()).toEqual([{ method: "POST", path: "/api/v1/settings/location", body: { lat: 9.9812, lon: 76.2834 } }]);
    expect(storedText(sessionStorage) + storedText(localStorage)).not.toContain("9.98");   // the position is not kept
    // the place shown is OpenStreetMap's: the credit stays with it, not with the moment it was found
    const place = await until(() => must(host.querySelector<HTMLInputElement>("#s-location")));
    await until(() => expect(place.closest(".field")?.querySelector(".credit")?.textContent).toBe("Place name © OpenStreetMap contributors"));
    expect(place.closest(".field")?.querySelector<HTMLAnchorElement>(".credit a")?.href).toBe("https://www.openstreetmap.org/copyright");
    await type(place, "Faro");   // typing another place: the credit is not his to carry
    expect(place.closest(".field")?.querySelector(".credit")).toBeNull();
  });

  it("credits nothing for a place typed by hand", async () => {
    visit("/settings");
    stubApi(read);
    const host = mount(<App />);
    await until(() => must(host.querySelector("#s-location")));
    expect(host.querySelector(".credit")).toBeNull();
  });

  it("drops a place typed and not saved, so the next Save cannot undo the place just found", async () => {
    geo((ok) => ok({ coords: { latitude: 9.9812, longitude: 76.2834 } } as GeolocationPosition));
    visit("/settings");
    const { writes } = stubApi({ ...read, "POST /api/v1/settings/location": json({ written: { location: "file", timezone: "file", coordinates: "file" }, location: "Kochi, Kerala, India", timezone: "Asia/Kolkata", local_time_now: "Thu 21:40" }) });
    const host = mount(<App />);
    const place = await until(() => must(host.querySelector<HTMLInputElement>("#s-location")));
    const stored = place.value;
    await type(place, "Faro");
    await type(must(host.querySelector("#s-timezone")), "Europe/Lisbon");
    expect(byText(host, "button", "Save settings")?.hasAttribute("disabled")).toBe(false);
    await click(byText(host, "button", "Use my location"));
    await until(() => must(host.querySelector(".locate .saved")));
    await until(() => expect(must(host.querySelector<HTMLInputElement>("#s-location")).value).toBe(stored));
    expect(byText(host, "button", "Save settings")?.hasAttribute("disabled")).toBe(true);
    expect(writes().map((x) => x.path)).toEqual(["/api/v1/settings/location"]);
  });

  it("says plainly when the browser will not share it, and sends nothing", async () => {
    geo((_ok, fail) => fail({ code: 1, message: "denied" } as GeolocationPositionError));
    visit("/settings");
    const { writes } = stubApi(read);
    const host = mount(<App />);
    await click(await until(() => must(byText(host, "button", "Use my location"))));
    await until(() => expect(host.querySelector(".locate .field-error")?.textContent).toContain("Location is blocked for this site"));
    expect(writes()).toEqual([]);
  });

  it("says when finding the position took too long", async () => {
    geo((_ok, fail) => fail({ code: 3, message: "timeout" } as GeolocationPositionError));
    visit("/settings");
    stubApi(read);
    const host = mount(<App />);
    await click(await until(() => must(byText(host, "button", "Use my location"))));
    await until(() => expect(host.querySelector(".locate .field-error")?.textContent).toContain("took too long"));
  });

  it("shows the server's reason when the place cannot be named", async () => {
    geo((ok) => ok({ coords: { latitude: 0, longitude: -30 } } as GeolocationPosition));
    visit("/settings");
    stubApi({ ...read, "POST /api/v1/settings/location": json({ error: "invalid", detail: "no named place is known at that position" }, 422) });
    const host = mount(<App />);
    await click(await until(() => must(byText(host, "button", "Use my location"))));
    await until(() => expect(host.querySelector(".locate .error-line")?.textContent).toContain("no named place"));
  });
});

describe("The + sheet", () => {
  it("saves a quick note as an entry of its own", async () => {
    visit("/");
    const { writes } = stubApi({ ...reads, "POST /api/v1/entries": { stem: "2026-10-15-j-2", path: "x", rule: "/j" } });
    const host = mount(<App />);
    await until(() => must(host.querySelector("li.practice")));
    await click(byText(host, "button.plus", "Write"));
    await click(await until(() => must(byText(host, '[role="dialog"] button', "Quick note"))));
    await type(host.querySelector('[role="dialog"] textarea'), "Buy stamps.");
    await click(byText(host, '[role="dialog"] button', "Save the note"));
    await until(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]?.body).toMatchObject({ text: "Buy stamps." });
    expect(writes()[0]?.body).not.toHaveProperty("practice");
    await until(() => expect(host.querySelector('[role="dialog"] .saved')?.textContent).toContain("Saved as a note"));
  });

  it("answers a prompt that is waiting", async () => {
    visit("/");
    const today = { ...fixtures.today, practices: { rows: [{ id: "gratitude", state: "open", sent: "21:30" }], open: ["gratitude"] } };
    const { writes } = stubApi({ ...reads, "GET /api/v1/today": today, "POST /api/v1/entries": { stem: "2026-10-15-gratitude", path: "x", rule: "web" } });
    const host = mount(<App />);
    await until(() => must(host.querySelector("li.practice")));
    await click(byText(host, "button.plus", "Write"));
    await click(await until(() => must(byText(host, '[role="dialog"] button', "Answer today's prompt"))));
    await click(await until(() => must(byText(host, '[role="dialog"] .picks button', /Daily gratitude/))));
    await type(host.querySelector('[role="dialog"] textarea'), "The tea.");
    await click(byText(host, '[role="dialog"] button', "Reply"));
    await until(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]?.body).toMatchObject({ practice: "gratitude", text: "The tea." });
  });

  it("closes on Escape", async () => {
    visit("/");
    stubApi(reads);
    const host = mount(<App />);
    await until(() => must(host.querySelector("li.practice")));
    await click(byText(host, "button.plus", "Write"));
    await until(() => must(host.querySelector('[role="dialog"]')));
    // (the sheet listens once its effect has run, a moment after it is drawn)
    await until(() => {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
      expect(host.querySelector('[role="dialog"]')).toBeNull();
    });
  });
});
