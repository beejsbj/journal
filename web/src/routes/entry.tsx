import { useEffect, useLayoutEffect, useRef, useState } from "preact/hooks";
import { api } from "../api";
import { Composer } from "../components/Composer";
import { Document, hourOf, isPending } from "../components/Document";
import { SideRow, dayParts } from "../components/EntryRow";
import { Markdown } from "../components/Markdown";
import { MetaSheet } from "../components/MetaSheet";
import { useLive } from "../events";
import { clip, dayLabel, duration, plainTitle, weekHeader } from "../format";
import { useResource } from "../hooks";
import { ChevronDown, ChevronLeft, ChevronUp, Envelope, Expand, Pen, SideDots, SideList, SideNone, Star } from "../icons";
import { Pane, openWrite, useNavigate, useShellMode, withTransition } from "../shell";
import { useTitle } from "../title";
import type { EntryDoc, EntryPage, EntrySummary } from "../types";
import { Await, Button, Calendar, Disclosure, Empty, IconButton, Segmented, Sheet, Skeleton, TextButton, Toast } from "../ui";
import { filterQuery, filtersOf, groupByWeek, localDay, params, rememberQuery, rememberedQuery, useWide, useWrittenDays, type Filters } from "./entries";
import "./entry.css";

const WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

// ---------------------------------------------------------------------------------------------------------------------
// the sidebar: titles, dates and dots, or none; chosen in the entry bar and kept on this device

export type SidebarMode = "list" | "rail" | "hidden";
export const SIDEBAR_KEY = "journal:sidebar";
const MODES: SidebarMode[] = ["list", "rail", "hidden"];

export function readSidebar(): SidebarMode {
  try {
    const v = localStorage.getItem(SIDEBAR_KEY);
    return MODES.includes(v as SidebarMode) ? (v as SidebarMode) : "list";
  } catch {
    return "list";
  }
}

function useSidebar(): [SidebarMode, (m: SidebarMode) => void] {
  const [mode, setMode] = useState<SidebarMode>(readSidebar);
  const choose = (m: SidebarMode) => {
    if (m === mode) return;
    try {
      if (m === "list") localStorage.removeItem(SIDEBAR_KEY);   // the default is no choice at all
      else localStorage.setItem(SIDEBAR_KEY, m);
    } catch {
      /* a device that keeps nothing still switches, for now */
    }
    void withTransition(() => setMode(m), "page");
  };
  return [mode, choose];
}

/** "Wednesday 14 October 2026" and its parts, read as a calendar date. */
function dateOf(day: string): { weekday: string; d: number; month: string; year: number } {
  const [y, m, d] = day.split("-").map(Number) as [number, number, number];
  return { weekday: WEEKDAYS[new Date(Date.UTC(y, m - 1, d)).getUTCDay()] ?? "", d, month: MONTHS[m - 1] ?? "", year: y };
}

// The sidebar's list: the list he came from (its filters), the first pages of it. Kept between entries so stepping
// from one to the next does not empty it while it is read again.
let sideCache: { key: string; items: EntrySummary[]; next: string | null } | null = null;

function useSideList(f: Filters, wanted: boolean): { items: EntrySummary[] | null; error: boolean; next: string | null; more: () => void; busy: boolean; reload: () => void } {
  const key = filterQuery(f);
  // a phone has no sidebar (and no newer/older): nothing to read there
  const [res, reload] = useResource(
    (signal) => (wanted ? api.entries({ ...params(f), limit: 60 }, signal) : Promise.resolve<EntryPage>({ items: [], next: null })),
    [key, wanted],
  );
  useLive(["entries-changed"], reload);
  const [extra, setExtra] = useState<{ key: string; items: EntrySummary[]; next: string | null; busy: boolean }>({ key, items: [], next: null, busy: false });
  const more = extra.key === key ? extra : { key, items: [], next: null, busy: false };
  if (res.state === "ready" && wanted) sideCache = { key, items: res.data.items, next: res.data.next };
  const first = res.state === "ready" ? res.data : sideCache?.key === key ? sideCache : null;
  const next = more.items.length ? more.next : first?.next ?? null;
  const load = () => {
    if (!next || more.busy) return;
    setExtra({ ...more, busy: true });
    api.entries({ ...params(f), limit: 60, cursor: next })
      .then((page) => setExtra((m) => (m.key === key ? { key, items: [...m.items, ...page.items], next: page.next, busy: false } : m)))
      .catch(() => setExtra((m) => ({ ...m, busy: false })));
  };
  return { items: first ? [...first.items, ...more.items] : null, error: res.state === "error" && !first, next, more: load, busy: more.busy, reload };
}

