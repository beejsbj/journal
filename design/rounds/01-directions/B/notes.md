# B · Night Room

**In one sentence:** a quiet writing room after dark: graphite and ink-blue grounds, panes that float on layered
surfaces, warm light coming off the page he is writing on, and motion that moves things through space (rows grow into
entries, the list folds into a column of dots, Write blooms into a room) rather than swapping screens.

Prototype: `index.html` (one file, no dependencies, ~142 KB). Open a screen directly from the hash:
`#entries`, `#entry` or `#entry/<stem>`, `#write` (from a prompt) / `#write?from=new` / `#write/<stem>` (adding to an
entry), `#insights`, `#library`, `#settings` or `#settings/<group>` (`ink`, `place`, `prompts`, `reflections`, `chat`,
`programs`, `device`, `health`). Params: `theme=light|dark`, `ink=blue|brown|violet|green`, `h=<px>` (fixes the app
height for screenshots), `open=all` (opens every disclosure). Without `theme` it follows `prefers-color-scheme`.
Keyboard: `N` opens the writing room, `Esc` closes the room, a menu, or the entry.

## The rule the whole system hangs on

**The serif means a person wrote it.** Newsreader roman is his words and nothing else; Newsreader italic in her ink is
her words and nothing else. Every piece of the interface (titles, labels, practice names, figures, buttons) is Geist.
A practice he wrote himself shows its title in his serif. So the two-voice invariant is structural, not a
decoration: you can tell who is speaking from the face alone, before colour, stroke or placement add to it. (Two
places where I had slipped her italic onto interface text, the ink-well notes and the "Yours" byline, were found in
review and fixed.)

## Type

| Role | Family | Size / leading (desktop · phone) | Notes |
| --- | --- | --- | --- |
| His words, document | Newsreader roman, `opsz` auto | 21/1.62 · 19/1.6 | Optical size tracks the size, so text is sturdier and display is finer without a second family |
| His words, writing room | Newsreader roman | 24/1.66 · 21/1.62 | Bigger than the document: you write larger than you read |
| His first lines in the list | Newsreader roman | 18/1.45 · 17.5 | Two lines max; notes (`/j`) at 17 in `--text-2` |
| Her voice | Newsreader italic, wght 380, in `--ink` | opener 21 · question 16–22 · margin note 15 · review pull-quote 30 | Always with a label (`Ada · 23:45`) and usually a 2 px ink stroke |
| Page titles | Geist 600, −0.022em | 30 · 30; entry title 34 · 28 | Contemporary, not bookish |
| Editorial lede (Insights) | Geist 300, figures in Geist 500 | 34/1.28 · 24 | Light sans at display size, so figures read as typeset without borrowing his serif |
| UI body / labels | Geist 400–550 | 15 base, 14.5 nav, 13.5 hints, 12.5 meta | Sentence case everywhere. No letter-spaced capitals |
| Numbers | Geist, `tabular-nums` | | Times, counts, steppers |

**Why Newsreader:** it was drawn for on-screen reading, has a real optical-size axis (6–72) and a calm, slightly
calligraphic italic that reads as a different hand without turning decorative. Source Serif 4 is crisper but colder
in italic; Literata's italic is lovely but its roman is heavier at 21 px on a dark ground. **Why Geist:** neutral,
tight, with excellent figures and a contemporary edge (the Linear/Vercel register the brief asks for) that keeps the
serif special. Both are SIL OFL. Self-hosting means two variable files for Newsreader (roman and italic) and one for
Geist, roughly 180 KB woff2 in total, subsettable.

## Colour roles

One token set, two grounds. Her ink is the only hue in the app; his actions use **the lamp** (a warm near-white at
night, near-black by morning), never her ink.

| Token | Night | Morning | Role |
| --- | --- | --- | --- |
| `--room` | `#0a0d12` | `#e7e6e1` | The deepest layer: behind the panes; the writing room's ground at night |
| `--ground` | `#0f131a` | `#efeee9` | Inset fields, the folded list column, the room by morning |
| `--pane` | `#141921` | `#faf9f6` | The floating page |
| `--raised` / `--raised-2` | `#1a2029` / `#222935` | `#fff` / `#f2f1ec` | Cards, hover, popovers |
| `--text` | `#ebe8e1` | `#1a1d23` | His words: warm paper-white / ink-black |
| `--text-2` | `#a9afba` | `#565c67` | Secondary UI |
| `--text-3` | `#8a91a0` | `#5c626c` | Meta, hints. ≥ 4.6:1 on every ground in both themes |
| `--lamp` / `--on-lamp` | `#f1ede4` / `#12161d` | `#1b1f27` / `#f7f5ef` | His primary actions, the play button, the recording orb, the active dot |
| `--glow` | warm 5.5 % | warm amber 32 % | Light off the page: a radial at the head of the entry pane and behind the writing column |
| `--hair` / `--hair-2` | cool white 7.5 / 13 % | ink 8.5 / 14 % | Rules and outlines |

