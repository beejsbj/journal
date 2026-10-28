import { defineConfig } from "@playwright/test";

/** The smoke test runs against `tools/serve_fixture.py`: the real server over a temporary journal. CI only. */
export const BASE = "http://127.0.0.1:8791";
export const PAIR_FILE = process.env.JOURNAL_PAIR_FILE ?? "/tmp/journal-pair-codes";

export default defineConfig({
  testDir: "e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: "list",
  globalSetup: "./e2e/pair.setup.ts",
  use: { baseURL: BASE, trace: "retain-on-failure", storageState: "e2e/.paired.json" },
  projects: [
    { name: "desktop", use: { browserName: "chromium", viewport: { width: 1100, height: 800 } } },
    { name: "phone", use: { browserName: "chromium", viewport: { width: 390, height: 780 }, isMobile: true, hasTouch: true } },
  ],
  webServer: {
    command: `python ../tools/serve_fixture.py --port 8791 --pair-file ${PAIR_FILE}`,
    url: `${BASE}/healthz`,
    reuseExistingServer: false,
    timeout: 60_000,
  },
});
