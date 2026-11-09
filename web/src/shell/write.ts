/**
 * The write surface's seam. Anything that opens it (the rail's Write, the tab bar's, the N key, Today's Answer, an
 * entry's "Keep writing…") calls `openWrite`, with what it is for and the control it grew from (the room blooms out
 * of it); the shell renders the surface while a request stands, and the surface calls `closeWrite`.
 *
 *   openWrite({ for: "note", from: e.currentTarget });
 *   const req = useWriteRequest();   // the shell, or the surface itself
 */
import { useEffect, useState } from "preact/hooks";

export interface WriteRequest {
  /** What he is writing: a note (the default), an answer to a practice (its id), or more for an entry (its stem). */
  for?: { kind: "note" } | { kind: "answer"; practice: string } | { kind: "entry"; stem: string };
  /** Where it was opened from: the surface grows from this control and gives focus back to it. */
  from?: HTMLElement | null;
}

let request: WriteRequest | null = null;
const listeners = new Set<(r: WriteRequest | null) => void>();

export function openWrite(req: WriteRequest = {}): void {
  request = { for: { kind: "note" }, ...req };
  listeners.forEach((l) => l(request));
}

export function closeWrite(): void {
  const from = request?.from;
  request = null;
  listeners.forEach((l) => l(null));
  from?.focus?.();
}

export function useWriteRequest(): WriteRequest | null {
  const [r, setR] = useState(request);
  useEffect(() => {
    listeners.add(setR);
    setR(request);
    return () => {
      listeners.delete(setR);
    };
  }, []);
  return r;
}