**Her four inks** (computed WCAG contrast on `--pane`; every one also clears 7.4:1 on raised surfaces):

| Ink | Night | Morning |
| --- | --- | --- |
| Blue-black (default) | `#aec0f4` · 9.8:1 | `#26387a` · 10.3:1 |
| Brown | `#e2b893` · 9.7:1 | `#74431f` · 7.8:1 |
| Violet | `#cdb4f3` · 9.6:1 | `#573690` · 8.5:1 |
| Green | `#98d3ae` · 10.3:1 | `#255c40` · 7.4:1 |

Derived from `--ink` with `color-mix`: `--ink-wash` (9 %, a tint behind a waiting prompt), `--ink-line` (50 %,
question dashes, margin-note rule), `--ink-glow` (38 % at night, 0 by morning: the soft halo on her stroke and
marks). `--ink` is a registered `@property <color>`, so changing ink cross-fades every use of it in 600 ms.

## Space, radius, elevation

- **Spacing:** 4-px base. Rhythm within the document: 26 px between his passages, 28–40 between voices, 34 between
  page sections. Page gutters 48 desktop / 20 phone. The document grid is `72px margin · text · 220px margin`, with
  28-px gaps.
- **Radius:** 8 (small controls) · 12 (buttons, rows, menus) · 18 (panes, cards) · 26–30 (sheets, the dock). Pills
  only where something is held (Write, the dock, the save bar, the tab bar).
- **Elevation:** three layers. `--room`, then panes at `--elev-1` (an inset top highlight plus a long, soft shadow),
  then the open entry pane at `--elev-2` sitting above the folded list. Popovers, the dock, the toolbar and the tab bar
  are frosted (`backdrop-filter: blur(20–24px) saturate(1.4)`) at `--elev-pop`. Blur is used only on things that
  float over text, where seeing what is underneath helps.

## Motion tokens

| Token | Value | Used for |
| --- | --- | --- |
| `--d-fast` / `--d-mid` / `--d-slow` | 160 / 280 / 520 ms | Hover, colour, rise-in of lists |
| `--ease-out` | `cubic-bezier(.16,1,.3,1)` | Entrances, the bloom |
| `--spring` | damped spring k 260, c 27 → CSS `linear()`, ~450 ms, overshoot under 0.5 % | Toggles, chevrons, segmented thumb, toolbars, popovers |
| `--spring-soft` | k 170, c 21 → `linear()`, ~600 ms, overshoot ~1 % | Panes, disclosures, sheets, shared elements |

The springs are generated at boot from stiffness and damping (`springLinear()`, about 15 lines) and written into
custom properties, so a production build can bake the two strings into the token sheet. Nothing bounces visibly; the
spring is felt as "settling".

**Transitions:**
- **Entries → entry (desktop):** View Transition. The list pane (`page`) morphs from full width to the 92-px column of
  dates and dots, clipped rather than scaled (`object-fit: none`); the rail folds to icons; the clicked row's title
  flies into the entry's heading (`entry-title`); the entry pane slides in from the right on `--spring-soft`. Back
  reverses all of it. Switching entries from the dots cross-fades only the document.
- **Entries → entry (phone):** push (new page in from the right, old drifts 28 % left and dims); pop reverses, and the
  browser's back button picks the matching direction.
- **Write:** the room **blooms** as a circle clip from the button that summoned it (the rail's Write, the tab bar's
  pen, a prompt's Write, the entry's "Keep writing…"), while the page behind recedes (scale .97, dims, 2 px blur).
  From a prompt, her prompt card is a shared element and travels into the room's header as the room opens. Closing
  shrinks the room back into the button; Save shrinks it into the Save button and a toast confirms.
- **Disclosures** (her 24 questions, Analysis, Reflection, the week review, library rows, health): `grid-template-rows:
  0fr → 1fr` on `--spring-soft`, so height animates with no measurement and no jump. The content blurs (2 px), lifts 8
  px and fades, then settles 60–90 ms after the height starts. The chevron turns on `--spring`. The one-line summary
  under each disclosure head fades out as it opens, because the content now says it.
- **Page to page:** the `page` pane cross-fades in place (it keeps its frame); the rail and tab bar never move.
- **Micro:** rows rise in with a 28-ms stagger after filtering; the ink picker drops a wash of the new ink across the
  sample; the recording orb breathes (3.2 s) with a level-driven ring and a slow halo; the autosave dot pulses while
  saving.
