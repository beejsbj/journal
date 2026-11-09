import { expect, test, type Page } from "@playwright/test";

// Entries and the entry (WEB-UX, the entries builder): the list, the month filter, the entry in each sidebar mode, her
// readings open, a passage's tools, light and dark, phone and desktop. Run with the design shots:
//   npx playwright test -c playwright.shots.config.ts shots/entries.spec.ts

const OUT = process.env.OUT ?? "shots/out";
const STEM = "/entries/2026-10-14-evening";

async function settle(page: Page): Promise<void> {
  await page.waitForLoadState("networkidle");
  await expect(page.locator(".skeleton")).toHaveCount(0);
  await page.evaluate(() => document.fonts?.ready);
  await page.waitForTimeout(250);
}

/** The entry pane scrolls inside the desk; let the document grow to its content for a whole-page picture. */
async function unroll(page: Page): Promise<void> {
  await page.evaluate(() => {
    const set = (sel: string, prop: string, value: string) =>
      document.querySelectorAll<HTMLElement>(sel).forEach((el) => el.style.setProperty(prop, value, "important"));
    set("html, body", "height", "auto");
    set("html, body", "overflow", "visible");
    set(".shell, .stage, .rail", "height", "auto");
    set(".main-pane, .pane", "overflow", "visible");
    set(".side-pane", "align-self", "stretch");
  });
}

async function sidebar(page: Page, mode: string): Promise<void> {
  await page.addInitScript((m) => localStorage.setItem("journal:sidebar", m), mode);
}

for (const scheme of ["light", "dark"] as const) {
  test.describe(scheme, () => {
    test.use({ colorScheme: scheme });

    test(`entries ${scheme}`, async ({ page }, info) => {
      await page.goto("/entries");
      await settle(page);
      const at = `${OUT}/${info.project.name}-${scheme}-x-entries`;
      await page.screenshot({ path: `${at}.png` });
      await page.goto("/entries?day=2026-10-14");
      await settle(page);
      await page.screenshot({ path: `${at}-day.png` });
      await page.getByRole("button", { name: "any practice" }).click();
      await page.waitForTimeout(400);
      await page.screenshot({ path: `${at}-menu.png` });
    });

    for (const mode of ["list", "rail", "hidden"]) {
      test(`entry ${mode} ${scheme}`, async ({ page }, info) => {
        test.skip(info.project.name === "phone" && mode !== "list", "a phone has no sidebar");
        await sidebar(page, mode);
        await page.goto(STEM);
        await settle(page);
        const at = `${OUT}/${info.project.name}-${scheme}-x-entry-${mode}`;
        await page.screenshot({ path: `${at}.png` });
        if (mode === "list" || info.project.name === "desktop") {
          await page.getByRole("button", { name: /What she noticed/ }).click();
          await page.getByRole("button", { name: /reflection/ }).click();
          await expect(page.getByRole("link", { name: "In Insights" })).toBeVisible();
          await page.waitForTimeout(800);
          await unroll(page);
          await page.screenshot({ path: `${at}-full.png`, fullPage: true });
        }
      });
    }

    test(`entry tools ${scheme}`, async ({ page }, info) => {
      await sidebar(page, "rail");
      await page.goto(STEM);
      await settle(page);
      const at = `${OUT}/${info.project.name}-${scheme}-x-entry-tools`;
      if (info.project.name === "desktop") {
        await page.locator("article.his").first().hover();
        await page.waitForTimeout(500);
        await page.screenshot({ path: `${at}-hover.png` });
        await page.locator("article.his").first().getByRole("button", { name: "Delete" }).click();
        await page.waitForTimeout(500);
        await page.screenshot({ path: `${at}-confirm.png` });
        await page.locator(".side-pane a.dot").first().hover();
        await page.waitForTimeout(400);
        await page.screenshot({ path: `${at}-tip.png` });
      } else {
        await page.getByRole("button", { name: "More for this passage" }).first().click();
        await page.waitForTimeout(800);
        await page.screenshot({ path: `${at}-sheet.png` });
        await page.keyboard.press("Escape");
        await page.waitForTimeout(400);
        await page.getByRole("button", { name: "Mood, people, star" }).click();
        await page.waitForTimeout(800);
        await page.screenshot({ path: `${at}-meta.png` });
      }
    });
  });
}

test("a program session", async ({ page }, info) => {
  await page.goto("/entries/2026-10-15-fix-life-s01");
  await settle(page);
  await page.screenshot({ path: `${OUT}/${info.project.name}-light-x-session.png` });
});
