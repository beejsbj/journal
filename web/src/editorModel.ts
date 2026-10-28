/**
 * The question-set editor's model: a form over a practice definition, and the pure moves on it. The server owns the
 * file's text (`library.render` writes the canonical form, `?validate=1` shows it), so nothing here renders
 * Markdown: the form becomes the same `definition` JSON `GET /library/{id}` gives, and the server does the rest.
 */
import type { Json } from "./schema";
import type { Definition, Slot } from "./types";

let counter = 0;
const fresh = (): string => `k${++counter}`;

export interface FormQuestion {
  key: string;
  id: string;
  text: string;
  pinned: boolean;
}
export interface FormSection {
  key: string;
  title: string | null;
  questions: FormQuestion[];
}
export interface FormSlot {
  key: string;
  time: string;
  weekday: number | null;
}
export interface Form {
  id: string;
  title: string;
  emoji: string;
  enabled: boolean;
  schedule: FormSlot[];
  collapse: string;   // the number as typed: a wrong one is the server's to name
  annotate: boolean;
  closes: "" | "day" | "week";
  openerMode: string;
  openerPlain: string;
  companion: string;
  sections: FormSection[];
}

const q = (text = "", id = "", pinned = false): FormQuestion => ({ key: fresh(), id, text, pinned });

/** The form for a new practice: a title, a time and one question, which is all a practice needs to be due. */
export function blankForm(): Form {
  return {
    id: "",
    title: "",
    emoji: "",
    enabled: true,
    schedule: [{ key: fresh(), time: "21:00", weekday: null }],
    collapse: "6",
    annotate: true,
    closes: "",
    openerMode: "companion",
    openerPlain: "{title}. A sentence or two is enough.",
    companion: "",
    sections: [{ key: fresh(), title: null, questions: [q("What stood out today?")] }],
  };
}

export function formFromDefinition(d: Definition): Form {
  return {
    id: d.id,
    title: d.title,
    emoji: d.emoji,
    enabled: d.enabled,
    schedule: d.schedule.map((s: Slot) => ({ key: fresh(), time: s.time, weekday: s.weekday })),
    collapse: String(d.collapse_hours ?? 6),
    annotate: d.annotate ?? true,
    closes: d.closes === "day" || d.closes === "week" ? d.closes : "",
    openerMode: d.opener.mode ?? "companion",
    openerPlain: d.opener.plain ?? "",
    companion: d.companion,
    sections: d.sections.map((s) => ({
      key: fresh(),
      title: s.title,
      questions: s.questions.map((x) => q(x.text, x.id, x.pinned)),
    })),
  };
}

/**
 * The `definition` the server takes. Fields the form does not edit (`kind`, `group`, the opener's shape and limit) are
 * carried over from the definition it was read from, so a save never drops them.
 */
export function definitionFromForm(f: Form, base: Definition | null): Record<string, Json> {
  const hours = Number(f.collapse);
  const opener: Record<string, Json> = { mode: f.openerMode, plain: f.openerPlain };
  if (base?.opener.shape !== undefined) opener.shape = base.opener.shape;
  if (base?.opener.limit !== undefined) opener.limit = base.opener.limit;
  return {
    id: f.id,
    kind: base?.kind ?? "practice",
    title: f.title,
    emoji: f.emoji,
    enabled: f.enabled,
    schedule: f.schedule.map((s) => ({ time: s.time, weekday: s.weekday })),
    collapse_hours: f.collapse.trim() !== "" && Number.isFinite(hours) ? hours : f.collapse,
    annotate: f.annotate,
    closes: f.closes === "" ? null : f.closes,
    opener,
    companion: f.companion,
    ...(base?.group !== undefined ? { group: base.group } : {}),
    sections: f.sections.map((s) => ({
      title: s.title,
      questions: s.questions.map((x) => ({ id: x.id, text: x.text, pinned: x.pinned })),
    })),
  };
}

// ── moves ────────────────────────────────────────────────────────────────

const swap = <T,>(list: T[], i: number, j: number): T[] => {
  const out = [...list];
  [out[i], out[j]] = [out[j] as T, out[i] as T];
  return out;
};

/** A question that has an id keeps it when it moves: its `{#id}` is pinned, so history keyed by that id still finds it
 *  (a new question has no id and nothing to keep). */
const keeping = (x: FormQuestion): FormQuestion => (x.id ? { ...x, pinned: true } : x);

/**
 * Move a question one place. At the top or bottom of its section it crosses into the neighbouring section (a flat list
 * has only one, so it stays). The moved question is pinned.
 */
export function moveQuestion(f: Form, si: number, qi: number, dir: -1 | 1): Form {
  const sections = f.sections.map((s) => ({ ...s, questions: [...s.questions] }));
  const from = sections[si];
  const moved = from?.questions[qi];
  if (!from || !moved) return f;
  const to = qi + dir;
  if (to >= 0 && to < from.questions.length) {
    from.questions = swap(from.questions, qi, to);
    from.questions[to] = keeping(moved);
    return { ...f, sections };
  }
  const next = sections[si + dir];
  if (!next) return f;
  from.questions.splice(qi, 1);
  if (dir < 0) next.questions.push(keeping(moved));
  else next.questions.unshift(keeping(moved));
  return { ...f, sections };
}

const keepingAll = (s: FormSection): FormSection => ({ ...s, questions: s.questions.map(keeping) });

