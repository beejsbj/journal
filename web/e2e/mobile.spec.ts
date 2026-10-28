import { expect, test, type Page, type TestInfo } from "@playwright/test";
import { readFileSync } from "node:fs";

// The mobile pass, measured: on the phone viewport every control of every route is at least 44 x 44 px, nothing
// scrolls sideways, and the composer and the bottom bar are within a thumb's reach. Reads only (the writes spec runs
// after this file and changes the journal).

test.use({ serviceWorkers: "block" });   // the page's own network, so a route below can answer for the server

test.beforeEach(({}, info) => {
  test.skip(info.project.name !== "phone", "measured on the phone viewport");
});

/** The fixture server has no program run in progress (and starting one would change what the smoke tests read), so the
 *  run screen is drawn from the answer the Python suite pins for it (tests/fixtures/api/run-one.json). */
async function withRun(page: Page): Promise<void> {
  const run = JSON.parse(readFileSync("../tests/fixtures/api/run-one.json", "utf-8"));
  await page.route("**/api/v1/runs/2026-10-15-run1", (route) => route.fulfill({ json: run }));
}

async function shot(page: Page, info: TestInfo, name: string): Promise<void> {
  await page.screenshot({ path: `screenshots/${info.project.name}-mobile-${name}.png`, fullPage: false });
}

/** Controls smaller than 44 px (the label counts for a checkbox; a link inside a sentence is exempt, WCAG 2.5.8). */
async function small(page: Page): Promise<string[]> {
  await page.waitForLoadState("networkidle");   // late answers (a reflection, a list) draw their links too
  return page.evaluate(() => {
    const MIN = 43.5;
    const out: string[] = [];
    const sel = 'a[href], button, input:not([type="hidden"]), select, textarea, summary, [role="tab"], [role="button"]';
    for (const el of Array.from(document.querySelectorAll<HTMLElement>(sel))) {
      const cs = getComputedStyle(el);
      if (cs.visibility === "hidden" || cs.display === "none") continue;
      if (el.closest(".visually-hidden")) continue;
      if (el.closest("details:not([open]) > :not(summary)")) continue;   // folded away
      const inSentence = el.tagName === "A" && el.closest("p, .md, .hint, .sub, .empty");
      if (inSentence && !el.matches(".crumb a")) continue;
      const isBox = el instanceof HTMLInputElement && (el.type === "checkbox" || el.type === "radio");
      const target = isBox ? (el.closest("label") ?? el) : el;
      const r = target.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) continue;
      if (r.width < MIN || r.height < MIN) {
        const name = (el.getAttribute("aria-label") || el.textContent || el.getAttribute("name") || el.id || "").trim().slice(0, 30);
        out.push(`${el.tagName.toLowerCase()}${el.className ? "." + String(el.className).split(" ")[0] : ""} "${name}" ${Math.round(r.width)}x${Math.round(r.height)}`);
      }
    }
    return out;
  });
}

async function sideways(page: Page): Promise<number> {
  return page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
}

const ROUTES: Array<[string, string]> = [
  ["today", "/"],
  ["entries", "/entries"],
  ["entry", "/entries/2026-10-14-evening"],
  ["library", "/library"],
  ["library-detail", "/library/gratitude"],
  ["editor", "/library/gratitude/edit"],
  ["new-practice", "/new-practice"],
  ["run", "/runs/2026-10-15-run1"],
  ["insights", "/insights"],
  ["settings", "/settings"],
];

for (const [name, path] of ROUTES) {
  test(`${name}: every control is at least 44 px and nothing scrolls sideways`, async ({ page }, info) => {
    if (name === "run") await withRun(page);
    await page.goto(path);
    await expect(page.locator("main h1, main .page-head, main .error-line, main .empty").first()).toBeVisible();
    await page.waitForLoadState("networkidle");
    expect(await small(page)).toEqual([]);
    expect(await sideways(page)).toBeLessThanOrEqual(1);
    await shot(page, info, name);
  });
}

// Typed figures and her letters run wider than the sans did (Courier Prime): on the narrowest phone in use, 360 px, no
// route may scroll sideways.
test("at 360 px nothing scrolls sideways on any route", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 740 });
  await withRun(page);
  for (const [name, path] of ROUTES) {
    await page.goto(path);
    await expect(page.locator("main h1, main .page-head, main .error-line, main .empty").first()).toBeVisible();
    await page.waitForLoadState("networkidle");
    expect(await sideways(page), name).toBeLessThanOrEqual(1);
  }
});

