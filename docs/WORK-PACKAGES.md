# Work packages

Each package is one agent in one worktree (`<worktrees>/journal/<branch>`), one PR to `main`, mergeable alone,
tests named. On the deploy host every build and test suite (`python3 -m unittest discover -s tests`) runs through
the host's resource queue (never a bare run; never `npm` there: the PWA builds in CI). Sizes: S ≤ half a day,
M ≤ two days, L ≤ a week for one agent. "Restart" = the companion's gateway must
restart for the change to be live (hook/tool/command/skill/entries/capture/compose load at gateway start; tick-side
code applies from the next tick after a pinned reinstall).

## Decisions

1. **Value before refactor.** Practices-as-files (WP1–WP2) and the today block (WP3) ship before programs
   (WP4–WP6) and before the ports extraction (WP7–WP8). The transcript readers are touched once, late.
2. **Three restart windows.** Window A after WP0+WP1+WP2+WP3; Window B after WP4+WP5+WP6 (+WP7+WP8 when ready);
   Window C for the cleanup WP13. Serve and the PWA (WP9–WP12) never restart the gateway.
3. **Every package carries a DEPLOY.md section** in the existing style: which side changes, restart or not, what
   the first ticks should show, rollback.
4. **No package edits his words, the vault layout, or the live install;** the practices directory of a Hermes install
   is `plugin-data/journal/practices` unless the person chooses a folder inside his notes.

## Dependency graph

```
WP0 ─┬─ WP1 ── WP2 ─┬─ WP3 ─────────────────────────┐
     │              ├─ WP4 ── WP5 ── WP6 ── WP7 ── WP8 ─┤
     │              └─ WP9 ── WP10 ── WP11 ── WP12 ─────┤
     └───────────────────────────────────────────── WP13 (cleanup, last)
WP14 (plainchat) depends on WP8; optional.
Ops task (DEPLOY-FRONTEND.md) runs beside WP9 and needs the owner's gate approvals.
```

WP3 and WP4 run in parallel after WP2; WP9 can start its auth/static/entries routes after WP2 and stub runs
until WP4 lands.

---

## WP0 — Entry kinds generalised; hook state on disk (S, restart: Window A)

**Goal.** Entries are recognised by pattern + front matter, not a five-kind regex; hidden phase keys never reach
his words; the companion's note tool accepts any id; the hook's in-process state survives a gateway restart.

**Files.** `entries.py` (`_ENGINE_NAME` → `^\d{4}-\d{2}-\d{2}-([a-z][a-z0-9-]*?)(?:-s(\d{2}))?(-\d+)?\.md$`,
`kind_of` → id, `entry_key` → `(id, session, n)`, with a front-matter fallback for `kind:`/`practice:`, `raw_entry`/`his_words`/
`his_conversation` strip `ENGINE_MARK`, front matter writes `practice:`), `capture.add_companion_note` (any id
from a `known_ids` callable; `note`), new `hookstate.py` (pending voice, pending packs, off-record turns, thread
stamps under `ledger.locked_state(state_dir/hook-state.json)` with TTLs), `hermes/hooks.py` uses it, `status.py`
row.

**Tests.** `test_entries_notebook`: `2026-10-20-fix-life-s01.md` recognised; his own `2026-10-20.md` and `-2.md`
ignored; a file with an unknown id but `kind: gratitude` front matter recognised; `his_words` drops a
`%% journal phase:2 %%` line; `test_hermes`: a pending voice note survives a simulated process restart
(module reload) and is still written at `pre_llm_call`; a pack queued before "restart" is still injected; an
off-record turn recorded before "restart" still refuses `journal_add_note`.

**Done.** Suite green; `journal doctor` shows `hook state` row; DEPLOY.md section written.

## WP1 — Library and the four built-in practice files (M, no restart; install only)

**Goal.** Definitions are files; the four rituals are built-ins byte-identical to today's banks and HTML.

**Files.** New `library.py` (parse/render/load/get/scheduled/reserved_ids/problems/validate_file, inherited
defaults, `{#id}` pins, flat front matter via `entries.front_matter`, dotted keys, slots), `library/practices/
{morning,evening,midweek,weekly}.md` **generated** by a one-off script `tools/gen_builtins.py` from
`questions.py`, `questions.py` → facade over the library, `config.py` (`practices_dir`, env layer,
`reserved_ids`), `cli.py` (`journal practices list|show|validate|fork`), `status.doctor` rows (practices dir,
library problems).

