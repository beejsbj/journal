# Web app inventory (2026-10-28, at main 2185825)

Surfaces, states, what the tests pin, behaviour a reskin must keep. Paths relative to `web/`.



## 1. Surfaces

### Shell (`src/app.tsx`)
- **Router and shell:**
  - Routes: `/`, `/entries`, `/entries/:stem`, `/library`, `/library/:id`, `/library/:id/edit`, `/library/:id/default`, `/new-practice`, `/runs/:id`, `/insights`, `/settings`, plus a NotFound default (L37-49). `/pair` is handled before the router (L64).
  - Markup: `div.shell > header.topbar > a.brand(Mark.brand-mark + span.wordmark)`, then `nav.nav[aria-label=Main]`, then `a.gear[aria-label=Settings]`. After the header come `OfflineRibbon`, `main#main > ErrorBoundary > Router`, `QuickAdd` (only while open) and `UpdateToast`.
  - `nav.nav` holds Today and Entries links, then `button.plus[aria-haspopup=dialog]` with `Plus` and `span.plus-label` "Write", then Library and Insights links. Each link is `a[aria-current=page] > Icon.nav-icon(22px) + span`.
  - The "Library" tab also matches `/new-practice` and `/runs/*` (L24).
- **Auth states (full-screen, no nav):**
  - Lost with a non-Tailscale sign-in: `PairPanel title="Not paired"` (L66-73).
  - Lost with Tailscale: `TailscaleLost` (L65).
- **CSS used:** `.topbar .brand .brand-mark .mark-ring .nav .nav-icon .plus .plus-label .gear`, `main`, `.page-head .eyebrow .crumb`.

### Routes

**Today** (`routes/today.tsx`)
- **Renders:**
  - `div.page.today` with `header.page-head.today-head`. The `h1` holds `.dow` and `.dmy`. Under it sit `.sub` ("09:12 IST · Hillside"), `.weather` and `OsmCredit` (`.credit`).
  - `WeekStrip`.
  - `section.practices > ul.cards > li.card.practice.{state}`. Each card has `p.practice-when` (a `time` plus a day-part `span`), `header.practice-head` (`.emoji`, `.practice-title` with `h3` and `p.pill.{state}`), `PromptMessage`, `a.his-reply` (`.who`, `.words`), `div.actions` and `.saved`. A card can also hold `.inline-composer` or `.error-line`.
  - Programs section: `li.card.run` (RunCard/HeldRunCard), with `.progress-bar` and `.pill.warn` "On a break".
  - "Questions coming later today": `ul.plain-list.interrupts`, each item a `time` plus a span.
  - Fallback `p.card.program`.
  - "Written today": `ul.plain-list.written` with `a.written-row` holding `.kind` and `.first-line`.
  - `section.carry` ("To carry forward", `blockquote`, `a.quiet-link` "Read the reflection").
  - "Due today": `ul.plain-list.tasks`, with `li.more` for the overflow.
- **States:**
  - Loading: `Loading rows=4` (L181).
  - Error: `ErrorLine` with Retry.
  - Empty: `Empty` "Nothing scheduled today." (L204), and "No program running. Start one from the Library." (L246-253).
  - Per-card states are `upcoming | open | answered | missed`; `rowState()` gives the pill text (L21-32):
    - upcoming: "at HH:MM" or "later today"
    - open: "waiting since …"
    - answered: "answered …"
    - missed: "came at …"
  - Stale/offline comes from the global ribbon.
- **Controls:**
  - Reply (primary, only when open or missed). It opens an inline Composer with a "Not now" link.
  - "Send now" (upcoming or missed, practice enabled). It changes to "On its way…" until a `ledger-changed` event.
  - "Open the entry" (`a.button.open-entry`).
  - RunCard: Continue (`a.button.primary`) and Pause/Resume. HeldRunCard: Open and ConfirmButton "Stop the questions" → "Cancel its questions?" / "Cancel them" / "Keep".
- **Live reload:** `ledger-changed`, `entries-changed`, `runs-changed`, `library-changed`, `tick` (L178).

**Entries** (`routes/entries.tsx`)
- **Renders:**
  - `div.page.entries`, `header.page-head` (eyebrow "History", `h1` "Entries"), and `form.search[role=search]` with `Search` icon and `input[type=search]` (aria-label and placeholder "Search your words").
  - `div.chips.filter-chips[role=group][aria-label=Filters]` with `button.chip(.on)[aria-pressed]`: "All", a chip per enabled library item (`{emoji} {title}`; practices first, then programs) and "Has audio".
  - `p.active-filter` ("Showing Wed 14 Oct" plus a `.link` "Show all" with a Close icon).
  - Then `section.week > h2.week-head`, `div.day#day-YYYY-MM-DD > h3` and `div.rows > a.entry-row` (EntryRow).
  - A `button.button.wide` "Load more" ("Loading…" while busy).
- **EntryRow** (`components/EntryRow.tsx`): `.emoji`, `.main` (`.line1` with `.title` and `time`; `.first-line`, falling back to "…"; `.chips` holding `.chip` and `.chip.person`) and `.marks` (icons with aria-labels "starred", "annotated", "has a conversation", "has audio").
- **States:**
  - Loading rows=5.
  - Error with Retry.
  - Empty: "No entries match." when any filter is active, otherwise "No entries yet."
  - Load-more error: `ErrorLine` with Retry.
- **Filters** are URL query params and the route is replaced, not pushed (L395, `route(url, true)`):
  - `kind`, `program`, `day`, `audio=1`, `q`.
  - The API params are `kind`, `program`, `from=day`, `to=day`, `audio=1`, `q`, `limit=40`, `cursor` (L318-327).
  - Kind and program are mutually exclusive; All clears kind, program and audio.
  - Search is debounced 350 ms and also fires on submit. An outside `?q=` fills the box.
  - A "Load more" page is dropped whenever the first page or the filters change (`origin` identity, L427-455).
  - The WeekStrip links here as `/entries?day=…`.

**Entry** (`routes/entry.tsx`)
- **Renders:**
  - `div.page.entry`, `p.crumb` ("‹ Entries"), and `header.page-head`: optional `.eyebrow`, `h1`, and `p.sub` with `span.date` and a `.link` "Mood, people, star" (only when `doc.writable`).
  - `p.chips` showing starred, mood and people chips.
  - `div.tabs[role=tablist][aria-label=Entry]` with `button[role=tab]#tab-entry/#tab-analysis`, class `on`, `aria-selected`, `aria-controls`.
  - `div[role=tabpanel]#panel-{tab}` holding `Document` or `Analysis`.
  - `Add` (entry tab only).
  - `Sheet title="Mood, people, star"` containing `MetaSheet`.
- **Document** (`components/Document.tsx`), `div.document`:
  - `PromptMessage` (folded: `details.questions`, summary "The N questions" or "The question").
  - `article.block.his#key[aria-label="{name}, {at}"]` containing:
    - `.margin` (`time`, `.source` with Mic and "voice · 2:31")
    - `.body` (`p.replying` with Reply icon and `q`, `.pictures a > img`, `audio[controls][preload=none]`, `p.words` or `p.badge.pending` or BlockEditor, `.block-actions`)
  - `details.block.hers#key > summary(.who, time, .peek, Chevron.chev) + .callout-body(Markdown)`
  - `div.phase-divider[role=separator]` ("Phase N · Title" or "Interrupt N")
  - `section.summary` (h3 "Summary")
  - `p.empty` "Nothing written yet."
