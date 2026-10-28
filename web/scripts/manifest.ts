/**
 * The web app manifest, built from `tokens.css` so the installed app's colours are the page's colours. Nothing here
 * names a host or a port: `start_url`, `scope` and `id` are `/` and the icons are relative to the manifest, so the
 * same build installs from any origin that serves it (a tailnet address with a port, a published host, loopback).
 * `tests/pwa.test.ts` pins these properties and the colours; `tests/test_web_build.py` checks the committed build.
 */

export interface Palette {
  light: { bg: string; accent: string };
  dark: { bg: string; accent: string };
}

function value(block: string, name: string): string {
  const m = new RegExp(`--${name}:\\s*(#[0-9a-fA-F]{6})\\s*;`).exec(block);
  if (!m?.[1]) throw new Error(`tokens.css has no --${name} colour in the block it should be in`);
  return m[1].toLowerCase();
}

/** The paper and the accent of the light sheet (the `:root` block) and of the dark one (`prefers-color-scheme: dark`). */
export function palette(tokens: string): Palette {
  const at = tokens.indexOf("prefers-color-scheme: dark");
  if (at < 0) throw new Error("tokens.css has no dark block");
  const light = tokens.slice(0, at);
  const dark = tokens.slice(at);
  return {
    light: { bg: value(light, "bg"), accent: value(light, "accent") },
    dark: { bg: value(dark, "bg"), accent: value(dark, "accent") },
  };
}

export const ICONS = [
  { src: "icons/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
  { src: "icons/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
  { src: "icons/icon-maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
] as const;

export function buildManifest(tokens: string) {
  const { light } = palette(tokens);
  return {
    id: "/",
    name: "Journal",
    short_name: "Journal",
    description: "Your journal: today, your entries and your practices.",
    start_url: "/",
    scope: "/",
    display: "standalone" as const,
    orientation: "any" as const,
    background_color: light.bg,
    theme_color: light.bg,
    icons: [...ICONS],
  };
}
