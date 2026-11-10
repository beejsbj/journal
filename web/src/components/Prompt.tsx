import { Chevron } from "../icons";
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

function Questions({ prompt }: { prompt: Prompt }) {
  const asked = new Set(prompt.asked.map((q) => q.id));
  const sections = sectionsOf(prompt.questions);
  const flat = sections.length === 1 && sections[0]?.title === null;
  return (
    <div class="question-sections">
      {sections.map((s, i) => {
        const items = s.items.map((q) => (
          <li key={q.id} class={asked.has(q.id) ? "asked" : undefined}>
            {q.text}
            {asked.has(q.id) && <span class="asked-mark"> · asked</span>}
          </li>
        ));
        return (
          <div key={i} class="question-section">
            {s.title && <h4>{s.title}</h4>}
            {flat ? <ol>{items}</ol> : <ul>{items}</ul>}
          </div>
        );
      })}
    </div>
  );
}

/**
 * Her message, as he was sent it: a letter. "From" and her name on a line that runs out as the fold, the time it was
 * sent, the opener, and the question set, all typed in her ink, never in his serif. Today shows the questions in
 * full (`fold` off: the web never collapses a prompt, only Telegram does); the entry's document folds them, since
 * there the page is his words.
 */
export function PromptMessage({ prompt, hers, fold = false }: { prompt: Prompt; hers?: string | undefined; fold?: boolean }) {
  if (!prompt.opener && !prompt.sent_at_local && prompt.questions.length === 0) return null;
  const n = prompt.questions.length;
  return (
    <section class="prompt" aria-label={hers ? `${hers}'s prompt` : "The prompt"}>
      <p class="prompt-meta">
        {hers && (
          <>
            <span class="letter-from">From</span> <span class="who">{hers}</span>
          </>
        )}
        {prompt.sent_at_local && <span class="sent">Sent <time>{prompt.sent_at_local}</time></span>}
      </p>
      {prompt.opener && <p class="opener">{prompt.opener}</p>}
      {n > 0 &&
        (fold ? (
          <details class="questions">
            <summary>
              <Chevron class="chev" /> {n === 1 ? "The question" : `The ${n} questions`}
            </summary>
            <Questions prompt={prompt} />
          </details>
        ) : (
          <div class="questions open">
            <Questions prompt={prompt} />
          </div>
        ))}
    </section>
  );
}

/**
 * Her message as the design system's letter (ui/Letter): "From Ada · sent 23:00", the opener, the question she asked
 * in italic, and the practice's questions by part. With `fold` (the entry; `FOLD_ENTRY_QUESTIONS`) the questions wait
 * behind "All 24 questions, in 10 parts"; without it (Today) they are shown in full, in the same sections. No fastening
 * of any kind (the owner's call on round 02). `PromptMessage` above is the older markup, kept for the pages that still
 * draw it.
 */
export function PromptLetter({ prompt, hers, fold = false, class: cls }: { prompt: Prompt; hers: string; fold?: boolean; class?: string }) {
  if (!prompt.opener && !prompt.sent_at_local && prompt.questions.length === 0) return null;
  const askedIds = prompt.asked.map((q) => q.id);
  const asked = prompt.asked.map((q) => q.question);
  const questions = prompt.questions.map((q) => ({ id: q.id, text: q.text, section: q.section }));
  // a single question folds into nothing: it is the asked one (shown large) or, when none was marked, shown as it is
  const lone = questions.length === 1 && asked.length === 0 ? questions[0] : undefined;
  return (
    <Letter from={hers} at={prompt.sent_at_local ?? undefined} opener={prompt.opener ?? undefined} asked={asked}
      askedIds={askedIds} questions={fold ? questions : []} class={cls} label={`${hers}'s prompt`}>
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
