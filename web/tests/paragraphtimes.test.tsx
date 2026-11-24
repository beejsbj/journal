import { describe, expect, it } from "vitest";
import { App } from "../src/app";
import { Document } from "../src/components/Document";
import { loadStamps, saveDraft, saveStamps } from "../src/drafts";
import { joinStamps, restamp, stampsFor, timesToSend } from "../src/paragraphTimes";
import { EntryDoc } from "../src/types";
import { HourLines, shownHours } from "../src/ui";
import { byText, click, fixtures, mount, must, stubApi, type, until } from "./helpers";

// Issue #40: the hour each paragraph was begun. The box notes it while he writes, the save sends it, the entry shows it.

describe("Stamping the paragraphs of a draft", () => {
  it("gives a paragraph the moment its first letter arrives, and none to a blank line", () => {
    let s = restamp("", [null], "T", 100);
    expect(s).toEqual([100]);
    s = restamp("T", s, "The sea", 200);
    expect(s).toEqual([100]);                     // the same line, still being typed
    s = restamp("The sea", s, "The sea\n", 300);
    expect(s).toEqual([100, null]);               // Enter: a line with nothing in it yet
    s = restamp("The sea\n", s, "The sea\n\n", 350);
    expect(s).toEqual([100, null, null]);
    s = restamp("The sea\n\n", s, "The sea\n\nA", 400);
    expect(s).toEqual([100, null, 400]);
  });

  it("keeps a paragraph's stamp while he edits it, wherever the change is", () => {
    const text = "one\ntwo\nthree";
    const s = [1, 2, 3];
    expect(restamp(text, s, "one\ntwo, then\nthree", 9)).toEqual([1, 2, 3]);       // typed inside a line
    expect(restamp(text, s, "one\nand two\nthree", 9)).toEqual([1, 2, 3]);         // a word in front
    expect(restamp(text, s, "one\nthree", 9)).toEqual([1, 3]);                     // a line taken out
    expect(restamp(text, s, "one\ntwothree", 9)).toEqual([1, 2]);                  // two lines joined: the first's
    expect(restamp(text, s, "one\ntw\no\nthree", 9)).toEqual([1, 2, 9, 3]);         // Enter inside a line: the rest is new
    expect(restamp(text, s, "zero\none\ntwo\nthree", 9)).toEqual([9, 1, 2, 3]);     // a paragraph added above
    expect(restamp(text, s, "Pasted over it all", 9)).toEqual([9]);                // replaced whole: begun now
  });

  it("trusts kept stamps only when they still fit the text", () => {
    expect(stampsFor("a\n\nb", [1, null, 2])).toEqual([1, null, 2]);
    expect(stampsFor("a\n\nb", [1, 2])).toEqual([null, null, null]);
    expect(stampsFor("a\n\nb", "nonsense")).toEqual([null, null, null]);
    expect(stampsFor("a\n\nb", [1, 5, "x"])).toEqual([1, null, null]);
    expect(restamp("a\nb", [1], "a\nb!", 9)).toEqual([null, null]);   // stamps that do not fit are none seen
  });

  it("sends a time for every paragraph, or none at all", () => {
    const t = Date.UTC(2026, 9, 15, 3, 42);
    expect(timesToSend("one\n\ntwo", [t, null, t + 60_000])).toEqual(["2026-10-15T03:42:00.000Z", "2026-10-15T03:43:00.000Z"]);
    expect(timesToSend("one\ntwo", [t, null])).toBeUndefined();     // a paragraph whose start it did not see
    expect(timesToSend("one\ntwo", [t])).toBeUndefined();
    expect(timesToSend("\n", [null, null])).toBeUndefined();
  });

  it("carries the stamps with words moved into another draft", () => {
    expect(joinStamps("", [null], "n1\nn2", [5, 6])).toEqual([5, 6]);
    expect(joinStamps("had\n\n", [1, null, null], "n1", [5])).toEqual([1, null, 5]);
  });

  it("keeps them beside the draft, and an emptied draft forgets them", () => {
    saveStamps("entry:x", [1, null]);
    expect(loadStamps("entry:x")).toEqual([1, null]);
    saveDraft("entry:x", "");
    expect(loadStamps("entry:x")).toBeNull();
  });
});

