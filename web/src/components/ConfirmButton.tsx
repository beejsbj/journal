import type { ComponentChildren } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { Button, TextButton } from "../ui";
import "./ConfirmButton.css";

/**
 * A button that asks once, in place, before doing what cannot be taken back with one tap ("Abandon this run? Abandon ·
 * Keep"). The question takes the button's place and the yes is the lamp (danger: status has no hue, the words carry
 * the warning); Keep, or Escape, puts the button back.
 */
export function ConfirmButton(props: {
  children: ComponentChildren;
  question: string;
  yes: string;
  onConfirm: () => void;
  disabled?: boolean;
  /** The first button reads as a link (a small action under some text), not as a button. */
  plain?: boolean;
  size?: "small" | "medium";
}) {
  const [asking, setAsking] = useState(false);
  const keep = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (asking) keep.current?.focus({ preventScroll: true });
  }, [asking]);
  if (!asking) {
    return props.plain ? (
      <TextButton disabled={props.disabled} onClick={() => setAsking(true)}>
        {props.children}
      </TextButton>
    ) : (
      <Button size={props.size} disabled={props.disabled} onClick={() => setAsking(true)}>
        {props.children}
      </Button>
    );
  }
  return (
    <span class="confirm" role="group" aria-label={props.question}
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.preventDefault();
          e.stopPropagation();
          setAsking(false);
        }
      }}>
      <span class="confirm-q">{props.question}</span>
      <Button variant="danger" size={props.size} onClick={() => {
        setAsking(false);
        props.onConfirm();
      }}>
        {props.yes}
      </Button>
      <button ref={keep} type="button" class={`button quiet${props.size === "small" ? " small" : ""}`} onClick={() => setAsking(false)}>
        Keep
      </button>
    </span>
  );
}
