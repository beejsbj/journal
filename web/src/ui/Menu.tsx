/**
 * A choice that opens a short list (the filter sentence's "every entry", "any practice"; a row's More). The trigger is
 * a button (`aria-haspopup="menu"`); the list is a frosted menu laid in the top layer where the browser has popovers
 * (so no pane, transform or container clips it), placed under its trigger and kept on screen. With `value` it is a
 * radio menu (one checked); without, a menu of actions. Keyboard: Enter, Space or Down opens it on the checked item;
 * Up and Down move, Home and End jump, Escape closes it and returns to the trigger, Tab closes it and moves on.
 */
import { Fragment, type ComponentChildren } from "preact";
import { useEffect, useLayoutEffect, useRef, useState } from "preact/hooks";
import { Check } from "../icons";
import "./Menu.css";

export interface MenuItem<T extends string> {
  value: T;
  label: ComponentChildren;
  /** A count set at the right (how many entries a choice would show). */
  count?: ComponentChildren;
  disabled?: boolean;
  /** A rule above this item. */
  rule?: boolean;
}

export function Menu<T extends string>({ label, items, value, onSelect, children, triggerClass = "choice", triggerLabel }: {
  /** The menu's name ("Which entries"). */
  label: string;
  items: readonly MenuItem<T>[];
  /** The checked item, for a radio menu. */
  value?: T;
  onSelect: (value: T) => void;
  /** The trigger's content. */
  children: ComponentChildren;
  /** The trigger's look: `choice` (a dotted underline in a sentence), or a button's classes. */
  triggerClass?: string;
  /** The trigger's accessible name, when its content alone does not say it. */
  triggerLabel?: string;
}) {
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const radio = value !== undefined;

  const close = (refocus: boolean) => {
    setOpen(false);
    if (refocus) trigger.current?.focus();
  };
  const focusItem = (which: "checked" | "first" | "last" | number) => {
    const all = [...(list.current?.querySelectorAll<HTMLElement>('[role^="menuitem"]:not([disabled])') ?? [])];
    if (!all.length) return;
    const at = all.findIndex((el) => el === document.activeElement);
    const pick = which === "first" ? 0 : which === "last" ? all.length - 1 : which === "checked"
      ? Math.max(0, all.findIndex((el) => el.getAttribute("aria-checked") === "true"))
      : (at + which + all.length) % all.length;
    all[pick]?.focus();
  };

  // place it under the trigger, in the top layer where there is one, and keep it on screen
  useLayoutEffect(() => {
    const menu = list.current;
    const btn = trigger.current;
    if (!open || !menu || !btn) return;
    const pop = menu as HTMLElement & { showPopover?: () => void };
    if (typeof pop.showPopover === "function") {
      try { pop.showPopover(); } catch { /* already shown */ }
    }
    const r = btn.getBoundingClientRect();
    const w = menu.offsetWidth;
    const h = menu.offsetHeight;
    const left = Math.max(8, Math.min(r.left, window.innerWidth - w - 8));
    const below = r.bottom + 6;
    const top = below + h > window.innerHeight - 8 && r.top - h - 6 > 8 ? r.top - h - 6 : below;
    menu.style.left = `${left}px`;
    menu.style.top = `${top}px`;
    focusItem(radio ? "checked" : "first");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // a press outside, or the page moving under it, closes it
  useEffect(() => {
    if (!open) return;
    const outside = (e: PointerEvent) => {
      const t = e.target as Node;
      if (!list.current?.contains(t) && !trigger.current?.contains(t)) close(false);
    };
    const away = () => close(false);
    document.addEventListener("pointerdown", outside, true);
    window.addEventListener("resize", away);
    return () => {
      document.removeEventListener("pointerdown", outside, true);
      window.removeEventListener("resize", away);
    };
  }, [open]);

  const onTriggerKey = (e: KeyboardEvent) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      setOpen(true);
    }
  };
  const onListKey = (e: KeyboardEvent) => {
    if (e.key === "ArrowDown") focusItem(1);
    else if (e.key === "ArrowUp") focusItem(-1);
    else if (e.key === "Home") focusItem("first");
    else if (e.key === "End") focusItem("last");
    else if (e.key === "Escape") close(true);
    else if (e.key === "Tab") { close(false); return; }
    else return;
    e.preventDefault();
    e.stopPropagation();
  };

  return (
    <>
      <button ref={trigger} type="button" class={triggerClass} aria-haspopup="menu" aria-expanded={open} aria-label={triggerLabel}
        onClick={() => setOpen((o) => !o)} onKeyDown={onTriggerKey}>
        {children}
      </button>
      {open && (
        <div ref={list} class="menu" role="menu" aria-label={label} popover="manual" onKeyDown={onListKey}>
          {items.map((it) => (
            <Fragment key={it.value}>
              {it.rule && <hr />}
              <button type="button" role={radio ? "menuitemradio" : "menuitem"} tabIndex={-1} disabled={it.disabled}
                aria-checked={radio ? it.value === value : undefined}
                onClick={() => {
                  close(true);
                  onSelect(it.value);
                }}>
                <span class="menu-label">{it.label}</span>
                {it.count !== undefined && <span class="menu-count">{it.count}</span>}
                {radio && it.value === value && <Check class="menu-check" />}
              </button>
            </Fragment>
          ))}
        </div>
      )}
    </>
  );
}
