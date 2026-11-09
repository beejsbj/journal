/**
 * A bookmark ribbon hanging from the rule above a Library row: solid for a practice he wrote, outlined for a built-in
 * he rewrote. It replaces the old boxed "yours" / "edited" badges; `label` names it for assistive technology when the
 * row does not say it in words.
 */
import "./Ribbon.css";

export function Ribbon({ kind, label }: { kind: "own" | "edited"; label?: string }) {
  return (
    <svg class={`ribbon-mark ${kind}`} viewBox="0 0 12 34" width="12" height="34" role={label ? "img" : undefined}
      aria-label={label} aria-hidden={label ? undefined : "true"} focusable="false">
      <path d="M.75 0h10.5v32.5L6 27.3.75 32.5z" />
    </svg>
  );
}
