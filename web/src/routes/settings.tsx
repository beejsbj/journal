/**
 * Settings (round 02, accepted: Folio's warmth and ink picker in Almanac's layout). A large title and a lede, "At a
 * glance" beside them (health, sign-in, her ink, each a jump to its section), then a numbered index that follows the
 * scroll beside eight sections: 01 Ink and page, 02-06 the settings the server lets the web app write (settingsModel),
 * 07 This device, 08 Health. While something is changed and not saved, a save bar rises at the foot ("2 changes not
 * saved · Discard · Save"); it sits at the end of the page's flow, so at the bottom it stands under the last row and
 * never over it. design/DESIGN.md, "Forms"; design/inventory.md §4, "Settings model".
 */
import { Fragment, type ComponentChildren } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { useLive } from "../events";
import { ApiError, api, byTailscale, locate, logout, session } from "../api";
import { lastVia } from "../auth";
import { OsmCredit, osmNamed } from "../components/OsmCredit";
import { asApiError, useAction, useResource, type Resource } from "../hooks";
import { InkBlot, Lock, Pin } from "../icons";
import { INKS, chooseInk, savedInk, type Ink } from "../ink";
import { isOnline } from "../offline";
import { FIELDS, GROUPS, buildPatch, keyOf, problem, shown, valueAt, type GroupId, type SettingField } from "../settingsModel";
import { useTitle } from "../title";
import type { Json } from "../schema";
import type { DoctorRow, Located, Session } from "../types";
import { Await, Button, ButtonLink, DefaultHint, Disclosure, ErrorLine, Letter, Skeleton, Switch, ThemeChoice, TypedStepper, reducedMotion } from "../ui";
import "./settings.css";

/** A settings tree as dotted rows ("windows.collapse_hours"), the way the config names its keys. */
export function flatten(value: Json, prefix = ""): [string, string][] {
  if (value !== null && typeof value === "object" && !Array.isArray(value)) {
    const entries = Object.entries(value);
    if (entries.length === 0) return prefix ? [[prefix, "{}"]] : [];
    return entries.flatMap(([k, v]) => flatten(v, prefix ? `${prefix}.${k}` : k));
  }
  const text = typeof value === "string" ? value : JSON.stringify(value);
  return [[prefix, text ?? ""]];
}

/** Settings grouped under their top-level key, which is the unit the config's origin is recorded for. */
export function groupSettings(settings: Record<string, Json>): { key: string; rows: [string, string][] }[] {
  return Object.keys(settings)
    .sort()
    .map((key) => ({ key, rows: flatten(settings[key] as Json, key) }));
}

// ── the sections and the index ────────────────────────────────────────────

const SECTIONS: { id: string; title: string }[] = [
  { id: "set-ink", title: "Ink and page" },
  ...GROUPS.map((g) => ({ id: `set-${g.id}`, title: g.title })),
  { id: "set-device", title: "This device" },
  { id: "set-health", title: "Health" },
];
const two = (n: number) => String(n).padStart(2, "0");

/** Bring a section to the top of the page and give its heading the focus (without a second scroll). */
function jump(id: string): void {
  const sec = document.getElementById(id);
  if (!sec) return;
  sec.scrollIntoView?.({ behavior: reducedMotion() ? "auto" : "smooth", block: "start" });
  sec.querySelector<HTMLElement>("h2")?.focus({ preventScroll: true });
}

/** The section the reader is in: the last whose head has passed the top of the view (a fifth of the way down). */
export function sectionAt(ids: string[], line: number): string {
  let at = ids[0] ?? "";
  for (const id of ids) {
    const el = document.getElementById(id);
    if (el && el.getBoundingClientRect().top <= line) at = id;
  }
  return at;
}

/** Which section is on screen, following the scroll (the pane's on a wide screen, the page's on a phone). */
function useCurrentSection(ids: string[]): [string, (id: string) => void] {
  const [current, setCurrent] = useState(ids[0] ?? "");
  const pinned = useRef<{ id: string; until: number } | null>(null);
  useEffect(() => {
    let frame = 0;
    const read = () => {
      frame = 0;
      const p = pinned.current;
      if (p && Date.now() < p.until) return;   // a jump in progress: the index keeps the section asked for
      pinned.current = null;
      const line = Math.min(160, (typeof window !== "undefined" ? window.innerHeight : 800) / 5);
      const ids2 = ids;
      // at the very end of the page the last sections cannot reach the top: the last one in view is the one
      const sc = document.querySelector<HTMLElement>(".main-pane");
      const atEnd = sc && sc.scrollHeight > sc.clientHeight
        ? sc.scrollTop + sc.clientHeight >= sc.scrollHeight - 2
        : window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 2 && window.scrollY > 0;
      setCurrent(atEnd ? (ids2[ids2.length - 1] ?? "") : sectionAt(ids2, line));
    };
    const onScroll = () => {
      if (!frame) frame = typeof requestAnimationFrame === "function" ? requestAnimationFrame(read) : (setTimeout(read, 0) as unknown as number);
    };
    document.addEventListener("scroll", onScroll, { capture: true, passive: true });
    return () => {
      document.removeEventListener("scroll", onScroll, { capture: true });
      if (frame && typeof cancelAnimationFrame === "function") cancelAnimationFrame(frame);
    };
  }, [ids.join(" ")]);
  const go = (id: string) => {
    pinned.current = { id, until: Date.now() + 900 };
    setCurrent(id);
    jump(id);
  };
  return [current, go];
}

