/**
 * The write room: where he writes, full screen, opened from anything that says Write (the rail, the tab bar's centre,
 * the N key, Today's Answer and Speak it, `openWrite()` anywhere: shell/write.ts). It blooms out of the button pressed
 * (a clip-path circle, so it needs no View Transitions) while the page behind recedes, and folds back into it.
 *
 * What is in it: her letter at the top when he is answering (ui Letter, never fastened), or a quiet start when it is a
 * new note, which also offers the prompts still open and the programs he could start (the old "+" menu's other jobs);
 * his words large in the book's roman, with **focus** on by default: every paragraph but the one with the caret greys.
 * The page is a real textarea (IME, paste, undo and spellcheck stay the browser's own) with a mirror of its text drawn
 * underneath, which does the greying; the textarea's own letters are transparent over it.
 *
 * Every rule of the composer holds here, through `useCompose`: the draft in sessionStorage on every keystroke (so
 * Escape or Close keeps it), the retry that is the same write (`client_id`), the upload reused or redone, the offline
 * line, a 422 (the /x refusal) shown without a Retry. A send says what it made ("Saved as a note.") and opens the
 * entry from there.
 */
import type { ComponentChildren } from "preact";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "preact/hooks";
import { api, type Words } from "../api";
import { longDay } from "../format";
import { useAction, useResource, type Resource } from "../hooks";
import { Close, Focus, Pen, Send, Wave } from "../icons";
import { isProgramBacked } from "../labels";
import { closeWrite, useNavigate, type WriteRequest } from "../shell";
import type { Created, LibraryItem, PracticeRow, Prompt, Today } from "../types";
import { AudioPlayer, Button, Disclosure, Empty, ErrorLine, IconButton, Letter, Skeleton, TextButton, reducedMotion } from "../ui";
import { KeptWords, OfflineNote, useCompose } from "./Composer";
import { Recorder } from "./Recorder";
import "./WriteRoom.css";

// ── the page's paragraphs: what focus greys ────────────────────────────────────────────────────────────────────────

/** One paragraph of his page: a line as he typed it (Enter starts the next), with its place in the text. */
export interface Para {
  start: number;
  end: number;
  text: string;
}

/**
 * The page's paragraphs. In the room a paragraph is what lies between two presses of Enter: each line he typed,
 * however many rows it wraps to (a blank line is a paragraph with nothing in it). Their offsets cover the text, with
 * the newline between two of them belonging to neither.
 */
export function paragraphs(text: string): Para[] {
  const out: Para[] = [];
  let start = 0;
  for (const line of text.split("\n")) {
    out.push({ start, end: start + line.length, text: line });
    start += line.length + 1;
  }
  return out;
}

/** The paragraphs the caret (or the selection, `start` to `end`) is in: the ones focus leaves lit. */
export function litParagraphs(paras: Para[], start: number, end = start): number[] {
  const [a, b] = start <= end ? [start, end] : [end, start];
  return paras.flatMap((p, i) => (p.end >= a && p.start <= b ? [i] : []));
}

/** "142 words · 1 min" (reading time, at 200 a minute), or the invitation when the page is empty. */
export function wordCount(text: string): string {
  const n = (text.trim().match(/\S+/g) ?? []).length;
  if (n === 0) return "Begin anywhere";
  return `${n} ${n === 1 ? "word" : "words"} · ${Math.max(1, Math.round(n / 200))} min`;
}

const hhmm = (t: number) => {
  const d = new Date(t);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
};
const clock = (s: number) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;

/** Focus is on by default; turning it off holds for this tab, not stored anywhere. */
let focusOn = true;

// ── what the room is for ─────────────────────────────────────────────────────────────────────────────────────────

type Target = NonNullable<WriteRequest["for"]>;

interface Day {
  today: Today | null;
  library: LibraryItem[];
}

/** The row of today's practices an answer is for: its own slot when it was named, else the first still open. */
export function rowFor(rows: PracticeRow[], practice: string, slot?: number): { row: PracticeRow; slot: number } | null {
  if (slot !== undefined && rows[slot]?.id === practice) return { row: rows[slot] as PracticeRow, slot };
  const i = rows.findIndex((r) => r.id === practice && (r.state === "open" || r.state === "missed"));
  const j = i >= 0 ? i : rows.findIndex((r) => r.id === practice);
  return j >= 0 ? { row: rows[j] as PracticeRow, slot: j } : null;
}

