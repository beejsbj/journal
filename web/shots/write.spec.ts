import { expect, test } from "@playwright/test";
import { SCHEMES, at, settle, writeButton } from "./lib";

// The write room: on a new note (empty, then with words and the caret in the middle paragraph, so focus greys the
// rest), answering her letter (from Today's Answer), carrying on an entry (from the entry's "In the writing room", a
// wide screen's), and its quiet start offering a program. Nothing is sent: the fixture journal stays as it was.

const WORDS = [
  "The harbour was grey at six, and the ferry late again.",
  "I stood with the others and did not mind the wait. Somebody had brought bread from the corner bakery and passed it along the rail.",
  "Later I thought of the call I have been putting off.",
].join("\n");

for (const scheme of SCHEMES) {
  test.describe(scheme, () => {
    test.use({ colorScheme: scheme });

    test(`a note ${scheme}`, async ({ page }, info) => {
      await page.goto("/");
      await settle(page);
      await writeButton(page).click();
      const room = page.getByRole("dialog");
      await expect(room).toBeVisible();
      await page.waitForTimeout(900);   // the bloom
      await page.screenshot({ path: `${at(info.project.name, scheme, "write-empty")}.png` });
      const box = room.getByLabel("A note", { exact: true });
      await box.fill(WORDS);
      await box.evaluate((el: HTMLTextAreaElement) => {
        el.focus();
        el.setSelectionRange(80, 80);   // in the second paragraph
        el.dispatchEvent(new Event("select", { bubbles: true }));
      });
      await page.waitForTimeout(700);
      await page.screenshot({ path: `${at(info.project.name, scheme, "write-note")}.png` });
      await box.fill("");   // leave no draft behind for the next picture
      await room.getByRole("button", { name: "Start a program" }).click();
      await expect(room.locator(".room-program-list button, .empty").first()).toBeVisible();
      await page.waitForTimeout(500);
      await page.screenshot({ path: `${at(info.project.name, scheme, "write-start-program")}.png` });
    });

    test(`answering her letter ${scheme}`, async ({ page }, info) => {
      await page.goto("/");
      await settle(page);
      await page.locator(".letter-actions").getByRole("button", { name: "Answer" }).first().click();
      const room = page.getByRole("dialog");
      await expect(room.locator(".letter")).toBeVisible();
      await page.waitForTimeout(1100);   // the bloom, then her letter dropping in
      await page.screenshot({ path: `${at(info.project.name, scheme, "write-answer")}.png` });
    });

    test(`carrying on an entry ${scheme}`, async ({ page }, info) => {
      test.skip(info.project.name !== "desktop", "the way in stands over the entry's box on a wide screen");
      await page.goto("/entries/2026-10-14-evening");
      await settle(page);
      await page.getByRole("button", { name: "In the writing room" }).click();
      const room = page.getByRole("dialog");
      await expect(room.getByLabel(/^More for /)).toBeVisible();
      await page.waitForTimeout(1100);
      await page.screenshot({ path: `${at(info.project.name, scheme, "write-entry")}.png` });
    });
  });
}
