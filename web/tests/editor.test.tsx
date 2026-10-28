import { describe, expect, it } from "vitest";
import { App } from "../src/app";
import {
  addQuestion, addSection, blankForm, definitionFromForm, flatten, formFromDefinition, freeId, moveQuestion, moveSection,
  removeQuestion, removeSection, setSectionTitle, slugFor, sortProblems,
} from "../src/editorModel";
import { LibraryOne } from "../src/types";
import { choose, click, fixtures, json, mount, must, byText, stubApi, type, until } from "./helpers";

const one = LibraryOne(fixtures.libraryGratitude);

function visit(path: string) {
  window.history.replaceState(null, "", path);
}

describe("the editor's model", () => {
  it("makes the definition the server gave, from the form it made from it", () => {
    // What the server serves for a practice (`GET /library/gratitude`) is what the form sends back unchanged: the
    // Python suite (`ValidateFixtureTest`) pins that this very body renders to `library-validate.json`'s file.
    const back = definitionFromForm(formFromDefinition(one.definition), one.definition);
    const { source: _s, path: _p, ...served } = fixtures.libraryGratitude.definition;
    expect(back).toEqual(served);
  });

  it("keeps what the form does not edit (kind, group, the opener's shape and limit)", () => {
    const d = { ...one.definition, kind: "practice", group: "evenings", opener: { ...one.definition.opener, shape: "a haiku", limit: 90 } };
    const out = definitionFromForm(formFromDefinition(d), d);
    expect(out).toMatchObject({ kind: "practice", group: "evenings", opener: { shape: "a haiku", limit: 90 } });
  });

  it("sends a collapse that is not a number as typed, for the server to name", () => {
    const f = { ...formFromDefinition(one.definition), collapse: "soon" };
    expect(definitionFromForm(f, one.definition).collapse_hours).toBe("soon");
  });

  it("pins a question that moves, so its id travels with it", () => {
    const f = formFromDefinition(one.definition);
    const moved = moveQuestion(f, 0, 0, 1);
    expect(moved.sections[0]?.questions.map((q) => [q.id, q.pinned])).toEqual([["2", false], ["1", true]]);
    expect(moved.sections[0]?.questions[1]?.text).toBe("Three things today, and why each one mattered.");
    // at the ends of a flat list there is nowhere to go
    expect(moveQuestion(f, 0, 0, -1)).toBe(f);
  });

  it("moves a question across a section boundary, and pins a moved section's questions", () => {
    let f = addSection(formFromDefinition(one.definition));
    f = setSectionTitle(f, 1, "Evening");
    const across = moveQuestion(f, 0, 1, 1);   // last of section 1 goes to the top of section 2
    expect(across.sections.map((s) => s.questions.length)).toEqual([1, 2]);
    expect(across.sections[1]?.questions[0]).toMatchObject({ id: "2", pinned: true });
    const swapped = moveSection(f, 0, 1);
    expect(swapped.sections.map((s) => s.title)).toEqual(["Evening", "Section 1"]);
    expect(swapped.sections[1]?.questions.every((q) => q.pinned)).toBe(true);
  });

  it("pins every question that has an id when its place in the numbering changes", () => {
    // ids are numbered by place on the server: a section move changes both sections' numbers, and going between a flat
    // list and sections changes all of them (the Python suite parses these very shapes back: PinnedIdsTest)
    const pinned = (f: ReturnType<typeof formFromDefinition>) => f.sections.flatMap((s) => s.questions).map((x) => x.pinned);
    let f = setSectionTitle(addSection(formFromDefinition(one.definition)), 1, "Evening");
    expect(pinned(f).slice(0, 2)).toEqual([true, true]);   // the flat list's questions, now under "Section 1"
    const moved = moveSection(f, 1, -1);                   // the pressed section and the one it swapped with
    expect(moved.sections.flatMap((s) => s.questions).filter((x) => x.id).every((x) => x.pinned)).toBe(true);
    expect(pinned(flatten(removeSection(f, 1)))).toEqual([true, true]);
    f = removeSection(moveSection(f, 0, 1), 0);             // those after a removed section move up one place
    expect(f.sections[0]?.questions.filter((x) => x.id).every((x) => x.pinned)).toBe(true);
    const gone = removeQuestion(formFromDefinition(one.definition), 0, 0);   // the second question becomes the first
    expect(gone.sections[0]?.questions[0]).toMatchObject({ id: "2", pinned: true });
  });

  it("does not pin a new question: it has no id to keep", () => {
    const f = addQuestion(formFromDefinition(one.definition), 0);
    const moved = moveQuestion(f, 0, 2, -1);
    expect(moved.sections[0]?.questions[1]).toMatchObject({ id: "", pinned: false });
  });

  it("goes between a flat list and sections", () => {
    const f = addSection(formFromDefinition(one.definition));
    expect(f.sections.map((s) => s.title)).toEqual(["Section 1", "Section 2"]);
    expect(removeSection(f, 1).sections).toHaveLength(1);
    expect(flatten(removeSection(f, 1)).sections[0]?.title).toBeNull();
  });

  it("names a file from a title, and keeps it free", () => {
    expect(slugFor("Évening  gratitude!")).toBe("evening-gratitude");
    expect(slugFor("2 minutes of calm")).toBe("minutes-of-calm");
    expect(freeId("Daily gratitude", ["gratitude"])).toBe("daily-gratitude");
    expect(freeId("Gratitude", ["gratitude", "gratitude-2"])).toBe("gratitude-3");
    expect(freeId("???", [])).toBe("");
  });

  it("sorts the server's problems to the field they are about", () => {
    const p = sortProblems(["schedule 2: needs a time", "section 1, question 3: needs one line of text", "section 2: the title is one line of text", "no questions"]);
    expect(p.schedule[2]).toEqual(["needs a time"]);
    expect(p.question["1,3"]).toEqual(["needs one line of text"]);
    expect(p.section[2]).toEqual(["the title is one line of text"]);
    expect(p.general).toEqual(["no questions"]);
  });

  it("starts a new practice with a title to fill in, a time and a question", () => {
    const f = blankForm();
    expect(f.schedule).toHaveLength(1);
    expect(f.sections[0]?.questions).toHaveLength(1);
    expect(f.sections[0]?.questions[0]?.text).not.toBe("");
  });
});

