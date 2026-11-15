import {
  addQuestion, addSection, flatten, moveQuestion, moveSection, removeQuestion, removeSection, setQuestion,
  setSectionTitle, type Form, type ProblemMap,
} from "../editorModel";
import { ChevronDown, ChevronUp } from "../icons";
import { IconButton, TextButton } from "../ui";
import "./QuestionSet.css";

/**
 * The questions of a practice as a form: sections (or one flat list), each question a line of text that can move up
 * or down, be added or removed. Moving is by buttons rather than dragging (a drag does not work with a finger and
 * is hard with a keyboard); a question that moves keeps its id by pinning it. Edits are `onChange(form)`; the server
 * decides what is valid. His words, so each question is set in the book's roman on a ruled line.
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
                class="input his section-title"
                value={s.title ?? ""}
                placeholder="Section title"
                onInput={(e) => onChange(setSectionTitle(form, si, (e.currentTarget as HTMLInputElement).value))}
              />
              <IconButton size="small" label={`Move section ${si + 1} up`} disabled={si === 0} onClick={() => onChange(moveSection(form, si, -1))}>
                <ChevronUp />
              </IconButton>
              <IconButton size="small" label={`Move section ${si + 1} down`} disabled={si === last} onClick={() => onChange(moveSection(form, si, 1))}>
                <ChevronDown />
              </IconButton>
              <TextButton onClick={() => onChange(removeSection(form, si))}>Remove section</TextButton>
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
                  <span class="q-n" aria-hidden="true">{qi + 1}</span>
                  <textarea
                    id={`q-${x.key}`}
                    class="input area his"
                    rows={2}
                    value={x.text}
                    placeholder="A question, in your words"
                    onInput={(e) => onChange(setQuestion(form, si, qi, (e.currentTarget as HTMLTextAreaElement).value))}
                  />
                  <div class="q-tools">
                    <IconButton size="small" label={`Move question ${qi + 1} up`} disabled={si === 0 && qi === 0} onClick={() => onChange(moveQuestion(form, si, qi, -1))}>
                      <ChevronUp />
                    </IconButton>
                    <IconButton size="small" label={`Move question ${qi + 1} down`} disabled={si === last && qi === s.questions.length - 1} onClick={() => onChange(moveQuestion(form, si, qi, 1))}>
                      <ChevronDown />
                    </IconButton>
                    <TextButton aria-label={`Remove question ${qi + 1}`} onClick={() => onChange(removeQuestion(form, si, qi))}>
                      Remove
                    </TextButton>
                  </div>
                </div>
                {x.pinned && <p class="hint q-pinned">Keeps its id {x.id}, so older entries still match it.</p>}
                {(problems.question[`${si + 1},${qi + 1}`] ?? []).map((p) => (
                  <p class="field-error" role="alert" key={p}>{p}</p>
                ))}
              </li>
            ))}
          </ol>
          <TextButton class="q-add" onClick={() => onChange(addQuestion(form, si))}>
            + Add a question
          </TextButton>
        </fieldset>
      ))}
      <div class="q-structure">
        <TextButton onClick={() => onChange(addSection(form))}>+ Add a section</TextButton>
        {form.sections.length === 1 && !flat && (
          <TextButton onClick={() => onChange(flatten(form))}>Make it a plain list</TextButton>
        )}
      </div>
    </div>
  );
}
