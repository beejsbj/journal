# A · Folio

**In one sentence:** Margin × Letters grown into a finished notebook app: his words set like a well-made book on warm paper, the hours hung in a ruled margin, her words as typed letters clipped to the page in her ink, inside quiet real-app chrome (a desk-coloured rail, the page as one sheet laid on it).

Prototype: `index.html` (self-contained, ~140 KB, Google Fonts only). Screens open straight from the hash:
`#today`, `#entries`, `#entry` (`&stem=…`), `#write` (`&from=morning|2026-10-14-evening|note`, `&focus=1`, `&rec=1`, `&sample=0`),
`#library`, `#insights` (`&week=current`), `#settings` (`&dirty=1`, `&scroll=%23s-prompts`). Every screen takes
`theme=light|dark` and `ink=blue-black|brown|violet|green`; `open=analysis,reflection,questions,review,health,room-q`
opens folds. Keys: `N` writes, `Esc` steps back (focus → room → entry), `⌘ .` toggles focus.

## Type

Three families, each with one job. No letter-spaced capitals anywhere.

| Family | Job | Where |
| --- | --- | --- |
| **Newsreader** (opsz 6–72, 300–600, italic) | His voice, and the book's own voice | his passages 20 px / 1.62 (22 px / 36 px when writing); page titles 44 px at 340; book heads in italic 20 px with a rule running out; margin hours in italic 15 px, tabular lining figures; the filter sentence; the Insights prose |
| **IBM Plex Mono** (400, 400 italic, 500) | Her words, and only her words | letters 14.5 px / 1.72; the question she asked in italic 17 px; pull lines in italic 21 px; question lists 13.5 px |
| **Instrument Sans** (400–600) | The interface | labels, buttons, meta, hints, settings, counts, nav; 12 / 13 / 15 px; weights 500–550 for labels |

Why Plex Mono over Courier Prime: it keeps the typed letter but has a true italic (the asked question and her pull lines
use it), sturdier strokes in dark mode, and better rhythm at 14–15 px on a phone. Courier Prime remains the fallback if
the owner wants the old ribbon back; the swap is one token.

Scale (rem at 16): 0.75 micro · 0.8125 small · 0.9375 ui · 0.906 her · 1.0625 her-lg · 1.25 read · 1.375 write ·
1.25 h3 (italic) · 1.75 h2 · 2.75 h1 · 4 display (Insights).

## Colour

One token sheet; `[data-theme=dark]` swaps values. Her ink is a registered `@property --her` so a new ink *flows* across
the page (700 ms) instead of snapping.

| Role | Light | Dark |
| --- | --- | --- |
| desk (rail, behind the sheet, write room) | `#eee7d9` | `#121110` |
| paper (the sheet) | `#fbf8f1` | `#1c1a17` |
| paper-2 (hover, player) | `#f5f0e5` | `#221f1b` |
| sunk (segmented track, selected) | `#ece5d6` | `#282520` |
| letter (base of her slips) | `#fffcf6` | `#24221e` |
| ink (his words, his actions) | `#1d1a15` | `#ede5d7` |
| ink-2 / ink-3 | `#4c443a` / `#6d6356` (5.5:1) | `#c0b5a3` / `#998e7c` (5.4:1) |
| rule / rule-2 | `#dcd2bf` / `#e9e2d3` | `#39342c` / `#2b2823` |

Her inks (values carried from the tested Margin × Letters set) and contrast on paper / on her letter wash; `--her-soft`
(her labels and times) measured on the wash:

| Ink | Light | Dark | Light paper · wash · soft | Dark paper · wash · soft |
| --- | --- | --- | --- | --- |
| Blue-black (default) | `#28367f` | `#b6c0ff` | 10.3 · 10.2 · 6.2 | 9.9 · 8.6 · 6.1 |
| Brown | `#7a4519` | `#dcab78` | 7.4 · 7.3 · 4.8 | 8.4 · 7.3 · 5.3 |
| Violet | `#4b2a8a` | `#d2b4ff` | 9.9 · 9.8 · 6.1 | 9.7 · 8.4 · 6.0 |
| Green | `#1b5642` | `#8fd3b4` | 8.1 · 8.0 · 5.1 | 10.0 · 8.8 · 6.2 |

