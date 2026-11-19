import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { GitCompareArrows, Loader2, Send } from "lucide-react";
import { api, type AskResult, type Ref, type Status } from "../api/client";
import ConceptPicker from "../components/ConceptPicker";
import ResultView from "../components/ResultView";
import { Dot, ErrorNote, OutcomeBadge, Segmented } from "../components/bits";
import { LABEL_TONE, LEGEND, SYSTEM_NAME, TONE_HEX } from "../utils/labels";
import { useTitle } from "../utils/hooks";

const SYSTEM_ORDER = ["G-QA", "G-QA+V", "L-RAG", "D-RAG", "B0"];
const SHORT_HELP: Record<string, string> = {
  "G-QA": "Follows exactly the IS_A links the question needs.",
  "G-QA+V": "The same, then checks every claim and retries once.",
  "L-RAG": "Keyword search over concept descriptions.",
  "D-RAG": "Meaning-based search over concept descriptions.",
  B0: "No evidence. The model answers from memory.",
};

function useRefById(id: string | null) {
  return useQuery({
    queryKey: ["ref", id], enabled: !!id,
    queryFn: async (): Promise<Ref | null> => (await api.search(id!, 1))[0] ?? null,
  });
}

function Stacked({ r }: { r: AskResult }) {
  const counts = LEGEND.map((l) => ({ ...l, n: r.claims.filter((c) => (LABEL_TONE[c.label] ?? "neutral") === l.tone).length }));
  const total = r.claims.length;
  if (!total) return <span className="text-xs text-ink-4">no claims</span>;
  return (
    <span className="flex w-full max-w-[160px] items-center gap-2">
      <span className="flex h-2 flex-1 overflow-hidden rounded-full bg-wash" aria-label={counts.map((c) => `${c.n} ${c.title.toLowerCase()}`).join(", ")}>
        {counts.map((c) => c.n > 0 && <span key={c.tone} style={{ width: `${(c.n / total) * 100}%`, background: TONE_HEX[c.tone] }} />)}
      </span>
      <span className="text-xs text-ink-3 tnum">{total}</span>
    </span>
  );
}

function CompareTable({ results, skipped, selected, onSelect }: {
  results: AskResult[]; skipped: { system: string; reason: string }[]; selected: string; onSelect: (s: string) => void;
}) {
  return (
    <div className="overflow-hidden rounded-xl border border-rule bg-surface">
      <div role="tablist" aria-label="Systems" className="divide-y divide-rule">
        {results.map((r) => {
          const on = r.system === selected;
          return (
            <button key={r.system} role="tab" aria-selected={on} type="button" onClick={() => onSelect(r.system)}
              className={`grid w-full grid-cols-[minmax(0,1.3fr)_auto] items-center gap-x-4 gap-y-2 px-4 py-3 text-left transition-colors sm:grid-cols-[minmax(0,1.3fr)_140px_minmax(0,1fr)_90px] ${on ? "bg-wash" : "hover:bg-paper"}`}>
              <span className="min-w-0">
                <span className="block font-medium">{SYSTEM_NAME[r.system]}</span>
                <span className="block text-xs text-ink-3">{r.system}</span>
              </span>
              <span><OutcomeBadge outcome={r.outcome} /></span>
              <span className="col-span-2 sm:col-span-1"><Stacked r={r} /></span>
              <span className="hidden text-right text-xs text-ink-3 tnum sm:block">{r.evidence.edges.length ? `${r.evidence.edges.length} links` : r.system === "B0" ? "no evidence" : `${r.evidence.text.length} chars`}</span>
            </button>
          );
        })}
      </div>
      {skipped.map((s) => (
        <div key={s.system} className="border-t border-rule px-4 py-2.5 text-xs text-ink-3">{SYSTEM_NAME[s.system]} skipped: {s.reason}</div>
      ))}
    </div>
  );
}

