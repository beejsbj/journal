import { expect, test } from "@playwright/test";
import { SCHEMES, at, settle, sideways, unroll, withRun } from "./lib";

// Every screen of the app, light and dark, at phone and desktop width, over the fixture server's `--demo` journal (the
// run screen from the answer the Python suite pins for it). One picture of the view as it opens and one of the whole
// page; /design also one per section. Nothing may run sideways. The states inside a screen are in the other specs
// here: entries.spec (filters, menus, sidebar modes, a passage's tools), pages.spec (Settings' save bar, Insights' days,
// a reset asked), write.spec (the room), shell.spec (signed out, /design opened, the inks, the mark, reduced motion).
//   npx playwright test -c playwright.shots.config.ts      (writes shots/out/*.png)

export const SCREENS: [string, string][] = [
  ["today", "/"],
  ["entries", "/entries"],
  ["entry", "/entries/2026-10-14-evening"],
  ["entry-session", "/entries/2026-10-15-fix-life-s01"],
  ["run", "/runs/2026-10-15-run1"],
  ["library", "/library"],
  ["library-detail", "/library/morning"],
  ["library-program", "/library/fix-life"],
  // a built-in edited in place (the demo's midweek, whose default changed since), and the default beside it
  ["library-edited", "/library/midweek"],
  ["library-default", "/library/midweek/default"],
  ["editor", "/library/gratitude/edit"],
  ["editor-builtin", "/library/morning/edit"],
  ["editor-program", "/library/fix-life/edit"],
  ["new-practice", "/new-practice"],
  ["insights", "/insights"],
  ["settings", "/settings"],
  ["design", "/design"],
  ["not-found", "/no/such/page"],
];

for (const scheme of SCHEMES) {
  test.describe(scheme, () => {
    test.use({ colorScheme: scheme });
    for (const [name, path] of SCREENS) {
      test(`${name} ${scheme}`, async ({ page }, info) => {
        if (name === "run") await withRun(page);
        await page.goto(path);
        await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
        await settle(page);
        const base = at(info.project.name, scheme, name);
        await page.screenshot({ path: `${base}.png` });
        expect.soft(await sideways(page), "nothing runs sideways").toBeLessThanOrEqual(1);
        if (name === "design") {
          // one picture per section: the whole guide is taller than a browser will draw in one image
          const secs = page.locator(".ds-sec");
          for (let i = 0; i < (await secs.count()); i++) await secs.nth(i).screenshot({ path: `${base}-${String(i + 1).padStart(2, "0")}.png` });
          return;
        }
        await unroll(page);
        await page.screenshot({ path: `${base}-full.png`, fullPage: true });
      });
    }
  });
}
