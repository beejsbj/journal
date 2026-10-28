import { useEffect, useState } from "preact/hooks";
import { useLive } from "../events";
import { ApiError, api, byTailscale, locate, logout, session } from "../api";
import { lastVia } from "../auth";
import { OsmCredit, osmNamed } from "../components/OsmCredit";
import { Await, ErrorLine } from "../components/Status";
import { asApiError, useAction, useResource } from "../hooks";
import { Pin } from "../icons";
import { INKS, chooseInk, savedInk, type Ink } from "../ink";
import { isOnline } from "../offline";
import { FIELDS, GROUPS, buildPatch, keyOf, problem, shown, valueAt, type SettingField } from "../settingsModel";
import { useTitle } from "../title";
import type { Json } from "../schema";
import type { Located, Session } from "../types";

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

/** This browser: signed in by its Tailscale identity (nothing to pair or unpair), else paired, with Unpair. Until
 *  the server has said which, a quiet line. When it cannot say (offline, the server restarting), the page says so
 *  rather than claim a pairing it has not seen; Unpair is offered then only if the browser was last known paired. */
function Pairing() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
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
  const out = () => {
    setBusy(true);
    setError(null);
    logout().catch((e: unknown) => {
      setError(asApiError(e));
      setBusy(false);
    });
  };
  const unpair = (
    <button type="button" class="button" disabled={busy} onClick={out}>
      Unpair
    </button>
  );
  let body;
  if (who === undefined) {
    body = <p class="hint" role="status">Asking the server…</p>;
  } else if (byTailscale(who)) {
    body = (
      <p>
        Signed in via Tailscale as <strong>{who.login}</strong>. On the tailnet there is nothing to pair.
      </p>
    );
  } else if (who === null) {
    const paired = lastVia() === "cookie" || lastVia() === "token";
    body = (
      <>
        <p>
          Couldn't confirm how this browser is signed in: the server didn't answer
          {isOnline() ? "" : " (you're offline)"}.
        </p>
        <div class="actions">
          <button type="button" class="button" onClick={() => setAsked(asked + 1)}>
            Check again
          </button>
          {paired && unpair}
        </div>
      </>
    );
  } else {
    body = (
      <>
        <p>
          Paired as <strong>{window.location.host}</strong>. Pairing lasts until you unpair or the server's token is rotated.
        </p>
        {unpair}
      </>
    );
  }
  return (
    <section class="settings-section" aria-labelledby="pair-h">
      <h2 id="pair-h">This browser</h2>
      <div class="panel">
        {body}
        {error && <ErrorLine error={error} />}
      </div>
    </section>
  );
}

/**
 * Her ink, chosen on this device: four inks, each shown as a line of a letter typed in it, in this light or dark. The
 * choice takes effect at once and is kept in this browser only (ink.ts); nothing is sent to the server, and nothing is
 * read for it either (a read here would mark the page as read from the kept copy when offline).
 */
export function InkChoice() {
  const [ink, setInk] = useState<Ink>(savedInk);
  return (
    <section class="settings-section" aria-labelledby="ink-h">
      <h2 id="ink-h">Ink</h2>
      <p class="section-note">The colour the companion's words are typed in. It changes at once, on this device only.</p>
      <fieldset class="inks">
        <legend class="visually-hidden">The companion's ink</legend>
        {INKS.map((i) => (
          <label key={i.id} class={`ink${ink === i.id ? " on" : ""}`} data-ink={i.id}>
            <input type="radio" name="ink" value={i.id} checked={ink === i.id}
              onChange={() => {
                chooseInk(i.id);
                setInk(i.id);
              }} />
            <span class="ink-name">
              {i.label} <span class="ink-note">{i.note}</span>
            </span>
            <span class="ink-sample" aria-hidden="true">
              <span class="ink-from">From the companion</span>
              <span class="ink-words">How did today go?</span>
            </span>
          </label>
        ))}
      </fieldset>
    </section>
  );
}

