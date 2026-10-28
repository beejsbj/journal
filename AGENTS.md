# journal

A journaling engine: the `journal` CLI, a Hermes plugin, and `journal serve` with a PWA (plus `journal mcp`). The
person it serves journals in his own words; the engine sends prompts, captures his replies verbatim into Markdown
entries, and keeps a companion's words apart from his. What it promises him (his words append-only for the engine,
hers never editable, `/x` on every surface, working with no model) is listed in README.md, *Guarantees*, with the
code and tests that hold each one; weakening one is a decision for `docs/DECISIONS.md`, never a side effect.

## The faces

One engine in `src/journal`, four faces over the same config, state and entry files.

| Face | Where | What it is |
| --- | --- | --- |
| `journal` CLI | `cli.py` (console script `journal`) | `journal tick`, run every five minutes, does all scheduled work; the other subcommands inspect and maintain (`journal --help`). |
| Hermes plugin | repo root: `plugin.yaml`, `__init__.py` → `hermes/` | The companion's integration: hooks (`pre_gateway_dispatch` capture, `pre_llm_call` context, `pre_tool_call` guard), the companion's tools, slash commands, the `skills/journal-system` skill, `hermes journal …`. The journal works in one chat on any gateway platform (the `channel` setting; default Telegram and `telegram.chat_id`): `channel.py` decides which messages and turns are the journal's (platform, chat, owners), and every send goes through the `Channel` port (`telegram.TelegramChannel`, else `hermes/channel.py` through `hermes journal deliver`). |
| `journal serve` + PWA | `serve/` (stdlib HTTP server and API); `web/` (Preact source) built into `serve/static/` | A private web app over the same files. Reads entries and state JSON, never the chat transcript; calls no model; sends by spawning `journal send`. |
| `journal mcp` | `mcp/` (optional extra `journal[mcp]`) | The engine's calls as MCP tools over stdio, under serve's rules. |

Adapters stay thin: the seam is `inbound`, `turncontext`, `tools`, `commands` and the two ports in `ports.py`
(README *Adapters* and *Ports*; `docs/ARCHITECTURE.md` §2).

## How a prompt flows

1. **Due.** `tick.run` takes `tick.lock`; `schedule.slots_due` checks every scheduled practice in the library
   (`library.py`: built-ins in `library/practices/`, his own in `practices_dir`).
2. **Opener.** `context.build` makes a dated, sourced pack (`standing.py`, `sources.py`); `agent.ask` asks the
   companion; `validate.check` rejects uncited claims and quotes not in the record. Any failure gives the plain opener.
