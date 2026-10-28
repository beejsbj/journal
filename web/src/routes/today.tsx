import { useState } from "preact/hooks";
import { api } from "../api";
import { Composer } from "../components/Composer";
import { HeldRunCard, RunCard, holdsQuestions } from "../components/RunCard";
import { OSM, OsmCredit } from "../components/OsmCredit";
import { PromptMessage } from "../components/Prompt";
import { Await, Empty, ErrorLine } from "../components/Status";
import { WeekStrip } from "../components/WeekStrip";
import { useLive } from "../events";
import { dayPart, longDay } from "../format";
import { useAction, useResource } from "../hooks";
import { kindLabel, kindOfStem } from "../labels";
import { useTitle } from "../title";
import type { Created, LibraryItem, PracticeRow, Run, Today } from "../types";

/**
 * The row's state in words, as the line under its title says it: quietly, in the page's own voice, and the time those
 * words name (typed, as every time on the page is). A prompt he did not answer is not a failure: it says when it
 * came, and Reply is still there under it.
 */
export function rowState(r: PracticeRow): [string, string | null] {
  switch (r.state) {
    case "upcoming":
      return r.at ? ["at", r.at] : ["later today", null];
    case "open":
      return r.sent ? ["waiting since", r.sent] : ["waiting for you", null];
    case "answered":
      return r.answered ? ["answered", r.answered] : ["answered", null];
    case "missed":
      return r.sent || r.at ? ["came at", r.sent ?? r.at ?? null] : ["not answered", null];
  }
}

/** When a row happens in the day (sent, else scheduled), so the cards read in the order the day went. */
export function rowTime(r: PracticeRow): string {
  return r.sent ?? r.at ?? NO_TIME;
}

const NO_TIME = "99:99";

/**
 * One prompt of the day. Once it is sent the card carries the whole message, her opener and every question, in her
 * voice, whatever its state: answered, still open or missed (only Telegram folds a finished prompt). Under it, in
 * his serif, the first line of his reply when there is one. Then what can be done: reply (a new entry, or a `/j`
 * note when the prompt is no longer open: the server says which with `rule`), send it now, open the entry.
 */
