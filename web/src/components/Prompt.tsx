import type { ComponentChildren } from "preact";
import type { Prompt } from "../types";
import { Letter } from "../ui";

type Question = Prompt["questions"][number];

/** The questions in their sections, in the order the prompt carried them (`compose.bank_html`). */
export function sectionsOf(questions: Question[]): { title: string | null; items: Question[] }[] {
  const out: { title: string | null; items: Question[] }[] = [];
  for (const q of questions) {
    const last = out[out.length - 1];
    if (last && last.title === q.section) last.items.push(q);
    else out.push({ title: q.section, items: [q] });
  }
  return out;
}

/**
 * Her message as the design system's letter (ui/Letter): "From Ada · sent 23:00", the opener, the question she asked
 * in italic, and the practice's questions by part. With `fold` (the entry; `FOLD_ENTRY_QUESTIONS`) the questions wait
 * behind "All 24 questions, in 10 parts"; without it (Today: the web never folds a prompt there, FRONTEND §1.1) they
 * are shown in full, in the same sections, with Today's actions on the letter's crease. No fastening
 * of any kind (the owner's call on round 02).
 */
export function PromptLetter({ prompt, hers, fold = false, class: cls, actions }: {
  prompt: Prompt; hers: string; fold?: boolean; class?: string; actions?: ComponentChildren;
}) {
  if (!prompt.opener && !prompt.sent_at_local && prompt.questions.length === 0) return null;
  const askedIds = prompt.asked.map((q) => q.id);
  const asked = prompt.asked.map((q) => q.question);
  const questions = prompt.questions.map((q) => ({ id: q.id, text: q.text, section: q.section }));
  // a single question folds into nothing: it is the asked one (shown large) or, when none was marked, shown as it is
  const lone = questions.length === 1 && asked.length === 0 ? questions[0] : undefined;
  return (
    <Letter from={hers} at={prompt.sent_at_local ?? undefined} opener={prompt.opener ?? undefined} asked={asked}
      askedIds={askedIds} questions={fold ? questions : []} class={cls} label={`${hers}'s prompt`} actions={actions}>
      {lone && <p class="letter-asked">{lone.text}</p>}
      {!fold && questions.length > 1 && (
        <div class="letter-qlist letter-qlist-open">
          {sectionsOf(prompt.questions).map((s, i) => (
            <section key={i} class="letter-qsec">
              {s.title && <h4>{s.title}</h4>}
              <ol>
                {s.items.map((q) => (
                  <li key={q.id} class={askedIds.includes(q.id) ? "asked" : undefined}>
                    <span class="letter-qid">{q.id}</span>
                    <span class="letter-qt">
                      {q.text}
                      {askedIds.includes(q.id) && <em class="letter-asked-tag"> asked</em>}
                    </span>
                  </li>
                ))}
              </ol>
            </section>
          ))}
        </div>
      )}
    </Letter>
  );
}
