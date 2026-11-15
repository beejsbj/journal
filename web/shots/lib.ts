import { expect, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

/**
 * What every shots spec shares. Names are stable: `shots/out/<project>-<scheme>-<screen>[-<state>].png`, project
 * "phone" (390 x 844) or "desktop" (1280 x 860), scheme "light" or "dark"; `-full` is the whole page unrolled.
 */
export const OUT = process.env.OUT ?? "shots/out";
export const SCHEMES = ["light", "dark"] as const;
export type Scheme = (typeof SCHEMES)[number];

export const at = (project: string, scheme: string, name: string) => `${OUT}/${project}-${scheme}-${name}`;

/** Wait for the page to be drawn: reads in, no skeleton left (/design draws some on purpose), fonts, a spring. */
export async function settle(page: Page, ms = 300): Promise<void> {
  await page.waitForLoadState("networkidle");
  await expect(page.locator(".skeleton:not([data-specimen] .skeleton)")).toHaveCount(0);
  await page.evaluate(() => document.fonts?.ready);
  await page.waitForTimeout(ms);
}

/** On a wide screen the page pane scrolls inside a fixed desk, so a full-page shot would show one screen: let the
 *  document grow to the panes' content first (through the CSSOM: the app's CSP refuses inline style tags). */
export async function unroll(page: Page): Promise<void> {
  await page.evaluate(() => {
    const set = (sel: string, prop: string, value: string) =>
      document.querySelectorAll<HTMLElement>(sel).forEach((el) => el.style.setProperty(prop, value, "important"));
    set("html, body", "height", "auto");
    set("html, body", "overflow", "visible");
    set(".shell, .stage, .rail", "height", "auto");
    set(".main-pane, .pane", "overflow", "visible");
    set(".side-pane", "align-self", "stretch");
    set(".rail", "position", "sticky");
    set(".rail", "top", "0");
    set(".rail", "align-self", "start");
    set(".rail", "height", "100vh");
  });
}

/** How far anything runs past the page sideways: the document past the viewport, or a pane past its own box. */
export async function sideways(page: Page): Promise<number> {
  const width = (page.viewportSize() as { width: number }).width;
  return page.evaluate((w) => {
    let worst = Math.max(document.documentElement.scrollWidth, window.innerWidth) - w;
    for (const el of Array.from(document.querySelectorAll<HTMLElement>(".pane, .main-pane"))) {
      worst = Math.max(worst, el.scrollWidth - el.clientWidth);
    }
    return worst;
  }, width);
}

/** The fixture server has no program run in progress, so the run screen is drawn from tests/fixtures/api/run-one.json. */
export async function withRun(page: Page): Promise<void> {
  const run = JSON.parse(readFileSync("../tests/fixtures/api/run-one.json", "utf-8"));
  await page.route("**/api/v1/runs/2026-10-15-run1", (route) => route.fulfill({ json: run }));
}

export async function withInk(page: Page, ink: string): Promise<void> {
  await page.addInitScript((value) => localStorage.setItem("journal:ink", value), ink);
}

export async function withSidebar(page: Page, mode: string): Promise<void> {
  await page.addInitScript((m) => localStorage.setItem("journal:sidebar", m), mode);
}

/** Scroll the page to its foot: the pane on a wide screen, the document on a phone. */
export async function toFoot(page: Page): Promise<void> {
  await page.evaluate(() => {
    const pane = document.querySelector<HTMLElement>(".main-pane");
    if (pane && pane.scrollHeight > pane.clientHeight) pane.scrollTop = pane.scrollHeight;
    window.scrollTo(0, document.documentElement.scrollHeight);
  });
}

/** Write, wherever it stands: the rail's lamp on a wide screen, the tab bar's disc on a phone. */
export const writeButton = (page: Page) => page.locator(".rail-write:visible, .nav .plus:visible").first();
