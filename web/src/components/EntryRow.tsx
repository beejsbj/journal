import { plainTitle } from "../format";
import { Chat, Mic, Note, Star } from "../icons";
import type { EntrySummary } from "../types";

/** One entry in a list: emoji and title, time, his first line, chips for what he set by hand, small indicators. */
export function EntryRow({ entry }: { entry: EntrySummary }) {
  const title = plainTitle(entry.title);
  const people = entry.meta.people ?? [];
  const mood = entry.meta.mood ?? [];
  return (
    <a class="entry-row" href={`/entries/${entry.stem}`}>
      {entry.emoji && <span class="emoji" aria-hidden="true">{entry.emoji}</span>}
      <span class="main">
        <span class="line1">
          <span class="title">{title}</span>
          {entry.time && <time>{entry.time}</time>}
        </span>
        <span class="first-line">{entry.first_line || "…"}</span>
        {(mood.length > 0 || people.length > 0) && (
          <span class="chips">
            {mood.map((m) => (
              <span key={`m-${m}`} class="chip">{m}</span>
            ))}
            {people.map((p) => (
              <span key={`p-${p}`} class="chip person">{p}</span>
            ))}
          </span>
        )}
      </span>
      <span class="marks">
        {entry.meta.starred && <Star width={14} height={14} aria-label="starred" />}
        {entry.flags.annotated && <Note width={14} height={14} aria-label="annotated" />}
        {entry.flags.has_conversation && <Chat width={14} height={14} aria-label="has a conversation" />}
        {entry.flags.audio && <Mic width={14} height={14} aria-label="has audio" />}
      </span>
    </a>
  );
}
