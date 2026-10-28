import { useLocation } from "preact-iso";
import { useState } from "preact/hooks";
import { api, type ApiError } from "../api";
import { ConfirmButton } from "../components/ConfirmButton";
import { Progress, sessionsDone } from "../components/RunCard";
import { Await, Empty, ErrorLine } from "../components/Status";
import { definitionFromForm, formFromDefinition } from "../editorModel";
import { useLive } from "../events";
import { cadence, dayLabel, plural } from "../format";
import { useAction, useResource } from "../hooks";
import { GROUP_ORDER, groupOf, isProgramBacked, sourceLabel } from "../labels";
import { useTitle } from "../title";
import type { LibraryItem, LibraryOne, Run } from "../types";

/** A program's runs that are in progress, by program id (for the cards' progress bars). */
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

function Card({ it, run }: { it: LibraryItem; run?: Run | undefined }) {
  const program = isProgramBacked(it);
  return (
    <li>
      <a class={`card library-card${it.enabled ? "" : " off"}`} href={`/library/${it.id}`}>
        {it.emoji && <span class="emoji" aria-hidden="true">{it.emoji}</span>}
        <span class="main">
          <span class="title">{it.title}</span>
          <span class="meta">
            {program
              ? [it.kind === "situational" ? "On demand" : null, it.sessions ? count(it.sessions, "session") : null, it.phases_total ? count(it.phases_total, "phase") : null].filter(Boolean).join(" · ") || "Program"
              : cadence(it.schedule)}
          </span>
          {run && run.sessions.length > 0 && (
            <>
              <Progress done={sessionsDone(run)} total={run.sessions.length} />
              <span class="meta">{sessionsDone(run)} of {run.sessions.length} sessions</span>
            </>
          )}
        </span>
        <span class="badges">
          <span class={`badge source ${it.source}`}>{sourceLabel(it.source)}</span>
          {it.default_changed && <span class="badge changed">default changed</span>}
          {!it.enabled && <span class="badge off">off</span>}
          {it.active_run && <span class="badge active">running</span>}
        </span>
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
        return null;   // the progress bars are a nicety: the cards still read without them
      }),
    ]);
    return { items, runs };
  }, []);
  useLive(["library-changed", "runs-changed"], reload);   // the "running" badge follows the run files
  return (
    <div class="page library">
      <header class="page-head">
        <p class="eyebrow">Practices and programs</p>
        <h1>Library</h1>
        <p class="head-actions">
          <a class="button primary" href="/new-practice">
            New practice
          </a>
        </p>
      </header>
      <Await res={res} reload={reload} rows={4}>
        {({ items, runs }) =>
          items.length === 0 ? (
            <Empty>The library is empty.</Empty>
          ) : (
            <>
              {groupLibrary(items).map((g) => (
                <section key={g.group} aria-labelledby={`g-${g.group}`}>
                  <h2 id={`g-${g.group}`}>{g.group}</h2>
                  <ul class="cards">
                    {g.items.map((it) => (
                      <Card key={it.id} it={it} run={liveRuns(runs).get(it.id)} />
                    ))}
                  </ul>
                </section>
              ))}
            </>
          )
        }
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
        <a class="button" href={`/library/${d.id}/default`}>
          {o.default_changed ? "See what changed" : "View the default"}
        </a>
        <ConfirmButton question="Replace your version with the default?" yes="Reset to default" disabled={act.busy} onConfirm={async () => {
          if (await act.run(async () => (await api.resetLibrary(d.id), true))) onReset();   // (a 204 answers nothing)
        }}>
          Reset to default
        </ConfirmButton>
      </div>
      {act.error && <ErrorLine error={act.error} />}
    </section>
  );
}

