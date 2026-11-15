import { expect, test } from "@playwright/test";
import { SCHEMES, at, settle, toFoot, withInk } from "./lib";

// Insights, Settings and the Library, their states: Settings' notifications card (below), Settings with a change waiting (the save bar docked at the foot),
// mid-page with the bar up (it must sit on no control), at its foot with the health check open, after a jump from the
// index, and with another ink chosen; Insights with a day's reflection open; a built-in's reset asked in place.

for (const scheme of SCHEMES) {
  test.describe(scheme, () => {
    test.use({ colorScheme: scheme });

    test(`settings, a change waiting ${scheme}`, async ({ page }, info) => {
      await page.goto("/settings");
      await settle(page, 700);
      const base = at(info.project.name, scheme, "settings");
      await page.getByRole("button", { name: "More: Hours a prompt stays open" }).click();
      await page.getByRole("switch", { name: "Week review" }).click();
      await page.waitForTimeout(800);
      await page.screenshot({ path: `${base}-pending.png` });
      // mid-page: the bar is docked across the foot, with its own backing; no control shows through it
      await page.getByRole("navigation", { name: "Settings sections" }).getByRole("link", { name: /Prompts/ }).click();
      await page.waitForTimeout(900);
      await page.screenshot({ path: `${base}-prompts.png` });
      const bar = (await page.locator(".save-bar").boundingBox()) as { y: number; height: number };
      const covered = await page.evaluate((top) => {
        const out: string[] = [];
        for (const el of Array.from(document.querySelectorAll<HTMLElement>(".settings-form button, .settings-form input, .settings-form [role=switch]"))) {
          const r = el.getBoundingClientRect();
          if (r.height === 0) continue;
          const y = r.top + r.height / 2;
          if (y > top && y < window.innerHeight) {
            const hit = document.elementFromPoint(r.left + r.width / 2, y);
            if (hit && !el.contains(hit) && hit.closest(".save-dock") === null) out.push(el.id || el.getAttribute("aria-label") || el.tagName);
          }
        }
        return out;
      }, bar.y);
      expect.soft(covered, "a control showing through the bar").toEqual([]);
      // its foot, the health check open: the bar sits under the last row
      await page.getByRole("button", { name: /Health check/ }).click();
      await page.waitForTimeout(900);
      await toFoot(page);
      await page.waitForTimeout(900);
      await page.screenshot({ path: `${base}-foot.png` });
    });

    test(`settings, another ink ${scheme}`, async ({ page }, info) => {
      await withInk(page, "brown");
      await page.goto("/settings");
      await settle(page, 700);
      await page.locator("#set-ink").scrollIntoViewIfNeeded();
      await page.screenshot({ path: `${at(info.project.name, scheme, "settings-ink-brown")}.png` });
    });

    test(`insights, a day open ${scheme}`, async ({ page }, info) => {
      await page.goto("/insights");
      await settle(page, 700);
      const slip = page.locator(".reflection .disclosure-head").first();
      await expect(slip).toBeVisible();
      await slip.click();
      await page.waitForTimeout(800);
      await page.screenshot({ path: `${at(info.project.name, scheme, "insights-day-open")}.png` });
    });

    test(`library, reset asked in place ${scheme}`, async ({ page }, info) => {
      await page.goto("/library/midweek");
      await settle(page, 700);
      await page.locator(".edited-note").getByRole("button", { name: "Reset to default" }).click();
      await page.locator(".edited-note .confirm").evaluate((el) => el.scrollIntoView({ block: "center" }));   // clear of the phone's tab bar
      await page.waitForTimeout(300);
      await page.screenshot({ path: `${at(info.project.name, scheme, "library-reset-asking")}.png` });
    });
  });
}

// Notifications on this device (OPEN-PUSH), at the head of "07 This device", with the worker allowed so the device is
// read, over a server that can push: as headless Chromium starts (notifications denied: "blocked for this site"), and
// with the permission not asked yet (Off for this device, Turn on notifications). Light and dark, phone and desktop. The
// other states (the server cannot push, an older server, an iPhone not on the Home Screen, no key yet, On) are pinned
// by web/tests/push.test.tsx.
const PUSH = { available: true, detail: "", public_key: "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4", when: "no-chat", active: true, subscriptions: 0 };
for (const scheme of SCHEMES) {
  test.describe(`notifications ${scheme}`, () => {
    test.use({ colorScheme: scheme, serviceWorkers: "allow" });
    test(`settings notifications ${scheme}`, async ({ page }, info) => {
      await page.route("**/api/v1/push", (r) => r.fulfill({ json: PUSH }));
      const shoot = async (name: string, button: string) => {
        await page.goto("/settings");
        await settle(page, 700);
        await expect(page.locator(".set-push").getByRole("button", { name: button })).toBeVisible({ timeout: 15_000 });
        await page.evaluate(() => document.querySelector(".set-push")?.scrollIntoView({ block: "center" }));
        await page.waitForTimeout(300);
        await page.screenshot({ path: `${at(info.project.name, scheme, name)}.png` });
      };
      await shoot("settings-push-blocked", "Turn on notifications");
      await expect(page.locator(".set-push .field-error")).toContainText("blocked");
      // headless Chromium keeps notifications denied whatever is granted: say "not asked yet", as a fresh browser does
      await page.addInitScript(() => Object.defineProperty(Notification, "permission", { get: () => "default" }));
      await shoot("settings-push-off", "Turn on notifications");
      await expect(page.locator(".set-push .field-error")).toHaveCount(0);
    });
  });
}
