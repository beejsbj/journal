import { expect, test, type Page } from "@playwright/test";

// The write room's shots (not CI), beside shots.spec.ts: the room on a new note, with words in it and the caret in
// the middle paragraph (focus greys the rest), and the room answering her letter, opened from Today's Answer. Light
// and dark, at phone and desktop width, over the fixture server's `--demo` journal.

const OUT = process.env.OUT ?? "shots/out";

async function settle(page: Page): Promise<void> {
  await page.waitForLoadState("networkidle");
  await expect(page.locator(".skeleton")).toHaveCount(0);
  await page.evaluate(() => document.fonts?.ready);
  await page.waitForTimeout(150);
}

const WORDS = [
  "The harbour was grey at six, and the ferry late again.",
  "I stood with the others and did not mind the wait. Somebody had brought bread from the corner bakery and passed it along the rail.",
  "Later I thought of the call I have been putting off.",
].join("\n");

for (const scheme of ["light", "dark"] as const) {
  test(`write room, a note, ${scheme}`, async ({ page }, info) => {
    await page.emulateMedia({ colorScheme: scheme });
    await page.goto("/");
    await settle(page);
    await page.getByRole("button", { name: "Write", exact: true }).click();
    const room = page.getByRole("dialog");
    await expect(room).toBeVisible();
    await page.waitForTimeout(900);   // the bloom
    await page.screenshot({ path: `${OUT}/${info.project.name}-${scheme}-write-empty.png` });
    const box = room.getByLabel("A note", { exact: true });
    await box.fill(WORDS);
    await box.evaluate((el: HTMLTextAreaElement) => {
      el.focus();
      el.setSelectionRange(80, 80);   // in the second paragraph
      el.dispatchEvent(new Event("select", { bubbles: true }));
    });
    await page.waitForTimeout(700);
    await page.screenshot({ path: `${OUT}/${info.project.name}-${scheme}-write-note.png` });
  });

  test(`write room, answering, ${scheme}`, async ({ page }, info) => {
    await page.emulateMedia({ colorScheme: scheme });
    await page.goto("/");
    await settle(page);
    await page.locator(".letter-actions").getByRole("button", { name: "Answer" }).first().click();
    const room = page.getByRole("dialog");
    await expect(room.locator(".letter")).toBeVisible();
    await page.waitForTimeout(1100);   // the bloom, then her letter dropping in
    await page.screenshot({ path: `${OUT}/${info.project.name}-${scheme}-write-answer.png` });
  });
}
