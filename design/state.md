# Web app design: state

The resume point for design passes on the PWA (`web/`). The language itself will live in `design/DESIGN.md` once a
direction is chosen; FRONTEND.md §2 and DECISIONS.md *IDENTITY* record the identity it replaces or refines.

## Current pass: WEB-UX (2026-10-28)

- **Stage:** 4, key-application loop. Round 01's directions were reviewed; the owner combined Folio and Night
  Room (below). Round 02 builds that combination as one prototype for his "looks good" before the system is written.
- **Why:** the owner's verdict on the built app (2026-10-28, verbatim): "its currently like lame, and feels like a
  typical 'cli served dashboard app' vibes. like i want it to have proper ui/ux feel. i want its settings menu to not
  look so ugly. immersive and inviting typing(literally a writing webapp). insight page text could be more
  interestingly placed? reviews and reflections collapsing more beautifully presented instead of the sudden collapsing
  and jumping. i want it to be inviting. good subtle animations. cohesive, interesting and useful layouting. better
  type. like that 'built-in' box is so amatuer. ugly write modal. amatuer pills in entries. cant entries slide into a
  sidebar when we enter one ? analysis page better presented. better page transitions. flex your visual/ux design
  muscles. have a ground up design system so future agents dont have trouble."
- **Key applications** (inferred, to confirm): (1) Entries → an entry, the list sliding into a sidebar; (2) Write,
  the immersive writing surface. Secondary, shown in every direction because they were named: Insights, Settings,
  a Library card.
- **Preserved (not reopened):** his words and hers are two visibly different voices; her ink is the only hue and the
  reader chooses it (4 inks, AA light and dark); no streaks, confetti or gamification; works with no model; offline
  reads; 44 px targets; reduced motion; self-hosted OFL faces in production.
- **Reopened by the owner's words:** type, layout, motion, components, chrome. Margin × Letters is a candidate among
  the directions (Folio refines it), not a given.

## Directions (round 01)

| id | name | point of view |
| --- | --- | --- |
| A | Folio | Margin × Letters grown up: a bound notebook with real app chrome |
| B | Night Room | an immersive writing room, dark-first, depth and spatial motion |
| C | Almanac | editorial: display type, a strict grid, the week as a magazine spread |

Files: `design/rounds/01-directions/<id>/index.html` (self-contained, interactive) and `notes.md` (tokens sketch,
rationale, tradeoffs). Baseline screenshots: the `screenshots` artifact of CI web run 40765879806.

## Round 01 result (2026-11-09, the owner's words, verbatim, on the screenshot comparison)

"I like folio a lot to be honest. I like folio as well. I like night as well. I think almanac is the least one I like,
but I think a combination of folio and rhinitroom is the best, but I do prefer folio colors. I like the gradient
textures of the nightroom. For example, the entry sidebar I think could be collapsible. It could be optional. So we
could have both nightrooms and folio entries. This thing I like that next from Ada thing from folio I like the journal
icon it could be combined with the J that is currently the Fabicon. I like the paperclip stuff that's happening in this
thing. But overall I do like the writing in the margin in the night room, yeah, so now I'm looking at the right
section. I definitely like the vibe, the graying out vibe of the night room, but I also like the paper clip. It doesn't
need to be a paper clip, but this section that is from Ada at the top I like that a lot as well in the from folio
insights. I like the Folios Insights page as well. I like her review of the week and the day-by-day sidebar. I like the
look and style of the nightroom overall, though. I like that the programs that loading thing is there, that's really
cool. I also like the look of Almanac. It feels very cool if it could be incorporated into the others, it'd be nice.
Settings page: most of the settings are all good. Overall, I do like the color of the folio, the warmth of folio, and I
like the ink design for the that comes from folio over the more digitaliness of nitro. Though I love the settings of
Almanarc to be honest. Like the layout. I like the layout of Almanac. Otherwise, I think for settings-wise, I lean more
towards folio. The library I think most of the libraries kind of are lame. They're just this list. I think they could
be better, but I think they're fine for now. A comb I think I like the clocks of Nightroom. I overall like the look of
nightroom, then I'd like the look of folio for library entries. I love the look of nightroom, but I love the calendar of
almanac. I do like the night room's dark version being this really nice. Cool, maybe the day version can be the folio
warmth, and the night version can be the night room's cool dark, i.e., the dark page."

(Voice transcript: "rhinitroom", "nitro", "Nightroom" = Night Room; "Almanarc" = Almanac; "Fabicon" = favicon.)

**Selected (user-accepted direction, a combination):** Night Room's look, style, motion and glow textures, with
Folio's warmth. Light = Folio's warm paper; dark = Night Room's cool dark. Almanac's influence where it fits.

| Element | From | Notes |
| --- | --- | --- |
| Overall look, motion, gradient/glow textures | B | |
| Light palette, warmth | A | |
| Dark palette | B | |
| Entry sidebar | A + B, collapsible | Choose titled list (A) or date rail (B), or hide it |
| "Next from Ada" in the rail | A | |
| Mark | A's notebook + the favicon's `j` (branch `favicon`, 24359f7) | |
| Her letter at the top, a fastening detail | A | "doesn't need to be a paper clip" |
| Notes in the right margin | B | Agent's reading of "writing in the margin… the right section" |
| Write: everything but the current paragraph greyed | B, with A's letter at the top | |
| Insights | A (prose figures, her week review, day-by-day column) + B's programs progress strip | |
| Calendar | C (the month calendar on Entries) | |
| Settings | A's warmth and ink picker in C's layout (numbered index, "at a glance") | |
| Library | A's rows + B's clocks | Lists "fine for now"; a later pass |

**Agent calls, unconfirmed (reversible):** her voice stays typed as in A's letter (IBM Plex Mono). Analysis and her
reflection unfold at the end of the entry, with no tabs. Today follows A. Both are logged for his check on round 02.

## Next

1. Owner picks/combines (gate). 2. Write `design/DESIGN.md` + rebuild `tokens.css` as the system. 3. Primitives and
compounds (Button, Field, Toggle, Disclosure, Sheet, Badge, Player, Toast). 4. Adopt across routes, transitions.
5. Rendered review (phone + desktop, light + dark), CI build, PR.