- **Analysis tab:**
  - `div.analysis`; `section.analysis-section` for "{her}'s annotations", "{her}'s notes" (`div.note` with `time`), Reflection (`ReflectionInline`: `h3#refl-h`, `.refl-title`, Markdown, link "In Insights") and "Program summary".
  - Empty: "Nothing here yet: {her} adds notes after the entry settles."
  - The reflection is read per entry via `api.reflections({from,to,kind:"reflection"})`.
  - "The reflection is not available." when missing.
- **Add** (below the document, entry tab only), by case:
  - Program entry: `p.composer-closed` with link "Answer in the program".
  - Reflection/week kind: `p.composer-closed` with a message.
  - Not writable: `p.composer-closed` "This page is yours…".
  - Otherwise: `section.add[aria-label="Add to this entry"]` containing a Composer with submit label "Add to the entry".
- **States:**
  - Loading rows=4.
  - 404: `Empty` "Not an engine entry: the journal has no entry by this name."
  - Error with Retry.
  - Stale: ribbon, and BlockButtons render nothing (L218).
- **Controls:** tabs; "Mood, people, star" (opens the Sheet); Edit and Delete per his-block; Composer.
- **Live reload:** `entries-changed`, `ledger-changed`, `library-changed`, `tick`.

**Library** (`routes/library.tsx`)
- **List:**
  - `div.page.library`, `header.page-head` with `.head-actions > a.button.primary` "New practice".
  - `section[aria-labelledby=g-{Group}] > h2#g-Group` (groups in `GROUP_ORDER` Situational, Daily, Frameworks, then others alphabetically) with `ul.cards > li > a.card.library-card(.off)`.
  - Each card has `.emoji`, `.main` (`.title`, `.meta`, optional `.progress-bar` and `.meta` "N of M sessions") and `.badges` (`.badge.source.{src}` with text built-in/yours/edited, `.badge.changed` "default changed", `.badge.off` "off", `.badge.active` "running").
  - States: loading rows=4; `Empty` "The library is empty."; error.
- **Detail** (`div.page.library-detail`):
  - `p.crumb`; `header.page-head` (eyebrow "{source} · {cadence}[ · off]", `h1` with emoji, `.sub` = plain opener).
  - Practice actions in `div.actions.detail-actions`: "Send now" (enabled only), `a.button.primary` Edit, "Turn off"/"Turn on", and ConfirmButton Delete ("Delete this practice?") for user-source only.
  - Program actions: `a.button.primary` "Continue" or `button` "Start" ("Starting…"), Edit, `p.hint` "This program is turned off.", an `error-line` with "Carry on with it" link (`active-run` / `program-in-progress`), and "Earlier runs" (`ul.plain-list.runs`, `.kind` + span).
  - `EditedNote` (`section.edited-note`: h2 "Edited", `p.default-changed`, "View the default"/"See what changed", ConfirmButton "Reset to default").
  - `DefinitionView`: `section.problems[role=alert]`, `section.session-set` (`li.phase-line`, `.phase-title`, `.phase-type`) or `section.question-set > ol`, `p.companion-line` (`.label` "For the companion"), and `details.raw > pre` "The file".
  - States: 404 `Empty` "No practice or program by that name."; loading rows=4.
- **Default** (`/library/:id/default`): `div.page.library-detail.library-default`, ConfirmButton Reset, `section.diff-section` / `details.diff-section` with `div.diff[role=group] > div.diff-line.{file|hunk|ins|del|same} > span.mark + span.text`. 404 `Empty` "Nothing ships under that name…".

**Editor** (`routes/editor.tsx`; `div.page.editor`)
- **Practice form** (`Editor`):
  - `div.tabs[role=tablist][aria-label=Editor]`, `button#tab-form` "Form" and `button#tab-raw` "Raw Markdown".
  - `.field` blocks with ids `f-title f-emoji f-collapse f-closes f-mode f-plain f-companion f-id`.
  - `label.check`, `fieldset.schedule > legend "When"`, `.slot` rows (`input[type=time]`, `select`, Remove link, `.field-error`), `h2.form-h` "Questions", `QuestionSet`, `details.more > summary "More options"`.
  - `.editor-raw` (`textarea#f-raw.raw-text`, `.hint`).
  - `.problems[role=alert]` and `.error-line` (the "leaving" warning, `Conflict` with Reload/Overwrite).
  - `.hint.ok` "Looks good."
  - `.editor-actions` (primary "Create"/"Save", `a.button` Cancel).
  - Validation runs 400 ms after typing (`?validate=1`).
- **Program/situational** (`FilesEditor`): `p.hint.files-hint`, `.field` with `select#f-file`, `textarea#f-raw`, Save, "Undo changes", Cancel.
- **QuestionSet** (`components/QuestionSet.tsx`):
  - `div.question-editor > fieldset.q-section`, with `.q-section-head` (`input.section-title`, icon-buttons "Move section N up/down" using local `Up`/`Down` SVGs, "Remove section" link).
  - `ol.q-list > li > .q-row` (`textarea#q-{key}` + `.q-tools` with "Move question N up/down" icon-buttons and "Remove" link), `p.hint` ("Keeps its id …"), `.field-error`.
  - Links "+ Add a question", "+ Add a section", "Make it a plain list" (`.q-structure`).
- **NewPractice:** `Empty`/Await rows=3, `.sub` text, `Editor base=null`.
- **Unloadable override:** message paragraphs plus "Back to {title}" (L452-461).

**Run** (`routes/run.tsx`; `div.page.run`)
- **Renders:**
  - `p.crumb` "‹ Today"; `header.page-head` (eyebrow = status label, `h1`, `.sub`, `Progress` as `.progress-bar[role=progressbar]`).
  - `ol.phase-strip[aria-label="Phase N of M"] > li(.done|.now)` with visually-hidden text.
  - `.controls > .actions` with buttons from `run.keyboard` (Next primary, Skip, Back, Pause, Resume) and ConfirmButton Abandon ("Abandon this run?").
  - `section.phase`: eyebrow, `h2#phase-h`, `.phase-sent` (reflect type) or `.phase-block` items, `.pill.answered`/`.pill.warn`, `Composer` (submit label "Add to my answer"; closed reasons shown as `.composer-closed`), `p.saved[role=status]`.
  - Insights section: `details.raw` "The compiled document" or a "Compile insights" button.
  - Sessions list (`ol.plain-list.sessions > li.{status}`) and a link "The program in the Library".
- **States:** 404 `Empty` "No run by that name."; `Empty` "The next session begins when you press Next."; "Moving on…" wait text with `.hint[role=status]`; `moved-on` error line.

**Insights** (`routes/insights.tsx`; `div.page.insights`)
- **Renders:**
  - Eyebrow "Week by week", `h1`, and `.stepper[role=group][aria-label="Choose a week"]` with "‹ Earlier", `p[aria-live=polite]` (`strong` week range, `span.sub` count) and "Later ›".
  - `article.week-card`: `dl.counts` (Entries, Words, Answered, Missed), `p.progress`, "The week's review" (`h2#review-h`) and "Day reflections" (`h2#days-h`).
  - Each reflection is `details.reflection#stem(open) > summary + Markdown + p.from` (source links).
- **States:**
  - Loading rows=3 (page), rows=2 (reflections).
  - `Empty` "Nothing to show yet." and "No day reflections this week."