**Tests.** `tests/golden/` captured before the change: `bank_lines`, `bank_items`, `bank_with_ids` for the four
kinds and `compose.telegram_html(kind, fixed_dt, opener)` for each; the new code must reproduce them
byte-for-byte. `test_library`: round trip `parse(render(x)) == x` for all built-ins and a user file; override
replaces whole; `enabled: false`; broken user file → problem + built-in used; duplicate ids; reserved ids;
three-field user practice gets every default; several slots; `{#9.2}` pin survives a reorder;
`validate_file` messages.

**Done.** Suite green; `journal practices list` shows four built-ins; no behaviour change in the tick.

## WP2 — The engine keyed by Practice; `/start <id>`; schedule override (M, restart: Window A)

**Goal.** Schedules, openers, folding, reflections and the skill come from the library; a user practice is sent
tonight with no code change; the `schedule` config key still works for one release.

**Files.** `schedule.py` (over `library.scheduled`, multi-slot ids), `compose.py`, `prompts.py`, `validate.py`,
`reflect.py` (`closes:`), `tick.py` (`send_due` by Practice; `send_prompt` extracted), `entries._prompt_md` by
Practice, `config.problems` (`schedule.<id>` warning), `commands.py`/`hermes/commands.py` (`/start <id>` resolving
ids and aliases at call time; ritual commands kept as aliases; `opt_out_problem` learns library ids and the new
commands), `hermes/__init__.py` skill tokens (Kinds rendered from `companion:` lines), `skills/journal-system/
SKILL.md`, `plugin.yaml` (`practices_dir`; `schedule` marked deprecated), `cli.py` `send <id>` accepts any id,
`journal practices migrate-schedule`.

**Tests.** Existing `test_engine`, `test_openers`, `test_reflect`, `test_cli` pass with the default library; a user
practice at 21:30 is due and sent with the plain opener and its bank; a disabled built-in is not due; the
`schedule.evening` override wins over the file and doctor warns; `/start gratitude` spawns `journal send
gratitude`; `/start nope` answers with the library list; `reflect` triggers on `closes: day` when evening is
renamed in a fork; the golden HTML still byte-equal; `opt_out_problem("/start")` is rejected.

**Done.** Suite green; DEPLOY.md section: install + restart; first ticks unchanged; `/start evening` works.

## WP3 — Today block and turn context budget (M, restart: Window A)

**Goal.** Once per thread the companion gets a ≤ 1,200-character today block built from state and the tick's
cache; never with a pack; never from the transcript; no source calls in the hook.

**Files.** New `today.py` (`collect`, `render`, `thread_started`), new `turncontext.py` (`build`, `BUDGET`),
`tick.py` `cache` step (`today-cache.json` from `sources.tududi`/`weather` every `today.cache_minutes`),
`hermes/hooks.on_llm_call` → `turncontext.build`, `hookstate` thread stamps (`last_his`, `last_send`,
`today_injected_at`), `status.py` (injections per day), `config.py` (`today.*`), `cli.py` (`journal today`).

**Tests.** `test_today`: first message of the day → block; 44 vs 46 minutes since `last_his`; a prompt send
between two messages → block; a pack turn → no block (and the block appears on the next thread start); exception in
any collector → that line dropped, never an error; cap enforced from the bottom; the output contains no string from
a `FakeTranscript` row; `sources.*` are patched to raise and `turncontext.build` never triggers them; `collect`
equals what `GET /today` will serialise (fixture shared with WP9).

**Done.** Suite green; DEPLOY.md: restart; verify by messaging the companion after 45 quiet minutes and reading the user
row's `api_content` (read-only).

## WP4 — Programs engine, fix-life content, situational sessions, Telegram delivery (L, restart: Window B)

**Goal.** `/start fix-life` runs the ten sessions over Telegram with reply keyboards, settle-advance, idempotent
transitions, reflect-phase validation and template fallback, and two situational definitions.

**Files.** New `programs.py` (run state, `start/advance/skip/back/pause/resume/abandon/status/step/
complete_session`), `library.parse_session`/`parse_program`, `library/programs/fix-life/program.md` +
`sessions/01…10.md` (ported per PRACTICES.md §1.4/§4.9), `library/situational/{conversation-prep,post-session-
debrief}.md`, `compose.phase_html/phase_text/phase_json/summary_html`, `telegram.send_html(reply_markup=)`,
`entries.py` (session entries, `## Prompt` phase folding, phase key lines, `## Summary` writer), `capture.py`
(phase-aware `record`; `source="web"` accepted), `inbound`/`hooks` (program reply note, spawn `journal program
settle|advance`), `commands` (`/next /skip /back /pause /status`), `cli.py` (`journal program start|next|skip|
back|pause|resume|abandon|status|settle|insights`), `tick.py` (`programs` step; `prune` exempts active runs),
`prompts.reflect_phase_request`, `validate.check_reflect_phase/check_bridge`, `plugin.yaml` (`programs.*`).

