# Round 02 · Combined (Folio × Night Room)

**In one sentence:** a writing room that is lit: Night Room's panes, motion and light laid over Folio's warm paper by
day (and Night Room's cool room after dark), with his words set like a book in Newsreader, hers typed in her ink in
IBM Plex Mono, and the hours hung in the margin wherever he writes.

Prototype: `index.html` (one file, ~193 KB, Google Fonts only, no dependencies). Every screen opens from the hash:

| Route | Params worth knowing |
| --- | --- |
| `#today` `#entries` `#insights` `#settings` `#library` `#mark` | `theme=light\|dark`, `ink=blue-black\|brown\|violet\|green`, `motion=off`, `open=<fold ids>` |
| `#entry` | `stem=<entry>`, `sidebar=list\|rail\|hidden`, `open=questions,analysis,reflection` |
| `#write` | `from=morning\|note\|<stem>`, `over=<route behind>`, `sample=0` (empty page), `rec=1`, `focus=0`, `open=room-q` |
| `#entries` | `day=YYYY-MM-DD` (calendar filter) |
| `#insights` | `week=current`, `open=review,refl-0` |
| `#settings` | `dirty=1` (shows the save bar), `scroll=%23s-prompts`, `open=health` |
| any | `fasten=postmark\|clip\|tape` (her letter's fastening; see open calls) |

Keys: `N` writes (on an entry: keeps writing into it), `Esc` steps back (menu, sheet, room, entry), `⌘ .` focus in
the room, `[` cycles the entry sidebar (titles, dates and dots, none). The ink choice and the sidebar choice persist
in `localStorage` (`journal:ink`, `journal:sidebar`); a hash param overrides for that load.

The whole token sheet is the `<style id="tokens">` block at the top of the file, written to be lifted into
`web/src/tokens.css` as it stands.

## Type

Three families, one job each. No letter-spaced capitals anywhere.

| Family | Job | Where |
| --- | --- | --- |
| **Newsreader** (opsz 6–72, 300–600, italic) | His words, and the book's own voice | his passages 20/1.62 (21 on the reading page, 24/1.66 in the room, 19–21 on a phone); page titles 44 at 340; italic book heads 20 with a rule running out; the hours in the margin, italic 15, lining tabular figures; Almanac's large numerals (88 light: the day beside an entry, Today's date, the Insights week); the filter sentence; the Insights prose; ledes |
| **IBM Plex Mono** (400, 400 italic, 500) | Her words, and only hers | letters 14.5/1.72; the asked question italic 17; margin notes 13/1.6; pull lines italic 21; the reflection's question italic 24 |
| **Instrument Sans** (400–600) | The interface | labels, buttons, nav, meta, hints, controls, calendar, counts; 12 / 13 / 15; 500–550 for labels |

**Why Instrument Sans over Geist.** Both are OFL and both are good. Geist is the cooler, more engineered face; next to
Plex Mono (already a "typed", technical voice) it pushes the whole app toward the developer-tool register the owner
called "cli served dashboard". Instrument Sans has a slightly humanist, warmer build (open apertures, softer joins)
that sits with Newsreader on warm paper by day and still reads crisp on the night room. It is also Folio's choice,
and the owner preferred Folio's type. Swapping is one token (`--f-ui`); Geist would be the alternative to try.

Scale (rem at 16): `--t-micro` .75 · `--t-small` .8125 · `--t-ui` .9375 · `--t-her` .906 · `--t-her-lg` 1.0625 ·
`--t-read` 1.25 · `--t-write` 1.5 · `--t-h3` 1.25 · `--t-h2` 1.75 · `--t-h1` 2.75 · `--t-display` 4 · `--t-numeral` 5.5.

## Colour

One sheet of roles; `[data-theme="dark"]` swaps values. Her ink is the only hue and is hers; his actions are the
**lamp** (ink on paper by day, a warm near-white at night). Status has no hue: health marks are hollow, half-filled
or ringed in his ink.

| Role | Light (Folio) | Dark (Night Room) | Used for |
| --- | --- | --- | --- |
| `--room` | `#eee7d9` | `#0a0d12` | the desk/room behind the panes, the rail |
| `--pane` | `#fbf8f1` | `#141921` | the page |
| `--raised` / `--raised-2` | `#f5f0e5` / `#efe8da` | `#1a2029` / `#222935` | hover, player, selected rows |
| `--sunk` | `#ece5d6` | `#0f131a` | segmented tracks, the sidebar (dark) |
| `--letter` | `#fffcf6` | `#1a2029` | the base of her letters (washed 3 % with her ink) |
| `--write-room` | `#fbf8f1` | `#0a0d12` | the writing room's ground |
| `--text` / `-2` / `-3` | `#1d1a15` / `#4c443a` / `#6d6356` | `#ebe8e1` / `#a9afba` / `#8a91a0` | his words / secondary / meta |
| `--hair` / `--hair-2` | `#e9e2d3` / `#dcd2bf` | cool white 7.5 % / 13 % | rules, outlines |
| `--lamp` / `--on-lamp` | `#1d1a15` / `#fbf8f1` | `#f1ede4` / `#12161d` | Write, Answer, Save, play, switches on, the orb recording |
| `--frost` | paper 84 % | night 74 % | blurred floaters: menus, dock, tab bar, save bar, block tools |

Measured WCAG contrast (the glow surfaces are the glow at full strength, its worst point):

| Text role | Light: worst / best surface | Dark: worst / best |
| --- | --- | --- |
| `--text` | 13.3 (desk + glow) / 16.9 (letter) | 11.9 (raised-2) / 15.9 (room) |
| `--text-2` | 7.3 / 9.3 | 6.6 / 8.8 |
| `--text-3` | **4.51** (desk + glow) / 5.75 | **4.62** (raised-2) / 6.15 |
| lamp button label | 16.4 | 15.5 |

Her four inks (Folio's set; the dark values are Folio's, re-measured on Night Room's cooler grounds):

| Ink | Light | Dark | Light: pane · letter wash · `--her-soft` on wash · worst | Dark: pane · wash · soft on wash · worst |
| --- | --- | --- | --- | --- |
| Blue-black (default) | `#28367f` | `#b6c0ff` | 10.3 · 10.1 · 6.3 · 8.4 | 10.1 · 8.8 · 6.0 · 8.3 |
| Brown | `#7a4519` | `#dcab78` | 7.4 · 7.3 · 4.8 · 6.0 | 8.5 · 7.5 · 5.2 · 7.1 |
| Violet | `#4b2a8a` | `#d2b4ff` | 9.9 · 9.8 · 6.1 · 8.1 | 9.8 · 8.6 · 5.9 · 8.2 |
| Green | `#1b5642` | `#8fd3b4` | 8.1 · 8.0 · 5.2 · 6.6 | 10.2 · 8.9 · 6.1 · 8.5 |

Every ink clears 7:1 for her body text on every surface in dark and ≥ 6:1 in light (brown is the lowest, 6.0 on the
desk under full glow, where her words never actually sit); `--her-soft` (her labels and times) clears 4.5:1 everywhere
(lowest 4.79, brown on its wash). Derived from `--her`: `--her-soft` (82 %), `--her-edge` (34 %, the dashed crease),
`--her-line` (50 %, note rules), `--her-hair` (10 %, the letter's edge), `--her-wash` / `-2` (3 % / 8 % into the letter),
`--her-glow` (38 % at night, 0 by day: Night Room's halo on her marks). `--her` is a registered `@property`, so a new
ink flows across the page in 700 ms.

## Space, radius, elevation, glow

- **Space:** 4-px base, `--s1…--s9` = 4 · 8 · 12 · 16 · 24 · 32 · 48 · 64 · 96. Page gutter 56 desktop / 20 phone.
  The entry grid is `--margin-l` 72 (hours) · `--measure` 35 rem (his column) · `--margin-r` 200 (her notes), 28 gaps;
  it is a **container query** on the entry pane, so the notes move under their passage when the pane is narrower than
  840 px (e.g. a narrow window with the titled sidebar), not by viewport.
- **Layout measures:** rail 228 (68 compact, while an entry is open or under 1100 px), titled sidebar 280, dates-and-dots 92.
- **Radius:** 6 · 8 · 12 (buttons, rows, menus) · 18 (panes) · 26 (sheets, dock) · pill (primary buttons, steppers,
  toast, save bar). Letters 14; with the clip fastening, `3 14 14 14` (the square corner is where it holds).
- **Elevation:** `--elev-tab` (selected nav, thumbs) · `--elev-1` (the page on the desk) · `--elev-2` (the open entry
  above its sidebar) · `--elev-pop` (frosted floaters) · `--elev-letter` (her letter lifts slightly off the page).
  Light uses Folio's warm brown shadows; dark uses Night Room's inset top highlight plus long black shadow.
- **Glow** (Night Room's light, now in both themes): `--glow` at the head of every page (a radial from the top centre:
  warm lamp-amber 30 % on paper by day, 5.5 % warm white at night); `--glow-room` behind the writing column;
  `--glow-desk`, a faint lamp at the top left of the desk (amber by day, a cool moonlight 4.5 % at night). It is the
  only gradient in the app and always means "light falling on the page".

## Motion

| Token | Value | Used for |
| --- | --- | --- |
| `--d-1` / `--d-2` / `--d-3` / `--d-4` | 120 / 200 / 340 / 480 ms | press and hover / colour / chevrons, knobs / push, recede |
| `--d-bloom` | 640 ms | the write room opening |
| `--d-ink` | 700 ms | her ink flowing to a new colour |
| `--stagger` | 28 ms | rows and ledger bars rising in |
| `--e-out` | `cubic-bezier(.16,1,.3,1)` | arrivals, the bloom |
| `--e-inout` | `cubic-bezier(.65,0,.35,1)` | the ink flow, fold-away of a deleted passage |
| `--e-paper` | `cubic-bezier(.3,.75,.12,1)` | letters dropping in, ledger bars |
| `--spring` / `-dur` | k 260 c 27 → `linear()`, 533 ms | toggles, segmented thumbs, chevrons, popovers, toast, block tools |
| `--spring-soft` / `-dur` | k 170 c 21 → `linear()`, 642 ms | panes, disclosures, sheets, the save bar |

The springs are baked into the token sheet as `linear()` strings (Night Room generated them at boot; here they are
constants, so production needs no JS for them).

**Which transition uses what:**
- **Page to page** (desktop): View Transition on the `page` pane, cross-fade 200/360 ms; the rail never moves.
  Without View Transitions the new pane settles 10 px → 0 in 460 ms (`--e-out`).
- **Entries → entry** (desktop): the list pane morphs into the sidebar (titled or dots; clipped, not scaled), the rail
  folds to icons, the entry pane floats in from the right (`paneIn`, `--spring-soft`); the row's practice name flies to
  the entry's kicker. Back reverses it. With the sidebar hidden, the list fades out and only the entry floats in.
- **Sidebar switch** (titles ↔ dots ↔ none): the side pane morphs width on `--spring-soft`; leaving it slides out 24 px.
- **Phone:** push (new page from the right on `--spring-soft`, old drifts −28 % and dims) and pop.
- **Write:** the room blooms as a circle from the button pressed (`--d-bloom`, `--e-out`) while the app behind recedes
  (scale .972, dims to `--recede`, 2 px blur); her letter drops in 12 px at 220 ms; closing folds back into the same
  button (460 ms). It is a CSS clip-path, so it works without View Transitions.
- **Disclosures** (questions, Analysis, Reflection, the week's review, the day slips, health): `grid-template-rows`
  0fr → 1fr on `--spring-soft`; the content lifts 8 px, un-blurs and fades 60–90 ms behind the height; the one-line
  summary in the head fades as the body arrives (it now says it). Closed bodies are `visibility: hidden`, out of the
  tab order.
- **Micro:** the ink proof retypes itself; the stepper value nudges; the autosave dot pulses; the voice orb breathes
  (3.2 s) with a level ring and a halo; the program strip's current segment fills to where it is.
- **Reduced motion** (`prefers-reduced-motion`, or `motion=off`): every duration token becomes 1 ms, View Transitions
  are skipped, folds open without lift or blur, the proof letter appears whole.

## Components (anatomy and states)

- **Rail** (desktop): mark + "journal" (italic Newsreader) · **Write** (lamp, `N`) · Today ("1 waiting") · Entries ·
  Library · Insights · spacer · **Next from Ada** (her ring, "Daily gratitude at 21:30, then the evening journal at
  23:00") · Settings + light/dark. States: item hover (half-tint), current (a paper tab by day, raised at night),
  compact (68 px icons; Next hidden) while an entry is open or under 1100 px.
- **Tab bar** (phone): floating frosted capsule, Today · Entries · Write (lamp disc) · Library · Insights; Settings and
  light/dark sit in each page's top bar.
- **Sidebar beside an entry**, three modes from a segmented switch in the entry bar (titles · dates and dots · none),
  remembered per device:
  - *Titles* (Folio): "Entries" head with an all-entries button, the mini month, then weeks → day numerals → rows
    (title, her dot if she wrote back, time); the open one is a paper tab. Calendar and filters apply here too.
  - *Dates and dots* (Night Room): week labels, day numeral + weekday, one dot per entry; the open one is a lamp dot
    with a ring; hover gives a frosted tooltip with title, time and his first line.
  - *None*: the entry alone, centred on the full pane.
- **Month calendar** (Almanac): month name in Newsreader, arrows, M–S heads, days; a written day is bold with a dot in
  his ink and is a button; today is ringed; a picked day fills with the lamp and filters the list ("Showing Wednesday
  14 October: 3 of 14 entries · Show all"). Top right of the Entries page; head of the titled sidebar.
- **Filter sentence** (Folio): "Showing *every entry* from *any practice*, newest first." Each choice is a dotted
  underline opening a frosted menu with counts. Search is an italic underlined line; hits are marked.
- **Entry row:** day numeral (Newsreader 34 light) + weekday in the left column; title (sans 550), flags (her dot,
  voice wave, conversation), time right; his first line in the serif, two lines.
- **Her letter** (Folio): wash of her ink on the letter base, a dashed crease, a fastening, "From Ada · sent 23:00"
  in `--her-soft`, the opener, the asked question in italic, a foot with "All 24 questions, in 10 parts" (a disclosure:
  two columns of numbered questions by section, "asked tonight" tagged). In the room the foot adds "Another of her
  questions" (cross-fades the asked one).
- **Fastening:** postmark (default: her ring with the date and hour, cancellation waves), paper clip, or tape.
- **Entry document:** entry bar (back, sidebar switch, newer/older, the date sliding in once scrolled; star, mood and
  people, more) · head (day numeral in the margin, practice kicker, weekday title, meta, "Add mood or people") · her
  letter with its hour in the margin · his passages · her chat replies as slips · "Keep writing…" · Her readings.
- **His passage:** hour (italic) and source in the left margin; text in the serif. **Block tools**: on hover or focus a
  frosted vertical pill (Edit · Copy · Delete) appears in the margin under the hour, never over his words. Delete asks
  in place ("Delete this passage? Delete / Keep"), folds the passage away and offers Undo in a toast. Edit is in place
  with Save / Cancel. Phone: long-press (480 ms, with a press-in) raises a sheet quoting the passage.
- **Margin note** (Night Room): "Ada · 08:02" with her dot, then her note in Plex 13 behind a hairline in her ink,
  in the right margin beside the passage it belongs to; under the passage when the pane is narrow or on a phone.
- **Audio player** (Night Room): lamp play disc · 54-bar waveform (34 on a phone) that fills in his text colour,
  seek by tap · elapsed / total · rate (1×, 1.25×, 1.5×, 2×; hidden on a phone).
- **Disclosure ("reading")**: envelope glyph whose flap opens · title in Newsreader · "Analysis · Ada, 08:02 the next
  morning" · a one-line summary in her ink that fades on open · chevron in a 32 px well. Analysis body: his phrases
  quoted in his serif, her note under each, numbered; threads across days. Reflection body: her question large in
  Plex italic, the body, "Carry forward" on a wash, a link to the week.
- **Stepper:** pill outline, − value unit +, 44 × 42 targets, ends disable; the value nudges on change. Time steppers
  move by 15 min. **Segmented:** sunk track, paper thumb on `--spring`, measured per option (on a phone it fills the
  row). **Switch:** 52 × 32, his lamp when on, the knob stretches while pressed. Locked rows show a lock and "Set in
  Hermes, so it is changed there". **Defaults as hints:** "*the default*" in italic serif, or "*default 6 hours*" plus
  "Use default" once moved; a changed row gets a dot after its label. Dependent rows fold away with their switch.
- **Save bar:** frosted pill, "2 changes not saved · Discard · Save", rises on `--spring-soft` only while dirty.
- **Settings layout** (Almanac): large title and lede left, **At a glance** right (health with its mark, sign-in,
  her ink with a swatch, each jumping to its section); below, a numbered section index (01–08, sticky; on a phone a
  sticky scrolling strip) beside the sections, each with a numbered Newsreader head and a lede.
- **Ink picker** (Folio): four ink blots with name and note; the chosen one swells and turns; below, a fastened letter
  retypes "How did today go?…" in the new ink as it flows in; "Page" segmented (System · Paper · Night).
- **Health:** one sentence ("Nearly everything is well.") and a cluster of marks; unfolds to the checks, problems first,
  then "Working as it should".
- **Library row:** a ribbon hanging from the rule above for his own (solid) or a rewritten built-in (outlined) · Night
  Room's clock (hand at the hour, shaded face for evening and night, weekday in the face for weekly ones, dashed rim
  for "when you need one", a progress arc and `2/10` for programs) · title in the serif with a dotted leader to the
  cadence · description · colophon ("Yours · Three questions · Written 3 October") · Send now + switch, Start, or
  Continue. Off rows dim.
- **Programs strip** (Night Room): name, "3 of 10", ten 4-px segments (done in his ink, the current one partly filled).
  On Today, Insights and the Library row.
- **Write room:** top bar (close, context in the centre, "Draft kept · 09:14" with a pulsing dot while keeping, Focus) ·
  her letter · his page in Newsreader 24 with each paragraph's start hour in the margin · **focus** (on by default)
  greys every paragraph but the one with the caret to 30 % by day, 22 % at night · the dock (word count and minutes,
  the breathing voice orb, the lamp send: "Send your answer", "Add to the entry" or "Keep this note"). A recording
  lands as a chip "Your voice · 0:07 · the words arrive once it's sent". Without a prompt the room offers "Answer this
  morning's instead".
- **Today** (Folio): Almanac's numeral "15" with Thursday / October beside it · the week strip (answered solid, missed
  ring, nothing sent a dash; a day opens Entries filtered to it) · "Waiting for you" as a fastened letter with Answer
  (blooms the room) and Speak it · Written today · aside: Later today leaders, Programs strip with Continue, Carry
  forward in her italic.
- **Insights** (Folio + an Almanac numeral): week stepper; the week as large numerals with the month in italic; left,
  the figures as prose, the ledger (bars in his ink, her dot under each day she reflected), Programs strip, By
  practice leaders; right, **her review of the week** as a fastened letter with the pull line large and the rest
  folded, then **Day by day, from Ada** (day numeral + her question, unfolding to her line and a link to the day).
  Phone order: prose, ledger, review, programs, day by day, by practice.
- **Mark** (`#mark`): the three options and the fastening specimen (below).

## The mark

Three ways to join Folio's notebook with the favicon's j (his j in his ink, its dot her ring in her ink), each shown at
rail size on paper and night, as a 32 and 16 favicon, and as the 512 app icon on paper and on night:

1. **Margin j** (in the rail): the notebook's margin rule bends into his j and her ring is its dot, with three faint
   ruled lines. The page and the letter become one drawing, which is the product in a glyph.
2. **A j on the page:** the notebook keeps a margin rule and the j is written on the page under her ring. The most
   literal; the j stays legible at 16 px.
3. **The favicon, kept:** the current favicon with a faint margin rule. The strongest at 16 px, but the notebook is
   nearly gone.

My call: **1 in the rail and as the app icon, 3 as the 16/32 favicon.** At 16 px the margin j loses its page; the
existing favicon is the same idea reduced, so the family reads as one mark at every size.

## Where each piece came from

| Piece | From | What changed in the merge |
| --- | --- | --- |
| Shell: rail on the room, floating panes, compact rail on an entry | B | Folio's rail content: wordmark, Write, Today with "1 waiting", **Next from Ada**, theme toggle |
| Light palette, desk and paper, shadows | A | Role names unified with B's (`--room`, `--pane`, `--raised`…) |
| Dark palette, inset highlights, frosted floaters | B | Her ink glow kept as `--her-glow` |
| Glow at the head of every page and behind the column | B | Now also by day, as warm lamp light on paper; plus a faint desk lamp |
| Type: Newsreader, Plex Mono, Instrument Sans | A | Night Room's rule kept in spirit: her face never appears on interface text |
| Her four inks, blots, retyping proof letter, ink flow | A | Re-measured on the night grounds |
| Her letter at the top (entry, room, Today, review) | A | Fastening can be a postmark (default), clip or tape |
| Hours in the left margin | A | Everywhere he writes, including the room |
| Her notes in the right margin | B | In Plex Mono; under the passage on narrow panes (container query) |
| Block tools in the margin, long-press sheet | B (position) + A (confirm in place, undo toast) | Vertical pill under the hour |
| Audio player | B | Lamp disc and waveform in his text colour |
| Analysis and Reflection unfolding at the end | B (disclosure motion) + A (envelopes, "What she noticed", carry forward) | No tabs |
| Entries list, filter sentence, search line | A | Night Room's rise-in stagger and frosted menus with counts |
| Titled sidebar | A | Plus the mini month |
| Dates-and-dots sidebar, tooltips | B | |
| Sidebar optional and remembered | new | Segmented switch in the entry bar, `[` key, `localStorage` |
| Month calendar | C | Days with entries dotted; filters the list and the sidebar |
| View Transitions, push/pop, springs | B | Springs baked into tokens; CSS fallbacks for browsers without View Transitions |
| Write room: focus greying, breathing orb, dock, autosave | B | Folio's letter on top; Folio's margin hours; bloom done in CSS so it needs no View Transitions |
| Today | A | Almanac numeral for the date; Night Room's program strip |
| Insights: prose figures, ledger, her review as a letter, day by day | A | Night Room's program strip; Almanac numerals for the week |
| Settings: numbered index, large title, at a glance | C | Folio's grouped rows, ink picker, health marks; Night Room's switches and spring thumbs |
| Library rows and ribbons | A | Night Room's clocks |
| Mark | A's notebook + the favicon's j | Three options above |

## Open calls (for the owner)

1. **Instrument Sans, not Geist,** for the interface (reasons above). One token to swap.
2. **Her voice stays typed** (Plex Mono) everywhere, including the margin notes, which in Night Room were serif italic.
   Kept from round 01's unconfirmed call.
3. **The fastening is a postmark** (her ring with the hour she wrote) rather than a paper clip. The clip and a strip of
   tape are one parameter away (`fasten=clip|tape`) and drawn side by side on `#mark`.
4. **Notes in the right margin** is my reading of "writing in the margin… the right section". If he meant the left
   margin's hours, they are also there.
5. **The rail folds to icons whenever an entry is open** (Night Room), so the entry keeps its two margins at 1280 with
   the titled sidebar. The cost: labels and "Next from Ada" are hidden while reading.
6. **Default sidebar is "titles".** First open shows Folio's list; the choice sticks per device.
7. **Today's heading is the date as a numeral** ("15", Thursday / October). An Almanac touch; Folio had "15 October".
8. **Analysis and Reflection unfold at the end** of the entry with no tabs. This replaces FRONTEND §1.3's Entry /
   Analysis tabs and should be recorded in `docs/DECISIONS.md` when adopted.
9. **Focus mode is on by default** in the room (the greying is the signature); one button or `⌘ .` turns it off.
10. **Mark:** margin j in the rail and app icon, the existing favicon at 16/32.

## Implementation notes (Preact + plain CSS, no new runtime dependency)

- Tokens: replace `tokens.css` with the `#tokens` block; add `@property --her`; self-host Newsreader (roman + italic
  variable), Plex Mono 400/400i/500, Instrument Sans variable (all OFL).
- The sidebar is a list/detail shell owning the open stem and the mode; the entry grid is a container query.
- View Transitions: a small helper around route changes (`startViewTransition` + a `data-vt` direction) and
  `view-transition-name` on the rail, page and entry panes. The room's bloom is plain CSS clip-path.
- New primitives: `Letter` (with `Fastening`), `Disclosure`, `Stepper`, `Segmented`, `Switch`, `MonthCalendar`,
  `SidebarSwitch`, `Player`, `ProgramStrip`, `Clock`, `SaveBar`, `Toast`, `ActionSheet`, `Menu`.
- Contract changes to check against `design/inventory.md` §3–4: the entries chips become the sentence + calendar
  (URL params `kind`, `program`, `day`, `audio`, `q` map cleanly); the entry tabs go; the native `<audio controls>`
  becomes the player (the e2e expects one `audio` element: keep a hidden `<audio>` driving it); the write sheet
  becomes the room (drafts stay in `sessionStorage`).

## Verified

Rendered in T3's headless Chrome against a local static server, at 1280 × 820 and 390 × 844, light and dark: every
screen, all three sidebar modes, the sidebar switch by click (View Transition), four inks (blue-black, green on
Insights dark, brown on Settings dark, green and blue-black through the picker). A scripted pass ran with no console
errors: filter menu and choice, calendar day filter on and off, search with hits, open an entry, fold Analysis, play,
delete and undo, sidebar list → rail → hidden → list (stored), open another entry from the sidebar, Keep writing →
room (app behind made inert), type, autosave, record and stop (voice chip), focus toggle, send (toast), settings
stepper / segmented / switch (dependent row folds, save bar shows 3 changes), discard, ink pick (glance updates),
health fold, Insights week step, week strip → filtered Entries. Fixed during review: calendar header wrapping, the
entry numeral crowding the title, the entry bar's hard edge over the glow, closed folds in the tab order, the save
bar / toast / sheet in the accessibility tree when hidden, her letter's dot rendering grey (a class collision with the
dots rail), the mark specimen wrapping, ink options misaligned when a note wraps, a 38 px overflow from a segmented
control on a phone, and the ink sticking across hash changes.

Screenshots of the prototype were shown to the owner at the time (desktop and phone, light and dark); they are not kept in the repository.

## Known gaps

- Not drawn: the run screen, the question-set editor, the practice detail and editor, pairing, offline ribbon, error
  and loading states, the mood/people sheet, empty weeks (Insights shows "her review comes on Sunday" for the current
  week only), program entries' phase dividers.
- The room is `contenteditable` with per-paragraph stamps; production may prefer a textarea plus a mirrored overlay
  for the greying (Night Room's note), keeping one stamp per send.
- The block tools are a vertical pill under the hour; a very short last passage can let it overlap the next hour.
- View Transitions are Chromium/Safari 18+; Firefox gets the CSS settle fallbacks (checked by code path, not rendered).
- Fixture data is static: the calendar only has October entries; month arrows show empty months.