/** Move a section one place. The ids are numbered by place (`2.1` is section 2's first question), so both sections
 *  change place and both keep their questions' ids by pinning them: the one pressed and the one it swapped with. */
export function moveSection(f: Form, si: number, dir: -1 | 1): Form {
  const other = si + dir;
  if (!f.sections[si] || !f.sections[other]) return f;
  const sections = swap(f.sections, si, other).map((s, i) => (i === si || i === other ? keepingAll(s) : s));
  return { ...f, sections };
}

export function addQuestion(f: Form, si: number): Form {
  return { ...f, sections: f.sections.map((s, i) => (i === si ? { ...s, questions: [...s.questions, q()] } : s)) };
}

/** Remove a question. The ones after it in its section would be renumbered (`1.3` becomes `1.2`), so they are pinned. */
export function removeQuestion(f: Form, si: number, qi: number): Form {
  const rest = (s: FormSection) => s.questions.flatMap((x, j) => (j < qi ? [x] : j === qi ? [] : [keeping(x)]));
  return { ...f, sections: f.sections.map((s, i) => (i === si ? { ...s, questions: rest(s) } : s)) };
}

export function setQuestion(f: Form, si: number, qi: number, text: string): Form {
  return {
    ...f,
    sections: f.sections.map((s, i) =>
      i === si ? { ...s, questions: s.questions.map((x, j) => (j === qi ? { ...x, text } : x)) } : s,
    ),
  };
}

export function setSectionTitle(f: Form, si: number, title: string): Form {
  return { ...f, sections: f.sections.map((s, i) => (i === si ? { ...s, title } : s)) };
}

/** A flat list becomes a sectioned one (its questions under "Section 1": `1` becomes `1.1`, so they are pinned), and a
 *  sectioned list grows another section after the others, which changes no id. */
export function addSection(f: Form): Form {
  const flat = f.sections.length === 1 && f.sections[0]?.title === null;
  const sections = f.sections.map((s, i) => (flat ? { ...keepingAll(s), title: "Section 1" } : { ...s, title: s.title ?? `Section ${i + 1}` }));
  return { ...f, sections: [...sections, { key: fresh(), title: `Section ${sections.length + 1}`, questions: [q()] }] };
}

/** Remove a section. Those after it move up one place, so their questions are pinned to keep their ids. */
export function removeSection(f: Form, si: number): Form {
  const sections = f.sections.flatMap((s, i) => (i < si ? [s] : i === si ? [] : [keepingAll(s)]));
  return { ...f, sections: sections.length > 0 ? sections : [{ key: fresh(), title: null, questions: [q()] }] };
}

/** A single titled section can go back to being a flat list (`1.1` becomes `1`: the questions are pinned). */
export function flatten(f: Form): Form {
  const only = f.sections.length === 1 ? f.sections[0] : undefined;
  return only ? { ...f, sections: [{ ...keepingAll(only), title: null }] } : f;
}

export function addSlot(f: Form): Form {
  return { ...f, schedule: [...f.schedule, { key: fresh(), time: "21:00", weekday: null }] };
}

export function setSlot(f: Form, i: number, patch: Partial<Omit<FormSlot, "key">>): Form {
  return { ...f, schedule: f.schedule.map((s, j) => (j === i ? { ...s, ...patch } : s)) };
}

export function removeSlot(f: Form, i: number): Form {
  return { ...f, schedule: f.schedule.filter((_, j) => j !== i) };
}

// ── names and problems ───────────────────────────────────────────────────

/** A file name for a title: lower-case words joined by hyphens, starting with a letter, 2 to 31 characters. */
export function slugFor(title: string): string {
  const words = title
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  const lead = words.replace(/^[^a-z]+/, "");
  return lead.slice(0, 31).replace(/-+$/, "");
}

export const ID_RULE = /^[a-z][a-z0-9-]{1,30}$/;

/** A slug that is free: `gratitude`, then `gratitude-2`, `gratitude-3`. Empty when the title gives none. */
export function freeId(title: string, taken: Iterable<string>): string {
  const used = new Set(taken);
  const base = slugFor(title);
  if (!ID_RULE.test(base)) return "";
  if (!used.has(base)) return base;
  for (let n = 2; n < 100; n++) {
    const tail = `-${n}`;
    const id = base.slice(0, 31 - tail.length).replace(/-+$/, "") + tail;
    if (!used.has(id)) return id;
  }
  return "";
}

export interface ProblemMap {
  schedule: Record<number, string[]>;
  question: Record<string, string[]>;   // "section,question" (1-based, as the server counts)
  section: Record<number, string[]>;
  general: string[];
}

/** The server's problem lines, sorted to where they belong in the form ("schedule 2: …", "section 1, question 3: …"). */
export function sortProblems(problems: string[]): ProblemMap {
  const out: ProblemMap = { schedule: {}, question: {}, section: {}, general: [] };
  for (const p of problems) {
    let m = /^schedule (\d+): (.*)$/.exec(p);
    if (m) {
      (out.schedule[Number(m[1])] ??= []).push(m[2] as string);
      continue;
    }
    m = /^section (\d+), question (\d+): (.*)$/.exec(p);
    if (m) {
      (out.question[`${m[1]},${m[2]}`] ??= []).push(m[3] as string);
      continue;
    }
    m = /^section (\d+): (.*)$/.exec(p);
    if (m) {
      (out.section[Number(m[1])] ??= []).push(m[2] as string);
      continue;
    }
    out.general.push(p);
  }
  return out;
}
