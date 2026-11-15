/**
 * The Library (round 02: Folio's rows, Night Room's clocks; "fine for now", a later pass will do more). Practices and
 * programs in their groups, each a row: a ribbon for a page he wrote (solid) or rewrote (outlined), a clock for when
 * it comes, its title with a dotted leader to its cadence, and its state in quiet words or a strip. No boxed badges,
 * no emoji tiles. A practice's page, the default beside his edit of a built-in, and the definition itself follow.
 */
import { useLocation } from "preact-iso";
import { useState } from "preact/hooks";
import { api, type ApiError } from "../api";
import { ConfirmButton } from "../components/ConfirmButton";
import { sessionsDone } from "../components/RunCard";
import { definitionFromForm, formFromDefinition } from "../editorModel";
import { useLive } from "../events";
import { cadence, dayLabel, plural } from "../format";
import { useAction, useResource } from "../hooks";
import { ChevronRight, Plus } from "../icons";
import { GROUP_ORDER, groupOf, isProgramBacked, sourceLabel } from "../labels";
import { useTitle } from "../title";
import type { LibraryItem, LibraryOne, Run, Slot } from "../types";
import { Await, Button, ButtonLink, Clock, Disclosure, Empty, ErrorLine, PageBack, ProgressStrip, Ribbon } from "../ui";
import "./library.css";

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

/** A program's runs that are in progress, by program id (for the rows' progress). */
function liveRuns(runs: Run[] | null): Map<string, Run> {
  const by = new Map<string, Run>();
  for (const r of runs ?? []) if ((r.status === "active" || r.status === "paused") && !by.has(r.program)) by.set(r.program, r);
  return by;
}

export function groupLibrary(items: LibraryItem[]): { group: string; items: LibraryItem[] }[] {
  const by = new Map<string, LibraryItem[]>();
  for (const it of items) {
    const g = groupOf(it);
    by.set(g, [...(by.get(g) ?? []), it]);
  }
  const names = [...by.keys()].sort((a, b) => {
    const ia = GROUP_ORDER.indexOf(a);
    const ib = GROUP_ORDER.indexOf(b);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || a.localeCompare(b);
  });
  return names.map((group) => ({ group, items: by.get(group) ?? [] }));
}

const count = (n: number, noun: string) => `${n} ${noun}${n === 1 ? "" : "s"}`;
const capital = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/** When a definition comes, as a clock: its first time (with the weekday for a weekly one), a run's progress, or a
 *  dashed rim for one he starts himself. */
function When({ schedule, run }: { schedule: Slot[]; run?: Run | undefined }) {
  if (run && run.sessions.length > 0) return <Clock progress={{ done: sessionsDone(run), total: run.sessions.length }} />;
  const first = schedule[0];
  if (!first) return <Clock />;
  return <Clock at={first.time} weekday={first.weekday === null ? undefined : WEEKDAYS[first.weekday]} />;
}

function Row({ it, run, i }: { it: LibraryItem; run?: Run | undefined; i: number }) {
  const program = isProgramBacked(it);
  const meta = program
    ? [it.kind === "situational" ? "On demand" : null, it.sessions ? count(it.sessions, "session") : null, it.phases_total ? count(it.phases_total, "phase") : null].filter(Boolean).join(" · ") || "Program"
    : cadence(it.schedule);
  const words = [
    it.source === "user" && <span key="s" class="source mine">Yours</span>,
    it.source === "fork" && <span key="s" class="source mine">Edited</span>,
    it.default_changed && <span key="c" class="changed">default changed</span>,
    !it.enabled && <span key="o" class="off-word">off</span>,
    it.active_run && !(run && run.sessions.length > 0) && <span key="r" class="running">running</span>,
    run && run.sessions.length > 0 && <span key="p" class="sessions">{sessionsDone(run)} of {run.sessions.length} sessions</span>,
  ].filter(Boolean);
  return (
    <li class="rise" style={{ "--i": i }}>
      <a class={`library-card prac${it.enabled ? "" : " off"}`} href={`/library/${it.id}`} data-vt="push">
        <span class="prac-ribbon">{it.source === "user" ? <Ribbon kind="own" /> : it.source === "fork" ? <Ribbon kind="edited" /> : null}</span>
        <When schedule={it.schedule} run={run} />
        <span class="prac-main">
          <span class="prac-title">
            <span class="title">{it.title}</span>
            <span class="lead" aria-hidden="true" />
            <span class="meta">{meta}</span>
          </span>
          {words.length > 0 && <span class="colophon">{words}</span>}
          {run && run.sessions.length > 0 && (
            <ProgressStrip label={`${it.title}, sessions`} done={sessionsDone(run)} total={run.sessions.length} />
          )}
        </span>
        <ChevronRight class="prac-go" aria-hidden="true" />
      </a>
    </li>
  );
}