/** Her letter, from a prompt as he was sent it. */
function HerLetter({ prompt, hers }: { prompt: Prompt; hers: string }) {
  const asked = prompt.asked.map((q) => q.question);
  // sent with the plain opener and nothing asked: the questions were the message, so they stand open (as on Today)
  const bare = !prompt.opener && asked.length === 0;
  return (
    <Letter class="room-letter" from={hers} at={prompt.sent_at_local ?? undefined} opener={prompt.opener ?? undefined}
      asked={asked} questions={bare ? [] : prompt.questions} askedIds={prompt.asked.map((q) => q.id)}>
      {bare && prompt.questions.length > 0 && (
        <ol class="room-questions">
          {prompt.questions.map((q) => (
            <li key={q.id}>{q.text}</li>
          ))}
        </ol>
      )}
    </Letter>
  );
}

// ── the room ─────────────────────────────────────────────────────────────────────────────────────────────────────

const EXIT_MS = 460;
const WIDE = "(min-width: 761px)";

/** Make everything but `keep` (and its ancestors) inert; returns the undo. For a browser without showModal. */
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

/** Where the room grows from and folds back into: the centre of the control pressed, or the foot of the screen. */
function origin(el: HTMLElement | null | undefined): [number, number] {
  if (el && el.isConnected && typeof el.getBoundingClientRect === "function") {
    const r = el.getBoundingClientRect();
    if (r.width > 0 && r.height > 0) return [r.left + r.width / 2, r.top + r.height / 2];
  }
  const w = typeof innerWidth === "number" ? innerWidth : 0;
  const h = typeof innerHeight === "number" ? innerHeight : 0;
  return [w / 2, h - 60];
}

