# Architecture: the journal as a CLI, a Hermes plugin, and a served frontend

Design against `beejsbj/journal` at `5b689b0` (2026-10-16). Synthesised from three candidate designs and two
review rounds; where this document disagrees with a candidate, this document wins. Companion docs:
[PRACTICES.md](PRACTICES.md) (definitions, programs, the today block), [API.md](API.md) (`journal serve`),
[FRONTEND.md](FRONTEND.md) (the PWA), [WORK-PACKAGES.md](WORK-PACKAGES.md) (order of work),
[DEPLOY-FRONTEND.md](DEPLOY-FRONTEND.md) (exposing it on a host).

## Decisions

1. **Core first, shipped value first.** The end state is a deep agent-neutral core behind two ports (`Transcript`,
   `Companion`) with a thin Hermes adapter. The *order* is value first: practices-as-files and the today block
   land before the ports refactor, so the privacy-critical transcript readers are touched only once, late, and
   never restarted for a refactor alone.
2. **One file format, stdlib-parseable.** Practice, program and situational definitions are Markdown with a flat
   `key: value` front matter read by the existing `entries.front_matter`. Dotted keys nest (`schedule.1.time`);
   lists are comma-separated. PyYAML stays optional (Hermes settings only); `dependencies = []` stays true.
3. **The engine owns capture and sending; serve and the companion never send in-process.** Anything that sends or
   advances a program from `journal serve` is a spawned `journal send <id>` / `journal program …` subprocess (the
   existing `commands.trigger` pattern). An HTTP worker never holds `tick.lock`.
4. **Serve never reads the chat transcript.** It reads entry files, state JSON (ledger, runs, today cache), the
   library and reflection files. Fenced by an import test and a test that patches `sqlite3.connect` to raise.
5. **The companion's power over programs is read-mostly.** `journal_program` offers `status` and `propose`; only
   his text (`/start`, `/next`, `/skip`, `/back`, `/break`, replies) and the PWA drive the state machine.
6. **Program advance is settle-then-advance by default.** A captured phase answer advances after a 2-minute
   settle (a correction or second voice note joins the same phase); `/next` advances at once; a program may set
   `advance: next` for explicit stepping. Idempotent on `(run, session, phase, last capture id)`.
7. **Context budget in code.** At most two injected blocks per turn; the today block never on a turn that
   already carries a reply pack or a program block; the notebook stays per-turn (no contract change); no source
   calls (tududi, weather) in `pre_llm_call`: the hook reads tick-cached data only.
8. **Hook state moves to disk.** `_pending_voice`, `_pending_pack`, `_off_turns` live in `state_dir/hook-state.json`
   under `ledger.locked_state`, so a gateway restart loses nothing.
9. **Network model follows the private-service pattern of the deploy host:** `serve.bind` is configurable; on the
   host it binds the Tailscale IP, the firewall admits only `10.0.0.0/24` (the reverse proxy's subnet), the reverse
   proxy routes to that IP, local DNS rewrites the name, and the tunnel/CDN denies with `403` *before* the proxy's
   router exists. Loopback is for dev/tests.
10. **The engine only appends his words.** No engine path, model or program edits an existing block of his, and the
    companion's words are never editable on any surface. He can edit and delete his own blocks from the web app
    (DECISIONS.md, Decision 4, amended 2026-10-20): plain Markdown he can already change in his notes app. README.md,
    *Guarantees*, names the code and tests that hold this.
11. **`practices_dir` defaults outside the vault.** The vault's own rule (automation writes only flat entries in
    the journal folder) is honoured; moving it inside needs a one-line amendment to the vault's `AGENTS.md` and the
    journal folder's `README.md`, written by the owner's hand.
12. **Inline Telegram buttons are a Hermes-adapter nicety, never required.** Hermes exposes
    `ctx.register_telegram_handler(factory)` (`hermes_cli/plugins.py:846`) and hoists plugin handlers ahead of
    its own `CallbackQueryHandler` (`plugins/platforms/telegram/adapter.py:2895`, `_wire_plugin_handlers`
    `:2904`), so a `pattern=`-scoped callback handler is possible; the agent-neutral path is slash commands and
    reply keyboards, and that is what ships first.
13. **Both installs pin the same sha.** The uv-installed `journal` (serve) and the plugin's `src/` (gateway, tick)
    must match; `journal doctor` has a `same version as tick` row and DEPLOY.md pins both in one step.
