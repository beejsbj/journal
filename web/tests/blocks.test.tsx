import { describe, expect, it, vi } from "vitest";
import { App } from "../src/app";
import { loadDraft } from "../src/drafts";
import { forgetReads } from "../src/offline";
import { CACHED_AT, FROM_CACHE } from "../src/swCore";
import { byText, click, fixtures, json, mount, must, stubApi, type, until } from "./helpers";

// Editing and deleting his own blocks (Decision 4, amended 2026-10-20): Edit and Delete under each block of his, an
// inline editor, a save bound to the block as it was read, and a conflict that keeps his words.

function visit(path: string) {
  window.history.replaceState(null, "", path);
}

const STEM = "2026-10-14-evening";
const PATCH_FIRST = `PATCH /api/v1/entries/${STEM}/blocks/words%3A0`;
const DELETE_FIRST = `DELETE /api/v1/entries/${STEM}/blocks/words%3A0`;

const reads = {
  "GET /api/v1/today": fixtures.today,
  "GET /api/v1/library": fixtures.library,
  "GET /api/v1/runs": [],
  "GET /api/v1/reflections": fixtures.reflections,
};

/** The words of an unsaved edit as kept: with the block's words and time it was an edit of. */
function draftOf(key: string): { was: string; at: string; text: string } | null {
  const raw = loadDraft(key);
  return raw ? JSON.parse(raw) : null;
}

function firstBlock(host: HTMLElement): HTMLElement {
  return must(host.querySelector<HTMLElement>("article.block.his"));
}

describe("Edit and Delete on his blocks", () => {
  it("are under each block of his and not under hers", async () => {
    visit(`/entries/${STEM}`);
    stubApi({ ...reads, [`GET /api/v1/entries/${STEM}`]: fixtures.entryEvening });
    const host = mount(<App />);
    await until(() => must(host.querySelector(".document article.block.his")));
    const his = [...host.querySelectorAll("article.block.his")];
    expect(his).toHaveLength(4);
    for (const article of his) {
      expect(byText(article, ".block-actions button", "Edit")).toBeDefined();
      expect(byText(article, ".block-actions button", "Delete")).toBeDefined();
    }
    expect(host.querySelectorAll("details.block.hers .block-actions")).toHaveLength(0);
    expect(host.querySelectorAll("details.block.hers button")).toHaveLength(0);
  });

  it("never appear on her callouts (README Guarantees: the companion's words are never editable)", async () => {
    visit(`/entries/${STEM}`);
    stubApi({ ...reads, [`GET /api/v1/entries/${STEM}`]: fixtures.entryEvening });
    const host = mount(<App />);
    await until(() => must(host.querySelector(".document article.block.his")));
    const hers = [...host.querySelectorAll<HTMLElement>("details.block.hers")];
    // the fixture carries her note: a page with none would pass this test for nothing
    expect(hers.length).toBeGreaterThan(0);
    expect(hers.length).toBe(fixtures.entryEvening.blocks.filter((b) => b.kind === "hers").length);
    for (const details of hers) {
      // (a closed <details> still has its content in the DOM, so this covers the callout open and shut)
      expect(details.querySelectorAll("button, input, textarea, [contenteditable], [role=button]")).toHaveLength(0);
      expect(details.querySelector(".block-actions")).toBeNull();
    }
  });

  it("are not offered on a page the web app may not write to", async () => {
    visit(`/entries/${STEM}`);
    stubApi({ ...reads, [`GET /api/v1/entries/${STEM}`]: { ...fixtures.entryEvening, writable: false } });
    const host = mount(<App />);
    await until(() => must(host.querySelector(".document article.block.his")));
    expect(host.querySelectorAll(".block-actions")).toHaveLength(0);
  });

  it("leave a recording that is still being transcribed alone", async () => {
    visit(`/entries/${STEM}`);
    const blocks = fixtures.entryEvening.blocks.map((b) =>
      b.key === "words:1" ? { ...b, text: "_(audio file — transcript pending)_" } : b,
    );
    stubApi({ ...reads, [`GET /api/v1/entries/${STEM}`]: { ...fixtures.entryEvening, blocks } });
    const host = mount(<App />);
    await until(() => must(host.querySelector(".document article.block.his")));
    const actions = [...host.querySelectorAll("article.block.his")].map((a) => a.querySelector(".block-actions") !== null);
    expect(actions).toEqual([true, false, true, true]);
  });

  it("give a program answer an Edit but no Delete", async () => {
    visit("/entries/2026-10-15-fix-life-s01");
    stubApi({ ...reads, "GET /api/v1/entries/2026-10-15-fix-life-s01": fixtures.entrySession });
    const host = mount(<App />);
    await until(() => must(host.querySelector(".document article.block.his")));
    for (const article of host.querySelectorAll<HTMLElement>("article.block.his")) {
      expect(byText(article, ".block-actions button", "Edit")).toBeDefined();
      expect(byText(article, ".block-actions button", "Delete")).toBeUndefined();
    }
  });
});

