import type { ComponentChildren } from "preact";
import { useLocation } from "preact-iso";
import { useEffect, useRef, useState } from "preact/hooks";
import { askSession, byTailscale, pair, session, type ApiError } from "../api";
import { setAuthLost } from "../auth";
import { ErrorLine } from "../components/Status";
import { Mark } from "../icons";
import { asApiError } from "../hooks";
import { useTitle } from "../title";

/** The code in `/pair#<code>`, or "" (also accepts a whole pairing URL pasted into the field). */
export function codeFrom(text: string): string {
  const t = text.trim();
  const hash = t.indexOf("#");
  return (hash >= 0 ? t.slice(hash + 1) : t).trim();
}

/**
 * Pairing: `journal serve pair` prints a link that ends in `#<one-time code>`; opening it trades the code for the
 * session cookie. The code is taken from the address and removed from it at once (it is single-use, but it has no
 * business staying in the history). The address is read as a code only on `/pair`. A code can also be pasted, as the fallback when the link was copied by hand.
 * On `/pair` the server is asked first how this browser is signed in: by its Tailscale identity, there is nothing to
 * pair, so the page goes to Today and the code is not spent.
 */
export function PairPanel({ title = "Pair this browser", intro, readHash = false }: { title?: string; intro?: ComponentChildren; readHash?: boolean }) {
  const loc = useLocation();
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const [text, setText] = useState("");
  const started = useRef(false);
  const tried = useRef("");   // the code in the last attempt, kept in memory only so a failed read can be retried

  const redeem = (raw: string) => {
    const code = codeFrom(raw);
    if (!code) {
      setError(asApiError(new Error("Paste the pairing link that `journal serve pair` printed.")));
      return;
    }
    tried.current = code;
    setBusy(true);
    setError(null);
    pair(code)
      .then(() => loc.route("/", true))
      .catch((e: unknown) => {
        setError(asApiError(e));
        setBusy(false);
      });
  };

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    if (!readHash) return;   // on any other page the hash is a link target (/insights#<stem>), never a pairing code
    const code = window.location.hash.slice(1);
    if (code) window.history.replaceState(null, "", window.location.pathname);
    const ctl = new AbortController();
    session(ctl.signal).then((s) => {
      if (ctl.signal.aborted) return;
      if (byTailscale(s)) loc.route("/", true);
      else if (code) redeem(code);
    });
    return () => ctl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <main class="pair-screen">
      <Mark class="pair-mark" size={44} />
      <h1>{title}</h1>
      <p class="pair-intro">{intro ?? <>Open the link that <code>journal serve pair</code> printed on the server. It works once, for ten minutes.</>}</p>
      {busy && <p role="status">Pairing…</p>}
      {error && <ErrorLine error={error} onRetry={error.unpaired || !tried.current ? undefined : () => redeem(tried.current)} />}
      <PairForm text={text} setText={setText} busy={busy} onPair={() => redeem(text)} />
    </main>
  );
}

function PairForm({ text, setText, busy, onPair }: { text: string; setText: (t: string) => void; busy: boolean; onPair: () => void }) {
  return (
    <form
      class="pair-form"
      onSubmit={(e) => {
        e.preventDefault();
        onPair();
      }}
    >
      <label for="pair-code">Pairing link or code</label>
      <input id="pair-code" type="text" autocomplete="off" autocapitalize="off" spellcheck={false} value={text}
        onInput={(e) => setText((e.target as HTMLInputElement).value)} />
      <button type="submit" class="button primary" disabled={busy}>
        Pair
      </button>
    </form>
  );
}

/**
 * A read was refused while this browser signs in by its Tailscale identity (the last `GET /session` said so). There
 * is nothing to pair on the tailnet, so the pairing form is not the screen. The server is asked once, on arrival and
 * on each Retry, and the screen says what it answered:
 * - it refuses this device (401): it no longer recognises it by Tailscale, so retrying will not help; the pairing fold
 *   opens, for a device that should now sign in another way;
 * - it cannot be reached (no network, a restart): Retry, which is the thing that helps;
 * - it recognises the device: on Retry the views read again. Only Retry does that, never the arrival check, so a read
 *   refused again comes back here instead of looping.
 */
export function TailscaleLost() {
  const [state, setState] = useState<"asking" | "ok" | "refused" | "unreachable">("asking");
  const ask = (andRead: boolean) => {
    setState("asking");
    askSession().then((a) => {
      if ("session" in a && byTailscale(a.session)) {
        if (andRead) setAuthLost(false);
        else setState("ok");
      } else setState("refused" in a || "session" in a ? "refused" : "unreachable");
    });
  };
  useEffect(() => ask(false), []);
  const refused = state === "refused";
  return (
    <main class="pair-screen tailscale-lost">
      <Mark class="pair-mark" size={44} />
      <h1>{refused ? "This device isn't recognised by Tailscale any more" : "Couldn't confirm your Tailscale sign-in"}</h1>
      {refused ? (
        <p class="pair-intro">
          The journal server answered, and no longer signs this device in by its Tailscale identity: it may be signed in to
          another Tailscale account, or no longer allowed. Retrying won't change that. Pair it with a link below, or sign in
          to the right tailnet and retry.
        </p>
      ) : (
        <p class="pair-intro">
          This browser signs in to the journal with its Tailscale identity, and the server didn't recognise it just now.
          Check that Tailscale is connected on this device, then retry.
        </p>
      )}
      <button type="button" class={`button wide${refused ? "" : " primary"}`} disabled={state === "asking"} onClick={() => ask(true)}>
        {state === "asking" ? "Checking…" : "Retry"}
      </button>
      {state === "unreachable" && (
        <p class="error-line" role="alert">
          The journal server didn't answer. It may be restarting, or this device is offline: try again in a moment.
        </p>
      )}
      <PairFallback open={refused} />
    </main>
  );
}

function PairFallback({ open }: { open: boolean }) {
  const loc = useLocation();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  return (
    <details class="pair-fallback" open={open}>
      <summary>Reaching the journal another way? Pair with a link</summary>
      {error && <ErrorLine error={error} />}
      <PairForm text={text} setText={setText} busy={busy} onPair={() => {
        const code = codeFrom(text);
        if (!code) return;
        setBusy(true);
        setError(null);
        pair(code)
          .then(() => loc.route("/", true))
          .catch((e: unknown) => {
            setError(asApiError(e));
            setBusy(false);
          });
      }} />
    </details>
  );
}

export function PairRoute() {
  useTitle("Pair");
  return <PairPanel readHash />;
}
