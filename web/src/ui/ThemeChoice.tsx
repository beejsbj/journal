/**
 * The page's theme, chosen on this device (theme.ts): ThemeChoice is the full choice (System · Paper · Night, a
 * Segmented, for Settings), ThemeToggle the quick switch in the rail's foot and the phone's top bar (sun by night,
 * moon by day: it flips to the other scheme and keeps that as the choice).
 */
import { Moon, Sun } from "../icons";
import { THEMES, useTheme } from "../theme";
import { IconButton } from "./Button";
import { Segmented } from "./Segmented";

export function ThemeChoice({ wide }: { wide?: boolean }) {
  const { theme, choose } = useTheme();
  return <Segmented label="Page" options={THEMES.map((t) => ({ value: t.id, label: t.label }))} value={theme} onChange={choose} wide={wide} />;
}

export function ThemeToggle({ class: cls }: { class?: string }) {
  const { scheme, choose } = useTheme();
  const night = scheme === "dark";
  return (
    <IconButton class={cls} label={night ? "Paper (light)" : "Night (dark)"} onClick={() => choose(night ? "light" : "dark")}>
      {night ? <Sun /> : <Moon />}
    </IconButton>
  );
}
