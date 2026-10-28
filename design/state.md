# Web app design: state

The resume point for design passes on the PWA (`web/`). The language itself will live in `design/DESIGN.md` once a
direction is chosen; FRONTEND.md §2 and DECISIONS.md *IDENTITY* record the identity it replaces or refines.

## Current pass: WEB-UX (2026-10-28)

- **Stage:** 3, directions. Three rendered directions on the key screens; the owner picks, combines or redirects.
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

## Directions (round 01, proposed; none selected)

| id | name | point of view |
| --- | --- | --- |
| A | Folio | Margin × Letters grown up: a bound notebook with real app chrome |
| B | Night Room | an immersive writing room, dark-first, depth and spatial motion |
| C | Almanac | editorial: display type, a strict grid, the week as a magazine spread |

Files: `design/rounds/01-directions/<id>/index.html` (self-contained, interactive) and `notes.md` (tokens sketch,
rationale, tradeoffs). Baseline screenshots: the `screenshots` artifact of CI web run 40765879806.

## Next

1. Owner picks/combines (gate). 2. Write `design/DESIGN.md` + rebuild `tokens.css` as the system. 3. Primitives and
compounds (Button, Field, Toggle, Disclosure, Sheet, Badge, Player, Toast). 4. Adopt across routes, transitions.
5. Rendered review (phone + desktop, light + dark), CI build, PR.
