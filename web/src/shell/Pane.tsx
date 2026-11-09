/**
 * A pane on the desk: the page's paper, rounded, lifted, with the lamp's light at its head (`glow`). The shell lays
 * one for every route; a route that asked for the stage (`useShellMode({ panes: true })`) lays its own, side by side
 * (`entry` lifts it above its neighbour, as an open entry sits above its list). It scrolls on its own on a wide
 * screen; on a phone the page scrolls as a whole. `vt` names it for View Transitions ("page", "entrypane").
 */
import type { ComponentChildren } from "preact";

export function Pane({ children, glow = true, entry, vt, class: cls, label, scroll = true }: {
  children: ComponentChildren;
  glow?: boolean;
  entry?: boolean;
  vt?: string;
  class?: string;
  label?: string;
  scroll?: boolean;
}) {
  return (
    <section class={["pane", entry && "raised-pane", scroll && "scroll", cls].filter(Boolean).join(" ")} aria-label={label}
      style={vt ? { "view-transition-name": vt } : undefined}>
      {glow && <div class="lamp-light" aria-hidden="true" />}
      {children}
    </section>
  );
}
