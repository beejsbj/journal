/**
 * His actions. `primary` is the lamp (one per view: Save, Send, Answer); `secondary` an outlined button; `quiet` text
 * on hover-paper (Cancel, Later, Discard); `danger` asks for what cannot be undone with one tap: the lamp too (status
 * has no hue; the in-place question carries the warning). Every button is at least 44 px on touch and presses in.
 * Her ink is never used for his controls. design/DESIGN.md, "Components".
 */
import type { AnchorHTMLAttributes, ButtonHTMLAttributes, ComponentChildren } from "preact";
import "./Button.css";

export type ButtonVariant = "primary" | "secondary" | "quiet" | "danger";
export type ButtonSize = "small" | "medium";

interface Look {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** Fills its row (a phone's sheet, a form's foot). */
  wide?: boolean;
  /** An icon before the label (decorative: the label names the action). */
  icon?: ComponentChildren;
  class?: string;
  children?: ComponentChildren;
}

/** The classes a button of this look carries: `button` plus its variant and size (the old sheet used the same names). */
export function buttonClass({ variant = "secondary", size = "medium", wide, class: cls }: Look): string {
  return ["button", variant !== "secondary" && variant, size === "small" && "small", wide && "wide", cls].filter(Boolean).join(" ");
}

type ButtonAttrs = Omit<ButtonHTMLAttributes<HTMLButtonElement>, "size" | "icon" | "class" | "className">;

export function Button({ variant, size, wide, icon, class: cls, children, type = "button", ...rest }: Look & ButtonAttrs) {
  return (
    <button type={type} class={buttonClass({ variant, size, wide, class: cls })} {...rest}>
      {icon}
      {children}
    </button>
  );
}

type AnchorAttrs = Omit<AnchorHTMLAttributes<HTMLAnchorElement>, "size" | "icon" | "class" | "className">;

/** A link that looks like a button: it goes somewhere (Open the entry, Continue). */
export function ButtonLink({ variant, size, wide, icon, class: cls, children, ...rest }: Look & AnchorAttrs & { href: string }) {
  return (
    <a class={buttonClass({ variant, size, wide, class: cls })} {...rest}>
      {icon}
      {children}
    </a>
  );
}

/** An action set as words in a line ("Show all", "Use default"): underlined, still 44 px tall on touch. */
export function TextButton({ class: cls, children, type = "button", ...rest }: { class?: string; children?: ComponentChildren } & ButtonAttrs) {
  return (
    <button type={type} class={["link", cls].filter(Boolean).join(" ")} {...rest}>
      {children}
    </button>
  );
}

/**
 * A control that is only an icon. `label` is its accessible name (and its tooltip on a pointer); `pressed` makes it a
 * toggle (aria-pressed). 40 px by default, 44 on touch.
 */
export function IconButton({ label, pressed, size = "medium", class: cls, children, type = "button", ...rest }:
  { label: string; pressed?: boolean; size?: ButtonSize; class?: string; children: ComponentChildren } & Omit<ButtonAttrs, "aria-label" | "aria-pressed">) {
  return (
    <button type={type} class={["icon-button", size === "small" && "small", cls].filter(Boolean).join(" ")} aria-label={label}
      title={label} aria-pressed={pressed === undefined ? undefined : pressed} {...rest}>
      {children}
    </button>
  );
}
