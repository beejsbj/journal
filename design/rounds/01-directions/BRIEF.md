# Round 01 brief: three directions for the journal's web app

## The product
`journal` is a private journaling engine. A companion (an AI, called "Ada" in fixtures) sends prompts on a schedule
(morning journal, evening journal, daily gratitude, weekly pattern scan, multi-session "programs"); the owner answers in
his own words, by text or voice. His words are captured verbatim into Markdown entries; the companion's words (her
prompt, her questions, short notes in the margin, an analysis of an entry, daily reflections, weekly reviews) are kept
apart and are never editable. The web app (Preact PWA, served privately) is where he reads his entries, writes, starts
practices and programs, sees the week, and changes settings. One person uses it, on phone and desktop. It must feel like
a writing app he wants to open, not an admin console.

Read for content and states: `docs/FRONTEND.md` (§1–2, §5), `web/src/routes/*.tsx`, `web/src/components/Document.tsx`,
`tests/fixtures/api/*.json` (realistic payloads: entries, an entry document, today, library, insights, settings).
Baseline as built: the screenshots of the web app before this pass (not kept in the repository).

## The owner's verdict (paraphrased; his exact words are in the deployment's private notes)
The app felt dull, like a dashboard served by a command-line tool; he wanted a proper UI and UX feel. Settings looked
ugly. The writing should be immersive and inviting (it is a writing app). Insights' text could be placed more
interestingly, and the analysis presented better. Reviews and reflections should open gracefully, not collapse and
jump. He wanted it inviting, with good subtle animations, cohesive, interesting and useful layouts, better type and
better page transitions. The "built-in" box, the pills in Entries and the write modal looked amateur. Could the entries
list slide into a sidebar when an entry is opened? And: show what a strong visual and UX design can do.

Diagnosis of the baseline (ours): four competing families; letter-spaced typewriter capitals on every label (the CLI
feel); inconsistent page alignment; native audio/select/time controls; Edit/Delete as bare text under every block;
settings is a 40-field form with DEFAULT stamps and a raw health table; filter pills with emoji; a bottom-sheet write
modal with a Back link; disclosure that snaps.

## Invariants every direction keeps
- Two voices: his words and hers are unmistakably different on every screen (face, ink, placement). Her ink is the only
  hue in the app and is hers; the reader picks it from 4 inks (blue-black default, brown, violet, green), AA for body
  text on every surface in light AND dark. His actions are not in her ink.
- No streaks, flames, confetti, scores or gamification. Calm.
- Phone (390 px) and desktop (1280 px) are both first-class. Touch targets >= 44 px. Light and dark from one token set.
- Motion is subtle and purposeful; `prefers-reduced-motion` turns it into instant or a plain fade.
- Faces must be SIL OFL (they will be self-hosted). In the prototype load them from Google Fonts.
- Content is neutral fixture content (lighthouse, harbour, gulls; Ada). Nothing personal.

## What each prototype must show (one self-contained HTML file, interactive, < 450 KB)
1. **Entries → entry (key).** The entries list (grouped by day/week, filters that are NOT a row of pills: find a better
   device). Opening an entry on desktop slides the list into a narrow sidebar and the entry opens beside it; going back
   reverses it. On phone, a push transition. The entry: his blocks (one with a custom audio player), her prompt and her
   questions (24 of them in the evening practice: show them well, folded or not), her margin notes, block actions that
   appear on hover / long-press (not bare text), an Analysis and a Reflection that open and close smoothly (animated
   height, no jump; content that settles).
2. **Write (key).** The immersive writing surface, opened from a Write button and from a prompt: a full writing room,
   not a modal sheet. Her prompt above as her voice, his text large and calm, autosave state, a voice-record control,
   a subtle count, a focus mode. The open/close transition matters.
3. **Insights.** The week: figures and her reflections/review placed interestingly (an editorial composition, not a
   stat row + a list).
4. **Settings.** Grouped and human: section navigation, toggles/steppers/segmented controls instead of raw number
   fields, defaults shown as quiet hints not stamps, the ink picker as a joy, the health check as a calm status summary
   that expands.
5. **Library row/card.** How built-in vs his own practices are told apart without an "amateur box".
6. **Navigation + page transitions** between these (a tiny router in the prototype is fine).
Light and dark (a toggle in the prototype plus `prefers-color-scheme`), the ink picker live (changes the page).

Use a phone/desktop toggle only if helpful; the page must work responsive at real widths 390 and 1280.

## Deliverables
- `design/rounds/01-directions/<ID>/index.html`
- `design/rounds/01-directions/<ID>/notes.md`: the direction in one sentence; the type system (families, roles, scale);
  colour roles light/dark with the 4 inks; spacing, radius, elevation; motion tokens (durations, easings, which
  transitions); component inventory with anatomy notes; the signature moves; tradeoffs and risks (incl. implementation
  cost in Preact + plain CSS, no new runtime deps unless argued).
- Screenshots if you can render: use the `mcp__t3-code__html_preview` tool (width 390 and 1280, light and dark) to
  check your work and iterate. Report what you saw. If that tool is not available to you, say so.

Write only inside your own `design/rounds/01-directions/<ID>/` directory. Do not edit the app, do not commit, do not run
node/npm/vite on this host. Hold the creative floor: no generic SaaS dashboard look, no stock card grids, no emoji
labels, no decorative gradients without a reason. Make it something a person who writes would love to open.