function SectionIndex({ current, go, healthMark }: { current: string; go: (id: string) => void; healthMark: boolean }) {
  const list = useRef<HTMLOListElement>(null);
  // on a phone the index is a strip that scrolls sideways: keep the current section in it
  useEffect(() => {
    const ol = list.current;
    const a = ol?.querySelector<HTMLElement>('[aria-current="location"]');
    if (!ol || !a || ol.scrollWidth <= ol.clientWidth) return;
    const left = a.offsetLeft - ol.offsetLeft - 20;
    if (left < ol.scrollLeft || a.offsetLeft + a.offsetWidth - ol.offsetLeft > ol.scrollLeft + ol.clientWidth) {
      ol.scrollTo?.({ left: Math.max(0, left), behavior: reducedMotion() ? "auto" : "smooth" });
    }
  }, [current]);
  return (
    <nav class="set-index" aria-label="Settings sections">
      <ol ref={list}>
        {SECTIONS.map((s, i) => (
          <li key={s.id}>
            <a href={`#${s.id}`} aria-current={current === s.id ? "location" : undefined}
              onClick={(e) => { e.preventDefault(); go(s.id); }}>
              <span class="set-n">{two(i + 1)}</span>
              <span class="set-index-t">{s.title}</span>
              {s.id === "set-health" && healthMark && <span class="set-index-dot" title="Something to look at"><span class="visually-hidden">(something to look at)</span></span>}
            </a>
          </li>
        ))}
      </ol>
    </nav>
  );
}

function Section({ id, n, title, lede, children }: { id: string; n: number; title: string; lede?: string; children: ComponentChildren }) {
  return (
    <section class="set-sec" id={id} aria-labelledby={`${id}-h`}>
      <h2 id={`${id}-h`} tabIndex={-1}><span class="set-n">{two(n)}</span>{title}</h2>
      {lede && <p class="set-lede">{lede}</p>}
      {children}
    </section>
  );
}

// ── the health check, read once for the glance and the section ────────────

type Counts = { ok: number; warn: number; fail: number };
const count = (rows: DoctorRow[]): Counts => ({
  ok: rows.filter((r) => r.status === "ok").length,
  warn: rows.filter((r) => r.status === "warn").length,
  fail: rows.filter((r) => r.status !== "ok" && r.status !== "warn").length,
});

/** "All is well", or what is not: "1 not working, 2 to look at". */
export function healthLine(c: Counts): string {
  if (c.fail === 0 && c.warn === 0) return "All is well";
  return [c.fail ? `${c.fail} not working` : "", c.warn ? `${c.warn} to look at` : ""].filter(Boolean).join(", ");
}

const STATUS_WORD: Record<string, string> = { ok: "fine", warn: "to look at", fail: "not working" };
const statusOf = (s: string) => (s === "ok" || s === "warn" ? s : "fail");

/** A check's state as a mark of his ink: hollow-ish and faint (fine), half filled (a look), ringed and filled (wrong). */
function HealthMark({ status }: { status: string }) {
  return <i class={`hm ${statusOf(status)}`} aria-hidden="true" />;
}

// ── sign-in ──────────────────────────────────────────────────────────────

/** How this browser is signed in, asked of the server: undefined while it is asked, null when it could not say. */
function useSignIn(): { who: Session | null | undefined; again: () => void } {
  const [who, setWho] = useState<Session | null | undefined>(undefined);
  const [asked, setAsked] = useState(0);
  useEffect(() => {
    const ctl = new AbortController();
    setWho(undefined);
    session(ctl.signal).then((s) => {
      if (!ctl.signal.aborted) setWho(s);
    });
    return () => ctl.abort();
  }, [asked]);
  return { who, again: () => setAsked((n) => n + 1) };
}

/** This browser: signed in by its Tailscale identity (nothing to pair or unpair), else paired, with Unpair. Until
 *  the server has said which, a quiet line. When it cannot say (offline, the server restarting), the page says so
 *  rather than claim a pairing it has not seen; Unpair is offered then only if the browser was last known paired. */
