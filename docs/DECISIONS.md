# Decisions

Taken by the owner's delegate before the work packages started (recorded in WP0, 2026-10-16). Later packages follow
them; a package that finds a reason to revisit one raises it in its report rather than choosing silently. The
design docs ([ARCHITECTURE.md](ARCHITECTURE.md) §Decisions, [PRACTICES.md](PRACTICES.md), [API.md](API.md),
[FRONTEND.md](FRONTEND.md), [WORK-PACKAGES.md](WORK-PACKAGES.md)) hold the detail; this page is the short list.

| # | Decision | Reason |
| --- | --- | --- |
| 1 | **Program advance = settle, then advance.** A captured phase answer advances the run after 2 quiet minutes; `/next` advances at once. | A correction or a second voice note within the settle joins the same phase instead of answering the next one; `/next` keeps him in control when he is done. |
| 2 | **Notebook injection stays per turn; the today block is a separate once-per-thread block.** | The notebook's contract is unchanged and tested; the today block is new and larger, so it goes in once per thread under the budget rule (at most two blocks a turn). |
| 3 | **`practices_dir` defaults to `<state_dir>/practices`, outside the vault.** | The journal folder holds only flat entries the engine writes; definitions live elsewhere, so the folder's layout, and whatever rules the person keeps for his notes, stay as they are. Moving them inside is his choice, not a default. |
| 4 | **The engine only ever appends his words; the web app is one more editor for him.** *Amended 2026-10-20 (the owner: his entries are Markdown files of his own, so the web app should let him edit them).* No code path of the engine, no model and no automatic path rewrites a block he wrote, and the companion's words are never editable on any surface. He can edit and delete **his own** blocks from the web app, a plain edit with no history, written through the entry lock and refused on drift. *Originally: append-only everywhere, the web app included: no editor for his blocks.* | His entries are plain Markdown he can already change in his notes app, so forbidding the web app protected nothing and sent him to another editor; the rule that matters is who may rewrite a block (the engine and models never; he, deliberately, yes). Code, not a model, still owns the layout: the web app changes one block's words and carries every other byte over. |
| 5 | **fix-life drops the Russian sessions, the intro image, inline buttons, and session 10's "create reminders?" question.** Interrupts come from the `HH:MM` confirmation. | Russian content and the image are the Telegram bot's packaging, not the practice; inline buttons are a Hermes-adapter nicety (reply keyboards and typed commands do the work); the `HH:MM` answer already says what to schedule, so a second question is noise. |
| 6 | **No AI titles and no mood/people chips in v1: no model calls from the UI.** | The web app stays a reader and a writer of his words and his definitions; every model call stays in the tick, where outages are handled and nothing he types waits on a model. |
| 7 | **Serve unit: `MemoryHigh=384M`, `MemoryMax=768M`, to be measured.** | The deploy host is shared; the figures are a starting envelope for a stdlib HTTP server and are revised from the first weeks' peak, not guessed upward. |
| 8 | **`serve.bind` and every firewall, reverse-proxy, local-DNS and tunnel step are the operator's at deploy time, not a package agent's.** | Network exposure is irreversible blast radius on a shared host; package agents ship code and a DEPLOY.md section, the operator runs the gates. |
| 9 | **WP14 (plainchat) is deferred.** | It depends on the ports extraction (WP8) and adds a second chat surface; value before refactor, and nothing in v1 needs it. |
| 10 | ~~**A responsive web app, not a PWA (the owner, 2026-10-19: a web app, not a phone app).**~~ **Superseded 2026-10-20 (row 11).** | It was a misreading: the phone app he ruled out was a native one, not the PWA layer. WP11 therefore shipped without a service worker, manifest or offline reads; the PWA package restores them. The other half of the row stands (Preact + TypeScript + Vite, routes, the two-voice document, pairing, the build in CI with the committed artefact). |
| 11 | **The front end is a PWA with a phone-first UX (the owner, 2026-10-20: yes to the PWA layer and a mobile UI; only a native app was ruled out).** | Installable from the tailnet origin, reads work offline, writes need the network; no native app. FRONTEND.md §4 and §5 hold the rules; the earlier "no PWA" wording in the docs is reverted. |
| 12 | **What gets journaled is configurable: `capture.mode` `everything` (default) or `arm` (the owner, 2026-10-20).** | A companion that is a general agent should not turn every chat into journal entries; under `arm` the journal is one of its arms: replies to prompts, `/j` notes, program answers, voice notes answering a prompt and the companion's explicit saves are journaled, with the conversation that follows them, and ordinary chat is not. `everything` is the behaviour since window A, byte for byte. |

## Raised by packages (open, not decided)

- **SCRUB-PERSONAL — the source adapters and `brain:` (open, the owner's).** The built-in context sources (tududi,
  chat-scrobbler, listenbrainz, storyteller, audiobookshelf, grimmory) are optional features, but together they
  describe one self-hosted stack. Kept as they are (renaming them is a config and test migration). To close: keep
  them, or move the less common ones behind a generic command source; likewise whether the `brain:` citation kind
  gains a neutral alias (`notes:`), which needs `context.py` and `validate.py` to accept both.
- **CHANNELS — one journal channel, or several (open, the owner's).** Built: **(a) one channel at a time**, switched by
  config (`channel.platform`, `channel.chat_id`): prompts go there, and capture, the sweep, the conversation writer,
  the privacy pass and the catch-up read only there. The other options: **(b) one delivery channel plus capture
  channels**: prompts still go to one chat, and he can also journal from a second platform (`/j`, `/x`, replies,
  the conversation) at the same time. Each further chat then needs its own conversation cursor, saved privacy pass,
  bare-`/x` windows and sweep watermark (`conversations.json`, `sweep.json` per chat: a pass saved over one chat and
  read for another turns that chat's private rows public), and the pack, "last heard" and the quote check read every
  chat. The engine is single-chat throughout, so (b) is the chat steps of the tick and the hook run once per channel
  over a config narrowed to it, with per-channel state names. **(c) Several delivery channels** (each prompt to every
  chat, folded and replied to on each) adds the most surface for the least: he answers in one place anyway.
  Recommendation: (a) now; (b) as its own package when he wants to write from a second platform without moving the
  prompts, with a test that two chats' passes never share a file. To close: the owner picks.
- **CHANNELS — `/x` in a chat that is not the journal's (open).** The journal handles `/x` only in its own chat, as it
  always did on Telegram. Elsewhere (the companion on Discord while the journal is on Telegram) nothing of that chat is
  ever captured or written, so nothing said there can reach an entry through the engine; but Hermes answers a bare `/x`
  there with the command's "off the record for the rest of this chat" while no window is kept, and the companion's
  journal-writing tools (`journal_add_note`, the notebook's add) are refused in such a turn only by the journal
  chat's own signals. To close: either track `/x` (message and bare window) per chat on every platform in
  `hookstate`, so `tools.refusal` holds there too, or have the off-the-record command say where it applies.
- **CHANNELS — a group chat or a thread as the journal's chat (open).** `channel.user_ids` keeps other people out:
  their messages pass through untouched and only his sessions are read. That holds where Hermes keeps one session per
  participant: a group outside a thread, by default (`group_sessions_per_user: true`). A thread's session is shared by
  default (`thread_sessions_per_user: false`; Discord auto-threads a server channel), and Hermes stores no sender on
  a message row, so in a shared session the transcript cannot tell his rows from others' and the conversation writer
  would take everyone's. A direct-message chat is the journal's chat the docs recommend; `journal doctor` warns when a
  channel other than Telegram has no `user_ids`. To close: refuse a channel whose sessions are shared (read
  `sessions.chat_type`/`session_key` and fail closed), or document it as unsupported.
- **CHANNELS — no fold, reply or keyboard off Telegram (open).** Out of process Hermes can only send (`send_message`'s
  routers): editing needs a live adapter in the gateway, and there is no generic reply keyboard. So on another
  platform a prompt stays as sent (its question bank does not fold away), annotation replies are plain messages and
  a phase's commands are typed. To close, per platform: an edit through the platform's own API (Discord and Slack
  have plain REST edits the deliver command could call with the token it already loads), or a gateway-side send
  through `ctx` when Hermes exposes one.

- **IDENTITY — the entry's questions: folded or in full (open, the owner's).** The entry page folds a practice's
  questions behind "The 24 questions", as the Margin × Letters mix did; Today shows them in full. The owner has not
  decided which the entry should do. Flipping it is one line: `FOLD_ENTRY_QUESTIONS` in
  `web/src/components/Document.tsx` (`false` shows them in full; on a wide screen they then run down her column,
  and a note of hers early in the entry moves down below them, away from the paragraph it answers).

- **SENT-AS-SENT — program phases keep no question snapshot (open; see the decided entry at the end).** A program
  edited while a run is live shows its current questions on the entry page and Today. Revisit if programs are edited
  mid-run.

- **PRIV3 — the tool guard can be walked around by a shell, and the faces it leaves open reach the journal's
  writes (finding 1 of the window-D seam review; not fixed here: structural, and the owner's decision).** `guard.check`
  reads a terminal command as text, so anything that does not spell a protected command or path literally passes:
  `$(which journal) config set …`, `` `which journal` config set …``, `J=journal; $J config set …`, `… | xargs journal
  config`, `journal config se"t" …` / `se\t`; `"hermes" config set …`, `'hermes' -p <profile> config set …`,
  `H=hermes; $H …`, `python3 -c "subprocess.run(['hermes', 'config', 'set', …])"`; relative paths after a `cd`
  (`cd ~/.config && cp /tmp/x journal/<profile>.json`, `sed -i … journal/<profile>.json`, `.her"mes"/profiles/…`);
  reading the serve token from the secrets helper and calling the web API with `curl` (`PATCH /settings`,
  `PUT /library/<id>`, `POST /pair`), `journal serve pair`, `journal mcp` over stdio, and `journal send <id>` (which
  also walks around `journal_start`'s new hold, below). The `patch` tool: a decoy `path` with the real file in the
  patch text, a V4A `*** Move File: … -> <protected>`, and `***Update File:` with no space or two spaces. The module
  says it is "a guardrail …, not a security boundary"; closing the class means a different mechanism (an allowlist of
  what the companion may run, or the companion's process without write access to the config and the journal's state,
  or the serve token out of its reach), not more patterns. Probes (read-only `guard.check` calls):
  `/tmp/seamD/guard/probe_guard.py` (terminal), `/tmp/seamD/guard/probe_patch.py` (`patch`); both re-run on this
  branch with the same verdicts (every case above ALLOWED; `env journal config set` and an exact `*** Update File:`
  BLOCKED).

- **PRIV2 — a scheduled ritual still ends a bare-`/x` window.** PRIV2 checked every automatic send for the pattern it
  fixed in programs (below, "Taken"). The one left is `tick.send_due`: a ritual whose slot comes up while a bare `/x`
  window is open is sent, the send ends the window (README: "until it goes quiet … or a prompt goes out"), and his
  next message, if it was meant privately, is captured as the ritual's answer. This is the documented rule since
  before the work packages and live since window A, so PRIV2 does not change it. Closing it is a choice between costs:
  hold the ritual while the window is open (`programs._off_the_record`, as the interrupts do) and send it when the
  window closes, which may be late or, past `windows.grace_hours`, never; or send it and keep the window open (a
  prompt send would no longer end a window, and his reply to it would need `/j` or a direct reply to be kept). The
  interrupts, a `/start <practice>` and the outage catch-up are not affected: the interrupts already wait, `/start` is
  his act, and the catch-up, summaries, notes, annotations and reflections are not prompts and end no window.
  A phase held back by the window meets the ritual in the same tick (the tick sends rituals before the programs
  step): the ritual's send ends the window after the step's `now`, so the step still calls the window open at `now`
  and holds the phase one more tick (`_held_off` asks at `now` and at the clock). The ritual is then the newest
  message and takes his reply to it; the phase follows at the next tick, after it. What this costs: a phase held by a
  `/x` window goes out one tick (a few minutes) after the ritual, and a reply he types to the ritual in that gap is
  the ritual's, not the phase's. It does not make the phase wait for the ritual to be answered.

- **WP12 review — `today.sources` and `coordinates` are not in the settings form.** `config.WEB_WRITABLE` has seventeen
  leaves; the form has fifteen. FRONTEND §1.7 names "today-block toggles", and `today.sources` is that (a checklist of
  `TODAY_SOURCES`); `coordinates` is a pair of numbers that `location` sets for him when he names a place. Neither is
  built: sources are rarely changed and a wrong list quietly empties the chat's today block, and coordinates have no
  safe free-text form. Add a sources checklist if he asks for one; the server already takes it.
- **WP12 review — a meta `PATCH` on a key whose value the server cannot parse still replaces it.** Block lists are
  now read, but a shape `entries.front_matter_lists` does not know (a nested map under `mood:`) shows as no mood, and
  setting `mood` from the sheet would replace it. The sheet sends only keys he changed, so this needs him to edit that
  very key. A `409 unreadable` on such a key would close it; not built, because nothing writes such a shape today.

- **WP4 follow-up — a `/next` or `/skip` that lands just after the settle moved the run on still moves it once more.**
  The command is bound to the phase read when it reaches the hook (`--expect`), so a tap sent for phase 1 that is
  handled a moment after the settle advanced to phase 2 matches phase 2. Telegram text commands carry no phase, and
  the window is the gap between his send and the hook's read (seconds, only when the settle fires in it). Not closed:
  closing it needs a message timestamp from Hermes and a rule about how soon after a send a tap can have seen the
  phase; the cost of the miss is one phase skipped, which `/back` undoes.
- **WP4 — the runs routes of `journal serve` are still the WP9 stubs.** `serve/api.py` answers `GET /runs` with `[]`
  and `/runs/{id}`, `/runs/{id}/insights` with 404 (API.md: "until WP4's `programs` module exists … then `[Run]`").
  WP4's file list does not include the serve routes and API.md does not define the `Run` JSON beyond "per-session entry
  stems and the current phase's text (`compose.phase_json`)", so WP4 does not invent it. `programs.all_runs`,
  `describe`, `entry_stem`, `answers` and `compose.phase_json` are what the routes need. To close: the package that
  builds the Run screen (WP10/WP11) wires the three read routes and defines the shape in API.md. The same holds for
  the other places that still behave as if no programs exist: `serve/writes.py` (`run_start` and `run_action` answer
  404, `block_add` answers `phase-not-open` for a program entry) and the library's write routes (WP11 made `GET /library` and
  `GET /library/{id}` list and show `lib.programs`, read only; the editors, fork and delete of a program are not wired). Each needs `journal program …` spawns and a JSON shape
  the API docs do not give yet; flagged by the independent review of WP4, left for the package that owns the Run
  screen. *Closed by the WP10 follow-up* (below, "Taken by packages"): the three read routes, `Run` in API.md,
  `run_start`, `run_action` and the phase block. **Still open:** the library routes list `lib.practices` only
  (WP11 adds program rows to `GET /library`), and `PUT`/`fork`/`DELETE`/`send` do nothing for a program (it is a
  directory of files; WP12 decides the editor: *decided in WP12*, not built, see "WP12 — programs are listed and run in the app" above). *Narrowed by the WP10 review fixes* (below): a `PUT` under a program's
  id is now refused (409 `is-program`), where it used to save a practice that hid the program.
- **WP0 — `kind_of` keeps returning a string.** WORK-PACKAGES.md and ARCHITECTURE.md say `kind_of → (id, session,
  n)`. Every caller compares the result with ids (`kind_of(p) in wanted`, `== "reflection"`, `not in ("note",)`) and
  `test_reflect` asserts `kind_of(made) == "reflection"`; the code's tests are the contract for existing behaviour,
  so `kind_of(path) -> str | None` stays and the tuple is `entries.entry_key(path)` (and `entries.name_parts(name)`
  for the name alone). WP4 (`entries_on`/`kind_of` recognise `-s03`) should use `entry_key` for the session number.
  To close: either amend the docs to name `entry_key`, or decide the tuple return and let WP4 move the callers.
  *Closed by WP4:* `entry_key` is the tuple API and WP4 reads the session number from it (`entries_on`/`kind_of`
  recognise `-s03` through it); ARCHITECTURE.md §6 and WORK-PACKAGES.md WP0 now name it.
- **WP1 — midweek `opener.shape`.** PRACTICES.md §1.2 gives midweek `a two- or three-line check-in (max ~700
  characters)`; the code it replaces (`prompts.OPENER_SHAPE["midweek"]`) says `a brief opener (max ~800
  characters)`. The built-in follows the code, because the goal is "byte-identical to today", and
  `test_golden.PracticeFilesTest` holds every file's shape and limit to `prompts.OPENER_SHAPE` / `validate.LIMITS`.
  To close: amend PRACTICES.md, or change the shape on purpose (a change to the opener request) in WP2 or later.
- **WP1 — standalone `practices_dir` default.** ARCHITECTURE.md §3 gives standalone `$XDG_CONFIG_HOME/journal/
  practices`; Decision 3 says `<state_dir>/practices`. Decision 3 is implemented for both (standalone that is
  `~/.local/state/journal/practices`). Hand-edited definitions arguably belong under XDG config; to close, amend
  ARCHITECTURE.md or make the standalone default an exception to Decision 3.
- **WP1 → WP2 — three things the files now say that the engine still reads from elsewhere.** (a) The built-ins'
  `companion:` lines follow PRACTICES.md (`orientation — where they are, what matters today`), not the skill's
  current Kinds text (`orientation: where {{they}} is …`, a pronoun token); rendering Kinds from the files (WP2)
  changes the skill's wording slightly and has to decide pronouns. (b) `collapse_hours: 6` in the files is today's
  `windows.collapse_hours` default; WP2 decides whether a configured `windows.collapse_hours` still wins (on the live
  install both are 6). (c) The times in the files are `config.DEFAULT_SCHEDULE`; `schedule.<ritual>` in the config stays
  the source until WP2's override rule. *Closed by WP2* (below, "Taken by packages"): (a) lines rendered as written,
  (b) the configured window still governs the built-ins, (c) the override rule.
- **WP2 — `/start` is Hermes' own command.** ARCHITECTURE.md §2/§5 and WORK-PACKAGES.md register `/start <id>` (and,
  in WP4, `/pause` and `/status`) as plugin commands. Hermes has all three built in (`hermes_cli/commands.py`:
  `start` acknowledges Telegram's start ping, `pause` is the global emergency stop, `status` the session report) and
  refuses a plugin command that collides (`plugins.py` `register_command`: "conflicts with a built-in command.
  Skipping."). WP2 registers `/journal-start` and has the dispatch hook rewrite `/start <id>` (in the journal's chat,
  with an id) to it before routing; bare `/start` stays Hermes'. WP4 has to choose for `/pause` and `/status`, whose
  bare forms mean something to Hermes (routing only `/pause <run>`-shaped text, or other names such as `/hold`).
  To close: amend the docs to name the routed form, or pick journal names that Hermes does not own.
  *Closed by WP4* for `/pause` and `/status`: journal names Hermes does not own, `/break` and `/program`, registered
  as they are (see "Taken by packages", WP4); PRACTICES.md and ARCHITECTURE.md amended. `/start` stays routed.
- **WP2 — the opener request does not fold in `practice.companion`.** PRACTICES.md §3 says `prompts.opener_request`
  "folds `practice.companion` into the request". Doing it changes the four rituals' requests (they all have a
  `companion:` line), and WP2's brief is byte-identical behaviour for the rituals (`tests/golden/*.opener_request.txt`
  holds it). The line reaches the companion through the skill's *Kinds* list instead. To close: accept a changed
  request on purpose (it is model input, not his message) and regenerate those goldens, or amend PRACTICES.md.
- **WP2 — an entry's bank is the practice as it is at the first reply.** An entry file is created when the first
  reply is captured, from the practice the library has then; an edit to the file between the send and that reply
  shows the edited bank in the entry (PRACTICES.md §7 says entries carry the questions they were sent with). The
  prompt's `plain_text` in the ledger keeps what was sent. To close: snapshot the bank on the record at send (larger
  records), or amend §7.
- **WP2 — three existing assertions changed on purpose.** "Existing test_engine … pass": `ConfigTest` asserted the
  removed config default (`schedule.evening` = 23:00 from `DEFAULTS`); it now asserts the file gives it. The two
  registered-command lists (`test_engine.CommandTest`, `test_hermes.RegisterTest`) gain `journal-start`.

- **WP2 review — a ritual's name alone still makes a file an entry for the readers.** `entries.entry_key` accepts
  `YYYY-MM-DD-evening.md` by name (WP0's rule for known ids), so a hand-written file of that name, with no engine
  front matter, is read by packs, reflections and the conversation writer's legacy matching as an evening entry.
  Capture no longer writes into it, and the companion's notes go to the engine's entry when the day has one
  (above); on a day with only his file of that name, her note still goes there. To close: ask for `type: journal` for known
  ids too, once existing journals are checked for ritual entries written before that marker existed.

- **WP3 — `.github/workflows/test.yml` ran only on pushes to `main` and on PRs,** so a branch waiting for review
  had no CI run to read. WP3 added `workflow_dispatch` (`gh workflow run test.yml --repo beejsbj/journal --ref
  <branch>`) and nothing else. To close: keep it, or run on every push (then PR branches run twice).
- **WP3 — `thread_started` takes the stamps, not the turn.** WORK-PACKAGES/PRACTICES give `thread_started(cfg, now,
  turn)`; the signature is `thread_started(cfg, now, stamps)` (a pure function of `hookstate` stamps), because the
  turn carries nothing the decision needs and WP9 or a test can call it with plain values. Likewise there is no
  `transcript.last_activity` refinement (PRACTICES.md §6.2): no Transcript abstraction exists yet (WP8), and the hook's
  own `last_his`/`last_send` are the activity it knows. A message steered into a running turn without the hook does not
  move `last_his`; the worst case is a block one message early. To close: add the refinement with WP8's Transcript.
- **WP3 — `today_injected_at` cannot be inside the current thread,** so PRACTICES.md §6.2's "and `today_injected_at`
  is not inside the current thread" is vacuous: `last_his` moves on every message, so a thread start is by definition
  after any earlier injection, and a retried turn is no longer a start. The stamp is kept for `journal status`
  (`today_count` has the per-day count). The `{thread_since, last_seen}` record names in §6.2 are not used; the stamps
  are the WORK-PACKAGES names.
- **WP3 — the `programs` source and the "program:" line wait for WP4.** `today.collect` returns `"program": null` and
  `today.sources` accepts `programs`; there are no run files to read yet. WP4 fills `program` with `{"text": …}` (the
  rendered line is already wired) and extends `tests/fixtures/today.json`. *Closed by WP4:* `program` is
  `{"text", "program"}` for the run he last touched (`programs.today_line`), and the fixture carries one (WP9 reads it).
- **WP3 — `today.collect`'s shape is not API.md's `GET /today`.** API.md §`/today` lists `{date, now_local, location,
  practices: [{id,title,emoji,scheduled,status,stem?,prompt_id?}], open_prompts, runs, scheduled, entries:
  [EntrySummary], reflection: {stem,carry_forward}, tasks: [...], weather, week}`. `collect` returns `{date, weekday,
  time, timezone, location, practices: {rows, open}, program, entries: [{stem, first_line}], reflection: {date,
  asked}, tasks: {count, names}, weather}`, and `tests/fixtures/today.json` pins that for both the block and WP9.
  Until WP9 decides, the fixture is the contract for the block; WP9 either maps `collect` to API.md in the route
  (adding `now_local`, `title`, `emoji`, `status`, `open_prompts`, `runs`, `week`, EntrySummary fields) or amends
  API.md to `collect`'s keys and extends `collect`. To close: WP9 picks one and updates the other doc or the fixture.
  *Closed by WP9* (below, "Taken by packages"): `GET /api/v1/today` serialises `collect` as it is; API.md is amended.
- **WP3 → WP9 — three fixed-date tests aged out (2026-10-17); and what they showed about the conversation writer.**
  `test_conversations` dates its rows around a fixed `T0` (2026-10-15), and three annotation-alignment cases call
  `context.companion_reply_after`, which asks `privacy.view` without a `now`; the view recomputed only the last 48
  hours from the real clock, so from T0 + 48 h nothing in those cases was classified private and they failed on
  `main` (CI on `main`, 2026-10-19). WP9 pinned the pass's clock in those three cases (`near_t0`). The review of WP9
  found the same gap is not only about annotations: `conversations.run` walks every row since its cursor, and a row
  that came after the last persisted pass (`conversations.json` `privacy.at`) but before `now - 48 h` had neither a
  persisted result nor a recompute, so it counted as public. After a tick outage longer than 48 hours, or a pass that
  stayed closed that long (the writer refuses to write while it is closed, then writes everything when it reopens), a
  `/x` exchange from the gap went into the entry: fail-closed turned into fail-open. *Closed by WP9:*
  `privacy.view` now also starts 48 hours (what a tick looks back on its own) before the last persisted pass, or
  before the conversation cursor when no pass is saved yet and the writer is on (`_unpersisted_from`; an unreadable
  stamp recomputes back to `KEEP_DAYS`; a cursor left by a disabled writer is ignored), which
  covers the writer, the sweep, recovery and `companion_reply_after` at once; `OutageTest` in `test_conversations`
  holds gaps of 0, 47, 50 and 200 hours. A very long gap costs one longer read of the sessions database, once. *Narrowed by PRIV* (below, "Taken by packages"): the
  saved stamp turned out not to prove coverage (a pass saved while the database was missing, another chat id, a
  clock ahead); readers now re-take the view for the rows they hold.

- **WP9 — the overlay carries `serve.*` but not `serve.bind`.** WORK-PACKAGES.md lists `serve.bind` among the overlay
  keys; Decision 8 makes the bind the operator's at deploy time. An overlay (`deploy/example-overlay.json` shows the
  shape) has `serve.port`, `host`, `token_command`/`token_key` and `trusted_proxies`, and no `bind`, so a merge of it
  leaves the server on loopback (DEPLOY-FRONTEND.md step 1, quarantine); `test_serve.UnitTest` holds it so. The
  operator adds `serve.bind` to the config when the firewall and route exist. To close: keep, or let the ops task add
  the bind to a deployment's own overlay as its record.
- **WP12 — programs are listed and run in the app, but their files are not edited there.** WORK-PACKAGES.md's WP12
  names the question-set editor, New practice and Fork/Reset (all practices); FRONTEND.md §1.5 adds "programs: v1
  edits sessions as raw Markdown, the program's front matter is a form". Not built, for three reasons that are each a
  piece of work of their own: `library.write`/`fork`/`remove` and `PUT`/`POST …/fork`/`DELETE` serve single-file
  practices (a program is a directory of `program.md` and `sessions/*.md`, with `programdefs.parse_program` but no
  `render` or canonical write, which WP4 also left open); the run stores phase numbers, so saving a program under a
  run in progress must refuse a change that moves a phase (the check "§7 refuse a phase edit under an active run" is
  still not in `library.problems`); and the form for a program's front matter has no definition JSON to round-trip
  (`GET /library/{id}` of a program serves `sections` as a read-only flattening). The app says so where Edit would be
  (`/library/<program>/edit` answers "Programs are not edited here yet"), and the Library page still shows the program
  file. To close: `programdefs.render` and `from_dict`, `PUT`/`fork`/`DELETE` that take a directory (API.md already
  says "programs: the whole directory" for fork), the active-run guard, then a raw editor in `editor.tsx`. *Closed by
  BUILTIN-EDIT* (below, "Taken by packages"), without a form: a program is edited as its files, one at a time, raw
  (no `render`/`from_dict` needed), `PUT` and `fork` take a directory, `DELETE` resets a built-in program's directory,
  and the run guard is `library.run_conflict`. **Still open:** a form for `program.md`'s front matter, making or
  removing a session file from the app, and `DELETE` of a program of his own (a folder that is no built-in's: still
  404; he removes the folder).
- **WP12 — drag to reorder is up/down buttons.** FRONTEND.md §1.5 asks for drag-to-reorder of sections and
  questions. HTML5 drag and drop does not work with a finger and is awkward by keyboard, and this is a phone-first app
  (the owner, 2026-10-19), so each question and section has Move up / Move down buttons (a question crosses into the
  neighbouring section at the end of its own). The pin rule is unchanged (a moved question that has an id pins it).
  To close: add a pointer-events drag on top of the same moves if it is missed.
- **WP11 — practice emoji.** API.md's example gives the evening practice the companion's emoji (🌿) and
  `Practice.opener_text` falls back to the companion's emoji for `{emoji}`, but `document.summary`/`practice_ref`
  return `""` for a practice with no emoji of its own (the code and `test_the_evening_document` pin `""`). The web
  app shows no emoji in that case. To close: resolve the effective emoji in the API (`p.emoji or
  names.emoji`) and update the test, or accept the gap.
- **WP11 — node and the artifact.** CI pins Node 22; the host-independent build is the CI artifact. A build
  made on another Node (a remote sandbox ran 25) can differ in bytes, and the workflow would then fail the diff;
  commit only the CI artifact.

- **WP4 — session 10's interrupts are listed, not scheduled.** Phase 3 (`type: schedule`) goes out with the eight
  interrupts at their offsets (`+2h` …) and question 3.1 as authored; its answer is an ordinary phase answer. The
  `YYYY-MM-DD HH:MM` confirmation line, the parse and the one-offs are WP5's (PRACTICES.md §4.8), so nothing promises an
  interrupt that would not come. Likewise `insights: compile+synthesis` compiles only until WP5 adds the synthesis. *Closed by WP5* (below, "Taken by packages"): the confirmation, the one-offs and the synthesis.
- **WP4 — no program block yet; a phase answer can carry the today block.** WP6 adds the program block (which replaces
  the pack on a phase-answer turn) and `journal_program`. Until then a phase answer has no pack (phases have no
  opener pack), and since a phase send starts a thread, his answer is a thread start: `turncontext` may give that
  turn the today block, whose `program:` line says where he is. Harmless, and WP6's budget rule removes it. *Closed by WP6* (below, "Taken by packages"): the program block, `journal_program`, and the budget rule.
- **WP4 — a program file cannot be rendered back yet, and §7's "refuse a phase edit under an active run" is not in
  `library.problems`.** `programdefs` parses and checks; `render` (and the round trip) for programs waits for the
  editors (WP12), which are also where inserting or removing a phase mid-run can be refused. Today a hand edit that
  renumbers phases moves a run's position silently (the run stores phase numbers).
