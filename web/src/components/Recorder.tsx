import { useEffect, useRef, useState } from "preact/hooks";
import { duration } from "../format";
import { Mic, Stop } from "../icons";
import { reducedMotion } from "../ui";
import "./Recorder.css";

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

type AudioCtx = typeof AudioContext;

/**
 * How loud the microphone is, 0-1, written to `--lvl` on `el` every frame while it records: the orb's ring swells
 * with his voice. Only where the browser offers an AudioContext and motion is allowed; the returned function stops it.
 */
function listen(stream: MediaStream, el: HTMLElement | null): () => void {
  const Ctx = (globalThis as { AudioContext?: AudioCtx; webkitAudioContext?: AudioCtx }).AudioContext
    ?? (globalThis as { webkitAudioContext?: AudioCtx }).webkitAudioContext;
  if (!Ctx || !el || reducedMotion()) return () => {};
  let ctx: AudioContext;
  try {
    ctx = new Ctx();
  } catch {
    return () => {};
  }
  const analyser = ctx.createAnalyser();
  analyser.fftSize = 256;
  ctx.createMediaStreamSource(stream).connect(analyser);
  const data = new Uint8Array(analyser.fftSize);
  let lvl = 0;
  let raf = 0;
  const loop = () => {
    analyser.getByteTimeDomainData(data);
    let peak = 0;
    for (const v of data) peak = Math.max(peak, Math.abs(v - 128));
    lvl += (Math.min(1, peak / 64) - lvl) * 0.2;
    el.style.setProperty("--lvl", lvl.toFixed(3));
    raf = requestAnimationFrame(loop);
  };
  raf = requestAnimationFrame(loop);
  return () => {
    cancelAnimationFrame(raf);
    el.style.removeProperty("--lvl");
    void ctx.close().catch(() => {});
  };
}

/**
 * Record from the microphone. It owns only the recording: when stopped it hands the audio to `onRecorded`, and what
 * to do with it (upload, post) is the composer's. The microphone is released the moment recording stops, and when
 * the component goes away.
 *
 * Two looks: a quiet "Voice" button beside the inline composer's send, and (`orb`) the write room's breathing orb,
 * which swells with his voice while it records and says how long. `start` begins at once (the room's "Speak it").
 */
export function Recorder({ onRecorded, disabled, orb, start: startNow, onRecording }: {
  onRecorded: (blob: Blob, seconds: number) => void;
  disabled?: boolean;
  orb?: boolean;
  start?: boolean;
  /** Told when recording starts and stops, with the seconds so far (the room shows them in its dock). */
  onRecording?: (recording: boolean, seconds: number) => void;
}) {
  const [recording, setRecording] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [problem, setProblem] = useState<string | null>(null);
  const media = useRef<{ recorder: MediaRecorder; stream: MediaStream; chunks: Blob[]; started: number; quiet: () => void } | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const wrap = useRef<HTMLDivElement>(null);
  const handoff = useRef(onRecorded);
  handoff.current = onRecorded;
  const told = useRef(onRecording);
  told.current = onRecording;

  const release = () => {
    if (timer.current) clearInterval(timer.current);
    timer.current = null;
    media.current?.quiet();
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
      m?.quiet();
      m?.stream.getTracks().forEach((t) => t.stop());
      if (timer.current) clearInterval(timer.current);
    },
    [],
  );

  useEffect(() => {
    told.current?.(recording, seconds);
  }, [recording, seconds]);

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
    media.current = { recorder, stream, chunks, started: Date.now(), quiet: orb ? listen(stream, wrap.current) : () => {} };
    recorder.start();
    setSeconds(0);
    setRecording(true);
    timer.current = setInterval(() => setSeconds((n) => n + 1), 1000);
  };

  const stop = () => {
    const m = media.current;
    if (m && m.recorder.state !== "inactive") m.recorder.stop();
  };

  // "Speak it": begin as the room opens (once)
  const began = useRef(false);
  useEffect(() => {
    if (!startNow || began.current || !canRecord()) return;
    began.current = true;
    void start();
  }, [startNow]);

  if (!canRecord()) {
    return (
      <p class="hint">Recording needs a secure page and a microphone, and this browser did not offer one.</p>
    );
  }

  const trouble = problem && (
    <p class="hint recorder-problem" role="alert">
      {problem}
    </p>
  );

  if (orb) {
    return (
      <div class={["recorder orb-wrap", recording && "rec"].filter(Boolean).join(" ")} ref={wrap}>
        <span class="orb-ring" aria-hidden="true" />
        <span class="orb-ring halo" aria-hidden="true" />
        {recording ? (
          <button type="button" class="orb recording" onClick={stop} aria-label="Stop recording" title="Stop recording">
            <Stop />
          </button>
        ) : (
          <button type="button" class="orb" onClick={() => void start()} disabled={disabled} aria-label="Record a voice note" title="Record a voice note">
            <Mic />
          </button>
        )}
        {trouble}
      </div>
    );
  }

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
      {trouble}
    </div>
  );
}