14. **The journal's chat is a channel on any Hermes platform, and sending is a port** (CHANNELS, 2026-10-27).
    `channel: {platform, chat_id, user_ids}` names the one chat the journal works in (default: Telegram and
    `telegram.chat_id`, so an existing install changes nothing). Sending goes through a third port, `Channel`
    (`send`, `edit`, a reply target, a keyboard, `caps`), beside `Transcript` and `Companion`; the Telegram adapter
    is the Bot API as it always was, every other platform sends through Hermes' own senders in Hermes' process
    (`hermes journal deliver`), and what a platform cannot do (edit, reply, keyboard) degrades, never fails.

## 0. Where Hermes couples today (verified at 5b689b0)

| Coupling | Modules and functions | Future home |
| --- | --- | --- |
| Reading `state.db` with Hermes row semantics (`display_kind`, `tool_calls`, `[Replying to: "…"]`, `Gateway message origin`, `[OUT-OF-BAND USER MESSAGE]`, `failed_turn` notices, compaction frames, `[Cron delivery:`, audio placeholders, `[Journal: ` mirrors) | `conversations.rows/classify/_ro/anchor/max_id`, `sweep.rows/read/_ro`, `recovery._rows/_his_text`, `context._his_messages/make_verifier/companion_reply_after`, `privacy.view` (via `conversations.rows`), `mirror.live_session/turn_running/busy` | `hermes/transcript.py` → `ports.Row` |
| Asking the companion | `agent.argv` (`hermes chat --oneshot`), `agent.is_failure_text/is_failed_turn`, `cfg.hermes_env/profile_args` | `hermes/companion.py` (`HermesCompanion`), `agent.py` keeps `ask()` and `CommandCompanion` |
| Hook glue that also orchestrates capture | `hermes/hooks.py` (`on_dispatch`, `_on_audio`, `_off_record`, `_remember_pack`, `on_llm_call`, `_take_pack`, `_capture_voice`, `_note_off_turn`, `off_record_block`) | engine side: `inbound.py`, `turncontext.py`; adapter keeps event↔dict translation |
| Writing into her session | `mirror._mirror_to_session` (imports `gateway.mirror`) | `Transcript.mirror()` on the Hermes adapter; `NullTranscript.mirror()` returns `False` |
| Sending into the chat (CHANNELS) | `telegram.send_html/edit_html` called from `tick`, `programs`, `reflect`, `recovery`, `inbound`, `cli`; `/x` and turns keyed on the string `telegram` | `ports.Channel` (`cfg.channel()`): `telegram.TelegramChannel`, `hermes/channel.py` (`HermesChannel` → `hermes journal deliver`); `channel.ours` for whose message and which chat |
| Tool/command registration | `hermes/tools.py`, `hermes/commands.py`, `hermes/__init__.py` | `tools.py` (specs + handlers), `commands.py` (specs); adapter registers |

Everything else (entries, capture rules, ledger, schedule, compose, prompts, validate, reflect, notebook,
outage, audio, guard rules, location, status) is agent-neutral already; `telegram` became the Telegram adapter of
the `Channel` port (CHANNELS).

## 1. Package layout (end state)

