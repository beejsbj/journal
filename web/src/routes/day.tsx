/**
 * A day as one log (issue #82): every line of every entry filed under one journal day, his and hers, in time order
 * (`GET /days/{date}`). Read-only: each line's hour opens it in its entry, where it can be changed; her replies fold
 * under the line they answer. Reached from Today ("The whole day") and from the Entries list's day headers. A day runs
 * until `windows.day_ends_at` (04:00), so a line after midnight sits at the end with its weekday beside the hour.
 */
import { dayParts } from "../components/EntryRow";
import { Markdown } from "../components/Markdown";
import { useLive } from "../events";
import { dayLabel, longDay, plainTitle } from "../format";
import { useResource } from "../hooks";
import { ChevronLeft, ChevronRight } from "../icons";
import { api } from "../api";
import { useTitle } from "../title";
import type { DayLog as Log, LogLineItem } from "../types";
import { Await, ButtonLink, DayLog, Empty, LogEntry, LogLine, LogReplies, PageBack } from "../ui";
import type { ComponentChildren } from "preact";
import "./day.css";

const SOURCES: Record<string, string> = { voice: "voice", photo: "photo", audio: "audio file" };

function sourceWord(source: string | undefined): string | undefined {
  if (!source || source === "text") return undefined;
  const key = Object.keys(SOURCES).find((k) => source.startsWith(k));
  return key ? SOURCES[key] : undefined;
}

/** The weekday of a line written on another calendar day than the page's (after midnight, or added days later). */
function weekdayIfOther(date: string | null | undefined, page: string): string | undefined {
  return date && date !== page ? dayParts(date).w : undefined;
}

/** The log's rows: the entry's name where the entry changes, his lines, and each run of her replies folded. */
export function logRows(log: Log): ComponentChildren[] {
  const titles = new Map(log.entries.map((e) => [e.stem, plainTitle(e.title)]));
  const out: ComponentChildren[] = [];
  let last: string | null = null;
  const lines = log.lines;
  for (let i = 0; i < lines.length; i++) {
    const l = lines[i] as LogLineItem;
    const title = titles.get(l.stem) ?? l.stem;
    const href = `/entries/${encodeURIComponent(l.stem)}`;
    if (l.stem !== last) {
      out.push(<LogEntry key={`e:${l.stem}:${i}`} href={href}>{title}</LogEntry>);
      last = l.stem;
    }
    if (l.kind === "hers") {
      const run: LogLineItem[] = [l];
      while (i + 1 < lines.length && lines[i + 1]?.kind === "hers" && lines[i + 1]?.stem === l.stem) run.push(lines[++i] as LogLineItem);
      out.push(
        <LogReplies key={`h:${l.stem}:${l.key}`} who={log.voices.hers}
          replies={run.map((r) => ({ key: r.key, at: r.time, day: weekdayIfOther(r.date, log.date), plain: r.text, text: <Markdown text={r.text} /> }))} />,
      );
      continue;
    }
    out.push(
      <LogLine key={`l:${l.stem}:${l.key}`} at={l.time} day={weekdayIfOther(l.date, log.date)} source={sourceWord(l.source)}
        href={`${href}#${l.key}`} label={`Open in ${title}`}>
        {l.text.trim() || (l.audio ? "(a recording)" : l.images?.length ? "(a picture)" : "")}
      </LogLine>,
    );
  }
  return out;
}

export function DayRoute({ date = "" }: { date?: string }) {
  useTitle(dayLabel(date));
  const [res, reload] = useResource((signal) => api.day(date, signal), [date]);
  useLive(["entries-changed"], reload);
  const [weekday = "", ...rest] = longDay(date).split(" ");
  return (
    <div class="day-page">
      <PageBack href={`/entries?day=${date}`}>Entries</PageBack>
      <header class="day-head">
        <span class="numeral day-num" aria-hidden="true">{dayParts(date).n}</span>
        <div class="day-dl">
          <h1 class="display day-title">{weekday}<span class="sr-only"> {rest.join(" ")}</span></h1>
          <p class="sub">{rest.join(" ")}</p>
        </div>
      </header>
      <Await res={res} reload={reload} rows={5}>
        {(log) => (
          <>
            {log.lines.length === 0 ? <Empty>Nothing written this day.</Empty> : <DayLog>{logRows(log)}</DayLog>}
            {(log.prev || log.next) && (
              <nav class="day-nav" aria-label="Other days">
                {log.prev ? (
                  <ButtonLink href={`/days/${log.prev}`} variant="quiet" size="small" icon={<ChevronLeft />} data-vt="pop">
                    {dayLabel(log.prev)}
                  </ButtonLink>
                ) : <span />}
                {log.next && (
                  <ButtonLink href={`/days/${log.next}`} variant="quiet" size="small" data-vt="push">
                    {dayLabel(log.next)}
                    <ChevronRight />
                  </ButtonLink>
                )}
              </nav>
            )}
          </>
        )}
      </Await>
    </div>
  );
}
