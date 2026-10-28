import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

// The design screenshots (not CI): every screen at phone and desktop width, light and dark, over the fixture server's
// `--demo` journal; the run screen from the answer the Python suite pins for it; the four inks side by side; the mark.

async function settle(page: Page): Promise<void> {
  await page.waitForLoadState("networkidle");
  await expect(page.locator(".skeleton")).toHaveCount(0);
  await page.evaluate(() => document.fonts?.ready);
  await page.waitForTimeout(150);
}

/** The fixture server has no program run in progress, so the run screen is drawn from tests/fixtures/api/run-one.json. */
async function withRun(page: Page): Promise<void> {
  const run = JSON.parse(readFileSync("../tests/fixtures/api/run-one.json", "utf-8"));
  await page.route("**/api/v1/runs/2026-10-15-run1", (route) => route.fulfill({ json: run }));
}

async function withInk(page: Page, ink: string): Promise<void> {
  await page.addInitScript((value) => localStorage.setItem("journal:ink", value), ink);
}

const SCREENS: [string, string][] = [
  ["today", "/"],
  ["entry", "/entries/2026-10-14-evening"],
  ["run", "/runs/2026-10-15-run1"],
  ["settings", "/settings"],
  ["entries", "/entries"],
  ["library", "/library"],
  ["library-detail", "/library/morning"],
  ["editor", "/library/gratitude/edit"],
  // a built-in edited in place (the demo's midweek, whose default changed since), the default beside it, and the
  // editors a built-in opens straight into: a practice's form, a program's files
  ["library-edited", "/library/midweek"],
  ["library-default", "/library/midweek/default"],
  ["editor-builtin", "/library/morning/edit"],
  ["editor-program", "/library/fix-life/edit"],
  ["insights", "/insights"],
];
const DARK = ["today", "entry", "run", "settings", "insights"];
const INKS = ["blue-black", "brown", "violet", "green"];
const OUT = process.env.OUT ?? "shots/out";