describe("The inline editor", () => {
  it("opens in the place of the words, saves what the block was and what it is now, and reads the entry again", async () => {
    visit(`/entries/${STEM}`);
    let saved = false;
    const edited = {
      ...fixtures.entryEvening,
      blocks: fixtures.entryEvening.blocks.map((b) => (b.key === "words:0" ? { ...b, text: "First words, corrected." } : b)),
    };
    const { writes, sent } = stubApi({
      ...reads,
      [`GET /api/v1/entries/${STEM}`]: () => (saved ? edited : fixtures.entryEvening),
      [PATCH_FIRST]: () => {
        saved = true;
        return { block: edited.blocks[0], mtime: 1760000001 };
      },
    });
    const host = mount(<App />);
    await until(() => must(host.querySelector(".document article.block.his")));
    await click(byText(firstBlock(host), "button", "Edit"));
    const box = must(firstBlock(host).querySelector<HTMLTextAreaElement>(".block-editor textarea"));
    expect(box.value).toBe("First words, about the lighthouse.");
    expect(firstBlock(host).querySelector(".words")).toBeNull();   // the editor stands in the place of the words
    expect(firstBlock(host).querySelector(".block-actions")).toBeNull();
    await type(box, "First words, corrected.");
    expect(draftOf(`edit:${STEM}:words:0`)).toEqual({
      was: "First words, about the lighthouse.", at: "23:12 IST", text: "First words, corrected.",
    });
    await click(byText(host, "button", "Save"));
    await until(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]).toMatchObject({
      method: "PATCH",
      body: { text: "First words, corrected.", was: "First words, about the lighthouse.", at: "23:12 IST" },
    });
    await until(() => expect(firstBlock(host).querySelector(".words")?.textContent).toBe("First words, corrected."));
    expect(host.querySelector(".block-editor")).toBeNull();
    expect(loadDraft(`edit:${STEM}:words:0`)).toBe("");
    expect(sent.filter((s) => s.method === "GET" && s.path.endsWith(STEM))).toHaveLength(2);
  });

  it("does not save words that were not changed, or left empty", async () => {
    visit(`/entries/${STEM}`);
    const { writes } = stubApi({ ...reads, [`GET /api/v1/entries/${STEM}`]: fixtures.entryEvening });
    const host = mount(<App />);
    await until(() => must(host.querySelector(".document article.block.his")));
    await click(byText(firstBlock(host), "button", "Edit"));
    const save = () => byText(host, "button", "Save") as HTMLButtonElement;
    expect(save().disabled).toBe(true);
    await type(host.querySelector(".block-editor textarea"), "   \n");
    expect(save().disabled).toBe(true);
    expect(writes()).toHaveLength(0);
  });

  it("Cancel sends nothing and forgets the edit", async () => {
    visit(`/entries/${STEM}`);
    const { writes } = stubApi({ ...reads, [`GET /api/v1/entries/${STEM}`]: fixtures.entryEvening });
    const host = mount(<App />);
    await until(() => must(host.querySelector(".document article.block.his")));
    await click(byText(firstBlock(host), "button", "Edit"));
    await type(host.querySelector(".block-editor textarea"), "never mind");
    await click(byText(host, "button", "Cancel"));
    expect(host.querySelector(".block-editor")).toBeNull();
    expect(firstBlock(host).querySelector(".words")?.textContent).toBe("First words, about the lighthouse.");
    expect(loadDraft(`edit:${STEM}:words:0`)).toBe("");
    expect(writes()).toHaveLength(0);
  });

  it("on a conflict says so, keeps his words, and offers them back after a reload when the block reads as it did", async () => {
    visit(`/entries/${STEM}`);
    const { writes, sent } = stubApi({
      ...reads,
      [`GET /api/v1/entries/${STEM}`]: fixtures.entryEvening,
      [PATCH_FIRST]: json({ error: "changed", detail: "That block changed since you read it; reload the entry and try again." }, 409),
    });
    const host = mount(<App />);
    await until(() => must(host.querySelector(".document article.block.his")));
    await click(byText(firstBlock(host), "button", "Edit"));
    const box = must(host.querySelector<HTMLTextAreaElement>(".block-editor textarea"));
    await type(box, "My careful rewrite.");
    await click(byText(host, "button", "Save"));
    const err = await until(() => must(host.querySelector(".block-editor .error-line")));
    expect(err.textContent).toContain("changed since you read it");
    expect(byText(err, "button", "Retry")).toBeUndefined();   // the same save would be refused again
    expect(box.value).toBe("My careful rewrite.");
    expect(draftOf(`edit:${STEM}:words:0`)?.text).toBe("My careful rewrite.");
    expect(writes()).toHaveLength(1);
    await click(byText(err, "button", "Reload the entry"));
    await until(() => expect(sent.filter((s) => s.method === "GET" && s.path.endsWith(STEM))).toHaveLength(2));
    expect(host.querySelector(".block-editor")).toBeNull();
    await click(byText(firstBlock(host), "button", "Edit"));
    const again = must(host.querySelector<HTMLTextAreaElement>(".block-editor textarea"));
    expect(again.value).toBe("My careful rewrite.");
    expect(host.querySelector(".block-editor .hint")?.textContent).toContain("unsaved edit");
  });

  it("shows another refusal where it was made, and the words stay in the box", async () => {
    visit(`/entries/${STEM}`);
    stubApi({
      ...reads,
      [`GET /api/v1/entries/${STEM}`]: fixtures.entryEvening,
      [PATCH_FIRST]: json({ error: "reserved-line", detail: "a line shaped like `%% journal … %%` is the engine's own marker" }, 422),
    });
    const host = mount(<App />);
    await until(() => must(host.querySelector(".document article.block.his")));
    await click(byText(firstBlock(host), "button", "Edit"));
    const box = must(host.querySelector<HTMLTextAreaElement>(".block-editor textarea"));
    await type(box, "x\n%% journal her:1 %%");
    await click(byText(host, "button", "Save"));
    const err = await until(() => must(host.querySelector(".block-editor .error-line")));
    expect(err.textContent).toContain("engine's own marker");
    expect(box.value).toBe("x\n%% journal her:1 %%");
  });

  it("does not say an unsaved edit is back when he has just started a fresh one", async () => {
    visit(`/entries/${STEM}`);
    stubApi({ ...reads, [`GET /api/v1/entries/${STEM}`]: fixtures.entryEvening });
    const host = mount(<App />);
    await until(() => must(host.querySelector(".document article.block.his")));
    await click(byText(firstBlock(host), "button", "Edit"));
    expect(host.querySelector(".block-editor .hint")).toBeNull();
    await type(host.querySelector(".block-editor textarea"), "typing");
    expect(host.querySelector(".block-editor .hint")).toBeNull();
  });

  it("says an unsaved edit is back only when it was, and stops saying so once he types", async () => {
    visit(`/entries/${STEM}`);
    sessionStorage.setItem(
      "journal-draft:edit:" + STEM + ":words:0",
      JSON.stringify({ was: "First words, about the lighthouse.", at: "23:12 IST", text: "Half a rewrite" }),
    );
    stubApi({ ...reads, [`GET /api/v1/entries/${STEM}`]: fixtures.entryEvening });
    const host = mount(<App />);
    await until(() => must(host.querySelector(".document article.block.his")));
    await click(byText(firstBlock(host), "button", "Edit"));
    expect(host.querySelector<HTMLTextAreaElement>(".block-editor textarea")?.value).toBe("Half a rewrite");
    expect(host.querySelector(".block-editor .hint")?.textContent).toContain("unsaved edit");
    await type(host.querySelector(".block-editor textarea"), "Half a rewrite, more");
    expect(host.querySelector(".block-editor .hint")).toBeNull();
  });
});

