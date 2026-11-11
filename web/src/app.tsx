import { ErrorBoundary, LocationProvider, Route, Router, lazy, useLocation } from "preact-iso";
import { useEffect, useLayoutEffect, useRef, useState } from "preact/hooks";
import { session } from "./api";
import { useAuthLost, useLastVia } from "./auth";
import { UpdateToast } from "./components/UpdateToast";
import { WriteRoom } from "./components/WriteRoom";
import { Entries, Insights, Library, Mark, Pen, Plus, Settings, Today } from "./icons";
import { forgetReads } from "./offline";
import { EditorRoute, NewPracticeRoute } from "./routes/editor";
import { EntriesRoute } from "./routes/entries";
import { EntryRoute } from "./routes/entry";
import { InsightsRoute } from "./routes/insights";
import { LibraryDefaultRoute, LibraryDetailRoute, LibraryRoute } from "./routes/library";
import { PairPanel, PairRoute, TailscaleLost } from "./routes/pair";
import { RunRoute } from "./routes/run";
import { SettingsRoute } from "./routes/settings";
import { TodayRoute } from "./routes/today";
import { useShellState } from "./shell/mode";
import { NextFrom, useNext } from "./shell/NextFrom";
import { useLinkTransitions } from "./shell/transition";
import { openWrite, useWriteRequest } from "./shell/write";
import "./shell/shell.css";
import { useTitle } from "./title";
import { OfflineRibbon } from "./ui/OfflineRibbon";
import { ThemeToggle } from "./ui/ThemeChoice";
import { ButtonLink } from "./ui/Button";

// the style guide is its own chunk: nothing on the everyday pages pays for it
const DesignRoute = lazy(() => import("./routes/design").then((m) => m.DesignRoute));

const NAV = [
  { href: "/", label: "Today", Icon: Today, match: (p: string) => p === "/" || p === "/today" },
  { href: "/entries", label: "Entries", Icon: Entries, match: (p: string) => p.startsWith("/entries") },
  { href: "/library", label: "Library", Icon: Library, match: (p: string) => p.startsWith("/library") || p === "/new-practice" || p.startsWith("/runs") },
  { href: "/insights", label: "Insights", Icon: Insights, match: (p: string) => p.startsWith("/insights") },
];

/** The rail's (and the phone's) wide-screen test: the rail and its reads exist from 761 px. */
const WIDE = "(min-width: 761px)";
function useWide(): boolean {
  const [wide, setWide] = useState(() => typeof matchMedia === "function" && matchMedia(WIDE).matches);
  useEffect(() => {
    if (typeof matchMedia !== "function") return;
    const mq = matchMedia(WIDE);
    const on = () => setWide(mq.matches);
    mq.addEventListener?.("change", on);
    return () => mq.removeEventListener?.("change", on);
  }, []);
  return wide;
}

function NavLink({ n, path, badge, folded }: { n: (typeof NAV)[number]; path: string; badge?: string; folded: boolean }) {
  return (
    <a class="nav-item" href={n.href} aria-current={n.match(path) ? "page" : undefined} title={folded ? n.label : undefined}>
      <n.Icon class="nav-icon" width={20} height={20} />
      <span class="nav-label">{n.label}</span>
      {badge && <span class="nav-badge">{badge}</span>}
    </a>
  );
}

function NotFound() {
  useTitle("Not found");
  return (
    <div class="not-found">
      <p class="kicker">Nothing at this address</p>
      <h1 class="display">Not here</h1>
      <p class="lede">There is no page at this address. It may have moved, or the link was cut short.</p>
      <ButtonLink href="/" data-vt="pop">Back to Today</ButtonLink>
    </div>
  );
}

/** N opens the write surface, from anywhere but a field (or a dialog, a composition, or with a modifier), and only
 *  in the app itself: not on the pairing screens, where a room would wait to open after pairing. */
function useWriteKey(enabled: boolean): void {
  const on = useRef(enabled);
  on.current = enabled;
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (e.key !== "n" && e.key !== "N") return;
      if (!on.current || e.isComposing) return;
      if (e.metaKey || e.ctrlKey || e.altKey || e.defaultPrevented) return;
      const t = e.target as HTMLElement | null;
      if (t?.closest?.("input, textarea, select, [contenteditable], [role=dialog], dialog")) return;
      e.preventDefault();
      openWrite({ from: document.querySelector<HTMLElement>(".rail-write") });
    };
    document.addEventListener("keydown", key);
    return () => document.removeEventListener("keydown", key);
  }, []);
}

