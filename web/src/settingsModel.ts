/**
 * The settings the web app may write (the server's allow-list is `config.WEB_WRITABLE`; anything else is refused
 * there, and not offered here). A field is a path into the settings tree, how to edit it, and the range the server
 * checks, so a wrong value is caught where it is typed and the server remains the judge.
 */
import type { Json } from "./schema";

export type FieldKind = "text" | "number" | "whole" | "flag" | "clock";

/** The groups the Settings page shows the fields in, in order. */
export const GROUPS = [
  { id: "place", title: "Place and time", note: "Prompts are sent by the local time here." },
  { id: "prompts", title: "Prompts", note: "How long a prompt stays open, and when the companion reads what you wrote." },
  { id: "reflections", title: "Reflections", note: "The day's reflection and the week's review." },
  { id: "chat", title: "In the chat", note: "The short note about today the companion gets when a conversation starts." },
  { id: "programs", title: "Programs", note: "" },
] as const;
export type GroupId = (typeof GROUPS)[number]["id"];

export interface SettingField {
  path: string[];
  label: string;
  group: GroupId;
  kind: FieldKind;
  hint?: string;
  min?: number;
  /** Strictly more than this (the server refuses 0 minutes for the quiet and cache windows: `_number`'s minimum is exclusive). */
  above?: number;
  max?: number;
}

export const FIELDS: SettingField[] = [
  { path: ["location"], label: "Place", group: "place", kind: "text", hint: "Looked up for its coordinates and time zone." },
  { path: ["timezone"], label: "Time zone", group: "place", kind: "text", hint: "An IANA name such as Asia/Kolkata; overrides the place's." },
  { path: ["windows", "collapse_hours"], label: "Hours a prompt stays open", group: "prompts", kind: "number", min: 0.25, max: 72 },
  { path: ["windows", "grace_hours"], label: "Hours of grace for a send", group: "prompts", kind: "number", min: 0.25, max: 24 },
  { path: ["windows", "opener_budget_seconds"], label: "Seconds an opener may take", group: "prompts", kind: "whole", min: 1, max: 900 },
  { path: ["windows", "annotate_after_minutes"], label: "Minutes before annotating", group: "prompts", kind: "whole", min: 0, max: 10080 },
  { path: ["reflections", "day", "enabled"], label: "Day reflection", group: "reflections", kind: "flag" },
  { path: ["reflections", "day", "fallback_time"], label: "Day reflection, latest time", group: "reflections", kind: "clock" },
  { path: ["reflections", "week", "enabled"], label: "Week review", group: "reflections", kind: "flag" },
  { path: ["reflections", "week", "fallback_time"], label: "Week review, latest time", group: "reflections", kind: "clock" },
  { path: ["conversations", "quiet_minutes"], label: "Minutes of quiet before the today block", group: "chat", kind: "number", above: 0, max: 1440 },
  { path: ["programs", "settle_minutes"], label: "Minutes of quiet before a phase settles", group: "programs", kind: "number", above: 0, max: 1440 },
  { path: ["today", "enabled"], label: "Today block in the chat", group: "chat", kind: "flag" },
  { path: ["today", "max_chars"], label: "Today block, characters", group: "chat", kind: "whole", min: 200, max: 20000 },
  { path: ["today", "cache_minutes"], label: "Today block, minutes cached", group: "chat", kind: "number", above: 0, max: 1440 },
];

export const keyOf = (f: SettingField): string => f.path.join(".");

/** The value at a field's path in the settings tree, or undefined when it is not set there. */
export function valueAt(settings: Record<string, Json>, path: string[]): Json | undefined {
  let node: Json | undefined = settings;
  for (const part of path) {
    if (node === null || typeof node !== "object" || Array.isArray(node)) return undefined;
    node = (node as { [k: string]: Json })[part];
  }
  return node;
}

/** What the box shows for a stored value. A flag is "on", "off" or "" (not set: the default applies, which the
 *  settings tree does not say, so the box says "Default" rather than guessing). */
export function shown(f: SettingField, v: Json | undefined): string {
  if (f.kind === "flag") return v === true ? "on" : v === false ? "off" : "";
  return v === undefined || v === null ? "" : String(v);
}

/** Why a typed value cannot be saved, or null. (The server checks again.) */
export function problem(f: SettingField, v: string): string | null {
  if (f.kind === "flag" || v === "") return null;
  const text = v.trim();
  if (f.kind === "text") return text.length > 200 ? "At most 200 characters." : null;
  if (f.kind === "clock") return /^([01][0-9]|2[0-3]):[0-5][0-9]$/.test(text) ? null : "A time like 21:30.";
  const n = Number(text);
  if (!Number.isFinite(n)) return "A number.";
  if (f.kind === "whole" && !Number.isInteger(n)) return "A whole number.";
  if (f.above !== undefined && n <= f.above) return `More than ${f.above}.`;
  if (f.min !== undefined && n < f.min) return `At least ${f.min}.`;
  if (f.max !== undefined && n > f.max) return `At most ${f.max}.`;
  return null;
}

function parsed(f: SettingField, v: string): Json {
  if (f.kind === "flag") return v === "on";
  const text = v.trim();
  return f.kind === "number" || f.kind === "whole" ? Number(text) : text;
}

/**
 * The change as the server takes it: nested objects holding only the keys that differ from what was read (an empty
 * box is "leave it", not "clear it": the web app has no way to unset). `location` is sent alone unless the time zone
 * was changed too.
 */
export function buildPatch(
  settings: Record<string, Json>,
  edited: Record<string, string>,
): Record<string, Json> {
  const patch: Record<string, Json> = {};
  for (const f of FIELDS) {
    const key = keyOf(f);
    if (!(key in edited)) continue;
    const now = edited[key] as string;
    if (now === shown(f, valueAt(settings, f.path)) || now === "") continue;
    let node = patch;
    for (const part of f.path.slice(0, -1)) {
      const next = node[part];
      node = (typeof next === "object" && next !== null && !Array.isArray(next) ? next : (node[part] = {})) as Record<string, Json>;
    }
    node[f.path[f.path.length - 1] as string] = parsed(f, now);
  }
  return patch;
}