function PracticeCard({ row, today, library, slot }: { row: PracticeRow; today: Today; library: LibraryItem[]; slot: number }) {
  const p = library.find((x) => x.id === row.id);
  // by the row's own entry: a practice with two slots has two rows with one id, and an id prefix names the first slot's entry
  const entry = row.stem ? today.entries.find((e) => e.stem === row.stem) : undefined;
  const [replying, setReplying] = useState(false);
  const [saved, setSaved] = useState<Created | null>(null);
  const [onItsWay, setOnItsWay] = useState(false);
  const send = useAction();
  useLive(["ledger-changed"], () => setOnItsWay(false));
  const canReply = row.state === "open" || row.state === "missed";
  const canSend = (row.state === "upcoming" || row.state === "missed") && (p?.enabled ?? true);
  const title = p?.title ?? row.id;
  const his = today.voices?.his ?? "You";
  const [words, when] = rowState(row);
  return (
    <li class={`card practice ${row.state}`}>
      {/* the hour in the margin, as a book page hangs it; when the line under the title names the same time the margin is
          left to the eye, otherwise (no time there, or another one: answered later than sent) it is read out too */}
      {rowTime(row) !== NO_TIME && (
        <p class="practice-when" aria-hidden={when === rowTime(row) ? "true" : undefined}>
          <time>{rowTime(row)}</time>
          <span>{dayPart(rowTime(row))}</span>
        </p>
      )}
      <header class="practice-head">
        {p?.emoji && <span class="emoji" aria-hidden="true">{p.emoji}</span>}
        <div class="practice-title">
          <h3>{title}</h3>
          <p class={`pill ${row.state}`}>
            {words}
            {when && (
              <>
                {" "}
                <time>{when}</time>
              </>
            )}
          </p>
        </div>
      </header>
      {row.prompt && <PromptMessage prompt={row.prompt} hers={today.voices?.hers} />}
      {entry && (
        <a class="his-reply" href={`/entries/${entry.stem}`}>
          <span class="who">
            {his}
            {row.answered ? ` · ${row.answered}` : ""}
          </span>
          <span class="words">{entry.first_line}</span>
        </a>
      )}
      {(canReply || canSend || onItsWay || entry) && !replying && (
        <div class="actions">
          {canReply && !saved && (
            <button type="button" class="button primary" onClick={() => setReplying(true)}>
              Reply
            </button>
          )}
          {canSend && (
            <button
              type="button"
              class="button"
              disabled={send.busy || onItsWay}
              onClick={async () => {
                const done = await send.run(() => api.sendNow(row.id));
                if (done) setOnItsWay(true);
              }}
            >
              {onItsWay ? "On its way…" : "Send now"}
            </button>
          )}
          {entry && (
            <a class="button open-entry" href={`/entries/${entry.stem}`}>
              Open the entry
            </a>
          )}
        </div>
      )}
      {send.error && <ErrorLine error={send.error} />}
      {saved && (
        <p class="saved">
          {saved.rule === "/j" ? "Saved as a note. " : "Saved. "}
          <a href={`/entries/${saved.stem}`}>Open the entry</a>
        </p>
      )}
      {replying && (
        <div class="inline-composer">
          <Composer
            draftKey={`reply:${row.id}:${slot}`}
            label={`Reply to ${title}`}
            placeholder="Reply in your own words"
            submitLabel="Reply"
            onSubmit={async (words) => setSaved(await api.createEntry({ practice: row.id, ...words }))}
            onDone={() => setReplying(false)}
          />
          <button type="button" class="link" onClick={() => setReplying(false)}>
            Not now
          </button>
        </div>
      )}
    </li>
  );
}

/** Session-10's one-off questions that are still to come today, as a quiet list with their times. */
function dueToday(runs: Run[], day: string): { key: string; time: string; text: string; title: string }[] {
  const out: { key: string; time: string; text: string; title: string }[] = [];
  for (const r of runs) {
    r.scheduled.forEach((s, i) => {
      if ((s.status ?? "pending") !== "pending" || !s.local?.startsWith(day)) return;
      const which = s.n && s.of ? `question ${s.n} of ${s.of}` : "a question";
      out.push({ key: `${r.id}:${i}`, time: s.local.slice(11, 16), text: which, title: r.title });
    });
  }
  return out.sort((a, b) => a.time.localeCompare(b.time));
}

