import { expect, test, type Page } from "@playwright/test";

// WEB-UX, the pages builder's screens (not CI): Insights, Settings (with a change waiting, and at its foot), the
// Library, a built-in he edited, its default, the editor, and pairing, at phone and desktop width, light and dark,
// over the fixture server's `--demo` journal.
//   npx playwright test -c playwright.shots.config.ts shots/pages.spec.ts   (writes shots/out/pages-*.png)

async function settle(page: Page): Promise<void> {
  await page.waitForLoadState("networkidle");
  await expect(page.locator(".skeleton")).toHaveCount(0);
  await page.evaluate(() => document.fonts?.ready);
  await page.waitForTimeout(700);   // springs and rises
}

const OUT = process.env.OUT ?? "shots/out";
const SCREENS: [string, string][] = [
  ["insights", "/insights"],
  ["settings", "/settings"],
  ["library", "/library"],
  ["library-edited", "/library/midweek"],
  ["library-default", "/library/midweek/default"],
  ["editor", "/library/gratitude/edit"],
  ["program", "/library/fix-life"],
];

for (const scheme of ["light", "dark"] as const) {
  test.describe(`paired ${scheme}`, () => {
    for (const [name, path] of SCREENS) {
      test(`${name} ${scheme}`, async ({ page }, info) => {
        await page.emulateMedia({ colorScheme: scheme });
        await page.goto(path);
        await settle(page);
        const at = `${OUT}/pages-${info.project.name}-${scheme}-${name}`;
        await page.screenshot({ path: `${at}.png` });
        if (name === "settings") {
          // a change waiting: the save bar rises
          await page.getByRole("button", { name: "More: Hours a prompt stays open" }).click();
          await page.getByRole("switch", { name: "Week review" }).click();
          await page.waitForTimeout(800);
          await page.screenshot({ path: `${at}-pending.png` });
          // its foot, the health check open: the bar sits under the last row
          await page.getByRole("button", { name: /Health check/ }).click();
          await page.waitForTimeout(900);   // the fold opens before the page is measured
          await page.evaluate(() => {
            const pane = document.querySelector<HTMLElement>(".main-pane");
            if (pane && pane.scrollHeight > pane.clientHeight) pane.scrollTop = pane.scrollHeight;
            window.scrollTo(0, document.documentElement.scrollHeight);
          });
          await page.waitForTimeout(900);
          await page.screenshot({ path: `${at}-foot.png` });
          await page.getByRole("navigation", { name: "Settings sections" }).getByRole("link", { name: /Prompts/ }).click();
          await page.waitForTimeout(900);
          await page.screenshot({ path: `${at}-prompts.png` });
        }
        if (name === "insights") {
          const slip = page.locator(".reflection .disclosure-head").first();
          if (await slip.count()) {
            await slip.click();
            await page.waitForTimeout(800);
            await page.screenshot({ path: `${at}-open.png` });
          }
        }
        if (name === "library-edited") {
          await page.locator(".edited-note").getByRole("button", { name: "Reset to default" }).click();
          await page.waitForTimeout(300);
          await page.locator(".edited-note").screenshot({ path: `${at}-asking.png` });
        }
      });
    }
  });
}

test.describe("unpaired", () => {
  test.use({ storageState: { cookies: [], origins: [] } });
  for (const scheme of ["light", "dark"] as const) {
    test(`pair ${scheme}`, async ({ page }, info) => {
      await page.emulateMedia({ colorScheme: scheme });
      await page.goto("/pair");
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await settle(page);
      await page.screenshot({ path: `${OUT}/pages-${info.project.name}-${scheme}-pair.png` });
      await page.goto("/settings");   // a refused read: "Not paired"
      await expect(page.getByRole("heading", { level: 1 })).toHaveText("Not paired");
      await page.waitForTimeout(600);
      await page.screenshot({ path: `${OUT}/pages-${info.project.name}-${scheme}-not-paired.png` });
    });
  }
});

test("not found", async ({ page }, info) => {
  await page.goto("/no/such/page");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Not here");
  await settle(page);
  await page.screenshot({ path: `${OUT}/pages-${info.project.name}-light-not-found.png` });
});
