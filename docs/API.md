# `journal serve`: the HTTP API

Companion to [ARCHITECTURE.md](ARCHITECTURE.md) §4 and [FRONTEND.md](FRONTEND.md). Base path `/api/v1`. All
shapes are the engine's own structures serialised (`library.Practice`, `programs.Run`, `today.collect`, the
entry parser), never a second model.

## Decisions

1. **Stdlib server.** `http.server.ThreadingHTTPServer`, a ~40-line router, `json`; no framework (one user, a
   private network, tens of requests a minute; `dependencies = []` stays true).
2. **Never the transcript.** No route opens `state.db`, `conversations.json`'s privacy results, `packs/`,
   `audio-stash/`, `mirror-queue.json` or the notebook. Fenced by `tests/test_serve_fence.py` (no `/api` handler
   imports `conversations`, `sweep`, `recovery`, `privacy`, `mirror`, `context` or `ports.Transcript`) and
   `tests/test_serve_privacy.py` (patches `sqlite3.connect` to raise and walks every route).
3. **Sends and program transitions are spawned**, never run in-process: `journal send <id>`, `journal program
   <action> <run>`. The response is `202 {"spawned": true, "log": "<state_dir>/manual-trigger.log"}`; the client
   watches `/events` or polls. An HTTP worker never takes `tick.lock`.
4. **Writes take the engine's locks in the engine's order** (`ledger.locked`, then `locked_state`), are short,
   and go through engine functions (`capture.capture_note`, `capture.capture_to`, `entries.record_reply`,
   `library.render` + atomic replace, `config.write_values`).
5. **Auth: network first, then a token on every `/api` request** (cookie or bearer). Pairing: `journal serve pair`
   prints a one-time `https://<host>/pair#<token>` URL; the PWA exchanges it for an `HttpOnly; Secure;
   SameSite=Lax` host-only cookie. `Origin` (or `Sec-Fetch-Site: same-origin`) is required on every write. The
   `Host` header must equal `serve.host` when set. `X-Forwarded-*` is believed only from `serve.trusted_proxies`.
   **Tailscale identity (TSAUTH, 2026-10-20):** with `serve.socket` set the server also listens on that Unix socket
   (0600), which `tailscale serve --https=<port> unix:<path>` proxies to. On the socket only, and only when the
   connecting process is root (tailscaled, read by `SO_PEERCRED`: other processes of the service user can open the
   socket too) and the client tailscaled saw (`X-Forwarded-For`, one parsable address) is another machine, not this host
   (tailscaled relays the host's own connections, signed in as the node's owner), and the browser has not marked it
   cross-site or same-site (`Sec-Fetch-Site`), a request carrying exactly one `Tailscale-User-Login` equal to a login in `serve.tailscale_users` is
   signed in with no cookie or token. tailscaled deletes a client's copy of the header before setting its own; a
   Funnel request is never signed in. On the socket the `Host` check reads `X-Forwarded-Host` (tailscaled sends
   `Host: localhost` there) and `X-Forwarded-*` is believed. Over TCP the header means nothing. The `Origin` check
   on writes is unchanged on both listeners.
6. **Headers:** `Cache-Control: no-store` on `/api`; `Content-Security-Policy: default-src 'self'; img-src 'self'
   data:; media-src 'self' blob:; worker-src 'self'; manifest-src 'self'; frame-ancestors 'none'; base-uri 'none'` on
   everything (`blob:` is for the composer's playback of a clip just recorded; `worker-src` and `manifest-src` are the
   PWA's `sw.js` and manifest, which are served `Cache-Control: no-cache`; the worker is at `/`, so its scope is `/` with
   no `Service-Worker-Allowed` header); `X-Content-Type-Options: nosniff`; no CORS (same origin only; `--dev` enables
   `http://localhost:5173`).
7. **Limits:** JSON bodies ≤ 1 MB; uploads ≤ 25 MB; every text field capped at 50,000 characters; URLs carry ids
   only, never content (the reverse proxy keeps no access log, but keep it so).
