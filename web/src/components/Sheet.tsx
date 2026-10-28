import type { ComponentChildren } from "preact";
import { useEffect, useRef } from "preact/hooks";
import { Close } from "../icons";

/**
 * A modal sheet: a bottom sheet on a phone, a centred panel on a desktop. Escape and the backdrop close it, focus
 * moves into it and goes back to where it came from. No library: a dialog is a `div` with the right roles.
 */
export function Sheet({ title, onClose, children }: { title: string; onClose: () => void; children: ComponentChildren }) {
  const panel = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    panel.current?.focus();
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") close.current();
    };
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("keydown", key);
      before?.focus?.();
    };
  }, []);
  return (
    <div class="sheet-backdrop" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div class="sheet" role="dialog" aria-modal="true" aria-label={title} tabIndex={-1} ref={panel}>
        <header class="sheet-head">
          <h2>{title}</h2>
          <button type="button" class="icon-button" aria-label="Close" onClick={onClose}>
            <Close />
          </button>
        </header>
        {children}
      </div>
    </div>
  );
}
