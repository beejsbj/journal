import { useState } from "preact/hooks";
import { BASE } from "../api";
import { clip, duration } from "../format";
import { Chevron, Mic, Reply } from "../icons";
import type { Block, EntryDoc } from "../types";
import { BlockButtons, BlockEditor, blockIdentity, type BlockTarget } from "./BlockActions";
import { Markdown } from "./Markdown";
import { PromptMessage } from "./Prompt";

/**
 * Whether the entry folds a practice's questions behind "The 24 questions" (Today always shows them in full). Which
 * the entry should do is still open (DECISIONS.md, IDENTITY); flipping this one line shows them in full here too.
 */
export const FOLD_ENTRY_QUESTIONS = true;

/** What the engine writes in place of a transcript that has not arrived (audio.py), shown as a badge instead. */
const PENDING = "_(audio file — transcript pending)_";
const NOT_TRANSCRIBED = "_(audio file — not transcribed)_";

/**
 * `onChanged` turns on editing his blocks (Edit, Delete): the entry's page passes it, with the reload to run after a
 * change. Without it, or when the entry is not one the web app may write to, the document is read-only.
 */
export function Document({ doc, onChanged }: { doc: EntryDoc; onChanged?: () => void }) {
  const out = [];
  let under = "";   // the divider the blocks below belong to: "phase:3", "interrupt:3", or none
  const titles = new Map((doc.prompt?.phases ?? []).map((p) => [p.number, p.title]));
  for (const b of doc.blocks) {
    // An interrupt's answer stands under its own label, and the phase after it gets its divider again.
    let now = "";
    let label = "";
    if (b.kind === "his" && b.interrupt != null) {
      now = `interrupt:${b.interrupt}`;
      label = `Interrupt ${b.interrupt}`;
    } else if (b.kind === "his" && b.phase !== null) {
      now = `phase:${b.phase}`;
      const title = titles.get(b.phase);
      label = title ? `Phase ${b.phase} · ${title}` : `Phase ${b.phase}`;
    }
    if (now && now !== under) out.push(<Divider key={`${now}:${b.key}`} label={label} />);
    if (now) under = now;
    out.push(
      b.kind === "his" ? (
        <HisBlock key={blockIdentity(b)} block={b} name={doc.voices.his}
          {...(onChanged && doc.writable
            ? { target: { stem: doc.stem, block: b, keep: b.phase !== null || b.interrupt != null, onChanged } }
            : {})} />
      ) : (
        <HersBlock key={b.key} block={b} name={doc.voices.hers} />
      ),
    );
  }
  return (
    <div class="document">
      {doc.prompt && <PromptMessage prompt={doc.prompt} hers={doc.voices.hers} fold={FOLD_ENTRY_QUESTIONS} />}
      {out.length === 0 && <p class="empty">Nothing written yet.</p>}
      {out}
      {doc.program && doc.summary && (
        <section class="summary">
          <h3>Summary</h3>
          <Markdown text={doc.summary} />
        </section>
      )}
    </div>
  );
}

function Divider({ label }: { label: string }) {
  return (
    <div class="phase-divider" role="separator">
      <span>{label}</span>
    </div>
  );
}

function HisBlock({ block, name, target }: { block: Block; name: string; target?: BlockTarget }) {
  const [editing, setEditing] = useState(false);
  const trimmed = block.text.trim();
  const pending = trimmed === PENDING || trimmed === NOT_TRANSCRIBED;
  const source = block.source && block.source !== "text" ? block.source : null;
  return (
    <article class="block his" id={block.key} aria-label={`${name}${block.at ? `, ${block.at}` : ""}`}>
      <div class="margin">
        {block.at && <time>{block.at}</time>}
        {source && (
          <span class="source">
            {source === "voice" && <Mic width={13} height={13} />} {source}
            {block.meta?.duration ? ` · ${duration(block.meta.duration)}` : ""}
          </span>
        )}
      </div>
      <div class="body">
        {block.reply_to && (
          <p class="replying">
            <Reply width={13} height={13} /> replying to <q>{clip(block.reply_to, 140)}</q>
          </p>
        )}
        {block.images && block.images.length > 0 && (
          <div class="pictures">
            {block.images.map((image) => {
              const src = `${BASE}/media/${encodeURIComponent(image)}`;
              return (
                <a key={image} href={src} target="_blank" rel="noopener">
                  <img src={src} alt={`Picture ${image}`} loading="lazy" />
                </a>
              );
            })}
          </div>
        )}
        {block.audio && (
          <audio controls preload="none" src={`${BASE}/media/${encodeURIComponent(block.audio)}`}>
            Your browser cannot play this recording.
          </audio>
        )}
        {editing && target ? (
          <BlockEditor target={target} onClose={() => setEditing(false)} />
        ) : pending ? (
          <p class="badge pending">{trimmed === PENDING ? "transcript pending" : "not transcribed"}</p>
        ) : (
          trimmed && <p class="words">{block.text}</p>
        )}
        {/* a recording still being transcribed is the tick's to fill in; he can change it once the words are there */}
        {target && !editing && trimmed !== PENDING && <BlockButtons target={target} onEdit={() => setEditing(true)} />}
      </div>
    </article>
  );
}

/** Her words: a note in her ink with her name and time, folded to its first line, never merged into his text and never
 *  editable. On a wide screen it stands in the column beside his words, level with the block it follows. */
function HersBlock({ block, name }: { block: Block; name: string }) {
  return (
    <details class="block hers" id={block.key}>
      <summary>
        <span class="who">{name}</span>
        {block.at && <time>{block.at}</time>}
        <span class="peek">{clip(block.text, 90)}</span>
        <Chevron class="chev" />
      </summary>
      <div class="callout-body">
        <Markdown text={block.text} />
      </div>
    </details>
  );
}
