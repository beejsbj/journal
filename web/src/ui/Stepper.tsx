/**
 * A number with its unit, moved a step at a time (hours a prompt stays open; a time, by 15 minutes). The value is a
 * spinbutton (arrow keys, Page Up/Down by ten steps, Home/End); the ends disable at min and max, and the value nudges
 * when it changes. The default is never a placeholder: DefaultHint says it quietly beside the row's label ("the
 * default", or "default 6 hours · Use default" once moved).
 */
import type { ComponentChildren } from "preact";
import { useRef } from "preact/hooks";
import { Minus, Plus } from "../icons";
import { TextButton } from "./Button";
import "./Stepper.css";

export function Stepper({ label, value, onChange, min = -Infinity, max = Infinity, step = 1, unit, format, disabled, id }: {
  label: string;
  value: number;
  onChange: (next: number) => void;
  min?: number;
  max?: number;
  step?: number;
  unit?: string;
  /** How the value reads (a time of day from minutes, say); the unit follows it. */
  format?: (value: number) => string;
  disabled?: boolean;
  id?: string;
}) {
  const last = useRef(value);
  const dir = value > last.current ? "up" : value < last.current ? "down" : "";
  last.current = value;
  const set = (v: number) => {
    const next = Math.min(max, Math.max(min, Math.round(v / step) * step));
    if (next !== value) onChange(next);
  };
  const text = format ? format(value) : String(value);
  const onKey = (e: KeyboardEvent) => {
    const moves: Record<string, number> = { ArrowUp: step, ArrowRight: step, ArrowDown: -step, ArrowLeft: -step, PageUp: step * 10, PageDown: -step * 10 };
    if (e.key in moves) set(value + (moves[e.key] as number));
    else if (e.key === "Home" && Number.isFinite(min)) set(min);
    else if (e.key === "End" && Number.isFinite(max)) set(max);
    else return;
    e.preventDefault();
  };
  return (
    <div class="num-stepper" role="group" aria-label={label} aria-disabled={disabled ? true : undefined}>
      <button type="button" aria-label={`Less: ${label}`} disabled={disabled || value <= min} onClick={() => set(value - step)}>
        <Minus />
      </button>
      <span id={id} class="num-stepper-value" role="spinbutton" tabIndex={disabled ? -1 : 0} aria-label={label}
        aria-valuenow={value} aria-valuemin={Number.isFinite(min) ? min : undefined} aria-valuemax={Number.isFinite(max) ? max : undefined}
        aria-valuetext={unit ? `${text} ${unit}` : text} onKeyDown={onKey}>
        <span key={value} class={dir ? `nudge-${dir}` : undefined}>{text}</span>
        {unit && <small>{unit}</small>}
      </span>
      <button type="button" aria-label={`More: ${label}`} disabled={disabled || value >= max} onClick={() => set(value + step)}>
        <Plus />
      </button>
    </div>
  );
}

/** "the default", or "default 6 hours" with Use default once the value has moved from it. */
export function DefaultHint({ isDefault, value, onUse }: { isDefault: boolean; value: ComponentChildren; onUse?: () => void }) {
  if (isDefault) return <span class="default-hint"><em>the default</em></span>;
  return (
    <span class="default-hint">
      <em>default {value}</em>
      {onUse && <TextButton onClick={onUse}>Use default</TextButton>}
    </span>
  );
}

/** Minutes after midnight as HH:MM (a time stepper's `format`). */
export const clockTime = (minutes: number): string =>
  `${String(Math.floor(minutes / 60) % 24).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
