/**
 * The states every read passes through: loading (Skeleton: the shape of what is coming, not a spinner, so nothing
 * jumps when it lands), a failed read (ErrorLine: what happened in words, with Retry), nothing there (Empty: a quiet
 * sentence in the book's italic, with the next step when there is one), and Await, which picks among them so a view
 * reads as its ready state only. Status has no hue: an error is said in words with a filled mark.
 */
import type { ComponentChildren } from "preact";
import type { ApiError } from "../api";
import type { Resource } from "../hooks";
import { Button } from "./Button";
import "./Status.css";

/** Placeholder rows while a read is out. `shape` picks the kind of row: a line of text, a row, a block. */
export function Skeleton({ rows = 3, shape = "row" }: { rows?: number; shape?: "line" | "row" | "block" }) {
  return (
    <div class={`skeletons ${shape}`} role="status" aria-live="polite" aria-label="Loading">
      {Array.from({ length: rows }, (_, i) => (
        <div class="skeleton" key={i} style={{ "--i": i }} />
      ))}
    </div>
  );
}
/** The old name. */
export const Loading = Skeleton;

/** A failed read or write, in words, with Retry. A network failure says so first. */
export function ErrorLine({ error, onRetry, children }: { error?: ApiError; onRetry?: () => void; children?: ComponentChildren }) {
  return (
    <div class="error-line" role="alert">
      <p>
        {error && error.slug === "network" ? "Can't reach the journal server. " : ""}
        {error?.detail}
        {children}
      </p>
      {onRetry && (
        <Button size="small" onClick={onRetry}>
          Retry
        </Button>
      )}
    </div>
  );
}

/** Loading, error or the ready state, so a view reads as the ready state only. */
export function Await<T>(props: {
  res: Resource<T>;
  reload: () => void;
  rows?: number;
  children: (data: T) => ComponentChildren;
}) {
  const { res } = props;
  if (res.state === "loading") return <Skeleton rows={props.rows} />;
  if (res.state === "error") return <ErrorLine error={res.error} onRetry={props.reload} />;
  return <>{props.children(res.data)}</>;
}

/** Nothing here: one quiet sentence, and the next step when there is one. */
export function Empty({ children }: { children: ComponentChildren }) {
  return <p class="empty">{children}</p>;
}
