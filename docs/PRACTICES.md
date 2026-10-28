# Practices, programs, situational sessions, and the "today" block

Companion to [ARCHITECTURE.md](ARCHITECTURE.md). Everything here is deterministic unless a line says "model":
every model step has a named plain fallback, and nothing waits on a model.

## Decisions

1. **Three kinds, one engine path.** *Practice* (recurring, unordered), *program* (ordered sessions of phases,
   stateful), *situational* (a one-session program started by an event). A phase send is a ledger prompt, so
   capture, folding, sweep, threading and annotation are the existing code paths.
2. **Format: Markdown with flat `key: value` front matter** parsed by `entries.front_matter` (stdlib). Dotted
   keys nest; lists are comma-separated; several schedule slots are `schedule.1.time`, `schedule.2.time`. The
   body uses `## ` sections and `- ` questions (practices) or `## Phase N` + `### Introduction|Questions|Task|
   Analysis|Transition|Completion` + `**N.M**` ids (programs, which keeps the fix-life files nearly verbatim).
3. **Question ids are positional unless pinned** (`{#9.2}` at the end of a line). Entries record id *and* text
   (`asked:` already does), so reordering never corrupts history; the PWA pins an id automatically when it moves a
   question.
4. **Whole-file override, inherited defaults.** A user file with a built-in's id replaces it entirely; a user file
   with three fields (`kind`, `id`, `title`) plus one question is a complete practice because every other key
   has a default. `enabled: false` hides a built-in. No `extends`/`+Section` mini-language.
5. **Built-ins were generated from `questions.py` once (the module is gone since WP13)** and frozen by a golden test: rendered bank lines,
   `bank_items`, `bank_with_ids` and the Telegram HTML for a fixed date are byte-equal to today's.
6. **Advance:** `advance: settle` (default, `programs.settle_minutes` = 2) or `advance: next`. `/next` always
   advances at once. Idempotency key `(run, session, phase, last_capture_id)`.
7. **Reflect phases are validated model text with a template fallback.** Quotes must come from his answers in
   this session; ≤ 900 characters; `agent.is_failure_text` rejects; failure or no model → the file's own text
   with `_({companion} would normally build this from your answers; write it yourself if you like.)_`.
8. **Summaries are slot-filled from his verbatim words**; the insights export compiles every session's summary
   and answers deterministically; an optional validated synthesis is appended, never substituted.
9. **The companion never advances a program.** `journal_program` has `status` and `propose` (returns the
   `/start <id>` text she may offer). Program files and run state are never hers to edit.
10. **Session-10 interrupts** are created only from an `HH:MM` wake-time confirmation (deterministic parse, else
    the engine asks again), stored in run state, and sent by the tick through `schedule.due`.
11. **The today block** is injected once per thread (45-minute boundary shared with capture), ≤ 1,200
    characters, built from ledger/run/entry/reflection state and the tick's cache, never from the transcript,
    and never on a turn that already carries a pack or program block.
12. **Companion role text is stance, not persona:** "You are {companion}. For this session, work as {role};
    keep your own voice." Third-party persona prose is never sent to him.

## 1. Definition format

### 1.1 Front-matter grammar

```
---                      first line
key: value               key = [a-z][a-z0-9_.]*  (dots nest: opener.plain, schedule.1.time)
---                      closing line; `entries.front_matter` returns {key: value} as strings
```

Value conventions (all parsed by `library._coerce` from the string):

| Shape | Example | Parsed as |
| --- | --- | --- |
| scalar | `title: Evening journal` | str; `true`/`false` → bool; digits → int/float |
| list | `sessions: 01-anti-vision, 02-hidden-goals` | `str.split(",")`, stripped |
| time | `schedule.time: 23:00` | `HH:MM` validated by `config._TIME` |
| slots | `schedule.1.time: 08:00`, `schedule.2.time: 20:30`, `schedule.2.weekday: 6` | `[Slot(time, weekday)]`; `schedule.time` alone = one slot |
| multi-line | not allowed in front matter | long prose belongs in the body |

Ids: `[a-z][a-z0-9-]{1,30}`. Reserved (not usable as a practice id): `j`, `journal`, `note`, `reflection`,
`week`, `start`, `next`, `skip`, `back`, `pause`, `status`, the opt-out command, and Hermes' own command names
(`config.HERMES_COMMANDS`). `config.RESERVED_COMMANDS` becomes `library.reserved_ids(cfg)`.

### 1.2 Practice (recurring, unordered): `library/practices/evening.md`

```markdown
---
kind: practice
id: evening
title: Evening journal
emoji:
schedule.time: 23:00
collapse_hours: 6
annotate: true
closes: day
opener.mode: companion
opener.shape: a short opener (max ~1200 characters)
opener.limit: 1400
opener.plain: {greet:Evening} Let the day land. Answer whatever pulls at you; you don't need to do them all.
companion: closure — process the day, release, carry forward one thread
---
## 1. Brain Dump & Letting Go
- What's spinning in your mind?
- What do you need to release before sleep?

## 2. Day Review
- What went well today?
- What challenged you?
- What did you learn?

## 3. Story-Worthy Moments
- What interesting moments happened, even if small?
- What made you smile or laugh?
- What surprised you?

… sections 4–10 exactly as `questions.EVENING` …
```

