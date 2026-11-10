import { expect, test, type Page } from "@playwright/test";

// WEB-UX, Settings in a real browser: the save bar that rises while something is changed never covers a row (at the
// foot of the page it sits under the last one, and on a phone above the tab bar), and the numbered index jumps to a
// section and follows the scroll. Nothing here saves: the change is made and discarded.

type Box = { x: number; y: number; width: number; height: number };

/** Scroll the page to its foot: the pane on a wide screen, the document on a phone. */
async function toFoot(page: Page): Promise<void> {
  await page.evaluate(() => {
    const pane = document.querySelector<HTMLElement>(".main-pane");
    if (pane && pane.scrollHeight > pane.clientHeight) pane.scrollTop = pane.scrollHeight;
    window.scrollTo(0, document.documentElement.scrollHeight);
  });
  await page.waitForTimeout(400);
}

test("the save bar rises with a change, and at the foot of the page it covers no row", async ({ page }) => {
  await page.goto("/settings");
  const more = page.getByRole("button", { name: "More: Hours a prompt stays open" });
  await expect(more).toBeVisible();
  await expect(page.locator(".save-bar")).toBeHidden();
  await more.click();
  const bar = page.locator(".save-bar");
  await expect(bar).toBeVisible();
  await expect(bar.locator(".save-count")).toHaveText("1 change not saved");
  await expect(bar.getByRole("button", { name: "Save" })).toBeEnabled();
  // wait for the Health section's folds to be drawn (the last rows of the page), then go to the foot
  await expect(page.locator(".diagnostics > .disclosure-head")).toBeVisible();
  await toFoot(page);
  await page.waitForTimeout(700);   // the bar's spring
  const barBox = (await bar.boundingBox()) as Box;
  const last = (await page.locator(".diagnostics > .disclosure-head").boundingBox()) as Box;
  expect(last.y + last.height).toBeLessThanOrEqual(barBox.y + 1);
  const view = page.viewportSize() as { width: number; height: number };
  expect(barBox.y + barBox.height).toBeLessThanOrEqual(view.height);
  const nav = await page.locator("nav.nav").boundingBox();
  if (nav && nav.y > view.height / 2) {
    // a phone: the bar floats above the tab bar, not under it
    const capsule = await page.locator("nav.nav .nav-item").first().boundingBox();
    expect(barBox.y + barBox.height).toBeLessThanOrEqual((capsule as Box).y);
  }
  await bar.getByRole("button", { name: "Discard" }).click();
  await expect(bar).toBeHidden();
});

test("the section index jumps to a section and follows the scroll", async ({ page }) => {
  await page.goto("/settings");
  const index = page.getByRole("navigation", { name: "Settings sections" });
  await expect(index.getByRole("link")).toHaveCount(8);
  await index.getByRole("link", { name: /Health/ }).click();
  await expect(index.getByRole("link", { name: /Health/ })).toHaveAttribute("aria-current", "location");
  await page.waitForTimeout(900);
  const head = (await page.locator("#set-health-h").boundingBox()) as Box;
  expect(head.y).toBeLessThan((page.viewportSize() as { height: number }).height / 2);
  await expect(page.locator("#set-health-h")).toBeFocused();
  await index.getByRole("link", { name: /Prompts/ }).click();
  await page.waitForTimeout(1200);
  // scrolling on by hand: the index moves with the reader
  await page.locator("#set-reflections").scrollIntoViewIfNeeded();
  await page.evaluate(() => document.getElementById("set-reflections")?.scrollIntoView({ block: "start" }));
  await expect(index.getByRole("link", { name: /Reflections/ })).toHaveAttribute("aria-current", "location");
});
