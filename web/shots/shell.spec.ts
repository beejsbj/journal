import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { OUT, SCHEMES, at, settle, withInk, writeButton } from "./lib";

// The app around the screens: signed out (pairing, "Not paired", a Tailscale sign-in not confirmed), the system's parts
// opened on /design (a menu, a fold, a sheet, a toast), her four inks side by side, the mark, and the app with reduced
// motion asked for. Light and dark, phone and desktop, unless a test says otherwise.

test.describe("signed out", () => {
  test.use({ storageState: { cookies: [], origins: [] } });
  for (const scheme of SCHEMES) {
    test.describe(scheme, () => {
      test.use({ colorScheme: scheme });
      test(`pair ${scheme}`, async ({ page }, info) => {
        await page.goto("/pair");
        await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
        await settle(page);
        await page.screenshot({ path: `${at(info.project.name, scheme, "pair")}.png`, fullPage: true });
      });
      test(`not paired ${scheme}`, async ({ page }, info) => {
        await page.goto("/");
        await expect(page.getByRole("heading", { level: 1 })).toHaveText("Not paired");
        await settle(page);
        await page.screenshot({ path: `${at(info.project.name, scheme, "not-paired")}.png`, fullPage: true });
      });
      test(`tailscale sign-in not confirmed ${scheme}`, async ({ page }, info) => {
        // the server said Tailscale once, then a read was refused: the app must not offer pairing
        await page.route("**/api/v1/session", (r) => r.fulfill({ json: { via: "tailscale", login: "sam@example.com" } }));
        await page.goto("/");
        await settle(page);
        await page.screenshot({ path: `${at(info.project.name, scheme, "tailscale-lost")}.png`, fullPage: true });
      });
    });
  }
});

for (const scheme of SCHEMES) {
  test(`the design system, opened, ${scheme}`, async ({ page }, info) => {
    await page.emulateMedia({ colorScheme: scheme });
    await page.goto("/design");
    await settle(page);
    const base = at(info.project.name, scheme, "design-open");
    await page.getByRole("button", { name: /every entry/ }).click();
    await page.waitForTimeout(500);
    await page.screenshot({ path: `${base}-menu.png` });
    await page.keyboard.press("Escape");
    await page.getByRole("button", { name: /What she noticed/ }).click();
    await page.waitForTimeout(800);
    await page.locator("#ds-folds").screenshot({ path: `${base}-fold.png` });
    await page.getByRole("button", { name: "Open a sheet" }).click();
    await page.waitForTimeout(800);
    await page.screenshot({ path: `${base}-sheet.png` });
    await page.keyboard.press("Escape");
    await page.waitForTimeout(400);
    await page.getByRole("button", { name: "Show a toast" }).click();
    await page.waitForTimeout(700);
    await page.screenshot({ path: `${base}-toast.png` });
  });
}

// The four inks in one row: the same letter of hers, once in each ink, light and dark.
const INKS = ["blue-black", "brown", "violet", "green"];
for (const scheme of SCHEMES) {
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
    const bg = scheme === "dark" ? "#0a0d12" : "#eee7d9";   // the desk (tokens.css --room)
    const ink = scheme === "dark" ? "#8a91a0" : "#6d6356";   // --text-3
    await page.setContent(`<!doctype html><body style="margin:0;background:${bg};font:14px ui-monospace,monospace;color:${ink}">
      <div style="display:flex;gap:16px;padding:16px;align-items:flex-start">
      ${tiles.map((t, i) => `<figure style="margin:0"><img src="data:image/png;base64,${t}" style="display:block;width:${info.project.name === "desktop" ? 640 : 390}px"><figcaption style="padding:8px 12px">${INKS[i]}</figcaption></figure>`).join("")}
      </div></body>`);
    await page.setViewportSize({ width: (info.project.name === "desktop" ? 656 : 406) * 4 + 16, height: 400 });
    await page.screenshot({ path: `${at(info.project.name, scheme, "inks")}.png`, fullPage: true });
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
    ${row("#fbf8f1", "#1d1a15", "#28367f")}${row("#141921", "#ebe8e1", "#b6c0ff")}
    <div style="display:flex;gap:36px;align-items:flex-end;padding:28px 36px;background:#eee7d9">
      <img src="data:image/png;base64,${icon("icon-512.png")}" width="160" style="border-radius:36px">
      <img src="data:image/png;base64,${icon("icon-maskable-512.png")}" width="160" style="border-radius:50%">
      <img src="data:image/png;base64,${icon("apple-touch-icon.png")}" width="120" style="border-radius:27px">
      <img src="data:image/png;base64,${icon("icon-192.png")}" width="48" style="border-radius:11px">
    </div></body>`);
  await page.evaluate(() => document.fonts.ready);
  await page.locator("body").screenshot({ path: `${OUT}/mark.png` });
});

// Reduced motion asked for: everything arrives at once (every duration token is 1 ms, base.css stops the rest, no View
// Transition), and nothing still moving is longer than that. Pictures taken at once, with no wait for a spring.
test.describe("reduced motion", () => {
  test.use({ reducedMotion: "reduce" });
  const still = () => document.getAnimations().filter((a) => {
    const t = a.effect?.getTiming();
    return a.playState === "running" && typeof t?.duration === "number" && (t.duration > 1 || t.iterations === Infinity);
  }).map((a) => `${(a as CSSAnimation).animationName ?? (a as CSSTransition).transitionProperty ?? "animation"} ${String(a.effect?.getTiming().duration)}`);

  test("today, an entry's reading, the room", async ({ page }, info) => {
    await page.goto("/");
    await settle(page, 0);
    expect.soft(await page.evaluate(still), "today: nothing moving").toEqual([]);
    await page.screenshot({ path: `${at(info.project.name, "light", "reduced-today")}.png` });
    await page.goto("/entries/2026-10-14-evening");
    await settle(page, 0);
    await page.getByRole("button", { name: /What she noticed/ }).click();
    expect.soft(await page.evaluate(still), "a fold: nothing moving").toEqual([]);
    await page.screenshot({ path: `${at(info.project.name, "light", "reduced-entry-reading")}.png` });
    await writeButton(page).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    expect.soft(await page.evaluate(still), "the room: nothing moving").toEqual([]);
    await page.screenshot({ path: `${at(info.project.name, "light", "reduced-room")}.png` });
  });
});