test.describe("paired", () => {
  for (const scheme of ["light", "dark"] as const) {
    for (const [name, path] of SCREENS) {
      if (scheme === "dark" && !DARK.includes(name)) continue;
      test(`${name} ${scheme}`, async ({ page }, info) => {
        await page.emulateMedia({ colorScheme: scheme });
        if (name === "run") await withRun(page);
        await page.goto(path);
        await settle(page);
        const at = `${OUT}/${info.project.name}-${scheme}-${name}`;
        await page.screenshot({ path: `${at}.png` });
        await page.screenshot({ path: `${at}-full.png`, fullPage: true });
      });
    }
  }

  test("entry with her note open", async ({ page }, info) => {
    await page.goto("/entries/2026-10-14-evening");
    await settle(page);
    await page.locator(".block.hers summary").first().click();
    await page.screenshot({ path: `${OUT}/${info.project.name}-light-entry-open.png`, fullPage: true });
  });

  test("settings, ink chosen", async ({ page }, info) => {
    await withInk(page, "brown");
    await page.goto("/settings");
    await settle(page);
    await page.locator("#ink-h").scrollIntoViewIfNeeded();
    await page.screenshot({ path: `${OUT}/${info.project.name}-light-settings-brown.png` });
  });

  test("reset to default, asked in place", async ({ page }, info) => {
    await page.goto("/library/midweek");
    await settle(page);
    await page.locator(".edited-note").getByRole("button", { name: "Reset to default" }).click();
    await page.locator(".edited-note .confirm").evaluate((el) => el.scrollIntoView({ block: "center" }));   // clear of the phone's tab bar
    await page.screenshot({ path: `${OUT}/${info.project.name}-light-library-reset-confirm.png` });
  });

  test("quick add sheet", async ({ page }, info) => {
    await page.goto("/");
    await settle(page);
    await page.getByRole("button", { name: "Write", exact: true }).click();
    await page.waitForTimeout(300);
    await page.screenshot({ path: `${OUT}/${info.project.name}-light-quick-add.png` });
  });

  // The four inks in one row: the same letter of hers, once in each ink, light and dark.
  for (const scheme of ["light", "dark"] as const) {
    test(`the four inks, ${scheme}`, async ({ page, browser }, info) => {
      const tiles: string[] = [];
      for (const ink of INKS) {
        const use = info.project.use;
        const ctx = await browser.newContext({
          baseURL: use.baseURL, viewport: use.viewport, deviceScaleFactor: use.deviceScaleFactor, isMobile: use.isMobile,
          hasTouch: use.hasTouch, storageState: "shots/.paired.json", colorScheme: scheme, serviceWorkers: "block",
        });
        const p = await ctx.newPage();
        await withInk(p, ink);
        await p.goto(info.project.name === "desktop" ? "/entries/2026-10-14-evening" : "/");
        await settle(p);
        await p.evaluate(() => { (document.querySelector(".nav") as HTMLElement | null)?.style.setProperty("display", "none"); });
        const box = info.project.name === "desktop"
          ? await p.locator(".document").boundingBox()
          : await p.locator("li.practice").first().boundingBox();
        const b = box as { x: number; y: number; width: number; height: number };
        const clip = { x: Math.max(0, b.x - 12), y: Math.max(0, b.y - 12), width: b.width + 24, height: Math.min(b.height + 24, info.project.name === "desktop" ? 560 : 640) };
        tiles.push((await p.screenshot({ clip })).toString("base64"));
        await ctx.close();
      }
      const bg = scheme === "dark" ? "#161513" : "#f4efe4";
      const ink = scheme === "dark" ? "#958a79" : "#6e6457";
      await page.setContent(`<!doctype html><body style="margin:0;background:${bg};font:14px ui-monospace,monospace;color:${ink}">
        <div style="display:flex;gap:16px;padding:16px;align-items:flex-start">
        ${tiles.map((t, i) => `<figure style="margin:0"><img src="data:image/png;base64,${t}" style="display:block;width:${info.project.name === "desktop" ? 640 : 390}px"><figcaption style="padding:8px 12px;letter-spacing:.1em;text-transform:uppercase">${INKS[i]}</figcaption></figure>`).join("")}
        </div></body>`);
      await page.setViewportSize({ width: (info.project.name === "desktop" ? 656 : 406) * 4 + 16, height: 400 });
      await page.screenshot({ path: `${OUT}/${info.project.name}-${scheme}-inks.png`, fullPage: true });
    });
  }

  test("the mark", async ({ page }, info) => {
    test.skip(info.project.name !== "desktop", "one board");
    const icon = (f: string) => readFileSync(`public/icons/${f}`).toString("base64");
    const face = readFileSync("public/fonts/newsreader-latin-italic-400.woff2").toString("base64");
    const app = await page.context().newPage();   // the mark as the app draws it; the board is a blank page (no CSP)
    await app.goto("/");
    await settle(app);
    const svg = await app.locator(".brand-mark").evaluate((el) => el.outerHTML);
    await app.close();
    const mark = (size: number, ring: string, ink: string) =>
      svg.replace(/width="\d+"/, `width="${size}"`).replace(/height="\d+"/, `height="${size}"`).replace('class="mark-ring"', `stroke="${ring}"`).replaceAll("currentColor", ink);
    const row = (bg: string, ink: string, ring: string) =>
      `<div style="display:flex;gap:36px;align-items:center;padding:28px 36px;background:${bg}">${[96, 48, 26, 16].map((s) => mark(s, ring, ink)).join("")}
       <span style="font:italic 400 44px Newsreader,serif;color:${ink}">journal</span></div>`;
    await page.setContent(`<!doctype html><style>@font-face{font-family:Newsreader;font-style:italic;src:url(data:font/woff2;base64,${face})}</style>
      <body style="margin:0;font-family:sans-serif;width:760px">
      ${row("#f4efe4", "#1b1813", "#28367f")}${row("#161513", "#ece4d6", "#b6c0ff")}
      <div style="display:flex;gap:36px;align-items:flex-end;padding:28px 36px;background:#ebe4d5">
        <img src="data:image/png;base64,${icon("icon-512.png")}" width="160" style="border-radius:36px">
        <img src="data:image/png;base64,${icon("icon-maskable-512.png")}" width="160" style="border-radius:50%">
        <img src="data:image/png;base64,${icon("apple-touch-icon.png")}" width="120" style="border-radius:27px">
        <img src="data:image/png;base64,${icon("icon-192.png")}" width="48" style="border-radius:11px">
      </div></body>`);
    await page.evaluate(() => document.fonts.ready);
    await page.locator("body").screenshot({ path: `${OUT}/mark.png` });
  });
});

test.describe("signed out", () => {
  test.use({ storageState: { cookies: [], origins: [] } });
  test("pair", async ({ page }, info) => {
    await page.goto("/pair");
    await settle(page);
    await page.screenshot({ path: `${OUT}/${info.project.name}-light-pair.png`, fullPage: true });
  });
  test("not paired", async ({ page }, info) => {
    await page.goto("/");
    await settle(page);
    await page.screenshot({ path: `${OUT}/${info.project.name}-light-not-paired.png`, fullPage: true });
  });
  test("tailscale sign-in not confirmed", async ({ page }, info) => {
    // the server said Tailscale once, then a read was refused: the app must not offer pairing
    await page.route("**/api/v1/session", (r) => r.fulfill({ json: { via: "tailscale", login: "sam@example.com" } }));
    await page.goto("/");
    await settle(page);
    await page.screenshot({ path: `${OUT}/${info.project.name}-light-tailscale-lost.png`, fullPage: true });
  });
});
