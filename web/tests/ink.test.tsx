import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";
import { DEFAULT_INK, INKS, applyInk, chooseInk, savedInk } from "../src/ink";
import { InkChoice } from "../src/routes/settings";
import { click, mount, must, stubFetch } from "./helpers";

const tokens = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "..", "src", "tokens.css"), "utf-8");

// The sheet's contract (WEB-UX, design/DESIGN.md "Colour"): roles, not colours. Light is the :root block; dark is
// written twice, under the device's scheme (the media query) and under the reader's choice ([data-theme="dark"]), and
// the two copies must be the same. Every text role and every ink reads at AA on every ground it can sit on.

/** WCAG 2 relative luminance and contrast ratio. */
function luminance(hex: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * (r as number) + 0.7152 * (g as number) + 0.0722 * (b as number);
}
function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}
const channels = (h: string) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16) / 255) as [number, number, number];
const toHex = (c: number[]) => "#" + c.map((v) => Math.round(Math.min(1, Math.max(0, v)) * 255).toString(16).padStart(2, "0")).join("");
/** `color-mix(in srgb, a p%, b)`. */
function mixSrgb(a: string, b: string, p: number): string {
  const [x, y] = [channels(a), channels(b)];
  return toHex(x.map((v, i) => v * p + (y[i] as number) * (1 - p)));
}
/** `color-mix(in oklab, a p%, b)`, as --her-soft is made (Björn Ottosson's matrices). */
function mixOklab(a: string, b: string, p: number): string {
  const lin = (c: number) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
  const gam = (c: number) => (c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055);
  const lab = (hex: string) => {
    const [r, g, bl] = channels(hex).map(lin) as [number, number, number];
    const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * bl);
    const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * bl);
    const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * bl);
    return [0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s, 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s, 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s];
  };
  const [x, y] = [lab(a), lab(b)];
  const [L, A, B] = x.map((v, i) => v * p + (y[i] as number) * (1 - p)) as [number, number, number];
  const l = (L + 0.3963377774 * A + 0.2158037573 * B) ** 3;
  const m = (L - 0.1055613458 * A - 0.0638541728 * B) ** 3;
  const s = (L - 0.0894841775 * A - 1.291485548 * B) ** 3;
  return toHex([4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s, -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s, -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s].map(gam));
}