- **Behaviour:** 26 weeks; `#hash` opens the reflection's week and scrolls it to centre.

**Settings** (`routes/settings.tsx`; `div.page.settings`)
- Sections, in order:
  - **Pairing** ("This browser"): `.panel` with four variants.
    - Asking: `.hint[role=status]` "Asking the server…".
    - Tailscale: "Signed in via Tailscale as …".
    - Unknown: "Couldn't confirm how this browser is signed in…" with "Check again", and "Unpair" only if the last known sign-in was cookie or token.
    - Paired: "Paired as {host}" plus "Unpair".
  - **InkChoice** ("Ink"): `fieldset.inks > label.ink(.on)[data-ink] > input[radio name=ink] + .ink-name(.ink-note) + .ink-sample(.ink-from "From the companion", .ink-words "How did today go?")`.
  - **SettingsEditor**: `form.settings-form` with one `section.settings-section > h2 + .section-note + .panel` per group. Each `Row` is `.field.setting > .label-row(label + .tag) + control + hints`. The place group ends with `UseMyLocation` (`.locate`). Then `.error-line`, `.saved`, `.save-bar(.dirty)` (`.save-count`, "Save settings").
  - **"Everything, as the server reads it"**: `details.settings-section.diagnostics` with `details.settings-group` (`.key`, `.origin`) and `dl.kv`.
  - **Versions**: `dl.kv`, `.warning[role=alert]`, and `details` for Next sends and Warnings.
  - **Health check**: `details.diagnostics > table.doctor` with `.pill.{status}`.
- **States:** each of Pairing/Settings/Version/Doctor has its own Await (rows 3/1/2) with Retry.
- **Locate states:** idle, "Finding you…", "Saving…", done (`.saved`), failed (`.field-error` or `ErrorLine`). Unsupported browsers get `p.hint` "This browser can't share its location here; type the place instead."

**Pair** (`routes/pair.tsx`)
- **PairPanel:** `main.pair-screen > Mark.pair-mark(44) + h1 + p.pair-intro`, "Pairing…" status, `ErrorLine`, `form.pair-form` (`label[for=pair-code]`, `input#pair-code`, "Pair" primary).
- **TailscaleLost:** `main.pair-screen.tailscale-lost` with h1 variants, a `button.wide` Retry ("Checking…"), a `p.error-line[role=alert]`, and `details.pair-fallback` (open when refused).
- **Note:** the shell uses `<main>` here but has no `#main`.

### Components (`src/components/*.tsx`)

| Component | Renders / states | CSS classes |
|---|---|---|
| **BlockActions** | `BlockEditor` form (hidden label, textarea `#edit-{key}`, `.lost-draft` with readonly textarea plus Copy and "Let it go", `.hint` "Your unsaved edit from before is back in the box.", `.offline-note`, `.error-line` or conflict, Cancel/Save). `BlockButtons`: Edit, Delete via ConfirmButton, `.offline-note`, error with "Reload the entry". Returns null when stale. | `block-actions link confirm block-editor lost-draft hint offline-note error-line composer-actions button primary visually-hidden` |
| **Composer** | `.composer-kept[role=status]` (put back or let go); `form.composer` (hidden label, textarea, `.clip` with `audio` + `.hint` "Voice note · 0:12" + Discard, `.offline-note`, `ErrorLine`, `.composer-actions` with `Recorder` and submit "Add" or "Saving…"). `closed` prop gives `p.composer-closed`. | `composer composer-kept composer-actions composer-closed clip offline-note hint link button primary` |
| **ConfirmButton** | Asks in place (see Behaviour) | `link\|button`, `confirm`, `button danger` |
| **Document** | See Entry above | `document block his hers margin source body words replying pictures badge pending phase-divider summary empty callout-body peek who chev` |
| **EntryRow** | See Entries above | `entry-row emoji main line1 title first-line chips chip person marks` |
| **Markdown** | `div.md` with `p`, `ul`, `strong`, `em` (safe renderer) | `md` |
| **MetaSheet** | `form.meta-form`: `label.check` (checkbox + Star icon "Starred"); `Tags` (`.field`, `label[for=tag-Mood/People]`, `p.chips` of `button.chip` "x ×" with aria-label "Remove x", `input#tag-…` placeholder "Add, then press Enter"); error/409 "Reload the entry"; Save | `meta-form check field chips chip error-line button primary` |
| **OfflineRibbon** | `p.ribbon[role=status]` "offline, as of 09:12", null if fresh | `ribbon` |
| **OsmCredit** | `p.credit` "Place name © OpenStreetMap contributors" with link | `credit` |
| **Prompt (PromptMessage)** | `section.prompt[aria-label="{her}'s prompt"]`, `p.prompt-meta` (`.letter-from` "From", `.who`, `.sent` "Sent <time>"), `p.opener`, then `details.questions` (fold) or `div.questions.open` containing `.question-sections > .question-section > h4 + ol\|ul > li(.asked) + .asked-mark " · asked"`. Returns null if empty. | `prompt prompt-meta letter-from who sent opener questions open question-sections question-section asked asked-mark chev` |
| **QuestionSet** | See Editor above | |
| **QuickAdd** | `Sheet`: menu `ul.plain-list.quick-menu > button.button.wide-left` for Quick note / Voice note / Answer today's prompt / Start a program. Sub-views have a `.link.back` "‹ Back". Note/voice use Composer; Answer uses `.picks` buttons; Start lists program buttons. Saved is `p.saved[role=status]` "Saved as a note. / Saved." + "Open the entry". Empty: "Nothing is waiting for an answer right now." and "Every program is already running, or none is turned on." Loading: Loading rows=2. | `plain-list quick-menu picks button wide-left primary back link saved` |
| **Recorder** | `.recorder` with a button (Mic + "Voice", `aria-label="Record a voice note"`) or recording state (`.button.recording`, `aria-label="Stop recording"`, `.rec-dot`, `time`). `p.hint[role=alert]` for problems. Unsupported: `p.hint` "Recording needs a secure page and a microphone…". | `recorder button recording rec-dot hint` |
| **RunCard** | RunCard / HeldRunCard (see Today). Exports `Progress`, `holdsQuestions`, `sessionsDone`, `CLOSED`. | `card run emoji main meta actions progress-bar pill warn button primary` |
| **Sheet** | `div.sheet-backdrop > div.sheet[role=dialog][aria-modal][aria-label][tabindex=-1] > header.sheet-head(h2 + button.icon-button[aria-label=Close])` | `sheet-backdrop sheet sheet-head icon-button` |
| **Status** | `Loading` (`.skeletons[role=status][aria-live=polite][aria-label=Loading] > .skeleton*N`), `ErrorLine` (`.error-line[role=alert]`, optional Retry; "Can't reach the journal server. " prefix when `slug==="network"`), `Await`, `Empty` (`p.empty`) | `skeletons skeleton error-line empty` |
| **UpdateToast** | `div.toast[role=status]` "Update available" + Later + Reload (primary) | `toast button primary` |
| **WeekStrip** | See Behaviour | `week-strip day dow dot dom answered partial missed none today` |

---

## 2. `app.css` structure

921 lines. Tokens come from `src/tokens.css` (172 lines).

### Sections (comment headers)

