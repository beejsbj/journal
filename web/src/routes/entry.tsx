import { useState } from "preact/hooks";
import { api } from "../api";
import { Composer } from "../components/Composer";
import { Document } from "../components/Document";
import { Markdown } from "../components/Markdown";
import { MetaSheet } from "../components/MetaSheet";
import { Sheet } from "../components/Sheet";
import { Await, Empty } from "../components/Status";
import { useLive } from "../events";
import { longDay, plainTitle } from "../format";
import { useResource } from "../hooks";
import { Star } from "../icons";
import { useTitle } from "../title";
import type { EntryDoc } from "../types";

type Tab = "entry" | "analysis";

/** What kind of page this is, in a word or two: the session of a program, the practice, or a note. */
function kindOf(doc: EntryDoc): string {
  return doc.program && doc.session ? `Session ${doc.session}` : doc.practice?.title ?? "Note";
}

function ReflectionInline({ doc }: { doc: EntryDoc }) {
  const stem = doc.analysis.reflection;
  const [res, reload] = useResource(
    (signal) => (stem ? api.reflections({ from: doc.date, to: doc.date, kind: "reflection" }, signal) : Promise.resolve([])),
    [stem, doc.date],
  );
  if (!stem) return null;
  return (
    <section class="analysis-section" aria-labelledby="refl-h">
      <h3 id="refl-h">Reflection</h3>
      <Await res={res} reload={reload} rows={1}>
        {(list) => {
          const r = list.find((x) => x.stem === stem);
          return r ? (
            <>
              <p class="refl-title">{r.title}</p>
              <Markdown text={r.body} />
              <a href={`/insights#${r.stem}`}>In Insights</a>
            </>
          ) : (
            <Empty>The reflection is not available.</Empty>
          );
        }}
      </Await>
    </section>
  );
}

function Analysis({ doc }: { doc: EntryDoc }) {
  const her = doc.voices.hers;
  const nothing = !doc.analysis.annotations && doc.analysis.notes.length === 0 && !doc.analysis.reflection && !doc.summary;
  return (
    <div class="analysis">
      {nothing && <Empty>Nothing here yet: {her} adds notes after the entry settles.</Empty>}
      {doc.analysis.annotations && (
        <section class="analysis-section" aria-labelledby="ann-h">
          <h3 id="ann-h">{her}'s annotations</h3>
          <Markdown text={doc.analysis.annotations} />
        </section>
      )}
      {doc.analysis.notes.length > 0 && (
        <section class="analysis-section" aria-labelledby="notes-h">
          <h3 id="notes-h">{her}'s notes</h3>
          {doc.analysis.notes.map((n, i) => (
            <div class="note" key={i}>
              {n.at && <time>{n.at}</time>}
              <Markdown text={n.text} />
            </div>
          ))}
        </section>
      )}
      <ReflectionInline doc={doc} />
      {doc.summary && (
        <section class="analysis-section" aria-labelledby="sum-h">
          <h3 id="sum-h">Program summary</h3>
          <Markdown text={doc.summary} />
        </section>
      )}
    </div>
  );
}

/** Add to the entry: a new block of his, at the end. A program session's answers go through the run, where the open
 *  phase is known (the server refuses a block on a program entry that does not name it). */
function Add({ doc, reload }: { doc: EntryDoc; reload: () => void }) {
  if (doc.program) {
    return (
      <p class="composer-closed">
        {doc.run ? <a href={`/runs/${doc.run}`}>Answer in the program</a> : "This is a program session."} The open phase takes your
        answer there.
      </p>
    );
  }
  if (/^(reflection|week)(-\d+)?$/.test(doc.kind)) {
    return <p class="composer-closed">This is the companion's reflection, not a page of yours. Add your own words as a note from the + button.</p>;
  }
  if (!doc.writable) {
    return <p class="composer-closed">This page is yours, not one the journal wrote, so the web app does not add to it.</p>;
  }
  return (
    <section class="add" aria-label="Add to this entry">
      <Composer
        draftKey={`entry:${doc.stem}`}
        label={`Add to this entry, as ${doc.voices.his}`}
        placeholder="Add more, in your own words"
        submitLabel="Add to the entry"
        onSubmit={(words) => api.addBlock(doc.stem, words)}
        onDone={reload}
      />
    </section>
  );
}

export function EntryRoute({ stem = "" }: { stem?: string }) {
  const [tab, setTab] = useState<Tab>("entry");
  const [editing, setEditing] = useState(false);
  const [res, reload] = useResource((signal) => api.entry(stem, signal), [stem]);
  useLive(["entries-changed", "ledger-changed", "library-changed", "tick"], reload);   // a day reflection is no "entries-changed", it follows the tick
  useTitle(res.state === "ready" ? plainTitle(res.data.title) : "Entry");

  return (
    <div class="page entry">
      <p class="crumb">
        <a href="/entries">‹ Entries</a>
      </p>
      {res.state === "error" && res.error.status === 404 ? (
        <Empty>Not an engine entry: the journal has no entry by this name.</Empty>
      ) : (
      <Await res={res} reload={reload} rows={4}>
        {(doc) => (
          <>
            <header class="page-head">
              {/* the kind above the title, unless it would only say the title again ("Evening journal" twice) */}
              {(doc.program || kindOf(doc) !== plainTitle(doc.title)) && (
                <p class="eyebrow">
                  {doc.practice?.emoji} {kindOf(doc)}
                </p>
              )}
              <h1>{plainTitle(doc.title)}</h1>
              <p class="sub">
                <span class="date">{longDay(doc.date)}</span>{" "}
                {doc.writable && (
                  <button type="button" class="link" onClick={() => setEditing(true)}>
                    Mood, people, star
                  </button>
                )}
              </p>
              {(doc.meta.mood?.length || doc.meta.people?.length || doc.meta.starred) && (
                <p class="chips">
                  {doc.meta.starred && <span class="chip"><Star width={13} height={13} /> starred</span>}
                  {doc.meta.mood?.map((m) => <span key={`m-${m}`} class="chip">{m}</span>)}
                  {doc.meta.people?.map((p) => <span key={`p-${p}`} class="chip person">{p}</span>)}
                </p>
              )}
            </header>
            <div class="tabs" role="tablist" aria-label="Entry">
              {(["entry", "analysis"] as const).map((t) => (
                <button type="button" role="tab" key={t} id={`tab-${t}`} aria-selected={tab === t} aria-controls={`panel-${t}`}
                  class={tab === t ? "on" : undefined} onClick={() => setTab(t)}>
                  {t === "entry" ? "Entry" : "Analysis"}
                </button>
              ))}
            </div>
            <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}>
              {tab === "entry" ? <Document doc={doc} onChanged={reload} /> : <Analysis doc={doc} />}
            </div>
            {tab === "entry" && <Add doc={doc} reload={reload} />}
            {editing && (
              <Sheet title="Mood, people, star" onClose={() => setEditing(false)}>
                <MetaSheet
                  doc={doc}
                  onSaved={() => {
                    setEditing(false);
                    reload();
                  }}
                  onReload={() => {
                    setEditing(false);
                    reload();
                  }}
                />
              </Sheet>
            )}
          </>
        )}
      </Await>
      )}
    </div>
  );
}