**Tests.** `test_programs`: a full ten-session run driven through `capture.capture` with `telegram._call` stubbed
and `agent.ask` raising `AgentUnavailable` throughout → every phase sent as authored (reflect phases with the
template and marker), summaries filled from his verbatim words, session entries named and front-mattered, the
insights export written; state machine: start, resume, skip, back (earlier answers kept), pause/resume, abandon,
jump to session N; settle: voice note then correction within 2 min → one advance with both blocks; duplicated
capture (sweep + hook) → one advance; a lost spawn → the tick's `step` advances; "gateway restart mid-spawn"
(state written, no send) → one send on the next step; `/x` on a phase answer leaves the phase open; a reflect
phase with a model answer quoting words not in his answers → rejected, template sent; content test: every
original `**N.M**` line appears in exactly one ported phase; `entries_on`/`kind_of` recognise `-s03`; ledger prune
keeps an active run's prompts; measure ledger size after the scripted run and record it in the PR.

**Done.** Suite green; `journal program status` lists fix-life; DEPLOY.md: restart; no run exists until he
sends `/start fix-life`; a hand-made predecessor skill, if any, is left for him to retire; first live phase send verifies the reply
keyboard routes `/skip` (fallback documented).

## WP5 — Session-10 interrupts and insights export (M, no restart: tick/spawn side)

**Goal.** The eight interrupts are scheduled only from an `HH:MM` confirmation and sent by the tick; the insights
document compiles deterministically with optional validated synthesis.

**Files.** `programs.py` (`schedule` phase handling, `scheduled` one-offs, `insights`), `schedule.due` (one-offs),
`tick.send_prompt` (one-off records), `reflect`-style synthesis via `agent.ask` + `validate.check_reflection`,
`entries.py` (`kind: insights` file), `compose.interrupt_html`.

**Tests.** Confirmation parse (`2026-11-02 07:00`, `07:00` alone → next day, free text → ask again once, nothing
scheduled); eight times at the stated offsets in local time across a DST change and after `set_location` changes
the timezone; the grace window; a reply to an interrupt lands in the session-10 entry under its label; compiled
export quotes verbatim; a synthesis failing validation leaves the compiled file intact.

**Done.** Suite green; DEPLOY.md: tick-side only.

## WP6 — Companion surface for programs (S, restart: Window B)

**Goal.** She sees the program block on reply turns, has `journal_program status|propose`, and the skill tells her
the rules (stance not persona; never advance; questions as authored).

**Files.** `tools.py` (`journal_program`), `turncontext.py` (program block, ≤ 3,000 chars, replaces the pack),
`skills/journal-system/SKILL.md` Programs section (rendered list of programs), `hermes/__init__.py` registration,
`plugin.yaml` `provides_tools`.

**Tests.** `propose` never mutates run state; `status` in an off-record turn is allowed (read); the block carries
his answers only (fixture with a conversation section); the budget rule holds (notebook + program block, no today,
no pack); the skill renders the program titles.

**Done.** Suite green; bundled into Window B.

## WP7 — Ports and the Hermes transcript/companion adapter (L, restart: Window B, bundled)

**Goal.** Row normalisation lives in one module; the privacy-critical readers take `Row`s; a second transcript
can be faked in tests.

**Files.** New `ports.py`; new `hermes/transcript.py` (today's `sweep.read`, `conversations.rows/classify`
assistant branch, `recovery._rows/_his_text`, `context._his_messages`, `mirror.live_session/turn_running/busy/
_mirror_to_session`, `agent.is_failed_turn`, `_HER_SKIP/_TURN_END/_STEER/_ORIGIN`, audio placeholders), new
`hermes/companion.py`; `conversations.py`, `sweep.py`, `recovery.py`, `privacy.py`, `context.py`, `mirror.py`
consume `cfg.transcript()`; `agent.py` keeps `ask()` and `CommandCompanion`; `config.py` (`adapter`,
`transcript()`, `companion()`); `tests/helpers.py` `FakeTranscript`; new `tests/test_purity.py`.

