import { expect, test, type Page, type TestInfo } from "@playwright/test";

// The writes, in a real browser, against the real server over its temporary journal. They run once, on the phone
// viewport, and last: the read tests of both viewports have already seen the journal as it was built. Nothing here
// sends (no "Send now", no program start: those spawn `journal …`), and nothing here touches the config.

async function shot(page: Page, info: TestInfo, name: string): Promise<void> {
  await page.screenshot({ path: `screenshots/${info.project.name}-${name}.png`, fullPage: true });
}

test.beforeEach(({}, info) => {
  test.skip(info.project.name !== "phone", "the writes run once, from the phone");
});

test("a three-field practice made on the phone is in the Library and due today", async ({ page }, info) => {
  await page.goto("/library");
  await page.getByRole("link", { name: "New practice" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("New practice");
  await page.getByLabel("Title", { exact: true }).fill("Night pages");
  await page.locator(".slot input[type=time]").fill("22:00");
  await page.locator(".q-row textarea").fill("What is on the page today?");
  await expect(page.getByText("Looks good.")).toBeVisible();   // the server's dry run parsed it
  await shot(page, info, "new-practice");
  await page.getByRole("button", { name: "Create", exact: true }).click();
  await expect(page).toHaveURL(/\/library\/night-pages$/);
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Night pages");
  await expect(page.locator(".question-set li")).toHaveText(["What is on the page today?"]);
  await page.goto("/");
  await expect(page.locator("li.practice h3")).toContainText(["Daily gratitude", "Night pages", "Evening journal"]);
  await expect(page.locator("li.practice").filter({ hasText: "Night pages" }).locator(".pill")).toHaveText("at 22:00");
});

test("editing it as a form, then as the file", async ({ page }, info) => {
  await page.goto("/library/night-pages/edit");
  await page.getByLabel("Title", { exact: true }).fill("Night pages, slowly");
  await page.getByRole("button", { name: "+ Add a question" }).click();
  await page.locator(".q-row textarea").nth(1).fill("What do you want to let go of?");
  await page.getByRole("button", { name: "Move question 2 up" }).click();
  await expect(page.locator(".q-row textarea").first()).toHaveValue("What do you want to let go of?");
  await page.getByRole("tab", { name: "Raw Markdown" }).click();
  await expect(page.locator("#f-raw")).toHaveValue(/title: Night pages, slowly/);
  await shot(page, info, "editor-raw");
  await page.getByRole("tab", { name: "Form" }).click();
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page).toHaveURL(/\/library\/night-pages$/);
  await expect(page.locator(".question-set li")).toHaveText(["What do you want to let go of?", "What is on the page today?"]);
});

test("a built-in is edited in place, then reset to the default", async ({ page }, info) => {
  await page.goto("/library/weekly");
  await page.getByRole("link", { name: "Edit", exact: true }).click();
  await expect(page).toHaveURL(/\/library\/weekly\/edit$/);
  await expect(page.locator(".eyebrow")).toHaveText("Built-in");
  await page.getByLabel("Title", { exact: true }).fill("Sunday scan");
  await expect(page.getByText("Looks good.")).toBeVisible();
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page).toHaveURL(/\/library\/weekly$/);
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Sunday scan");
  await expect(page.locator(".edited-note")).toContainText("Your version is used in place of the built-in");
  await shot(page, info, "library-edited");
  await page.locator(".edited-note").getByRole("link", { name: "View the default" }).click();
  await expect(page.locator(".diff .diff-line.ins")).toContainText(["+title: Sunday scan"]);
  await page.goBack();
  await page.locator(".edited-note").getByRole("button", { name: "Reset to default" }).click();
  await page.locator(".edited-note .confirm").getByRole("button", { name: "Reset to default" }).click();
  await expect(page.locator(".edited-note")).toHaveCount(0);
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Weekly pattern scan");
});

test("a note from the + sheet, and a block added to an entry", async ({ page }, info) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Write", exact: true }).click();
  await page.getByRole("button", { name: "Quick note" }).click();
  await page.getByLabel("A note", { exact: true }).fill("Buy stamps.");
  await shot(page, info, "quick-add");
  await page.getByRole("button", { name: "Save the note" }).click();
  await expect(page.getByText("Saved as a note.")).toBeVisible();
  await page.keyboard.press("Escape");

  await page.goto("/entries/2026-10-14-evening");
  await expect(page.locator("article.his").first()).toBeVisible();   // the document has been drawn
  const before = await page.locator("article.his").count();
  await page.locator(".composer textarea").fill("Added from the phone.");
  await page.getByRole("button", { name: "Add to the entry", exact: true }).click();
  await expect(page.locator("article.his")).toHaveCount(before + 1);
  // (it joins the words of the entry, before the conversation: the document keeps its order)
  await expect(page.locator("article.his .words").filter({ hasText: "Added from the phone." })).toHaveCount(1);
  await expect(page.locator("article.his .words").first()).toHaveText("First words, about the lighthouse.");   // his earlier words are as they were
  await shot(page, info, "entry-added");
});

test("/x is refused by the server and stays in the box", async ({ page }) => {
  await page.goto("/entries/2026-10-14-evening");
  await page.locator(".composer textarea").fill("/x not for the journal");
  await page.getByRole("button", { name: "Add to the entry", exact: true }).click();
  await expect(page.locator(".composer .error-line")).toContainText("not saved");
  await expect(page.locator(".composer textarea")).toHaveValue("/x not for the journal");
});

test("mood and a star on an entry", async ({ page }) => {
  await page.goto("/entries/2026-10-14-evening");
  await page.getByRole("button", { name: "Mood, people, star" }).click();
  await page.getByLabel("Mood", { exact: true }).fill("calm");
  await page.getByLabel("Mood", { exact: true }).press("Enter");
  await page.getByLabel("Starred", { exact: true }).check();
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.locator(".chips .chip")).toContainText(["starred", "calm"]);
});