Rules: `## N. Title` sections hold `- ` questions; a body with no `## ` lines is a flat list (morning: ids
`1..11`). Numbering in the heading is optional and ignored (ids are positional). `emoji:` empty means "use
`companion.emoji`" (today's behaviour); a value overrides it for this practice. `{greet:Word}` in
`opener.plain` renders as today's `compose.plain_opener` greeting (`Word, Sam 📓` / `Word.`); `{title}`,
`{user}`, `{emoji}` are also available.

Defaults inherited when a key is absent: `schedule` none (on demand only: `/start <id>` or the PWA),
`collapse_hours` 6, `annotate` true, `closes` none, `opener.mode` companion, `opener.shape` "a short opener
(max ~900 characters)", `opener.limit` 1100, `opener.plain` "{title}. A sentence or two is enough.",
`companion` "" (the skill lists the practice by title only), `emoji` "", `enabled` true.

The other three built-ins: `morning.md` (flat list, `schedule.time: 06:00`, `opener.plain: {greet:Morning} Start
wherever something feels alive — one line or a voice note is plenty.`, `companion: orientation — where they
are, what matters today`), `midweek.md` (`schedule.time: 12:00`, `schedule.weekday: 2`, `opener.shape: a two-
or three-line check-in (max ~700 characters)`, `opener.limit: 900`, `opener.plain: Midweek check-in. Quick
pulse — a sentence or two is enough.`), `weekly.md` (`schedule.time: 20:00`, `schedule.weekday: 6`, `closes:
week`, `opener.limit: 2200`, `opener.plain: End of the week. Let's look back — take whichever thread matters.`).
Titles are today's `TITLES`. The generation step (WP1) writes these four files from `questions.py` and the test
asserts byte equality of `bank_lines`, `bank_items`, `bank_with_ids` and `compose.telegram_html(...)` for a fixed
date against the pre-change outputs captured in `tests/golden/`.

A user's five-minute practice, complete:

```markdown
---
kind: practice
id: gratitude
title: Daily gratitude
schedule.time: 21:30
---
- Three things today, and why each one mattered.
- Who made one of them possible?
```

### 1.3 Program (ordered, stateful): `library/programs/fix-life/program.md`

```markdown
---
kind: program
id: fix-life
title: Fix your entire life in 1 day
emoji: 🧠
group: frameworks
advance: settle
pace: 1 session every 2–3 days
phases_of: diagnosis=1-4, understanding=5-7, transformation=8-9, integration=10
sessions: 01-anti-vision, 02-hidden-goals, 03-identity-tracer, 04-lifestyle-audit, 05-dissonance-engine, 06-cybernetic-debugger, 07-ego-navigator, 08-game-architect, 09-conditioning-excavator, 10-one-day-reset
debrief: post-session-debrief
insights: compile
sources: Dan Koe (How to fix your entire life in 1 day), @alex_prompter (10 prompts), @chip1cr (Clawdbot skill)
---
Ten structured sessions in four movements: 1–4 diagnosis (where you are and why you're stuck), 5–7
understanding (how the psyche works, what blocks), 8–9 transformation (building the new architecture),
10 integration (a full one-day protocol). Start with /start fix-life; one session every few days is the pace
that holds.
```

The body is the intro shown when a run starts (the original "rabbit hole" message, as text; the image is not
ported). `insights: compile | compile+synthesis`. `debrief:` names a situational definition offered after each
completed session.

### 1.4 Session: `library/programs/fix-life/sessions/01-anti-vision.md`

```markdown
---
kind: session
id: anti-vision
number: 1
title: The Anti-Vision Architect
minutes: 45-60
framework: Dan Koe's Anti-Vision concept
role: Future Trajectory Analyst who has listened to people describe the lives they wish they had changed, and shows them their probable future before they live it
mission: Guide the user through constructing a visceral anti-vision of the life they're drifting toward if nothing changes. This isn't pessimism. It's fuel.
quote: You are okay with your current standards because you are not fully aware of what they are or what they lead to. — Dan Koe
---
## Phase 1: Current Reality Excavation
### Introduction
I'm going to help you see where your current life is actually heading. Not where you hope it goes. Where the math says it goes.

Most people tolerate mediocrity because they've never stared at the compound effect of their daily choices. We're going to fix that.
### Questions
**1.1** What's the dull, persistent dissatisfaction you've learned to live with? Not deep suffering. What you've learned to tolerate.
**1.2** What do you complain about repeatedly but never actually change?
**1.3** Your age right now?
### Transition
Good. Let's see what this compounds into over time.

## Phase 2: The 5-Year Tuesday
type: reflect
### Introduction
Based on your inputs, I'm constructing a detailed Tuesday in your life 5 years from now if absolutely nothing changes.

This isn't prediction. It's projection based on current trajectory.
### Task
Describe yourself or let me construct:
- Where you wake up and what your body feels like
- The first thought that enters your mind
- Who's around you (or not)
- What happens between 9am and 6pm
- How you feel at 10pm before sleep
### Question
**2.1** Describe your average Tuesday 5 years from now if nothing changes. Be brutally honest. Details matter.
### Transition
That's 5 years. Let's go further — to 10 years.

… Phases 3–6 as in the original, `### Completion` kept as text …