const read = {
  "GET /api/v1/library/gratitude": fixtures.libraryGratitude,
  "GET /api/v1/library": fixtures.library,
};

describe("Edit a practice", () => {
  it("asks the server for the file the form makes, shows it under Raw Markdown, and saves with the mtime it read", async () => {
    visit("/library/gratitude/edit");
    const { writes } = stubApi({
      ...read,
      "PUT /api/v1/library/gratitude?validate=1": fixtures.libraryValidate,
      "PUT /api/v1/library/gratitude": fixtures.libraryGratitude,
    });
    const host = mount(<App />);
    const title = await until(() => must(host.querySelector<HTMLInputElement>("#f-title")));
    expect(title.value).toBe("Daily gratitude");
    await type(title, "Gratitude, nightly");
    await until(() => expect(writes().some((w) => w.path.endsWith("validate=1"))).toBe(true));
    const dry = writes().find((w) => w.path.endsWith("validate=1"));
    expect(dry?.body).toMatchObject({ definition: { id: "gratitude", title: "Gratitude, nightly" } });
    expect(dry?.body).not.toHaveProperty("mtime");   // nothing about a file is claimed by a dry run

    await click(byText(host, "button[role=tab]", "Raw Markdown"));
    const raw = await until(() => must(host.querySelector<HTMLTextAreaElement>("#f-raw")));
    await until(() => expect(raw.value).toBe(fixtures.libraryValidate.raw));

    await click(byText(host, "button", "Save"));
    await until(() => expect(writes().some((w) => w.method === "PUT" && !w.path.includes("validate"))).toBe(true));
    const put = writes().find((w) => w.method === "PUT" && !w.path.includes("validate"));
    expect(put?.body).toMatchObject({ mtime: 1760000000, definition: { title: "Gratitude, nightly" } });
    await until(() => expect(window.location.pathname).toBe("/library/gratitude"));
  });

  it("shows no file, and takes no typing, under Raw Markdown while the form does not parse", async () => {
    visit("/library/gratitude/edit");
    const { writes } = stubApi({
      ...read,
      "PUT /api/v1/library/gratitude?validate=1": (w: { body?: unknown }) => {
        const d = (w.body as { definition?: { title?: string; sections?: { questions: { text: string }[] }[] } } | undefined)?.definition;
        const empty = d?.sections?.some((s) => s.questions.some((x) => x.text === ""));
        return empty
          ? { valid: false, problems: ["section 1, question 3: needs one line of text"], raw: null }
          : { ...fixtures.libraryValidate, raw: `---\ntitle: ${d?.title}\n---\n` };
      },
    });
    const host = mount(<App />);
    const title = await until(() => must(host.querySelector<HTMLInputElement>("#f-title")));
    await type(title, "Renamed");
    await click(byText(host, "button", "+ Add a question"));
    await until(() => expect(writes().filter((w) => w.path.endsWith("validate=1")).length).toBeGreaterThan(0));
    await click(byText(host, "button[role=tab]", "Raw Markdown"));
    const raw = await until(() => must(host.querySelector<HTMLTextAreaElement>("#f-raw")));
    await until(() => expect(host.querySelector(".editor-raw .hint")?.textContent).toContain("Fix the form's problems first"));
    expect(raw.readOnly).toBe(true);
    expect(raw.value).toBe("");            // not the file as it was read: the title change is in the form, not in that file
    expect(raw.value).not.toContain("Daily gratitude");
  });

  it("sends the text itself when it was edited as text", async () => {
    visit("/library/gratitude/edit");
    const { writes } = stubApi({
      ...read,
      "PUT /api/v1/library/gratitude?validate=1": fixtures.libraryValidate,
      "PUT /api/v1/library/gratitude": fixtures.libraryGratitude,
    });
    const host = mount(<App />);
    await until(() => must(host.querySelector("#f-title")));
    await click(byText(host, "button[role=tab]", "Raw Markdown"));
    await type(await until(() => must(host.querySelector("#f-raw"))), "---\nkind: practice\nid: gratitude\ntitle: By hand\nschedule.time: 20:00\n---\n- One thing.\n");
    await click(byText(host, "button", "Save"));
    await until(() => expect(writes().some((w) => w.method === "PUT" && !w.path.includes("validate"))).toBe(true));
    const put = writes().find((w) => w.method === "PUT" && !w.path.includes("validate"));
    expect(put?.body).toMatchObject({ raw: expect.stringContaining("title: By hand"), mtime: 1760000000 });
    expect(put?.body).not.toHaveProperty("definition");
  });

  it("asks before dropping text edits when going back to the form", async () => {
    visit("/library/gratitude/edit");
    stubApi({ ...read, "PUT /api/v1/library/gratitude?validate=1": fixtures.libraryValidate });
    const host = mount(<App />);
    await until(() => must(host.querySelector("#f-title")));
    await click(byText(host, "button[role=tab]", "Raw Markdown"));
    await type(await until(() => must(host.querySelector("#f-raw"))), "---\nkind: practice\n---\n");
    await click(byText(host, "button[role=tab]", "Form"));
    expect(host.textContent).toContain("Going back to the form drops those edits");
    expect(host.querySelector("#f-raw")).not.toBeNull();   // still on the text
    await click(byText(host, "button", "Drop the text edits"));
    await until(() => must(host.querySelector("#f-title")));
  });

  it("says a file changed elsewhere (409) and offers to overwrite with the file's new mtime", async () => {
    visit("/library/gratitude/edit");
    let puts = 0;
    const { writes } = stubApi({
      "GET /api/v1/library": fixtures.library,
      "GET /api/v1/library/gratitude": () => json({ ...fixtures.libraryGratitude, mtime: puts > 0 ? 1760000999 : 1760000000 }),
      "PUT /api/v1/library/gratitude?validate=1": fixtures.libraryValidate,
      "PUT /api/v1/library/gratitude": () => {
        puts += 1;
        return puts === 1 ? json({ error: "mtime", detail: "gratitude.md changed since it was read; reload it, then save again" }, 409) : fixtures.libraryGratitude;
      },
    });
    const host = mount(<App />);
    await until(() => must(host.querySelector("#f-title")));
    await click(byText(host, "button", "Save"));
    const banner = await until(() => must(byText(host, ".error-line", /Changed elsewhere/)));
    expect(banner.textContent).toContain("Obsidian");
    await click(byText(banner, "button", "Overwrite"));
    await until(() => expect(window.location.pathname).toBe("/library/gratitude"));
    const puts2 = writes().filter((w) => w.method === "PUT" && !w.path.includes("validate"));
    expect(puts2.map((w) => (w.body as { mtime: number }).mtime)).toEqual([1760000000, 1760000999]);
  });

  it("shows a 422's problems beside the field they name and keeps the form as it was", async () => {
    visit("/library/gratitude/edit");
    stubApi({
      ...read,
      "PUT /api/v1/library/gratitude?validate=1": fixtures.libraryValidate,
      "PUT /api/v1/library/gratitude": () =>
        json({ error: "invalid", detail: "the definition is not a usable practice; nothing was saved", problems: ["schedule 1: needs a time like \"21:30\"", "collapse_hours must be a positive number of at most 8760 hours"] }, 422),
    });
    const host = mount(<App />);
    await until(() => must(host.querySelector("#f-title")));
    await click(byText(host, "button", "Save"));
    await until(() => must(host.querySelector(".slot .field-error")));
    expect(host.querySelector(".slot .field-error")?.textContent).toContain("needs a time");
    expect(host.querySelector(".problems")?.textContent).toContain("collapse_hours");
    expect(host.querySelector<HTMLInputElement>("#f-title")?.value).toBe("Daily gratitude");
    expect(window.location.pathname).toBe("/library/gratitude/edit");
  });

  it("edits a built-in in place: the form opens on it, and the first save claims no file of his", async () => {
    visit("/library/evening/edit");
    const builtin = { ...fixtures.libraryGratitude, definition: { ...fixtures.libraryGratitude.definition, id: "evening", source: "builtin" }, mtime: 1700000000 };
    const edited = { ...builtin, definition: { ...builtin.definition, source: "fork" }, mtime: 1760000009 };
    const { writes } = stubApi({
      "GET /api/v1/library/evening": builtin,
      "PUT /api/v1/library/evening?validate=1": fixtures.libraryValidate,
      "PUT /api/v1/library/evening": edited,
    });
    const host = mount(<App />);
    const title = await until(() => must(host.querySelector<HTMLInputElement>("#f-title")));
    expect(host.querySelector(".eyebrow")?.textContent).toBe("Built-in");
    expect(host.querySelector(".page-head .sub")?.textContent).toContain("Saving edits it in place");
    await type(title, "Evening, in my words");
    await click(byText(host, "button", "Save"));
    await until(() => expect(writes().some((w) => w.method === "PUT" && !w.path.includes("validate"))).toBe(true));
    const put = writes().find((w) => w.method === "PUT" && !w.path.includes("validate"));
    expect(put?.body).not.toHaveProperty("mtime");   // the package's mtime is not a file of his
    expect(put?.body).toMatchObject({ definition: { id: "evening", title: "Evening, in my words" } });
    expect(writes().map((w) => w.path)).not.toContain("/api/v1/library/evening/fork");
    await until(() => expect(window.location.pathname).toBe("/library/evening"));
  });

  it("says an edited file that does not load cannot be edited here until it is mended or reset", async () => {
    visit("/library/evening/edit");
    const broken = {
      ...fixtures.libraryGratitude,
      definition: { ...fixtures.libraryGratitude.definition, id: "evening", source: "builtin" },
      override: { default_changed: null, edited_at: null, in_force: false },
    };
    stubApi({ "GET /api/v1/library/evening": broken });
    const host = mount(<App />);
    await until(() => expect(host.textContent).toContain("Your edited file does not load"));
    expect(host.querySelector("#f-title")).toBeNull();
  });
});

