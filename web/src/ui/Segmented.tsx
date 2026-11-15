/**
 * One of a few, side by side (System · Paper · Night; titles · dates · none). A radio group: the arrow keys move the
 * choice, Tab leaves it; the thumb slides on the control spring, measured per option. `iconOnly` shows icons and keeps
 * each label as the option's name (and tooltip). On a phone, `wide` fills the row.
 */
import type { ComponentChildren } from "preact";
import { useLayoutEffect, useRef, useState } from "preact/hooks";
import "./Segmented.css";

export interface SegmentedOption<T extends string> {
  value: T;
  label: string;
  icon?: ComponentChildren;
}

export function Segmented<T extends string>({ label, options, value, onChange, iconOnly, wide, class: cls }: {
  label: string;
  options: readonly SegmentedOption<T>[];
  value: T;
  onChange: (next: T) => void;
  iconOnly?: boolean;
  wide?: boolean;
  class?: string;
}) {
  const group = useRef<HTMLDivElement>(null);
  const [thumb, setThumb] = useState<{ x: number; w: number; ready: boolean } | null>(null);
  useLayoutEffect(() => {
    const measure = () => {
      const on = group.current?.querySelector<HTMLElement>('[aria-checked="true"]');
      if (!on) return;
      setThumb((t) => ({ x: on.offsetLeft, w: on.offsetWidth, ready: t !== null }));
    };
    measure();
    const ro = typeof ResizeObserver === "function" ? new ResizeObserver(measure) : null;
    if (group.current) ro?.observe(group.current);
    return () => ro?.disconnect();
  }, [value, options.length]);

  const move = (to: number) => {
    const next = options[(to + options.length) % options.length];
    if (!next) return;
    onChange(next.value);
    // focus follows the choice (the roving tab stop), once the new state is drawn
    requestAnimationFrame(() => group.current?.querySelector<HTMLElement>('[aria-checked="true"]')?.focus());
  };
  const at = options.findIndex((o) => o.value === value);
  const onKey = (e: KeyboardEvent) => {
    if (e.key === "ArrowRight" || e.key === "ArrowDown") move(at + 1);
    else if (e.key === "ArrowLeft" || e.key === "ArrowUp") move(at - 1);
    else if (e.key === "Home") move(0);
    else if (e.key === "End") move(options.length - 1);
    else return;
    e.preventDefault();
  };
  const style = thumb ? { "--x": `${thumb.x}px`, "--w": `${thumb.w}px` } : undefined;
  return (
    <div ref={group} role="radiogroup" aria-label={label} onKeyDown={onKey} style={style}
      class={["segmented", iconOnly && "icons", wide && "wide", thumb && "measured", thumb?.ready && "ready", cls].filter(Boolean).join(" ")}>
      <span class="segmented-thumb" aria-hidden="true" />
      {options.map((o) => (
        <button key={o.value} type="button" role="radio" aria-checked={o.value === value} tabIndex={o.value === value ? 0 : -1}
          aria-label={iconOnly ? o.label : undefined} title={iconOnly ? o.label : undefined} onClick={() => onChange(o.value)}>
          {o.icon}
          {!iconOnly && <span>{o.label}</span>}
        </button>
      ))}
    </div>
  );
}