function Pairing({ who, again }: { who: Session | null | undefined; again: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const out = () => {
    setBusy(true);
    setError(null);
    logout().catch((e: unknown) => {
      setError(asApiError(e));
      setBusy(false);
    });
  };
  const unpair = <Button size="small" disabled={busy} onClick={out}>Unpair</Button>;
  let title: ComponentChildren;
  let hint: ComponentChildren = null;
  let actions: ComponentChildren = null;
  if (who === undefined) {
    title = <span role="status">Asking the server…</span>;
  } else if (byTailscale(who)) {
    title = <>Signed in via Tailscale as <strong>{who.login}</strong></>;
    hint = "On the tailnet there is nothing to pair.";
  } else if (who === null) {
    const paired = lastVia() === "cookie" || lastVia() === "token";
    title = "Couldn't confirm how this browser is signed in";
    hint = <>The server didn't answer{isOnline() ? "" : " (you're offline)"}.</>;
    actions = <><Button size="small" onClick={again}>Check again</Button>{paired && unpair}</>;
  } else {
    title = <>Paired as <strong>{window.location.host}</strong></>;
    hint = "Pairing lasts until you unpair or the server's token is rotated.";
    actions = unpair;
  }
  return (
    <div class="set-row set-pairing">
      <div class="set-lab">
        <p class="set-label">{title}</p>
        {hint && <p class="set-hint">{hint}</p>}
        {error && <ErrorLine error={error} />}
      </div>
      {actions && <div class="set-actions">{actions}</div>}
    </div>
  );
}

// ── 01 her ink and the page ──────────────────────────────────────────────

const PROOF = "How did today go? Tell me one thing that stayed with you.";

/** The proof letter types itself again in the ink just chosen (whole at once with reduced motion). */
function useRetype(text: string, key: string): number {
  const [n, setN] = useState(text.length);
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    if (reducedMotion()) {
      setN(text.length);
      return;
    }
    setN(0);
    let i = 0;
    const t = setInterval(() => {
      i += 1;
      setN(i);
      if (i >= text.length) clearInterval(t);
    }, 24);
    return () => clearInterval(t);
  }, [key]);
  return n;
}

/**
 * Her ink, chosen on this device: four ink blots, each named, and under them a letter of hers that types itself again
 * in the ink just chosen. The choice takes effect at once and is kept in this browser only (ink.ts); nothing is sent
 * to the server, and nothing is read for it either (a read here would mark the page as read from the kept copy when
 * offline). Each option carries its own `data-ink`, so its blot is drawn in that ink whatever the page's.
 */
export function InkChoice({ from = "the companion", onChoose }: { from?: string; onChoose?: (ink: Ink) => void }) {
  const [ink, setInk] = useState<Ink>(savedInk);
  const typed = useRetype(PROOF, ink);
  return (
    <div class="ink-choice">
      <fieldset class="inks">
        <legend class="visually-hidden">Her ink</legend>
        {INKS.map((i) => (
          <label key={i.id} class={`ink${ink === i.id ? " on" : ""}`} data-ink={i.id}>
            <input type="radio" name="ink" value={i.id} checked={ink === i.id}
              onChange={() => {
                chooseInk(i.id);
                setInk(i.id);
                onChoose?.(i.id);
              }} />
            <InkBlot class="ink-blot" />
            <span class="ink-name">{i.label}</span>
            <span class="ink-note">{i.note}</span>
          </label>
        ))}
      </fieldset>
      <Letter class="ink-proof" from={from} label="A line of hers, in this ink">
        <p class="ink-proof-line">
          <span aria-hidden="true">{PROOF.slice(0, typed)}</span>
          {typed < PROOF.length && <span class="ink-caret" aria-hidden="true" />}
          <span class="visually-hidden">{PROOF}</span>
        </p>
      </Letter>
    </div>
  );
}

// ── 02-06 the settings the web app may write ─────────────────────────────

const ORIGIN_WORDS: Record<string, string> = {
  file: "config file",
  hermes: "Hermes settings",
  env: "environment",
  default: "default",
};

/** Why a field cannot be written from here, with the line that would change it; null when it can. The server decides
 *  too (it answers 409 for a key it cannot write); this only keeps the person from typing into a locked box. */
export function lockedWhy(f: SettingField, origins: Record<string, string>, profile?: string): string | null {
  const origin = origins[f.path[0] as string] ?? "default";
  if (origin === "env") return "Set in the environment; change it there.";
  if (origin === "hermes") {
    // under a named profile the line needs -p, or it changes the shell's default profile instead
    return `Set in Hermes settings: hermes${profile ? ` -p ${profile}` : ""} config set plugins.entries.journal.settings.${keyOf(f)} …`;
  }
  return null;
}

/** Which layer a field's value comes from, in words, for a field that can be saved: the config file when it has the
 *  key, the default (which a save writes into the config file) when nobody set it. Hermes and the environment are
 *  `lockedWhy`. The layer is the top-level key's: that is what a write replaces. */
export function layerNote(f: SettingField, origins: Record<string, string>): string {
  const origin = origins[f.path[0] as string] ?? "default";
  return origin === "file" ? "In the config file." : "Not set: the default applies. Saving writes it to the config file.";
}

