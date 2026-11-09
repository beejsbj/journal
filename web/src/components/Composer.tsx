import { useEffect, useRef, useState } from "preact/hooks";
import { uploadAudio, type ApiError, type Words } from "../api";
import { loadDraft, newClientId, saveDraft } from "../drafts";
import { asApiError } from "../hooks";
import { OFFLINE_WRITE, useOnline } from "../offline";
import { duration } from "../format";
import { Button, ErrorLine, TextButton } from "../ui";
import { Recorder } from "./Recorder";
import "./Composer.css";

/**
 * The place his words are added: text, a voice note, or both. It only appends; changing a block he already wrote is
 * `BlockActions` (Decision 4, amended 2026-10-20). What is typed is kept in `sessionStorage` until it is sent; a write that fails stays in the box with its
 * error and a Retry, and a retry is the same write (`client_id`), so a lost answer cannot make two blocks. A
 * recording is uploaded first and named in the post; if the post fails the upload is reused, not sent again.
 *
 * The rules live in `useCompose`, which the inline box below and the write room (WriteRoom.tsx) both use: one set of
 * drafts, retries, uploads and offline lines, two ways of laying them out.
 */
/** The server forgets an upload nobody claimed after an hour; one older than this is uploaded again, not trusted. */
const UPLOAD_FRESH_MS = 50 * 60 * 1000;

export interface Clip {
  blob: Blob;
  seconds: number;
  url: string | null;
}

export interface Compose {
  online: boolean;
  text: string;
  edit: (value: string) => void;
  clip: Clip | null;
  record: (blob: Blob, seconds: number) => void;
  drop: () => void;
  busy: boolean;
  error: ApiError | null;
  /** Something to send: words or a recording. */
  has: boolean;
  send: () => Promise<void>;
  /** Words (and a recording) from the box's last place, kept aside when the place changed under them. */
  kept: { text: string; clip: { blob: Blob; seconds: number } | null } | null;
  restore: () => void;
  letGo: () => void;
  /** When the draft was last kept in this tab (null until he types here), and whether the store took it. */
  keptAt: { at: number; stored: boolean } | null;
}

/** The composer's rules, for any layout: draft, retry id, upload reuse, offline, the box moving under his words. */
export function useCompose({ draftKey, onSubmit, onDone }: {
  draftKey: string;
  onSubmit: (words: Words) => Promise<unknown>;
  onDone?: (() => void) | undefined;
}): Compose {
  const online = useOnline();
  const [text, setText] = useState(() => loadDraft(draftKey));
  const [clip, setClip] = useState<Clip | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [keptAt, setKeptAt] = useState<{ at: number; stored: boolean } | null>(null);
  const clientId = useRef<string | null>(null);
  const uploaded = useRef<{ id: string; at: number } | null>(null);
  // what was in the box when the box moved to another place, until he puts it back or lets it go
  const [kept, setKept] = useState<Compose["kept"]>(null);

  // another box (another entry, another phase): its own draft, and no leftovers of the last one's write. Words he had
  // typed and not sent are not dropped with the old box (a settle can move the run on while he is still typing):
  // they are kept aside and offered back.
  const key = useRef(draftKey);
  useEffect(() => {
    if (key.current === draftKey) return;
    key.current = draftKey;
    setKept(text.trim() !== "" || clip ? { text, clip: clip ? { blob: clip.blob, seconds: clip.seconds } : null } : null);
    setText(loadDraft(draftKey));
    setClip(null);
    setError(null);
    setKeptAt(null);
    clientId.current = null;
    uploaded.current = null;
  }, [draftKey]);

  useEffect(() => () => {
    if (clip?.url && typeof URL.revokeObjectURL === "function") URL.revokeObjectURL(clip.url);
  }, [clip]);

  const edit = (value: string) => {
    setText(value);
    setKeptAt({ at: Date.now(), stored: saveDraft(draftKey, value) });
    clientId.current = null;   // changed words are a new write, not the retry of the last
  };

  const record = (blob: Blob, seconds: number) => {
    uploaded.current = null;
    clientId.current = null;
    const url = typeof URL.createObjectURL === "function" ? URL.createObjectURL(blob) : null;
    setClip({ blob, seconds, url });
  };

  const drop = () => {
    uploaded.current = null;
    clientId.current = null;
    setClip(null);
  };

  const has = text.trim() !== "" || clip !== null;

  const restore = () => {
    if (!kept) return;
    edit(text.trim() === "" ? kept.text : `${text.replace(/\s+$/, "")}\n\n${kept.text}`);
    if (kept.clip && !clip) record(kept.clip.blob, kept.clip.seconds);
    setKept(null);
  };

  const send = async () => {
    if (!has || busy) return;
    setBusy(true);
    setError(null);
    try {
      const upload = async () => {
        if (clip) uploaded.current = { id: await uploadAudio(clip.blob), at: Date.now() };
      };
      if (clip && (!uploaded.current || Date.now() - uploaded.current.at > UPLOAD_FRESH_MS)) await upload();
      clientId.current ??= newClientId();
      const post = () => {
        const words: Words = { client_id: clientId.current as string };
        if (text.trim() !== "") words.text = text;
        if (uploaded.current) words.upload = uploaded.current.id;
        return onSubmit(words);
      };
      try {
        await post();
      } catch (e) {
        // the kept recording went stale (the server drops an unclaimed upload after an hour): send the same blob
        // again under the same write id, so the recording is not lost and Retry does not fail for ever
        const err = asApiError(e);
        if (!(clip && err.status === 404 && err.slug === "not-found" && /upload/.test(err.detail))) throw e;
        await upload();
        await post();
      }
      saveDraft(draftKey, "");
      setText("");
      setClip(null);
      setKeptAt(null);
      clientId.current = null;
      uploaded.current = null;
      onDone?.();
    } catch (e) {
      setError(asApiError(e));   // the text, the recording and the id stay: Retry is the same write
    } finally {
      setBusy(false);
    }
  };

  return { online, text, edit, clip, record, drop, busy, error, has, send, kept, restore, letGo: () => setKept(null), keptAt };
}

