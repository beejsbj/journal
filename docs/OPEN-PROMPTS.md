# Open prompts: a practice without a chat message

A design proposal for issues #13 and #14, read against `main` at `2185825`. No code changes yet: a map of where the
engine assumes that a prompt is a chat message, the proposed model, the rules that follow from it, and the choices
that are the owner's. [DECISIONS.md](DECISIONS.md) holds the open entry (*OPEN-PROMPTS*) that points here. An
independent review of the first draft changed the model; *Review* at the end says what it found.

## Why

The journal is meant to be one engine with several faces: the chat (through the Hermes plugin), the web app, the CLI
and, for someone without Hermes, MCP from whatever harness they use. Underneath, the engine is a notebook with a
clock: practices, a schedule and entries, none of which needs a model or a chat. A model is optional, either the
companion in the chat or any command (`agent.command`) for openers, annotations and reflections.

Today only the chat face can run a practice. The web app, the CLI and MCP can save a note and answer a prompt the
chat has already delivered, but none of them can do a practice on its own:

- **Web app:** *Send now* spawns `journal send <id>`, which sends the prompt to the chat.
- **MCP:** `journal_start` spawns the same send.
- **CLI:** `journal send <id>` sends to the chat. There is no command that writes his words.
- **A web-only install** (`adapter: none` on a platform the engine cannot send to) counts every scheduled prompt as
  a failed send and alerts after `SEND_ATTEMPTS`. Answering the morning practice from the web app saves his words as
  a `/j` note of its own, not as the morning entry, so the scheduled slot is still due.

Every one of these comes from a single assumption: a practice's prompt exists once a chat message with a
`message_id` exists.

## The model

**A record is written when something happens: a delivery to the chat, or his first words. A slot that has neither is
still a state: it is derived from the schedule, as *upcoming* already is.**

- **Opening a practice on a face is a view, not a write.** The web app's *Write now*, `journal open <id>` and MCP's
  `journal_start` show the practice's plain opener and questions from the library. Nothing is written until he saves
  words, so there is no "opened but not answered" record, and an accidental tap changes nothing.
- **His first words on a face write the record and the capture in one locked call** (`actions.answer`). The record
  takes the slot's prompt id, as a delivered prompt would, so the entry is the practice's entry and the slot is done.
- **The chat side does not change.** A delivery creates or updates a record exactly as today. Everything that reads
  the chat (capture matching, the sweep, folding, thread boundaries, the `/x` windows) keeps reading delivered
  records only (`message_id`). The one exception, in the conversation writer, is rule 10.

A face-made record looks like a `/j` note does today: `message_id: None`, `sent_at` the time of his first words (so
the many readers that need a time keep working), plus one new field, `opened_via` (`web`, `cli` or `mcp`). Its
opener is `compose.plain_opener`, and it carries the practice's question snapshot as a delivered prompt does.

### Slot states

Every scheduled slot of the day has one state, from the schedule and the ledger together (one read module, used by
Today, `journal today`, `status`, MCP and the today block):

| State | When |
| --- | --- |
| upcoming | its time has not come and it has no record |
| due | its time has come, it is inside its window, and it has no record, or only the record a failed send leaves (`send_attempts`, no `message_id`, no captures). A chat install shows this until the tick sends it, or while a send keeps failing; a no-chat install shows it as the open prompt |
| open | a delivered record, unanswered, inside its window (as today) |
| answered | a record with captures, made by any face |
| missed | its window is over, unanswered, with or without a record |

### One slot resolution for every face

Which slot words go to, and which slot `/start` delivers, comes from one function (`slots.resolve(cfg, practice,
at)`). `at` is the time on his words (the web app lets him backdate them). As in WP10, states are read at the
server's clock, and a delivered prompt is answered only when it was sent at or before `at`. The steps, in order:

