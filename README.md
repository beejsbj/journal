# journal

Guided journaling where code owns everything that must not fail, and a companion model only does what models are good at. You journal in your own words; the engine sends the prompts, captures your replies verbatim into Markdown entries, and keeps the companion's words apart from yours.

**What it does.** A *practice* is a Markdown file: a title, a time (or several, or none) and a set of questions. Four ship built in (the **morning**, **evening**, **midweek** and **weekly** rituals), you add your own by writing a file or in the web app, and `/start <id>` sends any of them at once. At its local time the journal sends the practice to your Telegram chat (or a chat on another platform Hermes runs: *Another platform*, under *Configure*): a short opener written by your companion persona, with the full question set folded underneath. Whatever you reply is saved **verbatim**, one Markdown file per entry, into a folder you choose, and so is the conversation that follows: everything you say to the companion in the chat, and its replies, goes into the journal as part of an entry (not as a chat log), unless you mark it `/x` (off the record). If the companion is a general agent and the journal only one of its arms, `capture.mode: arm` keeps ordinary chat out: then only replies to prompts, `/j` notes, program answers and the conversation that follows them are saved. A while later the companion annotates the entry, and the annotations land both in the file and as a Telegram reply to your message. After the evening entry it writes a short day reflection, and after the weekly entry a week review, both saved in the journal and sent to the chat. Voice notes are transcribed and kept beside the entry; audio files you send or forward are kept and transcribed too.

A *program* is a set of ordered sessions run phase by phase (`/next`, `/skip`, `/back`, `/break`): **fix-life** (ten sessions), and the situational **conversation-prep** and **post-session-debrief**. You can write your own.

**The rule the design follows.** Sending, dating, capturing and folding are deterministic code. The model writes the opener, converses, and drafts annotations and reflections, and each of those steps degrades to a plain, correct result (a stock opener, no annotation, no reflection) rather than an invented one: the journal keeps working with no model reachable. Openers that claim things about your life must cite them, and quotes from your journal or chat are checked against the real records. The engine only ever appends your words: no code path of its own, no model and no automatic step rewrites a block you wrote. They are plain Markdown files, so you can change them yourself, in Obsidian or in the web app (Edit and Delete under each of your blocks; hers are never editable).

**Four ways to run it, one engine.**

