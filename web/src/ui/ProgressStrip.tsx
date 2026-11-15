/**
 * A program's way through: its name, "3 of 10", and a strip of segments, the done ones in his ink and the current one
 * filled to where it is. On Today, Insights and the Library row. A progressbar for assistive technology.
 */
import type { ComponentChildren } from "preact";
import "./ProgressStrip.css";

export function ProgressStrip({ title, label, done, total, current, meta, action }: {
  title?: ComponentChildren;
  /** Its accessible name ("Fix your life, sessions"). */
  label: string;
  done: number;
  total: number;
  /** How far into the current segment (0-1). */
  current?: number;
  /** The line beside the title (default "3 of 10"). */
  meta?: ComponentChildren;
  action?: ComponentChildren;
}) {
  const n = Math.max(1, total);
  return (
    <div class={["progress-strip", !title && "bare"].filter(Boolean).join(" ")}>
      {title && <p class="strip-title">{title}</p>}
      {title && <p class="strip-meta">{meta ?? `${done} of ${total}`}</p>}
      {action && <div class="strip-action">{action}</div>}
      <div class="strip-segs" role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={total} aria-valuenow={done}
        aria-valuetext={`${done} of ${total}`} style={{ "--n": n }}>
        {Array.from({ length: n }, (_, i) => (
          <i key={i} class={i < done ? "done" : i === done && current ? "now" : undefined}
            style={{ "--k": i, ...(i === done && current ? { "--f": Math.min(1, Math.max(0, current)) } : {}) }} />
        ))}
      </div>
    </div>
  );
}
