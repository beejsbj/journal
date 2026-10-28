import { useCallback, useEffect, useRef, useState } from "preact/hooks";
import { ApiError } from "./api";

export type Resource<T> =
  | { state: "loading" }
  | { state: "error"; error: ApiError }
  | { state: "ready"; data: T };

/** What a failed read is called on screen: the server's own line when it sent one. */
const LOADING: Resource<never> = { state: "loading" };

export function asApiError(e: unknown): ApiError {
  return e instanceof ApiError ? e : new ApiError(0, "client", e instanceof Error ? e.message : "Something went wrong.");
}

/**
 * Load something when the view appears and whenever `deps` change; `reload()` asks again (after an event, or Retry).
 * A reload keeps showing what was there until the new answer arrives, and a failed one replaces it with the error:
 * the view never shows an old answer as if it were current.
 */
export function useResource<T>(load: (signal: AbortSignal) => Promise<T>, deps: unknown[]): [Resource<T>, () => void] {
  // The answer is kept with the inputs it was read for: under other inputs it is not shown, not even for the one
  // render before the effect below starts the new read.
  const [held, setHeld] = useState<{ res: Resource<T>; deps: unknown[] }>({ res: LOADING, deps });
  const res = held.deps.length === deps.length && held.deps.every((d, i) => Object.is(d, deps[i])) ? held.res : LOADING;
  const setRes = (next: Resource<T> | ((r: Resource<T>) => Resource<T>)) =>
    setHeld((h) => ({ res: typeof next === "function" ? next(h.res) : next, deps }));
  const [round, setRound] = useState(0);
  const loader = useRef(load);
  loader.current = load;
  const lastRound = useRef(round);
  useEffect(() => {
    // New inputs (another entry, another filter) start from the loading state; a reload keeps what is shown.
    if (lastRound.current === round) setRes((r) => (r.state === "loading" ? r : { state: "loading" }));
    lastRound.current = round;
    const ctl = new AbortController();
    loader
      .current(ctl.signal)
      .then((data) => {
        if (!ctl.signal.aborted) setRes({ state: "ready", data });
      })
      .catch((e: unknown) => {
        if (ctl.signal.aborted || (e instanceof DOMException && e.name === "AbortError")) return;
        setRes({ state: "error", error: asApiError(e) });
      });
    return () => ctl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [round, ...deps]);
  const reload = useCallback(() => setRound((n) => n + 1), []);
  return [res, reload];
}

/**
 * A write in flight: `run` calls it, keeps `busy` while it is out and the error when it fails (the view shows the
 * error where the write was made and leaves the form as it was). Resolves to the answer, or undefined on a failure.
 */
export function useAction(): {
  busy: boolean;
  error: ApiError | null;
  run: <T>(fn: () => Promise<T>) => Promise<T | undefined>;
  clear: () => void;
} {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const run = useCallback(async <T,>(fn: () => Promise<T>): Promise<T | undefined> => {
    setBusy(true);
    setError(null);
    try {
      return await fn();
    } catch (e) {
      setError(asApiError(e));
      return undefined;
    } finally {
      setBusy(false);
    }
  }, []);
  const clear = useCallback(() => setError(null), []);
  return { busy, error, run, clear };
}
