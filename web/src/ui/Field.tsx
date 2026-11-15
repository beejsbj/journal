/**
 * A labelled control with its hint and its error: Field wraps any control; TextField and TextArea are the common two,
 * wired up (label `for`, `aria-describedby` to the hint and the error, `aria-invalid`). The interface's fields are
 * Instrument Sans in a quiet box; `voice="his"` sets his words in the book's roman on a ruled line, as his page is.
 * design/DESIGN.md, "Forms".
 */
import type { ComponentChildren, InputHTMLAttributes, TextareaHTMLAttributes } from "preact";
import "./Field.css";

interface Labelled {
  id: string;
  label: ComponentChildren;
  /** Read by a screen reader but not shown (a composer whose page says what it is). */
  hideLabel?: boolean;
  hint?: ComponentChildren;
  error?: ComponentChildren;
  class?: string;
}

/** The ids a control is described by: its hint, then its error. */
export function describedBy(id: string, hint?: unknown, error?: unknown): string | undefined {
  return [hint ? `${id}-hint` : "", error ? `${id}-error` : ""].filter(Boolean).join(" ") || undefined;
}

export function Field({ id, label, hideLabel, hint, error, class: cls, children }: Labelled & { children: ComponentChildren }) {
  return (
    <div class={["text-field", error ? "invalid" : "", cls].filter(Boolean).join(" ")}>
      <label class={hideLabel ? "visually-hidden" : "field-label"} for={id}>{label}</label>
      {children}
      {hint && <p class="field-hint" id={`${id}-hint`}>{hint}</p>}
      {error && <p class="field-error" id={`${id}-error`} role="alert">{error}</p>}
    </div>
  );
}

type Voice = "ui" | "his";
type InputAttrs = Omit<InputHTMLAttributes<HTMLInputElement>, "id" | "label" | "class" | "className">;
type AreaAttrs = Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, "id" | "label" | "class" | "className">;

export function TextField({ id, label, hideLabel, hint, error, class: cls, voice = "ui", type = "text", ...input }: Labelled & InputAttrs & { voice?: Voice }) {
  return (
    <Field id={id} label={label} hideLabel={hideLabel} hint={hint} error={error} class={cls}>
      <input id={id} type={type} class={`input ${voice}`} aria-describedby={describedBy(id, hint, error)} aria-invalid={error ? true : undefined} {...input} />
    </Field>
  );
}

export function TextArea({ id, label, hideLabel, hint, error, class: cls, voice = "ui", ...area }: Labelled & AreaAttrs & { voice?: Voice }) {
  return (
    <Field id={id} label={label} hideLabel={hideLabel} hint={hint} error={error} class={cls}>
      <textarea id={id} class={`input area ${voice}`} aria-describedby={describedBy(id, hint, error)} aria-invalid={error ? true : undefined} {...area} />
    </Field>
  );
}
