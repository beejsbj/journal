/**
 * /design: the design system, drawn by its own components (web/src/ui, the shell's seams, the tokens). Not in the
 * navigation; Settings links it ("Design system"). Every primitive and compound is here with its states, beside the
 * type scale, the colour roles light and dark, her four inks, the motion and the mark. Neutral sample words only.
 * design/DESIGN.md is the written half; when the two disagree, fix the one that is wrong.
 */
import type { ComponentChildren, JSX } from "preact";
import { useEffect, useMemo, useState } from "preact/hooks";
import * as I from "../icons";
import { INKS, chooseInk, savedInk, type Ink } from "../ink";
import { withTransition } from "../shell/transition";
import { useTitle } from "../title";
import {
  AudioPlayer, Button, ButtonLink, Calendar, Clock, DefaultHint, Disclosure, Empty, ErrorLine, IconButton, Letter, MarginNote,
  Mark, Menu, ProgressStrip, Ribbon, Segmented, Sheet, Skeleton, Stepper, Switch, TextArea, TextButton, TextField, ThemeChoice,
  Toast, clockTime,
} from "../ui";
import { ApiError } from "../api";
import "./design.css";

const QUESTIONS = [
  { id: "1.1", text: "What's spinning in your mind?", section: "Letting go" },
  { id: "1.2", text: "What do you need to set down before sleep?", section: "Letting go" },
  { id: "2.1", text: "What went well today?", section: "The day" },
  { id: "2.2", text: "What asked something of you?", section: "The day" },
  { id: "3.1", text: "What made you smile?", section: "Small moments" },
  { id: "3.2", text: "What surprised you?", section: "Small moments" },
];

const ROLES = ["room", "pane", "raised", "raised-2", "sunk", "letter", "text", "text-2", "text-3", "hair", "hair-2", "lamp", "on-lamp"] as const;
const ICONS = Object.entries(I).filter(([name, v]) => typeof v === "function" && /^[A-Z]/.test(name) &&
  !["Mark", "Envelope", "InkBlot", "Book", "Shelf", "Spark", "Gear", "ChevronRight", "Back"].includes(name)) as [string, (p: JSX.SVGAttributes<SVGSVGElement>) => JSX.Element][];

/** A few seconds of silence as a WAV blob, so the player has a real file to play (media-src allows blob:). */
function silence(seconds: number): string {
  const rate = 8000;
  const n = rate * seconds;
  const buf = new ArrayBuffer(44 + n);
  const v = new DataView(buf);
  const text = (at: number, s: string) => [...s].forEach((c, i) => v.setUint8(at + i, c.charCodeAt(0)));
  text(0, "RIFF"); v.setUint32(4, 36 + n, true); text(8, "WAVE"); text(12, "fmt ");
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true); v.setUint32(24, rate, true);
  v.setUint32(28, rate, true); v.setUint16(32, 1, true); v.setUint16(34, 8, true); text(36, "data"); v.setUint32(40, n, true);
  for (let i = 0; i < n; i++) v.setUint8(44 + i, 128);
  return URL.createObjectURL(new Blob([buf], { type: "audio/wav" }));
}

function Section({ id, n, title, lede, children }: { id: string; n: string; title: string; lede?: string; children: ComponentChildren }) {
  return (
    <section class="ds-sec" aria-labelledby={id}>
      <h2 id={id}><span class="ds-n">{n}</span>{title}</h2>
      {lede && <p class="ds-lede">{lede}</p>}
      {children}
    </section>
  );
}

const INDEX: [string, string][] = [
  ["ds-type", "Type"], ["ds-colour", "Colour"], ["ds-inks", "Her inks"], ["ds-mark", "The mark"], ["ds-icons", "Icons"],
  ["ds-actions", "Actions"], ["ds-forms", "Forms"], ["ds-choices", "Choices"], ["ds-folds", "Folds and layers"],
  ["ds-her", "Her voice"], ["ds-media", "Voice and time"], ["ds-states", "States"], ["ds-motion", "Motion"],
];

