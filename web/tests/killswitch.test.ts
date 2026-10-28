import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

// deploy/killswitch-sw.js is what a rollback to a build without a worker serves as /sw.js (DEPLOY.md): a 404 would leave
// the installed worker, and the PWA shell, in place. Run here against a fake worker scope.
const source = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "..", "..", "deploy", "killswitch-sw.js"), "utf-8");

function run() {
  const handlers: Record<string, (event: any) => void> = {};
  const log: string[] = [];
  const stored = new Set(["journal-data-v1", "journal-media-v1", "workbox-precache-v2-https://h.example/"]);
  const scope = {
    addEventListener: (type: string, h: (event: any) => void) => (handlers[type] = h),
    skipWaiting: () => log.push("skipWaiting"),
    registration: { unregister: async () => void log.push("unregister") },
    clients: {
      matchAll: async (opts: unknown) => {
        log.push("matchAll " + JSON.stringify(opts));
        return [{ url: "https://h.example/entries", navigate: async (url: string) => void log.push("navigate " + url) }];
      },
    },
  };
  const caches = { keys: async () => [...stored], delete: async (n: string) => void stored.delete(n) };
  new Function("self", "caches", source)(scope, caches);
  return { handlers, log, stored };
}

describe("the kill-switch worker", () => {
  it("takes over at once", () => {
    const { handlers, log } = run();
    handlers.install?.({});
    expect(log).toEqual(["skipWaiting"]);
  });

  it("empties every cache, unregisters itself and reloads the open pages from the network", async () => {
    const { handlers, log, stored } = run();
    let work: Promise<unknown> = Promise.resolve();
    handlers.activate?.({ waitUntil: (p: Promise<unknown>) => (work = p) });
    await work;
    expect([...stored]).toEqual([]);
    expect(log).toContain("unregister");
    expect(log.some((l) => l.startsWith("matchAll") && l.includes('"includeUncontrolled":true'))).toBe(true);
    expect(log).toContain("navigate https://h.example/entries");
  });

  it("does not answer requests itself, so everything goes to the server", () => {
    expect(run().handlers.fetch).toBeUndefined();
  });
});
