/**
 * Page transitions. Every in-app link already gets one: the shell catches link clicks and runs the route change
 * inside a View Transition (a cross-fade of the page pane; the rail never moves). A link can say how it moves with
 * `data-vt`: "push" (deeper: on a phone the new page comes in from the right), "pop" (back), "entry" (the entry pane
 * floats in), "none". A route that changes the page without a link (a sidebar switch, a filter) wraps the change:
 *
 *   withTransition(() => setMode("rail"), "page");
 *   const go = useNavigate(); go("/entries/2026-10-14-evening", { kind: "push" });
 *
 * Where View Transitions are missing the change happens at once and the page settles in (10 px up, 460 ms) with the
 * Web Animations API; with reduced motion nothing moves at all.
 */
import { useLocation } from "preact-iso";
import { useCallback, useEffect } from "preact/hooks";
import { reducedMotion } from "../ui/motion";

export type TransitionKind = "page" | "push" | "pop" | "entry" | "none";

type VTDocument = Document & { startViewTransition?: (update: () => Promise<void> | void) => { finished: Promise<void> } };

/** One task later: Preact has drawn what the update asked for (it renders on a microtask). */
const drawn = () => new Promise<void>((r) => setTimeout(r, 0));

function settleIn(kind: TransitionKind): void {
  const page = document.querySelector<HTMLElement>("[data-vt-page]");
  if (!page || typeof page.animate !== "function") return;
  const from = kind === "push" ? "translateX(24px)" : kind === "pop" ? "translateX(-24px)" : "translateY(10px)";
  page.animate([{ opacity: 0, transform: from }, { opacity: 1, transform: "none" }], { duration: 460, easing: "cubic-bezier(.16,1,.3,1)" });
}

/** Run `update` (a state change, a route) as a transition of kind `kind`. Resolves when the motion is done. */
export function withTransition(update: () => void, kind: TransitionKind = "page"): Promise<void> {
  if (kind === "none" || typeof document === "undefined" || reducedMotion()) {
    update();
    return Promise.resolve();
  }
  const doc = document as VTDocument;
  if (typeof doc.startViewTransition === "function") {
    const root = document.documentElement;
    root.dataset.vt = kind;
    const t = doc.startViewTransition(async () => {
      update();
      await drawn();
    });
    const done = () => {
      if (root.dataset.vt === kind) delete root.dataset.vt;
    };
    return t.finished.then(done, done);
  }
  update();
  return drawn().then(() => settleIn(kind));
}

/** Navigate as a transition: `go(url, { kind: "push", replace })`. */
export function useNavigate(): (url: string, opts?: { kind?: TransitionKind; replace?: boolean }) => Promise<void> {
  const { route } = useLocation();
  return useCallback((url, opts = {}) => withTransition(() => route(url, opts.replace), opts.kind ?? "page"), [route]);
}

/**
 * The shell's half: in-app link clicks become transitions. It listens on the document, after any handler on the link
 * itself (one that called preventDefault keeps the click), and before the router's listener on the window, which it
 * then stops, since it routes the click itself; the filters are the router's own (same origin, no modifier, no
 * target, no download, not a bare #hash).
 */
export function useLinkTransitions(): void {
  const { route } = useLocation();
  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.altKey || e.shiftKey) return;
      const a = e.composedPath().find((el) => (el as Element).nodeName === "A") as HTMLAnchorElement | undefined;
      const href = a?.getAttribute("href");
      if (!a || !href || !a.href) return;
      if (a.origin !== location.origin || href.startsWith("#") || (a.target && !/^_?self$/i.test(a.target)) || a.hasAttribute("download")) return;
      const url = a.href.replace(location.origin, "");
      e.preventDefault();
      e.stopPropagation();
      void withTransition(() => route(url), (a.dataset.vt as TransitionKind | undefined) ?? "page");
    };
    document.addEventListener("click", onClick);
    return () => document.removeEventListener("click", onClick);
  }, [route]);
}