**Tests.** Every existing suite passes unchanged (they are the behavioural spec); `test_hermes_transcript` replays
today's regex cases (anonymised rows) against the normaliser; the conversation, privacy, sweep and recovery suites
run a second time parametrised over `FakeTranscript`; `test_purity` greps imports and `sqlite3.connect`
call sites; `doctor --deep` samples the newest 50 rows and reports how many normalised to `empty`/`system`.

**Done.** Suite green twice (sqlite and fake); no behaviour change visible to the companion; shipped only in Window B.

## WP8 — Inbound and tools in the core; the adapter thins (M, restart: Window B, bundled)

**Goal.** `hermes/hooks.py` is event↔dict translation only; `inbound.receive` and `tools.call` are the engine's.

**Files.** New `inbound.py` (from `on_dispatch/_on_audio/_off_record/_remember_pack`), `tools.py` handlers and
`call()` with the off-record refusal, `commands.SPECS`, `hermes/hooks.py`, `hermes/tools.py` (registration
only), `hermes/commands.py`.

**Tests.** `test_inbound` drives `receive` with plain dataclasses (text, voice, audio, `/j`, `/x`, bare `/x`
window, off-record fail-closed, program reply); `test_hermes` hook tests pass unchanged; `tools.call` refuses
writes in an off-record turn and allows reads.

**Done.** Suite green; `hermes/hooks.py` under 150 lines.

## WP9 — `journal serve`, read-only, auth, unit template (M, no gateway restart; new user unit)

**Goal.** The API's GET routes, pairing, cookie/bearer auth, Origin/Host checks, SSE, static PWA serving, the
systemd template and doctor rows, with both privacy fences as tests.

**Files.** New `serve/{app,api,auth,static,sse,unit}.py`, `cli.py` (`serve`, `serve pair|token|check|--print-unit`),
`config.py` (`serve.*`), `status.doctor` rows (serve token, bind reachable, assets present, same version as
tick), `deploy/journal-serve.service.example`, the deployment's overlay (`serve.bind`, `serve.port`, `serve.host`,
`serve.token_command`, `serve.trusted_proxies`; kept outside the repo, `deploy/example-overlay.json` shows the shape).

**Tests.** `http.client` against an ephemeral loopback port on a temp journal: 401 without token, 403 wrong
Origin on a write, 403 wrong Host when `serve.host` set, pairing single-use and expiring, path confinement on
`/media` (traversal, unreferenced file), document blocks for a fixture entry with conversation, notes and a
program session, `/entries?q` excludes her text, `/today` equals `today.collect`; `test_serve_fence` (import grep);
`test_serve_privacy` (`sqlite3.connect` patched to raise, every route); CSP and `no-store` headers present; SSE
emits `entries-changed` on a file touch; bind retry exits 1 after the timeout.

**Done.** Suite green; `journal serve --print-unit` renders the deployment's unit from its overlay; the ops task in
DEPLOY-FRONTEND.md can start.

## WP10 — Serve writes (M, no gateway restart; serve unit restart)

**Goal.** Notes and blocks from the web, meta edits, library PUT/fork/DELETE with mtime 409, spawned sends and
program transitions, settings PATCH.

**Files.** `serve/api.py` write handlers, `library.render` dry-run (`?validate=1`), `capture` `source="web"`,
`entries` meta writer, `config.write_values` allow-list.

**Tests.** A web capture folds the Telegram prompt (`needs_edit`); a web block on a program phase advances by the
program's rule (spawn stubbed); PUT writes nothing on 422; built-in PUT is 409 until forked; mtime drift 409;
DELETE with an active run 409; Hermes-owned setting 409 with the CLI hint; env-owned key 409;
`allow_write: false` → 405 everywhere; `test_locks` (hook capture + tick + web write concurrently).

**Done.** Suite green; serve unit restarted; DEPLOY.md section (serve side only).

## WP11 — PWA read views (L, no gateway restart)

**Goal.** Today, Entries, Entry (two-voice), Library (read), Insights, Settings (read), pairing page, SW, CI build
check. WP11 shipped the read views without the PWA layer (a misreading of the owner's request for a web app rather
than a phone app, 2026-10-19); the **PWA** package restores the layer (manifest, service worker, offline reads, the
mobile pass; the owner confirmed on 2026-10-20 that he wants the PWA layer and a mobile UI).

