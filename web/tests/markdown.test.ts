import { describe, expect, it } from "vitest";
import { parseInline, parseMarkdown } from "../src/markdown";

describe("the safe renderer", () => {
  it("makes paragraphs, bullets, bold and italic", () => {
    const blocks = parseMarkdown("A **bold** and *soft* line\nsecond line\n\n- one\n- two with _under_\n\nlast");
    expect(blocks).toEqual([
      { type: "p", inline: [{ text: "A " }, { text: "bold", bold: true }, { text: " and " }, { text: "soft", italic: true }, { text: " line\nsecond line" }] },
      { type: "ul", items: [[{ text: "one" }], [{ text: "two with " }, { text: "under", italic: true }]] },
      { type: "p", inline: [{ text: "last" }] },
    ]);
  });

  it("keeps everything else as text: HTML, links, headings", () => {
    const blocks = parseMarkdown("# Heading\n<script>alert(1)</script> [link](http://x)");
    expect(blocks).toHaveLength(1);
    const text = (blocks[0] as { inline: { text: string }[] }).inline.map((i) => i.text).join("");
    expect(text).toBe("# Heading\n<script>alert(1)</script> [link](http://x)");
  });

  it("leaves an unmatched marker and snake_case alone", () => {
    expect(parseInline("2 * 3 and a_b_c and **open")).toEqual([{ text: "2 * 3 and a_b_c and **open" }]);
  });

  it("joins a wrapped bullet", () => {
    expect(parseMarkdown("- first part\n  continued")).toEqual([{ type: "ul", items: [[{ text: "first part continued" }]] }]);
  });

  it("makes a quote of lines that begin with \"> \", its own paragraphs inside", () => {
    expect(parseMarkdown("> What did you notice?\n> Name it.\n>\n> **Then** rest.\n\nAfter.")).toEqual([
      { type: "quote", blocks: [
        { type: "p", inline: [{ text: "What did you notice?\nName it." }] },
        { type: "p", inline: [{ text: "Then", bold: true }, { text: " rest." }] },
      ] },
      { type: "p", inline: [{ text: "After." }] },
    ]);
  });

  it("stops nesting quotes after a few levels: the rest is text", () => {
    const deep = parseMarkdown(">>>>> deep");
    let level = deep[0] as { type: string; blocks?: unknown[] };
    let n = 0;
    while (level.type === "quote") {
      n += 1;
      level = (level.blocks as typeof level[])[0] as typeof level;
    }
    expect(n).toBe(3);
    expect(level).toEqual({ type: "p", inline: [{ text: ">> deep" }] });
  });
});