describe("The hours in the margin", () => {
  it("show where the hour changes, after the one already shown", () => {
    expect(shownHours(["23:12", "23:12", null, "23:20", "23:12"])).toEqual(["23:12", null, null, "23:20", "23:12"]);
    expect(shownHours(["23:12", "23:15"], "23:12")).toEqual([null, "23:15"]);
  });

  it("set his words line for line, each hour beside its own paragraph", () => {
    const host = mount(<HourLines text={"One.\nTwo.\n\nThree."} times={["09:01", "09:01", "09:07"]} />);
    const lines = [...host.querySelectorAll(".hour-line")];
    expect(lines.map((l) => l.textContent)).toEqual(["09:01One.", "Two.", "", "09:07Three."]);
    expect(lines[2]?.querySelector("br")).not.toBeNull();   // a blank line keeps its height
  });
});

describe("An entry's paragraph times", () => {
  const withTimes = () => {
    const raw = structuredClone(fixtures.entryEvening) as unknown as { blocks: Record<string, unknown>[] };
    const first = must(raw.blocks[0]);
    first.text = "The lighthouse first.\nThen the harbour.\n\nThen home.";
    first.times = ["23:02", "23:09", "23:09"];
    return EntryDoc(raw);
  };

  it("hangs the first hour beside the block and the others beside their paragraphs", () => {
    const host = mount(<Document doc={withTimes()} />);
    const first = must(host.querySelector("article.block.his"));
    expect(first.querySelector(".margin time")?.textContent).toBe("23:02 IST");   // begun, not saved (23:12)
    const hours = [...first.querySelectorAll(".hour-line .margin-hour")].map((t) => t.textContent);
    expect(hours).toEqual(["23:09"]);
    expect(first.querySelector(".hour-line:nth-child(2)")?.textContent).toBe("23:09Then the harbour.");
  });

  it("shows a block without them as it always has", () => {
    const host = mount(<Document doc={EntryDoc(fixtures.entryEvening)} />);
    const first = must(host.querySelector("article.block.his"));
    expect(first.querySelector(".margin time")?.textContent).toBe("23:12 IST");
    expect(first.querySelector(".hour-lines")).toBeNull();
    expect(first.querySelector("p.words")?.textContent).toBe("First words, about the lighthouse.");
  });
});

describe("The save sends them", () => {
  it("posts one ISO time per paragraph with his words", async () => {
    window.history.replaceState(null, "", "/entries/2026-10-14-evening");
    const { writes } = stubApi({
      "GET /api/v1/entries/2026-10-14-evening": fixtures.entryEvening,
      "GET /api/v1/today": fixtures.today,
      "GET /api/v1/library": fixtures.library,
      "GET /api/v1/reflections": fixtures.reflections,
      "POST /api/v1/entries/2026-10-14-evening/blocks": { key: "words:2" },
    });
    const host = mount(<App />);
    const box = await until(() => must(host.querySelector<HTMLTextAreaElement>(".composer textarea")));
    const before = Date.now();
    await type(box, "One more thing.");
    await type(box, "One more thing.\n\nAnd the sea.");
    await click(byText(host, "button", "Add to the entry"));
    await until(() => expect(writes()).toHaveLength(1));
    const body = writes()[0]?.body as { text: string; times: string[] };
    expect(body.text).toBe("One more thing.\n\nAnd the sea.");
    expect(body.times).toHaveLength(2);
    for (const t of body.times) {
      expect(t).toMatch(/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$/);
      expect(Date.parse(t)).toBeGreaterThanOrEqual(before - 1);
    }
    expect(Date.parse(body.times[0] as string)).toBeLessThanOrEqual(Date.parse(body.times[1] as string));
  });

  it("sends none for a draft whose paragraphs it did not see begun", async () => {
    window.history.replaceState(null, "", "/entries/2026-10-14-evening");
    saveDraft("entry:2026-10-14-evening", "Kept from before.\nAnother line.");   // (no stamps kept with it)
    const { writes } = stubApi({
      "GET /api/v1/entries/2026-10-14-evening": fixtures.entryEvening,
      "GET /api/v1/today": fixtures.today,
      "GET /api/v1/library": fixtures.library,
      "GET /api/v1/reflections": fixtures.reflections,
      "POST /api/v1/entries/2026-10-14-evening/blocks": { key: "words:2" },
    });
    const host = mount(<App />);
    const box = await until(() => must(host.querySelector<HTMLTextAreaElement>(".composer textarea")));
    expect(box.value).toBe("Kept from before.\nAnother line.");
    await type(box, "Kept from before.\nAnother line.\nA new one.");
    await click(byText(host, "button", "Add to the entry"));
    await until(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]?.body).toMatchObject({ text: "Kept from before.\nAnother line.\nA new one." });
    expect(writes()[0]?.body).not.toHaveProperty("times");
  });
});
