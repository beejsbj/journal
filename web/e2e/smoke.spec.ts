import { expect, test, type Page, type TestInfo } from "@playwright/test";
import { readFileSync } from "node:fs";
import { PAIR_FILE } from "../playwright.config";

/** Pictures of each view, uploaded by CI as an artifact for a human to look at (never committed). */
async function shot(page: Page, info: TestInfo, name: string): Promise<void> {
  await page.screenshot({ path: `screenshots/${info.project.name}-${name}.png`, fullPage: true });
}

test("Today reads the day, the week and the practices", async ({ page }, info) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Thursday 15 October 2026");
  await expect(page.locator(".week-strip a.day")).toHaveCount(7);
  await expect(page.locator("li.practice h3")).toContainText(["Daily gratitude", "Evening journal"]);
  await expect(page.locator(".week-strip a.day.answered")).toHaveAttribute("aria-label", "Wed 14 Oct: answered");
  await shot(page, info, "today");
});

// WEB-UX (round 02): her chat replies stand in the right margin as her notes (they were folded callouts), and her
// readings unfold at the end of the entry (the Entry/Analysis tabs are gone: DECISIONS.md WEB-UX-ENTRY-TABS).
test("an entry keeps his words plain, hers in the margin, and her readings at the end", async ({ page }, info) => {
  await page.goto("/entries");
  await expect(page.locator(".entry-row").first()).toBeVisible();
  await shot(page, info, "entries");
  await expect(page.locator(".week-head").first()).toHaveText("Week of 12 Oct");
  await page.getByRole("link", { name: /Evening journal/ }).first().click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Wednesday 14 October 2026");
  await expect(page.locator("article.his .words").first()).toHaveText("First words, about the lighthouse.");
  const hers = page.locator(".block.hers");
  await expect(hers).toHaveCount(1);
  await expect(hers).toContainText("Second line.");
  await expect(page.locator("audio")).toHaveCount(1);
  await shot(page, info, "entry");
  await page.getByRole("button", { name: /What she noticed/ }).click();
  await expect(page.locator(".analysis")).toContainText("A note of hers.");
  await shot(page, info, "analysis");
});

test("on a wide screen her notes stand in the right margin, level with the passage they follow, and offer no edit", async ({ page }, info) => {
  test.skip(info.project.name === "phone", "the spread is for a wide screen; a phone reads one column");
  // the dates-and-dots sidebar leaves the page wide enough for both margins at 1100 px
  await page.addInitScript(() => localStorage.setItem("journal:sidebar", "rail"));
  await page.goto("/entries/2026-10-14-evening");
  const before = page.locator("article.his", { hasText: "Later I thought" });
  await expect(before).toBeVisible();
  const passage = (await before.boundingBox()) as { x: number; y: number; width: number; height: number };
  const words = (await before.locator(".words").boundingBox()) as { x: number; width: number };
  const note = (await page.locator(".block.hers").boundingBox()) as { x: number; y: number };
  expect(note.x).toBeGreaterThanOrEqual(words.x + words.width);       // in the right margin
  expect(note.y).toBeGreaterThanOrEqual(passage.y - 2);                // level with the passage it follows
  expect(note.y).toBeLessThan(passage.y + passage.height);
  // her letter heads his column, above his first words
  const letter = (await page.locator(".doc-letter .letter").boundingBox()) as { x: number; y: number; height: number };
  const first = (await page.locator("article.his .words").first().boundingBox()) as { x: number; y: number };
  expect(Math.abs(letter.x - first.x)).toBeLessThan(2);
  expect(letter.y + letter.height).toBeLessThanOrEqual(first.y);
  await expect(page.locator(".block.hers").locator("button, textarea, [contenteditable]")).toHaveCount(0);
  await expect(page.locator(".block.hers").getByText(/^(Edit|Delete)$/)).toHaveCount(0);
  // with the titled list beside it the page is narrower than 840 px: her note goes under the passage instead
  await page.getByRole("radio", { name: "Titles beside the entry" }).click();
  await expect(page.locator(".side-pane.list")).toBeVisible();
  await expect.poll(async () => {
    const p = (await before.boundingBox()) as { y: number; height: number };
    const n = (await page.locator(".block.hers").boundingBox()) as { y: number };
    return n.y >= p.y + p.height - 1;
  }).toBe(true);
});

test("on a wide screen a program session's phases run down his column under her letter", async ({ page }, info) => {
  test.skip(info.project.name === "phone", "the spread is for a wide screen");
  await page.goto("/entries/2026-10-15-fix-life-s01");
  const divider = page.locator(".document > .phase-divider").first();
  await expect(divider).toBeVisible();
  const letter = (await page.locator(".doc-letter .letter").boundingBox()) as { x: number; y: number; height: number };
  const top = (await divider.boundingBox()) as { x: number; y: number };
  expect(top.y).toBeGreaterThanOrEqual(letter.y + letter.height);       // under her letter
  expect(Math.abs(top.x - letter.x)).toBeLessThan(2);                    // and in his column, as the letter is
});

test("searching his words finds the entry and never hers", async ({ page }) => {
  await page.goto("/entries");
  await page.getByRole("searchbox").fill("lighthouse");
  await expect(page.locator(".entry-row")).toHaveCount(1);
  await page.getByRole("searchbox").fill("SECRETHERS");
  await expect(page.getByText("No entries match.")).toBeVisible();
});

test("library, insights and settings read", async ({ page }, info) => {
  await page.goto("/library");
  await expect(page.getByRole("heading", { name: "Daily", exact: true })).toBeVisible();
  await shot(page, info, "library");
  await page.getByRole("link", { name: /Daily gratitude/ }).click();
  await expect(page.locator(".question-set li")).toHaveCount(2);
  await page.goto("/insights");
  await expect(page.locator(".week-card")).toBeVisible();
  await shot(page, info, "insights");
  await page.goto("/settings");
  await page.getByRole("button", { name: /Health check/ }).click();   // the checks are folded at the foot of the page
  await expect(page.locator(".doctor li.check").first()).toBeVisible();
  await expect(page.getByText(/Paired as/)).toBeVisible();
  await shot(page, info, "settings");
});

// WEB-UX: on a desktop the navigation is the rail down the left (it was a header bar), under the mark and Write.
test("the navigation is a bar at the foot on a phone and the rail on the left on a desktop", async ({ page }, info) => {
  await page.goto("/");
  const box = await page.locator("nav.nav").boundingBox();
  const view = page.viewportSize() ?? { width: 0, height: 0 };
  if (info.project.name === "phone") expect(box?.y ?? 0).toBeGreaterThan(view.height / 2);
  else {
    expect(box?.x ?? 999).toBeLessThan(80);
    expect((box?.x ?? 999) + (box?.width ?? 999)).toBeLessThan(240);
    expect(box?.y ?? 999).toBeLessThan(view.height / 3);
  }
});

test.describe("unpaired", () => {
  test.use({ storageState: { cookies: [], origins: [] } });

  test("the pairing link pairs once, then Today opens", async ({ page }, info) => {
    test.skip(info.project.name === "phone", "one code per run; the desktop project pairs");
    const code = readFileSync(PAIR_FILE, "utf-8").split("\n")[1]?.trim();
    await page.goto("/");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Not paired");
    await page.goto(`/pair#${code}`);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Thursday 15 October 2026");
    expect(page.url()).not.toContain("#");
  });
});
