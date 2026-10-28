import { useEffect, useRef, useState } from "preact/hooks";
import { uploadAudio, type ApiError, type Words } from "../api";
import { loadDraft, newClientId, saveDraft } from "../drafts";
import { asApiError } from "../hooks";
import { OFFLINE_WRITE, useOnline } from "../offline";
import { duration } from "../format";
import { Recorder } from "./Recorder";
import { ErrorLine } from "./Status";

/**
 * The place his words are added: text, a voice note, or both. It only appends; changing a block he already wrote is
 * `BlockActions` (Decision 4, amended 2026-10-20). What is typed is kept in `sessionStorage` until it is sent; a write that fails stays in the box with its
 * error and a Retry, and a retry is the same write (`client_id`), so a lost answer cannot make two blocks. A
 * recording is uploaded first and named in the post; if the post fails the upload is reused, not sent again.
 */
/** The server forgets an upload nobody claimed after an hour; one older than this is uploaded again, not trusted. */
const UPLOAD_FRESH_MS = 50 * 60 * 1000;

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

export function Composer(props: ComposerProps) {
  const online = useOnline();
  const [text, setText] = useState(() => loadDraft(props.draftKey));
  const [clip, setClip] = useState<{ blob: Blob; seconds: number; url: string | null } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const clientId = useRef<string | null>(null);
  const uploaded = useRef<{ id: string; at: number } | null>(null);
  // what was in the box when the box moved to another place, until he puts it back or lets it go
  const [kept, setKept] = useState<{ text: string; clip: { blob: Blob; seconds: number } | null } | null>(null);

  // another box (another entry, another phase): its own draft, and no leftovers of the last one's write. Words he had
  // typed and not sent are not dropped with the old box (a settle can move the run on while he is still typing):
  // they are kept aside and offered back.
  const key = useRef(props.draftKey);
  useEffect(() => {
    if (key.current === props.draftKey) return;
    key.current = props.draftKey;
    setKept(text.trim() !== "" || clip ? { text, clip: clip ? { blob: clip.blob, seconds: clip.seconds } : null } : null);
    setText(loadDraft(props.draftKey));
    setClip(null);
    setError(null);
    clientId.current = null;
    uploaded.current = null;
  }, [props.draftKey]);

  useEffect(() => () => {
    if (clip?.url && typeof URL.revokeObjectURL === "function") URL.revokeObjectURL(clip.url);
  }, [clip]);

  const edit = (value: string) => {
    setText(value);
    saveDraft(props.draftKey, value);
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
        return props.onSubmit(words);
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
      saveDraft(props.draftKey, "");
      setText("");
      setClip(null);
      clientId.current = null;
      uploaded.current = null;
      props.onDone?.();
    } catch (e) {
      setError(asApiError(e));   // the text, the recording and the id stay: Retry is the same write
    } finally {
      setBusy(false);
    }
  };

  if (props.closed) return <p class="composer-closed">{props.closed}</p>;

  return (
    <>
    {kept && (
      <div class="composer-kept" role="status">
        <p>This moved on while you were writing; your unsent {kept.clip ? "words and recording are" : "words are"} kept.</p>
        <button type="button" class="button" onClick={restore}>
          Put them back in the box
        </button>{" "}
        <button type="button" class="link" onClick={() => setKept(null)}>
          Let them go
        </button>
      </div>
    )}
    <form
      class="composer"
      onSubmit={(e) => {
        e.preventDefault();
        void send();
      }}
    >
      <label class="visually-hidden" for={`c-${props.draftKey}`}>
        {props.label}
      </label>
      <textarea
        id={`c-${props.draftKey}`}
        rows={4}
        value={text}
        placeholder={props.placeholder ?? "Write here"}
        disabled={busy}
        onInput={(e) => edit((e.currentTarget as HTMLTextAreaElement).value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
            e.preventDefault();
            void send();
          }
        }}
      />
      {clip && (
        <div class="clip">
          {clip.url && <audio controls src={clip.url} preload="metadata" />}
          <span class="hint">Voice note · {duration(clip.seconds)}</span>
          <button type="button" class="link" onClick={drop} disabled={busy}>
            Discard
          </button>
        </div>
      )}
      {!online && (
        <p class="offline-note" role="status">
          {OFFLINE_WRITE} What you write stays here{clip ? ", recording included" : ""}.
        </p>
      )}
      {error && <ErrorLine error={error} {...(error.status === 422 ? {} : { onRetry: () => void send() })} />}
      <div class="composer-actions">
        {props.voice !== false && !clip && <Recorder onRecorded={record} disabled={busy} />}
        <button type="submit" class="button primary" disabled={!has || busy}>
          {busy ? "Saving…" : (props.submitLabel ?? "Add")}
        </button>
      </div>
    </form>
    </>
  );
}
