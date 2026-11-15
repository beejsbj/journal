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
  /** What he is writing: a note (the default), an answer to a practice (its id; `slot`, the row of today's practices
   *  it answers, when the practice has two in a day), or more for an entry (its stem). */
  for?: { kind: "note" } | { kind: "answer"; practice: string; slot?: number } | { kind: "entry"; stem: string };
  /** Where it was opened from: the surface grows from this control and gives focus back to it. */
  from?: HTMLElement | null;
  /** Begin with the voice: the room opens recording ("Speak it"). */
  voice?: boolean;
}

let request: WriteRequest | null = null;
const listeners = new Set<(r: WriteRequest | null) => void>();

/** Open the room. One at a time: while it stands (open, saving, or folding away) another request is ignored, so a
 *  stray N or a late link never swaps what the open room is for or how it began. */
export function openWrite(req: WriteRequest = {}): void {
  if (request) return;
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

// what the room wrote: a page showing that entry reads it again (the entry's own box is told by the draft, drafts.ts)
const written = new Set<(stem: string) => void>();

/** Hear what the write room saved (the stem of the entry it wrote or added to). Returns the way to stop. */
export function onWritten(fn: (stem: string) => void): () => void {
  written.add(fn);
  return () => {
    written.delete(fn);
  };
}

/** The room says it saved into an entry. */
export function wrote(stem: string): void {
  for (const fn of [...written]) fn(stem);
}
