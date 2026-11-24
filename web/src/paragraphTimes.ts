/**
 * When each paragraph of a draft was begun (issue #40; DECISIONS.md, PARAGRAPH-TIMES). A paragraph is a line he typed
 * (Enter starts the next, as in the write room); a blank line is none. The box keeps one stamp per line of its text,
 * `null` for a blank line or one whose start it did not see (a draft from before, words another box wrote), and moves
 * them with the text on every change: a line keeps its stamp while he edits it, a new one takes the moment its first
 * letter arrives. A save sends the stamps only when every paragraph has one (`timesToSend`); the server checks them
 * and keeps them beside the block, or drops them and keeps his words with the block's one time.
 */

/** One stamp per line of the text (ms since the epoch), or null. */
export type Stamps = (number | null)[];

/** Whether an edited line is still the line it was: it keeps its first or its last letter (typing inside it, a word
 *  added in front or after, Enter in the middle of it) rather than being replaced whole (a paste over it). */
function sameLine(was: string, now: string): boolean {
  const a = was.trim();
  const b = now.trim();
  return a !== "" && b !== "" && (a[0] === b[0] || a[a.length - 1] === b[b.length - 1]);
}

/** The stamps of `after`, from those of `before` and the moment of the change. */
export function restamp(before: string, stamps: Stamps, after: string, now: number): Stamps {
  const a = before.split("\n");
  const b = after.split("\n");
  const old: Stamps = stamps.length === a.length ? stamps : a.map(() => null);
  let p = 0;
  while (p < a.length && p < b.length && a[p] === b[p]) p += 1;
  let s = 0;
  while (s < a.length - p && s < b.length - p && a[a.length - 1 - s] === b[b.length - 1 - s]) s += 1;
  const mid: Stamps = [];
  for (let j = 0; j < b.length - p - s; j += 1) {
    const line = b[p + j] as string;
    const was = p + j < a.length - s ? (a[p + j] as string) : null;
    // the same line keeps its stamp, or stays unknown (a line from a draft kept before is not begun by an edit)
    mid.push(!line.trim() ? null : was !== null && sameLine(was, line) ? (old[p + j] ?? null) : now);
  }
  return [...old.slice(0, p).map((t, i) => ((b[i] as string).trim() ? t : null)), ...mid, ...old.slice(a.length - s)];
}

/** Stamps read back from the store for `text`: kept when they still fit it, else none seen. */
export function stampsFor(text: string, kept: unknown): Stamps {
  const lines = text.split("\n");
  if (!Array.isArray(kept) || kept.length !== lines.length) return lines.map(() => null);
  return kept.map((t, i) => (typeof t === "number" && Number.isFinite(t) && (lines[i] as string).trim() ? t : null));
}

/** The times a save sends: one ISO time per paragraph, in their order, or nothing when any paragraph has none. */
export function timesToSend(text: string, stamps: Stamps): string[] | undefined {
  const lines = text.split("\n");
  if (stamps.length !== lines.length) return undefined;
  const out: string[] = [];
  for (let i = 0; i < lines.length; i += 1) {
    if (!(lines[i] as string).trim()) continue;
    const t = stamps[i];
    if (t === null || t === undefined) return undefined;
    out.push(new Date(t).toISOString());
  }
  return out.length > 0 ? out : undefined;
}

/** "09:05": a stamp as the margin shows it, on this device's clock. */
export function hourOf(t: number): string {
  const d = new Date(t);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

/** Stamps of a draft carried into another (a note's words going into an answer, after what the answer held):
 *  `had` trimmed at its end, a blank line, then `moved`, as the room joins the two texts. */
export function joinStamps(had: string, hadStamps: Stamps, moved: string, movedStamps: Stamps): Stamps {
  if (!had.trim()) return stampsFor(moved, movedStamps);
  const kept = had.replace(/\s+$/, "").split("\n").length;
  return [...stampsFor(had, hadStamps).slice(0, kept), null, ...stampsFor(moved, movedStamps)];
}