export function LibraryRoute() {
  useTitle("Library");
  const [res, reload] = useResource(async (signal) => {
    const [items, runs] = await Promise.all([
      api.library(signal),
      api.runs(signal).catch((e: unknown) => {
        if (e instanceof DOMException) throw e;
        return null;   // the progress is a nicety: the rows still read without it
      }),
    ]);
    return { items, runs };
  }, []);
  useLive(["library-changed", "runs-changed"], reload);   // "running" follows the run files
  return (
    <div class="library-page">
      <header class="lib-head">
        <div class="lib-head-words">
          <h1 class="display">Library</h1>
          <p class="lede">The practices sent to you on a schedule, and the ones you start when you need them.</p>
        </div>
        <ButtonLink variant="primary" href="/new-practice" data-vt="push" icon={<Plus />}>New practice</ButtonLink>
      </header>
      <p class="lib-legend">
        <Ribbon kind="own" />
        A ribbon marks a page you wrote, or rewrote. The clock says when it comes.
      </p>
      <Await res={res} reload={reload} rows={4}>
        {({ items, runs }) => {
          if (items.length === 0) return <Empty>The library is empty.</Empty>;
          const live = liveRuns(runs);
          let i = 0;
          return (
            <>
              {groupLibrary(items).map((g) => (
                <section key={g.group} class="lib-group" aria-labelledby={`g-${g.group}`}>
                  <h2 id={`g-${g.group}`} class="book-head">
                    {g.group}
                    <span class="count" aria-hidden="true">{g.items.length}</span>
                  </h2>
                  <ul class="prac-list">
                    {g.items.map((it) => <Row key={it.id} it={it} run={live.get(it.id)} i={i++} />)}
                  </ul>
                </section>
              ))}
            </>
          );
        }}
      </Await>
    </div>
  );
}

/** Turn a practice on or off: its file saved with the flag flipped. A built-in is saved in place (the first save makes
 *  his file of it, so there is no mtime of his to send); his file is saved against the mtime it was read at. */
async function setEnabled(one: LibraryOne, enabled: boolean): Promise<void> {
  const d = one.definition;
  const form = formFromDefinition(d);
  await api.saveLibrary(d.id, { definition: definitionFromForm({ ...form, enabled }, d), mtime: d.source === "builtin" ? null : one.mtime });
}

/** His edit of a built-in: that it is his version in the built-in's place, whether the default has moved on since, and
 *  the way back. Reset asks once, in place: his version is gone after it. */