```
plugin.yaml, __init__.py             repo root = Hermes plugin (wrapper → journal.hermes.register)
pyproject.toml                       console script `journal`; extras: hermes = [pyyaml]; no extra for serve
web/                                 PWA source (Preact + TypeScript + Vite); not a Python package
deploy/example-overlay.json          an overlay's shape (names, paths, serve.*); real overlays live outside the repo
deploy/journal-serve.service.example rendered by `journal serve --print-unit` from config
src/journal/
  ports.py            Protocols the core consumes (Transcript, Companion) + the Row dataclass (WP7)
  inbound.py          Inbound/Outcome dataclasses; receive(), begin_turn() (WP8: they live here, not in ports.py)
  channel.py          the journal's chat (CHANNELS): which platform and chat, whose messages (`ours`), a record's chat
                      (`same_chat`), the platform's name for the model (`label`)
  config.py           + env layer for paths/secrets, + practices_dir/media_dir/serve.*/programs.*/today.*
  library.py          NEW  loads/parses/renders practice, program and situational definitions; built-ins + user dir
  programs.py         NEW  runs: state machine, step delivery, summaries, insights export, scheduled one-offs
  today.py            NEW  today.collect(cfg) dict + render(); the thread-start block and GET /api/today share it
  turncontext.py      NEW  what the companion is told on a turn (notebook, pack, program block, today); budget rule
  inbound.py          NEW  engine-side handling of one incoming message (today's hooks.on_dispatch/_on_audio/_off_record)
  tools.py            NEW  agent-neutral tool specs + handlers (journal_add_note, journal_set_location,
                           journal_notebook, journal_program) and commands.SPECS
  hookstate.py        NEW  pending voice / pending pack / off-record turns, persisted under ledger.locked_state
  questions.py        (removed in WP13: the goldens and tools ask the Practice)
  schedule.py         due() over every scheduled practice + runs' one-off scheduled steps
  capture.py, entries.py, compose.py, prompts.py, validate.py, reflect.py   keyed by Practice, not `kind in KINDS`
  privacy.py, conversations.py, sweep.py, recovery.py, context.py, mirror.py   consume ports.Row via cfg.transcript()
  agent.py            ask() facade + CommandCompanion (argv template); raises AgentUnavailable
  telegram.py         the Bot API; `TelegramChannel`, the `Channel` adapter for Telegram (+ reply_markup on send_html)
  ledger.py, notebook.py, outage.py, guard.py, status.py, tick.py, cli.py, migrate.py, location.py, audio.py, sources.py
  library/            built-in definitions (package data)
    practices/morning.md evening.md midweek.md weekly.md
    programs/fix-life/program.md  programs/fix-life/sessions/01-anti-vision.md … 10-one-day-reset.md
    situational/conversation-prep.md  situational/post-session-debrief.md
  serve/              `journal serve`: app.py (ThreadingHTTPServer + router), api.py (one function per route),
                      auth.py (token, pairing, cookie, Origin), static.py, sse.py, unit.py (--print-unit)
    static/           committed PWA build (index.html, assets/, sw.js, manifest.webmanifest)
  hermes/             the Hermes adapter (thin): __init__ (register), hooks, tools (registration only), commands,
                      cron, runtime, transcript.py (HermesTranscript: state.db → Row), companion.py (HermesCompanion),
                      channel.py (HermesChannel: any other platform, through `hermes journal deliver`), deliver.py
                      (that command, run inside Hermes: its senders without its mirror)
  skills/journal-system/   skill template; gains a "Programs" section rendered from the library
tests/              unittest suites; helpers.py keeps Telegram, the model and gateway.mirror unreachable
```

Rule enforced by `tests/test_purity.py` (a grep over `src/journal`): no module outside `journal/hermes/` imports
`hermes_*`, `gateway.*`, `agent.*` (Hermes packages) or calls `sqlite3.connect` on anything but its own
`state_dir` files (the `sources.py` readers open their own read-only SQLite files; they are listed as allowed).

## 2. Core versus adapters: the ports