function Version() {
  const [res, reload] = useResource(async (signal) => {
    const [health, status] = await Promise.all([api.health(signal), api.status(signal)]);
    return { health, status };
  }, []);
  useLive(["tick", "library-changed"], reload);   // versions, warnings and the next sends change with each tick and schedule edit
  return (
    <section class="settings-section" aria-labelledby="ver-h">
      <h2 id="ver-h">Versions</h2>
      <Await res={res} reload={reload} rows={1}>
        {({ health, status }) => {
          const tick = status.last_tick;
          const legacy = !!tick?.at && !tick.code;   // a tick from before version stamps: it ran, its code is unknown
          const differs = !!tick?.code && (tick.code !== health.code || tick.version !== health.version);
          return (
            <>
              <dl class="kv">
                <div><dt>Server</dt><dd>{health.version} <code>{health.code}</code></dd></div>
                <div><dt>Tick</dt><dd>{tick?.version ? <>{tick.version} <code>{tick.code}</code></> : legacy ? "ran before versions were stamped" : "has not run yet"}</dd></div>
                <div><dt>Last tick</dt><dd>{tick?.at ?? health.tick_last_at ?? "never"}</dd></div>
                <div><dt>Time</dt><dd>{status.now_local} · {status.timezone}</dd></div>
              </dl>
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
              {Object.keys(status.next).length > 0 && (
                <details>
                  <summary>Next sends</summary>
                  <dl class="kv">
                    {Object.entries(status.next).map(([id, when]) => (
                      <div key={id}><dt>{id}</dt><dd>{when}</dd></div>
                    ))}
                  </dl>
                </details>
              )}
              {(status.warnings.length > 0 || status.recent_errors.length > 0) && (
                <details>
                  <summary>Warnings and recent errors ({status.warnings.length + status.recent_errors.length})</summary>
                  <ul class="plain-list">
                    {[...status.warnings, ...status.recent_errors].map((w, i) => (
                      <li key={i}>{typeof w === "string" ? w : JSON.stringify(w)}</li>
                    ))}
                  </ul>
                </details>
              )}
            </>
          );
        }}
      </Await>
    </section>
  );
}

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

/** The layer a field is in, as a short tag beside its label. */
function layerTag(f: SettingField, origins: Record<string, string>): string {
  const origin = origins[f.path[0] as string] ?? "default";
  return origin === "file" ? "config file" : origin === "hermes" ? "Hermes" : origin === "env" ? "environment" : "default";
}

function Row({ f, settings, origins, profile, value, onInput }: {
  f: SettingField; settings: Record<string, Json>; origins: Record<string, string>; profile?: string; value: string; onInput: (v: string) => void;
}) {
  const id = `s-${keyOf(f)}`;
  const locked = lockedWhy(f, origins, profile);
  const bad = problem(f, value);
  const stored = shown(f, valueAt(settings, f.path));
  return (
    <div class="field setting">
      <div class="label-row">
        <label for={id}>{f.label}</label>
        <span class="tag" aria-hidden="true">{layerTag(f, origins)}</span>
      </div>
      {f.kind === "flag" ? (
        <select id={id} value={value} disabled={locked !== null} onChange={(e) => onInput((e.currentTarget as HTMLSelectElement).value)}>
          <option value="">Default</option>
          <option value="on">On</option>
          <option value="off">Off</option>
        </select>
      ) : (
        <input
          id={id}
          type={f.kind === "clock" ? "time" : f.kind === "text" ? "text" : "number"}
          {...(f.kind === "number" || f.kind === "whole" ? { step: f.kind === "whole" ? 1 : "any", min: f.min, max: f.max, inputMode: f.kind === "whole" ? "numeric" : "decimal" } : {})}
          value={value}
          placeholder={stored === "" ? "default" : undefined}
          disabled={locked !== null}
          onInput={(e) => onInput((e.currentTarget as HTMLInputElement).value)}
        />
      )}
      {keyOf(f) === "location" && osmNamed(settings) && value === stored && <OsmCredit />}
      {/* the layer in full: a short tag beside the label for the eye, the whole sentence for a screen reader */}
      {locked ? <p class="hint layer">{locked}</p> : <p class="visually-hidden">{layerNote(f, origins)}</p>}
      {!locked && f.hint && <p class="hint">{f.hint}</p>}
      {bad && <p class="field-error" role="alert">{bad}</p>}
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
  if (!canLocate()) {
    return <p class="hint">This browser can't share its location here; type the place instead.</p>;
  }
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
    <div class="locate">
      <button type="button" class="button" disabled={locked || busy} onClick={go}>
        <Pin width={18} height={18} />
        {state.at === "finding" ? "Finding you…" : state.at === "saving" ? "Saving…" : "Use my location"}
      </button>
      {state.at === "done" && (
        <p class="saved" role="status">
          Now in {state.got.location}
          {state.got.timezone ? ` · ${state.got.timezone}` : ""}
          {state.got.local_time_now ? ` · ${state.got.local_time_now} there` : ""}.
        </p>
      )}
      {state.at === "failed" &&
        (typeof state.why === "string" ? (
          <p class="field-error" role="alert">{state.why}</p>
        ) : (
          <ErrorLine error={state.why} />
        ))}
    </div>
  );
}

/** The settings the server lets the web app change, in their groups. Only what was changed is sent; a key owned by
 *  Hermes or the environment is shown locked, with the line that changes it. While something is changed and not
 *  saved, the Save bar stays in reach at the foot of the screen. */
