import type { ComponentChildren } from "preact";
import { useRef, useState } from "preact/hooks";
import { BASE } from "../api";
import { clip, duration } from "../format";
import { Mic, Reply } from "../icons";
import type { Block, EntryDoc } from "../types";
import { AudioPlayer, MarginNote } from "../ui";
import { BlockButtons, BlockEditor, blockIdentity, type BlockTarget } from "./BlockActions";
import { Markdown } from "./Markdown";
import { PromptLetter } from "./Prompt";
import "./Document.css";

/**
 * Whether the entry folds a practice's questions behind "All 24 questions, in 10 parts" (Today always shows them in
 * full). Which the entry should do is still open (DECISIONS.md, IDENTITY); flipping this one line shows them in full
 * here too.
 */
export const FOLD_ENTRY_QUESTIONS = true;

/** What the engine writes in place of a transcript that has not arrived (audio.py), shown as a word instead. */
const PENDING = "_(audio file — transcript pending)_";
const NOT_TRANSCRIBED = "_(audio file — not transcribed)_";

export function isPending(text: string): boolean {
  const t = text.trim();
  return t === PENDING || t === NOT_TRANSCRIBED;
}

/** "23:12 IST" → "23:12": the margin keeps the hour; the zone is the same all page long. */
export function hourOf(at: string | null | undefined): string {
  return at?.match(/\d{1,2}:\d{2}/)?.[0] ?? at ?? "";
}

/**
 * The page as a spread: the hours in the left margin, his words in the book's roman down the middle, her notes in the
 * right margin beside the passage they follow (under it when the page is narrow), her letter at the head. His passages
 * and hers never merge: his are `article.block.his`, hers `aside.block.hers` (ui/MarginNote), read-only.
 *
 * `onChanged` turns on editing his blocks (Edit, Delete): the entry's page passes it, with the reload to run after a
 * change. Without it, or when the entry is not one the web app may write to, the document is read-only. `notify` is
 * where a done action says so (Copied, Deleted).
 */