- **Reduced motion:** View Transitions are skipped (instant swap), animations become 1 ms, colour transitions
  120 ms, disclosures open without lift or blur.

## Component inventory

- **Rail (desktop):** lamp mark and wordmark; **Write** (lamp pill, `N` hint); Entries, Library, Insights; Settings
  and a two-way night/morning switch at the foot. It compacts to 68 px when an entry is open.
- **Tab bar (phone):** a floating frosted capsule with five places. The middle one is the Write button, a lamp disc
  with a soft halo.
- **The lens (entries filter):** a sentence, not a row of pills: *Entries from **every practice**, **with or without
  voice**.* Each bold term is a dashed-underlined button that opens a frosted menu of options with counts. "Show
  everything" appears only when the lens is narrowed. Search ("Search your words", typed in his serif) sits beside
  the title.
- **Waiting prompt:** her opener and question in ink on a raised card with an ink wash from the corner; Write (lamp)
  and Speak (quiet). Shown at the top of Entries while one is open.
- **Entry row:** a large day numeral in the margin (Geist 300) with the weekday below; the practice title (Geist 550)
  with quiet indicators (an ink dot when Ada left notes, which is her presence and so the one hue in a row; a
  waveform for voice; a speech mark for a conversation); time right-aligned; his first line in serif.
- **Dot column:** weeks → days (numeral and weekday) → one 7-px dot per entry; the current one is a lamp dot with a
  ring; hover shows a frosted tooltip with title, time and his first line.
