/**
 * A modal sheet: a bottom sheet on a phone, a centred panel on a wider screen. It is a native `<dialog>` opened with
 * `showModal()`, so it sits in the top layer (no pane, transform or container clips it) and the browser makes the
 * rest of the page inert. Focus moves into it, Tab cycles inside it, Escape and the scrim close it, and focus goes back
 * where it came from. Where `showModal` is missing (an old browser, the tests' DOM) it opens in place and makes the
 * page around it inert itself. Mount it to open it; it calls `onClose` (after its exit, when it closes itself) and
 * the parent unmounts it.
 */
import type { ComponentChildren } from "preact";
import { useLayoutEffect, useRef, useState } from "preact/hooks";
import { Close } from "../icons";
import { IconButton } from "./Button";
import { reducedMotion } from "./motion";
import "./Sheet.css";

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])';
const EXIT_MS = 220;

/** Make everything but `keep` (and its ancestors) inert; returns the undo. The fallback for a browser without showModal. */
function isolate(keep: HTMLElement): () => void {
  const changed: HTMLElement[] = [];
  for (let el: HTMLElement | null = keep; el && el !== document.body; el = el.parentElement) {
    for (const sib of el.parentElement?.children ?? []) {
      if (sib !== el && sib instanceof HTMLElement && !sib.hasAttribute("inert")) {
        sib.setAttribute("inert", "");
        changed.push(sib);
      }
    }
  }
  return () => changed.forEach((el) => el.removeAttribute("inert"));
}

export function Sheet({ title, onClose, children, class: cls }: {
  title: string;
  onClose: () => void;
  children: ComponentChildren;
  class?: string;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const [leaving, setLeaving] = useState(false);
  const close = useRef(onClose);
  close.current = onClose;
  const leave = useRef(() => {});
  leave.current = () => {
    if (leaving) return;
    if (reducedMotion()) return close.current();
    setLeaving(true);
    setTimeout(() => close.current(), EXIT_MS);
  };

  useLayoutEffect(() => {
    const d = dialog.current;
    if (!d) return;
    const before = document.activeElement as HTMLElement | null;
    let undo = () => {};
    if (typeof d.showModal === "function") {
      try {
        d.showModal();
      } catch {
        d.setAttribute("open", "");
        undo = isolate(d);
      }
    } else {
      d.setAttribute("open", "");
      undo = isolate(d);
    }
    panel.current?.focus();
    return () => {
      undo();
      if (typeof d.close === "function" && d.open) d.close();
      before?.focus?.();
    };
  }, []);

  // in a layout effect, so the keys work from the first moment the sheet is on screen
  useLayoutEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        leave.current();
      } else if (e.key === "Tab" && dialog.current) {
        // the trap: Tab and Shift+Tab go round the sheet's own controls
        const all = [...dialog.current.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((el) => !el.closest("[inert]"));
        if (!all.length) return;
        const first = all[0] as HTMLElement;
        const last = all[all.length - 1] as HTMLElement;
        const at = document.activeElement;
        if (e.shiftKey && (at === first || at === panel.current)) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && at === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", key);
    return () => document.removeEventListener("keydown", key);
  }, []);

  return (
    // the browser's own Escape (the cancel event) is ours: the sheet leaves with its exit, then the parent unmounts it
    <dialog ref={dialog} class={["sheet-layer", leaving && "leaving"].filter(Boolean).join(" ")} role="dialog" aria-modal="true"
      aria-label={title} onCancel={(e) => e.preventDefault()}>
      <div class="sheet-backdrop" aria-hidden="true" onClick={() => leave.current()} />
      <div ref={panel} class={["sheet", cls].filter(Boolean).join(" ")} tabIndex={-1}>
        <header class="sheet-head">
          <h2>{title}</h2>
          <IconButton label="Close" onClick={() => leave.current()}>
            <Close />
          </IconButton>
        </header>
        {children}
      </div>
    </dialog>
  );
}

/** The same thing by its other name: a dialog is a sheet on a wide screen. */
export const Dialog = Sheet;
