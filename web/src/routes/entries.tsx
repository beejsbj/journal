import { useLocation } from "preact-iso";
import { useEffect, useRef, useState } from "preact/hooks";
import { api, get, query } from "../api";
import { EntryRow, dayParts } from "../components/EntryRow";
import { useLive } from "../events";
import { dayLabel, weekHeader, weekStart } from "../format";
import { useResource, asApiError } from "../hooks";
import { ChevronDown, Close, Search } from "../icons";
import { arr, obj, str } from "../schema";
import { useTitle } from "../title";
import type { ApiError } from "../api";
import type { EntrySummary, LibraryItem } from "../types";
import { Await, Button, Calendar, Empty, ErrorLine, Menu, TextButton, type MenuItem } from "../ui";
import "./entries.css";

const PAGE = 40;

export interface Filters {
  kind?: string | undefined;
  program?: string | undefined;
  day?: string | undefined;
  audio: boolean;
  q: string;
}

export function filtersOf(q: Record<string, string | undefined>): Filters {
  return { kind: q.kind || undefined, program: q.program || undefined, day: q.day || undefined, audio: q.audio === "1", q: q.q ?? "" };
}

/** The filters as the address carries them (`?kind=…&day=…`): replace navigation keeps them, a link can name them. */
export function filterQuery(f: Filters): string {
  return query({ kind: f.kind, program: f.program, day: f.day, audio: f.audio ? "1" : undefined, q: f.q || undefined });
}

/** The filters as the server reads them (`/entries` and `/days`): a day is a range of one. */
export function params(f: Filters): Record<string, string | boolean | number | undefined> {
  return {
    kind: f.kind,
    program: f.program,
    from: f.day,
    to: f.day,
    audio: f.audio ? "1" : undefined,
    q: f.q || undefined,
  };
}

// The list he opened an entry from: its filters, so the sidebar beside the entry shows the same list and the way back
// returns to it. Per tab, gone on a reload (a deep link shows every entry).
let lastQuery = "";
export function rememberedQuery(): string {
  return lastQuery;
}
/** The sidebar beside an entry narrowed its list (a day on its calendar): the way back keeps that too. */
export function rememberQuery(q: string): void {
  lastQuery = q;
}

/** Wide screens (761 px and up) lay the list beside an open entry; phones push the entry over the list. */
const WIDE = "(min-width: 761px)";
export function useWide(): boolean {
  const [wide, setWide] = useState(() => typeof matchMedia === "function" && matchMedia(WIDE).matches);
  useEffect(() => {
    if (typeof matchMedia !== "function") return;
    const mq = matchMedia(WIDE);
    const on = () => setWide(mq.matches);
    on();
    mq.addEventListener?.("change", on);
    return () => mq.removeEventListener?.("change", on);
  }, []);
  return wide;
}