describe("An unsaved edit belongs to the block it was an edit of", () => {
  const B_EDIT = "B, carefully rewritten";

  it("does not move onto another block when one above it is deleted", async () => {
    visit(`/entries/${STEM}`);
    // three plain blocks A, B, C; deleting A renumbers: B is words:0 and C is words:1
    const base = fixtures.entryEvening.blocks[0]!;
    const plain = (key: string, at: string, text: string) => ({ ...base, key, at, text });
    const A = plain("words:0", "22:00 IST", "A words");
    const B = plain("words:1", "22:30 IST", "B words");
    const C = plain("words:2", "23:00 IST", "C words");
    const before = { ...fixtures.entryEvening, blocks: [A, B, C] };
    const after = { ...fixtures.entryEvening, blocks: [{ ...B, key: "words:0" }, { ...C, key: "words:1" }] };
    let gone = false;
    const { writes } = stubApi({
      ...reads,
      [`GET /api/v1/entries/${STEM}`]: () => (gone ? after : before),
      [DELETE_FIRST]: () => {
        gone = true;
        return { mtime: 1760000002 };
      },
    });
    const host = mount(<App />);
    await until(() => must(host.querySelector(".document article.block.his")));
    const nth = (i: number) => host.querySelectorAll<HTMLElement>("article.block.his")[i]!;
    await click(byText(nth(1), "button", "Edit"));
    await type(nth(1).querySelector(".block-editor textarea"), B_EDIT);
    expect(draftOf(`edit:${STEM}:words:1`)).toEqual({ was: "B words", at: "22:30 IST", text: B_EDIT });
    // delete A while B's editor is open
    await click(byText(nth(0), ".block-actions button", "Delete"));
    await click(byText(must(nth(0).querySelector(".confirm")), "button", "Delete"));
    await until(() => expect(host.querySelectorAll("article.block.his")).toHaveLength(2));
    // the editor that held B's words is gone, and it did not move under C's heading
    expect(host.querySelector(".block-editor")).toBeNull();
    expect(nth(1).querySelector(".words")?.textContent).toBe("C words");
    // Edit on C (now at B's old key): B's words are not put into C's box
    await click(byText(nth(1), "button", "Edit"));
    const box = must(nth(1).querySelector<HTMLTextAreaElement>(".block-editor textarea"));
    expect(box.value).toBe("C words");
    expect(nth(1).querySelector(".block-editor .hint:not(.lost-draft)")).toBeNull();
    // his edit of B is not lost: it is offered as text to copy or let go, and Save stays off until he changes C
    expect(must(nth(1).querySelector<HTMLTextAreaElement>(".lost-draft textarea")).value).toBe(B_EDIT);
    expect((byText(nth(1), "button", "Save") as HTMLButtonElement).disabled).toBe(true);
    expect(writes()).toHaveLength(1);   // only the delete: B's words were not sent as an edit of C
    await click(byText(nth(1), ".lost-draft button", "Let it go"));
    expect(nth(1).querySelector(".lost-draft")).toBeNull();
    expect(loadDraft(`lost:${STEM}:words:1`)).toBe("");
    // and if he does save C, it is checked against C
    await type(box, "C words, kept");
    expect(draftOf(`edit:${STEM}:words:1`)).toEqual({ was: "C words", at: "23:00 IST", text: "C words, kept" });
  });

  it("is set aside, not restored, when the block was changed elsewhere; Save then checks against what is there now", async () => {
    visit(`/entries/${STEM}`);
    sessionStorage.setItem(
      "journal-draft:edit:" + STEM + ":words:0",
      JSON.stringify({ was: "First words, about the lighthouse.", at: "23:12 IST", text: "My careful rewrite." }),
    );
    const changed = {
      ...fixtures.entryEvening,
      blocks: fixtures.entryEvening.blocks.map((b) => (b.key === "words:0" ? { ...b, text: "Changed in Obsidian." } : b)),
    };
    const { writes } = stubApi({
      ...reads,
      [`GET /api/v1/entries/${STEM}`]: changed,
      [PATCH_FIRST]: { block: changed.blocks[0], mtime: 1760000003 },
    });
    const host = mount(<App />);
    await until(() => must(host.querySelector(".document article.block.his")));
    await click(byText(firstBlock(host), "button", "Edit"));
    const box = must(host.querySelector<HTMLTextAreaElement>(".block-editor textarea"));
    expect(box.value).toBe("Changed in Obsidian.");
    expect(host.querySelector<HTMLTextAreaElement>(".lost-draft textarea")?.value).toBe("My careful rewrite.");
    expect(draftOf(`edit:${STEM}:words:0`)).toBeNull();
    await type(box, "Changed in Obsidian, and by me.");
    await click(byText(host, "button", "Save"));
    await until(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]).toMatchObject({
      method: "PATCH",
      body: { text: "Changed in Obsidian, and by me.", was: "Changed in Obsidian.", at: "23:12 IST" },
    });
  });

  it("closes an open editor when the block under it changes, rather than carrying the editor over", async () => {
    visit(`/entries/${STEM}`);
    let changedElsewhere = false;
    const changed = {
      ...fixtures.entryEvening,
      blocks: fixtures.entryEvening.blocks.map((b) => (b.key === "words:0" ? { ...b, text: "Changed in Obsidian." } : b)),
    };
    const { sent } = stubApi({
      ...reads,
      [`GET /api/v1/entries/${STEM}`]: () => (changedElsewhere ? changed : fixtures.entryEvening),
      [PATCH_FIRST]: json({ error: "changed", detail: "That block changed since you read it." }, 409),
    });
    const host = mount(<App />);
    await until(() => must(host.querySelector(".document article.block.his")));
    await click(byText(firstBlock(host), "button", "Edit"));
    await type(host.querySelector(".block-editor textarea"), "mine");
    changedElsewhere = true;
    await click(byText(host, "button", "Save"));
    await click(byText(must(host.querySelector(".block-editor .error-line")), "button", "Reload the entry"));
    await until(() => expect(sent.filter((s) => s.method === "GET" && s.path.endsWith(STEM))).toHaveLength(2));
    await until(() => expect(firstBlock(host).querySelector(".words")?.textContent).toBe("Changed in Obsidian."));
    expect(host.querySelector(".block-editor")).toBeNull();
  });
});

