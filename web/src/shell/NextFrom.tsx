/**
 * "Next from Ada" at the foot of the rail: what she sends next today, from the today read the app already has (the
 * practices still to come, with their hour), titled from the library. A quiet read: it never raises the offline
 * ribbon, which speaks for the view on screen. Read again when he moves between pages (at most once a minute) and
 * when the tab comes back to the front. Says nothing when there is nothing more today, or the read fails.
 */
import { useLocation } from "preact-iso";
import { useEffect, useRef, useState } from "preact/hooks";
import { api } from "../api";
import type { LibraryItem, Today } from "../types";

export interface Next {
  who: string | null;
  waiting: number;
  items: { title: string; at: string }[];
}

export function nextFrom(today: Today, library: LibraryItem[]): Next {
  const title = (id: string) => library.find((p) => p.id === id)?.title ?? id;
  const items = today.practices.rows
    .filter((r) => r.state === "upcoming" && r.at)
    .sort((a, b) => (a.at as string).localeCompare(b.at as string))
    .slice(0, 2)
    .map((r) => ({ title: title(r.id), at: r.at as string }));
  return { who: today.voices?.hers ?? null, waiting: today.practices.open.length, items };
}

/** The rail's read: null until it lands (or when it fails), and only where the rail is shown. */
export function useNext(enabled: boolean): Next | null {
  const { path } = useLocation();
  const [next, setNext] = useState<Next | null>(null);
  const last = useRef(0);
  const [round, setRound] = useState(0);
  useEffect(() => {
    const back = () => {
      if (document.visibilityState === "visible") setRound((n) => n + 1);
    };
    document.addEventListener("visibilitychange", back);
    return () => document.removeEventListener("visibilitychange", back);
  }, []);
  useEffect(() => {
    if (!enabled || Date.now() - last.current < 60_000) return;
    last.current = Date.now();
    const ctl = new AbortController();
    Promise.all([api.todayQuiet(ctl.signal), api.libraryQuiet(ctl.signal).catch(() => [] as LibraryItem[])])
      .then(([t, lib]) => setNext(nextFrom(t, lib)))
      .catch(() => {
        if (!ctl.signal.aborted) setNext(null);
      });
    return () => ctl.abort();
  }, [enabled, path, round]);
  return enabled ? next : null;
}

export function NextFrom({ next }: { next: Next | null }) {
  if (!next) return null;
  return (
    <section class="rail-next" aria-label={next.who ? `Next from ${next.who}` : "Next"}>
      <p class="rail-next-head">
        <span class="her-mark" aria-hidden="true" />
        {next.who ? `Next from ${next.who}` : "Next today"}
      </p>
      {next.items.length ? (
        <ul>
          {next.items.map((i) => (
            <li key={`${i.title}${i.at}`}>
              {i.title} <span class="rail-next-at">at <time>{i.at}</time></span>
            </li>
          ))}
        </ul>
      ) : (
        <p class="rail-next-none">Nothing more today.</p>
      )}
    </section>
  );
}
