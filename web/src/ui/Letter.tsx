/**
 * Her letter: the companion's words as he was sent them, typed in her ink on a faint wash of it, under a "From Ada"
 * line that runs out as a dashed crease. Sectioned: the line it came with, the opener, the question she asked in
 * italic, and (when the practice has more than one) all its questions folded at the foot, numbered by part, the ones
 * she asked marked "asked". Never editable, never fastened (no stamp, clip or tape: the owner's call on round 02).
 * Used wherever her words open something: the entry's head, Today's waiting prompt, the room, her week review.
 */
import type { ComponentChildren } from "preact";
import { Disclosure } from "./Disclosure";
import "./Letter.css";

export interface LetterQuestion {
  id: string;
  text: string;
  section?: string | null;
}

/** "All 24 questions, in 10 parts" (or "All 3 questions"). */
export function questionsLine(qs: readonly LetterQuestion[]): string {
  const parts = new Set(qs.map((q) => q.section).filter(Boolean)).size;
  return `All ${qs.length} questions${parts > 1 ? `, in ${parts} parts` : ""}`;
}

export function Letter({ from, at, opener, asked = [], questions = [], askedIds = [], askedLabel = "asked", children, actions, foot, class: cls, label }: {
  /** Her name ("Ada"). */
  from: string;
  /** When it was sent, as the page says it ("23:00", "Wed 14 Oct, 23:00"). */
  at?: ComponentChildren;
  /** The opener: a string (paragraphs split on blank lines) or her rendered words. */
  opener?: ComponentChildren;
  /** The questions she asked this time, set large in italic. */
  asked?: readonly string[];
  /** The practice's questions, folded at the foot when there is more than one. */
  questions?: readonly LetterQuestion[];
  askedIds?: readonly string[];
  askedLabel?: string;
  /** More of her words, after the asked question. */
  children?: ComponentChildren;
  /** His answers to it (Answer, Speak it), on a crease of their own. */
  actions?: ComponentChildren;
  /** Anything else in the foot, beside the questions' fold. */
  foot?: ComponentChildren;
  class?: string;
  /** The region's name (default "From Ada"). */
  label?: string;
}) {
  const paras = typeof opener === "string" ? opener.split(/\n\s*\n/).map((p) => p.trim()).filter(Boolean) : null;
  const sections: { name: string | null; items: LetterQuestion[] }[] = [];
  for (const q of questions) {
    const last = sections[sections.length - 1];
    if (last && last.name === (q.section ?? null)) last.items.push(q);
    else sections.push({ name: q.section ?? null, items: [q] });
  }
  return (
    <section class={["letter", cls].filter(Boolean).join(" ")} aria-label={label ?? `From ${from}`}>
      <p class="letter-from">
        <span class="letter-dot" aria-hidden="true" />
        <span>From {from}</span>
        {at && <span class="letter-at">sent {at}</span>}
      </p>
      <div class="letter-body her">
        {paras ? paras.map((p, i) => <p key={i}>{p}</p>) : opener}
        {asked.map((q) => (
          <p key={q} class="letter-asked">{q}</p>
        ))}
        {children}
      </div>
      {(questions.length > 1 || foot) && (
        <div class="letter-foot">
          {questions.length > 1 && (
            <Disclosure class="letter-questions" head={questionsLine(questions)}>
              <div class="letter-qlist her">
                {sections.map((s, i) => (
                  <section key={i} class="letter-qsec">
                    {s.name && <h4>{s.name}</h4>}
                    <ol>
                      {s.items.map((q) => (
                        <li key={q.id} class={askedIds.includes(q.id) ? "asked" : undefined}>
                          <span class="letter-qid">{q.id}</span>
                          <span class="letter-qt">
                            {q.text}
                            {askedIds.includes(q.id) && <em class="letter-asked-tag"> {askedLabel}</em>}
                          </span>
                        </li>
                      ))}
                    </ol>
                  </section>
                ))}
              </div>
            </Disclosure>
          )}
          {foot}
        </div>
      )}
      {actions && <div class="letter-actions">{actions}</div>}
    </section>
  );
}
