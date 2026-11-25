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
  const box = await until(() => must(host.querySelector<HTMLInputElement>("#quick-line")));
  return { host, box, form: must(box.closest("form")) };
}

describe("a line put down on Today", () => {
  it("is one line of his, labelled, outside the write room", async () => {
    stubApi(reads);
    const { host, box } = await today();
    expect(box.tagName).toBe("INPUT");
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
});