**Files.** `web/**`, `src/journal/serve/static/**` (committed build), `.github/workflows/web.yml`,
`tests/fixtures/api/*.json` shared with Python tests, `status.doctor` build row (`static/build.json`).

**Tests.** Vitest: `Document` renders his blocks plain and hers folded with labels from fixture JSON; unknown API
fields dropped; `WeekStrip` from `today.week`; the error-and-retry state of a failed read; the service worker's routing
rules and the offline ribbon logic (PWA package). Playwright smoke in CI only. CI fails when the committed build differs.

**Done.** Installable at the private URL; reads work offline after one visit (the PWA package).

## WP12 — PWA editors and program controls (M, no gateway restart)

**Goal.** Question-set editor (form + raw), New practice, Fork/Reset, Run screen with Next/Skip/Back/Pause and
the phase composer, voice recording, meta sheet, Settings writes.

**Files.** `web/src/routes/{editor,run}.tsx`, `components/{QuestionSet,Recorder,Composer}.tsx`, and as built:
`components/{Sheet,QuickAdd,MetaSheet,RunCard,ConfirmButton}.tsx`, `editorModel.ts`, `settingsModel.ts`, `drafts.ts`, the write
client in `api.ts`; the Library, Today, Entry and Settings routes gain their buttons. Programs are listed and run, not
edited (DECISIONS.md, "Raised by packages").

**Tests.** Vitest round-trip of form → `definition` → rendered raw (via fixture from `library.render`); 409/422
handling; recorder uploads then posts a block (mocked API). Playwright smoke extended.

**Done.** A three-field practice created from the phone is due on the next tick.

## WP13 — Cleanup (S, restart: Window C)

**Goal.** Remove the `schedule` config override, the `questions.py` facade's legacy constants, `config.KINDS`;
`plugin.yaml` schema final; README and DEPLOY.md rewritten for the end state; skill final.

**Tests.** Suite green with the shims gone; `migrate-config` rewrites an old `schedule` block into forked practice
files in `practices_dir` with a note.

**As built** (DECISIONS.md, "Taken by packages", WP13): `questions.py` is deleted rather than thinned; `config.KINDS` and
`DEFAULT_SCHEDULE` are replaced by `library.builtin_ids()`; `practices migrate-schedule` is removed in favour of
`migrate-config` (forks written with `--to`, `--practices-dir` to place them); a leftover `schedule` key is a config
warning, not an error; `plugin.yaml`'s schema equals `config.TOP_LEVEL_KEYS` under a test. The WP7 thin entry points
the suites still call, and `audio.py`'s Hermes import, are left.

## WP14 — `plainchat` adapter (M, standalone only; optional)

**Goal.** Prove the ports: an OpenAI-compatible `Companion`, a sqlite `Transcript` of its own, a REPL and a
Telegram long-poll loop, so a standalone user has capture without Hermes.

**Tests.** The conversation/privacy/sweep suites parametrised over `PlainTranscript`; `inbound.receive` and
`turncontext.build` driven from the loop; no Hermes import anywhere in the adapter.

---

## Deploy windows for a live install (summary; details per package in DEPLOY.md)

| Window | Packages | Install | Restart | First checks |
| --- | --- | --- | --- | --- |
| A | WP0, WP1, WP2, WP3 | `hermes -p <profile> plugins install beejsbj/journal --force --ref <sha>`; `uv tool install --force git+…@<sha>` | yes | `journal doctor` (practices dir, library ✓, `schedule` warning, hook state, same version); `/start evening` works; after 45 quiet minutes a message carries the today block; golden Telegram HTML unchanged |
| B | WP4, WP5, WP6 (+WP7, WP8) | same, one sha | yes | `journal program status`; `/start fix-life` intro; first phase's keyboard routes `/skip`; `journal_program status` from the companion; `doctor --deep` normaliser sample |
| serve | WP9, WP10, WP11, WP12 | `uv tool install --force git+…@<sha>`; `systemctl --user enable --now journal-serve`; ops task | no | `journal serve check`; `/healthz` from inside the reverse proxy; postconditions in DEPLOY-FRONTEND.md |
| C | WP13 | same | yes | doctor clean, no deprecation rows |

Each window: pick a slot at least an hour from a ritual (06:00, Wed 12:00, Sun 20:00, 23:00 local time); the
restart takes every agent on the gateway offline for about a minute; rollback is `--force --ref <previous sha>` +
restart, as DEPLOY.md already describes.