Derived from her ink: `--her-wash` (2.5% into the letter base), `--her-wash-2` (7%), `--her-edge` (34%, the dashed
fold), `--her-soft` (82%). Status has **no hue**: a health check is a hollow, half-filled or ringed-solid mark in his
ink, so her ink stays the only colour on the page. His actions (Write, Answer, Save, the play button, toggles) are in
his ink, inverted in dark.

## Space, radius, elevation

- 4 px base: 4 · 8 · 12 · 16 · 24 · 32 · 48 · 64 · 96. Page padding 56 desktop / 20 phone; leaf margin column 92 px;
  measure 35 rem.
- Radii: 6 (small), 10 (buttons, rows), 14 (sheet, groups, letters), pill (primary buttons, steppers, the dock).
  Letters are `3px 14px 14px 14px`: the square corner is where the clip holds them.
- Elevation, three steps only: `sh-1` (selected nav item, selected row, segmented thumb), `sh-sheet` (the page on the
  desk; the write sheet), `sh-float` (menus, block toolbar, save bar, dock, action sheet). Everything else is rules and
  space.

## Motion

| Token | Value | Used for |
| --- | --- | --- |
| d-1 | 120 ms | press, hover |
| d-2 | 200 ms | small fades, toggles' colour |
| d-3 | 340 ms | folds, segmented thumb, switch knob |
| d-4 | 480 ms | list ↔ sidebar, push, room recede |
| d-ink | 700 ms | her ink flowing to a new colour |
| e-out | `cubic-bezier(.22,.8,.24,1)` | arrivals |
| e-inout | `cubic-bezier(.65,0,.35,1)` | the list narrowing (both ends matter) |
| e-paper | `cubic-bezier(.3,.75,.12,1)` | anything paper: folds, sheets, the room |

- **Page change:** the old page lifts 6 px and fades (140 ms); the new one settles 10 px → 0 (460 ms, 90 ms later).
- **Entries → entry (desktop):** the list pane narrows to 300 px (`width`, 480 ms, e-inout) while each row's first line
  folds away (grid-rows 1fr → 0fr), day numerals shrink 30 → 19 px and the heading trades for a compact one; the entry
  slides in 28 px and fades after 120–200 ms. Back reverses it. **Phone:** a push: the entry slides over from the right
  (460 ms, e-paper) and the list drifts −28% and dims.
- **Paper fold** (every disclosure): height via `grid-template-rows` 0fr → 1fr (420 ms, e-paper), contents translate
  −10 px → 0 and fade in after 150 ms, children settle in a 38 ms stagger; closing fades contents first (110 ms) and
  only then folds. The envelope glyph's flap opens with it.