A practice can be done at any time (the owner, 2026-11-09: "I can select a specific practice and just do it
anytime"). Doing it fills the slot it belongs to when one fits, and otherwise makes an extra entry of that practice.
It never falls back to a loose `/j` note.

Steps 2–4 work on the **journal day** of `at`: its local date, except that words written before `day_ends_at`
(a new setting, default `04:00`) belong to the day before. So a 21:00 evening with a two-hour window, missed by
00:30, takes that answer from step 2, and tonight's evening is left alone.

A deliberate delivery (`/start <id>`, `journal send <id>`, *Send to chat*) made before `day_ends_at` is the
exception: its slot is of today's calendar date, not the previous journal day (the owner, 2026-11-09). The same steps
run over today's slots only, so `/start morning` at 03:30 delivers today's morning (its 06:00 slot, early), and the
tick's 06:00 send is then done. Words he writes at 03:30 still go to the journal day before. The exception has its
own limit: when the practice's nearest scheduled slot is last night's, the delivery resolves as words do. `/start
evening` at 00:30 means last night's 23:00 evening, 1.5 hours behind, not tonight's, 22.5 hours ahead: it sends last
night's again while that is unanswered, and makes an extra entry of last night once it is answered. Tonight's send
is left alone. A tie, and a practice with no schedule, are today's.

1. A slot of that practice that is **due or open** now, today's or yesterday's, newest first, whose time is at or
   before `at`. An evening answer at 00:30 takes last night's slot.
2. Else the journal day's newest slot that is **missed and unanswered**, whose time is at or before `at`. The chat's fold
   already says "missed — reply anytime".
3. Else the **next slot later the same journal day** that has no record, or only the record a failed send left (no
   delivery, no captures): doing it early fills that slot, and its
   scheduled send is skipped. A slot that has already been delivered is not taken here, since WP10 refuses a prompt
   sent after `at`.
4. Else **an extra entry of that practice** for the journal day: slot 1's id when the practice has no schedule and that
   id is free, otherwise the next free number after the scheduled slots (`<date>-<id>-<n>`, the numbering
   `capture.entry_stem` already uses for a second entry).

Adding more words to an entry already written goes through that entry (the web app's *Add* on an entry, `POST
…/blocks`; `journal write --entry <stem>` on the CLI), not through the practice. Choosing a practice therefore always
starts something: a slot or a fresh entry.

Words dated before the previous journal day go straight to step 4. `serve/writes._at` accepts `at` up to 31 days back, and filling
an old slot would turn an old miss into an answer in the week strip and in the opener's missed count. When `answer`
writes into the record a failed send left, it fills in what that record lacks: the plain opener, the question
snapshot and the stem.

## The rules

1. **The schedule is unchanged on a chat install.** A slot is done when delivered, out of attempts or backfilled (as
   today), **or answered on any face**. A failed send keeps retrying within `grace_hours` and alerts as before.
2. **Before a scheduled send, a fresh check under the ledger lock.** The opener's model call can take minutes, and
   he may answer the practice on a face meanwhile. Just before `chat.send`, the scheduled path (`send_due` without
   `only`) reads the record again: if it now has captures, nothing is sent. The check is new (today `send_prompt`
   reads the record again only after the send). It applies to scheduled sends only: a deliberate `/start` or *Send
   to chat* of an answered record is what rule 3 is for. The pack is saved after this check, so a skipped send
   leaves no pack behind.
3. **A delivery never overwrites a face-made record.** A deliberate delivery (`/start`, *Send to chat*, `journal
   send`) takes its slot from `slots.resolve`, which never returns an answered record: step 4 makes an extra entry.
   So a face-made record only meets a delivery in the race of rule 2, when the scheduled send has already gone out
   with the tick's opener after he answered on a face. Then the record keeps its own fields (opener, questions, stem,
   captures) and gains the delivery's: `message_id`, `sent_at` (the delivery time, so a thread boundary is never
   dated in the past), `sent_at_local`, `chat_id`, `platform`, and **`sent_opener`**, `sent_html` and
   `sent_plain_text`, which hold what the chat message actually says. Readers of the chat message use those:
   `tick._fold_edit` (through a `capture.sent_opener(record)` that falls back to `opener`) and the sweep's quote
   matching (`sweep.prompt_texts`). The entry keeps `opener`. So the fold never rewrites the message to words he did
   not see there. The race also sets `needs_edit`, so the next tick folds the message to "answered" rather than
   leaving its questions showing for `collapse_hours`. (`capture._web_record` leaves `needs_edit` off on a record
   with no `message_id`.) Today's `q.update(record)` would overwrite the record's fields, so this replaces it for
   such records.
4. **Chat replies match delivered prompts only, as today.** A `direct-reply` is matched by the message he replied to
   (`ledger.by_message_id`), whatever state the prompt is in, so a late reply to an answered or expired prompt is
   still kept. A `next-message` capture reads `open_prompts`, which requires `message_id`, with the window measured
   from the delivery. A face-made record has no message, so neither rule can catch his chat words for it.
5. **The opener off the chat is the plain one.** It needs no model and no spawn, and serve still calls no model
   (Decision 6) *(choice A)*.
6. **No channel, no attempts.** With `channel.platform: none` the schedule writes nothing and sends nothing; slots
   go *upcoming → due → missed* by derivation. Nothing that sends is attempted, counted or alerted. That covers
   annotation replies, reflection sends, program summaries (`programs._send_summary`, `_retry_summary`), the
   program notes `programs._say_about` and `cli._notify`, and the `status` rows built on them
   (`pending_annotation_replies`, `undelivered`). With a channel configured, all of it behaves exactly as today.
   An annotation reply or a reflection that would have been sent is marked skipped, `no chat`, so a chat configured
   later never sends it, as a program's summary is (`summary_unsent: "no chat"`); instead `standing` tells the
   companion how many were written while there was no chat since the last delivered prompt, in counts, never their
   words (the owner, 2026-11-09).
7. **Privacy.** Saving words on a face writes nothing to `sent.jsonl`, `send-times.json` or the thread stamps
   (`today.note_send`, `last_send`), so it never ends a bare `/x` window or starts a thread. `conversations._send_times`
   and `privacy.send_times` keep their own reading (delivered records and every `sent.jsonl` line) and do not move
   to the new read module. The words themselves pass the same refusals as every write of his: `/x` at the start or
   on any line, the engine's `%% journal … %%` lines, and the transcript placeholder. All three live in
   `actions.answer` and `actions.note`, so no face has to repeat them (today each face holds its own copies:
   `serve/writes._his_words`, `mcp/handlers._not_off_record`, `_no_marker`, `_no_placeholder`).
8. **The companion learns from the record.** Nothing is mirrored into her session for words saved on a face (as
   for web answers today). She sees them where she already reads the ledger and the entries: the pack, `standing`,
   and the today block at the next thread start. The today block says where: a delivered prompt waiting in the
   chat reads as today, and a slot answered on a face reads "answered at 05:40 in the web app". A slot that is
   due with no chat message is never listed as "awaiting a reply" in the chat, so she does not invite an answer
   the chat would not file.
9. **Annotations.** `tick.annotate` aligns her annotations with her first chat reply after the first capture
   (`context.companion_reply_after`). For words saved on a face (no `platform_message_id` on the first capture)
   that reply is unrelated chat: no alignment text is read.
10. **The conversation writer.** `conversations._Saved` treats a capture with no `platform_message_id` as a legacy
   chat capture, and matches chat messages against its words (80% of a block, or an 80-character passage). A chat
   message that repeats words he wrote on a face is then filed as "already captured" in that entry, and its thread
   follows it there. It is not a leak: off-the-record rows are excluded before that point. It already happens to web
   replies today. Fix: captures that carry `via` are not legacy chat captures.
11. **PRIV2 stays as it is.** A scheduled ritual sent during a bare `/x` window still ends it (the owner kept that
    on 2026-10-20). This design does not touch it.
12. **A nudge is not a delivery.** A push notification (below) tells him a practice is due. It writes no prompt
    record and nothing in `sent.jsonl`, `send-times.json` or the thread stamps, so it is never a chat boundary and
    never ends a `/x` window. That is why push is not a `Channel`: a channel's send makes a delivered record, which
    the chat-side readers treat as a boundary.

## What each face gets

| Action | Chat | Web app | CLI | MCP |
| --- | --- | --- | --- | --- |
| See a practice | `/start <id>` delivers it | **Write now** on Today (a view) | `journal open <id>` (prints it) | `journal_start` returns it |
| Deliver it to the chat | `/start <id>` | **Send to chat** (as today) | `journal send <id>` (as today) | no *(choice C)* |
| Answer it | reply | Reply / Write now | `journal write <id>` (`$EDITOR`, or stdin) | `journal_save` with `practice` |
| A note | `/j` | as today | `journal write` | `journal_save` |
| A program phase or interrupt | reply, `/next` … | phases as today; with no chat too, later | later | later |

The writes sit in one engine module (`journal/actions.py`): `answer` and `note`, with the refusals of rule 7, and
`deliver`, which is the spawn. Serve, the CLI and MCP call it instead of `capture.capture_web`. `/start` and
`journal send` take their slot from `slots.resolve` too, which changes two things in the chat. `/start water` at 15:00
delivers the 12:00 slot rather than always slot 1. And `/start morning` after the morning is answered starts an
extra entry, where today it re-sends and appends to the same one. MCP keeps importing `serve.api` and `serve.document` for its reads
until a reading module exists; that is a later package.

## Decided (the owner, 2026-11-09)

- **A. The off-chat opener is the plain one.** No model and no wait, so serve's rules stay intact (rule 5).
- **B. Push notifications are in scope:** Web Push to the installed PWA (*Push notifications*, below; package
  OPEN-PUSH).
- **C. MCP shows and answers practices and never posts into the chat.** Saving words to a practice through MCP fills
  that practice's slot like any other face (`slots.resolve`).
- **D. A practice can be done at any time.** It fills the slot it belongs to, or makes an extra entry of that practice
  (`slots.resolve`, step 4). There is no lead-time limit and no `/j` fallback.
- **E. A no-chat install says so explicitly** with `channel.platform: none`. An empty chat id on another platform
  still fails and alerts, so a live install that loses its chat id is noticed.

## Push notifications

Web Push to the PWA, so a practice can reach him with no chat at all. It works on a phone once the PWA is installed:
on iOS that means added to the home screen, iOS 16.4 or later, with permission asked from a tap.

- **Serve sends them, not the tick.** The tick runs in the companion's Python (the cron stub uses `sys.executable`),
  and Web Push needs elliptic-curve and AES-GCM cryptography that the standard library does not have. `journal
  serve` is installed on its own (`uv tool install`), already serves the PWA, and runs on every install that has a
  web app to open, so it owns push. The core stays stdlib, and push needs no gateway restart.
- **Dependency.** An optional extra, `journal[push]` = `cryptography`, like `journal[mcp]`. A small module implements
  RFC 8291 (`aes128gcm` payload encryption) and RFC 8292 (VAPID), tested against the RFCs' vectors. That beats
  `pywebpush`, which adds `requests`, `http-ece` and `py-vapid` on top. Without the extra, Settings says push is
  unavailable and why.
- **Keys and subscriptions.** The VAPID key pair is generated on the first start and kept in `serve-push.json`
  (0600, beside `serve-auth.json`). Subscriptions come from the PWA: `POST /api/v1/push/subscriptions` with the
  browser's endpoint and keys, and `DELETE` to stop. They are stored in the same file, one per device. A push
  service answering 404 or 410 removes that subscription.
- **When.** A background thread in serve runs once a minute. A slot is pushed once (`push-sent.json` keeps the
  slot ids, pruned with the ledger) when its scheduled time has come, it is still within `grace_hours`, and it is
  not answered. Eligibility comes from the schedule and that marker, never from catching the slot in the *due*
  state, which a tick delivering it a moment earlier would hide. The setting `push.when` decides which slots: with
  `no-chat` (the default) only when `channel.platform` is `none`; with `always`, delivered ones too. Program phases
  and interrupts are pushed once OPEN-PROGRAMS lands.
- **What a push says.** The practice's title and "due now", and nothing else: never his words, never an opener,
  never anything read from an entry. The payload is encrypted end to end, but the browser vendor's push service
  (Apple, Google or Mozilla) still sees the endpoint, the time and the size. Tapping the notification opens Today
  with that practice ready to write (`/today?write=<id>`).
- **Fences.** The push thread reads the ledger, the library and the config, which is what serve already reads. It
  reads no chat, calls no model and never takes `tick.lock`. Its outbound calls go to the push services only. The
  route walk (`test_serve_privacy.py`) gets the new routes.

## How it would be built

Each step is one package and one PR. OPEN-CORE, OPEN-FACES and OPEN-PROGRAMS touch gateway-loaded modules, so each
goes out in a restart window ([DEPLOY.md](../DEPLOY.md)). OPEN-PUSH is serve side only.

1. **OPEN-CORE.** Add the slot-state read module and `slots.resolve`, with the journal day (`day_ends_at`). Add the new done rule, the pre-send check and
   the attach-only delivery (rules 1–3), and `channel.platform: none` with its quiet paths (rule 6). Add
   `actions.answer` and `note` with the refusals (rule 7). Move the readers in the map below to the slot states:
   Today's rows, `status`, `standing`, the today block's lines (rule 8), the week strip and insights in `serve/api`,
   and `prompts.missed_note` and `context.missed_openers`, which assume a delivered prompt. Add the annotation
   alignment skip (rule 9) and the conversation writer's `via` exclusion (rule 10).
2. **OPEN-FACES.** Serve: an answer route over `actions.answer`, and *Write now* on Today (the web build goes
   through CI). MCP: `journal_start` returns the practice, and `journal_save` goes through `actions`. CLI:
   `journal open` and `journal write`.
3. **OPEN-PUSH.** The `journal[push]` extra and its RFC 8291/8292 module; keys and subscription routes; the
   serve thread and `push.when`; the PWA's subscribe in Settings, the service worker's `push` and
   `notificationclick` handlers, and `/today?write=`. Serve side only: `uv tool install` with the extra, and a
   restart of the serve unit.
4. **OPEN-PROGRAMS.** Programs with no chat: a phase becomes current with no delivery, and interrupts can be
   answered on a face. The constraints the review found:
   - `send_due` today means "not delivered"; it must come to mean "owed a delivery".
   - `serve/writes._phase_target` must stop reading `send_due` as "not delivered".
   - The `/x` hold at the top of `_send_phase` must cover the delivery only, or web-driven runs stall while the chat
     is off the record.
   - An undelivered phase record still needs `sent_at`, since `capture.sent_by`, `superseded` and `interrupted`
     read it unconditionally.
   - A `reflect` phase's construction stays in the spawned `journal program …` process.

## The map

Where the prompt record is read or written, what the code assumes about delivery today, and what changes.

| Area | Code | Assumes today | Change |
| --- | --- | --- | --- |
| Due slots | `tick.send_due` (`done`), `schedule.slots_due` | Done = delivered, out of attempts, or backfilled. | Also done when answered on a face (rule 1). |
| Delivery | `tick.send_prompt`, `count_send_failure` | Every scheduled prompt goes to `cfg.channel()`; the record is read again only after the send. | A check before the send (rule 2), attach-only onto a face-made record (rule 3), nothing with no channel (rule 6). |
| Matching chat replies | `capture.open_prompts`, `match`, `window` | Open = delivered, inside the window from `sent_at`. | Unchanged (rule 4). |
| Words from a face | `capture.capture_web` (web, MCP) | Answers a delivered open prompt, else a `/j` note. | `actions.answer` over `slots.resolve`. |
| Notes | `capture.new_note_prompt`, `capture_note` | A note is a record with `message_id: None` and `sent_at`. | Unchanged. |
| Today | `today._practices`, `context.prompt_state` | Lists delivered records; slots with none are upcoming or absent. | The slot states. |
| Today block lines | `today.lines_of` | "sent 06:00, no reply yet", "open prompt awaiting a reply". | Says where (rule 8). |
| Status | `status.status` | Visible = delivered; undelivered = attempts and no `message_id`. | Slot states; *undelivered* and reply rows only with a channel. |
| The opener's signals | `standing.unanswered`, `prompts.missed_note`, `context.missed_openers`, `_opener_key`, `her_messages` | Read delivered prompts; `missed_note` indexes `sent_at`. | `unanswered` counts slots he saw: delivered, or answered on a face. A no-chat install's derived misses are not "his last N prompts got no reply". `her_messages` stays delivered-only (what she said in the chat). |
| Web history | `serve/api._sent_prompts`, `_prompt_outcome`, `week_strip`, `insights_weeks` | Delivered prompts only, so a no-chat install shows nothing. | Slot states. |
| Entry header | `entries._prompt_md`, MCP's prompt time | The time comes from the delivery. | A face-made record says when and where he wrote it. |
| Folding | `tick.fold` | Skips records with no `message_id`. | Unchanged. |
| Re-send | `tick.send_prompt` (`previous_message_ids`, `earlier_openers`) | A second send of a delivered id makes the new message current. | Unchanged for delivered records; attach for face-made ones (rule 3). |
| Mirror | `mirror.to_chat` from `send_prompt` | Every delivery is mirrored. | Unchanged; faces mirror nothing (rule 8). |
| Thread boundaries, `/x` | `conversations._send_times`, `privacy.send_times`, `today.note_send` | Delivered records plus every `sent.jsonl` line. | Unchanged, and pinned (rule 7). |
| Sweep, recovery | `sweep`, `recovery` | Chat only. | Unchanged. |
| Conversation writer | `conversations._Saved`, `classify` | A capture with no `platform_message_id` is a legacy chat capture, matched by its words. | Captures with `via` are not (rule 10). |
| Failed sends | `tick.count_send_failure` | Leaves a record with `send_attempts` and nothing else. | Reads as *due*; `answer` fills it in (slot resolution). |
| Annotations | `tick.annotate`, `context.companion_reply_after` | Need captures (not a message); aligned with her next chat reply. | No alignment for face captures (rule 9). |
| Sends after the fact | `tick.reply_annotations`, `reflect.run`, `programs._send_summary`, `_retry_summary`, `_say_about`, `cli._notify` | Always send, with attempts and alerts. | Nothing with no channel (rule 6). |
| Program phases, interrupts | `programs._send_phase`, `send_interrupts`, `serve/writes._phase_target` | A phase is open only once delivered; interrupts are chat-only. | OPEN-PROGRAMS (package 3). |
| Faces | `serve/writes.library_send`, `mcp/handlers.start`, `commands.start_reply`, `cli send` | "Start" means "send to the chat". | A view on faces, a delivery from the chat; `slots.resolve` for both. |
| Channel config | `Config.channel()`, `ports.NoChannel` | `NoChannel` refuses each send, so each send fails. | `channel.platform: none` (rule 6). |

Not affected: entry files and their format, the ledger's locking, `/j` notes, block edits, the privacy pass itself.

## Tests to add or keep

- **Chat installs unchanged.** A failed scheduled send still retries within `grace_hours` and alerts after
  `SEND_ATTEMPTS`. A face answer before the slot stops the send. A face answer made during the opener's model call
  stops the send (rule 2). A send that loses that race attaches without overwriting (rule 3).
- **Faces fill the slot.** Words from the web app, the CLI and MCP go into the slot's entry, not a `/j` note, for
  each step of `slots.resolve`: 00:30 evening, a missed morning at 20:00, a second write staying with its slot on a
  multi-slot practice, early, unscheduled, and the record a failed send left (filled in, then off the
  *undelivered* list). Backdated words before a delivered prompt never answer it.
- **Any time:** choosing a practice again after answering a slot fills the next slot that fits (after a 09:00 answer,
  choosing it at 10:00 fills the unrecorded 18:00 slot and skips its scheduled send); only when no slot fits does it
  make an extra entry (`-<n>`), never a `/j` note. Words dated before the previous journal day make an extra entry on
  that day and fill no old slot. A failed send's record still takes words through step 3.
- **Push:** RFC 8291/8292 test vectors; a slot is pushed once, also when the tick delivered it before the thread's
  first look (`push.when: always`); a 410 drops the subscription; `push.when: no-chat`
  pushes nothing while a chat is configured; a push writes nothing to the ledger, `sent.jsonl`, `send-times.json` or
  the thread stamps (rule 12); the payload carries the title only.
- **The lost race:** a scheduled send that goes out after a face answered keeps `opener` and gains `sent_opener`,
  `sent_html` and `sent_plain_text`; the next tick folds the message to "answered" with the sent opener; the sweep
  matches a quote of the sent text. A deliberate `/start` after a face answer, when no slot fits, makes an extra
  entry, never touching the answered record.
- **Overnight:** at 00:30 a missed 21:00 evening (two-hour window) takes the answer, and tonight's slot is untouched;
  at 15:00 the evening fills tonight's slot early. `day_ends_at` moves the boundary.
- **Late direct replies:** a reply to an answered or expired delivered prompt is still captured by message id.
- **The conversation writer** does not file a chat message into an entry because it repeats words written on a
  face (rule 10).
- **Privacy** (beside `test_privacy_seams.py`): a face answer writes nothing to `sent.jsonl`, `send-times.json` or
  `last_send`; it does not end a bare `/x` window, start a thread, or take his next chat message; a later delivery's
  boundary is its delivery time. `actions` refuses `/x` (start and any line), marker lines and the placeholder for
  every face.
- **No chat:** with `channel.platform: none`, a tick across a whole day writes no records, sends nothing, counts
  nothing, and exits 0. Slots read upcoming, due and missed; annotations are written and no reply is attempted.
- **Old records** (delivered, failed, backfilled, notes, phases) read the same through the slot states: a fixture
  of each.
- **The today block** never lists a slot as awaiting a reply in the chat unless it was delivered.
  `tests/fixtures/today.json` changes with the new lines.
- **Serve:** the route walk (`test_serve_privacy.py`) gets a sample for each new route. The goldens stay byte for
  byte, since a delivered prompt's text does not change.
- **Engine only:** with `hermes/`, `serve/` and `mcp/` removed from a copy of the package, the engine imports, and
  `open`, `write`, `today` and `tick` run. That needs `status` no longer to import `serve`, and
  `agent.is_failure_text` no longer to import the Hermes adapter (both found by the 2026-11-04 audit).

## Guarantees and decisions touched

- **README *Guarantees*:** none weakened. *Your words are append-only for the engine* and *`/x` holds on every
  surface* are untouched, and rule 7 adds tests for them. *Works with no model* gets wider: the whole practice loop
  runs with no model and no chat.
- **Decision 6** (no model calls from the UI): kept, by rule 5.
- **CHANNELS (a), one channel at a time:** becomes *none or one*. #9's home channel is the same slot.
- **MCP — `journal_start` sends practices only:** replaced by *returns the practice to answer* (choice C).
- **WP10, backdated words:** kept inside `slots.resolve`: states at the server's clock, no delivered prompt sent
  after `at`. New: words dated before yesterday make an extra entry on that day, never fill a slot.
- **PRIV2:** unchanged (rule 11).
- **PRACTICES.md decision 9** (the companion never advances a program): unchanged.

## Rollback

Old code reads a face-made record as an undelivered prompt with captures. It sends that prompt once more if the
rollback lands while the slot is still inside `grace_hours`: one chat message he has already answered. His words
and the entry stay unchanged, but the record's opener becomes the newly sent one. Two things are not that small:

- **A no-chat install:** old code reads `channel.platform: none` as a platform named "none" with no chat, so every
  slot, reflection and summary fails and alerts. Before rolling such an install back, set its channel to a real one
  or stop its tick.
- **Programs (package 3):** a phase made current without delivery is not delivered by old code. The package's
  rollback note must say how to deliver it again (`journal program next` or a resend).

## Review

An independent Opus review of the first draft (2026-11-09) found no off-the-record leak: everything that reads the
chat requires `message_id`. It found three defects in that draft:

- **Opened counted as done.** It would have stopped failed sends from retrying on every chat install.
- **The chat window measured from `opened_at`.** It would have dropped his chat replies after a late `/start`.
- **The today block.** It would have told the companion a prompt opened on a face was waiting in the chat.

It also found:

- **The delivery overwrote what was opened.** A delivery onto an existing record replaced its opener, questions
  and stem, and the race check was not atomic.
- **Slot choice was undefined** for backdated words, missed prompts, multi-slot practices and answers after midnight.
- **Gaps in the map:** two readers would crash, the web history read delivered prompts only, and program summaries
  still sent with no chat.
- **The rollback note was too narrow.**

It proposed the lazy model this version uses: a record only from a delivery or his first words, with slot states
derived from the schedule. That removed the opened-but-unanswered state, along with the old choice B ("skip the chat
at slot time") and most of the race. The rules above are the fixes. Its findings on programs became the constraints
listed under OPEN-PROGRAMS.

A second pass over this version found the main fixes holding: retries, the chat window, the today block and the
privacy boundary. It also found six more problems:

- **A failed send's record** fit no slot state.
- **A lost send race** would have had the fold rewrite the chat message to an opener he never saw.
- **`slots.resolve`** fell through without saying where, and could fill slots weeks old.
- **The conversation writer's legacy matching** is the one chat-side reader that sees face captures.
- **The pre-send check** would also have blocked a deliberate send.
- **A second write on a multi-slot practice** would have used up the next slot.

The slot table, the resolution steps, and rules 2, 3 and 10 carry those fixes.
