import { useLocation } from "preact-iso";
import { useState } from "preact/hooks";
import { api } from "../api";
import { useAction, useResource } from "../hooks";
import { isProgramBacked } from "../labels";
import type { Created, LibraryItem, PracticeRow } from "../types";
import { Composer } from "./Composer";
import { Sheet } from "./Sheet";
import { Await, Empty, ErrorLine } from "./Status";

type Mode = "menu" | "note" | "voice" | "answer" | "start";

function Saved({ made, onClose }: { made: Created; onClose: () => void }) {
  return (
    <p class="saved" role="status">
      {made.rule === "/j" ? "Saved as a note. " : "Saved. "}
      <a href={`/entries/${made.stem}`} onClick={onClose}>
        Open the entry
      </a>
    </p>
  );
}

/** A note on its own (`/j`): text, or a recording with a line if there is one to add. */
function Note({ voice, onClose }: { voice: boolean; onClose: () => void }) {
  const [made, setMade] = useState<Created | null>(null);
  return (
    <>
      <Composer
        draftKey={voice ? "quick-voice" : "quick-note"}
        label={voice ? "A line to go with the recording" : "A note"}
        placeholder={voice ? "A line to go with it (optional)" : "A note, in your own words"}
        submitLabel="Save the note"
        onSubmit={async (words) => setMade(await api.createEntry(words))}
      />
      {made && <Saved made={made} onClose={onClose} />}
    </>
  );
}

/** Reply to a prompt that is waiting: the prompts of today still open. */
function Answer({ onClose }: { onClose: () => void }) {
  const [res, reload] = useResource(async (signal) => {
    const [today, library] = await Promise.all([api.today(signal), api.library(signal).catch(() => [] as LibraryItem[])]);
    return { open: today.practices.rows.filter((r) => r.state === "open"), library };
  }, []);
  const [which, setWhich] = useState<PracticeRow | null>(null);
  const [made, setMade] = useState<Created | null>(null);
  return (
    <Await res={res} reload={reload} rows={2}>
      {({ open, library }) => {
        if (open.length === 0) return <Empty>Nothing is waiting for an answer right now.</Empty>;
        const title = (r: PracticeRow) => library.find((p) => p.id === r.id)?.title ?? r.id;
        return (
          <>
            <ul class="plain-list picks">
              {open.map((r, i) => (
                <li key={`${r.id}#${i}`}>
                  <button type="button" class={`button${which === r ? " primary" : ""}`} onClick={() => { setWhich(r); setMade(null); }}>
                    {title(r)} <span class="hint">sent {r.sent ?? ""}</span>
                  </button>
                </li>
              ))}
            </ul>
            {which && (
              <Composer
                draftKey={`reply:${which.id}:${open.indexOf(which)}`}
                label={`Reply to ${title(which)}`}
                placeholder="Reply in your own words"
                submitLabel="Reply"
                onSubmit={async (words) => setMade(await api.createEntry({ practice: which.id, ...words }))}
              />
            )}
            {made && <Saved made={made} onClose={onClose} />}
          </>
        );
      }}
    </Await>
  );
}

/** Begin a program or a situational session that has no run going. The server starts it a moment later. */
function Start({ onClose }: { onClose: () => void }) {
  const { route } = useLocation();
  const [res, reload] = useResource(async (signal) => {
    const [items, runs] = await Promise.all([api.library(signal), api.runs(signal).catch(() => [])]);
    const live = new Set(runs.filter((r) => r.status === "active" || r.status === "paused").map((r) => r.program));
    return items.filter((i) => isProgramBacked(i) && i.enabled && !live.has(i.id));
  }, []);
  const act = useAction();
  return (
    <Await res={res} reload={reload} rows={2}>
      {(items) =>
        items.length === 0 ? (
          <Empty>Every program is already running, or none is turned on.</Empty>
        ) : (
          <>
            <ul class="plain-list picks">
              {items.map((i) => (
                <li key={i.id}>
                  <button
                    type="button"
                    class="button"
                    disabled={act.busy}
                    onClick={async () => {
                      const done = await act.run(() => api.startRun(i.id));
                      if (done) {
                        onClose();
                        route(`/library/${i.id}`);
                      }
                    }}
                  >
                    {i.emoji} {i.title}
                  </button>
                </li>
              ))}
            </ul>
            {act.error && <ErrorLine error={act.error} />}
          </>
        )
      }
    </Await>
  );
}

const TITLES: Record<Mode, string> = {
  menu: "Write",   // the + is "Write" in the bar
  note: "Quick note",
  voice: "Voice note",
  answer: "Answer today's prompt",
  start: "Start a program",
};

/** The centre "+" of the bar: a note, a voice note, an answer to a waiting prompt, or the start of a program. */
export function QuickAdd({ onClose }: { onClose: () => void }) {
  const [mode, setMode] = useState<Mode>("menu");
  return (
    <Sheet title={TITLES[mode]} onClose={onClose}>
      {mode === "menu" ? (
        <ul class="plain-list quick-menu">
          {(["note", "voice", "answer", "start"] as const).map((m) => (
            <li key={m}>
              <button type="button" class="button wide-left" onClick={() => setMode(m)}>
                {TITLES[m]}
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <>
          <button type="button" class="link back" onClick={() => setMode("menu")}>
            ‹ Back
          </button>
          {mode === "note" && <Note voice={false} onClose={onClose} />}
          {mode === "voice" && <Note voice onClose={onClose} />}
          {mode === "answer" && <Answer onClose={onClose} />}
          {mode === "start" && <Start onClose={onClose} />}
        </>
      )}
    </Sheet>
  );
}
