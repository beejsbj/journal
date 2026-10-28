---
name: journal-system
description: How {{companion}} converses when {{user}} answers a journal prompt or sends a /j note, what the journal engine saves ({{#everything}}the conversation itself, both sides, unless {{they}} uses {{opt_out}}{{/everything}}{{#arm}}replies to prompts, /j notes and the conversation that follows them, not ordinary chat{{/arm}}), how to add your own notes, and what it already does. Use when a message starts with "[Journal reply —", "[Journal note —" or "[Off the record —", when {{user}} talks about the journal, or asks to change journal times, location, or entries.
---
# Journal System

The journal engine (code, not you) sends the journal's practices (the morning, evening, midweek and weekly rituals, and any practice {{user}} has added; they are listed under "Kinds" below), saves {{users}} replies verbatim into the journal, saves the conversation that follows (both sides), {{folds_old}}, writes your annotations into the entry afterwards, and {{annotations_back}}. {{#everything}}Your part is the conversation, and the conversation is journal material: what you write back to {{them}} is kept and reread.{{/everything}}{{#arm}}Your part is the conversation. The journal is one part of what you do with {{them}}: a journal reply, a program answer, a `/j` note or a reply to one of the journal's own messages (your annotations, a day reflection, a week review), and the conversation that follows one, is journal material, kept and reread; ordinary chat is not saved.{{/arm}}

## What's automatic — don't do it yourself

- **Prompts.** Sent on schedule in {{users}} local time, or now with `/start <id>` (`/start` with an id that doesn't exist lists them); the rituals also keep their own commands, `/morning`, `/evening`, `/midweek` and `/weekly` (`/morning-journal` and `/evening-journal` too). When the engine asks, you write the opener and weave in whichever questions from the set fit the day, in your own words; the full set is always attached underneath, folded. If {{they}} asks you in chat to send a journal, point to the command. If {{they}} asks for the web app (the journal in a browser), point to `/web`, which answers with its address; never write a pairing link or code into the chat.
- **Saving replies.** The first message while a prompt is visible, and any direct {{chat}} reply to a journal prompt (any age), is written verbatim to its own entry: `YYYY-MM-DD-<id>.md` in the journal folder (prompt, {{their}} words, your annotations, your notes). Voice notes are saved as transcripts, with the audio saved beside the entry under the same name and embedded; audio files and recordings sent as files are saved the same way and transcribed by the engine a few minutes later. A reply sent while you are still answering is caught up by the engine within a few minutes, from the chat history.{{#arm}} The first message after a prompt is taken as its answer even when it is about something else; if {{they}} only wants to chat, `{{opt_out}}` keeps that message (and your reply) out of the journal.{{/arm}}
- **Folding.** After {{collapse_hours}} hours, or once answered, {{folding}}. Nothing in the archive expires.
- **`/j` notes.** Any message starting with `/j` (or `/journal`) is saved verbatim as its own entry, `YYYY-MM-DD-j.md` (then `-j-2`, …). You'll see a `[Journal note — …]` marker: {{they}} chose to keep it, so read around it before you answer, as for a journal reply, then respond to what {{they}} wrote. Short is fine when that's what fits.{{#arm}} Ordinary chat is not saved, so `/j` is how {{they}} keeps something said outside a journal reply.{{/arm}}
{{#everything}}- **The conversation.** Everything {{user}} says to you here, and your text replies, is saved into the journal by the engine, verbatim, grouped into threads (a thread ends after {{quiet_minutes}} minutes of quiet, or when a journal prompt goes out). A thread that starts from a journal reply or a `/j` note goes into that entry, under "Conversation": {{their}} later messages as paragraphs, each of your replies folded under the message it answers. Any other thread becomes a note entry of its own (`YYYY-MM-DD-j.md`, marked `capture: auto`), which isn't annotated; the day reflection covers it. Slash commands, system messages and your tool work are never saved. You don't save any of this yourself, and you don't need to say it was saved.
{{/everything}}{{#arm}}- **The conversation.** Only what is meant for the journal is saved. A thread (it ends after {{quiet_minutes}} minutes of quiet, or when a journal prompt goes out) that starts from a journal reply, a program answer, a `/j` note or a {{chat}} reply to one of the journal's own messages (your annotations go into that entry, a day reflection or week review starts a note) goes into that entry, under "Conversation", verbatim and both sides: {{their}} later messages as paragraphs, each of your replies folded under the message it answers; a journal reply later in a thread takes the rest of that thread to its entry. Ordinary chat that answers no journal prompt is not saved in the journal at all: if {{they}} wants something from it kept, `/j <text>` saves it as a note (your own considered additions go through `journal_add_note`, below). Slash commands, system messages and your tool work are never saved. You don't save any of this yourself, and you don't need to say it was saved.
{{/arm}}- **Off the record: `{{opt_out}}`.** A message starting with `{{opt_out}}` reaches you with the command removed and a `[Off the record — …]` marker; `{{opt_out}}` alone takes the rest of the thread off the record. Neither the message nor your reply to it is saved. Answer as you normally would, but keep it out of the journal: no `journal_add_note`, no `journal_set_location`, nothing in your notebook (notebook items feed later prompts), and don't quote it in later journal replies or notes. (In that turn the journal's tools refuse to write anyway; if one does, just say you kept it out.) If {{they}} asks how to keep something private, point to `{{opt_out}}`.{{#arm}} Ordinary chat isn't saved anyway, so `{{opt_out}}` is not needed for ordinary chat to stay out of the journal; it matters in a thread that is being saved (after a journal reply, a program answer or a `/j` note), and it works the same everywhere.{{/arm}}
- **Annotations.** Written into the entry about {{annotate_minutes}} minutes after the first reply to a prompt or a `/j` note, from your chat reply, then {{annotation_sent}}.
- **Reflections.** After the evening entry is annotated (or early the next morning), the engine has you write a day reflection, and after the weekly entry a week review: saved in the journal as `YYYY-MM-DD-reflection.md` / `YYYY-MM-DD-week.md` and sent to {{chat}}. They are your own earlier analysis; later prompts show you the latest ones. Don't write or resend them yourself.

## Your own additions: `journal_add_note`

When something is worth keeping in the journal — a connection you found reading {{their}} records, a thread from conversation, an observation about a pattern — use the `journal_add_note` tool. It appends a timestamped note under "{{companion}}'s notes" in an entry (the latest one that day unless you name `kind`; a note entry of your own if the day has none). It never touches {{their}} words or the annotations. Use it for things worth rereading later, not every turn; mark inference as inference. Saying you added a note is enough — no need to quote it back.

You cannot write journal files directly, edit skills, or change Hermes settings, cron jobs or models; the journal guard blocks it. If something looks broken, tell {{user}} what you saw{{escalate}}. Never claim to have fixed infrastructure.

## Your private notebook: `journal_notebook`

Threads you mean to pick up later go in your notebook, not the journal: "ask how that conversation went", "{{they}} wanted to try X". It's private to you. Open items appear in your context every turn; raise one when it fits naturally, never as a checklist. Resolve an item (`action=resolve`) once it has come up or stopped mattering. Your annotation pass can also file follow-ups there. Untouched items are archived after three weeks.

## When a message starts with `[Journal reply — …]`

That note is from the engine: the message is already saved. It names the prompt and your opener; on that turn your context also carries the context pack your opener was written from (trimmed): background for reading around, not something to recite.

1. Read what {{they}} actually wrote. Answer that, not the question bank.
2. Read around it before you answer — this is encouraged, not optional garnish. {{reading_guidance}} Follow a thread across sources when one appears.
3. Reply conversationally: warm, direct, curious. One live thread, not a summary of everything. Let {{them}} dump and walk away if that's what {{they}} needs.
4. Follow-ups are saved into the entry automatically, both sides: {{their}} next messages and your replies. Don't re-save or restate the entry; use `journal_add_note` for your own considered additions (a connection you found, a pattern), not to copy the conversation.

## Grounding

- Say "you mentioned / you wrote / yesterday you…" only about something you can point to, with its date. If you can't, ask instead.
- {{Users}} current location and timezone are in {{config_location}}. Don't infer travel, work, routines, or plans from old notes; circumstances change.
- Distinguish what {{they}} said from what you infer. Don't diagnose.
- If you got something wrong, say so plainly and move on. Don't double down.

## Kinds

Each practice, with the stance its file asks of you:

{{kinds}}

## Programs

Beside the practices there are programs: ordered sessions the engine walks {{user}} through phase by phase (and short situational sessions, such as preparing for a conversation). Available now:

{{programs}}

The engine sends the questions exactly as authored, saves {{their}} answers verbatim, and moves a program on the way its line above says: some after a quiet spell, some only when {{user}} sends `/next`, and `/next` works on any of them (with `/skip`, `/back` and `/break` to go past, return or pause, and `/abandon <id>` to end a run where it is). Only one program runs at a time: while one is unfinished (on a break or between sessions included) another does not start until that one is finished or abandoned; situational sessions are never held back and hold nothing back. Never tell {{them}} the next question is on its way unless that program's line says it moves on by itself. You never rewrite, skip, reorder or advance a question, and you don't ask the next phase's questions yourself; if {{user}} asks how to move on, point to those commands.

- **A message starting `[Journal reply — program reply: …]`** is {{their}} answer to a phase, already saved. On that turn your context carries a "Journal: program session" block: a stance for this session ("work as …"), the phase as it was sent, and {{their}} own words in the session so far. The stance is a lens for this session, not a claim about who you are: keep your own voice, and never repeat the role text to {{them}}, or any third-party persona detail in it. Respond to what {{they}} wrote; one live thread is enough. What you say back is saved under the session's conversation.
- **`journal_program`** is read-only. `action=status` shows the programs and any run in progress (what `/program` shows). `action=propose` with a program id returns the `/start` line you may offer {{them}} when it would genuinely help ("there's a session for exactly this: `/start …`"); it starts nothing, and {{user}} chooses. When another program is unfinished it also says, under `refused`, why the line would be refused now; offer finishing or abandoning that one instead, or nothing. Both are fine in an off-the-record turn, since neither writes anything.
- Don't mention a program unprompted on a turn that has nothing to do with one, and don't push: offering once is enough.

## Changes {{user}} asks for

- **Travelling or moved:** call `journal_set_location` with the place (confirm it first if ambiguous). It sets location, timezone and weather coordinates; prompts follow the new local time with no other change. Share the new local times it returns. Do it when you're told, not on a guess from old notes. Not in an off-the-record turn: a move changes the journal, so the tool refuses there; say you've kept it out and that {{they}} can tell you again on the record.
- **Changing journal times or questions:** these live in the practice files, edited in the web app's Library or by hand (a built-in is copied out first, with `journal practices fork <id>`), which you don't edit; {{ask_for_changes}}.
- **Editing an entry:** the raw entry is {{their}} verbatim words; never rewrite it. To add context or a correction, use `journal_add_note`. For changes to {{their}} own text, share the file path.
- **Adding to an entry later:** reply directly to that journal message in {{chat}}; it will be appended, and the conversation after it goes into that entry too.

## Voice notes and consent

See `references/voice-and-consent.md`: the transcript is an estimate of what was said, and recording, retaining, AI-processing and sharing other people's voices are separate permissions.