/** A default as the hint says it: "6 hours", "on", "04:00". */
function defWords(f: SettingField): string {
  if (f.def === undefined) return "";
  if (typeof f.def === "boolean") return f.def ? "on" : "off";
  if (typeof f.def === "number") return `${f.def.toLocaleString("en")}${f.unit ? ` ${f.unit}` : ""}`;
  return f.def;
}

/** Whether a value (as the box holds it) is the default's. An unset value ("") is: the default applies. */
function isDefault(f: SettingField, v: string): boolean {
  if (v === "") return true;
  if (f.def === undefined) return false;
  if (f.kind === "flag") return (v === "on") === f.def;
  if (typeof f.def === "number") return Number(v.trim()) === f.def;
  return v.trim() === f.def;
}

/** What a field's control shows: the edit, else the stored value, else (nobody set it) the default itself. */
function shownValue(f: SettingField, edit: string | undefined, stored: string): string {
  if (edit !== undefined) return edit;
  if (stored !== "") return stored;
  return f.def === undefined || typeof f.def === "boolean" ? "" : String(f.def);
}

function Row({ f, settings, origins, profile, edit, folded, onInput }: {
  f: SettingField; settings: Record<string, Json>; origins: Record<string, string>; profile?: string;
  edit: string | undefined; folded: boolean; onInput: (v: string | undefined) => void;
}) {
  const id = `s-${keyOf(f)}`;
  const locked = lockedWhy(f, origins, profile);
  const stored = shown(f, valueAt(settings, f.path));
  const current = edit ?? stored;
  const value = shownValue(f, edit, stored);
  const bad = problem(f, current);
  const changed = edit !== undefined && edit !== stored && !(edit === "" && stored === "");
  const emptied = edit === "" && stored !== "";
  const hintId = `${id}-hint`;
  // "Use default": back to unset when it was unset (a save then leaves it), else the default's own value, written
  const useDefault = () => onInput(stored === "" ? undefined : f.kind === "flag" ? (f.def ? "on" : "off") : String(f.def));
  let control: ComponentChildren;
  if (f.kind === "flag") {
    const on = current === "on" || (current === "" && f.def === true);
    control = (
      <Switch id={id} labelledBy={`${id}-label`} checked={on} disabled={locked !== null}
        onChange={(next) => onInput(stored === "" && next === f.def ? undefined : next ? "on" : "off")} />
    );
  } else if (f.kind === "clock") {
    control = (
      <input id={id} class="input set-time" type="time" value={value} disabled={locked !== null} aria-describedby={hintId}
        aria-invalid={bad ? true : undefined} onInput={(e) => onInput((e.currentTarget as HTMLInputElement).value)} />
    );
  } else if (f.kind === "number" || f.kind === "whole") {
    const step = f.step ?? 1;
    control = (
      <TypedStepper id={id} label={f.label} value={value} onInput={onInput} step={step} unit={f.unit} whole={f.kind === "whole"}
        base={typeof f.def === "number" ? f.def : 0} min={f.min ?? (f.above !== undefined ? f.above + step : -Infinity)} max={f.max ?? Infinity}
        disabled={locked !== null} invalid={!!bad} describedBy={hintId} />
    );
  } else {
    control = (
      <input id={id} class="input set-text" type="text" value={value} disabled={locked !== null} aria-describedby={hintId}
        aria-invalid={bad ? true : undefined} autocomplete="off" onInput={(e) => onInput((e.currentTarget as HTMLInputElement).value)} />
    );
  }
  const row = (
    <div class={["setting", "set-row", `kind-${f.kind}`, locked && "locked", changed && "changed"].filter(Boolean).join(" ")}>
      <div class="set-lab">
        <label class="set-label" id={`${id}-label`} for={id}>{f.label}</label>
        <p class="set-hint" id={hintId}>
          {locked ? (
            <span class="set-lock"><Lock aria-hidden="true" />{locked}</span>
          ) : (
            <>
              {f.hint && <span>{f.hint}</span>}
              {emptied ? (
                <em class="set-left">Empty: left as it is</em>
              ) : f.def !== undefined && f.kind !== "text" ? (
                <DefaultHint isDefault={isDefault(f, current)} value={defWords(f)} onUse={useDefault} />
              ) : null}
            </>
          )}
        </p>
        {/* the layer in full for a screen reader: the hint says the default to the eye */}
        {!locked && <p class="visually-hidden">{layerNote(f, origins)}</p>}
        {bad && <p class="field-error" role="alert">{bad}</p>}
      </div>
      <div class="set-control">{control}</div>
      {keyOf(f) === "location" && osmNamed(settings) && current === stored && <OsmCredit />}
    </div>
  );
  if (!f.under) return row;
  return (
    <div class="set-fold" data-shut={folded ? "true" : "false"} {...(folded ? { inert: true } : {})}>
      <div class="set-fold-inner">{row}</div>
    </div>
  );
}