| Lines | Section |
|---|---|
| 1-10 | Preamble: the "Margin × Letters" rules |
| 12-42 | Base reset, `a`, `time` (typed), `code/pre`, `q`, `::selection`, `:focus-visible` (no header) |
| 44-85 | shell: `.topbar .brand .nav .plus .gear main` |
| 87-119 | page heads, section heads, typed labels: `.page-head .eyebrow .sub .crumb`, `section > h2`/`.week-head`/`.settings-section h2` (italic serif with a rule running out), `.section-note .empty .subhead` |
| 121-181 | buttons, links, chips, states: `.button` variants, `.link`, `.chips/.chip`, `.pill`, `.badge`, `.tag` |
| 183-205 | loading, errors, notes: `.skeletons/.skeleton`, `.error-line`, `.warning .hint .field-error .saved .visually-hidden` |
| 207-238 | cards and lists: `.cards`, `.card`, `.plain-list`, `.actions`, `.confirm` |
| 240-277 | her letter: `.prompt` family, question sections, `summary`/`.chev` |
| 279-366 | Today: head, weather, credit, `.week-strip`, `.card.practice` (margin-hour grid), `.his-reply`, `.inline-composer`, run cards, carry/tasks/interrupts |
| 368-391 | Entries: `.search`, `.week-head`, `.day`, `.rows`, `.entry-row` |
| 393-454 | the entry: `.tabs`, `.document`, `.block.his`, `.block.hers`, `.phase-divider`, `.summary`, `.analysis-section`, `.md`, `.note` |
| 456-474 | (no header) `.block-actions`, `.block-editor`, `.lost-draft` |
| 476-495 | (no header) `.composer*`, `.clip`, `.recorder`, `.rec-dot`, `.add`, `.offline-note` |
| 497-527 | sheets: `.sheet-backdrop .sheet .sheet-head .quick-menu .picks .back .icon-button` |
| 529-575 | Library: `.head-actions .detail-actions .question-set .companion-line .raw .problems .edited-note .diff* .progress-bar .sessions` |
| 577-674 | forms: `.field .row .check .schedule .slot .more .editor-* .q-*` (577-612), Settings (614-654: `.panel .settings-form .locate .save-bar .diagnostics .kv .settings-group .doctor`), Inks (656-674) |
| 676-711 | Run and Insights: `.phase*`, `.phase-strip`, `.controls`, `.stepper`, `.counts`, `.reflection`, `.from` |
| 713-726 | pairing and sign-in: `.pair-*` |
| 728-747 | offline and updates: `.ribbon`, `.toast`, reduced-motion |
| 749-791 | the spread (≥1024px) |
| 793-909 | phone and touch (797-828 and 830-909), 911-916 desktop-only (≥641), 918-920 standalone |

### Media queries / breakpoints
- `min-width:641px` L513 (sheet centred), L911 (frosted sticky topbar, nav icons hidden)
- `max-width:520px` L650 (doctor table to blocks)
- `max-width:640px` L743 (toast to top)
- `min-width:1024px` L753 (spread, `main:has(.page.today,.page.entry)`)
- `max-width:640px, pointer:coarse` L797 (44px targets, 16px inputs)
- `max-width:640px` L830 (phone layout: `--gutter:0`, fixed bottom nav, sticky composer/save bar)
- `prefers-reduced-motion:reduce` L745 (kills all animation and transition, `!important`)
- `display-mode:standalone` L918 (`overscroll-behavior-y:none`)

### Animations and transitions

| Line | Rule | Value |
|---|---|---|
| L66 | `.nav a` | color, box-shadow `var(--dur)` |
| L75 | `.nav .plus` | transform, background |
| L128 | `.button` | background, border-color, transform |
| L214 | `a.card` | border-color |
| L276 | `.chev` | transform (rotates 90° when `details[open]`) |
| L398 | `.tabs button` | color, box-shadow |
| L662 | `.ink` | border-color |
| L189-191 | `.skeleton` | `shimmer 1.6s linear infinite` |
| L492-493 | `.rec-dot` | `pulse 1.2s ease-in-out infinite` |
| L501, L510 | `.sheet-backdrop` | `fade var(--dur)` |
| L505, L509 | `.sheet` | `rise 200ms var(--ease)` (translateY 1.5rem to 0) |
| L78, L132 | press-scale | `.plus:active` 0.96, `.button:active` 0.98 |

All other motion is `--dur` 160ms with `--ease` cubic-bezier(0.2,0.7,0.2,1).

### Raw values not from `tokens.css`
- **Colour:** only `#000` at 38% for the sheet backdrop (L501). Everything else is `var(--…)`, `color-mix(...)`, or the literal `--lift` shadow in tokens.
- **Font sizes (rem literals):** 0.625, 0.6875, 0.72, 0.8, 0.8125, 0.84, 0.86, 0.875, 0.9, 0.9375, 0.95, 0.97, 1.05, 1.0625, 1.1, 1.15, 1.2, 1.25, 1.35, 1.45, 1.5, 1.55 and 2.6 (the phone `h1`, L97/L836).
- **Font weights:** 330, 350, 420, 450, 550 (variable-font weights), plus 400/500/600/700.
- **Font variation:** `"opsz" 18/20/24/36/60/72`, `"SOFT" 100`.
- **Spacing and sizes:**
  - Heights: 2.25, 2.4, 2.5, 2.75, 3, 3.25rem; 4.5rem (skeleton); 0.4 and 0.65rem (dots).
  - Widths/measures: 5.5rem (hour margin), 26rem (pair), 34rem (sheet), 20rem, 17rem, 14rem, 12rem, 8rem.
  - Other: 88vh and 92dvh sheet heights; 26rem image max height; 1.25rem page gutter literals; `max(1.25rem, env(...))` insets.
- **Radii:** 0, 6px (phone `.plus`, L857), 999px (chip), 50%, 4px (sheet grabber).
- **Blur:** 12px and 14px backdrop.
- **Borders:** 1px, 1.5px, 2px, 3px.
- **z-index scale:** 5, 10, 20, 30, 40.
- **Letter-spacing:** 0.01 to 0.14em.
- **Text-wrap and word-spacing:** `word-spacing:-0.15em` / `-0.1em` on all of her Courier text, and `text-wrap:balance`.
- **Classes with no CSS rule (hooks for tests):** `.shell .wordmark .week .week-card .library-card .library-detail .library-default .session-set .phase-line .phase-title .phase-type .runs .settings-groups .question-editor .open-entry .tailscale-lost`.
- **CSS rules with no matching TSX class:** `.phase-questions` and `.subhead`.

Fonts are declared in `tokens.css`: Newsreader, Instrument Sans, Courier Prime and Fraunces Figures (a digits-only subset), served from `public/fonts` and precached.

---

## 3. What tests pin

Vitest runs on jsdom, so it never loads `app.css`. Only the e2e specs see layout. Two unit tests read `tokens.css` as text (see 3e).

### 3a. Unit tests (`tests/*.test.tsx`) — selectors, classes, roles and strings

**`routes.test.tsx`**
- **Today:**
  - `h1` text "Thursday 15 October 2026"; `.sub` text "09:12 IST · Hillside"; `.weather`.
  - `.week-strip a.day` ×7; `li.practice h3`; `.pill` texts "answered 06:41", "at 23:00", "came at 06:00", "waiting since 20:05", "answered 21:38".
  - `.carry blockquote`; `.tasks`.
  - Class order: `li.practice` has `classList[2]` = state, so the markup must stay `class="card practice {state}"` (L64).
  - `.practice-when[aria-hidden]` is "true" when the line under the title says the same time (null otherwise); `.practice-when time`.
  - `.prompt .opener`, `.prompt li` count, `.prompt details` absent on Today, `li.asked`, `.prompt-meta .who`.
  - `a.his-reply[href]` with `.who` "Sam · 21:38" and `.words`; `.his-reply` is not inside `.prompt`.
  - Buttons in a card are exactly ["Reply","Send now"].
  - `.today-head .credit` with `a[href]` to OSM.