- **Entry document:** kicker date; Geist title; meta line (passages · voice length · time span · "Add mood or
  people"); star / people / more. Then:
  - *Her prompt:* label, opener in ink italic, a 2-px ink stroke hung in the margin so her text and his share one
    left edge. Under it, a disclosure whose head is the question she asked plus "24 questions in 10 parts". Open, it
    lays the sections out in two columns: section names in Geist, her questions in ink italic, the asked one marked
    with an ink dot and "asked".
  - *His passage:* time (and "voice") in the left margin; serif text. **Margin tools:** on hover or focus, a frosted
    pill (Edit · Copy · Delete) appears in the left margin under the time, never over his words. Delete turns the
    pill into "Delete this passage? Keep / Delete", and the passage collapses away. Edit makes the passage editable in
    place with Save / Cancel. On phone a long press (480 ms, with a press-in scale) opens a bottom sheet with the same
    three actions.
  - *Audio player:* a lamp play button, a 54-bar waveform that fills in his text colour (seek by clicking), elapsed /
    total in tabular figures, and a speed toggle (1× · 1.25× · 1.5× · 0.75×).
  - *Her message in the chat:* ink stroke, label with time, folded to one line with a chevron; a tap opens it.
  - *His reply to her:* "replying to *'The harbour keeps coming back…'*" with her words in her ink, then his text.
  - *Her margin note:* in the right margin beside the passage it belongs to (desktop); inline beneath it on narrow
    screens.
  - *Keep writing…:* a ruled empty line in his serif, with voice and pen buttons. It blooms into the room.
  - *From Ada, afterwards:* Analysis and Reflection as two disclosures (head: title and a one-line summary in ink;
    body: threads as dashed bullets, "What she noticed", "To carry forward", a link to the week).
- **Writing room:** full screen on `--room` with the page's warm glow; top bar: close (×), context, the autosave
  status (a dot and "Draft kept · 07:41"; "Keeping the draft…" with a pulsing dot while typing) and Focus. Her prompt
  above in ink, with "Another of her questions" (cross-fades to the next) and "All 11". His text is contenteditable
  paragraphs in 24-px serif; in **focus mode** every paragraph but the one with the caret dims to 22 %. A floating
  dock holds the count ("64 words · 1 min"), the voice orb, and Save entry / Add to entry / Keep note (lamp). Stopping
  a recording drops a "Voice note · 0:07 · will be transcribed" chip into the text.
- **Insights:** a week stepper; an editorial **lede sentence** with the figures set bold inside it; "When you wrote",
  a day × hour field where each entry is a dot sized by its words, the night hours are banded, and her reflections are
  ink rings at 04:00 the morning after; program progress as ten segments. Right column (phone: straight after the
  lede): **her week review** as a large ink pull-quote with a hanging quotation mark, "Read the whole review"
  (disclosure), and her question "For the week ahead". Below, her day reflections as a ruled table: day numeral ·
  What stood out · To carry forward · open.
- **Library row:** a little **clock face** (12-hour hand at the scheduled time; the face is shaded for evening and
  night times; the weekday sits in the face for weekly ones; a dashed rim for "whenever you start it"; a progress
  arc and `1/10` for programs). The title is Geist for built-ins and **his serif for his own**; the byline says
  "Built in", "**Yours** · written 2 Oct" (in his serif) or "Built in, edited by you", plus cadence and, when it
  applies, "• the default has changed since". On the right, the enabled switch (or Continue for a program) and a
  chevron. The row opens in place into the description, the numbered questions in two columns, Send now / Edit, and
  for an edited built-in "See what changed · Reset to default". No badge, no box: provenance is said in type and
  words.
- **Settings:** desktop shows a section list on the left (the Health item carries a hollow dot when something needs a
  look) and one group at a time on the right; phone shows a health card and a grouped list where each row gives a
  live summary ("Open 8 h · notes after 30 min"), pushing into the group. Controls: **steppers** (− value unit +,
  bounded, disabled at the ends), **segmented** controls with a sliding thumb ("Right away · 30 min · 1 h · 3 h"),
  **switches**, time steppers (±15 min), text fields. Rows that depend on a switch fold away with it. **Defaults are a
  sentence:** "The default." when it is the default, otherwise "Default 6 hours. *Use default*". A key owned by
  Hermes shows a lock and "Set in Hermes' plugin settings, so it is read-only here." A frosted **save bar** ("1 change
  not saved · Discard · Save") rises from the bottom while something is unsaved. **Ink & light:** a live sample (her
  line in the chosen ink with its stroke, his line beneath) above four **ink wells**: a glossy disc of each ink, its
  name and its note; choosing one rings the disc, checks it, and spreads a wash of the ink across the sample.
  **Health:** a breathing dot and "Running well · 9 of 10 checks are fine · 1 to look at · last tick 3 min ago" that
  opens into the checks list (a check-circle for fine, an alert circle in text colour for the one to look at).

## Signature moves

1. **The list folds into a column of dates and dots** when an entry opens, and the entry pane floats in above it.
   Your history stays in reach as a shape, not a list.
2. **Write blooms.** A circle of the room opens from wherever you asked to write; her prompt travels into the room
   with it.
3. **Light from the page.** The only gradient in the app is a warm radial where the page meets your eye: at the head
   of an entry and behind the writing column. It is why the night theme feels lit rather than black.
4. **Focus that follows the caret.** Only the paragraph you are in is fully lit.
5. **Her ink has a glow; his actions are lamp-light.** Two kinds of light, two voices.
6. **Settling, not snapping.** Every disclosure opens on a spring with content that arrives a beat after its space.

## Tradeoffs and risks

- **View Transitions** carry most of the spatial motion. They are in Chromium and Safari 18+; Firefox gets an instant
  swap (still correct, just not spatial). In Preact this is a small helper around the router (`startViewTransition`
  around a `flushSync`-style render, setting `data-vt` for direction) plus CSS, with **no new runtime dependency**.
  Cost: moderate. The tricky parts are naming only the right elements per transition (names are switched off for the
  bloom, or old panes would draw over the opening room) and keeping scroll positions. A manual FLIP fallback is
  possible but not worth it for one user on a current browser.
- **Snapshots, not live DOM:** during the pane morph the list is an image, so a very long scroll position may show a
  hard edge for ~600 ms. It is acceptable in practice; worth checking on a slow phone.
- **`backdrop-filter`** on the tab bar, dock and menus costs GPU on older phones; the fallback is the same colour at
  92 % opacity.
- **contenteditable** for the room (needed for per-paragraph focus) is more work than a textarea: paste
  sanitisation, IME, undo. A textarea plus a mirrored overlay for the dimming is the safer production route; it
  removes the risk and keeps the look.
- **Dark-first** suits evenings (most of his writing happens after 21:00 in the fixtures) but the morning theme is
  not an afterthought: it has its own warmer glow and darker muted text tuned to pass AA.
- **The serif rule** forbids one easy device, putting UI titles in a pretty serif. That makes the interface plainer
  than a book-like direction would be. I think the plainness is the point.
- **Inline Analysis** replaces the Entry / Analysis tabs of FRONTEND §1.3 with disclosures at the end of one document.
  This is a design decision to record in `docs/DECISIONS.md` if adopted.
- **Implementation cost overall:** medium. Tokens, type, rows, document, settings controls and disclosures are plain
  CSS on existing components. The writing room and the transitions are the real work, about a package each.

## Known gaps in the prototype

- Today, the run screen, the question-set editor, the meta sheet and pairing are not designed here (the Today waiting
  card on Entries stands in for "answer from a prompt").
- The week stepper does not page back (a toast explains); empty and loading states are only sketched (the lens's
  empty result).
- Audio is simulated (no file); recording is simulated (no `MediaRecorder`).
- Program entries (phase dividers, the summary) are not shown in their own layout.
