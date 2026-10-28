import { useLocation } from "preact-iso";
import { useEffect, useRef, useState } from "preact/hooks";
import { api, query } from "../api";
import { EntryRow } from "../components/EntryRow";
import { Await, Empty, ErrorLine } from "../components/Status";
import { useLive } from "../events";
import { dayLabel, weekHeader, weekStart } from "../format";
import { useResource, asApiError } from "../hooks";
import { Close, Search } from "../icons";
import { useTitle } from "../title";
import type { ApiError } from "../api";
import type { EntrySummary, LibraryItem } from "../types";

const PAGE = 40;

interface Filters {
  kind?: string;
  program?: string;
  day?: string;
  audio: boolean;
  q: string;
}

function params(f: Filters): Record<string, string | boolean | number | undefined> {
  return {
    kind: f.kind,
    program: f.program,
    from: f.day,
    to: f.day,
    audio: f.audio ? "1" : undefined,
    q: f.q || undefined,
  };
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

function Chips({ library, filters, go }: { library: LibraryItem[]; filters: Filters; go: (next: Partial<Filters>) => void }) {
  const mine = library.filter((p) => p.enabled || p.id === filters.kind || p.id === filters.program);
  const plain = mine.filter((p) => p.kind !== "program");
  const programs = mine.filter((p) => p.kind === "program");
  const none = !filters.kind && !filters.program && !filters.audio;
  return (
    <div class="chips filter-chips" role="group" aria-label="Filters">
      <button type="button" class={`chip${none ? " on" : ""}`} aria-pressed={none} onClick={() => go({ kind: undefined, program: undefined, audio: false })}>
        All
      </button>
      {plain.map((p) => (
        <button type="button" key={p.id} class={`chip${filters.kind === p.id ? " on" : ""}`} aria-pressed={filters.kind === p.id}
          onClick={() => go({ kind: filters.kind === p.id ? undefined : p.id, program: undefined })}>
          {p.emoji} {p.title}
        </button>
      ))}
      {programs.map((p) => (
        <button type="button" key={p.id} class={`chip${filters.program === p.id ? " on" : ""}`} aria-pressed={filters.program === p.id}
          onClick={() => go({ program: filters.program === p.id ? undefined : p.id, kind: undefined })}>
          {p.emoji} {p.title}
        </button>
      ))}
      <button type="button" class={`chip${filters.audio ? " on" : ""}`} aria-pressed={filters.audio} onClick={() => go({ audio: !filters.audio })}>
        Has audio
      </button>
    </div>
  );
}

export function EntriesRoute() {
  useTitle("Entries");
  const loc = useLocation();
  const filters: Filters = {
    kind: loc.query.kind || undefined,
    program: loc.query.program || undefined,
    day: loc.query.day || undefined,
    audio: loc.query.audio === "1",
    q: loc.query.q ?? "",
  };
  const key = JSON.stringify(filters);
  const [text, setText] = useState(filters.q);
  const pushed = useRef(filters.q);

  const go = (next: Partial<Filters>) => {
    const f = { ...filters, ...next };
    pushed.current = f.q;
    loc.route(`/entries${query({ kind: f.kind, program: f.program, day: f.day, audio: f.audio ? "1" : undefined, q: f.q || undefined })}`, true);
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

  return (
    <div class="page entries">
      <header class="page-head">
        <p class="eyebrow">History</p>
        <h1>Entries</h1>
      </header>
      <form class="search" role="search" onSubmit={(e) => { e.preventDefault(); go({ q: text.trim() }); }}>
        <Search width={16} height={16} />
        <input type="search" name="q" value={text} placeholder="Search your words" aria-label="Search your words"
          onInput={(e) => setText((e.target as HTMLInputElement).value)} />
      </form>
      <Chips library={library.state === "ready" ? library.data : []} filters={filters} go={go} />
      {filters.day && (
        <p class="active-filter">
          Showing {dayLabel(filters.day)}{" "}
          <button type="button" class="link" onClick={() => go({ day: undefined })}>
            <Close width={13} height={13} /> Show all
          </button>
        </p>
      )}
      <Await res={first} reload={reload} rows={5}>
        {(page) => {
          const items = [...page.items, ...more.items];
          const next = more.next !== undefined ? more.next : page.next;
          if (items.length === 0) {
            return <Empty>{filters.q || filters.kind || filters.program || filters.audio || filters.day ? "No entries match." : "No entries yet."}</Empty>;
          }
          return (
            <>
              {groupByWeek(items).map((w) => (
                <section class="week" key={w.week}>
                  <h2 class="week-head">{weekHeader(w.week)}</h2>
                  {w.days.map((d) => (
                    <div class="day" key={d.date} id={`day-${d.date}`}>
                      <h3>{dayLabel(d.date)}</h3>
                      <div class="rows">
                        {d.items.map((e) => (
                          <EntryRow key={e.stem} entry={e} />
                        ))}
                      </div>
                    </div>
                  ))}
                </section>
              ))}
              {more.error && <ErrorLine error={more.error} onRetry={next ? () => loadMore(next) : undefined} />}
              {next && !more.error && (
                <button type="button" class="button wide" disabled={more.busy} onClick={() => loadMore(next)}>
                  {more.busy ? "Loading…" : "Load more"}
                </button>
              )}
            </>
          );
        }}
      </Await>
    </div>
  );
}
