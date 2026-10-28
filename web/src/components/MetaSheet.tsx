import { useState } from "preact/hooks";
import { api } from "../api";
import { useAction } from "../hooks";
import { Star } from "../icons";
import type { EntryDoc } from "../types";
import { ErrorLine } from "./Status";

/** What the server allows in one mood or person: up to 60 characters, no commas, brackets or line breaks. */
export function cleanTag(raw: string): string {
  return raw.replace(/[,[\]\r\n\t]+/g, " ").replace(/\s+/g, " ").trim().slice(0, 60);
}

function same(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((x, i) => x === b[i]);
}

function Tags({ label, tags, onChange }: { label: string; tags: string[]; onChange: (t: string[]) => void }) {
  const [draft, setDraft] = useState("");
  const add = () => {
    const t = cleanTag(draft);
    if (t && !tags.includes(t)) onChange([...tags, t]);
    setDraft("");
  };
  return (
    <div class="field">
      <label for={`tag-${label}`}>{label}</label>
      <p class="chips">
        {tags.map((t) => (
          <button type="button" class="chip" key={t} aria-label={`Remove ${t}`} onClick={() => onChange(tags.filter((x) => x !== t))}>
            {t} ×
          </button>
        ))}
      </p>
      <input
        id={`tag-${label}`}
        type="text"
        value={draft}
        placeholder="Add, then press Enter"
        maxLength={60}
        onInput={(e) => setDraft((e.currentTarget as HTMLInputElement).value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === ",") {
            e.preventDefault();
            add();
          }
        }}
        onBlur={add}
      />
    </div>
  );
}

/**
 * Mood, people and a star on an entry: by hand, no model (Decision 6). These are the engine's own front-matter keys;
 * his blocks are not touched. The save is bound to the entry as it was read (`mtime`): if the file moved, the sheet
 * says so and offers a reload instead of overwriting.
 */
export function MetaSheet({ doc, onSaved, onReload }: { doc: EntryDoc; onSaved: () => void; onReload: () => void }) {
  const [mood, setMood] = useState(doc.meta.mood ?? []);
  const [people, setPeople] = useState(doc.meta.people ?? []);
  const [starred, setStarred] = useState(doc.meta.starred ?? false);
  const act = useAction();

  // Only what he changed goes to the server. A list he did not touch is never sent: the server replaces a key
  // whole, so sending "no mood" for a mood the sheet could not read would delete it.
  const changes = {
    ...(same(mood, doc.meta.mood ?? []) ? {} : { mood }),
    ...(same(people, doc.meta.people ?? []) ? {} : { people }),
    ...(starred === (doc.meta.starred ?? false) ? {} : { starred }),
  };
  const changed = Object.keys(changes).length > 0;

  const save = async () => {
    if (!changed) return;
    const done = await act.run(() => api.patchMeta(doc.stem, { ...changes, mtime: doc.mtime }));
    if (done) onSaved();
  };

  return (
    <form
      class="meta-form"
      onSubmit={(e) => {
        e.preventDefault();
        void save();
      }}
    >
      <label class="check">
        <input type="checkbox" checked={starred} onChange={(e) => setStarred((e.currentTarget as HTMLInputElement).checked)} />
        <Star width={16} height={16} /> Starred
      </label>
      <Tags label="Mood" tags={mood} onChange={setMood} />
      <Tags label="People" tags={people} onChange={setPeople} />
      {act.error &&
        (act.error.status === 409 && act.error.slug === "mtime" ? (
          <div class="error-line" role="alert">
            <p>{act.error.detail}</p>
            <button type="button" class="button" onClick={onReload}>
              Reload the entry
            </button>
          </div>
        ) : (
          <ErrorLine error={act.error} />
        ))}
      <button type="submit" class="button primary" disabled={act.busy || !changed}>
        {act.busy ? "Saving…" : "Save"}
      </button>
    </form>
  );
}
