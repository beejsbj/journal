# Web app design: state

The resume point for design passes on the PWA (`web/`). The language is `design/DESIGN.md`; FRONTEND.md §2 and
DECISIONS.md *IDENTITY* and *WEB-UX-IDENTITY* record the identity it replaces and refines.

The owner gave his verdicts by voice. This file keeps them as faithful paraphrases (what he liked, disliked and asked
for, in the third person); his exact words are kept in the deployment's private notes, not in this public repository.

## Current pass: WEB-UX (2026-10-28 to 2026-11-10)

- **Stage:** built. Round 02 (the Folio × Night Room combination) was accepted with the changes below; the system is
  written (DESIGN.md, tokens, components) and every screen is on it.
- **Why:** the owner's verdict on the app as it was built (2026-10-28), in substance:
  - it felt dull, like a typical dashboard served by a command-line tool, without a proper sense of UI and UX;
  - he wanted it to be inviting, cohesive, with interesting and useful layouts, better type, good subtle animations
    and better page transitions;
  - the writing should be immersive and inviting: it is a writing app; the write modal looked bad;
  - Settings looked ugly; the "built-in" box and the pills in Entries looked amateur;
  - Insights' text could be placed more interestingly, and the analysis presented better;
  - reviews and reflections should open gracefully instead of collapsing and jumping;
  - could the entries list slide into a sidebar when an entry is opened;
  - a design system from the ground up, so that later work does not struggle.
- **Key applications** (inferred, then confirmed by the rounds): (1) Entries → an entry, the list sliding into a
  sidebar; (2) Write, the immersive writing surface. Secondary, drawn in every direction because he named them:
  Insights, Settings, a Library card.
- **Preserved (not reopened):** his words and hers are two visibly different voices; her ink is the only hue and the
  reader chooses it (4 inks, AA light and dark); no streaks, confetti or gamification; works with no model; offline
  reads; 44 px targets; reduced motion; self-hosted OFL faces in production.
- **Reopened by his verdict:** type, layout, motion, components, chrome. Margin × Letters was a candidate among the
  directions (Folio refined it), not a given.

## Directions (round 01)

| id | name | point of view |
| --- | --- | --- |
| A | Folio | Margin × Letters grown up: a bound notebook with real app chrome |
| B | Night Room | an immersive writing room, dark-first, depth and spatial motion |
| C | Almanac | editorial: display type, a strict grid, the week as a magazine spread |

Files: `design/rounds/01-directions/<id>/index.html` (self-contained, interactive) and `notes.md` (tokens sketch,
rationale, tradeoffs).

## Round 01 result (2026-11-09, his verdict on the screenshot comparison, paraphrased)

- **Overall:** he liked Folio a lot and Night Room as well; Almanac least, though he found its look appealing and
  wanted it worked into the others where it fits. The best, to him, was a combination of Folio and Night Room.
- **Look:** Night Room's overall look and style; its gradient (glow) textures; its dark version, which he found
  especially good. Folio's colours and warmth, and Folio's ink design over Night Room's more digital feel. So: the
  light version Folio's warmth, the dark version Night Room's cool dark page.
- **The entry sidebar:** could be collapsible and optional, so both Night Room's and Folio's versions of it can be had.
- **Liked in Folio:** "Next from" the companion in the rail; the journal icon, to be combined with the `j` of the
  current favicon; the sectioned letter from her at the top of the entry; the fastening detail on it (it need not be a
  paper clip); its Insights page, her review of the week and the day-by-day column; the warmth and ink picker in
  Settings; Folio's look for Library entries.
- **Liked in Night Room:** the writing in the margin (read as her notes in the right margin); the greying out of all
  but the current paragraph while writing; the programs' progress strip; its clocks (for the Library).
- **Liked in Almanac:** its Settings layout (he loved it), and its calendar.
- **Settings:** most of it was fine; Almanac's layout with Folio's feel.
- **Library:** most versions felt like plain lists; better later, fine for now.

**Selected (user-accepted direction, a combination):** Night Room's look, style, motion and glow textures, with
Folio's warmth. Light = Folio's warm paper; dark = Night Room's cool dark. Almanac's influence where it fits.

| Element | From | Notes |
| --- | --- | --- |
| Overall look, motion, gradient/glow textures | B | |
| Light palette, warmth | A | |
| Dark palette | B | |
| Entry sidebar | A + B, collapsible | Choose titled list (A) or date rail (B), or hide it |
| "Next from Ada" in the rail | A | |
| Mark | A's notebook + the favicon's `j` | |
| Her letter at the top, a fastening detail | A | not necessarily a paper clip |
| Notes in the right margin | B | the reading of "writing in the margin" |
| Write: everything but the current paragraph greyed | B, with A's letter at the top | |
| Insights | A (prose figures, her week review, day-by-day column) + B's programs progress strip | |
| Calendar | C (the month calendar on Entries) | |
| Settings | A's warmth and ink picker in C's layout (numbered index, "at a glance") | |
| Library | A's rows + B's clocks | Lists "fine for now"; a later pass |

**Calls made in the design, unconfirmed then (reversible):** her voice stays typed as in A's letter (IBM Plex Mono).
Analysis and her reflection unfold at the end of the entry, with no tabs. Today follows A. All were put to him with
round 02.

## Round 02 result (2026-11-09, his verdict on the round-02 screenshots and the live prototype, paraphrased)

- **Disliked:** the stamps on her letter (the postmark and the wavy cancel lines); they looked cheap to him.
- **Asked for:** in the entry sidebar, the time under the date instead of at the right of the row, so everything about
  date and time sits in one place.
- **Noticed missing:** her notes in the right margin in the dark screenshot (he took it that they were simply not
  shown there).
- **Approved by name:** the entry date rail, the entry with no sidebar, the entry in dark (beautiful, to him), the
  sectioned letter from her (without the stamp), Today (very nice), Entries (beautiful) and its empty state, Insights,
  Settings (great), and the "margin j" for the mark. Otherwise, he found it all very good.

**Accepted (user-accepted, 2026-11-09):** round 02 as the system, with:
- **No fastening on her letter:** no postmark stamp, no wavy cancel lines, no clip, no tape. Keep the sectioned "From
  Ada" letter itself.
- **Entry sidebar rows:** the time goes under the date, with the date and time together in the left column, instead
  of at the right of the row.
- **Her margin notes must show in dark too.**
- **The mark is the "margin j"** (option 01 on `#mark`).
- Today, Entries, Entry (all three sidebar modes), dark, Insights and Settings were each approved by name.
- Calls he did not contest (stand, reversible): her voice typed in IBM Plex Mono; Analysis and Reflection unfold at
  the end of the entry, no tabs; Instrument Sans for the interface; the rail folds to icons while an entry is open;
  focus greying on by default in the write room; the titled list as the default sidebar.

## Next

A later pass: the Library beyond "fine for now", and whatever his use of the built app asks for.