- The **`journal` command** (`uv tool install`): sends what is due (`journal tick`, every five minutes from cron or a timer), and inspects and maintains everything (`status`, `doctor`, `practices`, `program`, `today`, `send`, `migrate-config`).
- The **[Hermes Agent](https://github.com/NousResearch/hermes-agent) plugin** (the primary integration: Hermes provides the Telegram gateway, the scheduler and the model): captures replies through the gateway hook, registers the commands and the companion's tools (`journal_add_note`, `journal_set_location`, `journal_notebook`, `journal_program`) and keeps the companion out of its own settings.
- **`journal serve`** (see *Web app*): a private web app and API over the same files: a responsive PWA you can install on a phone's home screen or open in a desktop browser. It never calls a model and never restarts anything.
- **`journal mcp`** (see *MCP*): the journal's tools for any MCP client (Claude Desktop, Claude Code, others) over stdio: read today and your entries, save words you give it, send a practice. An optional extra; same rules as the web app.

**Where things live.** All of it is configuration (see *Configure*), none of it code.

| What | Where | Setting |
| --- | --- | --- |
| Your entries (one Markdown file each) | the journal folder | `root` |
| Ledger, notebook, packs, program runs, hook state | the state directory | `state_dir` |
| Your practices and programs, and your edits of the built-in ones | the practices directory | `practices_dir` (default `<state_dir>/practices`) |
| Settings | a JSON file and/or the Hermes plugin settings | `config_file` / `plugins.entries.journal.settings` |
| Times, questions, openers of a practice | its file | `schedule.time`, the body, `opener.*` (not the config) |

## Guarantees

What the journal promises, as features of the code rather than requests to a model. Each is held by the modules named
and pinned by the tests named (under `src/journal/` and `tests/`); a change that would weaken one is a decision
recorded in [docs/DECISIONS.md](docs/DECISIONS.md), not a refactor.

| Guarantee | Held by | Pinned by |
| --- | --- | --- |
| **Your words are append-only for the engine.** No code path of the engine and no automatic step rewrites a block you wrote. Only you change your own blocks: the web app's Edit and Delete, or any editor of the Markdown files. The companion's own file and shell tools are kept off the journal folder by the guard, a guardrail rather than a security boundary (*Guard*, under *Architecture*). | `entries.py` (the engine appends); `serve/blocks.py` (your edit: one block spliced, every other byte carried over and checked, run only when you ask); `guard.py` `protect_journal_root` (on by default; `guard.enabled: false` turns the guard off) | `test_serve_blocks.py`, `test_summary_safety.py`, `test_fixes.py` (`test_any_markdown_survives_annotations_notes_and_appends`), `test_mcp.py` (`SaveTest`), `test_engine.py` (`GuardTest`); for the two other engine writers of his entries, `test_conversations.py` (`AppendOnlyTest`: the conversation writer leaves a reply or note of his byte for byte) and `test_audio.py` (`test_his_blocks_around_the_recording_stay_byte_for_byte`, `test_his_caption_under_the_recording_stays_byte_for_byte`, `test_a_recording_embed_in_his_own_words_is_never_filled`: the placeholder replacement, `entries.fill_audio_block`, changes the recording's own lines only). |
| **The companion's words are never editable**, on any surface. | `serve/blocks.py` refuses her blocks; MCP has no edit or delete tool (`mcp/server.py`); the web app offers Edit and Delete only under yours (`web/src/components/Document.tsx`) | `test_serve_blocks.py` (`test_hers_is_never_editable_here`) for the API; `test_mcp.py` (`NothingEditedTest`, and `ServerTest.test_no_tool_edits_or_deletes` with the SDK) for MCP: the tool list is pinned and has no edit or delete; `web/tests/blocks.test.tsx` ("never appear on her callouts, open or shut") for the web app. |
| **`/x` (off the record) holds on every surface**: the chat, the web app and MCP. Nothing marked `/x` is saved, read back into context or quoted. | `privacy.py`, the single privacy pass over the chat, which fails closed (if it cannot run, everything is private); `inbound.py` and `tools.refusal` in the chat; `capture.opt_out` at the web app's and MCP's writes; serve and MCP never open the chat transcript | `test_privacy_coverage.py`, `test_privacy_seams.py`, `test_privacy_seams_d.py`, `test_privacy_backlog.py`, `test_serve_writes.py`, `test_serve_privacy.py`, `test_serve_fence.py`, `test_mcp.py`, `test_mcp_fence.py` |
| **The journal keeps working with no model reachable.** Prompts go out with the plain opener; replies are saved; annotations, reflections and transcripts are deferred, not dropped (*When no model is reachable*, under *Architecture*). | `agent.ask` (an outage is `AgentUnavailable`, never an answer), `outage.py`; the web app and MCP call no model in their own process | `test_outage.py`, `test_programs.py` and `test_interrupts.py` (`test_ten_sessions_with_no_model`); `test_mcp_fence.py` for MCP and `test_serve_fence.py` for the web app (`agent` is in the fence's forbidden list, so no module of `serve/` imports a model directly; `reflect`, which serve reads through, imports `agent` at load, so the other half is behavioural: `helpers.py` makes `agent.ask` fail every test, and `test_serve_privacy.py` walks every route). Processes serve spawns, such as `journal program`, reach a model in their own process, with the usual fallback. |
| **The companion stays out of its own settings.** A guardrail, not a security boundary (*Guard*, under *Architecture*). | `guard.py`, run by the Hermes `pre_tool_call` hook | `test_engine.py` (`GuardTest`) |

## Install

### With Hermes (v0.22+)

```sh
hermes -p <profile> plugins install beejsbj/journal --enable
```

The repository root is the plugin. It has no third-party dependencies, so it declares `python_runtime: external` and enabling it never rebuilds Hermes' shared dependency environment. The install-time security scan reports a **caution** (`role_hijack`): a false positive on the wording of the first midweek "Drift Check" question, which is part of the fixed question bank. Since the banks became files that wording lives in `src/journal/library/practices/midweek.md` and in the copies of the bank the tests compare against (`tests/golden/`), no longer in `questions.py` (and in the web fixtures of an edited midweek, `tests/fixtures/api/library-midweek-*.json`), so expect the caution to name those files (or to disappear, if the scan skips Markdown; not verified before an install). Confirm, or pass `--force`.

Then configure (next section) and run:

```sh
hermes -p <profile> journal setup      # cron stub + every-5-minutes tick job; safe to rerun
hermes -p <profile> journal doctor     # everything the journal needs, checked
```

If the gateway was running while you enabled the plugin, restart it: hooks activate at once, but tools and the skill load with the next session.

### Standalone

```sh
uv tool install git+https://github.com/beejsbj/journal     # or: pipx install …, pip install …
journal --help
```

Standalone, the `journal` command sends prompts, folds them, annotates and replies on whatever schedule you run `journal tick` from (cron, systemd timer), to Telegram (another platform's chat is sent through Hermes). **Capturing replies needs a gateway hook**, which only the Hermes plugin provides today. `journal serve` runs a private web app and API over the same files (see *Web app*). Model steps run through `hermes chat --oneshot` by default; point `agent.command` at another command, or set `agent.enabled: false` for plain openers and no annotations.

## Configure

Settings come from three layers, lowest first:

1. **Built-in defaults** (below).
2. **A JSON config file**, the first of: `journal --config FILE`, `$JOURNAL_CONFIG`, the Hermes setting `config_file`, and (standalone only) `~/.config/journal/config.json` if it exists.
3. **Hermes plugin settings**: `plugins.entries.journal.settings` in the profile's `config.yaml`, editable with `hermes config set plugins.entries.journal.settings.<key> <value>` or in the Desktop's plugin settings form.

Objects merge key by key; lists and scalars replace. The two Hermes-flavoured ways to configure are equivalent: put everything in settings, or keep a JSON file and set only `config_file` (`hermes journal setup --config-file FILE` does that for you). When the companion updates your location (see Travel), each value is written back to the layer it came from.

Minimal config:

```json
{
  "root": "~/Journal",
  "timezone": "Europe/Lisbon",
  "telegram": {"chat_id": "123456789", "token_command": ["pass", "show", "telegram/bot"]},
  "companion": {"name": "Ada", "emoji": "🌿"},
  "user": {"name": "Sam", "pronouns": "she/her/her"}
}
```

### Reference

| Key | Default | Meaning |
| --- | --- | --- |
| `root` | *(required)* | Journal directory, set here or in the config file (the Hermes settings form shows it empty when it comes from `config_file`; `journal doctor` fails until something sets it). Entries are `YYYY-MM-DD-<kind>.md`, flat; `/j` notes are `YYYY-MM-DD-j.md`, `-j-2.md`, …; the companion's analyses are `YYYY-MM-DD-reflection.md` and `YYYY-MM-DD-week.md`. |
| `user.name` | `""` | Heading over your words (`## Sam`; `## Entry` when empty) and how prompts address you. |
| `user.pronouns` | `they/them/their` | Subject/object/possessive, used in the companion's instructions. |
| `companion.name` | `Companion` | Persona name: headings `## <name>'s annotations` / `## <name>'s notes`, prompts, refusal text. Files written under an older name keep parsing. |
| `companion.emoji` | `📓` | Leads prompt headers and annotation replies. `""` for none. |
| `timezone` | `UTC` | IANA name the schedule is read in. |
| `location`, `coordinates` | `""`, none | Where you are, in words, and `{lat, lon, place}` for weather. |
| `lookup.reverse_url`, `lookup.timezone_url` | OpenStreetMap Nominatim's `/reverse`, Open-Meteo's `/v1/forecast` | The keyless services the web app's "Use my location" asks: the first names the place at the device's position, the second gives the place's time zone. Point them at another instance if you run one. |
| `schedule` | *(retired)* | Not a setting: when a practice is sent is in its own file (`schedule.time`, see *Practices*), local wall time, so travel and DST need no changes. A `schedule` key left in a config layer is ignored and `journal doctor` names it; `journal migrate-config --from FILE --to NEW` moves one into forked practice files. |
| `windows.collapse_hours` | `6` | A built-in prompt stays open this long; then (or once answered) it folds: the header and the opener it was sent with stay, and a status line (`answered ✓` / `missed — reply anytime`) takes the place of the questions. A practice of your own (or a fork) folds after its own `collapse_hours`. |
| `windows.grace_hours` | `2` | How late a scheduled prompt may still go out (e.g. after downtime). |
| `windows.opener_budget_seconds` | `240` | Model time for the opener. |
| `windows.annotate_after_minutes` | `20` | Wait after the first reply before annotating. |
| `channel.platform` | `telegram` | The platform the journal's chat is on: any Hermes gateway platform (`telegram`, `discord`, `slack`, `signal`, `whatsapp`, `matrix`, `mattermost` …). See *Another platform* below. |
| `channel.chat_id` | `""` (on Telegram: `telegram.chat_id`) | The chat prompts go to and replies are captured from, as Hermes names it. |
| `channel.user_ids` | `[]` | Your own ids on that platform. Set, only your messages in the chat are journaled (anyone else's pass through untouched) and only your sessions are read; empty, everything in the chat is yours, as in a direct-message chat. |
| `telegram.chat_id` | `""` | The Telegram chat prompts go to and replies are captured from (when `channel` names no other). |
| `telegram.token_env` | `JOURNAL_TELEGRAM_TOKEN` | Environment variable holding the bot token. |
| `telegram.token_command` | none | Command (argv list, or a shell string) printing the token, as `KEY=value` lines or a single line. Run at most once per process, retried once; never logged. |
| `telegram.token_key` | `TELEGRAM_BOT_TOKEN` | Which `KEY=` line of the command's output holds the token. |
| `telegram.annotation_replies` | `true` | Send annotations back as a reply to the message they annotate. |
| `agent.enabled` | `true` | Model steps on/off. |
| `agent.command` | Hermes one-shot | Argv template; `{query_file}`, `{budget}`, `{toolsets}` are filled in. Must print the answer on stdout. |
| `hermes.profile` | none | Profile for `hermes -p <profile> chat --oneshot`. Unset: the profile the plugin runs in. |
| `hermes.command` | `hermes` on `PATH`, else `~/.local/bin/hermes` | The Hermes executable. |
| `hermes.state_db` | `<HERMES_HOME>/state.db` | Sessions database for chat history context and annotation alignment. |
| `hermes.cron_schedule` | `*/5 * * * *` | Tick schedule `journal setup` creates. |
| `hermes.failure_deliver` | `<channel.platform>:<chat_id>` | Where cron failure notices go (`hermes cron --failure-deliver` grammar). |
| `hermes.legacy_plugins` | `[]` | Other Hermes plugins that capture the same chat (a hand-wired predecessor, say). `journal doctor` fails while one of them is still in `plugins.enabled`, since every reply would be captured twice. |
| `adapter` | `auto` | The agent the journal runs beside. `auto` (and `hermes`): the steps that read the chat (sweep, conversation saving, `/x` windows, the outage catch-up, chat history in the pack, annotation alignment) read Hermes' sessions database when there is one (`hermes.state_db`, or the profile's), and model steps run `hermes chat --oneshot` unless `agent.command` is set. `none`: no chat transcript (those steps are off, as standalone) and model steps only through `agent.command` (without one they wait, like an outage). Any other value is a doctor warning and means `auto` (`plainchat` is not built yet). |
| `hermes.mirror_to_chat` | `true` | Mirror everything the journal sends (prompts, annotation replies, reflections, catch-ups) into the companion's chat session, so she sees it when you reply (see *Mirror* below). |
| `sources.weather.enabled` | `true` | Open-Meteo forecast at `coordinates` (no key). |
| `sources.telegram_history` | on, 7 days, 25 messages | Your recent messages to the companion (Hermes only), interleaved in the pack with what the journal sent you. |
| `sources.tududi` | off | `{enabled, command}`: argv of a tududi MCP stdio server. |
| `sources.scrobbler` | off | `{enabled, command}`: the chat-scrobbler CLI. |
| `sources.listenbrainz` | off | `{enabled, user}`. |
| `sources.storyteller`, `sources.audiobookshelf` | off | `{enabled, db}`: SQLite files, opened read-only. |
| `sources.grimmory` | off | `{enabled, container, database}`: MariaDB in Docker; credentials stay in the container. |
| `sources.not_connected` | `""` | Free text listed in the pack as not connected. |
| `pack.max_chars` | `32000` | The whole context pack (at least 8000). Over it, her own lines in the conversation go first, then the oldest material (a chat line, an entry, an old week review), then the current sources and pointer lines from the end; where things stand, her unanswered openers and your newest entry always stay. |
| `pack.entries` | `6` | How many of your newest entries the pack carries, whatever their age, each labelled with its date and age. |
| `pack.lookback_days` | `14` | How far back the prompts unanswered since your last practice reply are counted, and your chat is read for when you were last heard from (at most 120). |
| `pack.returning_days` | `3` | From this gap since you were last heard from on the record, the opener is asked to welcome you back: no recap, one light door. |
| `pack.pointers` | `[]` | Extra "where to look further" lines (text), after the ones made from the connected sources. |
| `guard.enabled` | `true` | The `pre_tool_call` guard (see Architecture). |
| `guard.protected_paths` | `[]` | Extra paths the companion may not write. Always protected: the Hermes data root, the state dir, the config file, and (with `protect_journal_root`) the journal. |
| `guard.allowed_write_paths` | `[]` | Exceptions; `<HERMES_HOME>/cache/scratch` is always allowed. |
| `guard.protected_tokens` | `[".hermes"]` | Substrings that make a write-ish shell command suspicious. |
| `guard.block_execute_code` | `false` | Refuse the `execute_code` tool entirely. |
| `guard.restrict_hermes_cli` | `true` | Allow only `hermes chat`, `cron list/status/runs`, `status`, `version` from the terminal tool. |
| `guard.block_system_commands` | `true` | Refuse `systemctl`, `crontab`, `loginctl`, `journalctl --vacuum`. |
| `guard.escalate_to` | `""` | Who the companion offers to ask when something looks broken. |
| `prompts.reading_guidance` | "older entries in `<root>`…" | Where the companion should read before an opener or a reply. |
| `entries.frontmatter_source` | `journal` | The `source:` value in each entry's front matter. |
| `audio.transcribe` | `true` | Transcribe audio files and recordings sent as documents (voice notes are always transcribed by the gateway). The tick does it, a few minutes after capture. |
| `audio.command` | none = Hermes speech-to-text | Argv template with `{file}` that prints a transcript. Unset under Hermes: the profile's own STT (`tools.transcription_tools.transcribe_audio`, as for voice notes). Standalone without it: not transcribed. |
| `audio.ffprobe` | `ffprobe` on `PATH` | Reads a file's recording time for the reply heading; optional. |
| `images.max_mb` | `20` | The largest picture kept (a photo or image file over it is not copied; its caption is still saved). |
| `images.describe` | `false` | When `true`, the tick asks the companion (no tools, the picture attached) for one short line describing each kept picture and adds it under her notes as `_Photo <name>:_ …`: hers, never in his block; a description that is not one line under 200 characters without quotation marks is dropped, and an unreachable model defers it like an annotation. A picture whose block you removed (in the web app) is not described. Needs a companion that can take a picture: Hermes' `chat --image`, or an `agent.command` containing `{image}`. |
| `reflections.day.enabled`, `.fallback_time` | `true`, `04:00` | Day reflection after the entry of the practice that says `closes: day` (the evening) is annotated, else at this local time the next morning (never earlier than an hour after that practice's prompt). Only days with at least one entry. |
| `reflections.week.enabled`, `.fallback_time` | `true`, `04:00` | Week review (the week ending on the day of the practice that says `closes: week`, the weekly ritual's Sunday) after that entry is annotated, else at this time the day after. |
| `sweep.enabled`, `sweep.lookback_hours` | `true`, `48` | Every tick, capture journal replies the gateway hook missed (sent while the companion was replying) from the Hermes sessions database, read-only. The first sweep looks back this far. |
| `capture.mode` | `everything` | What gets journaled. `everything`: every chat thread, as below (*Conversations*). `arm`: the journal is one arm of a general agent: replies to prompts, `/j` notes, program phase answers, voice notes answering a prompt and the companion's `journal_add_note` are saved, with the conversation that follows them in the same thread; ordinary chat that answers no prompt is not written into any entry (see *Capture mode*). Any other value is a doctor warning and means `everything`. `journal doctor` and `journal status` show the mode. |
| `conversations.enabled` | `true` | Save the chat itself (your messages and the companion's text replies) into entries, by thread (see *Conversations*). Hermes only: it reads the sessions database, read-only. |
| `conversations.quiet_minutes` | `45` | A thread ends after this much silence on both sides (a journal prompt send, or a Hermes cron delivery to the chat, also ends one). A value that is not a positive number means 45; one over a year (525,600) is read as a year; `journal doctor` names either. |
| `conversations.opt_out_prefix` | `/x` | A message starting with this is off the record; alone, it takes the rest of the thread off the record. Also registered as a slash command (it shows in the Telegram menu). It can't be one of the journal's own commands (`/j`, `/journal`, the rituals, `/start`, `/journal-start`, the program commands `/next`, `/skip`, `/back`, `/break`, `/abandon`, `/program`, and `/web`) or a Hermes command in the dated table `config.HERMES_COMMANDS` (the same verdict in the gateway and the tick); an unusable value falls back to `/x` and `journal doctor` says so. |
| `conversations.annotate_auto` | `false` | Annotate note entries started from conversation (`capture: auto`). Off: the day reflection covers them. |
| `recovery.enabled`, `recovery.max_age_hours` | `true`, `48` | After a model outage left your chat messages answered only by Hermes' failure line, send one catch-up reply once a model is back; messages older than this get no reply, only a note in the companion's chat session (see *When no model is reachable*). |
| `today.enabled`, `today.max_chars` | `true`, `1200` | The *today block* (see *Today block*): once per conversation thread the companion's turn gets a short block about the day. `max_chars` is a hard cap; whole lines are cut from the bottom to fit. |
| `today.sources` | all six | Which lines the block may carry: `practices`, `programs` (where the program run you last touched stands), `entries`, `reflection`, `tasks`, `weather`. |
| `today.cache_minutes` | `30` | How often the tick refreshes `today-cache.json` (tasks due from `sources.tududi`, the weather from `sources.weather`); the hook never calls a source itself. |
| `serve.bind`, `serve.port` | `127.0.0.1`, `8790` | Where `journal serve` listens. Loopback is for development and a first quarantine run; any wider address is a deployment decision (firewall, proxy) made by whoever runs the host. `$JOURNAL_SERVE_BIND` overrides for one run. |
| `serve.host` | `""` | The site's host name (`journal.example.org`): requests with another `Host` header are refused (`/healthz` excepted), a write must carry `Origin: https://<host>` (with `:<public_port>` when that is set), and pairing links use it. Empty: no Host check, links use the bind address. |
| `serve.public_port` | absent (443) | The HTTPS port the site is published on when it is not 443 (a proxy elsewhere owns 443, say `tailscale serve --https=8790`): pairing links and the write `Origin` become `https://<host>:<port>`, exactly, and nothing else is accepted. 1-65535; anything else is reported by doctor and ignored. |
| `serve.token_env`, `serve.token_command`, `serve.token_key` | `JOURNAL_SERVE_TOKEN`, none, `JOURNAL_SERVE_TOKEN` | Where the API token comes from, resolved like the bot token. With neither configured, `journal serve` generates `<state_dir>/serve-token` (0600). |
| `serve.trusted_proxies` | `[]` | CIDRs of the reverse proxy in front: only from these is `X-Forwarded-Proto`/`-For` believed. |
| `serve.socket` | none | An absolute path (at most 107 bytes) of a Unix socket `journal serve` listens on as well as TCP, for `tailscale serve --https=<port> unix:<path>`. Made 0600 at start (its missing directory 0700), removed at stop. A socket anywhere below `/run/user/<uid>/` (`journal-serve/serve.sock`, `journal/serve/serve.sock`) makes `--print-unit` add `RuntimeDirectory=<that path>`; anywhere else only has its mount gated (`RequiresMountsFor`). The path is normalised (`a/../b` is `b`). The server refuses a socket directory that is not the service user's own or that group or others can write (so not directly in `/tmp`), and never removes or reuses a socket file of another user's: another local user who could replace the socket would receive his browser's requests from tailscaled. On the socket the `Host` check reads `X-Forwarded-Host`. Unusable: `journal serve` will not start, doctor says why. |
| `serve.tailscale_users` | `[]` | Tailscale logins (`someone@example.com`) signed in with no pairing: a request **on the socket** whose one `Tailscale-User-Login` header is listed needs no cookie or token. Over TCP the header is ignored, whoever sends it. Writes still need the site's `Origin` (`serve.host`, `serve.public_port`). The web app then shows "Signed in via Tailscale as …" and no pairing or Unpair. |
| `serve.assets_dir` | the packaged `serve/static` | A web app build to serve instead (development). |
| `serve.allow_write` | `true` | Anything but `true` makes every route that changes the journal answer 405 (the web app then only reads); pairing still works. |
| `programs.settle_minutes` | `2` | A program phase answer moves the run on after this much quiet, so a correction or a second voice note joins the same phase; `/next` moves on at once (see *Programs*). |
| `programs.reflect`, `programs.bridges` | `true`, `false` | `reflect`: ask the model for a reflect phase's construction (quotes checked against your answers; the phase's own text with a note whenever that fails). `bridges`: a one-paragraph lead-in before each question phase, quoting only your last answer. |
| `programs.synthesis` | `false` | Add the companion's synthesis to a program's insights document (`## <Companion>'s synthesis`, after the compiled part; every quote checked against the compiled text, nothing added when it fails). A program file with `insights: compile+synthesis` asks for it too. |
| `programs.keyboard` | `true` | Send `/next` `/skip` `/back` `/break` as reply-keyboard buttons with each phase; `false` leaves typed commands only (the fallback if a client or gateway mishandles the buttons). |
| `state_dir` | `<HERMES_HOME>/plugin-data/journal`, standalone `~/.local/state/journal` | Ledger, notebook, packs, program runs (`runs/`), the hook's between-callback state (`hook-state.json`). `$JOURNAL_STATE_DIR` overrides. |
| `practices_dir` | `<state_dir>/practices` | Your own practice files and your edits of the built-in ones (see *Practices*). Outside the journal on purpose: the journal holds entries only. `$JOURNAL_PRACTICES_DIR` overrides (and is then not writable from a config layer). Use an absolute path (doctor warns on a relative one, as for `root` and `state_dir`: cron, the gateway and a shell each start in a different directory). |

### Another platform

The journal works in one chat, by default your Telegram chat (`telegram.chat_id`, no `channel` needed). To journal
on another platform the companion's gateway runs, name it and the chat:

```json
"channel": {"platform": "discord", "chat_id": "1180000000000000001", "user_ids": ["990000000000000002"]}
```

Use a direct-message chat with the companion; `chat_id` is that chat as Hermes names it (`hermes send --list
<platform>` lists the chats Hermes knows; for Discord it is the DM channel's id, not your user id), and `user_ids`
your own id there. Prompts,
annotation replies, reflections, catch-ups and program phases then go to that chat, and your replies, `/j`, `/x`,
voice notes and the conversation are captured there exactly as on Telegram, with the same privacy pass; nothing said
on another platform, in another chat or by anyone else is journaled. One chat at a time: after a switch the old chat
is no longer read, so a prompt sent there before is answered in the new chat (`/j`, or the entry in the web app).

Telegram is sent through the Bot API directly; any other platform through Hermes' own senders, inside Hermes
(`hermes journal deliver`, with the credentials Hermes already has for the profile: no token to configure here).
From outside the gateway those can send and nothing more, so on another platform a prompt is not folded (it stays as
sent, closed in the journal after its window), annotations and the catch-up come as messages of their own rather
than replies, a program phase has no buttons (its last line names `/next` `/skip` `/back` `/break`; type them), and
a message is plain text. A message longer than the platform allows (Discord: 2,000 characters) is split by Hermes;
replying to its last part is the surest way to reach the prompt, though the next message you send while it is open
answers it anyway. Where slash commands are gated, the platform needs its own `user_allowed_commands` list, as
Telegram does. `journal doctor` shows the channel and how it is sent to.

**Bot token under Hermes.** The tick runs as a Hermes cron script, and cron scripts do not see the gateway's credentials (Hermes refuses to pass gateway tokens through `terminal.env_passthrough`). Use `telegram.token_command`, or put a copy of the token in the profile's `.env` under another name (e.g. `JOURNAL_TELEGRAM_TOKEN`), add that name to `terminal.env_passthrough`, and keep `telegram.token_env` pointing at it. `journal doctor` says whether a token is obtainable without printing it.

## Use

| You | Journal |
| --- | --- |
| Reply to a prompt (any age), or send the first message while one is open | Saved verbatim to that entry; later direct replies append. The companion sees a `[Journal reply — …]` note. |
| `/j <text>`, or a voice note / audio file captioned `/j` | Saved as its own entry. The companion is asked to read around it before answering. |
| Keep talking (text, voice notes, audio) | Saved into the entry the thread started from, under *Conversation*, both sides; a thread with no journal reply or `/j` in it becomes its own note entry (`capture: auto`). See *Conversations*. |
| `/x <text>`, or `/x` alone | Off the record: that message (or the rest of the thread) and the companion's replies are never saved. She still gets the message, without the command. |
| Voice note | Saved after Hermes transcribes it, with the audio beside the entry (`2026-10-14-evening.ogg`, then `-2`…) and embedded. |
| Audio file, or a recording sent as a file (`audio/*`) | Kept beside the entry with its original extension and embedded at once; Hermes does not transcribe these, so the tick transcribes it a few minutes later (`audio.*`) and fills in the block. The heading carries the original file name, duration, recording time (ffprobe) and forward date when known: `### 13:49 IST · audio file "walk.m4a" · 12:04 · recorded 14 Oct 21:10`. |
| Photo, or an image file (jpg, png, webp, heic, gif) | Kept beside the entry like audio, flat and named after it (`2026-10-14-evening.jpg`, then `-2`…), and embedded above your caption: `### 14:02 IST · photo`, `![[2026-10-14-evening.jpg]]`, then your caption verbatim. Sent as the answer to a prompt, a `/j` note or a program phase, it goes into that entry; sent in ordinary chat it goes into the conversation's entry the same way (a lone captionless photo is written too). Off the record (`/x`) it is never kept. Over `images.max_mb` it is not kept, but the caption still is. Photos are written when they arrive; the web app shows them in the entry. |
| Reply while the companion is still answering | Hermes routes it past the hook (its busy path). The next tick's sweep finds it in the sessions database and captures it by the same rules, under its own time. |
| Answer the evening (or weekly) prompt | Once the entry is annotated, the companion writes a day reflection (week review) into the journal and sends it to the chat; unanswered, it comes at 04:00 the next morning, silently. The day closes on the practice that says `closes: day` (the evening), the week on the one that says `closes: week` (the weekly), so a fork that renames them keeps the reflections. |
| Start talking after a quiet spell (first message of the day, 45+ minutes since your last, or after a prompt went out) | The companion's turn carries the *today block*: where things stand (when you were last heard from on the record, if before today; prompts unanswered since; a program in progress with its pace), how today's practices went, today's entries, the last reflection's question, tasks due, the weather. Once per thread, never with a context pack or a program block. |
| `/start <id>` | Sends that practice now (in the background, after any running tick): any enabled practice, yours included, resolved when you send it, so a new file needs no restart. An id that doesn't exist (or none) answers with the list. A second send the same day becomes current; the earlier message still accepts replies. Under Hermes, which owns `/start` itself (Telegram's start ping), the plugin registers it as `/journal-start` and routes `/start <id>` there; `/start` alone stays Hermes'. Hermes checks the routed name, so where slash commands are gated (`allow_admin_from` set for the platform) a non-admin needs `journal-start` and `journal_start` in that platform's `user_allowed_commands`, like the ritual commands; otherwise Hermes answers "admin only". |
| `/morning`, `/evening`, `/midweek`, `/weekly` (`/morning-journal`, `/evening-journal`) | The same as `/start morning` and so on. |
| `/start fix-life` (or `/start prep`, `/start debrief`) | Starts a program, or picks its run up where you left it (see *Programs*); `/start fix-life 3` jumps to session 3, `/start fix-life insights` compiles the insights document into the journal. One program at a time: while one is unfinished (on a break or between sessions too), starting another answers with which one and where it is, and to carry on with it or `/abandon` it; situational sessions (`prep`, `debrief`) always start. |
| Answer a phase (one message or several, text or voice) | Saved verbatim into the session's entry under that phase; the next phase arrives once you've been quiet for two minutes. |
| `/next`, `/skip`, `/back`, `/break`, `/abandon <id>`, `/program` | During a program: move on now, skip this phase, have the previous phase sent again (what you wrote stays), take a break (`/start <id>` resumes; the run stays unfinished), end a run where it is (its entries stay; it must be named, and `/abandon` alone lists what could be ended), and where you are. The phase message carries the first four as buttons. Hermes owns `/pause` and `/status`, hence `/break` and `/program`. Where slash commands are gated, a non-admin needs `next`, `skip`, `back`, `break`, `abandon`, `program` in `user_allowed_commands`. |
| `/web` | Answers with the web app's address, `https://<serve.host>` (with `:<serve.public_port>` when that is set), built as `journal serve` builds its own origin (`config.serve_site`), and how a browser signs in: by its Tailscale identity where `serve.tailscale_users` and `serve.socket` are set, otherwise by pairing on the server with `journal serve pair`. It never sends a pairing link or a token (the chat's history is no place for a secret). With no `serve.host` it says the web app isn't set up. Where slash commands are gated, a non-admin needs `web` in `user_allowed_commands`. |
| Answer a program phase | The companion's turn carries the *program block* (see *Programs*: what she is told) instead of a context pack, and she can ask `journal_program` what is available or running. |
| Tell the companion you've travelled | `journal_set_location` geocodes the place and updates location, timezone and coordinates; the schedule follows local time. |

CLI:

```text
journal [--config FILE] [--hermes-home DIR] <command>
  tick [--dry-run]              what the scheduler runs: send due, fold, annotate, reply, prune
  send <id> [--dry-run]         send a practice now (any id in the library; waits for a running tick); --dry-run prints it
  sweep                         capture replies the gateway hook missed, from the sessions DB (the tick does this too)
  backfill-conversations --since YYYY-MM-DD [--dry-run | --write]
                                save past chat into the journal by the conversation rules (a dry run unless --write)
  today [--json]                the today block as the companion would get it now (--json: the data behind it)
  status [--json]               last run, due and next practices, open prompts, notebook, recent errors
  doctor [--json] [--deep]      config, journal root, state dir, practices, token, agent, sources, Hermes cron;
                                --deep also runs each source once and counts how the newest 50 chat rows read
  practices list [--json]       every practice (built-ins and your files): schedule, source, problems
  practices show <id> [--json]  one practice: schedule, opener, the questions with their ids
  practices validate [FILE…]    check files (default: the whole practices directory); exit 1 on a problem
  practices render <id|FILE>    the canonical Markdown of a practice, defaults written out
  practices fork <id>           copy a built-in (practice, program, situational) into practices_dir to edit it there
  practices reset <id>          remove your edit of a built-in: the shipped one applies again
  practices diff <id>           the default against your edit, and what changed in it since you edited
  config list [--json]          the settings you may change, with the value in force and the layer that owns each
  config get <key> [--json]     one setting (or a group such as windows)
  config set <key> <value> [--json]
                                change one in the config file, checked as the web app's Settings page checks it;
                                JSON (5, true, '{"lat": 1, "lon": 2}') or plain text (Europe/London). A key in Hermes
                                settings is not written: the `hermes config set …` line to use is printed (exit 1)
  serve [--bind HOST] [--port N] [--dev] [--access-log]
                                the private web app and API, in the foreground (a systemd user unit keeps it up)
  serve pair                    print a one-time link that pairs a browser (10 minutes, single use)
  serve token [--rotate]        where the token comes from; --rotate signs every paired browser out
  serve check                   bind address and port, token, web app build, same version as the tick
  serve --print-unit            the systemd user unit for this configuration
  program status [--json]       the programs in the library and the runs in progress
  program start <id> [--session N]
                                start a program (or resume its run); waits for a running tick
  program next|skip|back|pause|resume|abandon [RUN]
                                move the run (default: the one last active)
  program insights [RUN|ID]     compile the insights document (no model)
  program settle RUN [--now]    what the hook spawns: wait the settle, then move on if you've gone quiet
  migrate-config --from OLD [--merge OVERLAY] [--to NEW] [--force] [--practices-dir DIR]
                                convert an old config; a `schedule` block becomes forked practice files (with --to)
  migrate-state --from OLD_DIR [--to NEW_DIR] [--force] [--no-lock]

hermes [-p <profile>] journal setup [--paused] [--config-file FILE] | doctor | uninstall | status | today | send <id> | sweep
                                | backfill-conversations --since YYYY-MM-DD [--write] | practices … | program … | config …
```

`journal` reads Hermes settings and state when `$HERMES_HOME` (or `--hermes-home`) points at a profile home; otherwise it is standalone. `hermes journal uninstall` removes the cron job and stub and leaves the journal, state and settings alone.

### Practices

The four rituals are *practices*: one Markdown file each, packaged in `src/journal/library/practices/` (`morning.md`, `evening.md`, `midweek.md`, `weekly.md`). The front matter is flat `key: value` lines; the body is `## N. Section` headings holding `- ` questions, or a plain list of questions. This is a complete practice:

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

Every other key has a default (`journal practices render FILE` prints it with its defaults written out): `emoji` (empty = the companion's); `schedule.time` with an optional `schedule.weekday` (0 = Monday), or several slots as `schedule.1.time`, `schedule.2.time`, `schedule.2.weekday` (no schedule = on demand only); `collapse_hours` (6); `annotate` (`true`); `closes` (`day` or `week`: the practice whose entry ends the period a reflection covers); `opener.mode` (`companion` or `plain`), `opener.shape` and `opener.limit` (1100 characters) for the companion's opener; `opener.plain`, the opener used without a model (`{greet:Evening}` gives "Evening, Sam 🌿"; `{title}`, `{user}` and `{emoji}` also work); `companion` (one line of stance for the companion); `group`; `enabled` (`false` hides a built-in).

Question ids are positional (`3` in a plain list, `9.2` for section 9, question 2); end a line with `{#9.2}` to keep that id with its question when you move it. A file in `practices_dir` with a built-in's id replaces that built-in whole: that is how a built-in is edited in place, from the web app's Edit or by hand (`journal practices fork evening` copies it there to start from, with your `windows.collapse_hours` written in so it folds as before; `journal practices reset evening` goes back). `practices_dir/.library` keeps the shipped text your edit began from, so when a release changes the default your version is kept and the Library and `journal practices diff <id>` say what changed. A file that doesn't parse is reported by `journal doctor` and `journal practices validate`, and the built-in of the same id is used, so an editing mistake never silences a prompt. Prose between questions is ignored, but a line that starts like a list item without being a `- ` question (indented, `+ `, `1. `, `-text`), a heading with no question under it, and a key given twice are problems, so a typo never quietly shortens the bank; numbers are plain ASCII digits. Ids taken by commands and entry kinds (`j`, `journal`, `note`, `reflection`, `week`, `start`, `next`, `skip`, `back`, `pause`, `status`, `break`, `program`, `insights`, the opt-out command, Hermes' own commands as listed in `config.HERMES_COMMANDS`, and the repeat names of notes and analyses such as `j-2` or `reflection-2`) can't be used, nor an id that is another practice's later slot (`water-2` while `water` has two slots: its second prompt is `<date>-water-2`).

**The engine sends by practice.** A file of your own in `practices_dir` is scheduled from its `schedule.*` lines (several slots go out as `<date>-<id>`, then `<date>-<id>-2`, …), started any time with `/start <id>` (or `journal send <id>`), and its replies are saved to `YYYY-MM-DD-<id>.md` with its title and its bank, exactly as the rituals are; no restart is needed for a new or edited file. `opener.mode: plain` sends the plain opener without asking the model; `annotate: false` skips the annotations; `emoji` leads that practice's header; `closes: day`/`week` moves the reflections onto it. A fork of a built-in is what is sent in its place. An entry of your own practice is recognised by its front matter (`practice: <id>` and `type: journal`), never by name alone, so a hand-written `2026-10-20-gratitude.md` stays yours: a reply to that day's prompt goes to the next free name past the practice's slots (`2026-10-20-gratitude-2.md`) and never into your file, and the same holds for a reply to a ritual (the companion's notes then go to the engine's entry too, when the day has one). A ritual's name alone still makes a file count as an entry for packs and reflections, and for her notes on a day with no engine entry of that ritual. The skill's *Kinds* list is rendered from the library (each practice's `companion:` line, as written) when the gateway starts. The four rituals go out byte for byte as before (`tests/golden/`). The format in full, including programs: [docs/PRACTICES.md](docs/PRACTICES.md).

### Programs

A *program* is ordered sessions of phases, run by the engine one phase at a time; a *situational* definition is a one-session program started when something comes up. Built in: **fix-life** ("Fix your entire life in 1 day", ten sessions after Dan Koe's article, ported from the fix-life skill without the Russian sessions, the intro image and the inline buttons), **conversation-prep** (`/start prep`, before a hard conversation) and **post-session-debrief** (`/start debrief`; after each fix-life session the companion's notebook gets a note to offer it when it fits).

`/start fix-life` sends the intro and session 1, phase 1, with `/next` `/skip` `/back` `/break` as reply-keyboard buttons. Answer in your own words, in one message or several: the open phase takes every message while it is open (and the newest thing sent to you), so a correction or a second voice note joins it, and the next phase arrives once you've been quiet for `programs.settle_minutes` (2), or at once on `/next` (situational sessions wait for `/next`). Each session is one entry, `YYYY-MM-DD-fix-life-s01.md` (dated the day it started; a situational run writes `YYYY-MM-DD-<id>.md`): `## Prompt` folds each phase as it was sent, your words are blocks as for the rituals (each under a hidden `%% journal phase:N %%` key), the conversation with the companion follows, and when the session ends the engine writes a `## Summary` filled from your own words (and session 8's game map, session 10's protocol) and sends it, then says what is next. The companion annotates the session once it is complete. After session 10, `YYYY-MM-DD-fix-life-insights.md` compiles every summary and every answer, verbatim (again any time with `/start fix-life insights`). One session every few days is the intended pace: `/start fix-life` continues with the next one whenever you are ready. **One program at a time:** until fix-life is finished (or abandoned, with `/abandon fix-life`), another program does not start, on a break or between sessions included; practices and situational sessions carry on as ever (DECISIONS.md, ONE-PROGRAM-RUN). A situational session started mid-session takes the chat from the program's open phase; its summary says so, and `/start fix-life` sends that phase again.

Without a model nothing waits: the four *reflect* phases (where the companion builds something from your answers, such as the 5-year Tuesday) go out with their own text and a note that you can write it yourself; with a model, her construction is used only if every quote in it is from your answers in that session. A phase answered off the record (`/x`) is not an answer: the phase stays open. While a bare `/x` holds the chat off the record the engine sends no phase on its own (a send would end the window and file your next private message as an answer): the settle, a phase it still owes after a failed send, and one whose text the companion was writing when you said `/x` all wait until the window closes; your own `/next` still sends at once. A run lives in `<state_dir>/runs/<program>/<run>.json`; `journal program status` (or `/program`) says where it stands. Session 10's eight daytime interrupts are scheduled only from what you confirm (next paragraph). **Session 10's interrupts.** Phase 3 asks for the protocol day and your wake time, `YYYY-MM-DD HH:MM` (for example `2026-11-02 07:00`) or `HH:MM` alone for tomorrow. That reply, and nothing looser (`7am` is not read), times the eight interrupts at the offsets the phase lists (+2h … +14h) and the run moves on; any other reply is kept as your words, you are asked once more, and nothing is scheduled (`/skip` or `/next` goes on without interrupts). The times are wall-clock: a DST change, or `journal_set_location` to another place, leaves each at the clock time you stated. The tick sends each one as its own message (inside `windows.grace_hours`; a tick that was down sends only the newest of several that came due and records the others as missed); reply to the message and your words land in the session-10 entry under its question, in a block keyed `%% journal interrupt:N %%`. `journal program status` lists the ones still to come; `journal program abandon` cancels them, also after the run has completed (it leaves the run `completed`). A bare `/x` holds one back until the window closes. `/start fix-life insights` (or `journal program insights`) compiles the insights document again, with your interrupt answers; the compiled part is deterministic and quotes you verbatim, and the optional synthesis (`programs.synthesis`) is added below it only when every quote in it is yours.

**What the companion sees.** On the turn your phase answer arrives, her context carries a *program block* of at most 3,000 characters in the pack's place (so no pack and no today block on that turn): "You are <Companion>. For this session, work as <role>; keep your own voice. Mission: <mission>.", the phase as it was sent, and your own words in this session so far (read from the session entry: never her replies, never the chat, and nothing said off the record, which was never saved). The role in a session file is a stance for that session, not a persona: the skill tells her not to repeat it to you. She has one tool for programs, `journal_program`: `status` (what `/program` shows) and `propose <id>` (the `/start` line she may offer when it fits; it starts nothing). Both only read, so they work in an off-the-record turn. She never starts, advances, skips or edits a program; the engine sends the questions as authored and only your `/next`, `/skip`, `/back`, `/break` (or the web app) move a run. The skill's *Programs* list is rendered from the library when the gateway starts, like *Kinds*.

Your own programs go in `practices_dir` (`<name>/program.md` with `sessions/`, or one file with `kind: situational`); the format is [docs/PRACTICES.md](docs/PRACTICES.md) §1.3–1.5.

## Architecture

```
plugin.yaml, __init__.py      the repository root is the Hermes plugin (wrapper → src/journal/hermes)
pyproject.toml                the same code as a package with a `journal` console script
src/journal/
  config.py      layered settings, names, write-back
  schedule.py    which practice slots and which one-offs (session 10's interrupts) are due, in local time (library.scheduled)
  context.py     the dated, sourced context pack;  sources.py  the optional sources
  library.py     practice definitions as files: parse, render, load (built-ins + practices_dir), problems, fork;
                 what closes a day or week, the practice a prompt was sent for, the ids of the built-ins; his edits
                 of the built-ins (reset, the default they began from, the diffs, a program's run guard)
  commands.py    what the commands mean, for any chat platform: /start <id> (resolved against the library at call time), /j, /x, the ritual sends,
                 /next /skip /back /break /abandon /program; `SPECS` is the list an adapter registers, `spawn` is what it supplies
  library/practices/   the four built-in rituals (morning, evening, midweek, weekly .md), frozen by tests/golden/
  programdefs.py program and situational definitions: sessions, phases, templates, slots (parsed, checked)
  library/programs/fix-life/   program.md and sessions/01…10;  library/situational/  conversation-prep, post-session-debrief
  programview.py what the companion sees of programs: the program block of a phase-answer turn, `journal_program` status/propose
  programs.py    program runs: start/advance/skip/back/pause/resume/abandon, settle, the tick's step, summaries,
                 session 10's interrupts (confirmation, schedule, send), the insights document and its synthesis, what /program and the today block say
  prompts.py     instructions for the opener and annotations
  ports.py       the two ports the engine reads the companion through: Transcript (chat rows as Row) and Companion
  agent.py       ask(): the companion's text, or AgentUnavailable (an outage); CommandCompanion for agent.command
  outage.py      model / speech-to-text outages: defer, back off, alert once
  validate.py    grounding: claims need citations, journal/Telegram quotes must exist
  compose.py     prompt and annotation-reply messages (HTML)
  channel.py     the journal's chat: its platform and chat, whose messages are his (`ours`), a record's chat (`same_chat`)
  telegram.py    Bot API: send (optionally as a reply), edit; token resolution; `TelegramChannel` (the Channel port on Telegram)
  mirror.py      what the journal sent, mirrored into the companion's chat session (the transcript's one write)
  recovery.py    one catch-up reply after a model outage left the user unanswered
  capture.py     which prompt a message answers, and writing it
  inbound.py     one incoming message, before any model sees it: `receive(cfg, Inbound) -> Outcome` (/x, audio, /j, replies, program answers, program commands);
                 `begin_turn` (voice transcripts, the off-record turn mark)
  tools.py       the companion's tools for any agent: `specs`, `call(cfg, name, params, turn)`, and `refusal`, which keeps the writes out of an off-record turn
  sweep.py       the busy-gap sweep: replies the hook missed, read from the chat transcript
  conversations.py  the chat itself, both sides, written into entries by thread; /x; the backfill
  audio.py       kept audio: metadata, ffprobe, transcription of audio files
  images.py      kept pictures: which files count, the size cap, the name they are kept under, descriptions
  entries.py     the entry files: create, append, annotate, companion notes; which files are entries
  hookstate.py   what the gateway hook remembers between callbacks (voice notes awaiting STT, queued packs, off-record turns), on disk
  reflect.py     day reflections and week reviews: when, inputs, file, Telegram
  ledger.py      what was sent and captured (JSON under an exclusive flock)
  notebook.py    the companion's private notebook
  today.py       the today block: collect (state + the tick's cache) → render; thread-start stamps; the cache step
  turncontext.py what the companion is told on one turn (notebook, pack or program block, today) under the two-block budget
  tick.py        send → sweep → conversations → programs → fold → audio → images → annotate → reply → reflect → recover → cache → prune;
                 send_prompt delivers one prepared prompt (retries, sent.jsonl, re-sends, the mirror)
  status.py, migrate.py, cli.py
  guard.py       what the companion may not change
  privacy.py     /x: the one privacy pass over the chat's rows; every reader asks it
  hermes/        register(ctx): hooks.py (event ↔ dict, nothing else), tools.py and commands.py (registration), cron.py (setup), runtime.py;
                 transcript.py (Hermes' sessions database → Row: the only place it is opened, read-only) and
                 companion.py (`hermes chat --oneshot`), channel.py (`HermesChannel`: any other platform, through
                 `hermes journal deliver`, deliver.py: Hermes' senders without its mirror): the adapter behind the ports
  skills/journal-system/   the skill template, rendered per profile at load
```

**Adapters.** What happens to a message, a turn and a tool call is the engine's too: the Hermes hooks only turn a gateway event into an `inbound.Inbound` and the `Outcome` back into the hook's answer, `turncontext.build` says what the companion is told, and every tool call goes through `tools.call`, which refuses the journal's writes (her notes, the notebook's add and resolve) in an off-the-record turn and allows reads. Another agent's adapter implements the two ports below, calls `inbound.receive`, `inbound.begin_turn`, `turncontext.build` and `tools.call`, registers `tools.specs` and `commands.SPECS`, and passes a `spawn` for the commands that start a background `journal` process. Two limits to know: `/x` marking in `receive` and `begin_turn` applies in the journal's chat only (`channel.ours`: the configured platform, chat and owners; a message anywhere else passes through untouched, and the tool refusal then rests on the privacy pass alone), and `tools.call` does not run `guard.check`: the adapter runs it in its own pre-tool hook.

**Ports.** Nothing in the engine opens the chat, names Hermes or calls a platform: every send and edit goes through `cfg.channel()` (`ports.Channel`: `send`, `edit` and `caps`, what the platform can do; `TelegramChannel` on Telegram, `HermesChannel` elsewhere), and every reader of the chat (the privacy pass, the conversation writer, the sweep, the outage catch-up, the pack, the quote verifier, annotation alignment, the mirror) takes `ports.Row`s from `cfg.transcript()`, and every model step asks `cfg.companion()` through `agent.ask`. `journal/hermes/transcript.py` decides once what a stored Hermes row is (his words, the hook's note, a mirror, a frame or placeholder, a delivery; her reply, a tool step, a failed or closed turn; a compaction copy); `tests/test_purity.py` holds the line, and the reader suites run a second time over a fake transcript (`tests/test_fake_transcript.py`). Standalone there is no transcript (`NullTranscript`): those steps do nothing, as before.

**Flow.** *Due*: `schedule.slots_due` compares local wall time with every scheduled practice's slots (± `grace_hours`). *Opener*: `context.build` gathers a pack. It opens with *where things stand* (`standing.py`, code with no model): when he was last heard from on the record (his captured replies and notes, web blocks, his newest entry, and his newest chat message the privacy pass calls public; nothing off the record counts, and a pass that cannot run counts nothing from the chat), his last entry, the practice prompts unanswered since his last practice reply per practice (at most `pack.lookback_days`), each program in progress (session k of n, the phase, the movement, his last answer, the stated pace) and the gap in days. Then her last two unanswered openers word for word (`[you]` lines), his newest `pack.entries` entries whatever their age, each headed with its date and age, and at the end "Where to look further": one line per place, made from the config and what is on disk (older entries and how far back they go, her earlier analyses, program documents, the notebook archive, each connected source, `pack.pointers`). The whole is held to `pack.max_chars`: her own conversation lines drop first, then the far end. Its "Recent conversation" section interleaves, oldest first, his Telegram messages (7 days, 25 messages) with what the journal sent him in the last three days: each prompt with its local send time and whether it was answered or missed, annotation replies, reflections, catch-ups (each shortened, ~2,400 characters for all of hers), so a one-shot sees the shape of the conversation, doesn't repeat a quote or a question, and tries a different door after one that went unanswered. Her lines start `- [you] `: they are her words, so `validate.check` ignores them and `make_verifier` checks a `journal:<file>` quote against his words in that file only (his words section and his conversation lines: never the opener under `## Prompt`, her annotations, her notes or her reviews) and a `telegram:` quote against his public Telegram messages (without the quoted message of a reply, and without the journal's mirrored rows); for those two sources `validate.check` asks the verifier, never the pack's text. The opener request names the run of recent prompts that got no reply (one by one up to four, else counted per practice) and treats it as signal; past `pack.returning_days` since he was last heard from, it asks her to welcome him back without guilt, no recap and no list of what he missed, one light door in: reach for something else in the pack (reading, listening, weather, an open task, an older journal thread) or keep it light; quoting his words is optional when other cited facts ground the question. Tududi habits never once completed are one count line, not a list of hooks; the agent writes JSON (`opener`, `citations`, `asked`); `validate.check` rejects uncited claims and quotes not found in the journal or chat history; the woven question ids are validated against the bank and recorded in the entry. Any failure (including the model being unreachable, or Hermes printing its own error text) means the plain opener; an opener is never deferred. *Send*: header, opener, the full question bank in an expandable blockquote; the message id goes into the ledger; up to three attempts across ticks, then the run exits 1 so the scheduler alerts. *Capture*: `pre_gateway_dispatch` writes typed replies before any model runs; voice notes are matched there and written at `pre_llm_call` once transcribed (the match waits in `hook-state.json`, so a gateway restart in between loses nothing). *Fold*: answered or `collapse_hours` old (a built-in's `windows.collapse_hours`, else the practice's own, recorded at send) → edited to its header, the opener exactly as sent and an italic status line (`answered ✓` / `missed — reply anytime`); the question bank and the hint go. When the opener would push the message past Telegram's length limit, or Telegram cannot parse it, the fold is the header and the status alone; a program phase always folds to its header; six attempts. *Annotate*: `annotate_after_minutes` after the first reply, the agent (no tools) drafts bullets from the entry, aligned with its chat reply; `FOLLOW-UP:` lines go to the notebook; three attempts at a bad answer (see *Outages* below). *Reply*: the annotations go to Telegram as a reply to the first captured message (`reply_parameters`, `allow_sending_without_reply`), recorded in the ledger, never resent, six attempts, never blocking the file. *Sweep*: every tick reads the sessions database read-only from a row-id watermark and applies the capture rules at each message's own time (direct replies by the stored `[Replying to: "…"]` quote, matched against the prompt's header and sent text), skipping anything already captured (by message id, or carrying the hook's note). *Audio*: audio-file captures wait for the tick to transcribe them before they are annotated (three attempts at an empty transcript; a failing STT backend is an outage). *Reflect*: when due, the agent (no tools) reads only the period's entry files (plus the week's day reflections and its latest earlier analysis); every quote must appear in them, or nothing is written and it tries again (three attempts at a bad answer, then one alert); then the file is written and sent once. *Mirror*: after every successful send (prompt, manual re-send, annotation reply, reflection, week review, catch-up) the same text is appended to the companion's live chat session with Hermes' `gateway.mirror.mirror_to_session`, exactly as Hermes mirrors cron deliveries: a **user**-role row (an assistant-role mirror would make assistant→assistant pairs that break strict-alternation providers; a user row merges with his next message), framed `[Journal: Ada sent the evening prompt at 23:01 IST]`, `[Journal: Ada's annotations on 2026-10-14-evening, sent at 23:40 IST as a reply to his entry]`, `[Journal: Ada's reflection — Wed 14 Oct 2026, sent at 04:00 IST]`, then the full plain text. Before this the companion only saw Telegram's truncated `[Replying to: "…"]` quote of what she had sent. The gateway reloads the transcript from the database every turn, so the row is in her context on her next turn (merged ahead of his next message). Hermes stores no mirror metadata, so the `[Journal: ` prefix is how the sweep, the pack and the quote verifier skip those rows. Never mid-turn: the journal resolves the chat's live session itself (read-only, as Hermes does: the newest live Telegram session of the chat), passes its id to Hermes, and while that conversation holds a turn lease (the companion is answering) the mirror waits in `mirror-queue.json` and goes in on a later tick, its framing still giving the original time; a row appended mid-turn would merge into his message and make her answer look as if it ignored it. A manual `/morning` send runs on the gateway's bare interpreter, which cannot import Hermes, so its mirror is queued for the next tick too. Best-effort: a failed mirror is a log line, never a failed send; the ledger records `mirrored` (`true`, `false`, or `"queued"`); off with `hermes.mirror_to_chat: false`, and a no-op standalone or when `HERMES_HOME` is not the journal's profile. Costs, the same as Hermes' own cron mirror: the cross-process write makes the gateway rebuild its cached agent on the next turn, and context compaction summarises these rows like any other user row (the framing keeps them recognisable). The files hold only what was answered: an unanswered prompt's opener is not kept in the journal. Every delivered prompt is also appended to `sent.jsonl`, the engine's private memory of what it sent, pruned to the same `KEEP_DAYS` (120) as the ledger. *Outages*: `agent.ask` tells *no answer* from *a bad answer*. No answer (the run could not start, timed out, exited non-zero, or printed Hermes' failure text, which `hermes chat --oneshot -Q` does with exit 1 when the provider is unreachable or the turn fails) is an outage: annotation, reflection and transcription are deferred, not failed. They keep `deferred_since` / `last_deferred_at` / `defer_error`, are retried at most every 30 minutes for up to 7 days, and use no attempt; one alert (the tick exits 1 with a clear line) is raised once the outage has lasted 6 hours, and another when the 7 days run out and the work is dropped. A model that answers badly (empty, or fails validation) still uses an attempt as above. The tick exits 1 (so cron alerts) when a step crashes or a bounded retry runs out; retryable hiccups stay exit 0.

**When no model is reachable.** The journal keeps working without a model, deterministically: prompts go out on time with the plain opener and the full question bank; replies, `/j` notes and voice notes (Hermes' STT permitting) are saved verbatim; annotations, reflections and audio transcripts are deferred, not dropped (*Outages* above), and catch up on their own when a model answers again. In the chat, Hermes answers each message with its failure line (`⚠️ Something went wrong and I couldn't finish this reply…`); the journal sends nothing extra while the outage lasts. Hermes does not store that line: it closes the failed turn in the sessions database with an assistant row `display_kind = 'failed_turn'` ("Your request was not processed…"), which it strips before every model request. Afterwards (`recovery`), each tick reads the database (read-only) for the run of his messages since the companion's last real reply whose turns all failed (the newest one too: an unanswered newest message may be a live turn in progress). Interrupts and refusals close a turn the same way, so a catch-up also needs a sign it was an outage: a failed-turn row carrying the provider error, or the catch-up's own model run finding the model unreachable; otherwise nothing is sent. Once a model answers again (any model step earlier in the tick, or the catch-up's own attempt, at most every 30 minutes) one catch-up is written by a one-shot without tools, given those messages verbatim with their times, the outage window (first failure to recovery) and the conversation just before: say briefly and naturally that she was away, no technical detail, then answer what he said, inventing nothing. It must be short prose, not failure text, and quote only his messages (`validate.check_catchup`); it goes to Telegram as a reply to his last unanswered message (never while the companion is mid-turn) and is mirrored (`[Journal: Ada was offline from 14 Oct 22:10 to 15 Oct 03:05 IST; this reply was sent after reconnecting, to his messages from that time]`) so her live chat knows. At most one per outage (ledger `recovery` records; each covers the rows it answered). Nothing is sent if he has had a real answer since; messages older than `recovery.max_age_hours` (48) get no reply, only a mirrored note that he wrote while she was away, as do three bad answers in a row.

**Entry file.**

```markdown
---
date: 2026-10-14
kind: evening
practice: evening
type: journal
source: journal
---

# Evening journal — Wed 14 Oct 2026

## Prompt
_Sent 23:00 IST · opener: ada_
_Woven in: [9.2] What simple joys touched you today?_
<opener>
> [!question]- Questions
> **1. Brain Dump & Letting Go** …

## Sam
### 23:12 IST · text
<verbatim words>
### 23:40 IST · voice transcript · 2:31
![[2026-10-14-evening.ogg]]
<transcript>

## Conversation

> [!quote]- Ada · 23:41
> <her reply to his first message, folded>

**23:44** — <his next message, verbatim>

> [!quote]- Ada · 23:45
> <her reply to it>

## Ada's annotations
- …

## Ada's notes
### 08:02 IST
<the companion's own note>
```

**Reflection file** (`2026-10-14-reflection.md`; a week review is the same with `kind: week`, dated the week's last day):

```markdown
---
date: 2026-10-14
kind: reflection
type: journal
source: journal
author: ada
---

# Ada's reflection — Wed 14 Oct 2026

_From [[2026-10-14-morning]], [[2026-10-14-evening]] · written 14 Oct 23:45 IST_

**What stood out** — …
**Patterns and tensions** — … "your words, quoted exactly" …
**What moved** — …
**To carry forward** — one or two questions
```

Files are recognised as the engine's by name *and* front matter (`kind`, `author`), so a hand-written file of the same name is never listed as one or overwritten (the engine writes `-2` instead). Entries likewise: `YYYY-MM-DD-<id>[-sNN][-N].md` with an id known by name (the rituals, `j`, `note`), or any id (your own practices' included) when the file's own front matter says `kind: <id>` or `practice: <id>` *and* carries the engine's `type: journal`; your own `YYYY-MM-DD.md`, `YYYY-MM-DD-2.md` or a `…-thoughts.md` (even one you gave a `kind:`) is never read as one, and never receives the companion's notes. The engine's hidden `%% journal … %%` key lines are never part of your words when they are read (packs, annotations, quote checks). Context packs include the latest day reflection (within three days) and the latest week review whatever its age, dated, labelled as the companion's own analysis.

**Notebook.** `notebook.json` in the state dir, written only by the `journal_notebook` tool and annotation `FOLLOW-UP:` lines. Open items are injected into every turn (`pre_llm_call` context) and into the opener pack; resolved items move to `notebook-archive.jsonl`; items untouched for 21 days are archived as stale; at most 40 stay open.

**Guard.** A guardrail against a well-meaning model "fixing" its own infrastructure, not a security boundary: the agent runs as the same Unix user and a shell denylist cannot be complete. It refuses writes to protected paths, `hermes` subcommands that change things, system commands, `skill_manage` and `cronjob_manage` writes, and (optionally) `execute_code`. Every hook fails open: a bug never drops a message or blocks every tool.

## Conversations

Everything you say to the companion in its chat, and its text replies, is journal material: the tick writes it into the journal verbatim, from the Hermes sessions database (read-only), a few minutes after it is said. It is shaped as part of an entry, not as a chat log.

- **Threads.** Messages (yours and hers) belong to one thread while the silence between them stays under `conversations.quiet_minutes` (45). A journal prompt send ends a thread, and so does a Hermes cron delivery into the chat.
- **Where a thread goes.** If it starts with a journal reply or a `/j` note (or holds one as its first journal message), the rest of the thread goes into that entry. Otherwise it becomes a note entry exactly like a `/j` note (`YYYY-MM-DD-j.md`, `-j-2`, …), with your first message as the note's words and `capture: auto` in its front matter (a `/j` note says `capture: /j`). A journal reply later in the same thread takes the rest of it to that entry; nothing already written moves.
- **In the file.** A `## Conversation` section after your words and before the annotations. Your later messages are paragraphs with a small time marker (`**23:44** — …`; the date is added for the first line of a later day); voice notes are their transcript, with the audio kept beside the entry (`2026-10-14-evening-2.ogg`, original format) and embedded; audio files are kept the same way and transcribed by the tick. Each of the companion's replies is folded under the message it answers, as an Obsidian callout (`> [!quote]- Ada · 23:45`); her reply to your first message comes first. When you replied to an older message of hers, a folded `↩ replying to` line keeps that context. Your words are never rewritten; Hermes' envelopes (origin notes, reply quotes, the journal's own markers) are removed.
- **Hidden keys.** The section heading carries a hidden Obsidian comment (`%% journal:conversation %%`) and each block a key line (`%% journal his:<message id> %%`, `%% journal her:<row> %%`); they don't show in reading view. They let the writer tell its section from a `## Conversation` line in your own words, and never write a message twice, even after a crash; don't edit them. An entry written without an annotations section (a note saved from conversation, a practice that is not annotated) ends your words and the conversation with one more hidden line, `%% journal:end-of-words %%`, so a line of yours shaped like `## Ada's annotations` or `## Ada's notes` stays your words.
- **Her reply** is the text she ends her turn with: narration between tool steps, probes and Hermes' interruption notices are not saved, and a reply waits while her turn is still running (a long turn keeps its thread open; a message you send while she is working, which Hermes folds into her turn, does not end it either). A turn that took in an off-the-record message has no reply saved, even if you wrote something else in it too. Rows are read in the order Hermes stored them; rows that context compaction stored a second time are read once.
- **Never saved:** slash commands and their replies (`/j <text>` stays a note), Hermes and gateway rows (cron deliveries, compaction summaries, system notes, document and image placeholders: a caption you wrote with one is kept), the journal's own sends mirrored into the chat, failed turns, tool steps, and anything off the record.
- **Off the record: `/x`.** `/x <text>`: the message reaches the companion without the command, with a short marker telling her it is off the record and not to journal it; neither it nor her reply is saved anywhere in the journal, and it is left out of context packs and quote checks too. `/x` alone: the rest of the thread is off the record until it goes quiet for `quiet_minutes` or a prompt goes out (the slash command answers it; `/j` still saves a note). It shows in Telegram's command menu. The prefix is `conversations.opt_out_prefix`. It is enforced in code, not only asked of her: in a turn that took in an off-the-record message (the hook marks the turn in `hook-state.json`, so the mark outlives a gateway restart; for a message steered into a running turn without the hook, the newest user row in the sessions database is checked instead), `journal_add_note`, notebook writes and `journal_set_location` (a move said off the record can be said on the record later) are refused with a message she can relay (twice: by the tool guard before the call and by `tools.call` itself, so a guard that failed open does not let a write through). What she does in a later, public turn is up to her (the skill tells her to keep it out). Every privacy check fails closed: if the hook can't record a `/x` it still marks the message; if the tool guard can't tell, it refuses the write. A bare-`/x` window ends after `quiet_minutes` of silence on both sides (busy-path messages count as activity) or at a journal prompt; a cron delivery into the chat does not end it. Program phases are not sent into an open window unless you ask (`/next`), nor a practice an MCP client's `journal_start` asks for; a scheduled ritual still is, and ends it (an open point in `docs/DECISIONS.md`). Inside a window everything you send is off the record, a photo with no caption included (its picture is not kept, and a copy the hook made before it could tell is deleted at the next tick). A burst of messages Hermes merges into one is off the record as a whole when any line of it starts with `/x`, a `/j` note on its first line included, in the chat, the web app and MCP alike. Known limit: if `conversations.json` itself is unreadable, recorded off-record ids are lost (the marker in each stored message still protects everything the hook saw).
- **One privacy pass.** What is off the record is decided in one place (`privacy.py`): a pass over the chat's rows in the order Hermes stored them, with the bare `/x` commands the hook recorded and the prompt sends merged in by time. It marks single `/x` messages, bare-`/x` windows (half-open: they close at the first silence of `quiet_minutes` or at the next prompt, a busy-path `/x` included), and every reply of hers in a turn that took in a private message, until his next message after that turn. Every reader (the conversation writer, the sweep, context packs, the quote checker, annotation alignment, the outage catch-up, the tool guard, the hook itself) asks the same pass; the tick runs it first and persists it; if it can't run, everything counts as private until it can. A saved pass is not trusted as proof that it read a row: a reader that looks further back than the pass recomputed (a backfill, the 7-day pack, the quote checker, the 72-hour catch-up, the writer after an outage or after the sessions database was missing) recomputes over the rows it reads, and a pass is not saved while the sessions database is missing. A row Hermes stores again on context compaction (in place, with a new time, or in the child session of a rotation) is private when the original is. A message queued while she was busy is stored after rows with later times and carries the time you sent it: it is private when that time falls inside a window, open or already closed, and that time keeps the window going (so a message stored before it, sent within `quiet_minutes` of it, is inside too); a captionless photo inside a window takes her turn off the record as a message does. The saved pass names rows by id and keeps each one's time: a saved answer is not used for a row read under that id at another time (a replaced database, whose rows the pass recomputes). The view the gateway caches for a few seconds is taken again as soon as a prompt is sent from another process.
- **Your words stay yours.** Everything that reads an entry as your words leaves her folded replies out: the context pack's journal entries, the quote checker for openers, annotation input; reflections read the whole entry but see her replies labelled as hers (and shortened first when an entry is long), and may quote only you. Your conversation lines count as your words.
- **Annotations** are unchanged: written about `annotate_after_minutes` after the first capture of a ritual entry or a `/j` note. Note entries started from conversation are not annotated (`conversations.annotate_auto: false`); the day reflection covers them.
- **Context for the companion.** On the turn a journal reply is captured, her context also carries the context pack that prompt's opener was written from, trimmed to about 4,000 characters (the raw journal excerpts cut hardest) and labelled as such; for a `/j` note, the newest pack if it is under 12 hours old. Hermes keeps `pre_llm_call` context with the user row (`messages.api_content`) and replays it on later turns until the session is compacted, which is why it is trimmed and only added on that one turn.
- **Backfill.** `journal backfill-conversations --since YYYY-MM-DD` applies the same rules to past messages. By default it is a dry run: it lists, per file, what it would create or append, with message counts and a first-line preview, and writes nothing (no lock, no state). `--write` writes it. It never writes what an entry already holds (message ids, the writer's own keys, and, for replies saved before message ids were kept, a message that is most of such a saved block or a long passage of it), so it can be run again safely; it stops where the live writer started, or, if that never ran, the live writer carries on from where it stopped.
- **Where it lives.** `send-times.json` in the state dir keeps every prompt send time for good (the ledger keeps 120 days), so threads and a backfill of any age see their prompt boundaries. `conversations.json`: the cursor, the keys of what was written, the hook's off-the-record records (kept for good, like the send times: for a message steered in on the busy path inside a bare-`/x` window they are the only sign it is private, and a backfill or the quote checker can read chat of any age) and the privacy pass's results, and chat audio stashed by the hook until the tick writes it (`audio-stash/`, cleared after 3 days). The first tick after install starts from that moment; earlier chat is the backfill's.

**Known limits.** A message sent while the agent is mid-reply skips `pre_gateway_dispatch` (Hermes' busy path); the sweep captures it within one tick, but a *text* message folded into the running turn is stored without its reply pointer, so the sweep can only apply the "first message while a prompt is open" rule to it: a second direct reply to an already-answered prompt, sent while the companion is still replying, stays conversation. A `/j` note sent mid-reply is only recoverable when Hermes folded it into the turn (a queued slash command is discarded by Hermes). A swept voice note is recorded as its transcript without the embedded recording (Hermes does not link its cached audio to the message); a conversation voice note sent while the companion is busy is the same. Hermes stores no link from a reply to the message it answers, so a reply is placed by order. A thread that was open when the conversation writer was installed starts again as a new note. Voice capture depends on Hermes STT. Only Telegram is wired.

### Capture mode

`capture.mode` decides how much of the chat is journal material. **`everything`** (the default) is the section above: every thread is saved, into the entry it started from or a note of its own, and `/x` is how you keep something out. **`arm`** is for a companion that is a general agent with the journal as one of its arms:

- **Saved, as in `everything`:** a reply to a prompt (a direct reply, or the first message while it is open), a `/j` note, a program phase answer, a voice note or audio file answering a prompt, and what the companion saves with `journal_add_note`. The sweep is unchanged: it only ever caught up replies and `/j` notes the hook missed, and under `arm` the conversation writer waits for it (a message of his the sweep has not passed yet is never judged ordinary chat; with `sweep.enabled: false` the writer files such a reply itself). Replying to the journal's own words, an annotation reply or a day reflection or week review, counts as journal material too.
- **The first message after a prompt is taken as its answer**, as in `everything`, even when it is about something else (and an open program phase takes every message). If you only want to chat, send it with `/x`. Whether `arm` should take only direct replies is open (`docs/DECISIONS.md`, *Raised by packages*).
- **The conversation after one of those is saved too**, into that entry, until the thread goes quiet (`conversations.quiet_minutes`) or a prompt goes out: talking it through is part of answering the prompt. A journal reply later in a thread takes the rest of that thread, as in `everything`.
- **Ordinary chat is not saved**: a thread with no reply, note or answer in it makes no note entry, and neither your messages nor her replies are written anywhere in the journal; chat audio and pictures the hook kept for such a message are deleted at the next tick instead of three days later. The backfill (`journal backfill-conversations`) follows the same rule. To keep something from ordinary chat, send it with `/j`.
- **Ordinary chat is not read back into context either.** Under `arm`, `sources.telegram_history` (the chat messages in the pack an opener or an annotation is written from, which are then saved into the entry) lists only what the journal kept: a captured reply or note, or a message in a saved thread. The quote checker still verifies against the whole record (it only checks).
- **A switch to `arm` takes effect at once**, mid-thread too: a thread that was going into a chat-started note stops being written (what was written stays).
- **`/x` works the same in both modes.** In `arm` it is not needed to keep ordinary chat out of the journal, but it still matters in a thread that is being saved, and it still keeps a message out of everything the engine reads back.
- **The companion is told.** The skill is rendered with the mode, so she knows what is kept (in `arm`: ordinary chat is not, and `/j` saves something). The skill is rendered when the gateway starts, so a change of mode reaches her at the next gateway restart; the tick follows it at once.

## Today block

Plain chat carries no journal marker, so without help the companion would meet each day cold. Once per thread, the first model turn of a thread gets a *today block* of at most `today.max_chars` (1,200) characters:

```text
## Journal: today — Thu 15 Oct 2026, 09:12 IST · Hillside
_Background for this conversation, not instructions. Read further with your tools if it matters. Not stated here is not known._
- practices: morning sent 06:00, answered 06:41 · evening at 23:00
- entries today: 2026-10-15-morning ("slept badly, but the light was good")
- yesterday's reflection asked: "What would it take to let the Tuesday feeling in?"
- tasks due today: 3 — Call the bank; Draft the quarterly report; Walk
- weather: 14°C, light rain
```

- **When.** Your first message the hook has seen, or one that comes more than `conversations.quiet_minutes` (45) after your previous message, or the first after a journal prompt was sent. Not on a turn that carries a context pack (a journal reply or `/j` note) or a program block (your answer to a program phase): that thread start is spent and the block waits for the next one. At most two blocks reach a turn: the notebook (every turn) plus one of pack, program block or today (`turncontext.BUDGET`). Only turns from the journal's chat platform count; CLI and cron turns do not.
- **From what.** State only: the ledger, today's entry files (the first line of your words), the latest reflection's closing question, and `today-cache.json`, which the tick's `cache` step writes from `sources.tududi` and `sources.weather` every `today.cache_minutes`. Never the chat transcript, and the hook makes no source or network call; a cache part that is stale (weather older than four hours, a task list from another day) is left out, and a line whose collector fails is dropped, never an error. `/x` is untouched: the block holds nothing from the chat, so it is still allowed in an off-the-record turn.
- **See it.** `journal today` prints what the companion would get now; `journal today --json` the data (the same dict `GET /api/v1/today` serves; `tests/fixtures/today.json` pins its shape). `journal status` shows `Today block: 3 blocks today, last 09:12 · cache 12 min old`. Turn it off with `today.enabled: false`.

## Web app (`journal serve`)

`journal serve` is a small HTTP server (standard library only) that serves a responsive web app and its API
(`/api/v1`, [docs/API.md](docs/API.md)) for a phone or a desktop browser, installable as a PWA. It reads what the engine already
writes: entry files, the ledger, the library, reflections, the tick's caches. It never reads the chat transcript (the
privacy pass's territory), never calls a model, and never takes the tick's lock, so it keeps working with no model
reachable, and nothing said off the record can reach it: off-record words never reach a file in the first place.
It serves: today, entries (each as one two-voice document: your words plain, the companion's
replies folded and labelled), search over **your words only**, days, the companion's reflections, weekly counts, the
practice library, status, doctor and settings (secrets shown as `<set>`), plus `/events` (a server-sent event
stream that says when an entry, the ledger, the library or a tick changed), and program runs (`GET /runs`, a run's
open phase and the controls that apply now, its compiled insights).

**The app.** The package carries a built web app (Preact + TypeScript, source in `web/`, build in `src/journal/serve/static`)
and `/` serves it; a path with no file extension (`/entries/2026-10-14-evening`) answers `index.html` and the page
routes it. Six views: **Today** (the date, a week strip of answered and missed prompts from the ledger, each practice
with its state, what you wrote today, the reflection's question to carry forward, tasks due), **Entries** (by week and
day; filter by practice, program or audio; search your words), **Entry** (one continuous document: the prompt in a
muted wash with its questions folded, your blocks plain and full width with time and source in the margin and an audio
player for recordings, the companion's replies folded to one line with her name and time, and an Analysis tab for her
annotations, notes, the day's reflection and a program's summary), **Library** (the practices and programs, grouped,
with each definition's questions and the file itself), **Insights** (week by week: counts, the week's review, the day
reflections) and **Settings** (this browser's pairing with Unpair, or "Signed in via Tailscale as …", versions of the server and the tick, every setting with
the layer it comes from, doctor). They also write, through the routes below and nothing else:

- *Today* has **Reply** on a prompt that is waiting (a new entry of that practice) and **Send now** on one that is
  not; a card per program in progress with **Continue** and **Pause** or **Resume**; the one-off questions still to
  come today; and "No program running" when none is.
- *Entry* has a composer at the foot that adds a block of your words (text, or a voice note recorded in the page: the
  microphone is asked for only when you press Voice, and it needs a secure page) and **Mood, people, star**. Nothing
  edits a block that is there. An unsent draft is kept in the tab (`sessionStorage`), and a write that fails stays in the
  box with its error and a Retry that is the same write. A program session's answers go through the run.
- *Library* has **Send now**, **Turn on/off**, **Edit** (a built-in too: it is edited in place, under its own name,
  and an edited one says so, says when its default has changed since, shows the default beside your version, and has
  **Reset to default**), **Delete** for a practice of yours, **New practice** (a title, a time and a question is enough: the next tick
  sends it) and, for a program, **Start** or **Continue** with its progress bar. The editor is a form (title, emoji,
  times, sections and questions that move up and down, a moved question keeps its id by pinning it) and a **Raw
  Markdown** tab that is the file itself; the server checks as you type and says what is wrong beside the field. A
  file changed meanwhile (Obsidian) is never overwritten unless you say so. A program or situational definition is
  edited as its files, one at a time; while a run of it is in progress its sessions, phases and question numbers stay
  as they are (a change of words is sent from the next message).
- The **run screen** (`/runs/<id>`) shows the open phase as it was sent, a box for your answer, **Next**, **Skip**,
  **Back**, **Pause**, **Resume** and **Abandon** (as the server says they apply), a strip of phases and the run's
  sessions; a step takes a moment (it is a spawned `journal program …`) and the page follows the change.
- The **+** in the bar: a quick note, a voice note, an answer to a prompt that is waiting, or the start of a program.
- *Settings* changes location, time zone, the windows, the reflections, the quiet and settle minutes and the today
  block, each shown with where it lives; a setting in Hermes settings or the environment is locked, with the line that
  changes it.

**Installing it, and offline.** It is a PWA: open the site in a phone's browser and choose *Add to Home Screen* (or
*Install*) and it opens like an app, full screen, from the same address. A service worker keeps the app itself (so it
opens with no network) and the last answers of the read views (Today, Entries, an entry, the Library, reflections: network
first, the kept copy only when the server cannot be reached). Such a view says **offline, as of 09:12** at the top. Writes
need the network: a note or a block written offline is **not saved** ("You're offline; this will not be saved until you're
back"), and the words stay in the box (the tab's `sessionStorage`) so nothing is lost by trying. A new build shows
**Update available**; tap Reload when you like (or Later). Nothing kept outlives the session: **Unpair**, or any 401, empties what the
browser kept. A phone must be on the tailnet (or whatever network reaches the server) to read anything not kept yet; the
install itself needs a secure origin (`https://`, which the tailnet address has). The manifest names no host or port,
so the same build installs from any address that serves it. Pairing, the event stream and uploads are never kept.
On iOS the home-screen app can have its own cookie jar: if it opens unpaired, run `journal serve pair` again and paste the
new link into its pairing field. Rolling the server back to a build without a worker needs `deploy/killswitch-sw.js`
served as `/sw.js` (DEPLOY.md, PWA, Rollback): a 404 does not remove an installed worker.
The page is built for a thumb: touch targets of at least 44 px, the safe-area insets honoured, a bar at the foot on a
phone, the composer above it. Unknown fields in an answer are dropped at the client's
boundary, and the page never shows off-the-record material (it never reaches a file to be served). With no build
(`serve.assets_dir` pointing at an empty directory), `/` shows a placeholder page that can only pair a browser.

**Developing the app.** `web/` is built by CI, never on the host that runs the journal: `.github/workflows/web.yml` runs
`npm ci`, the type check, vitest, `npm run build`, and fails when the rebuilt `src/journal/serve/static` differs from
what is committed (it uploads the build as an artifact, which is how the committed copy is produced).
`static/build.json` records a hash of the build's inputs; `tests/test_web_build.py` recomputes it, so a change under
`web/` without a rebuilt `static/` fails the Python suite too, and `journal doctor`'s `serve assets` row names the
build. The browser smoke (Playwright against `tools/serve_fixture.py`, a throwaway server over a temporary journal)
runs in CI only; it also checks the service worker, the offline reads and a 44 px audit of every control on the phone
viewport. Icons are made from `web/public/icons/mark.svg` by `npm run icons` and committed. `tests/fixtures/api/*.json` pin the API answers the web tests read; regenerate them after an API change
with `JOURNAL_UPDATE_FIXTURES=1 python -m pytest tests/test_web_fixtures.py`. Locally: `cd web && npm ci && npm run dev`
proxies `/api` to `journal serve --dev --port 8790`.

**Writes.** The API can also add to the journal, always through the engine's own writers and its lock:

- *The engine only adds your words; you can change them.* A note (`POST /entries`) answers a practice's open prompt (the
  Telegram message then folds to "answered ✓") or, with no prompt open, becomes a `/j` note of its own; a block (`POST
  /entries/<stem>/blocks`) is appended after what you already wrote there. None of this touches the companion's side. A
  recording is uploaded first (`POST /uploads`, 25 MB, kept an hour) and named in the note or block; the tick
  transcribes it like a Telegram audio file.
- *Editing and deleting your own blocks* (`PATCH` / `DELETE /entries/<stem>/blocks/<key>`; **Edit** and **Delete**
  under each block of yours in the Entry view). Your entries are plain Markdown, so the web app is one more editor for
  you. Only the text of that block changes (or, deleting, the block: its heading, words and hidden key line); every
  other byte of the file stays, the companion's callouts, annotations and notes, the front matter and your other
  blocks included. It is a plain edit with no history. It goes through the same lock as the hook and the tick, and
  says what you were looking at: if the block (or the entry) changed since you read it, the answer is `409 changed`
  and nothing is overwritten (your edit stays in the box; reload and edit again). Her blocks refuse it. In a program
  session an answer can be edited but not deleted; the session's summary is the program's, filled from your words when
  the session completes, and editing an answer after that rewrites the summary from the new words (a moment later: the
  web app starts it and the page shows it on the next read). Only the `## Summary` section changes. The message that
  was sent to you is never changed, and an insights document already compiled keeps the old words until
  `/start <program> insights` compiles it again. If you changed the summary yourself in your notes app (added to it, edited a word, moved it or removed its hidden mark), the refresh leaves the whole section as you wrote it and says so in `manual-trigger.log`; so does a session completed before this behaviour existed. Another session's summary that quotes this answer (session 10 quotes earlier sessions) is not refreshed.
- *`/x` holds.* Text that starts with the off-the-record command (`conversations.opt_out_prefix`, default `/x`), or
  has a line that starts with it (as the chat takes a burst of messages merged into one), is refused with `422` and
  nothing is saved; so is a line shaped like the engine's own `%% journal … %%` marker, and the journal's
  "transcript pending" line for a recording.
- *Meta* (`mood`, `people`, `starred`) is set in an entry's front matter; the rest of the file is carried over byte
  for byte.
- *The library.* `PUT /library/<id>` saves a practice only if it parses, in canonical form, and only if the file is the
  one you read (`mtime`; a change made in Obsidian meanwhile is `409`, never overwritten); a built-in is edited in
  place (the first save makes your file of it; the packaged one is never written); a program takes one file at a time
  and keeps its structure while a run of it is in progress (`409 active-run`); `?validate=1` shows what would be saved
  without writing; `DELETE` resets an edited built-in to the default or removes a practice of yours;
  `GET /library/<id>/default` is the shipped one with the diffs. *Send now* (`POST /library/<id>/send`) spawns `journal send <id>`.
- *Settings* (`PATCH /settings`): location (through the same lookup as travel), timezone, coordinates, `windows.*`,
  `reflections.*`, `conversations.quiet_minutes`, `programs.settle_minutes`, `today.*`. A setting that lives in Hermes
  settings is `409` with the `hermes config set …` line to use; nothing else is writable from here. The page shows
  which layer each field is in. Saving writes a new config file beside the old one and renames it over it, so the
  file's directory must be writable by the user `journal serve` runs as (the unit has no sandbox to widen; a
  directory of its own, `~/.config/journal/`, is the tidy place). `journal config` below changes the same settings
  from a shell, with the same checks.
- `serve.allow_write: false` (or anything but `true`) makes every one of these `405`. A lock that cannot be had in five
  seconds is `423`; the client retries.
- *Programs.* A block on a program session's entry (`POST /entries/<stem>/blocks` with `phase`) is the answer to the
  run's open phase, exactly as a Telegram message would be (`%% journal phase:N %%`, the phase message folds), and the
  run then moves on by the program's own rule: `journal program settle <run>` is spawned (it waits
  `programs.settle_minutes` and moves on if you have gone quiet), or nothing for a program that waits for Next. A phase
  that is not the open one is `409`. `POST /runs` starts a program and `POST /runs/<id>/next|skip|back|pause|resume|
  abandon|insights` are the chat's commands as spawned `journal program …` processes; a command that means nothing for
  the run's state is `409`, and `expect` (the run's `where`) makes a stale tap do nothing, as the buttons do.

```sh
journal serve check                     # bind address free, token obtainable, same version as the tick
journal serve                           # foreground; http://127.0.0.1:8790/healthz
journal serve pair                      # prints http://127.0.0.1:8790/pair#<code>: open it in the browser once
curl -H "Authorization: Bearer $(cat ~/.local/state/journal/serve-token)" http://127.0.0.1:8790/api/v1/today
journal serve --print-unit > ~/.config/systemd/user/journal-serve.service      # review it first
systemctl --user daemon-reload && systemctl --user enable --now journal-serve
```

Access: every `/api` request needs the token (`Authorization: Bearer …`) or the session cookie pairing sets
(`HttpOnly; Secure; SameSite=Lax`, host-only; it is a signature of the token, not the token). `journal serve pair`
prints a link whose code sits in the URL fragment, which browsers never send to a server; the page posts it once
and the code is gone. `journal serve token --rotate` signs every browser out (and, for the generated token file,
replaces the token). Writes need the site's own `Origin`; with `serve.host` set, other `Host` headers are refused.
Responses carry a strict `Content-Security-Policy`, `nosniff`, and `Cache-Control: no-store` on the API; bodies are
never logged (`--access-log` records method, path, status and size). Network exposure (binding beyond loopback,
firewall, reverse proxy, DNS) is the operator's, not the code's; the unit binds whatever `serve.bind` says and, if
that address does not exist yet at boot, retries for 60 s before exiting so systemd restarts it.

**On a tailnet, no pairing.** Behind `tailscale serve`, the browser can be signed in by its Tailscale identity
instead of a cookie. Set `serve.socket` (say `/run/user/<uid>/journal-serve/serve.sock`) and `serve.tailscale_users`
(your logins), restart, and point Tailscale at the socket: `tailscale serve --bg --https=<port> unix:<path>`.
`tailscale serve` deletes any `Tailscale-User-Login` a client sent and sets the real one. The socket is 0600 and
the service user's, and the header counts only when the process that connected is root (tailscaled), as the kernel
reports it (`SO_PEERCRED`): another process of the same user, an agent's shell say, can open the socket but cannot
sign in with the header (a tailscaled that does not run as root is not supported). This is why the unit has no
filesystem sandbox: under a systemd user manager, `PrivateTmp=`, `ProtectSystem=`, `ProtectHome=`, `ReadWritePaths=`
and the like put the service in a user namespace of its own, where root reads as uid 65534 and no one is signed in
by identity; do not add them back in a drop-in (`NoNewPrivileges=`, the resource limits and `UMask=` are fine). tailscaled also relays
connections from the host itself, on its own tailnet address, and signs them in as the node's owner; so the client
tailscaled saw (`X-Forwarded-For`) must be another machine: loopback, an address of this host, or nothing usable is
no one, and a process here reaches the journal by its token only. A request the browser marks cross-site or
same-site (`Sec-Fetch-Site`) is not signed in by identity either (the SameSite=Lax cookie never was on those). The header counts only on the
socket: over TCP the token, cookie and pairing apply unchanged, and a header sent there is ignored. Writes still need the site's `Origin`, so set `serve.host` (and
`serve.public_port` when the HTTPS port is not 443).

Under Hermes, run it from the `uv tool` install with `--hermes-home <profile>` (the unit does) and the `hermes`
extra (`uv tool install 'journal[hermes] @ git+https://github.com/beejsbj/journal@<sha>'`) so it reads the same
plugin settings as the tick. Pin it to the plugin's commit: doctor's `same version as tick` row compares this
install's code with what the last tick ran.

## MCP (`journal mcp`)

`journal mcp` serves the journal to an [MCP](https://modelcontextprotocol.io) client over stdio, so an assistant you
already use can read your journal and add to it. It needs the official MCP Python SDK, an optional extra (the core
stays stdlib-only); without it, `journal mcp` says how to install it.

```sh
uv tool install --force 'journal[mcp] @ git+https://github.com/beejsbj/journal'          # standalone
uv tool install --force 'journal[mcp,hermes] @ git+https://github.com/beejsbj/journal'   # reading a Hermes profile's settings
```

It resolves its configuration exactly as every other command (`--config`, `--hermes-home`, the environment) and again
on every call, so a move or a new practice is seen at once. The tools are the engine's own calls, the same ones the web
app makes:

| Tool | What it does |
| --- | --- |
| `journal_today` | the today block: practices sent and answered, an open prompt, a program in progress, today's entries, the reflection's question |
| `journal_entries` | your entries, newest first (stem, title, first line); `query` searches **your words only** |
| `journal_entry` | one entry as the two-voice document: every block under a label saying whose words it is, yours or the companion's (`as_json` for the structured form) |
| `journal_save` | appends words you gave it, verbatim: the answer to a practice's open prompt (`practice`), else a `/j` note of its own |
| `journal_practices` | the practice library |
| `journal_start` | sends a practice now (a spawned `journal send <id> --hold-off-record`, as `/start <id>`), except while a bare `/x` holds the chat off the record (or the journal cannot tell): then nothing is sent, as with the journal's own automatic sends, and its answer says so; programs are started in the chat or the web app |
| `journal_program_status` | programs and the run in progress (`/program`) |
| `journal_add_note` | a note in the companion's voice, under the companion's notes in that day's entry |

Resources: `journal://today` and `journal://entries/recent`. The rules are the web app's: your words are only ever
appended, through the engine and its lock; text that starts with `/x`, or has a line that does, is refused and nothing
is saved; nothing reads
the chat transcript, and nothing calls a model. A client that runs `journal mcp` can read your whole journal, so add it
only to clients you trust with it.

Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "journal": {
      "command": "/path/to/journal",
      "args": ["--config", "/path/to/journal-config.json", "mcp"]
    }
  }
}
```

Claude Code:

```sh
claude mcp add --scope user journal -- /path/to/journal --config /path/to/journal-config.json mcp
```

Under Hermes, add `--hermes-home /path/to/hermes/profile` before `mcp`, as for `journal serve`. Only stdio is served;
the client starts the process and talks to it over its standard input and output.

## Update an installed plugin

What a change needs depends on where it runs. The tick is a fresh process every five minutes: the cron stub imports `journal` from the plugin's own `src/` (`<HERMES_HOME>/plugins/journal/src`), so tick-side changes (sending, sweep, audio transcription, annotations, reflections) apply from the next tick after the code is updated. The hooks, tools, slash commands and skill are loaded into the gateway at start, and with them most of the core (`journal.hermes` imports `inbound`, `capture`, `entries`, `privacy`, `guard`, `conversations`, `context`, `ledger`, `library` and more), so a change to any of that, `plugin.yaml` or the skill needs a gateway restart; only `serve/` and `mcp/` are certainly outside it (DEPLOY.md's opening table has the check).

```sh
hermes -p <profile> plugins update journal                                  # a plain git install
hermes -p <profile> plugins install beejsbj/journal --force --ref <sha>     # a pinned install (--ref): moves the pin; keeps settings and data
hermes -p <profile> journal doctor                                          # stub up to date, new settings sane
hermes gateway restart                                                      # when the change reaches the gateway (see above)
```

`plugins update` refuses a pinned install and prints the `install --force --ref` form. A forced reinstall of the same source is an update: it replaces the code and keeps `plugins.entries.journal.settings` and `plugin-data/journal`. Rerun `hermes journal setup` only if doctor says the stub is out of date (it changes when the plugin moves). The security scan's `role_hijack` caution appears again; confirm it.

## Develop

```sh
uv run --with pytest --with pyyaml python -m pytest -q    # or: python3 -m unittest discover -s tests
                                                          # tests never reach Telegram or a model