function SideTitled({ items, stem, f, setF, date, error, reload, next, more, busy }: {
  items: EntrySummary[] | null; stem: string; f: Filters; setF: (f: Filters) => void; date: string | undefined; error: boolean;
  reload: () => void; next: string | null; more: () => void; busy: boolean;
}) {
  const [month, setMonth] = useState<string | null>(null);
  const shown = month ?? f.day?.slice(0, 7) ?? date?.slice(0, 7) ?? localDay().slice(0, 7);
  const written = useWrittenDays(shown, f);
  return (
    <div class="side-titled">
      <div class="side-head">
        <h2>Entries</h2>
        <a class="icon-button" href={`/entries${filterQuery(f)}`} data-vt="entry" aria-label="All entries" title="All entries"><Expand /></a>
      </div>
      <div class="side-body">
        <Calendar class="side-calendar" month={shown} marked={written} selected={f.day ?? null} today={localDay()} onMonth={setMonth}
          onSelect={(day) => { setMonth(null); setF({ ...f, day: day ?? undefined }); }} />
        {(f.day || f.kind || f.program || f.audio || f.q) && (
          <p class="side-note">
            {f.day ? `Only ${dayLabel(f.day)}` : "Only the entries you chose"}
            <TextButton onClick={() => setF({ audio: false, q: "" })}>Show all</TextButton>
          </p>
        )}
        {error ? (
          <p class="side-note">The list could not be read. <TextButton onClick={reload}>Retry</TextButton></p>
        ) : items === null ? (
          <Skeleton rows={6} shape="line" />
        ) : items.length === 0 ? (
          <p class="side-note">No entries match.</p>
        ) : (
          <>
            {groupByWeek(items).map((w) => (
              <section class="side-week" key={w.week} aria-label={weekHeader(w.week)}>
                <h3>{weekHeader(w.week)}</h3>
                {w.days.flatMap((d) => d.items).map((e) => <SideRow key={e.stem} entry={e} current={e.stem === stem} vt="page" />)}
              </section>
            ))}
            {next && <Button size="small" variant="quiet" wide class="side-more" disabled={busy} onClick={more}>{busy ? "Loading…" : "Load more"}</Button>}
          </>
        )}
      </div>
    </div>
  );
}

