/**
 * Her ink: the colour the companion's words are typed in. Four inks, each with a light and a dark value in
 * `tokens.css` (`[data-ink="…"]`); blue-black is the default. The choice is a display preference of this device, kept
 * in `localStorage` under `journal:ink` and set on `<html data-ink>` before the first render, so the page never shows
 * another ink first. It is not sent to the server (the server keeps no display preferences: DECISIONS.md, IDENTITY)
 * and it survives an unpair, since it says nothing about the journal.
 */
export const INKS = [
  { id: "blue-black", label: "Blue-black", note: "a typewriter ribbon" },
  { id: "brown", label: "Brown", note: "iron-gall, as a reader annotates" },
  { id: "violet", label: "Violet", note: "a carbon copy" },
  { id: "green", label: "Green", note: "post-office ink" },
] as const;

export type Ink = (typeof INKS)[number]["id"];

export const DEFAULT_INK: Ink = "blue-black";
const KEY = "journal:ink";

export function isInk(value: unknown): value is Ink {
  return INKS.some((i) => i.id === value);
}

/** The ink this device chose, or the default (also when storage cannot be read). */
export function savedInk(): Ink {
  try {
    const v = localStorage.getItem(KEY);
    return isInk(v) ? v : DEFAULT_INK;
  } catch {
    return DEFAULT_INK;
  }
}

/** Put an ink on the page. */
export function applyInk(ink: Ink): void {
  document.documentElement.dataset.ink = ink;
}

/** Choose an ink: on the page at once, and kept for the next visit on this device. */
export function chooseInk(ink: Ink): void {
  applyInk(ink);
  try {
    if (ink === DEFAULT_INK) localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, ink);
  } catch {
    /* storage refused (a private window): the choice holds for this visit */
  }
}
