# Deploying an update

How an install is put together, and what an update touches. Newest section first; each package adds one (side, restart or not, first checks, rollback).

**The install.** A Hermes profile (`<profile>`; its directory is `$HERMES_HOME` below, exported, since the Python snippets read it from the environment) runs the journal three ways from one commit: the **plugin** (`$HERMES_HOME/plugins/journal`, installed pinned with `--ref <sha>` from `beejsbj/journal`, so `hermes plugins update` refuses it; moving the pin is a forced reinstall of the same source, which keeps settings in `plugins.entries.journal.settings` and state in `plugin-data/journal`), the **tick** (a Hermes cron job every five minutes whose stub imports the plugin's own `src/`), and **`journal serve`** (the web app and API, a systemd user unit running the `uv tool` install with `--config ~/.config/journal/<profile>.json --hermes-home $HERMES_HOME`). Pin the plugin and the CLI to the same sha: doctor's `same version as tick` row compares the code of the two. Settings: `config_file` is `~/.config/journal/<profile>.json` (a deployment may keep its own overlay beside it); the practices, programs and his edits of the built-ins are `plugin-data/journal/practices/` (no `schedule` key is read anywhere: times live in the practice files).

**Placeholders.** Before running a snippet, export the two paths it reads: `export HERMES_HOME=<the profile's directory> JOURNAL_ROOT=<the configured root>` (`journal doctor` prints both; `JOURNAL_ROOT` is this document's name for the configured `root`, not a variable the engine reads), and check them (`: "${HERMES_HOME:?}" "${JOURNAL_ROOT:?}"`), since an empty one turns a checksum or a path into a silent no-op. Angle-bracket names (`<profile>`, `<port>`, `<sha>`, `<previous sha>`, `<checkout>`, `<chat id>`) are replaced by hand: pasted as they are, the shell reads `<` and `>` as redirections.

**What needs what.**

| Where the change runs | Reaches the companion | Restart |
| --- | --- | --- |
| Tick side (sending, folds, annotations, reflections, the programs step, the sweep, audio) | the next tick after the plugin pin moves (the tick is a fresh process) | none, **unless the gateway also loads the module** (next row) |
| Hook side: anything the gateway process has imported. `journal.hermes` loads most of the core at import (`hermes/`, `inbound.py`, `capture.py`, `entries.py`, `compose.py`, `tools.py`, `commands.py`, and also `privacy.py`, `guard.py`, `conversations.py`, `context.py`, `ledger.py`, `library.py`, `sweep.py`, `today.py`, `programs.py` …), and function-level imports add more as they run; plus `plugin.yaml` and the skill. Treat any module outside `serve/` and `mcp/` as hook side unless checked: `python3 -I -c "import sys; sys.path.insert(0, 'src'); import journal.hermes; print(sorted(m for m in sys.modules if m.startswith('journal')))"` lists what loads at import | at the gateway restart | `hermes gateway restart` (every agent on the gateway offline for about a minute) |
| Serve side (`serve/**`, the committed web build) | `uv tool install --force` + the unit restart | `systemctl --user restart journal-serve`; the gateway never restarts |

The gateway restarts in **windows**, at least an hour from a ritual (the built-ins' default times: 06:00, Wed 12:00, Sun 20:00, 23:00): A (WP0 to WP3), B (WP4 to WP8), C (WP13, the last). A deploy section says which it rides. Rollback is always `hermes -p <profile> plugins install beejsbj/journal --force --ref <previous sha>` (and the CLI at the previous sha), plus a gateway restart when the section says the hook side changed. Receipts: the vault checksum (`sha256sum $JOURNAL_ROOT/*.md | sha256sum`) before and after any check that sends or stores a message.

## SCRUB-PERSONAL — the repo holds nothing specific to a deployment (2026-10-28; docs, tests and comments, one doctor row; no gateway restart needed; no config or state change)

**What changes.** Docs, tests and fixtures are generic; the deployment overlay left the repo
(`deploy/example-overlay.json` shows its shape). In code: the predecessor plugin doctor warns about is now the setting
`hermes.legacy_plugins` (default `[]`) instead of a name in `hermes/cron.py`; the opener request's citation example
reads `+00:00`; docstrings and a tool description changed. `tools/check_private.py` is new (development only).

**Side and restart.** `hermes/cron.py` (doctor only), `config.py` (a new default and a raw docstring), `prompts.py`
(one example string in the opener request) and `tools.py` (a tool description) are loaded by the gateway, but the
only behaviour that changes is the `legacy plugin` doctor row, which now fires only for a plugin named in
`hermes.legacy_plugins`. So **no gateway restart is needed**; the next window's restart picks the rest up. The tick
is a fresh process and sends the new example text from the next opener. Serve side: only `static/build.json`'s
fingerprint moved. Move the plugin pin and the CLI to the merge sha as usual.

1. Install the CLI at `<sha>` and move the plugin pin (`hermes -p <profile> plugins install beejsbj/journal --force
   --ref <sha>`); restart `journal-serve` so doctor's version rows agree. Nothing is sent.
2. First checks, without sending: `journal doctor` is as before; it shows no `legacy plugin` row unless
   `hermes.legacy_plugins` names a plugin that is still enabled. A deployment that relied on the old built-in name
   adds it there, in its config file, if that plugin could still be enabled.
3. Rollback: the CLI and the plugin pin at the previous sha, and restart `journal-serve`.

## BUILTIN-EDIT — built-ins edited in place, Reset to default, programs edited as files (2026-10-27; serve side and the CLI, the committed web build; no gateway restart needed; no config change; new state only when he edits)

**What changes.** The web app's Library offers **Edit** on every definition, a built-in included: the first save
writes his version of it into `practices_dir` under the built-in's own id (a practice or situational definition as
`<id>.md`, a program as `<id>/`), and an edited built-in shows an **Edited** panel with **Reset to default**, "the
default has changed since you edited it" when a later release changes it, and the default beside his version
(`/library/<id>/default`). Programs and situational definitions are edited as their files, one at a time; while a run
is in progress its structure is held (`409 active-run`). The CLI gains `journal practices reset <id>` and `diff <id>`,
and `fork` takes programs and situational definitions. API: `PUT` of a built-in now saves in place (it was `409
builtin`), `PUT` takes a program's `{raw, file, mtime}`, `DELETE` resets, `GET /library/{id}` carries `override`, list
rows `default_changed`, and `GET /library/{id}/default` is new (API.md). **State:** `practices_dir/.library` (beside
the existing `.library.lock`) is written the first time he edits a built-in, holding the shipped text the edit began
from. Where `plugin-data/journal/practices/` does not exist yet, there are no forks to carry over. Nothing is written to the vault; the packaged files are untouched
(DECISIONS.md, BUILTIN-EDIT).

**Side and restart.** Serve side (`serve/api.py`, `serve/writes.py`, the committed web build) and the CLI
(`cli.py`): `uv tool install --force` at the merge sha + `systemctl --user restart journal-serve`. `library.py` and
`programdefs.py` changed too, and the gateway imports both, but only with new functions and the writers `fork`,
`write`, `remove`, `reset`, `save_draft`, which no gateway path calls (`grep` of `hermes/`, `tools.py`,
`commands.py`, `inbound.py`, `tick.py`, `programs.py`: none); `load`, which the gateway and the tick use, is
unchanged. So **no gateway restart is needed**; the next window's restart picks the new module up with no change in
behaviour. Move the plugin pin to the same sha so doctor's `same version as tick` row stays green (the tick is a
fresh process and simply has the new functions). Open pages get the new shell through **Update available**.

1. Merge to `main`; with `<sha>` the merge commit: install the CLI at `<sha>`, move the plugin pin
   (`hermes -p <profile> plugins install beejsbj/journal --force --ref <sha>`), `systemctl --user restart journal-serve`.
   Nothing is sent.
2. First checks, all without sending: `journal practices list` shows the four built-ins as `built-in`;
   `journal practices diff evening` says "not edited". In the web app (after **Update available**): Library →
   Evening journal shows **Edit** and no fork button; Library → Fix your entire life → **Edit** opens the program's
   files with a file picker. To try the round trip without touching a prompt he uses, edit a built-in he does not
   rely on (or change nothing but a word), save, see **Edited**, then **Reset to default** and confirm: `ls -a
   $HERMES_HOME/plugin-data/journal/practices/` shows the file appear and go, and `.library` stay.
3. Rollback: install the CLI at the previous sha, move the plugin pin back, restart `journal-serve`. An edit he made
   keeps working under the previous build (it is a plain fork to it: same file, same id); the previous web app shows
   it as "customised" with "Reset to built-in", and reads `.library` not at all. Programs edited as files stay in
   force; the previous app just cannot edit them.

## CHANNELS — the journal's chat on any Hermes platform (2026-10-27; tick, hook and serve side; **gateway restart required**; no config or state change for current behaviour)

**What changes.** The journal's chat becomes a setting, `channel: {platform, chat_id, user_ids}`, on any platform
the gateway runs; without it, it is Telegram and `telegram.chat_id`, exactly as now. Every send and edit goes through
a `Channel` port (Telegram: the Bot API, the same calls and payloads; elsewhere: `hermes journal deliver`, Hermes' own
senders run inside Hermes, without Hermes' send mirror). The hook, the turn, the transcript, the sweep, the
conversation writer and the privacy pass ask which chat is the journal's (`channel.ours`) instead of testing the
string `telegram`. Telegram ledger records gain no field; doctor gains a `channel` row; `hermes.failure_deliver`'s
default is still `telegram:<chat_id>` on Telegram. DECISIONS.md, CHANNELS (taken and raised).

**Side and restart.** Hook side: `hermes/hooks.py`, `inbound.py`, `turncontext.py`, `capture.py`, `ledger.py`,
`privacy.py` (its cache key), `hermes/transcript.py`, `prompts.py`, `context.py`, `validate.py`, the skill and
`plugin.yaml` (a new `channel` schema entry) all load in the gateway, so **`hermes gateway restart` is required**; until
it, the gateway keeps the old hook (Telegram-only, which is what the config says anyway). The tick picks the new code
up at its next run. Serve side: serve spawns `journal send` and reads `config.py`, so install the CLI at the same sha
and restart `journal-serve` to keep doctor's `same version as tick` row green. Ride a window an hour from a ritual.

**No config change for current behaviour.** A `<profile>.json` with `telegram.chat_id` and no `channel` stays on
Telegram, the same chat, sent through the same Bot API with the same token. The companion's sessions on other platforms
(Discord, say) are not read, as before: the transcript reads `source = 'telegram'` sessions of the journal's chat only. Nothing to migrate in the ledger or state.

1. Merge; with `<sha>` the merge commit: the usual profile backups (`plugins/journal`, `plugin-data/journal/*.json`,
   `config.yaml`), then `hermes -p <profile> plugins install beejsbj/journal --force --ref <sha>`, the CLI at `<sha>`,
   `hermes gateway restart` in a window, `systemctl --user restart journal-serve`. Vault checksum before.
2. First checks (nothing sent):
   - `hermes -p <profile> journal doctor`: a `channel` row reading `telegram:<chat>; sent through the Telegram Bot API`
     (ok), the `telegram token` row as before, no new config warning.
   - `journal --hermes-home $HERMES_HOME today` prints as before.
   - The gateway log after the restart shows the plugin registered with no hook error; the next ritual folds in
     Telegram as before (`journal status`: the prompt open, then `answered ✓` / folded).
   - The first tick after the restart logs nothing new (`last-tick.json`), and doctor's `same version as tick` is green.
   - Vault checksum after: unchanged unless a ritual or a reply landed in between.
3. Rollback: `hermes -p <profile> plugins install beejsbj/journal --force --ref <previous sha>`, the CLI at the previous
   sha, `hermes gateway restart`, `systemctl --user restart journal-serve`. No state to undo: Telegram's records were
   written as before.

**To opt in to another platform later** (a deliberate change, not part of this deploy). For Discord: in the companion's
Discord DM, find its chat id (`hermes -p <profile> send --list discord`, or the `chat_id` of her `discord`/`dm` session in
`state.db`, opened read-only) and his Discord user id (`DISCORD_ALLOWED_USERS`). Then add to `<profile>.json`:

```json
"channel": {"platform": "discord", "chat_id": "<the DM channel id>", "user_ids": ["<his Discord user id>"]}
```

and give Discord the same slash-command allow-list Telegram has (`platforms.discord.extra.user_allowed_commands`:
`j, x, journal-start, journal_start, morning, evening, midweek, weekly, next, skip, back, break, abandon, program, web`) if
commands are gated there. The tick reads the change at its next run; the gateway at its next restart (it loads the
config per message, but restart so the skill's wording follows). Check with `hermes -p <profile> journal doctor` (`channel`
row: `discord:<chat>`, sent through `hermes journal deliver`), then `journal send morning --dry-run`, and one real
`/start morning` in the Discord DM when he is ready (it sends: the owner's call). Expect on Discord: prompts not folded,
annotations as plain messages, no phase buttons, plain text. The Telegram chat stops being the journal's at once:
nothing said there is captured any more, a reply to an older Telegram prompt included. Going back is removing the
`channel` key (and the same holds the other way).

## WEB-COMMAND — `/web` answers with the web app's address (2026-10-27; hook side; gateway restart; one Hermes config line; no state change)

**What changes.** A new chat command, `/web`, answers with the web app's address built from config (`https://<serve.host>`,
with `:<serve.public_port>` only when that is a usable port other than 443: the origin serve already uses for its
Origin check and pairing links) and one
line on signing in (Tailscale identity where `serve.tailscale_users` and `serve.socket` are set; see TSAUTH and
SERVE-UNSANDBOXED). It never sends a pairing link, a code or a token;
with no `serve.host` it says the web app isn't set up. The address is built by `config.serve_site`, which
`serve.auth.site` now returns (moved, not changed: the Origin check and `journal serve pair`'s link are the same
strings). Doctor's `serve.host` check (shared with `/web`) now also refuses `#`, `?`, `@`, `\` and brackets in the
host. `/web` is answered on Hermes' gated command path, not in the dispatch hook. The skill tells the companion to
point to `/web`. Nothing is written; no state or config file changes in
the code (DECISIONS.md, WEB-COMMAND).

**Side and restart.** Hook side (`commands.py`, `config.py`, the skill): **a gateway restart** registers `/web`.
Serve side only moves a function (`serve/auth.py` returns `config.serve_site`): `uv tool install --force` at the merge
sha and `systemctl --user restart journal-serve` keep the CLI, serve and the plugin on one sha (doctor's `same version
as tick`), with no behaviour change there. Ride a gateway window at least an hour from a ritual (06:00, Wed 12:00, Sun
20:00, 23:00). Nothing is sent by the deploy.

1. **Required, the owner or the operator (live Hermes config), before the restart:** in
   `$HERMES_HOME/config.yaml`, under `platforms.telegram.extra`, add `web` to
   `user_allowed_commands` (one spelling), keeping every name already there; copy the file first (`cp config.yaml
   config.yaml.pre-web-command`) and compare `grep -n user_allowed_commands` before and after. Without it `/web` gets
   Hermes' "admin only". Rollback of this step: remove the name, or put the copy back.
2. Merge; with `<sha>` the merge commit: `hermes -p <profile> plugins install beejsbj/journal --force --ref <sha>`, the CLI
   at `<sha>`, `hermes gateway restart`, `systemctl --user restart journal-serve`.

**First checks.**
1. `journal doctor`: `same version as tick` OK; the serve rows as before.
2. Read-only: `journal config get serve.host` and `journal config get serve.public_port` show what `/web` is built
   from (a public port of 443, or an unusable one, adds nothing; doctor names an unusable one). No `serve.host`
   problem in doctor.
3. Live, after the restart: `/web` in Telegram answers with the same origin `journal serve pair`'s links use (no `/pair`,
   no code) and, with `serve.tailscale_users` and `serve.socket` set, the Tailscale sign-in line; opening it on the
   phone signs in as before. A pairing on another browser still works (`journal serve pair` prints the same link as before).
4. Vault receipt: `sha256sum $JOURNAL_ROOT/*.md | sha256sum` unchanged.

**Rollback.** `hermes -p <profile> plugins install beejsbj/journal --force --ref <previous sha>`, the CLI at the previous
sha, `hermes gateway restart`, `systemctl --user restart journal-serve`; step 1's config line can stay (inert without
the command) or go. Nothing to undo in the state.

## BACKLOG-PRIVACY — five privacy-pass fixes: queued rows, a replaced database, the view cache, kept /x records, the pack's limit (2026-10-27; tick and hook side; gateway restart; no state or config change)

**What changes.** Five issues (DECISIONS.md, BACKLOG-PRIVACY). In the privacy pass
(`privacy.py`): a message queued while she was busy is private by the time he sent it, in a window open or already
closed, and that time keeps the window going; a captionless photo inside a window takes her turn off the record;
a saved private id is not used for a row read under that id at another time (a replaced database); the view the
gateway caches for a few seconds is taken again when the ledger, `sent.jsonl`, `send-times.json` or the sessions
database file changes. In the writer
(`conversations._prune`): the hook's `off` and `off_threads` records are no longer aged out. In the pack
(`context._his_messages`): `telegram_history.limit` counts the messages it shows. Every change makes rows more
private or shows more public ones; none makes a private row public. Entries already written are not revisited.

**Side and restart.** `privacy.py`, `conversations.py` and `context.py` load in the gateway at import
(`journal.hermes`), so the hook (`window_open`, the tool guard's taint) runs the old pass **until `hermes gateway
restart`**; the tick runs the new code from the next tick after the pin moves. Ride a gateway window at least an
hour from a ritual (06:00, Wed 12:00, Sun 20:00, 23:00). Serve and MCP import none of these (their fences):
`uv tool install --force` at the merge sha only to keep doctor's `same version as tick` row green; `journal-serve`
needs no restart for this. Nothing is sent by the deploy.

**First checks.**
1. `journal doctor`: `same version as tick` OK for the plugin and the CLI.
2. After the first tick: `last-tick.json` has no `privacy pass could not run` or `privacy pass not saved` line.
3. The cases on temporary roots, from a checkout at the merge sha (no Telegram, no live state, no vault):
   `uv run --with pytest --with pyyaml python -m pytest -q tests/test_privacy_backlog.py`
   passes.
4. Vault receipt: `sha256sum $JOURNAL_ROOT/*.md | sha256sum` unchanged by the deploy.

**Rollback.** `hermes -p <profile> plugins install beejsbj/journal --force --ref <previous sha>`, the CLI at the
previous sha, `hermes gateway restart`. Nothing to undo in the state: the format is unchanged, and the previous code
ages out `off`/`off_threads` older than 400 days again, as it did before.

## ONE-PROGRAM-RUN — one program unfinished at a time, and `/abandon <id>` (2026-10-27; tick/CLI, hook and serve side; gateway restart; one Hermes config line; no state change)

**What changes.** "One program" now means one unfinished run (DECISIONS.md, ONE-PROGRAM-RUN): while a program that
is not situational has a run active or on a break, mid-session or between sessions, starting another program is
refused with nothing changed, from any face (`/start`, `journal program start`, the web's Start). The answer names
the unfinished program and where it is, then the ways out: carry on with `/start <it>` and finish it, or end it with
`/abandon <it>`. A program's own run is never refused (resume, `/next` into its next session), and situational
sessions (`prep`, `debrief`) neither are refused nor refuse. `/break` pauses as before and holds the slot. **New chat
command `/abandon <id>`** (named only; `/abandon` alone lists what could be ended) spawns `journal program abandon
<run> --notify`. `/program`'s hint line names `/abandon` and the rule. The web app's 409 is renamed
`program-in-progress` and its line links "Carry on with it" to the unfinished run (new web build). The companion's
`journal_program propose` adds `refused` when the line would be refused; the skill says the rule and `/abandon`.
No new state: run files, the ledger and the config are read as before.

**Side and restart.** Hook side (`commands.py`, `programs.py`, `programview.py`, `config.py`, the skill): **a gateway
restart**, which registers `/abandon`. The spawned `journal program …` and the tick run new code from the moment the
plugin pin moves, so between the pin and the restart the rule already holds in the child (a refused `/start` comes
back as a `--notify` note after "on its way"). Serve side (`serve/writes.py`, the committed web build): `uv tool
install --force` at the merge sha and `systemctl --user restart journal-serve`. Ride a gateway window at least an hour
from a ritual (06:00, Wed 12:00, Sun 20:00, 23:00). Nothing is sent by the deploy.

1. **Required, the owner or the operator (live Hermes config), before the restart:** let the companion's chat run `/abandon`. In
   `$HERMES_HOME/config.yaml`, under `platforms.telegram.extra`, add `abandon` to
   `user_allowed_commands` (one spelling: no hyphen), keeping every name already there; copy the file first (`cp
   config.yaml config.yaml.pre-one-program-run`) and compare `grep -n user_allowed_commands` before and after. Without
   it `/abandon` gets Hermes' "admin only" (the refusal's other way out, carrying on, still works, and the web app's
   Abandon is unaffected). Rollback of this step: remove the name, or put the copy back.
2. Merge; with `<sha>` the merge commit: `hermes -p <profile> plugins install beejsbj/journal --force --ref <sha>`, the CLI
   at `<sha>` (`uv tool install --force`), `hermes gateway restart`, `systemctl --user restart journal-serve`.

**First checks.**
1. `journal doctor`: `same version as tick` is OK for the plugin and the CLI; `serve assets` names the new build.
2. `journal program status` (read only): the runs in progress. If two programs that are not situational are both
   unfinished, that is the pre-deploy state carrying on: each goes on, a third is refused (DECISIONS.md,
   ONE-PROGRAM-RUN (2)).
3. The rule on temporary roots, from a checkout at the merge sha (no Telegram, no live state, no vault):
   `uv run --with pytest --with pyyaml python -m pytest -q tests/test_one_program.py`
   passes.
4. Live, read-only: `/abandon` alone in Telegram answers with the list (or "No program is running …") and ends
   nothing; `/program` shows the new hint line. Do not abandon or start a program to test this.
5. Vault receipt: `sha256sum $JOURNAL_ROOT/*.md | sha256sum` unchanged by the deploy.

**Rollback.** `hermes -p <profile> plugins install beejsbj/journal --force --ref <previous sha>`, the CLI at the previous
sha, `hermes gateway restart`, `systemctl --user restart journal-serve`; step 1's config line can stay (an allowed
name with no command is inert) or go. Nothing to undo in the state: a refusal changes nothing, and `/abandon` leaves
what the web's Abandon would have left (a run in progress is `abandoned`; a finished run with interrupts still to
come stays `completed`, those interrupts cancelled). Neither is undone by the rollback.

## BACKLOG-BACKFILL — backfill and conversation fixes (2026-10-27; tick and hook side; gateway restart; no config change)

**What changes.** Five fixes from the October review audit (DECISIONS.md, BACKLOG-BACKFILL):
`journal backfill-conversations` honours a prompt sent between a queued row's time and the latest time it handled;
a recording or picture in the conversation of an entry whose ledger path names another root is queued for
transcription or description, and that prompt's `entry_path` is rewritten to the file in this journal; the
120-day prune keeps a prompt while a transcript (or, with descriptions on, a picture description) is pending;
an entry the engine starts without an annotations section (auto notes, practices with `annotate: false`) ends his
words with a hidden `%% journal:end-of-words %%` line, so a line of his shaped like the annotations heading (`## <companion>'s annotations`) stays his
words; a message steered into her running turn no longer writes the text she held before it. Existing
entry files are not rewritten; new unannotated files gain the hidden line.

**Side and restart.** `conversations.py`, `tick.py` and `hermes/transcript.py` run in the tick (a fresh process: the
next tick after the pin moves). `entries.py`, `conversations.py`, `ports.py` and `hermes/transcript.py` are loaded by
the gateway at import, so **restart the gateway in the same step as moving the plugin pin**: until it restarts, the
gateway's old `entries.py` does not know the hidden line, and a reply it appends to an auto note the new tick started
lands after the line. The new code then reads that file by the rule without the line (the line counts only when
nothing but her annotations or notes follows it), so the reply is his words as before this package; only that file loses the end-of-words
protection. Rides the next gateway window. Serve reads entries through `entries.py`: `uv tool install --force` at the same sha and
`systemctl --user restart journal-serve`, so the web app reads the line too (until then it shows the same as now).

1. Merge to `main`; with `<sha>` the merge commit: usual profile backups, then move the plugin pin
   (`hermes -p <profile> plugins install beejsbj/journal --force --ref <sha>`), `hermes gateway restart`, install the CLI
   at `<sha>`, `systemctl --user restart journal-serve`. Nothing is sent; the vault is not written by the deploy.
2. First checks: `journal doctor` green, `same version as tick` included; the next tick's `last-tick.json` has no
   error. The next auto note in `$JOURNAL_ROOT` (a chat that starts no entry) ends with
   `%% journal:end-of-words %%` before any notes (hidden in Obsidian's reading view), and its words and conversation
   read as before in the web app.
3. Rollback: plugin pin and CLI back to the previous sha, `hermes gateway restart`, restart `journal-serve`. Files
   written meanwhile keep their hidden line; the previous code reads it as a line of the words or conversation
   section and strips it from what it shows (an Obsidian comment, so nothing shows there either), and a rewritten
   `entry_path` names the file that exists. Moving forward again after a rollback is safe for the same reason: a
   reply or a `## Conversation` the previous code appended after the line makes that file read without it.

## BACKLOG-TESTS — tests and the README's Guarantees table only (2026-10-27; nothing deploys)

**What changes.** `tests/` and `web/tests/` (new pins for the guarantees the audit listed, `GuardTest` rebuilt on a neutral
layout) and README *Guarantees*. No module under `src/` changes, the committed web build is untouched
(`web/tests/` is not bundled), no config or state. **Side and restart:** none; not even a plugin pin move is needed.
**First checks:** CI green (the `web` workflow's `npm test` runs the new vitest case). **Rollback:** revert the merge.

## IDENTITY — the web app's look: "Margin × Letters", her ink chosen per device (2026-10-27; serve side only, the committed web build; no gateway restart; no config or state change)

**What changes.** Only the PWA's committed build (`src/journal/serve/static/`): new faces (Newsreader, Courier Prime,
Instrument Sans, Fraunces' figures; Literata is gone), the page and the letter (his words in the book's roman, hers typed
in her ink under a "From" line), a three-column spread on a wide screen for Today and an entry, typed figures, the mark
and the app icon (`icons/*.png`), and **Ink** in Settings (blue-black, brown, violet, green; kept in the browser's
`localStorage` as `journal:ink`, never sent to the server). The API, the engine, the config and every file of state are
as before (DECISIONS.md, IDENTITY).

**Side and restart.** Serve side only: `uv tool install --force` at the merge sha + `systemctl --user restart
journal-serve`. **No gateway restart:** no Python module changed (`tools/web_fonts.py` is a maintainer's tool, not part
of the package). Move the plugin pin to the same sha so doctor's `same version as tick` row stays green; the tick needs
nothing. Open pages get the new shell through the service worker's **Update available** prompt (tap Reload). The
precache grows by about 175 KB of faces, fetched once per device.

1. Merge to `main`; with `<sha>` the merge commit: install the CLI at `<sha>`, move the plugin pin
   (`hermes -p <profile> plugins install beejsbj/journal --force --ref <sha>`), `systemctl --user restart journal-serve`.
   Usual profile backups before. Nothing is sent and nothing is written to the vault.
2. First checks: `curl -s http://127.0.0.1:<port>/healthz` shows `<sha>`'s code; `journal doctor`'s `serve assets`
   row names the new build. On a phone and a desktop browser: **Update available**, Reload; Today shows the hour in the
   margin and her letter typed in blue-black; Settings → **Ink** → Green turns her words green at once and stays green
   after a reload; the desktop (a wide window) shows her letter in a column to the right of his words on Today and on an
   entry. The home-screen icon changes only when the browser refreshes the installed manifest (Android does so within a
   day; reinstalling forces it).
3. Rollback: install the CLI at the previous sha, move the plugin pin back, restart `journal-serve`. The previous
   build's service worker replaces this one at the next **Update available**. A stored `journal:ink` is ignored by the
   previous build (it reads no such key).

## ONE-PROGRAM — one program session in progress at a time, from any face (2026-10-26; tick/CLI, hook and serve side; gateway restart; no config or state change)

**What changes.** Starting or taking up a program session (`/start <program>`, `/next` or `/skip` between sessions,
`journal program start|resume|next`, the web app's Start, Resume and Next) while another program's session is in
progress is refused, with nothing changed, and the answer names the program mid-session, its session and how to go
on (`/break`, `/next` or finishing, `/start <it>`). A run between sessions or on a break blocks nothing; `/start` of
the program already mid-session re-sends its open phase as before. The tick begins no session, so nothing it does is
refused. `/next`, `/skip`, `/back` and `/break` (and `journal program …` with no run) now act on the
run with a session in progress when there is one, not on the run touched last. The web app answers `409 session-in-progress` and shows the line; its build is unchanged. No new state is
written: run files, the ledger and the config are as before. Two runs already mid-session at deploy both carry on
(DECISIONS.md, ONE-PROGRAM).

**Side and restart.** The rule is in `programs.py`, which the spawned `journal program …` (the plugin's own `src/`)
and the tick load: from the moment the plugin pin moves. The chat's own answer is in `commands.py` (hook side): **at
a gateway restart**; until then `/start` says "on its way" and the refusal comes from the spawned child as a Telegram
note (`--notify`), so nothing is unsafe in between. Serve side (`serve/writes.py`): `uv tool install --force` at the
merge sha and `systemctl --user restart journal-serve` (after SUMMARY-REFRESH's `KillMode=process` check). Ride a
gateway window at least an hour from a ritual (06:00, Wed 12:00, Sun 20:00, 23:00). Nothing is sent by the deploy.

**First checks.**
1. `journal doctor`: `same version as tick` is OK for the plugin and the CLI.
2. `journal program status` (read only): note which runs are mid-session ("phase k/n …") and which are between
   sessions ("whenever ready") or on a break. More than one mid-session is the pre-deploy state carrying on, not a
   fault.
3. The rule on temporary roots, from a checkout at the merge sha (no Telegram, no live state, no vault):
   `uv run --with pytest --with pyyaml python -m pytest -q tests/test_one_program.py`
   passes.
4. Live, only when a program is mid-session anyway and he wants to: `/start <another program>` in the journal's
   chat answers with the refusal there and nothing is sent; `/program` shows the same runs as before.
   Do not start a program to test this.
5. Vault receipt: `sha256sum $JOURNAL_ROOT/*.md | sha256sum` unchanged by the deploy.

**Rollback.** `hermes -p <profile> plugins install beejsbj/journal --force --ref <previous sha>`, the CLI at the previous
sha, `hermes gateway restart`, `systemctl --user restart journal-serve`. Nothing to undo in the state: the change
writes no new field, and a refused start changed nothing.

## SERVE-UNSANDBOXED — the serve unit drops its sandbox, so Tailscale identity signs him in (2026-10-26; serve side only; a unit change and a unit restart; no gateway restart; no config change)

**What changes.** The owner's decision (DECISIONS.md, SERVE-UNSANDBOXED; closes the Tailscale sign-in report): `journal serve --print-unit` no
longer renders `PrivateTmp=yes`, `ProtectSystem=strict`, `ProtectHome=read-only` or `ReadWritePaths=…`. Under the user
manager those put the service in a user namespace where tailscaled (root) reads as uid 65534 on the socket, so no
browser was signed in by Tailscale identity and every one fell back to pairing. Everything else in the unit stays,
`NoNewPrivileges=yes` included (it builds no namespace: checked with throwaway units, peer uid 0). A socket under
`/tmp` is no longer refused by `--print-unit`; instead the server refuses, at start, a socket directory that is
not the service user's own or that others can write (directly in `/tmp`, say), and never reuses another user's socket file
(`/run/user/1000/journal-serve`, made 0700 for the service user by systemd, passes). `serve.socket` is
normalised (`a/../b` is `b`). The settings `503` (config directory not writable) now says to make the directory
writable for the service user, or under an older sandboxed unit to re-render it, instead of naming `ReadWritePaths`. No state, file format or config key
changes; his words are not touched; nothing is sent.

**Side and restart.** Serve side only (`serve/unit.py`, `serve/app.py`, `config.py`, `cli.py`, `settings.py`). The gateway, the tick and the
plugin pin need nothing for this (the pin can move at the next window; `config.py` changes only `serve_socket`,
which only serve and doctor read, and `settings.py` only the text of a refusal). The unit file is the live install: back it up, change it, keep the receipt.

1. Merge to `main`; `uv tool install --force` the CLI at the merge sha (as in the sections below).
2. Render and compare. Read the live unit, do not edit it in place:
   `journal --config ~/.config/journal/<profile>.json --hermes-home $HERMES_HOME serve
   --print-unit > /tmp/journal-serve.service` then
   `diff -u ~/.config/systemd/user/journal-serve.service /tmp/journal-serve.service`. Expect exactly four lines
   removed (`PrivateTmp=yes`, `ProtectSystem=strict`, `ProtectHome=read-only`, `ReadWritePaths=…`) and, if
   SUMMARY-REFRESH's step 1 has not been applied yet, `KillMode=process` added after `TimeoutStopSec=20s` (that is
   wanted: it is SUMMARY-REFRESH's line). `RequiresMountsFor=/srv ~/.hermes`, the
   `ExecStartPre` lines, `RuntimeDirectory=journal-serve` and the limits are unchanged. Anything else: stop and ask.
3. Back up, install, reload, restart:
   `cp -p ~/.config/systemd/user/journal-serve.service ~/journal-serve.service.before-unsandboxed`;
   `cp /tmp/journal-serve.service ~/.config/systemd/user/journal-serve.service`;
   `systemctl --user daemon-reload`; `systemctl --user restart journal-serve` (after the reload, so the stop already
   uses `KillMode=process` and a queued send survives it).

**First checks.**
1. `systemctl --user show journal-serve -p PrivateUsers -p ProtectHome -p PrivateTmp -p ProtectSystem -p
   NoNewPrivileges -p KillMode` shows `PrivateUsers=no`, `ProtectHome=no`, `PrivateTmp=no`, `ProtectSystem=no`,
   `NoNewPrivileges=yes`, `KillMode=process`. `PrivateUsers=no` was reported before too, so also check the
   process itself: `P=$(systemctl --user show -p MainPID --value journal-serve); readlink /proc/$P/ns/user
   /proc/self/ns/user; cat /proc/$P/uid_map` gives the same namespace twice and `0 0 4294967295` (before:
   a namespace of its own and `1000 1000 1`).
2. `journal … serve check` and `journal doctor` as before; `ls -l /run/user/1000/journal-serve/` shows `srw-------
   user` (the service user); `curl -s --unix-socket /run/user/1000/journal-serve/serve.sock http://localhost/healthz` answers.
3. **Nothing on the host is him** (identity is live for the first time, so this matters now): from the host as the service user,
   `curl -s -o /dev/null -w '%{http_code}\n' https://host.tailnet.ts.net:8790/api/v1/session` prints **401**,
   as does `/api/v1/today`; and straight to the socket with a forged identity,
   `curl -s -o /dev/null -w '%{http_code}\n' --unix-socket /run/user/1000/journal-serve/serve.sock -H
   'Tailscale-User-Login: <his login>' -H 'X-Forwarded-Host: host.tailnet.ts.net:8790' -H 'X-Forwarded-For:
   100.64.0.9' http://localhost/api/v1/session` prints **401** (curl runs as the service user, not root). A 200 on either: roll back
   (below) and stop.
4. **On a phone** (another tailnet device, through `tailscale serve`): open
   `https://host.tailnet.ts.net:8790/` in a fresh private window. Today loads with no pairing screen; Settings
   says "Signed in via Tailscale as …" with no Unpair; `https://host.tailnet.ts.net:8790/api/v1/session` shows
   `"via": "tailscale"`. The same in a desktop browser clears the report's first symptom; its other one (a desktop browser's "Can't
   reach the journal server") is a separate check and may have another cause.
5. `journal … config list` shows the file-layer keys writable (no "cannot write in"): the Settings page can still save.
   No vault write is made by any of these; if a test entry is saved from the phone, it is his entry: take the vault
   checksum before and after.

**Rollback.** Restore the backup and put `KillMode=process` back in it, always (the backup predates that line, and
without it the restart kills a queued `journal send` or `journal program …` child):

```sh
U=~/.config/systemd/user/journal-serve.service
cp -p ~/journal-serve.service.before-unsandboxed "$U"
grep -q '^KillMode=process' "$U" || sed -i '/^TimeoutStopSec=/a KillMode=process' "$U"
grep -n KillMode "$U"                                   # one line: KillMode=process
systemctl --user daemon-reload
systemctl --user show journal-serve -p KillMode         # KillMode=process, before the restart
systemctl --user restart journal-serve
```

Identity stops working again and pairing applies; cookies paired before still work. The CLI
need not go back: the old unit runs the new code as before.

## SENT-AS-SENT — a folded prompt keeps the companion's opener; a send record keeps its question set (2026-10-26; tick, hook and serve side; gateway restart; no config change)

**What changes.** (1) Once a prompt's window passes (or it is answered), its Telegram message is edited to the header
line, the opener exactly as sent and a short italic status line (`answered ✓` / `missed — reply anytime`), instead of
the header alone with the status in it. The questions and the hint go. A manual re-send's old message gets the same
("sent again below"). Program phases still fold to their header. (2) `tick.send_due` stores the question set in the
prompt's ledger record (`questions: [{id, text, section}]`); the entry page and Today read it first
(`serve/document.prompt_of`), so a practice edited afterwards no longer changes the questions shown under the old
opener. Records sent before this fall back to the library, as before. No state or file format is migrated: a record
without `questions` is valid; a message folded before keeps its old text.

**Side and restart.** Tick side (`tick.py`, `library.py`), **hook side** (`compose.py`, `capture.py`, `sweep.py` are
imported by the gateway hook) and serve side (`serve/document.py`). Ride a gateway window: move the plugin pin to the
merge sha, `hermes gateway restart`, then `uv tool install --force` at the merge sha and
`systemctl --user restart journal-serve`. Not before an hour from a ritual. Nothing is sent by the deploy itself.

**Order.** This window also deploys WEB-DESIGN, SUMMARY-REFRESH and CONTEXT-PACK; their sections'
steps and first checks run too. First, SUMMARY-REFRESH's step 1: `KillMode=process` on the live unit
(`systemctl --user show journal-serve -p KillMode` says `process`) **before** any `systemctl --user restart
journal-serve`, here or there.

**First checks.**
1. `journal doctor`: `same version as tick` is OK for the plugin and the CLI.
2. A fold rendered from a fixture, with no message sent (nothing here touches Telegram, the state or the vault), by
   the installed tool's own interpreter (after `uv tool install --force`):
   `~/.local/share/uv/tools/journal/bin/python -c "from datetime import datetime; from journal import compose; print(compose.telegram_html('evening', datetime(2026,10,14,23,0).astimezone(), 'Evening. Let the day land.', 'missed — reply anytime'))"`
   prints a `<b>…Evening journal · Wed 14 Oct</b>` header, the opener, then `<i>missed — reply anytime</i>`, and no
   `<blockquote`.
3. After the next ritual's window passes (it folds at `windows.collapse_hours`, 6 h for the built-ins), look at the
   prompt in Telegram: header, her opener, the status line. Do not trigger a send for this.
4. After the first ritual sent by the new code, read its record from a copy of the state (never the live ledger):
   `T=$(mktemp -d); cp -a $HERMES_HOME/plugin-data/journal "$T/state"` then
   `python3 -c "import json,sys; p=json.load(open(sys.argv[1]))['prompts'][-1]; print(p['id'], len(p.get('questions') or []))" "$T/state/ledger.json"`
   shows a non-zero count. Opening that day's entry in the web app shows the same questions. Nothing is edited on the
   live instance for this check.
5. Vault receipt: `sha256sum $JOURNAL_ROOT/*.md | sha256sum` unchanged by the deploy.

**Rollback.** `hermes -p <profile> plugins install beejsbj/journal --force --ref <previous sha>`, the CLI at the previous sha,
`hermes gateway restart`, `systemctl --user restart journal-serve`. Records written meanwhile keep an extra
`questions` key that the old code ignores; folded messages keep their new text (Telegram edits are not undone, and the
old code would fold the rest to the header alone).

## CONTEXT-PACK — the pack knows how long it has been (2026-10-26; tick side from the next tick, hook side at a gateway restart; no config change)

**What changes.** The context pack an opener is written from opens with *where things stand*, counted by code with no
model: when he was last heard from on the record, his last entry, the prompts unanswered since his last practice
reply (per practice, at most 14 days back), each program in progress (session k of n, phase, movement, his last
answer, the stated pace) and the gap in days. Then her last two unanswered openers word for word, his newest 6
entries whatever their age with their date and age, her latest week review whatever its age, and at the end
*where to look further*, made from the config and the disk. The whole pack is held to `pack.max_chars` (32000). Past
3 days of silence on the record the opener request asks for a welcome back without a recap. The today block opens
with the same facts (the program line is the new, longer one). Nothing off the record counts: `/x` messages and
windows never move "last heard", and a privacy pass that cannot run counts nothing from the chat. No ledger, entry or
file-format change; `today-cache.json` gains a `heard` key (a time, never text). New config key `pack` (defaults
need nothing in `<profile>.json`); `today.sources` gains `standing`, on by default (on wherever the config does not list
`today.sources`). A configured `prompts.reading_guidance` is unchanged.

**Side and restart.** Tick side (`context.py`, `standing.py`, `prompts.py`, `tick.py`, `today.py`'s cache step):
**from the next tick** after the pin moves; the next opener is the first to use it. Hook side (`today.py`'s block,
read by `turncontext` in the gateway): **at a gateway restart** (a window at least an hour from a ritual: 06:00, Wed
12:00, Sun 20:00, 23:00); until then the gateway's today block is the old one, harmlessly. Serve side: no route
changes (`GET /today` is the same shape), but install the CLI at the same sha and restart `journal-serve` so doctor's
`same version as tick` row holds.

1. Receipts and backups as usual (profile backup, `sha256sum $JOURNAL_ROOT/*.md |
   sha256sum` before). With `<sha>` the merge commit: `hermes -p <profile> plugins install beejsbj/journal --force --ref
   <sha>`, the CLI at `<sha>` (`uv tool install --force …@<sha>`), `systemctl --user restart journal-serve`, then
   `hermes gateway restart` in the window (every agent on the gateway offline for about a minute).
2. First checks, nothing sent and nothing written outside a temp root. Inspect a generated pack and today block
   built from a copy of the state (the vault and the companion's `state.db` are only read; `state.db` opens `?mode=ro`):

   ```sh
   T=$(mktemp -d); cp -a $HERMES_HOME/plugin-data/journal "$T/state"
   sha256sum $JOURNAL_ROOT/*.md | sha256sum > "$T/vault.before"
   JOURNAL_STATE_DIR="$T/state" ~/.local/share/uv/tools/journal/bin/python - <<'PY' > "$T/pack.md"
   import os
   from datetime import datetime
   from journal import config, context, standing
   cfg = config.load("~/.config/journal/<profile>.json", hermes_home=os.environ["HERMES_HOME"])
   now = datetime.now(cfg.tz)
   print(context.build(cfg, now, standing.collect(cfg, now)))
   PY
   JOURNAL_STATE_DIR="$T/state" journal --config ~/.config/journal/<profile>.json \
     --hermes-home $HERMES_HOME today > "$T/today.txt"
   sha256sum $JOURNAL_ROOT/*.md | sha256sum | diff - "$T/vault.before" && echo vault unchanged
   wc -c "$T/pack.md"; sed -n '/^## Where things stand/,/^## Recent journal/p;/^## Where to look further/,$p' "$T/pack.md"
   ```

   Expect: the pack under 32000 characters; "Last heard from on the record" matching his last on-record reply or chat
   message (never a time inside an off-the-record thread); the unanswered counts matching the ledger; a fix-life line
   if a run is active; pointers for the entries, the analyses, the notebook archive and each configured source
   (tududi, scrobbler, ListenBrainz, reading, the weather), and none for a source that is off or unreachable. The today block shows
   `last heard` only when that was before today (its chat time comes from the tick's cache, so after the first
   `cache` step, at most 30 minutes after the pin moves). `rm -rf "$T"` afterwards: the copy holds his words.
   Then: `hermes -p <profile> journal doctor` shows no new problem; the next tick's log has no `error:` line; after the
   next ritual, `plugin-data/journal/packs/<prompt id>.md` opens with `## Where things stand` and the ledger's
   `opener_source` is the companion's or a `plain:` reason that is not new: `claim without citation` (the returning stance's
   "you were away" without the Gap line cited), `unverified quote from journal:…` (a quote now verifies only against
   his words, never her annotations, notes or reviews), `unrecognised source` (a journal-like source name the check
   doesn't know) or `too long` would point here. A tick line `opener: cited-elsewhere: …` is not an error: a quote of
   his passed under another entry's name; it names the two files only.
3. Rollback: `hermes -p <profile> plugins install beejsbj/journal --force --ref <previous sha>`, the CLI at the previous
   sha, `journal-serve` restarted, and the gateway at its next restart. No state to undo: the old code ignores the
   `heard` key in `today-cache.json` (its cache step drops it on the next write), and a `pack` key, if one was added
   to `<profile>.json` meanwhile, should be removed with it.

## SUMMARY-REFRESH — an edited phase answer rewrites its completed session's `## Summary` (2026-10-26; serve side and tick side; the live unit gets one line; no gateway restart)

**What changes.** Editing one of his phase answers in the web app, after the program session has completed, now writes
that session's `## Summary` again from the edited words (the owner's decision, 2026-10-26; DECISIONS.md, SUMMARY-REFRESH).
`PATCH /entries/<stem>/blocks/<key>` spawns a new command, `journal program refresh-summary <run> --session N`, which
takes the tick's lock and calls `programs._write_summary` (the ledger lock inside it, as `_refill_summary` does). Only
the `## Summary` section of that entry changes (the marked, engine-written section; his blocks, the conversation, the
annotations and the front matter are carried over byte for byte). The Telegram summary already sent and a compiled
insights document are not touched; `/start <program> insights` compiles the document again from the new words. No file
format or config key changes; the run file gains `summary_sha` on a session (the digest of the `## Summary` section as the engine wrote it, kept at completion, refill and refresh). The edit response gains `summary` (`refreshing` or `not-started`) for such an
edit only.

**Side and restart.** Serve side and tick side; neither restarts the gateway. The route is serve side
(`serve/writes.py`): `uv tool install --force` of the merge commit, then `systemctl --user restart journal-serve`.
The command is in `cli.py` / `programs.py`, which the serve process spawns from its own install (`python -m journal`):
the same install, so the unit restart brings both. The tick's `programs` step also changes: completion and the
refill now keep the digest (`summary_sha`), so move the plugin pin to the same sha **before the first program session
completes** (a session completed by an older tick has no digest and is never refreshed, DECISIONS.md); a tick is a
fresh process, so it reads the new code from the next tick after the pin moves. `entries.py` does change (the
digest functions; `write_summary` now returns the digest), and it is on the hook side list above, but the gateway's
hook does not call `write_summary` or the new functions (only `programs` does), and the old `entries.py` it keeps
loaded until a restart behaves as before for everything it does call: **no `hermes gateway restart`** is needed for
this package, and the hook-side file simply picks up the new code at the next restart anyway.

1. **The live unit needs `KillMode=process`** (the generated unit and `deploy/journal-serve.service.example` have it
   now; the installed one does not until it is changed). Without it every `systemctl --user restart journal-serve`
   stops the spawned `journal send` / `journal program …` children that are waiting for the tick's lock, so a send or a
   refresh queued at that moment is lost. Apply it at this deploy: `journal … serve --print-unit` and compare with
   `~/.config/systemd/user/journal-serve.service`, or add `KillMode=process` under `[Service]` (after
   `TimeoutStopSec`), then `systemctl --user daemon-reload`. `systemctl --user show journal-serve -p KillMode` must say
   `process` before the restart in step 2.
2. Merge to `main`; `uv tool install --force` from the merge commit; `systemctl --user restart journal-serve`.
3. First checks: `journal program refresh-summary --help` lists the action; `journal … serve check` and `doctor` as
   before. The behaviour needs a completed program session: on a scratch run only (a completed session of the live run
   is his own journal, so do not use one), edit one of its phase answers in the web app, wait a few seconds, reload:
   the `## Summary` shows the new words and the entry differs from before in that section and the edited block only.
   `tail manual-trigger.log` shows `session N summary written again from his edited words`. Receipt: per-file hashes
   before and after, `sha256sum $JOURNAL_ROOT/*.md | sort -k2 > /tmp/journal-before.txt`
   (and `-after`), then `diff /tmp/journal-before.txt /tmp/journal-after.txt` names exactly the one scratch entry (an
   aggregate hash could not say which file, or that it was only one). Also: type a line under the `## Summary` of that scratch entry in the notes app, edit an
   answer again: the line and the summary stay as they are and the log says `left as it is`.
4. Known limits: the refresh is a spawned process, so the summary changes a moment after the edit is saved (longer if
   the tick holds its lock), and a failure shows in `manual-trigger.log` only (the tick does not retry it; editing the
   answer again spawns it again). It never replaces a summary he changed, moved or whose mark he removed, nor one
   completed by earlier code (no digest): those keep the old words. A session that is open or whose summary waits for
   a transcript is not refreshed by the edit (the summary is written then, from the words as they are). Other sessions'
   summaries that quote the answer (session 10's `s1:5.1`-style slots) and a compiled insights document are not
   refreshed.
5. Rollback: `uv tool install --force` the previous sha and restart `journal-serve`. A summary already rewritten
   stays (it is the engine's section, filled from his words as they were edited); `summary_sha` on the sessions is
   ignored by the earlier code; `KillMode=process` may stay. Nothing else depends on this code.

## WEB-DESIGN — the web app's design pass, Today's prompts in full, "Use my location", the Tailscale sign-in screen (2026-10-26; serve side only, at the unit restart; no config change)

**What changes.** The PWA's look across every screen (one token sheet, the Literata reading face served from the app,
a tab bar with icons, grouped Settings); Today shows every prompt sent today in full, opener and questions, whatever
its state (`GET /today` rows gain `prompt`, and the answer gains `voices`); Settings gains **Use my location**
(`POST /api/v1/settings/location`, which names the place with Nominatim and the time zone with Open-Meteo and saves
them through `location.set_location`, as a typed place is saved); a refused read under Tailscale sign-in shows
"Couldn't confirm your Tailscale sign-in — Retry" instead of the pairing form. Telegram's folding is untouched; the
companion's thread-start block (`today.collect`) is unchanged. No state or file format changes.

**Side and restart.** Serve side only (`serve/api.py`, `serve/document.py`, `serve/writes.py`, `settings.py`,
`location.py`, the committed build in `serve/static/`): `uv tool install --force` at the merge sha +
`systemctl --user restart journal-serve`. **No gateway restart** (nothing on the hook side changed: `location.py` and
`settings.py` are also what `journal_set_location` and `journal config set` call, and their behaviour for a typed
place is the same; the gateway picks the code up at its next restart whenever that is). Move the plugin pin to the
same sha so doctor's `same version as tick` row stays green; the tick needs nothing. Open pages get the new shell
through the service worker's **Update available** prompt (tap Reload); a page that is not reloaded keeps working
against the new server, since the old client drops the new fields.

1. Merge to `main`; with `<sha>` the merge commit: install the CLI at `<sha>`, move the plugin pin
   (`hermes -p <profile> plugins install beejsbj/journal --force --ref <sha>`), `systemctl --user restart journal-serve`.
   Usual profile backups and a receipt of the vault checksum before.
2. First checks: `curl -s http://127.0.0.1:<port>/healthz` shows `<sha>`'s code; `journal doctor`'s `serve assets` row
   names the new build. On a phone and a desktop browser: the **Update available** prompt appears; after Reload, Today
   shows the morning prompt's opener and its questions under the card, and the Settings page shows the place and time
   group with **Use my location**. Tap it once on the phone: the browser asks for permission, then the line reads
   "Now in <place> · <time zone> …" with "Place name © OpenStreetMap contributors";
   `tail -1 <state_dir>/location-history.jsonl | jq .query` prints `"device position"` (only that field: the line also
   holds the place's coordinates, which a receipt has no need of);
   `journalctl --user -u journal-serve --since -5min` has no coordinates in it. If the place it names is wrong, type
   the place and save as before (the same write). No message is sent; vault checksum before and after (nothing here
   writes to the vault).
3. Rollback: install the CLI at the previous sha and restart `journal-serve` (and move the plugin pin back). The
   previous build's service worker replaces this one at the next **Update available**. A place saved by the button
   stays saved; to undo it, the previous values are the `before` of the last `location-history.jsonl` line (set them
   with `journal config set location …`).

## PRIV3 — `/x` and his-words seams after the window-D review (2026-10-20; tick side from the next tick, hook side at the next restart, serve side at the unit restart; no config change)

**What changes.** Fixes, each fail closed or narrower than before; no file format, ledger or state change, and his
words are not rewritten. Off the record: a busy-path burst of a `/j` note and a `/x` message on a later line is no
longer swept; a captionless photo inside a bare-`/x` window is private (not kept, not embedded, its vision note not
in packs or openers), and a picture the hook stashed for a message that turns out private is deleted at that tick;
the web app (`POST /entries`, `POST`/`PATCH …/blocks`) and MCP (`journal_save`, `journal_add_note`) refuse text with
`/x` on a later line (422 / a tool error), as the chat does; MCP's `journal_start` now spawns `journal send <id>
--hold-off-record`, which sends nothing while a bare-`/x` window is open; an unusable `conversations.quiet_minutes`
(infinite, huge) is capped at a year instead of breaking the hook's window check. His words: the audio fill only
touches the recording's own block; a picture whose block he removed is not described; the "transcript pending" line
is refused as his words in the web app (422 `placeholder`); a chat photo or recording after a session's phases is
no longer filed as phase 1's answer. Not changed: the guard bypass class (raised in `docs/DECISIONS.md`).

**Side and restart.** Tick side (`sweep.py`, `privacy.py`, `conversations.py`, `tick.py`, `entries.py`'s fill,
`programs.py`, `ledger.py`, `config.py`): **from the next tick** after the pin moves. Hook side (`privacy.py`'s pass and
`window_open`, `config.py`'s `quiet_minutes`, read in the gateway): **at the next gateway restart**; no restart of its
own is needed (the cap changes nothing for an ordinary value such as the default 45, and until the restart the
gateway runs the code it has). Serve side (`serve/writes.py`, `serve/api.py`) and MCP (`mcp/**`, run by the CLI
install): `uv tool install --force` at the same sha + `systemctl --user restart journal-serve`; an MCP client picks
it up at its next launch of `journal mcp`. Pin the plugin and the CLI to the same sha (the spawned `journal send
--hold-off-record` is the CLI's: an older CLI would refuse the flag, and the send would not happen at all).

1. Merge to `main`; with `<sha>` the merge commit: move the plugin pin (`hermes -p <profile> plugins install
   beejsbj/journal --force --ref <sha>`), install the CLI at `<sha>`, restart `journal-serve`. Usual profile backups
   and a receipt of the vault checksum before.
2. First checks: `hermes -p <profile> journal doctor` shows no new problem (no `quiet_minutes` row: the value is the
   default); the next tick's log has no `error:` line; `journal --config ~/.config/journal/<profile>.json send
   --help` lists `--hold-off-record`. In the web app, a block edited to `fine` + newline + `/x test` is refused
   (`422 off-the-record`) and nothing changes in the entry. No chat message is needed: the rest shows on the next real
   off-the-record thread and recording. Vault checksum before and after (nothing here writes to the vault by
   itself).
3. Rollback: `hermes -p <profile> plugins install beejsbj/journal --force --ref <previous sha>`, the CLI at the previous
   sha, `journal-serve` restarted (and the gateway at its next restart). No state to undo (a conversation capture filed on a
   session's newest phase reads to the old code as the old writer's did, on phase 1).

## SCANCLEAN — the tree passes Hermes' plugin install scan (2026-10-20; no behaviour change; no restart)

**What changes.** Nothing the journal does. Installing the plugin at `cdbfe75` was hard-blocked by Hermes' plugin
scanner (`tools/plugin_guard.py`, `plugin-guard-v11`): a "dangerous" verdict, which `--force` does not override, from two
false positives. (1) A first-checks line in this file piped `curl` into `python3 -m json.tool`; it now pipes into `jq`.
(2) The sha512 integrity of `workbox-cacheable-response` 7.4.1 in `web/package-lock.json` happens to contain the letters
x-m-r-i-g, which the crypto-mining pattern matches case-insensitively. `web/package.json` now has `overrides` pinning
`workbox-cacheable-response` to 7.4.0 (clean integrity) and `workbox-core` to 7.4.1 (so the tree keeps one core copy);
cacheable-response is a build-time dependency of workbox-build only, and the service worker (`src/sw.ts`, injectManifest)
does not use it, so `sw.js` and the assets are byte-identical and only `static/build.json` (the source fingerprint)
changed. New `tests/test_install_scan.py` runs the scanner's CRITICAL patterns over the tree so this cannot recur
unseen.

**Side and restart.** None needed: no code path changed. Serve side gets a new `build.json` only (served as before).
Tick and hook sides are untouched. The pin can move to this commit with the next real change, or alone; either way
`hermes plugins install` no longer meets a dangerous verdict (it asks to confirm: findings of lower severity remain).

**First checks.** `git clone` the pinned sha to a temporary directory and run Hermes' `scan_plugin` on it (temporary
`HERMES_HOME`, `HERMES_DISABLE_LAZY_INSTALLS=1`): the verdict is not `dangerous`. Never `hermes plugins validate` here.

**Rollback.** Move the pin back. (The previous pin is not installable until the two lines above are fixed, which is the
reason for this change.)

## CAPMODE — `capture.mode`: what gets journaled (2026-10-20; tick side + skill; no config change needed; no gateway restart of its own)

**What changes.** A new setting, `capture.mode`: `everything` (the default, the behaviour live since window A) or
`arm` (the journal as one arm of a general agent: prompt replies, `/j` notes, program answers, replies to the
journal's own annotations and reflections, and the conversation after them are journaled; ordinary chat makes no
entry and is not read back into context packs). The owner's decision of 2026-10-20 (`docs/DECISIONS.md` row 12,
"CAPMODE"): **an install that stays on `everything` sets nothing in `<profile>.json` or an overlay** and journals exactly as
before. New in `everything` too: a `capture mode` row in doctor and a `Capture mode:` line in status, and one fix: after
a tick whose sweep failed, his second message is no longer filed as the prompt's answer when his first was already
journaled (it had gone into two entries). No file format, ledger or state changes; his words are not touched.

**Side and restart.** It goes on top of windows B and C (once both are deployed) as an ordinary update. Tick side (`conversations.py`, `sweep.py`, `context.py`, `status.py`, `config.py`): the next tick after the pin
moves. Hook side (`hermes/__init__.py` and `SKILL.md`: the skill is rendered with the mode at gateway start): for
`everything` the rendered skill and the registered skill description are byte-identical to the ones now live, so
**no gateway restart is needed**; the new code reaches the gateway at the next routine restart, and nothing in it
changes behaviour there before then.

1. Merge to `main`; with `<sha>` the merge commit: move the plugin pin (`hermes -p <profile> plugins install
   beejsbj/journal --force --ref <sha>`) and install the CLI at `<sha>`. Do not add `capture` to `<profile>.json`. Take the
   usual backups of the profile first and a receipt of the vault checksum.
2. First checks, after the pin moves: `hermes -p <profile> journal doctor` shows `capture mode  ok  everything: every chat
   thread is journaled …` and no new warning; `hermes -p <profile> journal status` shows `Capture mode: everything (…)`. The
   next ordinary chat thread with the companion still becomes a `YYYY-MM-DD-j.md` note (`capture: auto`) a few minutes later, as
   before; no test message is needed for that, the next real one shows it. Vault checksum before and after the pin
   move (nothing in this change writes to the vault by itself).
3. Rollback: `hermes -p <profile> plugins install beejsbj/journal --force --ref <previous sha>` and the CLI at the previous
   sha. No state to undo (the setting is absent).

## IMAGES — photos and image files are kept beside the entry like audio (2026-10-20; hook side + tick side + serve side; rides restart window B)

**What changes.** A photo (or a jpg/png/webp/heic/gif sent as a file) that answers a prompt, a `/j` note or a program
phase is copied into the journal root as `<entry-stem>.<ext>` (`2026-10-14-evening.jpg`, then `-2`), embedded in his
block above his caption (`### 14:02 · photo`, `![[2026-10-14-evening.jpg]]`), and the caption stays verbatim. A
photo in ordinary chat is stashed by the hook and written into the conversation's entry by the tick. `/x` keeps
nothing. New settings `images.max_mb` (20) and `images.describe` (false; when true the tick adds a one-line
description of each picture under her notes, marked hers). `/media` serves the kept pictures (still only those an
entry embeds) and the entry page shows them. New files in the vault root: image files named after entries; no file
that exists is rewritten.

**Side and restart.** Hook side (`inbound.py`, `capture.py`, `entries.py`, `hermes/hooks.py`): **needs the gateway
restart, window B**; until then photos behave as before (the caption is captured, the picture is not kept). Tick side
(`conversations.py`, `tick.py` images step, `agent.py`): the next tick after the pin moves. Serve side (`serve/**`, the
committed web build): `uv tool install --force` + `systemctl --user restart journal-serve`. The pin and CLI move to
the same sha.

1. Merge to `main`; use the merge commit in the window B runbook for the plugin pin and the CLI alike. `plugin.yaml`
   gains the `images` setting (default values; nothing to set). To use descriptions later:
   `plugins.entries.journal.settings.images.describe: true` (and a companion that takes `--image`; Hermes does).
   HEIC is kept and embedded but not described (Hermes' `--image` does not take it), and only Safari shows it in the
   web app.
2. After the gateway restart and the tick: `hermes -p <profile> journal doctor` shows every row ok or as before, with no
   `images.*` problem. Send one photo as a reply to a prompt: expect `<stem>.jpg` beside the entry, the embed above
   the caption in the entry, and the picture in the web app. The vault receipt applies, since this check stores a
   message: `sha256sum $JOURNAL_ROOT/*.md | sha256sum` before and after, expecting only
   that entry to differ. Do not test `/x` with a real photo in the companion's chat just to see it; `tests/test_images.py` covers
   it.
3. Rollback: reinstall the plugin and CLI at the previous sha (`hermes -p <profile> plugins install beejsbj/journal --force
   --ref <previous sha>`), restart the gateway if the hook side had been restarted onto this change, and restart
   `journal-serve`. Pictures already kept stay in the vault with their embeds (Obsidian shows them); older code ignores
   `images` on a capture, and an older serve does not recognise the `· photo` heading (such a block reads as part of
   the block above it until the unit is back on this sha).

## TSAUTH — `journal serve` on a Unix socket, signed in by Tailscale identity (2026-10-20; serve side; no gateway restart)

**What changes.** The owner's decision of 2026-10-20: on the tailnet, opening the URL on any of his devices just works,
with no pairing. Two new optional settings. `serve.socket`: `journal serve` listens on that Unix socket as well as on
TCP (made 0600 at start, removed at stop). `serve.tailscale_users`: on the socket only, a request whose one
`Tailscale-User-Login` header is a listed login is signed in with no cookie or token, provided the process that
connected is root (tailscaled, read by `SO_PEERCRED`, so the companion's own tools, which run as the service user, cannot use the
header) and the client tailscaled saw (`X-Forwarded-For`) is another machine: tailscaled also relays connections
from the host itself and signs them in as the node's owner, so without that check any process on the host would be him
(found in review, fixed here; processes on the host use the token, as before). A request the browser marks cross-site
or same-site is not signed in by identity. Over TCP nothing changes: token, cookie and pairing as before, and the header is ignored. Writes still need
`Origin: https://host.tailnet.ts.net:8790` (`serve.host` + `serve.public_port`). New route `GET
/api/v1/session`; the web app shows "Signed in via Tailscale as …" and hides pairing and Unpair. With neither
setting, behaviour and the rendered unit are byte-identical to before. No state or file format changes; his words
are not touched.

**Side and restart.** Serve side only (`config.py`, `status.py`, `cli.py`, `serve/app.py`, `serve/auth.py`,
`serve/api.py`, `serve/unit.py`, the committed web build). `uv tool install --force` of the merge commit, a
re-rendered unit, `systemctl --user restart journal-serve`. The gateway, the plugin pin and the tick need nothing.
`tailscale serve` is reconfigured, which is the operator's (Decision 8). Not run by the package; for the
operator:

1. Merge to `main`; `uv tool install --force` the CLI at the merge commit.
2. In `~/.config/journal/<profile>.json`, under `serve` (beside `host: host.tailnet.ts.net` and
   `public_port: 8790` from SERVEPORT): `"socket": "/run/user/1000/journal-serve/serve.sock"` and
   `"tailscale_users": ["<his Tailscale login>"]` (the login exactly as `tailscale status --json` lists it under
   `User[].LoginName`). A deployment overlay, if any, is not changed.
3. Re-render the unit: `journal --config ~/.config/journal/<profile>.json --hermes-home
   $HERMES_HOME serve --print-unit > /tmp/journal-serve.service`; diff it against
   `~/.config/systemd/user/journal-serve.service`: the only change should be `RuntimeDirectory=journal-serve` and
   `RuntimeDirectoryMode=0700` (this is what makes the socket's directory exist and be writable under `ProtectSystem=strict`; on some hosts
   the unit's sandbox is not applied, see DECISIONS "Raised": the directory is still created by systemd). Back up the old unit, install the new one, `systemctl --user daemon-reload`, `systemctl
   --user restart journal-serve`.
4. First checks (before touching Tailscale): `journal … serve check` shows `serve socket: unix:… (0600)` and `serve
   identity: Tailscale logins signed in on the socket: …`; `ls -l /run/user/1000/journal-serve/` shows `srw-------
   user`; `curl -s --unix-socket /run/user/1000/journal-serve/serve.sock http://localhost/healthz` answers; the same
   with `-H 'Tailscale-User-Login: <his login>' -H 'X-Forwarded-Host: host.tailnet.ts.net:8790'
   http://localhost/api/v1/today` answers **401** (curl runs as the service user, not root: the header does not count); the TCP
   listener still answers on `127.0.0.1:8790` exactly as before. (This does not test the relay case below; step 6
   does, once Tailscale points at the socket.)
5. Switch Tailscale (backup first: `tailscale serve status --json > ~/tailscale-serve-before-tsauth.json`):
   `tailscale serve --bg --https=8790 unix:/run/user/1000/journal-serve/serve.sock` (replacing the
   `http://127.0.0.1:8790` proxy on that port; `tailscale serve status` then shows `|-- / proxy
   unix:/run/user/1000/journal-serve/serve.sock`).
6. **Check that the host itself is not him** (the one that matters): from the host, as the service user, `curl -s -o /dev/null -w
   '%{http_code}\n' https://host.tailnet.ts.net:8790/api/v1/session` must print **401**, as must the same
   with `--resolve host.tailnet.ts.net:8790:100.64.0.1`; and `/api/v1/today`. A 200 means a process on this
   host is signed in as him: run the rollback (step 7, first line) and stop. Then the same URL from a phone
   (not the host) must load.
   Checks on a device: on a phone and a laptop (logged into the tailnet as the listed login), open
   `https://host.tailnet.ts.net:8790/` in a fresh/private window: Today loads with no pairing; Settings says
   "Signed in via Tailscale as …" with no Unpair; a write (an entry save) succeeds. `/pair` goes straight to Today.
   No vault write is needed for the read checks; a test write is his entry, so take the vault checksum before and
   after if one is made.
7. Rollback, cheapest first: `tailscale serve --bg --https=8790 http://127.0.0.1:8790` (back to TCP, pairing
   applies again; cookies paired before still work); or remove `socket`/`tailscale_users` from `<profile>.json`, restore
   the backed-up unit, `daemon-reload` and restart; or reinstall the CLI at the previous sha.

## MCP — `journal mcp`, the journal's tools for an MCP client (2026-10-20; CLI only; no restart)

**What changes.** A new command, `journal mcp`: an MCP server over stdio that an MCP client (Claude Desktop, Claude
Code) starts itself, with eight tools (`journal_today`, `journal_entries`, `journal_entry`, `journal_save`,
`journal_practices`, `journal_start`, `journal_program_status`, `journal_add_note`) and two resources. It needs the
optional extra `journal[mcp]` (the `mcp` SDK, 2.x). Three engine seams move, with no change in behaviour:
`capture.capture_web` takes `via=` (default `web`), `capture.add_companion_note` takes an optional `check=` (none by
default: the plugin's `journal_add_note` is unchanged, so a hook-side `capture.py` that moves with the pin does not
restart anything new) and `serve.api.filter_entries` is the `/entries` filter as a function. Nothing else in the tick, the hook or serve changes; no file format, setting or state changes.

**Side and restart.** None of the three sides needs anything: the plugin pin, the tick, the gateway and the
`journal-serve` unit can stay where they are (moving them to this commit changes nothing they do). Nothing runs until
a client is configured to start `journal mcp`; that is the owner's choice, per client, not part of a deploy.

1. Merge to `main`. To use it, install the CLI with the extra at the merge commit, keeping the `hermes` extra:
   `uv tool install --force 'journal[hermes,mcp] @ git+https://github.com/beejsbj/journal@<sha>'`. If the CLI is moved
   to `<sha>`, move the plugin pin with it (doctor's `same version as tick` row compares them), or leave both where
   they are and install the extra later.
2. First checks: `journal --config ~/.config/journal/<profile>.json --hermes-home $HERMES_HOME
   doctor` as before. `journal … mcp` with no client waits on stdin (Ctrl-D ends it); without the extra it prints the
   install line and exits 1. Read-only checks go first in the client: `journal_today`, `journal_entries`,
   `journal_entry`. `journal_save`, `journal_add_note` and `journal_start` write to the vault or send to Telegram:
   use them only when the owner means to, and take the vault checksum before and after.
3. A client entry (Claude Code): `claude mcp add --scope user journal -- ~/.local/bin/journal --config
   ~/.config/journal/<profile>.json --hermes-home $HERMES_HOME mcp`. A client on another
   machine cannot start a process on the host over stdio; that needs `ssh <host> journal …` as the command, the owner's call.
4. Rollback: remove the client's `journal` entry (`claude mcp remove journal`), or reinstall the CLI without the extra
   (`uv tool install --force 'journal[hermes] @ …@<previous sha>'`). Nothing else holds state for it.

## PWA — the installable app: manifest, service worker, offline reads, the phone pass (2026-10-20; serve side only; no gateway restart)

**What changes.** The web app becomes a PWA again (the owner, 2026-10-20: the PWA layer and a phone-first UI were wanted
after all; WP11's "no PWA" was a misreading, DECISIONS row 10 is superseded by row 11).
`/manifest.webmanifest` (name Journal, `start_url` and `scope` `/`, standalone, icons, theme colours from `tokens.css`),
`/sw.js` (Workbox precache of the shell; the API rules are `web/src/swCore.ts`) and `/icons/*` are new static files in
the committed build. The browser keeps the app shell and the last answers of Today, Entries, an entry, the Library and
reflections (network first; the kept copy only when the server cannot be reached), shows "offline, as of HH:MM" on such a
view, and says "You're offline; this will not be saved until you're back" on a write (the draft stays in the tab).
**Update available** appears when a new build is installed. Unpair or any 401 empties everything kept. The page itself is
reworked for a phone (44 px targets, safe-area insets, a bar at the foot, the composer above it). One Python change: the
CSP gains `worker-src 'self'; manifest-src 'self'`. No data, file format or state changes; his words are not touched;
nothing is written by the deploy or by reading.

**Side and restart.** Serve side only (`serve/app.py`, `serve/static/**`). `uv tool install --force` of the merge commit,
the plugin pin moved to the same sha (tick side, no restart; only so `same version as tick` stays green), then
`systemctl --user restart journal-serve`. The gateway never restarts, so this rides no window.

**A phone must be on the tailnet** to reach `https://host.tailnet.ts.net:8790` (and so to install, pair or read
anything not already kept); the manifest and the worker name no host or port, so the same build installs from any
address that serves it. A service worker needs a secure origin, which that address is.

0. Merge to `main`, note the merge commit (`<sha>`).
1. Move the plugin pin, install the CLI at the same sha, restart the unit:
   ```sh
   hermes -p <profile> plugins install beejsbj/journal --force --ref <sha>     # confirm the role_hijack caution
   uv tool install --force 'journal[hermes] @ git+https://github.com/beejsbj/journal@<sha>'
   J="journal --config ~/.config/journal/<profile>.json --hermes-home $HERMES_HOME"
   $J serve check                                   # same version as tick; web app build named
   systemctl --user restart journal-serve
   journalctl --user -u journal-serve -n 5 --no-pager
   ```
2. First checks, from the host (reads only; the vault checksum is the receipt, nothing here stores a message):
   ```sh
   sha256sum $JOURNAL_ROOT/*.md | sha256sum        # before
   H='Host: host.tailnet.ts.net'; B=http://127.0.0.1:<port>     # the unit's bind and port
   curl -sS -D- -o /dev/null -H "$H" $B/sw.js | grep -i 'HTTP/\|content-type\|cache-control\|content-security'   # 200, text/javascript, no-cache, CSP with worker-src 'self'
   curl -sS -D- -o /dev/null -H "$H" $B/manifest.webmanifest | grep -i 'HTTP/\|content-type\|cache-control'      # 200, application/manifest+json, no-cache
   curl -sS -H "$H" $B/manifest.webmanifest | jq -c '{start_url,scope,display}'                                   # "/", "/", standalone: no host, no port
   for i in icon-192 icon-512 icon-maskable-512 apple-touch-icon; do curl -sS -o /dev/null -w "$i %{http_code}\n" -H "$H" $B/icons/$i.png; done   # 200 x4
   curl -sS -o /dev/null -w '%{http_code}\n' -H "$H" $B/api/v1/today          # 401 with no cookie: the worker never makes that readable
   sha256sum $JOURNAL_ROOT/*.md | sha256sum        # after: equal
   ```
   Then on the phone (on the tailnet; the pairing link is `$J serve pair`, it prints `https://host.tailnet.ts.net:8790/pair#<code>`):
   open it, pair, browse Today and an entry once; the browser offers *Add to Home Screen* (iOS: Share, Add to Home Screen)
   or *Install app* (Android/desktop Chrome). Open the installed app: full screen, Journal's icon. Turn on airplane mode
   and reopen it: the shell opens, Today and the entry you read show **offline, as of HH:MM**; Settings says it cannot
   be read; the composer on the entry says "You're offline; this will not be saved until you're back" and keeps what you
   type (do not send). Back online, the ribbon goes. **Settings, Unpair** returns to the pairing screen and, in the
   browser's site data (Chrome: DevTools, Application, Cache storage), the `journal-data-v1` cache is gone. A later
   deploy shows **Update available**; Reload applies it.
3. Not verified by the package: the install prompt and the home-screen icon on a real phone (CI installs nothing; it
   drives Chromium headlessly over loopback, which is a secure context like the https address), iOS standalone mode and
   its safe-area behaviour (the insets are in the CSS; there is no iPhone in CI), and whether the proxy in front (Tailscale
   Serve or another) passes `Cache-Control: no-cache` on `sw.js` unchanged (the first check above reads it at the unit; read it again
   through `https://host.tailnet.ts.net:8790/sw.js`).
   On iOS the home-screen app can keep its own cookie jar: if the installed app opens on "Not paired" (the pairing code
   is single-use), run `$J serve pair` again and paste the new link into the app's pairing field.

Rollback: the installed worker does not go away by itself. A server that goes back to a sha without a worker answers
`/sw.js` with 404, and a browser keeps its installed worker when an update check fails (checked in Chromium: the
registration stays, the page stays controlled, and `/` and `/entries` are still answered from the precached shell), so
a phone would run the new front end against the old API with offline caching still on. The way out is a worker that
answers: `deploy/killswitch-sw.js` empties every cache, unregisters itself and reloads the open pages from the network.
Serve it as `/sw.js` beside the previous build:

1. `uv tool install --force` the **previously deployed sha** (and the plugin pin back with `--force --ref <previous
   sha>`). Do not restart yet.
2. Make a web root from that sha's build plus the kill switch (it needs an `index.html`, so the sha must carry a web
   app build; one from before WP11 does not, and could not have installed a worker either):
   ```sh
   D=~/.local/share/journal/killswitch-web; R=<checkout>
   rm -rf $D && mkdir -p $D
   git -C $R archive <previous sha> src/journal/serve/static | tar -x -C $D --strip-components=4
   git -C $R show <this merge sha>:deploy/killswitch-sw.js > $D/sw.js
   ```
3. Put `"assets_dir": "~/.local/share/journal/killswitch-web"` under `serve` in
   `~/.config/journal/<profile>.json`, then `systemctl --user restart journal-serve`.
4. Check: `curl -sS -H "$H" $B/sw.js | head -3` shows the kill-switch comment (not 404), `$B/` is the old app. On the
   phone, open the app once with a network: it may show the new shell for a moment, then reloads into the old app; the
   browser's DevTools, Application, Service workers (Chrome) lists none, and `caches.keys()` is empty. A phone that is
   offline or not opened keeps the new shell until its next visit, then does the same.
5. Leave `assets_dir` in place until every phone has been opened once (an installed home-screen app counts); it is
   harmless to keep. Then remove it: `/sw.js` is a 404 again, and nothing is left to remove.

Fallback only: *Clear site data* for the address in the phone's browser settings, or remove and re-add the home-screen
icon. The `journal-data-v1` cache holds answers the phone already read; the kill switch empties it. No server data is
involved.

## SETTINGS — `journal config`, and the Settings page can save (2026-10-20; serve side + CLI; no gateway restart)

**What changes.** (1) `journal config list|get|set` and `hermes journal config …`: the settings the web app's Settings
page may change, from a shell, with the same allow-list and checks (`settings.apply`, one function for every face).
`set` writes the config file atomically; for a key that Hermes settings own it writes nothing and prints the
`hermes config set plugins.entries.journal.settings.<key> <value>` line (exit 1). (2) `journal serve --print-unit` now
lists the config file's directory in `ReadWritePaths`, so `PATCH /settings` (the Settings page's Save) can write there:
on the host the unit's `ProtectHome=read-only` made every save a `503`. The reasoning, and what it does not protect
against, are in `docs/DECISIONS.md` (SETTINGS): only allow-listed keys are ever written there, and the directory is
added only when it is the config file's own (`~/.config/journal/`; never `~/.config`, the home directory or a
Hermes profile). **The owner decides this one before step 2:** the sandbox is inherited by "Send now", which runs a model
for the opener, and the config file holds command-bearing keys (`telegram.token_command`, `sources.*.command`,
`hermes.command`, …); with the path writable the guard plugin is the only thing between that model and the file
(the guard now also refuses `journal config set` in the companion's terminal). Not re-rendering the unit keeps the
old sandbox; the Settings page then answers `503` to a save. (3) The Settings page shows under each field the
layer it is in, and its "Saved" line names the layer written. No file format or state changes; his words are not touched.

**Side and restart.** Serve side (`serve/**`, the committed web build, `settings.py`) and the CLI. `uv tool install
--force` of the merge commit, **re-render the unit** (it changed), `systemctl --user daemon-reload`, then
`systemctl --user restart journal-serve`. The gateway, the plugin pin and the tick need nothing (the tick never reads
`settings.py`); the plugin's `hermes journal config` and the guard's new refusal of `journal config set` (`guard.py` runs in the gateway's
`pre_tool_call` hook) arrive with the next plugin pin and gateway restart, which ride window B.

1. Merge to `main`. `uv tool install --force` from the merge commit.
2. Re-render and review the unit (the only diff should be the extra path):
   `journal --config ~/.config/journal/<profile>.json --hermes-home $HERMES_HOME serve
   --print-unit > /tmp/journal-serve.service; diff /tmp/journal-serve.service ~/.config/systemd/user/journal-serve.service`;
   expect `ReadWritePaths=… ~/.config/journal` added. Copy it over the installed unit, `systemctl --user
   daemon-reload`, `systemctl --user restart journal-serve`.
3. First checks: `systemctl --user show journal-serve -p ReadWritePaths` lists `~/.config/journal`;
   `journal --config … --hermes-home … config list` shows the values with their layers (Hermes-owned ones marked);
   `config get windows.collapse_hours`. A write that changes nothing in effect proves the path without moving a ritual:
   `config set windows.opener_budget_seconds <the value it has now>` (exit 0, `<profile>.json` rewritten with the same
   content; `diff` it against a copy taken first), then the same from the Settings page in the browser (Save with one
   field retyped to its current value is not sent: change a field and put it back, two saves). A key Hermes owns says so
   and prints the command: `config set <a key listed as hermes> …` exits 1 and changes nothing. `journal serve check`
   and `doctor` as before. No vault write is involved; checksum `$JOURNAL_ROOT/*.md` before and after anyway.
4. Rollback: reinstall the CLI at the previous sha, re-render the unit with it (no config directory in
   `ReadWritePaths`), `daemon-reload`, restart `journal-serve`. The Settings page then answers `503` to a save again;
   `<profile>.json` keeps whatever was saved, and any edit made by hand or by `config set` works as before.

## WEBEDIT — edit and delete his own blocks in the web app (2026-10-20; serve side; no gateway restart)

**What changes.** Two routes, `PATCH` and `DELETE /api/v1/entries/<stem>/blocks/<key>`, and **Edit** / **Delete**
under each block of his in the Entry view (Decision 4 amended: the engine only appends his words, the web app is one
more editor for him). They replace the words of one block, or remove one block (its hidden key line, heading and
body), and carry every other byte of the entry over; they run under the ledger lock, and a block or entry that
changed since he read it is `409 changed` with nothing written. Her blocks are refused; a program phase answer can
be edited but not deleted. No file format, state file or config key changes, and nothing is written until he presses
Save or Delete.

**Side and restart.** Serve side only (`serve/document.py`, `serve/blocks.py`, `serve/writes.py`, the committed web
build under `serve/static`). `uv tool install --force` of the merge commit, then `systemctl --user restart
journal-serve`. The gateway, the plugin pin and the tick need nothing, and none of the hook-side modules changed
(`entries.py` and `capture.py` are untouched), so this rides no restart window.

1. Merge to `main`; `uv tool install --force` from the merge commit; `systemctl --user restart journal-serve`.
2. First checks: `journal … serve check` and `doctor` unchanged (the `serve assets` row names the new build). In the
   browser, on a scratch entry that has **two** blocks (open any entry he does not mind changing and add a block to it
   with the composer; do not make a new `/j` note for this, deleting its only block would leave an empty entry and its
   ledger prompt in the vault): Edit the block you added, Save; the words change and nothing else on the page does;
   then Delete that same block, confirm; it is gone and the entry is as it was. In Obsidian the file shows the same,
   with the heading/time of any other block, her callouts and the front matter untouched. Receipt: the vault checksum
   (`sha256sum $JOURNAL_ROOT/*.md | sha256sum`) before and after is equal once the added
   block is deleted again (and differs by exactly that one file while it stands); on any entry he did not touch it is
   unchanged. Also try an edit that starts with `/x`: the page answers that it is off the record and the file does not
   change.
3. Negative check (the Host header is sent so the 403 is the Origin refusal, not the Host check that comes first):
   `curl -s -o /dev/null -w '%{http_code}\n' -X PATCH -H 'Host: <serve.host>[:<serve.public_port>]' -H 'Origin:
   https://evil.example' -H 'Content-Type: application/json' -d '{"text":"x","mtime":1}'
   http://<serve.bind>:<serve.port>/api/v1/entries/<any stem>/blocks/words:0` answers 403 and writes nothing.
4. Known limits, for him to weigh: it is a plain edit with no history (Obsidian's file history and the vault backups
   are what cover it); the `## Summary` of a completed program session was not rewritten by an edit of its answers
   (changed by SUMMARY-REFRESH, above); deleting a voice block leaves its recording file on
   disk; an unsaved edit of a block that has since changed is shown apart as text, not put back into the box.
5. Rollback: `uv tool install --force` the previous sha and restart `journal-serve`. Entries he changed stay changed
   (the files are his); nothing else depends on this code.

## SERVEPORT — `serve.public_port`: the app on a non-default HTTPS port (2026-10-20; serve side; no gateway restart)

**What changes.** A new optional setting, `serve.public_port`. `journal serve` published at
`https://host.tailnet.ts.net:8790` (where port 443 is taken by something else) answered every browser write with 403,
because the `Origin` check allowed only `https://<serve.host>`; and `journal serve pair` printed a link without the
port. With `serve.public_port` set (and not 443), `auth.site(cfg)` is `https://<serve.host>:<port>`, pairing links use
it, and a write's `Origin` must equal it exactly (no other port, no `http://`, nothing taken from the `Host` header).
Unset: nothing changes. A bad value is named by `journal doctor` and ignored. No file format or state changes; his
words are not touched.

**Side and restart.** Serve side only (`config.py`, `serve/auth.py`). `uv tool install --force` of the merge commit,
then `systemctl --user restart journal-serve`. The gateway, the plugin pin and the tick need nothing.

1. Merge to `main`. Add `"public_port": 8790` under `serve` in `~/.config/journal/<profile>.json` (with
   `"host": "host.tailnet.ts.net"`); a deployment overlay, if any, is not changed.
2. `uv tool install --force` from the merge commit; `systemctl --user restart journal-serve`.
3. First checks: `journal --config ~/.config/journal/<profile>.json --hermes-home $HERMES_HOME
   serve check` and `doctor` show no `serve.public_port` problem; `journal … serve pair` prints
   `https://host.tailnet.ts.net:8790/pair#<code>`; in the browser at that address, pairing and a write (an entry
   save) succeed. Negative check from the host: `curl -s -o /dev/null -w '%{http_code}\n' -X POST -H 'Origin:
   https://host.tailnet.ts.net' -H 'Host: host.tailnet.ts.net:8790' http://<serve.bind>:<serve.port>/api/v1/logout`
   answers 403 (the Origin lacks the port). No vault write is needed for any of this; a pairing code is the only state
   made, and it expires in ten minutes.
4. Rollback: remove `public_port` from `<profile>.json` (writes from the port-less origin work again, port-carrying ones do
   not) or reinstall the CLI at the previous sha and restart `journal-serve`.

## OFFLOC — `journal_set_location` is refused off the record (2026-10-20; hook side; rides the pending B+C restart)

**A privacy rule, the owner's decision of 2026-10-20.** In a `/x` turn (or while a bare `/x` window is open) the
companion's `journal_set_location` is refused like `journal_add_note` and the notebook's writes: the location,
timezone, weather coordinates and `location-history.jsonl` stay as they were, and the refusal tells her to suggest he
says it on the record. Outside `/x` nothing changes. No file format, setting or state changes (`docs/DECISIONS.md`,
"OFFLOC").

**Side and restart.** Hook side only (`tools.py`, which the `pre_tool_call` hook and the tool handlers share; the
skill text), so it reaches the companion at the gateway restart. It rides the pending combined **B+C restart**: deploy the
merge commit that contains it (or a later one) as `<sha>`, for the plugin pin and the CLI alike. No restart of its own;
the tick and the serve unit need nothing beyond the pin and CLI at the same sha.

1. Merge to `main`; use that merge commit (or later) in the B+C runbook.
2. After the gateway restarts, `hermes -p <profile> journal doctor` shows every row ok or as before. There is no live check
   that does not touch a journal setting; the tests cover the refusal (`tests/test_inbound.py`, `ToolCallTest`). Do not
   try it with a real `/x` in the companion's chat just to see it.

Rollback: the previous sha for the pin and the CLI, plus a gateway restart; nothing was written by this change.

## PRIV2 — privacy at the seams: automatic sends honour `/x`, three older edges (2026-10-20; both sides; rides the pending B+C restart)

**A privacy fix, no new behaviour when nobody says `/x`.** A read-only review of the stack (`a2410f1`) found two
automatic program sends that could end a bare-`/x` window and file his next private message as a phase answer: the
tick delivering a phase owed a send after a failed `/next` send, and the settle's send after a reflect phase's model
call. Both now wait while the window is open and go out once it has closed (`docs/DECISIONS.md`, "PRIV2"); `/next`
still sends at once. Also: a window message whose off-record note cannot be recorded is still marked off the record;
a bare `/x` caption on a voice note or audio file whose off-record note cannot be recorded is marked too (the audio is not kept); a busy-path bare `/x` stored with no timestamp makes the chat unreadable (privacy closed) instead of being skipped (defensive: Hermes's schema forbids a NULL time, so none should exist); a
`/x` message merged into a voice note's turn is not written as part of the transcript. No file format, setting or
state changes. Nothing already written is rewritten.

**Side and restart.** Tick and spawn side (`programs.py`: the tick's step and `journal program settle`;
`hermes/transcript.py`'s bounded read, used by the tick's privacy pass and sweep) from the next tick after the pin
moves; hook side (`inbound.py`; the same transcript read in the hook's pass) at the gateway restart. It rides the
pending combined **B+C restart** (`ff9d304` → main): deploy the merge commit that contains it, not `a2410f1`. No
restart of its own; the serve unit needs nothing beyond the CLI at the same sha.

0. Merge to `main`; use that merge commit (or a later one) as `<sha>` in the B+C runbook, for the plugin pin and the
   CLI alike.
1. Vault checksum before (`sha256sum $JOURNAL_ROOT/*.md | sha256sum`), as in every
   section.
2. After the pin moves and the gateway restarts (the B+C steps), the first checks need no `/x` and send nothing:
   ```sh
   hermes -p <profile> journal doctor            # every row ok or as before
   hermes -p <profile> journal program status    # as before; a phase "about to be sent" only if it was owed already
   grep -c 'held back: off the record' $HERMES_HOME/plugin-data/journal/last-tick.json   # 0 unless a /x window met a program send
   sha256sum $JOURNAL_ROOT/*.md | sha256sum                       # after: equal
   ```
   If the tick log (`last-tick.json`, `journal status`) says the chat is unreadable with "no readable time", a row in
   `state.db` has a NULL timestamp: that is the fix failing closed (nothing is captured until it is mended), not a
   regression; report the row id rather than editing the companion's database. This is a defensive guard: Hermes declares
   `messages.timestamp REAL NOT NULL`, so it should never fire.
3. Not verified by the package: the live gateway, a real `/x` window meeting a program send, and Hermes actually
   merging a queued message into a voice turn (the engine side is tested; the platform behaviour is not). CI is the
   suite.

Rollback: `hermes -p <profile> plugins install beejsbj/journal --force --ref <previous sha>`, the CLI at the previous sha,
`hermes gateway restart`. Nothing is written by this change that the previous code cannot read: a phase held back is
an ordinary `send_due`, which the previous code's step sends at the next tick.

## Program rollback safety: `write_summary` never drops a block of his (2026-10-19; tick and spawn side; rides the pending B+C restart)

**Nothing the companion sees changes.** Found by a read-only review of `a2410f1`: if the plugin is rolled back past the programs (`ff9d304`, whose `entries.py` does not stop at `## Summary`) and a reply to an old phase message of a completed session arrives, the old code appends his `###` block below the summary mark. Rolling forward and re-completing that session (`journal program start fix-life --session N`) then ran `entries.write_summary`, which replaced everything from the mark to the next `##` heading, and his block with it. Now `write_summary` replaces only the summary's own text. Any block of his, note or conversation line found under the mark is moved above `## Summary` first, byte for byte: an optional `%% journal phase|interrupt:N %%` line then a `### HH:MM <zone> …` heading, or a `%% journal his|her:<id> %%` line (old code appends conversation lines below the summary when a conversation sits above it). It runs to the annotations or notes heading and is cut only at key lines, so a `### 10:30` or a `## ` line he typed inside one stays in it. Placement: a keyed block goes behind the last block under the same key; a conversation line to the conversation's end; any other block to the end of his words. The summary's own copy is written so it cannot be taken for his blocks (verbatim answers carry `### 10:30`, and `demote_headings` makes it from his `# 7:00`): a `### H:MM` line becomes `#### H:MM` and engine key lines are dropped from it. Without that a second `write_summary` would have moved the summary's tail into his words. One consequence: a summary already written by earlier code that holds a `### H:MM` line would be read once as a block and moved into his words by the next `write_summary`, so before re-completing a session that earlier code completed, look in its `## Summary` for a `### H:MM` line. `tests/test_summary_safety.py` builds these entries (the conversation case with a fixture copied from ff9d304's `append_conversation`) and re-completes them, and checks that the count of his blocks never falls. It runs in the tick's `programs` step and in the spawned `journal program` commands, so it applies from the next tick after the pin moves; the gateway is not involved, and the fix rides the pending B+C restart only because the pin moves once. Limits: his words under the mark end where his words section would end (the last annotations heading, else the last notes heading), so a heading he typed in the engine's shape (`## Today's notes`) stays inside his block as long as a real annotations or notes heading follows. On an entry with neither (annotations off, no notes), a typed `## Someone's annotations` is taken for the real one: the part after it stays below the summary, unread as his and not lost. Also unhandled: when old code creates a fresh `## Conversation` under the summary, that heading ends the region, and its lines stay below the summary. Both are found only by the check before rolling forward and are moved by hand.

Also in this package, DEPLOY.md only: the rollback fold snippets for program and interrupt prompts (WP4 step 5, WP5 step 5) now set `p["annotate"] = False`, and the WP13 rollback names the stray `YYYY-MM-DD-fix-life-N.md` a direct reply to an old phase message lands in after a rollback, and the check before rolling forward.

First check: none live. After the pin moves, `sha256sum $JOURNAL_ROOT/*.md | sha256sum` is equal before and after (no write happens unless a session is re-completed). **Rollback:** move the pin back; no state or format changed. The old `write_summary` has the deletion hazard again, so do not re-complete a session whose entry has blocks under the mark.

## WP13 — cleanup: the shims go, README and DEPLOY rewritten for the end state (2026-10-20; both sides; restart: Window C)

**Nothing the companion sees should change.** Removed: the config's `schedule.<id>` override (and `journal practices migrate-schedule`, with its doctor rows), `questions.py` and its constants, `config.KINDS` and `config.DEFAULT_SCHEDULE`, two dead helpers (`mirror.busy`, `agent.is_failed_turn`). A config without a `schedule` key (WP2's step 1c removes it) reads nothing differently: the four rituals keep their file times (06:00, 23:00, Wed 12:00, Sun 20:00), and the goldens (banks, Telegram HTML, plain openers, entries, folded headers, opener requests, a week of schedule) are byte-equal. The ritual ids (the four commands `/morning` `/evening` `/midweek` `/weekly`, the names `entries.known_ids` recognises, the ones the off-the-record prefix may not take) now come from the packaged practice files. `plugin.yaml`'s `config_schema` drops `schedule` and is exactly the configurable keys. The skill's line on changing times and questions mentions the web app's Library. New: `journal migrate-config` moves a `schedule` block (from an old file, a file in the new shape, or an overlay) into forked practice files in `practices_dir`, written only with `--to` (`--practices-dir` places them); a `schedule` key left in any config layer is ignored and named by a doctor row.

**Restart: window C** (the hook side loads `commands.py`, `entries.py` and the skill at gateway start; the tick side applies from the next tick after the pin moves). The mix is harmless in between: the new tick beside the old gateway sends the same prompts at the same times, and the old hook's ritual commands are the same four. Do not roll the pin forward and leave the gateway for long.

0. Merge to `main`, note the merge commit (`<sha>`); one sha for the plugin pin and the CLI. Window C's slot as above.
1. Before anything, record the vault, and confirm the config has no `schedule` key (read-only):
   ```sh
   sha256sum $JOURNAL_ROOT/*.md | sha256sum                       # before
   grep -c '"schedule"' ~/.config/journal/<profile>.json                                 # 0
   grep -n '^ *schedule:' $HERMES_HOME/config.yaml                       # nothing under plugins.entries.journal.settings
   ```
   If either finds one, it is now ignored (it changed nothing since WP2 unless it differed from the files): `journal --config ~/.config/journal/<profile>.json --hermes-home $HERMES_HOME migrate-config --from ~/.config/journal/<profile>.json` prints what it would fork and writes nothing; `--to <new file>` writes the forks and a config without the key (the owner's yes before replacing `<profile>.json`). With `--hermes-home` it also reads a `schedule` under `plugins.entries.journal.settings` (it wins per entry, as it did) and moves it with the rest, but never edits `config.yaml`: that key stays until `hermes config unset plugins.entries.journal.settings.schedule` (the owner's yes). The forks go to the directory the engine will read (the config's `practices_dir`, a Hermes setting over it); `--practices-dir` elsewhere is written into the new config, or refused when a Hermes setting would override it.
2. Move the pin and check the tick side:
   ```sh
   hermes -p <profile> plugins install beejsbj/journal --force --ref <sha>     # confirm the role_hijack caution (it names library/practices/midweek.md)
   hermes -p <profile> journal doctor                  # every row ok or as before; no row mentions `schedule`; "same version as tick" after the next tick
   hermes -p <profile> journal practices list          # morning daily 06:00, evening daily 23:00, midweek Wed 12:00, weekly Sun 20:00 (plus any practice of his), none "(schedule.… in config …)"
   hermes -p <profile> journal practices migrate-schedule   # exits 2: the action is gone
   ```
3. Install the CLI at the same sha and restart the unit (serve side, no gateway):
   ```sh
   uv tool install --force 'journal[hermes] @ git+https://github.com/beejsbj/journal@<sha>'
   J="journal --config ~/.config/journal/<profile>.json --hermes-home $HERMES_HOME"
   $J serve check && systemctl --user restart journal-serve
   ```
4. Restart the gateway, then check it came back:
   ```sh
   hermes gateway restart
   hermes gateway status
   hermes logs --level WARNING | grep -i journal | tail     # nothing new; no "conflicts with a built-in command"
   ```
5. First checks, in the journal's chat (nothing needs a model): `/start evening` (or any practice) answers and sends; the ritual commands `/morning` and `/weekly` are still in the menu; a `/x` line is not saved. After the next tick: `journal status` shows the same due/next list as before. The first ritual after the restart goes out at its usual time with the usual text.
   ```sh
   grep -E 'journal\.' $HERMES_HOME/logs/errors.log | tail             # empty, or expected warnings only
   sha256sum $JOURNAL_ROOT/*.md | sha256sum                      # after: equal (a kept reply or note is the only change)
   ```
   The window-C line in the plan, "doctor clean, no deprecation rows", is step 2's doctor output: no `schedule` row and nothing marked deprecated.
6. Not verified by the package: the live gateway and a real install (CI is the suite; nothing here touched the companion's profile). `hermes plugins validate` was not run.

Rollback: `hermes -p <profile> plugins install beejsbj/journal --force --ref <previous sha>`, the CLI at the previous sha, `hermes gateway restart`. Nothing is written to state, settings or the vault by this package's deploy, and no file format changed, so the previous code reads everything as before (a fork `migrate-config` wrote stays and is read by both). **If a fix-life run exists** (rolling back past WP4): fold its phase and interrupt prompts first with the snippets in WP4 step 5 and WP5 step 5 (they also set `annotate = False`, so the old code does not annotate an unfinished or abandoned session or message him about it). After the rollback a direct Telegram reply to an old phase message is not matched to its session: the old code writes it to a stray `YYYY-MM-DD-fix-life-N.md`. His words are kept there; moving them back into the session entry is a manual step. Before rolling forward again, check the session entries for blocks under `%% journal:summary %%` (`grep -n -A8 'journal:summary' $JOURNAL_ROOT/*-fix-life-s*.md`, read-only); it is not belt-and-braces: for most blocks the `write_summary` fix above makes it redundant, but it is the only way to find his words that old code left below the summary where the fix does not read them (a fresh `## Conversation` old code created there, or text after a typed annotations heading on an entry with no real one). Those stay below `## Summary`, unread as his by packs and quote checks; move them by hand into his words or the conversation above the summary.

## WP8 — inbound and tools in the core; the adapter thins (2026-10-19; both sides; rides Window B, bundled)

**A refactor with one small addition; nothing visible to the live install should change.** What happens to a message when it arrives (the `/x` check, capture of a reply, a `/j` note, kept audio, a program phase answer and the settle spawn, the program commands `/next` `/skip` `/back` `/break` `/program`), what is written when a turn starts (a voice note's transcript, the off-record mark on the turn), the companion's four tools and the refusal of their writes in an off-the-record turn, and what each slash command does are now the engine's (`inbound.py`, `tools.py`, `commands.py`); `hermes/hooks.py` (447 to 146 lines), `hermes/tools.py` and `hermes/commands.py` translate Hermes' events and register. The gateway loads all of it at start, so this needs the restart. The tick imports the new modules (any tick that reads the chat goes through `journal.hermes`, whose package `__init__` imports `hooks`, `inbound`, `tools` and `commands`) but calls none of them, so it behaves as before; it needs the new sha only so `same version as tick` stays green. Nothing is added to the companion's config; `plugin.yaml` is unchanged.

The one addition: a journal-writing tool now refuses by itself in an off-the-record turn. The `pre_tool_call` hook still turns it away first, with the same words, and `tools.call` checks again before the handler, so a hook that failed open (a bug in the guard) no longer lets a write through. When it fires, the tool answers `{"success": false, "off_record": true, "error": "Off the record: …"}`. Reads (the notebook's list, `journal_program`) are unaffected. Log lines from the moved code now come from the loggers `journal.inbound`, `journal.tools` and `journal.commands` (they were `journal.hermes`), with the same messages: a grep for a journal error should include them. They are not in Hermes' `gateway.log` (it keeps only the `gateway`, `hermes_plugins` and `plugins.platforms` loggers); they land in the profile's `logs/agent.log` (INFO and up) and `logs/errors.log` (WARNING and up), so every log check below names those files.

**Restart: window B, bundled** with WP4 to WP7. Do not move the pin onto WP8 before window B: the gateway keeps the old hooks until it restarts, the tick would run the new modules, and that mix is not what was tested.

0. Merge to `main`, note the merge commit (`<sha>`); the pin below is that commit, one sha for the plugin pin and the CLI.
1. Before the restart, record the vault (the receipt for the checks below):
   ```sh
   sha256sum $JOURNAL_ROOT/*.md | sha256sum                 # before
   ```
2. Move the pin and restart (the same two commands as window B):
   ```sh
   hermes -p <profile> plugins install beejsbj/journal --force --ref <sha>     # confirm the role_hijack caution
   hermes -p <profile> journal doctor                                          # all rows ok; "hook state" ok
   hermes gateway restart
   ```
3. First checks, in the journal's chat (nothing here needs a model to be reachable):
   - `/program`: the list answers in the chat (it is read-only; this is the command now run from `inbound`, not the gateway).
   - `/x` followed by a short test line: the companion answers without the command and nothing of it is saved. The vault checksum is the receipt: run the `sha256sum` line again, it equals the one before. In `$HERMES_HOME/logs/errors.log` there is no `journal.inbound … failed` line.
   - The next reply to a journal prompt, or a `/j` note you are happy to keep, is saved verbatim with the `[Journal reply …]` / `[Journal note …]` note in front of it, as before. Voice notes: the next one is written once transcribed (as in window A; the first live one is the check).
   - Ask her to remember something in an `/x` message: she tries `journal_add_note` or the notebook and is told it is off the record; nothing appears in the entry or her notebook.
   - `journal_program status` from her still works in the same turn.
   ```sh
   grep -E 'journal\.(inbound|tools|commands|hermes)' $HERMES_HOME/logs/errors.log | tail     # empty, or expected warnings only ("refused in an off-the-record turn")
   sha256sum $JOURNAL_ROOT/*.md | sha256sum                 # after the /x test: equal to before (a kept reply or note adds a file or a block; that is the only expected change)
   ```
4. Not verified by the package: the live gateway (Hermes passes the tool handler its keyword arguments by signature, so whether `turn_id` reaches a handler is not known here; the refusal does not depend on it: with no turn id a marked session is refused, and the privacy pass over the chat is the second signal, as before).

Rollback: move the pin back with `--force --ref <previous sha>` and restart the gateway; nothing is written to state or the vault by this package's deploy, and `hook-state.json` has the same shape in both. The tick may stay on either side of the rollback.

## WP12 — the web app writes: composer, voice, editors, run screen, settings (2026-10-19; no gateway restart; serve unit restart)

**Serve side only.** Python changed in three small ways, all read-side or header: the CSP's `media-src` gains `blob:` (so the composer can play back a clip he just recorded before sending it), an entry's mood and people written as block lists (Obsidian's Properties editor) are read (`entries.front_matter_lists`), and `GET /entries/<stem>` carries `writable` (false for his own page that is only named like an entry). Nothing is written by any of them. The package is the web app's second half (`web/`, built into `src/journal/serve/static`): the routes were all served since WP9, WP10 and the WP10 follow-up, and `docs/API.md` gains the `writable` field and the CSP line. The app now has the composer on an entry (text and voice), mood/people/star, Reply and Send now on Today, program cards with Continue and Pause, the Library's Edit/Fork/Reset/Turn on-off/New practice and Start, the question-set editor (form and raw), the run screen (`/runs/<id>`: the open phase, an answer box, Next/Skip/Back/Pause/Resume/Abandon), settings writes, and the "+" sheet. No gateway restart and no change in behaviour of the tick, `capture` or the hook: `entries.py` (which the hook loads) gains two functions, `front_matter_lists` and `read_head`, and `front_matter_of` returns what it did; a gateway still running its old copy is correct until window B or C. It rides with or after the WP11 section below (same `uv tool install` and unit restart); it needs the same CLI at the same commit as the tick (`journal serve check`: same version).

- **Writes are real.** From this build, a browser paired to the unit can add blocks and notes to the vault (his words, append-only, `/x` refused by the server), set an entry's front matter (`PATCH …/meta`), write practice files in `practices_dir` (outside the vault), change settings the allow-list names, and spawn `journal send` / `journal program …` (as `/morning` and the chat commands do). `serve.allow_write: false` turns every one of these into 405 if the app must be read-only again, with no code change.
- **Browser storage.** An unsent draft is kept in `sessionStorage` for the tab and removed on send; nothing else besides the session cookie.
- **Where it was built.** CI only (`.github/workflows/web.yml`): type check, vitest (155 tests), build, the committed `static/` is the build's artifact, and the Playwright smoke (now with `web/e2e/writes.spec.ts`: a practice made on the phone viewport, edited, a note, a block, a refused `/x`, mood/star, against a throwaway journal; it sends nothing and touches no config).

0. Merge to `main`, note the merge commit (`<sha>`). Deploy as the WP11 section does (the plugin pin is moved only if WP11's step 0 says so; nothing here needs the gateway).
1. Install the CLI at the commit and restart the unit:
   ```sh
   uv tool install --force 'journal[hermes] @ git+https://github.com/beejsbj/journal@<sha>'
   J="journal --config ~/.config/journal/<profile>.json --hermes-home $HERMES_HOME"
   $J serve check                                   # ✓ same version as tick; ✓ web app build named
   systemctl --user restart journal-serve
   journalctl --user -u journal-serve -n 5 --no-pager
   ```
2. First checks, in the browser (pair with `$J serve pair` if the cookie is gone) and from the shell. The vault checksum is the receipt for everything except the two steps marked "writes":
   ```sh
   sha256sum $JOURNAL_ROOT/*.md | sha256sum        # before
   ```
   - **Library → New practice** (writes `practices_dir`, not the vault): title "zz check", **untick "Send it" (it is then saved with `enabled: false`, so no tick can send it to his chat)**, one question; "Looks good." appears, Create lands on its page. `ls $HERMES_HOME/plugin-data/journal/practices/zz-check.md` exists, `$J practices validate` is clean, and the next `$J doctor` lists it. Edit it (Raw Markdown shows the same file), Save; change the file in Obsidian or a shell and Save from the app again: **Changed elsewhere** with Reload and Overwrite, nothing overwritten. Then **Delete** (asks once): the file is gone.
   - **Library → a built-in → Turn off**: the app forks it (`practices_dir/<id>.md`, `enabled: false`); **Reset to built-in** removes the fork. Do this on a practice that is not due within the hour, and confirm `$J doctor` afterwards.
   - **Settings**: a field owned by Hermes (check `$J status` for which) is shown locked with the `hermes config set …` line; change one the file owns (for instance the quiet minutes if they live in `<profile>.json`) and put it back. Do not change location here as a check (it looks the place up and rewrites the timezone).
   - **Today**: the program card appears when a run is in progress; **Pause** then **Resume** (a spawned `journal program pause|resume`: the card says "Pausing…" and clears when `runs-changed` arrives; `manual-trigger.log` shows the spawn). Do not press **Send now** as a check; use it when you want a prompt sent.
   - `sha256sum $JOURNAL_ROOT/*.md | sha256sum` after: equal.
   - **Writes to the vault, by design (his to do, not a probe):** a quick note from the "+" sheet becomes `YYYY-MM-DD-j.md`; the composer on an entry appends a block; a voice note uploads, is kept beside the entry as `<stem>.webm` (or `.m4a` from Safari) and is transcribed by the next tick. **Unverified by the package:** that the tick's transcriber reads `.webm`/`.m4a` (it handles what Telegram keeps, usually `.ogg`); after the first voice note, read the entry after the tick: a transcript, or the "transcript pending" badge (the recording is kept either way; `$J doctor` names a stuck transcription). A voice note added to an old entry whose prompt the ledger has pruned is kept but not transcribed (`capture.add_web_block`: there is no capture for the tick to find); the entry then says "not transcribed". Also unverified: recording on the phone itself (it needs the https site and the microphone permission; headless CI uses a fake).
3. Not verified by the package: the page on a real phone over Tailscale, the memory of the unit with the editor open (`systemctl --user show journal-serve -p MemoryPeak`; the writes are short, the uploads are held in memory up to 25 MB per request and the limit is 768M), and the run screen against a live run (CI has a fixture run, not a live Telegram-sent phase).

Rollback: `uv tool install --force` the **previously deployed sha** and restart the unit (the app goes back to reading). Files the app wrote stay: practice files in `practices_dir` (remove with `$J practices` or by hand; a fork reverts to its built-in when its file is deleted), blocks and notes in the vault (his words, append-only: not rolled back), settings in the config file (put back by hand), and unclaimed uploads under `<state_dir>/uploads` expire after an hour. No gateway change to undo.

## WP11 — the web app, read views (2026-10-19; no gateway restart; serve unit restart)

**Serve side only.** The package now carries the built web app (`src/journal/serve/static`, source in `web/`): the unit's `/` stops being the placeholder page and serves Today, Entries, Entry, Library, Insights, Settings and the pairing page. It is a responsive web app for a browser on a phone or a desktop, **not a PWA** (the owner, 2026-10-19): no service worker, no offline cache, no install prompt, nothing stored in the browser but the session cookie. These views only read; the editors and the composer are the next package. No gateway restart: most of the change is `serve/*` and `status.py`'s doctor row, which the hook never loads. Two files the hook does load also changed, both additively: `today.py` (`collect(..., stems=False)`: the default is the old behaviour, so the today block and its goldens are byte-identical; only `GET /today` asks for stems) and `audio.py` (a new `media_type` function; `extension_for` and `keep` are untouched). A gateway that keeps running its old copy of either is correct, and nothing in WP11 depends on its picking the new one up; it does at the next restart (window B or C). The tick behaves as before. The tick needs the new code only so `same version as tick` stays green.

- **API additions the app needs** (all additive): `/today` carries `week` (seven days, answered/missed/partial/none, from the ledger), each practice row and the reflection carry their real entry `stem` (the route asks `today.collect(stems=True)`; the block and its goldens are untouched), entry documents carry `voices` (`{his, hers}`, the names for labels), `prompt.phases` (`[{number, title}]`, a program session's) and, on his blocks that answer a session-10 interrupt, `interrupt: N`, entry summaries carry `flags.audio` (any embed `/media` serves as audio, whatever its extension), `/entries` and `/days` take `audio=1`, and `GET /library` lists programs and situational definitions beside the practices.
- **Doctor.** `serve assets` now names the build (`built from web/ <hash>`). In a checkout it warns when `web/` has changed since the build.
- **Nothing is written** by this package's deploy or by the views; the vault is only read (check 3 takes the checksum).
- **Where it was built.** Node never runs on the host. CI (`.github/workflows/web.yml`) runs `npm ci`, the type check, vitest and the build on every push that touches `web/`, uploads the build as the `static` artifact, and fails when the committed `static/` differs from it. The browser smoke (Playwright) runs there too.

0. Merge to `main`, note the merge commit (`<sha>`). **Deploy with or after restart window B**: the merge commit also carries WP4, WP5, WP6 and the WP10 follow-up (and their review fixes). Moving the plugin pin (step 1) activates WP4's `programs` tick step and WP5's interrupt sends, and step 2 makes `POST /runs` spawn `journal program start` (the WP10 follow-up section below says to wait for or ride window B for that). Do the WP4, WP5, WP6 and WP10 follow-up steps with this one when they are still undeployed; if window B is done, any time.
1. Move the plugin pin (tick side, no restart; the tick's behaviour does not change):
   ```sh
   hermes -p <profile> plugins install beejsbj/journal --force --ref <sha>     # confirm the role_hijack caution
   hermes -p <profile> journal doctor                    # "serve assets": ok, "web app in … (built from web/ <12 hex>)"
   ```
2. Install the CLI at the same commit and restart the unit:
   ```sh
   uv tool install --force 'journal[hermes] @ git+https://github.com/beejsbj/journal@<sha>'
   J="journal --config ~/.config/journal/<profile>.json --hermes-home $HERMES_HOME"
   $J serve check                                   # ✓ same version as tick; ✓ web app build named
   systemctl --user restart journal-serve
   journalctl --user -u journal-serve -n 5 --no-pager      # "journal serve: listening on 127.0.0.1:<port>"
   ```
3. First checks, reads only (the vault checksum is the receipt):
   ```sh
   sha256sum $JOURNAL_ROOT/*.md | sha256sum        # before
   H='Host: journal.example.com'; B=http://127.0.0.1:<port>
   curl -sS -D- -o /dev/null -H "$H" $B/ | head -12           # 200 text/html, Cache-Control: no-cache, the CSP header
   curl -sS -H "$H" $B/ | grep -o 'src="/assets/[^"]*"'         # a hashed script; curl -I it: 200 text/javascript
   curl -sS -o /dev/null -w '%{http_code}\n' -H "$H" $B/sw.js                    # 404: there is no service worker
   curl -sS -o /dev/null -w '%{http_code}\n' -H "$H" $B/manifest.webmanifest     # 404
   $J serve pair                                    # a link: open it in the phone's browser (or a desktop's)
   sha256sum $JOURNAL_ROOT/*.md | sha256sum        # after: equal
   ```
   In the browser: the pairing link lands on **Today** (date, week strip, the day's practices with their state, what he wrote today); **Entries** lists by week and day and searches his words; open the evening entry: his blocks plain with time and source in the margin, the recording plays, her replies are one folded line each with her name and time, the **Analysis** tab shows her annotations and the reflection; **Library** shows each practice's questions and file; **Insights** steps through weeks; **Settings** shows `Paired as journal.example.com`, the server and tick versions (a warning if they differ), each setting's origin, doctor. **Unpair** returns to the pairing screen; `$J serve pair` again re-pairs. Turning the network off makes a view say it cannot reach the server with a Retry button (and shows no stale copy).
4. Not verified by the package: the page on a real phone over Tailscale (the layout is tested at 390 px in CI, headless), and the unit's memory with the app open (`systemctl --user show journal-serve -p MemoryPeak`; the app adds static files and the same reads as before).

Rollback: `uv tool install --force` the **previously deployed sha** and restart the unit (`/` is the placeholder page again if that sha predates the web app; the API additions above go away with it). Move the plugin pin back with `--force --ref <previous sha>` if it was moved. Note that the merge commit also holds WP4, WP5, WP6 and the WP10 follow-up: rolling back to the WP10 commit would undo those too, which is only right if none of them was live before. No data is involved.

## WP7 — ports and the Hermes transcript/companion adapter (2026-10-19; both sides; rides Window B, bundled)

**A refactor; nothing visible to the live install should change.** Every reader of the chat (the privacy pass, the conversation
writer, the busy-path sweep, the outage catch-up, the context pack, the quote verifier, annotation alignment, the
mirror's turn check) now reads Hermes' `state.db` through one adapter, `hermes/transcript.py` (read-only, as before),
and every model step asks the companion through `hermes/companion.py` (`hermes chat --oneshot`, as before). Both sides
load it: the gateway (the hook's `/x` window check and the tool guard's taint go through the privacy pass) and the tick.
The rows are read as before (`tests/golden/hermes_rows.json`, generated from the old code), the state files keep their
shape (`conversations.json` and `sweep.json` still name the database as `db`; the cursors and watermarks are
untouched), and the reader suites pass twice, over the database and over a fake transcript. Nothing is added to
the companion's config: the new `adapter` key defaults to `auto`. New: `journal doctor --deep` has a `transcript sample` row.
A row whose time cannot be read (Hermes writes a REAL, so none should exist) makes the chat unreadable: the readers log
`sessions database unavailable (ChatUnreadable …)` and retry, and the privacy pass closes, as before the ports.
Before merge, a read-only probe over a copy of a live `state.db` (the copy and the probe's state under a
private temporary directory, deleted after) compared main's readers with this branch's on every chat row: every
per-row decision (the writer's, the sweep's, the catch-up's, the pack's, annotation alignment's) and the privacy pass
(compaction copies included) were identical, and the vault checksum was the same before and after.

**Restart: window B, bundled** with WP4 to WP6 (and WP8 when it is ready): one sha for the plugin pin and the CLI, one
gateway restart. Do not move the pin onto WP7 before window B: until the restart the gateway keeps the old readers
while the tick runs the new ones (both read the database the same way, but that mix is not what was tested).

1. Before the install (old code), record what the readers see, writing nothing. The backfill without `--write` is a
   dry run (no lock, no file, no state), and it stops where the live writer started (`conversations.json` `start_id`),
   so a `--since` before that start gives the same rows before and after the install: its output (counts, and first
   lines of his words as previews, so keep it in a private directory) must not change.
   ```sh
   sha256sum $JOURNAL_ROOT/*.md | sha256sum                 # vault, before
   J="journal --config ~/.config/journal/<profile>.json --hermes-home $HERMES_HOME"
   D=$(mktemp -d)                                                                          # 0700
   $J backfill-conversations --since 2026-10-10 > $D/before.txt                            # a dry run
   ```
2. Install with window B's steps (plugin pin and CLI at the same commit), restart the gateway, then:
   ```sh
   $J backfill-conversations --since 2026-10-10 > $D/after.txt && diff $D/before.txt $D/after.txt && rm -r $D   # no diff
   hermes -p <profile> journal doctor --deep     # "transcript sample: ok newest 50 row(s): … N his words or saved replies …"
   ```
   The sample row counts kinds, never words. A `warn` saying none of his rows read as his words means the adapter
   does not recognise what Hermes stores: roll back.
3. After the first two ticks (read-only): `last-tick.json`'s log has no `conversations skipped`, `sweep skipped`,
   `privacy pass could not run` or `outage catch-up skipped` line; the cursors move:
   `python3 -c "import json;s=json.load(open('$S/conversations.json'));print(s['db'], s['cursor']['last_id'], s['privacy']['at'])"`
   and the same for `sweep.json` (`last_id`) with `S=$HERMES_HOME/plugin-data/journal`.
4. First live checks, as he chats (his to do; nothing to send for it): a conversation is saved into its entry or note
   as before; an `/x` message, and her answer to it, stay out of every file; a reply sent while she is answering is
   swept into its entry; `journal status` shows no new recent errors.
5. Vault receipt: the checksum from step 1 again after the install (the install writes nothing there; only his own
   messages, as he sends them, add to it afterwards).

**Rollback:** move the pin back with `--force --ref <previous sha>` (plugin and CLI) and restart the gateway (window
B's restart; if WP7 alone must go, once more). WP7 writes no new state and changes no state format, so the older code
reads `conversations.json`, `sweep.json` and the ledger as they are. No data to undo.

## WP6 — the companion's surface for programs (2026-10-19; gateway side; rides Window B with WP4 and WP5)

**Gateway side only; nothing changes for the tick or the spawns.** After the gateway restarts on this code: on the turn a program phase answer is captured, the companion's context carries a *program block* (at most 3,000 characters: her stance for the session, the phase as it was sent, his own words in the session so far) in the place of a context pack, and never the today block; she has a fourth tool, `journal_program` (`status`, `propose <id>`: both reads, both allowed in an off-record turn); and the skill gains a *Programs* section listing the enabled programs. Nothing is added to the companion's config. The tick, `capture`, the entries and the run state are untouched; the new code is `programview.py`, `turncontext.py`, `hookstate.py` (a `programs` section in `hook-state.json`), `hermes/hooks.py`, `hermes/tools.py`, `hermes/__init__.py` (registration, the skill token), `plugin.yaml` (`provides_tools`).

**Restart: window B** (WP4 to WP8, one gateway restart). Before that restart, WP4's gateway-side code is not live either, so no phase answer reaches the hook and there is nothing for this package to do: **it goes in with WP4 and WP5 and needs no restart of its own.** Moved alone onto a gateway on the old code it does nothing until the restart (the tool is registered and the skill rendered when the plugin loads).

1. Install, with WP4's steps (plugin pin and CLI at the same commit); then, before the restart:
   ```sh
   hermes -p <profile> journal doctor                     # no new warning; "hook state … 0 pack(s) and 0 program block(s) queued"
   ```
2. After window B's restart (read-only checks; do not run `hermes plugins validate`):
   ```sh
   S=$HERMES_HOME/plugin-data/journal
   grep -c journal_program $HERMES_HOME/plugins/journal/plugin.yaml          # 1
   ls $S/skill/journal-system/SKILL.md && grep -n "^## Programs" -A6 $S/skill/journal-system/SKILL.md | head   # the three built-in programs, rendered with her name
   ```
3. First live use, when he answers a program phase (his to do, or the first time he starts `/start fix-life` or `/start prep`): her reply comes after the settle begins; it should respond to what he wrote and not ask the next phase's questions. What proves the block was injected: `python3 -c "import json;print(json.load(open('$S/hook-state.json')).get('programs'))"` is `{}` again after the turn (it was queued and taken), and `journal doctor`'s hook-state row says `0 program block(s) queued`. A pointer that stays queued past 15 minutes means the LLM hook did not run for that message (expired and dropped on the next access). **Unverified until live:** the block on a *voice* phase answer (the transcript must be in it: the voice capture and the block both depend on `pre_llm_call` running after Hermes has put the transcript in the message).
4. Ask her in the chat which programs there are: she may call `journal_program status` (a tool call, no state change). `propose` returns the line and starts nothing; a run exists only after he sends `/start`. In an off-record turn (`/x what programs are there?`) both still work.
5. Vault receipt as for any deploy: `sha256sum $JOURNAL_ROOT/*.md | sha256sum` before and after the install; the install writes nothing there, and a phase answer adds only his own block (as since WP4).

**Rollback:** move the pin back with `--force --ref <previous sha>` and restart the gateway (the restart is window B's; if WP6 alone must go, restart once). The older code ignores the `programs` key in `hook-state.json` (it is one more section it does not read); the skill and tool registration return to the previous set. No data to undo.

## WP10 review fixes — library gate, collapse bound, retries, YAML, `at` (2026-10-20; serve unit restart; the gateway picks up the capture bound at window B)

**Serve side, plus two engine functions.** `serve/writes.py`, `library.py` (id gate, `collapse_hours` bound), `entries.py` (`flow_list`, `parse_flow_list`), `capture.py` (`window` clamp, `add_web_block(key_of=…)`). Restart the unit: `systemctl --user restart journal-serve`. **No gateway restart of its own**: the hook's capture loads `capture.window`, whose only change is the clamp for an absurd `collapse_hours` (none is expected: the built-ins record none and `PUT /library` is what could have written one); it takes effect at the next gateway restart (window B or C), and nothing depends on it. The tick applies the library bound from its next run (a user file with `collapse_hours` over 8760 is then a doctor problem and the practice is left out, like any file that does not parse).

First checks: `hermes -p <profile> journal doctor` shows no new warning (a user practice file with `collapse_hours` above 8760, or one named like a program, would show here; neither exists unless made by hand: `ls $HERMES_HOME/plugin-data/journal/practices 2>/dev/null`). Then in the web app: save a practice under a program's id (`fix-life`): 409. Vault receipt as for any deploy (no change expected). **Rollback:** previous sha, restart the unit. The `block` field the capture records is ignored by older code.

## WP10 Codex review fixes — queued starts and resumes, orphaned runs (2026-10-19; serve unit restart; no gateway restart of its own)

**Serve side and CLI** (`serve/{api,writes}.py`, `cli.py`, `programs.py`). Deploys with the WP10 follow-up section below it (same steps, same first checks): the unit spawns `journal program start <id> --new` and `resume <run> --paused`, flags the CLI at the same commit understands (an older CLI would reject them: install the CLI and restart the unit together). `programs.abandon` also stops loading the program definition; the gateway hook's `/abandon` picks that up when the gateway next restarts (window B or C), nothing depends on it.

- First check: `curl -s -H "Authorization: Bearer $T" $B/runs/<run>` still lists `keyboard`; for a run whose program file is gone it is `["abandon"]`.
- Rollback: move the pin and the CLI back and restart the unit.

## WP5 second review — interrupts and the quote check (2026-10-19; tick side, plus one hook-side rule; rides Window B)

**Tick and spawn side, plus `capture.py` (hook side).** From the next tick after the pin moves: a typed day and wake time beside a voice answer still waiting for its transcript is read on `/next`; an interrupt lost to a `/x` or to a privacy pass that cannot run (both still hold it: fail closed) raises one tick alert (`held back … until its grace window passed and is missed`); a situational run that expires cancels its pending interrupts and says so (tried again on the next ticks if Telegram is down); a protocol day more than 60 days ahead is refused (asked again; `/next` goes on without); the confirmation and `journal program status` show the year or date when it is not this week; the opener's quote check no longer accepts her synthesis, reflections or the engine's questions as his words. **Hook side:** an interrupt no longer takes an open program phase out of "the next typed message" (`capture.superseded`, `capture.match`); the gateway loads `capture.py`, so this part arrives with window B's restart (the sweep applies it from the next tick). Nothing to configure.

First checks: `tail $S/last-tick.json` after a tick (no `interrupts:` alert); `journal program status` on a run with interrupts shows the date. Rollback: move the pin back; `held_back` and `note` on an item are keys older code ignores.

## WP5 follow-up — review points left open after the merge (2026-10-19; tick and spawn side, no gateway restart)

**Tick and spawn side only** (`programs.py`, `tick.py`). Nothing the dispatch hook or the gateway loads changes, so no restart of its own. From the next tick after the pin moves: the tick sends session 10's interrupts in their own step ahead of the practices (a ritual due in the same tick goes out last and stays the newest message); one run's crash in the `programs` step is logged (`step failed`) and alerted by run id without holding back the interrupts or a pending synthesis (an interrupt timed by a settle in that same tick still goes out in it); an answer from before a `/back` is not re-read when a newer audio answer is still waiting for its transcript (the settle asks again until it lands); `journal program abandon` with no run named refuses when an active run and a completed run with interrupts to come both exist, and names both.

First checks: `tail $S/last-tick.json` after a tick (no `step failed`); `journal program abandon` while both exist answers "Two runs could be meant". Rollback: move the pin back; no state to undo.

## The opener's failure reason in the tick log (2026-10-19; no restart)

**Tick side only** (`agent.failure_reason`, `tick._opener`). Applies from the next tick after the pin moves; nothing in the hooks, serve or the companion's settings changes, so no gateway or unit restart.

- When the opener's model call fails, the tick log gains a line before the `sent …` line, e.g. `2026-10-19-evening: opener: plain:agent-failed: exit 1: 429 rate_limit_error`. It holds only a whitelist (exit status, HTTP 4xx/5xx, provider `*_error` class, or "timed out"), never stderr prose, and is capped at 200 characters. It is read only from provider-owned shapes (`Error code: 429`, `HTTP 502`, a structured `"type": "…_error"`), so a number or `…_error` word in echoed prose matches nothing; a signal death reads `killed by signal 9`. The line contains "failed", so the tick also files it in `errors.jsonl` (last 200 records), where `journal status` shows it under "Recent errors"; that is intended. A failure with nothing recognisable adds no line. `opener_source` stays `plain:agent-failed`: the ledger, `sent.jsonl` and the `sent …` line are unchanged.
- `journal status` already prints the last tick's final five log lines, so the reason shows there when the last tick sent a prompt, and "Recent errors" keeps older ones. `doctor` is unchanged.

First check: after a tick whose opener failed, `tail $S/last-tick.json` (or `journal status`, "Last run") shows the line. Rollback: move the pin back; no state to undo.

## WP10 follow-up — program blocks and transitions from the web (2026-10-19; no gateway restart; serve unit restart)

**Serve side, plus two engine functions.** WP10 merged before WP4, so its program routes were stubs; this wires them (`docs/DECISIONS.md`, "WP10 follow-up"). `src/journal/serve/{api,writes}.py` change, `capture.add_web_block` gains `phase_prompt`/`PhaseClosed`, and `programs.is_expired` is new. No gateway restart: the hook calls none of the changed behaviour (`capture.py` is loaded by the gateway but only `add_web_block`, which the hook never calls, changes). The tick's behaviour is unchanged. Restart the unit to load it: `systemctl --user restart journal-serve`. **Deploy this together with or after restart window B (WP4's hook live).** Before that, `GET /runs` is `[]` and nothing breaks, but `POST /runs` spawns `journal program start` from the unit's own `src` (this commit's WP4 code) and sends phase 1 to Telegram, whose replies a pre-WP4 gateway hook would not recognise as a program's. The CLI install is step 1 below, at the same commit.

- **What the token can now do.** Start a program and move a run on (`POST /runs`, `POST /runs/<id>/next|skip|back|pause|resume|abandon|insights`), and answer an open phase (`POST /entries/<stem>/blocks` with `phase`). Each is what `/start`, `/next` … and a Telegram reply already do; `serve.allow_write: false` closes all of them (405).
- **Spawned processes in the unit.** A phase block spawns `journal program settle <run>`, which sleeps `programs.settle_minutes` (2) in the unit's cgroup before moving the run on; restarting the unit inside that wait kills it and the tick's `programs` step moves the run on a few minutes later (nothing is lost). A transition (`start`, `next` …) waits for the tick's lock and sends the next phase to Telegram from the unit's sandbox, as *Send now* does (same `telegram.token_command`; `ProtectHome=read-only` with `ReadWritePaths` covering root, state_dir and practices_dir is enough).

1. Move the plugin pin (tick side, no restart; nothing the tick runs changes), then install the CLI at the same commit and restart the unit:
   ```sh
   hermes -p <profile> plugins install beejsbj/journal --force --ref <sha>
   uv tool install --force 'journal[hermes] @ git+https://github.com/beejsbj/journal@<sha>'
   J="journal --config ~/.config/journal/<profile>.json --hermes-home $HERMES_HOME"
   $J serve check && systemctl --user restart journal-serve
   ```
2. First checks that write nothing (the vault checksum is the receipt):
   ```sh
   sha256sum $JOURNAL_ROOT/*.md | sha256sum        # before
   B=http://127.0.0.1:<port>/api/v1; read -rs T   # paste the serve token (from the secret store serve.token_command reads)
   curl -s -H "Authorization: Bearer $T" $B/runs                                  # [] until a run exists, then the Run objects
   curl -s -H "Authorization: Bearer $T" $B/runs/<run>                            # "phase": {...,"open": true}, "keyboard": [...]
   sha256sum $JOURNAL_ROOT/*.md | sha256sum        # after: the same
   ```
3. First live use (a real run, the owner's yes): with a run in progress, answer its open phase with one `POST /entries/<session stem>/blocks {"text": …, "phase": N}`: the reply carries `"advance": "settle"`, the entry gains the block under `%% journal phase:N %%`, `tail $S/manual-trigger.log` shows the settle, and two minutes later the next phase arrives in Telegram. `{"phase": N+1}` while N is open is `409 phase-not-open`. Unverified until live: that the spawned settle can reach the sessions database (it reads the privacy pass read-only before moving on); if it cannot, the block is still saved and the tick's step moves the run on.
4. **Rollback:** move the pin and the CLI back and restart the unit. Blocks already written stay in the entries (his words are never removed); a run keeps its state in `runs/` as the chat would leave it.

## WP9 review fixes — the document, search, headers and events (2026-10-19; no gateway restart; serve unit restart)

**Serve side only** (`src/journal/serve/`). Nothing in the tick, the hooks or the companion's settings changes, so no gateway restart; restart the unit to load it: `systemctl --user restart journal-serve`.

- A program session's summary shown by the web app is now only the engine's marked `## Summary`; a `## Summary` line he typed stays in his block. Search (`/entries?q=`) and the Insights word counts read his words only: `q=` with the time zone's abbreviation from the block headings finds nothing, and a week's `words` drops by the headings and markers it used to count (expect smaller numbers than before the restart).
- `/%61pi/...` gets `Cache-Control: no-store`; Insights no longer counts program phase and interrupt prompts as missed practices; `/events` sends `library-changed` for edits under a user program's directory.

First checks: `curl -si -H "Authorization: Bearer $T" http://127.0.0.1:<port>/%61pi/v1/today | grep -i cache-control` gives `no-store`. Rollback: move the pin back and restart the unit.

## WP4 follow-up — review points left open after the merge (2026-10-19; tick side only, no gateway restart)

**Tick and spawn side only.** `programs.py` and `programdefs.py` change; nothing the dispatch hook or the gateway loads is touched, so this needs no restart of its own (and gains nothing from window B). From the next tick after the pin moves: a phase the run comes to again going forward (after a `/back`) settles only on an answer given after it came back; a session summary written while an audio answer was still being transcribed is written again once the transcript lands (log: `session 1 summary filled in from the transcript`); a failing phase send no longer stops the `programs` step for the other runs (its alert is raised after them); a program file whose `sessions:` names a path (`../x`) is rejected by `journal doctor` instead of read.

1. First checks: `hermes -p <profile> journal doctor` (a user program with a path in `sessions:` now shows as a problem); `tail $S/last-tick.json`.
2. **Rollback:** move the pin back. No state is migrated; `reopened_at` and `summary_audio_pending` are keys older code ignores.

## WP5 — session 10's interrupts and the insights synthesis (2026-10-19; no gateway restart of its own; rides Window B with WP4)

**Tick and spawn side.** From the next tick after the pin moves: the `programs` step now also sends session 10's interrupts when they are due (`programs.send_interrupts`, inside `windows.grace_hours`), the settle on a `schedule` phase waits for a day and wake time (`YYYY-MM-DD HH:MM`, or `HH:MM` for tomorrow) before moving the run on, and `journal program insights` can append a validated synthesis (`programs.synthesis`, off; fix-life's file says `insights: compile`). Nothing is added to the companion's config, and nothing is sent until a run reaches session 10 phase 3 and he answers it.

**What needs the gateway.** Sending is tick-side; his *reply* to an interrupt is captured by the dispatch hook (`capture`, `entries`, `programs.reply_note` as the gateway loaded them). A gateway on WP4's code writes such a reply as a plain block with no `interrupt` key and the generic reply note. WP4 is not live yet, so this costs no extra restart: **WP5 goes in with WP4 in Window B** (one install, one restart), and no run should reach session 10 before that restart. (`capture.match`'s rule that an interrupt sent after a ritual takes the ritual out of "the first message after a prompt" is hook-side too, so it also arrives with that restart; the sweep applies it from the next tick.) If WP5's pin were moved alone, nothing breaks (no run exists), it just does nothing.

What does not change: the four rituals, `schedule.due` for practices (the golden `schedule.due.txt` is byte-equal), phase sends, summaries, annotations, the existing insights compile for a program with no interrupts. The compiled insights document gains a section of his answers to a session's interrupts only for a session that has them.

1. Install (tick side), with WP4's steps 1 to 2; then:
   ```sh
   hermes -p <profile> journal doctor                  # no new warning
   hermes -p <profile> journal program status          # unchanged until a run exists; later: "Interrupts to come (8): 09:00, 11:00, …"
   ```
2. First live use, when he gets to session 10: phase 3 ends with "Confirm the protocol day and your wake time as YYYY-MM-DD HH:MM, for example … I'll time the 8 interrupts from it. /skip goes on without them." Answering `2026-11-02 07:00` (or `07:00`) is followed by the note "Interrupts set for …" listing the eight times, then phase 4. Any other answer is kept in the entry and gets one "I couldn't find a day and wake time" reply, with nothing scheduled. Read-only checks:
   ```sh
   S=$HERMES_HOME/plugin-data/journal
   python3 - <<'PY'
   import glob, json, os
   for f in glob.glob(os.environ["HERMES_HOME"] + "/plugin-data/journal/runs/fix-life/*.json"):
       for i in json.load(open(f)).get("scheduled", []):
           print(i["id"], i["offset"], i["local"], i["status"])
   PY
   tail -5 $S/manual-trigger.log        # "… 8 interrupts timed from 2026-11-02 07:00"
   ```
3. On the protocol day, each tick at or after a stated time sends one message ("Session 10/10 · Interrupt 3/8" and the question, no keyboard); `tail $S/last-tick.json` shows `sent <run>-s10-i3`. Reply to the message (Telegram's reply gesture): the entry `$JOURNAL_ROOT/<date>-fix-life-s10.md` gets his words under `%% journal interrupt:3 %%` with the question folded under `## Prompt` as `Interrupt 3/8 (+6h) · …`. A message that is not a reply is ordinary chat. An interrupt more than `windows.grace_hours` (2) late is recorded `missed` and never sent late; after a tick outage only the newest due interrupt goes. **Unverified until live:** that Hermes keeps the reply pointer for a voice note answering an interrupt (the same path as a phase answer).
4. Optional, the owner's yes: the synthesis. `hermes -p <profile> config set plugins.entries.journal.settings.programs '{"synthesis": true}'` (merge with `keyboard`/`bridges` if set; read by the next spawn, no restart), then `/start fix-life insights`: the document gains `## <companion>'s synthesis` only when every quote in it is in the compiled text; otherwise the log says `synthesis rejected (…)` and the compiled file stays as it was. A model outage defers it (retried every 30 minutes, an alert once after 6 hours, given up after 7 days).
5. **Rollback:** move the pin back (`hermes -p <profile> plugins install beejsbj/journal --force --ref <previous sha>`; with WP4 in the same window the gateway restart rolls both). Scheduled items stay in the run file (inert for WP4's code, which ignores `scheduled`); to stop interrupts at once without rolling back, `hermes -p <profile> journal program abandon` (a completed run keeps its status; its pending interrupts become `cancelled`; an active one is ended) or set `"status": "cancelled"` on the items by hand. Interrupt prompts already in the ledger carry `program` and no `phase`; WP4's fold step handles them as phases without a number, so fold them first if you roll back after a send:
   ```sh
   PYTHONPATH=$HERMES_HOME/plugins/journal/src python3 -c '
   import os; from datetime import datetime, timezone; from pathlib import Path; from journal import ledger
   with ledger.locked(Path(os.environ["HERMES_HOME"], "plugin-data/journal/ledger.json")) as d:
       for p in d["prompts"]:
           if p.get("interrupt"):
               p.setdefault("collapsed_at", datetime.now(timezone.utc).isoformat()); p["needs_edit"] = False; p["annotate"] = False; print(p["id"])'
   ```
   Replies already written stay in the entries (his words are never removed); under WP4's code the `%% journal interrupt:N %%` key line reads as an ordinary line of his words.

## PRIV — privacy coverage: proven by rows read, not by a stamp (2026-10-19; tick side now; hook side rides window B)

**A privacy fix, no new behaviour.** After earlier PR 15 a read-only verifier saw four ways `/x` words could still reach an entry, the pack, the quote checker, annotation alignment or the outage catch-up: a saved pass stamped over rows it never read (the sessions database missing or pointed elsewhere for more than 48 hours, another chat id, the writer enabled after a first pass, a clock that ran ahead), a backfill with no lookback before `--since`, readers that look back further than 48 hours (pack 7 days, verifier all, catch-up 72 hours, alignment of an old prompt), and compaction copies of a private row, in the same session or (new time, rotation child session) elsewhere in the chat (`docs/DECISIONS.md`, "PRIV"). Now a reader holding rows older than what the view recomputed re-takes it from its oldest row (`privacy.covering`), `update` saves no pass while the sessions database is missing, a backfill recomputes from 48 hours before `--since`, and a copy is private when its original is. Annotation alignment for a prompt recorded in another chat returns no text.

- **Tick side** (from the next tick after the pin moves): the writer, sweep, catch-up, pack, verifier and annotation alignment. In the ordinary case only the catch-up (72 hours) and a pack or verifier (7 days, all rows) read further than before: one recompute over those rows, from the read-only sessions database, per call. After an outage or a closed pass the writer recomputes once from its cursor. New tick-log line while the database is missing: `privacy pass not saved: no sessions database at …` (the sweep and writer already say `skipped`).
- **Hook side**: the gateway calls `privacy.view` for its end-state checks (a bare-`/x` window still open, the tool guard's tainted turn), which look only at the last 48 hours and do not change in what they answer; it loads the new `privacy.py` (a copy-aware pass, a cache keyed by start) at the next gateway restart, so it rides **restart window B** (WP4–WP8). The pack it injects on a reply turn is the file the tick saved, so that part is fixed from the next tick. Nothing depends on the restart.
- **State**: `conversations.json` `privacy.private` gains the ids of compaction copies of private rows. Nothing is migrated; an older build reads the file as before.

0. Merge to `main`, note the merge commit (`<sha>`). Any time.
1. Move the plugin pin (tick side, no restart), and keep the CLI on the same commit so `same version as tick` stays green:
   ```sh
   hermes -p <profile> plugins install beejsbj/journal --force --ref <sha>     # confirm the role_hijack caution
   uv tool install --force 'journal[hermes] @ git+https://github.com/beejsbj/journal@<sha>'
   hermes -p <profile> journal doctor
   ```
2. First checks after the next tick (read-only):
   ```sh
   sha256sum $JOURNAL_ROOT/*.md | sha256sum        # before the tick
   python3 -c "import json;d=json.load(open('$HERMES_HOME/plugin-data/journal/last-tick.json'));print(d['at'],d['exit']);print('\n'.join(d['lines']))"
   #   exit 0; no "privacy pass could not run", no "conversations skipped", no "privacy pass not saved"
   python3 -c "import json;p=json.load(open('$HERMES_HOME/plugin-data/journal/conversations.json'))['privacy'];print(p['at'],len(p['private']))"
   #   at = that tick's time; the count is the same as before or a little higher (copies of private rows)
   ```
   The checksum after a tick changes only where the writer wrote conversation, as on any tick; nothing already written is rewritten. A tick should take about as long as before (`journalctl`/cron timing); a catch-up or pack call is one extra read of a few days of one chat.
3. At window B's restart, nothing to do for this package beyond the window's own checks.

Rollback: move the plugin pin back with `--force --ref <previous sha>` (and the CLI with `uv tool install --force` at the same commit). The extra private ids in `conversations.json` are harmless to the older build (more private, never less); no data to undo.

## WP10 — serve writes (2026-10-19; no gateway restart; serve unit restart)

**Serve side only, in effect.** The web API can now add to the journal: notes and blocks of his words, entry meta, recordings, practice definitions (PUT, fork, DELETE), a spawned *Send now*, and a few settings (`docs/API.md` §2, README "Web app"). No gateway restart: the hook side changes in files the gateway has already loaded (`capture.py`'s `match` now calls `open_prompts`, `ledger.py`, `entries.py`, `library.py` and `config.py` gain functions; nothing the hook calls behaves differently), so it picks them up at its next restart (window B) and nothing depends on that. The tick behaves as before; it needs the new code only so `same version as tick` stays green.

- **What the token now means.** Until now `serve.token` could only read. From this commit the same token (and a paired browser) can add to the journal (`$JOURNAL_ROOT`) through the API, so keep exposure as it was (loopback until the operator's steps 2–9 in DEPLOY-FRONTEND.md). To keep a deployment read-only: `"allow_write": false` in the `serve` block of `<profile>.json`, restart the unit; every write route then answers `405 read-only` and doctor names any value that is not a boolean.
- **Where his words go.** `POST /entries` and `…/blocks` write through `entries.record_reply` under the ledger lock, appending only; there is no route that edits or removes a block, and text that starts with `/x` (or has a `%% journal … %%` line) is refused with nothing written. An audio recording is kept beside its entry as `YYYY-MM-DD-<kind>.<ext>`, transcribed by the tick like a Telegram audio file.
- **Sends run inside the serve unit.** *Send now* spawns `python -m journal --config … --hermes-home … send <id>` from the server; the child lives in the unit's sandbox and cgroup (`MemoryMax=768M`, `TasksMax=64`, `ProtectHome=read-only` with `ReadWritePaths` = root, state_dir, practices_dir). It sends to Telegram (the token comes from `telegram.token_command`, the same helper the serve token uses), writes the ledger and entry as usual, and mirrors into the chat session only if Hermes can be imported there (the spawn passes `HERMES_HOME`, as the plugin's own does); otherwise the mirror is queued and the next tick delivers it. Unverified until the first send (check 5).
- **Settings need the config file to be writable.** `PATCH /settings` writes `<profile>.json` (the file layer) through a temporary file beside it. `~/.config/journal` is not in the unit's `ReadWritePaths`, so such a write answers `503 cannot write: PermissionError` until the operator chooses to allow it (a drop-in: `systemctl --user edit journal-serve`, `[Service]` `ReadWritePaths=%h/.config/journal`). A key that lives in Hermes settings answers `409` with the `hermes config set …` line whatever the unit allows. Leaving this closed is fine: settings stay a CLI matter.
- **Program routes are stubs** (`POST /runs…` answer 404; a block on a program entry answers `409 phase-not-open`) until the WP10 follow-up (its section above) wires the engine into serve; nothing to do at deploy.

0. Merge to `main`, note the merge commit (`<sha>`). **Deploy with or after restart window B**: the merge commit also carries WP4, WP5, WP6 and the WP10 follow-up (and their review fixes). Moving the plugin pin (step 1) activates WP4's `programs` tick step and WP5's interrupt sends, and step 2 makes `POST /runs` spawn `journal program start` (the WP10 follow-up section above says to wait for or ride window B for that). Do the WP4, WP5, WP6 and WP10 follow-up steps with this one when they are still undeployed; if window B is done, any time.
1. Move the plugin pin (tick side, no restart; the tick's behaviour does not change):
   ```sh
   hermes -p <profile> plugins install beejsbj/journal --force --ref <sha>     # confirm the role_hijack caution
   hermes -p <profile> journal doctor
   ```
2. Install the CLI at the same commit and restart the unit:
   ```sh
   uv tool install --force 'journal[hermes] @ git+https://github.com/beejsbj/journal@<sha>'
   J="journal --config ~/.config/journal/<profile>.json --hermes-home $HERMES_HOME"
   $J serve check                                   # ✓ same version as tick: 2.0.0 <code> (the same code on both sides)
   systemctl --user restart journal-serve
   journalctl --user -u journal-serve -n 5 --no-pager      # "journal serve: listening on 127.0.0.1:<port>"
   ```
3. First checks that write nothing (on the host; the vault checksum is the receipt):
   ```sh
   sha256sum $JOURNAL_ROOT/*.md | sha256sum        # before
   H='Host: journal.example.com'; O='Origin: https://journal.example.com'
   read -rs T                                       # paste the serve token (from the secret store serve.token_command reads)
   B=http://127.0.0.1:<port>/api/v1
   # a write without the token or without the Origin is refused
   curl -sS -o /dev/null -w '%{http_code}\n' -H "$H" -H "$O" -X POST $B/entries -d '{"text":"x"}'                       # 401
   curl -sS -o /dev/null -w '%{http_code}\n' -H "$H" -H "Authorization: Bearer $T" -X POST $B/entries -d '{"text":"x"}'  # 403
   # off the record is refused and nothing is written
   curl -sS -H "$H" -H "$O" -H "Authorization: Bearer $T" -X POST $B/entries -d '{"text":"/x this is not saved"}'          # 422 off-the-record
   # a dry run of a definition: the canonical text, nothing written
   curl -sS -H "$H" -H "$O" -H "Authorization: Bearer $T" -X PUT "$B/library/gratitude?validate=1" \
        -d '{"raw":"---\nkind: practice\nid: gratitude\ntitle: Gratitude\nschedule.time: 21:30\n---\n- Three things today?\n"}' | head -c 400
   unset T
   sha256sum $JOURNAL_ROOT/*.md | sha256sum        # after: equal (compare outside a ritual hour)
   ```
4. One deliberate write, from the phone once paired (`$J serve pair`) or with the token: a note in the app, or `POST /entries {"text": "web write check"}` creates `YYYY-MM-DD-j[-N].md` in `$JOURNAL_ROOT` with `capture: /j`, source `journal` (or the configured `entries.frontmatter_source`), a `### HH:MM <zone> · text` block, and a ledger capture with `via: web`. It is a real entry of his: the only way to remove it is by hand in Obsidian (the API has no delete for words). Then reload the entry in the app: it shows as his, no companion text.
5. *Send now* (a real send to Telegram; use a practice he wants right now): `POST /library/<id>/send` answers `202`; within a minute `tail plugin-data/journal/manual-trigger.log` shows `sent <id>` and the Telegram message arrives. Then read how the opener was made: `python3 -c "import json;p=json.load(open('plugin-data/journal/ledger.json'))['prompts'][-1];print(p['id'],p.get('opener_source'))"` should print the companion's id (as the scheduled sends do), not a value starting `plain` (the model call runs inside the unit's read-only home and `MemoryMax`; if it does not work there, say so and spawn the send outside the unit rather than loosening it). If the log shows a token or permission error, the spawn's sandbox is the cause: say so rather than loosening the unit. Also `$J doctor` now warns when `windows.grace_hours` is under 15 minutes. `peak` memory: `systemctl --user show journal-serve -p MemoryPeak` (Decision 7: a send inside the unit is the new peak to watch).

Rollback: `"allow_write": false` in `<profile>.json` and `systemctl --user restart journal-serve` makes the server read-only again at once; or `uv tool install --force` the WP9 commit and restart the unit, and move the plugin pin back with `--force --ref <previous sha>` (nothing the tick runs changed). No data migration is involved: entries and blocks written through the API are his words and stay; practice files written by PUT are ordinary files in `practices_dir` (delete them, or `DELETE` from the app, to go back to the built-in); `state_dir/uploads/` holds only unclaimed recordings under an hour old and can be emptied by hand.

## WP9 — `journal serve`, read-only, auth, unit template (2026-10-19; no gateway restart; new user unit)

**No gateway restart.** Nothing on the hook side changes behaviour: the gateway keeps the code it loaded until its next restart (window B), and the new config key and doctor rows are harmless to it. Three things are new, each on its own side:

- **Tick side, a privacy fix** (from the next tick after the pin moves): `privacy.view` now also recomputes back to the last persisted pass (or, before any pass is saved, the conversation cursor) minus 48 hours, so after a tick outage or a closed pass longer than 48 hours a `/x` exchange from the gap stays out of the entry (`docs/DECISIONS.md`, "WP3 → WP9"). It changes what the writer does only in that case, plus one longer read of the sessions database after such a gap. The hook calls the same function; it picks the fix up at its next restart, and nothing depends on that.
- **Tick side** (from the next tick after the pin moves): `last-tick.json` gains `version` and `code` (a hash of the package's source), so doctor can say whether the CLI install runs the same code as the tick. `journal doctor` gains four rows: `serve token`, `serve bind`, `serve assets`, `same version as tick`.
- **Serve side** (new): `journal serve` from the `uv tool` install, as a systemd **user** unit, on **loopback** (quarantine, DEPLOY-FRONTEND.md step 1). Read-only: GET routes, pairing, `/events`; it reads entry files, the ledger, the library, reflections and the tick's caches, never `state.db`, never a model, never `tick.lock`. Until the web app ships (WP11), `/` is a placeholder page that can pair a browser. It writes only its own files in the state dir: `serve-auth.json` (cookie key), `serve-pairings.json` (hashed one-time codes), and `serve-token` only when no token source is configured.
- **Network exposure is not in this section:** binding the Tailscale IP, the firewall, the proxy's router, the public deny, local DNS and the TLS checks are the operator's task (DEPLOY-FRONTEND.md steps 2–9), with the owner's gate approvals (Decision 8). A deployment's overlay therefore carries `serve.port`, `host`, `token_command`, `token_key` and `trusted_proxies`, and no `serve.bind`.

What does not change: sends, captures, folds, the hook, goldens. Three `test_conversations` cases that started failing on `main` on 2026-10-17 (fixed dates aged out of the privacy pass's 48-hour window) now pin that pass's clock; test-only.

0. Merge to `main`, note the merge commit (`<sha>`). Any time: no ritual is touched.
1. Move the plugin pin (tick side, no restart):
   ```sh
   hermes -p <profile> plugins install beejsbj/journal --force --ref <sha>     # confirm the role_hijack caution
   hermes -p <profile> journal doctor
   #   ✓ serve token: not created yet: `journal serve` writes …/serve-token at its first start   (until step 2)
   #   ✓ serve bind: 127.0.0.1:8790                                                              (<port> after step 2)
   #   ✓ serve assets: the web app is not built into this install yet: the placeholder page (pairing only) is served
   #   ! same version as tick: the last tick (…) predates version stamps; the next tick records its own   → ✓ after the next tick
   ```
2. **The owner's yes (live journal config and the secret store):** give serve its settings and a token.
   - Add `JOURNAL_SERVE_TOKEN=<random>` to the secret store that `serve.token_command` reads (by hand; e.g. `python3 -c 'import secrets; print(secrets.token_urlsafe(32))'`). Never paste it into a chat. Keep it scoped to this profile: the token reads the journal now and, from WP10, appends to it, so no other agent's environment should carry it. `token_command` prints `KEY=value` lines (or the token alone on its first line); `token_key` names the line to read.
   - Add a `serve` block to `~/.config/journal/<profile>.json` (back the file up first, `cp <profile>.json <profile>.json.pre-wp9`):
     ```json
     "serve": {"port": <port>, "host": "journal.example.com",
               "token_command": ["<secret-helper>", "<scope>"], "token_key": "JOURNAL_SERVE_TOKEN",
               "trusted_proxies": ["10.0.0.0/24"]}
     ```
   - `hermes -p <profile> journal doctor` → `✓ serve token: obtained from token_command`, `✓ serve bind: 127.0.0.1:<port>`.
3. Install the CLI at the same commit, with the `hermes` extra so it reads the plugin settings like the tick does (without PyYAML it reads only `<profile>.json` and `serve check` warns):
   ```sh
   uv tool install --force 'journal[hermes] @ git+https://github.com/beejsbj/journal@<sha>'
   J="journal --config ~/.config/journal/<profile>.json --hermes-home $HERMES_HOME"
   $J serve check
   #   ✓ serve token: obtained from token_command · ✓ serve bind: 127.0.0.1:<port> · ✓ serve assets: … placeholder …
   #   ✓ same version as tick: 2.0.0 <code>      (✗ means the two installs are on different commits: re-pin one)
   #   ✓ serve port: 127.0.0.1:<port> is free
   ```
4. The unit (user systemd; no root):
   ```sh
   $J serve --print-unit                      # review: ExecStart as above, ReadWritePaths = $JOURNAL_ROOT and plugin-data/journal,
                                              # RequiresMountsFor=/srv, ProtectHome=read-only, MemoryHigh=384M, MemoryMax=768M
   $J serve --print-unit > ~/.config/systemd/user/journal-serve.service
   systemctl --user daemon-reload && systemctl --user enable --now journal-serve
   systemctl --user status journal-serve --no-pager | head -5           # active (running)
   journalctl --user -u journal-serve -n 5 --no-pager                    # "journal serve: listening on 127.0.0.1:<port>"
   ```
   If it exits with `no serve token (token_command …)`, the secret helper could not run inside the sandbox (`ProtectHome=read-only`: a helper that writes a cache or lock under the home directory fails); read the journal line, and either let the helper's path be writable in a drop-in or set the token another way. Unverified until this first start.
5. First checks (read-only, on the host):
   ```sh
   curl -sS http://127.0.0.1:<port>/healthz                                            # {"ok": true, "version": "2.0.0", "code": "…", "tick_last_at": "…"}
   curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:<port>/api/v1/today       # 403: Host is not journal.example.com
   curl -sS -o /dev/null -w '%{http_code}\n' -H 'Host: journal.example.com' http://127.0.0.1:<port>/api/v1/today   # 401: no token
   read -rs T                                                                          # paste the serve token (from the secret store serve.token_command reads)
   curl -sS -H 'Host: journal.example.com' -H "Authorization: Bearer $T" http://127.0.0.1:<port>/api/v1/today | head -c 300; echo
   curl -sS -H 'Host: journal.example.com' -H "Authorization: Bearer $T" 'http://127.0.0.1:<port>/api/v1/entries?limit=3' | head -c 400; echo
   systemctl --user show journal-serve -p MemoryCurrent -p MemoryPeak                  # a stdlib server: tens of MB; note the peak for Decision 7
   unset T
   ```
   The vault is only read; for the receipt, `sha256sum $JOURNAL_ROOT/*.md | sha256sum` before step 4 and after step 5 are equal (the tick may legitimately write an entry in between: compare outside a ritual hour).
6. Exposure (later, the operator's ops task, DEPLOY-FRONTEND.md steps 2–9): it ends by adding `"bind": "100.64.0.1"` to `<profile>.json`'s `serve` block and `systemctl --user restart journal-serve`; then `$J serve pair` prints `https://journal.example.com/pair#…` to open on the phone once (10 minutes, single use).

Rollback: `systemctl --user disable --now journal-serve`, remove `~/.config/systemd/user/journal-serve.service`, `systemctl --user daemon-reload`; `uv tool uninstall journal` (or install the previous commit). The `serve` block in `<profile>.json` and the `serve-*` files in `plugin-data/journal` are inert without the server (remove them, or put `<profile>.json.pre-wp9` back). The plugin pin can go back with `--force --ref <previous sha>`; the only tick-side change is the two new fields in `last-tick.json`.

## WP4 — programs (fix-life), situational sessions, the program commands (2026-10-19; restart: Window B)

**Needs a gateway restart; it rides Window B** (with WP5–WP8 when they are ready). Both sides change, but **nothing happens for the companion until the owner sends `/start fix-life`**: no run exists before that, and with no run every new path is idle. Tick side, from the next tick after the install: a `programs` step (between `conversations` and `fold`) that moves runs on; fold, annotate, prune and the day reflection learn to treat program phase prompts (none exist yet); the today block's `program:` line once a run exists; `journal program …` and `hermes journal program …`; doctor's new `programs` row. Hook side, at the restart: `/start <program>` (fix-life, `prep`, `debrief`) and five new plugin commands, `/next`, `/skip`, `/back`, `/break` (pause) and `/program` (status); the dispatch hook gives a phase answer the program reply note and spawns `journal program settle <run>` (the next phase follows two quiet minutes later, or at once on `/next`). Hermes owns `/pause` and `/status`, so neither is registered or routed (no "conflicts with a built-in command" warning). `plugin.yaml` declares the `programs` setting; nothing has to be set for the companion (`programs.settle_minutes: 2`, `reflect: true`, `bridges: false`, `keyboard: true`).

Behaviour worth knowing: the open phase takes every message only while it is the newest prompt in the chat (a ritual sent meanwhile is answered as before), `/break` closes it (a direct reply still lands; `/start` carries on from it), and the settle waits while a bare `/x` window is open (a phase send would end the window), so the next phase can arrive 45 quiet minutes after a private stretch instead of two.

What does not change: the four rituals, their sends, entries, folds and records (golden tests byte-equal); `/start <practice>`; capture of practice replies. `/start nope` now also lists `/start fix-life`, `/start prep`, `/start debrief`. A hand-made predecessor skill in the profile, if one exists (its own flow and state), is not touched and can be retired when wanted; until then both exist, and only `/start fix-life` writes into the journal.

Between the install and the restart: the new tick beside the old hook is harmless (no runs). Do **not** start a run before the restart: the old hook routes `/start fix-life` to the old resolver (it answers "There's no practice called “fix-life”"), and a run started from the CLI would have its answers captured by the old rules (first message only, no settle spawn, the tick moving it on up to five minutes late).

0. Merge to `main`, note the merge commit (`<sha>`). Window B's slot: at least an hour from a ritual (06:00, Wed 12:00, Sun 20:00, 23:00); the restart takes every agent on the gateway offline for about a minute.
1. Install and check (tick side):
   ```sh
   hermes -p <profile> plugins install beejsbj/journal --force --ref <sha>     # confirm the role_hijack caution
   hermes -p <profile> journal doctor            # ✓ programs: fix-life (10 sessions), conversation-prep (situational), post-session-debrief (situational); no new warning
   hermes -p <profile> journal program status    # "No program is running." then "Programs: /start fix-life (Fix your entire life in 1 day), /start prep (…), /start debrief (…)."
   ```
1b. **Required, the owner or the operator (live Hermes config):** let the companion's chat run the program commands. In `$HERMES_HOME/config.yaml`, under `platforms.telegram.extra`, add `next`, `skip`, `back`, `break` and `program` to `user_allowed_commands` (single spellings: no hyphen, so no underscore form), keeping every name already there; copy the file first (`cp config.yaml config.yaml.pre-wp4`) and compare `grep -n user_allowed_commands` before and after. Hermes reads it at start, so before the restart below. Without it every button tap gets Hermes' "admin only". `journal-start` from WP2's step 1b already covers `/start fix-life`. Leave `command_menu` alone (no menu entry is needed: the phase message carries the commands as buttons). Rollback of this step: remove the five names (or put the copy back).
2. Restart the gateway, then check it came back:
   ```sh
   hermes gateway restart
   hermes gateway status
   hermes logs --level WARNING | grep -i journal | tail     # nothing new; in particular no "conflicts with a built-in command"
   ```
3. First run, when the owner chooses to start (his `/start fix-life`; never sent on his behalf). It answers "🧠 Fix your entire life in 1 day is on its way." and the intro with session 1, phase 1 arrives with four buttons (`/next` `/skip` `/back` `/break`). Read-only checks:
   ```sh
   S=$HERMES_HOME/plugin-data/journal
   ls $S/runs/fix-life/                                                    # one <date>-<hex>.json
   hermes -p <profile> journal program status                                   # "• Fix your entire life in 1 day — session 1/10 (The Anti-Vision Architect), phase 1/6 open since HH:MM"
   tail -5 $S/manual-trigger.log                                           # "… started fix-life", "sent <run>-s01-p1 (opener: program …)"
   ```
   **Busy path (review finding, the check that needs her mid-reply):** Hermes runs plugin commands only while she is idle, so the dispatch hook runs `/next` `/skip` `/back` `/break` `/program` itself and drops the event. After his first answer, while she is still replying (the "typing" state), tap `/next` (or ask him to): `manual-trigger.log` shows `… → next phase (next)` and the companion's reply does not mention a `/next`; `hermes logs | grep "pre_gateway_dispatch skip"` shows `journal /next`. A tap that arrives after the run moved on logs `already moved on from S:P:V (… ignored)` and changes nothing. Idle taps are run by the hook too (the registered commands are the fallback), so step 3's first live check holds either way.
   **The first live phase send verifies the keyboard routes `/skip`** (PRACTICES.md §4.3): Hermes runs plugin commands from keyboard taps like typed ones, but this is the first real check. When he next taps a button (or ask him to tap `/skip` on a phase he doesn't mind skipping), `manual-trigger.log` shows `session 1 phase N → next phase (skip)` and the next phase arrives. If a tap does nothing (or reaches the companion as text), the fallback is typed commands with the keyboard dropped: `hermes -p <profile> config set plugins.entries.journal.settings.programs '{"keyboard": false}'` (a live-config edit, the owner's yes; read by the next spawn, no restart), and say so to the owner.
4. After his first answer: the entry `$JOURNAL_ROOT/<date>-fix-life-s01.md` (front matter `program: fix-life`, `session: 1`, `run: …`; his words under `%% journal phase:1 %%`), the next phase about two minutes after his last message (the spawned settle; the tick's step catches a lost spawn within five minutes), the folded header `… · Phase 1/6 · answered ✓` on the previous message. The companion's turn on a phase answer carries the program reply note (`[Journal reply — program reply: …]`); the program block arrives with WP6. Session 10's daytime interrupts are listed in its phase 3 but not scheduled until WP5.
5. **Rollback:** `hermes -p <profile> plugins install beejsbj/journal --force --ref <previous sha>`, then `hermes gateway restart` (step 1b's five names can stay: with no plugin command behind them they are inert). With no run started, that is all. If a run exists, end it first with the new code so no phase keeps taking messages (`hermes -p <profile> journal program abandon`), and fold its phase prompts so the old fold step never meets them:
   ```sh
   PYTHONPATH=$HERMES_HOME/plugins/journal/src python3 -c '
   import os; from datetime import datetime, timezone; from pathlib import Path; from journal import ledger
   with ledger.locked(Path(os.environ["HERMES_HOME"], "plugin-data/journal/ledger.json")) as d:
       for p in d["prompts"]:
           if p.get("program"):
               p.setdefault("collapsed_at", datetime.now(timezone.utc).isoformat()); p["needs_edit"] = False; p["phase_open"] = False; p["annotate"] = False; print(p["id"])'
   ```
   The session entries already written stay in the journal and are recognised by their front matter; `runs/` is inert for the old code and can stay. The old code would annotate a session entry phase by phase if a later reply landed in it. `annotate = False` in the snippet is what stops it annotating an unfinished or abandoned session (and messaging him in Telegram about it); a prompt without the key is annotated by the old code.

## WP3 — the today block and the turn context budget (2026-10-17; restart: alone, or Window B)

**Needs a gateway restart,** and it can go in its own restart or ride the next window (B, with WP4–WP8 when they are ready); it does not have to wait. Both sides change. Tick side, from the next tick after the install: a new `cache` step (between `mirror` and `prune`) writes `today-cache.json` in the state dir from `sources.tududi` and `sources.weather` at most every 30 minutes, and `tick.send_prompt` stamps `last_send` in `hook-state.json`. Hook side, at the restart: `on_llm_call` goes through `turncontext.build`, which adds the today block (at most 1,200 characters) to the first turn of a thread: his first message of the day, one 45+ minutes after his previous message, or the first after a prompt was sent. Never on a turn that carries a context pack (a journal reply, `/j`), never from the chat transcript, and the hook calls no source. No setting is added to the companion's config and a deployment overlay needs no change: the `today.*` defaults apply (`today.enabled: false` in the plugin settings is the off switch, read at the next turn without a restart once the new hook is loaded). `plugin.yaml` declares the new `today` setting.

What does not change: the notebook still goes into every turn; the pack still goes into the reply turn; golden tests (Telegram HTML, entry files, ledger records) are byte-equal. One existing assertion changed on purpose: a chat turn now writes `hook-state.json` (the `last_his` stamp), so `hook-state.json` is rewritten on every message of his, not only when it holds a record.

Between the install and the restart the new tick runs beside the old hook: harmless. The cache fills and the stamp is written; the old hook ignores both. So the order is free; restart when convenient.

1. Merge to `main`, note the merge commit (`<sha>`). Any quiet hour will do (no ritual within the hour; the restart takes every agent on the gateway offline for about a minute).
2. Install and check (tick side):
   ```sh
   hermes -p <profile> plugins install beejsbj/journal --force --ref <sha>     # confirm the role_hijack caution
   hermes -p <profile> journal doctor                                          # no new warning; sources row shows tududi and weather ok
   hermes -p <profile> journal today                                           # the block as it would read now (practices and entries only until the first cache)
   ```
   After the next tick (within five minutes):
   ```sh
   S=$HERMES_HOME/plugin-data/journal
   jq '{at, tasks: (.tasks | {date, count}), weather: .weather.text}' $S/today-cache.json     # tasks and weather lines present
   hermes -p <profile> journal today | tail -3                                  # "tasks due today" and "weather" lines
   hermes -p <profile> journal status | grep 'Today block'                      # Today block: 0 blocks today · cache N min old
   ```
   If the cache lacks `weather`: `coordinates` is unset in `<profile>.json` (the location tool writes it); if it lacks `tasks`: tududi returned nothing due, or the source failed (the tick log says `today cache: tasks unavailable (...)`).
3. Restart the gateway, then check it came back:
   ```sh
   hermes gateway restart
   hermes gateway status
   hermes logs --level WARNING | grep -i journal | tail     # nothing new
   ```
4. Verify in use (read-only). Message the companion after 45 quiet minutes (or the first message of the day), then read that user row's `api_content` in her sessions database: it should open with the notebook (if any items are open) and then `## Journal: today — …`. A second message within 45 minutes must not carry it; a reply to a prompt must carry the pack and no today block.
   ```sh
   python3 - <<'PY'
   import os, sqlite3
   con = sqlite3.connect("file:" + os.environ["HERMES_HOME"] + "/state.db?mode=ro", uri=True)
   for ts, api in con.execute("select m.timestamp, m.api_content from messages m join sessions s on s.id = m.session_id "
                           "where m.role='user' and s.source='telegram' order by m.id desc limit 3"):
       print(ts, "## Journal: today" in (api or ""))
   PY
   hermes -p <profile> journal status | grep 'Today block'                      # Today block: 1 block today, last HH:MM
   ```
   (`api_content` is the text the model saw, context included; `content` is what he typed.)
5. Rollback: set `today.enabled: false` in the plugin settings (`hermes -p <profile> config set plugins.entries.journal.settings.today '{"enabled": false}'`, a live-config edit, the owner's yes) to stop the block with the new code in place, or reinstall the previous pin and restart the gateway. The cache file and the stamps are inert and can stay; `rm $S/today-cache.json` is harmless.

## WP2 — the engine keyed by Practice, `/start <id>`, the schedule override (2026-10-17; restart: Window A)

**Needs a gateway restart** (Window A, with WP0, WP1 and WP3 if they are ready; alone is fine too). Both sides change. Tick side, from the next tick after the install: what is due comes from the practice library (`library.scheduled`, every enabled practice with a slot), sends go through the extracted `tick.send_prompt`, folding uses each prompt's own window, reflections follow `closes: day` / `closes: week`, status and `journal_set_location` list every scheduled practice. Hook side, at the restart: `capture.record` titles a new entry and folds in the bank of the practice the prompt was sent for, the dispatch hook routes `/start <id>` to the new `/journal-start` command, the skill's *Kinds* list is rendered from the library, `plugin.yaml` marks `schedule` deprecated. The cron stub is unchanged and no plugin setting is added. **One Hermes setting may have to change** (step 1b, a live-config edit for the owner or the operator): where the companion's chat gates slash commands (no admin listed in `allow_admin_from` under `platforms.telegram.extra` in `$HERMES_HOME/config.yaml`), Hermes checks the *rewritten* command, so `/start <id>` arrives as `/journal-start`, which is not in `user_allowed_commands` and gets Hermes' "admin only" reply. The skill now points the companion at `/start <id>`, so without the edit she would offer a command that is refused.

What does not change: for the four rituals every byte he receives, every entry file and every ledger record is what it was (`tests/test_golden_engine.py`, captured on `8249283` before the change: entry files, folded headers, opener requests, the record, `sent.jsonl` line, Telegram calls and mirror of a send and a re-send, and a week of schedule including a DST change). A read-only probe ran the old and the new code over a copy of a live ledger (paths rewritten to a temp root; the vault's `*.md` checksum the same before and after) and got identical folds, sweep header texts, missed-prompt runs, status `due_now`/`next`/open prompts, reflection triggers, capture matches and sends.

Small behaviour changes that do not show for the four built-ins, all from the review: the first reply to a prompt never lands in a file that is not that practice's engine entry (a hand-written `2026-10-14-evening.md` without the engine's `type: journal`, or another practice's entry): the entry takes the next free name past the practice's slots (`…-evening-2.md`) and the prompt records it; a fork (`practices fork`, `migrate-schedule`) is written with `windows.collapse_hours` (6 for the built-ins, the files' value), so it folds as the built-in did; a practice whose id is another one's later slot (`water-2` beside a two-slot `water`) or a note/analysis repeat name (`j-2`, `reflection-2`) is ignored with a doctor problem; a `schedule` that is not an object is a doctor warning instead of a crash; after a failed send the plain retry records `asked: []` (no "Woven in" for questions it never asked).

A `<profile>.json` that still carries `schedule` (say the four times, the same as the files) now holds a deprecated override, and it **wins over the files**: doctor says so four times (`schedule.evening is set in config; schedules now live in the practice file (moved automatically by journal practices migrate-schedule); it says the same as the file, so it can simply be removed`). While it says what the files say it changes nothing. But a time changed in a fork later would be silently ignored while it stays (a fork of evening edited to 22:00 is still sent at 23:00; only doctor, `practices list`, `practices validate` and the fork command itself say so). So removing it is a **recommended step of this deploy** (step 1c): reversible, no restart, but a live-config edit, so the owner's yes first. Changing a time from then on means a fork (`journal practices fork evening`, edit `schedule.time`), never the config.

Between the install and the restart the new tick runs beside the old hook. That is safe: the old hook captures exactly as before, and the new tick sends the same bytes. `/start <id>` does not work until the restart: the old hook does not route it, so Hermes' own `/start` takes it and acknowledges silently (after the restart it needs step 1b, or Hermes answers "admin only"). `/morning` … `/weekly` keep working throughout. So restart straight after installing.

0. Merge to `main`, note the merge commit (`<sha>`). Pick a window at least an hour from a ritual (06:00, Wed 12:00, Sun 20:00, 23:00): the restart takes every agent on the gateway offline for about a minute.
1. Install and check:
   ```sh
   hermes -p <profile> plugins install beejsbj/journal --force --ref <sha>     # confirm the role_hijack caution
   hermes -p <profile> journal doctor
   #   ! config: schedule.morning is set in config; … it says the same as the file, so it can simply be removed   (×4: morning, evening, midweek, weekly)
   #   ✓ day reflection: after the evening entry is annotated, fallback 04:00 the next day
   #   ✓ week review: after the weekly entry is annotated, fallback 04:00 the day after the week
   #   ✓ library: 4 practices (4 built-in): morning, evening, midweek, weekly
   hermes -p <profile> journal practices list                        # morning daily 06:00 · evening daily 23:00 · midweek Wed 12:00 · weekly Sun 20:00 (no "schedule.… in config" note: the override says the same)
   hermes -p <profile> journal practices migrate-schedule --dry-run  # "… already says …; nothing to move" ×4, then where the key lives
   hermes -p <profile> journal status                                # Next: morning …, evening …, midweek …, weekly … (unchanged)
   ```
1b. **Required, the owner or the operator (live Hermes config):** let the companion's chat run the routed command. In `$HERMES_HOME/config.yaml`, under `platforms.telegram.extra`, add `journal-start` and `journal_start` to `user_allowed_commands` (both spellings; Hermes reads it at start, so before the restart below). Add the two names to the list as it is then, keeping every name already there (copy the file first, e.g. `cp config.yaml config.yaml.pre-wp2`, and compare `grep -n user_allowed_commands` before and after); a list that held the journal's commands, for example, becomes:
   ```yaml
   user_allowed_commands: [j, x, morning, evening, midweek, weekly, journal-start, journal_start, …]
   ```
   Leave `command_menu` alone unless a menu entry is wanted: `/start <id>` is typed, so none is needed (with a full menu, one would mean dropping another command from `priority`). Rollback of this step: remove the two names again (or put the copy back).
1c. **Recommended, the owner's yes (live journal config):** drop the deprecated override so the practice files alone decide the times:
   ```sh
   hermes -p <profile> journal practices migrate-schedule --dry-run   # "… already says …; nothing to move" ×4: nothing is lost
   # then delete the "schedule" key from ~/.config/journal/<profile>.json (keep a copy of the file to undo)
   hermes -p <profile> journal doctor                                  # the four schedule.… warnings are gone
   hermes -p <profile> journal practices list                          # the same four times, from the files
   ```
   No restart: the tick reads the config fresh. Skipping it changes nothing while the override says what the files say; it only means that a later time change has to go into the override too (or the override has to go then). Rollback: put the key back.
2. Restart the gateway, then check it came back:
   ```sh
   hermes gateway restart
   hermes gateway status
   hermes logs --level WARNING | grep -i journal | tail     # nothing new; in particular no "conflicts with a built-in command"
   grep -A7 '^## Kinds' $HERMES_HOME/plugin-data/journal/skill/journal-system/SKILL.md
   #   - **Morning journal** (`/start morning`): orientation — where they are, what matters today.   (and evening, midweek, weekly)
   ```
   `/start` alone stays Hermes'. `/journal_start` need not be in the Telegram menu (see 1b): `/start <id>` is typed.
3. In Telegram (needs 1b): `/start nope` sends nothing and answers "There's no practice called “nope”. You have: /start morning (Morning journal), /start evening (Evening journal), /start midweek (Midweek check-in), /start weekly (Weekly pattern scan)." Then `/start evening`, when an evening prompt is welcome (it sends one: "<companion emoji> Your evening journal is on its way."; a second send the same day becomes current and the first still takes replies), works like `/evening` always did.
4. First ticks: unchanged output. After the next ritual has gone out, its record has no new keys and its bank is the previous send's:
   ```sh
   L=$HERMES_HOME/plugin-data/journal/ledger.json
   jq '[.prompts[] | select(.kind=="evening" and .message_id)][-1] | has("title") or has("collapse_hours")' $L   # false
   jq '[.prompts[] | select(.kind=="evening" and .message_id)][-2:] | map(.plain_text | split("\n\n") | last) | .[0] == .[1]' $L   # true
   ```
5. Verify in use, when they next happen: a reply to a ritual lands in its entry exactly as before; the day reflection follows the annotated evening entry; the folded header reads as before.
6. Nothing else to do beyond 1b (and 1c if agreed). A practice of the owner's own, when wanted, is a file in `$HERMES_HOME/plugin-data/journal/practices/` (`journal practices validate` checks it); it is sent from the next tick and startable at once, with no restart; the skill lists it from the next restart.

**Rollback:** `hermes -p <profile> plugins install beejsbj/journal --force --ref <previous sha>`, then `hermes gateway restart`. The old code reads the ritual records as before and ignores the four new optional keys. It is clean as long as no practice of his own has been sent. If one has, the old code cannot title it: its fold step fails on that prompt (`KeyError` in `compose.header`, and with it every fold of that tick) until the prompt is folded, and the old hook cannot start an entry for a reply to it. Before reinstalling, mark those prompts folded with the new code (it takes the ledger lock):
```sh
PYTHONPATH=$HERMES_HOME/plugins/journal/src python3 -c '
import os; from datetime import datetime, timezone; from pathlib import Path; from journal import ledger
with ledger.locked(Path(os.environ["HERMES_HOME"], "plugin-data/journal/ledger.json")) as d:
    for p in d["prompts"]:
        if p.get("kind") not in ("morning", "evening", "midweek", "weekly", "j", "note"):
            p.setdefault("collapsed_at", datetime.now(timezone.utc).isoformat()); p["needs_edit"] = False; p["annotate"] = False; print(p["id"])'
```
and move his practice files out of `plugin-data/journal/practices/` (the old code lists them but never sends them). A fork's or a user practice's entries already written stay in the journal and keep being recognised by their front matter. The skill goes back to the hand-written *Kinds* list at the restart.

## WP1 — practice library, the four rituals as files (2026-10-17; no gateway restart needed)

**No gateway restart needed by this package**; install only (it can also ride along with Window A). What changes: the four question banks now live in `src/journal/library/practices/{morning,evening,midweek,weekly}.md`, generated once from the old `questions.py` constants by `tools/gen_builtins.py`; `questions.py` reads those files and keeps every name and signature the engine uses (`MORNING`, `EVENING`, `TITLES`, `bank_lines`, `bank_items`, `bank_with_ids`), so composing, validating and recording a prompt reads the same text as before. `tests/test_golden.py` holds the files to copies of the old outputs captured at `1412843` (bank lines, `[id]` banks, `bank_items`, the Telegram HTML and plain text for a fixed date, the plain openers), byte for byte. New: `library.py` (parse, render, load, problems, fork); the setting `practices_dir` (default `<state_dir>/practices`, i.e. `plugin-data/journal/practices`, outside the vault per Decision 3; `$JOURNAL_PRACTICES_DIR` overrides; added to `plugin.yaml`'s `config_schema`, nothing to set for the companion); `journal practices list | show | validate | render | fork` (also `hermes journal practices …`); two doctor rows, `practices dir` and `library`. Config loading is stricter in two small ways that do not touch the companion's settings: a non-string `state_dir`/`practices_dir` is a `ConfigError` instead of a traceback, and doctor warns on a relative `root`, `state_dir` or `practices_dir` (absolute or unset paths add no row; if doctor shows one, make that path absolute). The engine does **not** send from user files yet: a fork or a new practice is listed and validated only, until WP2 keys the engine by practice. Nothing creates the practices directory until a `fork` writes into it.

Why no restart: the tick is a fresh process, so from the next tick it reads the banks through the new facade (same bytes). The running gateway keeps the old `questions.py` it loaded at start, whose constants hold the same text, so hook and tick never disagree; the hook-side diff is a moved import in `entries.py` with no behaviour change. `hermes journal …` runs in a fresh CLI process, so `hermes journal practices` works at once. The cron stub is unchanged.

If WP0 is not live yet, this install carries it too, and WP0 does need its restart: follow the WP0 section below for that part (Window A).

The install-time scan's `role_hijack` caution (the midweek "Drift Check" wording) should now name `src/journal/library/practices/midweek.md` and the golden copies under `tests/golden/` instead of `questions.py` (not verified: the scan only runs at install; it may also vanish if the scan skips Markdown). Same false positive; confirm it. Any *other* finding is new and worth a look before confirming.

0. Merge to `main`, note the merge commit (`<sha>`). Install at least 15 minutes from a ritual (06:00, Wed 12:00, Sun 20:00, 23:00), so no send tick imports a half-replaced tree.
1. Install and check:
   ```sh
   hermes -p <profile> plugins install beejsbj/journal --force --ref <sha>     # confirm the role_hijack caution
   hermes -p <profile> journal doctor
   #   ✓ practices dir: $HERMES_HOME/plugin-data/journal/practices will be created (no user practices yet)
   #   ✓ library: 4 practices (4 built-in): morning, evening, midweek, weekly
   hermes -p <profile> journal practices list         # morning daily 06:00 · evening daily 23:00 · midweek Wed 12:00 · weekly Sun 20:00, all built-in
   hermes -p <profile> journal practices validate     # ok: 4 practices (4 built-in, 0 user) in …/plugin-data/journal/practices
   hermes -p <profile> journal practices show evening # the ten sections with their [9.2]-style ids
   ```
2. First ticks: unchanged output. After the next ritual has gone out, its bank is byte-identical to the previous send of the same ritual (the bank is the last paragraph of the archived plain text):
   ```sh
   L=$HERMES_HOME/plugin-data/journal/ledger.json
   jq '[.prompts[] | select(.kind=="evening" and .message_id)][-2:] | map(.plain_text | split("\n\n") | last) | .[0] == .[1]' $L   # true
   ```
   The schedule still comes from `schedule.<ritual>` in `<profile>.json` (unchanged); the times in the files are the defaults and agree with it.
3. Nothing to verify in the gateway (no restart). When Window A restarts it for WP0/WP2/WP3, it loads the facade too; the goldens are what makes that a no-op for what the owner receives.

**Rollback:** `hermes -p <profile> plugins install beejsbj/journal --force --ref <previous sha>`; no restart. The old code has the banks in `questions.py` again and ignores `practices_dir` (a directory a `fork` created stays, unused; delete it or keep it for WP2).

## WP0 — entry kinds generalised, hook state on disk (2026-10-16; restart: Window A)

Both sides change, so it **needs a gateway restart** (Window A, together with WP1–WP3 if they are ready; alone is fine too). Hook side: `hermes/hooks.py` now keeps what it learns between callbacks (a voice note awaiting its transcript, the pack queued for a reply turn, a turn marked off the record) in `plugin-data/journal/hook-state.json` under the state lock instead of process memory, so a gateway restart no longer loses a voice note sent just before it, a queued pack, or the off-record mark on a running turn; `entries.py` recognises entries by shape (`YYYY-MM-DD-<id>[-sNN][-N].md`) plus a known id or the file's own `kind:`/`practice:` front matter together with the engine's `type: journal`, and strips the engine's hidden `%% journal … %%` key lines from his words wherever they are read; new ritual entries carry `practice: <id>` beside `kind:`; `journal_add_note` accepts any known id (and the id of an entry that already exists that day) instead of a five-word enum. Tick side (`status.py`) applies from the next tick. The cron stub and `plugin.yaml` are unchanged; no setting is added. This package also carries the design docs (`docs/`) and `docs/DECISIONS.md` to `main`.

Between the install and the restart the old hook keeps its in-memory state and the new tick reads none of it, which is the same as before; nothing is written to `hook-state.json` until the new hook is loaded.

0. Merge to `main`, note the merge commit (`<sha>`). Pick a window at least an hour from a ritual (06:00, Wed 12:00, Sun 20:00, 23:00) and, since pending hook state is exactly what a restart used to lose, not within 15 minutes of a voice note or a `/x` from the owner: the old hook's pending voice notes die with the old process (as they always did).
1. Install and check:
   ```sh
   hermes -p <profile> plugins install beejsbj/journal --force --ref <sha>     # confirm the role_hijack caution
   hermes -p <profile> journal doctor      # new row: hook state ✓ ".../plugin-data/journal/hook-state.json not written yet (0 voice note(s) awaiting transcript, 0 pack(s) queued, 0 off-record turn(s))"
   ```
2. Restart the gateway, then check it came back:
   ```sh
   hermes gateway restart
   hermes gateway status
   hermes logs --level WARNING | grep -i journal | tail     # nothing new
   ```
3. First ticks: unchanged output. `hermes -p <profile> journal status` shows a `Hook state:` line only while something is pending (or if the file is unreadable).
4. Verify in use, when they next happen:
   - A voice-note reply to a prompt: `jq .voice $HERMES_HOME/plugin-data/journal/hook-state.json` holds one record between the message arriving and the companion's turn starting (seconds), then the transcript is in the entry as before. The restart case itself is not something to provoke live (it needs a restart between the two callbacks); `tests/test_hermes.py::HookRestartTest` reloads the module between them, and in production the same file carries the record across `hermes gateway restart`.
   - A reply to a prompt: `jq .packs` shows the queued block until her turn takes it; her turn still carries the pack (read-only in `messages.api_content`).
   - `/x something`: `jq .off_turns` shows the session for the turn; `journal_add_note` in that turn is refused as before. A later public message of the same session clears it.
   - A hand-written `$JOURNAL_ROOT/2026-10-20.md` or `…-2.md` is not listed as an entry by packs or reflections (it never was; the pattern now says so explicitly); a file with an unknown id and `kind: <id>` front matter is.
5. Entries written after the restart have `practice: evening` (etc.) in their front matter; `/j` notes do not. Old entries are not rewritten.

**Rollback:** `hermes -p <profile> plugins install beejsbj/journal --force --ref <previous sha>`, then `hermes gateway restart`. The old code ignores `hook-state.json` (pending records in it at the moment of rollback are lost, as a restart always lost them), reads `practice:` lines as ordinary front matter, and recognises the same files it did before. Entries with `practice:` need no change.

## The `conversations` branch (the chat saved into entries, `/x`, pack on reply turns, backfill)

Both sides change, so it **needs a gateway restart**: the hook (`/x`, chat-audio stash, the context pack on reply turns, the new `/j` marker), the new `/x` slash command, `capture.py`, `entries.py`, `plugin.yaml` (a `conversations` setting) and the skill are loaded at gateway start; the conversation writer itself runs in the tick (`conversations.py`). The cron stub is unchanged.

Between the install and the restart the new tick already writes conversation into entries, while the old hook is still loaded: `/x` does not work yet in that window (Hermes answers "Unknown command" and the message never reaches the companion; nothing is saved), so restart straight after installing.

0. Merge to `main`, note the merge commit (`<sha>`). Pick a window at least an hour from a ritual (06:00, Wed 12:00, Sun 20:00, 23:00): the restart takes every agent on the gateway offline for about a minute.
1. Install and check:
   ```sh
   hermes -p <profile> plugins install beejsbj/journal --force --ref <sha>     # confirm the role_hijack caution
   hermes -p <profile> journal doctor      # new row: conversations ✓ "saved by thread (ends after 45 min quiet); /x is off the record; auto notes not annotated"
   ```
2. Restart the gateway, then check it came back:
   ```sh
   hermes gateway restart
   hermes gateway status
   hermes logs --level WARNING | grep -i journal | tail     # nothing new
   ```
   After the restart `/x` is in the companion's Telegram command menu (Hermes pushes plugin commands with `setMyCommands` at start).
3. First ticks. The first tick only records where the conversation writer starts (the newest row in `state.db`); nothing earlier is written by it. `hermes -p <profile> journal status` then shows `Conversations: 0 message(s) and 0 repl(ies) saved, up to row …`.
4. Verify in use, when they next happen:
   - A chat message to the companion that isn't a journal reply: within about seven minutes (the tick waits two minutes for a row to settle, and for her turn to end) `hermes -p <profile> cron runs <job>` shows `conversation → new note 2026-…-j.md: +1 from <owner>, +1 from <companion>`, and `$JOURNAL_ROOT/<date>-j.md` has `capture: auto`, his message under `## <owner>`, her reply folded under `## Conversation`.
   - A reply to a prompt, then more chat: the chat lands under `## Conversation` in that ritual entry, between his words and the annotations. The annotation still comes about 20 minutes after the first reply.
   - `/x test`: the companion answers normally; nothing appears in the journal; `jq '.off' $HERMES_HOME/plugin-data/journal/conversations.json` lists the message id. `/x` alone: the command answers "Off the record: …"; the chat after it is not saved until 45 quiet minutes or the next prompt.
   - A reply to a prompt: the companion's turn carries the opener's context pack (`## Journal: the context your opener for this … prompt was written from`); visible in `messages.api_content` of that user row, read-only.
5. Backfill (the owner's call, not part of the deploy): a dry run first, then the write.
   ```sh
   hermes -p <profile> journal backfill-conversations --since YYYY-MM-DD            # lists files, counts, first lines; writes nothing
   hermes -p <profile> journal backfill-conversations --since YYYY-MM-DD --write    # waits for a running tick; safe to repeat
   ```
   It stops at the row where the live writer started, so it never overlaps it. The dry run takes no lock and creates nothing, so it can run while ticks do.

**Rollback:** `hermes -p <profile> plugins install beejsbj/journal --force --ref <previous sha>`, then `hermes gateway restart`. The old code ignores `conversations.json` and the audio stash. Entries already written keep their `## Conversation` sections and auto notes; the old parser reads a `## Conversation` section as part of his words (her folded replies included) for packs, quote checks and annotations, so if a rollback is more than brief, move those sections out by hand or turn the feature off instead: `"conversations": {"enabled": false}` in `~/.config/journal/<profile>.json` (no restart needed for the tick; the hook keeps honouring `/x`).

## The `mirror-recovery` branch (chat mirror, conversation-shaped pack, outage catch-up)

Tick side only (`tick.py`, `mirror.py`, `recovery.py`, `context.py`, `prompts.py`, `validate.py`, `sources.py`, `reflect.py`, `agent.py`, `config.py`, `status.py`): it applies from the next tick after the install, and **no gateway restart is needed**. `plugin.yaml` gains two settings (`hermes.mirror_to_chat`, `recovery`), both on by default; the Desktop settings form shows them after a restart, but nothing depends on that. The cron stub is unchanged.

0. Merge to `main`, note the merge commit (`<sha>`).
1. Install and check:
   ```sh
   hermes -p <profile> plugins install beejsbj/journal --force --ref <sha>     # confirm the role_hijack caution
   hermes -p <profile> journal doctor                                          # cron stub ✓ (unchanged)
   ```
2. First ticks: `hermes -p <profile> cron runs <job>` shows nothing new until something is sent. After the next prompt, its line is followed by nothing (mirrored) or by `not mirrored into the chat session (…)` / `mirror queued for a later tick (…)`. Check the row read-only:
   ```sh
   sqlite3 "file:$HERMES_HOME/state.db?mode=ro" \
     "select id, role, substr(content, 1, 80) from messages where content like '[Journal: %' order by id desc limit 3"
   ```
   and `jq '.prompts[-1].mirrored' $HERMES_HOME/plugin-data/journal/ledger.json` → `true`.
3. Verify in use: reply to a prompt by voice or text and ask the companion what the prompt said; she should answer from the mirrored text, not the truncated quote. The next opener's pack (`plugin-data/journal/packs/<id>.md`) has one `## Recent conversation …` section with `- [you] …` lines interleaved with his messages, and `- tududi: N more habits never completed yet (not listed)`.
4. The outage catch-up only acts after a real outage; `hermes -p <profile> journal status` shows `Outage catch-up: …` once one happened.

**Rollback:** reinstall the previous ref (`--force --ref <previous sha>`). The old code ignores `mirror-queue.json` and the ledger's `recovery` list and `mirrored` keys. Rows already mirrored stay in the chat transcript (they are ordinary user rows); turn either off without a rollback by setting `"hermes": {"mirror_to_chat": false}` or `"recovery": {"enabled": false}` in `~/.config/journal/<profile>.json`.

## The `reflections` branch (busy-gap sweep, audio transcription, reflections, small fixes)

This release changes both sides, so it needs the gateway restart. Until the restart the new tick runs beside the old hook, which is safe: the old hook captures exactly as before, and the sweep never captures a message the hook already saved (message id, and the hook's note in the stored row).

0. Merge the branch to `main` and note the merge commit (`<sha>` below). Pick a window at least an hour from a ritual (06:00, Wed 12:00, Sun 20:00, 23:00): the restart takes every agent on the gateway offline for about a minute.
1. Optional, recommended: set the ffprobe path explicitly in `~/.config/journal/<profile>.json`, since the cron script's `PATH` may not include Homebrew: `"audio": {"ffprobe": "/usr/local/bin/ffprobe"}`. Without it the only loss is the "recorded …" part of audio headings.
2. Install:
   ```sh
   hermes -p <profile> plugins install beejsbj/journal --force --ref <sha>     # confirm the role_hijack caution
   jq .journal $HERMES_HOME/plugins/.install-metadata.json   # revision = <sha>, pinned
   hermes -p <profile> journal doctor
   ```
   Doctor should show, besides the usual rows: `day reflection` and `week review` ✓, `sweep` ✓ reading `$HERMES_HOME/state.db`, `audio transcription` ✓ (Hermes speech-to-text), `ffprobe` ✓, `cron stub` ✓ (the stub is unchanged, so no `journal setup` is needed).
3. Restart the gateway (hooks, capture, entries, `plugin.yaml` and the skill changed):
   ```sh
   hermes gateway restart
   hermes gateway status
   hermes logs --level WARNING | grep -i journal | tail     # nothing new; the root-required warning is gone
   ```
4. Check the first ticks:
   ```sh
   hermes -p <profile> journal sweep        # "swept: 0 message(s) captured" (or the messages a busy gap had missed)
   hermes -p <profile> journal status       # Sweep: last …, Reflections: …
   ```
   The first tick records `reflections_since` = today, so nothing older is back-filled. If yesterday has entries and it is past 04:00, yesterday's day reflection is written and sent at once, silently. The first week review comes after the next Sunday's weekly entry (or Monday 04:00).
5. Verify in use, when they next happen:
   - A reply sent while the companion is still answering appears in the entry within five minutes (`swept message … (next-message)` in `hermes -p <profile> cron runs <job>` output).
   - An audio file sent as a reply gets a "transcript pending" line, then the transcript a few minutes later; the heading shows file name and duration. This is the first real run of Hermes' STT inside the cron script; if it fails three times the block says "not transcribed" and the tick output says why.
   - After the evening entry is annotated: `$JOURNAL_ROOT/<date>-reflection.md` exists and the companion sends "<companion emoji> <companion>'s reflection — …".

**Rollback:** `hermes -p <profile> plugins install beejsbj/journal --force --ref <previous sha>`, then `hermes gateway restart`. The old code ignores the new state (`sweep.json`, `sent.jsonl`, the ledger's `reflections` list) and keeps it intact; reflection files already written stay in the journal.
