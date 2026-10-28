import {
  addQuestion, addSection, flatten, moveQuestion, moveSection, removeQuestion, removeSection, setQuestion,
  setSectionTitle, type Form, type ProblemMap,
} from "../editorModel";

const Up = () => (
  <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">
    <path d="M6 15l6-6 6 6" />
  </svg>
);
const Down = () => (
  <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">
    <path d="M6 9l6 6 6-6" />
  </svg>
);

/**
 * The questions of a practice as a form: sections (or one flat list), each question a line of text that can move up
 * or down, be added or removed. Moving is by buttons rather than dragging (a drag does not work with a finger and
 * is hard with a keyboard); a question that moves keeps its id by pinning it. Edits are `onChange(form)`; the server
 * decides what is valid.
 */
export function QuestionSet({ form, problems, onChange }: { form: Form; problems: ProblemMap; onChange: (f: Form) => void }) {
  const flat = form.sections.length === 1 && form.sections[0]?.title === null;
  const last = form.sections.length - 1;
  return (
    <div class="question-editor">
      {form.sections.map((s, si) => (
        <fieldset key={s.key} class="q-section">
          {!flat && (
            <div class="q-section-head">
              <label class="visually-hidden" for={`sec-${s.key}`}>
                Section {si + 1} title
              </label>
              <input
                id={`sec-${s.key}`}
                type="text"
                class="section-title"
                value={s.title ?? ""}
                placeholder="Section title"
                onInput={(e) => onChange(setSectionTitle(form, si, (e.currentTarget as HTMLInputElement).value))}
              />
              <button type="button" class="icon-button" aria-label={`Move section ${si + 1} up`} disabled={si === 0} onClick={() => onChange(moveSection(form, si, -1))}>
                <Up />
              </button>
              <button type="button" class="icon-button" aria-label={`Move section ${si + 1} down`} disabled={si === last} onClick={() => onChange(moveSection(form, si, 1))}>
                <Down />
              </button>
              <button type="button" class="link" onClick={() => onChange(removeSection(form, si))}>
                Remove section
              </button>
            </div>
          )}
          {(problems.section[si + 1] ?? []).map((p) => (
            <p class="field-error" role="alert" key={p}>{p}</p>
          ))}
          <ol class="q-list">
            {s.questions.map((x, qi) => (
              <li key={x.key}>
                <div class="q-row">
                  <label class="visually-hidden" for={`q-${x.key}`}>
                    Question {qi + 1}{flat ? "" : ` of section ${si + 1}`}
                  </label>
                  <textarea
                    id={`q-${x.key}`}
                    rows={2}
                    value={x.text}
                    placeholder="A question, in your words"
                    onInput={(e) => onChange(setQuestion(form, si, qi, (e.currentTarget as HTMLTextAreaElement).value))}
                  />
                  <div class="q-tools">
                    <button type="button" class="icon-button" aria-label={`Move question ${qi + 1} up`} disabled={si === 0 && qi === 0} onClick={() => onChange(moveQuestion(form, si, qi, -1))}>
                      <Up />
                    </button>
                    <button type="button" class="icon-button" aria-label={`Move question ${qi + 1} down`} disabled={si === last && qi === s.questions.length - 1} onClick={() => onChange(moveQuestion(form, si, qi, 1))}>
                      <Down />
                    </button>
                    <button type="button" class="link" aria-label={`Remove question ${qi + 1}`} onClick={() => onChange(removeQuestion(form, si, qi))}>
                      Remove
                    </button>
                  </div>
                </div>
                {x.pinned && <p class="hint">Keeps its id {x.id}, so older entries still match it.</p>}
                {(problems.question[`${si + 1},${qi + 1}`] ?? []).map((p) => (
                  <p class="field-error" role="alert" key={p}>{p}</p>
                ))}
              </li>
            ))}
          </ol>
          <button type="button" class="link" onClick={() => onChange(addQuestion(form, si))}>
            + Add a question
          </button>
        </fieldset>
      ))}
      <div class="q-structure">
        <button type="button" class="link" onClick={() => onChange(addSection(form))}>
          + Add a section
        </button>
        {form.sections.length === 1 && !flat && (
          <button type="button" class="link" onClick={() => onChange(flatten(form))}>
            Make it a plain list
          </button>
        )}
      </div>
    </div>
  );
}