- **WP4 — the ledger grows by about 3.6 KB per phase sent.** A phase record keeps its `html`, `plain_text` and
  `phase_text` (the entry's callout); after the scripted ten-session run the ledger is about 187 KB (51 phase prompts).
  An active run's prompts are exempt from prune, so a run left for months keeps them. To close: keep, or drop `html`
  from phase records (fold rebuilds the header from the record; a re-send recomposes).
- **WP4 — main's CI has been red since 2026-10-17** on three conversations tests whose privacy-pass clock aged out;
  wp9 carries the fix (`4bd08ac`, pins the clock). WP4 cherry-picked it so its CI reads green; the rebase drops the
  duplicate once wp9 lands.
- **WP5 — the interrupt's reply is a gateway-side write, though the package is tick-side.** The tick sends the
  interrupts (a spawn-side module, `programs.send_interrupts`), but his reply is captured by the dispatch hook, which
  runs `capture.record`/`entries.record_reply`/`programs.reply_note` as the gateway loaded them. A gateway still on
  WP4's code writes such a reply as a plain block (no `interrupt` key, so it reads as the tail of the phase before it)
  and notes it with the generic reply note. WP4 is not live yet (window B), so nothing is exposed today: DEPLOY.md says
  WP5 rides window B with WP4 and that a run must not reach session 10 before then. To close: nothing, once window B
  is done.
- **WP5 — the web app's entry document does not carry the interrupt.** `serve/document.py` reads the `%% journal
  phase:N %% ` key into each of his blocks (`phase`); a block under `%% journal interrupt:N %%` has `phase: null` and
  no other mark, so the PWA would show it as one of his words with no label. API.md's block shape has no `interrupt`
  field. To close: WP11 reads the key (add `interrupt: N` to the block and to API.md) or shows the `## Prompt` callout. *Closed by WP11* ("Taken by packages", WP11 review): the block carries `interrupt: N` and the entry view labels it.
- **WP5 — a repeat run of session 10 shares the interrupt keys.** A second confirmation after interrupts were sent
  (the session run again with `/start fix-life 10`) gets its own prompt ids (`s10-b2-i3`), but his answers go under
  the same `%% journal interrupt:3 %%` key, so the insights document joins the two days' answers to interrupt 3. The
  `## Prompt` callouts carry the date. To close: key the block by batch (`interrupt:2.3`) if a repeat ever matters.
- **WP5 — the insights document is compiled at the end of the run, before the protocol day.** `complete_session` writes
  it after session 10's last phase; the interrupts come later, so that first document says `(not yet sent)`. His
  `/start fix-life insights` (the line the session-10 summary ends with) compiles it again, with the interrupt
  answers, and runs the synthesis. Compiling again automatically when the last interrupt is answered was left out:
  it is a second write to a file he may already have read.
- **WP5 — a definition that goes away between the run and its protocol day.** `send_interrupts` loads the
  program for the question's title, emoji and session; if the definition was deleted or broke after the run was
  scheduled, the interrupt is not sent (the tick logs `interrupt not sent`), and it is `missed` once the grace window
  is over. This is the stance the rest of the program step takes (a run whose definition is gone waits). Persisting
  the display metadata on each scheduled item would carry them through; not done, since nobody edits fix-life between
  confirming the day and living it. To close it: snapshot `title`, `emoji`, `session_title`, `session_count` on the
  item when it is timed.
- **WP10 follow-up — a reply to a session-10 interrupt from the web.** The interrupt is a ledger prompt of its own
  (`%% journal interrupt:N %%`); `phase` on a block cannot address it, and the settle ignores interrupt replies, so
  adding it later is `interrupt: N` beside `phase`, resolved to the item's prompt. Not wired; Telegram's reply
  gesture remains the way to answer one.
- **WP10 follow-up — program definitions in the library routes** (see the closed WP4 item above): still practices only.

- **WP6 — a phase answer the hook missed gets no program block.** The block is queued by the dispatch hook when it
  captures the answer, as the pack is. A reply caught later by the sweep (Hermes' busy path) was never a turn the
  hook saw, and the companion's reply to it, if any, comes without the stance. The same is true of a pack today.
  To close: nothing, unless the sweep's catch-up reply (`recovery`) should carry it.
- **WP6 — the block for a voice phase answer is unverified until live.** The pointer waits in `hook-state.json` and
  the block is read from the entry when the LLM hook runs, after `_capture_voice` has written the transcript (tested
  with the hooks called in that order, as Hermes calls them). That Hermes calls `pre_llm_call` after the transcript
  is in the message for a phase voice note is the same assumption the voice capture already rests on; the first
  live voice phase answer is the check (DEPLOY.md).
- **WP6 — an interrupt's reply gets no program block.** An interrupt is a timed question, not a phase of the
  run: it has no phase text to show and the session's stance would be a stranger's to a one-line answer. It
  carries its reply note, as in WP5. If the companion should have the stance there too, the block can take the
  interrupt's question in place of the phase text; not done, since the spec's block is "the phase text and his
  answers so far".

- **WP7 — the chat readers' row definitions still differ; WP7 kept them, it did not unify them.** Before the ports
  each reader read Hermes rows its own way, and a refactor that must not change behaviour keeps those readings: they
  are `Row.meta` findings now (`loose`, `frame`, `delivery`, `reply`, `ends_turn`), pinned by
  `tests/golden/hermes_rows.json`. What they still do: the context pack and the quote verifier take a compaction
  frame, a task-list note or a `[System note:` stored as a user row for his words (they are in his "Telegram
  messages" and the verifier's corpus); the outage catch-up counts `[SILENT]`, an interruption and a typed row of hers
  as a real reply, and an attachment placeholder as a message of his; annotation alignment can take `[SILENT]` or
  Hermes' ⚠ failure copy for her reply; the sweep reads the words of a platform notice other than the internal one as
  a capture candidate. Unifying them on `Row.kind` changes model input (the pack, the catch-up) and what can be
  captured, so it is a behaviour change for a package of its own. To close: decide reader by reader, change the
  reader to read `Row.kind`, and regenerate the golden's expectations for that reader on purpose.
- **WP7 — `audio.py` still imports Hermes' speech-to-text in the core** (`tools.transcription_tools`, for audio files,
  under Hermes). `tests/test_purity.py` allows it by name, with the reason. To close: put transcription behind the
  adapter (a method of the Hermes companion, or a port of its own) in WP8 or WP13, and drop the allowance (the test
  fails when an allowance is no longer needed).
- **WP7 — the transcript's `last_activity` exists but nothing reads it yet.** WP3's open point (a thread start could
  use the transcript's newest row of either role, not only the hook's `last_his`/`last_send`) waits for WP8, which
  moves the hook's inbound handling into the engine; the method is on the port, the Hermes adapter and
  `NullTranscript`, and tested.
- **WP8 — left as they were, not WP8's brief:** `audio.py` still imports Hermes' speech-to-text (WP7's open
  point; `test_purity` allows it by name; WP13); the transcript's `last_activity` is still unread (WP3's open point: a
  thread start would change when the today block appears, which is a behaviour change, not a refactor); and
  `journal_set_location`, which changes the journal's place and timezone from what is said in a chat, was not refused
  in an off-record turn (it records no words of his; if a place said off the record should not move the journal, add
  it to `tools.WRITES`). **Closed 2026-10-20:** it is refused now (see "Taken", OFFLOC).
- **WP8 — the seam is Telegram-keyed, and `tools.call` does not run the guard.** `receive` and `begin_turn` (the `/x`
  marking and the off-record turn mark) act only when the platform string contains `telegram`, as `turncontext` does
  (WP8 kept this, as the code was). For a non-Telegram adapter: `receive` passes every message untouched (`/x` is not
  stripped, nothing is marked), `begin_turn` marks no turn, and `tools.call` then refuses a write only on the second
  signal (the privacy pass over the chat), which needs a transcript the adapter may not have; `tools.call` never runs
  `guard.check` (the Hermes `pre_tool_call` hook does, on every tool). WP14 (a REPL or plain chat) must either key
  these on the configuration or the adapter instead of the literal string, and call `guard.check` on its own agent's
  tools. To close: replace the `telegram` test with a `cfg`/adapter-supplied predicate. (README "Adapters" and
  ARCHITECTURE §1, §2 checklist (e) say the same.) **Closed 2026-10-27 by CHANNELS** for the string test: `receive`,
  `begin_turn` and `turncontext` ask `channel.ours` (the configured platform, chat and owners). `tools.call` still
  does not run the guard.

- **MCP — a note from an MCP client is filed as the companion's.** The journal has two voices, his and the
  companion's (`entries.add_companion_note` writes under `<companion>'s notes`), so `journal_add_note` from Claude
  Desktop or any other client lands there, labelled as hers, though no companion persona wrote it. Kept, because the
  rule that matters holds (it is never among his words) and a third voice is a change to the entry format, the
  document and the web app. To close: accept, or add a notes heading per writer (`notes_heading` from the client's
  name) and teach `document._notes` and the web app to label it.
- **MCP — `journal_save` takes no client id, so a client's retry of a save that did land saves it twice.** The web app
  sends a `client_id` (`capture.capture_web` dedupes on it); an MCP tool call over stdio has no retry of its own, and a
  model's "try again" would come with a new id anyway. To close: an optional `client_id` argument passed through, if
  duplicates show up.
- **MCP — no block on an existing entry, no program answer.** `journal_save` is `POST /entries` (an open prompt's
  reply, else a `/j` note); the web app's `POST /entries/{stem}/blocks` (append to a named entry, or answer a program's
  open phase and spawn the settle) is not mirrored, so a program phase is answered in the chat or the web app. To close:
  a `stem` (and `phase`) argument over `capture.add_web_block`, with serve's `_phase_target` checks moved where both
  faces can call them.
- **CAPMODE — under `capture.mode: arm`, the first message after a prompt is still taken as its answer (rule 2), so
  ordinary chat can still be journaled through it.** The hook, the sweep and `capture.match` file the first message
  while a prompt is open as its reply, and an open program phase takes every message; `arm` leaves that rule alone
  (the decision's list says "replies to prompts"). With 06:00 and 23:00 rituals and a 6 h window, his first request
  to a general agent in those windows ("what's the capital of Australia?") becomes the morning or evening entry's
  words, and the whole thread that follows it goes into that entry. It runs against what `arm` is for, and the
  README and the skill now say it plainly (the skill tells her, and `/x` keeps such a message out), but whether to
  change it is the owner's: (a) under `arm`, practices take only direct replies (a Telegram reply to the prompt) and the
  first-message rule stays for program phases only; (b) a shorter next-message window under `arm` (say 30 minutes
  instead of `collapse_hours`); (c) leave it, as now. (a) is the cleanest for a general agent and costs the habit of
  answering a prompt by just typing; the sweep and the hook would both change with it (`capture.match`,
  `sweep.visible_at`). Not decided; CAPMODE ships (c).
  Related, same rule in both modes: a direct reply to an annotation or reflection while another prompt is open is
  filed on that prompt by the hook (`capture.match` knows only prompt ids), not as a reply to the journal's own
  message; the words are journaled, and the writer never sees it as a reply to the annotation.
- **CAPMODE — `capture.mode` is not in the web app's settings form (`config.WEB_WRITABLE`).** It is set in the
  config file or Hermes settings, and the skill follows it only at the next gateway restart (the tick at once).

- **TSAUTH — the live `journal-serve` unit's sandbox is not in effect.** Found in review: the process shares the
  mount namespace of a login shell, and a transient user unit with `ProtectSystem=strict`, `ProtectHome=read-only`,
  `PrivateTmp=yes` could still write outside its `RuntimeDirectory`. With `kernel.apparmor_restrict_unprivileged_userns=1`
  (Ubuntu) and systemd 255, a user manager cannot build the namespace and skips those settings silently;
  `RuntimeDirectory=` itself works. The serve threat model (DEPLOY-FRONTEND.md) leans on the sandbox, so this is
  open: a system unit with `User=` the service user (the system manager can build the namespace), or accepting it
  explicitly. Not changed by TSAUTH. *Closed 2026-10-26 by SERVE-UNSANDBOXED* (below, "Taken by packages"): the sandbox
  is removed. The finding was half right: the mount namespace is not built (home writable, `/tmp` shared), but a user
  namespace is, and that is what broke Tailscale identity.

- **TSAUTH — the example overlay shows the reverse-proxy route, not `tailscale serve`.** `deploy/example-overlay.json`
  has a published `host` (`journal.example.com`), a port and the reverse proxy's subnet; a deployment behind
  `tailscale serve` (`host.tailnet.ts.net:8790`, SERVEPORT) also sets `socket` and `tailscale_users` (TSAUTH). The
  overlay drives the unit example's golden test. To close: decide whether the example should show the `tailscale
  serve` shape (then the example unit gains `RuntimeDirectory=journal-serve`) or keep the reverse-proxy route.

- **WEB-DESIGN — whether an opener can carry what was said off the record has not been verified.** Today now shows
  every opener in full (the entry page already did). The opener is written by the tick through `hermes chat
  --oneshot` in the companion's own profile, from a context pack the privacy pass filters. Whether that call can reach
  past the pack (the companion's memory, or tools that read the chat) and so bring something said under `/x` into the
  opener in other words has not been checked; the serve side's guard in `document.prompt_of` only catches a literal
  echo of the command or the notice. To close: trace what the one-shot call can read in the companion's profile (memory
  providers, toolsets enabled for it) and either show it cannot, or narrow the call.

- **BACKLOG-BACKFILL — older unannotated files, and unannotated program sessions, keep the old boundary.** A
  file the engine starts without an annotations section now ends his words with a hidden `%% journal:end-of-words %%`
  line (Taken, below). Files written before it have no such line and are not migrated: in one that has no
  annotations heading, a line of his shaped like `## <Companion>'s annotations` (or `… notes`) is still read as the
  boundary. Adding the line afterwards cannot be done safely (the engine cannot tell that line of his from a notes
  heading of hers below his words). A program session's entry (`_new_session_file`) with `annotate: false` (a
  situational program can say so) does not get the line either: its `## Summary` code reads to the next `## `
  heading and would take the line for summary text. To close: decide whether either is worth the work (a summary that
  stops at the line, then the line in session files too).

## Taken by packages (recorded here; later packages follow them)

