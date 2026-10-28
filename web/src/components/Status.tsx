import type { ComponentChildren } from "preact";
import type { ApiError } from "../api";
import type { Resource } from "../hooks";

/** Skeleton cards while a read is out: shape, not spinner, so the page does not jump when it arrives. */
export function Loading({ rows = 3 }: { rows?: number }) {
  return (
    <div class="skeletons" role="status" aria-live="polite" aria-label="Loading">
      {Array.from({ length: rows }, (_, i) => (
        <div class="skeleton" key={i} />
      ))}
    </div>
  );
}

/** A failed read, in words, with Retry. `unpaired` is the pairing screen's job, so it says so briefly. */
export function ErrorLine({ error, onRetry }: { error: ApiError; onRetry?: () => void }) {
  return (
    <div class="error-line" role="alert">
      <p>
        {error.slug === "network" ? "Can't reach the journal server. " : ""}
        {error.detail}
      </p>
      {onRetry && (
        <button type="button" class="button" onClick={onRetry}>
          Retry
        </button>
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
  if (res.state === "loading") return <Loading rows={props.rows} />;
  if (res.state === "error") return <ErrorLine error={res.error} onRetry={props.reload} />;
  return <>{props.children(res.data)}</>;
}

export function Empty({ children }: { children: ComponentChildren }) {
  return <p class="empty">{children}</p>;
}