describe("Editing while the page is not live", () => {
  const stale = (doc: unknown) => () =>
    new Response(JSON.stringify(doc), {
      status: 200,
      headers: { "Content-Type": "application/json", [FROM_CACHE]: "stale", [CACHED_AT]: String(Date.now() - 3600_000) },
    });

  it("offers neither Edit nor Delete on a document read from the worker's kept copy", async () => {
    visit(`/entries/${STEM}`);
    stubApi({ ...reads, [`GET /api/v1/entries/${STEM}`]: stale(fixtures.entryEvening) });
    const host = mount(<App />);
    await until(() => must(host.querySelector(".document article.block.his")));
    await until(() => expect(host.querySelector(".ribbon")).not.toBeNull());
    expect(host.querySelectorAll(".block-actions")).toHaveLength(0);
    forgetReads();
  });

  it("says the edit will not be saved while offline, keeps the box, and does not offer Delete or send anything", async () => {
    visit(`/entries/${STEM}`);
    const { writes } = stubApi({ ...reads, [`GET /api/v1/entries/${STEM}`]: fixtures.entryEvening });
    const host = mount(<App />);
    await until(() => must(host.querySelector(".document article.block.his")));
    const online = vi.spyOn(navigator, "onLine", "get").mockReturnValue(false);
    try {
      window.dispatchEvent(new Event("offline"));
      await until(() => expect(byText(firstBlock(host), ".block-actions button", "Delete")).toBeUndefined());
      expect(firstBlock(host).querySelector(".block-actions .offline-note")?.textContent).toContain("offline");
      await click(byText(firstBlock(host), "button", "Edit"));
      await type(host.querySelector(".block-editor textarea"), "typed offline");
      expect(host.querySelector(".block-editor .offline-note")?.textContent).toContain("You're offline");
      expect((byText(host, "button", "Save") as HTMLButtonElement).disabled).toBe(true);
      expect(host.querySelector<HTMLTextAreaElement>(".block-editor textarea")?.value).toBe("typed offline");
      expect(draftOf(`edit:${STEM}:words:0`)?.text).toBe("typed offline");
      expect(writes()).toHaveLength(0);
    } finally {
      online.mockRestore();
      window.dispatchEvent(new Event("online"));
    }
  });
});

