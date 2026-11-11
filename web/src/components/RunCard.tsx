import { useState } from "preact/hooks";
import { api } from "../api";
import { useLive } from "../events";
import { useAction } from "../hooks";
import type { Run } from "../types";
import { Button, ButtonLink, ErrorLine, ProgressStrip } from "../ui";
import { ConfirmButton } from "./ConfirmButton";
import "./RunCard.css";

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

/** How far into the session that is open the run is (0-1), for the strip's current segment. */
export function sessionShare(run: Pick<Run, "sessions" | "current">): number | undefined {
  if (!run.current) return undefined;
  const total = run.sessions[run.current.session - 1]?.phases_total ?? 0;
  return total > 0 ? Math.max(0.08, (run.current.phase - 0.5) / total) : undefined;
}

/** A program's way through, as Today and the run screen draw it: its sessions as segments, the open one filling. */
export function RunStrip({ run }: { run: Run }) {
  return (
    <ProgressStrip label={`${run.title}: ${sessionsDone(run)} of ${run.sessions.length} sessions done`} done={sessionsDone(run)}
      total={run.sessions.length} current={sessionShare(run)} />
  );
}

/**
 * A run in progress, on Today: its name and where it is, the strip of its sessions, Continue (the run screen) and Pause
 * or Resume. A transition is spawned by the server (`202`) and happens a moment later: the card says so and clears
 * when the stream reports the change.
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
  const at = run.current?.session ?? sessionsDone(run);
  return (
    <li class="run-card run">
      <div class="run-card-head">
        <h3>{run.title}</h3>
        {/* the session he is in, said as that (the strip under it counts the sessions done) */}
        {run.sessions.length > 1 && <p class="run-card-at num">{run.current ? `session ${at} of ${run.sessions.length}` : `${at} of ${run.sessions.length} done`}</p>}
      </div>
      {run.sessions.length > 1 && <RunStrip run={run} />}
      <p class="run-card-text">{run.text}</p>
      {paused && <p class="run-card-state">On a break</p>}
      <div class="run-card-actions">
        <ButtonLink href={`/runs/${run.id}`} size="small" data-vt="push">
          Continue
        </ButtonLink>
        <Button variant="quiet" size="small" disabled={act.busy || wait !== null} onClick={() => void go(paused ? "resume" : "pause")}>
          {wait ?? (paused ? "Resume" : "Pause")}
        </Button>
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
    <li class="run-card run held">
      <div class="run-card-head">
        <h3>{run.title}</h3>
      </div>
      <p class="run-card-text">Finished. Its last questions are still on their way.</p>
      <div class="run-card-actions">
        <ButtonLink href={`/runs/${run.id}`} size="small" variant="quiet" data-vt="push">
          Open
        </ButtonLink>
        <ConfirmButton size="small" question="Cancel its questions?" yes="Cancel them" disabled={act.busy || wait} onConfirm={() => void stop()}>
          {wait ? "Cancelling…" : "Stop the questions"}
        </ConfirmButton>
      </div>
      {act.error && <ErrorLine error={act.error} />}
    </li>
  );
}