- **Pair:** `.pair-screen`, `h1` "Not paired", no `.nav`; `.tailscale-lost`; h1 strings "Couldn't confirm your Tailscale sign-in" and "This device isn't recognised by Tailscale any more"; `[role="alert"]` containing "may be restarting"; `.pair-fallback[open]`; `input#pair-code`; button text "Retry".
- **Entries:**
  - `.entry-row`; `.week-head` "Week of 12 Oct"; `.day h3` ["Thu 15 Oct","Wed 14 Oct"]; `a[href="/entries/…"]` with `.title`/`.first-line`; `[aria-label="annotated"]`, `[aria-label="has a conversation"]`.
  - `.active-filter` containing "Wed 14 Oct"; `button.wide`; `.empty` "No entries match."
- **Entry:** `.document`, `#tab-analysis`, `.analysis` text ("Ada's annotations", "A note of hers.", "To carry forward"), `.refl-title`, `.empty` "Not an engine entry".
- **Library:** `.library-card`, `.meta` "Daily 21:30", `.badge.source` "yours", `.question-set li` ×2, `.raw pre`.
- **Insights:** `.week-card`, `.stepper strong` "12 – 18 Oct", `.stepper button`, `.counts` text "Words32" (dt then dd, no separator), `.reflection summary` "Ada's reflection — Wed 14 Oct 2026", "No day reflections this week."
- **Settings:** `.settings-group`, `.key`, `.origin` "config file", `.warning`, `table.doctor tbody tr` ×2, text "Paired as localhost:3000" / "Signed in via Tailscale as …", buttons "Unpair" and "Check again".

**`document.test.tsx`**
- `article.block.his` ×4, `.words`, `.margin time` "23:12 IST", `.source` containing "voice" and "2:31", `audio[src]`.
- `details.block.hers` closed, `summary .who`, `summary time`, `.callout-body`; no `details` inside `article.his`.
- `.replying` with `q`; `.prompt` containing "Sent 23:00 IST"; `.opener`; `details.questions` closed; `li.asked`.
- `.phase-divider` texts "Phase 1 · Current Reality Excavation" and "Interrupt …".
- `.document` last child's first class is `summary`; `.summary`.
- A read-only document has no `button, textarea, input, [contenteditable]`.
- `.block.his .pictures img[src]`; `.badge.pending` "transcript pending"; `.words` absent for pending.

**`blocks.test.tsx`**
- `.document article.block.his`; `.block-actions button` with texts "Edit" and "Delete"; none under `details.block.hers`; no `button/input/textarea/[contenteditable]/[role=button]` in her blocks.
- `.block-editor textarea`, `.block-editor .error-line`, `.block-editor .hint`, `.lost-draft textarea`, `.lost-draft button` "Let it go", `.block-editor .offline-note`.
- Buttons "Save", "Cancel", "Retry" (absent on 409), "Reload the entry".
- `.confirm` text "Delete this block", buttons "Keep"/"Delete"; `.words` removed while editing.
- Error substrings "changed since you read it", "engine's own marker", "unsaved edit", "You're offline".
- `.ribbon` present when stale.

**`writes.test.tsx`**
- Composer:
  - `.composer textarea`, `.composer .error-line`, `.composer .offline-note`, `.composer-closed`, `.composer-closed a[href]`, `.composer-kept` (text "unsent words are kept"), `.recorder` (absent), `.composer-actions`.
  - `[aria-label="Record a voice note"]`, `[aria-label="Stop recording"]`, text "Voice note", button "Add to the entry", "Recording needs a secure page".
- Meta sheet:
  - `[role="dialog"]` with `#tag-Mood`, `input[type=checkbox]`, "Mood, people, star", "Save", "Reload the entry", `.page-head`.
- Today cards:
  - `li.practice.open`/`.upcoming`, `.saved a[href]`, "On its way", `li.run` ("session 1/10", `a.button[href]`, `[role=progressbar]`, "Pausing…", "On a break", "Resume", "still on their way", "Stop the questions", "Cancel them").
  - `.interrupts li time`; `.empty a` (Library); `.card.program` text "fix-life: session 1"; text "No program running".
- Run screen:
  - `.phase h2`, `.phase-strip[aria-label="Phase 1 of 6"]`, `.phase textarea`, `.phase .saved`.
  - `.controls button` texts exactly ["Next","Skip","Pause","Abandon"] (or ["Resume","Abandon"]), `.controls .confirm button`, `.controls .error-line` ("has moved on"), `.phase-sent` ("Written by the companion"), "Compile insights", button "Add to my answer".
- Library:
  - Buttons "Send now", "Turn off", "Start"; `a.button` Edit (href), "Continue", "See what changed".
  - `.edited-note` (`.default-changed`, `.confirm`), `.eyebrow` text, `.badge.source` "edited", `.badge.changed` "default changed".
  - `[aria-labelledby="since-h"] .diff`, `.diff-line.ins/.del`, `.diff-section summary` "Your version against the default", `a[href="/runs/r9"]`, "Carry on with it".
  - `a[href="/library/fix-life"] [role="progressbar"]` and text "0 of 10 sessions".
- Settings:
  - ids `#s-windows\.collapse_hours`, `#s-today\.enabled`, `#s-conversations\.quiet_minutes`, `#s-location`, `#s-timezone`, `#s-today\.max_chars`.
  - `.field` text ("In the config file.", "default applies", "hermes config set plugins.entries.journal.settings…", "At most 72").
  - `.settings-form .saved` ("windows.collapse_hours (config file)"), `.settings-form .error-line`, button "Save settings" (disabled attribute).
  - Location: "Use my location", `.locate .saved` exact "Now in Kochi, Kerala, India · Asia/Kolkata · Thu 21:40 there.", `.locate .field-error`, `.locate .error-line`, `.credit`, `.credit a`.
- `+` sheet:
  - `button.plus` text "Write", `[role="dialog"] button` "Quick note", "Answer today's prompt", "Save the note", "Reply".
  - `[role="dialog"] .saved` ("Saved as a note"), `[role="dialog"] .picks button`, closes on a `keydown` Escape on `document`.

**`editor.test.tsx`**
- `#f-title`, `#f-raw`, `#f-id`, `#f-file`, `button[role=tab]` "Raw Markdown"/"Form", `.editor-raw .hint`, `.slot .field-error`, `.problems`, `.error-line`, `details.more`, `.slot` count, `.slot select`, `.slot input[type=time]`, `.q-row textarea`, `.eyebrow` "Built-in", `.page-head .sub`.
- Buttons "Save", "Create", "+ Add a question", "+ Add a time", "Drop the text edits", "Overwrite"; text "Going back to the form drops those edits", "Changed elsewhere" with "Obsidian", "Looks good.", "Your edited file does not load".

**`review.test.tsx`**
- `button.wide`, `.filter-chips .chip` text "Has audio", `.rows a`, `window.location.search` containing `audio=1` and `q=harbour`.
- `.week-card`, `.reflection summary`, `.pair-screen`, `.error-line button`, `a.open-entry` ×2, `.carry a[href]`.
- `#g-Frameworks`, `.meta`, `.session-set .phase-line` ×2, `.questions summary`, `.questions` text "Phase 1", `.phase-divider` matching /^Phase 1 · \S/.
- `.warning` ("predates version stamps"), text "ran before versions were stamped".
- `input[type=search]`.

