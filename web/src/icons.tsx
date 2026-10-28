/** A small inline icon set (stroke icons, `currentColor`); decorative, so hidden from assistive technology. */
import type { JSX } from "preact";

type P = Omit<JSX.SVGAttributes<SVGSVGElement>, "d">;

function Svg(props: P & { d: string }) {
  const { d, ...rest } = props;
  const labelled = typeof rest["aria-label"] === "string";
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.7"
      stroke-linecap="round" stroke-linejoin="round" aria-hidden={labelled ? undefined : "true"}
      role={labelled ? "img" : undefined} focusable="false" {...rest}>
      <path d={d} />
    </svg>
  );
}

export const Mic = (p: P) => <Svg d="M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3zM5 11a7 7 0 0 0 14 0M12 18v3" {...p} />;
export const Chat = (p: P) => <Svg d="M4 5h16v11H9l-5 4z" {...p} />;
export const Note = (p: P) => <Svg d="M6 3h9l4 4v14H6zM14 3v5h5M9 13h7M9 17h5" {...p} />;
export const Chevron = (p: P) => <Svg d="M9 6l6 6-6 6" {...p} />;
export const Gear = (p: P) => (
  <Svg d="M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19 12a7 7 0 0 0-.1-1.2l2-1.5-2-3.4-2.3.9a7 7 0 0 0-2-1.2L14.2 3h-4l-.4 2.6a7 7 0 0 0-2 1.2l-2.3-.9-2 3.4 2 1.5A7 7 0 0 0 5 12c0 .4 0 .8.1 1.2l-2 1.5 2 3.4 2.3-.9a7 7 0 0 0 2 1.2l.4 2.6h4l.4-2.6a7 7 0 0 0 2-1.2l2.3.9 2-3.4-2-1.5c.1-.4.1-.8.1-1.2z" {...p} />
);
export const Search = (p: P) => <Svg d="M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM21 21l-5-5" {...p} />;
export const Close = (p: P) => <Svg d="M6 6l12 12M18 6L6 18" {...p} />;
export const Star = (p: P) => <Svg d="M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1-4.4-4.3 6.1-.9z" {...p} />;
export const Reply = (p: P) => <Svg d="M10 8L5 13l5 5M5 13h9a5 5 0 0 0 5-5V6" {...p} />;
export const Plus = (p: P) => <Svg d="M12 5v14M5 12h14" {...p} />;
// the tab bar's four places, and the Settings page's location button
export const Sun = (p: P) => <Svg d="M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM12 2.5v2M12 19.5v2M4.6 4.6l1.4 1.4M18 18l1.4 1.4M2.5 12h2M19.5 12h2M4.6 19.4L6 18M18 6l1.4-1.4" {...p} />;
export const Book = (p: P) => <Svg d="M5 4.5A1.5 1.5 0 0 1 6.5 3H19v15H6.5A1.5 1.5 0 0 0 5 19.5zM5 19.5A1.5 1.5 0 0 0 6.5 21H19M9 7h6" {...p} />;
export const Shelf = (p: P) => <Svg d="M4 4h4v16H4zM10 4h4v16h-4zM15.5 5.2l3.8-1 3.2 15.1-3.8 1z" {...p} />;
export const Spark = (p: P) => <Svg d="M3 17l5-6 4 3 6-8M15 6h3v3" {...p} />;
export const Pin = (p: P) => <Svg d="M12 21s-6.5-5.6-6.5-11A6.5 6.5 0 0 1 18.5 10c0 5.4-6.5 11-6.5 11zM12 7.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5z" {...p} />;
export const Check = (p: P) => <Svg d="M5 12.5l4.5 4.5L19 7.5" {...p} />;
/**
 * The app's mark (public/icons/mark.svg): his page with its margin ruled, three lines of his, and in the margin a postmark
 * ring in her ink (the `mark-ring` class takes `--accent`, so it follows the chosen ink). No date in it. Decorative.
 */
export function Mark({ size = 24, class: cls }: { size?: number; class?: string }) {
  return (
    <svg class={cls} viewBox="0 0 24 24" width={size} height={size} fill="none" aria-hidden="true" focusable="false">
      <rect x="4.5" y="2.75" width="15" height="18.5" rx="1.2" stroke="currentColor" stroke-width="1.5" />
      <path d="M9.25 2.75v18.5" stroke="currentColor" stroke-width="1" />
      <path d="M11.75 8h5.25M11.75 11.5h5.25M11.75 15h3.25" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
      <circle class="mark-ring" cx="6.9" cy="8" r="1.45" stroke-width="1.1" />
    </svg>
  );
}