describe("Edit a program", () => {
  const FILE = "sessions/01-anti-vision.md";

  it("edits one file at a time, checks it with the server, and saves it in place under its name", async () => {
    visit("/library/fix-life/edit");
    const one = fixtures.libraryFixLife;
    const s1 = must(one.sessions.find((s) => s.file === FILE));
    const { writes } = stubApi({
      "GET /api/v1/library/fix-life": one,
      "PUT /api/v1/library/fix-life?validate=1": (sent: { body: unknown }) => ({ valid: true, problems: [], raw: (sent.body as { raw: string }).raw }),
      "PUT /api/v1/library/fix-life": { ...one, definition: { ...one.definition, source: "fork" } },
    });
    const host = mount(<App />);
    const picker = await until(() => must(host.querySelector<HTMLSelectElement>("#f-file")));
    expect([...picker.options].map((o) => o.value)).toEqual(["program.md", ...one.sessions.map((s) => s.file)]);
    expect(must(host.querySelector<HTMLTextAreaElement>("#f-raw")).value).toBe(one.raw);
    await choose(picker, FILE);
    const raw = must(host.querySelector<HTMLTextAreaElement>("#f-raw"));
    expect(raw.value).toBe(s1.raw);
    await type(raw, s1.raw.replace("Current Reality Excavation", "Where You Are"));
    expect(picker.disabled).toBe(true);   // one file's changes at a time
    await until(() => expect(writes().some((w) => w.path.endsWith("validate=1"))).toBe(true));
    expect(writes().find((w) => w.path.endsWith("validate=1"))?.body).toMatchObject({ file: FILE });
    await until(() => expect(host.textContent).toContain("Looks good."));
    await click(byText(host, "button", "Save"));
    await until(() => expect(writes().some((w) => w.method === "PUT" && !w.path.includes("validate"))).toBe(true));
    const put = writes().find((w) => w.method === "PUT" && !w.path.includes("validate"));
    expect(put?.body).toMatchObject({ file: FILE });
    expect(put?.body).not.toHaveProperty("mtime");   // a built-in: the first save makes his copy
    expect((put?.body as { raw: string }).raw).toContain("## Phase 1: Where You Are");
    await until(() => expect(window.location.pathname).toBe("/library/fix-life"));
  });

  it("says why a change that moves the structure waits for the run in progress", async () => {
    visit("/library/fix-life/edit");
    const one = { ...fixtures.libraryFixLife, definition: { ...fixtures.libraryFixLife.definition, source: "fork" } };
    const why = "Fix your entire life in 1 day has a run in progress (session 1, phase 1): its sessions, phases and question numbers stay as they are until it ends.";
    const { writes } = stubApi({
      "GET /api/v1/library/fix-life": one,
      "PUT /api/v1/library/fix-life?validate=1": { valid: false, problems: [why], raw: null },
      "PUT /api/v1/library/fix-life": json({ error: "active-run", detail: why, runs: ["r1"] }, 409),
    });
    const host = mount(<App />);
    const raw = await until(() => must(host.querySelector<HTMLTextAreaElement>("#f-raw")));
    await type(raw, one.raw.replace(", 10-one-day-reset", ""));
    await until(() => expect(host.querySelector(".problems")?.textContent).toContain("run in progress"));
    await click(byText(host, "button", "Save"));
    await until(() => expect(host.querySelector(".error-line")?.textContent).toContain("run in progress"));
    const put = writes().find((w) => w.method === "PUT" && !w.path.includes("validate"));
    expect(put?.body).toMatchObject({ file: "program.md", mtime: 1760000000 });   // his file: saved against its mtime
    expect(window.location.pathname).toBe("/library/fix-life/edit");
  });
});

