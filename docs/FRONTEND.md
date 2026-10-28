# The PWA at `journal.<domain>`

Companion to [API.md](API.md). Rosebud-inspired surface (one continuous document per entry, his words plain
and the companion's folded; Analysis separate from Entry; history by day and week; a Today view; a programs
library; editable question sets; the weekly review inline), without copying it. A responsive web app served by
`journal serve`, installable as a **PWA** (manifest, service worker, offline reads) and designed phone-first; it is
not a native app. History: WP11 shipped without the PWA layer; DECISIONS.md rows 10 and 11 record why it was
restored, and the PWA package restored it and did the mobile pass.

## Decisions

1. **Stack: Preact + TypeScript + Vite + `vite-plugin-pwa` (`injectManifest`, Workbox for the precache only),
   `preact-iso` for routing, hand CSS with a token sheet, no component library.** ~15 KB runtime; the surface is documents and forms; TypeScript earns its
   cost across the entry-document JSON shapes. Alternatives: Svelte (comparable, compiler-specific), plain HTML
   + `htm` (no build, but no types once the editor exists).
2. **Build in CI, commit the artefact, the host never runs node.** `npm run build` writes `src/journal/serve/static/`;
   the directory is committed; a GitHub Action rebuilds on PRs touching `web/**` and **fails the PR when the
   committed `static/` differs** (no bot commits). `static/build.json` records the hash of the `web/` sources the
   build came from (`web/scripts` and `tests/test_web_build.py` compute it the same way), so the suite fails when
   `web/` changed and `static/` did not, and `journal doctor` shows which source the installed build is from.
3. **Offline reads, online writes.** The service worker precaches the shell and keeps the last answers of the read
   views (network first, the cache only when the network fails); writes require the network (no IndexedDB queue). A
   failed write stays in the composer with its error line and a retry button, and an unsent draft stays in
   `sessionStorage`. Nothing that needs the session survives a logout or a 401 (§4).
4. **Two voices, one document.** His blocks are plain full-width text; hers are folded callouts, always labelled; the
   composer appends. *Edit* and *Delete* sit under each block of his (never hers): an inline editor in the place of
   the words, Save/Cancel, a conflict message when the block changed meanwhile, a delete that asks once (DECISIONS.md,
   Decision 4 amended 2026-10-20).
5. **No streaks, no model calls from the client, no AI titles or chips in v1.** The week strip shows
   answered/missed from the ledger; titles are the practice's; `mood`/`people` chips render only when
   `meta.mood/people` exist (set by hand through the Entry's meta sheet in v1; an engine tagging step is a later
   decision).
6. **Auth UX: pairing link, not pasted token.** `journal serve pair` prints a URL; opening it on the phone sets
   the cookie; the app shows "Paired as <host>" in Settings with Unpair. Bearer paste remains as a fallback field.
   On the tailnet (TSAUTH) the browser is signed in by its Tailscale identity instead: no pairing, Settings shows
   "Signed in via Tailscale as <login>" with no Unpair, and `/pair` goes to Today (`GET /api/v1/session`). The app
   remembers how `/session` last answered (`journal:via`, the word only): a refused read under Tailscale shows
   "Couldn't confirm your Tailscale sign-in" with Retry when the server cannot be reached, or "This device isn't
   recognised by Tailscale any more" with the pairing fold open when it answers 401, never the bare pairing form
   (WEB-DESIGN, DECISIONS.md); Settings says
   so when `/session` cannot be read rather than claiming "Paired as …".
7. **Never show `/x`.** The client shows only fields it knows from the API types; unknown fields are dropped at
   the boundary (a zod-like runtime check in `api.ts`), so a future API field cannot leak by accident.

## 1. Information architecture

Bottom bar: **Today · Entries · + · Library · Insights**; Settings behind the avatar/gear on Today.

The centre **+** opens a sheet: *Quick note* (a `/j` note: text), *Voice* (record with `MediaRecorder`, upload,
then POST block/entry), *Answer today's prompt* (lists open prompts), *Start* (situational sessions and
programs with no active run).

### 1.1 Today

