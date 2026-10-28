import { expect, test, type Page } from "@playwright/test";

// The PWA layer in a real browser: the worker installs and takes control, the manifest and icons are served, a view
// read once opens offline with the ribbon, a write offline says it is not saved and keeps the draft, and nothing
// kept survives an unpair. 127.0.0.1 is a secure context, as the https site is. Reads only on the journal.

async function controlled(page: Page): Promise<void> {
  await page.evaluate(() => navigator.serviceWorker.ready);
  await expect.poll(() => page.evaluate(() => !!navigator.serviceWorker.controller), { timeout: 15_000 }).toBe(true);
}

const kept = (page: Page) => page.evaluate(async () => (await caches.keys()).filter((n) => n.startsWith("journal-")));

test("the worker, the manifest and the icons are served as they must be", async ({ page, request }) => {
  await page.goto("/");
  const sw = await request.get("/sw.js");
  expect(sw.status()).toBe(200);
  expect(sw.headers()["content-type"]).toContain("javascript");
  expect(sw.headers()["cache-control"]).toBe("no-cache");
  expect(sw.headers()["content-security-policy"]).toContain("worker-src 'self'");
  const manifest = await request.get("/manifest.webmanifest");
  expect(manifest.headers()["content-type"]).toContain("manifest+json");
  const body = await manifest.json();
  expect(body).toMatchObject({ start_url: "/", scope: "/", display: "standalone" });
  for (const icon of body.icons) expect((await request.get("/" + icon.src)).status(), icon.src).toBe(200);
  await expect(page.locator('link[rel="manifest"]')).toHaveAttribute("href", "/manifest.webmanifest");
  await controlled(page);
});

test("the very first visit is kept too: no reload is needed before going offline", async ({ page, context }) => {
  await page.goto("/");                      // read before the worker controls the page
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Thursday 15 October 2026");
  await controlled(page);                    // once it does, the views read again through it
  await expect
    .poll(() => page.evaluate(async () => (await (await caches.open("journal-data-v1")).keys()).map((r) => new URL(r.url).pathname)), { timeout: 15_000 })
    .toContain("/api/v1/today");
  await context.setOffline(true);
  await page.reload();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Thursday 15 October 2026");
  await expect(page.locator(".ribbon")).toHaveText(/^offline, as of /);
  await context.setOffline(false);
});

test("a view read once opens offline with the ribbon; a view never read says so", async ({ page, context }) => {
  await page.goto("/");
  await controlled(page);
  await page.reload();                       // read through the worker, so it is kept
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Thursday 15 October 2026");
  await expect.poll(() => kept(page)).toContain("journal-data-v1");
  await page.goto("/entries/2026-10-14-evening");
  await expect(page.locator("article.his").first()).toBeVisible();
  await expect(page.locator(".ribbon")).toHaveCount(0);   // online: no ribbon

  await context.setOffline(true);
  await page.goto("/");                      // the shell is precached, Today is the kept copy
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Thursday 15 October 2026");
  await expect(page.locator(".ribbon")).toHaveText(/^offline, as of \d\d:\d\d$/);
  await page.goto("/entries/2026-10-14-evening");
  await expect(page.locator("article.his .words").first()).toHaveText("First words, about the lighthouse.");
  await expect(page.locator(".ribbon")).toHaveText(/^offline, as of /);
  await page.goto("/settings");              // never kept: an honest error, and no ribbon
  await expect(page.locator(".error-line").first()).toBeVisible();
  await expect(page.locator(".ribbon")).toHaveCount(0);

  await context.setOffline(false);
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Thursday 15 October 2026");
  await expect(page.locator(".ribbon")).toHaveCount(0);
});

test("a write offline is not saved, says so, and keeps the draft", async ({ page, context }) => {
  await page.goto("/entries/2026-10-14-evening");
  await controlled(page);
  await page.reload();
  await expect(page.locator("article.his").first()).toBeVisible();
  await context.setOffline(true);
  await page.reload();
  await expect(page.locator(".composer .offline-note")).toContainText("You're offline; this will not be saved until you're back.");
  await page.locator(".composer textarea").fill("Written without a network.");
  await page.getByRole("button", { name: "Add to the entry", exact: true }).click();
  await expect(page.locator(".composer .error-line")).toContainText("You're offline; this will not be saved until you're back.");
  await expect(page.locator(".composer textarea")).toHaveValue("Written without a network.");
  await page.reload();                       // even a reload keeps it (sessionStorage)
  await expect(page.locator(".composer textarea")).toHaveValue("Written without a network.");
  await context.setOffline(false);
  await page.locator(".composer textarea").fill("");   // nothing was sent: leave the journal as it was
});

test("unpairing drops everything kept", async ({ page }) => {
  await page.goto("/");
  await controlled(page);
  await page.reload();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Thursday 15 October 2026");
  await expect.poll(() => kept(page)).toContain("journal-data-v1");
  await page.goto("/settings");
  await page.getByRole("button", { name: "Unpair" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Not paired");
  await expect.poll(() => kept(page)).toEqual([]);
});