describe("New practice", () => {
  it("makes a file from a title, a time and a question, due from the next tick", async () => {
    visit("/new-practice");
    const { writes } = stubApi({
      "GET /api/v1/library": fixtures.library,
      "PUT /api/v1/library/morning-pages?validate=1": { valid: true, problems: [], raw: "---\nkind: practice\n---\n- x\n" },
      "PUT /api/v1/library/morning-pages": { ...fixtures.libraryGratitude, definition: { ...fixtures.libraryGratitude.definition, id: "morning-pages" } },
    });
    const host = mount(<App />);
    const title = await until(() => must(host.querySelector<HTMLInputElement>("#f-title")));
    expect(byText(host, "button", "Create")?.hasAttribute("disabled")).toBe(true);   // no title, no file name
    await type(title, "Morning pages");
    await type(host.querySelector(".slot input[type=time]"), "07:30");
    await type(host.querySelector(".q-row textarea"), "What is on the page today?");
    await until(() => expect(byText(host, "button", "Create")?.hasAttribute("disabled")).toBe(false));
    await click(byText(host, "button", "Create"));
    await until(() => expect(writes().some((w) => w.method === "PUT" && !w.path.includes("validate"))).toBe(true));
    const put = writes().find((w) => w.method === "PUT" && !w.path.includes("validate"));
    expect(put?.path).toBe("/api/v1/library/morning-pages");
    expect(put?.body).not.toHaveProperty("mtime");   // a new file claims no mtime
    expect(put?.body).toMatchObject({
      definition: {
        id: "morning-pages", title: "Morning pages", enabled: true,
        schedule: [{ time: "07:30", weekday: null }],
        sections: [{ title: null, questions: [{ text: "What is on the page today?" }] }],
      },
    });
    await until(() => expect(window.location.pathname).toBe("/library/morning-pages"));
  });

  it("takes the next free file name when the title is taken", async () => {
    visit("/new-practice");
    stubApi({ "GET /api/v1/library": fixtures.library, "PUT /api/v1/library/gratitude-2?validate=1": fixtures.libraryValidate });
    const host = mount(<App />);
    await type(await until(() => must(host.querySelector("#f-title"))), "Gratitude");
    host.querySelector("details.more")?.setAttribute("open", "");
    await until(() => expect(host.querySelector<HTMLInputElement>("#f-id")?.value).toBe("gratitude-2"));
  });

  it("can pick a weekday and add a second time", async () => {
    visit("/new-practice");
    stubApi({ "GET /api/v1/library": fixtures.library });
    const host = mount(<App />);
    await until(() => must(host.querySelector(".slot")));
    await choose(host.querySelector(".slot select"), "2");
    await click(byText(host, "button", "+ Add a time"));
    expect(host.querySelectorAll(".slot")).toHaveLength(2);
    expect(host.querySelector<HTMLSelectElement>(".slot select")?.value).toBe("2");
  });
});