export function WriteRoom({ req }: { req: WriteRequest }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [open, setOpen] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const [target, setTarget] = useState<Target>(req.for ?? { kind: "note" });
  const [made, setMade] = useState<Created | null>(null);
  const go = useNavigate();

  // the day: the open prompts and her letter for an answer, the offers on a new note, the hour in the margin
  const [day, reloadDay] = useResource<Day>(async (signal) => {
    if (target.kind === "entry") return { today: null, library: [] };
    const [today, library] = await Promise.all([
      api.today(signal),
      api.library(signal).catch((e: unknown) => {
        if (e instanceof DOMException) throw e;
        return [] as LibraryItem[];
      }),
    ]);
    return { today, library };
  }, [target.kind]);
  const [doc, reloadDoc] = useResource(
    (signal) => (target.kind === "entry" ? api.entry(target.stem, signal) : Promise.resolve(null)),
    [target.kind === "entry" ? target.stem : ""],
  );

  const leave = useRef(() => {});
  leave.current = () => {
    if (leaving) return;
    document.documentElement.classList.remove("room-open");
    const d = dialog.current;
    if (reducedMotion() || !d) return closeWrite();
    const [x, y] = origin(req.from);
    d.style.setProperty("--bx", `${x}px`);
    d.style.setProperty("--by", `${y}px`);
    setLeaving(true);
    setOpen(false);
    setTimeout(() => closeWrite(), EXIT_MS);
  };

  // open: into the top layer (a modal dialog: the page behind is inert, Tab stays in the room), then bloom
  useLayoutEffect(() => {
    const d = dialog.current;
    if (!d) return;
    const [x, y] = origin(req.from);
    d.style.setProperty("--bx", `${x}px`);
    d.style.setProperty("--by", `${y}px`);
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
    document.documentElement.classList.add("room-open");
    let raf = 0;
    if (reducedMotion() || typeof requestAnimationFrame !== "function") setOpen(true);
    else raf = requestAnimationFrame(() => requestAnimationFrame(() => setOpen(true)));
    return () => {
      if (raf && typeof cancelAnimationFrame === "function") cancelAnimationFrame(raf);
      undo();
      document.documentElement.classList.remove("room-open");
      if (typeof d.close === "function" && d.open) d.close();
    };
  }, []);

  // Escape closes (the draft is already kept); from the first moment the room is on screen
  useLayoutEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !e.defaultPrevented) {
        e.preventDefault();
        leave.current();
      }
    };
    document.addEventListener("keydown", key);
    return () => document.removeEventListener("keydown", key);
  }, []);

  const hers = day.state === "ready" ? (day.data.today?.voices?.hers ?? "the companion")
    : doc.state === "ready" && doc.data ? doc.data.voices.hers : "the companion";
  const titleOf = (id: string) => (day.state === "ready" ? day.data.library.find((p) => p.id === id)?.title : undefined) ?? id;

  let page: Page;
  if (target.kind === "answer") {
    const title = titleOf(target.practice);
    const found = day.state === "ready" && day.data.today ? rowFor(day.data.today.practices.rows, target.practice, target.slot) : null;
    const slot = target.slot ?? found?.slot ?? 0;
    page = {
      key: `reply:${target.practice}:${slot}`,
      where: title,
      when: found?.row.sent ? `her letter of ${found.row.sent}` : "an answer",
      label: `Your answer to ${title}`,
      placeholder: "Answer in your own words",
      send: "Send your answer",
      submit: (words) => api.createEntry({ practice: target.practice, ...words }),
      head: day.state === "loading" ? <Skeleton rows={3} shape="line" />
        : day.state === "error" ? <ErrorLine error={day.error} onRetry={reloadDay} />
        : found?.row.prompt ? <HerLetter prompt={found.row.prompt} hers={hers} /> : null,
    };
  } else if (target.kind === "entry") {
    const d = doc.state === "ready" ? doc.data : null;
    page = {
      key: `entry:${target.stem}`,
      where: d ? `${d.title}, continued` : "The entry, continued",
      when: d ? `adding to ${longDay(d.date)}` : "",
      label: d ? `More for ${d.title}` : "More for the entry",
      placeholder: "Carry on in your own words",
      send: "Add to the entry",
      submit: async (words) => {
        const b = await api.addBlock(target.stem, words);
        return { stem: b.stem ?? target.stem } as Created;
      },
      closed: d && !d.writable ? "This page is yours: the journal does not write into it." : undefined,
      head: doc.state === "loading" ? <Skeleton rows={2} shape="line" />
        : doc.state === "error" ? <ErrorLine error={doc.error} onRetry={reloadDoc} />
        : d?.prompt ? <HerLetter prompt={d.prompt} hers={hers} /> : null,
    };
  } else {
    const today = day.state === "ready" ? day.data.today : null;
    page = {
      key: "quick-note",
      where: "A note",
      when: today ? longDay(today.date) : "just for you",
      label: "A note",
      placeholder: "Begin anywhere. It's yours.",
      send: "Put it in the journal",
      submit: (words) => api.createEntry(words),
      head: (
        <Start day={day} titleOf={titleOf}
          onAnswer={(practice, slot) => setTarget({ kind: "answer", practice, slot })}
          onStarted={(id) => {
            leave.current();
            void go(`/library/${id}`);
          }} />
      ),
    };
  }
  const hour = day.state === "ready" ? day.data.today?.time : undefined;

  return (
    <dialog ref={dialog} role="dialog" aria-modal="true" aria-label="Write"
      class={["room", open && "open", leaving && "leaving"].filter(Boolean).join(" ")}
      onCancel={(e) => {
        e.preventDefault();
        leave.current();
      }}>
      {made ? (
        <Done made={made} onClose={() => leave.current()} onAgain={() => {
          setMade(null);
          setTarget({ kind: "note" });
        }} />
      ) : (
        <Desk page={page} hour={hour} voice={!!req.voice} onClose={() => leave.current()} onMade={setMade} />
      )}
    </dialog>
  );
}

interface Page {
  key: string;
  where: string;
  when: string;
  label: string;
  placeholder: string;
  send: string;
  submit: (words: Words) => Promise<Created>;
  head: ComponentChildren;
  closed?: string | undefined;
}

