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

test("an entry keeps his words plain and hers folded", async ({ page }, info) => {
  await page.goto("/entries");
  await expect(page.locator(".entry-row").first()).toBeVisible();
  await shot(page, info, "entries");
  await expect(page.locator(".week-head").first()).toHaveText("Week of 12 Oct");
  await page.getByRole("link", { name: /Evening journal/ }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Evening journal");
  await expect(page.locator("article.his .words").first()).toHaveText("First words, about the lighthouse.");
  const hers = page.locator("details.hers");
  await expect(hers).toHaveCount(1);
  await expect(hers).not.toHaveAttribute("open", "");
  await expect(hers.locator(".callout-body")).toBeHidden();
  await shot(page, info, "entry-folded");
  await hers.locator("summary").click();
  await expect(hers.locator(".callout-body")).toContainText("Second line.");
  await expect(page.locator("audio")).toHaveCount(1);
  await page.getByRole("tab", { name: "Analysis" }).click();
  await expect(page.locator(".analysis")).toContainText("A note of hers.");
  await shot(page, info, "analysis");
});

test("on a wide screen her words stand in a column beside his, where she spoke, and offer no edit", async ({ page }, info) => {
  test.skip(info.project.name === "phone", "the spread is for a wide screen; a phone reads one column");
  await page.goto("/entries/2026-10-14-evening");
  const first = page.locator("article.his").first();
  await expect(first).toBeVisible();
  const his = (await first.boundingBox()) as { x: number; y: number; width: number };
  const prompt = (await page.locator(".document > .prompt").boundingBox()) as { x: number; y: number };
  const note = (await page.locator("details.hers").boundingBox()) as { x: number; y: number };
  const before = (await page.locator("article.his", { hasText: "Later I thought" }).boundingBox()) as { y: number; height: number };
  const after = (await page.locator("article.his", { hasText: "yes, and the gulls" }).boundingBox()) as { y: number };
  expect(prompt.x).toBeGreaterThanOrEqual(his.x + his.width);     // her letter is in the column to the right
  expect(Math.abs(prompt.y - his.y)).toBeLessThan(2);              // at the head of the page, level with his first words
  expect(note.x).toBeGreaterThanOrEqual(his.x + his.width);
  expect(note.y).toBeGreaterThanOrEqual(before.y + before.height - 1);   // from the end of the paragraph it follows
  expect(note.y).toBeLessThanOrEqual(after.y + 2);                        // and no lower than the next one
  // his column runs on without a hole: the paragraph after her note follows the one before it at the usual spacing
  expect(after.y - (before.y + before.height)).toBeLessThan(60);
  await expect(page.locator("details.hers").locator("button, textarea, [contenteditable]")).toHaveCount(0);
  await expect(page.locator("details.hers").getByText(/^(Edit|Delete)$/)).toHaveCount(0);
});

test("on a wide screen a program session's first phase starts beside her letter, not below it", async ({ page }, info) => {
  test.skip(info.project.name === "phone", "the spread is for a wide screen");
  await page.goto("/entries/2026-10-15-fix-life-s01");
  const divider = page.locator(".document > .phase-divider").first();
  await expect(divider).toBeVisible();
  const prompt = (await page.locator(".document > .prompt").boundingBox()) as { x: number; y: number; height: number };
  const top = (await divider.boundingBox()) as { x: number; y: number; width: number };
  expect(Math.abs(top.y - prompt.y)).toBeLessThan(2);                  // his column opens at the head of the page
  expect(top.x + top.width).toBeLessThanOrEqual(prompt.x);              // and the divider stays in his column
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
  await page.getByText("Health check").click();   // the diagnostics are folded at the foot of the page
  await expect(page.locator("table.doctor")).toBeVisible();
  await expect(page.getByText(/Paired as/)).toBeVisible();
  await shot(page, info, "settings");
});

test("the navigation is a bar at the foot on a phone and in the header on a desktop", async ({ page }, info) => {
  await page.goto("/");
  const box = await page.locator("nav.nav").boundingBox();
  const height = page.viewportSize()?.height ?? 0;
  if (info.project.name === "phone") expect(box?.y ?? 0).toBeGreaterThan(height / 2);
  else expect(box?.y ?? 999).toBeLessThan(120);
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
