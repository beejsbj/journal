/**
 * A number with its unit, moved a step at a time (hours a prompt stays open; a time, by 15 minutes). The value is a
 * spinbutton (arrow keys, Page Up/Down by ten steps, Home/End); the ends disable at min and max, and the value nudges
 * when it changes. The default is never a placeholder: DefaultHint says it quietly beside the row's label ("the
 * default", or "default 6 hours · Use default" once moved).
 *
 * `TypedStepper` is the same face with the value typed in place: for a number the step is too small to reach (a
 * latitude, a minute count), as Settings uses it. It holds what he typed, as a string, valid or not; the form says
 * what is wrong.
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

/** − [typed value] unit +: the value is a text box he can type in, and the steps move it from what is there (or from
 *  `base` while it is empty). `value` is his text as typed; `onInput` gets the new text. */
export function TypedStepper({ label, value, onInput, base = 0, min = -Infinity, above, max = Infinity, step = 1, unit, whole, disabled, invalid, describedBy, id }: {
  label: string;
  value: string;
  onInput: (next: string) => void;
  /** Where the steps start while the box is empty (the default). */
  base?: number;
  min?: number;
  /** An open lower bound: the value must be more than this (a step down that would reach it is not taken). */
  above?: number;
  max?: number;
  step?: number;
  unit?: string;
  /** Whole numbers only: the phone offers digits. */
  whole?: boolean;
  disabled?: boolean;
  invalid?: boolean;
  describedBy?: string;
  id?: string;
}) {
  const n = Number(value.trim());
  const from = value.trim() !== "" && Number.isFinite(n) ? n : base;
  // an open lower bound ("more than 0") lets a step land anywhere above it, and refuses one that would reach it
  const ok = (v: number) => v >= min && v <= max && (above === undefined || v > above);
  const move = (by: number) => {
    let next = Math.round((from + by) * 1000) / 1000;
    if (!ok(next)) next = Math.min(max, Math.max(min, next));
    if (!ok(next) || next === n) return;
    onInput(String(next));
  };
  const onKey = (e: KeyboardEvent) => {
    const by = e.key === "ArrowUp" ? step : e.key === "ArrowDown" ? -step : e.key === "PageUp" ? step * 10 : e.key === "PageDown" ? -step * 10 : 0;
    if (!by) return;
    e.preventDefault();
    move(by);
  };
  const atFloor = from <= min || (above !== undefined && from - step <= above);
  return (
    <div class="num-stepper num-stepper-typed" aria-disabled={disabled ? true : undefined}>
      <button type="button" aria-label={`Less: ${label}`} disabled={disabled || atFloor} onClick={() => move(-step)}><Minus /></button>
      <span class="num-stepper-field">
        <input id={id} class="num-stepper-input" type="text" inputMode={whole ? "numeric" : "decimal"} autocomplete="off"
          role="spinbutton" aria-valuenow={value.trim() !== "" && Number.isFinite(n) ? n : undefined}
          aria-valuemin={Number.isFinite(min) ? min : undefined} aria-valuemax={Number.isFinite(max) ? max : undefined}
          aria-invalid={invalid ? true : undefined} aria-describedby={describedBy} value={value} disabled={disabled}
          onKeyDown={onKey} onInput={(e) => onInput((e.currentTarget as HTMLInputElement).value)} />
        {unit && <small aria-hidden="true">{unit}</small>}
      </span>
      <button type="button" aria-label={`More: ${label}`} disabled={disabled || from >= max} onClick={() => move(step)}><Plus /></button>
    </div>
  );
}