python3 tools/gen_builtins.py --check     # the built-in practice files are in the canonical form library.render writes
hermes plugins validate .                 # catalog checks
hermes plugins doctor . --ci              # discovery + register() in a temporary home
```

`tests/golden/` holds what the four rituals sent before they became files (bank lines, `[id]` banks, the id → question maps, the Telegram HTML and plain text for a fixed date, the plain openers; `tests/test_golden.py` holds the packaged files and the code to them byte for byte) and what the engine did with them before it was keyed by practice (entry files, folded headers, opener requests, the ledger record and `sent.jsonl` line of a send, a week of schedule; `tests/test_golden_engine.py`, whose `--write` regenerates them on purpose). Changing a built-in on purpose means editing its file and regenerating the goldens, with the reason in the commit.

## A deployment

A deployment's own specifics (names, paths, the chat, token helpers, source paths, guard paths, the serve host) live in an overlay kept with that install, outside the repo; [deploy/example-overlay.json](deploy/example-overlay.json) shows its shape with placeholder values. `journal migrate-config --from OLD.json --merge OVERLAY.json --to NEW.json` writes a config (and any practice forks a legacy `schedule` needs) from the hand-wired predecessor's flat file and the overlay; later changes go through `journal config` or the web app's Settings. Rolling out a release is in [DEPLOY.md](DEPLOY.md), exposing the web app in [docs/DEPLOY-FRONTEND.md](docs/DEPLOY-FRONTEND.md). Set `audio.ffprobe` to an absolute path when the cron job's `PATH` lacks the directory ffprobe is in.