export function DesignRoute() {
  useTitle("Design system");
  const [ink, setInk] = useState<Ink>(savedInk);
  const [sw, setSw] = useState(true);
  const [seg, setSeg] = useState<"list" | "rail" | "none">("list");
  const [hours, setHours] = useState(6);
  const [time, setTime] = useState(21 * 60 + 30);
  const [kind, setKind] = useState<"all" | "audio" | "her" | "starred">("all");
  const [practice, setPractice] = useState("any");
  const [sheet, setSheet] = useState(false);
  const [toast, setToast] = useState(false);
  const [day, setDay] = useState<string | null>("2026-10-14");
  const [month, setMonth] = useState("2026-10");
  const [moved, setMoved] = useState(false);
  const audio = useMemo(() => (typeof URL.createObjectURL === "function" ? silence(4) : ""), []);
  useEffect(() => () => { if (audio) URL.revokeObjectURL(audio); }, [audio]);
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(false), 6000);
    return () => clearTimeout(t);
  }, [toast]);

  return (
    <div class="ds">
      <header class="ds-head">
        <p class="kicker">This device · <a href="/settings">Settings</a></p>
        <h1 class="display">Design system</h1>
        <p class="lede">
          Every part the journal is made of, drawn by the parts themselves. His words in the book's roman, hers typed in
          her ink, the interface in a quiet sans; lamp light on paper by day, a cool room at night.
        </p>
        <nav class="ds-index" aria-label="Sections">
          {INDEX.map(([id, t], i) => (
            <a key={id} href={`#${id}`}><span class="ds-n">{String(i + 1).padStart(2, "0")}</span>{t}</a>
          ))}
        </nav>
      </header>

      <Section id="ds-type" n="01" title="Type" lede="Three families, one job each. No letter-spaced capitals anywhere.">
        <div class="ds-type">
          <p class="ds-t-label">Display · Newsreader 340, 44</p>
          <p class="display">Thursday, the fifteenth</p>
          <p class="ds-t-label">Numeral · Newsreader 300, 88</p>
          <p class="numeral">15</p>
          <p class="ds-t-label">Book head · Newsreader italic 400, 20</p>
          <h3 class="book-head">Written today<span class="count">3</span></h3>
          <p class="ds-t-label">His words, reading · Newsreader 400, 20 / 1.62</p>
          <p class="read">The lighthouse came on at dusk the way it always does, without asking anyone, and I stood at the end of the pier longer than I meant to.</p>
          <p class="ds-t-label">His words, writing · Newsreader 400, 24 / 1.66</p>
          <p class="ds-t-write">Later I thought of the harbour, how it holds still while everything in it moves.</p>
          <p class="ds-t-label">Her words · IBM Plex Mono 400, 14.5 / 1.72, in her ink</p>
          <p class="her">Evening. The lamp's on and the harbour's gone dark. Let the day land.</p>
          <p class="ds-t-label">The question she asked · Plex Mono italic, 17</p>
          <p class="her ds-t-asked">What's spinning in your mind?</p>
          <p class="ds-t-label">Interface · Instrument Sans 15 / 13 / 12</p>
          <p class="ds-t-ui">Send now <span class="hint">· every day at 21:30</span> <span class="ds-t-micro">3 of 10</span></p>
          <p class="ds-t-label">Kicker and lede</p>
          <p class="kicker">Evening journal</p>
          <p class="lede">The practices Ada sends on a schedule, and the ones you start when you need them.</p>
        </div>
      </Section>

      <Section id="ds-colour" n="02" title="Colour" lede="Roles, not colours: a component reads --pane or --text, never a hex. Paper by day, the night room after dark. Status has no hue.">
        <div class="ds-row"><ThemeChoice /></div>
        <div class="ds-schemes">
          {(["light", "dark"] as const).map((t) => (
            <div key={t} class="ds-scheme" data-theme={t} data-ink={ink}>
              <p class="ds-scheme-name">{t === "light" ? "Paper" : "Night"}</p>
              <ul class="ds-swatches">
                {ROLES.map((r) => (
                  <li key={r}><span class="ds-sw" style={{ background: `var(--${r})` }} /><code>--{r}</code></li>
                ))}
                <li><span class="ds-sw" style={{ background: "var(--her)" }} /><code>--her</code></li>
                <li><span class="ds-sw" style={{ background: "var(--her-soft)" }} /><code>--her-soft</code></li>
                <li><span class="ds-sw" style={{ background: "var(--her-wash)" }} /><code>--her-wash</code></li>
              </ul>
              <div class="ds-glow"><span>--glow: lamp light at the head of a page</span></div>
              <p class="ds-elev"><span style={{ boxShadow: "var(--elev-1)" }}>elev-1</span><span style={{ boxShadow: "var(--elev-2)" }}>elev-2</span><span style={{ boxShadow: "var(--elev-pop)" }}>elev-pop</span><span style={{ boxShadow: "var(--elev-letter)" }}>elev-letter</span></p>
            </div>
          ))}
        </div>
      </Section>

      <Section id="ds-inks" n="03" title="Her inks" lede="Her ink is the only hue on the page, and it is hers alone. The reader chooses it on this device; each holds AA on every ground, light and dark.">
        <div class="ds-row" role="radiogroup" aria-label="Her ink">
          {INKS.map((i) => (
            <button key={i.id} type="button" role="radio" aria-checked={ink === i.id} class="ds-inkopt" data-ink={i.id}
              onClick={() => { chooseInk(i.id); setInk(i.id); }}>
              <I.InkBlot class="ds-blot" />
              <b>{i.label}</b>
              <small>{i.note}</small>
            </button>
          ))}
        </div>
        <div class="ds-inks">
          {(["light", "dark"] as const).map((t) =>
            INKS.map((i) => (
              <div key={`${t}${i.id}`} class="ds-ink-tile" data-theme={t} data-ink={i.id}>
                <p class="letter-from"><span class="letter-dot" aria-hidden="true" />{i.label}, {t === "light" ? "paper" : "night"}</p>
                <p class="her">How did today go?</p>
              </div>
            )),
          )}
        </div>
      </Section>

      <Section id="ds-mark" n="04" title="The mark" lede="The margin j: the notebook's margin rule bends into his j, and her ring, in her ink, is its dot. The tab keeps its own small j.">
        <div class="ds-row ds-marks">
          {[96, 48, 30, 16].map((s) => <Mark key={s} size={s} class="ds-mark" />)}
          <span class="ds-wordmark"><Mark size={30} /> journal</span>
        </div>
        <div class="ds-row">
          <img src="/icons/icon-512.png" width={120} height={120} alt="The app icon" class="ds-appicon" />
          <img src="/icons/icon-maskable-512.png" width={120} height={120} alt="The app icon, masked" class="ds-appicon round" />
          <img src="/icons/favicon.svg" width={32} height={32} alt="The tab's icon" />
          <img src="/icons/favicon.svg" width={16} height={16} alt="The tab's icon, at 16" />
        </div>
      </Section>

      <Section id="ds-icons" n="05" title="Icons" lede="One stroke family on a 24 grid, stroke 1.6, round joins. An icon alone is always a labelled control.">
        <ul class="ds-icons">
          {ICONS.map(([name, Icon]) => <li key={name}><Icon width={22} height={22} /><code>{name}</code></li>)}
          <li><I.Envelope /><code>Envelope</code></li>
          <li><I.InkBlot width={30} height={26} /><code>InkBlot</code></li>
        </ul>
      </Section>

      <Section id="ds-actions" n="06" title="Actions" lede="His actions are the lamp. One primary per view; quiet for the way back; danger asks in place first.">
        <div class="ds-row">
          <Button variant="primary">Send your answer</Button>
          <Button>Start</Button>
          <Button variant="quiet">Cancel</Button>
          <Button variant="danger">Delete</Button>
          <Button variant="primary" disabled>Saving…</Button>
        </div>
        <div class="ds-row">
          <Button variant="primary" size="small" icon={<I.Pen />}>Write</Button>
          <Button size="small" icon={<I.Mic />}>Speak it</Button>
          <ButtonLink href="/design#ds-actions" size="small">Open the entry</ButtonLink>
          <TextButton>Show all</TextButton>
        </div>
        <div class="ds-row">
          <IconButton label="Edit"><I.Pen /></IconButton>
          <IconButton label="Copy"><I.Copy /></IconButton>
          <IconButton label="Delete"><I.Trash /></IconButton>
          <IconButton label="Starred" pressed><I.Star /></IconButton>
          <IconButton label="More"><I.More /></IconButton>
          <IconButton label="Newer" disabled><I.ChevronUp /></IconButton>
          <IconButton label="Close" size="small"><I.Close /></IconButton>
        </div>
      </Section>

      <Section id="ds-forms" n="07" title="Forms" lede="A label above, the hint under it, the error said in words with a filled mark. His words go on a ruled line in his own face.">
        <div class="ds-grid2">
          <TextField id="ds-place" label="Place" hint="Looked up for its coordinates and time zone." value="Hillside" />
          <TextField id="ds-zone" label="Time zone" hint="An IANA name, such as Asia/Kolkata." error="That is not a time zone this server knows." value="Moon/Crater" />
          <TextArea id="ds-note" label="A note" voice="his" placeholder="Write a line…" rows={3} />
          <TextField id="ds-off" label="Set in Hermes" hint="hermes config set plugins.entries.journal.settings…" value="Asia/Kolkata" disabled />
        </div>
      </Section>

      <Section id="ds-choices" n="08" title="Choices" lede="A switch acts at once; a segmented control picks one of a few; a stepper moves a number with its unit; a menu opens a short list from a word in a sentence.">
        <div class="ds-rows">
          <div class="ds-srow">
            <div class="ds-lab"><b id="ds-sw-l">Today block in the chat</b><span class="hint">Sent with her first message of the day.</span></div>
            <Switch checked={sw} onChange={setSw} labelledBy="ds-sw-l" />
          </div>
          <div class="ds-srow">
            <div class="ds-lab"><b id="ds-sw2">Weekly pattern scan</b><span class="hint">Off: she will not send it.</span></div>
            <Switch checked={false} onChange={() => {}} labelledBy="ds-sw2" disabled />
          </div>
          <div class="ds-srow">
            <div class="ds-lab"><b>Beside an entry</b><span class="hint">Titles, dates and dots, or nothing.</span></div>
            <Segmented label="Beside an entry" iconOnly value={seg} onChange={setSeg}
              options={[{ value: "list", label: "Titles", icon: <I.SideList /> }, { value: "rail", label: "Dates and dots", icon: <I.SideDots /> }, { value: "none", label: "None", icon: <I.SideNone /> }]} />
          </div>
          <div class="ds-srow">
            <div class="ds-lab"><b>Page</b><span class="hint">This device only.</span></div>
            <ThemeChoice />
          </div>
          <div class="ds-srow">
            <div class="ds-lab"><b>Hours a prompt stays open</b><span class="hint"><DefaultHint isDefault={hours === 6} value="6 hours" onUse={() => setHours(6)} /></span></div>
            <Stepper label="Hours a prompt stays open" value={hours} onChange={setHours} min={1} max={72} unit="hours" />
          </div>
          <div class="ds-srow">
            <div class="ds-lab"><b>Day reflection, latest time</b><span class="hint"><DefaultHint isDefault={time === 21 * 60 + 30} value="21:30" onUse={() => setTime(21 * 60 + 30)} /></span></div>
            <Stepper label="Day reflection, latest time" value={time} onChange={setTime} min={0} max={23 * 60 + 45} step={15} format={clockTime} />
          </div>
        </div>
        <p class="ds-sentence">
          Showing{" "}
          <Menu label="Which entries" value={kind} onSelect={setKind}
            items={[{ value: "all", label: "every entry", count: 14 }, { value: "audio", label: "entries with your voice", count: 3 }, { value: "her", label: "entries Ada wrote back to", count: 6 }, { value: "starred", label: "starred entries", count: 1, rule: true }]}>
            {{ all: "every entry", audio: "entries with your voice", her: "entries Ada wrote back to", starred: "starred entries" }[kind]}<I.ChevronDown />
          </Menu>{" "}
          from{" "}
          <Menu label="Which practice" value={practice} onSelect={setPractice}
            items={[{ value: "any", label: "any practice" }, { value: "morning", label: "Morning journal", count: 4 }, { value: "evening", label: "Evening journal", count: 5 }, { value: "note", label: "notes of your own", count: 2, rule: true }]}>
            {practice === "any" ? "any practice" : practice === "note" ? "notes of your own" : practice === "morning" ? "Morning journal" : "Evening journal"}<I.ChevronDown />
          </Menu>
          , newest first.
        </p>
        <div class="ds-row">
          <Menu label="This passage" items={[{ value: "edit", label: "Edit these words" }, { value: "copy", label: "Copy" }, { value: "delete", label: "Delete…", rule: true }]}
            onSelect={() => {}} triggerClass="icon-button" triggerLabel="More for this passage">
            <I.More />
          </Menu>
          <span class="hint">An action menu (no checked item).</span>
        </div>
      </Section>

      <Section id="ds-folds" n="09" title="Folds and layers" lede="Disclosures grow on the soft spring and their content settles a beat behind. A sheet comes up from the foot on a phone, centred on a wide screen; a toast says what just happened.">
        <div class="ds-reading">
          <Disclosure icon={<I.Envelope class="ds-env" />} head={<b class="ds-rt">What she noticed</b>}
            peek={<span class="her">Three things: the light that doesn't ask, the call you keep not making, the gulls' sudden quiet.</span>}>
            <ol class="ds-noticed">
              <li><q>without asking anyone</q><p class="her">Things that arrive unbidden are where tonight's words settle.</p></li>
              <li><q>all at once, stop</q><p class="her">An ending you admire. Perhaps a way you'd like your evenings to close.</p></li>
            </ol>
          </Disclosure>
          <Disclosure head={<b class="ds-rt">Her reflection</b>} peek="To carry forward: make the call before the ferry." defaultOpen>
            <p class="her ds-refl-q">What does the sea ask?</p>
            <p class="her">Wednesday went from rain on the skylight to the gulls going quiet. In between, the same unmade call sat under everything.</p>
          </Disclosure>
        </div>
        <div class="ds-row">
          <Button onClick={() => setSheet(true)}>Open a sheet</Button>
          <Button onClick={() => setToast(true)}>Show a toast</Button>
        </div>
        {sheet && (
          <Sheet title="Mood, people, star" onClose={() => setSheet(false)}>
            <div class="ds-sheet-body">
              <TextField id="ds-mood" label="Mood" placeholder="Add, then press Enter" />
              <div class="ds-row"><Button variant="primary" onClick={() => setSheet(false)}>Save</Button><Button variant="quiet" onClick={() => setSheet(false)}>Cancel</Button></div>
            </div>
          </Sheet>
        )}
        <Toast open={toast} actions={<Button size="small" onClick={() => setToast(false)}>Undo</Button>}>The passage is gone.</Toast>
      </Section>

      <Section id="ds-her" n="10" title="Her voice" lede="Her letter: sectioned, typed in her ink, under a dashed crease. Never fastened, never editable. Her notes stand in the right margin beside the passage they belong to.">
        <Letter from="Ada" at="23:00" opener={"Evening. The lamp's on and the harbour's gone dark.\n\nLet the day land; answer whatever pulls at you and leave the rest."}
          asked={["What's spinning in your mind?"]} questions={QUESTIONS} askedIds={["1.1"]}
          actions={<><Button variant="primary" size="small" icon={<I.Pen />}>Answer</Button><Button variant="quiet" size="small" icon={<I.Mic />}>Speak it</Button></>} />
        <div class="ds-margin">
          <div class="ds-margin-hour"><time>23:12</time><span>typed</span></div>
          <p class="read">First words, about the lighthouse. It came on at dusk the way it always does, without asking anyone.</p>
          <MarginNote who="Ada" at="08:02">Certainty you admire in the light, and want for yourself. It comes back on Friday, at the point.</MarginNote>
        </div>
        <Letter from="Ada" at="Sunday" opener="The week leaned on the water. You wrote five evenings out of seven." class="ds-slip" />
      </Section>

      <Section id="ds-media" n="11" title="Voice and time" lede="His recording, the month he wrote in, a program's way through, when a practice comes, and the ribbon on a page he wrote.">
        {audio && <AudioPlayer src={audio} duration={4} label="your voice" />}
        <div class="ds-grid2">
          <Calendar month={month} onMonth={setMonth} marked={["2026-10-02", "2026-10-05", "2026-10-06", "2026-10-08", "2026-10-09", "2026-10-11", "2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15"]}
            selected={day} today="2026-10-15" onSelect={setDay} legend />
          <div>
            <ProgressStrip title="Fix your life" label="Fix your life, sessions" done={3} total={10} current={0.4} action={<Button size="small" variant="primary">Continue</Button>} />
            <ProgressStrip label="A bare strip" done={6} total={10} />
            <ul class="ds-clocks">
              <li><Clock at="06:00" /><span>Every day · 06:00</span></li>
              <li><Clock at="21:30" /><span>Every day · 21:30</span></li>
              <li><Clock at="12:00" weekday="Wed" /><span>Wednesdays · 12:00</span></li>
              <li><Clock /><span>When you need one</span></li>
              <li><Clock progress={{ done: 2, total: 10 }} /><span>A program</span></li>
              <li><Ribbon kind="own" label="Yours" /><span>Yours</span></li>
              <li><Ribbon kind="edited" label="Rewritten by you" /><span>A built-in he rewrote</span></li>
            </ul>
          </div>
        </div>
      </Section>

      <Section id="ds-states" n="12" title="States" lede="Loading is the shape of what is coming; an error says what happened, with Retry; nothing there is one quiet sentence. Offline, the page says how old it is.">
        {/* specimens: the shots' "settled" check skips skeletons under [data-specimen] */}
        <div data-specimen>
          <Skeleton rows={2} />
          <Skeleton rows={3} shape="line" />
        </div>
        <ErrorLine error={new ApiError(0, "network", "The server could not be reached.")} onRetry={() => {}} />
        <Empty>No entries match. <a href="/entries">Show all of them</a></Empty>
        <p class="ribbon ds-ribbon" role="note"><I.Offline /><span>offline, as of 09:12</span></p>
      </Section>

      <Section id="ds-motion" n="13" title="Motion" lede="Purposeful and short. Arrivals ease out; the controls ride a firm spring, panes and folds a soft one. With reduced motion everything is instant.">
        <div class="ds-motion">
          {[["--e-out", "arrivals"], ["--e-paper", "anything paper"], ["--spring", "controls"], ["--spring-soft", "panes, folds"]].map(([t, use]) => (
            <div key={t} class="ds-curve"><span class={`ds-dot${moved ? " moved" : ""}`} style={{ transitionTimingFunction: `var(${t})` }} /><code>{t}</code><small>{use}</small></div>
          ))}
        </div>
        <div class="ds-row">
          <Button onClick={() => setMoved((m) => !m)}>Play the curves</Button>
          <Button onClick={() => void withTransition(() => setMoved((m) => !m), "page")}>A page transition</Button>
        </div>
      </Section>
    </div>
  );
}
