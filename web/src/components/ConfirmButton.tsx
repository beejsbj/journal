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
  // Keep (or Escape) gives focus back to the button the question stood in for, never to the page's <body>
  const back = useRef(false);
  const wrap = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (asking) keep.current?.focus({ preventScroll: true });
    else if (back.current) {
      back.current = false;
      wrap.current?.querySelector<HTMLElement>("button")?.focus({ preventScroll: true });
    }
  }, [asking]);
  const keepIt = () => {
    back.current = true;
    setAsking(false);
  };
  if (!asking) {
    return (
      <span class="confirm-wrap" ref={wrap}>
        {props.plain ? (
          <TextButton disabled={props.disabled} onClick={() => setAsking(true)}>
            {props.children}
          </TextButton>
        ) : (
          <Button size={props.size} disabled={props.disabled} onClick={() => setAsking(true)}>
            {props.children}
          </Button>
        )}
      </span>
    );
  }
  return (
    <span class="confirm" role="group" aria-label={props.question}
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.preventDefault();
          e.stopPropagation();
          keepIt();
        }
      }}>
      <span class="confirm-q">{props.question}</span>
      <Button variant="danger" size={props.size} onClick={() => {
        setAsking(false);
        props.onConfirm();
      }}>
        {props.yes}
      </Button>
      <button ref={keep} type="button" class={`button quiet${props.size === "small" ? " small" : ""}`} onClick={keepIt}>
        Keep
      </button>
    </span>
  );
}