- **Write room:** opens *out of whatever was pressed* (`clip-path: inset()` from the button's rect to full, 560 ms);
  the app behind recedes to 98.5% and dims; the sheet rises 24 px; her letter drops in 12 px at 260 ms; the caret lands
  at 420 ms. Closing folds it back into the same button.
- Insights: the ledger's ink lines grow from the base (700 ms, 60 ms stagger); a new week cross-settles.
- Ink change: the registered `--her` interpolates; the proof letter retypes itself in the new ink.
- `prefers-reduced-motion`: every duration becomes 1 ms, delays and staggers drop; only the end states remain.

## Components (anatomy)

- **Rail** (desktop ≥ 900): wordmark · Write (primary, `N`) · Today (with a quiet "1 waiting") · Entries · Library ·
  Insights · a footnote "Next from Ada" · Settings + theme toggle. Selected item is a paper tab with `sh-1`.
- **Tab bar** (phone): Today · Entries · Write (an ink tile) · Library · Insights; Settings from Today's header.
- **Filter sentence:** "Showing *every entry* from *any practice*, newest first." Each italic phrase is a dotted-underline
  choice opening a paper menu; a result line "4 of 12 entries · Show all" appears only when filtered. Search is an
  underlined line in italic.
- **Entry row:** day tab (numeral + weekday) holding the day's rows; row = practice title (sans 550) + time + quiet
  glyphs (voice wave; a dot in her ink when she wrote back) + his first line in the serif, two lines.
- **Leaf** (entry): sticky bar (list toggle, newer/older, star, mood/people, more; the title slides in on scroll) ·
  kicker + display date + meta · her letter (clip, "From Ada · sent 23:00", opener, the asked question in italic, a fold
  for all 24 questions in 10 parts with "asked tonight" marked) · his passages with the hour and source in the margin ·
  her replies as slips beside a tick in the margin · "Continue in your own words" · *Her readings* (two envelope folds).
- **Block actions:** a floating paper toolbar (Edit · Copy · Delete) above a passage's top-right on hover or focus;
  Delete asks once in place ("Delete this passage? Delete / Keep"); a delete folds the passage away and offers Undo in a
  toast. Touch: long-press (480 ms) raises an action sheet quoting the passage. Edit is in place, Save / Cancel.
- **Player:** ink play disc · 64-bar waveform (seek by tap) · elapsed / total · rate 1× / 1.5× / 2×.
- **Analysis ("What she noticed"):** his phrase quoted in his serif, her note under it in her ink, numbered; threads
  across days; her note with its time. **Reflection:** her question large in Plex italic, the body, a "Carry forward" tab.
- **Write room:** bar (close · where/when · "Draft kept · 23:58" with a breathing dot while saving · focus · primary) ·
  the sheet with a double-ruled margin where each paragraph's start time hangs · faint baseline ruling · her letter
  clipped over the top edge (folds its other questions) · a floating dock: record (stop with a soft ring, live waveform,
  timer; a voice clip lands in the page) and "56 words · 1 min". Focus mode drops the chrome and dims every paragraph but
  the one with the caret.
- **Library row:** title in the serif with a dotted leader to its cadence · one-line description · a *colophon* line
  ("Built in · Twenty-four questions"; "Yours · Three questions · Written 3 October"; "Built in, rewritten by you · The
  default has changed since") · switch or Start / Continue. **Provenance is a ribbon:** his own practices carry a solid
  bookmark ribbon hanging from the rule above them; an edited built-in carries an outlined one. No box, no badge.
- **Insights:** a centred display date; a paragraph that *is* the figures ("You wrote on **six** of seven days:
  **2,117** words in **10** entries…"); a seven-day ledger of ink lines on ruled paper with a dot in her ink under each day
  she reflected; "By practice" leaders; on the right her review as a letter (pull line large, the rest folded) and "Day
  by day, from Ada" slips. Phone reorders so her review follows the ledger.
- **Settings:** a section index (desktop: a sticky list with a tick; phone: sticky pills) · italic heads with a lede in
  the serif · grouped rows on paper (label, hint with "default 6 hours" in the hint itself, control on the right) ·
  steppers, switches, segmented controls, a time chip · a changed row gets a dot and a Reset link · a locked row says
  "Set in Hermes, change it there" with a lock · a floating save bar ("1 change · Discard · Save") slides up only while
  dirty · **Ink:** four ink blots you pick, the chosen one swells; a proof letter types "How did today go?…" in it ·
  **Health:** one calm sentence and a cluster of marks; it unfolds into the checks, problems first, "Working as it should"
  below.
- **Today:** the date as a page heading · the week as seven days with marks (answered solid, missed ring, none a dash)
  · "Waiting for you" as her letter with Answer / Speak it · "Written today" · a side column: "Later today" leaders, the
  program with ten ticks, "Carry forward" in her italic.

## Signature moves

1. **The page on the desk.** Chrome is the desk; content is one sheet laid on it. On entering Write, the desk recedes
   and a new sheet opens out of the button you pressed.
2. **Hours hung in the margin**, everywhere his words are: the entry, the write room (each paragraph stamps its start
   time as you write), the list's day tabs.
3. **Her letters are clipped on.** Every prompt is a slip with a clip and a dashed fold; in the room it overlaps the
   sheet's top edge.
4. **The list becomes the book's spine.** Opening an entry narrows the list to a sidebar of days and titles rather than
   navigating away.
5. **Filters as a sentence** you edit, not a row of pills.
6. **Ribbons for provenance** in the Library.
7. **Ink that flows**, and a letter that retypes itself in the ink you pick.
8. **Figures as prose** on Insights; status as marks, never colour.

## Tradeoffs and risks

- **Plex Mono changes her voice.** Folio's case is legibility and a real italic; the cost is the loss of Courier's
  ribbon texture, which the owner chose. One token reverts it. Both are OFL.
- **Width transition for the sidebar** animates layout (480 ms); on a slow phone it is the push instead, and on desktop
  the list is short. If it janks with long histories, swap to a FLIP (transform-only) version: same look, more code.
- **Rows inside the narrowed list** keep their DOM and fold their first line; long lists will need windowing later.
- **`@property` for her ink** is Chromium/Safari 16.4+/Firefox 128+; elsewhere the ink simply snaps.
- **clip-path room opening** needs a measured rect; it degrades to opening from the centre.
- **Margin stamps while writing** come from paragraph start times, which the engine does not keep today for a single
  `POST /blocks`; in production they would be client-side display only (or one stamp per send).
- **Settings density:** steppers and segmented controls cover every field the API exposes as a number or choice; a
  free-text field (place) stays a value + action. The time chip opens a picker not drawn here.
- **Not drawn:** the question-set editor, the run screen, pairing, offline and error states, the mood/people sheet,
  library detail.

### Implementation cost (Preact + plain CSS, no new runtime deps)

- Tokens: rewrite `tokens.css` (three faces, the registered `--her`, motion tokens). Fonts: drop Courier Prime and
  Fraunces Figures, add IBM Plex Mono 400/400i/500 (self-hosted, OFL).
- New primitives, all small: `Fold` (grid-rows disclosure + settle), `Letter` (from-line, clip, fold), `Stepper`,
  `Segmented` (thumb measured once), `Switch`, `Menu` (popover), `Toast`, `ActionSheet`, `Player`. Each is under 80
  lines.
- Routes: Entries becomes a list/detail shell owning the selected stem (`preact-iso` route `/entries/:stem` renders the
  same shell open). Write becomes a route-level overlay with the clip-path entry; drafts already live in
  `sessionStorage`.
- Highest-risk pieces: the write room's contenteditable (paragraph stamps, focus dimming) — a textarea fallback keeps
  everything but per-paragraph stamps; the long-press sheet on iOS (needs `-webkit-touch-callout: none`).
- Rough size: CSS ~22 KB unminified, components ~10 KB; no dependency added.

## Verification

Iterated in T3's headless Chrome at 1280 × 800 and 390 × 844, light and dark (blue-black, brown, violet, green):
Today, Entries (full list, filter menu, search), the entry (sidebar state, hover toolbar, questions open, analysis
open, phone push, phone long-press sheet), Write (desktop dark, desktop light with the questions open, focus mode,
phone), Library (desktop light, phone dark), Insights (desktop light and dark/brown, phone dark), Settings (desktop
light with a change pending, desktop dark with health open, phone light/green, phone dark/violet). A scripted pass
(filters, search, open, play, fold, delete + undo, edit, step, write, record, focus, Esc, week step, ink, stepper,
discard, theme) ran with no console errors. `mcp__t3-code__html_preview` was not used for the final passes: it takes
inline HTML and cannot follow a hash, so the same headless browser was driven through the preview tab against a local
static server instead.
