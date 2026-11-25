import { describe, expect, it } from "vitest";
import { App } from "../src/app";
import { loadDraft } from "../src/drafts";
import { click, fixtures, json, mount, must, stubApi, type, until } from "./helpers";

// Today's one-line box (issue #82): exactly what /j does, POST /entries with no practice (actions.note), Enter to put
// it down; the write room stays for longer pieces.

function visit(path: string) {
  window.history.replaceState(null, "", path);
}

const reads = {
  "GET /api/v1/today": fixtures.today,
  "GET /api/v1/library": fixtures.library,
  "GET /api/v1/runs": [],
};

async function today() {
  visit("/");
  const host = mount(<App />);
  const box = await until(() => must(host.querySelector<HTMLTextAreaElement>("#quick-line")));
  return { host, box, form: must(box.closest("form")) };
}

describe("a line put down on Today", () => {
  it("is one line of his, labelled, outside the write room", async () => {
    stubApi(reads);
    const { host, box } = await today();
    expect(box.tagName).toBe("TEXTAREA");   // (an <input> would flatten a pasted paragraph: his words changed silently)
    expect(box.rows).toBe(1);
    expect(box.classList.contains("his")).toBe(true);
    expect(host.querySelector('label[for="quick-line"]')?.textContent).toBe("A line for today, saved as a note");
    expect(host.querySelector("dialog")).toBeNull();
  });

  it("is posted as a note, with a client id and no practice, and the box empties", async () => {
    const { writes } = stubApi({ ...reads, "POST /api/v1/entries": { stem: "2026-10-15-j-2", rule: "/j" } });
    const { host, box, form } = await today();
    await type(box, "The kettle, the rain.");
    expect(loadDraft("today-line")).toBe("The kettle, the rain.");   // kept in the tab until it is sent
    await click(must(form.querySelector('button[type="submit"]')));
    await until(() => expect(writes()).toHaveLength(1));
    const [w] = writes();
    expect(w?.path).toBe("/api/v1/entries");
    const body = w?.body as Record<string, unknown>;
    expect(body.text).toBe("The kettle, the rain.");
    expect(typeof body.client_id).toBe("string");
    expect("practice" in body).toBe(false);
    await until(() => expect(box.value).toBe(""));
    expect(loadDraft("today-line")).toBe("");
    const said = must(host.querySelector(".quick-line-said"));
    expect(said.textContent).toContain("Saved as a note.");
    expect(said.querySelector("a")?.getAttribute("href")).toBe("/entries/2026-10-15-j-2");
  });

  it("keeps /x words in the box with the server's reason, and offers no Retry", async () => {
    const detail = "this starts with the off-the-record command, so it stays out of the journal: nothing was saved";
    stubApi({ ...reads, "POST /api/v1/entries": () => json({ error: "off-the-record", detail }, 422) });
    const { host, box, form } = await today();
    await type(box, "/x not for the journal");
    await click(must(form.querySelector('button[type="submit"]')));
    const line = await until(() => must(host.querySelector(".quick-line .error-line")));
    expect(line.textContent).toContain("nothing was saved");
    expect(line.querySelector("button")).toBeNull();
    expect(box.value).toBe("/x not for the journal");
    expect(host.querySelector(".quick-line-said")).toBeNull();
  });

  it("offers Retry when the server could not be reached, and the retry is the same write", async () => {
    let n = 0;
    const { writes } = stubApi({
      ...reads,
      "POST /api/v1/entries": () => (++n === 1 ? json({ error: "http", detail: "The server answered 503." }, 503) : json({ stem: "2026-10-15-j-2" }, 201)),
    });
    const { host, box, form } = await today();
    await type(box, "A line.");
    await click(must(form.querySelector('button[type="submit"]')));
    const retry = await until(() => must(host.querySelector<HTMLButtonElement>(".quick-line .error-line button")));
    await click(retry);
    await until(() => expect(writes()).toHaveLength(2));
    const [a, b] = writes();
    expect((a?.body as { client_id: string }).client_id).toBe((b?.body as { client_id: string }).client_id);
    await until(() => expect(box.value).toBe(""));
  });

  const enter = (box: HTMLTextAreaElement, shift = false) =>
    box.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", shiftKey: shift, bubbles: true, cancelable: true }));

  it("puts the line down on Enter; Shift+Enter is a new line, not a send", async () => {
    const { writes } = stubApi({ ...reads, "POST /api/v1/entries": { stem: "2026-10-15-j-2", rule: "/j" } });
    const { box } = await today();
    await type(box, "The kettle,");
    const shifted = enter(box, true);
    expect(shifted).toBe(true);   // (not prevented: the browser adds the line)
    await type(box, "The kettle,\nthe rain.");
    expect(writes()).toHaveLength(0);
    expect(enter(box)).toBe(false);   // Enter alone is taken: the line goes down
    await until(() => expect(writes()).toHaveLength(1));
    expect((writes()[0]?.body as { text: string }).text).toBe("The kettle,\nthe rain.");
  });

  it("keeps a pasted paragraph as it is, its lines shown and sent unchanged", async () => {
    const { writes } = stubApi({ ...reads, "POST /api/v1/entries": { stem: "2026-10-15-j-2", rule: "/j" } });
    const { box, form } = await today();
    const words = "First line.\n\nSecond, after a blank.\nThird.";
    await type(box, words);
    expect(box.value).toBe(words);
    expect(box.rows).toBe(4);   // the box grows with his lines (up to six), so what is sent is what he sees
    await click(must(form.querySelector('button[type="submit"]')));
    await until(() => expect(writes()).toHaveLength(1));
    expect((writes()[0]?.body as { text: string }).text).toBe(words);
  });

  it("an empty box does not send on Enter", async () => {
    const { writes } = stubApi({ ...reads, "POST /api/v1/entries": { stem: "2026-10-15-j-2", rule: "/j" } });
    const { box } = await today();
    await type(box, "   ");
    enter(box);
    await new Promise((r) => setTimeout(r, 20));
    expect(writes()).toHaveLength(0);
  });

  it("keeps words too long to save in the box with the plain reason, and offers no Retry", async () => {
    const detail = "text is at most 20,000 characters";
    stubApi({ ...reads, "POST /api/v1/entries": () => json({ error: "too-large", detail }, 413) });
    const { host, box, form } = await today();
    await type(box, "a very long line");
    await click(must(form.querySelector('button[type="submit"]')));
    const line = await until(() => must(host.querySelector(".quick-line .error-line")));
    expect(line.textContent).toContain("at most 20,000 characters");
    expect(line.querySelector("button")).toBeNull();
    expect(box.value).toBe("a very long line");
  });
});