test("the entry's other tab, the raw editor and the + sheet are as large", async ({ page }) => {
  await page.goto("/entries/2026-10-14-evening");
  await page.getByRole("tab", { name: "Analysis" }).click();
  await expect(page.getByRole("link", { name: "In Insights" })).toBeVisible();   // the day's reflection has been read
  expect(await small(page)).toEqual([]);
  await page.goto("/entries/2026-10-14-evening");
  await page.locator("details.hers summary").click();
  expect(await small(page)).toEqual([]);
  await page.goto("/library/gratitude/edit");
  await page.getByRole("tab", { name: "Raw Markdown" }).click();
  expect(await small(page)).toEqual([]);
  await page.goto("/");
  await page.getByRole("button", { name: "Write", exact: true }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  expect(await small(page)).toEqual([]);
  await page.getByRole("button", { name: "Quick note" }).click();
  expect(await small(page)).toEqual([]);
});

test.describe("unpaired", () => {
  test.use({ storageState: { cookies: [], origins: [] } });

  test("the pairing page has large controls and zooms nothing on focus", async ({ page }) => {
    await page.goto("/pair");
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    expect(await small(page)).toEqual([]);
    const size = await page.locator("input").first().evaluate((el) => parseFloat(getComputedStyle(el).fontSize));
    expect(size).toBeGreaterThanOrEqual(16);   // below 16 px iOS zooms the page when a field takes focus
  });
});

test("the bar is at the foot, the + in its middle, and the composer sits above it, within reach", async ({ page }, info) => {
  await page.goto("/entries/2026-10-14-evening");
  await expect(page.locator("article.his").first()).toBeVisible();
  const view = page.viewportSize() as { width: number; height: number };
  const nav = (await page.locator("nav.nav").boundingBox()) as { x: number; y: number; width: number; height: number };
  expect(nav.y + nav.height).toBeGreaterThanOrEqual(view.height - 1);
  const plus = (await page.getByRole("button", { name: "Write", exact: true }).boundingBox()) as { x: number; width: number };
  expect(Math.abs(plus.x + plus.width / 2 - view.width / 2)).toBeLessThan(6);
  const add = (await page.locator(".add").boundingBox()) as { y: number; height: number };
  expect(add.y).toBeGreaterThan(0);
  expect(add.y + add.height).toBeLessThanOrEqual(nav.y + 1);        // not under the bar
  expect(add.y + add.height).toBeGreaterThan(nav.y - 12);           // and right above it, where the thumb is
  await page.locator(".add textarea").tap();
  const grown = (await page.locator(".add textarea").boundingBox()) as { height: number };
  expect(grown.height).toBeGreaterThan(100);                        // the box opens when it is used
  await shot(page, info, "composer-focused");
});

test("the page lets the phone's own insets in: the viewport covers the screen and the bars keep clear of them", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator('meta[name="viewport"]')).toHaveAttribute("content", /viewport-fit=cover/);
  const css = await page.evaluate(async () => {
    const sheets = await Promise.all(
      Array.from(document.querySelectorAll<HTMLLinkElement>('link[rel="stylesheet"]')).map((l) => fetch(l.href).then((r) => r.text())),
    );
    return sheets.join("\n");
  });
  for (const side of ["top", "bottom", "left", "right"]) expect(css).toContain(`safe-area-inset-${side}`);
});

// "Update available" must not sit on the composer's buttons (it once did: both were pinned to the foot of the screen).
// The toast comes from a waiting service worker, which a test cannot summon in a build with the worker blocked, so the
// element is drawn here with the component's own markup and classes (web/src/components/UpdateToast.tsx).
for (const [name, path] of [["entry", "/entries/2026-10-14-evening"], ["run", "/runs/2026-10-15-run1"]] as const) {
  test(`${name}: the update toast leaves the composer's buttons reachable, and can be put away`, async ({ page }) => {
    if (name === "run") await withRun(page);
    await page.goto(path);
    await page.waitForLoadState("networkidle");
    await page.evaluate(() => {
      const toast = document.createElement("div");
      toast.className = "toast";
      toast.setAttribute("role", "status");
      toast.innerHTML = '<span>Update available</span><button type="button" class="button">Later</button><button type="button" class="button primary">Reload</button>';
      (document.querySelector("#app > div") ?? document.body).appendChild(toast);
    });
    const toast = page.locator(".toast");
    await expect(toast).toBeVisible();
    const buttons = page.locator(".composer-actions button");
    const count = await buttons.count();
    expect(count).toBeGreaterThan(0);
    for (let i = 0; i < count; i++) {
      const covered = await buttons.nth(i).evaluate((el) => {
        const r = el.getBoundingClientRect();
        const t = document.querySelector(".toast") as HTMLElement;
        const points: Array<[number, number]> = [[r.left + r.width / 2, r.top + r.height / 2], [r.left + 8, r.top + r.height / 2], [r.right - 8, r.top + r.height / 2]];
        return points.some(([x, y]) => t.contains(document.elementFromPoint(x, y)));
      });
      expect(covered, `composer button ${i} under the toast`).toBe(false);
    }
    const box = (await toast.boundingBox()) as { y: number; height: number };
    const view = page.viewportSize() as { height: number };
    expect(box.y + box.height).toBeLessThan(view.height / 2);   // at the top, not over the foot of the screen
  });
}
