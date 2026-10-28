import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";
import { DEFAULT_INK, INKS, applyInk, chooseInk, savedInk } from "../src/ink";
import { InkChoice } from "../src/routes/settings";
import { click, mount, must, stubFetch } from "./helpers";

const tokens = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "..", "src", "tokens.css"), "utf-8");

/** WCAG 2 relative luminance and contrast ratio. */
function luminance(hex: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * (r as number) + 0.7152 * (g as number) + 0.0722 * (b as number);
}
function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}
/** `color-mix(in srgb, a p%, b)`, as --her-soft is made. */
function mix(a: string, b: string, p: number): string {
  const ch = (h: string, i: number) => parseInt(h.slice(i, i + 2), 16);
  return "#" + [1, 3, 5].map((i) => Math.round(ch(a, i) * p + ch(b, i) * (1 - p)).toString(16).padStart(2, "0")).join("");
}

const DARK = "@media (prefers-color-scheme: dark)";
const darkAt = [...tokens.matchAll(/@media \(prefers-color-scheme: dark\)/g)].map((m) => m.index as number);
function colour(block: string, name: string): string {
  const m = new RegExp(`--${name}:\\s*(#[0-9a-fA-F]{6})\\s*;`).exec(block);
  if (!m?.[1]) throw new Error(`no --${name}`);
  return m[1].toLowerCase();
}
const paper = {
  light: { bg: colour(tokens.slice(0, darkAt[0]), "bg"), surface: colour(tokens.slice(0, darkAt[0]), "surface") },
  dark: { bg: colour(tokens.slice(darkAt[0], darkAt[1]), "bg"), surface: colour(tokens.slice(darkAt[0], darkAt[1]), "surface") },
};
/** Each ink's light and dark value, read from its `[data-ink]` rules. */
const inks = new Map<string, { light?: string; dark?: string }>();
for (const m of tokens.matchAll(/\[data-ink="([\w-]+)"\]\s*\{\s*--accent:\s*(#[0-9a-fA-F]{6});\s*\}/g)) {
  const entry = inks.get(m[1] as string) ?? {};
  entry[(m.index as number) > (darkAt[1] ?? Infinity) ? "dark" : "light"] = (m[2] as string).toLowerCase();
  inks.set(m[1] as string, entry);
}
const soft = Number(/--her-soft:\s*color-mix\(in srgb, var\(--accent\) (\d+)%, var\(--bg\)\)/.exec(tokens)?.[1]) / 100;

afterEach(() => {
  delete document.documentElement.dataset.ink;
});

describe("her inks", () => {
  it("are the four the settings offer, each with a light and a dark value, blue-black the default", () => {
    expect(darkAt).toHaveLength(2);
    expect(tokens.indexOf(DARK)).toBe(darkAt[0]);
    expect([...inks.keys()]).toEqual(INKS.map((i) => i.id));
    for (const [id, v] of inks) {
      expect(v.light, `${id} light`).toMatch(/^#[0-9a-f]{6}$/);
      expect(v.dark, `${id} dark`).toMatch(/^#[0-9a-f]{6}$/);
    }
    // the sheet's own default (the :root blocks) is the blue-black ink, light and dark
    expect(colour(tokens.slice(0, darkAt[0]), "accent")).toBe(inks.get(DEFAULT_INK)?.light);
    expect(colour(tokens.slice(darkAt[0], darkAt[1]), "accent")).toBe(inks.get(DEFAULT_INK)?.dark);
  });

  it("each read at AA (4.5:1) as body text on the paper and the surface, in light and in dark, and so does the softer grade", () => {
    expect(soft).toBeGreaterThan(0.5);
    for (const [id, v] of inks) {
      for (const mode of ["light", "dark"] as const) {
        const ink = v[mode] as string;
        for (const ground of ["bg", "surface"] as const) {
          expect(contrast(ink, paper[mode][ground]), `${id} on ${mode} ${ground}`).toBeGreaterThanOrEqual(4.5);
        }
        expect(contrast(mix(ink, paper[mode].bg, soft), paper[mode].bg), `${id} soft on ${mode}`).toBeGreaterThanOrEqual(4.5);
      }
    }
  });
});

describe("his inks and the status colours", () => {
  it("read at AA as body text wherever they are set, light and dark", () => {
    const blocks = { light: tokens.slice(0, darkAt[0]), dark: tokens.slice(darkAt[0], darkAt[1]) };
    // [text, ground]: his three greys on every ground they meet, the status words, the danger button's hover, a primary button
    const pairs: [string, string][] = [
      ["ink", "bg"], ["ink", "surface"], ["ink", "sunk"], ["ink-2", "bg"], ["ink-2", "surface"], ["ink-2", "sunk"],
      ["ink-3", "bg"], ["ink-3", "surface"], ["ink-3", "sunk"], ["warn", "bg"], ["bad", "bg"], ["bg", "bad"], ["bg", "ink"],
    ];
    for (const mode of ["light", "dark"] as const) {
      // the dark sheet sets only what changes: the rest is the light sheet's
      const value = (name: string) => {
        try {
          return colour(blocks[mode], name);
        } catch {
          return colour(blocks.light, name);
        }
      };
      for (const [text, ground] of pairs) {
        expect(contrast(value(text), value(ground)), `${text} on ${ground}, ${mode}`).toBeGreaterThanOrEqual(4.5);
      }
    }
  });
});

describe("the ink this device chose", () => {
  it("is blue-black until one is chosen, and a stored value that is not an ink is ignored", () => {
    expect(savedInk()).toBe("blue-black");
    localStorage.setItem("journal:ink", "chartreuse");
    expect(savedInk()).toBe("blue-black");
  });

  it("is put on the page at once and kept in this browser; the default keeps nothing", () => {
    chooseInk("violet");
    expect(document.documentElement.dataset.ink).toBe("violet");
    expect(localStorage.getItem("journal:ink")).toBe("violet");
    expect(savedInk()).toBe("violet");
    chooseInk("blue-black");
    expect(document.documentElement.dataset.ink).toBe("blue-black");
    expect(localStorage.getItem("journal:ink")).toBeNull();
    applyInk("green");
    expect(localStorage.getItem("journal:ink")).toBeNull();   // applying is not choosing
  });
});

describe("Settings, Ink", () => {
  it("offers the four inks as a radio group, each shown typed in itself, and the choice is live and asks the server nothing", async () => {
    localStorage.setItem("journal:ink", "brown");
    const api = stubFetch({});
    const host = mount(<InkChoice />);
    const radios = [...host.querySelectorAll<HTMLInputElement>('input[type="radio"][name="ink"]')];
    expect(radios.map((r) => r.value)).toEqual(["blue-black", "brown", "violet", "green"]);
    expect(radios.filter((r) => r.checked).map((r) => r.value)).toEqual(["brown"]);
    // every option carries its own ink, so its sample is drawn in it whatever the page's ink is
    expect([...host.querySelectorAll("label.ink")].map((l) => l.getAttribute("data-ink"))).toEqual(["blue-black", "brown", "violet", "green"]);
    expect(host.querySelector(".ink-from")?.textContent).toBe("From the companion");
    await click(must(radios[3]));
    expect(document.documentElement.dataset.ink).toBe("green");
    expect(localStorage.getItem("journal:ink")).toBe("green");
    expect(host.querySelector("label.ink.on")?.getAttribute("data-ink")).toBe("green");
    // a display preference of this device: nothing is read for it or written to the server
    expect(api.fn).not.toHaveBeenCalled();
  });
});
