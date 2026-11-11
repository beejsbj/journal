import { useLocation } from "preact-iso";
import { useEffect, useMemo, useState } from "preact/hooks";
import { api, type ApiError, type DefinitionBody } from "../api";
import { QuestionSet } from "../components/QuestionSet";
import { Check } from "../icons";
import {
  ID_RULE, addSlot, blankForm, definitionFromForm, formFromDefinition, freeId, removeSlot, setSlot, sortProblems,
  type Form,
} from "../editorModel";
import { asApiError, useAction, useResource } from "../hooks";
import { isProgramBacked } from "../labels";
import { useTitle } from "../title";
import type { LibraryOne } from "../types";
import { Await, Button, ButtonLink, Disclosure, Empty, ErrorLine, Field, PageBack, Switch, TextButton, TextField } from "../ui";
import "./library.css";
import "./editor.css";

const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

interface Checked {
  raw: string | null;
  problems: string[];
  valid: boolean;
}

/** What the server would write for this body, and what is wrong with it: asked a moment after the last keystroke.
 *  Nothing is written by it (`?validate=1`). */
function useValidation(id: string, body: DefinitionBody | null): [Checked | null, ApiError | null, boolean] {
  const [checked, setChecked] = useState<Checked | null>(null);
  const [checkedKey, setCheckedKey] = useState("");
  const [failed, setFailed] = useState<ApiError | null>(null);
  const key = body ? `${id}\n${JSON.stringify(body)}` : "";
  useEffect(() => {
    if (!body) {
      setChecked(null);
      return;
    }
    let live = true;
    const timer = setTimeout(() => {
      api.validateLibrary(id, body).then(
        (r) => {
          if (!live) return;
          setChecked(r);
          setCheckedKey(key);
          setFailed(null);
        },
        (e: unknown) => live && setFailed(asApiError(e)),
      );
    }, 400);
    return () => {
      live = false;
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  // `current`: the answer is for this very body, not for the one a moment ago
  return [checked, failed, key !== "" && checkedKey === key];
}

type Tab = "form" | "raw";

/** The mtime a save claims to have read: his file's. A built-in has no file of his yet (the first save makes it), so
 *  there is none to claim. */
function mtimeOf(one: LibraryOne | null): number | null {
  return one && one.definition.source !== "builtin" ? one.mtime : null;
}

/** Changed elsewhere since it was read: reload, or save over it (the person has seen the warning and chose this). */
function Conflict({ busy, onOverwrite }: { busy: boolean; onOverwrite: () => void }) {
  return (
    <div class="error-line" role="alert">
      <p>Changed elsewhere (Obsidian?) since you opened it. Reload to see what is there, or overwrite it with your version.</p>
      <Button size="small" onClick={() => window.location.reload()}>Reload</Button>
      <Button size="small" variant="danger" disabled={busy} onClick={onOverwrite}>Overwrite</Button>
    </div>
  );
}

/**
 * Edit a practice, or make one. The form is the structured view; "Raw Markdown" is the file itself, which the server
 * renders canonically from the form (and which can be edited as text). Saving asks the server, which parses what is
 * sent and writes only what parses: problems come back by field. A file changed elsewhere since it was read (Obsidian)
 * is a 409, never overwritten unless asked.
 */
export function Editor({ base, taken }: { base: LibraryOne | null; taken: string[] }) {
  const { route } = useLocation();
  const fresh = base === null;
  const [form, setForm] = useState<Form>(() => (base ? formFromDefinition(base.definition) : blankForm()));
  const [idTouched, setIdTouched] = useState(false);
  const [tab, setTab] = useState<Tab>("form");
  const [text, setText] = useState<string | null>(null);   // the raw file as edited; null while the form is the source
  const [leaving, setLeaving] = useState(false);
  const save = useAction();
  const [serverProblems, setServerProblems] = useState<string[]>([]);

  const id = fresh ? (idTouched ? form.id : freeId(form.title, taken)) : (base?.definition.id ?? "");
  const idOk = ID_RULE.test(id);
  const setFormChecked = (f: Form) => {
    setForm(f);
    setServerProblems([]);
    save.clear();
  };

  const body: DefinitionBody | null = useMemo(() => {
    if (!idOk) return null;
    return text !== null ? { raw: text } : { definition: definitionFromForm({ ...form, id }, base?.definition ?? null) };
  }, [form, text, id, idOk, base]);
  const [checked, validateFailed, current] = useValidation(id, body);

  const problems = sortProblems([...(checked?.problems ?? []), ...serverProblems]);
  // The file the form makes, only while the server has said so for this very form. Otherwise (a form that does not
  // parse, or a check still on its way) the Raw tab shows no file and takes no typing: seeding the text from the
  // file as it was read would drop the form's edits.
  const canonical = current ? (checked?.raw ?? null) : null;

  const write = async (mtime: number | null) => {
    if (!body) return;
    const sent: DefinitionBody = { ...body, mtime };
    const done = await save.run(() => api.saveLibrary(id, sent));
    if (done) route(`/library/${id}`);
  };
  const submit = () => void write(mtimeOf(base));
  const error = save.error;
  const isConflict = error?.status === 409 && error.slug === "mtime";
  const overwrite = async () => {
    // read what is there now and save over it: the person has seen the warning and chose this
    const now = await save.run(() => api.libraryOne(id));
    if (now) await write(mtimeOf(now));
  };

  useEffect(() => {
    if (error?.status === 422) setServerProblems(error.problems);
  }, [error]);

  const showRaw = () => setTab("raw");
  const showForm = () => {
    if (text !== null) setLeaving(true);
    else setTab("form");
  };

  const input = (key: keyof Form) => (e: Event) => setFormChecked({ ...form, [key]: (e.currentTarget as HTMLInputElement).value });
  return (
    <>
      <div class="editor-tabs" role="tablist" aria-label="Editor">
        <button type="button" role="tab" id="tab-form" aria-selected={tab === "form"} class={tab === "form" ? "on" : undefined} onClick={showForm}>
          Form
        </button>
        <button type="button" role="tab" id="tab-raw" aria-selected={tab === "raw"} class={tab === "raw" ? "on" : undefined} onClick={showRaw}>
          Raw Markdown
        </button>
      </div>
      {leaving && (
        <div class="error-line" role="alert">
          <p>You have edited the file as text. Going back to the form drops those edits; save first to keep them.</p>
          <Button size="small" variant="danger" onClick={() => { setText(null); setLeaving(false); setTab("form"); }}>
            Drop the text edits
          </Button>
          <Button size="small" onClick={() => setLeaving(false)}>
            Keep editing
          </Button>
        </div>
      )}
      {!idOk && fresh && <p class="hint">Give it a title; the file name follows from it.</p>}
      {validateFailed && <ErrorLine error={validateFailed} />}
      {problems.general.length > 0 && (
        <section class="problems error-line" role="alert" aria-label="Problems">
          <p><b>What needs fixing</b></p>
          <ul>
            {problems.general.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        </section>
      )}

      {tab === "form" ? (
        <div class="editor-form">
          <TextField id="f-title" label="Title" voice="his" value={form.title} placeholder="Evening gratitude" onInput={input("title")} />
          <div class="editor-pair">
            <TextField id="f-emoji" class="short" label="Emoji" hint="Empty uses the companion's." maxLength={8} value={form.emoji} onInput={input("emoji")} />
            <div class="editor-switch">
              <span class="field-label" id="f-enabled-label">Send it</span>
              <span class="editor-switch-row">
                <Switch id="f-enabled" labelledBy="f-enabled-label" checked={form.enabled} onChange={(on) => setFormChecked({ ...form, enabled: on })} />
                <span class="field-hint">{form.enabled ? "On its schedule." : "Kept, and not sent."}</span>
              </span>
            </div>
          </div>

          <fieldset class="schedule">
            <legend class="field-label">When</legend>
            {form.schedule.length === 0 && <p class="hint">No time: it is only sent when you ask (Send now), never by itself.</p>}
            {form.schedule.map((s, i) => (
              <div class="slot" key={s.key}>
                <label class="visually-hidden" for={`t-${s.key}`}>Time {i + 1}</label>
                <input id={`t-${s.key}`} class="input" type="time" value={s.time} onInput={(e) => setFormChecked(setSlot(form, i, { time: (e.currentTarget as HTMLInputElement).value }))} />
                <label class="visually-hidden" for={`d-${s.key}`}>Day {i + 1}</label>
                <select id={`d-${s.key}`} class="input" value={s.weekday === null ? "" : String(s.weekday)} onChange={(e) => {
                  const v = (e.currentTarget as HTMLSelectElement).value;
                  setFormChecked(setSlot(form, i, { weekday: v === "" ? null : Number(v) }));
                }}>
                  <option value="">Every day</option>
                  {DAYS.map((d, n) => (
                    <option key={d} value={String(n)}>{d}</option>
                  ))}
                </select>
                <TextButton aria-label={`Remove time ${i + 1}`} onClick={() => setFormChecked(removeSlot(form, i))}>
                  Remove
                </TextButton>
                {(problems.schedule[i + 1] ?? []).map((p) => (
                  <p class="field-error" role="alert" key={p}>{p}</p>
                ))}
              </div>
            ))}
            <TextButton class="editor-add" onClick={() => setFormChecked(addSlot(form))}>
              + Add a time
            </TextButton>
          </fieldset>

          <h2 class="form-h book-head">Questions</h2>
          <QuestionSet form={form} problems={problems} onChange={setFormChecked} />

          <Disclosure class="more" head="More options" peek="How long it stays open, annotation, what it closes, its opener">
            <div class="editor-more">
              <TextField id="f-collapse" class="short" label="Hours a prompt stays open" hint="After this, a reply starts a new entry."
                type="number" step="any" min="0" value={form.collapse} onInput={input("collapse")} />
              <div class="editor-switch">
                <span class="field-label" id="f-annotate-label">Let the companion annotate the entry</span>
                <span class="editor-switch-row">
                  <Switch id="f-annotate" labelledBy="f-annotate-label" checked={form.annotate} onChange={(on) => setFormChecked({ ...form, annotate: on })} />
                </span>
              </div>
              <Field id="f-closes" label="Closes" hint="The practice whose answer ends the day or the week for a reflection.">
                <select id="f-closes" class="input" value={form.closes} aria-describedby="f-closes-hint"
                  onChange={(e) => setFormChecked({ ...form, closes: (e.currentTarget as HTMLSelectElement).value as Form["closes"] })}>
                  <option value="">Neither</option>
                  <option value="day">The day</option>
                  <option value="week">The week</option>
                </select>
              </Field>
              <Field id="f-mode" label="Opener">
                <select id="f-mode" class="input" value={form.openerMode} onChange={(e) => setFormChecked({ ...form, openerMode: (e.currentTarget as HTMLSelectElement).value })}>
                  <option value="companion">Written by the companion</option>
                  <option value="plain">Plain text</option>
                </select>
              </Field>
              <TextField id="f-plain" label="Plain opener" hint="Used when the opener is plain, or when no model is reachable. {title}, {user}, {greet}, {emoji} fill in."
                value={form.openerPlain} onInput={input("openerPlain")} />
              <TextField id="f-companion" label="A line for the companion" hint="One line of stance for this practice; empty lists the title only."
                value={form.companion} onInput={input("companion")} />
              {fresh && (
                <TextField id="f-id" label="File name" hint={idOk ? `Saved as ${id}.md` : "Lower-case letters, digits and hyphens; 2 to 31 characters, starting with a letter."}
                  error={taken.includes(id) ? `A practice named ${id} already exists.` : undefined}
                  value={id} onInput={(e) => { setIdTouched(true); setFormChecked({ ...form, id: (e.currentTarget as HTMLInputElement).value.toLowerCase() }); }} />
              )}
            </div>
          </Disclosure>
        </div>
      ) : (
        <div class="editor-raw">
          <label class="visually-hidden" for="f-raw">The file</label>
          <textarea id="f-raw" class="input raw-text" spellcheck={false} rows={18} readOnly={text === null && canonical === null}
            value={text ?? canonical ?? ""}
            onInput={(e) => { setText((e.currentTarget as HTMLTextAreaElement).value); setServerProblems([]); save.clear(); }} />
          <p class="hint">
            {text !== null
              ? "Editing the text. Saving writes exactly what the server makes of it."
              : canonical !== null
                ? "This is the file the server will write for the form. Type here to edit the text itself; saving it replaces the form."
                : !body
                  ? "Give it a title first: the file has no name yet."
                  : current
                    ? "The form has problems, so there is no file to show yet. Fix the form's problems first (see above)."
                    : "Checking the form…"}
          </p>
        </div>
      )}

      {checked?.valid && !error && <p class="hint ok"><Check aria-hidden="true" />Looks good.</p>}
      {error && !isConflict && error.status !== 422 && <ErrorLine error={error} onRetry={submit} />}
      {error?.status === 422 && <p class="field-error" role="alert">{error.detail}</p>}
      {isConflict && <Conflict busy={save.busy} onOverwrite={() => void overwrite()} />}
      <div class="editor-actions">
        <Button variant="primary" disabled={save.busy || !body || (fresh && taken.includes(id))} onClick={submit}>
          {save.busy ? "Saving…" : fresh ? "Create" : "Save"}
        </Button>
        <ButtonLink variant="quiet" href={fresh ? "/library" : `/library/${id}`} data-vt="pop">
          Cancel
        </ButtonLink>
      </div>
    </>
  );
}

/** One file of a program or situational definition, as the files editor offers it. */
interface DefFile {
  key: string;
  label: string;
  file: string | undefined;   // what a save names (a program's file); undefined: the definition's one file
  raw: string;
  mtime: number | null;
}

export function filesOf(one: LibraryOne): DefFile[] {
  if (one.definition.kind !== "program") return [{ key: "self", label: "The file", file: undefined, raw: one.raw, mtime: one.mtime }];
  return [
    { key: "program.md", label: "The program (program.md)", file: "program.md", raw: one.raw, mtime: one.mtime },
    ...(one.sessions ?? []).flatMap((s) =>
      s.file ? [{ key: s.file, label: `Session ${s.number} · ${s.title}`, file: s.file, raw: s.raw ?? "", mtime: s.mtime ?? null }] : []),
  ];
}

/**
 * Edit a program or situational definition as its files, one at a time (FRONTEND.md §1.5: programs are raw
 * Markdown). The server parses the whole definition the file would make, a moment after the last keystroke, and saves
 * only what loads; a run in progress keeps its sessions, phases and question numbers, so a change to those is refused
 * until it ends (409 `active-run`), while a change of words applies from the next message.
 */
export function FilesEditor({ one }: { one: LibraryOne }) {
  const { route } = useLocation();
  const d = one.definition;
  const files = filesOf(one);
  const [which, setWhich] = useState(files[0]?.key ?? "self");
  const current = files.find((f) => f.key === which) ?? files[0]!;
  const [text, setText] = useState<string | null>(null);   // null: the file as it was read
  const value = text ?? current.raw;
  const dirty = text !== null && text !== current.raw;
  const save = useAction();
  const [serverProblems, setServerProblems] = useState<string[]>([]);
  const named = current.file ? { file: current.file } : {};
  const body: DefinitionBody | null = useMemo(() => (dirty ? { raw: value, ...named } : null), [dirty, value, current.file]);   // eslint-disable-line react-hooks/exhaustive-deps
  const [checked, validateFailed, fresh] = useValidation(d.id, body);
  const problems = [...(fresh ? checked?.problems ?? [] : []), ...serverProblems];
  const write = async (mtime: number | null) => {
    const done = await save.run(() => api.saveLibrary(d.id, { raw: value, ...named, mtime }));
    if (done) route(`/library/${d.id}`);
  };
  const submit = () => void write(d.source === "builtin" ? null : current.mtime);
  const overwrite = async () => {
    const now = await save.run(() => api.libraryOne(d.id));
    if (now) await write(now.definition.source === "builtin" ? null : filesOf(now).find((f) => f.key === current.key)?.mtime ?? null);
  };
  const error = save.error;
  useEffect(() => {
    if (error?.status === 422) setServerProblems(error.problems);
  }, [error]);
  return (
    <>
      <p class="hint files-hint">
        {d.kind === "program"
          ? "Each file is Markdown, as in the program's folder. While a run is in progress its sessions, phases and question numbers stay as they are; a change of words is sent from the next message."
          : "The file is Markdown. While a session of it is in progress its phases and question numbers stay as they are; a change of words is sent from the next message."}
      </p>
      {files.length > 1 && (
        <Field id="f-file" label="File" hint={dirty ? "Save or undo your changes to open another file." : undefined}>
          <select id="f-file" class="input" value={which} disabled={dirty} onChange={(e) => {
            setWhich((e.currentTarget as HTMLSelectElement).value);
            setText(null);
            setServerProblems([]);
            save.clear();
          }}>
            {files.map((f) => (
              <option key={f.key} value={f.key}>{f.label}</option>
            ))}
          </select>
        </Field>
      )}
      <div class="editor-raw">
        <label class="visually-hidden" for="f-raw">{current.label}</label>
        <textarea id="f-raw" class="input raw-text" spellcheck={false} rows={22} value={value}
          onInput={(e) => { setText((e.currentTarget as HTMLTextAreaElement).value); setServerProblems([]); save.clear(); }} />
      </div>
      {validateFailed && <ErrorLine error={validateFailed} />}
      {problems.length > 0 && (
        <section class="problems error-line" role="alert" aria-label="Problems">
          <p><b>What needs fixing</b></p>
          <ul>
            {problems.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        </section>
      )}
      {fresh && checked?.valid && !error && <p class="hint ok"><Check aria-hidden="true" />Looks good.</p>}
      {error?.status === 409 && error.slug === "mtime" ? (
        <Conflict busy={save.busy} onOverwrite={() => void overwrite()} />
      ) : error && error.status !== 422 ? (
        <ErrorLine error={error} onRetry={error.slug === "active-run" ? undefined : submit} />
      ) : error?.status === 422 ? (
        <p class="field-error" role="alert">{error.detail}</p>
      ) : null}
      <div class="editor-actions">
        <Button variant="primary" disabled={save.busy || !dirty} onClick={submit}>
          {save.busy ? "Saving…" : "Save"}
        </Button>
        {dirty && (
          <Button onClick={() => { setText(null); setServerProblems([]); save.clear(); }}>
            Undo changes
          </Button>
        )}
        <ButtonLink variant="quiet" href={`/library/${d.id}`} data-vt="pop">
          Cancel
        </ButtonLink>
      </div>
    </>
  );
}


/** `/library/:id/edit`: any definition, a built-in included: the first save of a built-in makes his version of it, in
 *  its place (same id, same /start word, same times); Reset to default on its page brings the shipped one back. A
 *  practice is a form (and its Markdown); a program or situational definition is its files. */
export function EditorRoute({ id = "" }: { id?: string }) {
  const [res, reload] = useResource((signal) => api.libraryOne(id, signal), [id]);
  useTitle(res.state === "ready" ? `Edit ${res.data.definition.title}` : "Edit");
  return (
    <div class="library-page editor-page">
      <PageBack href={`/library/${id}`}>{res.state === "ready" ? res.data.definition.title : "Library"}</PageBack>
      {res.state === "error" && res.error.status === 404 ? (
        <Empty>No practice or program by that name.</Empty>
      ) : (
        <Await res={res} reload={reload} rows={4}>
          {(one) => {
            const d = one.definition;
            const program = isProgramBacked(d) || one.sessions !== undefined;
            const head = (
              <header class="page-head editor-head">
                <p class="kicker">{d.source === "builtin" ? "Built-in" : d.source === "fork" ? "Edited built-in" : "Yours"}</p>
                <h1 class="display">Edit {d.title}</h1>
                {d.source === "builtin" && (
                  <p class="sub">
                    Saving edits it in place: the same name and {program ? "/start word" : "times"}, no copy. Reset to default brings the shipped one back.
                  </p>
                )}
              </header>
            );
            if (one.override && !one.override.in_force) {
              return (
                <>
                  {head}
                  <p class="editor-note">Your edited file does not load, so the built-in is used meanwhile. Mend the file where it is, or reset it to the default from its page.</p>
                  <p class="actions">
                    <ButtonLink href={`/library/${d.id}`} data-vt="pop">Back to {d.title}</ButtonLink>
                  </p>
                </>
              );
            }
            return (
              <>
                {head}
                {program ? <FilesEditor key={`${one.mtime}`} one={one} /> : <Editor key={`${one.mtime}`} base={one} taken={[]} />}
              </>
            );
          }}
        </Await>
      )}
    </div>
  );
}

/** `/new-practice`: the five-minute path, a title, a time and a question. */
export function NewPracticeRoute() {
  useTitle("New practice");
  const [res, reload] = useResource((signal) => api.library(signal), []);
  return (
    <div class="library-page editor-page">
      <PageBack href="/library">Library</PageBack>
      <header class="page-head editor-head">
        <p class="kicker">Yours</p>
        <h1 class="display">New practice</h1>
        <p class="sub">A title, a time and a question is enough: it is sent from the next tick.</p>
      </header>
      <Await res={res} reload={reload} rows={3}>
        {(items) => <Editor base={null} taken={items.map((i) => i.id)} />}
      </Await>
    </div>
  );
}
