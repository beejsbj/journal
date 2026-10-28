import { useEffect, useState } from "preact/hooks";
import { api } from "../api";
import { Markdown } from "../components/Markdown";
import { Await, Empty } from "../components/Status";
import { useLive } from "../events";
import { addDays, plural, weekRange } from "../format";
import { useResource } from "../hooks";
import { useTitle } from "../title";
import type { Reflection, Week } from "../types";

const WEEKS = 26;

function ReflectionItem({ r, open }: { r: Reflection; open: boolean }) {
  return (
    <details class="reflection" id={r.stem} open={open || undefined}>
      <summary>{r.title}</summary>
      <Markdown text={r.body} />
      {r.sources.length > 0 && (
        <p class="from">
          From{" "}
          {r.sources.map((s, i) => (
            <span key={s}>
              {i > 0 && ", "}
              <a href={`/entries/${s}`}>{s}</a>
            </span>
          ))}
        </p>
      )}
    </details>
  );
}

/**
 * One week: its counts at once, its reflections read for that week alone (from the Monday before its end to its end).
 * Reading all 26 weeks' reflections in one capped list would silently drop the oldest weeks'.
 */
function WeekCard({ week, focus }: { week: Week; focus: string }) {
  const [res, reload] = useResource(
    (signal) => api.reflections({ from: addDays(week.week_end, -6), to: week.week_end, limit: 50 }, signal),
    [week.week_end],
  );
  useLive(["entries-changed", "ledger-changed", "tick"], reload);   // a new day reflection is no entries-changed
  useEffect(() => {
    if (res.state === "ready" && focus) setTimeout(() => document.getElementById(focus)?.scrollIntoView({ block: "center" }), 0);
  }, [res.state === "ready", focus, week.week_end]);
  const progress = Object.entries(week.program_progress);
  return (
    <article class="week-card">
      <dl class="counts">
        <div><dt>Entries</dt><dd>{week.entries}</dd></div>
        <div><dt>Words</dt><dd>{week.words}</dd></div>
        <div><dt>Answered</dt><dd>{week.answered}</dd></div>
        <div><dt>Missed</dt><dd>{week.missed}</dd></div>
      </dl>
      {progress.length > 0 && (
        <p class="progress">
          {progress.map(([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`).join(" · ")}
        </p>
      )}
      <Await res={res} reload={reload} rows={2}>
        {(reflections) => {
          const review = reflections.find((r) => r.stem === week.review);
          const days = week.reflections.map((s) => reflections.find((r) => r.stem === s)).filter((r): r is Reflection => !!r);
          return (
            <>
              {review && (
                <section aria-labelledby="review-h">
                  <h2 id="review-h">The week's review</h2>
                  <ReflectionItem r={review} open={focus === review.stem} />
                </section>
              )}
              <section aria-labelledby="days-h">
                <h2 id="days-h">Day reflections</h2>
                {days.length === 0 ? <Empty>No day reflections this week.</Empty> : days.map((r) => <ReflectionItem key={r.stem} r={r} open={focus === r.stem} />)}
              </section>
            </>
          );
        }}
      </Await>
    </article>
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
    const today = (await api.today(signal)).date;
    const from = addDays(today, -7 * WEEKS);
    return api.weeks({ from, to: today }, signal);
  }, []);
  useLive(["ledger-changed", "entries-changed", "library-changed", "tick"], reload);   // a prompt can pass its window with no file changing   // the week boundary is a library setting

  const focus = hashStem();
  useEffect(() => {
    if (res.state !== "ready" || !focus) return;
    const i = weekFor(res.data, focus);
    if (i >= 0) setIndex(i);   // the week card scrolls to the reflection once it has read it
  }, [res.state === "ready", focus]);

  return (
    <div class="page insights">
      <header class="page-head">
        <p class="eyebrow">Week by week</p>
        <h1>Insights</h1>
      </header>
      <Await res={res} reload={reload} rows={3}>
        {(weeks) => {
          const week = weeks[Math.min(index, weeks.length - 1)];
          if (!week) return <Empty>Nothing to show yet.</Empty>;
          const at = Math.min(index, weeks.length - 1);
          return (
            <>
              <div class="stepper" role="group" aria-label="Choose a week">
                <button type="button" class="button" disabled={at >= weeks.length - 1} onClick={() => setIndex(at + 1)}>
                  ‹ Earlier
                </button>
                <p aria-live="polite">
                  <strong>{weekRange(week.week_end)}</strong>
                  <span class="sub">{plural(week.entries, "entry", "entries")}</span>
                </p>
                <button type="button" class="button" disabled={at <= 0} onClick={() => setIndex(at - 1)}>
                  Later ›
                </button>
              </div>
              <WeekCard week={week} focus={focus} />
            </>
          );
        }}
      </Await>
    </div>
  );
}