/** What can be done with a practice: send it now, switch it, change it, put it back. */
function PracticeActions({ one, reload }: { one: LibraryOne; reload: () => void }) {
  const d = one.definition;
  const act = useAction();
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
    <div class="actions detail-actions">
      {d.enabled && (
        <button
          type="button"
          class="button"
          disabled={act.busy || onItsWay}
          onClick={async () => {
            const done = await act.run(() => api.sendNow(d.id));
            if (done) setOnItsWay(true);
          }}
        >
          {onItsWay ? "On its way…" : "Send now"}
        </button>
      )}
      <a class="button primary" href={`/library/${d.id}/edit`}>
        Edit
      </a>
      <button type="button" class="button" disabled={act.busy} onClick={() => void go(() => setEnabled(one, !d.enabled))}>
        {d.enabled ? "Turn off" : "Turn on"}
      </button>
      {d.source === "user" && (
        <ConfirmButton question="Delete this practice?" yes="Delete" disabled={act.busy} onConfirm={async () => {
          if (await go(() => api.resetLibrary(d.id))) route("/library");
        }}>
          Delete
        </ConfirmButton>
      )}
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
      <div class="actions">
        {live ? (
          <a class="button primary" href={`/runs/${live.id}`}>
            Continue
          </a>
        ) : (
          <button
            type="button"
            class="button primary"
            disabled={act.busy || starting || !d.enabled || runs === null}
            onClick={async () => {
              const done = await act.run(() => api.startRun(d.id));
              if (done) {
                setStarting(true);
                reload();
              }
            }}
          >
            {starting ? "Starting…" : "Start"}
          </button>
        )}
        <a class="button" href={`/library/${d.id}/edit`}>
          Edit
        </a>
      </div>
      {!d.enabled && <p class="hint">This program is turned off.</p>}
      {error &&
        (carryOn ? (
          <p class="error-line" role="alert">
            {error.detail} <a href={`/runs/${carryOn}`}>Carry on with it</a>
          </p>
        ) : (
          <ErrorLine error={error} />
        ))}
      {past.length > 0 && (
        <section aria-labelledby="runs-h">
          <h2 id="runs-h">Earlier runs</h2>
          <ul class="plain-list runs">
            {past.map((r) => (
              <li key={r.id}>
                <a href={`/runs/${r.id}`}>
                  <span class="kind">{STARTED(r)} · {r.status}</span>
                  <span>{plural(sessionsDone(r), "session")} done{r.insights_entry ? " · insights compiled" : ""}</span>
                </a>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

/** A definition's questions (a practice's sections, or a program's sessions and phases), her line, the problems in
 *  its file, and the file itself: the Library page's body, and the default's. */
export function DefinitionView({ one }: { one: LibraryOne }) {
  const { definition: d, raw, problems, sessions } = one;
  return (
    <>
      {problems.length > 0 && (
        <section class="problems" role="alert">
          <h2>Problems in the file</h2>
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
            <h2>
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
            {s.title && <h2>{s.title}</h2>}
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
          <span class="label">For the companion</span> {d.companion}
        </p>
      )}
      <details class="raw">
        <summary>The file</summary>
        <pre>{raw}</pre>
      </details>
    </>
  );
}

/** What a definition is, in one line: a practice's times, a program's or a situational definition's sessions. */
export function cadenceOf(one: LibraryOne): string {
  const { definition: d, sessions } = one;
  return d.kind === "program" ? "program" : sessions ? `on demand · ${count(sessions.length, "session")}` : cadence(d.schedule);
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
    <div class="page library-detail">
      <p class="crumb">
        <a href="/library">‹ Library</a>
      </p>
      {res.state === "error" && res.error.status === 404 ? (
        <Empty>No practice or program by that name.</Empty>
      ) : (
        <Await res={res} reload={reload} rows={4}>
          {({ one, runs }) => {
          const { definition: d, sessions } = one;
          return (
            <>
              <header class="page-head">
                <p class="eyebrow">
                  {sourceLabel(d.source)} · {cadenceOf(one)}
                  {!d.enabled && " · off"}
                </p>
                <h1>
                  <span aria-hidden="true">{d.emoji} </span>
                  {d.title}
                </h1>
                {d.opener.plain && <p class="sub">{d.opener.plain}</p>}
              </header>
              {isProgramBacked(d) || sessions ? (
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
    <div class="page library-detail library-default">
      <p class="crumb">
        <a href={`/library/${id}`}>‹ {res.state === "ready" ? res.data.definition.title : "Library"}</a>
      </p>
      {res.state === "error" && res.error.status === 404 ? (
        <Empty>Nothing ships under that name: only a built-in has a default.</Empty>
      ) : (
        <Await res={res} reload={reload} rows={4}>
          {(one) => {
            const { definition: d, diff } = one;
            return (
              <>
                <header class="page-head">
                  <p class="eyebrow">The default · {cadenceOf(one)}</p>
                  <h1>
                    <span aria-hidden="true">{d.emoji} </span>
                    {d.title}
                  </h1>
                  <p class="sub">As it ships with the journal{diff.yours !== null ? "; your edit is used in its place" : ""}.</p>
                </header>
                {diff.yours !== null && (
                  <div class="actions detail-actions">
                    <ConfirmButton question="Replace your version with the default?" yes="Reset to default" disabled={act.busy} onConfirm={async () => {
                      if (await act.run(async () => (await api.resetLibrary(d.id), true))) route(`/library/${d.id}`);
                    }}>
                      Reset to default
                    </ConfirmButton>
                    {act.error && <ErrorLine error={act.error} />}
                  </div>
                )}
                {diff.since && (
                  <section class="diff-section" aria-labelledby="since-h">
                    <h2 id="since-h">What changed in the default since you edited it</h2>
                    <Diff text={diff.since} label="The default when you edited it, against the default now" />
                  </section>
                )}
                {diff.yours !== null && (
                  <details class="diff-section" open={!diff.since}>
                    <summary>Your version against the default</summary>
                    {diff.yours ? (
                      <Diff text={diff.yours} label="The default, against your version" />
                    ) : (
                      <p class="hint">Your version is the same as the default.</p>
                    )}
                  </details>
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
