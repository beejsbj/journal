import type { LibraryItem } from "./types";

/** What an entry of `kind` is called: the library's title when it knows the id, "Note" for a /j note. */
export function kindLabel(kind: string, library: LibraryItem[]): string {
  if (kind === "j" || kind === "note") return "Note";
  const hit = library.find((p) => p.id === kind);
  if (hit) return hit.title;
  const session = /^(.*)-s(\d{2})$/.exec(kind);
  if (session) {
    const base = library.find((p) => p.id === session[1]);
    return `${base?.title ?? session[1]} · Session ${Number(session[2])}`;
  }
  const again = /^(.*)-(\d+)$/.exec(kind);   // the second /j note or evening slot of a day: "j-2", "evening-2"
  if (again) {
    const base = kindLabel(again[1]!, library);
    if (base !== again[1]) return base;
  }
  return kind;
}

/** The kind an entry stem names: "2026-10-14-evening" is "evening". */
export function kindOfStem(stem: string): string {
  return stem.slice(11);
}

/** Library groups in the order the app shows them; any other group a definition names follows, alphabetically. */
export const GROUP_ORDER = ["Situational", "Daily", "Frameworks"];

export function groupOf(item: Pick<LibraryItem, "group" | "kind" | "schedule">): string {
  if (item.group) return item.group.charAt(0).toUpperCase() + item.group.slice(1); // files say "frameworks"
  if (item.kind === "program") return "Frameworks";
  return item.schedule.length > 0 ? "Daily" : "Situational";
}

export function sourceLabel(source: LibraryItem["source"]): string {
  return source === "builtin" ? "built-in" : source === "user" ? "yours" : "edited";
}

/** A definition made of sessions and phases: a program, or a situational one (a one-session program the server lists
 *  with `kind: "situational"`). Practices have a schedule instead. */
export function isProgramBacked(item: { kind: string; sessions?: number | undefined }): boolean {
  return item.kind === "program" || (item.kind === "situational" && item.sessions !== undefined);
}
