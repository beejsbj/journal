import { useState } from "preact/hooks";
import { api } from "../api";
import { Composer } from "../components/Composer";
import { ConfirmButton } from "../components/ConfirmButton";
import { Markdown } from "../components/Markdown";
import { RunStrip, sessionsDone } from "../components/RunCard";
import { useLive } from "../events";
import { useAction, useResource } from "../hooks";
import { useTitle } from "../title";
import type { BlockWritten, RunAction, RunOne } from "../types";
import { Await, Button, Disclosure, Empty, ErrorLine, Letter, PageBack } from "../ui";
import "./run.css";

const LABEL: Record<RunAction, string> = {
  next: "Next",
  skip: "Skip",
  back: "Back",
  pause: "Pause",
  resume: "Resume",
  abandon: "Abandon",
};
const WAITING: Record<RunAction, string> = {
  next: "Moving on…",
  skip: "Skipping…",
  back: "Going back…",
  pause: "Pausing…",
  resume: "Resuming…",
  abandon: "Abandoning…",
};

const STATUS: Record<string, string> = {
  active: "In progress",
  paused: "On a break",
  completed: "Complete",
  abandoned: "Abandoned",
  expired: "Expired",
};

/** What happens to the run after an answer is saved, in words (the server's `advance`). */
function afterAnswer(w: BlockWritten): string {
  if (w.advance === "next") return "Saved. Press Next when you are done with this phase.";
  if (w.advance === "tick") return "Saved. The next phase comes with the next tick.";
  return "Saved. If you stay quiet a moment the next phase follows; or press Next now.";
}

/** Her name, from the day's quiet read (the run itself does not carry it); her letters are never unlabelled. */
function useHers(): string {
  const [res] = useResource((signal) => api.todayQuiet(signal).then((t) => t.voices?.hers ?? null).catch(() => null), []);
  return (res.state === "ready" && res.data) || "the companion";
}

/** The open phase of a run: where it is, her text as she sent it (a letter), and the box for his answer. */
function PhaseView({ run, hers }: { run: RunOne; hers: string }) {
  const phase = run.phase;
  const [note, setNote] = useState<string | null>(null);
  if (!phase || !run.current) return null;
  const session = run.sessions[run.current.session - 1];
  const stem = session?.entry ?? null;
  const reflect = phase.type === "reflect";
  const closed =
    run.status === "paused" ? "On a break: resume to carry on." :
    run.status !== "active" ? `This run is ${run.status}.` :
    !phase.delivered ? "This phase is being sent; it will be here in a moment." :
    !phase.open ? "This phase takes no more answers. Next moves on; Back opens an earlier phase again." :
    !stem ? "This session has no entry to write to yet." :
    undefined;
  return (
    <section class="phase" aria-labelledby="phase-h">
      <PhaseStrip run={run} />
      <h2 id="phase-h" class="phase-title">{phase.title}</h2>
      {reflect && phase.sent_text ? (
        <div class="phase-sent">
          <p class="hint">Written by the companion for this phase, from what you have said so far.</p>
          <Letter class="phase-letter" from={hers}>
            <Markdown text={phase.sent_text} />
          </Letter>
        </div>
      ) : (
        <>
          {reflect && <p class="hint">The companion writes this phase when it is sent; this is the template.</p>}
          <Letter class="phase-letter" from={hers}>
            {phase.blocks.map((b, i) => (
              <div class="phase-block" key={i}>
                {b.heading && <h3>{b.heading}</h3>}
                <Markdown text={b.text} />
              </div>
            ))}
          </Letter>
        </>
      )}
      {(phase.answered || phase.skipped) && (
        <p class="phase-state">{phase.answered ? "Answered" : "Skipped"}</p>
      )}
      <Composer
        draftKey={`run:${run.id}:${run.current.session}:${run.current.phase}`}
        label={`Your answer to ${phase.title}`}
        placeholder="Answer in your own words"
        submitLabel="Add to my answer"
        rest
        closed={closed}
        onSubmit={async (words) => setNote(afterAnswer(await api.addBlock(stem as string, { ...words, phase: phase.phase })))}
      />
      {note && <p class="saved" role="status">{note}</p>}
    </section>
  );
}

