/** The API's answers, as the app understands them. The parsers drop every field not named here. */
import {
  arr, bool, json, nullable, num, obj, oneOf, opt, rec, str,
  type Infer,
} from "./schema";

export const Flags = obj({
  annotated: bool,
  has_conversation: bool,
  transcript_pending: bool,
  audio: opt(bool),
});

export const Meta = obj({
  mood: opt(arr(str)),
  people: opt(arr(str)),
  starred: opt(bool),
});

export const EntrySummary = obj({
  stem: str,
  date: str,
  kind: str,
  practice: nullable(str),
  program: nullable(str),
  session: nullable(num),
  time: nullable(str),
  title: str,
  emoji: str,
  first_line: str,
  flags: Flags,
  meta: Meta,
});
export type EntrySummary = Infer<typeof EntrySummary>;

export const EntryPage = obj({ items: arr(EntrySummary), next: nullable(str) });
export type EntryPage = Infer<typeof EntryPage>;

// ── The prompt as he was sent it (the entry's document and Today's rows) ──

export const Prompt = obj({
  sent_at_local: nullable(str),
  opener: nullable(str),
  opener_source: nullable(str),
  asked: arr(obj({ id: str, question: str })),
  questions: arr(obj({ id: str, text: str, section: nullable(str) })),
  phases: opt(arr(obj({ number: num, title: str }))),   // a program session's phases, for the dividers
});
export type Prompt = Infer<typeof Prompt>;

export const Voices = obj({ his: str, hers: str });
export type Voices = Infer<typeof Voices>;

// ── Today ────────────────────────────────────────────────────────────────

export const PracticeRow = obj({
  id: str,
  state: oneOf("upcoming", "open", "answered", "missed"),
  at: opt(str),
  sent: opt(str),
  answered: opt(str),
  stem: opt(str), // the entry of the prompt sent for this row
  prompt: opt(Prompt), // the whole message, for a row that was sent: Today shows it in full, whatever its state
});
export type PracticeRow = Infer<typeof PracticeRow>;

export const WeekDay = obj({
  date: str,
  weekday: str,
  answered: num,
  missed: num,
  state: oneOf("answered", "partial", "missed", "none"),
});
export type WeekDay = Infer<typeof WeekDay>;

export const Today = obj({
  date: str,
  weekday: str,
  time: str,
  timezone: str,
  location: nullable(str),
  practices: obj({ rows: arr(PracticeRow), open: arr(str) }),
  program: nullable(obj({ text: str })),
  entries: arr(obj({ stem: str, first_line: str })),
  reflection: nullable(obj({ date: str, asked: str, stem: opt(str) })),
  tasks: nullable(obj({ count: num, names: arr(str) })),
  weather: nullable(str),
  week: arr(WeekDay),
  voices: opt(Voices),   // absent from an older server: the prompt is then labelled without her name
  location_source: opt(nullable(str)),   // "openstreetmap" while OSM named the place: the header credits it
});
export type Today = Infer<typeof Today>;

// ── The entry document ───────────────────────────────────────────────────

export const Block = obj({
  kind: oneOf("his", "hers"),
  key: str,
  at: nullable(str),
  source: opt(str),
  text: str,
  audio: nullable(str),
  images: opt(arr(str)),   // pictures kept with the block (images.py), shown above his words
  phase: nullable(num),
  interrupt: opt(num),   // a session-10 interrupt this block answers: labelled, and not a phase's words
  reply_to: nullable(str),
  meta: opt(obj({ duration: opt(num), file_name: opt(str) })),
});
export type Block = Infer<typeof Block>;

export const EntryDoc = obj({
  stem: str,
  date: str,
  kind: str,
  practice: nullable(obj({ id: str, title: str, emoji: str })),
  program: nullable(str),
  session: nullable(num),
  run: nullable(str),
  title: str,
  voices: Voices,
  prompt: nullable(Prompt),
  blocks: arr(Block),
  summary: nullable(str),
  analysis: obj({
    annotations: nullable(str),
    notes: arr(obj({ at: nullable(str), text: str })),
    reflection: nullable(str),
  }),
  flags: Flags,
  meta: Meta,
  mtime: num,
  writable: bool,   // false for his own page that is only named like an entry: the server refuses writes to it
});
export type EntryDoc = Infer<typeof EntryDoc>;