function EmptyState() {
  return (
    <div className="rounded-xl border border-dashed border-rule-strong px-6 py-10 sm:px-10">
      <h2 className="text-lg font-semibold">Each claim gets a colour</h2>
      <p className="mt-2 max-w-[60ch] text-ink-3">
        The model has to back its answer with claims such as “X is a direct child of Y”. Every claim is then checked against
        the evidence it was given and against the full release.
      </p>
      <ul className="mt-6 grid gap-4 sm:grid-cols-2">
        {LEGEND.map((l) => (
          <li key={l.tone} className="flex gap-3">
            <span className="mt-1.5"><Dot color={TONE_HEX[l.tone]} /></span>
            <span><span className="font-medium">{l.title}</span><span className="block text-sm text-ink-3">{l.text}</span></span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function AskPage({ status }: { status: Status }) {
  useTitle("Ask and verify");
  const [params, setParams] = useSearchParams();
  const task = params.get("task") ?? "T1";
  const xId = params.get("x");
  const yId = params.get("y");
  const [system, setSystem] = useState(params.get("system") ?? "G-QA");
  const [withheld, setWithheld] = useState(Number(params.get("withheld") ?? 0));
  const [marked, setMarked] = useState(true);
  const [model, setModel] = useState(status.reference_model);
  const [selected, setSelected] = useState("G-QA");
  const x = useRefById(xId);
  const y = useRefById(yId);
  const taskInfo = status.tasks.find((t) => t.id === task) ?? status.tasks[0];
  const maskable = system === "G-QA" || system === "G-QA+V";

  const set = (k: string, v: string | null) => setParams((p) => { const n = new URLSearchParams(p); v ? n.set(k, v) : n.delete(k); return n; }, { replace: true });

  const ask = useMutation({ mutationFn: api.ask });
  const compare = useMutation({ mutationFn: api.compare });
  const [mode, setMode] = useState<"one" | "all" | null>(null);

  useEffect(() => { if (!maskable) setWithheld(0); }, [maskable]);

  const ready = !!xId && (!taskInfo.needs_y || !!yId);
  const busy = ask.isPending || compare.isPending;
  const run = (which: "one" | "all") => {
    setMode(which);
    if (which === "one") ask.mutate({ task, x: xId!, y: taskInfo.needs_y ? yId : null, system, withheld, marked, model });
    else compare.mutate({ task, x: xId!, y: taskInfo.needs_y ? yId : null, model });
  };
  const error = mode === "one" ? ask.error : mode === "all" ? compare.error : null;
  const compareSel = compare.data?.results.find((r) => r.system === selected) ?? compare.data?.results[0];

  return (
    <div className="mx-auto max-w-[1440px] px-4 pb-24 pt-8 sm:px-6">
      <div className="grid gap-10 lg:grid-cols-[380px_minmax(0,1fr)]">
        <form className="space-y-7"
          onSubmit={(e) => { e.preventDefault(); if (ready) run("one"); }} aria-label="Question">
          <div>
            <h1 className="text-xl font-semibold">Ask and verify</h1>
            <p className="mt-1 text-sm text-ink-3">Same pipeline as the study, on the same release.</p>
          </div>

          <fieldset>
            <legend className="field-label">Question</legend>
            <div className="space-y-1" role="radiogroup">
              {status.tasks.map((t) => (
                <label key={t.id} className={`flex cursor-pointer items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-focus ${task === t.id ? "bg-surface font-medium ring-1 ring-ink/70" : "hover:bg-wash"}`}>
                  <input type="radio" name="task" value={t.id} checked={task === t.id} onChange={() => set("task", t.id)} className="sr-only" />
                  <span className={`h-3.5 w-3.5 shrink-0 rounded-full border ${task === t.id ? "border-[4px] border-ink" : "border-ink-4"}`} aria-hidden />
                  {t.text}
                </label>
              ))}
            </div>
          </fieldset>

          <div className="space-y-4">
            <ConceptPicker id="x" label="Concept X" value={x.data ?? null} onChange={(c) => set("x", c?.id ?? null)} />
            {taskInfo.needs_y && <ConceptPicker id="y" label="Concept Y" value={y.data ?? null} onChange={(c) => set("y", c?.id ?? null)} />}
          </div>

          <fieldset>
            <legend className="field-label">Evidence the model sees</legend>
            <div className="space-y-1" role="radiogroup">
              {SYSTEM_ORDER.map((s) => {
                const disabled = s === "D-RAG" && !status.dense_available;
                return (
                  <label key={s} className={`flex items-start gap-3 rounded-lg px-3 py-2 text-sm transition-colors has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-focus ${disabled ? "cursor-not-allowed opacity-45" : "cursor-pointer"} ${system === s ? "bg-surface ring-1 ring-ink/70" : !disabled ? "hover:bg-wash" : ""}`}>
                    <input type="radio" name="system" value={s} checked={system === s} disabled={disabled} onChange={() => setSystem(s)} className="sr-only" />
                    <span className={`mt-[3px] h-3.5 w-3.5 shrink-0 rounded-full border ${system === s ? "border-[4px] border-ink" : "border-ink-4"}`} aria-hidden />
                    <span><span className="font-medium">{SYSTEM_NAME[s]}</span><span className="block text-ink-3">{disabled ? "Needs the exported vectors in data/snomed_db/dense." : SHORT_HELP[s]}</span></span>
                  </label>
                );
              })}
            </div>
          </fieldset>

          <div className={`space-y-3 ${maskable ? "" : "opacity-50"}`}>
            <div>
              <span className="field-label" id="withheld-l">Withhold links the answer needs</span>
              <Segmented label="Withhold links the answer needs" value={withheld} onChange={setWithheld} disabled={!maskable}
                options={[0, 25, 50, 75].map((v) => ({ value: v, label: `${v}%` }))} />
            </div>
            <label className="flex cursor-pointer items-center gap-3 text-sm">
              <button type="button" role="switch" aria-checked={marked} disabled={!maskable} onClick={() => setMarked((m) => !m)}
                className={`relative h-5 w-9 shrink-0 rounded-full transition-colors ${marked ? "bg-ink" : "bg-rule-strong"}`}>
                <span className={`absolute left-0 top-0.5 h-4 w-4 rounded-full bg-white transition-transform ${marked ? "translate-x-[18px]" : "translate-x-0.5"}`} />
              </button>
              Tell the model which lists are complete
            </label>
            {!maskable && <p className="text-xs text-ink-3">Only graph evidence can be withheld.</p>}
          </div>

          <div>
            <label htmlFor="model" className="field-label">Answered by</label>
            <select id="model" value={model} onChange={(e) => setModel(e.target.value)}
              className="h-10 w-full rounded-lg border border-rule bg-surface px-3 text-sm outline-none focus:border-ink">
              {status.models.map((m) => <option key={m} value={m}>{m === status.reference_model ? "Reference answerer (no model, free)" : m}</option>)}
            </select>
            <p className="mt-1.5 text-xs text-ink-3">
              {model === status.reference_model ? "Answers strictly from the evidence and declines when it is incomplete. Uses no quota."
                : "A new question uses one or two calls of the free daily quota. Answers already cached are replayed for free."}
            </p>
          </div>

          <div className="flex flex-wrap gap-2 border-t border-rule pt-5">
            <button type="submit" className="btn-primary flex-1" disabled={!ready || busy}>
              {ask.isPending ? <Loader2 size={16} className="animate-spin" /> : <Send size={15} />}Ask
            </button>
            <button type="button" className="btn-quiet flex-1" disabled={!ready || busy} onClick={() => run("all")}>
              {compare.isPending ? <Loader2 size={16} className="animate-spin" /> : <GitCompareArrows size={15} />}Compare all systems
            </button>
          </div>
        </form>

        <div className="min-w-0" aria-live="polite">
          {error && <div className="mb-6"><ErrorNote title="That question could not be asked." detail={(error as Error).message} /></div>}
          {busy && (
            <div className="space-y-3 rounded-xl border border-rule bg-surface p-6">
              <div className="flex items-center gap-2 text-sm text-ink-2"><Loader2 size={16} className="animate-spin" />
                {compare.isPending ? "Asking all five systems" : "Retrieving evidence and asking"}{model !== status.reference_model ? ", this can take 10 to 30 seconds" : ""}</div>
              <div className="h-1 overflow-hidden rounded-full bg-wash"><div className="h-full w-1/3 animate-pulseline rounded-full bg-ink/60" /></div>
            </div>
          )}
          {!busy && mode === "one" && ask.data && <ResultView r={ask.data} />}
          {!busy && mode === "all" && compare.data && (
            <div className="space-y-8">
              <section aria-labelledby="cmp-h">
                <h2 id="cmp-h" className="mb-1 text-md font-semibold">Five systems, one question</h2>
                <p className="mb-3 text-sm text-ink-3">Select a row to see that answer in full.</p>
                <CompareTable results={compare.data.results} skipped={compare.data.skipped} selected={compareSel?.system ?? ""} onSelect={setSelected} />
              </section>
              {compareSel && <ResultView r={compareSel} />}
            </div>
          )}
          {!busy && !mode && <EmptyState />}
        </div>
      </div>
    </div>
  );
}