- **WP0 — an unknown entry id is recognised only with the engine's authorship marker.** The spec's fallback
  (`kind: <id>` or `practice: <id>` in the file's own front matter) alone would have turned a hand-written
  `2026-10-20-thoughts.md` with `kind: thoughts` into an engine entry, and `journal_add_note` (whose default
  target is the day's most recent entry) would have appended the companion's notes into his file; before WP0 the
  five-name regex guaranteed the engine only appended to files it created, and `is_analysis` already requires
  `author:` for the same reason. Rule: a file whose id is unknown counts as an engine entry only when its front
  matter claims the id **and** says `type: journal` (`entries.ENGINE_TYPE`), which every engine-written entry
  carries; `kind:`/`practice:` without it is his file. Writers in WP1/WP4 keep writing `type: journal` and
  `source:` in every entry they create; the web app writes his words through the engine, never a bare file.
- **WP0 — one set of ids per lookup.** `entries.entry_key`, `entries_on` and `kind_of` take the same optional
  `ids`; `capture.add_companion_note(known_ids=…)` threads the caller's ids through all three, so an id a
  library knows finds its entry without front matter (as the rituals always did). When WP1 replaces
  `entries.known_ids` with the library's ids, the callable parameter becomes redundant and can go.
- **WP0 — a name that reads two ways is settled by the known ids, longest id first.** `level-10` and
  `reset-s01` are valid ids, and by name alone they are also the tenth `level` and session 1 of `reset`.
  `entries.name_candidates` lists every reading; `entry_key` takes the first whose id is known (so a practice
  `level-10` beats a tenth `level` when both exist), else the one the file's front matter claims (with the
  `type: journal` marker). A library should not define both `level` and `level-10`; `library.problems` (WP1) may
  warn on it. (WP1: it does.)
- **WP1 — the facade reflects the packaged built-ins only.** `questions.py` (and so everything that sends) reads
  `library.builtins()`, never `practices_dir`: WP1 promises no change in the tick, so a user file is listed,
  shown and validated but not sent until WP2 keys the engine by practice. For the same reason
  `entries.known_ids` stays the four rituals plus `j`/`note`: it means "ids the engine writes", and a library id
  the engine has never written would let `journal_add_note` append into a hand-written `YYYY-MM-DD-<id>.md`
  (the hazard WP0's marker rule closes). WP2 widens it to the enabled library ids when it starts writing them,
  and should weigh that hazard for user ids.
- **WP1 — CLI actions are `list | show | validate | render | fork`.** The docs name `fork`; the WP1 brief named
  `render`. Both shipped: `render <id|FILE>` prints the canonical Markdown (what the web app's "Raw Markdown" tab will
  show; also a tidy for a hand-written file), `fork <id>` copies a built-in into `practices_dir` under its lock.
  `hermes journal practices …` is the same parser.
- **WP1 — `source` is on the library, not the practice.** `Library.source(p)` is `builtin | fork | user` (API.md's
  `GET /library` values): a parsed file cannot know whether a built-in of its id exists. `Library.to_dict(p)` is
  the JSON shape (`journal practices … --json`), for serve to reuse.
- **WP1 — `Practice.plain_opener(names)` renders `opener.plain`.** `{greet:Word}` is today's greeting
  (`Word, Sam 📓` / `Word 📓` / `Word, Sam.` / `Word.`) with the practice's emoji, else the companion's; a bare
  `{greet}` is a parse problem. `tests/golden/*.plain_opener.txt` captured `compose.plain_opener` at `1412843`
  for four shapes of names, and the files reproduce it; WP2's `compose.plain_opener(practice, names)` should call
  this and keep that golden byte-equal.
- **WP1 — two parser details for exact round trips.** A section heading's number is stripped only as `N. `
  (dot and space), so `## 3.5 hours of sleep` keeps its title; `render` adds one layer of quotes to a front-matter
  value that starts and ends with the same quote, because `entries.front_matter` strips one. A leading
  byte-order mark is dropped and a closing `---` on the last line counts, so neither reads as "kind is required".
- **WP1 — library ids are reserved against the dated table, not the live registry.** `library.reserved_ids` uses
  `config.HERMES_COMMANDS` (PRACTICES.md §1.1 names it) and the *configured* opt-out name, never
  `config.hermes_commands()`, which also reads Hermes' `COMMAND_REGISTRY` when Hermes happens to be imported. The
  gateway (Hermes loaded) and the cron tick (not loaded) must resolve the same library, or a practice would exist
  in one process and be ignored in the other. Cost: a command Hermes adds after the table's date can be taken as
  a practice id until the table is updated. `opt_out_problem` still reads the live registry (its behaviour from
  before WP1): when WP2 teaches it the library ids and `/start`, it should get the same treatment, or the
  effective opt-out prefix can differ between the gateway and the tick.
- **WP1 — a practice file is read strictly where a mistake would lose a question.** Numbers are ASCII digits
  (`str.isdigit` and `\d` take `²` and `٦`); a line that starts like a list item but is not a `- ` question, a
  `## ` section with no questions, a `{#pin}` with no text, a front-matter line with no colon (`enabled false`
  would otherwise be no setting at all), a front-matter key given twice and a slot number
  with a leading zero (`schedule.01` would merge with `schedule.1`) are problems. Any exception the parser
  raises becomes the file's problem (`library._parse_guarded`), so one file can never take the library, the CLI
  or doctor down. Prose is still ignored.
- **WP1 — config paths.** A `state_dir` or `practices_dir` that is not a string is a `ConfigError` (it was a
  `TypeError` traceback); a relative `root`, `state_dir` or `practices_dir` is a doctor warning, not resolved
  against anything, because each process (cron, gateway, shell) has its own working directory.
- **WP1 — `Practice.schedule` and `Practice.sections` are tuples** (PRACTICES.md §2 sketches lists), and
  `Question` carries `pinned`. A loaded `Practice` is frozen and shared by the library cache, so its parts must
  not be mutable: `opener` (PRACTICES.md's `opener: dict`) is a `library.ReadOnlyDict`, a dict that refuses
  changes (read it as `opener["limit"]`; `dict(...)`, JSON, copy, pickle and `dataclasses.asdict` work), and is
  left out of the hash, so a `Practice` can be a set member or a cache key. `dataclasses.asdict` keeps it a
  `ReadOnlyDict`, so a payload to edit in place starts from `dict(...)` or from `Library.to_dict` (the JSON shape
  serve will use). `render` needs `pinned` to write `{#id}` back only where the file had one.
- **WP2 — `schedule.<id>` is an override of the file, and the config no longer has a default for it.** A default
  would override every fork (`DEFAULTS["schedule"]` was the four rituals' times), so `cfg.schedule` now holds only
  what a config layer set. An entry is read the way the old merge read it: its keys on top of the practice file's
  first slot (`{"time": "21:00"}` keeps the weekly's Sunday), one slot as the result (a multi-slot file is replaced
  whole), `enabled: false` = not scheduled while `/start` still sends it. An unusable entry (bad time or weekday, an
  unknown id) is ignored and named by doctor, where before a bad time crashed the send step every tick. Doctor warns
  on every entry in force, saying whether it changes anything. `journal practices migrate-schedule` forks a built-in
  with the override written in, never rewrites a file of his (it prints the lines: a rewrite would drop his prose),
  and does not touch the config layer (it says where the key lives). `config.DEFAULT_SCHEDULE` stays as the record of
  the old defaults (`test_golden` holds the files to it).
- **WP2 — the configured window still governs the built-ins.** A built-in's prompt records no window, so
  `windows.collapse_hours` decides when it folds and stops taking the next message, as before (on the live install, 6 =
  the files' 6). A practice of his own, and a fork, folds after its file's `collapse_hours`, recorded on the prompt at
  send (`capture.window`). Such a prompt also records its `title` (and `emoji`), so a fold or a late reply still has
  them after the file is renamed or deleted (`library.for_prompt` → `placeholder`), and `annotate: false` when it asks
  for none. A built-in's record carries nothing new: the rituals' ledger records are byte-identical
  (`tests/golden/*.send.json`).
- **WP2 — `entries.known_ids` is not widened to the library.** WP1 asked WP2 to weigh the hazard: with a user id known
  by name, a hand-written `YYYY-MM-DD-gratitude.md` would read as the engine's and receive the companion's notes.
  Every entry the engine writes carries `practice: <id>` and `type: journal`, which is how a user practice's entries
  are recognised (WP0's front-matter rule), so the ids known by name stay the four rituals plus `j`/`note`.
- **WP2 — the skill's *Kinds* lines are the files' `companion:` lines as written,** with no pronoun tokens (a
  practice file is his text; the built-ins say "where they are"), one per enabled practice in library order with its
  `/start <id>`. They are rendered when the gateway starts: a practice added later is sent and startable at once and
  joins the list at the next restart.
- **WP2 — what closes a period.** The day closes on the enabled `closes: day` practice scheduled latest (its latest
  slot that day sets "an hour after"), the week on the first `closes: week` practice in library order (its slot's
  weekday ends the week; unscheduled by an override, the file's weekday still does; none: Sunday). With nothing
  closing the day, only the fallback time writes the reflection.
- **WP2 — `/start <id>`.** The first word, case-insensitive, with the two legacy aliases (`morning-journal`,
  `evening-journal`); resolved against the library at call time; a disabled practice is refused with the list, as is
  an unknown id or none. The ritual commands are `/start <id>` with the id given. `journal send <id>` takes any
  enabled id (unknown or disabled: exit 2). The agent-neutral resolver is `journal/commands.py`; the Hermes routing
  (`/journal-start`, see "Raised") lives in `hermes/commands.py`.
- **WP2 — the opt-out verdict reads the dated table only** (WP1's recommendation): `config.hermes_commands()` (which
  added the live registry when Hermes was loaded) is gone, so the gateway and the tick agree on what the opt-out
  prefix is. Hermes' names are checked before the journal's own (which now include `start`, `next`, `skip`, `back`,
  `pause`, `status`, `journal-start`), so `/status` still says "Hermes' own commands".
- **WP2 — `opener.mode: plain` is honoured:** no context pack, no model call; recorded as `opener: plain:practice`.
- **WP2 — `tick.send_prompt(cfg, record, log, *, retry, refold, label)`** is the one delivery path (retries and the
  attempt count, `sent.jsonl`, a re-send's refold, the mirror). `retry()` gives the plain-opener fields for the
  immediate second try, with `asked` and `citations` emptied (they belong to the model's opener); `label` is what the mirror says was sent (default "the <kind> prompt"). Its log line now names
  the opener actually sent after a retry (`plain:send-retry`), where it used to name the first one.
- **WP2 review — a first reply never writes into a file that is not that practice's entry.** At the first capture
  (`capture.entry_stem`), a file already at the prompt's stem is used only when its front matter says the engine wrote
  it for that practice (`type: journal` and `kind:`/`practice:` = the id) and no other prompt writes to it; otherwise
  the entry takes the first free `<date>-<id>-<n>` past the practice's slot numbers (so never a later slot's name) and
  the prompt records it as its `stem`. Rituals included: a hand-written `2026-10-14-evening.md` was appended to before.
  The voice-note path asks the same function when it plans the path, so the note and the kept audio agree with it.
  Once a prompt has an entry, its `entry_path` decides (a same-day re-send keeps the stem it had), and the
  companion's note for a kind prefers that day's engine-written file over a hand-written one of the same name
  (when there is no engine file, the by-name rule below still applies).
- **WP2 review — prompt ids are unique by construction of the library.** A user practice whose id is another
  practice's later slot (`walk-2` while `walk.md` has two or more slots) is ignored with a problem (the suffixed one
  gives way: the other keeps every slot); `_suffix_clash` stays a warning for the rest. The repeat names of notes and
  analyses (`j-2`, `note-3`, `reflection-2`, `week-2`, and `-sNN` forms) are reserved: they would share a /j note's
  prompt id or file, or read as a week review.
- **WP2 review — a fork folds as the built-in did.** `library.fork` (and so `migrate-schedule`) writes the configured
  `windows.collapse_hours` into the copy (the file's 6 only when the configured value is unusable), because a fork's
  prompt records its file's window while a built-in's records none. The commands say so when it differs from 6.
- **WP2 review — an override that shadows a file is said where he edits.** `practices fork` warns when `schedule.<id>`
  is set for the id (it will decide the times of the copy), `practices validate` whenever the override differs from the
  file; neither changes the exit status (the file is valid). DEPLOY.md makes removing the live install's `schedule` a
  recommended, owner-approved step of the WP2 deploy. `migrate-schedule` prints what it already forked when a later fork
  fails.
- **WP2 review — `/journal-start` under Hermes' slash gating.** Hermes gates the rewritten command name, and the plugin
  leaves an operator's gating as it is. A gated install lists `journal-start` and `journal_start` in `user_allowed_commands`; for such an install that is a deploy step
  (DEPLOY.md 1b), and an explicit `command_menu` is left alone.
- **WP2 review — a `schedule` that is not an object** is ignored with a config warning (doctor and status), and a
  `schedule.<id>` that is not an object is kept so doctor names it; neither stops config loading.
- **WP3 — a chat turn now writes `hook-state.json` (its `last_his` stamp).** `HookRestartTest.test_only_chat_turns_
  touch_the_state_file` asserted that a chat turn finding none of its records leaves the file untouched; with thread
  stamps every message of his moves `last_his`, so the test now asserts that a chat turn changes only `stamps` and a
  CLI/cron turn changes nothing. (A deliberate change to an existing assertion.)
- **WP3 — only the journal's chat platform has threads.** `turncontext.build` stamps and injects only for a
  `telegram` turn (the platform the dispatch hook and the pack already use); a CLI or cron turn neither gets the block
  nor spends the thread start. Another chat platform joins when the adapter extraction (WP8) names it.
- **WP3 — the tick stamps `last_send` in `tick.send_prompt`,** after a prompt is delivered (the one delivery path), so
  re-sends and `/start <id>` count; reflections, annotation replies and cron deliveries are not prompts and do not
  start a thread for the block. A failure to stamp is logged and never fails the send.
- **WP3 — what the cache holds and how stale it may be.** `today-cache.json` is `{at, tasks: {date, count, names, at},
  weather: {text, at}}`, written atomically (0600) by the `cache` step between `mirror` and `prune`. Tasks are those
  `sources.tududi_due` returns (open, non-habit, `due_date` on or before today, overdue included; it reads the
  structured reply, not the display lines of `sources.tududi`, which are cut to 30 before any due filter); the block shows them only for the local date they were fetched. Weather is the
  current line plus today's range and rain chance, shown while at most four hours old. A source that fails keeps its
  previous part; the other source still refreshes. Enabled by `sources.tududi.enabled` / `sources.weather.enabled`
  (+ `coordinates`), not by `today.*` alone.
- **WP3 — when a thread starts.** A message opens a thread (and may carry the block) when it is the first the hook has
  seen, or his previous one was more than `conversations.quiet_minutes` ago, or it falls on a later local day than his
  previous one (a chat past midnight meets the new day), or a prompt was sent since, or the last start found no block
  to show (`today_owed`: a start is not spent on nothing). A pack turn still spends the start (the block waits for the
  next one).
- **WP3 — line rules.** Lines appear only when they have something (so no "open prompt: none"). Practices are listed
  for the slots that fall today (weekday-aware) in library order, then any other practice sent today; entries are
  today's engine entries in name order (four shown, `+N more`), the first line of his words with the engine's time
  marker and headings removed, and the engine's own lines (audio embeds, transcript placeholders, bare markers) skipped; the reflection line is the first question after `**To carry forward**` in the latest
  day reflection of the last two days. Order and the cut-from-the-bottom rule are PRACTICES.md §6.1.
- **WP3 — config sanity.** `today.max_chars` below 200 is raised to 200 (doctor names it); unusable `max_chars` or
  `cache_minutes` fall back to 1200 / 30 with a doctor warning; an unusable `today.sources` means all six.
- **WP9 — `GET /api/v1/today` is `today.collect(cfg, now)` as it is,** not API.md's earlier sketch (`now_local`,
  `practices[].status`, `open_prompts`, `runs`, `week`, EntrySummary rows). API.md's own rule is that every shape is
  the engine's structure serialised, never a second model, and the block and the web app then cannot drift
  (`tests/fixtures/today.json` pins both; `test_serve.ReadRoutesTest.test_today_is_today_collect`). A field the web
  app needs later is added to `collect` itself, with the fixture, by the package that needs it (WP4: `program`/runs;
  WP11: `week` for the week strip, if it wants it there rather than from `/insights/weeks`). API.md is amended.
- **WP9 — `/healthz` reports `code`, not `commit`.** Neither install carries the git sha (a plugin install copies
  files; a wheel built from git keeps only the version), so `version.code_id()` is a sha256 over the package's `.py`
  files; the tick writes it (and `version`) into `last-tick.json`, and doctor's `same version as tick` row fails when
  this process's code differs from the tick's (warns while the last tick predates the stamp). API.md and
  ARCHITECTURE.md §5 are amended.
- **WP9 — the token and the cookie.** The token comes from `serve.token_env`, else `serve.token_command`, else (neither
  configured) a generated `state_dir/serve-token` (0600); a configured command that fails stops `journal serve` at
  start rather than falling back to the file. The cookie is `HMAC(cookie key, sha256(token))` with the key in
  `state_dir/serve-auth.json` (0600): a cookie is not a bearer token, and changing either signs every browser out.
  It is one value for every paired browser (no server-side session list in v1): logout clears this browser's
  cookie; `journal serve token --rotate` (a new key, and a new token when file-backed) is how every browser is
  signed out. Pairing codes are stored as sha256, live 10 minutes, and are spent under `locked_state`.
- **WP9 — the session cookie is `__Host-journal_session`,** parsed by hand. The `__Host-` prefix (Secure, `Path=/`,
  no Domain, which the code already set) makes a browser refuse the cookie from a parent or sibling domain, so another
  app cannot plant or shadow it; plain-HTTP `--dev` keeps `journal_session` (a browser drops a Secure cookie over
  http). `Cookie` is split on `;` and the first cookie of the name wins, instead of `http.cookies.SimpleCookie`, which
  drops the whole header at one malformed or JSON-valued cookie from another app. `journal serve token --rotate` also
  empties `serve-pairings.json`: a code printed before a rotation (say, one suspected of leaking) cannot pair a
  browser under the new key. API.md's cookie description is unchanged in substance.
- **WP9 — a reply closes the connection when the request's body was not read** (a refused request, a GET with a body,
  any `Transfer-Encoding`): the bytes would otherwise be parsed as the next request on a kept-alive connection. `HEAD
  /api/v1/events` answers the stream's headers and gives the slot back; `version.code_id()` is computed when the
  server primes, so `/healthz` names the code the process loaded, not the disk after an upgrade.
- **WP9 — the order of checks.** `Host` (= `serve.host` when set; `/healthz` exempt so the proxy can probe the bind
  address, DEPLOY-FRONTEND.md step 2), then `Origin`/`Sec-Fetch-Site` on every non-GET **including `POST
  /api/v1/pair`** (API.md marks pairing auth "–"; a cross-site pairing post would be login CSRF), then the token on
  every `/api` path but health and pairing **before routing** (an unknown `/api` path is 401, not 404), then the
  route (404 / 405). CSP adds `frame-ancestors 'none'; base-uri 'none'` to API.md's string. `X-Forwarded-Proto`/`-For`
  only from `serve.trusted_proxies`; the proto decides the origin a write must carry when no `serve.host` is set.
- **WP9 — the fence is serve's own imports; the reach is fenced by behaviour.** `test_serve_fence` reads every
  `serve/*.py` with `ast`: none imports `conversations`, `sweep`, `recovery`, `privacy`, `mirror`, `context`, `ports`,
  `notebook` or `sqlite3`. The engine functions serve calls do reach modules that import those (`today.collect` uses
  `context.prompt_state`, a pure ledger function; `status` imports `notebook`), so `test_serve_privacy` walks every
  GET route (and pairing, logout, `/`) with `sqlite3.connect` replaced by a function that records and raises, and
  asserts it was never called; a GET route added to `api.ROUTES` without a sample fails it. `status.status(web=True)`
  and `status.doctor(web=True)` leave out the privacy pass (`conversations.off_now`), `conversations.json`, the
  mirror queue, the notebook count and the hook's records (API.md Decision 2 lists all of them as unservable); a
  test asserts none of those is called for `/status` or `/doctor`.
- **WP9 — the document.** Blocks follow the code, which is the contract: a `↩ replying to` callout sits above *his*
  message, so `reply_to` is on his block (API.md's example had it on hers; amended); her block is exactly the callout
  after a `%% journal her:<id> %%` line (what `entries.strip_callouts` removes from his words), so search and the
  document agree on whose words are whose. Words blocks are keyed `words:<n>`, conversation blocks by their mark
  (`his:<id>`/`her:<id>`), anything unmarked `conv:<n>`. `phase` comes from the `%% journal phase:N %%` line above a
  words heading. `prompt` comes from the ledger record whose `entry_path` (else `stem`) is the entry, so after the
  ledger prunes it the opener and `asked` are null while `questions` (the library's bank) remain. `analysis.reflection`
  is the day reflection whose `_From [[…]]` line names the entry. EntrySummary's `practice` is the id (null for
  `j`/`note`), `time` the first words block's `HH:MM`, and the `/entries` cursor an offset (`next`).
- **WP9 — media.** `/api/v1/media/<name>` serves only a plain name (`^[\w][\w.-]*$`), with an audio type, directly in
  the journal root, embedded (`![[name]]`) by an engine entry of the same date (kept audio is named after its entry).
  `media_dir` (ARCHITECTURE.md §3) is not added: the root is the only place audio is kept today.
- **WP9 — programs stub.** WP4's `programs` module is not on `main`: `/runs` is `[]`, `/runs/<id>` and its insights 404,
  `insights/weeks` has `program_progress: {}`, `/events` already watches `state_dir/runs/`. *Runs closed by the WP10
  follow-up*; `program_progress` stays `{}` (the Insights view is WP11's). *Closed by WP11* (review): see "WP11 — program progress".
- **WP9 — doctor's serve rows run in every `journal doctor`** (`serve token`, `serve bind`, `serve assets`, `same
  version as tick`), none of them failing an install that never runs serve: a token not yet generated is ok, the
  bind row only checks that the address exists here, and a missing web app build is ok until WP11 packages one
  (only a configured `serve.assets_dir` without `index.html` warns). Until then `/` serves a placeholder page
  (`serve/fallback/`) that can pair a browser and nothing else: no service worker, no manifest (the 2026-10-19 scope
  change: a web app, not a PWA).
- **WP9 — the unit.** `journal serve --print-unit` renders from the loaded config: the `journal` executable, `--config`,
  `--hermes-home`, `ReadWritePaths` = root, state_dir, practices_dir (one inside another dropped),
  `RequiresMountsFor`/`ExecStartPre=mountpoint` for any of them on a separate mount, and Decision 7's
  `MemoryHigh=384M`/`MemoryMax=768M` (DEPLOY-FRONTEND.md's draft had 256M/512M; the decision wins, and that page is
  amended). No `After=tailscaled.service`: code is generic, and a user unit cannot order itself after a system
  unit anyway (DEPLOY-FRONTEND.md §5 had it unverified); the 60-second bind retry plus `Restart=on-failure` cover an
  address that is late. `deploy/journal-serve.service.example` is the overlay's rendering with example paths
  (`test_serve.UnitTest`).
- **WP9 — `serve.allow_write`** exists in the config with its default; WP9 has no route that writes his journal, so it
  is first enforced by WP10.
- **WP4 — program commands: `/next`, `/skip`, `/back`, `/break` (pause), `/program` (status); resume is `/start <id>`.**
  Hermes owns `/pause` (its emergency stop), `/status` and `/resume` (and `/start`), so the journal never registers or
  routes them: routing only `/pause <run>` would still leave the keyboard's bare `/pause` stopping Hermes. `next`,
  `skip`, `back`, `break`, `program` are not in Hermes' registry (checked against `hermes_cli/commands.py` and the
  dated `config.HERMES_COMMANDS`), so they are plain plugin commands and the reply keyboard's text buttons reach them
  as typed text. They join `config.RESERVED_COMMANDS` and `library` reserved ids (with `insights`). In a gated chat a
  non-admin needs all five in `user_allowed_commands` (DEPLOY.md, WP4 step 1b). Each spawns `journal program <action>
  <run> --notify` (it waits for the tick's lock; an error is sent to the chat); `/program` answers at once (a read).
  `/start <program>` (ids and aliases) starts or resumes; `/start fix-life 3` jumps; `/start fix-life insights`
  compiles. `/start nope` now lists the programs after the practices (an existing assertion changed on purpose), and
  the two registered-command lists gain the five names.
- **WP4 — the open phase takes every message; a phase the run has left takes only direct replies.** PRACTICES.md §4.1
  says `capture.match` is untouched; §4.3 needs a correction inside the settle (and every reply under `advance: next`)
  to join the open phase, which "first message while visible" cannot do. So `capture.open_phase`: a phase record with
  `phase_open` (set when sent, cleared with `advanced_at` when the run moves) matches every message while visible; the
  sweep asks the same at the message's own time. A phase that was answered or skipped no longer takes the next
  message by rule 2 (only a direct reply, which lands under that phase's key). Practices are unchanged.
- **WP4 — the advance key and the visit.** `<run>:<session>:<phase>:<newest capture id>` for a settle,
  `…:cmd<visit>` for a command, where `visit` counts arrivals at the phase, so `/back` then `/skip` is a new key while
  a stale settle spawn from before the `/back` is still the used key. A transition writes the run (`send_due`) before
  sending; the tick's `programs` step sends a phase owed a send. `/next` between sessions begins the next one; `/next`
  on a paused run says it is on a break; `/back` on phase 1 is refused (no crossing into the previous session).
  `/start <program>` on a run whose open phase was answered during a break moves on from that answer instead of
  re-sending the phase.
- **WP4 — review fixes: an open phase is the newest prompt, a break closes it, a command knows its phase, a send
  never closes a private window.** (1) The open phase takes every message only while no later prompt of any kind has
  been sent in the chat (`capture.superseded`; the sweep asks it at the message's own time): a ritual sent after it is
  answered as on `main`, so DEPLOY's "capture of practice replies does not change" holds; the phase then takes direct
  replies only until the ritual is answered or folded away. (2) `/break` closes the open phase in the ledger (`pause`
  folds it): during a break only a direct reply is captured, `/start` carries on from one or re-sends the phase, and the
  reply note stops promising the next phase. (3) `/next`, `/skip`, `/back` carry `--expect session:phase:visit`
  (read by the plugin when the command arrives); a command whose phase the run has since left logs "already moved on"
  and does nothing (a settle, a second tap, the tick's lock held through model calls). `settle` already did this.
  (4) The settle waits while a bare `/x` window is open (or the privacy pass cannot run): a phase send ends a window
  (`privacy.py`), so sending automatically while he talks privately would capture his next private message. The tick's
  `step` retries; after the window's first `quiet_minutes` of silence the phase goes out. His own `/next` still sends at
  once and, as every prompt does, ends the window. (5) Hermes runs plugin commands only on its idle path; mid-reply they
  become text in her turn. The dispatch hook (which runs first) therefore runs `/next` `/skip` `/back` `/break`
  `/program` itself in the journal's chat, answers in the chat when there is something to say, and drops the event;
  the registered commands stay for any path the hook misses. (6) A phase message folds to a header line the sweep now
  knows (`sweep.PHASE_STATUSES`), so a reply quoting a folded phase lands under that phase, not the open one. (7) A run
  completes when every session is completed: finishing a session reached by a jump points `current` at the next session
  not yet done (the first one before it after the last), and the session he jumped away from is marked `left` (it
  restarts from its first phase, in the same entry). (8) A bad annotation answer is one attempt for a session's entry
  (all its phase prompts count it), and one model call a tick. (9) Re-sending a split phase keeps every earlier message
  id in `previous_message_ids`.
- **WP4 — cross-review fixes.** A run's activity is its last transition or his newest answer to one of its phases
  (`programs.last_activity`: `current_run` and a situational run's `expires_hours` use it, since a capture changes only
  the ledger); a failed preliminary chunk of a split phase counts as a failed send (`tick.count_send_failure`: alert,
  then wait) and a chunk already sent is kept for the retry (`chunk_ids`); a retried session summary says the same as the
  first (`next_line` stored on the session, the document recomputed); a malformed `annotate:` rejects a situational
  file; a prompt sent again keeps its earlier send times (`earlier_sent_at`) so the sweep still files an answer given
  before the re-send; the last phase marks `completion_due` and the tick's `step` finishes a completion that died;
  the session's entry follows where capture put the first answer (`programs.entry_stem`, `capture.entry_stem` for the
  later phases), so a file of his that appeared at the planned name is never written to; `/skip` and `/back` refuse a
  paused run (a command queued behind the tick lock could un-pause it or mark the phase skipped).
- **WP4 — cross-review fixes, second round.** An audio answer not yet transcribed holds the settle; a completed run's
  unsent summary is retried by the tick and the attempt that uses the last try alerts; the retry recomputes the summary
  and the document (the entry's `## Summary` holds both); a re-compiled insights document that he has written in is left
  as it is and a new numbered one is made (`insights_sha` on the run); `/start` on an *active* run sends the phase again
  (only a run that was paused carries on from an answer given on the break, and not under `advance: next`); a phase
  prompt keeps the spans in which it was the open phase (`open_spans`, read by the sweep at a message's own time), so a
  break and a re-send are told apart; a jump closes the old phase at once. *Not changed, on purpose:* a phase he
  answered and then `/skip`ped keeps his answer in the summary (the words are his, and `/skip` after an answer means
  "go on"); prompts of a completed run age out of the ledger at `KEEP_DAYS` like every prompt (the entries stay).
- **WP4 — cross-review fixes, third round.** A settle ignores the answers given before a `/back` reopened the phase
  (`reopened_at`; the earlier advance was by command, so its key did not cover them); a completed session whose
  message was never attempted is sent by the tick (zero attempts counts); an untranscribed audio answer holds the
  settle only when transcription is configured.
- **WP4 — cross-review fixes, fourth round.** An insights file with no noted hash (the engine died after writing it)
  is treated as possibly edited: a new numbered one is made; run ids carry 24 bits and are checked against every run; a
  long quoted block cut to Telegram's limit has its tags closed; a program id that reads as entry *n* of a practice
  (`morning-2`) is refused, as a practice id would be; the engine's note about an untranscribed recording is not put in
  a prose slot. *Not changed:* `_(audio file — not transcribed)_` still stands as the phase's answer in the entry and
  the summary (an answer exists that could not be read; calling it skipped would say he did not answer).
- **WP4 — the program reply note keeps the journal reply frame.** §4.5's `[Program reply — …]` would not be recognised
  by the readers that strip or skip the hook's note (`context.clean_user_text`, `sweep._HANDLED`, the conversation
  writer's `verbatim to <path>`), so the note is `[Journal reply — program reply: … saved verbatim to <path>. … and
  annotations are written automatically.]` with §4.5's content (which program, session, phase; the engine moves on
  itself; don't ask the next phase's questions).
- **WP4 — the session entry.** `YYYY-MM-DD-<program>-sNN.md` (situational `YYYY-MM-DD-<id>.md`; the next free `-N`
  when another run has the name), created at the first answer like every entry (the journal only holds answered
  entries), so a phase is folded under `## Prompt` when it is first answered, not when sent (§4.1 says as sent); a
  re-sent phase is not folded twice, and a reflect phase sent with its template says `_reflect: skipped (no model)_`
  there. The program's intro is on the first message of a new run but not in the entry. `## Summary` carries a hidden
  `%% journal:summary %%` mark (as `## Conversation` does) and ends his words and the conversation, so a late direct
  reply still lands above it; the copy of his words in it has `#`/`##` headings demoted (his blocks are untouched).
- **WP4 — summary and document slots.** `{N.M}` is his whole answer to that question's phase (§4.7), but a phase is
  quoted once per template: a later slot of the same phase says `_(in the answer above)_` (S1's 1.1 and 1.2 would
  otherwise repeat the whole phase-1 answer); `_(skipped)_` for a skipped, off-the-record or unanswered phase. A prose
  slot (`slot.<name>: 1.1`, or `s1:5.1` across sessions) takes his answer only when it is one line of at most 140
  characters, else the slot's name in italics: dropping a five-question answer into "in the area of …" reads as
  nonsense. `{user}` is his name. The ported summaries keep §4.9's slots; the original "insights for insights.md"
  lists (which needed a model) and button lines are gone.
- **WP4 — reflect and bridges are two settings.** §4.4 gates the bridge with `programs.reflect`; here
  `programs.reflect` (default on) asks the model for a reflect phase's construction and `programs.bridges` (default
  off: one more model call per question phase) adds the bridge. Both validate (`check_reflect_phase`: ≤ 900 characters, not
  failure text, every quote in his answers this session; `check_bridge`: one paragraph ≤ 400, no question, quotes
  from his last answer) and fall back without waiting; the construction stands in for the phase's Task/Analysis
  blocks (after the intro when it has none, S6 P3). `programs.keyboard: false` drops the reply keyboard (the
  documented fallback) without a code change.
- **WP4 — what the port changed.** Front matter `role` is a one-line stance per session (Decision 12), not the
  original persona biography; `mission` and the first quote moved as written; the rest of each Mission section (the
  7 steps, the 3 phases, the 9 stages, a second quote) is the session preamble, sent with phase 1, since those phases
  refer to it. `Transition` lines lost their `After answers: "…"` wrapper; letter ids (`**4A.1**`) are reference text;
  S10's table is a list, 5.2 is dropped and 5.3 is the closing line (under `Completion`). The originals are kept in
  `tests/fixtures/fix-life-en` for the content test.
- **WP4 — annotations once per session; the day reflection does not wait for one.** Phase records are annotated only
  once the session completes (every phase record of it then carries `session_complete`; the first answered one is
  annotated, and the others find the annotations already written), and `reflect._in_progress` ignores an
  unfinished session, which may run for days. A session's entry is read by the day reflection like any entry.
- **WP4 — a long phase is split on its boundary.** Over 4,096 characters the phase goes out as two messages (up to the
  questions, then the questions and the hint); the last is the reply target and the first is in
  `previous_message_ids`. No fix-life phase is that long today.
- **WP10 — `serve.allow_write` is enforced, and fails closed.** A route that changes the journal is registered as
  `api.WRITE`; any value of `serve.allow_write` other than the boolean `true` (`false`, `"no"`, `"yes"`, `0`) answers
  `405 {"error": "read-only"}` after the token check and before the body is read, and doctor names a value that is not a
  boolean. Pairing and `POST /logout` are not journal writes and stay open (a read-only server can still be paired).
- **WP10 — a web write is the engine's write.** `capture.capture_web` and `capture.add_web_block` call `record` /
  `entries.record_reply` under `ledger.locked`, the same lock and function the hook and the sweep use. A reply to a
  practice's open prompt (the newest prompt of that id that is sent, unfolded, unanswered and inside its window: the
  rule `capture.match` uses, now `capture.open_prompts`) is rule `web` and sets `needs_edit`, so the tick folds the
  Telegram message to "answered ✓". With no `practice`, or a `practice` with no open prompt, the words become a `/j`
  note of their own (API.md's rule, kept as written; the response's `rule` says which happened, so a client can tell
  him it became a note). Captures carry `source: "web"` (`"audio"` for a recording) and `via: "web"`; the entry's
  heading says `text`, as for every typed reply. An optional `client_id` makes a retry harmless (it is stored as the
  capture's own `client_id`, never as `platform_message_id`, which is a Telegram message id the tick threads its
  annotation reply under; a repeat returns `200` and the first result; a block on an entry the ledger no longer
  knows is remembered for three days in the ledger's `web_blocks`). `at` stamps the heading and the capture; it
  must carry an offset and be at most five minutes ahead. The open prompt is chosen by the server's clock, and only
  one sent at or before `at` is answered: a note backdated before a prompt was sent is a `/j` note of its own.
- **WP10 — `/x` and the engine's marker on the web.** The web never reads the chat, so the privacy pass has nothing to
  decide for a web write; what holds is the command itself: text that starts with the configured opt-out command
  (`capture.opt_out`, so `conversations.opt_out_prefix` counts) is refused `422 off-the-record`, nothing is written,
  and what he typed stays in his browser. A line shaped like the engine's own marker (`%% journal … %%`) anywhere in
  his text is refused `422 reserved-line`: it could pass for her voice, or for a program's `phase:N` key. Both are
  refusals, never edits of his text. An upload named in a refused request stays unclaimed.
- **WP10 — locks are bounded, not changed.** `ledger.deadline(seconds)` sets a per-thread absolute deadline that
  `locked` and `locked_state` honour (poll with `LOCK_NB`, then `ledger.LockBusy`); every write handler runs inside
  `deadline(5)` and a `LockBusy` is `423`. The hook and the tick set no deadline and wait as before. A request takes
  the ledger lock, or `library.held` (the practices directory's `locked_state`), never both, so the tick → ledger →
  state order is not at stake; `tests/test_locks.py` runs a hook capture, a tick round (fold, prune, state and
  library locks) and web writes together and checks nothing is lost or doubled.
- **WP10 — blocks.** `POST /entries/{stem}/blocks` goes through the ledger prompt whose entry it is (so the tick sees
  the reply), else straight into the file (an entry the ledger has pruned, or one the companion started): there is
  then no capture for the tick, so an audio file added that way is kept but marked not transcribed. Nothing above the
  new block moves; `entries.record_reply` strips only the blank lines around his words section. There is no route that
  edits or removes a block (PUT/PATCH/DELETE on an entry or a block are 405/404 and a test walks them). A block on a
  program session entry (`program:` in its front matter), or any `phase`, is `409 phase-not-open` until WP4 is
  installed: no run says which phase is open, and guessing would write a phase answer into the wrong place.
- **WP10 — meta.** `PATCH …/meta` accepts `mood`, `people` (lists of up to 60 characters, no commas or brackets;
  written `mood: [a, b]`) and `starred` (written `true`); an empty list or `false` removes the key. `mtime` is
  optional here (the merge is key by key); with it, drift is `409`. Any other key is `400`, immutable ones included.
  `entries.set_meta` rewrites the front-matter block only and carries the rest over byte for byte, under the ledger lock.
- **WP10 — the library.** `PUT /library/{id}` takes `{raw}` or `{definition}` (the shape `GET` gives) plus the
  `mtime` it was read at, parses strictly (`library.parse`; a definition goes through `library.from_dict`, is rendered
  and must read back as written), checks the id (`library.check`: reserved, slot clash; the URL's id must be the
  file's) and saves the **canonical** text (`library.render`), so a line of prose between questions, which the parser
  ignores, is not kept; `?validate=1` shows the text that would be saved, with the problems, and writes nothing
  (`200 {valid, problems, raw}`). 422 lists problems and writes nothing; a built-in is `409 builtin` until forked
  (*superseded by BUILTIN-EDIT: a `PUT` of a built-in is his edit of it, in place*); an
  existing user file needs its `mtime` (`400` without, `409` on drift: the file Obsidian changed is never overwritten).
  **A PUT to an id nobody has creates a user practice** (API.md listed 404 for PUT; FRONTEND.md's "New practice" needs
  a way to create, and this is the one route that already has the validation). The file is found by id, whatever its
  name, else `<id>.md` (`409` if a file of that name exists but does not parse: delete it first). `DELETE` removes
  that file (a fork reverts, a practice of his own is gone), `409 builtin` when there is none to remove, and `409
  active-run` when `state_dir/runs/<id>/*.json` holds a run that is `active` or `paused` (none until WP4). Writes take
  `library.held(practices_dir)`.
- **WP10 — settings.** The allow-list is `config.WEB_WRITABLE`, a tree: `location`, `timezone`, `coordinates`,
  `windows.{collapse_hours, grace_hours, opener_budget_seconds, annotate_after_minutes}`,
  `reflections.{day,week}.{enabled, fallback_time}`, `conversations.quiet_minutes`, `programs.settle_minutes` (the key
  WP4 reads; accepted here so the web app need not wait for it), `today.{enabled, max_chars, cache_minutes, sources}`.
  Dotted keys are the nested form. Anything else is `400 key-not-allowed` naming it; a bad value is `422`; the
  check runs before anything is written. `config.patch_values` merges a nested change into the value the owning layer
  holds and writes the top-level key through `write_values`. A key whose layer is Hermes settings is `409` with the
  `hermes config set plugins.entries.journal.settings.<key>` line; so is a key from the environment; so is any key when
  there is no config file and the install is under Hermes (a file made at the standalone path is one nothing reads).
  `location` is looked up first (`location.set_location`, which writes location, timezone and coordinates together and
  logs the change; `timezone` beside it overrides the lookup's), after the ownership of all three is checked, so a
  Hermes-owned timezone stops it before the geocoder is called. Sending `coordinates` with `location` is `400`. The
  answer is `{written: {key: layer}}` with the keys the client sent.
- **WP10 — uploads.** `POST /uploads` (multipart, one audio part, at most 25 MB; the 1 MB JSON limit does not apply
  to this route) is parsed by a small splitter, not `cgi` (gone in 3.13) or `email` (three copies of the body in
  memory). It is kept in `state_dir/uploads/<id>.bin` (0600) with a record, claimed by an entry or a block
  (`audio.keep`, as a Telegram audio file; `transcript: "pending"` when a backend exists), single use, and deleted
  after an hour (swept when the next upload arrives; an unclaimed one is not served by any route).
- **WP10 — sends and the sandbox.** `POST /library/{id}/send` spawns `python -m journal [--config …] [--hermes-home …]
  send <id>` detached (stdin closed, output appended to `manual-trigger.log`), as `/start` does from the chat. The child
  is in the serve unit's cgroup and sandbox: it counts against `MemoryMax=768M` and `TasksMax=64`, cannot write under
  the home directory except `ReadWritePaths`, and mirrors into the chat session only if Hermes is importable there
  (otherwise `mirror` queues it for the next tick). Accepted for v1 (Decision 7 says the figures are to be measured);
  the first checks in DEPLOY.md watch for it. The unit is not widened for the config file: `PATCH /settings` writes the
  config file and answers `503` if its directory is not in `ReadWritePaths`; a drop-in is the operator's choice. *Superseded 2026-10-26 by SERVE-UNSANDBOXED: the unit has no sandbox, so the child is limited by the cgroup
  (`MemoryMax`, `TasksMax`) and file permissions only, and the `503` means the config directory is not writable by
  the service user.*
- **WP10 — programs are stubs.** WP4's `programs` module was not on `main` when this was written (it is now; the wiring is still raised under WP4). `POST /runs` and
  `POST /runs/{id}/{next|skip|back|pause|resume|abandon|insights}` answer `404` (after a `400` for a missing `program`),
  the same answer the read routes give, and spawn nothing; `api.WRITE` routes for them are registered so the table
  is complete. WORK-PACKAGES.md's test "a web block on a program phase advances by the program's rule (spawn stubbed)"
  cannot run until WP4 lands; whoever merges the later of WP4 and WP10 wires `writes.block_add` (open phase check,
  then `_spawned(req, ["program", "advance", run])`), `run_start` and `run_action`
  (`_spawned(req, ["program", action, run])`) and the `409` for a wrong state, and adds that test. *Done by the WP10
  follow-up* (next entries).
- **WP10 — the web writes only to files the engine wrote.** A block or a meta change needs the file's front matter to
  carry the engine's `type: journal` and to claim an id the name can mean (`document.engine_file`, the test
  `capture.entry_stem` applies for Telegram). A hand-written `2026-10-13-evening.md` is readable but answers `409
  not-engine-entry` to a write. Line endings in his text are normalised to `\n` before the opt-out and marker checks
  and before writing (a bare `\r` would otherwise slip past the pattern and become a line when the file is read back);
  an upload's name loses control and line-separator characters; a front-matter value that `splitlines()` would split
  is refused.
- **WP10 — settings have ranges, not just signs.** `windows.collapse_hours` 0.25–72, `grace_hours` 0.25–24 (under one
  15-minute tick a scheduled slot can be missed; doctor now says so for a config file that sets it), `opener_budget_seconds`
  1–900, `annotate_after_minutes` 0–10080 (whole minutes; the loader would truncate a fraction silently), `quiet_minutes`, `settle_minutes` and `cache_minutes` up to 1440,
  `today.max_chars` 200–20000. `today: false` in a layer reads as `{enabled: false}` before a web change merges into it.
- **WP10 — a spawned send carries `HERMES_HOME`** (the profile home) as the plugin's own spawn does; without it
  `mirror.to_chat` refuses and nothing queues. Whether the opener's model call works inside the unit's read-only
  home is unmeasured: DEPLOY.md check 5 reads the send's `opener_source` from the ledger.
- **WP11 — a web app, not a PWA (decision 10; superseded 2026-10-20 by decision 11 and the PWA package below).** No `vite-plugin-pwa`, no service worker, no manifest, no offline
  ribbons; FRONTEND.md §4 is now "connection behaviour" (online only: a failed read says so with Retry and never
  shows an older answer). The static handler still maps `.webmanifest`; nothing in the build uses it.
- **WP11 — the build and its guard.** `web/` (Preact 10, preact-iso, TypeScript, Vite 6, vitest 3; hand CSS, no
  component library) builds into `src/journal/serve/static` with `base: "/"`. `.github/workflows/web.yml` (Node 22):
  `npm ci`, type check, vitest, build, upload the build as the `static` artifact (on every run), fail when
  `git status` shows `src/journal/serve/static` changed; a second job runs the Playwright smoke (`web/e2e`,
  desktop and phone viewports) against `tools/serve_fixture.py`, the real server over the Python suite's neutral
  journal with a fixed clock. The committed copy is the artifact of a CI run, not a local build. `static/build.json`
  holds `{"source": <sha256 of the build's inputs>}` (no timestamp, so a rebuild is byte-identical);
  `serve.static.web_source_hash` recomputes it in Python, `tests/test_web_build.py` fails a `web/` change that did not
  rebuild `static/` (Node never runs on the host), and doctor's `serve assets` row names the build. Inputs: `index.html`,
  `package.json`, `package-lock.json`, `tsconfig.json`, `vite.config.ts`, `src/`, `scripts/`, `public/` (not tests or
  e2e, so a test edit never needs a rebuild).
- **WP11 — `/today` carries `week`, added by the route, not by `today.collect`.** API.md said the week strip's field
  would be added to `collect`; that would change the dict the thread-start block renders and the pinned
  `tests/fixtures/today.json`. The route returns `{**collect, "week": [...]}` (seven local days, oldest first; each
  `{date, weekday, answered, missed, state}` with state `answered | partial | missed | none`) and
  `test_serve` asserts the rest equals `collect` exactly. `insights/weeks` and the strip share one rule
  (`_prompt_outcome`: a capture is answered, a closed window with none is missed, an open prompt is neither). Program
  phase prompts (`program` set) are in neither: `/skip` is recorded on the run, not the ledger, so a skipped or
  passed-over phase would read as missed, and Today's rows leave them out too (found in review once WP4 was on `main`).
- **WP11 — two more additive API fields.** Entry documents carry `voices: {his, hers}` (`names.user` or "You";
  `names.companion`), because the callout must be labelled with her name and the blocks do not carry it;
  `EntrySummary.flags.audio` and `?audio=1` on `/entries` and `/days` serve the "has audio" filter chip (FRONTEND §1.2
  asks for the indicator and the filter; the summary had neither).
- **WP11 — Library lists programs (found by the Codex review).** `GET /library` appends the programs and situational
  definitions (`kind` `program|situational`, `sessions`, `phases_total`, `active_run` from `programs.active_runs`,
  `schedule: []`); `GET /library/{id}` answers a program with its sessions' questions as `sections`, the program file as
  `raw` and `sessions: [{number, id, title, raw, phases}]` (API.md). Read only. Without it Frameworks and the program
  filter chips were empty. The client capitalises a file's `group` (`frameworks`).
- **WP11 — the week strip's mixed day.** A day with an answered prompt and one still waiting is `partial` (it was
  `answered`, which told the reader every prompt was answered); `_prompt_outcome` now says `open` for a prompt inside
  its window. A day with only open prompts is still `none`. Titles lose only the engine's date suffix (`Mind — Body`
  keeps both halves); Today and Library also listen to `runs-changed`; a pairing code that failed on a 5xx or a network
  error can be retried from memory (it is out of the address at once); the second `/j` note or slot of a day takes its
  base kind's label.
- **WP11 — Codex round 3.** A program session entry's `prompt.questions` come from the program's own session (sections
  `Phase n: Title`; `practice` names the program's title), so the Entry view shows what was asked; `GET /library`
  serializes `library.with_slots` (the `schedule.<id>` override that the tick uses); a row's `stem` is the prompt's
  `entry_path` stem, then `stem`, then `id` (a collision renames the entry); the stream reports `library-changed` for a
  program's folder (`<id>/program.md`, `<id>/sessions/*.md`); Entry also follows `ledger-changed` and `tick` (a day
  reflection is not an `entries-changed`); Entries follows `library-changed`. Known and left: an audio-only entry still
  waiting for its transcript has no first line, so it is not in `today.entries` and its row has no Open link yet.
- **WP11 — Codex round 4.** `useResource` keeps the answer with the inputs it was read for, so a changed filter or id
  never shows the old answer for the render before the new read starts; Settings follows `tick`, Entry also
  `library-changed`, each Insights week the ledger and tick. Not taken: Entries keeps its filter chips out of a failed
  `/library` read (an empty library, no error line) on purpose: the list is the page, the chips a nicety, as on Today.
- **WP11 — Codex round 5.** A program session entry's `prompt.phases` (`[{number, title}]`, from the program's session)
  lets the phase dividers say `Phase 2 · The 5-Year Tuesday`; Insights also follows `tick` (a prompt passes its window
  with no file changing). Codex kept finding "subscribe view X to event Y" after each round; the rule now is: a view
  follows every event whose data it reads (`entries`, `ledger`, `runs`, `library`, `tick`). A new view declares it.
- **WP11 — known, not taken.** Editing the deprecated `schedule.<id>` override in the config while the Library is open
  does not push a refresh (the stream fingerprints definition files, not the config); a reload shows it. The override
  is deprecated in favour of the practice files, which do report. *Closed by WP13:* the override is gone.
- **WP11 — `today.collect(stems=True)`.** The Today route asks for the entry stem on each practice row and on the
  reflection (additive; the block and its goldens never pass it): a practice with two slots has two rows with one id, so
  linking by an id prefix gave both the first slot's entry, and a reflection written as `<date>-reflection-2` had no
  link. The audio flag (`flags.audio`, `/entries?audio=1`) counts audio embeds only (`![[x.ogg]]`), not `![[x.png]]`.
- **WP11 — `tests/fixtures/api/*.json` pin the answers the web tests read** (eleven GETs over the neutral temporary
  journal, paths and mtimes normalised; `status` and `doctor` are host-specific and are not pinned: the web tests use
  small inline objects). `tests/test_web_fixtures.py` compares the live answers with the files and regenerates them
  with `JOURNAL_UPDATE_FIXTURES=1`.
- **WP11 — the client's boundary.** `schema.ts` parsers keep known fields and drop the rest (decision 7 of
  FRONTEND.md); a wrong type is an error naming the field, shown as "unexpected shape". The settings tree is kept as
  JSON and shown generically (the server already redacts secrets to `<set>`). Entry text is rendered as text
  (`white-space: pre-wrap`), never as HTML; the companion's Markdown goes through a ~100-line renderer (paragraphs,
  bullets, bold, italic) that builds elements from data, never an HTML string.
- **WP11 — pairing fallback is a pasted pairing code, not a pasted bearer token.** FRONTEND.md decision 6 kept
  "Bearer paste" as a fallback; the cookie is `HttpOnly` precisely so page script never holds the token, and storing a
  pasted bearer token in `localStorage` would undo that. The pairing page takes the code (or the whole link) in a
  field and posts it to the same `POST /pair`. The code is removed from the address as soon as it is read.
- **WP11 — what Today, Entries and Library do not do yet** (they read; WP12 writes): no Reply / Send now / Start /
  Pause / Continue buttons, no "+" sheet, no composer, no meta sheet, no fork/edit/reset, no Run screen (`GET /runs` and the transitions are served since the WP10 follow-up, but no screen uses them yet). Differences from the sketch that are choices: a day in the
  week strip links to `/entries?day=YYYY-MM-DD` (the list filtered to that day, with "Show all") instead of
  scrolling a long list to it; the list pages by cursor with a "Load more" button instead of infinite scroll;
  Settings is a gear in the header; the Library groups by `group` when a definition names one, else Frameworks
  (programs), Daily (scheduled) and Situational (on demand). *Closed by WP12* (below): the buttons, the composer, the meta sheet, the fork/edit/reset flows, the Run screen, the program cards and the "+" sheet exist.
- **WP11 — program progress, and the program reads left for WP12 (found by the review).** `GET /insights/weeks` fills
  `program_progress` (program id -> a line such as `"3 of 10 sessions done, 1 this week"`), from the run files: a
  program shows in a week where one of its runs began or finished a session, and in the current week while a run is
  going; the newest run begun by the week's end speaks; "done" counts sessions completed by the week's end. The
  Insights card already printed `program_progress` as `id: line`, so no web change was needed. Not built here, and
  deferred to WP12 on purpose rather than left out silently: on Today a card per active run and the "No program
  running; start one from the Library" empty state (FRONTEND §1.1; today shows the one `programs.today_line`), the
  quiet list of today's scheduled session-10 interrupts with their times (§1.1; `GET /runs` already carries
  `scheduled`), and the Library card's progress bar from the active run (§1.4). All three are built on `GET /runs`,
  and a run card without Continue / Pause and a Run screen to open is a label with nothing behind it, so they ride
  with WP12's run controls. Until then Library cards show the `active_run` badge and Today the one-line summary.
- **WP11 — Insights reads 26 weeks** (`/insights/weeks` with `from`/`to`) and steps one
  week at a time; each week reads its own reflections (`/reflections?from&to`), because one capped list of the
  newest 200 left the oldest weeks empty; `/insights#<reflection stem>` opens the week and the reflection it names (Today's "carry
  forward" and the Entry's Analysis tab link there).
- **WP11 — Codex round 6: four points fixed.** (1) `GET /library/{id}` of a program already returned `sessions`
  (every phase, questions or not); the client's parser dropped it, so a phase with no question was invisible. It is
  parsed now and the detail lists each session's phases, with their questions beneath. (2) A situational definition
  (`kind: "situational"`, one session) is program-backed: its card says "On demand · 1 session · N phases", not just
  "On demand"; the test is `labels.isProgramBacked`. The Library group still comes from the file's `group`. (3) A
  `last-tick.json` from before version stamps (`at`, no `code`) reads "ran before versions were stamped" with the
  doctor's own wording, not "has not run yet". (4) When the browser closes the event stream for good (a refused
  open), the client makes one authenticated read (`/status`) per attempt; a 401 there marks the browser unpaired, so a
  rotated token no longer leaves a page of private data retrying silently. The probe is the existing `get`, no new
  endpoint.
- **WP11 — review round (the last): five points fixed, one reading kept.** (1) A block under `%% journal
  interrupt:N %%` carries `interrupt: N` (and `phase: null`; the key is present only then, so the pinned fixtures
  are unchanged) and the entry view gives it an "Interrupt N" divider, then the next phase's divider again; this closes
  WP5's raised point. The label is the number only: the interrupt's question and offset are in the entry's `## Prompt`
  callout. (2) An audio embed is any `![[name]]` that `audio.media_type` calls audio, the test `/media` itself uses
  (`api.media_type` now delegates to it): `hooks.original_extension` keeps the sender's suffix, so a kept `.oga` or
  `.aif` is a recording and gets its player. (3) `prompt.phases` and the program questions come from the session
  number the document reports (the name's, else the front matter's), and a number outside the program (`0`, `-1`,
  too large) finds none; before, `session: 0` indexed from the end. (4) The web client keeps its one `/events` stream
  across view changes and closes it after 5 s with nobody listening (`IDLE_MS`): the server frees a closed stream's slot
  only at its next write (a ping, up to 25 s), so a close and reopen on every navigation filled `MAX_STREAMS=8` after
  about eight clicks and refused the open view's pushes and other devices. Left as it is: the server still cannot tell
  a vanished client before its next ping; shortening that is a server change with its own cost (a thread per stream).
  (5) API.md's `/today` row says what the route returns (`week`, the row and reflection `stem`) and the entry shape
  has `prompt.phases` and `interrupt`; the earlier "`/runs` is still `[]`" and "until wired" lines were stale after
  the WP10 follow-up and are corrected; DEPLOY's WP11 section says it deploys with or after window B and rolls back
  to the previously deployed sha, not the WP10 commit.

- **PRIV — coverage is proven by rows read, not by a stamp.** A saved pass (`conversations.json` `privacy.at`) says
  when a pass ran, not which rows it read, and WP9's `_unpersisted_from` trusted it as proof: with the sessions
  database missing or misconfigured for more than 48 hours, another chat id, the writer enabled after a first pass,
  or a clock that ran ahead and was corrected, a pass was saved over rows it never saw, and the writer then wrote a
  `/x` exchange from its cursor (seen by a read-only verifier after earlier PR 15). Rule: a view records where it began
  recomputing (`View.start`), and **a reader that holds rows older than that asks `privacy.covering(cfg, oldest)`**,
  which re-takes the view from its oldest row (and 48 hours before it); saved results are a floor (more private),
  never the proof that a row is public. Readers that use it: the conversation writer (its oldest settled row), the
  sweep (its lookback, which can be set above 48 hours), the outage catch-up (`max_age` plus a day: 72 hours by
  default), the pack (`telegram_history.days`), the quote verifier (every row it reads) and annotation alignment
  (the prompt's time). The hook's end-state checks (`window_open`, the tool guard's taint) look only at the last 48
  hours and keep `view`. Besides: `update` saves no pass while the configured sessions database is not a file;
  `view(since=…)` recomputes from 48 hours before `since` (a backfill keeps a window or tainted turn begun just
  before `--since`); the writer also checks a row's own marks (`row_is_off`), as the other readers do. A later
  reader of chat rows follows the same rule. Cost: a pack or verifier call recomputes over its whole horizon; the
  view cache is keyed by start so the two views `covering` may ask for do not evict each other.
- **PRIV — a compaction copy is private when its original is.** Compaction stores a session's tail again (same
  session, role, time and words, a new id); the pass skipped copies, so a copy of a private row never got a private
  id, and readers of raw rows (annotation alignment; the catch-up, which reads only active rows, so only the copies)
  showed it. `conversations.rows` now carries the original's row id in its `copy` field (None for an original);
  the pass marks a copy private exactly when its original is (also when the original is private by a saved result
  only), and saves the copy's id with the rest. A copy is still no activity of its own (it neither opens nor
  extends a window), and the writer still reads only the first.
- **PRIV (review) — the wider copy rule.** The first rule above found copies only in the same session at the same
  time. Hermes also stores the carried tail with the compaction's time (older Hermes, or its best-effort timestamp
  carry failing) and, when `compression.in_place` is false, in the child session of a rotation. A live
  `state.db` held such copies (read-only count), with a new time and no shared `message_uid`, and the rule missed
  all of them: the writer wrote steered `/x` words and her private reply, and
  the catch-up listed them. Now a copy is an earlier row of the chat (any of its sessions) with the same role,
  words and Telegram id, at or before the copy's time, with **words** (a tool step has none; the wider rule never takes empty rows
  for copies of each other, or the writer would lose its tool-step events; the older same-session, same-time rule is
  unchanged), and either his Telegram id is there (a
  repeat has another) or, for her rows and his without an id, the earlier row is archived (`active = 0`) or its
  session ended by compression. Two live replies with the same words stay two replies. Limit, taken on purpose:
  a reply she genuinely repeats after the first was archived (a rewind, a session reset) reads as a copy, so the
  writer skips it and the pass gives it its original's answer; to keep that from being a way into an `/x` turn, a
  copy with no Telegram id whose original is public is still private inside an open window or tainted turn (more
  private, never less). Rows are matched in Python over the chat's rows with words, not in SQL (a correlated
  subquery scanned the table per row: 4 s over a live install's whole history, 30 ms now). Not covered: a carried tail
  whose words were changed by compaction.
- **PRIV (review) — `covering` also re-takes at the edge.** A view begins at `now - 48 h`; a reader whose oldest
  row sat exactly there (the sweep's default lookback) was given the default view, so a bare `/x` begun just before
  it was not known when the passes that should have seen it had run on a clock that was ahead (they saved nothing
  for those rows). `covering` now re-takes unless the view already began `RECOMPUTE` before the oldest row: the
  writer and sweep each run one more pass over about 48 more hours per tick (cached with the other).
- **PRIV (review) — annotation alignment reads the configured chat only.** `context.companion_reply_after` for a
  prompt recorded in another chat (`chat_id` differing from `cfg.chat_id`) returns nothing: the pass computes the
  configured chat's rows, and her rows carry no mark of their own, so a reply in a tainted turn there read as
  public. This reverses the WP-era behaviour "a prompt recorded in another chat reads that chat" (the test now
  asserts None); the cost is no alignment text for such a prompt, which the annotation does without.
- **WP5 — what counts as a confirmation.** A reply that is, whole, `YYYY-MM-DD HH:MM` or `HH:MM` (PRACTICES.md §4.8's
  pattern with a two-digit hour; `6:30` is not read; the time alone means tomorrow, the day after the local date he sent
  it, not the day the settle ran). Anything else is kept as his words and costs nothing. Of the blocks he sent in the visit to the phase the
  newest one that parses wins, so a correction inside the settle replaces the first try; an answer from before a
  `/back` is not read again. A confirmation whose last interrupt is already over is refused like free text (the ask
  says the day is over). `/skip` and `/next` go on with nothing scheduled (and a one-line note for `/next`): they are
  his own act, so the settle's "ask again" does not apply to them. A `/next` after a `/back` to a phase already
  confirmed keeps the interrupts that were timed and says so; a `/skip` there cancels them (the phase says it goes
  on without them); one after a day that is over says it is over. After the single re-ask the settle stays silent;
  the phase waits for a confirmation, `/skip` or `/next` (programs do not expire; `journal program status` says
  `waiting for a day and wake time`). A break does not take the "answered while on a break" shortcut for this phase:
  `/start` sends it again and the settle reads the answer.
- **WP5 — the interrupt lines come from the phase, the ask line from the engine.** `programdefs.interrupts` reads the
  `- +2h: "question"` lines of a `type: schedule` phase (offsets in hours, fractions allowed); a schedule phase that
  lists none is a definition problem. The engine adds the closing ask (`programs.confirm_request`, with an example
  date computed from the send) to the phase's message, and question 3.1 stays as authored (the content test holds the
  original lines), so the phase reads "write your specific times for each interrupt" and then says what to send; the
  engine's line is the last word. Question text is filled with the session's prose slots at confirmation time.
- **WP5 — stored as wall-clock times, read in the current timezone.** `run.scheduled[]` holds `local` (`2026-11-02T13:00`,
  no zone, the authority) and `at` (the same time with the offset of the zone at confirmation, for display). Offsets
  are added to the stated clock time (wall-clock arithmetic), so across a DST change an interrupt is at its stated
  clock time, not so many elapsed hours after waking. `schedule.wall_clock` reads `local` in `cfg.tz` on the day, so
  a `journal_set_location` between the confirmation and the day moves nothing off his clock. A time the clocks skip is
  read with the offset from before the change (the hour after); one they repeat is the first. Compared as instants,
  never as same-zone wall times.
- **WP5 — the grace window, and only the newest of several due.** An interrupt is sent while `now` is in
  `[at, at + windows.grace_hours)` (default 2); after that it is recorded as `missed` and never sent late. When a tick
  finds several of a run due (it was down), only the newest is sent and the older ones are `missed` with
  `note: superseded`: three questions back to back are not the three questions. A send that fails is retried on the
  next ticks within the window and alerts after `SEND_ATTEMPTS` like any prompt (`failed`). Item states: `pending`,
  `sent`, `missed`, `failed`, `cancelled` (the run was abandoned). A paused run still sends them (a break stops
  advancing, it does not move a day he chose); an abandoned run does not. A due interrupt also waits while a bare `/x`
  holds the chat off the record (`_off_the_record`, as a settle does): a send ends that window, so his next private
  message would be filed; it stays `pending` and is sent when the window closes, or `missed` once the grace window is
  over. Session 10 is the last session, so by the protocol day the run is `completed`: `journal program abandon`
  then cancels the pending interrupts of the newest completed run that holds some (or the one named) and leaves it
  `completed`. A bare `abandon` (no run named) while an active run and such a completed run both exist refuses and
  names both (`journal program abandon <run>` ends the one meant): neither guess is undone by a revert.
- **WP5 — an interrupt is a ledger prompt that only a direct reply answers.** Record `<run>-s10-i3`: `kind`/`practice`/
  `program` the program, `run`, `session`, `interrupt: 3` (+ `interrupt_count`, `interrupt_offset`, `interrupt_at`,
  `interrupt_text`), `stem` the session's entry, `collapse_hours` 24, no `phase`. PRACTICES.md §4.8 says "a reply lands
  in the session-10 entry under the interrupt's label": taken as Telegram's reply gesture (and the sweep's quoted
  reply), not "the first message while visible" that practices use, because eight interrupts in a day would make a
  passing remark to the companion a journal entry; an answer typed without replying is ordinary chat: the
  conversation writer files it as it files any chat (nothing is lost, but it is not under the interrupt). An interrupt
  sent after an unanswered ritual takes that ritual out of "the first message after a prompt" (`capture.interrupted`,
  in `match` and `sweep.visible_at`; the web app's own write to a practice is unaffected), so the typed answer is not
  filed as the ritual's. The reply is written
  under `%% journal interrupt:3 %%` with the interrupt folded into `## Prompt` as `> [!question]- Interrupt 3/8 (+6h) ·
  Mon 2 Nov 13:00`. `entries.phase_answers` stops a phase's chunk at an interrupt key, so a phase's answer (and the
  summary slots built from it) never swallows an interrupt's; `entries.interrupt_answers` reads them back. A reply moves
  nothing in the run (`hooks._settle_later` skips it) and the companion's reply note says what it is
  (`programs.reply_note`). No per-interrupt annotation: the session is annotated once, as before.
- **WP5 — the insights document.** Per session (after the phase answers) a section of his answers to the interrupts:
  each question and his answer, or `_(no answer)_`, `_(not yet sent)_`, `_(not sent: missed)_`. The synthesis is
  `programs.synthesize`: only when the program file says `insights: compile+synthesis` or `programs.synthesis` is
  true (fix-life stays `compile`, so nothing new reaches the vault until the owner turns it on); a one-shot without tools
  given the compiled text; `validate.check_reflection("insights", …)` (new limit 5,000 characters, asked for 3,500;
  every quote must be in his own words of the run, the phase and interrupt answers, not the questions, titles
  and summary prose the engine wrote, nor the note about an untranscribed recording, nor an answer added after the document was compiled (it is not among
  the answers above the synthesis), and no quote may run from one answer into the next; a misquote rejects all of it); appended under `## <Companion>'s synthesis`
  with a line saying it is hers. The compiled part is never rewritten by a synthesis; a model outage defers it
  (`outage`: every 30 minutes, an alert once after 6 hours, given up after 7 days, no attempt used); a bad answer is one
  of 3 attempts, then the compiled document stands. A document he has written in since it was compiled (`insights_sha`)
  gets no synthesis, and the write re-checks it under the ledger lock after the model call (up to 240 s): a note he
  added meanwhile is kept and the synthesis is `skipped`. Compiling again with the same answers after a written synthesis changes nothing and asks nothing; while it is
  still pending (deferred, or with attempts used) the record stands, so `/start … insights` does not restart the
  30-minute wait, the 7-day age or the 3-attempt limit. A confirmation is refused as over only when its *latest*
  interrupt is, whatever order the phase lists them in; an interrupt question too long for one Telegram message is
  shortened on a word (the definition file has it whole). An interrupt line reads at most 999.999 hours (a longer
  number is not an interrupt of a day and would overflow a date), one-offs are ordered by instant, and a confirmed
  day at the end of the calendar is refused like free text. An interrupt that asks nothing (`- +2h: ""`) is not
  one; offsets are rounded to the minute they are stored in; "every interrupt is over" is judged by the latest by instant. A program or session title over 120 characters is a definition problem (every message
  header carries it, and Telegram refuses one over 4,096). A synthesis is checked against the blocks of his that the
  compiled document shows, so a later reply under the same phase or interrupt leaves the earlier ones quotable.
  A session may have only one schedule phase (the interrupt ids and a confirmation are the session's). The older
  interrupts of several due at once are saved as `missed` before the newest is sent, so a death in between cannot
  send them next tick. A reply the sweep files late (sent before a `/back`) is told from the new visit's by pairing
  blocks with captures before the `/back` filter. A manual `insights` leaves the outage alert and the give-up to the
  tick's next try (it has nowhere to raise them).
  An interrupt question over 1,500 characters is a definition problem; one that is still too long once its slots are
  filled is shortened on a word and the shortened text is what the ledger keeps (so a reply folds under what was
  actually sent). `/skip`'s cancellation is saved before its note goes out. Switching `programs.synthesis` off stops a
  synthesis that was waiting for the model (`skipped`).
- **WP5 follow-up — review points left open after the merge.** (1) `programs.step` isolates each run: a run whose
  share raises anything but `Alert` (an edited definition, a damaged file) is logged (`step failed`) and alerted by run
  id after the others, and the interrupts and the syntheses still go; `send_interrupts` is guarded the same way, so a
  failure there does not skip the syntheses. (2) `_visit_blocks` pairs his blocks with the ledger's captures only when
  there are as many of each; a block with no words yet (audio waiting for its transcript, an uncaptioned photo) breaks
  the one-for-one count, and counting from either end could give a block another's time (an answer from before a
  `/back` re-read, a bare time given the wrong day). Unequal: nothing is read, and the settle asks again until the
  transcript lands. The identity route (a capture id in the block) was left out: it would change his entry's markup.
  (3) The tick sends session 10's interrupts in their own step *before* the practices (`programs.send_due_interrupts`),
  so a ritual due in the same tick as an interrupt (a 09:00 wake puts +13h/+14h in the evening window) is sent last
  and stays the newest message: his typed evening entry is still filed as its answer. `programs.step` passes over the
  interrupts again later in the tick, for the ones a settle in that step has just timed (an interrupt near the end of
  its grace window would otherwise wait a tick and be missed); the two passes share a `tried` set of prompt ids, so a
  failed send is counted once a tick, not twice. An interrupt due later than the ritual still takes the ritual out of next-message, as decided
  above. The step reads the live privacy view, so running ahead of `privacy.update` costs nothing. (4) A bare
  `abandon` refuses between an active run and a completed one with interrupts to come (above).
- **WP5 second review — seven points, each pinned by a test (`tests/test_wp5_rev2.py`).** (1) `_visit_blocks` now
  counts a block with no words yet as a block (`entries.phase_blocks(keep_empty=True)`), so a pending voice note no
  longer hides a typed confirmation from `/next`; it still reads nothing when the counts differ (the entry and the
  ledger have drifted), which replaces the earlier "any empty block hides all" rule. (2) **Rejected:** the first review
  suggested that, when the privacy pass cannot run, the interrupts follow the hook's own fallback
  (`privacy.window_open`: only a `/x` recorded within `quiet_minutes`), so a broken sessions database would not cost
  the protocol day. The cross-review showed why not: a send is a window boundary the recomputed windows honour once
  the database is readable again, and the fallback cannot see a private thread that went on past `quiet_minutes`, so
  the send would cut it. The pass stays fail closed (`_off_the_record` is unchanged: a closed pass holds the
  interrupts). What changes is that the loss is loud: an interrupt held back (by a `/x` or a closed pass) is marked
  `held_back`, and when it then goes `missed` (note `held back`) the tick raises one alert for it. An interrupt missed
  because the tick was away is still not alerted.
  (3) A situational run that expires (`_expire`, in the tick's step and in `start`) cancels its pending interrupts as
  `abandon` does (after noting the ones the ledger shows delivered, `_reconcile_sent`, which `abandon` now does too)
  and says so in the chat (the note is owed, `expiry_notice` on the run: tried again on the next ticks
  if Telegram is down, and alerted after `SEND_ATTEMPTS`). A run does not count an upcoming interrupt as activity: a situational run
  that goes quiet past `expires_hours` is over, whatever it had timed. (4) **Choice:** a confirmed day more than
  `MAX_DAYS_AHEAD` (60) days ahead is refused, like a day that is over (asked once more; `/next` goes on without), which
  also keeps a day at the end of the calendar out of the ledger; the 60 is a guess at "a protocol day is tomorrow or
  next week", easy to move. `send_interrupts` and `oneoffs_due` also survive an item already beyond the calendar
  (`missed`, note `date out of range`), so one bad item cannot stop every run's interrupts. (5) **Choice:** an interrupt
  does not supersede an open program phase (`capture.superseded` ignores prompts with `interrupt`; `match` keeps open
  program phases beneath an interrupt): the phase is open on purpose and takes every message, an interrupt takes only a
  direct reply, so a typed answer to the phase after an interrupt has gone is still the phase's. A ritual beneath an
  interrupt is unchanged (the typed message is filed under neither). Cost: a passing remark typed after an interrupt
  while a phase is open is the phase's answer, as it was before the interrupt. (6) The quote verifier's journal corpus
  leaves out the files the engine or she wrote whole (`entries.authored_by_engine`: a reflection or week review as
  `is_analysis` defines it, or an `…-insights[-n].md` with `kind: insights`, `author: engine` and a `run`; a file of
  his own with an `author`, or with one of those kinds under another name, still counts. One named exactly like the
  engine's reflection and carrying an `author` is taken for hers, as everywhere else) and the marked `## Summary`
  (`entries.without_summary`), so her synthesis and the engine's interrupt questions cannot pass as `you wrote …`.
  (7) The confirmation note and the status show the year when it is not this one, and the status shows the date for a
  day more than six days off.
- **WP5 — existing assertions changed on purpose.** `test_programs.FullRunTest` answers session 10 phase 3 with a
  confirmation (`2026-10-27 07:00`) where it answered every phase with the same filler: free text no longer advances a
  schedule phase (the spec's "nothing is scheduled from free text"). `schedule.due` also returns the one-offs due (its
  tests and the golden `schedule.due.txt` have none, so they are unchanged).
- **WP9 review fixes — the document requires the summary's mark.** *Closes the raised item "a program session's
  `## Summary` has no engine mark".* WP4 keys the summary (`## Summary` then `%% journal:summary %%`), so the
  document takes `entries.summary_text` (the marked heading only) and `serve/document.py` no longer cuts the body
  itself: `entries.words_span` already ends his words at the marked summary. A `## Summary` he typed stays in his
  block, is searchable and is never shown as the engine's summary.
- **WP9 review fixes — search and word counts read his text only.** `document.parsed` builds `search` and `words` from
  the text of his blocks (the words section and his conversation messages as the document isolates them), not from
  `entries.his_words`: block headings (`### 23:12 IST · text`), clock markers, audio embeds and the
  "not transcribed" placeholders are the engine's and neither match a query nor count as words. `first_line` and the
  flags still read `his_words`, as the today block does.
- **WP9 review fixes — /api is decided on the decoded path.** The handler's `Cache-Control: no-store` follows the same
  decoded path the router used, so `/%61pi/v1/today` is an /api response too. Insights count no record with a
  `program` as a practice prompt answered or missed (as `today._practices` does); per-program progress still waits for
  the Run routes (raised by WP4). `/events` reports a user program's directory (`practices_dir/<name>/*.md`) as
  `library-changed` with the directory's name.
- **WP4 follow-up — review points that were open after the merge.** A phase the run comes to again by moving on (after
  a `/back`, or a jump back to a left session) takes `reopened_at`, so only answers given after that count toward the
  settle (the same stamp `/back` set on its target). A session that completes (by `/next` or `/skip`) while an audio
  answer waits for its transcript sets `summary_audio_pending`; the tick's `programs` step writes `## Summary` again
  from his words once no transcript of the session is pending (the entry's section is the engine's; the Telegram
  message that was sent stays as sent). `programs.step` is one `_step_run` per run: a send that raises an alert no
  longer skips the other runs, and the alerts are raised together after the loop. A `sessions:` name in a program file
  must be a plain file name (`SESSION_FILE`), never a path, so a user program cannot read a file outside its own
  `sessions/` directory.
- **WP10 follow-up (programs wired) — a web block on a program is the open phase's answer.** `POST
  /entries/<stem>/blocks` on a session entry needs `phase` (400 without; 400 for a non-program entry given one) and
  answers `409 phase-not-open` unless that phase is the run's open phase (run `active`, `current` = this session and
  phase, the phase delivered, its ledger prompt `phase_open`), with `where` (the `S:P:V` token) in the body. The rule
  is `capture.open_phase`, the one a Telegram message follows; the web names the phase because a browser has no
  "newest prompt in the chat" and a `superseded` open phase (a ritual sent after it) still takes a block the web
  addresses to it. It does not use the 3-hour `collapse_hours` window: a phase stays answerable from the web for as
  long as it is the run's open phase. `capture.add_web_block(phase_prompt=…)` rechecks `open_phase` under the ledger
  lock (`PhaseClosed` → 409) and writes the block through `record`, so the entry gets `%% journal phase:N %%`, the
  capture is rule `web`, and the Telegram phase message folds as after a Telegram answer. The session's entry does not
  exist before its first answer, so the *run* says which stem is a session's (`programs.entry_stem`), not the file; a
  file of his at the planned name is never written to (the engine files the answer under the next free name, as for
  Telegram), and the reply's `stem` says where it went.
- **WP10 follow-up — the advance follows the program's rule, by spawn.** After a new phase block the route spawns
  `journal program settle <run>` when the prompt's `advance` is `settle` (the hook's `_settle_later`, same command),
  and nothing for `advance: next` (the person presses Next). The reply carries `advance`: `settle`, `next`, or `tick`
  when the spawn could not start (the block is saved; the tick's `programs` step settles the run a few minutes late).
  A retry with the same `client_id` is answered from the ledger (`200`, no second spawn) even after the run has moved
  on, because the dedupe is checked before the phase is. The settle process lives in the serve unit's cgroup for
  `programs.settle_minutes` plus a moment (a sleeping Python, well inside `MemoryMax`/`TasksMax`); a restart of the
  unit during that wait kills it, and the tick's step settles the run instead.
- **WP10 review fixes — a phase block takes the server's clock, not `at`.** `at` still validates (400 when malformed
  or in the future) but a block on a program's open phase is stamped `req.now`: with a backdated `at` the capture
  would predate the phase's send or the run's `reopened_at` (a `/back`), and `programs.settle`, which ignores captures
  older than `reopened_at`, would then never advance the run; a forward `at` made the spawned settle find the capture
  too new. This is the rule `capture_web` already follows for a note (it answers a prompt only if it was sent at or
  before `at`), made trivially true. A retry with the same `client_id` is answered from the ledger for a program's
  prompt without asking whether the URL's stem is an engine entry: the answer may have been filed away from a file of
  his at the planned name, and the reply (`200`) carries the `stem` it went to.
- **WP10 review fixes — a changed definition does not break the run list.** `programs.describe` says "its definition
  changed: session S, phase P is no longer there" for a run whose program was edited to fewer sessions or phases, so
  `GET /runs` (which lists ended runs for good) and the chat's `/program` status keep working for every other run.
- **WP10 cross-review — what a run reads as.** `open` in `PhaseJSON` needs `delivered` (a re-send owed after a
  `/back` or a jump leaves the old prompt in the ledger, but `phase-not-open` is the answer to a block until it is
  sent). A situational run idle past `expires_hours` reads as `expired` in `GET /runs` (and `?status=`) and `GET
  /runs/<id>` before the tick's step writes it, as `POST /runs` already treats it; the file is unchanged. Two runs
  never share a planned session name (`programs._entry_stem` skips another run's prompt names), so an abandoned run
  cannot shadow a new one's entry (pinned by `SessionNameTest`).
- **WP10 Codex review — a queued transition re-checks its precondition under the lock.** `POST /runs` spawns
  `journal program start <id> --new` and `POST /runs/<id>/resume` spawns `… resume <run> --paused`: the request checks
  the state, but two requests can both pass before the first child takes the tick's lock, and a second plain `start`
  or `resume` on a run in progress re-sends its phase (or `--session` jumps it). With the flags the child does nothing
  (`programs.start(only_new=True)`, `programs.resume(only_paused=True)`; a line in the log says so). The chat's own
  `/start` and `/resume` are unchanged (a second `/start` re-sending the phase is its documented use). Read and
  write routes agree on a run whose program file is gone or no longer parses: `GET /runs/<id>` offers only `abandon`
  and every other action is `409 wrong-state` (before: `202`, then a spawn that failed unseen, since the web passes no
  `--notify`). `programs.abandon` no longer loads the definition (`_require_run`), so such a run can be ended, from
  the chat as well.
- **WP10 review — settle processes are not coalesced.** One `journal program settle` per phase block (as the hook
  spawns one per Telegram message) costs about 30 MB while it sleeps. A marker to skip the spawn while one is pending
  would lose the later block's settle: the pending process wakes while the newest capture is still inside the window
  and finds nothing to do, so the run would wait for the tick. A burst large enough to press `MemoryHigh` (10-20
  blocks in two minutes) is not a person answering; the cgroup limits and the tick's step are the backstop.
- **WP10 follow-up — transitions are spawned after a state check.** `POST /runs {program, session?}` is `202` and
  spawns `journal program start <id> [--session N]`; `409 active-run` (with `run`) when the program has a run in
  progress (active or paused; a situational run idle past `expires_hours` does not count, `programs.is_expired`),
  `409 disabled`, `404` unknown program, `400` for a bad `session`. `POST /runs/<id>/<action>` spawns `journal program
  <action> <id>`; the state is checked first and a wrong one is `409 wrong-state` (`api.wrong_state`, the one rule
  that also decides `keyboard` in `GET /runs/<id>`): next, skip and back need an `active` run, back also a begun
  session past phase 1, pause an `active` one, resume a `paused` one, abandon an active, paused or (with interrupts
  still to come) completed one; insights any run. Optional `expect` (`S:P:V`, the `where` the run was read at) binds
  next, skip and back as `--expect` does in the chat: a run that has moved on is `409 moved-on` and nothing is spawned.
  The engine is still the authority (it re-checks under the tick's lock); the 409s only keep known no-ops out of
  `manual-trigger.log`. The check is made again after the opener is built, just before delivery (the model call can take
  minutes, and a `/x` sent meanwhile would be ended by the send). Spawned errors are not sent to the chat (`--notify` is not passed: the person is in a browser).
- **WP10 follow-up — the Run JSON (API.md).** `GET /runs` is newest first, `?status=` (`active|paused|completed|
  abandoned|expired`, else 400) and `?program=`. A `Run`: `id, program, title, emoji, status, started_at,
  completed_at, paused_since, last_activity_at, current {session, phase}, where, text` (`programs.describe`),
  `sessions [{number, title, status, entry, phases_total, started_at, completed_at}]` (`status` is `not-started|in_progress|completed|left`; `entry` is null until the session begins,
  then the planned stem, then the stem the first answer was filed under), `insights_entry, scheduled [{id, session, n, of, offset, local, at, status, text}]`. `GET
  /runs/<id>` adds `phase` (`compose.phase_json` of the definition with his earlier answers filled into its prose
  slots, plus `sent_text` (what Telegram was sent: a reflect phase's construction exists only there), `delivered`,
  `open`, `answered`, `skipped`, `phases_total`; null when the run is over or its session has not begun, and present
  with `delivered: false` while a send is owed) and `keyboard`
  (the controls that mean something now). `/runs/<id>/insights` is `{stem, body}` of the compiled file, found through
  the run, 404 until compiled. None of this opens the chat transcript or the privacy pass
  (`test_serve_programs.NoChatTest`).


- **WP6 — the program block is read when the turn starts, from a queued pointer.** The dispatch hook (typed answers
  and voice or audio replies alike) queues `{run, session, phase, prompt}` under the turn's reply note in
  `hook-state.json` (`hookstate.remember_program` / `take_program`, a new section `programs`, same 15-minute
  deadline as a pack); `turncontext.build` takes it, once, and `programview.block` reads the entry then. A block
  rendered at capture time would have missed a voice note's transcript, which `_capture_voice` writes just before
  the turn. The block replaces the pack: it is tried first, and a turn that has one never takes the today block (the
  start of the thread is spent, as for a pack). If the pointer or the run is unreadable the turn goes on without
  it, and the today block may then take the seat; the notebook is unaffected. An older hook-state file has no
  `programs` section and is read as having one empty.
- **WP6 — what is in the block, and how 3,000 characters are spent.** In order: the heading; the stance sentence
  as PRACTICES.md §4.5 words it (role and mission each cut to 400 characters, so a hand-written file cannot crowd
  out his words; defaults "a careful guide" and "help <them> see clearly", the ones the reflect prompts use); one
  background line (not instructions; the engine sends the questions; the role is a lens and is not repeated to him);
  where the run is (its current phase; when the reply belongs to an earlier phase the line says so, so a late answer never reads as his position) and how the program moves on (titles cut to 80 characters, so a long hand-written title cannot push
  his answer out of the cap; a settle program "after N minute(s) of quiet, or at once on /next", an `advance: next`
  one "when he sends /next"; "they send", "quiet from them": both pronoun forms come from the names); the phase as it went out (the ledger record's `phase_text`, so a reflect phase's
  construction is in it; at most 1,100 characters, cut on a line, and given only the room his words leave, with 300
  characters held back for it); then his words in this session, labelled by phase, verbatim. This
  turn's phase claims the room first, then earlier phases newest first; one that does not fit is cut at its end with
  ` […]` (the entry is untouched; the block is a copy) and the ones left out are counted. The engine's notes about an
  untranscribed recording are not his words and are left out, block by block (`programview._words` over
  `entries.phase_blocks`), so a note beside typed words or under a caption is cut too; the joined
  `programs.answers` would have let it through as his. The answers come from the session entry's
  `%% journal phase:N %%` blocks and nowhere else: not `## Conversation`, not the chat transcript. A message sent
  off the record was never captured, so it is not in the entry; the block has no privacy pass of its own to fail.
- **WP6 — `journal_program` is a shell in `hermes/tools.py` over `programview`.** The core `tools.py` of
  ARCHITECTURE.md is WP8's; until then the Hermes handlers live in `hermes/tools.py`, and the reads
  (`programview.status`, `propose`) are engine-side so WP8 only moves the registration. `status` returns
  `programs.status` plus its text (what `/program` shows). `propose` takes one program id or alias (a trailing word such as a
  session number or `insights` is refused: she offers the plain line, and he can add the number himself), resolves it
  with `commands.start` (so a turned-off or unknown program is refused the way `/start` refuses it) and returns the
  `/start <id or first alias>` line, whether a run is in progress (the line picks it up), and a note that nothing
  was started. With no program it lists every startable one. Neither action writes (a test compares every file under
  the state dir and the journal before and after), so neither is in `hooks.JOURNAL_WRITES` and both run in an
  off-record turn. It is registered after the three existing tools (the registration order test moved with it).
- **WP6 — the skill's Programs section.** `{{programs}}` is rendered at gateway start from the library's enabled
  programs, with the line that starts each (an alias when the file has one: `/start prep`) and how it moves on
  (from the program's `advance` and `programs.settle_minutes`, never one sentence for all), like Kinds. The rules
  it gives: the engine sends the questions as authored and she never rewrites, skips, reorders or advances them;
  the role text is a lens for the session, not a claim about who she is, and neither it nor any third-party persona
  detail is repeated to him; `journal_program` is read-only and `propose` is offered at most once and only when it
  helps. A program added after the gateway started is startable at once but joins that list at the next restart.
- **WP6 — existing assertions changed on purpose.** `test_hermes.RegisterTest` expects the fourth tool and
  `test_hookstate` the `programs` section; both are the spec's additions.
- **WP10 review fixes — what the review of the merged WP10 found, and how each was settled.**
  (1) *One id space for the library's write gate.* `library.check` (the gate of `PUT` and of `?validate=1`) refuses a
  practice whose id is any program's or situational definition's id, and a new practice whose id is one's alias;
  `PUT` answers `409 is-program` for such an id. A definition of his that does not parse still holds its id and aliases
  (read from its front matter and its folder or file name). The ids are read from the definitions themselves (`library.program_words`),
  not from `load().programs`, because a program that a same-named practice shadows drops out of that map. `DELETE`'s
  active-run guard applies to a definition that is not a practice: a practice has no runs, so a run under its id
  belongs to a program it hides, and removing that file gives the program back. Editing a user situational or
  program file stays WP12's. (2) *`collapse_hours` is at most 8760.* `library.MAX_COLLAPSE_HOURS` (a year, so a
  hand-written longer window is now a named problem, not a silent one); `capture.window` also holds whatever a prompt
  or the settings carry to that bound, so one definition can never stop capture or the fold of the other rituals.
  The settings range (0.25 to 72) is unchanged. (3) *A retry is the first result.* An upload that is gone while the
  `client_id` is already in the ledger (a capture, or `web_blocks`) is not a 404: the capture answers with the first
  result. The key of a block is computed under the ledger lock when it is written (`add_web_block(key_of=…)`) and kept
  as `block` on the capture or `web_blocks` record (a retry whose `web_blocks` record has expired is a 404 again, not a
  second block without its audio); a record from before that has none, and the newest block's key is
  the fallback. (4) *Mood and people are valid YAML.* `entries.flow_list` writes an item as a double-quoted scalar when
  YAML would not read it back as the same text (a leading `#*!@%"'`, `: `, `true`/`yes`/`null`, numbers, dates);
  other items stay as they were. `entries.parse_flow_list` reads both. (5) *`at` has a floor.* At most 31 days
  back, not before the day of the entry a block is added to (a program answer ignores `at`), 400 on a time that
  cannot be converted. (6) *A phase block needs the run's definition* (409 `phase-not-open`), as `wrong_state`
  already did for the transitions. (7) `test_the_limit_is_25_mb_not_one` sends headers only for both 413 cases.
- **WP7 — the ports as built** (`ports.py`; ARCHITECTURE.md §2 is amended to match). `Row.copy` is the id of the row it
  repeats (`int | None`), not a flag: the privacy pass gives a copy its original's answer by that id (PRIV). `Row`
  gains `stored`, the text exactly as stored (her reply verbatim, which annotation alignment returns; and for his rows
  the one place the core looks for the journal's *own* marks: the off-the-record marker and the hook's note path,
  never the platform's envelopes). Her kinds split `empty` from `frame` (the catch-up skips an empty row but took a
  typed or compaction row with text for a reply). `meta` keys are listed in `ports.py`. The `Transcript` protocol adds
  `source` (what is read, the string kept in `conversations.json` and `sweep.json` as `db`: unchanged for an existing
  install), `errors` (what a read raises when the chat cannot be read: the readers catch these and log as before, so the
  six readers no longer import `sqlite3`), `available()` (the store exists: the privacy pass computes over no rows and
  is not saved when it does not), `first_id()` (the backfill's hand-over cursor, any role, as its query had it) and
  `tail(n)` (doctor). `mirror(text, flushing=…)` returns `True` or `"queued"` and raises `ports.NotMirrored(reason)` for a
  reason worth one log line; `NullTranscript.mirror` returns `False`.
- **WP7 — `cfg.transcript()` is the Hermes adapter whenever a sessions database is configured,** not "under Hermes and
  `state_db` exists" (ARCHITECTURE.md §2 said so): a standalone install with `hermes.state_db` set, and every test
  fixture, has always been read, and a missing file has always been "sessions database unavailable" for the readers
  (logged, tried again next tick) and "not saved" for the privacy pass, which `available()` keeps. `adapter` is a new
  top-level key: `auto` (default) and `hermes` behave the same; `none` makes `state_db` None (no transcript, as
  standalone) and model steps run only through `agent.command`, else `agent.NoCompanion` raises `AgentUnavailable`
  (an outage: deferred, never an attempt spent). Any other value is a config warning and reads as `auto`.
- **WP7 — the companion.** `agent.ask(cfg, …)` keeps its signature and asks `cfg.companion()`. `CommandCompanion` is the
  runner it always was (query file, `budget + 60` s, exit status, the trailing `session_id:` line and helper notes
  dropped, Hermes' failure copy refused even with exit 0), for `agent.command` too, as before; `HermesCompanion`
  (`hermes/companion.py`) supplies only the `hermes chat --oneshot` argv, and holds Hermes' failure copy
  (`is_failure_text`). `AgentUnavailable` is `ports.CompanionUnavailable` (one class).
- **WP7 — the old entry points stay, thin.** `conversations.rows` (the tuple shape), `sweep.rows`, `sweep.read`,
  `sweep.parse`, `mirror.turn_running`, `mirror.busy`, `context.clean_user_text`, `agent.is_failure_text`,
  `agent.is_failed_turn` and `agent.FAILED_TURN_*` answer from the adapter (imported when called, never at module
  load). The unchanged suites call some of them, and `mirror._mirror_to_session` stays the test seam every suite
  replaces: the adapter writes through it, and its body asks the adapter, which alone imports `gateway.mirror`.
  WP13 can remove the ones only tests use.
- **WP7 — what the refactor changes at the edges, on purpose and only where nothing realistic moves.** (a) A row
  whose time cannot be read makes the chat unreadable: `HermesTranscript.rows` and `tail` raise `ChatUnreadable`
  (an `OSError`, so the readers' own catch logs it and tries again, as the `ValueError` did before the ports). It is
  never skipped: a skipped bare `/x` would open the privacy pass's window on the private words after it and let them,
  and her answers, into the journal. `to_row` alone still returns None for such a row.
  (b) Bounds are compared on `Row.at` (microseconds), where the queries compared stored floats: a row within a microsecond of a lookback bound can fall on the other side. (c) The verifier's corpus
  is in stored order (its query had no `ORDER BY`). (d) One of his rows with no stored text whose Telegram id the hook
  recorded off the record is now off the record (`row_is_off` checked the row's own marks only when it had text): more
  private, never less. (e) The catch-up's log line when the privacy pass cannot run names `ChatUnreadable` where it
  said `Error`. (f) Every read waits up to 5 s for a database lock (the pack's readers waited 10). (g) The catch-up, the
  pack and the verifier now read compaction copies' links too (one query for every reader), which they ignore.
- **WP7 — `test_serve_fence`'s self-check changed on purpose.** It asserted that `context.py` imports `sqlite3`, as
  "a module that does read the transcript"; after WP7 that module is `hermes/transcript.py`, and the fence also forbids
  `transcript`. (A deliberate change to an existing assertion.)
- **WP7 — two runs of the reader suites.** `tests/test_fake_transcript.py` runs `test_conversations`,
  `test_privacy_coverage`, `test_sweep` and `test_recovery` a second time, unchanged, with `cfg.transcript()` answered by
  `helpers.FakeTranscript` over the same fixture: rows normalised by the adapter's `to_row`, but the chat, the order,
  the bounds and the compaction copies worked out in Python, so a reader that leaned on the adapter's SQL would fail
  there. The turn lease and the mirror write stay the adapter's (they are not row reading). `PortOnlyTest` drives the
  writer, the pass, the sweep, the catch-up and the pack from hand-made `Row`s with `sqlite3.connect` made to fail.
- **WP7 — checked on the live transcript before merge.** A read-only probe ran main's readers and this branch's over a
  copy of a live `state.db` (the copy, the hook's records and the send times in a private temporary
  directory, removed after; only hashes and counts printed): every row's decisions and the privacy pass matched, and
  the vault checksum did not move. That copy held no `/x`, so the off-the-record paths rest on the golden and the
  suites.
- **WP7 — `doctor --deep` samples the transcript.** The newest 50 rows, counted by kind (his words or saved replies,
  mirrors, deliveries, empty or system; her replies), never words; a warning when none of his rows that could be a
  message (journal mirrors and deliveries are not counted) reads as his words (Hermes changed its storage) or the
  transcript cannot be read. Not for the web's `/doctor`.
- **WP7 — with `adapter: none` (no transcript) the edges are explicit.** `mirror.flush` records a queued mirror as not
  mirrored (`mirrored: false`, one log line) when `mirror` returns `False`, never as mirrored; the hook does not stash
  chat audio (nothing would write it), and a tick that reads no chat (no transcript, or a sessions database that
  cannot be read) still ages the audio stash out (`conversations._prune_stash`); `doctor` warns "no companion: model
  steps wait (set agent.command)" instead of reporting the agent healthy.
- **WP8 — the seam as built** (`inbound.py`, `tools.py`, `commands.py`; ARCHITECTURE.md §2.1 is amended to match).
  `Inbound` is the adapter's reading of one event: `platform`, `chat_id`, `message_id`, `text`, `at`, `reply_to`, `kind`
  (`text`, `voice`, `audio`, `document`, `photo`, `video`, `other`), `media_path`, `mime`, `file_name` (the sender's
  name for the attachment, when it has a suffix), `has_media` (any attachment: a bare `/x` with one is a message, not
  a window; the spec's `kind` alone cannot say so for a forwarded file) and `meta` (`audio.event_meta`). `Outcome`
  gains `reason` (a dropped message says why). The one thing the engine cannot do for itself, running `python -m
  journal …` detached with the adapter's environment (`hermes/commands.spawn`: the gateway's interpreter, the
  profile's settings, the bot token), is passed in as `spawn`; an adapter that passes none loses only what needs a
  process (a settle is left to the tick's `programs` step; `/next` `/skip` `/back` `/break` go on to the platform, as
  before, while `/program`, which only reads, is still answered here).
  `receive` still decides "the journal's chat is a Telegram chat" by the platform string, as `turncontext` does.
  `begin_turn(cfg, Turn)` is the other half of the old `pre_llm_call` glue (voice transcripts, the off-record turn
  mark); the hook builds the `Turn` once and hands it to `begin_turn` and to `turncontext.build`.
- **WP8 — `tools.call` is a second check, not the only one.** `tools.refusal(cfg, name, params, turn)` is the old
  `off_record_block` (the same two signals, the same text, fails closed); the Hermes `pre_tool_call` hook still turns a
  write away first with it, and `tools.call` runs it again before the handler, so a hook that failed open (its guard
  does, by design) no longer lets a write through. That is new behaviour on one path: a write in an off-record turn
  that the hook missed is refused by the handler (`{"success": false, "off_record": true, "error": …}`). Reads (the
  notebook's `list`, `journal_program`) are allowed, and `journal_set_location` was not a write of his words and was not
  refused (changed by OFFLOC below). The Hermes handler passes the `session_id` (and `turn_id`, if Hermes gives one) it receives; with
  neither, the second signal (the privacy pass over the chat) still holds, so the worst case is the hook's own.
- **WP8 — `commands.SPECS` is a list of `Spec(name, description, run(cfg, raw_args, spawn), needs_config,
  unconfigured)`.** `run` returns the text to answer with (or None); `unconfigured` is what the chat is told when the
  adapter could not load the configuration for the command (kept per command, so every message is the one that was).
  The off-the-record command's name comes from `conversations.opt_out_prefix`, so `specs(cfg)` is the list for a
  configuration and `SPECS` the default `/x`. `/start`'s routing (`routed_start`) and the program commands' (`routed_program`) are
  the engine's; `hermes/commands.py` keeps `start`, `program_action`, `program_status`, `trigger`, `off_record` and
  `PROGRAM_COMMANDS` under their old names as adapters over the specs, so every caller and test of them is unchanged.
- **WP8 — what the move changes at the edges, on purpose.** (a) Log lines from the moved code carry the loggers
  `journal.inbound`, `journal.tools` and `journal.commands` instead of `journal.hermes` (the messages are the same);
  the test helpers silence the whole `journal` tree. (b) A voice note's transcript is written with the turn's `at` (read
  once, when the hook builds the turn) rather than a second clock read a few microseconds later. (c) Five existing tests
  that named moved private helpers now name their homes: `hooks.transcript` → `inbound.transcript` (`test_engine`),
  `hooks.keep_audio` → `inbound.keep_audio` (`test_fixes`), `hooks._remember_program` → `inbound._remember_program` and
  `hooks.off_record_block` → `tools.refusal` (`test_programview`), `hooks.NOT_TRANSCRIBED` / `hooks.audio` →
  `audio.NOT_TRANSCRIBED` / `audio.TRANSCRIBING` (`test_audio`); `test_programview`'s registration check reads
  `tools.specs` instead of grepping `hermes/__init__.py` for `register_tool(`. No assertion about behaviour changed.
  (d) `hermes/hooks.py` is 146 lines (it was 447); `tests/test_purity.py` holds it under 150 and keeps engine state
  (capture, ledger, hookstate, privacy …) out of its imports.
  (e) `hooks.to_inbound` cannot fail on the attachment: reading the media or its metadata is wrapped, and a failure
  means "no attachment" (logged), so `receive`'s `/x` check always sees the message (it ran before any media read in
  the old hook, and fails closed; a translation error ahead of it would have let an `/x` message through unmarked).
- **WP8 — checked.** `tests/test_inbound.py` drives `receive` and `begin_turn` with plain dataclasses and no Hermes
  object: a reply with its pack, an unmatched message, another platform and another chat, `/j`, `/start` routing, a bug
  in capture (passes through), `/x` with text, a bare `/x` window (what follows is marked, `/j` still saved), a bare
  `/x` with an attachment, `note_off` failing (still marked, nothing captured), a voice note (kept, written at the
  turn), an audio file, audio that answers nothing, the extension rule, a voice note the state file cannot hold, a
  program phase answer (note, block queued, settle spawned), no `spawn`, an off-record answer, the program commands
  run and dropped, and `tools.call` (writes refused in an off-record turn, reads allowed, a bare-`/x` window with no turn,
  fails closed, the Hermes handler refusing on its own). The hook tests (`test_hermes`, `test_conversations`,
  `test_programs*`, `test_practices_engine`, `test_audio`) pass as they were.

- **WP12 — the app writes, and only appends.** Every write is `api.ts`'s: a JSON `POST`/`PUT`/`PATCH`/`DELETE`
  checked against the same shape parsers as the reads (unknown fields dropped; the `ApiError` carries the server's
  `problems`, `where` and `run`), plus `uploadAudio` (multipart). Nothing is retried by the client; a failed write is
  shown where it was made. There is no editor for any block of his (Decision 4): the entry's composer adds a block, the
  run's composer adds a phase answer, the "+" sheet makes a note or answers a prompt, and the only thing that rewrites an
  entry file is `PATCH …/meta` (the front matter). No model is called from the UI (Decision 6); mood and people are typed.
- **WP12 — the composer: draft, client id, upload.** `Composer.tsx` keeps what is typed in `sessionStorage` under
  `journal-draft:<key>` until it is sent (FRONTEND.md decision 3: the only thing besides the session cookie the page
  stores). A send makes one `client_id` and keeps it for as long as the words are unchanged, so Retry after a lost
  answer is the same write (the server answers the first result); editing the text or the recording makes a new id. A
  recording is uploaded first and named in the post; if the post fails the upload id is kept and reused, so Retry does
  not upload twice (uploads live an hour server-side; one older than 50 minutes, or one the server answers 404 for, is
  uploaded again from the kept recording under the same `client_id`, so Retry never fails for ever and the recording is never
  lost to the clock). A `422` (`off-the-record`, `reserved-line`) shows the server's
  line and no Retry button, because sending the same words again would be refused again; the text stays in the box.
  The client does not guess the opt-out command: the server is the only judge of `/x`.
- **WP12 — voice.** `Recorder.tsx` uses `MediaRecorder` with the first supported of `audio/webm;codecs=opus`,
  `audio/webm`, `audio/ogg;codecs=opus`, `audio/mp4` (Safari), asks for the microphone only when Voice is pressed,
  releases it the moment recording stops, and hands the blob to the composer, which shows it in an `<audio>` to hear
  before sending (the CSP's `media-src` is `'self' blob:` for this: a `blob:` URL names bytes the page holds, not a
  network origin; found by the review, which a jsdom test cannot see). Where there is no `MediaRecorder` or no `getUserMedia` (an insecure page) it says so. The server keeps
  the file with the extension its MIME type gives (`.webm`, `.m4a`) and the tick transcribes it like a Telegram file.
  **Unverified until live:** that the tick's transcriber reads `.webm` and `.m4a` recordings (it reads what the Telegram
  path keeps, usually `.ogg`); DEPLOY.md's WP12 section has the check (a short voice note from the phone, then the
  entry after the next tick), and the fallback is a "transcript pending" badge, not a lost recording.
- **WP12 — the editor.** The form (`editorModel.ts`) is the practice's `definition` JSON, the shape `GET /library/{id}`
  serves and `library.from_dict` takes; it carries over what it does not edit (`kind`, `group`, the opener's `shape`
  and `limit`). The client renders no Markdown: the Raw tab is the canonical text the server returns from
  `PUT ?validate=1`, asked a moment (400 ms) after the last edit, and nothing is written by it. The Raw tab can be
  edited as text; saving then sends `{raw}` and the form is re-read from the answer; going back to the form first asks
  before dropping unsaved text edits. A save carries the `mtime` it read (none for a new file). `409 mtime` shows
  "Changed elsewhere (Obsidian?)" with Reload and Overwrite; Overwrite reads the file's new mtime and saves over it;
  `422` puts each problem beside the field it names (`schedule N:`, `section N, question M:`), the rest above the form.
  The editor does not follow `library-changed` (a reload would drop what is being typed); drift is the `409`'s job.
  The pure moves are tested in `editor.test.tsx`; the round trip is pinned to the server by
  `tests/fixtures/api/library-validate.json` (`ValidateFixtureTest`: the gratitude definition sent as the form sends it,
  and the file the server renders for it), and `definitionFromForm(formFromDefinition(d)) == d` is a vitest.
- **WP12 — New practice is `/new-practice`, not `/library/new`.** A practice id may be `new` (`ID` allows it), so a
  literal route under `/library/` would hide such a practice's page. The file name is the title's slug (lower-case,
  hyphens, starts with a letter, 2 to 31 characters), the first free of `slug`, `slug-2`, ... against the library's ids
  (built-ins and programs included), editable under More options; the form starts with a title, a daily time (21:00)
  and one question, and "a three-field practice is due on the next tick" is the server's (`PUT` writes the file, the
  library is re-read on the next tick).
- **WP12 — enabling a built-in forks it.** A built-in cannot be saved, so "Turn off" on one is fork, then `PUT` of the
  fork with `enabled: false` against the fork's returned mtime (FRONTEND.md §1.4: "enabled: false via fork"). "Turn on"
  on a fork is a `PUT` with `enabled: true`; "Reset to built-in" is `DELETE` after one in-place confirmation (the file
  of his is gone, so it asks once). Programs have no toggle (their files are not edited here). *Superseded by
  BUILTIN-EDIT:* a built-in is saved in place, so "Turn off" is one `PUT`; the reset reads "Reset to default".
- **WP12 — program cards, interrupts and progress, from `GET /runs`.** Today shows a card per run in `active` or
  `paused` (title, `programs.describe`, a progress bar of completed sessions, Continue, Pause or Resume), the session-10
  one-offs still `pending` with today's `local` date as a quiet list ("question 2 of 8" and the time, not the text:
  the text is the question and arrives when it is due), and "No program running. Start one from the Library" when the
  read succeeded with none; when `GET /runs` fails Today falls back to the one-line `program.text` it had. The
  Library card draws the progress bar from the same list. A transition is `202`: the card says "Pausing…" and clears on
  `runs-changed`, and Send now says "On its way…" and clears on `ledger-changed`.
- **WP12 — the run screen** (`/runs/<id>`) is `GET /runs/{id}` followed by the events of runs, ledger, entries, library
  and tick (the rule of WP11 round 5). Its controls are exactly `keyboard`; Next, Skip and Back carry `expect` (the
  run's `where`), and a `409 moved-on` says the program moved and reads it again. The answer box is closed (with the
  reason) while the phase is not open, not yet delivered, or the run is on a break; an answer is
  `POST /entries/<session entry>/blocks` with `phase`, and what the server says will happen next (`advance`: settle,
  next, tick) is told in a line. The phase is shown from `compose.phase_json`'s `blocks` (which already hold the
  questions); a reflect phase shows `sent_text` when there is one, else the template with a note. Abandon asks once.
  A program session's entry page has no composer of its own: it points at the run (a block there needs the phase).
- **WP12 — settings are a form over `config.WEB_WRITABLE`**, not a free key editor: `settingsModel.FIELDS` lists
  fifteen of the seventeen keys the server allows (place, time zone, the four windows, the day and week reflection, quiet and settle
  minutes, the today block), with the server's ranges, so a value out of range is named where it is typed and Save is
  off until it is fixed (a minimum that the server holds exclusive, as 0 for the quiet, settle and cache minutes, is an `above`
  rule: "More than 0"); the server still checks. `coordinates` and `today.sources` are not offered (a pair of numbers
  and a checklist of sources: see "Raised by packages", WP12 review). Only changed fields are sent, as nested objects; an emptied box means
  "leave it" (the web app cannot unset). A flag is Default / On / Off because the settings tree holds only what is
  configured, never defaults. A key whose origin is `hermes` or `env` is locked with the line that changes it
  (`hermes config set plugins.entries.journal.settings.<key> …`); the server's `409` is the backstop. The read-only tree
  stays below, as before.
- **WP12 — the "+" sheet** (a button in the bar between Entries and Library) offers Quick note, Voice note, Answer
  today's prompt (the rows of `/today` that are `open`) and Start a program (enabled, program-backed, no run going).
  A note is `POST /entries` without `practice` (the server says `rule: "/j"`, and the sheet says "Saved as a note");
  Start spawns `journal program start` and goes to that program's Library page, where the run appears on
  `runs-changed`. Escape and the backdrop close it; focus goes into it and back.
- **WP12 — tests.** Vitest: `editor.test.tsx` (model, editor, new practice), `writes.test.tsx` (composer, voice with a
  fake `MediaRecorder`, meta sheet, Today, run screen, Library actions, settings, the sheet), `settings.test.ts`. The
  fixtures `runs.json`, `run-one.json` and `library-validate.json` come from the real server
  (`RunFixturesTest`, `ValidateFixtureTest`; the run's "open since" time is the real clock's and is normalised).
  Playwright `web/e2e/writes.spec.ts` runs once, on the phone viewport and last (it changes the throwaway journal), and
  sends nothing (no Send now, no program start, no settings: those spawn `journal` or write the config).
- **WP12 review — what was fixed, and the rules it left.** (1) *The meta sheet sends only what he changed.* The server
  replaces a key whole, so a list the sheet could not read (Obsidian's block style, `mood:` then `  - calm`) sent back as
  "none" deleted it. Both halves are fixed: `entries.front_matter_lists` reads block lists (so the sheet opens with them),
  and the sheet omits a key he did not touch (Save is off until something changed). A value the server still cannot read
  (a nested map under `mood:`) would be replaced if he edited that very key; refusing that with a 409 is not built
  (see Raised). (2) *Today lists the one-offs of every run that is not `abandoned` or `expired`* (`programs.pending_interrupts`'s
  rule): session 10's go out the day after it, when the run is `completed`. A completed run that still holds some gets a
  small card with "Stop the questions" (`abandon` cancels them; the entries stay). (3) *The raw tab never seeds text from
  the file as it was read.* It shows the file only while the dry run answers for the form as it is now, and is read-only
  (with the reason) while the form does not parse or the check is on its way. (4) *Pins:* moving a section pins both
  sections' questions, going between a flat list and sections pins them, removing a question or a section pins those
  after it, because ids are numbered by place (`PinnedIdsTest` parses the shapes back through `library`). (5) *Words he
  typed survive a phase change:* the composer keeps them aside with "Put them back in the box" / "Let them go". (6) *A
  reflect phase's `sent_text` is labelled* "Written by the companion for this phase" (the run's JSON carries no
  companion name, so the label is generic). (7) *`GET /entries/<stem>` carries `writable`*; his own page that is only
  named like an entry gets no composer and no meta sheet, and a meta `409` that is not `mtime` shows the server's line
  without the Reload offer.
- **WP12 — no API change.** The routes were all there (WP9, WP10, WP10 follow-up); API.md is unchanged. The web client
  parses fields it used to drop (`LibraryOne.definition.annotate`, `collapse_hours`, `group`, `opener.mode/shape/limit`,
  which the server already sent), because the editor needs them.

- **WP13 — the `schedule` config key is retired, not deprecated.** WP2 kept `schedule.<id>` as an override of a file's
  slots for one release. It is gone: `Config.schedule`, `DEFAULTS["schedule"]`, `library.slots`/`with_slots`/
  `override_slots`/`schedule_problems`, the doctor rows and `journal practices migrate-schedule` (and its `--dry-run`)
  are removed, and when a practice is sent is its file's `schedule.*` lines and nothing else. A `schedule` key left in
  a config layer is ignored, never an error (a stale key must not stop every send), and `from_dict` names it in
  `Config.warnings`, which doctor lists (`` `schedule` in the config is no longer read … ``). An empty value (`{}`,
  `null`) is silent; any other, an object or not, is named. Under Hermes the gateway reads only the keys of
  `TOP_LEVEL_KEYS`, so it never sees the key; the tick and the CLI do, which is where doctor runs. The window-C
  check "no deprecation rows" is that the warning is absent (a config without a `schedule` key).
- **WP13 — `journal migrate-config` is the one way a `schedule` block moves.** It reads the block from the source file
  (the predecessor's flat `journal.json`, or a file already in the new shape, which now passes through minus the key)
  and from a `--merge` overlay (the overlay wins per entry), never copies it into the new config, and turns each
  entry that still changes something into a fork of that built-in in `practices_dir`, written the way WP2's
  `migrate-schedule` did: the entry's keys on top of the file's first slot (`{"time": "07:30"}` keeps the weekly's
  Sunday), one slot as the result, `enabled: false` = a fork with no schedule lines (on demand only), the fork
  carrying `windows.collapse_hours` so it folds as the built-in did, a file of his own never rewritten (it gets the
  lines to change), an entry that says what the file says, names an unknown id, or is unusable, named and skipped.
  The forks are written only with `--to` (printing the config to stdout also prints "would fork …" and "nothing was
  forked"), the config last, so a directory that cannot be written stops the run with the config untouched and the
  forks already made listed in the error. `practices_dir` is where the new config would put it (its `practices_dir`,
  else `<state_dir>/practices` with the `--hermes-home` / `$HERMES_HOME` profile, else the standalone default);
  `--practices-dir` overrides. The note that `schedule` was not carried over is always printed.
  *Review (WP13):* the directory is resolved the way `config.load` will — the new config under the plugin settings
  of `--hermes-home` / `$HERMES_HOME` and `$JOURNAL_PRACTICES_DIR` — so the forks land where the engine reads them.
  A `--practices-dir` that differs is written into the new config (and noted); when a Hermes setting or the
  environment would override it, the run is refused before anything is written. A `schedule` under the plugin
  settings is read as well (it won per entry before WP13, so it still does) and the doctor warning for one says to
  run `migrate-config --hermes-home …` and then `hermes config unset …settings.schedule`; this command never edits
  `config.yaml`. The timezone plays no part in locating the directory (a bad one no longer stops the migration).
- **WP13 — the ritual ids are read from the packaged files.** `config.KINDS` is replaced by `library.builtin_ids()`
  (the ids of `library/practices/*.md`, in library order, read once per process): `commands.specs` registers the four
  ritual commands from it, `entries.known_ids` names them beside `j` and `note`, and `config.opt_out_problem` refuses
  them as the off-the-record prefix (looked up at call time; if the files cannot be read it falls back to the other
  reserved names, so `/x` never depends on the package data). `config.RESERVED_COMMANDS` keeps the names that are not
  practices. `config.DEFAULT_SCHEDULE` is gone too: `tests/test_golden.py` holds the files to the four times as
  literals (an unchanged expectation, moved).
- **WP13 — `questions.py` is deleted, not thinned.** Nothing in `src/` used its constants or its `bank_*` functions
  after WP2; the goldens, the opener tests and `tools/gen_builtins.py` were the callers, and they now ask the
  `Practice` (`library.builtins()[id].bank_lines()`, `.items()`, `.bank_with_ids()`, `.bank_text()`), whose bytes the
  goldens already pinned. `tools/gen_builtins.py` (still run by CI with `--check`) is now the canonical-form round trip
  of the four files, no longer the one-off generator: the constants it generated from no longer exist. The install-time
  scan's `role_hijack` caution therefore names `src/journal/library/practices/midweek.md`, as the README says.
- **WP13 — `plugin.yaml` is the configuration, and a test says so.** `config_schema` loses `schedule` and now has exactly
  the keys of `config.TOP_LEVEL_KEYS` (`tests/test_config.EndStateTest`), each with a description, and every dict
  default names only keys the engine has. The plugin version stays `2.0.0` (it is a release-numbering matter for
  the deploy, and doctor compares code, not this number).
- **WP13 — two dead thin entry points removed, the rest of WP7's left.** `mirror.busy` and `agent.is_failed_turn` had no
  caller and no test and are gone. The others WP7 listed (`conversations.rows`, `sweep.rows`/`read`/`parse`,
  `mirror.turn_running`, `context.clean_user_text`, `agent.is_failure_text`) are still called by unchanged suites that
  are the contract for the behaviour; removing them means rewriting those suites against the adapter, which is a
  refactor of tests, not a cleanup of shims, so it is not taken. `audio.py`'s import of Hermes' speech-to-text (WP7's
  and WP8's open point) stays too: it needs a place on the ports and `tests/test_purity.py` still allows it by name.
- **WP13 — the skill.** The only change is the line on changing times and questions, which now mentions the web app's
  Library beside the files. It loads at gateway start, so it rides window C.
- **PRIV2 — an automatic send never closes a `/x` window, including owed sends and sends after a model call.** A
  phase send is a prompt and ends a bare-`/x` window (`privacy.py`), so a send he did not ask for, made while he talks
  privately, files his next private message as the phase's answer. WP4 made the settle wait; a read-only review of the
  stack found two sends it missed: the tick's step delivering a phase owed a send (its `/next` send had failed) with no
  check at all, and the settle's own send, which comes after a reflect construction or bridge model call (up to about
  three minutes), during which a `/x` could arrive. Rule: `_send_phase`/`_send_current` take `auto` (default False);
  `advance` passes `auto=(reason == "settle")` and the step's owed path `auto=True`. With `auto`, the window is asked
  before the model calls (a tick inside a long window spends no model call on a phase it will not send) and again just
  before the first message goes, at the later of the transition's `now` and the clock, and held if `now` alone says
  open too (`_held_off`): a `/x` that came after `now` opens a window `now` cannot see, and a ritual sent earlier in
  the same tick has closed one that `now` still sees (the phase then goes out at the next tick, after the ritual:
  see the ritual open point above). A held send logs, returns False and leaves `send_due` set; the step
  tries again each tick and sends once the window has closed (or, with the pass closed, once it can run: fail
  closed). `/next`, `/skip`, `/back`, `/start` and the web app's button are his acts and send at once, as before.
  The interrupts already waited (WP5) and send right after their check with no model call between. Scheduled rituals
  are raised above, not changed.
- **PRIV2 — three older edges, each now fail closed.** (1) A message inside a bare-`/x` window whose
  `conversations.note_off` raises is still rewritten off the record (the error is logged); it used to fall to
  `receive`'s `except`, which looks only for a `/x <text>` prefix, and was captured. The same `except` now also marks a
  bare `/x` with a file attached (a captioned voice note or audio file), which it used to pass on: the audio was kept
  beside the entry and transcribed into it. (2) The sessions database's
  time-bounded read (`raw_rows(since=…)`) keeps rows with no timestamp (`or m.timestamp is null`), so a busy-path bare
  `/x` stored without a time reaches `_read`'s unreadable-time path and the chat is refused, instead of being dropped
  in SQL and opening no window. This is defensive, not a hole found live: Hermes declares `messages.timestamp REAL NOT
  NULL` and the live `state.db` checked had no such row, so only a NULL (an older schema, a hand edit) is newly covered
  (out-of-range and text values were already returned); if one ever appeared, every `since`-bounded view would close
  until the row is mended, since the widened read keeps no-time rows of any age. `FakeTranscript` now agrees: it refuses exactly the reads that would return such a row
  (by id range, and by `since` as SQLite compares: no time or a non-numeric time is in range), and no longer refuses
  `max_id`, `anchor`, `first_id` or `last_activity`, which the adapter never did. (3) A voice transcript is cut at the
  first off-the-record marker in the turn before the note and caption are taken out: if the platform merges a queued
  `/x` message into the voice note's turn, the marker and his private words are not written as the transcript (a
  transcript placed after them is lost; the recording is still kept). `journal_set_location` in an off-record turn is
  unchanged (WP8's open point above; the owner's decision).
- **OFFLOC — `journal_set_location` is refused in an off-the-record turn (the owner's decision, 2026-10-20).** Off the
  record means nothing in the journal changes, a move included: a place said in a `/x` turn must not move the
  location, timezone or weather coordinates, nor append to `location-history.jsonl`, and a move can be said on the
  record later. `tools.WRITES` lists it (`None`: every call writes), so the two routes that already refuse the other
  writes refuse it with no new code path: the Hermes `pre_tool_call` hook (`tools.refusal`, which also blocks it when
  the configuration will not load), the handler (`tools.call` checks again before it runs, so a hook that failed open
  lets nothing through). The check is the one the other writes use and fails closed: when it cannot tell whether the turn is off the record, the move is refused ("couldn't check"). The refusal says the
  location and timezone were not changed and suggests saying it on the record; the skill tells the companion the same.
  This reverses WP8's "unchanged" for this one tool (WP8's open point and the PRIV2 note above recorded it as left
  alone). Not changed: the web app's own Settings location change (`serve/writes.py`), which is his
  deliberate act in his own app, not a conversation turn.
- **SERVEPORT (2026-10-20) — the site can sit on a non-default HTTPS port, named by `serve.public_port`.** `journal
  serve` behind `tailscale serve` is published at `https://host.tailnet.ts.net:8790` (port 443 on the host
  belongs to another proxy). `host_ok` already takes any port, but the write `Origin` check allowed only
  `https://<serve.host>`, so every browser write was a 403, and `journal serve pair` printed a link with no port. A new
  optional setting, `serve.public_port` (1-65535; absent or 443 means no port in URLs), makes `auth.site(cfg)` return
  `https://<host>[:<port>]`, and the write `Origin` must equal that string when `serve.host` is set: no other port,
  no `http://`, no port borrowed from the request's `Host` header (the first design, dropped: the explicit setting
  gives the check one configured truth instead of a client-supplied value). An unusable value is named by doctor and
  treated as absent. With `--dev` and with no `serve.host`, nothing changes.
- **PWA (2026-10-20) — the layer WP11 removed is back, on the owner's correction** (yes to the PWA layer and a mobile
  UI; only a native app was ruled out). Row 10
  (the 2026-10-19 "web app, not a PWA") is marked superseded by row 11; FRONTEND.md §4 (offline) and §5 (mobile),
  WORK-PACKAGES §WP11/§WP12 and the docs that said "PWA" are as before WP11's scope change, plus what was built.
  Choices made on the way, for a reviewer to revisit:
  - **One allow-list, in code that runs without a browser.** `web/src/swCore.ts` takes `fetch` and `CacheStorage` as
    arguments, so the rules (what is kept, what never is) are vitest cases and the worker (`sw.ts`) only wires events.
    An unlisted `/api` route is not intercepted at all: nothing is kept by default. Kept: `today`, `entries`, `entries/<stem>`,
    `days`, `library`, `library/<id>`, `reflections` (FRONTEND.md §4 had `days*`, `library*`, `reflections*`; `insights/weeks`,
    `runs`, `settings`, `status` and `doctor` are left out on purpose: the Insights view is the one read view that does
    not work offline, and the others hold settings or server state). Network first with a 5 s limit (FRONTEND.md said
    stale-while-revalidate: a phone on a poor link should show what is true when it can, and the kept copy is said to be
    old); the kept copy also answers a 5xx (a restart behind the proxy), and a late answer is kept for next time.
    7 days and 200 entries as designed.
  - **Audio.** The brief's "never cache `/media` of audio larger than a cap": a cap of 2 MiB, only a whole `200` with a
    `Content-Length`, at most 20 files, and a `Range` request (what an audio element always sends) goes to the server
    and is never answered from a copy. In practice a long recording is never kept; this is deliberately conservative, not
    FRONTEND.md's old "cache-first, 50 files". Revisit when a recording needs to play offline.
  - **Auth.** The cookie is `HttpOnly`, so the worker cannot see a session; instead nothing it keeps outlives one: a 401 on
    a kept route, any `/logout` request (cleared before it is sent, so a failed logout still leaves nothing) and the page's
    own `setAuthLost(true)` (`auth.ts` -> `offline.clearOfflineData`, which also messages the worker) delete every cache
    named `journal-*`. A kept answer is per browser profile, as FRONTEND.md said. Not done: clearing on `journal serve token
    --rotate` (the next read that 401s does it) and a cap on what an unpaired browser could hold before its first 401.
  - **Review fixes (2026-10-20).** (1) *Rollback needs a worker that answers.* A 404 on `/sw.js` leaves an installed worker in
    place (checked in Chromium), so `deploy/killswitch-sw.js` is what a rollback to a build without a worker serves as
    `/sw.js` (DEPLOY.md); it clears all caches, unregisters itself and navigates the open pages. (2) *The cache has a
    generation.* `clearData` bumps a counter; a read in flight when it ran is not stored when it lands (also the timeout
    branch's late store and audio), and the logout clears again after its request. (3) *A late 401* (after the 5 s limit,
    when the kept copy was shown) empties the caches and the worker posts `AUTH_LOST` to the open pages, which mark the
    browser unpaired. (4) *"Update available" rides at the top on a phone* (the foot is the sticky composer's and the
    bar's) and has a Later button.
    Cross-review (Codex, on the PR): (5) a *ranged* media 401 (the audio element's own request) now clears the caches and
    notifies like any other; (6) the `AUTH_LOST` notice is awaited inside the deferred work, so the worker is not stopped
    before it is out; (7) the "offline, as of" ribbon aggregates the latest answer of each read of the view (oldest
    copy wins) instead of the last answer to arrive; (8) on a first visit the views read again through the worker once it
    takes control (`controllerchange`), so what was on screen is kept without a reload.
  - **Update flow.** `registerType: prompt`: a new `sw.js` waits, `Update available` calls `SKIP_WAITING` and reloads when the
    new worker controls the page. The page checks every 30 minutes and when it becomes visible. First install claims the
    page (`clients.claim()`) so the first visit works offline after one reload.
  - **Server.** `sw.js` and the manifest go out `Cache-Control: no-cache` like every static file (a test pins it);
    `worker-src 'self'` and `manifest-src 'self'` are in the CSP; `Service-Worker-Allowed` is not sent (the worker is at `/`,
    scope `/`, nothing wider is wanted); the manifest has `id`, `start_url` and `scope` `/` and relative icon paths, so no
    port or host is hardcoded (a test pins it), and a bare `https://host.tailnet.ts.net:8790` installs.
  - **Icons** are made from `web/public/icons/mark.svg` by `npm run icons` (`@resvg/resvg-js`, a dev dependency) and
    committed with the SVG, so a build never rasterises and CI has nothing to diff; the favicon is the same SVG (it was a
    data: URI).
  - **The phone pass is measured.** `web/e2e/mobile.spec.ts` checks, at 390 px on every route, that every control (a link inside
    a sentence excepted, WCAG 2.5.8) is at least 44 x 44 px and nothing scrolls sideways, that the composer sits above the bar,
    and that the CSS honours all four safe-area insets. The run screen is drawn from the pinned `run-one.json` fixture
    because the fixture server has no run in progress and starting one would change what the smoke tests read. Not
    measured: a real phone (iOS standalone mode, the home-screen install), which the DEPLOY section lists.
  - **Drafts stay in `sessionStorage`** (FRONTEND.md decision 3): an installed app keeps them until it is closed. A recording
    made offline stays in the page only and is lost with it; the composer says so.
  - **Not done, raised for later:** background sync of an offline write (FRONTEND.md ruled it out for v1), an IndexedDB draft
    that survives closing the app, and offline Insights.
- **MCP (2026-10-20) — `journal mcp`, a fourth face, mirrors the engine and adds no logic.** `journal/mcp/handlers.py`
  is one stdlib function per tool over what the engine and `journal serve` already do; `journal/mcp/server.py` is the
  only module that imports the SDK. The rules it follows are serve's (WP9, WP10): the import fence
  (`tests/test_mcp_fence.py` reuses `test_serve_fence.FORBIDDEN`, and also forbids `agent` and `tools`), every tool run
  with `sqlite3.connect` made to fail (`test_mcp.PrivacyTest`), his words appended only through
  `capture.capture_web` under the ledger lock with the five-second deadline (a busy lock is a refusal, nothing
  written), `/x` and the engine's marker refused on the text itself. Like the web, this face never reads the chat, so a
  bare-`/x` window open in Telegram does not hold an MCP save (WP10's rule: the command itself is what holds).
- **MCP — the SDK is `mcp` 2.x (`MCPServer`), the extra `journal[mcp]` = `mcp>=2,<3`.** 1.x's `FastMCP` is not
  supported (2.x renamed it; one major is one thing to test). CI runs `tests/test_mcp.py` and the fence a second time
  with the extra installed, so the SDK's own dispatch (`MCPServer.call_tool`, `list_tools`, `read_resource`) is
  exercised on 3.11 and 3.12; without the extra those tests skip. A refusal is raised as the SDK's `ToolError`, whose
  message the client sees; any other exception is a crash the client sees only as "Error executing tool".
- **MCP — the writes and their names.** `capture.capture_web(via=…)` (default `web`, byte-identical for the web app):
  an MCP save is `source: "mcp"`, `via: "mcp"`, rule `mcp` for an open prompt's reply (the Telegram prompt folds, as
  for the web), `/j` for a note; the entry's heading says `text`, as for every typed reply. The text is written as
  given inside; its outer whitespace (leading spaces of the first line, trailing blank lines) is trimmed, as every
  capture path trims it (`capture._web_record`'s `text.strip()`). `journal_save` without a
  `practice` is always a `/j` note: an MCP client cannot see which prompt is newest in his chat, so "the next message
  answers the visible prompt" (capture rule 2) is not guessed at. `journal_add_note` is `capture.add_companion_note`,
  the companion's own tool, with the same `date`/`kind` rules, and two refusals of this face's own: a note that starts
  with the off-the-record command (this face cannot see a chat turn's state, so the text is all it has; the server
  instructions say so too), and a note whose target is a page of his own that only looks like an entry (a
  hand-written `2026-10-13-evening.md`): the web app answers 409 `not-engine-entry` to a write there, and so does
  this face (`add_companion_note(check=…)`, run under the ledger lock). A day with no entry at all still gets a
  companion-started note entry. The plugin's `journal_add_note` keeps the older "or day" fallback (the WP2 open
  point stands); closing it in `capture.add_companion_note` is a plugin change, not made here.
- **MCP — `journal_start` sends practices only.** It resolves the word as `/start` does (`commands.start`: ids, the
  legacy aliases, `enabled`), then spawns `journal send <id>` (`serve.writes.spawn_journal`: detached, output to
  `manual-trigger.log`, never to the protocol's stdout). A program or situational id is refused with the `/start`
  line to send: a run moves on his own words from the chat or the web app (PRACTICES.md decision 9), and an MCP
  client is a model acting for him.
- **MCP — what the reads return.** `journal_today` is `today.render_data(today.collect(…))`, answered even when
  `today.enabled` is off and not cut to `today.max_chars` (that switch and budget are the chat's injection; a block cut
  to nothing must not read as an empty day); only a day with no line says "nothing recorded yet today". `journal_entries` is `/entries` (EntrySummary; `serve.api.filter_entries`, factored out of the
  route, so `q` is his words only). `journal_entry` renders `serve.document.document` as text: a label line per block
  (`**<his name> (his words)**` / `**<her name> (companion, not his words)**`, with time, source and phase), every line
  of the block's text behind `> ` so nothing inside a block (her reply included) can pass for a label; the prompt (its opener, attributed to
  her when she wrote it, and the questions the ledger says were `asked`; the practice's current bank only when nothing
  is recorded as asked, under a heading that says it is not necessarily what was sent) and the summary are labelled as
  the journal's, annotations and notes as hers; the line of hers a message of his answered (`reply_to`) is quoted and
  led by "(replying to <her name>'s message:)" above his label, never under it. `as_json` returns the document itself.
- **MCP — configuration per call.** `journal mcp` loads the configuration at start (a broken one exits 2, as every
  command) and the tools call the loader again on every call, so a timezone move or a new practice is seen without
  restarting the client. Only stdio is served; streamable HTTP would need serve's auth story and is not built.
- **SETTINGS (2026-10-20) — one settings function for every face; the serve unit can write the config file's
  directory.** The owner asked that settings be changeable from every face. `settings.apply` is the one function: the
  allow-list (`config.WEB_WRITABLE`), the ranges (`config.web_patch`), which layer owns a key
  (`config.check_writable`), the place lookup, and the atomic write (`config.patch_values`), refusing with a
  `SettingsError` whose `code` is the API's error code. The web app's `PATCH /settings` maps the codes to statuses
  (400 / 422 / 409 / 503, unchanged); `journal config set` and `hermes journal config set` map the same codes to exit
  codes (2 for a key or value not allowed, 1 for a change that cannot be made here). A table in
  `tests/test_serve_writes.py` runs the same changes through both and asserts they agree. `journal config list|get|set`
  (also under `hermes journal config`) are these: `list [--json]` gives every allow-listed key with its value in force,
  the layer that owns it (`file`, `hermes`, `env`, `default`) and whether it can be changed; `get` reads only
  allow-listed keys or groups (so no token, path or command is readable this way); `set KEY VALUE` takes JSON or, when
  it is not JSON, plain text (`Europe/London`; `'"5"'` is the string 5). The layer is the top-level key's, which is
  what a write replaces (`windows.collapse_hours` shows `file` when the file has any `windows` block), and `default`
  means a save writes the key into the config file.
  *Hermes-owned keys are never written by any face.* The command line prints the line to run, with the value:
  `hermes config set plugins.entries.journal.settings.windows.collapse_hours 5` (shell-quoted; `--json` carries it as
  `command`), exit 1. This is the web app's 409 and keeps "the companion never edits Hermes settings" true of the
  `hermes journal` face as well (it passes no writer). Under Hermes with no config file in use every key is
  Hermes-owned in this sense (a file made at the standalone path would be one nothing reads), so the command is the way.
  *Superseded 2026-10-26 by SERVE-UNSANDBOXED: the unit has no sandbox and no `ReadWritePaths`, so the widening,
  "which directory is added" and the `--print-unit` fix in the `503` below are gone; a save needs the directory
  writable by the service user.* *The serve unit now lists the config file's directory in `ReadWritePaths`* (when a config file is in use). WP10 left
  the unit alone and answered `503` for a save, and on the deploy host that meant the Settings page could not save: the
  sandbox (`ProtectHome=read-only`) refuses the temporary file `write_json_file` makes beside the config file before
  renaming it over (it is the directory that must be writable, not only the file). Reasoning for widening it: the only
  code in this repository that writes there is `settings.apply`, which accepts the keys on the allow-list and nothing
  else (not a token, a path, `serve.*`, `telegram.*`, `guard.*`, `hermes.*` or `agent.*`); it is reached only with the
  serve token and `serve.allow_write`; the file is replaced whole and atomically; and the unit's other limits stay.
  *What the widening costs (the owner's call; found by review, not by the first design).* The unit sandbox is inherited
  by everything it starts. "Send now" (`POST /library/<id>/send`) runs `journal send` inside the unit
  (`writes.spawn_journal`), and that runs the opener through `agent.ask` with the companion's full toolset
  (`tick.py`). So a model run for an opener now executes inside a sandbox that can write the config directory, and the
  config file holds keys that are commands: `telegram.token_command`, `serve.token_command`, `sources.*.command`,
  `hermes.command`, `agent.command`, `audio.command`. The cron tick and the gateway run those outside any sandbox.
  Before this change `ProtectHome=read-only` stopped such a write whatever the model did; now the only thing in the
  way is the guard plugin (`guard.py`), which says of itself that it is a guardrail against a "helpful" model and not
  a security boundary. No agent was run to watch it write; this is the reading of the code. The guard now also refuses
  `journal config set` from the companion's terminal (the new CLI is a write path the path check cannot see: no path,
  no write token in the command), and `write_file`/`patch` on the config file were already refused. The alternative
  that keeps the unit narrow is not taken here: store the web-writable settings as an overlay file in `state_dir`
  (already writable) and have `load()` merge only the `WEB_WRITABLE` keys from it, so the overlay can never carry a
  command and `ReadWritePaths` stays as it was; it costs a fourth settings layer (and a place in `origins` and
  `hermes journal config`) and moves where `journal config set` writes. Until the owner chooses, the widening is only
  in effect where the unit is re-rendered and installed by hand (DEPLOY.md, SETTINGS), and declining to re-render
  is the way to keep the old, narrower sandbox (saves then answer `503`).
  *Which directory is added.* Only one that is the config file's own: it is `.config/journal` (the
  XDG convention; a directory merely called `journal` is not enough, a project's holds its source), or it exists and holds nothing but the config file and a save's temporary files
  (`.<name>.XXXX`). Never the home directory or above it, `~/.config`, `~/.local`, `~/.cache` (`~/.config/systemd/user`
  is where a written unit would run outside the sandbox), anything under `~/.config/systemd` or the Hermes data root
  (profile `config.yaml`, `.env`, `state.db`), nor a directory with other files in it; the path is normalised first (a
  `..` in it made systemd drop the entry). Then the unit carries a comment saying so and the page reports the save as
  refused with the `ReadWritePaths` fix. A config file inside a directory the unit can already write adds
  nothing. A save is checked before anything is written or looked up: if the config file's directory is not writable
  the answer is `503` naming the directory and the fix (`journal serve --print-unit`), and a place is not geocoded for
  nothing; `config list` marks the file-layer keys not writable in that case too. When Hermes owns a key the printed advice is
  complete: one `hermes config set` line per key of a group (`windows` with two values is two lines; `commands` in the
  `--json` output and in the 409 body, `command` the same lines as one text), and for a place all three of
  `location`, `timezone` and `coordinates` (the lookup sets them together; the label alone would leave the schedule
  where it was). Under a named profile the lines say `hermes -p <profile> config set ...` (pasted without `-p` they
  would change the shell's default profile; `GET /settings` carries `profile` for the page's own line). `config get` of a group
  is cut to its allow-listed keys. Under Hermes with no config file the `layer` of an unset key is `hermes` (and the web app's `origins`
  say so, which locks the Settings field), not `default`. `config get today.enabled` reads the effective `today`
  (`today: false` in a file means off, the rest at its defaults), as the web's `GET /settings` now does. The web
  Settings page shows, under each field, the layer it is in (the config file, or the default that a save writes into
  the config file) and, for a key Hermes or the environment owns, the line that changes it; its "Saved" line names the
  layer written. This supersedes WP10's "The unit is not widened for the config file".


- **TSAUTH (2026-10-20) — on the tailnet, signed in by Tailscale identity; no pairing (the owner's decision).** Opening
  the URL on any of his devices should just work. Choices:
  - **Both listeners, always.** `serve.socket` (an absolute path, at most 107 bytes, the `AF_UNIX` limit) is served
    *in addition to* TCP, never instead: TCP keeps the token, cookie and pairing for anything that is not `tailscale
    serve` (curl, a second proxy, the doctor's `/healthz`), and making TCP optional would be a second change to the
    listener with no use yet. The socket is made 0600 (umask narrowed around `bind`, then `chmod`), its missing
    parent 0700; a stale socket (nobody answers) is replaced, a live one is retried like a busy port, anything else
    at the path is never removed and stops the start; removed at stop (with a socket, SIGTERM stops the server as
    Ctrl-C does so the `finally` runs). Set but unusable: `journal serve` refuses to start (exit 2) rather than
    run TCP-only and leave `tailscale serve` with a 502 nobody explains.
  - **The header counts only on the socket, and only from tailscaled.** `serve.tailscale_users` is a list of logins
    compared exactly (trimmed of optional whitespace). A request on the socket with exactly one
    `Tailscale-User-Login` that is listed is signed in; two copies, a Funnel request (`Tailscale-Funnel-Request`),
    an unlisted or RFC 2047-encoded login, or a value that is not a list sign no one in. Over TCP the header is never
    read, not even from `serve.trusted_proxies`. **Beyond the brief:** the socket being 0600 does not mean "nothing
    else can reach it": on the deploy host Hermes (the companion's agent, with tools) and other agents run as the
    same user as the service. So the header also requires the connecting process's uid, from the kernel
    (`SO_PEERCRED`), to be in `auth.TAILSCALED_UIDS` = `(0,)`: tailscaled runs as root (on the deploy host: `ps` shows
    `root /usr/sbin/tailscaled …`). Cost: a tailscaled run unprivileged (userspace networking as a user) cannot sign
    anyone in; a setting can be added if that is ever wanted. A non-root peer can still use the token or a cookie on
    the socket.
  - **The host itself is not him (found in review).** tailscaled also answers connections from the machine it
    runs on, on its own tailnet address (`ipn/ipnlocal/serve.go`: that listener "exists purely for connections from
    the machine itself"), and `addTailscaleIdentityHeaders` signs them in as the node's owner (`WhoIs` of the
    node's own address is the owner; observed on the deploy host with a one-shot header echo behind an existing
    route). The root-peer check cannot tell: the peer is tailscaled either way. So any process on the host (Hermes,
    other agents, containers, `nobody`) could have been signed in as him with no token. `auth.remote_client`:
    `X-Forwarded-For` (which tailscaled `Set`s from the connection's source) must be exactly one parsable address
    that is not loopback, not unspecified, and not one this host can bind; anything else is no one (fail closed:
    a host with `ip_nonlocal_bind` signs no one in). Processes on the host use the token. Alternative not taken,
    the owner's call: tag the host's node (tagged sources get no identity headers), which changes ownership and ACLs.
  - **Identity is ambient, so it keeps SameSite=Lax's protection (found in review).** A cookie is not sent on a
    cross-site subresource request; an identity sign-in would be, letting any site he visits embed his voice notes
    or time searches over his words. A request with `Sec-Fetch-Site: cross-site` or `same-site` (another port of
    the same host is same-site) is not signed in by identity; the token and cookie decide it (so: 401, not 403).
    Absent header (curl, old browsers) and `same-origin`/`none` are unaffected.
  - **On the socket, `X-Forwarded-*` is believed and the Host check reads `X-Forwarded-Host`**, because tailscaled
    proxies to a Unix socket with `Host: localhost` (`r.Out.Host = rp.url.Host` for socket backends) and sets
    `X-Forwarded-Host` to the browser's Host, `X-Forwarded-Proto: https` and `X-Forwarded-For` with `Set`, replacing a
    client's. A request with no `X-Forwarded-Host` falls back to `Host` and fails a configured `serve.host`.
  - **Writes keep the `Origin` check** (`site(cfg)`, with `serve.public_port`) on both listeners, however the request
    is signed in: identity says who, `Origin` says the page is the journal's own.
  - **`GET /api/v1/session`** (`{"via": "tailscale"|"cookie"|"token", "login"}`) tells the web app how it is
    signed in; Settings shows "Signed in via Tailscale as <login>" without Unpair, and `/pair` goes to Today
    without spending its code. A 401 or 404 there (an older server) leaves the app as it was.
  - **`--print-unit`:** a socket anywhere below `<runtime dir>` adds `RuntimeDirectory=<its directory, relative>`
    (`journal-serve`, or nested `journal/serve`), `RuntimeDirectoryMode=0700` (systemd makes it writable under
    `ProtectSystem=strict` and removes it at stop); a socket under `/tmp` or `/var/tmp` is refused with exit 2
    (`PrivateTmp=yes` gives the service an empty /tmp, so the `ReadWritePaths` entry does not exist and tailscaled
    cannot see the socket; doctor cannot tell whether the server runs under the unit, so only `--print-unit` says
    so); a
    socket under a path already in `ReadWritePaths` needs nothing; elsewhere its directory joins `ReadWritePaths`.
    Without a socket the unit is byte-identical (the example's golden test is unchanged). *Superseded 2026-10-26
    (SERVE-UNSANDBOXED): with no private /tmp and no `ReadWritePaths`, only the `RuntimeDirectory=` rule stays; a
    socket anywhere else only has its mount gated. The server, not the unit, now refuses a socket directory that is
    not the service user's own or that others can write.*
  - **Doctor and `serve check`:** `serve socket` (fail when a non-socket file is in the way, warn on a wrong mode or
    owner) and `serve identity` (warn when users are listed but there is no socket), shown only when either setting
    is set, so existing installs' doctor output does not change.

  *Evidence (2026-10-20, the deploy host, read-only; Tailscale config not changed):* `tailscale version` 1.98.10 (commit
  `0ee734d3089846b27bc6ebcddd3d6ee5ec13e04d`). `tailscale serve --help`: "On Unix-like systems, you can also specify a
  Unix domain socket (e.g., unix:/tmp/myservice.sock)", example `tailscale serve unix:/var/run/myservice.sock`, and
  `--https value  Expose an HTTPS server at the specified port (default mode)`; so `tailscale serve --bg
  --https=8790 unix:<path>`. The help does not name the identity headers; the installed `/usr/sbin/tailscaled`
  contains the strings `Tailscale-User-Login`, `Tailscale-User-Name`, `Tailscale-User-Profile-Pic`,
  `Tailscale-Funnel-Request`, `Tailscale-Headers-Info`, `Tailscale-App-Capabilities`. The source at that commit
  (`ipn/ipnlocal/serve.go`, fetched with `gh api`) shows `addTailscaleIdentityHeaders` first deleting
  `Tailscale-User-Login`, `-User-Name`, `-User-Profile-Pic`, `-Funnel-Request` and `-Headers-Info` ("Clear any
  incoming values squatting in the headers"), then setting `Tailscale-Funnel-Request: ?1` and nothing else for a
  Funnel request, nothing for a tagged node or a source that is not a tailnet peer, else the login (`LoginName`,
  RFC 2047 Q-encoded when not ASCII), display name, profile picture URL and the info link; `proxyHandlerForBackend`
  handles `unix:` targets, refuses tailscaled's own socket, and its `Rewrite` sets `Host` to `localhost` for a
  socket backend and `X-Forwarded-Host`/`-Proto`/`-For` with `Set`. tailscaled runs as root
  (`systemctl cat tailscaled`: no `User=`, no sandboxing), so it can open a 0600 socket in `/run/user/<uid>/`.
- **IMAGES (2026-10-20) — pictures are kept like audio: flat, named after the entry, embedded in his block.** The
  owner's spec: pictures are kept like audio, named after the day in the same flat structure, and a picture sent in
  answer to a journal prompt is linked in that entry's file. A photo, or a document that is a jpg/jpeg/png/webp/
  heic/gif (`images.IMAGE_TYPES`; never svg), is copied into the journal root by `images.keep`, which is `audio.keep`
  with a size check: **the name is the one audio uses**, `<entry-stem>.<ext>` then `-2`, `-3` on collision (claimed
  with `O_EXCL`), not the `<stem>-HHMM` the task text offered as an example; the same stem and a counter keeps
  `web/media` confinement (`references()` reads only that day's entries) and the Obsidian look of audio, and the day
  is already in the name. A picture and a recording of one entry never collide (different suffix). The kept name is
  embedded as `![[name]]` between the block heading and his caption; several pictures of one message (an album) are
  several embeds. **The heading says `photo`** (`### 14:02 IST · photo`, `**14:02** · photo` in the conversation) so
  the file reads as it is and `serve/document.py` can tell it; a picture that could not be kept leaves an ordinary
  `· text` block with the caption. His caption is passed to the entry exactly as audio's caption is: verbatim, and the
  embed is the engine's line (`entries._answer_blocks` and `conversations` already leave `![[…]]` lines out of his
  words). A captionless photo to a prompt is an answer: a block with the embed and no words (program phases already
  counted such blocks, `programs._visit_blocks`).
  *Where it lands.* A photo that answers a prompt, a `/j` caption or a program phase is kept and written at arrival
  (`inbound._image`, before the companion sees it; a program phase also spawns the settle). A photo in ordinary chat
  is stashed by the hook (`conversations.stash_images`, in the same `audio-stash/`, kept 3 days) and the conversation
  writer keeps it beside the entry the thread lands in or starts, keyed by message id so a replay keeps it once;
  its row is Hermes' image placeholder plus caption (text mode), the caption alone (native mode: no placeholder is
  added) or empty (native, no caption), which `classify` takes as his message when the hook stashed a picture for it
  (a captionless photo with no stash is still nothing). *Off the record* is decided first in
  `receive`, as for audio: a `/x` photo, a photo in a bare-`/x` window and a bare `/x` with a file attached are never
  copied, stashed or embedded; the failure path of that check stays closed (PRIV2). A `/j` photo inside a window
  is kept like any `/j`.
  *Size and failures.* `images.max_mb` (default 20, a bad value means 20 and doctor says so): a larger picture, or one
  that cannot be copied, is logged and left out; the caption is saved. The hook fails open for capture as everywhere.
  *Not done, said plainly.* (1) The busy-path sweep does not keep pictures: a photo that reached Hermes while the
  companion was mid-turn skips `pre_gateway_dispatch`, so its caption is captured as before and the picture is not
  (as a swept voice note keeps no recording; README "Known limits"). (2) The web app's composer sends no pictures
  (uploads stay audio-only, `serve/uploads.py`): this package is the Telegram path plus showing them. (3) A document
  that is not one of the kept kinds (a `.dng`, a PDF) is handled as the text it was.
  *Describing (`images.describe`, off).* With it on, the capture is marked `describe: "pending"` and the tick's
  `images` step (after `audio`) asks the companion with `toolsets="none"` and the picture attached for one line.
  `Companion.ask` takes an optional `image` (`hermes chat --image`; an `agent.command` takes `{image}`); a companion
  that cannot take one answers None rather than a description of something unseen. The answer must be one line under
  200 characters with no quotation marks, headings, embeds or Hermes failure copy (`images.valid_description`);
  it is added with `entries.add_companion_note` as `_Photo <name>:_ <line>`, under her notes, never in his block, and
  is outside `entries.his_words` (so it is not his, not searched, not counted). Two bad answers end it
  (`describe: "none"`); an unreachable model defers it (30 minutes, 7 days, no attempt used) with no alert, since it
  is a nicety. The picture goes to the companion's model only when he turned the setting on.
  *Web.* `/media` serves the names `audio.media_type` or `images.media_type` accepts, still only when an engine entry
  embeds them (ranges only for audio); the document's block gets `images: [names]` (absent when none; read only from
  the embeds a block opens with, so a picture further down in his own text stays his text) and the web app shows them
  above his words, each opening full size. The change touches `web/`, so the committed build is the CI artefact of
  this branch.
- **IMAGES review (2026-10-20) — what the independent review changed.** (1) *Native image input* (`decide_image_input_mode`
  gives it for a vision-capable main model) stores no placeholder, so a photo with no caption is an empty row:
  `classify` checks the stash before dropping an empty row. (2) *Hermes' picture notes are read by shape.*
  `transcript.caption` skips each `[The user sent an image~ … ~]` / `[The user sent an image but … ]` note up to its
  own fixed trailer (the vision description may hold `]` and blank lines, which the first-blank-line rule took for
  the end), so an album keeps his caption and no description is written as his words; other placeholders (documents,
  video) still end at the first `]` + blank line. (3) *A redelivered `/j` photo* returns the existing note's rewrite
  (`inbound._image`), as a redelivered text `/j` does, instead of reserving an empty note and passing. The audio `/j`
  path keeps its pattern. (4) ***/x on a later line of an event*: raised and taken.** Hermes joins an album's captions
  and messages sent within a moment into one event, so `/x` on item two sat mid-text, where the command is never read.
  `capture.opt_out_inside` takes any later line starting with the opt-out command as off the record for the whole
  event (the inbound hook, the privacy pass's `row_marked` and `classify` agree); nothing is kept or written. The
  cost, named: a long message of his with a line that starts with `/x` is not saved at all, not even its other lines.
  Text-only messages are covered too, since the weakness was the same. (5) *HEIC* is kept and embedded, but Hermes'
  `--image` takes no `.heic` (`images.DESCRIBABLE`): the tick skips describing it (an unreachable-model deferral would
  have stalled the other pictures for 7 days), and an entry with nothing describable settles as `describe: "none"`.
  Most browsers cannot display HEIC in `<img>` (Safari can); the file is kept and served, and a viewer or download
  link for it is not built here. (6) The document lifts leading image embeds into `images` only for `· photo` blocks;
  a text block that opens with an embed he or Obsidian wrote reads as written.
  *Cross-review (a second model, 2026-10-20).* Three more changes, all small: the conversation writer finds a
  message's kept picture names before looking at the stash (a commit drops the stash, as it does for audio, and the
  record stays); a bare `/j` photo that cannot be kept (over `images.max_mb`) takes its reserved note back instead of
  leaving an empty one in the ledger (`capture.release_note`); and an outage midway through an album saves which
  pictures were already described, so a retry asks only for the rest.
  *Cross-review by Codex, then (same day).* The privacy pass read `/x` only in `his` rows, but Hermes stores an image or
  document with a caption as a `system` row (the placeholder is the row; the caption is his typed text,
  `meta["caption"]`), and the pass skipped such rows: sent on the busy path, where the hook never sees it, a
  `/x` caption (or a `/x` on a later line of a merged album) left the row public and her reply with it, and the
  readers of `meta["loose"]` could take it into a later prompt. The pass now reads the caption of such a row as his
  words (`privacy._said`), for the marks and for the window; a bare `/x` caption is one message, not a window, as at
  the hook. **Raised against the golden corpus:** `tests/golden/hermes_rows.json` pinned the old behaviour for the
  `[The user sent a document: 'a.txt']\n\n/x …` row (`marked: false`); only that row and its `private` entry
  changed (`marked`, `marked_by_id`, `compute.private` + 38), since the pinned behaviour was the hole. Also: a
  caption of his that opens with an embed of another file stays his caption (`serve/document._pictures` lifts only
  the names the entry gives its pictures, `<stem>.ext`, `<stem>-N.ext`); and `agent.ask` with a picture on a
  companion that cannot take one returns None without marking the model as having answered (it ran nothing, so
  it is no sign an outage is over for `recovery`).




- **WEBEDIT (2026-10-26) — the sacred-ground line in `AGENTS.md` (and `CLAUDE.md`, its symlink) amended.** The owner's
  reason: AGENTS.md speaks to the agents building the engine, so it states the engine's rule: "His words are append-only
  for the engine. No code path, model or program rewrites a block he wrote; only he changes his own blocks." The old
  "no … form rewrites" read as forbidding the web edit of Decision 4.
- **WEBEDIT (2026-10-20) — editing and deleting his own blocks from the web app (Decision 4, amended).** `PATCH
  /entries/<stem>/blocks/<key>` replaces the words of one block of his; `DELETE` removes it (its hidden key line,
  heading and body). `<key>` is the document's own block key (`words:N`, `his:<id>`, `conv:N`; `serve/document.py`),
  found by the same parser the document uses (`document.block_parts` gives each block's spans), so the block he edits
  is the block he was shown. What the rest of the file keeps is byte for byte: the other blocks, the engine's hidden
  key lines, the heading and time of the edited block, its audio embed, a `↩ replying to` callout, her callouts,
  the annotations, notes, summary and front matter. `serve/blocks.py` does the splice and then checks itself: the new
  text is parsed again and must show exactly the blocks the old one did (the edited one with the new words; a delete,
  the others), the same annotations, notes, summary and front matter; otherwise `422 ambiguous-text` and nothing is
  written (words shaped like a block heading, or an audio embed in a block that has none, would be read as something
  else).
  - *Locking and drift.* The write runs under the ledger lock, like capture, the conversation writer and the tick
    (423 after 5 s), and is atomic (`entries.write_file`). The request must say what he was looking at: `was` (the
    block's words as read; `at` its time) and/or `mtime` (the entry's). A block that reads differently now, or an entry
    whose mtime moved, is `409 changed` ("reload") and nothing is written. The web app sends `was`/`at`, not `mtime`:
    the tick writes annotations into the same file a few minutes after he writes, and a whole-file `mtime` would have
    refused the first edit of most entries. The cost: two blocks with the same words and time are not told apart if
    his keys shifted under him (another tab removed a block before them); `mtime` alone, or both, closes it.
  - *What is refused.* Her blocks (`409 not-his`); a block whose recording is still being transcribed (`409
    transcribing`: the tick fills it in); a block with one of the engine's marker lines inside it, or words above its
    recording (`409 not-editable`: the document shows both as one text and an in-place splice cannot tell); an empty
    edit (`400`: delete the block instead); a `%% journal … %%` line in the new words (`422 reserved-line`, as for a new
    block). `/x` holds here too (review finding, 2026-10-20): words that begin with the opt-out command are `422
    off-the-record` and nothing is written, as for a new block. The first design let it through ("words already
    saved"), but a stored block that begins with `/x` is read as off the record and still reaches `his_words()`, the
    annotation input and search. No stored block can begin with `/x` (capture refuses or strips it), so this never
    blocks re-saving a real one; a block he wrote by hand in his notes app that does begin with it can be deleted but
    not saved as is.
  - *Delete.* A block of his that answers a program's phase or interrupt can be edited but **not** deleted (`409
    program-answer`): the program pairs each phase's blocks one for one with the captures that made them
    (`programs._visit_blocks`), and a missing block would make it read nothing for that visit. Any other block can go,
    including the only one (the section is left empty and still takes a new block). Deleting a block does not delete
    the recording it points at (it stays on disk, unreferenced), does not remove her reply to a message of his, and
    does not touch the ledger (the capture record stays; the annotation the companion already wrote stays and may
    quote what is gone).
  - *Photo blocks (after the images package).* The block is the pictures the engine put first and his caption after
    them. An edit changes the caption only: the picture lines are never in the span that is replaced
    (`document._pictures_end` is the same walk as `_pictures`), and the re-parse check would refuse (`422
    ambiguous-text`) a caption that opens with an embed of the entry's own picture names. A photo with no caption can
    be given one. Delete removes the heading, the picture lines and the caption; the image files stay on disk, like a
    recording.
  - *Program session entries.* Editing a phase answer is allowed. The `## Summary` of the session is code's: it is
    filled from his words by `programs._write_summary` when a session completes (and again if an audio answer was
    still being transcribed). *Decided 2026-10-26 (SUMMARY-REFRESH, below): an edit of a phase answer after the
    session completed has the summary written again from the edited words.*
  - *History.* Plain edit; the old words are not kept. History can be added later (a `<stem>.history/` sidecar, or the
    previous text in the response for an undo) without changing these routes; Obsidian's own file history, or whatever
    backs up the journal folder, is what covers it today.
  - *Web.* The Entry view shows **Edit** and **Delete** under each block of his on a page the web app may write to
    (`writable`); Delete asks once, in place, and is not offered on a program answer. The editor stands in the place
    of the words; his unsaved edit is kept in `sessionStorage` (as a composer's draft is) until he saves or cancels.
    The draft is kept with what it was an edit *of* (the block's words and time): it comes back into the box only
    while the block still reads exactly so (a 409 and a reload of an unchanged block, a closed tab). When the block
    has changed since, or another block now stands at that position (one above it was deleted), the old words are
    never put into the editor: they are shown apart as text to copy or let go. Save always sends the words and time
    the editor was opened on, and each block is keyed by key, time and a fingerprint of its words, so an open editor
    closes when the block under it changes instead of moving to another block. On a document read from the offline
    copy no Edit or Delete is offered; offline, Delete is withheld and the editor keeps the box, says "You're
    offline; this will not be saved until you're back" and does not send. A recording still being transcribed has
    neither action.
- **SUMMARY-REFRESH (the owner, 2026-10-26) — an edit of a phase answer in a completed session rewrites that session's
  `## Summary` from the edited words.** Closes the open point "WEBEDIT — an edit does not refresh a completed session's
  `## Summary`" (it was between leaving the old words and calling `_write_summary` after the edit; he chose to refresh).
  Choices made:
  (1) **Spawned, not run in the request.** `PATCH …/blocks/<key>` spawns `journal program refresh-summary <run>
  --session N` after a successful edit, like every other program step. The command takes the tick's lock
  (`programs.locked`), reads the run again, and calls `programs._write_summary` (`programs.refresh_summary`), which
  takes the ledger lock for the write, in the engine's order: tick lock, then ledger lock. The web request never takes
  `tick.lock` (a documented property of `serve`, and the edit already releases the ledger lock before the spawn, so
  there is no second lock to take out of order), and it still answers at once. Running it in the request would have
  had to either take `tick.lock` (the tick can hold it for minutes) or run unlocked against `complete_session` (an edit
  landing between its read of the answers and its mark of the session as completed would leave the old words).
  Under the tick's lock the command is serialised with `complete_session` and `_refill_summary`: it sees the session
  completed with the words as they stand, or not completed, when completion will read the edited file.
  (2) **What triggers it:** a block that carries a `phase` (an answer to a phase of the program), in an entry whose
  front matter names a run, in a session of that run with status `completed`. The API reply then has `"summary":
  "refreshing"`; `"not-started"` if the spawn failed (the edit is saved; the summary keeps the old words until the
  next `write_summary`); no `summary` key otherwise. The command decides again under the lock, so a session reopened
  by `/back` or a re-run meanwhile is left alone.
  (3) **What does not get a refresh, and the command that is sent anyway.** A block that is not a phase answer (a
  conversation message, a note: the summary is built from phase answers only) sends nothing. A session still open
  (its summary is written when it completes, from the words as they are then) and a completed session whose
  `summary_audio_pending` is set (`_refill_summary` writes it when the transcript is in, from the words as they are
  then) get no promise in the reply, but the command is spawned for them all the same and does nothing unless the
  session is due once it holds the tick's lock. Both gaps are real: `complete_session` reads the answers and only
  then marks the session completed, and `_refill_summary` renders from the answers and only then clears its flag, so
  an edit saved in between is seen by the request as an open or a waiting session while the summary written a moment
  earlier lacks it (CodeRabbit and the independent review of SUMMARY-REFRESH; tested by injecting the edit inside
  `complete_session` and inside `_refill_summary`). Deleting an answer stays `409 program-answer`, as before.
  (4) **What it touches, and when it refuses (fail closed).** The `## Summary` section of the entry (the summary text
  and, when the session has one, its document), through `entries.write_summary`, the one writer of that section: the
  mark `%% journal:summary %%` stays, so it remains the engine's own section and not one of his blocks (readers end
  his words at the mark); his blocks, the conversation, the annotations, notes and front matter are carried over
  byte for byte (the test compares them). `entries.write_summary` replaces everything from the mark to the next `## `
  heading, so a refresh could delete words he typed or moved there in his notes app; a fresher summary is never worth
  that. The refresh therefore replaces the section only if it is **byte for byte what the engine last wrote**: the
  engine records a digest of the section (`summary_sha` on the session in the run file) whenever it writes it, at
  completion, at the refill and at a refresh, and the refresh compares the section in the file, read under the ledger
  lock, with it. It leaves the entry as it is, and says so in `manual-trigger.log`, when: the section differs (he
  added to it or changed a word of it); the mark is not found (it creates no section: only completion does, so a
  summary he deleted stays deleted and one whose mark he removed is not written beside); a block of his sits under it
  (`entries._stray_blocks` finds anything: a summary he moved between his answers); or there is no digest, which is
  every session completed by code from before this change. **Those never refresh**: the answers as they were before
  the edit cannot be got back from the entry, so what the engine wrote cannot be rebuilt and compared, and a guess
  could replace words of his. Nothing is migrated: sessions completed before this change keep no digest. If it is
  ever needed, a one-time adoption at deploy is possible: store the digest on a completed session where the section in
  the file equals what `_write_summary` renders from the answers as they stand then (and only there), and leave the
  rest without one. The digest ignores trailing
  whitespace on each line (an editor that strips it on save must not silently end the refreshes); the write uses the
  exact bytes. The web reply says `summary: "kept"` with `summary_reason` and spawns nothing when the same check
  (read-only, no lock) already fails, so it does not promise a refresh that will not happen; the command checks
  again under the locks. Tradeoff: the **run file is now written** (the digest, by completion, refill and refresh); the
  ledger is taken under its lock and rewritten with identical content (its mtime changes); the first version of this
  decision said neither was written. What he changed by hand in the summary stays as he wrote it, and the summary
  then keeps old words: his words over a fresher summary.
  (5) **What it does not touch:** the Telegram summary message already sent stays as sent (as `_refill_summary` leaves
  it), and so does a compiled insights document (`YYYY-MM-DD-<program>-insights.md`): nothing recompiles it
  behind his back. A later `/start <program> insights` (or `journal program insights`) compiles it again from the
  entries, so it reads the refreshed summary and the edited answers; if he wrote in the old document it is kept and a
  new numbered one is made, and a synthesis already written for the old words is made again for the new ones (the
  corpus differs).
  (6) **Known limits.** (a) The refresh is a second writer of the entry from the web side, but the same function
  under the same locks as the tick's; the spawned command is as reliable as the other spawned steps (a failure lands
  in `manual-trigger.log`, and the tick does not retry it). (b) **Summaries elsewhere are not refreshed:** a later
  session's text that pulls from this answer (session 10's slots such as `s1:5.1`, filled when that summary was
  written; the prose slots in questions, interrupts and the compiled insights document) keeps the words it was
  filled with until that session is completed again or the insights are compiled again. (c) **Coalescing:** a
  command waiting for the tick's lock holds a per-session `refresh-summary-<run>-s<N>.lock` (taken without waiting)
  until it has the tick's lock; another for the session exits quietly, since the one waiting reads the entry later.
  (d) **A restart of `journal-serve` must not kill a waiting command:** systemd's default `KillMode=control-group`
  stops every process of the unit, and `start_new_session` does not leave the cgroup. `journal serve --print-unit`
  and `deploy/journal-serve.service.example` now say `KillMode=process`; the live unit needs the same line, applied
  at deploy (DEPLOY.md). It is true of every spawned send and step, not only this one.

- **CAPMODE (2026-10-20) — `capture.mode`: what the conversation writer journals.** The owner's decision (table row
  12). `config.capture_mode(cfg)` is `everything` or `arm`; the writer (`conversations._Threads._admit`) is the
  only code that differs. Choices made:
  (1) **The conversation that follows a journal reply is saved under `arm`**, into that entry, until the thread goes
  quiet or a prompt goes out, as under `everything`: talking it through is part of answering the prompt. The same holds
  after a `/j` note and a program phase answer (each is a captured message that gives the thread its entry), and a
  journal reply later in a thread takes the rest of it. Only a thread with no captured message is left out: no auto
  note, no `## Conversation` blocks, her replies in it neither.
  (2) **A `/j` the hook and sweep both missed still starts a note under `arm`** (the writer's fallback for a row with
  no platform id): he asked for it to be kept.
  (3) **The hook is unchanged.** It still stashes chat audio that answers no prompt, because it cannot know whether
  the thread will be saved; under `arm` the writer drops the stash of a message it does not journal at that tick's
  commit (not three days later).
  (4) **The sweep's rules are unchanged** (it only ever captured prompt replies and `/j` notes), but under `arm` the
  writer follows it: a message of his the sweep has not passed yet (its row id is past `sweep.json` `last_id`, and it
  is inside the sweep's lookback) is never judged ordinary chat. The walk stops before it and the cursor waits for the
  next tick, so a sweep that failed once, or has not run, costs a tick of delay and not the reply and its thread
  (found in review: the writer's cursor had already passed them). With `sweep.enabled: false` the writer takes the
  sweep's place for such a message: it applies `sweep.reconcile` itself, and so does a write backfill (which the sweep
  never read: a busy-path reply older than its lookback would otherwise be dropped); a dry run captures nothing. The
  thread follows the entry it chose.
  (5) **An unusable value means `everything`** (today's behaviour) with a doctor problem, a non-object `capture` a load
  warning: a typo must not quietly stop his conversations being kept, and `/x` covers what must stay out.
  (6) **The skill is rendered with the mode** through `{{#everything}}…{{/everything}}` / `{{#arm}}…{{/arm}}` blocks
  in `SKILL.md`, which `hermes.render_skill` keeps or drops; the `everything` rendering is byte-identical to the one
  before CAPMODE. Under `arm` she is told ordinary chat is not saved, that `/j <text>` saves something, and that `/x`
  is not needed for ordinary chat but still matters in a saved thread.
  (7) **Doctor shows a `capture mode` row; status a `capture_mode` key and a `Capture mode:` line** (in the web
  status too: it is configuration, not chat state). That line and row are new in `everything` as well; the only
  other change there is (11) below, a fix for a sweep failure, and no golden moved.
  (8) **Under `arm`, his reply to the journal's own words is journal material.** A message that quotes an annotation
  reply joins the entry annotated; one that quotes a day reflection or a week review starts a note (the thread
  follows). The platform stores only the quoted text, so the match is by words against what the journal sent
  (`conversations._Writer.answers_ours`; at least six words of quote, so "thank you" quoted from an annotation is not a reply to it). A quote of anything else is
  ordinary chat.
  (9) **Switching to `arm` mid-thread takes effect at once**: a thread whose entry is a note the writer started from
  chat (`capture: auto`, and not started by journal material under `arm`, which the thread records as `kept`) stops
  being written; what was written stays.
  (10) **Under `arm`, `telegram_history` reads back only what the journal kept** (a capture, or a message the writer
  wrote). A pack is what an opener or an annotation is written from, and that is then saved into the entry
  (`## Prompt`), where the quote checker allows a verbatim quote of `telegram:` lines: with the whole chat in the pack,
  ordinary chat reached the entries through her opener. The quote checker's own corpus is unchanged (it only verifies).
  (11) **A prompt is answered once the writer has made an auto note of a message of his after its send**
  (`sweep.visible_at` / `journaled_between`: the note and the conversation after it; a /j the writer recovered
  (`asked` on its ledger record) and the conversation in an entry a reply went into do not count, the hook and the
  sweep handle those themselves). Found in review, in `everything`: after a one-tick sweep failure the writer filed
  his first message into an auto note, and the next sweep took his second as the prompt's answer (in two entries,
  and the wrong answer).
  (12) **`sweep.lookback_hours` is read in one place** (`sweep.lookback`; an unusable value means 48 hours), for the
  sweep and for the writer that waits on it under `arm`. It used to raise in the sweep on `null` or `"bad"` every
  tick.
- **PRIV3 (2026-10-20) — seam fixes after the window-D review: `/x` and his-words edge cases.** A read-only seam
  review of `main` confirmed each with a probe (`/tmp/seamD/priv/`, `/tmp/seamD/words/`); each is now a test that failed
  first (`tests/test_privacy_seams_d.py`). The guard bypass class it also found is raised above, not changed.
  (2) **The sweep asks every row whether it is off the record**, a `/j` row too: it used to skip the check for a row
  starting with `/j`, so a busy-path burst of a `/j` note and a `/x` message on a later line was swept with the `/x`
  words (`probe_sweep_j_inside.py`). A `/j` inside a bare-`/x` window stays public: the pass's own `explicit` rule
  keeps it so, and `row_marked` does not mark it.
  (3) **A captionless photo inside an open bare-`/x` window is private.** The pass skipped user-role `system` and
  `empty` rows with no caption (Hermes' image placeholder, a native-mode empty row), so a photo in a window the hook
  did not record (a busy-path `/x` row) was kept and embedded (`probe_window_photo.py`), and Hermes' vision
  description reached the `telegram_history` pack and openers (`probe_window_pack.py`). Such a row is now private when
  its time falls inside the open window, and it counts as activity in it (Codex's review of the PR): else her reply to
  it, or his next message, more than `quiet_minutes` after the activity before the photo would read as outside the window
  and be saved. A gateway row of the same kinds extends the window too, which errs toward more privacy. **No golden pin moved:** the anonymised corpus (`tests/golden/hermes_rows.json`) has no such row
  inside a window, so `test_the_privacy_pass_over_the_corpus_is_unchanged` stays byte-equal.
  (4) **`/x` on a later line counts on every face, as in the chat** (the orchestrator's decision, for consistency with
  the owner's rule). `serve/writes._his_words` (`POST /entries`, `POST` and `PATCH …/blocks`) and MCP's `journal_save`
  and `journal_add_note` use the hook's `capture.opt_out_inside`: 422 `off-the-record` / a tool error, nothing written.
  A `/x` mid-line is words, and a zero-width space before `/x` is not the command in the chat either, so neither
  changes. (5) **`entries.fill_audio_block` fills only the recording's own block**: the embed as a whole line with only
  blank lines between it and an engine heading of a recording above it (`### HH:MM … · audio file|voice transcript …`
  among his words, `**HH:MM** · …` in the conversation). His words that carry the same `![[…]]` (mid-line after a web
  edit, or a line of his own under a text heading after an edit in his notes app) are never filled; with no such block
  the function writes nothing and returns False, and the tick logs `no block of <file> in <entry>; nothing written`
  (`probe_fill.py`).
  (6) Smaller edges. (a) **A picture whose block he removed is not described**: the images step asks the model only
  while the entry still embeds the picture as a line of its own, checks again under the ledger lock before writing
  her note, and ends the capture's describing (`describe: none`) once the block is gone (an entry that cannot be read just now is
  not a gone block: the capture stays pending)
  (`probe_delete_describe.py`, `probe_describe_after_delete.py`). (b) **The "transcript pending" placeholder is not his
  words**: a web edit or new block containing `audio.TRANSCRIBING` is 422 `placeholder`, nothing written (the editor
  would lock the block as a recording in transcription). MCP's `journal_save` and `journal_add_note` refuse it too (a
  tool error): no recording would fill it there either, and the web app would then lock that block for good. The
  spec change is in API.md (the `/x` later-line rule and 422 `placeholder`). (c) **A chat photo or recording in a session entry is not phase 1's answer**: the writer filed its
  capture (`rule: conversation`) on the first prompt of the entry, phase 1's, breaking the one-for-one pairing of
  `programs._visit_blocks` (`probe_conv_capture.py`). `ledger.answers(prompt)` is a prompt's captures without the
  conversation's, and `programs._captures` and the web app's phase `answered` read it; the writer still files on the
  entry's practice or note prompt as before and, in a session entry, on the newest phase's prompt, deduplicated
  across all the entry's prompts. "As conversation per capture mode" is read as: under either `capture.mode`, a message
  after the phases is the conversation the thread keeps (CAPMODE (1)), never an answer. The tick's audio step builds the
  recording's marker from the entry's own date (the file name), not from the prompt it is filed on, so the date label
  the writer gave a block of another day survives the fill. (d) **A private stashed
  picture (or recording) is purged at the tick**: the writer drops the stash of an off-the-record message at its
  commit, as it did for ordinary chat under `arm`, instead of leaving it for `STASH_DAYS` (`probe_stash_linger.py`;
  under `arm` the probe needs the sweep to run first, as the tick does). (e) **An unusable `quiet_minutes` fails
  closed**: `config.quiet_minutes` caps at `QUIET_MAX` (a year; infinity and an integer too big for a float included)
  rather than resetting to 45, which would shorten the privacy asked for; NaN and non-numbers stay 45; doctor names
  both. `privacy.window_open` never
  raises: if its fallback cannot run either, it says open. Before, `.inf` overflowed `timedelta`, the hook's window check
  raised into `inbound.receive`'s failure path, which does not consider the window, and a reply inside a hook-recorded
  window was captured (`probe_inf_quiet.py`). (f) **MCP's `journal_start` holds while a bare-`/x` window is open**: a
  model acting for him, like the automatic sends (PRIV2). Choice: the MCP face does not read the chat (its import
  fence and `test_mcp.PrivacyTest`, which runs every tool with `sqlite3.connect` failing), so the check runs in the
  spawned process: `journal_start` spawns `journal send <id> --hold-off-record`, and with that flag `tick.run` sends
  nothing while the pass says a window is open or cannot say (fail closed), logging `send <id> held: …` to
  `manual-trigger.log`. The tool's answer cannot know the verdict when it returns, so it carries a `note` saying it is
  held while the chat is off the record and to ask again once it has gone quiet; the description says so too. A
  synchronous answer would need either the fence opened or a blocking child call (which waits for the tick lock);
  neither was taken. `/start` in the chat and the web app's Send are his acts and do not hold. `journal send` from the
  companion's terminal does not hold either: that is the guard class raised above.
- **WEB-DESIGN (2026-10-26) — the web app's design pass, Today's prompts in full, "Use my location", and the
  sign-in screen under Tailscale.** The owner asked for an independent design pass
  on the PWA. Choices, for a reviewer to revisit:
  - **One token sheet, two voices.** `tokens.css` now holds the whole scale (type `--text-xs`…`--text-3xl`, space
    `--s1`…`--s7`, radii, the paper, ink and accent colours in light and dark, the accent wash and edge); `app.css`
    consumes only tokens. The FRONTEND.md §2 direction stands and is made stricter: his words are the only text in the
    reading serif, at full contrast, on the paper; everything the companion or the engine says is the sans on the
    accent wash with her accent at its left edge and her name on it (Today's prompts, the entry's prompt block, her
    callouts, a run's phase text). No shadows except what floats (a sheet, a toast). Section heads are sentence case,
    not tracked capitals (calmer; "a journal, not a dashboard").
  - **The reading face is served, not assumed.** The serif stack (`Iowan Old Style`, `Charter`, `Palatino`,
    `Georgia`) exists on a Mac and not on Android, so an Android phone showed his words in whatever serif the system
    had. Literata (SIL OFL, designed for long reading on phone screens; `web/public/fonts`, the latin and latin-ext
    subsets of the variable weight axis, 95 KB) is now first in `--serif` and precached by the service worker (`woff2`
    joins the precache globs), so his words look the same on his phone, on a desktop and offline. Alternatives: Source
    Serif 4, Newsreader (similar size; Literata reads best small). Italic is not shipped (his words have none; the
    browser slants if a model's Markdown asks).
  - **Today shows each sent prompt in full, whatever its state.** `GET /today` rows that were sent carry `prompt`, the
    same object the entry document has, built by one function (`serve/document.prompt_of`) for both views; `upcoming`
    rows carry none. The web never folds a prompt; Telegram's fold (`tick.fold`, `compose.telegram_html`) is
    untouched. The cards are ordered by the time of day (sent, else due) instead of schedule-then-on-demand; his reply's
    first line sits under her message in his serif, linked to the entry. The entry's own page still folds the
    questions behind a disclosure: there the page is his words. **Privacy, a conflict with the brief:** the brief asked
    that an extra field pass through `privacy.py`; the serve side may not import it (API.md Decision 2,
    `tests/test_serve_fence.py`). The opener comes from the ledger record the entry document already served, written by
    the tick from what the privacy pass let through; `prompt_of` adds a narrow guard of the surface's own instead of opening
    the fence: it catches a literal echo (an opener with a line that starts with the opt-out command, or that holds
    the off-the-record notice, is withheld whole). It is not a privacy pass and cannot see a paraphrase; see the open
    point on the opener's sources above. Each row's `prompt` is built on its own: a record that cannot be read costs
    that card its prompt, not the route. `today.collect`, the companion's thread-start block, is unchanged. `/today` also names the two voices
    (`voices`), as the document does.
  - **"Use my location" is keyless and goes through `location.set_location`.** `POST /settings/location {lat, lon}` →
    `settings.apply_near`: the ownership checks first (nothing leaves the host if Hermes owns a key), the position cut to
    two decimals (about a kilometre) and sent once, to OpenStreetMap **Nominatim**'s reverse lookup (`zoom=10`: city,
    town or village; an identifying `User-Agent` naming the software, per its usage policy; one request per tap is far
    under its 1/s limit), which names the place; the time zone by **Open-Meteo**'s forecast with `timezone=auto`
    (already the journal's weather and geocoding service), asked for the place. Both base addresses are config
    (`lookup.reverse_url`, `lookup.timezone_url`; Nominatim's policy asks that an app can be pointed at another
    instance), defaulting to the public services; an address that is not a plain http(s) URL is not used. A place the
    reverse lookup named is OpenStreetMap's data, so its `coordinates` carry `source: "openstreetmap"` (written by the
    same `set_location`; a typed place writes them without, which clears it), and wherever the app shows the stored
    place (Today's header, the Settings place field) a small caption reads "Place name © OpenStreetMap contributors",
    linked to OSM's copyright page, for as long as that place is in force (Nominatim's attribution requirement; review
    round 2: a credit shown only at the moment of finding left the place uncredited afterwards). `source` sits inside
    `coordinates` rather than in a new top-level key, so the ownership checks and the single write stay as they were. A lookup
    curl cannot reach (non-zero exit), or one that answers an HTTP error (a 429 or 5xx in JSON: curl prints the status
    after the body, `-w`, and anything outside 2xx is refused), is 503, never "no such place" (422); `geocode()` for a
    typed place had the same flaw and now fails the same way. Open-Meteo has no reverse place lookup,
    which is why there are two services. Rejected: BigDataCloud's free "client" endpoint (its terms keep it to
    browsers), a bundled time-zone polygon file (tens of MB for one button), the browser's `Intl` time zone (it is the
    device's setting, not the place's). The three values are written by `set_location(near=…)`, so a typed place and a
    device position share one write and one `location-history.jsonl` line (whose `query` reads `device position`). **What is
    stored is the matched place's own coordinates** (Nominatim's answer: the centre of the city, town or village at
    zoom 10), exactly what a typed place stores, never the device's position (review, 2026-10-26: a stored position
    would have been on curl's command line at every weather refresh, `sources.weather`, where `/proc` is readable on
    the deploy host, and a trail in `location-history.jsonl`). The position is never logged, never in an answer or a
    refusal, not kept by the page, and curl reads the lookup addresses from stdin (`-K -`), so it is on no command line.
  - **A refused read under Tailscale no longer shows the pairing form.** The app remembers (localStorage
    `journal:via`, the word only, never the login) how `GET /session` last said this browser signs in; the shell asks
    once at start, and pairing and Unpair update it. When a read is refused and the last answer was `tailscale`, the
    screen asks `/session` once and says what it answered (review, 2026-10-26: one message for both left a device the
    server had stopped recognising retrying for ever): a **401** is "This device isn't recognised by Tailscale any
    more", with the pairing fold open, since retrying will not help; **no answer** (no network, a restart's 5xx) is
    "Couldn't confirm your Tailscale sign-in", the server "may be restarting", and Retry. Only Retry lets the views read
    again, when the server confirms the identity: it is his act, so a read refused again cannot loop. Otherwise "Not paired" and the form, as before.
    Settings no longer claims "Paired as …" when `/session` could not be read: it says so, with "Check again", and
    offers Unpair only when this browser was last known to be paired.
  - **Settings is grouped** (place and time, prompts, reflections, in the chat, programs: `settingsModel.GROUPS`), each
    field's layer is a short tag beside its label (the full sentence stays for a screen reader), the Save bar stays in
    reach while a change is unsaved, and the diagnostics (everything as the server reads it, the health check) are
    folded at the foot. On a phone the page went from about 10,000 px to a few screens.
  - **Fixed on the way:** on a phone the "Written today" rows were drawn side by side by the touch-target rule for bare
    links and pushed the page sideways (to 627 px wide at 390); they now have their own class.
  - **Not verified:** a real Android phone and desktop browser (a remote sandbox's headless Chromium at 390 x 844 and
    1280 x 860 stood in), and the button end to end against the live services (the tests stub both; each was asked once
    from the deploy host with a neutral position, central Lisbon, through the same `curl -K -` call: Nominatim answered
    `address.city` "Lisbon", Open-Meteo `timezone` "Europe/Lisbon", the shapes the code reads).
- **CONTEXT-PACK (2026-10-26) — the pack knows how long it has been.** Agreed with the owner: deterministic code is
  the grounding, the model is encouraged to read further. The pack opens with *where things stand* (`standing.py`,
  no model) and ends with *where to look further*; the opener request gains a returning stance; the today block
  opens with the same facts; the whole pack has a budget (`pack.*`, a new top-level key). Choices made on the way:
  (1) **"Last heard from on the record"** is the newest of: a ledger capture (capture never takes an off-the-record
  message), a web block (`web_blocks`), the date of his newest entry, and his newest chat message that
  `conversations.row_is_off` calls public under `privacy.covering` over `pack.lookback_days`. The hook's
  `last_his` stamp is never read (every message moves it, `/x` included). A closed pass counts nothing from the
  chat and the section says so. Under `capture.mode: arm`, ordinary chat counts too: its *time* says he was around,
  its words are still never read back (CAPMODE (10)).
  (2) **The unanswered run** is the one `context.unanswered` always had (practice prompts back to the first
  answered one, by send time; program phases and notes are not practice prompts), now reaching `pack.lookback_days`
  instead of 3 days; "since the newest practice prompt he answered" is how the pack and the block word it (a late
  reply to an older prompt does not reach past newer ones). A prompt with any capture is
  answered, as `fold` and `prompt_state` read it. Up to four are named one by one in the request (the old sentence,
  byte for byte); a longer run is counted per practice.
  (3) **The gap is in calendar days** (what "5 days ago" says), so the returning stance agrees with the line she
  reads; it needs a heard date (a journal with no record of him gets no welcome-back).
  (4) **A program's "last touched" is his newest captured answer in the run**, not `last_activity_at` (the engine's
  sends move it) nor `started_at`/`paused_since` (a `/start` or `/break` typed inside a bare-`/x` window would be
  timing from off the record). The open phase's send time is not shown either: a `/start` or `/next` in such a
  window re-sends the phase and moves it (review M2). The movement comes from `phases_of` (parsed here; nothing read
  it before).
  (5) **The today block reads the chat part from the tick's cache** (`today-cache.json` `heard`, written by the
  `cache` step through the privacy pass): the hook still never reads the transcript (WP3). A pass that cannot run
  keeps the time already cached (a public message stays public). `GET /today` and MCP's today text do not carry
  `standing` (`collect(with_standing=)`), so the API shape and `tests/fixtures/today.json` are unchanged;
  `tests/fixtures/today.txt` changes on purpose (standing's program line replaces the block's own).
  (6) **The budget drops her lines first, then the far end** (review L1, L2): her conversation lines that repeat an
  opener quoted above, then her other conversation lines, then the oldest dated item anywhere (his chat lines, his
  entries but the newest, her analyses; an entry counts at its day's end, so it outlasts that day's chat lines), then
  the current sections and the pointer lines from the pack's end. Where things stand, the two openers (each cut at
  1200 characters), *not connected* (600) and his newest entry are never dropped; `pack.pointers` lines are cut at
  320; a final cut of whole lines holds `pack.max_chars` in any case. Default 32000: measured live packs stayed well under it, even with this
  package's ~4k of new sections, so an ordinary pack is not trimmed; the floor is 8000.
  (7) **Pointers are generated, never fixed**: his entries (only those with words of his; with what the budget left
  out) and her analyses, counted on disk with the earliest date, program documents when a run exists, the notebook
  archive when it has lines (what it holds, never its path: it sits in the state directory, review M3), each source that is enabled *and*
  passes `sources.check`, then `pack.pointers`. **No pointer into his chat history**: it holds what he said off the
  record, and whether the one-shot opener call can read past the pack into it is the open WEB-DESIGN point above; the
  quote verifier catches a verbatim `/x` quote, not a paraphrase. Add the pointer when that point closes (the pack's
  own chat section is unchanged and privacy-filtered). The default `prompts.reading_guidance` now points at that section;
  a configured one (a deployment's own) is unchanged.
  (8) **A journal: or telegram: quote verifies only against his words** (review M1, and the re-review). The check
  used the whole pack's text, so her week review, the pack's own prose and the openers' intro passed as his; and the
  verifier's journal corpus was every file minus the opener, her replies and the summary, so her annotations and her
  notes in his entry passed too (older than this package). Now `validate.check` asks only the verifier for those
  sources. `make_verifier` reads `journal:<file>` (the name's leading `[\w.-]+` token, so a heading copied whole
  works; a name two nested files share names neither) and a `brain:` path that lands inside the journal folder as his
  words in that file (`context.his_text`), and `telegram:` as his public chat rows: the rows `standing._his_message`
  counts as his (no cron delivery, compaction summary, platform notice or mirror) and, of a picture, his caption only,
  never the vision description beside it (`context._his_chat_text`). Source names are read case- and
  space-insensitively (`Journal:`, ` journal:`); a journal-like or telegram-like name the check doesn't know
  (`journals:`, `telegram-chat:`) is refused, fail closed. Quotes and records are folded alike on every side
  (`validate.fold`: curly and straight quotes, dashes, case, whitespace, end punctuation). A `brain:` path outside the
  journal is not checked, like any other named source. Not handled: a quote that runs across two of his blocks (a
  time heading sits between them in the file) is not found.
  **His words cited under the wrong name are still his (the owner's delegate, 2026-10-26).** When a quote is not in the
  cited file's words but is in his words in exactly one other file, it passes and the tick logs `<prompt>: opener:
  cited-elsewhere: <source> is in journal:<file>` (names only, never the quote). A quote found in two or more other
  files, or in none, fails. The reviewer's read-only replay of a live ledger showed the strict rule turning away
  real openers (a true quote of his, under a neighbouring entry's name) for no gain in keeping the voices apart:
  her words are in no file's `his_text`, so they still fail under every name. This changes a tested contract:
  `test_wp5_rev2.VerifierCorpusTest` asked `journal:x` (any label checked the whole journal); it now cites the file,
  asks every file for her synthesis and the engine's questions, and pins the cited-elsewhere note.
  **A claim about him needs a citation that grounds it** (re-review 4). `CLAIM_MARKERS` ("you said", "you were", "you're
  back"…) are satisfied by: his words that verified (`journal:`, `telegram:`, `brain:` in the journal); a `standing`
  quote of at least 12 characters (`STANDING_GROUNDS`), checked against "Where things stand" only, so the returning
  stance can name the time away by citing the Gap line (review L3); or a named fact of another source (a book, the
  weather, a task). Not by her own analysis (`week:`, `reflection:`), nor by a scrap of the standing section.
  **Where this departs from the request:** the coordinator's wording was "only his words or a standing quote of 12+
  characters". Taken literally it turns away an opener grounded in a book or the weather ("you were 19% into Moby-Dick",
  `book:`), which `test_openers.AnchoringTest` pins and which the opener request itself invites ("a good
  question can stand on the pack's other facts … cite whatever you used"). So named external sources still ground a
  claim; their quotes stay unchecked, as before. Open for the owner if that should be narrowed.
  (9) **Known limit (review L9):** the pack injected into a reply turn (`trim_for_turn`, 4000 characters, 900 per
  section) now opens with where things stand and the openers, so the notebook and the sources usually fall off the
  end of it. Accepted: the notebook is injected on every turn anyway, and she can read the sources herself.
  (10) **Found on the way:** the pack's chat reader (`context._his_messages`) read "the last 7 days" from the real
  clock, not the pack's `now_local`; it made `ArmPackTest` a date time-bomb, which WEB-DESIGN pinned in the test, and would
  have made this package's `/x` assertions pass vacuously after 1 Nov (CodeRabbit). The pack now passes its own
  `now_local` (`telegram_messages`, with no time, keeps the wall clock), and the test asserts the chat was read.
- **SENT-AS-SENT (2026-10-26) — a prompt's questions are read from what was sent; a fold keeps the opener (closes
  the WEB-DESIGN open point, Codex on the WEB-DESIGN PR).** (1) `tick.send_due` stores the question set in the send record
  (`questions: [{id, text, section}]`, `library.question_snapshot`); `serve/document.prompt_of` reads it first
  (`library.snapshot_sections`, which ignores anything malformed) and falls back to the library for records from before
  and for a set over `SNAPSHOT_MAX_CHARS` (20,000). Size: about 2.2 KB for the evening bank (the largest built-in), less than the
  `html` and `plain_text` the record already holds together (each carries the bank); `sent.jsonl` (the engine's memory, with no reader of
  questions) is unchanged. A manual re-send replaces the set with the one the new message carried. (2) Program
  phases do **not** snapshot: a phase record already keeps the text sent (`phase_text`, `plain_text`), a phase has no
  opener, so there is no old opener to sit above edited questions, and the entry is a session whose phases are
  several records, so a set would have to be merged from them. A program edited mid-run shows its current questions
  on the entry page, as the run itself walks the current definition (`programs.py`: "its definition changed"). Open (listed above):
  revisit if programs are edited while runs are live. (3) The entry file's own `## Prompt` section
  (`entries._prompt_md`) is written at the first reply from the library as it is then; it is not changed here.
  (4) The Telegram fold keeps the header and the opener from the record (the plain opener after a fallback or a
  send-retry) and puts the status line (`answered ✓`, `missed — reply anytime`, `sent again below`) where the questions
  and the hint were; the status moved from the header to its own line. Only the questions are dropped, so the folded
  message is shorter than the sent one and fits Telegram's limit when that did (should it not, the opener is dropped
  and the fold is header + status). The opener passes the web app's narrow check (`capture.stored_opener`: an opener
  with a `/x` line or the off-the-record notice is withheld whole); the fold reads nothing from the chat, the privacy
  pass or the library's text, so `/x` is as before. A manual re-send's "sent again below" edit keeps the old message's
  opener too. Program phases still fold to their header line.
  A re-send keeps the replaced opener in `earlier_openers` (last 5) so the busy-path quote matcher still recognises
  the old message, and its edit applies the same opener check.
  Costs, said plainly: **her openers now stay in Telegram permanently** (a folded prompt keeps the opener it went out
  with; before, the fold removed everything but the header), and **the ledger records grew by about 40%** (the
  question set, 2.2 KB for the evening prompt, on top of `html` and `plain_text`; 120 days of retention as before).
  A markup Telegram cannot parse in an opener (crossed tags from the model's text, as `send_html` already survives by
  sending plain text) is handled for the fold too: `telegram.edit_html` retries an edit as plain text, and `fold` and
  the re-send edit try once more without the opener if that fails, so a fold is never lost to an opener. The
  re-send edit reads the replaced record as it stood (`send_prompt` hands it to `refold`), and the record always
  writes `questions` (None over the cap), so a re-send over the cap clears the old set.

- **SERVE-UNSANDBOXED (the owner, 2026-10-26) — `journal serve`'s unit has no filesystem sandbox (closes the Tailscale sign-in report and the
  TSAUTH open point on the sandbox).** The owner ruled the sandbox unneeded. His intent is that Tailscale
  identity is the sign-in and pairing the fallback when identity is not available. *Why.* Under a systemd user
  manager, `PrivateTmp`, `ProtectSystem`, `ProtectHome` and `ReadWritePaths` (and the other mount-namespace
  settings: `Private*`, `Protect*`, `ReadOnlyPaths`, `InaccessiblePaths`, `TemporaryFileSystem`, `Bind*Paths`) make
  systemd put the service in a user namespace of its own, mapping only the service user's uid. tailscaled is root,
  so on the socket its `SO_PEERCRED` uid reads as the overflow 65534, `auth.tailscale_login` (which trusts only
  uid 0, rightly) signs no one in, and every browser fell back to pairing. The other way to keep the sandbox, a
  system unit with `User=` the service user (the system manager builds the namespaces without a user namespace), was
  judged not worth it: a second kind of unit to install and keep, for a sandbox that, on the deploy host, never
  confined anything (see
  below). Trusting uid 65534 or the service user's uid was never an option: that would let any process of the user,
  an agent's shell included, sign in as him.
  *What was checked (the deploy host, systemd 255, `kernel.apparmor_restrict_unprivileged_userns=1`), with throwaway
  `systemd-run --user` units, not the live one.* The live `journal-serve` is in its own user namespace (`uid_map`
  `<uid> <uid> 1`) but the host's mount namespace. A unit with `ProtectHome=read-only`, `PrivateTmp=yes` or
  `ProtectSystem=strict` alone: its own user namespace, and `SO_PEERCRED` on `/var/run/tailscale/tailscaled.sock`
  gives `uid 65534`; with all three, the home directory is still writable and `/tmp` is the host's (the mount
  namespace is not built). A unit with `NoNewPrivileges=yes` plus `UMask`, the memory and task limits and
  `RuntimeDirectory`: the host's user namespace (`0 0 4294967295`), `NoNewPrivs: 1`, and tailscaled's peer uid
  `0`. So **`NoNewPrivileges` stays**: it is a process flag (`PR_SET_NO_NEW_PRIVS`), not a namespace.
  *What the unit keeps.* `NoNewPrivileges`, `UMask=0077`, the cgroup limits (Decision 7's `MemoryHigh`/`MemoryMax`,
  `MemorySwapMax=0`, `TasksMax=64`, `CPUWeight=50`), `RuntimeDirectory=`/`RuntimeDirectoryMode=0700` for a socket
  below the runtime directory, `RequiresMountsFor` and the `mountpoint -q` gates, `Restart=on-failure`,
  `KillMode=process`. *What the code lost.* `unit.config_dir` and its rules (the config directory in
  `ReadWritePaths`), the socket directory in `ReadWritePaths`, the refusal of a socket under `/tmp` (`UnitError`;
  without a private /tmp tailscaled sees the path the server bound, and the server makes the directory 0700 itself).
  The mount gates now cover `root`, `state_dir`, `practices_dir`, the config file's directory (before, only when it
  was "the config file's own") and the socket's directory outside the runtime directory (never gated before; review);
  the socket path is normalised in `config.serve_socket`, so the server, the unit and doctor agree on one path and
  a `..` can neither make a `RuntimeDirectory=` systemd refuses nor leave a stray directory.
  *The socket's directory (review, earlier PR 68).* The old `/tmp` refusal existed for `PrivateTmp`, but it also kept out a
  second local user (a shared host has other uids besides the service user's): one who can write the socket's directory
  can unlink the server's socket and bind their own, and tailscaled (root) would then proxy his browser's requests,
  cookie included, to them. So `UnixServer.server_bind` refuses (`EPERM`, not retried; `journal serve` exits 1 saying
  why) a directory that is a symlink, belongs to another uid, or is group- or world-writable, and `_clear_stale` never
  removes or reuses a socket file owned by another uid. Only the socket's own directory is checked, not its
  ancestors (a sticky `/tmp` above a directory of the user's own is fine).
  *Older units.* The settings `503` also says to re-render the unit: under an older, sandboxed unit on a host where
  the namespace is built, the directory is read-only (`EROFS`), and `chmod` would not help. The settings `503` names the directory and says to make it writable for
  the service user, not to re-render the unit.
  *Cost, said plainly.* Where a user manager *can* build the namespace (a host without Ubuntu's AppArmor userns
  restriction), the unit no longer keeps the server, and everything it spawns, out of the rest of the home
  directory: "Send now" runs `journal send`, which runs the opener through the companion's model with its tools,
  now with the same reach as the cron tick and the gateway, which never had a sandbox. On the deploy host nothing
  changes in practice: the sandbox was not confining the filesystem. The guard plugin and the serve token's checks are
  as before.

- **ONE-PROGRAM (2026-10-26) — one program session in progress at a time, across every program and every face.**
  *Superseded in its reading by ONE-PROGRAM-RUN (2026-10-27, below): he clarified that "one program" means one
  unfinished run, not one session. What stands from here: one rule in one function asked under the tick's lock, the
  faces' read-only pre-checks, `programs.Busy`, and (6)'s `current_run` preferring the session in progress.*
  The owner asked for one program running at a time, globally, whichever face it comes from (Discord, Telegram, the
  CLI or the web).
  **Read as (the package's interpretation; his to correct):** at most one run is *mid-session* at a time: active
  (not on `/break`, not over) with its current session begun and not completed (`programs.in_session`). A long program
  between sessions, or on a break, blocks nothing: otherwise a ten-session fix-life would lock out a one-off
  conversation prep for weeks. (1) **The rule is one function, `programs.one_at_a_time`, asked by the two transitions
  that take a run from not mid-session to mid-session, under the tick's lock and before they change anything:**
  `start` (a new run; a run taken up after a break, which `resume` and the web's resume are; the next session of a run
  between sessions; `--new`, the web app's spawn) and `advance`'s between-sessions branch (`/next` or `/skip` that
  would begin the next session). Re-sending a program's own open phase, or jumping it to another session while it is
  mid-session, is unchanged: its own session never blocks it. A refusal is `programs.Busy` (a `ProgramError`): the CLI
  prints it and exits 2, and `--notify` (the chat's spawns) says it in the chat. Wording: "‹Title› is mid-session
  (session ‹n›: ‹session title›), and only one program runs at a time. /break pauses it, /next or finishing the
  session carries it on, or /start ‹A› picks it back up. Then /start ‹B›." (a situational program has one session,
  so no session number). (2) **The faces check first, read-only, so the refusal is the command's own answer:** the
  chat's `/start <program>` and `/next`/`/skip` on a run between sessions (`commands._busy`; Telegram, Discord and
  Slack share it through the plugin) answer with the refusal and spawn nothing, in the channel the command came from;
  the web's `POST /runs`, and `resume`, `next`, `skip` that would begin a session, are `409 session-in-progress` with
  `run` and `program` (the one mid-session) and the same facts in the app's words ("Pause it, or carry on to the end
  of that session, first."); the app shows `detail` as it is, so the build is unchanged. These pre-checks are read
  without the lock; the spawned child asks again under it and that answer counts (a race lost there is refused in
  `manual-trigger.log`, and through `--notify` from the chat). (3) **Calls the code's own terms made:** a situational
  run idle past `expires_hours` does not block (the tick's step closes it; the web already reads it as expired); a
  run whose program definition no longer loads does not block (it can only be abandoned, and the step leaves it
  alone). A session whose first send failed is in progress (its phase is owed and the tick delivers it).
  (4) **No deferral path was needed:** the tick never begins a session. Its step delivers phases owed to a session
  already in progress, settles, retries summaries and expires; `complete_session` sets the next session `pending`
  and waits for `/start` or `/next`. So nothing the tick does is refused or deferred, and session 10's interrupts
  (timed questions of a completed session, not a session) neither block nor are blocked. (5) **Not migrated:** two
  runs already mid-session when this deploys both carry on (the rule refuses only new starts); `/next` acts on the
  one last active, as before. Open for him: whether "one program" should mean one *run* (between sessions included),
  which this reading deliberately does not do.
  (6) **Review fixes (Opus review, earlier PR 70).** *The run commands act on the run mid-session:* `current_run` with no
  program named returns the run with a session in progress first (`session_in_progress`), and only when there is none
  the run touched last. Before, a reply to an older run's phase made that run current, so `/break` (which the refusal
  recommends) could pause the run it did not name. This changes `/next`, `/skip`, `/back`, `/break`, `journal program
  …` with no run, `abandon` with no run and the today line alike; the web app names the run in its URL, so it already
  acted on the one shown. *A session left completing is not mid-session:* once his last phase is done
  (`completion_due`), only the engine's summary is owed and the tick's step retries it; counting it would let a
  completion that keeps failing block every program, with `/next` answering "nothing to move on". Cost, said plainly:
  if another program starts in that window, the late summary's send removes the reply keyboard of that program's open
  phase (typed commands still work; its next phase brings the keyboard back). The window is a crash between the last
  answer and the summary, normally closed by the next tick. *A program turned off* is not offered as `/start` in the
  refusal (`/break` and `/next` still reach its run). *A web start that loses the race* to another face is refused in
  the spawned child, logged in `manual-trigger.log`, and the app shows the page as it was; a second tap gets the 409
  with the reason. Accepted rather than built: the window is a start from two faces within the time the tick's lock is
  held.
- **IDENTITY (2026-10-27) — the web app's one visual identity: "Margin × Letters".** The owner chose Margin × Letters
  from the identity exploration, with elements of Marginalia and Carbon (below). What was built, and the calls made for him to
  revisit:
  - **One identity, no switch.** The candidate sheets and the `?identity=` flag are gone; `tokens.css` and `app.css`
    are the design. FRONTEND.md §2's two-voices rule stands and changes its means: his words are Newsreader roman at
    full ink on the paper (Literata is retired); hers are typed in Courier Prime in her ink under a "From" line that
    runs out as a dashed fold (the sans-on-a-wash callout is retired); the engine is Instrument Sans. Her ink is used
    for her words and nothing else: his actions (buttons, the +, progress, focus, links) are in his own ink.
  - **Where her words are.** Everything the companion wrote is typed in her ink: Today's prompts, the entry's prompt
    and notes, a run's phase text, the practice's companion line, the Analysis tab, the reflections in Insights, a
    program's summary, and (a change from the mix, which set it in the italic) the "To carry forward" line on Today,
    since that is the question her reflection asked. Her notes stay read-only on every screen.
  - **Desktop is Marginalia's spread.** At 1024 px and wider, Today and an entry open into three columns: the hour,
    his column, her column. On Today each prompt is a row of the spread, her letter level with his title; the
    sections after the prompts keep the reading width. On an entry her letter heads the margin column and each note
    of hers stands in the margin where she spoke, from the end of the paragraph of his it follows; they are floats
    in the margin, as sidenotes are set, so his column runs on without a hole. (A grid, as the exploration did,
    aligned a note with the top of that paragraph but opened gaps in his column wherever her letter or a note ran
    taller than his words; the independent review found it.) A note that would overlap her letter, or an earlier
    note, moves down below it. Between 641 and 1023 px the page is the mix's two columns (the hour in the margin, her
    letter under his title); a phone is one column. Lists, forms and settings keep the reading width.
  - **Her ink is the reader's choice, on this device (localStorage, `journal:ink`).** Four inks, from the four mixes:
    blue-black (the mix, the default), brown (Marginalia), violet (Carbon), green (Postmark), each with a light and a
    dark value. Violet is Carbon's moved about 15° towards purple (`#4b2a8a` / `#d2b4ff` for `#3a2f8f` / `#c4bcff`):
    side by side in the chooser, Carbon's own violet read as a second blue-black, in the dark especially; `web/tests/ink.test.tsx` holds every ink, and the softer grade made from it, at WCAG AA (4.5:1) on
    the paper and the surface in both schemes. Settings has an **Ink** section: four options, each a line of a letter
    typed in that ink ("From the companion": Settings reads nothing for the name, since a read there would mark the
    page as read from the kept copy when offline), and the choice applies at once. **Why client-side:** there is no server-side
    place for display preferences (the config is the engine's: schedules, places, windows; `config.WEB_WRITABLE` is
    its allow-list), an ink is a property of a screen rather than of the journal (his phone and a desktop may differ,
    and the companion, the tick and Telegram never read it), and a server setting would add a config key, an API
    field and a write path for something nothing on the server uses. Cost: the choice does not follow him to a new
    browser, and clearing site data resets it to blue-black. It survives an unpair (it says nothing about the
    journal). The installed app's icon keeps blue-black: an icon cannot follow a per-device choice.
  - **No dated stamps.** Postmark's date ring, the hour stamps and the week as rings are not built. The mark is the
    mix's: his page with its margin ruled, three lines of his, and a postmark ring in the margin in her ink, with no
    date (`Mark` in `icons.tsx`; `public/icons/mark.svg` and the PNGs made from it are the app icon).
  - **Carbon's figures.** Every time is typed (`time` is Courier Prime everywhere; the hour in Today's margin at 700);
    the days of the week strip and the week's counts are Fraunces' soft figures (opsz, SOFT 100); labels above titles,
    dates under them, day heads, phase dividers and typed badges are Courier Prime capitals. A session number in a
    run's list is typed bold. Fraunces is cut to its ten digits (9 KB) and the Newsreader italic pinned at weight 400
    (64 KB) by `tools/web_fonts.py`, which checks its Fontsource inputs by hash and makes the same bytes again. The
    faces the app serves went from 95 KB (Literata) to about 270 KB, all precached; that is the cost of the type.
    Courier runs about 30% wider than the sans: `web/e2e/mobile.spec.ts` now checks that no route scrolls sideways at
    360 px.
  - **Words, from the mix.** A prompt's state reads "at 23:00", "waiting since 20:05", "answered 21:38", "came at
    06:00": an unanswered prompt says when it came, with no colour and no "missed" (Reply stays under it); the time in
    it is typed. The desktop + reads "Write" (its accessible name is now "Write", and the sheet it opens is titled
    "Write"; on a phone the word is hidden and the + alone shows). The entry's label above its title is left out when it would only repeat the title. On Today
    the practice emoji is not shown (the hour and the title carry the card); lists keep it.
  - **Not changed:** the API, the engine, privacy, capture, the PWA layer (manifest, worker, offline reads), focus
    styles, reduced motion and landmarks. Nothing on the server reads the ink.

- **ONE-PROGRAM-RUN (2026-10-27) — one program means one unfinished run; situational sessions are exempt both ways.**
  Supersedes ONE-PROGRAM's reading (its "open for him" point). The owner: one program means that while a program
  (fix-life, say) is unfinished, no other program can start; practices and situational sessions keep working.
  (1) **The rule** (`programs.blocking`, asked by `one_at_a_time`): while a program that is not situational has an
  unfinished run (status `active` or `paused`; mid-session, between sessions or on a break alike), starting another
  program that is not situational is refused with `programs.Busy`, before anything changes. Finishing the run (its
  last session complete) or abandoning it frees the slot. `/break` still pauses, and no longer frees anything.
  Practices are untouched; situational definitions (`library/situational/`, his own `kind: situational`) are never
  refused and never refuse, whatever else is running.
  (2) **A program's own run is never refused.** "Starting or resuming a different program" was the brief; a program
  that has an unfinished run already holds the slot, so taking it up after a break (`/start`, `resume`, the web's
  Resume) or beginning its next session (`/next`, `/skip` between sessions) goes ahead. So the check bites only where
  a *new run* would begin: `start` under the tick's lock (with `--new`, the web's spawn, too); the chat's `/start`
  and the web's `POST /runs` pre-check the same function without the lock, as before. The run routes
  (`resume|next|skip`) and the chat's `/next`/`/skip` lost their checks: under this rule they could not fire. The
  reading taken for the case it matters: two programs unfinished from before the rule each carry on (both hold a
  slot) and a third is refused until both are done; refusing their resumes too would leave each refusal pointing at
  a `/start` the other refuses. No such pair exists among the built-ins (fix-life is the only long program).
  (3) **Wording.** Chat: "‹Title› isn't finished (‹where›), and only one program runs at a time. Carry on with
  /start ‹word› and finish it, or end it with /abandon ‹word› (its entries stay). Then /start ‹wanted›." where
  ‹where› is "in session 3 of 10: ‹session title›", "on a break in session 3 of 10: …" or "session 3 of 10 is next:
  …". A program turned off can't be taken up, so its refusal reads "It's turned off: end it with /abandon ‹word›
  (its entries stay), or turn it back on to finish it." Web: the same lead, then "Finish it or abandon it first.",
  shown with the "Carry on with it" link to the unfinished run (whose page has Abandon). The 409 is renamed
  `program-in-progress` (with `run` and `program`), the client in step. The companion's `journal_program propose`
  adds `refused` (the chat's line) when the line it offers would be refused, so she does not offer a dead command.
  (4) **`/abandon <id>`, new in chat.** The chat had no way to abandon (only the web's Abandon and `journal program
  abandon`), and the refusal needs one. It is a spec beside the program commands (`commands.abandon_spec`, so every
  adapter registers it), spawning `journal program abandon <run> --notify`. Abandoning is not undone by a revert (an
  ended run stays ended), so it must be named: `/abandon` alone ends nothing and lists `/abandon ‹word›` for each run
  in progress; an id or alias with no run says so. It is not on the reply keyboard and not on the arrival path (it
  runs where `/start` runs: Hermes' command path, when she is idle). Name checked free in Hermes' registry; added to
  `config.RESERVED_COMMANDS`. A chat that gates commands needs `abandon` in `user_allowed_commands` (a deploy step).
  (5) **Unchanged calls from ONE-PROGRAM:** a run whose definition no longer loads holds nothing (it can only be
  abandoned); the tick begins no run, so nothing it does is refused; `current_run` with no program named still
  prefers a run with a session in progress (now possibly a situational session beside a program's), so `/next`
  reaches the session he is in. A completed run with session 10's interrupts still to come is finished: it holds
  nothing.
  (6) **Review fixes (Opus review, earlier PR 75).** *The cost of the situational exemption, named and softened:* a
  situational session started while a program is mid-session sends newer prompts, so the program's open phase no
  longer takes the next message (`capture.superseded`), and the situational summary's send removes its keyboard;
  under `capture.mode: arm` an untargeted message after that is not saved. This is what keeping situational sessions
  working costs, and it was the behaviour before ONE-PROGRAM too. The engine does not re-send the open
  phase by itself (a send he did not ask for); instead the situational session's summary ends with "‹Title› is still
  open at session ‹s›, phase ‹p›: /start ‹word› sends it again." (`programs._still_open_line`), and `/start` makes
  that phase the newest again; `/abandon` of a situational run says the same. A situational run that expires says
  nothing (no message goes out then). *Closed 2026-10-29:* the re-send stays manual (PHASE-RESEND-MANUAL, at the end of this file). *A refusal for a run
  left past a trimmed definition* keeps the title and the `/start` offer (only "where" drops); it read "It's turned
  off" before. */abandon reaches session 10's interrupts:* `/abandon fix-life` on a finished run with interrupts
  still to come cancels them (as `journal program abandon` and the web's Abandon do); a run whose definition is gone
  is ended without offering a `/start` that cannot work; `/abandon` alone lists such a run too. With both a new run
  of a program and an older finished one still holding interrupts, `/abandon ‹word›` ends the run in progress (the
  one the word names now); a second `/abandon ‹word›` then reaches the interrupts.
- **BACKLOG-BACKFILL — backfill and conversation fixes from the review audit.**
  - **Queued rows:** a backfill bounds the prompt sends it merges in by the latest row time it handled, not the last row's
    (rows go by id; a queued message carries an earlier time).
  - **Other roots:** the conversation writer files a recording or picture on the entry's prompt by the path read through
    `in_root`, as it appended there, and **rewrites that prompt's `entry_path` to the path in this journal**: the
    audio and images steps read `entry_path` as recorded and would otherwise look for the entry under the old root.
    Only the prompt filed on is changed, and only to the file that holds the block. A side effect: the tick's
    annotation step (and the annotation reply to Telegram) reads `entry_path` too, so a relocated entry it skipped
    as missing can now be annotated, once, like any other.
  - **Prune:** `tick.prune` keeps a prompt older than 120 days while it has work queued: a pending transcript (the
    audio step always ends one), or a pending picture description while descriptions are on (the images step runs
    only then; with them off such a record would be kept for ever, so it goes with its date as before). The
    companion switched off (`agent.enabled: false`) does not drop it: that is taken as for a while.
  - **End of words:** a file the engine starts without an annotations section (`_new_file` with `annotate: false`: an auto
    note, a practice that is not annotated) carries a hidden `%% journal:end-of-words %%` line where that section
    would be. His words and the conversation end there, and the annotations and notes are looked for only after it.
    A hidden line rather than marks on every annotations and notes heading: the rituals' entries (and their goldens)
    are unchanged, and only files that lacked any boundary of the engine's gain one. The line counts only as the
    last one with nothing after it but blank lines and then the end of the file or her annotations or notes heading, and her
    annotations and notes are written without any `%% journal … %%` line (`entries._hers`, as the summary's copy
    already was): a copy he pastes moves nothing (unless it is the last line of his words in a file without the
    engine's line: his next reply then goes in above that hidden line, which changes nothing visible), and a reply older code appends after the line (a rollback, or the
    gateway before its restart), or a `## Conversation` it opens there, leaves the file read by the rule without the
    line, as before this package. Open:
    older files and unannotated program sessions (Raised, above).
  - **Steers:** a row Hermes stores as a steer (`display_kind` `steer`: his message folded into her running turn) is
    marked `steer` by the transcript adapter (`Row.meta`, documented in `ports.py`), and the conversation writer does
    not write her held text when one of his steered messages arrives (kept or off the record): her turn's later rows
    decide it, as for any held text. If nothing more of her turn comes, the held text is written at the tick's end
    as before, after his steered message in the file, and always into the entry it was held for (the held text
    records it): a steered message that moves the thread to another entry, or leaves it without one, does not take
    her earlier text with it. A steered message of his (kept or off the record) does not end a thread a prompt send
    left draining either: it joins that thread, with her reply, since her turn is still the one running (before, the
    steer ended it and her reply followed the steer into a new thread; now both stay in the entry of the turn they
    belong to). A steered message that is a journal reply the hook captured still writes the held text first and
    moves the thread, draining or not, as an unsteered one does. Held text records the arm-mode `kept` of its thread
    when held, so a later steer that makes its own note journal material does not make the chat note she held it
    for one; held text saved by older code (no entry recorded) is written before a steer, as it was then. Under arm,
    a steered message the writer files apart from its thread (a /j he asked for, a prompt reply it files where the
    sweep would, a reply to the journal's own words) does not join the draining thread: it ends it and goes its own
    way, as unsteered. A later text of hers that is not saved (after a steered command, say) means the text she held
    was not the reply either: it is dropped.

- **BACKLOG-PRIVACY — the hook's off-the-record records are kept for good.** `conversations._prune` no longer
  ages out `off` (ids the hook took off the record) or `off_threads` (bare `/x` commands the gateway answered, which
  store no row). For a message steered in on the busy path inside a bare-`/x` window they are the only sign it is
  private, and the chat they cover can be read for longer than 400 days: `backfill-conversations --since` takes any
  date and the quote verifier reads the whole chat. The audit's suggestion, refusing an older `--since`, would have
  left the verifier (and a pack with a long `telegram_history.days`) reading those rows as public, so the inputs are
  kept instead, as `send-times.json` keeps the prompt sends. The saved pass (`privacy.private`, `privacy.windows`)
  still ages out at 400 days: `covering` recomputes a reader's own rows from these inputs, except a compaction copy
  whose original is older than the recompute, which is private only by the original's saved id (a limit this
  package leaves as it was: such a copy of a row older than 400 days reads as public to the catch-up, the pack and
  the verifier; the writer skips copies; tracked in #4). Cost: one small entry per off-the-record message in
  `conversations.json`, for good.
- **BACKLOG-PRIVACY — a queued row is private by its own time, and that time is activity of the window.** The
  pass walks rows in stored order; a message queued while she was busy is stored after rows with later times and
  carries the time he sent it. Before, a window that lapsed by the stored-order clock before the queued row was
  reached left the row tested against the open window only, and it was read as public. Now (1) every row's own time
  is tested against the closed windows too (his message, her row, a captionless photo, a copy with no Telegram id);
  (2) the own times of his queued rows count as activity when a window's end is decided (`_late`, used by `lapse`),
  so a row stored before the queued one but sent within `quiet_minutes` of it is inside the window too, and the end
  state the hook reads holds as long. (2) goes past the issue's ask (it named only the closed-window test): without
  it, the window as the docstring defines it ("while the activity keeps going") would still be cut short for rows
  stored between, and those would be public. Both only grow windows and make rows private, never the reverse; an
  in-order row is unaffected. (3) **Judgment call:** a captionless photo inside a window now taints her turn, as a
  private message of his does (the docstring's own rule: "once a private row of his is taken in"). Needed for a
  queued photo stored after the window ended, whose description in her reply would otherwise be public; it applies
  to every such photo, so after a window that ends on a photo with no reply of hers, her reply to his first public
  message is off the record too. A gateway row of the same kinds (a system note inside a window) taints as well; that
  errs toward privacy, as the window extension for those rows already did. Not changed: a bare `/x` that came in
  on the busy path still opens its window at the stored-order clock, not its own time (#5, open: whether Hermes
  can store it after a message he sent later).
- **BACKLOG-PRIVACY — a saved private id is checked against its own row.** The saved pass names rows by id
  and keeps each one's time; `view` no longer merges a saved id for a row it reads under that id at another time
  (a replaced database: rebuilt, restored from an older backup, a new install), so his new public messages no longer
  inherit old private answers. An id the view does not read is kept as it is. The first version of this fix tied
  all the ids to one store-wide anchor (the newest row of the database) and dropped them all when it changed; the
  independent review of earlier PR 77 showed that Hermes deletes rows in normal use (a deleted session, a transcript
  rewritten after a model switch or `/restore`), and that the saved ids are not only a floor: a compaction copy whose
  original is older than a reader's recompute is private only by its original's saved id, so dropping them let the
  outage catch-up read a private turn's copies. Hence the check per row, which needs no new state. Limits, both
  toward privacy: a stale id the view never reads stays saved until it ages out (400 days), and a copy whose
  original's id is such a stale one (a replaced database, the original older than the recompute) reads as private.
  The view cache also keys on the sessions database file's own stamp (CodeRabbit, earlier PR 77): a database replaced at
  the same path can keep the newest id.
- **BACKLOG-PRIVACY — the view cache is keyed on every file the pass reads.** The key adds a stamp (mtime in
  ns, size, inode) of the ledger, `sent.jsonl` and `send-times.json` to `conversations.json`'s: a prompt sent by
  another process closes a bare-`/x` window, and the gateway's view cached before it (up to `CACHE_SECONDS`) took his
  reply right after the prompt off the record. Cost: the gateway's cache is taken again after any ledger write.
- **BACKLOG-PRIVACY — the pack's history limit counts the messages it shows.** `context._his_messages` takes
  the newest `telegram_history.limit` messages after mirrors, off-the-record rows and rows with no words are left
  out, within `days` as before, so a burst of `/x` messages no longer pushes his public ones out of the pack.
- **WEB-COMMAND (2026-10-27) — `/web` answers with the web app's address, and never with a secret.** There was no
  chat command that gave him the web app. `/web` is a plain spec beside the others (`commands.web_spec`, so every
  adapter registers it; name checked free in Hermes' registry and added to `config.RESERVED_COMMANDS`). (1) **One
  builder of the address.** `serve.auth.site` built the site's origin (`https://<serve.host>`, `:<serve.public_port>`
  when it is not 443, else the bind address); it moved, unchanged, to `config.serve_site`, which `auth.site` now
  returns, so `/web` reads the same function without the hook importing `journal.serve` (serve's package imports the
  ledger and telegram; config is already loaded everywhere). (2) **Configured means `serve.host` is set.** With no
  host, `serve_site` falls back to the bind address (`http://127.0.0.1:8790`), which is no address for a phone, so
  `/web` says "The web app isn't set up here: serve.host isn't set, so there's no address to give."; a host that is
  not a bare name (doctor's check, now `config.serve_host_ok`) is not given out either. (3) **Never a pairing link,
  code or token.** The chat's history is not a place for a secret: Tailscale identity is the sign-in, and pairing
  stays a CLI step. The reply adds one line on signing in, chosen from config: "On a device signed in to Tailscale it
  lets you straight in." when `serve.tailscale_users` and `serve.socket` are set, else "A browser that isn't paired
  yet is paired from the server, with `journal serve pair`." It reads no state and spawns nothing. A chat that gates
  commands needs `web` in `user_allowed_commands` (a deploy step).
  (4) **Review fixes (earlier PR 84).** *Not answered on arrival:* answering `/web` in the dispatch hook, as `/program` is,
  would have it answered while she is mid-reply (Opus), but the hook runs before Hermes' command gating
  (`user_allowed_commands`), so anyone in the configured chat could have it (CodeRabbit). It stays on Hermes' own
  command path, gated, at the cost the rituals already carry: sent while she is mid-reply, it reaches her turn as
  the text "/web" (the skill tells her to point to `/web`; she has no address or secret to give).
  *A bare host means bare:* `serve_host_ok` (doctor's check too) also refuses `#`, `?`, `@`, `\` and brackets, so a
  host like `journal.example.org#x` is named by doctor and not given out as an address (CodeRabbit).

- **CHANNELS (2026-10-27) — the journal's chat is a channel on any Hermes platform (the owner asked for a plugin
  that is channel-agnostic, usable on any Hermes channel).** A new setting, `channel: {platform, chat_id, user_ids}`:
  `platform` is a Hermes gateway platform name (`telegram`, `discord`, `slack`, `signal`, `whatsapp`, `matrix`,
  `mattermost` …; Hermes' `gateway/config.py` `Platform`), `chat_id` the chat as Hermes names it (the event's
  `source.chat_id`, `sessions.chat_id`), `user_ids` his own ids on that platform. With no `channel` key, or
  `platform: telegram` and no `chat_id`, the channel is `telegram` and `telegram.chat_id`: today's configuration,
  ledger and state are read as they are, with no migration, and a Telegram install behaves byte for byte as before
  (the tests that pinned it pass unchanged). `Config.platform`, `Config.chat_id` (as before) and `Config.owner_ids`
  carry it; `channel.py` answers the questions about it (`ours`, `same_chat`, `label`). One channel at a time is
  the recommendation built (the options are under *Raised*).
  - **Owners.** With `user_ids` set, a message in the chat from anyone else passes through untouched (nothing
    captured, nothing marked) and the transcript reads only the sessions Hermes stored for those users
    (`sessions.user_id`). Empty, everything in the chat is his, as Telegram's private chat always was; right for a
    direct-message chat, which is what the docs recommend (a shared thread is under *Raised*). `journal doctor` warns
    on a channel other than Telegram with no `user_ids`.
  - **Records keep their platform.** A prompt sent to a channel other than Telegram records `platform` (Telegram's
    records stay as they were: no field means `telegram`), and every comparison of a record's chat with the
    configured one (`capture.match`, `ledger.by_message_id`, the sweep's matches, a later prompt superseding an open
    phase, annotation alignment) compares the platform too (`channel.same_chat`). After a switch, an old prompt is
    neither folded nor replied to through the new platform: it is closed in the ledger as it would have folded, and
    its annotations go to the new chat as a plain message.
  - **The `Channel` port is the seam for sending** (`ports.Channel`, beside `Transcript` and `Companion`):
    `send(chat_id, html, reply_to=, silent=, keyboard=)` → message id, `edit(chat_id, message_id, html)`, and `caps`
    (`edit`, `reply`, `keyboard`, `html`); `ports.Keyboard` is a reply keyboard as rows of button texts (none: remove
    it). `cfg.channel()` picks the adapter as `cfg.transcript()` and `cfg.companion()` do: `telegram.TelegramChannel`
    on Telegram (the Bot API exactly as before: the token, HTML, `reply_parameters`, the reply keyboard, edits);
    `hermes.channel.HermesChannel` on any other platform; with `adapter: none` and no Telegram, `NoChannel`, which
    refuses every send. Every send and edit of the engine goes through it (prompts and phases, folds, annotation
    replies, reflections, the catch-up, program messages, `/program`'s answer, `--notify`), and the callers catch
    `ports.ChannelError` (`telegram.TelegramError` is one).
  - **Other platforms send through Hermes' senders, in Hermes' process, without Hermes' mirror.** Hermes' cross-platform
    send (`tools/send_message_tool.py`, behind `hermes send`, cron and MCP) works without the gateway for bot-token
    platforms and returns the message id, but after every send it mirrors the raw text into the chat's session as an
    *assistant* row (`_mirror_sent_message`; `mirror_to_session`'s role default), which the transcript would read as
    her reply (the conversation writer would save it, beside the journal's own framed user-role mirror), and it reads
    `MEDIA:<path>` in the text as a file to upload (no opener's text may attach a file). So `HermesChannel` runs
    `hermes [-p <profile>] journal deliver` (a command of the plugin's own CLI, so it runs in Hermes' interpreter
    with the profile's credentials loaded as `hermes send` loads them) with `{platform, chat_id, text}` as JSON on
    stdin, and `hermes/deliver.py` resolves the target and the platform's config as `send_message_tool` does and calls
    its router (`_send_to_platform`) itself: no mirror, no media. Every other guard `_handle_send` puts around the
    router is kept, in its order (review): lone surrogates scrubbed, a Slack user target opened as its DM, the relay
    egress authorization (it refuses when it cannot answer), and every error redacted (`_sanitize_error_text`)
    before it reaches the ledger or the log; `HermesChannel` repeats only an exception's class from a run that printed
    no answer. The journal's own mirror (framed, user-role, after the turn lease) then runs as on Telegram. These are
    Hermes internals (`_resolve_tool_target`, `_resolve_platform_config`, `_send_to_platform`,
    `_authorize_relay_target`, `_slack_dm_chat_id`, `_sanitize_error_text`, `_sanitize_surrogates`,
    `hermes_cli.send_cmd._load_hermes_env`, `model_tools._run_async`; read at Hermes `e06df9c`): a missing one is a
    failed send with the reason (the tick alerts after three), never a fallback to the mirroring path or a send
    without the guard. The companion's terminal cannot run `hermes journal deliver` while the guard restricts her
    Hermes CLI (`guard.restrict_hermes_cli`, on by default).
  - **A send that starts a process stays off the gateway's event loop.** Hermes runs a sync hook callback inline on
    its loop, so the answer to `/program` or `/next` through `HermesChannel` (seconds: Hermes' CLI starts) would stall
    every platform. `ports.Channel.blocking` says so (`TelegramChannel` False: one bounded HTTPS call, inline as
    before; `HermesChannel` True), and `inbound` sends such an answer, which nothing waits for, from a daemon thread.
  - **Capabilities degrade, never fail.** Out of process Hermes can send and nothing more, so `HermesChannel.caps` has
    no edit, no reply and no keyboard. *No edit:* a prompt is closed in the ledger when it would have folded
    (`collapsed_at`, so capture's rules are unchanged) and the message stays as sent; a re-send leaves the earlier
    message alone. *No keyboard:* a phase goes without buttons; its text already ends with the hint line, and the
    commands are registered on every platform. *No reply:* an annotation reply and the catch-up go as plain messages
    (their header says what they answer). His own direct reply still counts wherever the platform reports what he
    replied to (`reply_to_message_id`: Discord, Slack, Matrix, Signal, WhatsApp …); otherwise the next-message rule
    threads by time. Hermes splits a long message at the platform's limit (Discord 2,000) and returns the last
    part's id, which the ledger keeps: a direct reply to an earlier part is matched by time, or in the sweep by its
    quote. *Markup:* Telegram gets the composed HTML; another platform `compose.plain(html)`, the same text without
    tags; the questions are then shown in full, so a prompt's hint keeps only "reply to this message anytime".
  - **`/x` and the privacy pass are the same code on every platform.** `inbound.receive`, `inbound.begin_turn` and
    `turncontext.build` ask `channel.ours(cfg, platform, chat_id, user_id)` (a message) or `channel.turn_ours` (a
    turn) where they tested the string `telegram`,
    and the transcript reads the configured platform's sessions (`sessions.source`). A message or a turn on another
    platform, in another chat or from another person is not the journal's: it passes through untouched and nothing
    of it is captured or written; an unknown or empty platform is never the journal's (fails closed). The privacy
    pass, its windows and its failure mode are unchanged; it reads the channel's rows.
  - **A turn is the journal's only in the journal's chat.** `pre_llm_call` gives the platform but not the chat; the
    Hermes hook reads the chat and the sender from Hermes' session context (`gateway.session_context.get_session_env`,
    bound for every gateway turn) into `Turn.chat_id` and `Turn.user_id`. A turn in another chat of the platform (a
    Discord server channel beside the DM) gets no pack, program block or today block, and is neither stamped nor
    marked. Where the adapter cannot name the turn's chat or its sender (no Hermes in the process, or a Hermes that
    binds neither), the platform alone decides, as before CHANNELS: `turn_ours` lets such a turn through even with
    `user_ids` set, so for turns the owner check holds only where Hermes names the sender (every gateway turn at
    `e06df9c`). A message's check (`channel.ours`) is not loosened that way: with `user_ids` set, a message whose
    sender is unnamed is not his.
  - **Model-facing words name the platform.** The opener, reflection and catch-up instructions and the pack's notes
    said "Telegram"; they say the channel's name (`channel.label`: "Telegram" stays, byte for byte), and on another
    platform the pack cites his chat messages as `chat:<time>` (`telegram:<time>` on Telegram); `validate` takes both
    as his chat. Internal names stay (`sources.telegram_history`, a reflection record's `telegram`, the
    `telegram.*` settings).
  - **The hook loads the config for every message now** (only then does it know the journal's platform), so a
    configuration that cannot load is logged once per distinct failure (`runtime.config_for_event`) rather than with
    a traceback per message on every platform; messages pass through uncaptured meanwhile, as before, and doctor
    says why. On Telegram that is one log line where there was one per message.
  - **Elsewhere.** `hermes.failure_deliver` defaults to `<platform>:<chat_id>`; doctor's token row is Telegram's only,
    and a `channel` row says where the journal sends, how and with what capabilities. The privacy view's short cache
    is keyed by the channel too. `capture.match`'s next-message rule considers only prompts made in the configured
    chat (on Telegram, where every record's chat is the configured one, nothing changes; it matters after a switch).
  - **Two edges that move on Telegram, on purpose.** (a) A citation whose source starts `chat` (`chat:`, `chats:`,
    `chatgpt:`) was an unknown source and grounded a claim unchecked; it is now his chat, checked against his
    messages, and a near miss is refused (fail closed): an opener citing one falls back to the plain opener. (b)
    `turncontext` and `begin_turn` check the turn's chat when Hermes' session names it: a Telegram turn in another
    Telegram chat (a group) no longer gets the today block or the pack queued for the journal's chat. An install whose
    Telegram sessions are all one chat sees neither.
  - **Checked.** The whole suite passes, with four test edits that change no assertion about behaviour:
    `test_one_program` reaches `telegram` directly (it went through `tick.telegram`, an import the tick no longer
    needs), `test_programs` reads the keyboard's buttons from `compose.KEYBOARD.rows`, `helpers.add_prompt` stamps the
    platform as `send_prompt` does (a no-op on Telegram), and the fake transcript picks the chat's sessions by the
    channel. `tests/test_channels.py` runs a neutral Discord channel beside Telegram through config,
    identity, inbound, the hook, the transcript (both adapters, a shared server channel), the privacy pass, the
    sweep, the conversation writer, both `Channel` adapters, the deliver command and every degrade. The deliver
    command was also run against Hermes itself (`e06df9c`, its in-tree dependencies, a temporary `HERMES_HOME`, lazy
    installs off, the router stubbed so nothing was sent): an unconfigured Discord and an unknown platform come back
    as Hermes' own errors, and with a token in the environment the target and the platform's config resolve and the
    router receives `Platform.DISCORD`, the chat and the text with a `MEDIA:` tag left as text; nothing was written
    outside the temporary home. Not checked: a real send to Discord (no sends in development).


- **BUILTIN-EDIT (the owner, 2026-10-26) — a built-in is edited in place, with a way back to the default.** The
  owner asked that even built-ins be changed in place, with an option to reset to the default. Before, a built-in
  (practice, program, situational) could only be forked: the web app's "Fork to edit" wrote a copy first, programs and
  situational definitions could not be edited in the app at all, and `PUT` of a built-in was `409 builtin`.
  - **His version lives in `practices_dir`, under the built-in's id.** That was already the override model (a user
    file with a built-in's id replaces it whole, `library.load`), so nothing about resolution changed: the same id
    means the same `/start` word and aliases, the same schedule slots and prompt ids, and no duplicate in the library.
    `practices_dir` rather than `state_dir` because his edit is his data, beside his own practices, written under the
    directory's lock, read by the loader that already re-reads on a change, and visible to a notes app if the
    directory moves into the vault (Decision 3); `state_dir` is the engine's own state. A practice or situational
    definition is `<id>.md`; a program is the directory `<id>/` with every file (made whole under a hidden name, then
    renamed, so the loader never reads a half-copied program). The packaged files under `src/journal/library/` are
    never written; `tests/golden` still freezes them.
  - **The first save is the edit.** The web app's Edit opens the editor on the built-in itself; the first `PUT`
    writes his file (no `mtime`: there is no file of his to have drifted; a stale one is ignored) and nothing exists
    until then, so opening the editor and cancelling leaves no copy. "Turn off" on a built-in is one `PUT`.
    `POST …/fork` and `journal practices fork` stay (for editing by hand), and now copy programs and situational
    definitions too. A built-in practice's `GET` reports the window it folds on (`windows.collapse_hours`, as
    `library.fork` already wrote into a fork), so an edit saved unchanged does not move the window to the file's 6.
  - **Reset to default** is `DELETE` (the web app asks once, in place), `journal practices reset <id>`, or deleting
    the file. It removes his file (a program: its `program.md` and the session files that are the shipped program's
    or that his `sessions:` lists, then its folders only when nothing else of his is in them: a session he is
    drafting stays) and the record below. Only a file that is his edit of that built-in counts: one at `<id>.md` or
    `<id>/` that names another id (a fork he renamed by hand) is a definition of its own and is never touched.
  - **When a later release changes the shipped built-in, his version is kept, and the change is said.**
    `practices_dir/.library`, the state file of the directory's lock (hidden: the loader, the event stream and a
    notes app skip it), keeps under `defaults.<id>` the shipped files his edit began from and their fingerprint, so
    `library.edit_status` can say "the default has changed since you edited it" (the Library card's badge, the
    detail's Edited panel, `journal practices list|show`) and `library.default_diff` can show what changed in the
    default since (base → shipped now) and the default against his version (`GET /library/{id}/default`, the web
    app's default page, `journal practices diff <id>`). Nothing is merged for him: a release never rewrites his
    file, and Reset takes the new default. Kept whole rather than as a hash so the "what changed" diff is possible.
    An edit made before this record existed (a fork, a hand-made file) or a damaged record reads as "not known",
    never as "changed"; the record of an edit he deleted by hand is dropped by the next write from the app or the
    CLI (every writer prunes under the lock), so until then a new hand-made edit of that id is read against it. Considered and not done: a three-way merge offered as a suggestion (it would write into his
    file on a guess), and a hash only (it could say "changed" but not show what).
  - **A program edited while a run of it is in progress keeps its structure; words may change.** A save or a reset
    whose definition would add, remove, reorder or retype a session or a phase, or move a question id
    (`programdefs.shape`), while a run of that program is active or paused (or completed with session 10's
    interrupts still to come: they are keyed by session and phase), is refused (`library.run_conflict`, checked
    again under the directory's lock by `save_draft` and `reset`, which raise `RunInProgress`: `409 active-run` from
    serve with the run ids, a non-zero exit from `journal practices reset`, a problem in `?validate=1`) until the
    run ends. A program save also re-checks, under the lock, that its other files are the ones the draft was
    checked against (`409 mtime` otherwise). A run that starts in the instant between is not excluded (runs are
    started under the tick's lock, not this one). A program of his own that does not load while a run of it is in
    progress cannot be saved from the app (nothing says which structure the run was keyed by): he mends its files by
    hand, or abandons the run. The run's state and his entries are keyed by session and phase numbers, and his
    answers are matched to questions by id, so a structural change mid-run would put his words under another
    question; a change of words (a question's text, an intro, a transition, a summary's prose) is safe and applies
    from the next send, while what was already sent is in his entries as sent (SENT-AS-SENT). This is the rule
    PRACTICES.md §7 already wrote. *Alternatives weighed:* (a) a run keeps a snapshot of the definition it started
    with, edits applying from the next run: immune even to a release's change, but it threads a snapshot through
    every reader of a run's program (about 20 call sites in `programs.py`, `standing.py`, `programview.py` and serve),
    adds a migration for runs started before it, and puts new code on the tick's and the hook's hot path of a run in
    progress; and a typo fixed in session 7 during a ten-session run would not reach that run. (b) Sessions not yet started take the edit: the same
    snapshot cost per session, and a change to the session list still has to be refused. The guard is at the
    writers, not in `library.problems`: **known, not taken:** a file edited by hand mid-run is not stopped (as
    before), and a completed run whose insights are compiled again later reads the definition as it is then.
  - **Faces.** Web: Edit on every definition; an edited built-in's Edited panel (in force or not, default changed,
    View the default / See what changed, Reset to default); `/library/<id>/default`; programs and situational
    definitions edited as their files, one at a time, raw (FRONTEND.md §1.5's form for `program.md` is not built).
    CLI: `journal practices fork|reset|diff <id>` (the same parser serves `hermes journal practices`). MCP exposes
    no library writes (`practice_list` is read-only and still reports `source`), so nothing changed there. The chat
    (Telegram, the Hermes commands, the companion's tools) edits nothing and was left as it was: the brief allowed at
    most a mention that a built-in is edited, and none was needed.
  - **Names.** The API keeps `source: fork` (`builtin | user | fork`, API.md, the CLI's `--json`); the web app and the
    CLI's table say "edited". Changing the API value would break a client for a word.
  - **Existing tests changed on purpose:** `test_serve_writes` `test_a_built_in_is_409_until_it_is_forked` became
    `test_a_built_in_is_edited_in_place_and_reset_to_the_default` (the rule it pinned is the one the owner reversed);
    `test_library` `test_fork_copies_the_built_in_once` expects `.library` beside the copy; `test_cli` reads "edited"
    in the list and the longer list of what can be forked; the web tests "offers to fork a built-in", "does not offer
    to edit a program here", "forks a built-in before turning it off" and "resets a fork" became their in-place
    counterparts.
- **SCRUB-PERSONAL — the repo holds nothing specific to a deployment (the owner, 2026-10-28, when the repo went
  public).** Everything specific to the person, the companion's deployment and its host lives on that host, outside
  the repo; the published tree is generic.
  - **Overlays and runbooks.** The deployment overlay and the one-time cutover runbook left the repo;
    `deploy/example-overlay.json` has the overlay's shape with placeholder values, and `tests/test_serve.py`'s unit
    tests render it (the example unit is unchanged). README's section on one deployment became a short note on
    overlays. `docs/DEPLOY-FRONTEND.md`, a runbook for one host's network, became a generic guide to a private name.
    `DEPLOY.md` keeps every package's section (side, restart, first checks, rollback) with placeholders; a
    deployment's receipts (its paths, hosts and live checks) stay in its private notes, and later sections are
    written the same way.
  - **Words.** Dated attributions say "the owner"; his chat messages are paraphrased as the requirement they set,
    not quoted. Fixtures use invented names (`PERSON` = Theo / Iris / 🌻 beside `NEUTRAL` = Sam / Ada), ids, places
    (Hillside; Chicago for the travel and daylight-saving tests) and sample text; Asia/Kolkata stays the DST-free
    fixture zone, since many tests rest on its fixed +05:30 offset.
  - **Code.** The predecessor plugin that doctor warns about was one deployment's name in code; it is now
    `hermes.legacy_plugins` (default `[]`): doctor fails exactly as before for a plugin listed there.
  - **The guard.** `tools/check_private.py` greps the tracked tree for regex terms kept outside the repo
    (`$JOURNAL_PRIVATE_TERMS`, default `~/.config/journal/private/terms.txt`) and exits 1 on a hit; CI has no list
    and passes. AGENTS.md asks for it before every push.
  - **Left on purpose:** the GitHub slug (install commands need it); the source adapters (tududi, chat-scrobbler,
    listenbrainz, storyteller, audiobookshelf, grimmory) as optional features; the `brain:` citation kind (a code
    name; renaming it is a migration); the built-in practices' default times; `his`/`her` in the entry keys and the
    API's voices (a format, not a person); Obsidian as the file format the entries follow; Hermes, Telegram and
    Discord as platforms; the fixtures' Asia/Kolkata zone with its `IST` abbreviation, and Kochi (Kerala) as the
    geocoding example that matches it in tests, the web tests and `tools/serve_fixture.py`. Commit hashes cited in
    these docs are history: after a history rewrite they no longer resolve.
  - **Open:** the source adapters and `brain:` (top of this file).
- **CHAT-EDITING (the owner, 2026-10-29) — no editing from chat surfaces (Telegram, Discord).** Forms stay on the web
  app and the CLI; `/web` links there.
- **PHASE-RESEND-MANUAL (the owner, 2026-10-29) — a program's interrupted phase is not resent on its own.** After a
  situational session interrupts a program's open phase, the program does not resend that phase by itself;
  `/start <word>` brings it back. Manual by design. Closes the open point in ONE-PROGRAM-RUN, review fixes (6) ("whether
  the engine should re-send it on its own").