/** The offline line under a box: the write will fail, and what he wrote stays here. */
export function OfflineNote({ clip }: { clip: boolean }) {
  return (
    <p class="offline-note" role="status">
      {OFFLINE_WRITE} What you write stays here{clip ? ", recording included" : ""}.
    </p>
  );
}

/** Words kept aside when the box moved on under them: put them back, or let them go. */
export function KeptWords({ c }: { c: Compose }) {
  if (!c.kept) return null;
  return (
    <div class="composer-kept" role="status">
      <p>This moved on while you were writing; your unsent {c.kept.clip ? "words and recording are" : "words are"} kept.</p>
      <div class="composer-kept-actions">
        <Button size="small" onClick={c.restore}>
          Put them back in the box
        </Button>
        <TextButton onClick={c.letGo}>Let them go</TextButton>
      </div>
    </div>
  );
}

/** A recording waiting to be sent: hear it, see how long, or discard it. */
export function ClipLine({ c }: { c: Compose }) {
  if (!c.clip) return null;
  return (
    <div class="clip">
      {c.clip.url && <audio controls src={c.clip.url} preload="metadata" />}
      <span class="hint">Voice note · {duration(c.clip.seconds)}</span>
      <TextButton onClick={c.drop} disabled={c.busy}>
        Discard
      </TextButton>
    </div>
  );
}

export interface ComposerProps {
  draftKey: string;
  label: string;
  placeholder?: string;
  submitLabel?: string;
  voice?: boolean;
  /** Why nothing can be written now (shown in place of the box). */
  closed?: string | undefined;
  onSubmit: (words: Words) => Promise<unknown>;
  onDone?: () => void;
}

/** The inline box: at the foot of an entry, under a program's phase. His words on a ruled line, Voice and the send. */
export function Composer(props: ComposerProps) {
  const c = useCompose(props);
  if (props.closed) return <p class="composer-closed">{props.closed}</p>;
  return (
    <>
      <KeptWords c={c} />
      <form
        class="composer"
        onSubmit={(e) => {
          e.preventDefault();
          void c.send();
        }}
      >
        <label class="visually-hidden" for={`c-${props.draftKey}`}>
          {props.label}
        </label>
        <textarea
          id={`c-${props.draftKey}`}
          class="composer-box"
          rows={4}
          value={c.text}
          placeholder={props.placeholder ?? "Write here"}
          disabled={c.busy}
          onInput={(e) => c.edit((e.currentTarget as HTMLTextAreaElement).value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
              e.preventDefault();
              void c.send();
            }
          }}
        />
        <ClipLine c={c} />
        {!c.online && <OfflineNote clip={!!c.clip} />}
        {c.error && <ErrorLine error={c.error} {...(c.error.status === 422 ? {} : { onRetry: () => void c.send() })} />}
        <div class="composer-actions">
          {props.voice !== false && !c.clip && <Recorder onRecorded={c.record} disabled={c.busy} />}
          <Button type="submit" variant="primary" disabled={!c.has || c.busy}>
            {c.busy ? "Saving…" : (props.submitLabel ?? "Add")}
          </Button>
        </div>
      </form>
    </>
  );
}
