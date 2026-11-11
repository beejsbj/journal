/**
 * What a route asks of the shell while it is on screen: `fold` folds the rail to its icons (an open entry, so the
 * page keeps both its margins), `panes` hands the stage to the route, which lays its own panes on the desk (the
 * entries list beside an entry). Asked with a hook; the ask ends when the route unmounts or asks otherwise.
 *
 *   useShellMode({ fold: true, panes: true });
 */
import { useEffect, useLayoutEffect, useState } from "preact/hooks";

export interface ShellMode {
  fold: boolean;
  panes: boolean;
}

const asks = new Map<symbol, Partial<ShellMode>>();
const listeners = new Set<() => void>();
const publish = () => listeners.forEach((l) => l());

function current(): ShellMode {
  let fold = false;
  let panes = false;
  for (const a of asks.values()) {
    fold ||= !!a.fold;
    panes ||= !!a.panes;
  }
  return { fold, panes };
}

/** Ask the shell for a fold and/or the stage, for as long as the calling component is mounted. */
export function useShellMode(mode: Partial<ShellMode>): void {
  useLayoutEffect(() => {
    const key = Symbol("shell-mode");
    asks.set(key, { ...mode });
    publish();
    return () => {
      asks.delete(key);
      publish();
    };
  }, [mode.fold, mode.panes]);
}

/** The shell's side: what every route on screen has asked for, together. */
export function useShellState(): ShellMode {
  const [state, setState] = useState(current);
  useEffect(() => {
    const again = () => setState(current());
    listeners.add(again);
    again();
    return () => {
      listeners.delete(again);
    };
  }, []);
  return state;
}
