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

  // WEB-UX (round 02): her chat replies were folded callouts (details.block.hers); they now stand in the right margin as
  // her notes (ui/MarginNote), in full, beside the passage they follow. Still labelled, still never inside his text.
  it("sets hers beside the passage it follows: labelled with her name and time, never inside his text", () => {
    const host = mount(<Document doc={evening} />);
    const hers = [...host.querySelectorAll(".block.hers")];
    expect(hers).toHaveLength(1);
    const note = must(hers[0]);
    expect(note.tagName).toBe("ASIDE");
    expect(note.getAttribute("aria-label")).toBe("Ada's note");
    expect(note.querySelector(".margin-note-lab")?.textContent).toBe("Ada · 23:45");
    expect(note.querySelector(".margin-note-body")?.textContent).toContain("Second line.");
    // beside the passage it follows, in the same row of the spread, and outside his article
    const passage = must(note.closest(".passage"));
    expect(passage.querySelector("article.his .words")?.textContent).toBe("Later I thought of the harbour.");
    expect(note.closest("article")).toBeNull();
    expect(host.querySelectorAll("article.his .block.hers")).toHaveLength(0);
    for (const words of host.querySelectorAll("article.his .words")) expect(words.textContent).not.toContain("SECRETHERS");
  });

  it("quotes the line of hers his message answered", () => {
    const host = mount(<Document doc={evening} />);
    const replying = must(host.querySelector(".replying"));
    expect(replying.textContent).toContain("replying to");
    expect(replying.querySelector("q")?.textContent).toBe("The harbour sounds like");
  });

  // WEB-UX: the prompt is the design system's letter (ui/Letter): "From Ada · sent 23:00", sectioned, no fastening.
  it("shows the prompt as her letter with its questions folded and the asked one marked", () => {
    const host = mount(<Document doc={evening} />);
    const letter = must(host.querySelector(".document > .doc-letter > .letter"));
    expect(letter.getAttribute("aria-label")).toBe("Ada's prompt");
    expect(letter.querySelector(".letter-from")?.textContent).toContain("From Ada");
    expect(letter.textContent).toContain("sent 23:00 IST");
    expect(letter.querySelector(".letter-body p")?.textContent).toBe("Evening 🌿");
    expect(letter.querySelector(".letter-asked")?.textContent).toBe("What's spinning in your mind?");
    const questions = must(letter.querySelector(".letter-questions"));
    expect(questions.getAttribute("data-open")).toBe("false");
    expect(questions.querySelector(".disclosure-head")?.textContent).toContain("All 24 questions, in 10 parts");
    expect(questions.querySelectorAll("li.asked")).toHaveLength(1);
    expect(questions.querySelector("li.asked")?.textContent).toContain("What's spinning in your mind?");
    // no stamp, clip or tape: the owner's call on round 02
    expect(letter.querySelector(".postmark, .clip, .tape, .fasten")).toBeNull();
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

  // WEB-UX: the recording plays in ui/AudioPlayer (a play button and a speed button over a real <audio>), and the
  // letter folds its questions behind a button: neither edits anything. Every other control is still absent.
  it("has no control that edits or removes a block of his", () => {
    const host = mount(<Document doc={evening} />);
    const controls = [...host.querySelectorAll("button, textarea, input, [contenteditable]")]
      .filter((el) => !el.closest(".player") && !el.closest(".letter-questions"));
    expect(controls).toHaveLength(0);
    expect(host.querySelectorAll(".block-actions, .block-editor")).toHaveLength(0);
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

  it("shows a quiet word, not the engine's marker, for a transcript that has not arrived", () => {
    const doc = EntryDoc({
      ...fixtures.entryEvening,
      blocks: [{ kind: "his", key: "words:0", at: "23:40 IST", source: "voice", text: "_(audio file — transcript pending)_", audio: "a.ogg", phase: null }],
    });
    const host = mount(<Document doc={doc} />);
    expect(host.querySelector(".transcript-pending")?.textContent).toBe("transcript pending");
    expect(host.querySelector(".words")).toBeNull();
  });
});
