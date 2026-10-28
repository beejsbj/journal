import { defineConfig } from "@playwright/test";

/**
 * The design screenshots: every screen at phone and desktop width, light and dark, over the fixture server's
 * `--demo` journal (a prompt in every state). Not part of CI's smoke test; run off the journal's host:
 *   npx playwright test -c playwright.shots.config.ts   (writes shots/out/*.png)
 */
export const SHOTS_BASE = "http://127.0.0.1:8792";
export const SHOTS_PAIR_FILE = process.env.JOURNAL_SHOTS_PAIR_FILE ?? "/tmp/journal-shots-pair-codes";

export default defineConfig({
  testDir: "shots",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: "list",
  globalSetup: "./shots/pair.setup.ts",
  use: { baseURL: SHOTS_BASE, serviceWorkers: "block", storageState: "shots/.paired.json" },
  projects: [
    { name: "phone", use: { browserName: "chromium", viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true } },
    { name: "desktop", use: { browserName: "chromium", viewport: { width: 1280, height: 860 } } },
  ],
  webServer: {
    command: `python3 ../tools/serve_fixture.py --demo --port 8792 --pair-file ${SHOTS_PAIR_FILE}`,
    url: `${SHOTS_BASE}/healthz`,
    reuseExistingServer: false,
    timeout: 60_000,
  },
});
