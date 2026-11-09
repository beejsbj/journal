/**
 * His recording: a lamp play disc, a waveform of bars that fills in his text colour as it plays (tap or drag to
 * seek), the time, and the speed (1×, 1.25×, 1.5×, 2×; hidden on a phone). A real `<audio>` element underneath does
 * the playing, so the browser's own media keys, the network rules (no Range from the worker) and the fallback text
 * stand. The waveform is a slider: arrow keys move 5 s, Page keys 30 s, Home and End. The bars are drawn from the
 * file's name, not its sound: a shape, not a measurement.
 */
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { Pause, Play } from "../icons";
import "./AudioPlayer.css";

const RATES = [1, 1.25, 1.5, 2];

export const clock = (s: number): string => {
  const t = Math.max(0, Math.floor(Number.isFinite(s) ? s : 0));
  return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, "0")}`;
};

/** n bar heights (0.14-1) shaped like speech: louder in the middle, seeded so a file always draws the same. */
export function bars(n: number, seed: string): number[] {
  let s = [...seed].reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 233280, 7);
  const r = () => (s = (s * 9301 + 49297) % 233280) / 233280;
  return Array.from({ length: n }, (_, i) => {
    const env = 0.35 + 0.65 * Math.sin((Math.PI * (i + 0.5)) / n) ** 0.6;
    return Math.max(0.14, Math.min(1, env * (0.35 + r() * 0.75)));
  });
}

export function AudioPlayer({ src, duration, label = "the recording", preload = "none" }: {
  src: string;
  /** Seconds, when known before the file loads (the block's "voice · 2:31"). */
  duration?: number;
  /** What is playing, for the controls' names ("your voice"). */
  label?: string;
  preload?: "none" | "metadata";
}) {
  const audio = useRef<HTMLAudioElement>(null);
  const wave = useRef<HTMLDivElement>(null);
  const [playing, setPlaying] = useState(false);
  const [pos, setPos] = useState(0);
  const [dur, setDur] = useState(duration ?? 0);
  const [rate, setRate] = useState(1);
  const heights = useMemo(() => bars(54, src), [src]);
  useEffect(() => {
    if (audio.current) audio.current.playbackRate = rate;
  }, [rate]);

  const toggle = () => {
    const a = audio.current;
    if (!a) return;
    if (a.paused) void a.play().catch(() => setPlaying(false));
    else a.pause();
  };
  const seek = (t: number) => {
    const a = audio.current;
    const end = dur || a?.duration || 0;
    const next = Math.max(0, Math.min(end || t, t));
    if (a) {
      try { a.currentTime = next; } catch { /* not loaded yet: the position is shown, the file starts there once it can */ }
    }
    setPos(next);
  };
  const seekAt = (clientX: number) => {
    const r = wave.current?.getBoundingClientRect();
    if (!r || !dur) return;
    seek(((clientX - r.left) / r.width) * dur);
  };
  const onKey = (e: KeyboardEvent) => {
    const step: Record<string, number> = { ArrowRight: 5, ArrowUp: 5, ArrowLeft: -5, ArrowDown: -5, PageUp: 30, PageDown: -30 };
    if (e.key in step) seek(pos + (step[e.key] as number));
    else if (e.key === "Home") seek(0);
    else if (e.key === "End") seek(dur);
    else if (e.key === " " || e.key === "Enter") toggle();
    else return;
    e.preventDefault();
  };
  const done = dur ? pos / dur : 0;
  return (
    <div class={["player", playing && "playing"].filter(Boolean).join(" ")}>
      <button type="button" class="player-play" aria-label={playing ? `Pause ${label}` : `Play ${label}`} onClick={toggle}>
        {playing ? <Pause /> : <Play />}
      </button>
      <div ref={wave} class="player-wave" role="slider" tabIndex={0} aria-label={`Position in ${label}`} aria-valuemin={0}
        aria-valuemax={Math.round(dur)} aria-valuenow={Math.round(pos)} aria-valuetext={`${clock(pos)} of ${clock(dur)}`}
        onKeyDown={onKey}
        onPointerDown={(e) => {
          (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
          seekAt(e.clientX);
        }}
        onPointerMove={(e) => {
          if (e.buttons & 1) seekAt(e.clientX);
        }}>
        {heights.map((h, i) => (
          <i key={i} class={(i + 0.5) / heights.length <= done ? "done" : undefined} style={{ height: `${Math.round(h * 100)}%` }} />
        ))}
      </div>
      <span class="player-time"><span>{clock(pos)}</span> / {clock(dur)}</span>
      <button type="button" class="player-rate" aria-label={`Speed: ${rate} times`} onClick={() => setRate(RATES[(RATES.indexOf(rate) + 1) % RATES.length] ?? 1)}>
        {rate}×
      </button>
      <audio ref={audio} src={src} preload={preload}
        onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onEnded={() => setPlaying(false)}
        onTimeUpdate={(e) => setPos((e.currentTarget as HTMLAudioElement).currentTime)}
        onLoadedMetadata={(e) => {
          const d = (e.currentTarget as HTMLAudioElement).duration;
          if (Number.isFinite(d) && d > 0) setDur(d);
        }}>
        Your browser cannot play this recording.
      </audio>
    </div>
  );
}
