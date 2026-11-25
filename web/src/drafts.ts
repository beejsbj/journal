/**
 * An unsent draft, kept in `sessionStorage` so a failed write, a dropped pairing or a reload does not lose what was
 * typed (FRONTEND.md decision 3). It lives as long as the tab; nothing else is stored in the browser, and nothing is
 * written to the journal until the person sends it.
 */
const PREFIX = "journal-draft:";

function store(): Storage | null {
  try {
    return typeof sessionStorage === "undefined" ? null : sessionStorage;
  } catch {
    return null;   // storage can be blocked: the draft is then just not kept
  }
}

export function loadDraft(key: string): string {
  try {
    return store()?.getItem(PREFIX + key) ?? "";
  } catch {
    return "";
  }
}

/** Who changed a draft (a box's own token), so a box does not answer its own change. */
export type DraftOwner = object;
type Listener = (text: string, from: DraftOwner | undefined) => void;
const listeners = new Map<string, Set<Listener>>();

/**
 * Hear every change to one key's draft made in this tab: two boxes can hold the same draft (the entry's box and the
 * write room on that entry), and each must see what the other wrote or sent, or one overwrites the other's words
 * (or sends them twice). Returns the way to stop listening.
 */
export function onDraft(key: string, fn: Listener): () => void {
  let set = listeners.get(key);
  if (!set) listeners.set(key, (set = new Set()));
  set.add(fn);
  return () => {
    set?.delete(fn);
    if (set?.size === 0) listeners.delete(key);
  };
}

/** Keep (or, for nothing, forget) the draft, and tell the other boxes on this key. Says whether the store took it:
 *  the write room only says "Draft kept" when it was. A draft emptied takes its paragraph times with it. */
export function saveDraft(key: string, text: string, from?: DraftOwner): boolean {
  let stored = false;
  try {
    const s = store();
    if (s) {
      if (text) s.setItem(PREFIX + key, text);
      else {
        s.removeItem(PREFIX + key);
        s.removeItem(TIMES + key);
      }
      stored = true;
    }
  } catch {
    stored = false;   // a full or blocked store: the text stays on the page
  }
  for (const fn of [...(listeners.get(key) ?? [])]) fn(text, from);
  return stored;
}

const TIMES = "journal-draft-times:";

/** When each line of the draft was begun (`paragraphTimes.ts`), as kept beside it; unchecked: `stampsFor` fits it to
 *  the text. Null when nothing is kept. */
export function loadStamps(key: string): unknown {
  try {
    const raw = store()?.getItem(TIMES + key);
    return raw ? (JSON.parse(raw) as unknown) : null;
  } catch {
    return null;
  }
}

/** Keep the draft's stamps beside it. Written before the draft itself, so a box that hears the draft change reads
 *  the stamps that go with it. A store that refuses them only loses the times, never the words. */
export function saveStamps(key: string, stamps: (number | null)[]): void {
  try {
    const s = store();
    if (!s) return;
    if (stamps.some((t) => t !== null)) s.setItem(TIMES + key, JSON.stringify(stamps));
    else s.removeItem(TIMES + key);
  } catch {
    // (full or blocked: the words are kept on their own)
  }
}

/** An id for one write, so that sending it twice (a retry after a lost answer) is one write. */
export function newClientId(): string {
  const c = (globalThis as { crypto?: Crypto }).crypto;
  if (c && typeof c.randomUUID === "function") return c.randomUUID();
  return `w${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
}