export const Reflection = obj({
  stem: str,
  date: str,
  kind: nullable(str),
  title: str,
  body: str,
  sources: arr(str),
});
export type Reflection = Infer<typeof Reflection>;

export const Week = obj({
  week_end: str,
  entries: num,
  words: num,
  answered: num,
  missed: num,
  program_progress: rec(json),
  review: nullable(str),
  reflections: arr(str),
});
export type Week = Infer<typeof Week>;

// ── Library ──────────────────────────────────────────────────────────────

export const Slot = obj({ time: str, weekday: nullable(num) });
export type Slot = Infer<typeof Slot>;

export const LibraryItem = obj({
  id: str,
  kind: str,
  title: str,
  emoji: str,
  group: str,
  source: oneOf("builtin", "user", "fork"),
  enabled: bool,
  schedule: arr(Slot),
  closes: nullable(str),
  sessions: opt(num),
  phases_total: opt(num),
  active_run: nullable(str),
  mtime: nullable(num),
  /** An edited built-in (`source: "fork"`): whether the shipped default has changed since he edited it (null: not known). */
  default_changed: nullable(bool),
});
export type LibraryItem = Infer<typeof LibraryItem>;

export const Question = obj({ id: str, text: str, pinned: bool });
export type Question = Infer<typeof Question>;

export const Section = obj({ title: nullable(str), questions: arr(Question) });
export type Section = Infer<typeof Section>;

/** A practice's opener as the editor edits it. Absent keys are the server's defaults (a program has only `plain`). */
export const Opener = obj({ mode: opt(str), shape: opt(str), limit: opt(num), plain: opt(str) });
export type Opener = Infer<typeof Opener>;

/** His edit of a built-in: whether the default changed since he made it (null: not known), when he began it, and
 *  whether it is in force (false: his file does not load, so the built-in is used meanwhile). */
export const Override = obj({ default_changed: nullable(bool), edited_at: nullable(str), in_force: bool });
export type Override = Infer<typeof Override>;

const libraryOneShape = {
  definition: obj({
    id: str,
    kind: str,
    title: str,
    emoji: str,
    source: oneOf("builtin", "user", "fork"),
    enabled: bool,
    schedule: arr(Slot),
    closes: nullable(str),
    companion: str,
    annotate: opt(bool),
    collapse_hours: opt(num),
    group: opt(str),
    opener: Opener,
    sections: arr(Section),
  }),
  raw: str,
  problems: arr(str),
  mtime: nullable(num),
  // a program's (or situational definition's) sessions with every phase, questions or not; absent for a practice.
  // `file` is the name a save of the session's file takes (a program's `sessions/<name>.md`; absent in a situational
  // definition, whose one file is `raw`), `mtime` that file's.
  sessions: opt(
    arr(
      obj({
        number: num,
        title: str,
        raw: opt(str),
        file: opt(str),
        mtime: opt(num),
        phases: arr(obj({ number: num, title: str, type: str, questions: arr(Question) })),
      }),
    ),
  ),
  /** Absent or null unless this is a built-in he has edited. */
  override: nullable(Override),
};
export const LibraryOne = obj(libraryOneShape);
export type LibraryOne = Infer<typeof LibraryOne>;

/** `GET /library/{id}/default`: the shipped definition, and two unified diffs: what changed in the default since his
 *  edit began (null: nothing, or not known) and the default against his version (null: he has none; "": the same). */
export const LibraryDefault = obj({
  ...libraryOneShape,
  diff: obj({ since: nullable(str), yours: nullable(str) }),
});
export type LibraryDefault = Infer<typeof LibraryDefault>;

/** The practice the editor works on: `LibraryOne["definition"]` without the fields only the server fills. */
export type Definition = LibraryOne["definition"];

/** `PUT /library/{id}?validate=1`: nothing written; the canonical file the form would save, and what is wrong. */
export const Validation = obj({ valid: bool, problems: arr(str), raw: nullable(str) });
export type Validation = Infer<typeof Validation>;

// ── Programs: runs ───────────────────────────────────────────────────────