8. **The engine appends; he edits.** The only automatic writes to his words are new blocks. `PATCH` and `DELETE` of
   one block of his (`/entries/{stem}/blocks/{key}`) are his own acts from the web app (DECISIONS.md, Decision 4
   amended 2026-10-20): plain edit, no history, under the ledger lock, refused with `409 changed` when the block or
   the entry is not as he read it. Her blocks are never editable. `PUT` of a block does not exist.
9. **`serve.allow_write: false`** turns every route that changes the journal into `405 {"error": "read-only"}`; any
   value but a boolean `true` does (fail closed). Pairing and logout are not journal writes and stay available.
   **`/x` holds on the web:** text that starts with the opt-out command, or has a later line that does (the chat takes
   a merged burst so), is never written (`422 off-the-record`); nor is text with an engine marker line (`422
   reserved-line`) or the engine's transcript-pending line (`422 placeholder`); none is edited, all are refused.
10. **Errors** are `{"error": "<slug>", "detail": "<human line>"}` with these codes: 400 bad request, 401 no or
    bad token, 403 Origin/Host mismatch, 404 unknown id/stem, 405 read-only, 409 conflict (mtime or block drift, built-in
    PUT, active run), 413 too large, 422 validation (`"problems": [...]`), 423 lock not obtained within 5 s,
    503 engine unavailable (config unusable).

## 1. Entry document model

`GET /entries/{stem}` returns the two-voice document the PWA renders. Built from `entries.front_matter`,
`words_span`, `conversation_span`, `annotations_span`, `notes_span` and the conversation parser's block keys.

```json
{
  "stem": "2026-10-14-evening", "date": "2026-10-14", "kind": "evening",
  "practice": {"id": "evening", "title": "Evening journal", "emoji": "🌿"},
  "program": null, "session": null, "run": null,
  "title": "Evening journal — Wed 14 Oct 2026",
  "voices": {"his": "Sam", "hers": "Ada"},
  "prompt": {"sent_at_local": "23:00 IST", "opener": "…", "opener_source": "ada",
             "asked": [{"id": "9.2", "question": "What simple joys touched you today?"}],
             "questions": [{"id": "1.1", "text": "What's spinning in your mind?", "section": "Brain Dump & Letting Go"}],
             "phases": []},
  "blocks": [
    {"kind": "his", "key": "words:0", "at": "23:12 IST", "source": "text", "text": "…", "audio": null, "phase": null},
    {"kind": "his", "key": "words:1", "at": "23:40 IST", "source": "voice", "text": "…", "audio": "2026-10-14-evening.ogg", "meta": {"duration": 151}},
    {"kind": "his", "key": "words:2", "at": "23:41 IST", "source": "photo", "text": "his caption", "audio": null, "images": ["2026-10-14-evening.jpg"]},
    {"kind": "hers", "key": "her:4812", "at": "23:41", "text": "…"},
    {"kind": "his", "key": "his:813", "at": "23:44", "source": "text", "text": "…", "audio": null, "reply_to": null},
    {"kind": "hers", "key": "her:4815", "at": "23:45", "text": "…"},
    {"kind": "his", "key": "his:816", "at": "23:50", "source": "text", "text": "…", "audio": null, "reply_to": "the line of hers he answered"}
  ],
  "summary": null,
  "analysis": {"annotations": "- …", "notes": [{"at": "08:02 IST", "text": "…"}], "reflection": "2026-10-14-reflection"},
  "flags": {"annotated": true, "has_conversation": true, "transcript_pending": false, "audio": true},
  "path": "/…/Journal/2026-10-14-evening.md", "mtime": 1760480000.0, "writable": true
}
```

