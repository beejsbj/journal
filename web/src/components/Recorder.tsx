import { useEffect, useRef, useState } from "preact/hooks";
import { duration } from "../format";
import { Mic } from "../icons";

/** The recording types a browser may offer, best first; the first it can record. Safari records `audio/mp4`. */
const TYPES = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"];

export function recordingType(): string | undefined {
  if (typeof MediaRecorder === "undefined" || typeof MediaRecorder.isTypeSupported !== "function") return undefined;
  return TYPES.find((t) => MediaRecorder.isTypeSupported(t));
}

/** Whether this page can record at all: it needs `MediaRecorder` and a secure page (`getUserMedia`). */
export function canRecord(): boolean {
  return typeof MediaRecorder !== "undefined" && typeof navigator !== "undefined" && !!navigator.mediaDevices?.getUserMedia;
}

/**
 * Record from the microphone. It owns only the recording: when stopped it hands the audio to `onRecorded`, and what
 * to do with it (upload, post) is the composer's. The microphone is released the moment recording stops, and when
 * the component goes away.
 */
export function Recorder({ onRecorded, disabled }: { onRecorded: (blob: Blob, seconds: number) => void; disabled?: boolean }) {
  const [recording, setRecording] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [problem, setProblem] = useState<string | null>(null);
  const media = useRef<{ recorder: MediaRecorder; stream: MediaStream; chunks: Blob[]; started: number } | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const handoff = useRef(onRecorded);
  handoff.current = onRecorded;

  const release = () => {
    if (timer.current) clearInterval(timer.current);
    timer.current = null;
    media.current?.stream.getTracks().forEach((t) => t.stop());
  };
  useEffect(
    () => () => {
      const m = media.current;
      media.current = null;
      if (m && m.recorder.state !== "inactive") {
        m.recorder.onstop = null;   // going away: nothing to hand over
        m.recorder.stop();
      }
      m?.stream.getTracks().forEach((t) => t.stop());
      if (timer.current) clearInterval(timer.current);
    },
    [],
  );

  if (!canRecord()) {
    return (
      <p class="hint">Recording needs a secure page and a microphone, and this browser did not offer one.</p>
    );
  }

  const start = async () => {
    setProblem(null);
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
      const denied = e instanceof DOMException && (e.name === "NotAllowedError" || e.name === "SecurityError");
      setProblem(denied ? "The microphone is blocked for this page. Allow it in the browser's site settings." : "No microphone could be opened.");
      return;
    }
    const type = recordingType();
    let recorder: MediaRecorder;
    try {
      recorder = new MediaRecorder(stream, type ? { mimeType: type } : undefined);
    } catch {
      stream.getTracks().forEach((t) => t.stop());
      setProblem("This browser cannot record audio.");
      return;
    }
    const chunks: Blob[] = [];
    recorder.ondataavailable = (e) => {
      if (e.data && e.data.size > 0) chunks.push(e.data);
    };
    recorder.onstop = () => {
      const took = Math.round((Date.now() - (media.current?.started ?? Date.now())) / 1000);
      release();
      media.current = null;
      setRecording(false);
      if (chunks.length > 0) handoff.current(new Blob(chunks, { type: recorder.mimeType || type || "audio/webm" }), took);
    };
    media.current = { recorder, stream, chunks, started: Date.now() };
    recorder.start();
    setSeconds(0);
    setRecording(true);
    timer.current = setInterval(() => setSeconds((n) => n + 1), 1000);
  };

  const stop = () => {
    const m = media.current;
    if (m && m.recorder.state !== "inactive") m.recorder.stop();
  };

  return (
    <div class="recorder">
      {recording ? (
        <button type="button" class="button recording" onClick={stop} aria-label="Stop recording">
          <span class="rec-dot" aria-hidden="true" /> Stop · <time>{duration(seconds)}</time>
        </button>
      ) : (
        <button type="button" class="button" onClick={() => void start()} disabled={disabled} aria-label="Record a voice note">
          <Mic /> Voice
        </button>
      )}
      {problem && (
        <p class="hint" role="alert">
          {problem}
        </p>
      )}
    </div>
  );
}