/** The room's working state: the top bar, her letter or the start, his page, the dock. */
function Desk({ page, hour, voice, onClose, onMade }: {
  page: Page;
  hour: string | undefined;
  voice: boolean;
  onClose: () => void;
  onMade: (made: Created) => void;
}) {
  const made = useRef<Created | null>(null);
  const c = useCompose({
    draftKey: page.key,
    onSubmit: async (words) => {
      made.current = await page.submit(words);
    },
    onDone: () => made.current && onMade(made.current),
  });
  const area = useRef<HTMLTextAreaElement>(null);
  const [focus, setFocus] = useState(focusOn);
  const [sel, setSel] = useState<[number, number]>(() => [c.text.length, c.text.length]);
  const [rec, setRec] = useState<{ on: boolean; s: number }>({ on: false, s: 0 });
  const id = `room-${page.key}`;

  const toggleFocus = () => {
    focusOn = !focus;
    setFocus(focusOn);
  };

  // where the caret is: on every input, key, click and selection change in the page
  const track = () => {
    const t = area.current;
    if (t) setSel([t.selectionStart ?? 0, t.selectionEnd ?? 0]);
  };
  useEffect(() => {
    const onSel = () => {
      if (document.activeElement === area.current) track();
    };
    document.addEventListener("selectionchange", onSel);
    return () => document.removeEventListener("selectionchange", onSel);
  }, []);

  // a wide screen begins with the caret at the end of his words; a phone waits for his tap (the keyboard would cover
  // her letter)
  useEffect(() => {
    const t = area.current;
    if (!t || typeof matchMedia !== "function" || !matchMedia(WIDE).matches) return;
    t.focus({ preventScroll: true });
    t.setSelectionRange(t.value.length, t.value.length);
  }, [page.key]);

  // the page grows with its mirror, so the textarea never scrolls inside itself
  useLayoutEffect(() => {
    if (area.current) area.current.scrollTop = 0;
  });

  const paras = useMemo(() => paragraphs(c.text), [c.text]);
  // (a caret past the end, after the box took another draft, is at its end: the last paragraph is lit)
  const lit = useMemo(() => new Set(litParagraphs(paras, Math.min(sel[0], c.text.length), Math.min(sel[1], c.text.length))), [paras, sel]);

  const status = c.keptAt
    ? (c.keptAt.stored ? `Draft kept · ${hhmm(c.keptAt.at)}` : "This browser keeps no draft")
    : c.text.trim() ? "Draft kept" : "Nothing written yet";
  const count = rec.on ? `${clock(rec.s)} · tap to stop` : wordCount(c.text);

  return (
    <>
      <header class="room-top">
        <IconButton label="Close" onClick={onClose}>
          <Close />
        </IconButton>
        <p class="room-ctx">
          <b>{page.where}</b>
          {page.when && <> · {page.when}</>}
        </p>
        <div class="room-tools">
          <span class="room-saved">
            <span class="room-dot" key={c.keptAt?.at ?? 0} aria-hidden="true" />
            <span class="room-saved-words" role="status">{status}</span>
          </span>
          <button type="button" class="room-focus" aria-pressed={focus} onClick={toggleFocus}
            title="Focus: grey all but the paragraph you're in (Ctrl .)" aria-keyshortcuts="Control+. Meta+.">
            <Focus />
            <span class="room-focus-label">Focus</span>
          </button>
        </div>
      </header>

      <div class="room-body">
        <div class="room-col">
          {page.head && <div class="room-head">{page.head}</div>}
          <KeptWords c={c} />
          {page.closed ? (
            <p class="composer-closed">{page.closed}</p>
          ) : (
            <div class={["room-page", focus && "focus", c.busy && "busy"].filter(Boolean).join(" ")}>
              {hour && <time class="room-hour" aria-hidden="true">{hour}</time>}
              <div class="room-mirror" aria-hidden="true">
                {paras.map((p, i) => (
                  <span key={i}>
                    {i > 0 && "\n"}
                    <span class={lit.has(i) ? "room-p lit" : "room-p"}>{p.text}</span>
                  </span>
                ))}
                {"\n​"}
              </div>
              <label class="visually-hidden" for={id}>{page.label}</label>
              <textarea
                id={id}
                ref={area}
                class="room-text"
                value={c.text}
                placeholder={page.placeholder}
                disabled={c.busy}
                spellcheck
                onInput={(e) => {
                  c.edit((e.currentTarget as HTMLTextAreaElement).value);
                  track();
                }}
                onKeyUp={track}
                onClick={track}
                onFocus={track}
                onSelect={track}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                    e.preventDefault();
                    void c.send();
                  } else if (e.key === "." && (e.metaKey || e.ctrlKey)) {
                    e.preventDefault();
                    toggleFocus();
                  }
                }}
              />
            </div>
          )}
          {c.clip && (
            <div class="room-clip">
              <p class="room-clip-line">
                <Wave />
                Your voice · {clock(c.clip.seconds)} · the words arrive once it's sent
              </p>
              {c.clip.url && <AudioPlayer src={c.clip.url} duration={c.clip.seconds} label="your recording" preload="metadata" />}
              <TextButton onClick={c.drop} disabled={c.busy}>Discard</TextButton>
            </div>
          )}
          {!c.online && <OfflineNote clip={!!c.clip} />}
          {c.error && <ErrorLine error={c.error} {...(c.error.status === 422 ? {} : { onRetry: () => void c.send() })} />}
        </div>
      </div>

      {!page.closed && (
        <div class="room-dock">
          <span class={["room-count", rec.on && "recording"].filter(Boolean).join(" ")} aria-live="off">{count}</span>
          <div class="room-orb">
            {!c.clip && <Recorder orb start={voice} onRecorded={c.record} disabled={c.busy}
              onRecording={(on, s) => setRec({ on, s })} />}
          </div>
          <div class="room-send">
            <Button variant="primary" icon={<Send />} disabled={!c.has || c.busy} onClick={() => void c.send()}>
              {c.busy ? "Saving…" : page.send}
            </Button>
          </div>
        </div>
      )}
    </>
  );
}