const DARK = "@media (prefers-color-scheme: dark)";
const darkAt = [...tokens.matchAll(/@media \(prefers-color-scheme: dark\)/g)].map((m) => m.index as number);
function colour(block: string, name: string): string {
  const m = new RegExp(`--${name}:\\s*(#[0-9a-fA-F]{6})\\s*;`).exec(block);
  if (!m?.[1]) throw new Error(`no --${name}`);
  return m[1].toLowerCase();
}
/** The declarations of the first rule `selector { … }` at or after `from`. */
function rule(selector: string, from = 0): string {
  const at = tokens.indexOf(`${selector} {`, from);
  if (at < 0) throw new Error(`no rule ${selector}`);
  const open = tokens.indexOf("{", at);
  return tokens.slice(open + 1, tokens.indexOf("}", open));
}
const decls = (body: string) => body.split(";").map((d) => d.replace(/\s+/g, " ").trim()).filter(Boolean);
const scheme = {
  light: rule(":root, [data-theme=\"light\"]"),
  dark: rule(":root:not([data-theme=\"light\"])", darkAt[0]),
};
const GROUNDS = ["room", "pane", "raised", "raised-2", "sunk", "letter"] as const;
const TEXT = ["text", "text-2", "text-3"] as const;
/** Each ink's light and dark values, read from its `[data-ink]` rules (dark: both copies). */
const inks = new Map<string, { light: string[]; dark: string[] }>();
for (const m of tokens.matchAll(/\[data-ink="([\w-]+)"\]\s*\{\s*--her:\s*(#[0-9a-fA-F]{6});\s*\}/g)) {
  const entry = inks.get(m[1] as string) ?? { light: [], dark: [] };
  entry[(m.index as number) > (darkAt[1] ?? Infinity) ? "dark" : "light"].push((m[2] as string).toLowerCase());
  inks.set(m[1] as string, entry);
}
const soft = Number(/--her-soft:\s*color-mix\(in oklab, var\(--her\) (\d+)%, var\(--pane\)\)/.exec(tokens)?.[1]) / 100;
const wash = Number(/--her-wash:\s*color-mix\(in srgb, var\(--her\) (\d+)%, var\(--letter\)\)/.exec(tokens)?.[1]) / 100;

afterEach(() => {
  delete document.documentElement.dataset.ink;
});

describe("the token sheet", () => {
  it("writes dark twice, for the device's scheme and for the reader's choice, and the two copies are the same", () => {
    expect(darkAt).toHaveLength(2);
    expect(tokens.indexOf(DARK)).toBe(darkAt[0]);
    expect(decls(rule("[data-theme=\"dark\"]"))).toEqual(decls(scheme.dark));
    for (const name of [...GROUNDS, ...TEXT, "lamp", "on-lamp"]) {
      expect(colour(scheme.light, name), `light --${name}`).toMatch(/^#[0-9a-f]{6}$/);
      expect(colour(scheme.dark, name), `dark --${name}`).toMatch(/^#[0-9a-f]{6}$/);
    }
  });

  it("holds his three greys at AA on every ground, light and dark, and the lamp's label on the lamp", () => {
    for (const mode of ["light", "dark"] as const) {
      for (const text of TEXT) {
        for (const ground of GROUNDS) {
          expect(contrast(colour(scheme[mode], text), colour(scheme[mode], ground)), `${text} on ${ground}, ${mode}`).toBeGreaterThanOrEqual(4.5);
        }
      }
      expect(contrast(colour(scheme[mode], "on-lamp"), colour(scheme[mode], "lamp")), `lamp, ${mode}`).toBeGreaterThanOrEqual(4.5);
    }
  });
});

describe("her inks", () => {
  it("are the four the settings offer, each with one light and one dark value, blue-black the default", () => {
    expect([...inks.keys()]).toEqual(INKS.map((i) => i.id));
    for (const [id, v] of inks) {
      expect(v.light, `${id} light`).toHaveLength(1);
      expect(v.light[0], `${id} light`).toMatch(/^#[0-9a-f]{6}$/);
      expect(v.dark, `${id} dark, both copies`).toHaveLength(2);
      expect(new Set(v.dark).size, `${id}: the two dark copies agree`).toBe(1);
    }
    // the registered default of --her is the blue-black ink
    expect(/@property --her \{[^}]*initial-value:\s*(#[0-9a-f]{6})/.exec(tokens)?.[1]).toBe(inks.get(DEFAULT_INK)?.light[0]);
  });

  it("each read at AA as body text on every ground and on her letter's wash, light and dark, and so does the softer grade", () => {
    expect(soft).toBeGreaterThan(0.5);
    expect(wash).toBeGreaterThan(0);
    for (const [id, v] of inks) {
      for (const mode of ["light", "dark"] as const) {
        const ink = v[mode][0] as string;
        const ground = (name: string) => colour(scheme[mode], name);
        const letter = mixSrgb(ink, ground("letter"), wash);
        for (const g of GROUNDS) expect(contrast(ink, ground(g)), `${id} on ${mode} ${g}`).toBeGreaterThanOrEqual(4.5);
        expect(contrast(ink, letter), `${id} on its wash, ${mode}`).toBeGreaterThanOrEqual(4.5);
        const softer = mixOklab(ink, ground("pane"), soft);
        expect(contrast(softer, ground("pane")), `${id} soft on ${mode} pane`).toBeGreaterThanOrEqual(4.5);
        expect(contrast(softer, letter), `${id} soft on its wash, ${mode}`).toBeGreaterThanOrEqual(4.5);
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
