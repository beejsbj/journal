import { existsSync, readFileSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { ICONS, buildManifest, palette } from "../scripts/manifest";

const web = join(dirname(fileURLToPath(import.meta.url)), "..");
const tokens = readFileSync(join(web, "src/tokens.css"), "utf-8");
const html = readFileSync(join(web, "index.html"), "utf-8");

describe("the manifest", () => {
  const m = buildManifest(tokens);

  it("installs from any origin: root start_url and scope, no host or port anywhere", () => {
    expect(m).toMatchObject({ id: "/", start_url: "/", scope: "/", display: "standalone", name: "Journal" });
    const text = JSON.stringify(m);
    expect(text).not.toMatch(/https?:/);
    expect(text).not.toMatch(/:\d{2,5}\b/);
    for (const icon of m.icons) expect(icon.src.startsWith("/")).toBe(false);   // relative to the manifest, so any origin
  });

  it("takes its colours from tokens.css", () => {
    const p = palette(tokens);
    expect(p.light.bg).toBe("#f4efe4");
    expect(m.theme_color).toBe(p.light.bg);
    expect(m.background_color).toBe(p.light.bg);
  });

  it("is the page's theme-color too, light and dark", () => {
    const p = palette(tokens);
    expect(html).toContain(`content="${p.light.bg}" media="(prefers-color-scheme: light)"`);
    expect(html).toContain(`content="${p.dark.bg}" media="(prefers-color-scheme: dark)"`);
  });

  it("names icons that exist: 192, 512 and a maskable 512, made from the SVG mark", () => {
    expect(m.icons.map((i) => `${i.sizes} ${i.purpose}`)).toEqual(["192x192 any", "512x512 any", "512x512 maskable"]);
    for (const icon of ICONS) {
      const file = join(web, "public", icon.src);
      expect(existsSync(file), icon.src).toBe(true);
      expect(readFileSync(file).subarray(1, 4).toString("latin1")).toBe("PNG");
    }
    expect(existsSync(join(web, "public/icons/mark.svg"))).toBe(true);
    expect(statSync(join(web, "public/icons/apple-touch-icon.png")).size).toBeGreaterThan(500);
    expect(html).toContain('rel="apple-touch-icon" href="/icons/apple-touch-icon.png"');
  });
});