function SettingsEditor({ settings, origins, profile, reload }: { settings: Record<string, Json>; origins: Record<string, string>; profile?: string; reload: () => void }) {
  const [edited, setEdited] = useState<Record<string, string>>({});
  const [done, setDone] = useState<string | null>(null);
  const act = useAction();
  const value = (f: SettingField) => edited[keyOf(f)] ?? shown(f, valueAt(settings, f.path));
  const patch = buildPatch(settings, edited);
  const changes = Object.keys(patch).length;
  const invalid = FIELDS.some((f) => problem(f, value(f)) !== null);
  const save = async () => {
    setDone(null);
    const out = await act.run(() => api.patchSettings(patch));
    if (out) {
      setEdited({});
      setDone(`Saved: ${Object.entries(out.written).map(([key, layer]) => `${key} (${ORIGIN_WORDS[layer] ?? layer})`).join(", ")}.`);
      reload();
    }
  };
  const placeLocked = ["location", "timezone", "coordinates"].some((k) => (origins[k] ?? "default") === "hermes" || origins[k] === "env");
  return (
    <form class="settings-form" onSubmit={(e) => { e.preventDefault(); void save(); }}>
      {GROUPS.map((g) => (
        <section key={g.id} class="settings-section" aria-labelledby={`grp-${g.id}`}>
          <h2 id={`grp-${g.id}`}>{g.title}</h2>
          {g.note && <p class="section-note">{g.note}</p>}
          <div class="panel">
            {FIELDS.filter((f) => f.group === g.id).map((f) => (
              <Row key={keyOf(f)} f={f} settings={settings} origins={origins} profile={profile} value={value(f)} onInput={(v) => { setEdited({ ...edited, [keyOf(f)]: v }); setDone(null); act.clear(); }} />
            ))}
            {g.id === "place" && (
              <UseMyLocation
                locked={placeLocked}
                onSaved={() => {
                  // what was found is saved: a place or time zone typed before and not saved must not win at the next Save
                  setEdited((now) => Object.fromEntries(Object.entries(now).filter(([k]) => k !== "location" && k !== "timezone")));
                  setDone(null);
                  reload();
                }}
              />
            )}
          </div>
        </section>
      ))}
      {act.error && (
        <div class="error-line" role="alert">
          <p>{act.error.detail}</p>
          {act.error.problems.length > 0 && <ul>{act.error.problems.map((p) => <li key={p}>{p}</li>)}</ul>}
        </div>
      )}
      {done && <p class="saved" role="status">{done}</p>}
      <div class={`save-bar${changes > 0 ? " dirty" : ""}`}>
        <span class="save-count">{changes === 0 ? "No changes" : changes === 1 ? "1 change" : `${changes} changes`}</span>
        <button type="submit" class="button primary" disabled={act.busy || invalid || changes === 0}>
          {act.busy ? "Saving…" : "Save settings"}
        </button>
      </div>
    </form>
  );
}

function SettingsTable() {
  const [res, reload] = useResource((signal) => api.settings(signal), []);
  return (
    <Await res={res} reload={reload} rows={3}>
      {({ settings, origins, profile }) => (
        <>
          <SettingsEditor settings={settings} origins={origins} profile={profile} reload={reload} />
          <details class="settings-section diagnostics">
            <summary>
              <h2 id="set-h">Everything, as the server reads it</h2>
            </summary>
            <p class="section-note">Read-only. Each group shows where it comes from.</p>
            <div class="settings-groups">
              {groupSettings(settings).map((g) => (
                <details key={g.key} class="settings-group">
                  <summary>
                    <span class="key">{g.key}</span>
                    <span class="origin">{ORIGIN_WORDS[origins[g.key] ?? "default"] ?? origins[g.key]}</span>
                  </summary>
                  <dl class="kv">
                    {g.rows.map(([k, v]) => (
                      <div key={k}><dt>{k}</dt><dd>{v}</dd></div>
                    ))}
                  </dl>
                </details>
              ))}
            </div>
          </details>
        </>
      )}
    </Await>
  );
}

function Doctor() {
  const [res, reload] = useResource((signal) => api.doctor(signal), []);
  useLive(["tick", "library-changed"], reload);   // its rows read the library's files and the tick's version
  return (
    <details class="settings-section diagnostics">
      <summary>
        <h2 id="doc-h">Health check</h2>
      </summary>
      <Await res={res} reload={reload} rows={2}>
        {(rows) => (
          <table class="doctor">
            <thead>
              <tr><th scope="col">Check</th><th scope="col">Status</th><th scope="col">Detail</th></tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.check} class={r.status}>
                  <th scope="row">{r.check}</th>
                  <td><span class={`pill ${r.status}`}>{r.status}</span></td>
                  <td>{r.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Await>
    </details>
  );
}

export function SettingsRoute() {
  useTitle("Settings");
  return (
    <div class="page settings">
      <header class="page-head">
        <p class="eyebrow">This install</p>
        <h1>Settings</h1>
      </header>
      <Pairing />
      <InkChoice />
      <SettingsTable />
      <Version />
      <Doctor />
    </div>
  );
}