const GEO_WORDS: Record<number, string> = {
  1: "Location is blocked for this site. Allow it in the browser's site settings, or type the place above.",
  2: "This device couldn't work out where it is. Type the place above instead.",
  3: "Finding your location took too long. Try again, or type the place above.",
};

type Locating = { at: "idle" } | { at: "finding" } | { at: "saving" } | { at: "done"; got: Located } | { at: "failed"; why: string | ApiError };

/** Whether this browser can be asked for its position at all: the API exists only in a secure context. */
export function canLocate(): boolean {
  return typeof navigator !== "undefined" && "geolocation" in navigator && (window.isSecureContext ?? true);
}

/**
 * "Use my location": ask the browser where the device is, once, and send the position to the server, which names
 * the place and its time zone and saves them the way a typed place is saved (POST /settings/location). The
 * position goes nowhere else: it is not kept on the page or in storage. A refusal, a timeout or a lookup that fails
 * is said in a line, with the typed place still there to use.
 */
export function UseMyLocation({ locked, onSaved }: { locked: boolean; onSaved: () => void }) {
  const [state, setState] = useState<Locating>({ at: "idle" });
  const supported = canLocate();
  const busy = state.at === "finding" || state.at === "saving";
  const go = () => {
    if (!isOnline()) {
      setState({ at: "failed", why: "You're offline: the place can't be looked up until you're back." });
      return;
    }
    setState({ at: "finding" });
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setState({ at: "saving" });
        locate(pos.coords.latitude, pos.coords.longitude)
          .then((got) => {
            setState({ at: "done", got });
            onSaved();
          })
          .catch((e: unknown) => setState({ at: "failed", why: asApiError(e) }));
      },
      (err) => setState({ at: "failed", why: GEO_WORDS[err.code] ?? "Your location isn't available just now. Type the place instead." }),
      { enableHighAccuracy: false, timeout: 15_000, maximumAge: 10 * 60_000 },
    );
  };
  return (
    <div class="set-row locate">
      <div class="set-lab">
        <p class="set-label">Where you are</p>
        {supported ? (
          <p class="set-hint">Asks this browser once and looks the place up; the position itself is not kept.</p>
        ) : (
          <p class="hint">This browser can't share its location here; type the place instead.</p>
        )}
        {state.at === "done" && (
          <p class="saved" role="status">
            Now in {state.got.location}
            {state.got.timezone ? ` · ${state.got.timezone}` : ""}
            {state.got.local_time_now ? ` · ${state.got.local_time_now} there` : ""}.
          </p>
        )}
        {state.at === "failed" &&
          (typeof state.why === "string" ? <p class="field-error" role="alert">{state.why}</p> : <ErrorLine error={state.why} />)}
      </div>
      {supported && (
        <div class="set-control">
          <Button size="small" icon={<Pin />} disabled={locked || busy} onClick={go}>
            {state.at === "finding" ? "Finding you…" : state.at === "saving" ? "Saving…" : "Use my location"}
          </Button>
        </div>
      )}
    </div>
  );
}

/** The fields of one group, in their card (with Use my location after the place's). */
function Group({ id, settings, origins, profile, edited, setField, onLocated }: {
  id: GroupId; settings: Record<string, Json>; origins: Record<string, string>; profile?: string;
  edited: Record<string, string>; setField: (key: string, v: string | undefined) => void; onLocated: () => void;
}) {
  const placeLocked = ["location", "timezone", "coordinates"].some((k) => (origins[k] ?? "default") === "hermes" || origins[k] === "env");
  const flagOn = (key: string) => {
    const f = FIELDS.find((x) => keyOf(x) === key);
    if (!f) return true;
    const v = edited[key] ?? shown(f, valueAt(settings, f.path));
    return v === "on" || (v === "" && f.def !== false);
  };
  return (
    <div class="set-group">
      {FIELDS.filter((f) => f.group === id).map((f) => {
        const key = keyOf(f);
        const edit = edited[key];
        const folded = !!f.under && !flagOn(f.under) && problem(f, edit ?? shown(f, valueAt(settings, f.path))) === null;
        return <Row key={key} f={f} settings={settings} origins={origins} profile={profile} edit={edit} folded={folded} onInput={(v) => setField(key, v)} />;
      })}
      {id === "place" && <UseMyLocation locked={placeLocked} onSaved={onLocated} />}
    </div>
  );
}

// ── 08 health: the checks, the versions, everything as read ──────────────

