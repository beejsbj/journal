/**
 * The page's theme: the device's own scheme (System, the default), or the reader's choice on this device: Paper (light)
 * or Night (dark). Kept like the ink (ink.ts): `localStorage` under `journal:theme`, put on `<html data-theme>` before
 * the first render, never sent to the server. System keeps nothing and sets no attribute, so `tokens.css` follows
 * `prefers-color-scheme`; a choice sets `data-theme="light|dark"`, which wins over the media query either way.
 *
 * The browser's own colour (`<meta name="theme-color">`, one per scheme in index.html) follows the choice too: both
 * metas are pointed at the chosen theme's page colour, and put back as index.html has them for System.
 */
import { useEffect, useState } from "preact/hooks";

export const THEMES = [
  { id: "system", label: "System", note: "as this device is set" },
  { id: "light", label: "Paper", note: "warm paper, by day" },
  { id: "dark", label: "Night", note: "the cool room, after dark" },
] as const;

export type Theme = (typeof THEMES)[number]["id"];
export type Scheme = "light" | "dark";

export const DEFAULT_THEME: Theme = "system";
const KEY = "journal:theme";
/** The page colour of each scheme (tokens.css `--pane`; the manifest and index.html carry the same). */
export const PANE: Record<Scheme, string> = { light: "#fbf8f1", dark: "#141921" };

export function isTheme(value: unknown): value is Theme {
  return THEMES.some((t) => t.id === value);
}

/** The theme this device chose, or System (also when storage cannot be read). */
export function savedTheme(): Theme {
  try {
    const v = localStorage.getItem(KEY);
    return isTheme(v) ? v : DEFAULT_THEME;
  } catch {
    return DEFAULT_THEME;
  }
}

const listeners = new Set<() => void>();

/** Put a theme on the page (not kept). */
export function applyTheme(theme: Theme): void {
  const root = document.documentElement;
  if (theme === "system") delete root.dataset.theme;
  else root.dataset.theme = theme;
  for (const meta of document.querySelectorAll<HTMLMetaElement>('meta[name="theme-color"]')) {
    const own = /dark/.test(meta.media) ? PANE.dark : PANE.light;
    meta.content = theme === "system" ? own : PANE[theme];
  }
  for (const l of [...listeners]) l();
}

/** Choose a theme: on the page at once, and kept for the next visit on this device. */
export function chooseTheme(theme: Theme): void {
  applyTheme(theme);
  try {
    if (theme === DEFAULT_THEME) localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, theme);
  } catch {
    /* storage refused (a private window): the choice holds for this visit */
  }
}

/** The scheme the device asks for, when nothing is chosen. */
export function systemScheme(): Scheme {
  return typeof matchMedia === "function" && matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

/** The scheme on screen now: the choice, or the device's. */
export function currentScheme(): Scheme {
  const set = document.documentElement.dataset.theme;
  return set === "dark" || set === "light" ? set : systemScheme();
}

/** The chosen theme and the scheme on screen, kept current (a choice anywhere, or the device switching at dusk). */
export function useTheme(): { theme: Theme; scheme: Scheme; choose: (t: Theme) => void } {
  const [, redraw] = useState(0);
  useEffect(() => {
    const again = () => redraw((n) => n + 1);
    listeners.add(again);
    const mq = typeof matchMedia === "function" ? matchMedia("(prefers-color-scheme: dark)") : null;
    mq?.addEventListener?.("change", again);
    return () => {
      listeners.delete(again);
      mq?.removeEventListener?.("change", again);
    };
  }, []);
  const set = document.documentElement.dataset.theme;
  return { theme: set === "dark" || set === "light" ? set : "system", scheme: currentScheme(), choose: chooseTheme };
}
