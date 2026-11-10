/**
 * Insights (round 02, accepted: Folio's page with Night Room's programs strip). The week, chosen with Earlier and
 * Later (26 weeks), set large; its figures said as a sentence built only from the counts the server gives; the
 * programs it moved, as strips; her review of the week as a letter of hers, the rest of it folded; and "Day by day,
 * from Ada": each day's reflection folded to its date and her first line. `/insights#<stem>` opens the week that
 * holds that reflection and unfolds it. No chart of words per day: the weeks' answer has no per-day counts, and the
 * page does not invent them.
 */
import type { ComponentChildren } from "preact";
import { useEffect, useState } from "preact/hooks";
import { api } from "../api";
import { Markdown } from "../components/Markdown";
import { useLive } from "../events";
import { addDays, dayLabel, parseDay, weekdayIndex } from "../format";
import { useResource } from "../hooks";
import { ChevronLeft, ChevronRight } from "../icons";
import { useTitle } from "../title";
import type { LibraryItem, Reflection, Week } from "../types";
import { Await, Button, Disclosure, Empty, Letter, ProgressStrip } from "../ui";
import "./insights.css";

const WEEKS = 26;
const MONTHS_LONG = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

const n = (x: number) => x.toLocaleString("en");

/** The week's figures as a sentence, from the counts the server gives and nothing else; a clause whose count says
 *  nothing (no prompts recorded, no reflections) is left out rather than said as a zero. */
export function weekProse(w: Week, her: string | null): ComponentChildren[] {
  const out: ComponentChildren[] = [];
  const she = her ?? "She";
  if (w.entries === 0) out.push("Nothing was written this week.");
  else if (w.words > 0) out.push(<>You wrote <b>{n(w.words)}</b> {w.words === 1 ? "word" : "words"} in <b>{n(w.entries)}</b> {w.entries === 1 ? "entry" : "entries"}.</>);
  else out.push(<><b>{n(w.entries)}</b> {w.entries === 1 ? "entry" : "entries"} this week.</>);
  const asked = w.answered + w.missed;
  const from = her ? ` from ${her}` : "";
  if (asked === 1) out.push(w.answered === 1 ? <>You answered <b>the one</b> prompt{from}.</> : <>The one prompt{from} passed unanswered.</>);
  else if (asked > 1 && w.answered === asked) out.push(<>You answered <b>all {n(asked)}</b> prompts{from}.</>);
  else if (asked > 1 && w.answered === 0) out.push(<><b>{n(asked)}</b> prompts{from} passed unanswered.</>);
  else if (asked > 1) out.push(<>You answered <b>{n(w.answered)}</b> of <b>{n(asked)}</b> prompts{from}.</>);
  const days = w.reflections.length;
  if (days > 0 && w.review) out.push(<>{she} wrote <b>{n(days)}</b> day {days === 1 ? "reflection" : "reflections"} and her review of the week.</>);
  else if (days > 0) out.push(<>{she} wrote <b>{n(days)}</b> day {days === 1 ? "reflection" : "reflections"}.</>);
  else if (w.review) out.push(<>{she} wrote her review of the week.</>);
  return out;
}

/** The week's range as its title: "12 – 18" and "October", or across a month's end "28 – 4" and "September into
 *  October". */
export function weekTitle(end: string): { nums: string; month: string } {
  const a = parseDay(addDays(end, -6));
  const b = parseDay(end);
  if (!a || !b) return { nums: end, month: "" };
  return { nums: `${a.d} – ${b.d}`, month: a.m === b.m ? MONTHS_LONG[b.m - 1]! : `${MONTHS_LONG[a.m - 1]} into ${MONTHS_LONG[b.m - 1]}` };
}

/** "This week", "Last week", "3 weeks ago". */
const ago = (i: number) => (i === 0 ? "This week" : i === 1 ? "Last week" : `${i} weeks ago`);

