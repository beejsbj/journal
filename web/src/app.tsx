import { ErrorBoundary, LocationProvider, Route, Router, useLocation } from "preact-iso";
import { useEffect, useLayoutEffect, useState } from "preact/hooks";
import { session } from "./api";
import { useAuthLost, useLastVia } from "./auth";
import { OfflineRibbon } from "./components/OfflineRibbon";
import { QuickAdd } from "./components/QuickAdd";
import { UpdateToast } from "./components/UpdateToast";
import { Book, Gear, Mark, Plus, Shelf, Spark, Sun } from "./icons";
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
import { useTitle } from "./title";

const NAV = [
  { href: "/", label: "Today", Icon: Sun, match: (p: string) => p === "/" },
  { href: "/entries", label: "Entries", Icon: Book, match: (p: string) => p.startsWith("/entries") },
  { href: "/library", label: "Library", Icon: Shelf, match: (p: string) => p.startsWith("/library") || p === "/new-practice" || p.startsWith("/runs") },
  { href: "/insights", label: "Insights", Icon: Spark, match: (p: string) => p.startsWith("/insights") },
];

function NavLink({ n, path }: { n: (typeof NAV)[number]; path: string }) {
  return (
    <a href={n.href} aria-current={n.match(path) ? "page" : undefined}>
      <n.Icon class="nav-icon" width={22} height={22} />
      <span>{n.label}</span>
    </a>
  );
}

function NotFound() {
  useTitle("Not found");
  return (
    <div class="page">
      <header class="page-head">
        <h1>Not here</h1>
      </header>
      <p>
        There is no page at this address. <a href="/">Back to Today</a>
      </p>
    </div>
  );
}

function Shell() {
  const { path } = useLocation();
  const lost = useAuthLost();
  const via = useLastVia();
  const [adding, setAdding] = useState(false);
  // the ribbon describes the view on screen: a new view starts without it (layout effects run before any read starts)
  useLayoutEffect(() => forgetReads(), [path]);
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
  return (
    <div class="shell">
      <header class="topbar">
        <a class="brand" href="/">
          <Mark class="brand-mark" size={26} />
          <span class="wordmark">journal</span>
        </a>
        <nav class="nav" aria-label="Main">
          {NAV.slice(0, 2).map((n) => (
            <NavLink key={n.href} n={n} path={path} />
          ))}
          <button type="button" class="plus" aria-haspopup="dialog" onClick={() => setAdding(true)}>
            <Plus width={22} height={22} stroke-width={2} />
            <span class="plus-label">Write</span>
          </button>
          {NAV.slice(2).map((n) => (
            <NavLink key={n.href} n={n} path={path} />
          ))}
        </nav>
        <a class="gear" href="/settings" aria-label="Settings" aria-current={path.startsWith("/settings") ? "page" : undefined}>
          <Gear />
        </a>
      </header>
      <OfflineRibbon />
      <main id="main">
        <ErrorBoundary>
          <Router>
            <Route path="/" component={TodayRoute} />
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
            <Route default component={NotFound} />
          </Router>
        </ErrorBoundary>
      </main>
      {adding && <QuickAdd onClose={() => setAdding(false)} />}
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