**`weekstrip.test.tsx`**
- `a.day` ×7, `href="/entries?day=YYYY-MM-DD"`, `.dom` text "9", classes `answered`/`partial`/`missed`/`today`, `aria-current="date"`, `aria-label` "Wed 14 Oct: answered" / "Thu 15 Oct: nothing sent" / "Mon 12 Oct: partly answered" / "Tue 13 Oct: missed".

**Others**
- `status.test.tsx`: `[role="alert"]` with "Can't reach the journal server", Retry button, `[role="status"]` for Loading.
- `update.test.tsx`: `.toast` text "Update available", buttons "Reload"/"Later".
- `offline.test.tsx`: `.ribbon` text matching `/^offline, as of (\w{3} \d+ \w{3} )?\d\d:\d\d$/`.
- `ink.test.tsx`: `input[type=radio][name=ink]` values, `label.ink[data-ink]`, `label.ink.on`, `.ink-from` text "From the companion", `document.documentElement.dataset.ink`, `localStorage["journal:ink"]`.
- `settings.test.ts` pins `settingsModel` behaviour. `api`, `format`, `markdown`, `schema`, `swcore`, `killswitch` and `pwa` tests are not DOM tests.

### 3b. `e2e/*.spec.ts` (Playwright; projects `desktop` 1100×800, `phone` 390×780 touch)

**`smoke.spec.ts`**
- Headings and counts:
  - `h1` "Thursday 15 October 2026"; `.week-strip a.day` ×7; `.week-strip a.day.answered[aria-label="Wed 14 Oct: answered"]`; `li.practice h3` ["Daily gratitude","Evening journal"].
  - `.entry-row`; `.week-head` "Week of 12 Oct"; link /Evening journal/; `h1` "Evening journal".
- Entry and her notes:
  - `article.his .words` first = "First words, about the lighthouse."
  - `details.hers` ×1, not `open`, `.callout-body` hidden then visible after clicking `summary` ("Second line."); `audio` ×1.
  - tab "Analysis" then `.analysis` "A note of hers."
- Search and library:
  - searchbox, "No entries match."; heading "Daily" (exact); link /Daily gratitude/; `.question-set li` ×2; `.week-card`.
  - Settings: text "Health check" (click), `table.doctor`, /Paired as/.
- Layout assertions:
  - `nav.nav` `y` > height/2 on the phone; `y` < 120 on the desktop.
  - Desktop spread: `.document > .prompt` is right of `article.his`, with its top within 2px of the first `article.his`; `details.hers` is between the blocks it follows (matched by text "Later I thought" / "yes, and the gulls"); the gap between those two blocks is under 60px; no `button/textarea/[contenteditable]` in `details.hers`; no "Edit"/"Delete" text in it.
  - Program session: `.document > .phase-divider` top within 2px of `.document > .prompt`, and its right edge ≤ prompt.x.
- Unpaired: `h1` "Not paired", then `/pair#code` → Today, URL has no `#`.

**`mobile.spec.ts`** (phone project only; `serviceWorkers: block`; the run route is faked from `../tests/fixtures/api/run-one.json`)
- **44px measurement (`small()`, L26-50):**
  - Selector: `a[href], button, input:not([type=hidden]), select, textarea, summary, [role=tab], [role=button]`.
  - Minimum is 43.5px width and height from `getBoundingClientRect`, run after `networkidle`.
  - Skipped: `visibility:hidden`/`display:none`, anything inside `.visually-hidden`, folded `details:not([open])` content, and `<a>` inside `p, .md, .hint, .sub, .empty` unless it matches `.crumb a`.
  - Checkbox/radio are measured by their enclosing `label`.
  - Routes: `/`, `/entries`, `/entries/2026-10-14-evening`, `/library`, `/library/gratitude`, `/library/gratitude/edit`, `/new-practice`, `/runs/2026-10-15-run1`, `/insights`, `/settings`. Each also asserts `scrollWidth - innerWidth ≤ 1`.
  - Page-ready selector: `main h1, main .page-head, main .error-line, main .empty`.
- Also measured at 44px: Analysis tab, opened `details.hers summary`, Raw Markdown tab, the `+` dialog (`role=dialog`), "Quick note" view, and `/pair` (plus input font-size ≥16px).
- No sideways scroll at 360×740 on all routes.
- Bottom bar and composer:
  - `nav.nav` bottom ≥ viewport height − 1.
  - `button "Write"` centre within 6px of the viewport centre.
  - `.add` bottom ≤ `nav.y + 1` and > `nav.y − 12`, and `.add.y > 0`.
  - After `.add textarea` tap, height > 100px.
- Safe areas:
  - `meta[viewport]` contains `viewport-fit=cover`.
  - Fetched stylesheets contain `safe-area-inset-top`, `-bottom`, `-left` and `-right`.
- Toast (entry and run): a manually injected `.toast` (markup copied from `UpdateToast`, so keep `.toast > span + .button + .button.primary`) must not cover any `.composer-actions button` and must sit in the top half of the viewport.

**`pwa.spec.ts`**
- `.ribbon` text `/^offline, as of /` and `/^offline, as of \d\d:\d\d$/`; none when online.
- `.composer .offline-note` text "You're offline; this will not be saved until you're back."; `.composer .error-line` with the same text; `.composer textarea` keeps its value after reload.
- Button "Add to the entry" (exact), `.error-line` visible for a never-kept view, button "Unpair" → `h1` "Not paired".

**`writes.spec.ts`** (phone only)
- Library and editor:
  - link "New practice"; `h1` "New practice"; `getByLabel("Title",{exact})`; `.slot input[type=time]`; `.q-row textarea`; "Looks good."; button "Create".
  - `h1` containing the title; `.question-set li`; `li.practice .pill` "at 22:00".
  - "+ Add a question", "Move question 2 up", tabs "Raw Markdown"/"Form", `#f-raw`, button "Save" (exact).
  - `.eyebrow` "Built-in"; link "Edit" (exact); `.edited-note` containing "Your version is used in place of the built-in"; link "View the default"; `.diff .diff-line.ins`; `.edited-note` button "Reset to default" then `.edited-note .confirm` button "Reset to default".
- Notes and entries:
  - Buttons "Write" (exact), "Quick note"; `getByLabel("A note",{exact})`; "Save the note"; text "Saved as a note."; Escape closes.
  - `article.his` count +1; `.composer textarea`; button "Add to the entry".
  - "/x…" yields `.composer .error-line` containing "not saved".
  - "Mood, people, star"; `getByLabel("Mood",{exact})`; `getByLabel("Starred",{exact})`; `.chips .chip` ["starred","calm"].

