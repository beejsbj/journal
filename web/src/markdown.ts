/**
 * The safe renderer for the companion's Markdown (annotations, reflections, review): paragraphs, bullet lists, bold
 * and italic; everything else is text. The output is a tree of plain data the views turn into elements, so no HTML
 * string is ever built and `<script>` in a reply is just letters (FRONTEND.md §1.3).
 */

export type Inline = { text: string; bold?: boolean; italic?: boolean };
export type MdBlock = { type: "p"; inline: Inline[] } | { type: "ul"; items: Inline[][] };

/** `**bold**`, `*italic*` and `_italic_`; an unmatched marker stays literal. */
export function parseInline(text: string): Inline[] {
  const out: Inline[] = [];
  let buf = "";
  const flush = () => {
    if (buf) out.push({ text: buf });
    buf = "";
  };
  let i = 0;
  while (i < text.length) {
    const two = text.startsWith("**", i);
    const one = !two && (text[i] === "*" || (text[i] === "_" && isBoundary(text, i)));
    if (two || one) {
      const mark = two ? "**" : (text[i] as string);
      const close = text.indexOf(mark, i + mark.length);
      const inner = close > i + mark.length ? text.slice(i + mark.length, close) : "";
      if (inner && !/^\s|\s$/.test(inner) && (two || !inner.includes("\n\n"))) {
        flush();
        out.push(two ? { text: inner, bold: true } : { text: inner, italic: true });
        i = close + mark.length;
        continue;
      }
    }
    buf += text[i];
    i += 1;
  }
  flush();
  return out;
}

/** An underscore opens emphasis only at a word edge, so snake_case stays as written. */
function isBoundary(text: string, at: number): boolean {
  const before = at === 0 ? " " : (text[at - 1] as string);
  return /[\s([{>"'“‘—-]/.test(before);
}

export function parseMarkdown(source: string): MdBlock[] {
  const blocks: MdBlock[] = [];
  let paragraph: string[] = [];
  let list: string[] | null = null;
  const endParagraph = () => {
    if (paragraph.length) blocks.push({ type: "p", inline: parseInline(paragraph.join("\n")) });
    paragraph = [];
  };
  const endList = () => {
    if (list) blocks.push({ type: "ul", items: list.map(parseInline) });
    list = null;
  };
  for (const raw of source.replace(/\r\n?/g, "\n").split("\n")) {
    const line = raw.trimEnd();
    const bullet = /^\s{0,3}[-*+•]\s+(.*)$/.exec(line);
    if (!line.trim()) {
      endParagraph();
      endList();
    } else if (bullet) {
      endParagraph();
      (list ??= []).push(bullet[1] ?? "");
    } else if (list && /^\s{2,}\S/.test(line)) {
      // a wrapped bullet: it continues the item above
      list[list.length - 1] += ` ${line.trim()}`;
    } else {
      endList();
      paragraph.push(line);
    }
  }
  endParagraph();
  endList();
  return blocks;
}
