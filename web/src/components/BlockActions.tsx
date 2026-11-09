import { useEffect, useRef, useState } from "preact/hooks";
import { api } from "../api";
import { loadDraft, saveDraft } from "../drafts";
import { useAction } from "../hooks";
import { Copy, More, Pen, Trash } from "../icons";
import { OFFLINE_WRITE, useOnline, useStaleAsOf } from "../offline";
import type { Block } from "../types";
import { Button, ErrorLine, IconButton, Sheet } from "../ui";
import "./BlockActions.css";

/**
 * Edit and delete one block of his. His entries are plain Markdown files he can already change in his notes app, so
 * the web app is one more editor for him (Decision 4, amended 2026-10-20): a plain edit, no history. Only his own
 * blocks come here; hers has no such control.
 *
 * Every change names the block as he read it (`was`, `at`): if it reads differently by the time he saves, the server
 * answers 409 `changed` and nothing is written. His unsaved edit is kept in `sessionStorage` (like a composer's
 * draft) until he saves or cancels, so a conflict, a failed write or a reload does not lose it.
 */
export interface BlockTarget {
  stem: string;
  block: Block;
  /** An answer to a program's question: it can be edited but not removed (the program counts it). */
  keep: boolean;
  /** Called after a change was made, so the entry is read again. */
  onChanged: () => void;
  /** Where a done action says so in a word ("Copied your words."): the entry's toast. */
  notify?: ((said: string) => void) | undefined;
}

const draftKey = (stem: string, key: string) => `edit:${stem}:${key}`;
const lostKey = (stem: string, key: string) => `lost:${stem}:${key}`;

/**
 * Who a block is: its key (a position) plus its time and a fingerprint of its words. A key alone names a place, and
 * another block takes that place when one above it is removed; keyed by this, a block that changes or moves is a new
 * component, so an open editor closes instead of carrying one block's words onto another.
 */
export function blockIdentity(b: Block): string {
  let h = 5381;
  for (let i = 0; i < b.text.length; i++) h = ((h * 33) ^ b.text.charCodeAt(i)) >>> 0;
  return `${b.key}|${b.at ?? ""}|${h.toString(36)}`;
}

/** An unsaved edit and the block it was an edit of: what it is restored onto must still read exactly so. */
interface EditDraft {
  was: string;
  at: string;
  text: string;
}

function readDraft(key: string): EditDraft | null {
  const raw = loadDraft(key);
  if (!raw) return null;
  try {
    const d = JSON.parse(raw) as Partial<EditDraft>;
    if (typeof d.was === "string" && typeof d.text === "string") return { was: d.was, at: typeof d.at === "string" ? d.at : "", text: d.text };
  } catch {
    /* not a draft of this shape: an older one, or damaged */
  }
  return { was: "", at: "", text: raw };   // words with no known basis are never an edit of any block: they are only kept
}

/**
 * What the editor starts from, worked out once when it opens. A draft whose basis (the block's words and time when it
 * was typed) is the block as it reads now is his edit, back in the box. Any other draft belongs to a block that has
 * since changed or moved away from this place: it is never put into this block's editor, it is set aside as text he
 * can copy or let go.
 */
function begin(stem: string, block: Block): { text: string; resumed: boolean; lost: string } {
  const key = draftKey(stem, block.key);
  const kept = readDraft(key);
  const earlier = loadDraft(lostKey(stem, block.key));
  if (!kept) return { text: block.text, resumed: false, lost: earlier };
  const basis = kept.was === block.text && kept.at === (block.at ?? "");
  if (basis && kept.text !== block.text) return { text: kept.text, resumed: true, lost: earlier };
  saveDraft(key, "");
  if (basis || kept.text.trim() === "") return { text: block.text, resumed: false, lost: earlier };
  const lost = earlier && earlier !== kept.text ? `${earlier}\n\n---\n\n${kept.text}` : kept.text;
  saveDraft(lostKey(stem, block.key), lost);
  return { text: block.text, resumed: false, lost };
}