```python
# ports.py (as built in WP7; DECISIONS.md "WP7 — the ports as built")
@dataclass(frozen=True)
class Row:                        # one normalised transcript row, either role
    id: int                       # monotonic in stored order (the order the model saw it)
    at: datetime                  # UTC
    role: str                     # "user" | "assistant"
    kind: str                     # user: his | handled | mirror | system | boundary | empty
                                  # assistant: hers | tool_step | failed_turn | turn_end | frame | empty
    text: str                     # his words alone (envelopes removed) / her text, stripped
    platform_message_id: str | None
    quote: str | None = None      # the message he replied to, when the platform stores it
    source: str = "text"          # text | voice | audio
    audio_path: str | None = None
    meta: Mapping = {}            # uid, stored_id, active, file_name; his: notice, internal, caption, loose, frame,
                                  # delivery; hers: ends_turn, outage, reply (each documented in ports.py)
    copy: int | None = None       # the row this one repeats (stored again by compaction): read once
    stored: str = ""              # the text exactly as stored (her reply verbatim; the journal's own marks in his)

class Transcript(Protocol):       # read-only view of the companion chat (+ one framed write: mirror)
    source: str | None            # what is read (kept in state files as "db"); None: no chat (NullTranscript)
    errors: tuple                 # what a read raises when the chat cannot be read now
    def available(self) -> bool: ...
    def rows(self, after_id: int = 0, since: datetime | None = None, upto_id: int | None = None) -> list[Row]: ...
    def tail(self, n: int) -> list[Row]: ...                     # doctor --deep
    def max_id(self) -> int: ...
    def anchor(self, row_id: int) -> dict | None: ...            # conversations.same_db
    def first_id(self, since: datetime) -> int | None: ...       # the backfill's hand-over cursor
    def busy(self) -> bool: ...                                  # a turn is running right now
    def last_activity(self, before: datetime) -> datetime | None: ...   # newest row of either role
    def mirror(self, text: str, *, flushing: bool = False) -> bool | str: ...  # True | "queued"; raises NotMirrored

class Companion(Protocol):
    def ask(self, query: str, budget: int, toolsets: str | None = None) -> str | None: ...  # raises CompanionUnavailable

@dataclass(frozen=True)
class Caps:                       # what a platform can do with a message (CHANNELS)
    edit: bool = False            # change a sent message (the fold)
    reply: bool = False           # send as a reply to a given message (annotation replies, the catch-up)
    keyboard: bool = False        # reply-keyboard buttons (a phase's /next /skip /back /break)
    html: bool = False            # takes the composed Telegram HTML; else plain text (compose.plain)

@dataclass(frozen=True)
class Keyboard:                   # a reply keyboard: rows of button texts; no rows removes it
    rows: tuple[tuple[str, ...], ...] = (); placeholder: str = ""

class Channel(Protocol):          # sending into the journal's chat; raises ChannelError
    platform: str
    caps: Caps
    def send(self, chat_id: str, html: str, *, reply_to: str | None = None, silent: bool = False,
             keyboard: Keyboard | None = None) -> str: ...      # the new message's id; ignores what caps lack
    def edit(self, chat_id: str, message_id: str, html: str) -> None: ...  # only when caps.edit

class NullTranscript:             # standalone: every chat-reading step is a no-op, as today
    source = None                 # rows/tail → [], max_id → 0, anchor/first_id/last_activity → None,
                                  # busy/available → False, mirror → False
```

`Row.kind` is where Hermes row semantics are decided once, in `hermes/transcript.py`: today's `sweep.read`,
`conversations.classify`'s assistant branch, `agent.is_failed_turn`, `_HER_SKIP`, `_TURN_END`, `_STEER`, `_ORIGIN`,
audio placeholders, `[Cron delivery:` → `boundary`, `[Journal: ` → `mirror`, the hook's note → `handled`.
`privacy.compute`, `conversations.classify/_Threads`, `sweep.reconcile`, `recovery.scan`, `context` keep their
logic and take `Row`s. The ledger-dependent parts of `classify` (is this message already captured; which entry is
home) stay in core: they are about the journal, not about Hermes. Where the readers read rows differently before the
ports, the difference is kept as a `meta` finding rather than unified (DECISIONS.md, WP7, raised), so no reader changed.

Resolution: `cfg.transcript(db=None)` returns `HermesTranscript` whenever a sessions database is configured
(`hermes.state_db`, else the profile's `state.db`; a missing file is the readers' "unavailable", as before), else
`NullTranscript`; `adapter: none` means no transcript. `cfg.companion()` returns `CommandCompanion` when
`agent.command` is set, else `HermesCompanion` (with `adapter: none`, `NoCompanion`, which waits like an outage).
`agent.ask(cfg, …)` keeps its signature and delegates. `cfg.channel()` returns `telegram.TelegramChannel` when
`channel.platform` is `telegram` (the default), else `hermes.channel.HermesChannel` (with `adapter: none`,
`NoChannel`, which refuses every send). The transcript reads the configured platform's sessions (`sessions.source`),
and with `channel.user_ids` set only those users' sessions. `tests/helpers.py` has a `FakeTranscript(rows)`; the
conversation, privacy, sweep and recovery suites run a second time over it (`tests/test_fake_transcript.py`; their
sqlite fixtures stay as the Hermes-adapter tests).