- Header: weekday, date, local time and location from `/today`.
- Week strip: seven dots (answered / missed / none) from `today.week`; tap a day → Entries scrolled to it.
- Practice cards (one per scheduled practice today, plus any on-demand practice sent today), in the order of the day:
  emoji + title, a state (`Comes at 23:00` / `Waiting since 06:00` / `Answered 06:41` / `Missed · reply anytime`),
  and, once sent, **the whole prompt in her voice, opener and every question, whatever its state** (the row's
  `prompt`; the web never folds a prompt, only Telegram does), then the first line of his reply in his serif when
  there is one; actions: *Reply* (opens the entry composer; for a prompt with no entry yet, POST `/entries` with `practice`),
  *Send now* (POST `/library/{id}/send`, shows "on its way", resolves on `ledger-changed`).
- Program card for each active run: "Session 3/10 · The Identity Construction Tracer · Phase 2/5 open since
  Sunday", buttons *Continue* (opens the Run screen), *Pause*.
- Scheduled one-offs today (session-10 interrupts) as a quiet list with times.
- "Carry forward" quote from the latest reflection, linking to it.
- Tasks due today (plain lines from the tick cache; read-only; absent when the source is off).
- Empty states: "Nothing scheduled today" / "No program running — start one from the Library".
- Loading: skeleton cards; offline: last cached `/today` with an "offline, as of 09:12" ribbon.

### 1.2 Entries (History)

- `/days` grouped: week headers ("Week of 13 Oct"), day headers, rows: practice emoji + title (program rows:
  "🧠 Fix your life · Session 3"), time, kind label, his first line, chips when `meta.mood/people` exist, small
  indicators (annotated, has conversation, audio).
