/**
 * Something folded that unfolds in place: her readings at the end of an entry, the week's review, a day's slip, the
 * health checks, the questions under her letter. The head is a button (aria-expanded, Enter and Space); the body grows
 * from nothing to its height on the soft spring (grid rows 0fr to 1fr) and its content lifts, sharpens and fades in a
 * beat behind, so nothing jumps. A one-line `peek` in the head fades as the body arrives (the body now says it).
 * Closed, the body is hidden and inert: out of the tab order and the accessibility tree. Controlled (`open` and
 * `onToggle`) or not (`defaultOpen`).
 */
import type { ComponentChildren } from "preact";
import { useId, useState } from "preact/hooks";
import { ChevronDown } from "../icons";
import "./Disclosure.css";

export function Disclosure({ head, peek, icon, open, defaultOpen = false, onToggle, children, class: cls, id, headClass }: {
  head: ComponentChildren;
  peek?: ComponentChildren;
  /** Drawn before the head (her reading's envelope). */
  icon?: ComponentChildren;
  open?: boolean;
  defaultOpen?: boolean;
  onToggle?: (open: boolean) => void;
  children: ComponentChildren;
  class?: string;
  id?: string;
  headClass?: string;
}) {
  const [own, setOwn] = useState(defaultOpen);
  const shown = open ?? own;
  const body = `${useId()}-body`;
  const toggle = () => {
    setOwn(!shown);
    onToggle?.(!shown);
  };
  return (
    <div id={id} class={["disclosure", cls].filter(Boolean).join(" ")} data-open={shown ? "true" : "false"}>
      <button type="button" class={["disclosure-head", headClass].filter(Boolean).join(" ")} aria-expanded={shown} aria-controls={body} onClick={toggle}>
        {icon}
        <span class="disclosure-title">
          {head}
          {peek && <span class="disclosure-peek">{peek}</span>}
        </span>
        <span class="disclosure-chevron" aria-hidden="true"><ChevronDown /></span>
      </button>
      <div class="disclosure-body" id={body} {...(shown ? {} : { inert: true })}>
        <div class="disclosure-inner">
          <div class="disclosure-content">{children}</div>
        </div>
      </div>
    </div>
  );
}