**What an adapter for another agent must implement**, as a checklist: (a) a `Transcript` or accept
`NullTranscript` (no conversation saving, no `/x` windows beyond the hook, no catch-up); (b) a `Companion`;
(c) call `inbound.receive(cfg, Inbound)` before the model sees a message and apply the `Outcome` (rewrite text,
drop, or pass); (d) call `turncontext.build(cfg, Turn)` per turn and inject the string; (e) register `tools.specs(names)` and
route calls through `tools.call(cfg, name, params, turn)`, which runs the off-record refusal; `guard.check` is not part
of `call`: run it in the agent's own pre-tool hook, on its own tools (Hermes: `pre_tool_call`); (f) register
`commands.SPECS` (`/j`, `/x`, `/start`, `/next`, `/skip`, `/back`, `/break`, `/program`, and the four ritual
sends); (g) a `Channel` for its platform, or accept `HermesChannel` where Hermes' senders reach it. Hermes does all
seven. The `/x` marking in `receive` and `begin_turn` (and so the refusal that rests on it) applies in the configured
channel (`channel.ours`: platform, chat and owners; DECISIONS, CHANNELS), on whichever platform that is. A second adapter (`plainchat`: OpenAI-compatible companion + its own sqlite
transcript + REPL/long-poll loop) is the proof of the seam and is the last, optional package.

### 2.1 Engine-side inbound and turn context

```python
# inbound.py (as built in WP8; DECISIONS.md "WP8 — the seam as built")
@dataclass(frozen=True)
class Inbound:
    platform: str; chat_id: str; message_id: str; text: str; at: datetime
    user_id: str | None = None    # the sender (CHANNELS: with channel.user_ids, only his messages are the journal's)
    reply_to: str | None = None
    kind: str = "text"            # text | voice | audio | document | photo | video | other
    media_path: str | None = None; mime: str = ""; file_name: str | None = None
    has_media: bool = False       # any attachment (a bare /x with one is a message, not a window)
    meta: Mapping = {}            # audio.event_meta: original name, duration, forward date

@dataclass(frozen=True)
class Outcome:
    action: str                   # "pass" | "rewrite" | "drop"
    text: str = ""                # the rewritten text the model should see
    captured: dict | None = None  # capture info, for logging
    reason: str = ""              # why a message was dropped

def receive(cfg: Config, msg: Inbound, *, spawn: Spawn | None = None) -> Outcome: ...  # fail-open for capture,
                                                                                         # fail-closed for privacy
def begin_turn(cfg: Config, turn: Turn, *, spawn=None) -> None: ...   # voice transcripts; the off-record turn mark

# tools.py
def specs(names) -> tuple[ToolSpec, ...]: ...             # name, schema, description, emoji: the registration list
def refusal(cfg, name, params, turn: ToolTurn | None) -> str | None: ...   # a write in an off-record turn; fails closed
def call(cfg, name, params, turn=None, **env) -> str: ... # refusal, then the handler; JSON, never raises

# commands.py
SPECS = specs()   # Spec(name, description, run(cfg, raw_args, spawn) -> reply | None, needs_config, unconfigured)

# turncontext.py
@dataclass(frozen=True)
class Turn:
    platform: str; session_id: str | None; turn_id: str | None; user_message: str; at: datetime
    chat_id: str | None = None; user_id: str | None = None   # from the adapter's session, when it knows them

def build(cfg: Config, turn: Turn) -> str | None: ...    # notebook + (pack | program block) + today, under BUDGET
BUDGET = {"blocks": 2, "today_with_pack": False}
```

