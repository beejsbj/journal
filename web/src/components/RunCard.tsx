import { useState } from "preact/hooks";
import { api } from "../api";
import { useLive } from "../events";
import { useAction } from "../hooks";
import type { Run } from "../types";
import { ConfirmButton } from "./ConfirmButton";
import { ErrorLine } from "./Status";

/** Whether a run still has a one-off question to send. Session 10 is the last session, and its questions go out
 *  the day after it: by then the run is `completed`, and `programs.pending_interrupts` still counts it. */
export function holdsQuestions(run: Pick<Run, "status" | "scheduled">): boolean {
  return !CLOSED.includes(run.status) && run.scheduled.some((s) => (s.status ?? "pending") === "pending");
}

/** Statuses of a run that ended without finishing: its unsent one-offs are cancelled (`programs.CLOSED`). */
export const CLOSED = ["abandoned", "expired"];

/** Sessions of a run that are finished (the progress bar's numerator). */
export function sessionsDone(run: Pick<Run, "sessions">): number {
  return run.sessions.filter((s) => s.status === "completed").length;
}

export function Progress({ done, total }: { done: number; total: number }) {
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;
  return (
    <div class="progress-bar" role="progressbar" aria-valuemin={0} aria-valuemax={total} aria-valuenow={done}
      aria-label={`${done} of ${total} sessions done`}>
      <span style={{ width: `${pct}%` }} />
    </div>
  );
}

/**
 * A run in progress, on Today: where it is, Continue (the run screen) and Pause or Resume. A transition is spawned
 * by the server (`202`) and happens a moment later: the card says so and clears when the stream reports the change.
 */
export function RunCard({ run }: { run: Run }) {
  const act = useAction();
  const [wait, setWait] = useState<string | null>(null);
  useLive(["runs-changed"], () => setWait(null));
  const paused = run.status === "paused";
  const go = async (action: "pause" | "resume") => {
    const done = await act.run(() => api.runAction(run.id, action));
    if (done) setWait(action === "pause" ? "Pausing…" : "Resuming…");
  };
  return (
    <li class="card run">
      {run.emoji && <span class="emoji" aria-hidden="true">{run.emoji}</span>}
      <div class="main">
        <h3>{run.title}</h3>
        <p class="meta">{run.text}</p>
        {run.sessions.length > 1 && <Progress done={sessionsDone(run)} total={run.sessions.length} />}
        {paused && <p class="pill warn">On a break</p>}
      </div>
      <div class="actions">
        <a class="button primary" href={`/runs/${run.id}`}>
          Continue
        </a>
        <button type="button" class="button" disabled={act.busy || wait !== null} onClick={() => void go(paused ? "resume" : "pause")}>
          {wait ?? (paused ? "Resume" : "Pause")}
        </button>
      </div>
      {act.error && <ErrorLine error={act.error} />}
    </li>
  );
}

/**
 * A finished run whose last questions are still to come. It has no controls but one: stopping the questions
 * (`abandon` on a completed run only cancels them; the entries stay).
 */
export function HeldRunCard({ run }: { run: Run }) {
  const act = useAction();
  const [wait, setWait] = useState(false);
  useLive(["runs-changed"], () => setWait(false));
  const stop = async () => {
    const done = await act.run(() => api.runAction(run.id, "abandon"));
    if (done) setWait(true);
  };
  return (
    <li class="card run">
      {run.emoji && <span class="emoji" aria-hidden="true">{run.emoji}</span>}
      <div class="main">
        <h3>{run.title}</h3>
        <p class="meta">Finished. Its last questions are still on their way.</p>
      </div>
      <div class="actions">
        <a class="button" href={`/runs/${run.id}`}>
          Open
        </a>
        <ConfirmButton question="Cancel its questions?" yes="Cancel them" disabled={act.busy || wait} onConfirm={() => void stop()}>
          {wait ? "Cancelling…" : "Stop the questions"}
        </ConfirmButton>
      </div>
      {act.error && <ErrorLine error={act.error} />}
    </li>
  );
}
