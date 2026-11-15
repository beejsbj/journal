/**
 * The icon set: one stroke family on a 24 grid (stroke 1.6, round caps and joins, `currentColor`), drawn for the
 * round-02 system (design/DESIGN.md, "Icons"). Decorative unless given an `aria-label` (then `role="img"`); a control
 * that is only an icon carries its own label (IconButton). Default size 18; the rail and the tab bar draw them at 20-21.
 * A few glyphs fill a shape (play, pause, stop, the dots of More): those set their own fill.
 */
import type { ComponentChildren, JSX } from "preact";

type P = JSX.SVGAttributes<SVGSVGElement>;

function Svg(props: P & { children: ComponentChildren }) {
  const { children, ...rest } = props;
  const labelled = typeof rest["aria-label"] === "string";
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.6"
      stroke-linecap="round" stroke-linejoin="round" aria-hidden={labelled ? undefined : "true"}
      role={labelled ? "img" : undefined} focusable="false" {...rest}>
      {children}
    </svg>
  );
}
const dot = (cx: number, cy: number, r = 1.3) => <circle cx={cx} cy={cy} r={r} fill="currentColor" stroke="none" />;

// ---- places ----
export const Today = (p: P) => <Svg {...p}><rect x="4" y="5" width="16" height="15" rx="2.5" /><path d="M4 9.5h16M8.5 3v4M15.5 3v4" />{dot(12, 14.5)}</Svg>;
export const Entries = (p: P) => <Svg {...p}><path d="M7 3.5h8l4 4v13H7z" /><path d="M15 3.5v4h4M10 12h6M10 15.5h6" /><path d="M4.5 7v15.5H15" opacity=".55" /></Svg>;
export const Library = (p: P) => <Svg {...p}><path d="M5 4.5h3.5v15H5zM10.5 4.5H14v15h-3.5zM15.6 5.3l3.3-.9 3.6 14.4-3.3.9z" /></Svg>;
export const Insights = (p: P) => <Svg {...p}><path d="M12 6.5C10 5 7 4.5 3.5 5v13c3.5-.5 6.5 0 8.5 1.5 2-1.5 5-2 8.5-1.5V5C17 4.5 14 5 12 6.5zM12 6.5v13" /></Svg>;
export const Settings = (p: P) => <Svg {...p}><path d="M4 7h9M17 7h3M4 17h3M11 17h9" /><circle cx="15" cy="7" r="2.2" /><circle cx="9" cy="17" r="2.2" /></Svg>;
export const Pen = (p: P) => <Svg {...p}><path d="M4.5 19.5l1.2-4.6L15.6 5a2.2 2.2 0 013.1 3.1L8.8 18z" /><path d="M13.8 6.8l3.1 3.1" /></Svg>;

// ---- direction ----
/** Points right; a disclosure turns it (legacy) or uses ChevronDown. */
export const Chevron = (p: P) => <Svg {...p}><path d="M9.5 5.5L16 12l-6.5 6.5" /></Svg>;
export const ChevronDown = (p: P) => <Svg {...p}><path d="M6.5 9.5l5.5 5.5 5.5-5.5" /></Svg>;
export const ChevronUp = (p: P) => <Svg {...p}><path d="M6.5 14.5L12 9l5.5 5.5" /></Svg>;
export const ChevronLeft = (p: P) => <Svg {...p}><path d="M14.5 5.5L8 12l6.5 6.5" /></Svg>;
export const ChevronRight = Chevron;
export const Back = ChevronLeft;

