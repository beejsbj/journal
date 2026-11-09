# WEB-UX build: the shared brief for every builder

The owner accepted a new design for the PWA (`web/`): the round-02 prototype, with his changes. This pass builds it
into the real app as a design system and moves every screen onto it. Read this whole file, then your own assignment.

## Sources of truth, in order

1. `design/state.md`: *Round 01 result*, *Round 02 result* (his words, verbatim, and what was accepted). His words
   win over everything below.
2. `design/DESIGN.md` (once the foundation has written it): the design language and the web rules. New work draws
   from it; a gap in it is raised, not invented around.
3. `design/rounds/02-combined/index.html` and `notes.md`: the accepted prototype, the visual reference for every screen
   it draws. Its `<style id="tokens">` block is the token sheet. Reuse its CSS and markup ideas; do not copy its demo
   fixture text or its hash router into the app.
4. `design/inventory.md`: every real surface, state and behaviour, and what the tests pin. Behaviour listed in its §4
   is preserved exactly (drafts, retries, offline rules, /x refusals, block edit/delete rules, her blocks read-only).
5. The repo's own rules: `CLAUDE.md` (root), `docs/FRONTEND.md`, `docs/DECISIONS.md`. Tests are the contract for
   behaviour; a test that pins *presentation* (a class name, a label string, a layout) may be changed deliberately
   when the design changes it, and the commit says so.

## The owner's changes to round 02 (do not miss these)

- Her letter has **no fastening**: no postmark stamp, no wavy lines, no clip, no tape. The sectioned "From Ada"
  letter stays.
- Entry sidebar rows put the **time under the date** in the left column, not at the right of the row.
- Her **margin notes show in dark** as in light.
- The mark is the **"margin j"** (round 02, `#mark`, option 01). The browser tab keeps the small `j` favicon from the
  local `favicon` branch (`git show favicon:web/public/icons/favicon.svg` in the canonical checkout).

## Hard rules

- **Never run node, npm, npx, vite or playwright on this host.** Use Daytona (the `daytona-run` skill; read
  the `daytona-run` skill file in your skills directory and its README). Set your own state directory so builders don't
  collide: `DAYTONA_RUN_STATE=$HOME/.local/state/daytona-run-<your-name>`. Typical check, from the worktree root:

  ```sh
  DAYTONA_RUN_STATE=$HOME/.local/state/daytona-run-<name> daytona-run run --cwd "$PWD/web" --profile general \
    --timeout 1200 -- sh -lc 'npm ci && npm run typecheck && npm test'
  ```

  For a production build add `&& npm run build` and `--collect` what you need; note `vite build` writes to
  `../src/journal/serve/static`, outside `web/`, so run from the repo root with `--cwd "$PWD"` and
  `sh -lc 'cd web && npm ci && …'` when you need the build output. Read the exit status and the log; a finished
  transfer is not a passing run.
- **Do not commit `src/journal/serve/static/`.** The committed build must be CI's bytes; the orchestrator commits it
  at the end. `tests/test_web_build.py` failing on the branch until then is expected.
- **Python suite** (if you touch anything outside `web/`): through the queue,
  `cockpit resources run --profile local-small -- uv run --with pytest --with pyyaml python -m pytest -q`.
- **The repo is public.** Neutral fixture content only. Run `python3 tools/check_private.py` before each commit.
- **Write only your assigned paths.** Shared files (`web/src/tokens.css`, `web/src/styles/base.css`, `web/src/app.tsx`,
  `web/src/icons.tsx`, `web/src/ui/*`, `web/src/api.ts`, `web/src/types.ts`, test helpers) belong to the foundation;
  after it lands, a route builder who needs a change there writes it in their report ("needs from foundation") or
  makes the smallest additive change and lists it explicitly. Never restyle another builder's route.
- **Accessibility floor:** 44 px targets on touch (e2e `mobile.spec.ts` measures every control), visible focus,
  real labels, keyboard paths for everything hover reveals, `prefers-reduced-motion` honoured (instant or a plain
  fade), AA contrast for every text role in light and dark and every ink.
- **Motion:** purposeful and short; the tokens name the durations and curves. View Transitions where supported, a CSS
  fallback otherwise, nothing when reduced motion is set. Disclosures animate height (grid-template-rows 0fr→1fr)
  and fade content in after; no jump.
- **Core stays dependency-free.** No new runtime npm dependency without a reason in your report.
- **Commits:** small coherent slices on your branch, each saying what changed, why, and how far it was verified
  (which Daytona run passed). End each message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Verification you owe

- Daytona: `npm run typecheck`, `npm test` pass (or each failure explained and fixed); `npm run build` passes.
- Rendered evidence of your screens at 1280×820 and 390×844, light and dark, against the fixture server. The shots
  harness is `web/shots/` (`playwright.shots.config.ts`, the `--demo` fixture server, `tools/serve_fixture.py`); run it
  in a Daytona `--profile browser` sandbox with `--collect 'web/shots/out/**'`, then look at the images yourself
  (Read tool) and fix what is wrong. Also run the e2e suite there (`npm run e2e`; it needs python + pyyaml and
  `npx playwright install --with-deps chromium`).
- Report: what you built, files changed, commits, the runs that passed (job ids), screenshots path, anything you
  could not verify, and every deliberate test change with its reason.
