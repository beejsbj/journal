import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";
import { palette } from "../scripts/manifest";
import { PANE, applyTheme, chooseTheme, savedTheme } from "../src/theme";
import "./helpers";

const tokens = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "..", "src", "tokens.css"), "utf-8");

afterEach(() => {
  delete document.documentElement.dataset.theme;
  for (const m of document.head.querySelectorAll('meta[name="theme-color"]')) m.remove();
});

function metas(): HTMLMetaElement[] {
  const make = (content: string, media: string) => {
    const m = document.createElement("meta");
    m.name = "theme-color";
    m.content = content;
    m.media = media;
    document.head.appendChild(m);
    return m;
  };
  return [make("#fbf8f1", "(prefers-color-scheme: light)"), make("#141921", "(prefers-color-scheme: dark)")];
}

describe("the theme this device chose", () => {
  it("knows each scheme's page colour as tokens.css has it", () => {
    const p = palette(tokens);
    expect(PANE).toEqual({ light: p.light.pane, dark: p.dark.pane });
  });

  it("is System until one is chosen, and a stored value that is not a theme is ignored", () => {
    expect(savedTheme()).toBe("system");
    localStorage.setItem("journal:theme", "sepia");
    expect(savedTheme()).toBe("system");
  });

  it("is put on <html data-theme> at once and kept in this browser; System keeps nothing and sets nothing", () => {
    chooseTheme("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(localStorage.getItem("journal:theme")).toBe("dark");
    expect(savedTheme()).toBe("dark");
    chooseTheme("system");
    expect(document.documentElement.dataset.theme).toBeUndefined();
    expect(localStorage.getItem("journal:theme")).toBeNull();
    applyTheme("light");
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(localStorage.getItem("journal:theme")).toBeNull();   // applying is not choosing
  });

  it("points the browser's colour at the chosen page, and gives each scheme its own back for System", () => {
    const [light, dark] = metas() as [HTMLMetaElement, HTMLMetaElement];
    applyTheme("dark");
    expect([light.content, dark.content]).toEqual(["#141921", "#141921"]);
    applyTheme("light");
    expect([light.content, dark.content]).toEqual(["#fbf8f1", "#fbf8f1"]);
    applyTheme("system");
    expect([light.content, dark.content]).toEqual(["#fbf8f1", "#141921"]);
  });
});