// ---- actions ----
export const Plus = (p: P) => <Svg {...p}><path d="M12 5.5v13M5.5 12h13" /></Svg>;
export const Minus = (p: P) => <Svg {...p}><path d="M5.5 12h13" /></Svg>;
export const Close = (p: P) => <Svg {...p}><path d="M6.5 6.5l11 11M17.5 6.5l-11 11" /></Svg>;
export const Check = (p: P) => <Svg {...p}><path d="M5 12.5l4.5 4.5L19 7.5" /></Svg>;
export const Search = (p: P) => <Svg {...p}><circle cx="11" cy="11" r="6" /><path d="M20 20l-4.5-4.5" /></Svg>;
export const Trash = (p: P) => <Svg {...p}><path d="M5 7h14M10 7V4.5h4V7M7 7l1 13h8l1-13" /></Svg>;
export const Copy = (p: P) => <Svg {...p}><rect x="8.5" y="8.5" width="11" height="12" rx="2" /><path d="M5 15.5V6a1.5 1.5 0 011.5-1.5H15" /></Svg>;
export const More = (p: P) => <Svg {...p}>{dot(6, 12)}{dot(12, 12)}{dot(18, 12)}</Svg>;
export const Reply = (p: P) => <Svg {...p}><path d="M9 7.5L4.5 12 9 16.5M4.5 12h9a6 6 0 016 6" /></Svg>;
export const Send = (p: P) => <Svg {...p}><path d="M12 19V6M6.5 11.5L12 6l5.5 5.5" /></Svg>;
export const Star = (p: P) => <Svg {...p}><path d="M12 4.2l2.4 4.9 5.4.8-3.9 3.8.9 5.4L12 16.6l-4.8 2.5.9-5.4-3.9-3.8 5.4-.8z" /></Svg>;
export const Focus = (p: P) => <Svg {...p}><path d="M4.5 9V5.5a1 1 0 011-1H9M15 4.5h3.5a1 1 0 011 1V9M19.5 15v3.5a1 1 0 01-1 1H15M9 19.5H5.5a1 1 0 01-1-1V15" /><circle cx="12" cy="12" r="2.4" /></Svg>;
export const Expand = (p: P) => <Svg {...p}><path d="M4 6.5h16M4 12h16M4 17.5h10" /></Svg>;

// ---- media and voice ----
export const Mic = (p: P) => <Svg {...p}><rect x="9" y="3.5" width="6" height="11" rx="3" /><path d="M5.5 11.5a6.5 6.5 0 0013 0M12 18v3" /></Svg>;
export const Play = (p: P) => <Svg {...p}><path d="M8.5 5.8v12.4L18.5 12z" fill="currentColor" stroke="none" /></Svg>;
export const Pause = (p: P) => <Svg {...p}><path d="M8 5.5h2.8v13H8zM13.2 5.5H16v13h-2.8z" fill="currentColor" stroke="none" /></Svg>;
export const Stop = (p: P) => <Svg {...p}><rect x="7" y="7" width="10" height="10" rx="2" fill="currentColor" stroke="none" /></Svg>;
export const Wave = (p: P) => <Svg {...p}><path d="M4 12h1M8 8.5v7M12 5.5v13M16 9v6M20 12h-1" /></Svg>;

// ---- things ----
export const Chat = (p: P) => <Svg {...p}><path d="M5 5.5h14v9.5H10l-5 4z" /></Svg>;
/** Her notes on an entry (annotated). */
export const Note = (p: P) => <Svg {...p}><path d="M6.5 3.5h8l3.5 3.5v13.5h-11.5z" /><path d="M14.5 3.5V7H18M9.5 12h5.5M9.5 15.5h4" /></Svg>;
export const Person = (p: P) => <Svg {...p}><circle cx="12" cy="8.5" r="3.5" /><path d="M5 20c1-3.5 4-5 7-5s6 1.5 7 5" /></Svg>;
export const Lock = (p: P) => <Svg {...p}><rect x="5" y="11" width="14" height="9" rx="2" /><path d="M8 11V8a4 4 0 018 0v3" /></Svg>;
export const Pin = (p: P) => <Svg {...p}><path d="M12 21s-6.5-6-6.5-11a6.5 6.5 0 0113 0c0 5-6.5 11-6.5 11z" /><circle cx="12" cy="10" r="2.3" /></Svg>;
export const Sun = (p: P) => <Svg {...p}><circle cx="12" cy="12" r="4" /><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4" /></Svg>;
export const Moon = (p: P) => <Svg {...p}><path d="M19 14.5A7.5 7.5 0 019.5 5a7.5 7.5 0 109.5 9.5z" /></Svg>;
export const Offline = (p: P) => <Svg {...p}><path d="M3.5 9a13 13 0 0117 0M6.5 12.5a8.5 8.5 0 0111 0M9.5 16a4 4 0 015 0" opacity=".5" /><path d="M4 4l16 16" />{dot(12, 19.2, 1.2)}</Svg>;