export function EditedNote({ one, onReset }: { one: LibraryOne; onReset: () => void }) {
  const d = one.definition;
  const o = one.override;
  const act = useAction();
  if (!o) return null;
  const program = isProgramBacked(d) || one.sessions !== undefined;
  return (
    <section class="edited-note" aria-labelledby="edited-h">
      <Ribbon kind="edited" />
      <div class="edited-words">
        <h2 id="edited-h">Edited</h2>
        <p>
          {o.in_force
            ? `Your version is used in place of the built-in: the same name${program ? ", the same /start word" : ", the same times"}.`
            : "Your edited file does not load, so the built-in is used meanwhile. Mend the file, or reset it."}
        </p>
        {o.default_changed === true && (
          <p class="default-changed">The default has changed since you edited it. Your version is kept until you reset it.</p>
        )}
        <div class="actions">
          <ButtonLink href={`/library/${d.id}/default`} data-vt="push">
            {o.default_changed ? "See what changed" : "View the default"}
          </ButtonLink>
          <ConfirmButton question="Replace your version with the default?" yes="Reset to default" disabled={act.busy} onConfirm={async () => {
            if (await act.run(async () => (await api.resetLibrary(d.id), true))) onReset();   // (a 204 answers nothing)
          }}>
            Reset to default
          </ConfirmButton>
        </div>
        {act.error && <ErrorLine error={act.error} />}
      </div>
    </section>
  );
}

/** What can be done with a practice: write it now (Today, open on it), send it to the chat, switch it, change it, put
 *  it back. Send to chat is not offered on a journal with no chat (the day's quiet read says so, as Today reads it). */
function PracticeActions({ one, reload }: { one: LibraryOne; reload: () => void }) {
  const d = one.definition;
  const act = useAction();
  const [day] = useResource((signal) => api.todayQuiet(signal).catch(() => null), []);
  const chat = day.state === "ready" ? day.data?.chat : undefined;
  const { route } = useLocation();
  const [onItsWay, setOnItsWay] = useState(false);
  useLive(["ledger-changed"], () => setOnItsWay(false));
  const go = async (fn: () => Promise<unknown>) => {
    const done = await act.run(async () => {
      await fn();
      return true;
    });
    if (done) reload();
    return done;
  };
  return (
    <div class="detail-actions">
      <div class="actions">
        <ButtonLink variant="primary" href={`/library/${d.id}/edit`} data-vt="push">Edit</ButtonLink>
        {/* (turned off is not scheduled, nothing more: it is still written and sent from here; the owner, 2026-11-09) */}
        <ButtonLink href={`/today?write=${encodeURIComponent(d.id)}`}>Write now</ButtonLink>
        {day.state !== "loading" && chat !== false && (   /* (asked first: it never shows, then goes, on a journal with no chat) */
          <Button disabled={act.busy || onItsWay} onClick={async () => {
            const done = await act.run(() => api.sendNow(d.id));
            if (done) setOnItsWay(true);
          }}>
            {onItsWay ? "On its way…" : "Send to chat"}
          </Button>
        )}
        <Button disabled={act.busy} onClick={() => void go(() => setEnabled(one, !d.enabled))}>
          {d.enabled ? "Turn off" : "Turn on"}
        </Button>
        {d.source === "user" && (
          <ConfirmButton question="Delete this practice?" yes="Delete" disabled={act.busy} onConfirm={async () => {
            if (await go(() => api.resetLibrary(d.id))) route("/library");
          }}>
            Delete
          </ConfirmButton>
        )}
      </div>
      {!d.enabled && <p class="hint">Turned off: not sent on its schedule. Write now and Send to chat still work.</p>}
      {act.error && <ErrorLine error={act.error} />}
    </div>
  );
}

const STARTED = (r: Run) => (r.started_at ? dayLabel(r.started_at.slice(0, 10)) : "");

/** A program: carry on with its run, start one, read what past runs compiled. A start is spawned by the server and
 *  appears a moment later; the page follows `runs-changed`. */
