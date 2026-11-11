/**
 * Almanac's month: the month's name in the book's roman, arrows to the months either side, Monday to Sunday, the days.
 * A day he wrote on is set bold with a dot and is a button (it filters what is shown to that day); today is ringed; the
 * chosen day fills with the lamp. Keyboard: one tab stop; the arrows move between written days (Left and Right in
 * order, Up and Down by about a week), Home and End, Page Up and Page Down change the month.
 */
import { useRef } from "preact/hooks";
import { ChevronLeft, ChevronRight } from "../icons";
import { IconButton } from "./Button";
import "./Calendar.css";

const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const pad = (n: number) => String(n).padStart(2, "0");
const iso = (y: number, m: number, d: number) => `${y}-${pad(m)}-${pad(d)}`;

/** "2026-10" moved by `by` months. */
export function shiftMonth(month: string, by: number): string {
  const [y, m] = month.split("-").map(Number) as [number, number];
  const at = new Date(y, m - 1 + by, 1);
  return `${at.getFullYear()}-${pad(at.getMonth() + 1)}`;
}

export function Calendar({ month, marked, selected, today, onSelect, onMonth, legend, class: cls }: {
  /** "YYYY-MM". */
  month: string;
  /** Days he wrote on ("YYYY-MM-DD"). */
  marked: Iterable<string>;
  selected?: string | null;
  today?: string;
  /** A written day chosen; the chosen one again clears it (null). */
  onSelect: (day: string | null) => void;
  /** Without it there are no arrows. */
  onMonth?: (month: string) => void;
  legend?: boolean;
  class?: string;
}) {
  const grid = useRef<HTMLTableElement>(null);
  const [y, m] = month.split("-").map(Number) as [number, number];
  const days = new Date(y, m, 0).getDate();
  const lead = (new Date(y, m - 1, 1).getDay() + 6) % 7;   // Monday first
  const written = new Set(marked);
  const inMonth = [...written].filter((d) => d.startsWith(`${month}-`)).sort();
  const stop = (selected && inMonth.includes(selected) ? selected : null) ?? (today && inMonth.includes(today) ? today : null) ?? inMonth[0];

  const cells: (number | null)[] = [...Array(lead).fill(null), ...Array.from({ length: days }, (_, i) => i + 1)];
  while (cells.length % 7) cells.push(null);
  const weeks = Array.from({ length: cells.length / 7 }, (_, w) => cells.slice(w * 7, w * 7 + 7));

  const focusDay = (day: string | undefined) => {
    if (day) grid.current?.querySelector<HTMLElement>(`[data-day="${day}"]`)?.focus();
  };
  const onKey = (e: KeyboardEvent) => {
    const at = (e.target as HTMLElement).dataset.day;
    if (!at) return;
    const i = inMonth.indexOf(at);
    const near = (delta: number) => {
      const [yy, mm, dd] = at.split("-").map(Number) as [number, number, number];
      const target = iso(yy, mm, Math.min(days, Math.max(1, dd + delta)));
      return delta > 0 ? inMonth.find((d) => d >= target) ?? inMonth[inMonth.length - 1] : [...inMonth].reverse().find((d) => d <= target) ?? inMonth[0];
    };
    if (e.key === "ArrowRight") focusDay(inMonth[Math.min(inMonth.length - 1, i + 1)]);
    else if (e.key === "ArrowLeft") focusDay(inMonth[Math.max(0, i - 1)]);
    else if (e.key === "ArrowDown") focusDay(near(7));
    else if (e.key === "ArrowUp") focusDay(near(-7));
    else if (e.key === "Home") focusDay(inMonth[0]);
    else if (e.key === "End") focusDay(inMonth[inMonth.length - 1]);
    else if (e.key === "PageUp" && onMonth) onMonth(shiftMonth(month, -1));
    else if (e.key === "PageDown" && onMonth) onMonth(shiftMonth(month, 1));
    else return;
    e.preventDefault();
  };

  return (
    <div class={["calendar", cls].filter(Boolean).join(" ")}>
      <div class="calendar-head">
        <h3 class="calendar-month" id={`cal-${month}`}>{MONTHS[m - 1]}</h3>
        <span class="calendar-year">{y}</span>
        {onMonth && (
          <>
            <IconButton size="small" label="Earlier month" onClick={() => onMonth(shiftMonth(month, -1))}><ChevronLeft /></IconButton>
            <IconButton size="small" label="Later month" onClick={() => onMonth(shiftMonth(month, 1))}><ChevronRight /></IconButton>
          </>
        )}
      </div>
      <table ref={grid} class="calendar-grid" aria-labelledby={`cal-${month}`} onKeyDown={onKey}>
        <thead>
          <tr>{DAYS.map((d) => <th key={d} scope="col" abbr={d}>{d[0]}</th>)}</tr>
        </thead>
        <tbody>
          {weeks.map((w, wi) => (
            <tr key={wi}>
              {w.map((d, di) => {
                if (d === null) return <td key={di} />;
                const day = iso(y, m, d);
                const isToday = day === today;
                const future = today !== undefined && day > today;
                if (!written.has(day)) {
                  return <td key={di}><span class={[isToday && "today", future && "future"].filter(Boolean).join(" ") || undefined} aria-current={isToday ? "date" : undefined}>{d}</span></td>;
                }
                const name = `${DAYS[(lead + d - 1) % 7]} ${d} ${MONTHS[m - 1]}`;
                return (
                  <td key={di}>
                    <button type="button" data-day={day} class={isToday ? "today" : undefined} aria-pressed={day === selected}
                      aria-current={isToday ? "date" : undefined} aria-label={`${name}, written`} tabIndex={day === stop ? 0 : -1}
                      onClick={() => onSelect(day === selected ? null : day)}>
                      {d}
                    </button>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      {legend && (
        <p class="calendar-legend" aria-hidden="true">
          <span><i class="written" />written</span>
          <span><i class="ring" />today</span>
        </p>
      )}
    </div>
  );
}