describe("Delete", () => {
  it("asks first, and keeps the block when he keeps it", async () => {
    visit(`/entries/${STEM}`);
    const { writes } = stubApi({ ...reads, [`GET /api/v1/entries/${STEM}`]: fixtures.entryEvening });
    const host = mount(<App />);
    await until(() => must(host.querySelector(".document article.block.his")));
    await click(byText(firstBlock(host), ".block-actions button", "Delete"));
    const ask = must(firstBlock(host).querySelector(".confirm"));
    expect(ask.textContent).toContain("Delete this block");
    await click(byText(ask, "button", "Keep"));
    expect(firstBlock(host).querySelector(".confirm")).toBeNull();
    expect(writes()).toHaveLength(0);
  });

  it("sends the block as it was read and reads the entry again", async () => {
    visit(`/entries/${STEM}`);
    let gone = false;
    const without = { ...fixtures.entryEvening, blocks: fixtures.entryEvening.blocks.filter((b) => b.key !== "words:0") };
    const { writes } = stubApi({
      ...reads,
      [`GET /api/v1/entries/${STEM}`]: () => (gone ? without : fixtures.entryEvening),
      [DELETE_FIRST]: () => {
        gone = true;
        return { mtime: 1760000002 };
      },
    });
    const host = mount(<App />);
    await until(() => must(host.querySelector(".document article.block.his")));
    await click(byText(firstBlock(host), ".block-actions button", "Delete"));
    await click(byText(must(firstBlock(host).querySelector(".confirm")), "button", "Delete"));
    await until(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]).toMatchObject({
      method: "DELETE",
      body: { was: "First words, about the lighthouse.", at: "23:12 IST" },
    });
    await until(() => expect(host.querySelectorAll("article.block.his")).toHaveLength(3));
  });

  it("says so where it was asked when the block changed meanwhile, and offers the reload", async () => {
    visit(`/entries/${STEM}`);
    stubApi({
      ...reads,
      [`GET /api/v1/entries/${STEM}`]: fixtures.entryEvening,
      [DELETE_FIRST]: json({ error: "changed", detail: "That block changed since you read it; reload the entry and try again." }, 409),
    });
    const host = mount(<App />);
    await until(() => must(host.querySelector(".document article.block.his")));
    await click(byText(firstBlock(host), ".block-actions button", "Delete"));
    await click(byText(must(firstBlock(host).querySelector(".confirm")), "button", "Delete"));
    const err = await until(() => must(firstBlock(host).querySelector(".block-actions .error-line")));
    expect(err.textContent).toContain("changed since you read it");
    expect(byText(err, "button", "Reload the entry")).toBeDefined();
    expect(host.querySelectorAll("article.block.his")).toHaveLength(4);   // nothing was taken out of the page
  });
});
