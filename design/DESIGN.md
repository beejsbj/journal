# journal · the design language

The single written source for how the web app looks, moves and reads. The tokens are `web/src/tokens.css`; the parts
are `web/src/ui/` (and the shell's seams in `web/src/shell/`); every part is drawn, with its states, at **`/design`**
in the running app (linked from Settings). When this file, the tokens and `/design` disagree, the owner's words in
`design/state.md` win, then this file; fix the one that is wrong. A gap here is raised, not invented around.

Accepted: round 02, "Folio × Night Room" (`design/rounds/02-combined/`), with the owner's changes of 2026-11-09: no
fastening on her letter; the time under the date in the entry sidebar; her margin notes in dark as in light; the
"margin j" mark.

## 1. Intent

A writing room that is lit. His journal is a book he writes in: warm paper on a desk by day, a cool quiet room after
dark, with lamp light falling on the page. Two people write in it and must never be confused: **he** writes in the
book's roman, **she** (the companion, "Ada" in the fixtures; the name comes from the server) types her letters and
notes in her own ink. The interface around them stays quiet, precise and warm, never a "served dashboard": no chrome
louder than the words, no boxes where rules and space will do, nothing that scores him.

What it promises (inherited, not reopened): his words and hers are two visibly different voices; her ink is the only
hue and he chooses it; no streaks, confetti or gamification; it works with no model; offline reads; 44 px targets;
reduced motion honoured; self-hosted OFL faces.

## 2. Vocabulary

### Type

Three families, one job each (`--f-his`, `--f-her`, `--f-ui`). No letter-spaced capitals anywhere.

| Role | Family, weight, size / leading | Token or class | Where |
| --- | --- | --- | --- |
| His words, reading | Newsreader 400, 20 / 1.62 (21 on the reading page, 19 on a phone) | `--t-read`, `.read` | passages, his first lines |
| His words, writing | Newsreader 400, 24 / 1.66 (21 on a phone) | `--t-write` | the write room |
| Page title | Newsreader 340, 44 / 1.04, −0.018em | `--t-h1`, `.display` | every page's head |
| Display | Newsreader 300, 64 | `--t-display` | Settings, Insights' week, /design |
| Numeral | Newsreader 300, 88 / 0.8, lining | `--t-numeral`, `.numeral` | the day beside an entry, Today's date, the week |
| Book head | Newsreader italic 400, 20, a rule running out | `--t-h3`, `.book-head` | section heads in a page |
| Section title | Newsreader 400, 28 | `--t-h2` | Settings' numbered sections |
| Lede, filter sentence, prose figures | Newsreader 400, 17–19 / 1.5 | `.lede` | under a title |
| The hours | Newsreader italic 400, 15, tabular lining figures | — | the left margin |
| Her words | IBM Plex Mono 400, 14.5 / 1.72, in `--her` | `--t-her`, `.her` | letters, notes, readings |
| The question she asked | Plex Mono italic, 17 | `--t-her-lg` | her letter |
| Her pull line / reflection question | Plex Mono italic, 21–24 | — | review, reflection |
| Interface | Instrument Sans 400–600: 15 body and nav, 13 meta and hints, 12 micro | `--t-ui`, `--t-small`, `--t-micro` | labels, buttons, rows, counts |

Weights: labels 500–550, body 400. Kicker (`.kicker`): sans 500, 13, `--text-3`, sentence case. Figures are tabular
wherever they line up (`.num`, `time`).

### Colour

Roles, not colours. A component reads a role; it never writes a hex. Light is Folio's warm paper, dark Night Room's
cool room; `tokens.css` holds both and `web/tests/ink.test.tsx` holds every text role at AA on every ground.

| Role | Paper (light) | Night (dark) | For |
| --- | --- | --- | --- |
| `--room` | `#eee7d9` | `#0a0d12` | the desk behind the panes, the rail |
| `--pane` | `#fbf8f1` | `#141921` | the page (also the browser's theme colour) |
| `--raised`, `--raised-2` | `#f5f0e5`, `#efe8da` | `#1a2029`, `#222935` | hover, the player, selected rows |
| `--sunk` | `#ece5d6` | `#0f131a` | tracks, wells, the sidebar at night |
| `--letter` | `#fffcf6` | `#1a2029` | the base of her letters (washed with her ink) |
| `--text`, `--text-2`, `--text-3` | `#1d1a15`, `#4c443a`, `#6d6356` | `#ebe8e1`, `#a9afba`, `#8a91a0` | his words / secondary / meta |
| `--hair`, `--hair-2` | `#e9e2d3`, `#dcd2bf` | cool white 7.5 %, 13 % | rules, outlines |
| `--lamp`, `--on-lamp` | `#1d1a15`, `#fbf8f1` | `#f1ede4`, `#12161d` | his primary action |
| `--frost` | paper 84 % | night 74 % | blurred floaters: menus, tab bar, toast, save bar |
| `--scrim` | warm 24 % | black 55 % | behind a sheet |
| `--glow`, `--glow-room`, `--glow-desk` | lamp amber | warm white / moonlight | the lamp light (below) |

**Status has no hue.** Health and errors use marks in his ink: hollow (fine), half-filled (needs a look), ringed and
filled (wrong), always with words. An error line is a raised box with a filled mark and the sentence.

**The theme** is the device's (System) or the reader's choice on this device (Paper, Night): `web/src/theme.ts`,
`localStorage["journal:theme"]`, `<html data-theme>`, set before the first render. The dark roles are written twice in
`tokens.css` (under the media query, and under `[data-theme="dark"]`); change both, the test holds them equal.

**The glow** is the only gradient in the app and always means light falling on paper: `--glow` at the head of every
pane (radial from the top centre), `--glow-room` behind the writing column, `--glow-desk` a faint lamp at the top left
of the desk. Amber by day, a faint warm white (and a cool moonlight on the desk) at night.

### Her four inks

Her ink (`--her`) is the only hue on the page and it is hers alone: her words, her dot, her ring in the mark, her
readings' envelopes. Never his buttons, links, focus or progress. Chosen per device (`web/src/ink.ts`,
`journal:ink`, `<html data-ink>`); a swatch carries its own `data-ink` (and `data-theme` to show the other scheme).
`--her` is a registered `@property`, so a new ink flows across the page in `--d-ink`.

| Ink | Paper | Night |
| --- | --- | --- |
| Blue-black (default) | `#28367f` | `#b6c0ff` |
| Brown | `#7a4519` | `#dcab78` |
| Violet | `#4b2a8a` | `#d2b4ff` |
| Green | `#1b5642` | `#8fd3b4` |

Grades made from it, on `:root, [data-ink], [data-theme]`: `--her-soft` (82 % into the pane, oklab: her labels and
times), `--her-edge` (34 %: the letter's crease, dashed rules in her letters), `--her-line` (50 %: the rule beside a
margin note), `--her-hair` (10 %: the letter's edge), `--her-wash` / `--her-wash-2` (3 % / 8 % into `--letter`: her
letter's ground), `--her-glow` (a halo at night, none by day). Every ink and `--her-soft` clear AA on every ground and
on her wash, light and dark (the test).

### The mark

The **margin j**: the notebook's margin rule bends into his j, three faint ruled lines beside it, and her ring, in
her ink, is its dot. The page and the letter in one drawing. `Mark` in `web/src/icons.tsx` (30 in the rail, 26 on a
phone); `web/public/icons/mark.svg` and the PNGs made from it (`npm run icons`) are the app icon on the paper, with her
default ink (an icon cannot follow a device's choice). The browser tab keeps its own small `j` (`favicon.svg`, drawn for
16 px). The wordmark is "journal" in Newsreader italic 23, lower case.

### Icons

One stroke family on a 24 grid: stroke 1.6, round caps and joins, `currentColor` (`web/src/icons.tsx`). 18 px inline,
20 in the rail and icon buttons, 21 in the tab bar. A few glyphs fill (play, pause, stop, the dots of More). An icon
alone is always a labelled control (`IconButton label="…"`); decorative icons are `aria-hidden`. Add an icon by
drawing it on the same grid and stroke; show it on `/design`.

### Voice and copy

- Plain words, sentence case, the second person for him ("Your voice", "Send your answer"), her name for her ("From
  Ada", "Next from Ada"). Times as the page reads them ("23:00", "Wed 14 Oct").
- Say what happened and what to do: "Can't reach the journal server. Retry", "You're offline; this will not be saved
  until you're back." Never blame, never cheer.
- No emoji in labels, no exclamation marks, no counts that score him (no streaks, no "great job").
- Her words are hers verbatim; the interface never paraphrases her inside her letter.
- Empty states are one quiet sentence in the book's italic, plus the next step when there is one.

## 3. Grammar

### Layout

- **Wide screens (761 px and up): the desk.** The **rail** (228 px; 68 px folded to icons) stands on the desk:
  mark and wordmark, **Write** (the lamp, `N`), Today ("1 waiting"), Entries, Library, Insights, then at the foot
  **Next from Ada** and Settings with the theme toggle. The **pane** (the page) is laid on the desk beside it:
  rounded 18, lifted `--elev-1`, the glow at its head, scrolling on its own. The rail folds while a route asks (an
  open entry) and below 1100 px.
- **Phones (760 px and under): the page.** A top bar (mark and wordmark; theme; Settings), the page scrolling as a
  whole, and a floating frosted tab bar at the foot: Today, Entries, **Write** (a lamp disc, centred), Library,
  Insights. Content keeps `--bar-h` clear at the bottom.
- **Panes.** A route may take the stage and lay its own panes side by side (the entries list beside an open entry;
  10 px apart, the open one raised `--elev-2`).
- **The page measure.** His column is `--measure` (35 rem). Page padding is `--gutter` (56 desktop, 20 phone).
  Lists, forms and settings keep a reading width (about 1000–1060 px max for a whole page).
- **The margins.** Wherever he writes, the **hours hang in the left margin** (`--margin-l`, 72 px: the time in italic,
  its source under it), and **her notes stand in the right margin** (`--margin-r`, 200 px) beside the passage they
  belong to. The entry grid is a **container query** on its pane: under 840 px of pane the notes move under their
  passage and the hours fold into a running head above it.
- **The spread** is those three columns: hours, his column, her margin, 28 px apart.

### Density and hierarchy

- Rules and space make structure; boxes are for things that float (menus, sheets, toast) or are hers (her letter).
  Settings groups are the one quiet card (a hairline outline on the pane).
- One primary (lamp) action per view. Secondary actions are outlined or quiet.
- Hierarchy by type, not colour: display title, book heads with a rule running out, kicker above, meta in `--text-3`.
- Rows are generous (44 px minimum, 70 for a settings row); sections are 40–64 px apart.

### The two voices

The rule that holds everything: **his words are the book's roman in his ink; hers are typed in Plex Mono in her
ink**, under a "From Ada" line, wherever they appear (letters, margin notes, readings, reflections, the carry-forward
line). Her face never appears on interface text, and his face never sets her words. Her words are never editable and
never offered for editing. The interface (labels, buttons, meta) is Instrument Sans in his greys.

## 4. Motion

Purposeful and short; the tokens name every duration and curve. With `prefers-reduced-motion` every duration token is
1 ms, View Transitions are skipped, folds open without lift or blur, and `base.css` stops everything else.

| Token | Value | Use |
| --- | --- | --- |
| `--d-1` | 120 ms | press and hover |
| `--d-2` | 200 ms | colour, small fades |
| `--d-3` | 340 ms | chevrons, knobs, peeks |
| `--d-4` | 480 ms | push, recede |
| `--d-bloom` | 640 ms | the write room opening |
| `--d-ink` | 700 ms | her ink flowing to a new colour |
| `--stagger` | 28 ms | rows rising in (`.rise` with `--i`) |
| `--e-out` | `cubic-bezier(.16,1,.3,1)` | arrivals |
| `--e-inout` | `cubic-bezier(.65,0,.35,1)` | both ends matter: the ink flow, a passage folding away |
| `--e-paper` | `cubic-bezier(.3,.75,.12,1)` | anything paper: letters dropping in, bars |
| `--spring` / `--spring-dur` | k260 c27, 533 ms (`linear()`) | controls: switches, segmented thumbs, chevrons, menus, toast |
| `--spring-soft` / `--spring-soft-dur` | k170 c21, 642 ms | panes, disclosures, sheets, the save bar |

Which transition uses what:

- **Page to page:** a View Transition on the page pane (old out 200 ms, new in 360 ms); the rail never moves. Every
  in-app link gets it (the shell routes link clicks through `withTransition`). Without View Transitions the new page
  settles 10 px up in 460 ms (`--e-out`).
- **Deeper and back on a phone:** `data-vt="push"` / `"pop"` on the link: the new page comes in from the right on the
  soft spring, the old drifts left and dims.
- **An entry opening beside its list:** `data-vt="entry"`, the entry pane named `entrypane` floats in from the right.
- **Disclosures:** height by `grid-template-rows` 0fr → 1fr on the soft spring; the content lifts 8 px, un-blurs and
  fades 60–90 ms behind; a peek in the head fades as the body arrives.
- **Sheets:** up from the foot (phone) or a small rise and fade (wide), on the soft spring; out in 220 ms.
- **Micro:** a pressed button scales to .97; a stepper's value nudges; the switch knob stretches; skeletons shimmer;
  the program strip's current segment fills.

## 5. Components

All in `web/src/ui/` (TSX plus a CSS file each), exported from `web/src/ui/index.ts`, drawn on `/design`.

| Component | Anatomy and states | Use it for | Notes |
| --- | --- | --- | --- |
| `Button` | label, optional icon; `primary` (lamp), `secondary` (outlined, default), `quiet`, `danger`; `small`; `wide`; disabled; press-in | his actions | one primary per view; danger is the lamp too, asked in place first |
| `ButtonLink` | a link with a button's look | going somewhere (Open the entry, Continue) | |
| `TextButton` | underlined words | an action inside a line (Show all, Use default) | |
| `IconButton` | 40 px (44 touch) face, `label` required, `pressed` toggle, `small` | icon-only controls | the label is its name and tooltip |
| `Field`, `TextField`, `TextArea` | label, control, hint, error (filled mark + words); `voice="his"` for his words on a ruled line; `hideLabel` | every form field | ids wire `aria-describedby` and `aria-invalid` |
| `Switch` | 52 × 32 track in a 44 px target, the lamp when on | something that acts at once | named by `label` or `labelledBy` |
| `Segmented` | sunk track, paper thumb on the spring; text or `iconOnly`; `wide` on a phone | one of a few | radio group, arrows move, one tab stop |
| `Stepper` | − value unit +, ends disable, value nudges | a number with a unit, a time (`format={clockTime}`, step 15) | value is a spinbutton |
| `DefaultHint` | "the default", or "default 6 hours · Use default" | beside a setting's label | defaults are never placeholders |
| `Menu` | a trigger (`.choice` in a sentence, or any button) and a frosted list, radio or actions, counts | the filter sentence, a row's More | top layer, keyboard, Escape returns focus |
| `Disclosure` | head (icon, title, peek), chevron in a well, body | readings, the week's review, a day's slip, health | animated height; closed body is inert |
| `Sheet` (`Dialog`) | scrim, panel, head with title and Close | a short task over the page (mood, people, star) | native modal dialog: top layer, real trap, Escape, focus returns |
| `Toast` | message, up to two actions, on the lamp | "The passage is gone. Undo"; the update prompt | foot of a wide screen, top on a phone; `role=status` |
| `Letter` | From line with her dot and the time; opener; the asked question in italic; questions folded by part, "asked" marked; actions on a crease | her prompt anywhere: entry head, Today, the room, her week review | **no fastening**; read-only |
| `MarginNote` | "Ada · 08:02" with her dot; her words behind a hairline of her ink | her note beside a passage | the entry's grid places it |
| `AudioPlayer` | lamp play disc, 54 bars (34 on a phone), time, speed (hidden on a phone) | his recording | a real `<audio>` underneath; the bars are a slider |
| `Calendar` | month in the roman, arrows, M–S, written days bold with a dot (buttons), today ringed, chosen day on the lamp | filtering by day | one tab stop, arrows walk written days |
| `ProgressStrip` | title, "3 of 10", segments (done in his ink, current partly filled) | a program's way through | a progressbar |
| `Clock` | a face: hand at the hour, shaded at night, weekday for weekly, dashed for "when you need one", arc for a program | Library rows | decorative |
| `Ribbon` | a bookmark ribbon, solid (his own) or outlined (a built-in he rewrote) | Library rows | replaces boxed "yours"/"edited" badges |
| `Skeleton` (`Loading`) | rows, lines or blocks shimmering | while a read is out | `role=status` "Loading" |
| `ErrorLine` | a filled mark, the sentence, Retry | a failed read or write | `role=alert` |
| `Empty` | one sentence in the italic, the next step | nothing there | |
| `OfflineRibbon` | a frosted strip at the page's head: "offline, as of 09:12" | the view came from the kept copy | sticky |
| `ThemeChoice`, `ThemeToggle` | System · Paper · Night; a sun/moon icon button | Settings; the rail's foot and the phone's top bar | |
| `Mark` | the margin j | the rail, the phone top, pairing | in `icons.tsx` |

The shell's own parts (`web/src/shell/`): `Pane` (a pane on the desk, `glow`, `entry` to raise it, `vt` to name it),
`useShellMode`, `withTransition` / `useNavigate`, `openWrite` / `useWriteRequest` (section 8).

## 6. Web rules

- **Navigation.** Places are links (`<a href>`), actions are buttons. The rail and the tab bar mark the current place
  with `aria-current="page"`. Filters live in the URL (replace, not push). A link deeper says `data-vt="push"`, a way
  back `data-vt="pop"`. The `N` key opens Write anywhere but a field.
- **Forms.** A visible label (or `hideLabel` when the page already says it), the hint under it, the error in words
  with `role=alert`. Inputs are 16 px or more on touch. Save is the lamp; a form with several changes shows a save bar
  ("2 changes not saved · Discard · Save"). Defaults show as hints, never as placeholders. Cmd/Ctrl+Enter sends what
  he wrote.
- **Feedback.** A write in flight says so on its button ("Saving…"); a done write says what happened ("Saved as a
  note.") or offers Undo in a toast. Destructive actions ask in place ("Delete this passage? Delete / Keep").
- **States.** Every read has its loading (Skeleton, the shape of what comes), error (ErrorLine with Retry), empty
  (Empty) and offline (the ribbon; writes say they will not be saved) state. Keep showing what is there during a
  reload.
- **Accessibility floor.** 44 px targets on touch (`web/e2e/mobile.spec.ts` measures every control); visible focus
  (`:focus-visible`, 2 px in `--text-2`); real labels; keyboard paths for everything hover reveals; one tab stop per
  composite control (radio groups, calendars, menus); dialogs trap focus and return it; `prefers-reduced-motion`
  honoured; AA contrast for every text role and every ink in light and dark (the test).
- **Touch.** Long-press for a passage's tools on a phone (a sheet quoting it); nothing that matters is hover-only;
  `touch-action: manipulation` on controls; safe-area insets on the top bar, the tab bar, sheets and the toast.
- **CSP.** The server allows no inline style attributes in markup: set styles through Preact `style={{…}}` objects
  (CSSOM), never `style="…"` strings or `setAttribute("style")`. Images may be `data:`, media `blob:`.

## 7. Do and don't

Do: rules and space; the lamp for his one primary action; her ink for her words only; the hours in the left margin
and her notes in the right; book heads in the italic with a rule; quiet italic sentences for empty states; tokens for
every value; the springs for controls and panes.

Don't (the owner's vetoes and the system's):

- **No stamps or fastenings** on her letter: no postmark, no wavy cancel lines, no clip, no tape.
- **No letter-spaced capitals**, anywhere (no "EYEBROW" labels).
- **No pill rows** of filters or tags: filters are a sentence with choices; tags are words.
- **No boxed badges** ("YOURS", "EDITED", "ASKED"): a ribbon, an italic word, a dot.
- **No emoji labels** in the interface (a practice's own emoji is data, not a label).
- **No gamification**: no streaks, scores, confetti, celebration, red dots demanding attention.
- No hue for status; no her-ink on his controls; no his-face on her words; no hex outside `tokens.css`; no new
  gradient but the glow; no spinner where a skeleton fits; no layout that jumps when content lands.

## 8. Agent guide

**Making a screen that belongs.**

1. Read section 1–3 here and look at `/design`; find the screen in the round-02 prototype
   (`design/rounds/02-combined/index.html`) and its behaviour in `design/inventory.md` §1 and §4.
2. Put the route in `web/src/routes/<name>.tsx` with its own `web/src/routes/<name>.css`, imported by the TSX.
   Build from `web/src/ui` (`import { Button, Letter } from "../ui"`). If a part is missing, it belongs in `ui/` (raise
   it; the foundation owns `ui/`, `tokens.css`, `styles/base.css`, `icons.tsx`, `app.tsx`).
3. Delete your route's rules from `web/src/styles/legacy.css` as you convert it (and the alias block's names you no
   longer read). Nothing new goes into `legacy.css`.
4. Use the shell's seams:
   - **Fold the rail** while your route is on screen: `useShellMode({ fold: true })`.
   - **Lay your own panes** (list beside entry): `useShellMode({ panes: true })`, then render `<Pane>` elements
     (`import { Pane } from "../shell"`), e.g. `<Pane class="side-pane" glow={false}>…</Pane><Pane entry vt="entrypane">…</Pane>`.
   - **Transitions:** links transition by themselves; mark direction with `data-vt="push" | "pop" | "entry"`.
     For a change without a link: `withTransition(() => setMode("rail"))`; to navigate from code:
     `const go = useNavigate(); go(url, { kind: "push", replace })`.
   - **Write:** `openWrite({ for: { kind: "answer", practice: id }, from: e.currentTarget })`; the surface reads
     `useWriteRequest()` and calls `closeWrite()`.
5. Check it at 1280 and 390, light and dark (`web/shots`), with the e2e 44 px test, and with reduced motion.

**Common mistakes.** A hex or a raw px size where a token exists. Her ink on a button or a link. His face on her
words, or hers on a label. Capitals with tracking. A badge in a box. A hover-only control. A `details/summary` where
`Disclosure` gives the animated, inert-when-closed fold. A `position: fixed` popover inside a pane with `transform`,
`filter`, `backdrop-filter` or `container-type` (use `Menu`/`Sheet`, which use the top layer). Forgetting the dark
copy of a token. An inline `style="…"` string (the CSP drops it).

**Where things are.** Tokens: `web/src/tokens.css`. Base and voices: `web/src/styles/base.css`. Components:
`web/src/ui/`. Shell: `web/src/app.tsx`, `web/src/shell/` (`shell.css`, `mode.ts`, `transition.ts`, `write.ts`,
`Pane.tsx`, `NextFrom.tsx`). Theme and ink: `web/src/theme.ts`, `web/src/ink.ts`. Icons and mark:
`web/src/icons.tsx`, `web/public/icons/`. Faces: `web/public/fonts/` (sources in `tools/web_fonts.py`). The style
guide: `web/src/routes/design.tsx` (`/design`). The accepted prototype and its notes: `design/rounds/02-combined/`.
The owner's words: `design/state.md`.

**CSS convention.** One sheet per component or route, beside its TSX and imported by it; class names are the
component's own (kebab case: `.letter`, `.num-stepper`, `.calendar-grid`); a route scopes under its root class.
Order: `tokens.css`, `styles/base.css`, `styles/legacy.css` (imported first in `main.tsx`), then every component's and
route's sheet as the app imports it, so the system wins a tie with the old sheet. Breakpoints: phone `max-width: 760px`;
the folded rail `max-width: 1100px`; touch `(pointer: coarse)`; an entry's margins by container query on its pane.