/** A new note's quiet start: just him, or (the old menu's other jobs) a prompt still open, or a program to begin. */
function Start({ day, titleOf, onAnswer, onStarted }: {
  day: Resource<Day>;
  titleOf: (id: string) => string;
  onAnswer: (practice: string, slot: number) => void;
  onStarted: (id: string) => void;
}) {
  const [starting, setStarting] = useState(false);
  const rows = day.state === "ready" ? (day.data.today?.practices.rows ?? []) : [];
  const open = rows.map((r, slot) => ({ r, slot })).filter(({ r }) => r.state === "open");
  return (
    <div class="room-start">
      <p class="room-none">No prompt, no questions: just you.</p>
      {open.length > 0 && (
        <ul class="room-offers" aria-label="Or answer a prompt">
          {open.map(({ r, slot }) => (
            <li key={`${r.id}#${slot}`}>
              <Button variant="quiet" size="small" icon={<Pen />} onClick={() => onAnswer(r.id, slot)}>
                Answer {titleOf(r.id)}
                {r.sent && <span class="room-offer-when"> (open since {r.sent})</span>}
              </Button>
            </li>
          ))}
        </ul>
      )}
      <Disclosure class="room-programs" head="Start a program" open={starting} onToggle={setStarting}>
        {starting ? <StartList onStarted={onStarted} /> : <span />}
      </Disclosure>
    </div>
  );
}

/** The programs he could begin: enabled, and with no run going. The server starts one a moment after it is asked. */
function StartList({ onStarted }: { onStarted: (id: string) => void }) {
  const [res, reload] = useResource(async (signal) => {
    const [items, runs] = await Promise.all([api.library(signal), api.runs(signal).catch(() => [])]);
    const live = new Set(runs.filter((r) => r.status === "active" || r.status === "paused").map((r) => r.program));
    return items.filter((i) => isProgramBacked(i) && i.enabled && !live.has(i.id));
  }, []);
  const act = useAction();
  if (res.state === "loading") return <Skeleton rows={2} shape="line" />;
  if (res.state === "error") return <ErrorLine error={res.error} onRetry={reload} />;
  if (res.data.length === 0) return <Empty>Every program is already running, or none is turned on.</Empty>;
  return (
    <>
      <ul class="room-program-list">
        {res.data.map((i) => (
          <li key={i.id}>
            <Button size="small" disabled={act.busy} onClick={async () => {
              const done = await act.run(() => api.startRun(i.id));
              if (done) onStarted(i.id);
            }}>
              {i.emoji} {i.title}
            </Button>
          </li>
        ))}
      </ul>
      {act.error && <ErrorLine error={act.error} />}
    </>
  );
}

/** After a send: what it made, and the way to it. */
function Done({ made, onClose, onAgain }: { made: Created; onClose: () => void; onAgain: () => void }) {
  const first = useRef<HTMLAnchorElement>(null);
  useEffect(() => first.current?.focus({ preventScroll: true }), []);
  return (
    <>
      <header class="room-top">
        <IconButton label="Close" onClick={onClose}>
          <Close />
        </IconButton>
      </header>
      <div class="room-body">
        <div class="room-col room-done">
          <p class="saved" role="status">
            {made.rule === "/j" ? "Saved as a note. " : "Saved. "}
            <a ref={first} href={`/entries/${made.stem}`} onClick={() => closeWrite()}>
              Open the entry
            </a>
          </p>
          <div class="room-done-actions">
            <Button onClick={onAgain}>Write another</Button>
            <Button variant="quiet" onClick={onClose}>Close</Button>
          </div>
        </div>
      </div>
    </>
  );
}
