# C — Almanac

**In one sentence:** the journal as a private almanac of one life: a magazine whose body copy is his words and whose
editor's voice (standfirsts, pull-quotes, the margin) is hers, set on a strict, visible grid with big numerals for
days and figures, on paper that warms and cools with the hour.

Open `index.html`. The first screen comes from the hash, and theme, ink and paper come from hash params:

```
#today  #entries  #entry?stem=2026-10-14-evening  #write  #insights  #settings  #library
…?theme=dark&ink=violet&tod=dusk     (ink: blue-black | brown | violet | green; tod: dawn | day | dusk | night)
…&motion=off                         (stills: no reveals, for screenshots)
```

Press `g` to show the 12-column grid. Press Esc to close the writing room, leave focus mode, or go back from an entry.
Ctrl/⌘ + `.` toggles focus while writing. On a touch screen, long-press one of his blocks to get its actions.

---

## Type system (three families, all SIL OFL)

| Family | Role | Why this face |
| --- | --- | --- |
| **Instrument Serif** (roman + italic) | The almanac's display: page titles, week heads, every big numeral (days, times, figures). **Her voice** in italic, in her ink. | A narrow, high-contrast display serif that holds up at 120–200 px and looks like a magazine. Its italic is lively but restrained, so it can carry her standfirst, her pull-quote and her notes. In print, display italic belongs to the editor (standfirst, pull-quote), and that is the role she plays here. |
| **Newsreader** (optical sizes 6–72) | **His words**, roman, full ink: entry blocks, the writing room, his headlines in the contents (first line at 32 px), his own practice titles in the Library. | A text face built for long reading, with optical sizes, so the same family works as his 32 px headline and his 20 px body. The current app already self-hosts it, so his voice carries over. |
| **Schibsted Grotesk** (400/500/600) | Interface: nav, labels, kickers, times, hints, buttons, settings rows. | A newspaper grotesk (made for a news group's mastheads and UI), so it sits naturally beside the serifs. Sentence case everywhere: no letter-spaced capitals, which was most of the baseline's CLI feel. |

The two voices are told apart three ways on every screen: **face** (Newsreader roman vs Instrument Serif italic),
**ink** (his is the page's ink; hers is the only hue), and **placement** (his in the main column; hers as standfirst
above, in the note column at the right on desktop, or indented under a dashed fold on the phone, always under an
"Ada · time" line).

**Scale** (1280 → 390):

| Token | Desktop | Phone | Use |
| --- | --- | --- | --- |
| `--t-xs` | 12 | 12 | hints under figures, "Ada · 23:45" |
| `--t-sm` | 13.5 | 13.5 | kickers, meta, times |
| `--t-ui` | 15 | 15 | interface body |
| `--t-his` / `--t-her` | 20 | 19 | his blocks / her notes (her notes run 18–20) |
| `--t-lede` | 27 | 23 | standfirsts, the filter sentence |
| `--t-h3` | 34 | 28 | department heads (Analysis, Reflection), week heads |
| `--t-h2` | 48 | 36 | settings sections, review headline |
| `--t-h1` | 96 | 58 | page titles |
| `--t-num` | 120 | 76 | day numerals in the contents |
| `--t-mega` | 200 | 120 | today's date, the week's word count |

Leading: his words 1.62; hers 1.35–1.48; display 0.82–1.0. Figures in Instrument Serif lining; times in the
grotesk.

## Colour roles

One hue only: her ink. Everything else is warm neutral. **His actions are in his ink** (the solid Write, Reply and
Save buttons, switches, the play button, his rule in the Library).

| Role | Light (day) | Dark (night) |
| --- | --- | --- |
| `--ground` (paper) | `#f7f5f0` | `#121110` |
| `--raised` (writing room, menus) | `#fbfaf6` | `#1a1917` |
| `--sunk` (hover, track) | `#efebe2` | `#0d0c0b` |
| `--ink` (his words, his actions, strong rules) | `#171512` | `#ede7dc` |
| `--ink-2` (secondary text) | `#47423a` | `#bdb5a8` |
| `--ink-3` (meta, hints; ≥ 5:1) | `#6b6458` | `#928a7d` |
| `--rule` / `--rule-2` | `#d6cfc2` / `#e7e2d8` | `#36322c` / `#25221f` |

**Paper by time of day** (`data-tod`, set from the clock and overridable): light dawn `#f8f2ee` (faint pink), day
`#f7f5f0`, dusk `#f4eee4` (warmer), night `#f1f0ec` (cooler). Dark: night `#121110`, dawn `#141317`, day `#151413`,
dusk `#16130f`. Chroma stays low enough to read as paper, never as a second hue. The ground changes over 700 ms.

**Her inks**: the existing tested values, unchanged:

| Ink | Light | Dark |
| --- | --- | --- |
| Blue-black (default) | `#28367f` | `#b6c0ff` |
| Brown | `#7a4519` | `#dcab78` |
| Violet | `#4b2a8a` | `#d2b4ff` |
| Green | `#1b5642` | `#8fd3b4` |

Derived from her ink: `--her-soft` (87% ink on the ground: her questions not asked tonight, her quoted reply), `--her-rule`
(38%: dashed folds above her notes), `--her-wash` (6%, unused so far). **Checked:** every ink, and `--her-soft`, is
≥ 4.5:1 on all eight grounds and both surfaces (worst 5.02:1, brown-soft on light dusk). At 80% `--her-soft` failed
for brown (4.29), which is why it sits at 87%.

## Space, radius, elevation, grid

- Space on a 4 px base: 4 · 8 · 12 · 16 · 24 · 32 · 48 · 64 · 96.
- Grid: 12 columns on desktop, 4 on phone (`--cols`). Margin `clamp(20px, 3.4vw, 48px)`, gutter
  `clamp(14px, 1.9vw, 28px)`, max width 1320. Placement uses two custom properties per element (`--c` desktop,
  `--cp` phone) on a single `.g` class.
- The grid is visible as rules: a 2 px ink rule opens every major section (week, spread, settings section,
  library group); 1 px rules divide rows; vertical hairlines split the spread's columns. On each page change the 12
  columns flash in (see Motion).
- Radius: 2 / 4 / pill (the record button only). Nothing is a rounded card.
- Elevation: none on the page. Only floating things have a shadow: the filter menu, the save bar, the phone's block
  toolbar. The writing room is a full surface, not a raised sheet.

## Motion tokens

| Token | Value | Used for |
| --- | --- | --- |
| `--e-out` | `cubic-bezier(.16,.84,.24,1)` | reveals, settles, thumbs |
| `--e-io` | `cubic-bezier(.66,0,.34,1)` | column slide, wipes, folds |
| `--e-in` | `cubic-bezier(.5,0,.75,0)` | exits |
| `--d-xs/s/m/l/xl` | 120 / 200 / 320 / 520 / 720 ms | |
| `--stagger` | 45 ms | per line/column in a reveal |

Transitions:
- **Page change** (between Today, Entries, Library, Insights, Settings): the outgoing page fades up 8 px in 180 ms.
  Then the masthead rule redraws left to right (760 ms), the 12 grid columns wipe down one after another (28 ms
  apart) and fade out, the page title rises out of a mask by its line, and every block fades and slides 12 px into
  place, 45 ms apart. This is the "wipe on the grid".
- **Entries → entry (desktop)**: the contents fade (150 ms), the column narrows from full width to a 17.5 rem index
  (560 ms, `--e-io`) while the entry reveals block by block beside it. The index keeps the big day numerals (34 px),
  practice and first line, and marks the open entry with an ink bar. Back reverses it. Opening another entry from the
  index cross-fades only the reading pane.
- **Entries → entry (phone)**: a push. The entry slides in from the right and the contents slide 28% left and fade
  (460 ms); scroll position is restored on the way back.
- **Folds** (her 24 questions, Analysis, Reflection, the week review, day reflections, health): `grid-template-rows:
  0fr → 1fr` over 520 ms, so the height animates with no jump. Children fade and rise 10 px in after 140 ms, 55 ms
  apart; on close they leave first and faster. Chevrons turn 180°.
- **Writing room**: the room wipes down over the page (`clip-path`, 640 ms), then her prompt, the rule (drawn left to
  right) and his field arrive 70 ms apart. Closing wipes it back up.
- **Ink change**: a blot of the new ink spreads from the swatch you tapped, and every word of hers on the page
  changes ink over 600 ms.
- **Reduced motion**: every transition and animation becomes ~instant. The grid flash and the line masks are off, and
  the room appears without a wipe.

## Component inventory

- **Masthead**: italic wordmark + "No. 288" (the day of the year as the issue number); text nav with a 3 px active
  underline that sits on the masthead rule; theme, settings, Write (solid, his ink). On a phone: a five-place tab bar
  with Write as an ink square in the middle.
- **Contents row**: time in the margin column, practice kicker, his first line as a 32 px Newsreader headline,
  indicators as words on the right ("voice 2:31 · Ada replied · analysed", the "Ada" part in her ink). Under a day,
  her reflection's first line in her italic, under a dashed fold.
- **Day numeral column**: 120 px day plus weekday (on a phone: 56 px plus "Wed").
- **Filter sentence**, instead of pills: "All entries, from *every practice*, *in whatever form*." Each underlined
  phrase opens a small menu with counts (practices / programs / loose notes; spoken aloud / where Ada answered).
  Search ("Search his words") is a ruled line, not a box.
- **Month almanac** (desktop contents): October as a 7-column table; days written are underlined and jump to the
  day; today is ringed. It shows the shape of the month without counting anything up.
- **Entry header**: big day numeral in the margin column, kicker (date, span of time, "four blocks, one spoken"),
  practice title at 64 px, "Add to this".
- **Standfirst**: her opener at 27 px italic in her ink under "Ada · sent 23:00".
- **Question fold**: "Her 24 questions, in ten parts · one asked tonight". Unfolds into two columns with a column
  rule; section names in the grotesk; numbered questions in her softer ink; tonight's question at full ink, tagged
  "asked tonight".
- **His block**: margin (time, "typed / voice · 2:31"); body in Newsreader 20. On hover or focus, actions appear
  in the margin under the time (Edit, Delete; no Delete on a voice block). On a phone, long-press shows a small
  floating toolbar and lightly tints the block. Edit is inline (a ruled textarea with Save, Cancel and a reminder
  that only his words are editable); Delete asks once, inline.
- **Audio player**: an ink disc with play/pause; a 72-bar waveform that fills in his ink and can be clicked, dragged
  or moved with the arrow keys; elapsed / length; speed 1× / 1.5× / 2×. The transcript follows as his words.
- **Her note**: a dashed rule in her ink, "Ada · 23:45, in the chat", the note in her italic. Sits in the right-hand
  note column level with the block it answers; indented inline on a phone. His reply carries "↩ to Ada" and a short
  quote of hers.
- **Departments**: Analysis and Reflection as titled sections, each with a heavy rule, a 34 px head, a kicker ("by
  Ada · 08:02 the next morning · three observations") and a fold. Her run-in heads ("What stood out —") are set in
  roman, the rest in italic.
- **Compose line**: "Add to this entry, in your own words…" in his italic, plus a mic; both open the writing room.
- **Writing room**: top bar (close, a folio saying what you are writing into, "Kept on this device" with a breathing
  dot while it saves, focus); her prompt as standfirst with an optional hint ("11 questions if you want them…");
  an ink rule; voice notes as rows with a mini waveform ("its words will follow"); his field in Newsreader 20–23 on a
  40 rem measure; footer with Record (it turns into an ink pill with a running time and level bars), a quiet word
  count, and "Put it in the journal" ("Keep it" on a phone). With no prompt, the standfirst is the date ("Thursday,
  *the 15th*"). Focus hides the bars until the pointer reaches an edge, and dims her prompt to 32%.
- **Spread** (Insights): heading "The week *of the 12th*" with a week stepper; her pull-quote at up to 118 px with a
  hanging quotation mark, attributed and linked; figures as type (412 at 200 px, then 6 and 4/5 in a split row with
  rules); seven day columns with numerals, his first line and short ink marks (answered solid, let pass outlined);
  "The word he came back to: *harbour*" with his line of the week in his own face; the week review as a column with
  her headline, a 5-line drop cap in her ink, and three more paragraphs that unfold; her day reflections as rows
  that unfold.
- **Library row**: time numeral or session fraction (`1/10`) in the margin; title; description; provenance; on the
  right, question count, on/off (built-ins), Send now / Edit. **Built-in vs his own is told by type, not a box**:
  standard titles are set in the almanac's display face, with a dotted rule and "From the journal's standard set";
  his own (and edited ones) are set in **his** face, Newsreader, with a solid ink rule and "Written by you" / "Edited
  by you, from the standard · the standard has changed since. See what changed". Programs get a 10-segment rule for
  progress.
- **Settings ("the colophon")**: a numbered section index (sticky on desktop, a scrolling strip on a phone, with
  scroll-spy); numbered 48 px section heads with one plain sentence of intro; rows with label + hint on the left
  and a real control on the right: steppers (−  6 hours  +), segmented controls with a sliding thumb, switches, a
  lock and "set in Hermes' settings, so it is changed there" for a field this page cannot write. **Defaults as hints**:
  "*the default*" in italic, or "changed · the default is 6 hours" once moved. An "At a glance" column (health,
  device, ink) fills the top right. The **ink picker** is four specimens (a big italic *Aa*, a sample sentence in that
  ink, its name and its note), a sliding ink underline for the one in use, and the blot. **Health**: "All is well" with
  an ink dot and one line ("one voice note is waiting for its words"), unfolding into calm check rows; a warning is
  an outlined dot and plain words, never a red. A save bar rises from the bottom while there are unsaved changes.
- **Today (the cover)**: the date at 200 px with "No. 288 of the year · 09:12 · Hillside"; the carried-forward
  question in her ink; the day's slots as rows (time numeral, practice, state, her prompt once sent, Reply / Send
  now).

## Signature moves

1. **The contents page.** Entries read like a magazine's contents: huge day numerals, his first line as the headline,
   and a contents column that narrows into an index beside the entry, still showing the numerals.
2. **Type tells you whose it is.** Her voice is the editor's italic, his is the body face. The rule holds even in the
   Library, where his own practices are set in his type.
3. **The week as a spread.** Her question as a pull-quote at 118 px, the word count as a 200 px figure, the word he
   came back to, and the review as a column with a drop cap that unfolds.
4. **The grid shows itself** on each page change (columns wiping in, the masthead rule redrawing) and on demand
   (`g`).
5. **Paper that keeps the hour.** Dawn, day, dusk and night grounds, with her ink as the only colour.
6. **The issue number.** "No. 288" in the masthead is the day of the year: an almanac's dateline, not a counter.

## Tradeoffs and risks

- **Her voice in a display face at small sizes.** Instrument Serif italic is narrow and slightly fragile at 16–18 px
  (her questions, the "↩ to Ada" quote). Long reading in it is fine for her paragraphs; at 18 px it is the weakest
  point in the system. Fallback if it tests badly: Newsreader italic at opsz 16 for her small text only. The cost is
  that her face and his become siblings, so ink and placement would carry more of the difference.
- **Two serifs.** Newsreader and Instrument Serif are both serifs. They are distinct (a narrow display face against a
  wide text face, italic against roman, her ink against his), but a quick reader relies on ink and labels as much as on
  face. Every one of her blocks therefore keeps its "Ada · time" line.
- **This drops Courier Prime** (her typed letters in the current identity, DECISIONS.md IDENTITY). The almanac's editor
  speaks in italic, not typewriter. Fraunces' soft figures also go. That is a change to a decided identity and should
  be recorded as such if this direction is picked. Fonts to self-host: Instrument Serif (2 files, ~60 KB), Newsreader
  (already shipped), Schibsted Grotesk (variable, ~45 KB subset).
- **Visual density of big numerals.** At 1280 the contents and the spread read as a magazine. At ~800–1000 px the
  12-column rows with 120 px numerals get cramped: the tablet range needs its own pass (one breakpoint at 1100 is
  only roughed in for the entry).
- **The column slide reflows text.** Animating the contents column's `width` makes the browser lay it out on every
  frame. It is hidden behind a short fade (content out → width moves → compact content in) but costs layout on long
  lists. Alternative: animate a `transform: scaleX` mask over a fixed-width index. Same look, cheaper, more code.
- **Implementation in Preact + plain CSS (no new runtime deps).** Everything here is CSS plus about 400 lines of
  component logic:
  - folds use `grid-template-rows` (supported since 2023);
  - the room wipe uses `clip-path`;
  - the segmented thumb is measured once with `offsetLeft`;
  - the stagger is a `--i` custom property set when a list renders;
  - the router keeps the existing `preact-iso` routes. The entries → entry transition needs Entries and Entry rendered
    as one layout with the list kept mounted (a layout route), which changes the route structure.
  - `subgrid` is used in one place (the entry header).
  - The month table, figures and "word he came back to" need data the API does not yet give: a per-day map for the
    month, and a most-repeated word with its line (a stopword-filtered count over his words for the week, done in the
    engine, no model). The week's word count and answered/missed already exist.
- **Hover-only actions.** On desktop the Edit/Delete actions appear on hover or focus. Keyboard users get them on focus.
  Touch needs long-press, which is less discoverable; the toolbar could also open from a tap on the time in the
  margin.
- **Focus mode** hides the controls until the pointer reaches an edge. On touch there is no hover, so focus is left
  by Esc or by tapping where the controls were; a phone build should show a small "leave focus" control.

## Prototype limits (known gaps)

- Fixture content is extended beyond `tests/fixtures/api` (eight entries over two weeks, a written week review, a
  most-repeated word) so the editorial layouts have something to set. All neutral (lighthouse, harbour, gulls; Ada).
- Only the evening entry is a full document; the others open a one-block entry.
- Audio, recording, saving and sending are simulated (playback runs 6× fast so the fill is visible).
- The Library's Edit, New practice and See what changed, a program's Run screen, and the meta sheet (mood, people,
  star) are not drawn: they toast.
- The 768–1100 px tablet range is not designed.
- Verified with headless Chromium at real widths. Phone views were rendered inside a 390 px frame, because the
  local Chrome won't make a window narrower than about 500 px. T3's `html_preview` could not load the file from disk
  or from a localhost server (blank frame), and inlining 124 KB for each render was not practical, so it was not used
  for the final checks.