/** The words, or the editor in their place. `open` and `close` are the block's: the buttons live under the words. */
export function BlockEditor({ target, onClose }: { target: BlockTarget; onClose: () => void }) {
  const { stem, block, onChanged } = target;
  const key = draftKey(stem, block.key);
  // The words he is looking at when he opens the editor are what the save is checked against: a block that changed
  // after that (here or in another tab) is a conflict, not something to overwrite with a form that does not know.
  const [seen] = useState(block);
  const [start] = useState(() => begin(stem, block));
  const [text, setText] = useState(start.text);
  const [resumed, setResumed] = useState(start.resumed);
  const [lost, setLost] = useState(start.lost);
  const online = useOnline();
  const stale = useStaleAsOf() !== null;
  const act = useAction();
  const changed = text !== seen.text && text.trim() !== "";

  const edit = (value: string) => {
    setText(value);
    setResumed(false);
    const draft: EditDraft = { was: seen.text, at: seen.at ?? "", text: value };
    saveDraft(key, value === seen.text ? "" : JSON.stringify(draft));
  };
  const cancel = () => {
    saveDraft(key, "");
    onClose();
  };
  const save = async () => {
    if (!changed || act.busy || !online || stale) return;
    const done = await act.run(() => api.editBlock(stem, seen.key, { text, was: seen.text, ...(seen.at ? { at: seen.at } : {}) }));
    if (!done) return;
    saveDraft(key, "");
    onClose();
    onChanged();
  };
  const conflict = act.error?.status === 409 && act.error.slug === "changed";

  return (
    <form
      class="block-editor"
      onSubmit={(e) => {
        e.preventDefault();
        void save();
      }}
    >
      <label class="visually-hidden" for={`edit-${block.key}`}>
        Edit your words{block.at ? `, ${block.at}` : ""}
      </label>
      <textarea
        id={`edit-${block.key}`}
        rows={Math.min(14, Math.max(4, text.split("\n").length + 1))}
        value={text}
        disabled={act.busy}
        onInput={(e) => edit((e.currentTarget as HTMLTextAreaElement).value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
            e.preventDefault();
            void save();
          }
        }}
      />
      {lost && (
        <div class="hint lost-draft" role="status">
          <p>An unsaved edit you made earlier was of words that are no longer here, so it is not in the box:</p>
          <textarea readOnly rows={Math.min(8, Math.max(2, lost.split("\n").length))} value={lost} aria-label="Your earlier unsaved edit" />
          <button
            type="button"
            class="link"
            onClick={() => {
              void navigator.clipboard?.writeText(lost).catch(() => undefined);
            }}
          >
            Copy
          </button>{" "}
          <button
            type="button"
            class="link"
            onClick={() => {
              saveDraft(lostKey(stem, block.key), "");
              setLost("");
            }}
          >
            Let it go
          </button>
        </div>
      )}
      {resumed && !act.error && <p class="hint">Your unsaved edit from before is back in the box.</p>}
      {(!online || stale) && (
        <p class="offline-note" role="status">
          {OFFLINE_WRITE} What you write stays here.
        </p>
      )}
      {act.error &&
        (conflict ? (
          <div class="error-line" role="alert">
            <p>{act.error.detail || "This changed since you opened it."} Your edit is kept: reload, then edit again.</p>
            <Button
              size="small"
              onClick={() => {
                onClose();
                onChanged();
              }}
            >
              Reload the entry
            </Button>
          </div>
        ) : (
          <ErrorLine error={act.error} {...(act.error.status === 422 || act.error.status === 409 ? {} : { onRetry: () => void save() })} />
        ))}
      <div class="block-editor-actions">
        <Button variant="primary" size="small" type="submit" disabled={!changed || act.busy || !online || stale}>
          {act.busy ? "Saving…" : "Save"}
        </Button>
        <Button variant="quiet" size="small" onClick={cancel} disabled={act.busy}>
          Cancel
        </Button>
        <span class="block-editor-note">Only this passage changes.</span>
      </div>
    </form>
  );
}

const QUESTION = "Delete this block from the entry?";

/**
 * His tools for one passage. On a pointer that hovers they stand in the left margin under the hour as a frosted pill
 * that shows on hover or focus (keyboard: Tab reaches them); on touch a "more" button in the passage's running head,
 * or a long press on the words, raises them in a sheet that quotes the passage. Edit opens the editor in place of the
 * words; Copy copies them; Delete asks once, in place, under the words ("Delete this block from the entry?"), then
 * sends the block as it was read. A program's answer has no Delete (the program counts it); offline there is no
 * Delete (the editor says why it cannot save); a page read from the worker's kept copy gets nothing at all.
 * There is no server undo, so there is no Undo: the question in place is the safety, as it was.
 */
