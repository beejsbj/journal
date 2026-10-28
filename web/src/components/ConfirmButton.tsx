import type { ComponentChildren } from "preact";
import { useState } from "preact/hooks";

/** A button that asks once, in place, before doing what cannot be taken back with one tap. */
export function ConfirmButton(props: {
  children: ComponentChildren;
  question: string;
  yes: string;
  onConfirm: () => void;
  disabled?: boolean;
  /** The first button reads as a link (a small action under some text), not as a button. */
  plain?: boolean;
}) {
  const [asking, setAsking] = useState(false);
  if (!asking) {
    return (
      <button type="button" class={props.plain ? "link" : "button"} disabled={props.disabled} onClick={() => setAsking(true)}>
        {props.children}
      </button>
    );
  }
  return (
    <span class="confirm" role="group" aria-label={props.question}>
      <span>{props.question}</span>
      <button
        type="button"
        class="button danger"
        onClick={() => {
          setAsking(false);
          props.onConfirm();
        }}
      >
        {props.yes}
      </button>
      <button type="button" class="button" onClick={() => setAsking(false)}>
        Keep
      </button>
    </span>
  );
}
