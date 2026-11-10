import { expect, test } from "@playwright/test";
import { SCHEMES, at, settle, unroll, withSidebar } from "./lib";

// Entries and the entry, their states: the list filtered to a day, the filter sentence's menu open, the entry in each
// sidebar mode (titles, dates and dots, none) with her readings open, a passage's tools (hover and the asked delete on
// a desktop, the sheet and the mood sheet on a phone), the composer at rest and in use on a phone.

const STEM = "/entries/2026-10-14-evening";

for (const scheme of SCHEMES) {
  test.describe(scheme, () => {
    test.use({ colorScheme: scheme });

    test(`entries filtered, the menu open, ${scheme}`, async ({ page }, info) => {
      await page.goto("/entries?day=2026-10-14");
      await settle(page);
      const base = at(info.project.name, scheme, "entries");
      await page.screenshot({ path: `${base}-day.png` });
      await page.getByRole("button", { name: "any practice" }).click();
      await page.waitForTimeout(500);
      await page.screenshot({ path: `${base}-menu.png` });
    });

    for (const mode of ["list", "rail", "hidden"]) {
      test(`entry beside ${mode} ${scheme}`, async ({ page }, info) => {
        test.skip(info.project.name === "phone" && mode !== "list", "a phone has no sidebar");
        await withSidebar(page, mode);
        await page.goto(STEM);
        await settle(page);
        const base = at(info.project.name, scheme, `entry-${mode}`);
        await page.screenshot({ path: `${base}.png` });
        await page.getByRole("button", { name: /What she noticed/ }).click();
        await page.getByRole("button", { name: /reflection/ }).click();
        await expect(page.getByRole("link", { name: "In Insights" })).toBeVisible();
        await page.waitForTimeout(800);
        await unroll(page);
        await page.screenshot({ path: `${base}-readings-full.png`, fullPage: true });
      });
    }

    test(`entry tools ${scheme}`, async ({ page }, info) => {
      await withSidebar(page, "rail");
      await page.goto(STEM);
      await settle(page);
      const base = at(info.project.name, scheme, "entry-tools");
      if (info.project.name === "desktop") {
        await page.locator("article.his").first().hover();
        await page.waitForTimeout(500);
        await page.screenshot({ path: `${base}-hover.png` });
        await page.locator("article.his").first().getByRole("button", { name: "Delete" }).click();
        await page.waitForTimeout(500);
        await page.screenshot({ path: `${base}-confirm.png` });
        await page.keyboard.press("Escape");
        await page.mouse.move(0, 0);
        await page.locator(".side-pane .dot").first().hover();
        await page.waitForTimeout(500);
        await page.screenshot({ path: `${base}-tip.png` });
      } else {
        await page.getByRole("button", { name: "More for this passage" }).first().click();
        await page.waitForTimeout(800);
        await page.screenshot({ path: `${base}-sheet.png` });
        await page.keyboard.press("Escape");
        await page.waitForTimeout(400);
        await page.getByRole("button", { name: "Mood, people, star" }).click();
        await page.waitForTimeout(800);
        await page.screenshot({ path: `${base}-meta.png` });
      }
    });

    // a phone: the box at the foot rests as one quiet line, and is the whole composer the moment it is used
    test(`entry composer at rest and in use ${scheme}`, async ({ page }, info) => {
      test.skip(info.project.name !== "phone", "the resting box is a phone's");
      await page.goto(STEM);
      await settle(page);
      const form = page.locator(".add form.composer");
      await expect(form).toHaveAttribute("data-rest");
      expect((await page.locator(".add textarea").boundingBox())?.height).toBeLessThan(50);
      await page.screenshot({ path: `${at(info.project.name, scheme, "entry-composer-rest")}.png` });
      await page.locator(".add textarea").tap();
      await page.waitForTimeout(400);
      expect((await page.locator(".add textarea").boundingBox())?.height).toBeGreaterThan(100);
      await page.screenshot({ path: `${at(info.project.name, scheme, "entry-composer-open")}.png` });
    });
  });
}
