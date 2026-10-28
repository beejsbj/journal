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

export function saveDraft(key: string, text: string): void {
  try {
    if (text) store()?.setItem(PREFIX + key, text);
    else store()?.removeItem(PREFIX + key);
  } catch {
    /* a full or blocked store: the text stays on the page */
  }
}

/** An id for one write, so that sending it twice (a retry after a lost answer) is one write. */
export function newClientId(): string {
  const c = (globalThis as { crypto?: Crypto }).crypto;
  if (c && typeof c.randomUUID === "function") return c.randomUUID();
  return `w${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
}
