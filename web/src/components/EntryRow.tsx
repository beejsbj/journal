import type { ComponentChildren } from "preact";
import { plainTitle } from "../format";
import { Chat, Star, Wave } from "../icons";
import type { EntrySummary } from "../types";
import "./EntryRow.css";

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

/** "2026-10-14" → { n: "14", w: "Wed" }: the day's numeral and its weekday, read as a calendar date (no zone). */
export function dayParts(date: string): { n: string; w: string } {
  const [y, m, d] = date.split("-").map(Number) as [number, number, number];
  return { n: String(d), w: WEEKDAYS[new Date(Date.UTC(y, m - 1, d)).getUTCDay()] ?? "" };
}

/** His words with each place the search found marked (`mark.hit`); the text itself is unchanged. */
export function marked(text: string, q: string): ComponentChildren {
  const needle = q.trim();
  if (!needle) return text;
  const out: ComponentChildren[] = [];
  const lower = text.toLowerCase();
  const n = needle.toLowerCase();
  let at = 0;
  for (let i = lower.indexOf(n); i !== -1 && n; i = lower.indexOf(n, at)) {
    if (i > at) out.push(text.slice(at, i));
    out.push(<mark class="hit" key={i}>{text.slice(i, i + n.length)}</mark>);
    at = i + n.length;
  }
  if (at < text.length) out.push(text.slice(at));
  return out;
}

/** What an entry carries, as small marks: her dot when she annotated it, a voice, a conversation, his star. */
export function Marks({ entry }: { entry: EntrySummary }) {
  const any = entry.meta.starred || entry.flags.annotated || entry.flags.has_conversation || entry.flags.audio;
  if (!any) return null;
  return (
    <span class="marks">
      {entry.flags.annotated && <span class="her-mark" role="img" aria-label="annotated" title="annotated" />}
      {entry.flags.audio && <Wave aria-label="has audio" />}
      {entry.flags.has_conversation && <Chat aria-label="has a conversation" />}
      {entry.meta.starred && <Star aria-label="starred" />}
    </span>
  );
}

/**
 * One entry in the list: the practice's name and the time, quiet, on a line with its marks; then his first line, the
 * line that matters, in the book's roman (two lines at most, the search's hits marked); then the moods and people he
 * set, as words. No emoji, no pills.
 */
export function EntryRow({ entry, q = "", i = 0, vt }: { entry: EntrySummary; q?: string; i?: number; vt?: string }) {
  const title = plainTitle(entry.title);
  const tags = [...(entry.meta.mood ?? []), ...(entry.meta.people ?? []).map((p) => `with ${p}`)];
  const note = entry.kind === "j" || /^j-\d+$/.test(entry.kind);
  return (
    <a class={["entry-row", "rise", note && "note"].filter(Boolean).join(" ")} href={`/entries/${entry.stem}`} data-vt={vt}
      style={{ "--i": Math.min(i, 14) }}>
      <span class="row-top">
        <span class="title">{title}</span>
        <Marks entry={entry} />
        {entry.time && <time>{entry.time}</time>}
      </span>
      <span class="first-line">{marked(entry.first_line || "…", q)}</span>
      {tags.length > 0 && <span class="row-tags">{tags.join(" · ")}</span>}
    </a>
  );
}

/**
 * One entry in the titled sidebar beside an open entry: the date and the time together in the left column, the time
 * under the date (the owner's change to round 02), then the title; nothing at the right of the row but its marks.
 */
export function SideRow({ entry, current, vt }: { entry: EntrySummary; current: boolean; vt?: string }) {
  const { n, w } = dayParts(entry.date);
  return (
    <a class="side-row" href={`/entries/${entry.stem}`} aria-current={current ? "page" : undefined} data-vt={vt}>
      <span class="side-when">
        <span class="side-date"><b>{n}</b> {w}</span>
        {entry.time && <time>{entry.time}</time>}
      </span>
      <span class="side-title">{plainTitle(entry.title)}</span>
      <Marks entry={entry} />
    </a>
  );
}