function HealthCheck({ res, reload }: { res: Resource<DoctorRow[]>; reload: () => void }) {
  const rows = res.state === "ready" ? res.data : [];
  const sorted = [...rows].sort((a, b) => ({ fail: 0, warn: 1, ok: 2 })[statusOf(a.status)] - ({ fail: 0, warn: 1, ok: 2 })[statusOf(b.status)]);
  const c = count(rows);
  const head = (
    <span class="health-head">
      <span class="health-marks" aria-hidden="true">{sorted.map((r, i) => <HealthMark key={i} status={r.status} />)}</span>
      <span class="health-words">
        <b>{res.state === "ready" ? `${healthLine(c)}.` : res.state === "error" ? "The health check didn't answer." : "Checking…"}</b>
        <span class="set-hint">
          Health check
          {res.state === "ready" && ` · ${c.ok} ${c.ok === 1 ? "check" : "checks"} fine${c.warn ? `, ${c.warn} to look at` : ""}${c.fail ? `, ${c.fail} not working` : ""}`}
        </span>
      </span>
    </span>
  );
  const firstOk = sorted.findIndex((r) => statusOf(r.status) === "ok");
  return (
    <Disclosure class="set-group health" headClass="health-sum" head={head}>
      <Await res={res} reload={reload} rows={2}>
        {(list) => list.length === 0 ? <p class="set-hint health-none">The server listed no checks.</p> : (
          <ul class="doctor">
            {sorted.map((r, i) => (
              <Fragment key={`${r.check}-${i}`}>
                {i === firstOk && i > 0 && <li class="health-sep" aria-hidden="true">Working as it should</li>}
                <li class={`check ${statusOf(r.status)}`}>
                  <HealthMark status={r.status} />
                  <b class="check-name">{r.check}</b>
                  <span class="check-status">{STATUS_WORD[statusOf(r.status)]}</span>
                  <span class="check-detail">{r.detail}</span>
                </li>
              </Fragment>
            ))}
          </ul>
        )}
      </Await>
    </Disclosure>
  );
}

function Versions() {
  const [res, reload] = useResource(async (signal) => {
    const [health, status] = await Promise.all([api.health(signal), api.status(signal)]);
    return { health, status };
  }, []);
  useLive(["tick", "library-changed"], reload);   // versions, warnings and the next sends change with each tick and schedule edit
  if (res.state !== "ready") {
    return (
      <div class="set-group set-versions">
        <div class="set-row"><div class="set-lab"><p class="set-label">Versions</p></div></div>
        <Await res={res} reload={reload} rows={1}>{() => null}</Await>
      </div>
    );
  }
  const { health, status } = res.data;
  const tick = status.last_tick;
  const legacy = !!tick?.at && !tick.code;   // a tick from before version stamps: it ran, its code is unknown
  const differs = !!tick?.code && (tick.code !== health.code || tick.version !== health.version);
  const notes = status.warnings.length + status.recent_errors.length;
  return (
    <>
      {legacy && (
        <p class="warning" role="alert">
          The last tick predates version stamps, so its code cannot be compared; the next tick records its own.
        </p>
      )}
      {differs && (
        <p class="warning" role="alert">
          The server and the tick run different code: install both at one commit (see DEPLOY.md).
        </p>
      )}
      <Disclosure class="set-group set-versions" head="Versions"
        peek={`${health.version}${differs ? " · the tick runs other code" : legacy ? "" : tick?.code ? " · the server and the tick agree" : ""}${notes ? ` · ${notes} ${notes === 1 ? "warning" : "warnings"}` : ""}`}>
        <dl class="kv">
          <div><dt>Server</dt><dd>{health.version} <code>{health.code}</code></dd></div>
          <div><dt>Tick</dt><dd>{tick?.version ? <>{tick.version} <code>{tick.code}</code></> : legacy ? "ran before versions were stamped" : "has not run yet"}</dd></div>
          <div><dt>Last tick</dt><dd>{tick?.at ?? health.tick_last_at ?? "never"}</dd></div>
          <div><dt>Time</dt><dd>{status.now_local} · {status.timezone}</dd></div>
        </dl>
        {Object.keys(status.next).length > 0 && (
          <Disclosure class="set-sub" head="Next sends">
            <dl class="kv">
              {Object.entries(status.next).map(([id, when]) => (
                <div key={id}><dt>{id}</dt><dd>{when}</dd></div>
              ))}
            </dl>
          </Disclosure>
        )}
        {notes > 0 && (
          <Disclosure class="set-sub" head={`Warnings and recent errors (${notes})`}>
            <ul class="set-notes">
              {[...status.warnings, ...status.recent_errors].map((w, i) => (
                <li key={i}>{typeof w === "string" ? w : JSON.stringify(w)}</li>
              ))}
            </ul>
          </Disclosure>
        )}
      </Disclosure>
    </>
  );
}

function Everything({ settings, origins }: { settings: Record<string, Json>; origins: Record<string, string> }) {
  return (
    <Disclosure class="set-group diagnostics" head="Everything, as the server reads it" peek="Read-only; each group says where it comes from">
      <div class="settings-groups">
        {groupSettings(settings).map((g) => (
          <Disclosure key={g.key} class="settings-group" head={<span class="settings-group-head"><span class="key">{g.key}</span><span class="origin">{ORIGIN_WORDS[origins[g.key] ?? "default"] ?? origins[g.key]}</span></span>}>
            <dl class="kv">
              {g.rows.map(([k, v]) => (
                <div key={k}><dt>{k}</dt><dd>{v}</dd></div>
              ))}
            </dl>
          </Disclosure>
        ))}
      </div>
    </Disclosure>
  );
}