export function BlockButtons({ target, onEdit, sheet = false, onSheet }: {
  target: BlockTarget;
  onEdit: () => void;
  /** The touch sheet, open (the passage's long press opens it too). */
  sheet?: boolean;
  onSheet?: (open: boolean) => void;
}) {
  const act = useAction();
  const online = useOnline();
  const stale = useStaleAsOf() !== null;
  const [asking, setAsking] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const focus = (which: "keep" | "trash") => root.current?.querySelector<HTMLElement>(`[data-focus="${which}"]`)?.focus();
  const { stem, block, onChanged, notify } = target;
  const canDelete = !target.keep && online;
  useEffect(() => {
    if (asking) focus("keep");
  }, [asking]);
  const remove = async () => {
    if (!online || stale) return;
    const done = await act.run(() => api.deleteBlock(stem, block.key, { was: block.text, ...(block.at ? { at: block.at } : {}) }));
    if (done) {
      saveDraft(draftKey(stem, block.key), "");
      notify?.("The passage is gone from the entry.");
      onChanged();
    }
  };
  const copy = () => {
    const done = navigator.clipboard?.writeText(block.text);
    if (!done) return notify?.("This browser won't copy from here.");
    void done.then(() => notify?.("Copied your words."), () => notify?.("This browser won't copy from here."));
  };
  const fromSheet = (then: () => void) => {
    onSheet?.(false);
    then();
  };
  // A document read from the worker's kept copy is not what is in the file now: nothing is offered on it (a delete
  // there would be a guess about a file nobody has read), and the editor says so if one was already open.
  if (stale) return null;
  return (
    <div class="block-actions" ref={root}>
      <div class="block-tools" role="toolbar" aria-label={`This passage${block.at ? `, ${block.at}` : ""}`}>
        <IconButton label="Edit" onClick={onEdit} disabled={act.busy}>
          <Pen /><span class="visually-hidden">Edit</span>
        </IconButton>
        <IconButton label="Copy" onClick={copy}>
          <Copy /><span class="visually-hidden">Copy</span>
        </IconButton>
        {canDelete && (
          <IconButton data-focus="trash" label="Delete" onClick={() => setAsking(true)} disabled={act.busy} pressed={asking || undefined}>
            <Trash /><span class="visually-hidden">Delete</span>
          </IconButton>
        )}
      </div>
      <IconButton class="block-more" label="More for this passage" onClick={() => onSheet?.(true)}>
        <More />
      </IconButton>
      {(asking || act.error || !online) && (
        <div class="block-status">
          {!online && <p class="offline-note" role="status">{OFFLINE_WRITE}</p>}
          {asking && (
            <span class="confirm" role="group" aria-label={QUESTION}>
              <span>{QUESTION}</span>
              <Button variant="danger" size="small" onClick={() => { setAsking(false); void remove(); }}>
                Delete
              </Button>
              <Button data-focus="keep" variant="quiet" size="small" onClick={() => { setAsking(false); focus("trash"); }}>
                Keep
              </Button>
            </span>
          )}
          {act.error &&
            (act.error.status === 409 ? (
              <div class="error-line" role="alert">
                <p>{act.error.detail}</p>
                <Button size="small" onClick={() => { act.clear(); onChanged(); }}>
                  Reload the entry
                </Button>
              </div>
            ) : (
              <ErrorLine error={act.error} />
            ))}
        </div>
      )}
      {sheet && (
        <Sheet title="This passage" onClose={() => onSheet?.(false)} class="block-sheet">
          <blockquote class="block-sheet-quote">
            <small>Your words{block.at ? ` · ${block.at}` : ""}</small>
            <span class="read">{block.text}</span>
          </blockquote>
          <div class="block-sheet-actions">
            <Button wide icon={<Pen />} onClick={() => fromSheet(onEdit)}>Edit these words</Button>
            <Button wide icon={<Copy />} onClick={() => fromSheet(copy)}>Copy</Button>
            {canDelete && <Button wide icon={<Trash />} onClick={() => fromSheet(() => setAsking(true))}>Delete…</Button>}
            {!online && <p class="offline-note" role="status">{OFFLINE_WRITE}</p>}
          </div>
        </Sheet>
      )}
    </div>
  );
}