`hermes/hooks.py` shrinks to: event → `Inbound`, `Outcome` → Hermes hook dict; `pre_llm_call` → `Turn` →
`inbound.begin_turn`, then `{"context": turncontext.build(…)}`; `pre_tool_call` → `tools.refusal(cfg, name, args, turn)`
then `guard.check`. `spawn` is the one thing the engine cannot do for itself (`python -m journal …` detached, with the
adapter's environment): the adapter passes it to `receive`, `begin_turn` and `commands.SPECS[i].run`.

## 3. Config layering and storage

Layers, lowest first (the first three exist):

1. `DEFAULTS`
2. JSON config file (`--config`, `$JOURNAL_CONFIG`, Hermes `config_file`, XDG default)
3. Hermes plugin settings
4. **Environment overrides, only for paths and secrets, never written back:** `JOURNAL_ROOT`,
   `JOURNAL_STATE_DIR` (exists), `JOURNAL_PRACTICES_DIR`, `JOURNAL_MEDIA_DIR`, `JOURNAL_SERVE_BIND`,
   `JOURNAL_SERVE_TOKEN`. `origins[key] = "env"`; `config.write_values` refuses an env-owned key with the reason.

New keys (all names generic; a deployment's overlay supplies its values):

| Key | Default | Meaning |
| --- | --- | --- |
| `practices_dir` | Hermes: `<state_dir>/practices`; standalone: `$XDG_CONFIG_HOME/journal/practices` | User-defined definitions and his in-place edits of the built-ins (BUILTIN-EDIT; `.library` keeps the default each edit began from). The PWA edits here. Outside the vault by default (Decision 11). |
| `media_dir` | `root` | Where kept audio lands; the vault's convention keeps audio beside entries, so the default stays. |
| `schedule.<id>` | *(retired in WP13)* | No longer read: when a practice is sent is its file's `schedule.*` lines. A key left in a config layer is ignored and named in doctor; `journal migrate-config` moves one into forked practice files. `deploy/example-overlay.json` does not carry it. |
| `programs.enabled` | `true` | Program delivery on/off. |
| `programs.settle_minutes` | `2` | Default settle before an auto-advance (Decision 6). |
| `programs.reflect` | `true` | Allow model-written `reflect` phases (validated); `false` = always the template. |
| `today.enabled`, `today.max_chars`, `today.sources` | `true`, `1200`, `["standing","practices","programs","entries","reflection","tasks","weather"]` | The thread-start block ([PRACTICES.md](PRACTICES.md) §6). |
| `today.cache_minutes` | `30` | How often the tick refreshes `today-cache.json` (tasks, weather, the time of his last public chat message). |
| `pack.max_chars`, `pack.entries`, `pack.lookback_days`, `pack.returning_days`, `pack.pointers` | `32000`, `6`, `14`, `3`, `[]` | The context pack (CONTEXT-PACK): its size budget, his entries by count, how far back unanswered prompts and "last heard" reach, the gap that asks for the returning stance, extra pointers. |
| `serve.bind`, `serve.port` | `127.0.0.1`, `8790` | Loopback for dev and tests; a deployment's overlay sets the Tailscale IP and its own port. |
| `serve.host` | `""` | Canonical host for the `Host` check and cookie scope (`journal.<domain>`); empty = skip the check. |
| `serve.token_env`, `serve.token_command`, `serve.token_key` | `JOURNAL_SERVE_TOKEN`, none, `JOURNAL_SERVE_TOKEN` | Same resolver as the bot token. |
| `serve.allow_write` | `true` | Anything but `true` turns every route that changes the journal into 405 (pairing and logout stay). |
| `serve.assets_dir` | packaged `serve/static` | Override for a dev build. |
| `serve.trusted_proxies` | `[]` | CIDRs whose `X-Forwarded-*` are believed (e.g. the reverse proxy's subnet, `10.0.0.0/24`). |
| `serve.public_port` | none (443) | The HTTPS port the site is published on when not 443 (SERVEPORT). |
| `serve.socket` | none | A Unix socket served as well as TCP (0600), for `tailscale serve … unix:<path>` (TSAUTH). |
| `serve.tailscale_users` | `[]` | Logins signed in by `Tailscale-User-Login` on the socket only, from tailscaled (root peer) only, for a client that is not this host and not marked cross-site (TSAUTH). |
| `adapter` | `auto` | `hermes` / `none` / `plainchat`; auto = hermes when `hermes_home` is set. |
| `channel.platform`, `channel.chat_id`, `channel.user_ids` | `telegram`, `telegram.chat_id`, `[]` | The journal's chat (CHANNELS): any Hermes gateway platform, the chat as Hermes names it, and his ids there (empty: the chat is his, as a DM is). |

Storage, by owner:

| Path | Owner | Contents |
| --- | --- | --- |
| `root/` (flat) | engine | entries `YYYY-MM-DD-<id>.md`, program sessions `YYYY-MM-DD-<program>-sNN.md`, analyses, audio |
| `state_dir/ledger.json` (+`.lock`) | engine | prompts, captures, reflections, recovery; program phase prompts too |
| `state_dir/runs/<program>/<run>.json` | engine | run state (pointers only; never his words) |
| `state_dir/hook-state.json` | engine | pending voice, pending packs, off-record turns, thread stamps (`last_his`, `last_send`, `today_injected_at`) |
| `state_dir/today-cache.json` | tick | tasks/weather lines and `heard` (the time of his newest public chat message, read through the privacy pass; never its text), refreshed every `today.cache_minutes` |
| `state_dir/practices/` or `practices_dir` | user, PWA | user definitions and his edits of the built-ins (`.library`: the defaults they began from) |
| `state_dir/packs/`, `audio-stash/`, `mirror-queue.json`, `conversations.json`, `sweep.json`, `send-times.json`, `sent.jsonl`, `notebook.json` | engine | as today; **unservable** by the API |

## 4. Process model

Four processes may touch the journal; locks and order are fixed:

| Process | Runs | Locks it takes |
| --- | --- | --- |
| Gateway (Hermes) with the plugin loaded | hooks, tools, slash commands | `ledger.locked`, `ledger.locked_state(hook-state.json / conversations.json)`; never `tick.lock` |
| Tick (`journal tick`, every 5 min, fresh process) | send → privacy → sweep → conversations → programs → fold → audio → annotate → reply → reflect → recover → mirror → cache → prune → notebook | `tick.lock` (non-blocking: skips the round if held), then `ledger.locked` per write |
| Spawned one-shots (`journal send <id>`, `journal program <action> <run>`) | one send or one program transition | `tick.lock` blocking, then `ledger.locked` |
| `journal serve` (long-running, user unit) | reads; small locked writes; spawns for anything that sends | `ledger.locked` / `locked_state` for writes; **never** `tick.lock` |

Lock order is always `tick.lock` → `ledger.lock` → `<state>.lock`; no process takes them in another order
(`tests/test_locks.py` runs a hook write, a tick and a serve write concurrently in-process and asserts no
deadlock and no lost update). Reads are lock-free: every engine write is an atomic `os.replace`.

The `programs` tick step re-checks every active run: a phase whose settle has elapsed but whose advance spawn was
lost (gateway restart mid-spawn) is advanced here, at most five minutes late. Program sends reuse
`tick.send_prompt(cfg, record, log)`, extracted from `send_due`, so prompts, phases and one-off interrupts share
retries, `sent.jsonl`, mirroring and folding.

## 5. Plugin and CLI installs coexisting

Unchanged mechanics, made explicit:

- **Hermes plugin:** root `plugin.yaml` + `__init__.py` → `journal.hermes.register(ctx)`; `python_runtime: external`;
  `library/**` and `serve/static/**` are package data so the plugin install carries them. `plugin.yaml`'s
  `config_schema` gains `practices_dir`, `programs`, `today`, `serve` and drops `schedule` (WP13: the schema is exactly `config.TOP_LEVEL_KEYS`).
- **CLI:** `uv tool install git+https://github.com/beejsbj/journal@<sha>` gives `journal` with
  `tick | send <id> | program … | practices … | today | serve | status | doctor | …`. `journal serve` is a CLI
  command only; Hermes never runs it.
- **Same code, two entry points:** the cron stub imports `journal.cli` from the plugin's `src/`; `serve` runs
  from the uv install with `--hermes-home <profile>` so `state_dir`, settings and the library resolve identically.
  `journal doctor` row `same version as tick`: compares `__version__` + `version.code_id()` (a sha256 over the
  package's `.py` files, since neither install carries the git sha; WP9) recorded in `last-tick.json` with the
  running process, and fails when they differ. DEPLOY.md pins both installs in one step.
- **Commands without restarts:** `/start <id>` resolves ids against the library at call time, so a new user
  practice or program needs no gateway restart. The four ritual commands stay registered for compatibility.

## 6. Migration map (module by module)

| Today (5b689b0) | End state | Package |
| --- | --- | --- |
| `config.KINDS`, `DEFAULT_SCHEDULE`, `RESERVED_COMMANDS`, `opt_out_problem`, `problems` | `library.scheduled(cfg)` and `library.builtin_ids()`; reserved = `j journal x start next skip back pause status` + the built-in ids; `problems` validates practice files and names a leftover `schedule` key | WP1, WP2, WP13 |
| `questions.py` constants | `library/practices/*.md` generated from the constants (byte-equal assertion); the `questions.py` facade removed | WP1, WP13 |
| `entries._ENGINE_NAME`, `title_for`, `_prompt_md` | pattern `^\d{4}-\d{2}-\d{2}-([a-z][a-z0-9-]*?)(?:-s(\d{2}))?(-\d+)?\.md$` with the id checked against the library plus `j|note`, falling back to front matter `kind:`/`practice:`; `kind_of` → the id (a string), `entry_key` → `(practice_id, session_no, n)`; `raw_entry`/`his_words` strip `ENGINE_MARK` | WP0, WP2 |
| `schedule.due(cfg, now_local, sent_ids)` | iterates `library.scheduled(cfg)` + `programs.scheduled_due(cfg, now)` | WP2, WP5 |
| `compose.plain_opener/header/bank_html/telegram_html/plain_text` | take a `Practice`; `compose.phase_html/phase_text/to_json` for program phases; one renderer per definition | WP2, WP4 |
| `prompts.OPENER_SHAPE`, `validate.LIMITS` | `practice.opener.shape`, `practice.opener.limit`; `validate.check_reflect_phase` | WP2, WP4 |
| `reflect.due` (literal `evening`/`weekly`) | `practice.closes: day|week` | WP2 |
| `capture.add_companion_note` kind enum | any library id or `note` | WP0 |
| `hermes/hooks.py` in-process dicts | `hookstate.py` on disk | WP0 |
| `hermes/commands.py` per-ritual triggers | `commands.SPECS`: `/j /x /start /next /skip /back /break /program` + ritual aliases (Hermes owns `/pause`, `/status`); `trigger()` spawns `journal send <id>` / `journal program …` | WP2, WP4 |
| `hermes/tools.py` | `tools.py` specs + handlers; adapter registers; `journal_program` added | WP6, WP8 |
| `hooks.on_llm_call` notebook + pack | `turncontext.build` with the budget rule and the today block | WP3, WP8 |
| `conversations.rows/classify`, `sweep.rows/read`, `recovery._rows`, `context._his_messages/make_verifier/companion_reply_after`, `mirror.live_session/turn_running/busy/_mirror_to_session`, `agent.is_failed_turn/is_failure_text` | `hermes/transcript.py`, `hermes/companion.py`; core consumes `Row` | WP7 |
| `tick.send_due` | `send_due` + extracted `send_prompt`; `programs` and `cache` steps added | WP4, WP3 |
| `status.doctor` | rows: practices dir, library problems, `schedule` override warning, serve token/bind/assets, same version as tick | WP1–WP9 |
| `skills/journal-system/SKILL.md` Kinds | rendered from the library's `companion:` lines; new Programs section | WP2, WP6 |

## 7. How the judges' concerns are resolved here

- **Callbacks:** Decision 12. Reply keyboards (`ReplyKeyboardMarkup`, `one_time_keyboard`) carry `/next /skip
  /back /break` as plain text; verified on the first phase send; fallback is typed commands. Inline buttons are a
  later Hermes-adapter option.
- **Advance semantics:** Decision 6, with the test "voice note then correction within the settle" in WP4.
- **Front matter vs dependencies:** Decision 2; the PWA writes the canonical flat form through the parser's
  render.
- **Serve and state.db:** Decisions 4 and 7, with both fences as tests (WP9, WP3).
- **Network model:** Decision 9 and [DEPLOY-FRONTEND.md](DEPLOY-FRONTEND.md).
- **Model-written phase content:** `reflect` phases are validated (quotes only from his answers in that
  session, length cap, `is_failure_text`) and degrade to the file's template with a marker ([PRACTICES.md](PRACTICES.md) §4.4).
- **`schedule` key:** transitional override, then removed (table above).
- **Entry recognition / ENGINE_MARK:** WP0.
- **Ledger scale:** phase prompts are ledger prompts; `prune` exempts prompts whose run is still active; the
  programs package measures the ledger after a scripted 10-session run and splits into `runs/<run>.json` if
  the file passes 1 MB.
- **Hook state:** Decision 8.
- **Notebook injection:** unchanged (per turn); flagged in open decisions in case the owner prefers once-per-thread.
- **Session-10 interrupts:** HH:MM confirmation, run-state one-offs, sent by `schedule.due`, DST test (WP5).
- **practices_dir:** Decision 11.
- **Version skew:** Decision 13.
- **Persona text:** stance, not persona ([PRACTICES.md](PRACTICES.md) §4.5); the skill says so.
- **Migration order:** [WORK-PACKAGES.md](WORK-PACKAGES.md): practices and today first, ports refactor bundled
  into the second restart window.