export function Document({ doc, onChanged, notify }: { doc: EntryDoc; onChanged?: () => void; notify?: (said: string) => void }) {
  const out: ComponentChildren[] = [];
  let under = "";   // the divider the blocks below belong to: "phase:3", "interrupt:3", or none
  const titles = new Map((doc.prompt?.phases ?? []).map((p) => [p.number, p.title]));
  // a passage of his and the notes of hers that follow it, laid side by side
  let passage: { his: Block | null; hers: Block[] } | null = null;
  const flush = () => {
    if (!passage) return;
    const p = passage;
    passage = null;
    const lead = p.his ? blockIdentity(p.his) : `hers:${p.hers[0]?.key}`;
    out.push(
      <div class="passage" key={`p:${lead}`}>
        {p.his && (
          <HisBlock key={blockIdentity(p.his)} block={p.his} name={doc.voices.his}
            {...(onChanged && doc.writable
              ? { target: { stem: doc.stem, block: p.his, keep: p.his.phase !== null || p.his.interrupt != null, onChanged, notify } }
              : {})} />
        )}
        {p.hers.length > 0 && (
          <div class="passage-notes">
            {p.hers.map((b) => <HersBlock key={b.key} block={b} name={doc.voices.hers} />)}
          </div>
        )}
      </div>,
    );
  };
  for (const b of doc.blocks) {
    if (b.kind === "hers") {
      if (!passage) passage = { his: null, hers: [] };
      passage.hers.push(b);
      continue;
    }
    // An interrupt's answer stands under its own label, and the phase after it gets its divider again.
    let now = "";
    let label = "";
    if (b.interrupt != null) {
      now = `interrupt:${b.interrupt}`;
      label = `Interrupt ${b.interrupt}`;
    } else if (b.phase !== null) {
      now = `phase:${b.phase}`;
      const title = titles.get(b.phase);
      label = title ? `Phase ${b.phase} · ${title}` : `Phase ${b.phase}`;
    }
    flush();
    if (now && now !== under) out.push(<Divider key={`${now}:${b.key}`} label={label} />);
    if (now) under = now;
    passage = { his: b, hers: [] };
  }
  flush();
  return (
    <div class="document">
      {doc.prompt && (doc.prompt.opener || doc.prompt.sent_at_local || doc.prompt.questions.length > 0) && (
        <div class="doc-letter">
          <div class="doc-margin her-hour" aria-hidden="true">{doc.prompt.sent_at_local && <time>{hourOf(doc.prompt.sent_at_local)}</time>}</div>
          <PromptLetter prompt={doc.prompt} hers={doc.voices.hers} fold={FOLD_ENTRY_QUESTIONS} class="doc-prompt" />
        </div>
      )}
      {out.length === 0 && <p class="empty">Nothing written yet.</p>}
      {out}
      {doc.program && doc.summary && (
        <section class="summary" aria-labelledby={`sum-${doc.stem}`}>
          <h3 class="book-head" id={`sum-${doc.stem}`}>Summary</h3>
          <div class="her">
            <Markdown text={doc.summary} />
          </div>
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

const PRESS_MS = 480;

function HisBlock({ block, name, target }: { block: Block; name: string; target?: BlockTarget }) {
  const [editing, setEditing] = useState(false);
  const [sheet, setSheet] = useState(false);
  const press = useRef<{ t: ReturnType<typeof setTimeout>; x: number; y: number } | null>(null);
  const [pressing, setPressing] = useState(false);
  const trimmed = block.text.trim();
  const pending = isPending(block.text);
  const source = block.source && block.source !== "text" ? block.source : null;
  // a recording still being transcribed is the tick's to fill in; he can change it once the words are there
  const tools = !!target && !editing && trimmed !== PENDING;

  // a long press on a phone raises the passage's tools in a sheet (nothing that matters is hover-only)
  const endPress = () => {
    if (press.current) clearTimeout(press.current.t);
    press.current = null;
    setPressing(false);
  };
  const onPointerDown = (e: PointerEvent) => {
    if (!tools || e.pointerType === "mouse") return;
    if ((e.target as HTMLElement).closest("a, button, textarea, input, .player")) return;
    const { clientX: x, clientY: y } = e;
    setPressing(true);
    press.current = {
      x, y, t: setTimeout(() => {
        press.current = null;
        setPressing(false);
        navigator.vibrate?.(8);
        setSheet(true);
      }, PRESS_MS),
    };
  };
  const onPointerMove = (e: PointerEvent) => {
    const p = press.current;
    if (p && Math.hypot(e.clientX - p.x, e.clientY - p.y) > 8) endPress();
  };

  return (
    <article class={["block", "his", editing && "editing", pressing && "pressing"].filter(Boolean).join(" ")} id={block.key}
      aria-label={`${name}${block.at ? `, ${block.at}` : ""}`}>
      <div class="margin">
        {block.at && <Hour at={block.at} />}
        {source && (
          <span class="source">
            {source === "voice" && <Mic width={13} height={13} />} {source}
            {block.meta?.duration ? ` · ${duration(block.meta.duration)}` : ""}
          </span>
        )}
      </div>
      <div class="body" onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={endPress}
        onPointerCancel={endPress} onPointerLeave={endPress}
        onContextMenu={(e) => { if (pressing) e.preventDefault(); }}>
        {block.reply_to && (
          <p class="replying">
            <Reply width={15} height={15} /> <span>replying to <q class="her">{clip(block.reply_to, 140)}</q></span>
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
          <AudioPlayer src={`${BASE}/media/${encodeURIComponent(block.audio)}`} duration={block.meta?.duration} label={`your voice, ${hourOf(block.at)}`} />
        )}
        {editing && target ? (
          <BlockEditor target={target} onClose={() => setEditing(false)} />
        ) : pending ? (
          <p class="badge pending">{trimmed === PENDING ? "transcript pending" : "not transcribed"}</p>
        ) : (
          trimmed && <p class="words read">{block.text}</p>
        )}
      </div>
      {tools && target && <BlockButtons target={target} onEdit={() => setEditing(true)} sheet={sheet} onSheet={setSheet} />}
    </article>
  );
}

/** The hour in the margin: "23:12", with the zone kept for a screen reader ("23:12 IST" reads whole). */
function Hour({ at }: { at: string }) {
  const hour = hourOf(at);
  const rest = at.startsWith(hour) ? at.slice(hour.length) : "";
  return rest ? <time>{hour}<span class="visually-hidden">{rest}</span></time> : <time>{at}</time>;
}

/** Her words beside his: a note in her ink with her name and time, in the right margin level with the passage it
 *  follows (under it on a narrow page), never merged into his text and never editable. */
function HersBlock({ block, name }: { block: Block; name: string }) {
  return (
    <MarginNote who={name} at={block.at ? <time>{block.at}</time> : undefined} class="block hers">
      <Markdown text={block.text} />
    </MarginNote>
  );
}