function Shell() {
  const { path } = useLocation();
  const lost = useAuthLost();
  const via = useLastVia();
  const writing = useWriteRequest();
  const mode = useShellState();
  const wide = useWide();
  const next = useNext(wide && !lost);
  const main = useRef<HTMLElement>(null);
  useLinkTransitions();
  useWriteKey(path !== "/pair" && !lost);
  // the ribbon describes the view on screen: a new view starts without it (layout effects run before any read starts);
  // and a new page starts at its top (a filter or a #hash on the same page keeps its place)
  useLayoutEffect(() => {
    forgetReads();
    if (!location.hash) {
      if (main.current) main.current.scrollTop = 0;
      if (document.scrollingElement) document.scrollingElement.scrollTop = 0;
    }
  }, [path]);
  // learn once how this browser is signed in, so a refused read later leads to the screen that can help (auth.ts)
  useEffect(() => {
    const ctl = new AbortController();
    void session(ctl.signal);
    return () => ctl.abort();
  }, []);
  if (path === "/pair") return <PairRoute />;
  if (lost && via === "tailscale") return <TailscaleLost />;
  if (lost) {
    return (
      <PairPanel
        title="Not paired"
        intro={<>This browser is not paired with the journal server. Open the link that <code>journal serve pair</code> printed, or paste it below.</>}
      />
    );
  }
  const folded = mode.fold;
  const waiting = next?.waiting ? `${next.waiting} waiting` : undefined;
  const settingsHere = path.startsWith("/settings");
  return (
    <div class="shell" data-rail={folded ? "compact" : "full"} data-stage={mode.panes ? "panes" : "pane"}>
      <a class="skip-link" href="#main">Skip to the page</a>
      <header class="phone-top">
        <a class="brand" href="/">
          <Mark class="phone-mark" size={26} />
          <span class="wordmark">journal</span>
        </a>
        <ThemeToggle />
        <a class="icon-button" href="/settings" aria-label="Settings" title="Settings" aria-current={settingsHere ? "page" : undefined}>
          <Settings />
        </a>
      </header>
      <aside class="rail" aria-label="journal">
        <a class="brand" href="/" title={folded ? "journal" : undefined}>
          <Mark class="brand-mark" size={30} />
          <span class="wordmark">journal</span>
        </a>
        <button type="button" class="button primary rail-write" aria-keyshortcuts="N" title={folded ? "Write" : undefined}
          onClick={(e) => openWrite({ from: e.currentTarget })}>
          <Pen />
          <span class="nav-label">Write</span>
          <kbd aria-hidden="true">N</kbd>
        </button>
        <nav class="nav" aria-label="Main">
          {NAV.slice(0, 2).map((n) => (
            <NavLink key={n.href} n={n} path={path} folded={folded} badge={n.href === "/" ? waiting : undefined} />
          ))}
          <button type="button" class="plus" aria-haspopup="dialog" onClick={(e) => openWrite({ from: e.currentTarget })}>
            <Plus width={24} height={24} stroke-width={2} />
            <span class="plus-label">Write</span>
          </button>
          {NAV.slice(2).map((n) => (
            <NavLink key={n.href} n={n} path={path} folded={folded} />
          ))}
        </nav>
        <div class="rail-spacer" />
        <NextFrom next={folded ? null : next} />
        <div class="rail-foot">
          <a class="nav-item gear" href="/settings" aria-current={settingsHere ? "page" : undefined} title={folded ? "Settings" : undefined}>
            <Settings class="nav-icon" width={20} height={20} />
            <span class="nav-label">Settings</span>
          </a>
          <ThemeToggle />
        </div>
      </aside>
      <div class="stage">
        <main id="main" ref={main} class="pane main-pane" data-vt-page tabIndex={-1}>
          <div class="lamp-light" aria-hidden="true" />
          <OfflineRibbon />
          <ErrorBoundary>
            <Router>
              <Route path="/" component={TodayRoute} />
              {/* (Today's other name: /today?write=<id> opens a practice to write, the link a notification will carry) */}
              <Route path="/today" component={TodayRoute} />
              <Route path="/entries" component={EntriesRoute} />
              <Route path="/entries/:stem" component={EntryRoute} />
              <Route path="/library" component={LibraryRoute} />
              <Route path="/library/:id" component={LibraryDetailRoute} />
              <Route path="/library/:id/edit" component={EditorRoute} />
              <Route path="/library/:id/default" component={LibraryDefaultRoute} />
              <Route path="/new-practice" component={NewPracticeRoute} />
              <Route path="/runs/:id" component={RunRoute} />
              <Route path="/insights" component={InsightsRoute} />
              <Route path="/settings" component={SettingsRoute} />
              <Route path="/design" component={DesignRoute} />
              <Route default component={NotFound} />
            </Router>
          </ErrorBoundary>
        </main>
      </div>
      {/* the write room, while something asked for it (shell/write.ts is its seam) */}
      {writing && <WriteRoom req={writing} />}
      <UpdateToast />
    </div>
  );
}

export function App() {
  return (
    <LocationProvider>
      <Shell />
    </LocationProvider>
  );
}