// ── the save bar ─────────────────────────────────────────────────────────

const changesWords = (n: number) => (n === 1 ? "1 change not saved" : `${n} changes not saved`);

/**
 * The page's foot while something is changed and not saved: a frosted bar docked across the foot of the page, rising
 * on the soft spring, "2 changes not saved · Discard · Save". It is the page's last child (outside the form; Save
 * names the form) and sticks to the foot of the view (above the tab bar on a phone): mid-page the rows pass under its
 * backing, never under a floating control, and at the bottom of the page it sits under the last row. Hidden, it keeps
 * its place (its height is the page's last padding) and leaves the tab order and the accessibility tree.
 */
function SaveBar({ changes, busy, invalid, done, error, onDiscard }: {
  changes: number; busy: boolean; invalid: boolean; done: string | null; error: ApiError | null; onDiscard: () => void;
}) {
  const shown = changes > 0 || !!done || !!error;
  return (
    <div class="save-dock" data-shown={shown ? "true" : "false"}>
      {error && (
        <div class="error-line save-error" role="alert">
          <p>{error.detail}</p>
          {error.problems.length > 0 && <ul>{error.problems.map((p) => <li key={p}>{p}</li>)}</ul>}
        </div>
      )}
      <div class={`save-bar${changes > 0 ? " dirty" : ""}`} {...(shown ? {} : { inert: true, "aria-hidden": "true" })}>
        {done && changes === 0 ? (
          <p class="saved" role="status">{done}</p>
        ) : (
          <>
            <span class="save-count" aria-live="polite">{changes === 0 ? "No changes" : changesWords(changes)}</span>
            <Button variant="quiet" size="small" disabled={busy || changes === 0} onClick={onDiscard}>Discard</Button>
            <Button type="submit" form="settings-form" variant="primary" size="small" disabled={busy || invalid || changes === 0}>
              {busy ? "Saving…" : "Save"}
            </Button>
          </>
        )}
      </div>
    </div>
  );
}

// ── the page ─────────────────────────────────────────────────────────────

function Glance({ health, who, ink, go }: { health: Resource<DoctorRow[]>; who: Session | null | undefined; ink: Ink; go: (id: string) => void }) {
  const c = health.state === "ready" ? count(health.data) : null;
  const worst = c ? (c.fail ? "fail" : c.warn ? "warn" : "ok") : null;
  const inkInfo = INKS.find((i) => i.id === ink) ?? INKS[0];
  const signIn = who === undefined ? ["Asking the server…", "How this browser signs in"]
    : who === null ? ["Couldn't confirm the sign-in", "The server didn't answer"]
    : byTailscale(who) ? ["Signed in by Tailscale", who.login ?? ""]
    : ["Paired with this browser", "Until you unpair it"];
  const item = (id: string, mark: ComponentChildren, title: ComponentChildren, hint: ComponentChildren) => (
    <a href={`#${id}`} onClick={(e) => { e.preventDefault(); go(id); }}>
      <span class="glance-mark" aria-hidden="true">{mark}</span>
      <span><b>{title}</b><span class="set-hint">{hint}</span></span>
    </a>
  );
  return (
    <aside class="glance" aria-label="At a glance">
      <p class="kicker">At a glance</p>
      {item("set-health", worst ? <HealthMark status={worst} /> : <i class="hm" />,
        c ? healthLine(c) : health.state === "error" ? "Health unknown" : "Checking…",
        c ? `Health check, ${c.ok + c.warn + c.fail} ${c.ok + c.warn + c.fail === 1 ? "check" : "checks"}` : "Health check")}
      {item("set-device", <span class="glance-seal" />, signIn[0], signIn[1])}
      {item("set-ink", <span class="glance-ink" />, "Her ink", `${inkInfo?.label}, ${inkInfo?.note}`)}
    </aside>
  );
}