## Summary
✅ **Session 1 complete: The Anti-Vision Architect**
**Key findings**
- Current dissatisfaction: {1.1}
- Repeated complaints: {1.2}
- Living example: {4.1}
- Protective identity: {5.1}
**Anti-vision statement:** "{6.1}"
```

Parser rules (`library.parse_session`): a phase is `## Phase N: Title`; an optional `type: ask|reflect|task|
schedule` line directly under it (default `ask`); recognised H3s are `Introduction`, `Question(s)`, `Task(s)`,
`Analysis`, `Transition`, `Completion`, `Information and Tasks`, `Repair Protocols`, `Practices by Transition`,
`Choose a domain to examine:`, `For each belief marked "I":`, `Final Task`, `Morning Protocol — Your Questions`,
`Daytime Interrupts — Set Reminders`, `Evening Protocol — Your Questions` (any unrecognised H3 is kept as
prose under the phase, so nothing authored is lost); questions are `**N.M**` lines (also `**4A.1**`-style ids,
kept verbatim) or `- ` bullets under `### Questions`; `{N.M}` slots in `## Summary` name question ids; `{what
you want to change}`-style prose slots are mapped in the session's front matter (`slot.what you want to
change: 1.1`). A `## Final Document` / `## Generated Document` section is a second template compiled like
`## Summary`. The mechanical edits to the original files: `## Metadata`, `## AI Role`, `## Mission` move into
front matter (never sent to him), `## Summary Template` becomes `## Summary` with `{N.M}` slots, and the
`[▶️ Continue] [⏸ Take a Break]` button lines are dropped (commands replace them). Russian sessions are not
ported (`lang` folders can come later).

### 1.5 Situational (one-off): `library/situational/conversation-prep.md`

```markdown
---
kind: situational
id: conversation-prep
title: Conversation prep
emoji: 🗣️
group: situational
aliases: prep
expires_hours: 12
annotate: true
advance: next
---
## Phase 1: The conversation
**1.1** Who is it with, when, and what is it about?
**1.2** What do you want to be true when it ends?
### Transition
Now the part you're bracing for.

## Phase 2: The fear and your line
**2.1** What's the thing you're afraid they'll say? What's true in that fear?
**2.2** One sentence you want to be able to say, however it goes.

## Summary
- With {1.1}. Want: {1.2}. Bracing for: {2.1}. My line: {2.2}.
```