/** Her first line, as plain words: the first line of her Markdown without its marks. */
export function firstLine(body: string): string {
  const line = body.split("\n").map((l) => l.trim()).find((l) => l.length > 0) ?? "";
  return line.replace(/^([-*>#]+\s*)+/, "").replace(/\*\*|__|\*|_/g, "").trim();
}

/** Her words after the line the fold already shows (all of them when that line is all there is). */
export function afterFirstLine(body: string): string {
  const lines = body.split("\n");
  const at = lines.findIndex((l) => l.trim().length > 0);
  const rest = lines.slice(at + 1).join("\n").trim();
  return rest || body;
}

/** An entry's stem as words: "Wed 14 Oct, evening". */
function stemWords(stem: string): string {
  const day = stem.slice(0, 10);
  const kind = stem.slice(11).replace(/-/g, " ");
  return parseDay(day) ? `${dayLabel(day)}${kind ? `, ${kind}` : ""}` : stem;
}

/** A program's line for the week ("3 of 10 sessions done, 1 this week") read for its strip, when it says so. */
export function progressOf(line: string): { done: number; total: number } | null {
  const m = /(\d+) of (\d+)/.exec(line);
  return m ? { done: Number(m[1]), total: Number(m[2]) } : null;
}

function Sources({ r }: { r: Reflection }) {
  if (r.sources.length === 0) return null;
  return (
    <p class="from">
      From{" "}
      {r.sources.map((s, i) => (
        <span key={s}>
          {i > 0 && ", "}
          <a href={`/entries/${s}`} data-vt="push">{stemWords(s)}</a>
        </span>
      ))}
    </p>
  );
}

/** Her review of the week: a letter of hers, its first paragraph large, the rest folded. */
function Review({ r, her, open, onToggle }: { r: Reflection; her: string | null; open: boolean; onToggle: (o: boolean) => void }) {
  const paras = r.body.split(/\n\s*\n/).map((p) => p.trim()).filter(Boolean);
  const [first, ...rest] = paras;
  return (
    <div id={r.stem} class="review-wrap">
      <Letter class="review" from={her ?? "the companion"} at={dayLabel(r.date)} label="Her review of the week"
        opener={<div class="review-pull"><Markdown text={first ?? ""} /></div>}
        foot={
          rest.length > 0 || r.sources.length > 0 ? (
            <Disclosure class="review-more" head="Read the whole review" open={open} onToggle={onToggle}>
              {rest.length > 0 && <Markdown text={rest.join("\n\n")} />}
              <Sources r={r} />
            </Disclosure>
          ) : undefined
        } />
    </div>
  );
}

/** A day's reflection, folded to its date and her first line. */
function DaySlip({ r, open, onToggle, i }: { r: Reflection; open: boolean; onToggle: (o: boolean) => void; i: number }) {
  const d = parseDay(r.date);
  return (
    <Disclosure id={r.stem} class="reflection rise" headClass="slip-head" open={open} onToggle={onToggle}
      head={
        <span class="slip">
          <span class="day-n" aria-hidden="true">{d?.d ?? ""}<small>{WEEKDAYS[weekdayIndex(r.date)]}</small></span>
          <span class="visually-hidden">{dayLabel(r.date)}: </span>
          <span class="slip-line her">{firstLine(r.body) || r.title}</span>
        </span>
      }>
      <div class="slip-body her" style={{ "--i": i }}>
        <Markdown text={afterFirstLine(r.body)} />
      </div>
      <Sources r={r} />
    </Disclosure>
  );
}

/**
 * One week: its counts at once, its reflections read for that week alone (from the Monday before its end to its end).
 * Reading all 26 weeks' reflections in one capped list would silently drop the oldest weeks'.
 */
function WeekBody({ week, index, focus, her, titles }: { week: Week; index: number; focus: string; her: string | null; titles: Map<string, string> }) {
  const [res, reload] = useResource(
    (signal) => api.reflections({ from: addDays(week.week_end, -6), to: week.week_end, limit: 50 }, signal),
    [week.week_end],
  );
  useLive(["entries-changed", "ledger-changed", "tick"], reload);   // a new day reflection is no entries-changed
  const [opened, setOpened] = useState<Set<string>>(() => new Set(focus ? [focus] : []));
  useEffect(() => {
    if (focus) setOpened((s) => new Set([...s, focus]));
  }, [focus]);
  useEffect(() => {
    if (res.state === "ready" && focus) setTimeout(() => document.getElementById(focus)?.scrollIntoView?.({ block: "center" }), 0);
  }, [res.state === "ready", focus, week.week_end]);
  const toggle = (stem: string) => (o: boolean) =>
    setOpened((s) => {
      const next = new Set(s);
      if (o) next.add(stem);
      else next.delete(stem);
      return next;
    });
  const progress = Object.entries(week.program_progress);
  const name = her ?? "her";
  return (
    <div class={`ins-grid week-card${progress.length === 0 ? " no-programs" : ""}`}>
      <div class="ins-col">
        <p class="prose-figs">
          {weekProse(week, her).map((s, i) => <span key={i}>{i > 0 && " "}{s}</span>)}
        </p>
        {progress.length > 0 && (
          <section class="ins-sec ins-programs" aria-labelledby="programs-h">
            <h2 id="programs-h" class="book-head">Programs</h2>
            {progress.map(([id, v]) => {
              const line = typeof v === "string" ? v : JSON.stringify(v);
              const p = progressOf(line);
              const title = titles.get(id) ?? id;
              return p ? (
                <ProgressStrip key={id} title={title} label={`${title}, sessions`} done={p.done} total={p.total} meta={line} />
              ) : (
                <p key={id} class="progress"><b>{title}</b> {line}</p>
              );
            })}
          </section>
        )}
      </div>
      <div class="ins-col">
        <Await res={res} reload={reload} rows={2}>
          {(reflections) => {
            const review = reflections.find((r) => r.stem === week.review);
            const days = week.reflections.map((s) => reflections.find((r) => r.stem === s)).filter((r): r is Reflection => !!r);
            return (
              <>
                <section class="ins-review" aria-labelledby="review-h">
                  <h2 id="review-h" class="kicker">Her review of the week</h2>
                  {review ? (
                    <Review r={review} her={her} open={opened.has(review.stem)} onToggle={toggle(review.stem)} />
                  ) : (
                    <p class="review-pending">
                      {index === 0
                        ? `${her ? `${her}'s` : "Her"} review of the week comes when the week closes. Until then, the days speak for themselves.`
                        : "No review was written for this week."}
                    </p>
                  )}
                </section>
                <section class="ins-days" aria-labelledby="days-h">
                  <h2 id="days-h" class="book-head">Day by day, from {name}</h2>
                  {days.length === 0 ? (
                    <Empty>No day reflections this week.</Empty>
                  ) : (
                    days.map((r, i) => <DaySlip key={r.stem} r={r} i={i} open={opened.has(r.stem)} onToggle={toggle(r.stem)} />)
                  )}
                </section>
              </>
            );
          }}
        </Await>
      </div>
    </div>
  );
}

/** Which week (index into the newest-first list) holds the reflection a link names. */
export function weekFor(weeks: Week[], stem: string): number {
  const i = weeks.findIndex((w) => w.review === stem || w.reflections.includes(stem));
  return i;
}

/** The stem a link names in the address (`/insights#<stem>`); a hash that is not valid percent-encoding is read raw. */
function hashStem(): string {
  const raw = window.location.hash.slice(1);
  try {
    return decodeURIComponent(raw);
  } catch {
    return raw;
  }
}

export function InsightsRoute() {
  useTitle("Insights");
  const [index, setIndex] = useState(0);
  const [res, reload] = useResource(async (signal) => {
    const today = await api.today(signal);
    const from = addDays(today.date, -7 * WEEKS);
    const [weeks, library] = await Promise.all([
      api.weeks({ from, to: today.date }, signal),
      api.library(signal).catch((e: unknown) => {
        if (e instanceof DOMException) throw e;
        return null as LibraryItem[] | null;   // the programs' titles are a nicety: their ids still read
      }),
    ]);
    return { weeks, her: today.voices?.hers ?? null, titles: new Map((library ?? []).map((i) => [i.id, i.title])) };
  }, []);
  useLive(["ledger-changed", "entries-changed", "library-changed", "tick"], reload);   // a prompt can pass its window with no file changing   // the week boundary is a library setting

  const focus = hashStem();
  useEffect(() => {
    if (res.state !== "ready" || !focus) return;
    const i = weekFor(res.data.weeks, focus);
    if (i >= 0) setIndex(i);   // the week scrolls to the reflection once it has read it
  }, [res.state === "ready", focus]);

  return (
    <div class="insights-page">
      <Await res={res} reload={reload} rows={3}>
        {({ weeks, her, titles }) => {
          const at = Math.min(index, weeks.length - 1);
          const week = weeks[at];
          if (!week) return <><h1 class="display">Insights</h1><Empty>Nothing to show yet.</Empty></>;
          const t = weekTitle(week.week_end);
          return (
            <>
              <div class="week-stepper" role="group" aria-label="Choose a week">
                <Button variant="quiet" size="small" icon={<ChevronLeft />} disabled={at >= weeks.length - 1} onClick={() => setIndex(at + 1)}>
                  Earlier
                </Button>
                <p class="kicker week-ago" aria-live="polite">
                  {ago(at)}
                  <span class="visually-hidden">: {t.nums} {t.month}</span>
                </p>
                <Button variant="quiet" size="small" class="later" disabled={at <= 0} onClick={() => setIndex(at - 1)}>
                  Later
                  <ChevronRight />
                </Button>
              </div>
              <h1 class="ins-title" key={week.week_end}>
                <span class="visually-hidden">Insights, the week of </span>
                <span class="nums">{t.nums}</span> <em>{t.month}</em>
              </h1>
              <WeekBody key={week.week_end} week={week} index={at} focus={focus} her={her} titles={titles} />
            </>
          );
        }}
      </Await>
    </div>
  );
}