export function TodayRoute() {
  useTitle("Today");
  const [res, reload] = useResource(async (signal) => {
    const [today, library, runs] = await Promise.all([
      api.today(signal),
      api.library(signal).catch((e: unknown) => {
        if (e instanceof DOMException) throw e;
        return [] as LibraryItem[]; // titles are a nicety: Today still reads without them
      }),
      api.runs(signal).catch((e: unknown) => {
        if (e instanceof DOMException) throw e;
        return null; // the program cards are built on it; without it the one-line summary of /today stands
      }),
    ]);
    return { today, library, runs };
  }, []);
  useLive(["ledger-changed", "entries-changed", "runs-changed", "library-changed", "tick"], reload);

  return (
    <Await res={res} reload={reload} rows={4}>
      {({ today, library, runs }) => {
        // in the order the day went: sent (or due) earliest first; the server lists the schedule, then the on-demand sends
        const rows = today.practices.rows
          .map((r, slot) => ({ r, slot }))
          .sort((a, b) => rowTime(a.r).localeCompare(rowTime(b.r)) || a.slot - b.slot);
        const live = (runs ?? []).filter((r) => r.status === "active" || r.status === "paused");
        const held = (runs ?? []).filter((r) => r.status === "completed" && holdsQuestions(r));
        const interrupts = dueToday((runs ?? []).filter(holdsQuestions), today.date);
        const nothing = rows.length === 0 && !today.program && live.length === 0 && held.length === 0 && today.entries.length === 0;
        return (
          <div class="page today">
            <header class="page-head today-head">
              <p class="eyebrow">Today</p>
              <h1>
                <span class="dow">{longDay(today.date).split(" ")[0]}</span>{" "}
                <span class="dmy">{longDay(today.date).split(" ").slice(1).join(" ")}</span>
              </h1>
              <p class="sub">{[`${today.time} ${today.timezone}`, today.location].filter(Boolean).join(" · ")}</p>
              {today.weather && <p class="weather">{today.weather}</p>}
              {today.location && today.location_source === OSM && <OsmCredit />}
            </header>
            <WeekStrip week={today.week} today={today.date} />
            {nothing && <Empty>Nothing scheduled today.</Empty>}
            {rows.length > 0 && (
              <section class="practices" aria-labelledby="practices-h">
                <h2 id="practices-h">Practices</h2>
                <ul class="cards">
                  {rows.map(({ r, slot }) => (
                    <PracticeCard key={`${r.id}#${slot}`} row={r} today={today} library={library} slot={slot} />
                  ))}
                </ul>
              </section>
            )}
            {live.length + held.length > 0 && (
              <section aria-labelledby="program-h">
                <h2 id="program-h">Programs</h2>
                <ul class="cards">
                  {live.map((r) => (
                    <RunCard key={r.id} run={r} />
                  ))}
                  {held.map((r) => (
                    <HeldRunCard key={r.id} run={r} />
                  ))}
                </ul>
              </section>
            )}
            {interrupts.length > 0 && (
              <section aria-labelledby="interrupts-h">
                <h2 id="interrupts-h">Questions coming later today</h2>
                <ul class="plain-list interrupts">
                  {interrupts.map((i) => (
                    <li key={i.key}>
                      <time>{i.time}</time> <span>{i.title} · {i.text}</span>
                    </li>
                  ))}
                </ul>
              </section>
            )}
            {runs === null && today.program && (
              <section aria-labelledby="program-h">
                <h2 id="program-h">Program</h2>
                <p class="card program">{today.program.text}</p>
              </section>
            )}
            {runs !== null && live.length + held.length === 0 && (
              <section aria-labelledby="program-h">
                <h2 id="program-h">Programs</h2>
                <Empty>
                  No program running. <a href="/library">Start one from the Library.</a>
                </Empty>
              </section>
            )}
            {today.entries.length > 0 && (
              <section aria-labelledby="written-h">
                <h2 id="written-h">Written today</h2>
                <ul class="plain-list written">
                  {today.entries.map((e) => (
                    <li key={e.stem}>
                      <a class="written-row" href={`/entries/${e.stem}`}>
                        <span class="kind">{kindLabel(kindOfStem(e.stem), library)}</span>
                        <span class="first-line">{e.first_line}</span>
                      </a>
                    </li>
                  ))}
                </ul>
              </section>
            )}
            {today.reflection && (
              <section aria-labelledby="carry-h" class="carry">
                <h2 id="carry-h">To carry forward</h2>
                <blockquote>{today.reflection.asked}</blockquote>
                <a class="quiet-link" href={`/insights#${today.reflection.stem ?? `${today.reflection.date}-reflection`}`}>Read the reflection</a>
              </section>
            )}
            {today.tasks && (
              <section aria-labelledby="tasks-h">
                <h2 id="tasks-h">Due today</h2>
                <ul class="plain-list tasks">
                  {today.tasks.names.map((n) => (
                    <li key={n}>{n}</li>
                  ))}
                  {today.tasks.count > today.tasks.names.length && (
                    <li class="more">and {today.tasks.count - today.tasks.names.length} more</li>
                  )}
                </ul>
              </section>
            )}
          </div>
        );
      }}
    </Await>
  );
}
