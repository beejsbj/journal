import { describe, expect, it } from "vitest";
import { Document } from "../src/components/Document";
import { EntryDoc } from "../src/types";
import { fixtures, mount, must } from "./helpers";

const evening = EntryDoc(fixtures.entryEvening);
const session = EntryDoc(fixtures.entrySession);

describe("Document: two voices, one page", () => {
  it("renders his blocks as plain full-width text with time and source in the margin", () => {
    const host = mount(<Document doc={evening} />);
    const his = [...host.querySelectorAll("article.block.his")];
    expect(his).toHaveLength(4);
    const first = must(his[0]);
    expect(first.querySelector(".words")?.textContent).toBe("First words, about the lighthouse.");
    expect(first.querySelector(".margin time")?.textContent).toBe("23:12 IST");
    expect(first.closest("details")).toBeNull();
    const voice = must(his[1]);
    expect(voice.querySelector(".source")?.textContent).toContain("voice");
    expect(voice.querySelector(".source")?.textContent).toContain("2:31");
    expect(voice.querySelector("audio")?.getAttribute("src")).toBe("/api/v1/media/2026-10-14-evening.ogg");
    expect(voice.querySelector(".words")?.textContent).toBe("A spoken thought.");
  });

  it("folds hers: labelled with her name and time, closed by default, never inside his text", () => {
    const host = mount(<Document doc={evening} />);
    const hers = [...host.querySelectorAll("details.block.hers")];
    expect(hers).toHaveLength(1);
    const callout = must(hers[0]);
    expect(callout.hasAttribute("open")).toBe(false);
    expect(callout.querySelector("summary .who")?.textContent).toBe("Ada");
    expect(callout.querySelector("summary time")?.textContent).toBe("23:45");
    expect(callout.querySelector(".callout-body")?.textContent).toContain("Second line.");
    expect(host.querySelectorAll("article.his details")).toHaveLength(0);
    for (const words of host.querySelectorAll("article.his .words")) expect(words.textContent).not.toContain("SECRETHERS");
  });

  it("quotes the line of hers his message answered", () => {
    const host = mount(<Document doc={evening} />);
    const replying = must(host.querySelector(".replying"));
    expect(replying.textContent).toContain("replying to");
    expect(replying.querySelector("q")?.textContent).toBe("The harbour sounds like");
  });

  it("shows the prompt in a wash with its questions folded and the asked one marked", () => {
    const host = mount(<Document doc={evening} />);
    const prompt = must(host.querySelector(".prompt"));
    expect(prompt.textContent).toContain("Sent 23:00 IST");
    expect(prompt.querySelector(".opener")?.textContent).toBe("Evening 🌿");
    const questions = must(prompt.querySelector("details.questions"));
    expect(questions.hasAttribute("open")).toBe(false);
    expect(questions.querySelectorAll("li.asked")).toHaveLength(1);
    expect(questions.querySelector("li.asked")?.textContent).toContain("What's spinning in your mind?");
  });

  it("puts phase dividers between program blocks and the summary at the end", () => {
    const host = mount(<Document doc={session} />);
    const dividers = [...host.querySelectorAll(".phase-divider")].map((d) => d.textContent);
    expect(dividers).toEqual(["Phase 1 · Current Reality Excavation", "Phase 2 · The 5-Year Tuesday"]);
    const children = [...must(host.querySelector(".document")).children].map((c) => c.className.split(" ")[0]);
    expect(children.at(-1)).toBe("summary");
    expect(host.querySelector(".summary")?.textContent).toContain("Two phases done.");
  });

  it("labels an interrupt's answer and gives the phase after it its divider again", () => {
    const at = { source: "text", audio: null, reply_to: null };
    const blocks = [
      { kind: "his", key: "words:0", at: "07:00 IST", text: "I wake at six.", phase: 3, ...at },
      { kind: "his", key: "words:1", at: "11:00 IST", text: "The tax form.", phase: null, interrupt: 3, ...at },
      { kind: "his", key: "words:2", at: "15:00 IST", text: "The tax form again.", phase: null, interrupt: 4, ...at },
      { kind: "his", key: "words:3", at: "20:00 IST", text: "Evening.", phase: 3, ...at },
    ];
    const host = mount(<Document doc={EntryDoc({ ...fixtures.entrySession, blocks })} />);
    expect([...host.querySelectorAll(".phase-divider")].map((d) => d.textContent)).toEqual([
      "Phase 3 · The 10-Year Tuesday",
      "Interrupt 3",
      "Interrupt 4",
      "Phase 3 · The 10-Year Tuesday",
    ]);
  });

  it("has no control that edits or removes a block of his", () => {
    const host = mount(<Document doc={evening} />);
    expect(host.querySelectorAll("button, textarea, input, [contenteditable]")).toHaveLength(0);
  });

  it("shows the pictures kept with a block above his caption, served from /media", () => {
    const doc = EntryDoc({
      ...fixtures.entryEvening,
      blocks: [{ kind: "his", key: "words:0", at: "14:02 IST", source: "photo", text: "The lake.", audio: null,
                 images: ["2026-10-14-evening.jpg", "2026-10-14-evening-2.png"], phase: null, reply_to: null }],
    });
    const host = mount(<Document doc={doc} />);
    const imgs = [...host.querySelectorAll(".block.his .pictures img")];
    expect(imgs.map((i) => i.getAttribute("src"))).toEqual([
      "/api/v1/media/2026-10-14-evening.jpg",
      "/api/v1/media/2026-10-14-evening-2.png",
    ]);
    expect(host.querySelector(".source")?.textContent).toContain("photo");
    expect(host.querySelector(".words")?.textContent).toBe("The lake.");
  });

  it("shows a badge, not the engine's marker, for a transcript that has not arrived", () => {
    const doc = EntryDoc({
      ...fixtures.entryEvening,
      blocks: [{ kind: "his", key: "words:0", at: "23:40 IST", source: "voice", text: "_(audio file — transcript pending)_", audio: "a.ogg", phase: null }],
    });
    const host = mount(<Document doc={doc} />);
    expect(host.querySelector(".badge.pending")?.textContent).toBe("transcript pending");
    expect(host.querySelector(".words")).toBeNull();
  });
});