export function SettingsRoute() {
  useTitle("Settings");
  const [res, reload] = useResource((signal) => api.settings(signal), []);
  const [doctor, reloadDoctor] = useResource((signal) => api.doctor(signal), []);
  useLive(["tick", "library-changed"], reloadDoctor);   // its rows read the library's files and the tick's version
  const [her] = useResource(async (signal) => (await api.todayQuiet(signal).catch(() => null))?.voices?.hers ?? null, []);
  const sign = useSignIn();
  const [ink, setInk] = useState<Ink>(savedInk);
  const [current, go] = useCurrentSection(SECTIONS.map((s) => s.id));
  const [edited, setEdited] = useState<Record<string, string>>({});
  const [done, setDone] = useState<string | null>(null);
  const act = useAction();

  // a link from elsewhere (/settings#set-health) opens at its section once the page has drawn
  useEffect(() => {
    const id = window.location.hash.slice(1);
    if (id && SECTIONS.some((s) => s.id === id)) setTimeout(() => go(id), 0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  // "Saved: …" says what happened, then gives the foot back
  useEffect(() => {
    if (!done) return;
    const t = setTimeout(() => setDone(null), 6000);
    return () => clearTimeout(t);
  }, [done]);

  const ready = res.state === "ready" ? res.data : null;
  const settings = ready?.settings ?? {};
  const value = (f: SettingField) => edited[keyOf(f)] ?? shown(f, valueAt(settings, f.path));
  const patch = ready ? buildPatch(settings, edited) : {};
  // what the bar counts is fields changed (the patch nests them under their top-level keys)
  const changes = ready ? FIELDS.filter((f) => Object.keys(buildPatch(settings, { [keyOf(f)]: edited[keyOf(f)] ?? "" })).length > 0).length : 0;
  const invalid = FIELDS.some((f) => problem(f, value(f)) !== null);
  const setField = (key: string, v: string | undefined) => {
    setEdited((now) => {
      const next = { ...now };
      if (v === undefined) delete next[key];
      else next[key] = v;
      return next;
    });
    setDone(null);
    act.clear();
  };
  const save = async () => {
    if (!ready || changes === 0 || invalid) return;
    setDone(null);
    const out = await act.run(() => api.patchSettings(patch));
    if (out) {
      setEdited({});
      setDone(`Saved: ${Object.entries(out.written).map(([key, layer]) => `${key} (${ORIGIN_WORDS[layer] ?? layer})`).join(", ")}.`);
      reload();
    }
  };
  const onLocated = () => {
    // what was found is saved: a place or time zone typed before and not saved must not win at the next Save
    setEdited((now) => Object.fromEntries(Object.entries(now).filter(([k]) => k !== "location" && k !== "timezone")));
    setDone(null);
    reload();
  };
  const health = doctor.state === "ready" ? count(doctor.data) : null;

  return (
    <div class="settings-page">
      <header class="set-head">
        <div>
          <h1 class="display">Settings</h1>
          <p class="lede">How the journal keeps time, when {her.state === "ready" && her.data ? her.data : "she"} writes back, and how her words look on this device.</p>
        </div>
        <Glance health={doctor} who={sign.who} ink={ink} go={go} />
      </header>
      <div class="set-grid">
        <SectionIndex current={current} go={go} healthMark={!!health && (health.warn > 0 || health.fail > 0)} />
        <form id="settings-form" class="settings-form" onSubmit={(e) => { e.preventDefault(); void save(); }}>
          <Section id="set-ink" n={1} title="Ink and page" lede="The colour her words are typed in, and the page they sit on. Both change at once, on this device only.">
            <InkChoice from={her.state === "ready" && her.data ? her.data : "the companion"} onChoose={setInk} />
            <div class="set-group">
              <div class="set-row set-theme">
                <div class="set-lab">
                  <p class="set-label" id="theme-label">Page</p>
                  <p class="set-hint">Warm paper by day, the night room after dark, or as this device is set.</p>
                </div>
                <div class="set-control"><ThemeChoice /></div>
              </div>
            </div>
          </Section>
          {GROUPS.map((g, i) => (
            <Section key={g.id} id={`set-${g.id}`} n={i + 2} title={g.title} lede={g.note || undefined}>
              {ready ? (
                <Group id={g.id} settings={ready.settings} origins={ready.origins} profile={ready.profile} edited={edited} setField={setField} onLocated={onLocated} />
              ) : res.state === "error" ? (
                i === 0 ? <Await res={res} reload={reload}>{() => null}</Await> : <p class="set-hint">Not read: see above.</p>
              ) : (
                <Skeleton rows={i === 0 ? 3 : 2} />
              )}
            </Section>
          ))}
          <Section id="set-device" n={7} title="This device" lede="How this browser reaches the journal, and the look of the app, part by part.">
            <div class="set-group">
              <Pairing who={sign.who} again={sign.again} />
              <div class="set-row">
                <div class="set-lab">
                  <p class="set-label">Design system</p>
                  <p class="set-hint">Every part of the app's look, on one page.</p>
                </div>
                <div class="set-control"><ButtonLink size="small" href="/design" data-vt="push">Open it</ButtonLink></div>
              </div>
            </div>
          </Section>
          <Section id="set-health" n={8} title="Health" lede="What the server checks about itself, which code it runs, and every setting as it reads them.">
            <HealthCheck res={doctor} reload={reloadDoctor} />
            <Versions />
            {ready && <Everything settings={ready.settings} origins={ready.origins} />}
          </Section>
        </form>
      </div>
      <SaveBar changes={changes} busy={act.busy} invalid={invalid} done={done} error={act.error}
        onDiscard={() => { setEdited({}); act.clear(); }} />
    </div>
  );
}
