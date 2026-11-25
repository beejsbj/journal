/**
 * A day as one log (issue #82): the day's lines from every entry, in time order. The hour hangs in the left margin
 * and is the way into the entry the line is in (its block); his words are the book's roman; a run of her replies
 * folds under one quiet head ("Ada · 2 replies"), her words typed in her ink when it opens (MarginNote). When the
 * entry changes, its name stands above its first line as a link. Read-only: nothing here edits a word.
 *
 * `LogEntry` (the entry's name), `LogLine` (one line of his), `LogReplies` (her replies after it) are the rows of an
 * `<ol class="day-log">`; the route lays them out, /design draws them.
 */
import type { ComponentChildren } from "preact";
import { clip } from "../format";
import { Disclosure } from "./Disclosure";
import { MarginNote } from "./MarginNote";
import "./DayLog.css";

export function DayLog({ children, class: cls }: { children: ComponentChildren; class?: string }) {
  return <ol class={["day-log", cls].filter(Boolean).join(" ")}>{children}</ol>;
}

/** The entry the lines below come from: its name, a link to it. */
export function LogEntry({ href, children }: { href: string; children: ComponentChildren }) {
  return (
    <li class="log-entry">
      <a href={href} data-vt="push">{children}</a>
    </li>
  );
}

/** One line of his: the hour (a link to the line in its entry; `day` is the weekday when it fell after midnight),
 *  a word for how it came when it was not typed (voice, photo), and his words. */
export function LogLine({ at, day, source, href, label, children }: {
  at: string | null;
  day?: string | undefined;
  source?: string | undefined;
  href: string;
  /** What the hour's link says to a screen reader: where it goes ("Open in Evening journal"). */
  label: string;
  children: ComponentChildren;
}) {
  return (
    <li class="log-line">
      <a class="log-hour" href={href} data-vt="push" aria-label={`${at ?? "No time"}${day ? `, ${day}` : ""}. ${label}`}>
        {day && <span class="log-day">{day}</span>}
        <time>{at ?? "·"}</time>
        {source && source !== "text" && <span class="log-source">{source}</span>}
      </a>
      <div class="log-words read">{children}</div>
    </li>
  );
}

/** Her replies after a line of his, folded under one head; each opens as her note with its time. */
export function LogReplies({ who, replies, open, onToggle }: {
  who: string;
  replies: { key: string; at: string | null; text: ComponentChildren; plain: string }[];
  open?: boolean;
  onToggle?: (open: boolean) => void;
}) {
  const first = replies[0];
  const n = replies.length;
  return (
    <li class="log-hers">
      <Disclosure class="log-hers-fold" open={open} onToggle={onToggle}
        head={<span class="log-hers-head"><span class="her-mark" aria-hidden="true" />{who}{n > 1 ? ` · ${n} replies` : first?.at ? ` · ${first.at}` : ""}</span>}
        peek={first ? clip(first.plain, 90) : undefined}>
        {replies.map((r) => (
          <MarginNote key={r.key} who={who} at={r.at ? <time>{r.at}</time> : undefined}>{r.text}</MarginNote>
        ))}
      </Disclosure>
    </li>
  );
}
