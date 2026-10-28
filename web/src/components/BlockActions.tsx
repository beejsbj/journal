import { useState } from "preact/hooks";
import { api } from "../api";
import { loadDraft, saveDraft } from "../drafts";
import { useAction } from "../hooks";
import { OFFLINE_WRITE, useOnline, useStaleAsOf } from "../offline";
import type { Block } from "../types";
import { ConfirmButton } from "./ConfirmButton";
import { ErrorLine } from "./Status";

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
            <button
              type="button"
              class="button"
              onClick={() => {
                onClose();
                onChanged();
              }}
            >
              Reload the entry
            </button>
          </div>
        ) : (
          <ErrorLine error={act.error} {...(act.error.status === 422 || act.error.status === 409 ? {} : { onRetry: () => void save() })} />
        ))}
      <div class="composer-actions">
        <button type="button" class="button" onClick={cancel} disabled={act.busy}>
          Cancel
        </button>
        <button type="submit" class="button primary" disabled={!changed || act.busy || !online || stale}>
          {act.busy ? "Saving…" : "Save"}
        </button>
      </div>
    </form>
  );
}

/** Edit and Delete under a block of his. Delete asks once, in place. */
export function BlockButtons({ target, onEdit }: { target: BlockTarget; onEdit: () => void }) {
  const act = useAction();
  const online = useOnline();
  const stale = useStaleAsOf() !== null;
  const { stem, block, onChanged } = target;
  const remove = async () => {
    if (!online || stale) return;
    const done = await act.run(() => api.deleteBlock(stem, block.key, { was: block.text, ...(block.at ? { at: block.at } : {}) }));
    if (done) {
      saveDraft(draftKey(stem, block.key), "");
      onChanged();
    }
  };
  // A document read from the worker's kept copy is not what is in the file now: nothing is offered on it (a delete
  // there would be a guess about a file nobody has read), and the editor says so if one was already open.
  if (stale) return null;
  return (
    <div class="block-actions">
      <button type="button" class="link" onClick={onEdit} disabled={act.busy}>
        Edit
      </button>
      {!online && <p class="offline-note" role="status">{OFFLINE_WRITE}</p>}
      {!target.keep && online && (
        <ConfirmButton
          question="Delete this block from the entry?"
          yes="Delete"
          disabled={act.busy}
          onConfirm={() => void remove()}
          plain
        >
          Delete
        </ConfirmButton>
      )}
      {act.error &&
        (act.error.status === 409 ? (
          <div class="error-line" role="alert">
            <p>{act.error.detail}</p>
            <button type="button" class="button" onClick={() => { act.clear(); onChanged(); }}>
              Reload the entry
            </button>
          </div>
        ) : (
          <ErrorLine error={act.error} />
        ))}
    </div>
  );
}