/** The dates and dots: a week's start, each day's numeral and weekday, one dot per entry; a tooltip names it. */
function SideDotsRail({ items, stem, f }: { items: EntrySummary[] | null; stem: string; f: Filters }) {
  const tip = useRef<HTMLDivElement>(null);
  const [at, setAt] = useState<{ e: EntrySummary; x: number; y: number } | null>(null);
  useLayoutEffect(() => {
    const el = tip.current as (HTMLDivElement & { showPopover?: () => void; hidePopover?: () => void }) | null;
    if (!el) return;
    if (at && typeof el.showPopover === "function") {
      try { el.showPopover(); } catch { /* already shown */ }
    }
  }, [at]);
  const show = (e: EntrySummary) => (ev: Event) => {
    const el = ev.currentTarget as HTMLElement;
    const r = el.getBoundingClientRect();
    const edge = el.closest(".side-pane")?.getBoundingClientRect().right ?? r.right;   // beside the rail, not over it
    setAt({ e, x: edge + 8, y: r.top + r.height / 2 });
  };
  return (
    <div class="side-dots">
      <a class="icon-button" href={`/entries${filterQuery(f)}`} data-vt="entry" aria-label="All entries" title="All entries"><ChevronLeft /></a>
      {items === null ? null : groupByWeek(items).map((w) => (
        <section class="dots-week" key={w.week} aria-label={weekHeader(w.week)}>
          <h3>{weekHeader(w.week).replace(/^Week of /, "")}</h3>
          {w.days.map((d) => {
            const { n, w: wd } = dayParts(d.date);
            return (
              <div class={["dots-day", d.items.some((e) => e.stem === stem) && "on"].filter(Boolean).join(" ")} key={d.date}>
                <span class="dots-n">{n}</span>
                <span class="dots-w">{wd}</span>
                <span class="dots-row">
                  {d.items.map((e) => (
                    <a key={e.stem} class={["dot", e.stem === stem && "on"].filter(Boolean).join(" ")} href={`/entries/${e.stem}`} data-vt="page"
                      aria-current={e.stem === stem ? "page" : undefined}
                      aria-label={`${plainTitle(e.title)}, ${dayLabel(e.date)}${e.time ? `, ${e.time}` : ""}`}
                      onPointerEnter={show(e)} onFocus={show(e)} onPointerLeave={() => setAt(null)} onBlur={() => setAt(null)} />
                  ))}
                </span>
              </div>
            );
          })}
        </section>
      ))}
      {at && (
        <div ref={tip} class="dots-tip" popover="manual" role="tooltip" style={{ left: `${at.x}px`, top: `${at.y}px` }}>
          <span class="dots-tip-1">{plainTitle(at.e.title)}{at.e.time ? ` · ${at.e.time}` : ""}</span>
          <span class="dots-tip-2">{clip(at.e.first_line || "…", 110)}</span>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------------------------------------------------
// the entry: its head, the document, her readings at the end, and his way to add to it

function words(text: string): number {
  return text.trim() ? text.trim().split(/\s+/).length : 0;
}

/** The meta line under the title: the month, the hours it spans, how much he wrote, his voice. */
function metaOf(doc: EntryDoc): string[] {
  const his = doc.blocks.filter((b) => b.kind === "his");
  const hours = his.map((b) => hourOf(b.at)).filter(Boolean);
  const { month, year } = dateOf(doc.date);
  const out = [`${month} ${year}`];
  if (hours.length) out.push(hours.length > 1 && hours[0] !== hours[hours.length - 1] ? `${hours[0]}–${hours[hours.length - 1]}` : hours[0] as string);
  if (his.length) out.push(`${his.length} ${his.length === 1 ? "passage" : "passages"}`);
  const n = his.reduce((s, b) => s + (isPending(b.text) ? 0 : words(b.text)), 0);
  if (n) out.push(`${n} ${n === 1 ? "word" : "words"}`);
  const voice = his.reduce((s, b) => s + (b.source === "voice" ? b.meta?.duration ?? 0 : 0), 0);
  if (voice) out.push(`your voice, ${duration(voice)}`);
  return out;
}

function EntryHead({ doc }: { doc: EntryDoc }) {
  const { weekday, d, month, year } = dateOf(doc.date);
  const tags: { key: string; text: string; star?: boolean }[] = [
    ...(doc.meta.starred ? [{ key: "star", text: "starred", star: true }] : []),
    ...(doc.meta.mood ?? []).map((m) => ({ key: `m-${m}`, text: m })),
    ...(doc.meta.people ?? []).map((p) => ({ key: `p-${p}`, text: `with ${p}` })),
  ];
  return (
    <header class="page-head entry-head">
      <div class="entry-numeral numeral" aria-hidden="true">{d}</div>
      <div class="entry-head-main">
        <p class="kicker entry-kicker">{plainTitle(doc.title)}</p>
        <h1 class="display">{weekday}<span class="visually-hidden"> {d} {month} {year}</span></h1>
        <p class="entry-meta">{metaOf(doc).map((m) => <span key={m}>{m}</span>)}</p>
        {tags.length > 0 && (
          <p class="entry-tags">
            {tags.map((t) => <span key={t.key} class="entry-tag">{t.star && <Star aria-hidden="true" />}{t.text}</span>)}
          </p>
        )}
      </div>
    </header>
  );
}

/** Her day reflection, read when its fold first opens. */
function ReflectionBody({ doc }: { doc: EntryDoc }) {
  const stem = doc.analysis.reflection;
  const [res, reload] = useResource(
    (signal) => (stem ? api.reflections({ from: doc.date, to: doc.date, kind: "reflection" }, signal) : Promise.resolve([])),
    [stem, doc.date],
  );
  return (
    <Await res={res} reload={reload} rows={2}>
      {(list) => {
        const r = list.find((x) => x.stem === stem);
        return r ? (
          <div class="reflection-body">
            <p class="refl-title">{r.title}</p>
            <div class="her"><Markdown text={r.body} /></div>
            <a class="refl-link" href={`/insights#${r.stem}`}>In Insights</a>
          </div>
        ) : (
          <Empty>The reflection is not available.</Empty>
        );
      }}
    </Await>
  );
}

/** "- a theme." → "a theme.": the first line of her Markdown, plain, for the fold's one-line peek. */
function firstLine(md: string): string {
  const line = md.split("\n").map((l) => l.replace(/^\s*(?:[-*]|\d+\.)\s+/, "").replace(/[*_`]/g, "").trim()).find(Boolean) ?? "";
  return clip(line, 120);
}

/**
 * Her readings, at the end of the entry (no tabs: round 02, DECISIONS.md WEB-UX-ENTRY-TABS): what she noticed (her
 * annotations and her notes) and her reflection on the day, each a fold that unfolds in place. All in her ink.
 */
function Readings({ doc }: { doc: EntryDoc }) {
  const her = doc.voices.hers;
  const a = doc.analysis;
  const [reflOpened, setReflOpened] = useState(false);
  const noticed = !!a.annotations || a.notes.length > 0;
  const nothing = !noticed && !a.reflection;
  const { weekday } = dateOf(doc.date);
  const noteAt = a.notes.find((n) => n.at)?.at;
  return (
    <section class="readings analysis" aria-labelledby="readings-h">
      <h2 class="book-head" id="readings-h">Her readings</h2>
      {nothing && <Empty>Nothing here yet: {her} adds notes after the entry settles.</Empty>}
      {noticed && (
        <Disclosure class="reading" id="reading-analysis" icon={<Envelope class="reading-env" />}
          head={<><b class="reading-title">What she noticed</b><span class="reading-sub">Analysis · {her}{noteAt ? `, ${hourOf(noteAt)}` : ""}</span></>}
          peek={<span class="her">{firstLine(a.annotations ?? a.notes[0]?.text ?? "")}</span>}>
          <div class="reading-body">
            {a.annotations && (
              <section class="analysis-section" aria-labelledby="ann-h">
                <h3 id="ann-h">{her}'s annotations</h3>
                <div class="her"><Markdown text={a.annotations} /></div>
              </section>
            )}
            {a.notes.length > 0 && (
              <section class="analysis-section" aria-labelledby="notes-h">
                <h3 id="notes-h">{her}'s notes</h3>
                <ol class="her-notes">
                  {a.notes.map((n, i) => (
                    <li class="note" key={i}>
                      {n.at && <time>{n.at}</time>}
                      <div class="her"><Markdown text={n.text} /></div>
                    </li>
                  ))}
                </ol>
              </section>
            )}
          </div>
        </Disclosure>
      )}
      {a.reflection && (
        <Disclosure class="reading" id="reading-reflection" icon={<Envelope class="reading-env" />} onToggle={(o) => { if (o) setReflOpened(true); }}
          head={<><b class="reading-title">{weekday}'s reflection</b><span class="reading-sub">{her} · the day as a whole</span></>}>
          <div class="reading-body">{reflOpened && <ReflectionBody doc={doc} />}</div>
        </Disclosure>
      )}
    </section>
  );
}

/** Add to the entry: a new block of his, at the end. A program session's answers go through the run, where the open
 *  phase is known (the server refuses a block on a program entry that does not name it). */
function Add({ doc, reload, wide }: { doc: EntryDoc; reload: () => void; wide: boolean }) {
  if (doc.program) {
    return (
      <p class="composer-closed">
        {doc.run ? <a href={`/runs/${doc.run}`}>Answer in the program</a> : "This is a program session."} The open phase takes your
        answer there.
      </p>
    );
  }
  if (/^(reflection|week)(-\d+)?$/.test(doc.kind)) {
    return <p class="composer-closed">This is the companion's reflection, not a page of yours. Add your own words as a note from Write.</p>;
  }
  if (!doc.writable) {
    return <p class="composer-closed">This page is yours, not one the journal wrote, so the web app does not add to it.</p>;
  }
  return (
    <section class="add" aria-label="Add to this entry">
      {wide && (
        <p class="add-head">
          <span>Keep writing</span>
          <TextButton onClick={(e) => openWrite({ for: { kind: "entry", stem: doc.stem }, from: e.currentTarget as HTMLElement })}>
            <Pen aria-hidden="true" /> In the writing room
          </TextButton>
        </p>
      )}
      <Composer
        draftKey={`entry:${doc.stem}`}
        label={`Add to this entry, as ${doc.voices.his}`}
        placeholder="Add more, in your own words"
        submitLabel="Add to the entry"
        onSubmit={(words) => api.addBlock(doc.stem, words)}
        onDone={reload}
      />
    </section>
  );
}

// ---------------------------------------------------------------------------------------------------------------------

export function EntryRoute({ stem = "" }: { stem?: string }) {
  const wide = useWide();
  useShellMode({ fold: true, panes: wide });
  const [mode, setMode] = useSidebar();
  const [editing, setEditing] = useState(false);
  const [said, setSaid] = useState<{ text: string; n: number } | null>(null);
  const [f, setFilters] = useState<Filters>(() => filtersOf(Object.fromEntries(new URLSearchParams(rememberedQuery()))));
  const setF = (next: Filters) => {
    rememberQuery(filterQuery(next));
    setFilters(next);
  };
  const [res, reload] = useResource((signal) => api.entry(stem, signal), [stem]);
  useLive(["entries-changed", "ledger-changed", "library-changed", "tick"], reload);   // a day reflection is no "entries-changed", it follows the tick
  useTitle(res.state === "ready" ? plainTitle(res.data.title) : "Entry");
  const side = useSideList(f, wide);
  const go = useNavigate();
  const doc = res.state === "ready" ? res.data : null;

  // a word about what just happened, for a few seconds
  useEffect(() => {
    if (!said) return;
    const t = setTimeout(() => setSaid((s) => (s && s.n === said.n ? { ...s, text: "" } : s)), 4200);
    return () => clearTimeout(t);
  }, [said]);
  const notify = (text: string) => setSaid((s) => ({ text, n: (s?.n ?? 0) + 1 }));

  // newer and older: the neighbours in the list beside it
  const list = side.items ?? [];
  const at = list.findIndex((e) => e.stem === stem);
  const newer = at > 0 ? list[at - 1] : undefined;
  const older = at >= 0 ? list[at + 1] : undefined;
  const step = (e: EntrySummary | undefined) => e && void go(`/entries/${e.stem}`, { kind: "page" });

  // "[" cycles the sidebar, as the entry bar's switch does
  useEffect(() => {
    if (!wide) return;
    const key = (e: KeyboardEvent) => {
      if (e.key !== "[" || e.metaKey || e.ctrlKey || e.altKey || e.defaultPrevented) return;
      if ((e.target as HTMLElement | null)?.closest?.("input, textarea, select, [contenteditable], dialog")) return;
      e.preventDefault();
      setMode(MODES[(MODES.indexOf(mode) + 1) % MODES.length] as SidebarMode);
    };
    document.addEventListener("keydown", key);
    return () => document.removeEventListener("keydown", key);
  });

  // the date slides into the bar once the head has scrolled away
  const sentinel = useRef<HTMLDivElement>(null);
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => {
    const el = sentinel.current;
    if (!el || typeof IntersectionObserver !== "function") return;
    const io = new IntersectionObserver(([e]) => setScrolled(!!e && !e.isIntersecting));
    io.observe(el);
    return () => io.disconnect();
  }, [wide]);

  const back = `/entries${filterQuery(f)}`;
  const date = doc ? dateOf(doc.date) : null;
  const bar = (
    <div class={["entry-bar", scrolled && "scrolled"].filter(Boolean).join(" ")}>
      {wide ? (
        <>
          <a class="icon-button" href={back} data-vt="entry" aria-label="All entries" title="All entries"><ChevronLeft /></a>
          <Segmented label="Beside the entry" iconOnly value={mode} onChange={setMode} class="side-switch"
            options={[
              { value: "list", label: "Titles beside the entry", icon: <SideList /> },
              { value: "rail", label: "Dates and dots beside the entry", icon: <SideDots /> },
              { value: "hidden", label: "The entry alone", icon: <SideNone /> },
            ]} />
          <span class="entry-bar-sep" aria-hidden="true" />
          <IconButton label="Newer entry" disabled={!newer} onClick={() => step(newer)}><ChevronUp /></IconButton>
          <IconButton label="Older entry" disabled={!older} onClick={() => step(older)}><ChevronDown /></IconButton>
          {date && <span class="entry-bar-title" aria-hidden={!scrolled}>{date.weekday} {date.d} {date.month}</span>}
        </>
      ) : (
        <a class="entry-back" href={back} data-vt="pop"><ChevronLeft aria-hidden="true" /> Entries</a>
      )}
      <span class="entry-bar-grow" />
      {doc?.writable && (
        <IconButton label="Mood, people, star" pressed={editing} onClick={() => setEditing(true)} class="entry-meta-button">
          <Star /><span class="visually-hidden">Mood, people, star</span>
        </IconButton>
      )}
    </div>
  );

  const body = (
    <div class="entry-frame">
      {bar}
      <div ref={sentinel} class="entry-sentinel" aria-hidden="true" />
      <div class="entry-doc">
        {res.state === "error" && res.error.status === 404 ? (
          <Empty>Not an engine entry: the journal has no entry by this name.</Empty>
        ) : (
          <Await res={res} reload={reload} rows={4}>
            {(d) => (
              <>
                <EntryHead doc={d} />
                <Document doc={d} onChanged={reload} notify={notify} />
                <Add doc={d} reload={reload} wide={wide} />
                <Readings doc={d} />
                {editing && (
                  <Sheet title="Mood, people, star" onClose={() => setEditing(false)}>
                    <MetaSheet
                      doc={d}
                      onSaved={() => {
                        setEditing(false);
                        reload();
                      }}
                      onReload={() => {
                        setEditing(false);
                        reload();
                      }}
                    />
                  </Sheet>
                )}
              </>
            )}
          </Await>
        )}
      </div>
    </div>
  );

  const toast = said && <Toast open={!!said.text}>{said.text}</Toast>;
  if (!wide) {
    return (
      <div class="entry-solo">
        {body}
        {toast}
      </div>
    );
  }
  return (
    <>
      {mode !== "hidden" && (
        <Pane class={`side-pane ${mode}`} glow={false} vt="page" label="Entries">
          {mode === "list" ? (
            <SideTitled items={side.items} stem={stem} f={f} setF={setF} date={doc?.date} error={side.error} reload={side.reload}
              next={side.next} more={side.more} busy={side.busy} />
          ) : (
            <SideDotsRail items={side.items} stem={stem} f={f} />
          )}
        </Pane>
      )}
      <Pane entry class="entry-pane" vt="entrypane" label={doc ? plainTitle(doc.title) : "The entry"}>
        {body}
      </Pane>
      {toast}
    </>
  );
}