A situational definition is a one-session program with no `sessions:` list: its phases are in the file. It starts
on `/start prep` (aliases resolve), from the PWA, or when he accepts the companion's `propose`; it is not resumed
after `expires_hours`. `post-session-debrief.md` is the same shape (`after: fix-life` lets the engine offer it,
through a notebook `FOLLOW-UP` the code files when a fix-life session completes: "offer the post-session debrief
when it fits").

## 2. Loading, overriding, validating (`library.py`)

```python
@dataclass(frozen=True)
class Slot: time: str; weekday: int | None
@dataclass(frozen=True)
class Question: id: str; text: str; section: str | None
@dataclass(frozen=True)
class Practice:
    id: str; kind: str; title: str; emoji: str; schedule: list[Slot]; sections: list[tuple[str | None, list[Question]]]
    collapse_hours: float; annotate: bool; closes: str | None; opener: dict; companion: str; group: str
    path: Path; builtin: bool; enabled: bool; raw: str
    def items(self) -> dict[str, str]            # == questions.bank_items(kind) for the four built-ins
    def bank_lines(self) -> list[str]            # == questions.bank_lines(kind)
@dataclass(frozen=True)
class Phase: number: int; title: str; type: str; intro: str; questions: list[Question]; task: str; analysis: str; transition: str; extra: str
@dataclass(frozen=True)
class Session: id: str; number: int; title: str; minutes: str; framework: str; role: str; mission: str; quote: str; phases: list[Phase]; summary: str; document: str; slots: dict[str, str]
@dataclass(frozen=True)
class Program: id: str; kind: str; title: str; emoji: str; group: str; advance: str; pace: str; phases_of: dict[str, range]; sessions: list[Session]; debrief: str | None; insights: str; intro: str; aliases: list[str]; expires_hours: float | None; path: Path; builtin: bool; enabled: bool

def load(cfg) -> Library                       # built-ins, then practices_dir; cached by directory mtimes
def get(cfg, id_or_alias) -> Practice | Program  # raises UnknownDefinition
def scheduled(cfg) -> list[Practice]           # enabled practices with ≥1 slot in their files
def reserved_ids(cfg) -> set[str]
def problems(cfg) -> list[str]                 # doctor rows: bad front matter, duplicate ids, empty bank, bad slot, missing session file, unknown {slot}
def parse(text, path, builtin) -> Practice | Program   # strict on structure, lenient on prose
def render(definition) -> str                  # canonical Markdown; parse(render(x)) == x (round trip, tested)
def validate_file(path) -> list[str]           # `journal practices validate FILE`
```

Resolution: `journal/library/` first, then every `*.md` and `*/program.md` (with `sessions/*.md`) under
`practices_dir`. Same id → the user file replaces the built-in whole. **A built-in is edited in place**
(BUILTIN-EDIT, DECISIONS.md): the PWA's Edit opens on the built-in and the first save writes his version under the
same id into `practices_dir` (a practice or situational definition as `<id>.md`, a program as `<id>/` with all its
files), so it keeps the built-in's `/start` word and slots and nothing is duplicated; "Reset to default" deletes it.
The packaged files are never written. `practices_dir/.library` keeps, per edited built-in, the shipped text the edit
began from: when a later release changes the default, his version is kept and the Library says "the default has
changed since you edited it", with the diffs (`GET /library/{id}/default`, `journal practices diff <id>`). A broken
user file is a doctor problem and the built-in of the same id (if any) is used, so an editing mistake never silences
the evening prompt. The hook, the tick and serve all go through `library.load`, so an edit is live on the next tick
and the next message without a gateway restart.

CLI: `journal practices list | show <id> | validate [FILE] | render <id|FILE> | fork <id> | reset <id> | diff <id>`.

## 3. Generalising the four rituals (what changes in the engine)

- `schedule.due(cfg, now_local, sent_ids)` iterates `library.scheduled(cfg)`; a practice with several slots
  yields `prompt_id = f"{date}-{id}"` for the first slot and `f"{date}-{id}-{n}"` for later ones; the entry stem
  follows the same rule (`entries.stem_for`). Existing ids and stems (`2026-10-14-evening`) are untouched.
- `entries._ENGINE_NAME` → `^\d{4}-\d{2}-\d{2}-([a-z][a-z0-9-]*?)(?:-s(\d{2}))?(-\d+)?\.md$`, the id checked
  against the library plus `j|note`; a file whose id is unknown is still recognised when its front matter says
  `kind: <id>` or `practice: <id>` (a renamed or deleted practice keeps its history). Front matter gains
  `practice:`; program entries add `program:`, `session:`, `run:`.
- `raw_entry`, `his_words`, `his_conversation` strip `ENGINE_MARK` lines (so phase keys never reach packs or
  annotation input).
- `compose.plain_opener(practice, names)`, `compose.header(practice, …)`, `compose.bank_html(practice)`,
  `compose.telegram_html(practice, …)`, `compose.plain_text(practice, …)`; `prompts.opener_request` takes
  `practice.opener["shape"]` and folds `practice.companion` into the request; `validate.check(practice, …)`
  uses `practice.opener["limit"]` and `practice.items()`.
- `reflect.due`: "the practice with `closes: day` was annotated" and "`closes: week`", with the same fallback
  times; the week ends on the `closes: week` practice's weekday.
- `capture.add_companion_note(kind=…)` accepts any library id or `note`.
- The skill's "Kinds" section is rendered from each enabled practice's `companion:` line.
- `config.problems` validates the library; a leftover `schedule` key in a config layer is named by a config warning
  and not read (WP13), and `journal migrate-config` moves it into forked practice files.

## 4. Programs: state, delivery, capture, companion, no model

### 4.1 Run state (`state_dir/runs/<program>/<run>.json`, written under `ledger.locked_state`)

```json
{"program": "fix-life", "id": "2026-10-20-a1f3", "status": "active",
 "started_at": "2026-10-20T15:02:11+00:00", "completed_at": null, "paused_since": null, "last_activity_at": "…",
 "current": {"session": 3, "phase": 2},
 "sessions": {
   "1": {"status": "completed", "entry": "2026-10-20-fix-life-s01", "started_at": "…", "completed_at": "…",
         "phases": {"1": {"prompt_id": "2026-10-20-a1f3-s01-p1", "sent_at": "…", "captures": ["812"], "answered_at": "…", "advanced_at": "…", "advance_key": "2026-10-20-a1f3:1:1:812"},
                    "2": {"prompt_id": "…", "skipped": true}}},
   "3": {"status": "in_progress", "entry": "2026-10-23-fix-life-s03", "started_at": "…", "phases": {"1": {…}, "2": {"prompt_id": "…", "sent_at": "…", "captures": []}}}
 },
 "scheduled": [{"id": "s10-i3", "at": "2026-11-02T11:00:00+05:30", "text": "Am I moving toward the life I hate or the life I want?", "prompt_id": null}],
 "insights_entry": null}
```

Answers are never in the run file: his words live in the journal, one entry per session,
`YYYY-MM-DD-<program>-sNN.md`, dated the day the session started (a session spanning days keeps its file), flat at
the journal root. Front matter: `kind: fix-life`, `practice: fix-life`, `program: fix-life`, `session: 3`,
`run: 2026-10-20-a1f3`, `type: journal`, `source: …`. `## Prompt` holds the session title, framework and the intro
of each phase as it is sent (appended, folded under `> [!question]- Phase N: Title`), his words section holds
`### 21:14 IST · text` blocks each preceded by a hidden `%% journal phase:2 %%` key line, `## Conversation` holds
the back-and-forth as for rituals, `## Summary` is written by code at session end (before the annotations
heading), annotations follow once the session is complete (not per phase). A situational run writes
`YYYY-MM-DD-<id>.md` (no `-sNN`).

The ledger: each phase send is a prompt record `{id: "<run>-sNN-pM", stem: <session entry stem>, kind: <program
id>, practice: <program id>, program, run, session, phase, chat_id, message_id, sent_at, html, plain_text,
collapse_hours: 3}`. `capture.match` is untouched: a phase message is a prompt message (direct reply at any age;
first message while visible). `tick.prune` keeps prompts whose `run` is active or completed within `KEEP_DAYS`.

### 4.2 Transitions (`programs.py`)

```python
def start(cfg, program_id, now, log, session: int | None = None) -> Run      # one active run per program; resumes if one exists
# one_at_a_time: one program unfinished at a time (situational aside), across faces (DECISIONS.md, ONE-PROGRAM-RUN)
def advance(cfg, run_id, now, log, *, reason: str, capture_id: str | None) -> bool   # idempotent: False if key already advanced
def skip(cfg, run_id, now, log) -> bool        # marks the phase skipped, then advance
def back(cfg, run_id, now, log) -> bool        # re-sends the previous phase; its earlier answers stay
def pause(cfg, run_id, now, log) / resume(...) / abandon(...)
def status(cfg) -> dict                        # library + active runs (what journal_program and /program show)
def step(cfg, now, log) -> None                # the tick's step: settle-advances due, lost spawns, scheduled one-offs, expiries
def complete_session(cfg, run, session, now, log) -> None   # fills ## Summary, sends it, offers the next session / debrief
```

All transitions run under `tick.lock` (blocking) in a spawned `journal program <action> <run>` or in the tick's
`step`; the send itself goes through `tick.send_prompt`. A transition is recorded with `advance_key` =
`"<run>:<session>:<phase>:<last capture id or 'cmd'>"`; a second call with the same key is a no-op (a sweep
re-capture, a redelivered update, a lost-then-retried spawn, or the tick and a spawn racing all collapse to one
send). **One program at a time:** while a program has an unfinished run (active or on a break, mid-session or
between sessions), `start` of a new run of another program is refused (`Busy`) before it changes anything; finishing
or abandoning the run frees the slot, `/break` does not. A program's own run is never refused, and situational
definitions are never refused and never refuse (DECISIONS.md, ONE-PROGRAM-RUN). A phase send ends a conversation thread (it is a prompt send), so the chat after his answer lands in the
session entry's `## Conversation`.

### 4.3 Advance semantics

- `advance: settle` (default; fix-life): when a capture lands on the open phase, the hook spawns
  `journal program settle <run>`, which waits `programs.settle_minutes` (2) and then advances if the newest capture
  on that phase is at least that old; a correction, a second voice note or a follow-up sent inside the window
  joins the same phase and restarts nothing (the key is the newest capture). If the spawn is lost, the tick's
  `step` advances on the next round (≤ 5 minutes late). `/next` advances immediately whatever the mode.
- `advance: next`: nothing advances without `/next` (or the PWA button); replies keep appending to the open phase.
- `/skip`, `/back`, `/break` (pause), `/program` (status) are plain commands (Hermes owns `/pause` and `/status`,
  so the journal's names differ; DECISIONS.md, WP4); the reply keyboard sent with each phase carries `/next`,
  `/skip`, `/back`, `/break` as text buttons (`ReplyKeyboardMarkup`, `one_time_keyboard`, `resize_keyboard`),
  removed with the session-complete message. Hermes routes a keyboard-originated `/skip` like a typed one
  (plugin command handlers are appended after `filters.COMMAND`, `adapter.py` ~2906); this is verified on the
  first live phase send; the fallback is typed commands with the keyboard dropped.
- A `/x` message is never a capture; a phase answered off the record is simply not answered (`/program` says
  "phase 2 open since Sunday"; he answers again or `/skip`s).
- Test required (WP4): voice note at T, correction at T+40 s → both blocks under phase N, one advance at
  T+2 min; the same with the sweep re-delivering the first message → one advance.

### 4.4 Phase types and what the companion writes

| Type | Sent by code | Model step | No model / validation failure |
| --- | --- | --- | --- |
| `ask` | header, intro, questions (ids kept), transition of the *previous* phase as a lead-in, hint `reply here · /next · /skip · /back · /break` | none (an optional one-paragraph *bridge* when `programs.bridges` is on: ≤ 400 chars, quotes only his last answer, `validate.check_bridge`) | phase text alone |
| `reflect` | header, intro, the companion's construction, then the question | `prompts.reflect_phase_request`: the session's `role` as stance, his answers so far in this session (verbatim, privacy-filtered by construction: they come from the entry file), the task bullets; answer ≤ 900 chars; `validate.check_reflect_phase`: every quote in his answers, length cap, not `is_failure_text`, no claims about anything outside those answers | the file's `### Task` text with `_({companion} would normally build this from your answers; write it yourself if you like.)_`; the entry gets `_reflect: skipped (no model)_` under the phase key |
| `task` | header, intro, the task text with prose slots filled by code from his earlier answers (`slot.*`), the question if any | none | identical |
| `schedule` | header, intro, the interrupt table rendered from the confirmed wake time | none | identical |

A `reflect` phase whose model text is unavailable is **sent at once** with the fallback (a phase is never
deferred); the deferred-model machinery is for annotations and reflections only. Four fix-life phases are
`reflect`: S1 P2 (the 5-year Tuesday), S2 P2 (behaviour audit analysis), S4 P2 (required lifestyle mapping),
S6 P3 (malfunction diagnosis).

### 4.5 What the companion sees and may do

On the turn a phase answer is captured, `inbound.receive` rewrites his message with
`[Program reply — {title}, session 3 "The Identity Construction Tracer", phase 2/5; saved verbatim to <path>.
The engine advances and sends the next phase itself (after a short settle, or at once on /next); respond to what
{they} wrote, don't ask the next phase's questions.]` and `turncontext.build` injects, once, the **program block**
(≤ 3,000 chars): "You are {companion}. For this session, work as {role}; keep your own voice. Mission: {mission}."
then the phase text and his answers so far in this session (his words only, from the entry file). The program
block replaces the reply pack on that turn (budget rule). She converses; her replies land under `## Conversation`.

Tool `journal_program(action: status | propose, program?: id)`: `status` returns the library and active runs (what
`/program` shows); `propose` returns the `/start <id>` line she can offer him and starts nothing. Both are reads,
so they are allowed in an off-record turn; there is no write action to refuse. The skill's "Programs" section
says: the engine sends the questions as authored; you never rewrite, skip or advance them; the role text is a
lens for this session, not a claim about who you are; third-party persona details are never repeated to him.

### 4.6 With no model

Phases go out on time with their authored text (reflect phases with the template fallback); answers are saved
verbatim; summaries are filled from his words; the session-complete message is sent; interrupts fire on time;
the insights export is written on completion. Bridges, reflect text, annotations and the final synthesis are
absent or deferred (`outage`) as today. Nothing in §4 waits on a model.

### 4.7 Summaries and the insights export

`## Summary` slots `{N.M}` are filled with his verbatim answer to that question (the whole block text for the
phase when a phase has one question; when several, the block text is used for each slot of that phase, since he
answers a phase in one message; a `/x` or skipped phase gives `_(skipped)_`). The filled summary is written into the
entry before the annotations heading and sent as the completion message (`compose.summary_html`), followed by
`Next: /start fix-life continues with session 4 whenever you're ready.` and, when `debrief:` is set, the notebook
FOLLOW-UP described in §1.5.

On completion (or `/start fix-life insights` / `journal program insights <run>`), `insights: compile` writes
`YYYY-MM-DD-fix-life-insights.md` (front matter `kind: insights`, `author: engine`, `program`, `run`): per session,
title, dates, the filled summary and his answers in full with their phase labels; deterministic, no model. With
`compile+synthesis`, a one-shot without tools adds `## {Companion}'s synthesis` after it, validated like a
reflection (every quote must appear in the compiled text); a failed validation leaves the compiled document alone.
Session 10's `## Generated Document` is compiled into the session-10 entry the same way.

### 4.8 Session 10: interrupts (`schedule` phase)

Phase 3 of session 10 asks for the wake time. The engine reads the phase-1 answer for 1.4/1.5 only as a hint;
it schedules nothing until he confirms: the phase-3 send ends with "Confirm the protocol day and wake time as
`YYYY-MM-DD HH:MM` (e.g. `2026-11-02 07:00`) and I'll time the eight interrupts from it." A reply matching
`^\s*(\d{4}-\d{2}-\d{2})?\s*([01]\d|2[0-3]):([0-5]\d)\s*$` (date optional: defaults to the next day) creates
eight one-offs at wake +2h, +4h, +6h, +8h, +10h, +12h, +13h, +14h in the configured timezone (`{protective
identity}` filled from S1 5.1), stored in `run.scheduled`, and advances the phase. Any other reply is captured as
his words (it may be commentary) and the engine asks again once. `schedule.due` returns due one-offs alongside
practice prompts; they are ledger prompts (`kind: fix-life`, `interrupt: 3`, `stem: <session-10 entry>`), so a
reply lands in the session-10 entry under the interrupt's label. Tests: wall-clock arithmetic across a DST change
and a timezone change via `journal_set_location` between scheduling and sending (times stay at the stated local
clock), the grace window, and that nothing is scheduled from free text.

As built (WP5; the reasons are in DECISIONS.md, "Taken by packages"): the run stores each interrupt's wall-clock
time as `local` (read in whatever timezone applies on the day) beside `at`; only a Telegram reply to the interrupt's
message is its answer (it is written under a `%% journal interrupt:N %%` key); of several due at once the tick sends
the newest; `/skip` and `/next` go on without interrupts.

### 4.9 Fix-life mapped onto the format

| Session | Title | Phases (type) | Summary slots |
| --- | --- | --- | --- |
| 1 anti-vision | The Anti-Vision Architect | 1 ask (1.1–1.3) · 2 **reflect** (2.1) · 3 ask (3.1–3.2) · 4 ask (4.1–4.3) · 5 ask (5.1–5.4) · 6 task (6.1) | 1.1, 1.2, 4.1, 5.1, 6.1 |
| 2 hidden-goals | The Hidden Goal Decoder | 1 ask (1.1–1.3) · 2 **reflect** (2.1) · 3 ask (3.1–3.3) · 4 task (4.1) · 5 ask (5.1–5.4) | 1.1, 3.1, 4.1, 5.1, 5.3 |
| 3 identity-tracer | The Identity Construction Tracer | 1 ask (1.1) · 2 ask (2.1–2.4) · 3 ask (3.1–3.4) · 4 ask (4.1–4.4) · 5 ask (5.1–5.4) | 1.1, 2.2, 3.3, 5.3 |
| 4 lifestyle-audit | The Lifestyle-Outcome Alignment Auditor | 1 ask (1.1–1.3) · 2 **reflect** (2.1) · 3 ask (3.1–3.5) · 4 ask (4.1–4.2) · 5 ask (5.1–5.4) | 1.1, 4.1, 4.2, 5.1, 5.3 |
| 5 dissonance-engine | The Dissonance Engine | 1 ask (1.1–1.3) · 2 ask (2.1–2.3) · 3 ask (3.1–3.3) · 4 task (4.1) · 5 ask (5.1–5.3; "Information and Tasks" kept as prose) | 1.1, 2.1, 3.2, 4.1, 5.3 |
| 6 cybernetic-debugger | The Cybernetic Debugger | 1 ask (1.1–1.3) · 2 ask (2.1–2.5) · 3 **reflect** (3.1) · 4 ask (4.1; 4A.1–4E.1 kept as reference text) · 5 ask (5.1–5.4) | 1.1, 3.1, 4.1, 5.1 |
| 7 ego-navigator | The Ego Stage Navigator | 1 ask (1.1–1.3) · 2 ask (2.1; stage descriptions as prose) · 3 ask (3.1–3.3) · 4 ask (4.1–4.4) · 5 ask (5.1–5.4; practices as prose) | 2.1, 4.1, 5.1, 5.2 |
| 8 game-architect | The Game Architecture Engineer | 1 ask (1.1–1.3) · 2 ask (2.1–2.3) · 3 ask (3.1–3.3) · 4 ask (4.1–4.3) · 5 ask (5.1–5.3) | `## Final Document: Game Map` compiled from 1.1–5.3 |
| 9 conditioning-excavator | The Conditioning Excavator | 1 ask (1.1) · 2 ask (2.1) · 3 ask (3.1) · 4 ask (4.1–4.2) · 5 ask (5.1–5.4) | 1.1, 3.1, 5.4 |
| 10 one-day-reset | The One-Day Reset Architect | 1 ask (1.1–1.5) · 2 task (morning protocol with `{what you want to change}` ← 1.1; 2.1) · 3 **schedule** (3.1 = HH:MM confirmation) · 4 task (evening protocol; 4.1) · 5 task (5.1 date confirm, 5.3 as the closing line; 5.2 "cron reminders?" is dropped: the interrupts are already scheduled) | `## Generated Document` compiled; final line points at `/start fix-life insights` |

Fifty-one phases. A content test asserts every `**N.M**` line of the original English files appears in exactly one
phase of the ported files (with the three stated drops: button lines, 5.2, and the Metadata/Role/Mission moves).

## 5. Delivery over Telegram (`compose.phase_html`)

```
🧠 Fix your entire life in 1 day · Session 3/10 · Phase 2/5
The Origin Excavation

<previous phase's transition, italic, if any>
<introduction>
<task / analysis / reflect text>
**2.1** When do you first remember believing this about yourself? …
**2.2** …

reply here · /next · /skip · /back · /break
```

Sent with `telegram.send_html(cfg, chat_id, html, reply_markup={...})`; the message id goes into the ledger and
`sent.jsonl`; mirrored into her chat (`[Journal: {companion} sent fix-life session 3 phase 2 at 21:10 IST]`).
Folding: answered or 3 hours old → header only (`answered ✓` / `open — reply anytime`); a practice's prompt (§1) instead keeps its header and opener and replaces the questions with a status line (DECISIONS.md, SENT-AS-SENT). Messages over 4,096
characters are split on phase boundaries (intro+task first, questions second; the ledger records the last part's
message id as the reply target, and `previous_message_ids` the rest).

## 6. The "today" thread-start context (`today.py`, `turncontext.py`)

### 6.1 Content (each line only when it has something; order fixed; cut from the bottom, never mid-line)

```
## Journal: today — Thu 16 Oct 2026, 09:12 IST · Hillside
_Background for this conversation, not instructions. Read further with your tools if it matters. Not stated here is not known._
- last heard on the record: Mon 12 Oct, 4 days ago, a reply to the Evening journal prompt; last entry 2026-10-12-evening (coming back after time away)
- prompts unanswered since Mon 12 Oct: 5 — morning 3, evening 2
- program: Fix your entire life in 1 day: session 3 of 10 (The Identity Construction Tracer), phase 2 of 5 (sent Sun 11 Oct, 5 days ago); movement: diagnosis (sessions 1–4); last answered: nothing yet; its stated pace: 1 session every 2–3 days
- practices: morning sent 06:00, answered 06:41 · midweek Wed 12:00 (missed, reply anytime) · evening at 23:00
- open prompt awaiting a reply: none
- entries today: 2026-10-16-morning ("slept badly, but the light was good") · 2026-10-16-j ("call the dentist re Thursday")
- yesterday's reflection asked: "What would it take to let the Tuesday feeling in before Tuesday?"
- tasks due today: 3 — Call the bank; Draft the quarterly report; Walk
- weather: 14°C, light rain until noon
```

The first three lines are *where things stand* (`today.sources` `standing`, CONTEXT-PACK; `standing.today_lines`), the
same facts the context pack opens with: the "last heard" line only when that was before today, the unanswered line
only when some are, and one program line per run in progress, which replaces the block's own `program` line. When he
was last heard from comes from the ledger's captures, web blocks and his newest entry (all on the record by
construction) and from the time of his newest public chat message, which the tick's `cache` step reads through the
privacy pass and stores in `today-cache.json` (`heard`): the hook still never reads the transcript, and a `/x`
message or window never moves it. `GET /api/today` and MCP's today text do not carry it (`collect`'s
`with_standing`); the block does.