// ---- the entry sidebar's three modes ----
export const SideList = (p: P) => <Svg {...p}><rect x="3.5" y="4.5" width="17" height="15" rx="2.5" /><path d="M10 4.5v15M5.8 8.5h2M5.8 11.5h2M5.8 14.5h2" /></Svg>;
export const SideDots = (p: P) => <Svg {...p}><rect x="3.5" y="4.5" width="17" height="15" rx="2.5" /><path d="M8 4.5v15" />{dot(5.8, 8.5, 0.9)}{dot(5.8, 12, 0.9)}{dot(5.8, 15.5, 0.9)}</Svg>;
export const SideNone = (p: P) => <Svg {...p}><rect x="3.5" y="4.5" width="17" height="15" rx="2.5" /><path d="M8 4.5v15" stroke-dasharray="1.4 2.4" opacity=".5" /></Svg>;

// ---- the old names, kept so routes not yet converted still draw (Sun stays the sun: the theme toggle uses it) ----
/** @deprecated the old Entries tab icon; use Entries. */
export const Book = Entries;
/** @deprecated the old Library tab icon; use Library. */
export const Shelf = Library;
/** @deprecated the old Insights tab icon; use Insights. */
export const Spark = Insights;
/** @deprecated the old Settings icon; use Settings. */
export const Gear = Settings;

/** Her reading arriving: an envelope whose flap opens when its disclosure does (`.flap` turns; Disclosure.css). */
export function Envelope(p: P) {
  return (
    <svg viewBox="0 0 34 26" width="34" height="26" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"
      aria-hidden="true" focusable="false" {...p}>
      <rect x="2" y="5" width="30" height="19" rx="2" />
      <path class="flap" d="M2.5 6L17 15.5 31.5 6" />
    </svg>
  );
}

/** An ink blot in her ink (the ink picker): the colour comes from `color`, so a swatch with its own data-ink draws its own. */
export function InkBlot(p: P) {
  return (
    <svg viewBox="0 0 60 52" width="54" height="48" fill="currentColor" aria-hidden="true" focusable="false" {...p}>
      <path d="M31 3c8 .5 15 4 19.5 9.5 4.6 5.6 7.2 13 4.6 19.8-2.3 6-8.3 9.4-13.8 12.6-5.4 3.1-11.7 5.4-17.6 3.6C17.3 46.6 11 43 7 37.6 3 32.2 1.8 24.6 4.6 18.4 7.2 12.6 13 8.6 18.6 5.8 22.5 3.8 26.7 2.8 31 3zm19 39.5c1.6 0 2.8 1.3 2.8 2.8S51.6 48 50 48s-2.8-1.2-2.8-2.7 1.2-2.8 2.8-2.8zM9.5 6.5c1.2 0 2.2 1 2.2 2.2s-1 2.1-2.2 2.1-2.1-1-2.1-2.1 1-2.2 2.1-2.2z" />
    </svg>
  );
}

/**
 * The mark: the "margin j" (design/DESIGN.md, "The mark"). The notebook's margin rule bends into his j, three faint
 * ruled lines beside it, and her ring is its dot, in her ink (`.mark-ring` takes `--her`, so it follows the chosen
 * ink). Decorative. The app icon (`public/icons/mark.svg`) is the same drawing; the tab keeps its own small j
 * (`favicon.svg`). The shots board rewrites this markup's `width`, `height`, `class="mark-ring"` and `currentColor`:
 * keep those attributes as they are.
 */
export function Mark({ size = 30, class: cls }: { size?: number; class?: string }) {
  return (
    <svg class={cls} viewBox="0 0 32 32" width={size} height={size} fill="none" aria-hidden="true" focusable="false">
      <rect x="5.5" y="3.5" width="21" height="25" rx="3" stroke="currentColor" stroke-width="1.6" />
      <path d="M14 12.5v8.2a3.6 3.6 0 0 1-3.6 3.6H9" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" />
      <path d="M17.5 12.5h5.5M17.5 16.5h5.5M17.5 20.5h3.5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" opacity=".45" />
      <circle class="mark-ring" cx="14" cy="8" r="2.3" stroke-width="1.7" />
    </svg>
  );
}