/** The device's date as "YYYY-MM-DD". */
export function localDay(at: Date = new Date()): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${at.getFullYear()}-${p(at.getMonth() + 1)}-${p(at.getDate())}`;
}

/** The days of a month he wrote on, under the other filters (not the day itself): `/days`, which groups by day. */
const Days = arr(obj({ date: str, entries: arr(obj({ stem: str })) }));
function lastDay(month: string): string {
  const [y, m] = month.split("-").map(Number) as [number, number];
  return `${month}-${String(new Date(y, m, 0).getDate()).padStart(2, "0")}`;
}
export function useWrittenDays(month: string, f: Filters): Set<string> {
  const key = JSON.stringify([month, f.kind, f.program, f.audio, f.q]);
  const [res, reload] = useResource(
    (signal) => get(`/days${query({ ...params({ ...f, day: undefined }), from: `${month}-01`, to: lastDay(month) })}`, Days, signal)
      .catch((e: unknown) => {
        if (e instanceof DOMException) throw e;
        return [];   // the month is only a way in: without it the list still reads
      }),
    [key],
  );
  useLive(["entries-changed"], reload);
  return new Set(res.state === "ready" ? res.data.filter((d) => d.entries.length > 0).map((d) => d.date) : []);
}

/** Entries by week and day, newest first. The weeks are Monday to Sunday; the day headers are the entries' dates. */
export function groupByWeek(items: EntrySummary[]): { week: string; days: { date: string; items: EntrySummary[] }[] }[] {
  const weeks: { week: string; days: { date: string; items: EntrySummary[] }[] }[] = [];
  for (const e of items) {
    const key = weekStart(e.date);
    let w = weeks[weeks.length - 1];
    if (!w || w.week !== key) {
      w = { week: key, days: [] };
      weeks.push(w);
    }
    let d = w.days[w.days.length - 1];
    if (!d || d.date !== e.date) {
      d = { date: e.date, items: [] };
      w.days.push(d);
    }
    d.items.push(e);
  }
  return weeks;
}

type Which = "all" | "audio";
const WHICH: MenuItem<Which>[] = [
  { value: "all", label: "every entry" },
  { value: "audio", label: "entries with your voice" },
];

/**
 * "Showing every entry from any practice, newest first." Each choice is a phrase that opens a menu (ui/Menu); the
 * choices are the same address as ever (`audio`, `kind`, `program`), replaced, not pushed. Kind and program exclude
 * each other.
 */
function FilterSentence({ library, filters, go }: { library: LibraryItem[]; filters: Filters; go: (next: Partial<Filters>) => void }) {
  const mine = library.filter((p) => p.enabled || p.id === filters.kind || p.id === filters.program);
  const plain = mine.filter((p) => p.kind !== "program");
  const programs = mine.filter((p) => p.kind === "program");
  const from: MenuItem<string>[] = [
    { value: "any", label: "any practice" },
    ...plain.map((p) => ({ value: `kind:${p.id}`, label: p.title })),
    ...programs.map((p, i) => ({ value: `program:${p.id}`, label: p.title, rule: i === 0 })),
  ];
  const fromValue = filters.kind ? `kind:${filters.kind}` : filters.program ? `program:${filters.program}` : "any";
  const fromLabel = from.find((o) => o.value === fromValue)?.label ?? filters.kind ?? filters.program ?? "any practice";
  const which: Which = filters.audio ? "audio" : "all";
  return (
    <p class="filter-sentence">
      Showing{" "}
      <Menu label="Which entries" items={WHICH} value={which} onSelect={(v) => go({ audio: v === "audio" })}>
        <span>{WHICH.find((o) => o.value === which)?.label}</span>
        <ChevronDown />
      </Menu>{" "}
      from{" "}
      <Menu label="Which practice" items={from} value={fromValue} onSelect={(v) => {
        const [k, id] = v.split(":") as [string, string | undefined];
        go(k === "kind" ? { kind: id, program: undefined } : k === "program" ? { program: id, kind: undefined } : { kind: undefined, program: undefined });
      }}>
        <span>{fromLabel}</span>
        <ChevronDown />
      </Menu>, newest first.
    </p>
  );
}

export function EntriesRoute() {
  useTitle("Entries");
  const loc = useLocation();
  const wide = useWide();
  const filters = filtersOf(loc.query);
  const key = JSON.stringify(filters);
  const [text, setText] = useState(filters.q);
  const pushed = useRef(filters.q);
  lastQuery = filterQuery(filters);

  const go = (next: Partial<Filters>) => {
    const f = { ...filters, ...next };
    pushed.current = f.q;
    loc.route(`/entries${filterQuery(f)}`, true);
  };

  // Typing searches after a short pause; the URL carries the search so a reload or a link keeps it. The pause ends in
  // the latest `go` (a filter chosen meanwhile is kept, not undone by the filters the timer was set under) and
  // is dropped when the address changes under it.
  const latestGo = useRef(go);
  latestGo.current = go;
  useEffect(() => {
    if (text === filters.q) return;
    const t = setTimeout(() => latestGo.current({ q: text.trim() }), 350);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text, key]);
  // A search that came from outside (a link, Back) fills the box; one we pushed ourselves must not overwrite typing.
  useEffect(() => {
    if (filters.q !== pushed.current) setText(filters.q);
    pushed.current = filters.q;
  }, [filters.q]);

  const [library, reloadLibrary] = useResource((signal) => api.library(signal).catch((e: unknown) => {
    if (e instanceof DOMException) throw e;
    return [] as LibraryItem[];
  }), []);
  useLive(["library-changed"], reloadLibrary);
  const [first, reload] = useResource((signal) => api.entries({ ...params(filters), limit: PAGE }, signal), [key]);
  useLive(["entries-changed"], reload);

  // The month on the calendar: the chosen day's, else the one he moved to, else the newest entry's, else this one.
  const today = localDay();
  const [month, setMonth] = useState<string | null>(null);
  const newest = first.state === "ready" ? first.data.items[0]?.date : undefined;
  const shown = (filters.day && !month ? filters.day : null)?.slice(0, 7) ?? month ?? newest?.slice(0, 7) ?? today.slice(0, 7);
  const written = useWrittenDays(shown, filters);

  // A page of "Load more" belongs to the first page it was asked after (cursors are offsets into that very list), so
  // it is kept with that first page's identity and used only while that first page is the one on screen. When the
  // filters change or the first page is read again, a page still in flight is aborted and, should it arrive anyway,
  // dropped: never an older answer under newer filters.
  type More = { origin: unknown; items: EntrySummary[]; next?: string | null; busy: boolean; error?: ApiError };
  const [moreState, setMore] = useState<More>({ origin: null, items: [], busy: false });
  const more: More = moreState.origin === first ? moreState : { origin: first, items: [], busy: false };
  const current = useRef(first);
  current.current = first;
  const inflight = useRef<{ ctl: AbortController; origin: unknown } | null>(null);
  useEffect(() => {
    if (inflight.current && inflight.current.origin !== first) {
      inflight.current.ctl.abort();
      inflight.current = null;
    }
  }, [first]);
  useEffect(() => () => inflight.current?.ctl.abort(), []);

  const loadMore = (cursor: string) => {
    const origin = first;
    inflight.current?.ctl.abort();
    const ctl = new AbortController();
    inflight.current = { ctl, origin };
    const live = () => !ctl.signal.aborted && current.current === origin;
    setMore({ ...more, origin, busy: true, error: undefined });
    api.entries({ ...params(filters), limit: PAGE, cursor }, ctl.signal)
      .then((page) => {
        if (live()) setMore((m) => ({ origin, items: [...(m.origin === origin ? m.items : []), ...page.items], next: page.next, busy: false }));
      })
      .catch((e: unknown) => {
        if (live()) setMore((m) => ({ ...(m.origin === origin ? m : { origin, items: [] }), busy: false, error: asApiError(e) }));
      });
  };

  const vt = wide ? "entry" : "push";
  return (
    <div class="entries-page">
      <header class="page-head entries-head">
        <div class="entries-lead">
          <h1 class="display">Entries</h1>
          <FilterSentence library={library.state === "ready" ? library.data : []} filters={filters} go={go} />
          <form class="search" role="search" onSubmit={(e) => { e.preventDefault(); go({ q: text.trim() }); }}>
            <Search aria-hidden="true" />
            <input type="search" name="q" value={text} placeholder="Search your words" aria-label="Search your words" autoComplete="off"
              onInput={(e) => setText((e.target as HTMLInputElement).value)} />
          </form>
          {filters.day && (
            <p class="active-filter">
              <span>Only {dayLabel(filters.day)}</span>
              <TextButton onClick={() => go({ day: undefined })}>
                <Close aria-hidden="true" /> Show all
              </TextButton>
            </p>
          )}
        </div>
        <Calendar class="entries-calendar" month={shown} marked={written} selected={filters.day ?? null} today={today}
          onSelect={(day) => { setMonth(null); go({ day: day ?? undefined }); }} onMonth={setMonth} legend />
      </header>
      <Await res={first} reload={reload} rows={5}>
        {(page) => {
          const items = [...page.items, ...more.items];
          const next = more.next !== undefined ? more.next : page.next;
          if (items.length === 0) {
            return <Empty>{filters.q || filters.kind || filters.program || filters.audio || filters.day ? "No entries match." : "No entries yet."}</Empty>;
          }
          let i = 0;
          return (
            <div class="entries-list">
              {groupByWeek(items).map((w) => (
                <section class="week" key={w.week}>
                  <h2 class="book-head week-head">{weekHeader(w.week)}</h2>
                  {w.days.map((d) => {
                    const { n, w: wd } = dayParts(d.date);
                    return (
                      <div class="day" key={d.date} id={`day-${d.date}`}>
                        <h3 class="day-tab"><span class="day-n">{n}</span> <span class="day-w">{wd}</span></h3>
                        <div class="rows">
                          {d.items.map((e) => (
                            <EntryRow key={e.stem} entry={e} q={filters.q} i={i++} vt={vt} />
                          ))}
                        </div>
                      </div>
                    );
                  })}
                </section>
              ))}
              {more.error && <ErrorLine error={more.error} onRetry={next ? () => loadMore(next) : undefined} />}
              {next && !more.error && (
                <Button wide class="load-more" disabled={more.busy} onClick={() => loadMore(next)}>
                  {more.busy ? "Loading…" : "Load more"}
                </Button>
              )}
            </div>
          );
        }}
      </Await>
    </div>
  );
}
