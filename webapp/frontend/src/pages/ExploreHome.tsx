import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight, Search } from "lucide-react";
import { Command } from "cmdk";
import { api, type Status } from "../api/client";
import { fmtInt } from "../utils/labels";
import { useDebounced, useTitle } from "../utils/hooks";

const STARTERS = [
  { id: "22298006", name: "Myocardial infarction" },
  { id: "74400008", name: "Appendicitis" },
  { id: "44054006", name: "Diabetes mellitus type 2" },
  { id: "195967001", name: "Asthma" },
];

const QUESTIONS = [
  { text: "What are the direct parents of myocardial infarction?", q: "task=T1&x=22298006" },
  { text: "Is appendicitis a kind of clinical finding?", q: "task=T3&x=74400008&y=404684003" },
  { text: "What do myocardial infarction and asthma have in common?", q: "task=T6&x=22298006&y=195967001" },
];

function HeroSearch() {
  const [text, setText] = useState("");
  const q = useDebounced(text.trim(), 150);
  const navigate = useNavigate();
  const { data = [], isFetching } = useQuery({ queryKey: ["search", q, 7], queryFn: () => api.search(q, 7), enabled: q.length > 1 });
  const showList = q.length > 1;
  return (
    <Command shouldFilter={false} loop label="Search concepts" className="relative">
      <div className="flex h-14 items-center gap-3 rounded-xl border border-rule-strong bg-surface px-4 shadow-node focus-within:border-ink focus-within:ring-4 focus-within:ring-focus/10">
        <Search size={20} className="shrink-0 text-ink-3" />
        <Command.Input
          value={text} onValueChange={setText} autoFocus aria-label="Search concepts"
          placeholder="Search by name, synonym or concept ID"
          className="h-full w-full bg-transparent text-md outline-none placeholder:text-ink-4"
        />
        {isFetching && <span className="h-1.5 w-1.5 animate-pulseline rounded-full bg-ink-3" aria-hidden />}
      </div>
      {showList && (
        <Command.List className="absolute left-0 right-0 top-[calc(100%+8px)] z-20 max-h-96 overflow-auto rounded-xl border border-rule bg-surface p-1.5 shadow-pop">
          {!isFetching && data.length === 0 && <Command.Empty className="px-3 py-4 text-sm text-ink-3">No concept matches “{q}”.</Command.Empty>}
          {data.map((h) => (
            <Command.Item key={h.id} value={h.id} onSelect={() => navigate(`/concept/${h.id}`)}
              className="flex cursor-pointer items-center justify-between gap-3 rounded-lg px-3 py-2.5">
              <span className="min-w-0">
                <span className="block truncate font-medium">{h.name}</span>
                <span className="block truncate text-xs text-ink-3">{h.tag}{h.hierarchy && h.hierarchy.id !== h.id ? `, in ${h.hierarchy.name}` : ""}</span>
              </span>
              <span className="sctid shrink-0">{h.id}</span>
            </Command.Item>
          ))}
        </Command.List>
      )}
    </Command>
  );
}

export default function ExploreHome({ status }: { status: Status }) {
  useTitle("");
  const hierarchies = [...(status.hierarchies ?? [])].sort((a, b) => (b.size ?? 0) - (a.size ?? 0));
  const max = Math.max(1, ...hierarchies.map((h) => h.size ?? 0));
  return (
    <div className="mx-auto max-w-[1180px] px-4 pb-24 sm:px-6">
      <section className="grid gap-10 pb-14 pt-12 sm:pt-20 lg:grid-cols-[minmax(0,1fr)_300px] lg:gap-16">
        <div className="max-w-[680px]">
          <h1 className="text-2xl font-semibold sm:text-3xl sm:[text-wrap:balance] lg:whitespace-nowrap">Walk the SNOMED CT hierarchy</h1>
          <p className="mt-4 max-w-[54ch] text-md text-ink-3">
            Look up any clinical concept, see every IS_A link above and below it, and check how a language model answers
            questions about it, claim by claim.
          </p>
          <div className="mt-8"><HeroSearch /></div>
          <div className="mt-4 flex flex-wrap items-center gap-x-1 gap-y-2 text-sm text-ink-3">
            <span className="mr-1">Start with</span>
            {STARTERS.map((s) => (
              <Link key={s.id} to={`/concept/${s.id}`} className="rounded-md border border-rule bg-surface px-2.5 py-1 text-ink-2 hover:border-ink hover:text-ink">
                {s.name}
              </Link>
            ))}
          </div>
        </div>
        <dl className="grid grid-cols-2 gap-x-6 gap-y-5 self-end border-t border-rule pt-6 text-sm lg:grid-cols-1 lg:border-l lg:border-t-0 lg:pl-8 lg:pt-0">
          <div><dt className="text-ink-3">Release</dt><dd className="mt-0.5 font-medium">International Edition, 1 February 2026</dd></div>
          <div><dt className="text-ink-3">View</dt><dd className="mt-0.5 font-medium">International concepts only</dd></div>
          <div><dt className="text-ink-3">Concepts</dt><dd className="mt-0.5 text-lg font-semibold tnum">{fmtInt(status.concepts)}</dd></div>
          <div><dt className="text-ink-3">IS_A links</dt><dd className="mt-0.5 text-lg font-semibold tnum">{fmtInt(status.edges)}</dd></div>
        </dl>
      </section>

      <section aria-labelledby="hier" className="border-t border-rule pt-10">
        <div className="mb-5 flex flex-wrap items-baseline justify-between gap-2">
          <h2 id="hier" className="text-lg font-semibold">Top-level hierarchies</h2>
          <p className="text-sm text-ink-3">Bar length is the number of concepts below each one.</p>
        </div>
        <ul className="grid gap-x-12 md:grid-cols-2">
          {hierarchies.map((h) => (
            <li key={h.id}>
              <Link to={`/concept/${h.id}`} className="group grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 border-b border-rule py-2.5">
                <span className="truncate text-[14.5px] group-hover:text-focus">{h.name}</span>
                <span className="text-sm text-ink-3 tnum">{fmtInt(h.size)}</span>
                <span className="col-span-2 mt-1.5 h-1 overflow-hidden rounded-full bg-wash">
                  <span className="block h-full rounded-full bg-ink/80 transition-colors group-hover:bg-focus" style={{ width: `${Math.max(0.6, ((h.size ?? 0) / max) * 100)}%` }} />
                </span>
              </Link>
            </li>
          ))}
        </ul>
      </section>

      <section aria-labelledby="try" className="mt-16 grid gap-8 lg:grid-cols-[300px_minmax(0,1fr)] lg:gap-16">
        <div>
          <h2 id="try" className="text-lg font-semibold">Ask and verify</h2>
          <p className="mt-2 text-ink-3">
            Pick a question, choose what evidence the model sees, and watch each claim in its answer get checked against the release.
          </p>
        </div>
        <ul className="divide-y divide-rule border-y border-rule">
          {QUESTIONS.map((q) => (
            <li key={q.q}>
              <Link to={`/ask?${q.q}`} className="group flex items-center justify-between gap-4 py-4 text-md hover:text-focus">
                {q.text}
                <ArrowUpRight size={18} className="shrink-0 text-ink-4 group-hover:text-focus" />
              </Link>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