### 3c. `shots/` (design screenshots, not CI; as it stood before WEB-UX: it is now one suite, `screens`, `entries`, `pages`, `write` and `shell` over `shots/lib.ts`)
- **Files:** `shots/shots.spec.ts` and `shots/pair.setup.ts`; config `playwright.shots.config.ts` (phone 390×844 @2x, desktop 1280×860, service workers blocked, `--demo` fixture server on port 8792).
- **Screens, light:** `/`, `/entries/2026-10-14-evening`, `/runs/2026-10-15-run1`, `/settings`, `/entries`, `/library`, `/library/morning`, `/library/gratitude/edit`, `/library/midweek` (edited), `/library/midweek/default`, `/library/morning/edit`, `/library/fix-life/edit`, `/insights`. **Dark:** today, entry, run, settings, insights.
- **Waits:** `.skeleton` count 0 (L216).
- **Selectors it needs:**
  - `.block.hers summary`
  - `#ink-h`
  - `.edited-note`, `.edited-note .confirm`, button "Reset to default"
  - button "Write"
  - `.nav` (hidden for ink tiles)
  - `.document` (desktop ink tile), `li.practice` (phone ink tile)
  - `.brand-mark` (its `outerHTML` is regex-patched: `width="N"`, `height="N"`, `class="mark-ring"`, `currentColor` — L342-343; keep `Mark`'s attribute shape)
  - `localStorage["journal:ink"]`
- **Hard-coded colours:** `#161513`, `#f4efe4`, `#958a79`, `#6e6457` (L322-323) and the ink hexes in the "mark" board (`#1b1813`, `#28367f`, `#ece4d6`, `#b6c0ff`, `#ebe4d5`). A palette change will leave the screenshot boards stale but not failing.
- **Also reads:** `public/icons/*.png` and `public/fonts/newsreader-latin-italic-400.woff2`.

### 3d. Strings that must stay or be deliberately updated
Pill words ("answered", "waiting since", "came at", "at", "later today"), the `OFFLINE_WRITE` sentence, "Week of …", the day-label formats, `ribbonText`, "Saved as a note.", "No entries match.", "Looks good.", the `h1` strings above, "Write" as `.plus` text, and the accessible names "Record a voice note" / "Stop recording" / "Move question N up".

### 3e. Constraints on `tokens.css` (not DOM)
- `tests/ink.test.tsx`:
  - Exactly two `@media (prefers-color-scheme: dark)` blocks, the `:root` one first and the `[data-ink]` one second.
  - `--bg --surface --sunk --ink --ink-2 --ink-3 --warn --bad --accent` must be 6-digit hex in `:root` (and the dark block). The dark block only needs overrides.
  - `[data-ink="…"] { --accent: #hex; }` for each of the four inks in light and dark, each ≥4.5:1 on bg and surface.
  - `--her-soft: color-mix(in srgb, var(--accent) N%, var(--bg))` is parsed, with N>50 and soft ≥4.5:1.
  - Contrast pairs ≥4.5:1: ink/ink-2/ink-3 on bg/surface/sunk, warn on bg, bad on bg, bg on bad, bg on ink.
- `tests/pwa.test.ts`: light `--bg` must equal `#f4efe4` (manifest `theme_color`, `index.html` theme-color meta, light and dark). `scripts/manifest.ts` builds the manifest from `tokens.css`. `../tests/test_web_build.py` checks the committed build against `--bg`.
- Changing the palette therefore means updating `index.html` theme-color meta, `pwa.test.ts` (L24), and the icons (`scripts/make-icons.mjs`).

---

## 4. Behaviour a reskin must preserve

**Composer (`components/Composer.tsx`)**
- **Draft:** kept in `sessionStorage` under `journal-draft:{draftKey}` on every keystroke, cleared on a successful send. `draftKey` is one of `entry:{stem}`, `reply:{id}:{slot}`, `quick-note`, `quick-voice`, `run:{id}:{session}:{phase}`, `edit:{stem}:{key}` or `lost:{stem}:{key}`.
- **Retry:** a `client_id` makes a retry the same write. Editing the words, or recording or dropping a clip, resets the id. A failed send keeps text, clip and id; Retry is shown except on 422.
- **Upload:** a recording is uploaded first and the upload reused on retry; an upload older than 50 minutes, or a server 404 "upload", is re-uploaded under the same write id.
- **Key change:** if `draftKey` changes while words are unsent, they are shown in `.composer-kept` ("Put them back in the box" / "Let them go").
- **Offline:** when `!online`, `.offline-note` shows the sentence `OFFLINE_WRITE` plus "What you write stays here[, recording included]." The write is still attempted and fails with the same line. The submit label comes from props, and while busy it reads "Saving…".
- **Submit:** Cmd/Ctrl+Enter sends. The send is disabled when there is no text and no clip, or while busy. The textarea is disabled while busy.
- **Voice:** the `Recorder` is hidden once a clip exists; unsupported browsers see a `.hint`.

**Block edit/delete (`BlockActions`)**
- **Scope:** only his blocks; her blocks never get controls. Program/phase answers (`b.phase !== null || b.interrupt != null`) get Edit but no Delete. Nothing is offered unless `doc.writable`, and nothing at all when stale.
- **Pending transcripts:** a recording whose transcript is pending gets no buttons.
- **Editor:**
  - Save sends `{text, was, at}`, and an unchanged or blank edit is disabled.
  - Offline or stale disables Save and shows the note.
  - The draft is stored as JSON `{was, at, text}` and restored only when the basis still matches, otherwise it goes to `.lost-draft` with Copy and "Let it go".
  - A 409 `changed` shows "Your edit is kept: reload, then edit again." plus "Reload the entry"; there is no Retry on 409 or 422.
  - Block identity is `key|at|hash(text)`, so an editor closes when its block changes.
- **Delete:** a ConfirmButton asking "Delete this block from the entry?" with Delete/Keep. It sends `{was, at}`, and a 409 shows an error plus "Reload the entry". Delete is hidden when offline.

**ConfirmButton:** the first control is a `.button`, or a `.link` when `plain`. Asking swaps to `span.confirm[role=group][aria-label=question]` with a danger confirm and a "Keep" button.

**QuickAdd (`components/QuickAdd.tsx`)**
- **Modes:** the menu offers note, voice, answer, start; titles are "Quick note", "Voice note", "Answer today's prompt", "Start a program", and the sheet title for the menu is "Write".
- **Saving:** Note and Voice share the `api.createEntry(words)` path with a separate draft key. A saved note shows "Saved as a note." / "Saved." and a link "Open the entry".
- **Answer:** lists today's open prompts with "sent {time}", replies via `createEntry({practice, ...words})`, and has `Empty` when none is open.
- **Start:** lists enabled program-backed items that have no live run. A successful start closes the sheet and routes to `/library/{id}`.

**Sheet (`components/Sheet.tsx`) — no real focus trap**
- On mount it focuses the panel (`tabindex=-1`), saving `document.activeElement`.
- Escape on `document` closes it, and so does a click on the backdrop (target equals currentTarget).
- On unmount it restores focus to the saved element.
- There is no Tab-cycling trap and no inert background, and no test pins one.
- Roles are `role=dialog`, `aria-modal=true`, `aria-label={title}`.

**ink.ts**
- Four inks: blue-black (default), brown, violet, green, each with a label and a note.
- `localStorage["journal:ink"]`; choosing the default removes the key.
- `applyInk` sets `<html data-ink>` before first render (`main.tsx` L8) and the choice is device-only; nothing is sent to the server.
- Unknown values fall back to the default; storage errors are swallowed.
- CSS contract: `--accent`, plus `--her-soft` and `--edge` derived on `:root, [data-ink]`. A swatch element gets its own `data-ink`.
- Her ink is used only for her words: prompt, notes, summary, carry, phase blocks, companion-line, `.mark-ring`, ink samples, and the swatch radio's `accent-color`.

**WeekStrip**
- Seven days, each `a.day.{answered|partial|missed|none}[.today]` with `href=/entries?day=…`.
- `aria-label` and `title` read "Wed 14 Oct: answered" (words: answered, partly answered, missed, nothing sent).
- `aria-current=date` on today.
- DOM order is `.dow` (first letter), `.dot`, `.dom` (day number); CSS gives `.dot` `order:3` so it sits last visually.
- Only answered and partial dots are filled; missed is not coloured.

**Settings model (`settingsModel.ts`)**
- **Field kinds:** `text | number | whole | flag | clock`. 15 fields in 5 groups (place, prompts, reflections, chat, programs).

| Path | Label | Kind | Limits |
|---|---|---|---|
| `location` | Place | text | max 200 chars; hint "Looked up for its coordinates and time zone." |
| `timezone` | Time zone | text | max 200 chars; hint "An IANA name such as Asia/Kolkata; overrides the place's." |
| `windows.collapse_hours` | Hours a prompt stays open | number | 0.25–72 |
| `windows.grace_hours` | Hours of grace for a send | number | 0.25–24 |
| `windows.opener_budget_seconds` | Seconds an opener may take | whole | 1–900 |
| `windows.annotate_after_minutes` | Minutes before annotating | whole | 0–10080 |
| `reflections.day.enabled` | Day reflection | flag | |
| `reflections.day.fallback_time` | Day reflection, latest time | clock | HH:MM |
| `reflections.week.enabled` | Week review | flag | |
| `reflections.week.fallback_time` | Week review, latest time | clock | HH:MM |
| `conversations.quiet_minutes` | Minutes of quiet before the today block | number | above 0, max 1440 |
| `programs.settle_minutes` | Minutes of quiet before a phase settles | number | above 0, max 1440 |
| `today.enabled` | Today block in the chat | flag | |
| `today.max_chars` | Today block, characters | whole | 200–20000 |
| `today.cache_minutes` | Today block, minutes cached | number | above 0, max 1440 |

- **Default display:**
  - Flags use a `<select>` with options `""` labelled "Default", "On" and "Off". An unset flag shows "Default" because the tree does not hold defaults.
  - Text, number and clock inputs show the placeholder "default" when unset.
  - Each label row carries a `.tag` (aria-hidden) reading "config file", "Hermes", "environment" or "default", plus a visually-hidden sentence: "In the config file." or "Not set: the default applies. Saving writes it to the config file."
  - Fields owned by Hermes or the environment are disabled, with a `.hint.layer` giving the exact line that changes them.
- **Saving:** only changed keys are sent, as a nested patch. An emptied box is "leave it" (the web app cannot unset). The Save button is disabled when there are no changes or any field is invalid. After Save, `.saved` reads "Saved: key (layer), …".
- **Save bar:** `.save-bar.dirty` is sticky; its count reads "No changes" / "1 change" / "N changes".
- **Use my location:** needs a secure context; sends the position once to the server (`locate()`); drops an unsaved typed place and time zone afterwards. The OSM credit shows only when `osmNamed(settings)` and the box is unchanged.

**Analysis tab:** annotations and notes (`doc.analysis`), the per-entry reflection read (shown only when `doc.analysis.reflection` is set), and the program summary. Everything on it is rendered in her ink. The Entry tab's Add composer is hidden while on Analysis.

**Reflections disclosure (Insights):**
- Each reflection is `details.reflection#{stem}`, `open` only when it matches the hash.
- The hash chooses the week, then scrolls to centre after the week's reflections load.
- Also linked from Today's carry-forward (`/insights#{stem}`) and from Analysis ("In Insights").
- Question disclosure: `details.questions` on entries ("The N questions"); Today always shows questions open (`fold` off).
- Her note in the document: `details.block.hers`, folded to a one-line `.peek` that hides when open; on the wide spread the peek wraps and shows full-width.

**Audio player:**
- Native `<audio controls preload="none">` in his block, fallback text "Your browser cannot play this recording."; source `${BASE}/media/{file}`.
- The `.source` meta shows a Mic icon, "voice" and the duration.
- The composer preview is `<audio controls preload="metadata">` inside `.clip` with "Voice note · M:SS" and Discard.
- A pending transcript shows `.badge.pending` "transcript pending" or "not transcribed". The e2e expects exactly one `audio` in the demo entry.

**Offline and live:**
- **Ribbon:** `.ribbon` appears when any read of the current view came from the worker's kept copy (oldest time). It resets on route change (`forgetReads` in a layout effect).
- **Writes offline:** composer and block editor show the note and fail with the offline line; Delete and Edit are withheld on stale docs.
- **Events:** `useLive` events trigger reloads; reloads keep the old data on screen until the new answer arrives (never an old answer under new inputs).
- **Update toast:** Later / Reload; on a phone it goes to the top so it never covers the composer or the tab bar (mobile spec).

**Layout contracts a reskin must keep:**
- `.page.today` and `.page.entry` classes (the `:has()` spread selector, L755).
- `.card.practice` as a 3-column grid on ≥1024px (hour / his / her letter).
- `.document` is `flow-root` with her letter and notes floated into the right margin on ≥1024px (L783-790).
- On phones, `.nav` is fixed at the bottom with the `+` centred in a 5-column grid, `main` has bottom padding, and `.add` / `.phase > .composer` / `.editor-actions` / `.save-bar.dirty` are sticky above the bar.
- Inputs are ≥16px on phones and touch.

---

## 5. Icons (`src/icons.tsx`)

- **Base:** an internal `Svg` helper draws `viewBox 0 0 24 24`, default 18×18, `fill=none`, `stroke=currentColor`, `stroke-width 1.7`, round caps and joins, `focusable=false`. It is `aria-hidden` unless an `aria-label` is passed, in which case `role="img"`. Props override size, class and stroke-width. Nav icons are 22px and `.nav-icon` sets stroke 1.4 (2 when current, on phone).
- **Stroke icons:** `Mic`, `Chat`, `Note`, `Chevron`, `Gear`, `Search`, `Close`, `Star`, `Reply`, `Plus`, `Sun`, `Book`, `Shelf`, `Spark`, `Pin`, `Check`.
- **Where used:**
  - Tab bar: `Sun` (Today), `Book` (Entries), `Plus` (+), `Shelf` (Library), `Spark` (Insights).
  - `Gear` (Settings link); `Chevron` (`.chev` rotates 90° when open); `Close` (Sheet and "Show all"); `Search` (entries search); `Pin` (Use my location).
  - `Mic` (Recorder, voice source, audio mark); `Star` (starred); `Note` (annotated); `Chat` (conversation); `Reply` (replying-to); `Plus` (nav).
  - `Check` is exported but not used by any file I looked at.
- **`Mark`:** `Mark({size=24, class})` is the app mark. It has no label and is `aria-hidden`. It draws a 15×18.5 rounded rect for the page, a margin rule, three text lines, and a `circle.mark-ring` (the postmark ring, `stroke: var(--accent)` from CSS). The shots spec regex-patches its `class="mark-ring"` attribute, so keep that serialization. Used in the brand (26px) and the pair screens (44px). The static asset is `public/icons/mark.svg`, plus the PNG icons in `public/icons/` (192, 512, maskable 512, apple-touch).
- **Not in `icons.tsx`:** `QuestionSet.tsx` defines its own local `Up` and `Down` chevron SVGs (16px, stroke 1.8).
- **Emoji:** library and practice emoji (`.emoji`) are rendered as text from data, not icons.