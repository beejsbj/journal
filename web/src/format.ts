/** Dates and labels. Days are `YYYY-MM-DD` strings from the server; they are read as calendar days, never as
 *  instants, so the browser's time zone cannot move a day. Weeks start on Monday. */

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const WEEKDAYS_LONG = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const MONTHS_LONG = [
  "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November",
  "December",
];

interface Ymd {
  y: number;
  m: number;
  d: number;
}

export function parseDay(day: string): Ymd | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(day);
  if (!m) return null;
  const [y, mo, d] = [Number(m[1]), Number(m[2]), Number(m[3])];
  const probe = new Date(Date.UTC(y, mo - 1, d));
  return probe.getUTCFullYear() === y && probe.getUTCMonth() === mo - 1 && probe.getUTCDate() === d ? { y, m: mo, d } : null;
}

/** Monday = 0 … Sunday = 6, as the server's schedule slots count. */
export function weekdayIndex(day: string): number {
  const p = parseDay(day);
  if (!p) return 0;
  return (new Date(Date.UTC(p.y, p.m - 1, p.d)).getUTCDay() + 6) % 7;
}

export function addDays(day: string, n: number): string {
  const p = parseDay(day);
  if (!p) return day;
  const t = new Date(Date.UTC(p.y, p.m - 1, p.d + n));
  return `${t.getUTCFullYear()}-${String(t.getUTCMonth() + 1).padStart(2, "0")}-${String(t.getUTCDate()).padStart(2, "0")}`;
}

/** "Wed 14 Oct" */
export function dayLabel(day: string): string {
  const p = parseDay(day);
  return p ? `${WEEKDAYS[weekdayIndex(day)]} ${p.d} ${MONTHS[p.m - 1]}` : day;
}

/** "Wednesday 14 October 2026" */
export function longDay(day: string): string {
  const p = parseDay(day);
  return p ? `${WEEKDAYS_LONG[weekdayIndex(day)]} ${p.d} ${MONTHS_LONG[p.m - 1]} ${p.y}` : day;
}

/** "14 Oct" */
export function shortDay(day: string): string {
  const p = parseDay(day);
  return p ? `${p.d} ${MONTHS[p.m - 1]}` : day;
}

/** The Monday of the week `day` is in. */
export function weekStart(day: string): string {
  return addDays(day, -weekdayIndex(day));
}

/** "Week of 12 Oct" */
export function weekHeader(day: string): string {
  return `Week of ${shortDay(weekStart(day))}`;
}

/** "Mon 12 – Sun 18 Oct" style range for a week ending on `end`. */
export function weekRange(end: string): string {
  const start = addDays(end, -6);
  const a = parseDay(start);
  const b = parseDay(end);
  if (!a || !b) return end;
  const left = a.m === b.m ? `${a.d}` : `${a.d} ${MONTHS[a.m - 1]}`;
  return `${left} – ${b.d} ${MONTHS[b.m - 1]}`;
}

export interface SlotLike {
  time: string;
  weekday: number | null;
}

/** "Daily 23:00", "Wed 12:00", "Daily 08:00, Sun 20:30", "On demand". */
export function cadence(schedule: SlotLike[]): string {
  if (schedule.length === 0) return "On demand";
  return schedule
    .map((s) => `${s.weekday === null ? "Daily" : (WEEKDAYS[s.weekday] ?? "?")} ${s.time}`)
    .join(", ");
}

/** A title without the date the engine appends: "Evening journal — Wed 14 Oct 2026" is "Evening journal". */
export function plainTitle(title: string): string {
  // Only the engine's date suffix: a title of his own may hold " — " ("Mind — Body").
  return title.replace(/ — [A-Za-z]{3} \d{1,2} [A-Za-z]{3} \d{4}$/, "");
}

/** The first `n` characters of `text` on a word boundary, with an ellipsis when cut. */
export function clip(text: string, n: number): string {
  const flat = text.replace(/\s+/g, " ").trim();
  if (flat.length <= n) return flat;
  const cut = flat.slice(0, n);
  const space = cut.lastIndexOf(" ");
  return `${(space > n * 0.6 ? cut.slice(0, space) : cut).replace(/[,;:.\s]+$/, "")}…`;
}

/** "2:31" for 151 seconds, "1:02:03" for an hour and more. */
export function duration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = String(s % 60).padStart(2, "0");
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${r}` : `${m}:${r}`;
}

/** The part of the day an "HH:MM" falls in, as the margin names it under the hour: "morning" (04:00-10:59),
 *  "afternoon", "evening" (17:00-21:59), "night". */
export function dayPart(hhmm: string): "morning" | "afternoon" | "evening" | "night" {
  const h = Number(hhmm.slice(0, 2));
  if (!Number.isFinite(h) || hhmm.length < 2) return "afternoon";
  if (h >= 4 && h < 11) return "morning";
  if (h >= 11 && h < 17) return "afternoon";
  if (h >= 17 && h < 22) return "evening";
  return "night";
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}