function ProgramActions({ one, runs, reload }: { one: LibraryOne; runs: Run[] | null; reload: () => void }) {
  const d = one.definition;
  const act = useAction();
  const [starting, setStarting] = useState(false);
  useLive(["runs-changed"], () => setStarting(false));
  const mine = (runs ?? []).filter((r) => r.program === d.id);
  const live = mine.find((r) => r.status === "active" || r.status === "paused");
  const past = mine.filter((r) => r !== live);
  const error: ApiError | null = act.error;
  // this program's own run, or (one program at a time) another program's unfinished one: the line links to it
  const carryOn =
    error && (error.slug === "active-run" || error.slug === "program-in-progress") && typeof error.extra.run === "string"
      ? error.extra.run
      : null;
  return (
    <div class="detail-actions">
      {live && live.sessions.length > 0 && (
        <ProgressStrip title="Where you are" label={`${d.title}, sessions`} done={sessionsDone(live)} total={live.sessions.length}
          meta={`${sessionsDone(live)} of ${live.sessions.length} sessions done${live.status === "paused" ? ", on a break" : ""}`} />
      )}
      <div class="actions">
        {live ? (
          <ButtonLink variant="primary" href={`/runs/${live.id}`} data-vt="push">Continue</ButtonLink>
        ) : (
          <Button variant="primary" disabled={act.busy || starting || !d.enabled || runs === null} onClick={async () => {
            const done = await act.run(() => api.startRun(d.id));
            if (done) {
              setStarting(true);
              reload();
            }
          }}>
            {starting ? "Starting…" : "Start"}
          </Button>
        )}
        <ButtonLink href={`/library/${d.id}/edit`} data-vt="push">Edit</ButtonLink>
      </div>
      {!d.enabled && <p class="hint">This program is turned off.</p>}
      {error &&
        (carryOn ? (
          <div class="error-line" role="alert">
            <p>
              {error.detail} <a href={`/runs/${carryOn}`}>Carry on with it</a>
            </p>
          </div>
        ) : (
          <ErrorLine error={error} />
        ))}
      {past.length > 0 && (
        <section class="lib-runs" aria-labelledby="runs-h">
          <h2 id="runs-h" class="book-head">Earlier runs</h2>
          <ul>
            {past.map((r) => (
              <li key={r.id}>
                <a href={`/runs/${r.id}`} data-vt="push">
                  <span class="run-when">{STARTED(r)}</span>
                  <span class="run-what">{r.status} · {plural(sessionsDone(r), "session")} done{r.insights_entry ? " · insights compiled" : ""}</span>
                  <ChevronRight aria-hidden="true" />
                </a>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

/** A definition's questions (a practice's sections, or a program's sessions and phases), the line for the companion,
 *  the problems in its file, and the file itself: the Library page's body, and the default's. */
export function DefinitionView({ one }: { one: LibraryOne }) {
  const { definition: d, raw, problems, sessions } = one;
  return (
    <div class="definition">
      {problems.length > 0 && (
        <section class="problems error-line" role="alert">
          <p><b>Problems in the file</b></p>
          <ul>
            {problems.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        </section>
      )}
      {sessions && sessions.length > 0 ? (
        sessions.map((s) => (
          <section key={s.number} class="session-set">
            <h2 class="book-head">
              Session {s.number} · {s.title}
            </h2>
            <ol>
              {s.phases.map((ph) => (
                <li key={ph.number} class="phase-line">
                  <span class="phase-title">
                    {ph.title} <span class="phase-type">({ph.type})</span>
                  </span>
                  {ph.questions.length > 0 && (
                    <ul>
                      {ph.questions.map((q) => (
                        <li key={q.id}>{q.text}</li>
                      ))}
                    </ul>
                  )}
                </li>
              ))}
            </ol>
          </section>
        ))
      ) : d.sections.length === 0 ? (
        <Empty>No questions: this definition asks nothing by itself.</Empty>
      ) : (
        d.sections.map((s, i) => (
          <section key={i} class="question-set">
            <h2 class="book-head">{s.title ?? (d.sections.length === 1 ? "The questions" : `Part ${i + 1}`)}</h2>
            <ol>
              {s.questions.map((q) => (
                <li key={q.id}>
                  {q.text}
                </li>
              ))}
            </ol>
          </section>
        ))
      )}
      {d.companion && (
        <p class="companion-line">
          <span class="label">For the companion</span> <span class="companion-words">{d.companion}</span>
        </p>
      )}
      <Disclosure class="raw" head="The file" peek="The Markdown, as it is saved">
        <pre>{raw}</pre>
      </Disclosure>
    </div>
  );
}

/** What a definition is, in one line: a practice's times, a program's or a situational definition's sessions. */
export function cadenceOf(one: LibraryOne): string {
  const { definition: d, sessions } = one;
  return d.kind === "program" ? "program" : sessions ? `on demand · ${count(sessions.length, "session")}` : cadence(d.schedule);
}

/** "Built-in · Daily 06:00 · off": where a definition comes from and when, in the line above its title. */
function kickerOf(one: LibraryOne): string {
  const d = one.definition;
  const from = d.source === "fork" ? "edited built-in" : sourceLabel(d.source);
  return capital(`${from} · ${cadenceOf(one)}${d.enabled ? "" : " · off"}`);
}


export function LibraryDetailRoute({ id = "" }: { id?: string }) {
  const [res, reload] = useResource(async (signal) => {
    const [one, runs] = await Promise.all([
      api.libraryOne(id, signal),
      api.runs(signal).catch((e: unknown) => {
        if (e instanceof DOMException) throw e;
        return null;   // a practice has no runs; for a program the Start button waits for them
      }),
    ]);
    return { one, runs };
  }, [id]);
  useLive(["library-changed", "runs-changed"], reload);
  useTitle(res.state === "ready" ? res.data.one.definition.title : "Library");
  return (
    <div class="library-page library-detail">
      <PageBack href="/library">Library</PageBack>
      {res.state === "error" && res.error.status === 404 ? (
        <Empty>No practice or program by that name.</Empty>
      ) : (
        <Await res={res} reload={reload} rows={4}>
          {({ one, runs }) => {
          const { definition: d, sessions } = one;
          const program = isProgramBacked(d) || !!sessions;
          return (
            <>
              <header class="lib-detail-head">
                <div class="lib-detail-clock"><When schedule={d.schedule} run={program ? (runs ?? []).find((r) => r.program === d.id && (r.status === "active" || r.status === "paused")) : undefined} /></div>
                <div>
                  <p class="kicker">
                    {d.emoji && <span class="lib-emoji" aria-hidden="true">{d.emoji}</span>}
                    {kickerOf(one)}
                  </p>
                  <h1 class="display">{d.title}</h1>
                  {d.opener.plain && <p class="lede">{d.opener.plain}</p>}
                </div>
              </header>
              {program ? (
                <ProgramActions one={one} runs={runs} reload={reload} />
              ) : (
                <PracticeActions one={one} reload={reload} />
              )}
              <EditedNote one={one} onReset={reload} />
              <DefinitionView one={one} />
            </>
          );
          }}
        </Await>
      )}
    </div>
  );
}

interface DiffRow {
  kind: "file" | "hunk" | "ins" | "del" | "same";
  mark: string;
  text: string;
}

/** A unified diff as rows to read: a file's name once (from its `+++` line; the `---` line is dropped), a gap where
 *  a hunk starts, and each line with its mark (`+` put in, `-` taken out) apart from its words, which wrap. A file's
 *  header is read only between hunks: inside one, the `@@` counts say how many lines are still to come, so a line
 *  `-- x` taken out (`--- x`) and `++ y` put in (`+++ y`) stay lines. */
export function diffRows(text: string): DiffRow[] {
  const rows: DiffRow[] = [];
  const lines = text.replace(/\n$/, "").split("\n");
  let old = 0;   // lines of the current hunk still to come, on each side
  let neu = 0;
  lines.forEach((line, i) => {
    const inHunk = old > 0 || neu > 0;
    const hunk = inHunk ? null : /^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@/.exec(line);
    if (!inHunk && line.startsWith("--- ") && lines[i + 1]?.startsWith("+++ ")) return;
    if (!inHunk && line.startsWith("+++ ") && lines[i - 1]?.startsWith("--- ")) {
      rows.push({ kind: "file", mark: "", text: line.slice(4).replace(/^[^/]*\//, "") });
    } else if (hunk) {
      old = hunk[1] === undefined ? 1 : Number(hunk[1]);
      neu = hunk[2] === undefined ? 1 : Number(hunk[2]);
      rows.push({ kind: "hunk", mark: "", text: "⋯" });
    } else if (line.startsWith("+")) {
      neu -= 1;
      rows.push({ kind: "ins", mark: "+", text: line.slice(1) });
    } else if (line.startsWith("-")) {
      old -= 1;
      rows.push({ kind: "del", mark: "-", text: line.slice(1) });
    } else {
      old -= 1;
      neu -= 1;
      rows.push({ kind: "same", mark: "", text: line.slice(1) });
    }
  });
  return rows;
}

export function Diff({ text, label }: { text: string; label: string }) {
  return (
    <div class="diff" role="group" aria-label={label}>
      {diffRows(text).map((r, i) => (
        <div key={i} class={`diff-line ${r.kind}`}>
          <span class="mark">{r.mark}</span>
          <span class="text">{r.text || "\u00a0"}</span>
        </div>
      ))}
    </div>
  );
}

/** `/library/:id/default`: the shipped definition, beside his edit of it: what changed in the default since he edited
 *  it, his version against the default, and the way back to it. */
export function LibraryDefaultRoute({ id = "" }: { id?: string }) {
  const [res, reload] = useResource((signal) => api.libraryDefault(id, signal), [id]);
  useLive(["library-changed"], reload);
  useTitle(res.state === "ready" ? `${res.data.definition.title}: the default` : "The default");
  const act = useAction();
  const { route } = useLocation();
  return (
    <div class="library-page library-detail library-default">
      <PageBack href={`/library/${id}`}>{res.state === "ready" ? res.data.definition.title : "Library"}</PageBack>
      {res.state === "error" && res.error.status === 404 ? (
        <Empty>Nothing ships under that name: only a built-in has a default.</Empty>
      ) : (
        <Await res={res} reload={reload} rows={4}>
          {(one) => {
            const { definition: d, diff } = one;
            return (
              <>
                <header class="lib-detail-head">
                  <div class="lib-detail-clock"><When schedule={d.schedule} /></div>
                  <div>
                    <p class="kicker">The default · {cadenceOf(one)}</p>
                    <h1 class="display">{d.title}</h1>
                    <p class="lede">As it ships with the journal{diff.yours !== null ? "; your edit is used in its place" : ""}.</p>
                  </div>
                </header>
                {diff.yours !== null && (
                  <div class="detail-actions">
                    <div class="actions">
                      <ConfirmButton question="Replace your version with the default?" yes="Reset to default" disabled={act.busy} onConfirm={async () => {
                        if (await act.run(async () => (await api.resetLibrary(d.id), true))) route(`/library/${d.id}`);
                      }}>
                        Reset to default
                      </ConfirmButton>
                    </div>
                    {act.error && <ErrorLine error={act.error} />}
                  </div>
                )}
                {diff.since && (
                  <section class="diff-section" aria-labelledby="since-h">
                    <h2 id="since-h" class="book-head">What changed in the default since you edited it</h2>
                    <Diff text={diff.since} label="The default when you edited it, against the default now" />
                  </section>
                )}
                {diff.yours !== null && (
                  <Disclosure class="diff-section" head="Your version against the default" defaultOpen={!diff.since}>
                    {diff.yours ? (
                      <Diff text={diff.yours} label="The default, against your version" />
                    ) : (
                      <p class="hint">Your version is the same as the default.</p>
                    )}
                  </Disclosure>
                )}
                <DefinitionView one={one} />
              </>
            );
          }}
        </Await>
      )}
    </div>
  );
}
