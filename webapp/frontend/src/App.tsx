import { useEffect, useState } from "react";
import { NavLink, Route, Routes, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { api } from "./api/client";
import CommandSearch from "./components/CommandSearch";
import { ErrorNote } from "./components/bits";
import ExploreHome from "./pages/ExploreHome";
import ConceptPage from "./pages/ConceptPage";
import AskPage from "./pages/AskPage";
import ResultsPage from "./pages/ResultsPage";

function Mark() {
  return (
    <svg width="26" height="26" viewBox="0 0 32 32" aria-hidden>
      <rect width="32" height="32" rx="7" fill="#14213D" />
      <circle cx="16" cy="8" r="3" fill="#fff" /><circle cx="9" cy="22" r="3" fill="#fff" /><circle cx="23" cy="22" r="3" fill="#0E8A6A" />
      <path d="M16 11v4M16 15l-6 4.5M16 15l6 4.5" stroke="#fff" strokeWidth="1.8" fill="none" strokeLinecap="round" />
    </svg>
  );
}

const NAV = [
  { to: "/", label: "Explore", end: false, match: (p: string) => p === "/" || p.startsWith("/concept") },
  { to: "/ask", label: "Ask and verify", end: true, match: (p: string) => p.startsWith("/ask") },
  { to: "/results", label: "Results", end: true, match: (p: string) => p.startsWith("/results") },
];

function Loading({ seconds }: { seconds: number }) {
  return (
    <div className="grid min-h-[70vh] place-items-center px-4">
      <div className="w-full max-w-md">
        <div className="text-lg font-semibold">Loading SNOMED CT</div>
        <p className="mt-1 text-ink-3">
          Reading 351,751 concepts and 574,430 IS_A links from snomed.db into memory. This takes about 40 seconds, once per start.
        </p>
        <div className="mt-5 h-1 overflow-hidden rounded-full bg-wash">
          <div className="h-full rounded-full bg-ink transition-[width] duration-1000 ease-out" style={{ width: `${Math.min(95, (seconds / 45) * 100)}%` }} />
        </div>
        <div className="mt-2 text-xs text-ink-3 tnum">{seconds} s</div>
      </div>
    </div>
  );
}

export default function App() {
  const [searchOpen, setSearchOpen] = useState(false);
  const { pathname } = useLocation();
  const status = useQuery({
    queryKey: ["status"], queryFn: api.status, staleTime: 0,
    refetchInterval: (q) => (q.state.data?.ready || q.state.data?.error ? false : 1000),
  });

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = e.target instanceof HTMLElement && (e.target.isContentEditable || /INPUT|TEXTAREA|SELECT/.test(e.target.tagName));
      if ((e.key === "k" && (e.metaKey || e.ctrlKey)) || (e.key === "/" && !typing)) {
        e.preventDefault();
        setSearchOpen((o) => !o);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => { window.scrollTo(0, 0); }, [pathname]);

  const s = status.data;
  return (
    <div className="min-h-screen">
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-3 focus:top-3 focus:z-50 focus:rounded focus:bg-surface focus:px-3 focus:py-2">Skip to content</a>
      <header className="sticky top-0 z-30 border-b border-rule bg-paper/85 backdrop-blur-md">
        <div className="mx-auto flex h-14 max-w-[1440px] items-center gap-3 px-4 sm:gap-6 sm:px-6">
          <NavLink to="/" className="flex shrink-0 items-center gap-2.5 rounded-md" aria-label="SNOMED CT Explorer, home">
            <Mark />
            <span className="hidden font-semibold tracking-tight md:inline">SNOMED CT Explorer</span>
          </NavLink>
          <nav className="flex min-w-0 items-center gap-0.5 overflow-x-auto" aria-label="Main">
            {NAV.map((n) => {
              const on = n.match(pathname);
              return (
                <NavLink key={n.to} to={n.to} end={n.end} aria-current={on ? "page" : undefined}
                  className={`whitespace-nowrap rounded-md px-2.5 py-1.5 text-sm transition-colors sm:px-3 ${on ? "bg-surface font-medium text-ink shadow-node ring-1 ring-rule" : "text-ink-3 hover:text-ink"}`}>
                  {n.label}
                </NavLink>
              );
            })}
          </nav>
          <button
            type="button" onClick={() => setSearchOpen(true)} disabled={!s?.ready}
            className="ml-auto flex h-9 shrink-0 items-center gap-2 rounded-lg border border-rule bg-surface px-2.5 text-sm text-ink-3 hover:border-rule-strong disabled:opacity-50 sm:w-64 sm:px-3"
            aria-label="Search concepts"
          >
            <Search size={15} />
            <span className="hidden sm:inline">Search concepts</span>
            <kbd className="ml-auto hidden rounded border border-rule px-1.5 text-[11px] sm:inline">Ctrl K</kbd>
          </button>
        </div>
      </header>

      <main id="main">
        {status.isError && (
          <div className="mx-auto max-w-xl px-4 py-16">
            <ErrorNote title="The server is not answering." detail="Start it with: python -m webapp.server" />
          </div>
        )}
        {s?.error && (
          <div className="mx-auto max-w-xl px-4 py-16"><ErrorNote title="The database could not be loaded." detail={s.error} /></div>
        )}
        {s && !s.ready && !s.error && <Loading seconds={s.loading_seconds} />}
        {s?.ready && (
          <Routes>
            <Route path="/" element={<ExploreHome status={s} />} />
            <Route path="/concept/:id" element={<ConceptPage />} />
            <Route path="/ask" element={<AskPage status={s} />} />
            <Route path="/results" element={<ResultsPage />} />
            <Route path="*" element={<div className="mx-auto max-w-xl px-4 py-16"><ErrorNote title="This page does not exist." detail="Use the menu above." /></div>} />
          </Routes>
        )}
      </main>
      {s?.ready && <CommandSearch open={searchOpen} onOpenChange={setSearchOpen} />}
    </div>
  );
}