export const RunSession = obj({
  number: num,
  title: str,
  status: str,
  entry: nullable(str),
  phases_total: num,
  started_at: nullable(str),
  completed_at: nullable(str),
});
export type RunSession = Infer<typeof RunSession>;

export const Scheduled = obj({
  session: opt(num),
  n: opt(num),
  of: opt(num),
  offset: opt(str),
  local: opt(str),   // "YYYY-MM-DDTHH:MM" wall clock
  status: opt(str),
  text: opt(str),
});
export type Scheduled = Infer<typeof Scheduled>;

const runShape = {
  id: str,
  program: str,
  title: str,
  emoji: str,
  status: str,
  started_at: nullable(str),
  completed_at: nullable(str),
  paused_since: nullable(str),
  last_activity_at: nullable(str),
  current: nullable(obj({ session: num, phase: num })),
  where: nullable(str),
  text: str,
  sessions: arr(RunSession),
  insights_entry: nullable(str),
  scheduled: arr(Scheduled),
};
export const Run = obj(runShape);
export type Run = Infer<typeof Run>;

export const Phase = obj({
  session: num,
  phase: num,
  type: str,
  title: str,
  header: str,
  blocks: arr(obj({ heading: nullable(str), text: str })),
  questions: arr(obj({ id: str, text: str })),
  hint: opt(str),
  sent_text: nullable(str),
  delivered: bool,
  open: bool,
  answered: bool,
  skipped: bool,
  phases_total: num,
});
export type Phase = Infer<typeof Phase>;

export const RUN_ACTIONS = ["next", "skip", "back", "pause", "resume", "abandon"] as const;
export type RunAction = (typeof RUN_ACTIONS)[number];

export const RunOne = obj({
  ...runShape,
  phase: nullable(Phase),
  keyboard: arr(oneOf(...RUN_ACTIONS)),
});
export type RunOne = Infer<typeof RunOne>;

// ── Writes: what the server answers ──────────────────────────────────────

export const Spawned = obj({ spawned: bool });
export const Created = obj({ stem: str, rule: opt(str) });
export type Created = Infer<typeof Created>;
export const Block202 = obj({ key: nullable(str), stem: opt(str), advance: opt(oneOf("settle", "next", "tick")) });
export type BlockWritten = Infer<typeof Block202>;
export const UploadMade = obj({ upload: str, bytes: num, mime: str });
export type UploadMade = Infer<typeof UploadMade>;
export const BlockEdited = obj({ block: nullable(Block), mtime: num });
export type BlockEdited = Infer<typeof BlockEdited>;
export const BlockRemoved = obj({ mtime: num });
export type BlockRemoved = Infer<typeof BlockRemoved>;
export const MetaWritten = obj({ meta: Meta, mtime: num });
export type MetaWritten = Infer<typeof MetaWritten>;
export const SettingsWritten = obj({ written: rec(str) });
export type SettingsWritten = Infer<typeof SettingsWritten>;

/** "Use my location": the place and time zone it was taken as (never the position). */
export const Located = obj({ written: rec(str), location: nullable(str), timezone: nullable(str), local_time_now: nullable(str) });
export type Located = Infer<typeof Located>;

// ── Settings, status, doctor, health ─────────────────────────────────────

export const Settings = obj({ settings: rec(json), origins: rec(str), profile: opt(str) });
export type Settings = Infer<typeof Settings>;

/** How this browser is signed in: `tailscale` (by the tailnet's identity, with the login: nothing to pair), else
 *  `cookie` (paired) or `token`. */
export const Session = obj({ via: str, login: nullable(str) });
export type Session = Infer<typeof Session>;

export const DoctorRow = obj({ check: str, status: str, detail: str });
export type DoctorRow = Infer<typeof DoctorRow>;

export const Status = obj({
  now_local: str,
  timezone: str,
  last_tick: nullable(obj({ at: opt(str), version: opt(str), code: opt(str) })),
  next: rec(str),
  warnings: arr(json),
  recent_errors: arr(json),
});
export type Status = Infer<typeof Status>;

export const Health = obj({ ok: bool, version: str, code: str, tick_last_at: nullable(str) });
export type Health = Infer<typeof Health>;
