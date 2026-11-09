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
- **The chat side does not change.** A delivery creates or updates a record exactly as today, and everything that
  reads the chat (capture matching, the sweep, folding, thread boundaries, the `/x` windows) keeps reading delivered
  records only (`message_id`).

A face-made record looks like a `/j` note does today: `message_id: None`, `sent_at` the time of his first words (so
the many readers that need a time keep working), plus one new field, `opened_via` (`web`, `cli` or `mcp`). Its
opener is `compose.plain_opener`, and it carries the practice's question snapshot as a delivered prompt does.

### Slot states

Every scheduled slot of the day has one state, from the schedule and the ledger together (one read module, used by
Today, `journal today`, `status`, MCP and the today block):

| State | When |
| --- | --- |
| upcoming | its time has not come and it has no record |
| due | its time has come, it is inside its window, it has no record (a chat install shows this until the tick sends it, or while a send keeps failing; a no-chat install shows it as the open prompt) |
| open | a delivered record, unanswered, inside its window (as today) |
| answered | a record with captures, made by any face |
| missed | its window is over, unanswered, with or without a record |

### One slot resolution for every face

Which slot words go to, and which slot `/start` delivers, comes from one function (`slots.resolve(cfg, practice,
at)`). Every step is evaluated at `at` (the time on his words, which the web app lets him backdate), not at the
server's clock:

1. A slot of that practice that is **due or open** at `at`, today's or yesterday's, newest first. An evening answer
   at 00:30 takes last night's slot.
2. Else today's newest slot that is **missed and unanswered**. The chat's fold already says "missed — reply anytime".
3. Else the **next slot later the same local day**: answering early *(choice D)*.
4. Else today's newest **answered** record of that practice: the words are appended to it, as a reply after a
   `/start` re-send is today.
5. A practice with no schedule uses slot 1 of the day, as `/start` does today.

A delivered prompt is answered only when it was sent at or before `at` (the WP10 rule for backdated words, kept).

## The rules

1. **The schedule is unchanged on a chat install.** A slot is done when delivered, out of attempts or backfilled (as
   today), **or answered on any face**. A failed send keeps retrying within `grace_hours` and alerts as before.
2. **Before a scheduled send, a fresh check under the ledger lock.** The opener's model call can take minutes, and
   he may answer the practice on a face meanwhile. Just before `chat.send`, `send_prompt` reads the record again: if
   it now has captures, nothing is sent. That check is new: today `send_prompt` reads the record again only after
   the send. The pack is saved after this check, so a skipped send leaves no pack behind.
3. **Delivering onto a face-made record attaches, never overwrites.** When a delivery meets a record that has no
   `message_id` (a `/start` after he answered on the web, or a send that lost the race in rule 2), it writes the
   delivery fields only: `message_id`, `sent_at` (the delivery time, so a thread boundary is never dated in the
   past), `sent_at_local`, `chat_id`, `platform`. It sends the record's own opener, so the chat and the entry show
   the same words, and keeps the opener, questions, stem and captures. Today's `q.update(record)` would overwrite
   all of them. When the race in rule 2 is lost after the check, the chat gets a prompt he has already answered,
   which the fold then closes as answered.
4. **Chat replies match delivered prompts only.** `direct-reply` and `next-message` keep reading `open_prompts`,
   which requires `message_id`, with the window measured from the delivery. Words saved on a face never make a
   prompt that catches his next chat message.
5. **The opener off the chat is the plain one.** It needs no model and no spawn, and serve still calls no model
   (Decision 6) *(choice A)*.
6. **No channel, no attempts.** With `channel.platform: none` the schedule writes nothing and sends nothing; slots
   go *upcoming → due → missed* by derivation. Nothing that sends is attempted, counted or alerted. That covers
   annotation replies, reflection sends, program summaries (`programs._send_summary`, `_retry_summary`), the
   program notes `programs._say_about` and `cli._notify`, and the `status` rows built on them
   (`pending_annotation_replies`, `undelivered`). With a channel configured, all of it behaves exactly as today.
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
10. **PRIV2 stays as it is.** A scheduled ritual sent during a bare `/x` window still ends it (the owner kept that
    on 2026-10-20). This design does not touch it.
11. **Nudging without a chat** is a later channel: a send-only push (ntfy, Web Push) fits the `Channel` port with
    `Caps()` and needs nothing from this design *(choice B)*.

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
`journal send` take their slot from `slots.resolve` too, which changes one thing in the chat: `/start water` at 15:00
delivers the 12:00 slot rather than always slot 1. MCP keeps importing `serve.api` and `serve.document` for its reads
until a reading module exists; that is a later package.

## Choices for the owner

- **A. The off-chat opener.** Plain (recommended: no wait, no model, serve's rules intact); or the companion's,
  through a spawned process, which keeps him waiting up to `opener_budget_seconds` before he can write.
- **B. Nudging on a no-chat install.** None until a push channel exists (recommended: a separate package), or build
  the push channel together with this one.
- **C. MCP and the chat.** MCP shows and answers only (recommended: a model acting for him does not post into his
  chat), or it can also deliver, with the `/x` hold it has today. Either way, `journal_save` with a practice now
  fills that day's slot: a client that saves words as the morning practice uses up the morning prompt.
- **D. How early he can answer.** Any time earlier the same local day (recommended: simple, matches "I'll do my
  evening now"); or only within a lead time before the slot (say two hours), with anything earlier a `/j` note.
- **E. An install with no chat configured.** Keep needing an explicit `channel.platform: none` (recommended: a live
  install that loses its chat id should alert, not go quiet); or treat an empty chat id as `none`, with only a
  doctor warning.

## How it would be built

Each step is one package and one PR. All but the web build touch gateway-loaded modules, so each one goes out in a
restart window ([DEPLOY.md](../DEPLOY.md)).

1. **OPEN-CORE.** Add the slot-state read module and `slots.resolve`. Add the new done rule, the pre-send check and
   the attach-only delivery (rules 1–3), and `channel.platform: none` with its quiet paths (rule 6). Add
   `actions.answer` and `note` with the refusals (rule 7). Move the readers in the map below to the slot states:
   Today's rows, `status`, `standing`, the today block's lines (rule 8), the week strip and insights in `serve/api`,
   and `prompts.missed_note` and `context.missed_openers`, which assume a delivered prompt. Add the annotation
   alignment skip (rule 9).
2. **OPEN-FACES.** Serve: an answer route over `actions.answer`, and *Write now* on Today (the web build goes
   through CI). MCP: `journal_start` returns the practice, and `journal_save` goes through `actions`. CLI:
   `journal open` and `journal write`.
3. **OPEN-PROGRAMS.** Programs with no chat: a phase becomes current with no delivery, and interrupts can be
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
  each step of `slots.resolve`: 00:30 evening, a missed morning at 20:00, early, a second answer, unscheduled,
  multi-slot, and backdated `at`.
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
- **WP10, backdated words:** kept inside `slots.resolve`.
- **PRIV2:** unchanged (rule 10).
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
