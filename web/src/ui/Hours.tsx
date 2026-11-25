/**
 * The hours in the left margin beside his words (design/DESIGN.md, "The margins"): the time each paragraph was begun,
 * in the book's italic, hung in the margin level with its paragraph. An hour shows where it changes, not on every
 * line: three paragraphs begun at 23:12, 23:12 and 23:20 show 23:12 and 23:20.
 *
 * `MarginHour` is one hour, its look only; where it hangs is the page's (the write room places its own over a
 * textarea). `HourLines` is his words line for line (as typed: each line a paragraph, a blank one kept) with their
 * hours; `times` has one "HH:MM" per line that is not blank (the entry document's `times`, issue #40). The gap to the
 * column is `--hour-gap` (28 px), and `shown` is an hour already in the margin beside the first line (the block's own).
 */
import type { JSX } from "preact";
import "./Hours.css";

/** Each hour where it differs from the one shown before it, else null: what the margin shows. `shown`: the hour
 *  already in the margin above the first. */
export function shownHours(hours: (string | null | undefined)[], shown?: string | null): (string | null)[] {
  let last = shown ?? null;
  return hours.map((h) => {
    if (!h || h === last) return null;
    last = h;
    return h;
  });
}

export function MarginHour({ at, class: cls, ...rest }: { at: string; class?: string } & Omit<JSX.HTMLAttributes<HTMLTimeElement>, "class">) {
  return <time class={["margin-hour", cls].filter(Boolean).join(" ")} {...rest}>{at}</time>;
}

export function HourLines({ text, times, shown, class: cls }: { text: string; times: string[]; shown?: string; class?: string }) {
  const lines = text.split("\n");
  let n = 0;
  const own = lines.map((line) => (line.trim() ? (times[n++] ?? null) : null));
  const hours = shownHours(own, shown);
  return (
    <div class={["hour-lines", cls].filter(Boolean).join(" ")}>
      {lines.map((line, i) => (
        <div class="hour-line" key={i}>
          {hours[i] && <MarginHour at={hours[i] as string} />}
          {line || <br />}
        </div>
      ))}
    </div>
  );
}