3. **Send.** `compose.py` → `cfg.channel()` (the `Channel` port: the Bot API on Telegram, Hermes' senders elsewhere); the record goes into the ledger (`ledger.py`) and `sent.jsonl`; `mirror.py`
   copies it into the companion's chat session.
4. **Capture.** His reply reaches the gateway hook before any model runs: `hermes/hooks.py` → `inbound.receive` →
   `capture.py` → `entries.record_reply`. The next tick's `sweep.py` catches what the hook missed (the busy path), and
   `conversations.py` writes the chat that follows into the entry, by thread. Every one of them asks `privacy.py`.
5. **After.** Later ticks move program runs (`programs.py`), fold the sent message where the platform allows edits
   (header, the opener as sent when it fits and parses, a status line; elsewhere the prompt is closed as sent), transcribe audio and describe pictures (`tick.process_audio`, `tick.process_images`),
   annotate and reply (`tick.annotate`, `tick.reply_annotations`), write reflections (`reflect.py`), and catch up
   after an outage (`recovery.py`, `outage.py`).

`tick.py`'s docstring is the order of steps; README *Architecture* walks every module and step in full;
`docs/ARCHITECTURE.md` has the ports, process model and config layering.

## Where state lives

- **Entries**: `root`, one Markdown file per entry, kept audio and pictures beside it. Format in README *Entry file*;
  parsed by `entries.py` and `serve/document.py`. The `%% journal … %%` lines are the writer's keys against double
  writes; splice through those modules.
- **State**: `state_dir` (default under Hermes `<HERMES_HOME>/plugin-data/journal`; `$JOURNAL_STATE_DIR` or the
  `state_dir` setting moves it), among others: `ledger.json`,
  `sent.jsonl`, `send-times.json`, `conversations.json` (cursor and the saved privacy pass), `sweep.json` (the sweep's
  row-id watermark), `hook-state.json`, `mirror-queue.json`, `notebook.json`, `notebook-archive.jsonl`,
  `today-cache.json`, `packs/`, `runs/`, `practices/`, `audio-stash/`, `serve-auth.json`, `serve-pairings.json`,
  `last-tick.json`, `errors.jsonl`. Each name is a constant or literal in the module that owns it
  (`grep -rnE '"[a-z-]+\.jsonl?"' src/journal` finds them). Shared JSON state is read, changed and written under `ledger.locked` /
  `ledger.locked_state`, with a `<name>.lock` beside the file; `tick.lock` serialises ticks; `sent.jsonl`,
  `errors.jsonl` and the other `.jsonl` logs are plain appends.
- **The chat transcript**: Hermes' sessions database, opened read-only, and only by `hermes/transcript.py`.
- **Config**: defaults → JSON file → Hermes plugin settings, with `$JOURNAL_*` environment overrides for one run
  (`JOURNAL_CONFIG`, `JOURNAL_STATE_DIR`, `JOURNAL_PRACTICES_DIR`, `JOURNAL_SERVE_BIND`, …) (`config.py`; README
  *Configure*).

## Contracts

Read `docs/DECISIONS.md` before changing anything, and your package's section of `docs/WORK-PACKAGES.md` in full.

- **Tests are the contract for existing behaviour.** Where a doc and the tests disagree, the tests win; record the
  conflict in `docs/DECISIONS.md` rather than choosing silently.
- `docs/DECISIONS.md`: decisions taken and open points. A package that would revisit one raises it there.
- `docs/WORK-PACKAGES.md`: the plan (WP0–WP14); read your package's section in full.
- The spec, as each package references it: `docs/ARCHITECTURE.md`, `docs/PRACTICES.md` (definition format, programs,
  the today block), `docs/API.md` (serve routes, privacy at the surface, locking), `docs/FRONTEND.md` (the PWA),
  `docs/DEPLOY-FRONTEND.md` (exposing serve on a private name).
- `tests/golden/` freezes the built-in rituals byte for byte (`tests/test_golden.py`, `tests/test_golden_engine.py`);
  `tools/gen_builtins.py --check` keeps the files canonical. A deliberate change to a built-in edits its file,
  regenerates the goldens, and says why in the commit.

## Architectural rules

- **`privacy.py` is the single privacy pass, and it fails closed.** Every reader of the chat asks it, a new reader
  included; when the pass cannot run, everything counts as private. Every path that writes his words refuses `/x`
  through `capture.opt_out` / `capture.opt_out_inside`, as `serve/writes.py` and `mcp/handlers.py` do; a new route or
  tool does the same.
- **Fences hold the shape.** `tests/test_purity.py`: Hermes is imported only under `hermes/` (and by `audio.py` for
  Hermes' speech-to-text, the one entry in `HERMES_ALLOWED`), and `sqlite3.connect` is called only by the transcript
  adapter and `sources.py`. `tests/test_serve_fence.py` and `tests/test_mcp_fence.py`:
  serve and mcp import nothing that reads the chat, its privacy results, packs, the mirror or the notebook (mcp no
  model either). `tests/test_serve_privacy.py` runs every route with `sqlite3.connect` failing; a new route gets a
  sample there.
- **The engine owns sending and capture.** Serve, MCP and the companion spawn `journal send` / `journal program`; an
  HTTP worker never holds `tick.lock`.
- **The core is stdlib.** `dependencies = []`; PyYAML (`journal[hermes]`) and the MCP SDK (`journal[mcp]`) stay optional.
- **Code is generic, and the repo is public.** Names, paths and timezones come from config; a deployment's personal
  overlay lives outside the repo (`deploy/example-overlay.json` shows the shape with placeholders). Nothing personal is
  committed: no names, hosts, paths, places, ids or words of a real deployment, in code, tests, fixtures or docs.
  Tests use neutral fixtures (`tests/helpers.py`, `tests/serve_helpers.py`, `tests/fixtures/`).

## Gotchas

- **Where a change runs decides its deploy.** The gateway is a long-lived process: anything it has imported keeps
  running old code until `hermes gateway restart`. Importing `journal.hermes` loads most of the core (privacy, guard,
  inbound, capture, entries, conversations, context, ledger, library, sweep, tools, today …), and function-level
  imports load more as they run. So treat a change to any module outside `serve/` and `mcp/` as needing a gateway
  restart, plus `plugin.yaml` and the skill, unless you have checked that the change cannot reach a gateway path and
  say why in the DEPLOY.md section (as SERVE-UNSANDBOXED does for `config.py`). To see what loads at import (no
  Hermes needed):

  ```sh
  python3 -I -c "import sys; sys.path.insert(0, 'src'); import journal.hermes; print(sorted(m for m in sys.modules if m.startswith('journal')))"
  ```

  The tick is a fresh process, so it runs new code from the next tick. Serve side waits for `uv tool install --force`
  at the merge sha and `systemctl --user restart journal-serve`. DEPLOY.md's opening table says the same.
- **The serve unit is rendered, and it has no sandbox.** `serve/unit.py` renders it (`journal serve --print-unit`); a
  change there means re-rendering the live unit, diffing, backing up, `daemon-reload` and a restart, since the unit
  file is part of the live install. Namespace-creating directives (`PrivateTmp=`, `ProtectSystem=`, `ProtectHome=`,
  `ReadWritePaths=`) stay out of it, in a drop-in too: under the user manager they break Tailscale identity sign-in
  (DECISIONS.md, SERVE-UNSANDBOXED; README *Web app*).
- **The PWA build is committed.** `src/journal/serve/static/` must be byte for byte what `.github/workflows/web.yml`
  builds, and `tests/test_web_build.py` fails a `web/` change without a rebuilt `static/`. Node and vite run in CI
  (or a remote sandbox to iterate), never on the deploy host; commit the CI run's `static` artifact, since another
  Node version builds different bytes.
- **Hermes' install scanner reads the whole tree.** `tests/test_install_scan.py` applies its CRITICAL patterns to
  docs and lockfiles too: a doc line shaped like an attack (piping a download into an interpreter) blocks a deploy.
  The `role_hijack` caution on the midweek Drift Check question is a known false positive (README *Install*).
- **Hermes loads lazily.** The core imports `journal.hermes` only at run time (`Config.transcript()`,
  `Config.companion()`). Importing Hermes itself is a live-install matter: see *Safety on the host*.

## Safety on the host

The concrete paths behind these placeholders, on the owner's host, are in `~/.config/journal/private/AGENTS.local.md`
(outside the repo); read it when it exists.

- **The vault** (the notes folder holding the configured `root`, below `$JOURNAL_ROOT`: a name for it, not a
  variable the engine reads) holds real words. Tests and probes write only under temporary roots. A probe that copies
  the live ledger rewrites its entry paths to the temp root first, and checksums `$JOURNAL_ROOT/*.md` before and
  after.
- **The live install** (the companion's Hermes profile, `$HERMES_HOME/**`; Hermes itself, e.g.
  `~/.hermes/hermes-agent`; cron; the gateway) changes only in a deliberate deploy with a backup and a receipt. The
  profile's `state.db` opens read-only (`?mode=ro`). Importing Hermes (a probe, the real install scanner) needs
  `HERMES_DISABLE_LAZY_INSTALLS=1` and a temporary `HERMES_HOME`: otherwise lazy installs change Hermes' shared
  environment and the default `HERMES_HOME` is the live install. `hermes plugins validate`, Telegram sends and
  `hermes chat` in the live profile stay out of development.

## Workflow

- The canonical checkout stays on `main`, clean. Work in a worktree beside it (`<worktrees>/journal/<branch>`):
  one package per branch, one PR per package.
  A leftover worktree from a cut-off run is reused: inspect it, keep what is correct, continue.
- Commits are small coherent slices saying what changed, why, and how far it was verified.
- The suite (on a shared host, through its job queue; AGENTS.local.md names it):

  ```sh
  uv run --with pytest --with pyyaml python -m pytest -q
  ```

  CI (`.github/workflows/test.yml`) runs the same suite, `gen_builtins.py --check` and the MCP tests on every push; a
  green CI run counts as the suite.
- `DEPLOY.md` records every release's deploy needs, newest first: each package adds its section (side, restart or
  not, first checks, rollback), written generically; a deployment's own receipts (its paths, hosts and live checks)
  stay in its private notes. `docs/DECISIONS.md` takes what a package decided or left open.
- Before pushing, run `python3 tools/check_private.py`: it greps the tracked tree for the private terms listed outside
  the repo (`$JOURNAL_PRIVATE_TERMS`, default `~/.config/journal/private/terms.txt`) and exits 1 on any hit. Without
  the list (CI) it passes with a note.

## Reviews and merge

A PR merges with CI green, every conversation resolved, and an independent review addressed (Codex when available;
otherwise a Claude Opus pass plus a non-Claude model). The `review-evidence` check
(`.github/workflows/review-evidence.yml`) fails until each review thread is resolved with a reply that shows why:
`Fixed in <sha>` (a commit in this repo), `Declined: <reason of 20+ characters>` (or `Not a bug:`, `Obsolete:`,
`Duplicate:`), or `Tracked in #<issue>`. Resolving fires no workflow: reply first, or push, to re-run it.

Technical notices go to the operator's ops channel; the journal's chat is his personal chat with the companion.
