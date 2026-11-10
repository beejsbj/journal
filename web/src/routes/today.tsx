import type { ComponentChildren } from "preact";
import { useLocation } from "preact-iso";
import { useEffect, useRef, useState } from "preact/hooks";
import { api } from "../api";
import { HeldRunCard, RunCard, holdsQuestions } from "../components/RunCard";
import { OSM, OsmCredit } from "../components/OsmCredit";
import { WeekStrip } from "../components/WeekStrip";
import { useLive } from "../events";
import { longDay, parseDay } from "../format";
import { useAction, useResource } from "../hooks";
import { Chevron, Mic, Pen } from "../icons";
import { kindLabel, kindOfStem } from "../labels";
import { writeTarget } from "../components/WriteRoom";
import { openWrite } from "../shell";
import { useTitle } from "../title";
import type { LibraryItem, PracticeRow, Prompt, Run, Today } from "../types";
import { Await, Button, ButtonLink, Empty, ErrorLine, Letter, TextButton } from "../ui";
import "./today.css";

/**
 * The row's state in words, as the line under its title says it: quietly, in the page's own voice, and the time those
 * words name. A prompt he did not answer is not a failure: it says when it came, and Write now is still there under it.
 */
export function rowState(r: PracticeRow): [string, string | null] {
  switch (r.state) {
    case "upcoming":
      return r.at ? ["at", r.at] : ["later today", null];
    case "due":
      return r.at ? ["due since", r.at] : ["due now", null];
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

/** The state, as quiet words with their time (no pill: DESIGN.md, "Don't"). */
function StateWords({ row }: { row: PracticeRow }) {
  const [words, when] = rowState(row);
  return (
    <p class={`pill ${row.state}`}>
      {words}
      {when && (
        <>
          {" "}
          <time>{when}</time>
        </>
      )}
    </p>
  );
}

/**
 * Her letter on Today, as he was sent it: the opener, the question she asked in her italic, the practice's questions
 * folded at the foot. When nothing was recorded but the questions (the plain opener), the questions are the letter,
 * so they stand open.
 */
function TodayLetter({ prompt, hers, actions }: { prompt: Prompt; hers: string; actions?: ComponentChildren }) {
  const asked = prompt.asked.map((q) => q.question);
  const bare = !prompt.opener && asked.length === 0;
  // one question and nothing marked asked: the letter folds nothing away (Letter folds two or more), so it is shown
  const lone = !bare && asked.length === 0 && prompt.questions.length === 1 ? prompt.questions[0] : undefined;
  return (
    <Letter from={hers} at={prompt.sent_at_local ?? undefined} opener={prompt.opener ?? undefined} asked={asked}
      questions={bare ? [] : prompt.questions} askedIds={prompt.asked.map((q) => q.id)} actions={actions}>
      {bare && prompt.questions.length > 0 && (
        <ol class="today-questions">
          {prompt.questions.map((q) => (
            <li key={q.id}>{q.text}</li>
          ))}
        </ol>
      )}
      {lone && <p class="letter-asked">{lone.text}</p>}
    </Letter>
  );
}

interface CardProps {
  row: PracticeRow;
  today: Today;
  library: LibraryItem[];
  slot: number;
}

export { writeTarget };

/** Send to chat: the server spawns the send; the line says it is on its way until the ledger reports it. */
function useSendNow(row: PracticeRow) {
  const [onItsWay, setOnItsWay] = useState(false);
  const send = useAction();
  useLive(["ledger-changed"], () => setOnItsWay(false));
  const run = async () => {
    const done = await send.run(() => api.sendNow(row.id));
    if (done) setOnItsWay(true);
  };
  return { onItsWay, busy: send.busy, error: send.error, run };
}

/**
 * A practice of the day that has come: her letter once it was sent, whatever its state (only Telegram folds a finished
 * prompt), with what can be done on its crease. **Write now** on every one, whatever its state (the write room opens
 * on the practice as a view: the slot his words fill, said in words; answered, the words start the next slot that
 * fits or an extra entry), and Speak it beside it while it waits; **Send to chat** while it is due or missed, unless
 * the journal has no chat (a practice turned off is only off the schedule: OPEN-FACES, the owner's decision 4). Under
 * it, in his serif, the first line of his reply when there is one.
 */
function SentCard({ row, today, library, slot }: CardProps) {
  const p = library.find((x) => x.id === row.id);
  // by the row's own entry: a practice with two slots has two rows with one id, and an id prefix names the first slot's entry
  const entry = row.stem ? today.entries.find((e) => e.stem === row.stem) : undefined;
  const send = useSendNow(row);
  const canSend = (row.state === "due" || row.state === "missed") && today.chat !== false;
  const waits = row.state === "open" || row.state === "due";
  const title = p?.title ?? row.id;
  const his = today.voices?.his ?? "You";
  const hers = today.voices?.hers ?? "the companion";
  const write = (e: Event, voice = false) =>
    openWrite({ for: { kind: "answer", practice: row.id, slot }, from: e.currentTarget as HTMLElement, voice });
  const open = entry && (
    <ButtonLink href={`/entries/${entry.stem}`} variant="quiet" size="small" class="open-entry">
      Open the entry
    </ButtonLink>
  );
  const sendButton = canSend && (
    <Button variant="quiet" size={waits ? "medium" : "small"} disabled={send.busy || send.onItsWay} onClick={() => void send.run()}>
      {send.onItsWay ? "On its way…" : "Send to chat"}
    </Button>
  );
  const actions = waits ? (
    <>
      <Button variant="primary" icon={<Pen />} onClick={(e) => write(e)}>
        Write now
      </Button>
      <Button variant="quiet" icon={<Mic />} onClick={(e) => write(e, true)}>
        Speak it
      </Button>
      {sendButton}
      {open}
    </>
  ) : (
    <>
      <Button size="small" icon={<Pen />} onClick={(e) => write(e)}>
        Write now
      </Button>
      {sendButton}
      {open}
    </>
  );
  return (
    <li class={`today-card practice ${row.state}`}>
      <header class="practice-head">
        <h3>{title}</h3>
        <StateWords row={row} />
      </header>
      {row.prompt ? (
        <TodayLetter prompt={row.prompt} hers={hers} actions={actions} />
      ) : (
        <div class="today-actions">{actions}</div>
      )}
      {entry && (
        <a class="his-reply" href={`/entries/${entry.stem}`}>
          <span class="who">
            {his}
            {row.answered ? ` · ${row.answered}` : ""}
          </span>
          <span class="words">{entry.first_line}</span>
        </a>
      )}
      {send.error && <ErrorLine error={send.error} />}
    </li>
  );
}

/** A prompt still to come today: its name, a dotted leader to its hour, Write now (it fills that slot early, and it is
 *  then not sent) and Send to chat (not on a journal with no chat). */
function LaterRow({ row, today, library, slot }: CardProps) {
  const p = library.find((x) => x.id === row.id);
  const send = useSendNow(row);
  const canSend = today.chat !== false;
  return (
    <li class="today-card practice upcoming">
      <div class="later-line">
        <h3>{p?.title ?? row.id}</h3>
        <span class="lead" aria-hidden="true" />
        <StateWords row={row} />
      </div>
      <div class="later-actions">
        <TextButton onClick={(e) => openWrite({ for: { kind: "answer", practice: row.id, slot }, from: e.currentTarget as HTMLElement })}>
          Write now
        </TextButton>
        {canSend && (
          <TextButton disabled={send.busy || send.onItsWay} onClick={() => void send.run()}>
            {send.onItsWay ? "On its way…" : "Send to chat"}
          </TextButton>
        )}
      </div>
      {send.error && <ErrorLine error={send.error} />}
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

/** "since 20:05": when the first prompt waiting came (sent), or fell due (its hour). */
const since = (r: PracticeRow | undefined): string | undefined => {
  const t = r ? (r.sent ?? r.at) : undefined;
  return t ? `since ${t}` : undefined;
};

/** A section of the page: a book head with a rule running out, rising in after the one before it. */
function Sec({ id, title, count, i, class: cls, children }: {
  id: string;
  title: string;
  count?: string | undefined;
  i: number;
  class?: string;
  children: ComponentChildren;
}) {
  return (
    <section class={["today-sec rise", cls].filter(Boolean).join(" ")} style={{ "--i": i }} aria-labelledby={id}>
      <h2 class="book-head" id={id}>
        {title}
        {count && <span class="count">{count}</span>}
      </h2>
      {children}
    </section>
  );
}

export function TodayRoute() {
  useTitle("Today");
  // ?write=<id> (the link a notification will carry; the Library's Write now) is used once: on the first read it opens
  // the write room on the row the words will fill (writeTarget: the engine's slot order), or on the practice alone when
  // it has no row today, and it leaves the address, so neither a later visit nor the reload after his save opens it
  // again (OPEN-FACES)
  const query = useLocation().query;
  const [write] = useState<string | undefined>(() => query.write || undefined);
  const used = useRef(false);
  useEffect(() => {
    if (write) window.history.replaceState(window.history.state, "", window.location.pathname);
  }, []);
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
  useEffect(() => {
    if (!write || used.current || res.state !== "ready") return;
    used.current = true;
    const at = writeTarget(res.data.today.practices.rows, write);
    openWrite({ for: at >= 0 ? { kind: "answer", practice: write, slot: at } : { kind: "answer", practice: write } });
  }, [res.state]);

  return (
    <div class="today-page">
      <Await res={res} reload={reload} rows={4}>
        {({ today, library, runs }) => {
          // in the order the day went: sent (or due) earliest first; the server lists the schedule, then the on-demand sends
          const rows = today.practices.rows
            .map((r, slot) => ({ r, slot }))
            .sort((a, b) => rowTime(a.r).localeCompare(rowTime(b.r)) || a.slot - b.slot);
          const waiting = rows.filter(({ r }) => r.state === "open" || r.state === "due");
          const earlier = rows.filter(({ r }) => r.state === "answered" || r.state === "missed");
          const later = rows.filter(({ r }) => r.state === "upcoming");
          const live = (runs ?? []).filter((r) => r.status === "active" || r.status === "paused");
          const held = (runs ?? []).filter((r) => r.status === "completed" && holdsQuestions(r));
          const interrupts = dueToday((runs ?? []).filter(holdsQuestions), today.date);
          const nothing = rows.length === 0 && !today.program && live.length === 0 && held.length === 0 && today.entries.length === 0;
          const [dow = "", d = "", month = "", year = ""] = longDay(today.date).split(" ");
          const card = ({ r, slot }: { r: PracticeRow; slot: number }) => (
            <SentCard key={`${r.id}#${slot}`} row={r} today={today} library={library} slot={slot} />
          );
          let i = 0;
          return (
            <div class="today-grid">
              <div class="today-main">
                <header class="today-head">
                  <span class="numeral today-num" aria-hidden="true">{parseDay(today.date)?.d ?? d}</span>
                  <div class="today-dl">
                    <h1>
                      <span class="today-dow">{dow}</span>
                      <span class="sr-only"> {d}</span> <span class="today-month">{month}</span>
                      <span class="sr-only"> {year}</span>
                    </h1>
                    <p class="sub">{[`${today.time} ${today.timezone}`, today.location].filter(Boolean).join(" · ")}</p>
                    {today.weather && <p class="weather">{today.weather}</p>}
                    {today.location && today.location_source === OSM && <OsmCredit />}
                  </div>
                </header>
                <WeekStrip week={today.week} today={today.date} />
                {nothing && <Empty>Nothing scheduled today.</Empty>}
                {waiting.length > 0 && (
                  <Sec id="waiting-h" title="Waiting for you" i={i++} class="waiting"
                    count={since(waiting[0]?.r)}>
                    <ul class="today-cards">{waiting.map(card)}</ul>
                  </Sec>
                )}
                {earlier.length > 0 && (
                  <Sec id="earlier-h" title="Earlier today" i={i++}>
                    <ul class="today-cards">{earlier.map(card)}</ul>
                  </Sec>
                )}
                {today.entries.length > 0 && (
                  <Sec id="written-h" title="Written today" i={i++} class="written">
                    <ul class="written-list">
                      {today.entries.map((e) => (
                        <li key={e.stem}>
                          <a class="written-row" href={`/entries/${e.stem}`}>
                            <span class="kind">{kindLabel(kindOfStem(e.stem), library)}</span>
                            <span class="first-line">{e.first_line}</span>
                          </a>
                        </li>
                      ))}
                    </ul>
                  </Sec>
                )}
              </div>
              <aside class="today-side" aria-label="The rest of the day">
                {later.length > 0 && (
                  <Sec id="later-h" title="Later today" i={i++}>
                    <ul class="today-later">
                      {later.map(({ r, slot }) => (
                        <LaterRow key={`${r.id}#${slot}`} row={r} today={today} library={library} slot={slot} />
                      ))}
                    </ul>
                  </Sec>
                )}
                {live.length + held.length > 0 && (
                  <Sec id="program-h" title="Programs" i={i++}>
                    <ul class="today-runs">
                      {live.map((r) => (
                        <RunCard key={r.id} run={r} />
                      ))}
                      {held.map((r) => (
                        <HeldRunCard key={r.id} run={r} />
                      ))}
                    </ul>
                  </Sec>
                )}
                {runs === null && today.program && (
                  <Sec id="program-h" title="Program" i={i++}>
                    <p class="program-line">{today.program.text}</p>
                  </Sec>
                )}
                {runs !== null && live.length + held.length === 0 && (
                  <Sec id="program-h" title="Programs" i={i++}>
                    <Empty>
                      No program running. <a href="/library">Start one from the Library.</a>
                    </Empty>
                  </Sec>
                )}
                {interrupts.length > 0 && (
                  <Sec id="interrupts-h" title="Questions coming later" i={i++}>
                    <ul class="interrupts">
                      {interrupts.map((q) => (
                        <li key={q.key}>
                          <span>{q.title} · {q.text}</span>
                          <span class="lead" aria-hidden="true" />
                          <time>{q.time}</time>
                        </li>
                      ))}
                    </ul>
                  </Sec>
                )}
                {today.reflection && (
                  <section class="today-sec carry rise" style={{ "--i": i++ }} aria-labelledby="carry-h">
                    <h2 class="kicker" id="carry-h">Carry forward</h2>
                    <blockquote class="her">{today.reflection.asked}</blockquote>
                    <a class="carry-link" href={`/insights#${today.reflection.stem ?? `${today.reflection.date}-reflection`}`}>
                      From {longDay(today.reflection.date).split(" ")[0]}'s reflection
                      <Chevron />
                    </a>
                  </section>
                )}
                {today.tasks && (
                  <Sec id="tasks-h" title="Due today" i={i++}>
                    <ul class="tasks">
                      {today.tasks.names.map((n) => (
                        <li key={n}>{n}</li>
                      ))}
                      {today.tasks.count > today.tasks.names.length && (
                        <li class="more">and {today.tasks.count - today.tasks.names.length} more</li>
                      )}
                    </ul>
                  </Sec>
                )}
              </aside>
            </div>
          );
        }}
      </Await>
    </div>
  );
}