/** The session's phases as segments, the done ones in his ink and the open one filled; named "Phase 2 of 6". */
function PhaseStrip({ run }: { run: RunOne }) {
  const total = run.phase?.phases_total ?? 0;
  if (!run.current || !run.phase || total === 0) return null;
  const at = run.current.phase;
  return (
    <div class="phase-where">
      <p class="phase-count num" aria-hidden="true">
        Session {run.current.session} · phase {at} of {total}
      </p>
      <ol class="phase-strip" aria-label={`Phase ${at} of ${total}`}>
        {Array.from({ length: total }, (_, i) => (
          <li key={i} class={i + 1 < at ? "done" : i + 1 === at ? "now" : undefined} aria-current={i + 1 === at ? "step" : undefined}
            style={{ "--k": i }}>
            <span class="visually-hidden">Phase {i + 1}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}

function Controls({ run, reload }: { run: RunOne; reload: () => void }) {
  const act = useAction();
  const [wait, setWait] = useState<string | null>(null);
  useLive(["runs-changed"], () => setWait(null));
  const go = async (action: RunAction) => {
    const bound = action === "next" || action === "skip" || action === "back" ? run.where : null;
    const done = await act.run(() => api.runAction(run.id, action, bound));
    if (done) setWait(WAITING[action]);
    else reload();   // a 409 means the run is not where this page thought: read it again
  };
  const moved = act.error?.slug === "moved-on";
  return (
    <div class="controls">
      <div class="run-actions">
        {run.keyboard
          .filter((a) => a !== "abandon")
          .map((a) => (
            <Button key={a} variant={a === "next" || a === "resume" ? "primary" : a === "pause" ? "quiet" : "secondary"}
              disabled={act.busy || wait !== null} onClick={() => void go(a)}>
              {wait && (a === "next" || a === "resume") ? wait : LABEL[a]}
            </Button>
          ))}
        {run.keyboard.includes("abandon") && (
          <span class="run-abandon">
            <ConfirmButton question="Abandon this run?" yes="Abandon" disabled={act.busy || wait !== null} onConfirm={() => void go("abandon")}>
              Abandon
            </ConfirmButton>
          </span>
        )}
      </div>
      {wait && <p class="hint" role="status">{wait} It takes a moment.</p>}
      {act.error && (moved ? <p class="error-line" role="alert">The program has moved on since you looked; this page now shows where it is.</p> : <ErrorLine error={act.error} />)}
    </div>
  );
}

function Insights({ run }: { run: RunOne }) {
  const [res, reload] = useResource(
    (signal) => (run.insights_entry ? api.runInsights(run.id, signal) : Promise.resolve(null)),
    [run.id, run.insights_entry],
  );
  const act = useAction();
  const [asked, setAsked] = useState(false);
  if (run.status !== "completed") return null;
  return (
    <section class="run-sec" aria-labelledby="ins-h">
      <h2 class="book-head" id="ins-h">Insights</h2>
      {run.insights_entry ? (
        <Await res={res} reload={reload} rows={1}>
          {(doc) =>
            doc ? (
              <Disclosure class="run-compiled" head="The compiled document">
                <div class="read">
                  <Markdown text={doc.body} />
                </div>
              </Disclosure>
            ) : null
          }
        </Await>
      ) : (
        <>
          <p class="empty">Nothing has been compiled from this run yet.</p>
          <Button
            disabled={act.busy || asked}
            onClick={async () => {
              const done = await act.run(() => api.runAction(run.id, "insights"));
              if (done) setAsked(true);
            }}
          >
            {asked ? "Compiling…" : "Compile insights"}
          </Button>
          {act.error && <ErrorLine error={act.error} />}
        </>
      )}
    </section>
  );
}

const SESSION_WORDS: Record<string, string> = {
  completed: "done",
  in_progress: "open",
  left: "left",
  pending: "to come",
  "not-started": "to come",
  skipped: "skipped",
};

export function RunRoute({ id = "" }: { id?: string }) {
  const [res, reload] = useResource((signal) => api.run(id, signal), [id]);
  useLive(["runs-changed", "ledger-changed", "entries-changed", "library-changed", "tick"], reload);
  useTitle(res.state === "ready" ? res.data.title : "Program");
  const hers = useHers();
  return (
    <div class="run-page">
      <PageBack href="/">Today</PageBack>
      {res.state === "error" && res.error.status === 404 ? (
        <Empty>No run by that name.</Empty>
      ) : (
        <Await res={res} reload={reload} rows={4}>
          {(run) => (
            <>
              <header class="run-head rise">
                <p class="kicker">{STATUS[run.status] ?? run.status}</p>
                <h1 class="display">
                  {run.emoji && <span class="run-emoji" aria-hidden="true">{run.emoji} </span>}
                  {run.title}
                </h1>
                <p class="lede">{run.text}</p>
                {run.sessions.length > 1 && (
                  <div class="run-sessions-strip">
                    <p class="strip-meta num">
                      {sessionsDone(run)} of {run.sessions.length} sessions done
                    </p>
                    <RunStrip run={run} />
                  </div>
                )}
              </header>
              <PhaseView run={run} hers={hers} />
              {!run.phase && run.status === "active" && <Empty>The next session begins when you press Next.</Empty>}
              {run.keyboard.length > 0 && <Controls run={run} reload={reload} />}
              <Insights run={run} />
              {run.sessions.length > 1 && (
                <section class="run-sec" aria-labelledby="sessions-h">
                  <h2 class="book-head" id="sessions-h">Sessions</h2>
                  <ol class="sessions">
                    {run.sessions.map((s) => {
                      const inner = (
                        <>
                          <span class="n num">{s.number}</span>
                          <span class="t">{s.title}</span>
                          <span class="kind">{SESSION_WORDS[s.status] ?? s.status.replace(/[_-]/g, " ")}</span>
                        </>
                      );
                      return (
                        <li key={s.number} class={s.status}>
                          {s.entry && (s.status === "completed" || s.status === "left") ? (
                            <a class="row" href={`/entries/${s.entry}`}>{inner}</a>
                          ) : (
                            <span class="row">{inner}</span>
                          )}
                        </li>
                      );
                    })}
                  </ol>
                </section>
              )}
              <p class="run-foot">
                <a href={`/library/${run.program}`}>The program in the Library</a>
              </p>
            </>
          )}
        </Await>
      )}
    </div>
  );
}