- Filter chips: practice, program, has audio; search field (`q=`; his words only).
- Pages by cursor (a "Load more" button; no infinite scroll to fight the browser's back button); offline shows the
  pages read before.

### 1.3 Entry (the two-voice document)

- Title row: emoji + practice title, date; a meta sheet (mood, people, starred) → `PATCH /meta`.
- Tabs **Entry** / **Analysis**.
- Entry tab, one continuous document:
  - Prompt block in a muted accent: sent time, opener text, "Questions" disclosure with the woven ids marked.
  - His blocks: body type, full width, time and source in the margin (`23:12 · voice`), inline audio player for
    kept files (`/media/{name}`), "transcript pending" badge when flagged.
  - Her blocks: a slim callout with her name and time, collapsed to one line by default, tap to expand; a
    "↩ replying to" quiet quote when present. Never merged into his text; never without a label.
  - Program entries: phase dividers ("Phase 2 · The Origin Excavation") between his blocks, the filled Summary at
    the end.
  - Composer at the bottom: text or voice → `POST /entries/{stem}/blocks`. Under each block of his, *Edit* (inline
  editor → `PATCH /entries/{stem}/blocks/{key}`, bound to the words he opened it on) and *Delete* (asks once →
  `DELETE`; not offered on a program answer). Hers has neither.
- Analysis tab: annotations (Markdown rendered through a 2 KB safe renderer: paragraphs, bullets, bold/italic,
  everything else escaped), her notes with times, the day's reflection (link + inline body), the program summary.
- States: loading skeleton; 404 "Not an engine entry"; offline (cached document with the ribbon); write failure
  (composer keeps the text, shows the error line, Retry).

### 1.4 Library

- Three groups from `group`: **Situational**, **Daily** (practices with schedules; cadence label "Daily 23:00",
  "Wed 12:00"), **Frameworks** (programs: sessions count, phases, progress bar from the active run).
- Source badge: built-in / yours / edited (`source: fork`, a built-in he edited in place), and "default changed"
  when the shipped default has moved on since his edit. Enabled toggle for built-ins (`enabled: false` saved in place).
- Card → Detail: title, description/intro, the question set (practices) or the sessions and phases (programs),
  buttons *Send now* (practices), *Start* / *Continue* / *Insights* (programs; `/runs`), *Edit* on every definition,
  a built-in included (it is edited in place: BUILTIN-EDIT), *New practice* (the five-minute path: a form with title,
  time, and one question field pre-filled; saving writes a complete file).
- An edited built-in shows an *Edited* panel under the actions: his version is used in place of the built-in; "The
  default has changed since you edited it" when it has; *View the default* / *See what changed*
  (`/library/<id>/default`: the shipped definition, what changed in it since the edit, his version against it, as
  unified diffs); *Reset to default* (`DELETE`) after one in-place confirmation. His file that does not load says so
  (the built-in is used meanwhile) and can be reset.
- Run screen: the current phase rendered (`compose.phase_json`): header, intro, questions, his answer composer
  (`POST /entries/{stem}/blocks` with `phase`), buttons *Next*, *Skip*, *Back*, *Pause*; a phase strip across the top;
  after `202` the screen waits on `runs-changed`. A reflect phase shows the companion's text when present or the
  template with its note.

### 1.5 Question-set editor

- Structured form over the parsed definition: title, emoji, enabled, schedule rows (time, weekday, add/remove),
  collapse hours, annotate, closes, opener mode/plain text, companion line; sections with drag-to-reorder and
  add/remove, questions with drag-to-reorder/add/remove (moving a question pins `{#id}` automatically).
- "Raw Markdown" tab showing the canonical file the server will write (`library.render` of the current form,
  fetched from `PUT` dry-run mode `?validate=1` which returns the rendered text and problems without writing).
- Save → `PUT /library/{id}` with `mtime`; 409 → "Changed elsewhere (Obsidian?) — reload or overwrite"; 422 →
  problems inline next to the field.
- Programs: v1 edits sessions as raw Markdown only (phase structure is fragile to form-edit); the program's
  front matter (title, advance, pace, debrief) is a form. **As built (BUILTIN-EDIT)**: a program or situational
  definition is edited as its files, one at a time (a picker of `program.md` and each session file; the picker waits
  while a file has unsaved changes), every file raw Markdown, `program.md`'s front matter included (no form yet);
  the server's dry run checks the whole definition the file makes. Under a run in progress a change of structure is
  refused with the server's reason; a change of words is sent from the next message.
- As built (WP12): the form's up/down buttons replace drag-to-reorder (a finger cannot drag; a keyboard can press); the
  Raw Markdown tab is editable (saving it sends the text; the form is then re-read from the answer); a new practice is
  `/new-practice` (title, time, one question; the file name follows the title); Reset to default and Delete ask once.
  A built-in opens straight into the editor (no fork step); the first save claims no `mtime`.

### 1.6 Insights

- Week stepper; per week: counts (entries, words, answered/missed), program progress, the week review inline
  (collapsible), the week's day reflections as a list. No charts in v1.

### 1.7 Settings

- As built (WEB-DESIGN): the fields in groups (place and time, prompts, reflections, in the chat, programs), each
  field's layer a short tag beside its label, a Save bar that stays in reach while a change is unsaved, **Use my
  location** in the place group (the browser's position → `POST /settings/location` → the place and time zone saved
  through `location.set_location`; a refusal, a timeout or a lookup failure is said in a line; a place OSM named is
  credited "Place name © OpenStreetMap contributors" in a caption under it, here and in Today's header, for as long
  as it is the place in force; a place or time zone typed and not saved is dropped), and the read-only
  tables and the doctor folded at the foot.
- Pairing status (Paired / Unpaired, Unpair; or "Signed in via Tailscale as <login>" with no Unpair when the
  server signs the browser in by identity, TSAUTH; "Couldn't confirm how this browser is signed in" with Check again
  when `/session` cannot be read), version row (server `version`/`commit` and the tick's; a warning
  when they differ), location/timezone (via `PATCH /settings` → `set_location`), windows, reflections, quiet
  minutes, settle minutes, today-block toggles; each field shows its origin (file / hermes / env) and is locked
  when not writable from here (with the CLI hint). Doctor and Status pages (read-only tables).

## 2. Visual direction (in words)

Quiet, paper-like, dark and light themes from one token sheet. His text is the only thing at full contrast: a
humanist serif or a soft grotesque at 17–18 px, generous leading, full width. Everything the engine or the
companion adds is secondary: prompt blocks in a muted accent wash, her callouts in a thin-bordered card with a
small name label and time, collapsed by default so a day reads as his day. One accent colour (the companion's,
configurable later) used for actions and the prompt wash only. No illustrations in v1, no streak flame, no
confetti. Motion is limited to disclosure (expand/collapse at 160 ms ease-out) and list entry; `prefers-reduced-
motion` disables both. Icons are a small inline set (send, mic, fold, chevron, filter). Typography carries the
hierarchy; cards are borders and spacing, not shadows.

As built (WEB-DESIGN, 2026-10-26): `tokens.css` holds the whole scale (type `--text-xs`…`--text-3xl`, space
`--s1`…`--s7`, radii, paper, ink, the accent with its wash and edge, light and dark) and `app.css` consumes only
tokens. On a phone the bar at the foot carries an icon and a label per place; only floating things (a sheet, a toast)
have a shadow. (Its faces and her callout are superseded by the identity below.)

As built (IDENTITY, 2026-10-27; DECISIONS.md, IDENTITY): one identity, **Margin × Letters**, the owner's pick from the
exploration. The page is his book page: his words in **Newsreader** roman at full ink, the hours hung in a ruled margin,
rules and space instead of boxes, section heads in the book's italic with a rule running out. Her words are a letter:
typed in **Courier Prime** in her ink under a "From <name>" line that runs out as a dashed fold, wherever they appear
(Today's prompts, the entry's prompt and notes, a run's phase text, the Analysis tab, reflections, the carry-forward
line), never editable. Her ink is the only hue and is hers alone; his actions are in his ink. **Her ink is the
reader's choice** (Settings, *Ink*: blue-black, the default; brown; violet; green), kept on the device in
`localStorage` (`web/src/ink.ts`), each held at AA in light and dark by `web/tests/ink.test.tsx`. **Figures are
typed** (every `time` in Courier Prime; the week's days and the counts in Fraunces' soft figures; small labels in
Courier capitals). **On a wide screen (1024 px and up)** Today and an entry open into a spread: the hour, his column,
and her letter and notes in a column beside it, each note where she spoke (from the end of the paragraph it follows);
from 641 px the page is two columns, on a phone one. The engine's interface is **Instrument Sans**. All faces are SIL OFL, served from the app
(`web/public/fonts`, licences beside them; two cut down by `tools/web_fonts.py`) and precached. The mark is his ruled
page with a postmark ring in her ink in its margin, with no date in it.

## 3. Build and repository layout

```
web/
  package.json           preact, preact-iso, vite, typescript, vitest
  src/
    api.ts               typed client + runtime shape checks (unknown fields dropped)
    routes/              today.tsx entries.tsx entry.tsx library.tsx editor.tsx run.tsx insights.tsx settings.tsx pair.tsx
    components/          Document.tsx (two-voice), Composer.tsx, Recorder.tsx, QuestionSet.tsx, WeekStrip.tsx, Sheet.tsx,
                         QuickAdd.tsx, MetaSheet.tsx, RunCard.tsx, ConfirmButton.tsx, Status.tsx, Markdown.tsx,
                         OfflineRibbon.tsx, UpdateToast.tsx
    editorModel.ts settingsModel.ts drafts.ts   the editor's form and moves, the settings the server allows, unsent drafts
    sw.ts swCore.ts      the service worker (Workbox precache + the runtime rules) and its testable rules
    offline.ts pwa.ts    what the page knows about cached answers and being offline; registration and the update prompt
  public/icons/          mark.svg and the PNGs made from it (scripts/make-icons.mjs)
  scripts/               source-hash.ts, manifest.ts (the manifest, colours read from tokens.css), make-icons.mjs
    tokens.css, app.css
  tests/                 vitest on fixture JSON from tests/fixtures/ (shared with the Python suite)
.github/workflows/web.yml   npm ci && npm run build; `git diff --exit-code src/journal/serve/static` fails the PR;
                            Playwright smoke against `journal serve --dev` on an ephemeral port (CI only, never the host)
```

Local development: `vite dev` proxies `/api` to `journal serve --dev --bind 127.0.0.1 --port 8790` (CORS for
`http://localhost:5173` only in `--dev`; the service worker is built for production only). On the deploy host the only
step is the pinned install; the PWA is already in the package.

## 4. Offline behaviour, in detail

- **Install.** `manifest.webmanifest`: `name` Journal, `start_url` and `scope` `/`, `id` `/`, `display: standalone`,
  `theme_color` and `background_color` from `tokens.css` (`--bg`; a test pins them), icons made from `public/icons/mark.svg`
  (192, 512, a maskable 512, and the iOS touch icon). Nothing in it names a host or a port: the same build installs
  from the tailnet origin (`https://<name>.ts.net:8790`), from a published host or from loopback. The route is served at
  `/` with no path prefix (`Host` routing at the reverse proxy). A phone must be on the tailnet to reach it; a service
  worker needs a secure origin, which `https://` (or `localhost`) is.
- **Shell.** Workbox precache of `index.html`, the hashed assets, the manifest and the icons, versioned by content
  hash; every navigation that is not `/api` answers the precached `index.html` (the client routes it). A new build is
  a new `sw.js` (served `no-cache`): the old worker keeps running until the person taps **Update available**, which
  activates the new one and reloads (drafts are in `sessionStorage`, so a reload loses nothing).
- **Data.** The rules are one allow-list in `swCore.ts`, tested: `GET /api/v1/today`, `/entries` (every page and
  filter), `/entries/<stem>`, `/days`, `/library`, `/library/<id>`, `/reflections` are **network first** (a 5 s limit),
  the answer is stored when it is a `200`, and when the network fails, times out or the server answers `5xx` the stored
  answer (at most 7 days old; the last 200 are kept) stands in, marked `X-Journal-Cache: stale` with the time it was
  stored. Everything else on `/api` is not touched by the worker: writes (any method but GET), `/pair`, `/logout`,
  `/uploads`, `/events` (SSE), `/settings`, `/status`, `/doctor`, `/runs*`, `/insights/*`. `/media/<name>` is stored
  only as a full `200` of at most 2 MiB and never answers a `Range` request (the audio element sends one, so a long
  recording is always read from the server).
- **Auth.** The cookie is `HttpOnly`, so neither the page nor the worker sees the token. The stored answers are
  dropped on Unpair (the logout), on any `401` (the page and the worker both), and when `/logout` is requested even if
  the request then fails. They are never shared across browsers or profiles.
- **Ribbon.** A view read from the stored copy shows "offline, as of 09:12" (the time it was stored; a day and date
  when older than today) until a read comes from the server again; a connection that returns (the `online` event)
  makes the live views read again.
- **Writes offline.** The composer says "You're offline; this will not be saved until you're back." and keeps the
  draft in `sessionStorage` (a recording stays on the page); no background sync. Editing his own blocks follows the
  same rule: offline (or on a document read from the kept copy) Delete is not offered, Edit is not offered on a kept
  copy, and an open editor says the edit will not be saved, keeps the box, and sends nothing. A retry is the same write (a
  `client_id` kept while the words are unchanged), a recording is uploaded once and its id reused, and a `422` is not
  retried.
- `/events` (SSE) refreshes the views that care (`entries-changed`, `ledger-changed`, `library-changed`); when the
  stream drops the browser reconnects by itself; when the server refuses it (a 502 during a restart, a 401) the browser
  gives up, so the app opens a new stream with a backoff (1 s doubling to 30 s) and, once it is back, every listening
  view reads again. The views keep what they show until the next successful read.

## 5. Mobile UX

Phone-first, the same document at every width. Touch targets are at least 44 x 44 px (a Playwright test measures every
control on every route at 390 px); the page respects the safe-area insets (`viewport-fit=cover`, `env(safe-area-inset-*)`
on the top bar, the bottom bar, sheets and the composer); under 640 px the navigation is a bar at the foot (Today,
Entries, +, Library, Insights; Settings behind the gear) and the **+** sits in its middle where a thumb lands; body
type is 16 px or more (his words 17-18 px) and form fields are at least 16 px so the browser does not zoom on focus;
the entry's composer sticks above the bottom bar, and the recorder button is a full-height control beside Add, so one
hand writes, records and sends. Motion respects `prefers-reduced-motion`. The visual language stays in `tokens.css`.