Every `text` is the file's text with `ENGINE_MARK` lines removed; the server never reflows. `reply_to` (his
conversation messages) is the quoted line of hers his message answered, from the `↩ replying to` callout the
conversation writer puts above his message. `prompt.opener`/`asked` come from the ledger record and are null once
it is pruned. `voices` names the two voices for labels (`names.user`, else "You"; `names.companion`), so the client never guesses a name. Also present: `meta` (`mood`/`people`/`starred` when set) and the entry's `practice` as `{id, title,
emoji}` (null for `/j` and companion-started notes). `writable` is false for a file that is only named like an entry (his own page): writes to it are `409 not-engine-entry`, so the app offers no composer or meta sheet there (WP12 review). Implemented in `serve/document.py` (WP9). `kind: "hers"`
blocks are rendered folded by the client and never merged into his. Program session entries add `summary`
(the filled `## Summary`) and `phase` on his blocks (from `%% journal phase:N %%`). A block of his under
`%% journal interrupt:N %%` (session 10's one-off questions) has `phase: null` and `interrupt: N` (the key is
absent on every other block); the client labels it "Interrupt N". `prompt.phases` is `[{"number", "title"}]` for a
program session entry (the program's session, by the number `session` reports; `[]` for a practice), and for a
program entry `prompt.questions[].section` reads `Phase N: Title`. An embed is `audio` when its name is a recording
`/media` would serve (`audio.media_type`), whatever its extension.

## 2. Routes

Auth column: `T` = token (cookie, `Authorization: Bearer`, or a listed Tailscale login on the Unix socket) required; `T+O` = token and `Origin`/`Sec-Fetch-Site`
check; `–` = none. Lock column names what the handler takes; `spawn` = subprocess under `tick.lock` outside the
server.

| Method | Path | Auth | Request | Response | Lock | Errors |
| --- | --- | --- | --- | --- | --- | --- |
| GET | `/healthz` (also at the site root) | – (no Host check) | – | `{"ok": true, "version", "code", "tick_last_at"}`; `code` is `version.code_id()`, a hash of the package's source (no install carries the git sha) | none | 503 if config unusable |
| GET | `/pair` (site root, not under `/api/v1`) | – (one-time token in fragment, posted by the page) | – | the pairing page (static) | none | |
| POST | `/pair` | – (Origin checked: a write) | `{"token"}` | `204`, sets the cookie; the pairing token is single-use (`state_dir/serve-pairings.json`, 10-minute TTL) | `locked_state` | 401 unknown/expired |
| POST | `/logout` | T+O | – | `204`, clears the cookie | none | |
| GET | `/session` | T | – | `{"via": "tailscale" \| "cookie" \| "token", "login": "<Tailscale login>" \| null}`: how this request is signed in; the web app hides pairing and Unpair for `tailscale` | none | |
| GET | `/today` | T | – | `today.collect(cfg, now)` serialised as it is (DECISIONS.md, WP9): `{"date", "weekday", "time", "timezone", "location", "practices": {"rows": [{"id", "state": "upcoming|open|answered|missed", "at"? \| "sent", "answered"?}], "open": [ids]}, "program": {"text"}?, "entries": [{"stem", "first_line"}], "reflection": {"date", "asked"}?, "tasks": {"count", "names"}?, "weather": "…"?}`; `tests/fixtures/today.json` pins it for the block and this route. The route adds `week` (seven local days, oldest first: `[{date, weekday, answered, missed, state}]`, state `answered\|partial\|missed\|none`) and asks `collect(stems=True)`, which adds the entry `stem` to each `practices.rows` item that has one and to `reflection`; `collect` without `stems` (the thread-start block) has neither. Each row with a `stem` (a prompt that was sent: open, answered or missed) also carries `prompt`, the same object the entry document has (§1: `sent_at_local`, `opener`, `opener_source`, `asked`, `questions`, `phases`; `document.prompt_of`), so Today shows the whole message whatever its state; an `upcoming` row has none. The route also adds `voices` (`{"his", "hers"}`, the names the document labels the two voices with) and `location_source`: `"openstreetmap"` while the place in force was named by "Use my location" (OSM data: the page credits it under the place), else null. `tests/fixtures/api/today-prompts.json` pins a day with a prompt in every state | none | |
| GET | `/entries` | T | `?from&to&kind&program&audio=1&q&limit=50&cursor` | `{"items": [EntrySummary], "next": cursor}`; EntrySummary = `{stem, date, kind, practice, program?, session?, time, title, emoji, first_line, flags, meta: {mood?, people?, starred?}}`; `q` is a case-insensitive substring over **his words only** | none | 400 bad dates |
| GET | `/days` | T | `?from&to` (and the filters of `/entries`) | `[{"date", "entries": [EntrySummary], "reflection": stem?}]` grouped for History | none | |
| GET | `/entries/{stem}` | T | – | the document (§1) | none | 404 not an engine entry (hand-written `YYYY-MM-DD.md` files are never served) |
| POST | `/entries` | T+O | `{"practice"?: id, "text"?: str, "upload"?: id, "at"?: iso, "client_id"?: str}` | `201 {"stem","path","rule"}`: with `practice` and an open prompt of it → `capture.capture_web` (rule `web`, `needs_edit` so the Telegram prompt folds "answered ✓"); with no `practice`, or none open, a `/j` note of its own (rule `/j`; the client can tell him). A repeated `client_id` returns `200` and the first result, also when the retry names an upload the first request consumed. `at` needs an offset, may not be in the future or more than 31 days back | `ledger.locked` | 400 empty or malformed, 404 unknown practice or upload, 413, 422 `off-the-record` (text, or a later line of it, starts with the opt-out command: nothing is written), `reserved-line` (a `%% journal … %%` line) or `placeholder` (the transcript-pending line), 423 |
| POST | `/entries/{stem}/blocks` | T+O | `{"text"?: str, "upload"?: id, "phase"?: int}` | `201 {"key"}`: appends a block of **his** words via `entries.record_reply` (through the entry's ledger prompt when it has one; `client_id` and `at` as for POST entries; a program's block ignores `at`, the server's clock stamps it, so an answer cannot predate its phase). Program session entries (the stem is the run's session entry, which does not exist before the first answer): `phase` is required and must be the run's open phase, else `409 phase-not-open` (with `where`); the block is that phase's answer (`%% journal phase:N %%`) and the response adds `stem` (where it was filed; a retry with the same `client_id` is answered from the ledger, `200`, with the same `stem`, wherever the URL points) and `advance` (`settle`: `journal program settle <run>` was spawned, as the hook does; `next`: the program waits for Next; `tick`: the spawn could not start, the tick's step settles it) | `ledger.locked` | 400, 404, 409 phase not open or `not-engine-entry` (a file the engine did not write), 413, 422 (as POST entries), 423 |
| PATCH | `/entries/{stem}/blocks/{key}` | T+O | `{"text": str, "was"?: str, "at"?: str, "mtime"?: float}` | `200 {"block", "mtime"}`: the words of one block of **his** replaced (`key` is the document's: `words:N`, `his:<id>`, `conv:N`). Only the words change: the heading, time, hidden key line, audio embed, `↩ replying to` callout and every other byte of the file stay (`serve/blocks.py` parses the result again and refuses, `422 ambiguous-text`, anything that would read as other than the same blocks with the new words). He must say what he was looking at: `was` (the block's `text` as read; `at` its time) and/or `mtime` (the entry's); either mismatching is `409 changed`, nothing written. `text` has the usual limit and line-ending rules; it is not empty; the engine's `%% journal … %%` lines are `422 reserved-line`; text that begins with the off-the-record command, or has a later line that does, is `422 off-the-record` and nothing is written (`/x` holds on every surface, as for a new block); the transcript-pending line is `422 placeholder`. A program phase answer can be edited; when its session has completed, the response adds `summary`: `refreshing` (`journal program refresh-summary <run> --session N` was spawned: it takes the tick's lock and writes the entry's `## Summary` again from his words as they are then; DECISIONS.md, SUMMARY-REFRESH) or `not-started` (the spawn could not start; the edit is saved, the summary keeps the old words). No `summary` for a block that is not a phase answer, a session still open, or one whose summary waits for a transcript (the command is sent for the last two as well and does nothing unless the session is due once it has the lock). When the summary could not be refreshed because the section is not what the engine last wrote, has lost its mark, has a block of his under it, or the session has no digest (completed by earlier code), the response says `summary: "kept"` with `summary_reason` (the words to show), nothing is spawned and the entry is untouched; the command makes the same check again under the locks (so `refreshing` can still end in the summary being kept, logged in `manual-trigger.log`). Deleting an answer stays `409 program-answer` | `ledger.locked` | 400 (no `text`, empty, neither `was` nor `mtime`), 404 no such block, 409 `changed`, `not-his` (a block of hers), `transcribing`, `not-editable`, `not-engine-entry`, 413, 422, 423 |
| DELETE | `/entries/{stem}/blocks/{key}` | T+O | `{"was"?: str, "at"?: str, "mtime"?: float}` | `200 {"mtime"}`: the block's hidden key line, heading and body removed, nothing else (later blocks are renumbered; her replies and the recording file stay). Same preconditions as PATCH. A block that answers a program's phase or interrupt is `409 program-answer` (the program counts it) | `ledger.locked` | 400, 404, 409 (as PATCH, plus `program-answer`), 423 |
| PATCH | `/entries/{stem}/meta` | T+O | `{"mood"?: [str], "people"?: [str], "starred"?: bool}` | `200 {"meta", "mtime"}`: front-matter keys the engine owns (`mood`, `people`, `starred`; an empty list or `false` removes the key); `date`, `kind`, `practice`, `program`, `session`, `run` are immutable and any other key is refused; only the front matter is rewritten. `mtime` is optional | `ledger.locked` | 400 unknown or bad key, 404, 409 mtime drift or `not-engine-entry`, 423 |
| GET | `/media/{name}` | T | – | the audio file beside its entry (`Content-Type` by extension, `Accept-Ranges: bytes`); `name` must match `^[\w.-]+$`, resolve inside `media_dir`, and be referenced by an engine entry | none | 404 |
| POST | `/uploads` | T+O | `multipart/form-data` audio ≤ 25 MB | `201 {"upload": id, "bytes", "mime"}`; stored in `state_dir/uploads/` for 1 hour; consumed once by POST entries/blocks through `audio.keep` (the file lands beside the entry, `transcript: "pending"`, the tick transcribes it as it does Telegram audio files). The 25 MB limit is this route's; JSON bodies stay at 1 MB | none | 400 not multipart or no file part, 413, 415 not audio |
| GET | `/reflections` | T | `?kind=reflection|week&from&to&limit` | `[{"stem","date","kind","title","body","sources":[stems]}]` via `reflect.body_of` | none | |
| GET | `/insights/weeks` | T | `?from&to` | `[{"week_end","entries","words","answered","missed","program_progress":{…},"review": stem?, "reflections":[stems]}]`, counts computed from entry files and the ledger | none | |
| GET | `/library` | T | – | `[{"id","kind","title","emoji","group","source":"builtin\|user\|fork","enabled","schedule":[Slot],"closes","sessions"?:n,"phases_total"?:n,"active_run"?:id,"mtime","default_changed"}]`; `source: fork` is a built-in he has edited in place (the web app says "edited"), and `default_changed` says whether the shipped default has changed since (null: not known, or not an edited built-in) | none | |
| GET | `/library/{id}` | T | – | `{"definition": <Practice\|Program as JSON>, "raw": "<markdown>", "sessions"?: [{"number","id","title","raw","file","mtime","phases":[…]}], "problems": [...], "mtime", "override"}`. A program's session `file` is the name a save of it takes (`sessions/<name>.md`; absent in a situational definition, whose one file is `raw`). `override` is null unless he has edited this built-in: `{"default_changed": bool\|null, "edited_at": iso\|null, "in_force": bool}` (`in_force` false: his file does not load, the built-in is used meanwhile, and the library's problems with his file are in `problems`). A built-in practice's `collapse_hours` is the window it folds on (`windows.collapse_hours`), so an edit starts from what is in force | none | 404 |
| GET | `/library/{id}/default` | T | – | The shipped definition of a built-in in `GET /library/{id}`'s shape (`source: builtin`, `mtime` null, `override` null), with `"diff": {"since", "yours"}`: unified diffs of what changed in the default since his edit began (null when nothing did or that is not known) and of the default against his version (null when he has none, `""` when they are the same). BUILTIN-EDIT | none | 404 nothing ships under the id |
| PUT | `/library/{id}` (`?validate=1`) | T+O | a practice: `{"raw": str} \| {"definition": {...}}` + `{"mtime": float}`; a program or situational definition: `{"raw": str, "file"?: str, "mtime": float}` | `200` the merged view (as `GET /library/{id}`). **A practice**: validates with `library.parse`, renders canonical Markdown (`library.render`; prose between questions is not kept) and writes atomically into `practices_dir`; **a built-in is edited in place**: the first save makes his file of the same id (any `mtime` is ignored: there is no file of his to have drifted) and remembers the shipped text it began from; a program's or situational definition's id given a practice's text (or a `definition`) is 409 `is-program` (a practice cannot take it; with `?validate=1` it is a problem, and a new practice named like one's alias is 422); `mtime` must equal his file's current mtime (409 on drift; 400 when missing for an existing file); an id nobody has creates a user practice (no `mtime`); 422 lists `problems` and writes nothing. **A program or situational definition** is saved one file at a time, as typed: `file` is `program.md` or a `sessions/<name>.md` it has (a situational definition is its one file; any other name is 404); the whole definition it makes must parse and keep its id (422 `problems` otherwise); while a run of it is active or paused (or completed with session 10's interrupts still to come), a change to its structure (sessions, phases, phase types, question ids) is 409 `active-run` with `runs` (words may change: they apply from the next send); the first save of a built-in copies its whole directory with the change in it. With `?validate=1` nothing is written and the answer is `200 {"valid", "problems", "raw"}` (`raw`: for a practice the canonical text it would save, null when it does not parse; for a program or situational file the text as it would be saved, which is the text sent with its line endings made `\n` (those files are not re-rendered), null when the definition does not load; a program's run conflict is a problem there) | `locked_state(library.lock)` | 400, 404, 409, 422, 423 |
| POST | `/library/{id}/fork` | T+O | – | `201` the merged view; copies the built-in into `practices_dir` (a practice or situational definition as `<id>.md`, a program as `<id>/`) and remembers the shipped text. The web app no longer uses it (a save edits a built-in in place); kept for the CLI's `fork` and older clients | `locked_state` | 404, 409 already forked or not a built-in |
| DELETE | `/library/{id}` | T+O | – | `204` removes the user file: an edited built-in is **reset to the default** (his file, or a program's `program.md` and its shipped or listed session files, and the record of the default it began from; a program reset is 409 `active-run` with `runs` while a run of it is active or paused, or completed with session 10's interrupts still to come, and the default's structure differs from his); a practice or situational file of his own disappears (a program folder of his own is not removed here: 404) (409 if a run of it is active: `runs`, their ids; not for a practice that hides a program: removing it gives the program back); 409 `builtin` when there is no edit of his to reset | `locked_state` | 404, 409 |
| POST | `/library/{id}/send` | T+O | – | `202 {"spawned": true, "log"}`: `journal send <id>` (same as `/morning`) | spawn | 404, 409 id is a program (use `/runs`) or turned off, 503 cannot start |
| GET | `/runs` | T | `?status=active|paused|completed|abandoned|expired&program` | `[Run]` newest first (400 for an unknown status). `Run` = `{id, program, title, emoji, status, started_at, completed_at, paused_since, last_activity_at, current: {session, phase}\|null, where: "S:P:V"\|null, text, sessions: [{number, title, status: "not-started\|in_progress\|completed\|left", entry: stem\|null, phases_total, started_at, completed_at}], insights_entry, scheduled: [{id, session, n, of, offset, local, at, status, text}]}`; `text` is `programs.describe`, `where` the token a transition is bound to; `entry` is null until the session begins, then the planned stem (no file yet: the first answer is posted to it) and, once answered, the stem it was filed under (a file of his at the planned name moves it one on) | none | 400 |
| GET | `/runs/{id}` | T | – | `Run` + `{"phase": PhaseJSON\|null, "keyboard": ["next","skip","back","pause","resume","abandon"] filtered to what means something now}`; `PhaseJSON` = `compose.phase_json` of the definition (his earlier answers filled into its prose slots) + `{sent_text, delivered, open, answered, skipped, phases_total}` (`sent_text` is what Telegram was sent: a reflect phase's construction is only there); null when the run is over or its session has not begun; while a send is owed the object is there with `delivered: false`, `open: false` | none | 404 |
| POST | `/runs` | T+O | `{"program": id, "session"?: int}` | `202 {"spawned", "log"}`: `journal program start <id> --new [--session N]` (`--new`: the child does nothing if a run began meanwhile); 409 `active-run` (with `run`) if the program has a run in progress, paused or not (the client carries on with it), 409 `disabled`, 409 `program-in-progress` (with `run` and `program`: another program, not situational, has an unfinished run, active or paused, and only one program runs at a time; `detail` says which, where it is and to finish or abandon it, and the client links `run` as "Carry on with it"; never for a situational definition; DECISIONS.md, ONE-PROGRAM-RUN). The child asks again under the tick's lock and does nothing if another face's start won meanwhile | spawn | 400, 404, 409 |
| POST | `/runs/{id}/{next\|skip\|back\|pause\|resume\|abandon}` | T+O | `{"expect"?: "S:P:V"}` (next, skip, back) | `202 spawned`: `journal program <action> <run> [--expect S:P:V]` (resume adds `--paused`). A run whose program file is gone or unparsable takes only abandon (409 `wrong-state` for the rest). The state is checked first: next, skip, back, pause need an active run (back also a begun session past phase 1), resume a paused one, abandon an active, paused or (interrupts still to come) completed one. `expect` makes it do nothing if the run has moved on. A run's own transitions are never refused for another program (an unfinished run already holds its slot: DECISIONS.md, ONE-PROGRAM-RUN) | spawn | 400 bad `expect`, 404, 409 `wrong-state`, 409 `moved-on` (with `where`) |
| GET | `/runs/{id}/insights` | T | – | `{"stem","body"}` of the compiled insights entry (the file the run names; front matter removed), or 404 until written | none | 404 |
| POST | `/runs/{id}/insights` | T+O | – | `202 spawned`: `journal program insights <run>` | spawn | 404 |
| GET | `/status` | T | – | `status.status(cfg, web=True)` as JSON (includes today-block injections per day; leaves out `conversations`, `mirror_queued`, `notebook_open`, `hook_state`: the chat side) | none | |
| GET | `/doctor` | T | – | `status.doctor(cfg, token=False, web=True)` rows (no secret probing from the web; no hook-state row) | none | |
| GET | `/settings` | T | – | `{"settings", "origins", "profile"?}` (`profile`: the Hermes profile's name when there is one, so the page prints `hermes -p <profile> config set ...`): the effective config, redacted (`telegram.token_*`, `serve.token_*`, `sources.*.command` shown as `"<set>"`), with `origins` per key | none | |
| PATCH | `/settings` | T+O | `{"location"?, "timezone"?, "coordinates"?, "windows"?: {...}, "reflections"?: {...}, "conversations.quiet_minutes"?, "programs.settle_minutes"?, "today"?: {...}}` | `200 {written: {key: layer}}` through `config.patch_values` (a nested change is merged into the layer that owns the key; dotted keys are the nested form); `location` goes through `location.set_location` (looked up first; writes location, timezone and coordinates; `timezone` beside it overrides the lookup's; not together with `coordinates`); a Hermes-owned key → 409 with the `hermes config set` hint (the same refusal `journal config set` gives; both run `settings.apply`); an env-owned key → 409; nothing is written unless every key can be | none (atomic file write) | 400 key not allowed (`telegram`, `guard`, `hermes`, `agent`, `serve`, `root`, `state_dir`, `practices_dir` are never writable here; the allow-list is `config.WEB_WRITABLE`), 422 bad value, 503 place lookup unreachable or the config file's directory not writable by the service user |
| POST | `/settings/location` | T+O | `{"lat": number, "lon": number}` (the browser's position; other keys are ignored) | `200 {"written": {key: layer}, "location", "timezone", "local_time_now"}`: "Use my location". `settings.apply_near` checks that location, timezone and coordinates can all be written (as for a place: a Hermes-owned one is 409 with the lines that set it) **before** the position leaves the host, cuts it to two decimals (about a kilometre), names the place there with OpenStreetMap Nominatim's reverse lookup (zoom 10: a city, town or village; `lookup.reverse_url`) and the place's time zone with Open-Meteo (`timezone=auto`; `lookup.timezone_url`), and saves the three through `location.set_location(near=…)`, the same write and the same `location-history.jsonl` line as a typed place (its `query` reads `device position`). The `coordinates` saved are **the matched place's own** (its centre), never the device's position. The answer names the place, never a position; nothing logs it, and curl reads the lookup address from stdin, so it is not on a command line (DECISIONS.md, WEB-DESIGN) | none (atomic file write) | 422 `invalid` (no number pair, off the map, no named place or no time zone there), 409 not writable, 503 a lookup curl could not reach (any non-zero exit) or that answered an HTTP error (a 429, a 5xx; also for a typed place) or the config file's directory not writable |
| GET | `/events` | T | – | `text/event-stream`: `entries-changed {stem}`, `ledger-changed`, `runs-changed {id}`, `library-changed {id}`, `tick {at}`; from a 2-second mtime poller over `root`, `ledger.json`, `runs/`, `practices_dir`, `last-tick.json`; a `: ping` comment every 25 s (a reverse proxy's idle timeout can be as short as 180 s); `Cache-Control: no-transform` | none | |

Not routes, by decision: the notebook (hers), `packs/`, `audio-stash/`, the transcript, `sent.jsonl`,
annotations/notes writes (`write_annotations`, `add_companion_note` stay the engine's and the companion's).

## 3. Privacy rules at the surface

- **No endpoint reads the chat.** The server's sources are entry files, `ledger.json`, `runs/`, `today-cache.json`,
  `last-tick.json`, reflection files and the library. Off-record material never reaches a file (the privacy pass
  gates every writer), so the API cannot serve it by construction. `/today`'s first lines come from entry files,
  so no `privacy.view` call is needed or allowed (unlike design C).
- `/entries?q=` and `/days` search and preview **his words only** (`entries.his_words`); her folded replies are never
  searched and are tagged `kind: "hers"` in the document so the client always folds them.
- The notebook has no route. `packs/` and `audio-stash/` are unservable (`/media` resolves names only inside
  `media_dir` and only when an engine entry references them).
- Nothing on the server logs bodies; the access log (off by default, `--access-log` for dev) records method, path,
  status and bytes.
- `tests/test_serve_privacy.py`: `sqlite3.connect` patched to raise `AssertionError`, every route exercised against
  a temp journal with a conversation-bearing entry, a reflection, a program run and a user practice; all return
  their normal status; the write routes get the same walk (one sample each, `sqlite3.connect` and the privacy pass
  made to fail, a route added without a sample fails the test). `tests/test_serve_fence.py`: the import grep.

## 4. Locking against the tick

- Reads take no lock. The engine's writes are atomic replaces, so a read sees a whole file; `mtime` is returned so
  a client can detect drift.
- Writes to entries take `ledger.locked` (the same lock the hook and tick take for every entry write); writes to run
  files take `locked_state`; writes to `practices_dir` take `locked_state(practices_dir/.library.lock)`, whose
  state file `practices_dir/.library` keeps, per edited built-in, the shipped text the edit began from (`defaults`). A lock not
  obtained within 5 s is `423`; the client retries.
- Anything that sends or advances is a spawned `journal …` process that waits on `tick.lock` like `/morning`
  does today; the API returns `202` at once. The spawn's output goes to `manual-trigger.log`; `/events` reports the
  resulting `ledger-changed`/`runs-changed`.
- Lock order (tick → ledger → state) is never inverted by the server; `tests/test_locks.py` runs a hook capture, a
  tick round and a `POST /entries/{stem}/blocks` concurrently in-process and asserts no deadlock and both writes
  present.

## 5. CLI surface for the server

```
journal serve [--bind HOST] [--port N] [--dev] [--access-log]      run (foreground; systemd keeps it up)
journal serve pair                                                 print a one-time pairing URL (10 min)
journal serve token --rotate                                       replace the token (invalidates cookies)
journal serve --print-unit                                         the systemd user unit filled from config
journal serve check                                                bind, token, assets, same-version-as-tick
```

Binding to an address that does not exist yet (the Tailscale IP at boot) retries for 60 s, then exits 1 so
`Restart=on-failure` loops. With `serve.socket` the server listens on the socket as well as on TCP (both, always:
TCP keeps the token, cookie and pairing for anything that is not `tailscale serve`); a stale socket file is
replaced, a socket another server answers on is retried like a busy port, anything else at the path is never
removed and stops the start; the file is removed at stop (SIGTERM included).
