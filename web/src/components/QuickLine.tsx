import { useEffect, useRef, useState } from "preact/hooks";
import { api } from "../api";
import { Button, ErrorLine } from "../ui";
import { KeptWords, OfflineNote, useCompose } from "./Composer";
import "./QuickLine.css";

/**
 * A line put down from Today (issue #82): one line of his, Enter to put it down, exactly what `/j` does in the chat
 * (`POST /entries` with no practice, `actions.note`: a note of its own, stamped now, filed by the journal day). The
 * write room stays the place for longer pieces. Every rule of the composer holds (`useCompose`): the draft is kept in
 * the tab, a retry is the same write, offline says so; words the server refuses (`/x`, an engine line) stay in the box
 * with the server's reason and no Retry, as everywhere else, and so do words too long to save (413): the reason is
 * plain, and sending them again would fail the same way.
 *
 * The box is a one-row textarea, not an `<input>`: an input flattens a pasted paragraph into one line, changing his
 * words without a word. Enter puts the line down; Shift+Enter adds a line (as does a multi-line paste), and the box
 * grows with them, up to `MAX_ROWS`, so what is sent is what he sees.
 */
const MAX_ROWS = 6;
/** Statuses where sending the same words again would fail the same way: a refusal (422) or too long (413). */
const FINAL = new Set([413, 422]);

export function QuickLine({ onDone }: { onDone?: () => void }) {
  const saved = useRef<string | null>(null);
  const [said, setSaid] = useState<{ stem: string; n: number } | null>(null);
  const c = useCompose({
    draftKey: "today-line",
    onSubmit: async (words) => {
      const made = await api.createEntry(words);
      saved.current = made.stem;
      return made;
    },
    onDone: () => {
      if (saved.current) setSaid((s) => ({ stem: saved.current as string, n: (s?.n ?? 0) + 1 }));
      onDone?.();
    },
  });
  // the word about it stays a few seconds, then goes
  useEffect(() => {
    if (!said) return;
    const t = setTimeout(() => setSaid((s) => (s && s.n === said.n ? null : s)), 6000);
    return () => clearTimeout(t);
  }, [said]);
  return (
    <div class="quick-line">
      <KeptWords c={c} />
      <form
        class="quick-line-form"
        onSubmit={(e) => {
          e.preventDefault();
          void c.send();
        }}
      >
        <label class="visually-hidden" for="quick-line">A line for today, saved as a note</label>
        <textarea
          id="quick-line"
          class="input his quick-line-box"
          rows={Math.min(MAX_ROWS, c.text.split("\n").length)}
          autocomplete="off"
          enterkeyhint="send"
          value={c.text}
          placeholder="A line for today"
          readOnly={c.busy}
          aria-describedby={c.error ? "quick-line-error" : undefined}
          onInput={(e) => c.edit((e.currentTarget as HTMLTextAreaElement).value)}
          onKeyDown={(e) => {
            // Enter puts it down; Shift+Enter is a new line; Enter that ends an IME composition is the composition's
            if (e.key !== "Enter" || e.shiftKey || e.isComposing) return;
            e.preventDefault();
            if (c.text.trim() && !c.busy) void c.send();
          }}
        />
        <Button type="submit" size="small" disabled={!c.text.trim() || c.busy}>
          {c.busy ? "Saving…" : "Put down"}
        </Button>
      </form>
      {!c.online && <OfflineNote clip={false} />}
      {c.error && (
        <div id="quick-line-error">
          <ErrorLine error={c.error} {...(FINAL.has(c.error.status) ? {} : { onRetry: () => void c.send() })} />
        </div>
      )}
      {said && (
        <p class="quick-line-said" role="status">
          Saved as a note. <a href={`/entries/${encodeURIComponent(said.stem)}`}>Open it</a>
        </p>
      )}
    </div>
  );
}