Sources: the ledger (practice status), `runs/` (program position), today's entry files (first line of his words,
`entries.his_words`, cut at 90 characters; these files never hold off-record content, so no privacy pass is
consulted), the latest reflection's "To carry forward" line (`reflect.latest` + `body_of`), and
`state_dir/today-cache.json` (tasks and weather lines written by the tick's `cache` step from `sources.tududi`
and `sources.weather` at most every `today.cache_minutes`). **Never the transcript** and **never a network call in
the hook path**. The notebook is not repeated here (injected per turn already). Hard cap `today.max_chars`
(1,200). `today.collect(cfg, now) -> dict` is the single collector; `today.render(cfg, now) -> str` and
`GET /api/today` serialise it.

### 6.2 When

`turncontext.build` injects the block when `today.thread_started(cfg, now, turn)` is true: the last activity
known to the hook (`hook-state.json`: `last_his`, `last_send`; refined by `transcript.last_activity(before=now)`
when a Transcript is available, excluding this message's own row) is older than `conversations.quiet_minutes`
(45) or absent, **or** a prompt was sent since that activity, **and** `today_injected_at` is not inside the
current thread. The record `{thread_since, last_seen, today_injected_at}` is updated each turn under
`ledger.locked_state`. A prompt send also starts a thread, matching `privacy`/`conversations` boundaries. In a
`/x` turn the block is still allowed (it holds no chat content). Fail-open: any exception → no block.

### 6.3 Budget rule (in code, `turncontext.BUDGET`)

At most two blocks on a turn: the notebook (per turn, unchanged) plus one of {reply pack, program block, today
block}. The today block is never injected on a turn that carries a pack or a program block; it waits for the next
thread start. `journal status` shows injections per day (`today: 3 blocks today, last 09:12`). Tests (WP3): first
message, 44 vs 46 minutes, prompt-send boundary, pack turn → no today, exception → no block, cap enforced,
no transcript text in the output, `sources.*` never called from `turncontext`.

## 7. User override rules, in one place

- A user file with a built-in's id replaces it whole; `enabled: false` hides it; deleting the user file restores
  the built-in (the PWA's Reset to default, `journal practices reset <id>`). The packaged files are read-only; a
  `PUT` of a built-in writes his version in its place (BUILTIN-EDIT). A later release that changes a default keeps
  his version, and says the default has changed when the default his edit began from is known (kept in
  `practices_dir/.library`); for an edit made without that record (an older fork, a file made by hand) whether it
  changed is not known (`default_changed: null`, no "since" diff). Reset takes the new default.
- Edits apply to the next prompt or the next phase send; entries already written carry the questions they were
  sent with. A program (or situational definition) edited while a run of it is active or paused (or completed with
  session 10's interrupts still to come) keeps its structure: a save or a reset that adds, removes, reorders or retypes a session or a phase, or moves a question id,
  is refused until the run ends (`library.run_conflict`: `409 active-run` from serve, an error from the CLI); a
  change of words applies from the next step. The guard is at the writers (serve, the CLI), not in `library.problems`:
  a file edited by hand mid-run is not stopped.
- `schedule.<id>` in config no longer overrides anything (removed in WP13); `journal migrate-config` moves an old
  block into forked files.
- Any PUT compares the file mtime the client loaded (`If-Unmodified-Since` / `mtime` in the body) and returns
  409 on drift, since Obsidian and the PWA can both edit `practices_dir`.
- The engine only appends his words; no override changes that. Editing or deleting a block of his is his own act, from
  the web app (DECISIONS.md, Decision 4, amended 2026-10-20), and no override makes it possible for anything else.
