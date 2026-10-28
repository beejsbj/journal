import { defineConfig } from "vitest/config";
import type { Plugin } from "vite";
import preact from "@preact/preset-vite";
import { VitePWA } from "vite-plugin-pwa";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { buildManifest } from "./scripts/manifest";
import { sourceHash } from "./scripts/source-hash";

const root = fileURLToPath(new URL(".", import.meta.url));

/** `static/build.json`: which sources this build came from (no timestamp, so a rebuild is byte-identical). */
function buildInfo(): Plugin {
  return {
    name: "journal-build-info",
    generateBundle() {
      this.emitFile({
        type: "asset",
        fileName: "build.json",
        source: JSON.stringify({ source: sourceHash(root) }) + "\n",
      });
    },
  };
}

export default defineConfig({
  plugins: [
    preact(),
    buildInfo(),
    // The service worker is `src/sw.ts` (Workbox precaches the shell; the API rules are ours, `src/swCore.ts`); the
    // plugin builds it to `sw.js`, injects the precache list (revisioned by content), writes `manifest.webmanifest`
    // from `scripts/manifest.ts` (colours from `tokens.css`) and links it from index.html. Registration and the
    // "Update available" prompt are `src/pwa.ts`, so the plugin injects no registration script (the CSP has no
    // inline script). `build.json` is ours and is not precached.
    VitePWA({
      strategies: "injectManifest",
      srcDir: "src",
      filename: "sw.ts",
      registerType: "prompt",
      injectRegister: false,
      manifestFilename: "manifest.webmanifest",
      manifest: buildManifest(readFileSync(new URL("./src/tokens.css", import.meta.url), "utf-8")),
      injectManifest: {
        rollupFormat: "iife",
        globPatterns: ["**/*.{js,css,html,svg,png,webmanifest,woff2}"],   // the reading face too, so his words look the same offline
        globIgnores: ["build.json"],
      },
      devOptions: { enabled: false },
    }),
  ],
  base: "/",
  build: {
    outDir: "../src/journal/serve/static",
    emptyOutDir: true,
    sourcemap: false,
    target: "es2022",
  },
  server: {
    // `journal serve --dev --bind 127.0.0.1 --port 8790` answers the API; the app is a same-origin page in production.
    proxy: { "/api": "http://127.0.0.1:8790", "/healthz": "http://127.0.0.1:8790" },
  },
  test: {
    environment: "jsdom",
    environmentOptions: { jsdom: { url: "http://localhost:3000/" } },
    include: ["tests/**/*.test.{ts,tsx}"],
  },
});
