import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type Stage } from "../api/client";
import { ErrorNote, Segmented, Skeleton } from "../components/bits";
import { fmtInt, fmtRate, SYSTEM_NAME, TONE_HEX } from "../utils/labels";
import { useTitle } from "../utils/hooks";

const MEASURES: { key: string; label: string; hint: string; good: "high" | "low" }[] = [
  { key: "score", label: "Score", hint: "Mean answer score against the release (1 is fully correct).", good: "high" },
  { key: "abstention_rate", label: "Declined", hint: "Share of questions the model declined to answer.", good: "low" },
  { key: "unsupported_claim_rate", label: "Claims not in evidence", hint: "Share of claims the evidence does not state.", good: "low" },
  { key: "false_claim_rate", label: "False claims", hint: "Share of claims that are false in SNOMED CT.", good: "low" },
  { key: "withheld_restated_rate", label: "Withheld facts restated", hint: "Of the needed links withheld from the model, the share it still named.", good: "low" },
  { key: "refusal_f1", label: "Refusal F1", hint: "How well declining matches the evidence actually being incomplete.", good: "high" },
];

function label(condition: string) {
  const [base, ...rest] = condition.split(/(?=[@:])/);
  const parts = [SYSTEM_NAME[base] ?? base];
  rest.forEach((r) => {
    if (r.startsWith("@")) parts.push(`${r.slice(1)}% withheld`);
    if (r === ":random") parts.push("random links removed");
    if (r === ":unmarked") parts.push("no completeness marks");
  });
  return { main: parts[0], sub: parts.slice(1).join(", "), code: condition };
}

function Bar({ v, good }: { v: number; good: "high" | "low" }) {
  return (
    <span className="mt-1 block h-1 w-full overflow-hidden rounded-full bg-wash" aria-hidden>
      <span className="block h-full rounded-full" style={{ width: `${Math.max(0, Math.min(1, v)) * 100}%`, background: good === "high" ? "#14213D" : "#8A93A6" }} />
    </span>
  );
}

function SummaryTable({ rows }: { rows: Record<string, string>[] }) {
  return (
    <div className="overflow-x-auto rounded-xl border border-rule bg-surface">
      <table className="w-full min-w-[860px] text-sm">
        <thead>
          <tr className="border-b border-rule text-left text-ink-3">
            <th scope="col" className="px-4 py-3 font-medium">Condition</th>
            <th scope="col" className="px-3 py-3 text-right font-medium">Questions</th>
            {MEASURES.map((m) => <th key={m.key} scope="col" className="px-3 py-3 font-medium" title={m.hint}>{m.label}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const l = label(r.system);
            return (
              <tr key={r.system} className="border-b border-rule last:border-b-0">
                <th scope="row" className="px-4 py-2.5 text-left font-normal">
                  <span className="block font-medium">{l.main}</span>
                  <span className="block text-xs text-ink-3">{l.sub || l.code}</span>
                </th>
                <td className="px-3 py-2.5 text-right tnum text-ink-2">{fmtInt(Number(r.n_items))}</td>
                {MEASURES.map((m) => {
                  const v = r[m.key];
                  const n = v === "" || v == null ? null : Number(v);
                  return (
                    <td key={m.key} className="px-3 py-2.5 align-top tnum">
                      {n == null || !Number.isFinite(n) ? <span className="text-ink-4">–</span> : (
                        <>{m.key === "score" || m.key === "refusal_f1" ? n.toFixed(2) : fmtRate(n)}<Bar v={n} good={m.good} /></>
                      )}
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Outcomes({ rows }: { rows: Record<string, string>[] }) {
  return (
    <ul className="space-y-3">
      {rows.map((r) => {
        const l = label(r.system);
        const parts = [
          { k: "correct", c: TONE_HEX.supported, n: Number(r.correct) }, { k: "declined", c: "#C5CCD7", n: Number(r.abstained) },
          { k: "wrong", c: TONE_HEX.false, n: Number(r.wrong) },
        ];
        const n = Number(r.n) || 1;
        return (
          <li key={`${r.system}-${r.evidence_sufficient}`} className="grid grid-cols-[minmax(0,200px)_minmax(0,1fr)] items-center gap-4 text-sm">
            <span className="min-w-0"><span className="block truncate font-medium">{l.main}</span><span className="block truncate text-xs text-ink-3">{l.sub || l.code}</span></span>
            <span>
              <span className="flex h-3 overflow-hidden rounded-full bg-wash" role="img"
                aria-label={parts.map((p) => `${p.n} ${p.k}`).join(", ")}>
                {parts.map((p) => p.n > 0 && <span key={p.k} style={{ width: `${(p.n / n) * 100}%`, background: p.c }} />)}
              </span>
              <span className="mt-1 flex gap-4 text-xs text-ink-3 tnum">
                {parts.map((p) => <span key={p.k}>{p.n} {p.k}</span>)}
              </span>
            </span>
          </li>
        );
      })}
    </ul>
  );
}

function Contrasts({ rows }: { rows: Record<string, string>[] }) {
  const main = rows.filter((r) => r.scope === "all");
  return (
    <div className="overflow-x-auto rounded-xl border border-rule bg-surface">
      <table className="w-full min-w-[760px] text-sm">
        <thead><tr className="border-b border-rule text-left text-ink-3">
          {["Question", "Comparison", "Measure", "A", "B", "Pairs", "p (Holm)"].map((h) => <th key={h} scope="col" className="px-3 py-3 font-medium first:pl-4">{h}</th>)}
        </tr></thead>
        <tbody>
          {main.map((r, i) => {
            const p = r.p_holm === "" ? null : Number(r.p_holm);
            return (
              <tr key={i} className="border-b border-rule last:border-b-0 align-top">
                <td className="py-2.5 pl-4 pr-3 font-medium">{r.family}</td>
                <td className="px-3 py-2.5"><span className="block">{r.question}</span><span className="block text-xs text-ink-3">{r.a} vs {r.b}</span></td>
                <td className="px-3 py-2.5 text-ink-2">{r.measure}</td>
                <td className="px-3 py-2.5 tnum">{fmtRate(r.rate_a)}</td>
                <td className="px-3 py-2.5 tnum">{fmtRate(r.rate_b)}</td>
                <td className="px-3 py-2.5 tnum text-ink-3">{r.n}</td>
                <td className={`px-3 py-2.5 tnum ${p != null && p < 0.05 ? "font-semibold" : "text-ink-3"}`}>{p == null ? "–" : p < 0.001 ? "< 0.001" : p.toFixed(3)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function StageView({ stage }: { stage: Stage }) {
  const models = useMemo(() => [...new Set(stage.summary.map((r) => r.model))].filter((m) => m !== "R0"), [stage]);
  const [model, setModel] = useState(models[0] ?? "");
  const [suff, setSuff] = useState<"True" | "False">("True");
  const m = models.includes(model) ? model : models[0];
  const summary = stage.summary.filter((r) => r.model === m && r.task === "ALL");
  const outcomes = stage.outcomes.filter((r) => r.model === m && r.task === "ALL" && r.evidence_sufficient === suff);
  const contrasts = stage.contrasts.filter((r) => r.model === m);
  if (!stage.summary.length) {
    return <p className="text-ink-3">{fmtInt(stage.rows)} answers recorded, not analysed yet. Run: <code className="font-mono text-sm">python -m evaluation.study2_report results\{stage.id}</code></p>;
  }
  return (
    <div className="space-y-12">
      {models.length > 1 && <Segmented label="Model" value={m} onChange={setModel} options={models.map((x) => ({ value: x, label: x }))} />}
      <section aria-labelledby="sum-h">
        <h2 id="sum-h" className="mb-1 text-lg font-semibold">All questions, by condition</h2>
        <p className="mb-4 text-sm text-ink-3">Hover a column heading for its definition.</p>
        <SummaryTable rows={summary} />
      </section>
      <section aria-labelledby="out-h" className="grid gap-6 lg:grid-cols-[280px_minmax(0,1fr)] lg:gap-12">
        <div>
          <h2 id="out-h" className="text-lg font-semibold">Correct, declined or wrong</h2>
          <p className="mt-2 text-sm text-ink-3">The right behaviour depends on the evidence: answer when it is complete, decline when it is not.</p>
          <div className="mt-4">
            <Segmented label="Evidence" value={suff} onChange={setSuff} options={[{ value: "True", label: "Complete evidence" }, { value: "False", label: "Incomplete" }]} />
          </div>
        </div>
        {outcomes.length ? <Outcomes rows={outcomes} /> : <p className="text-sm text-ink-3">No questions in this group.</p>}
      </section>
      {contrasts.length > 0 && (
        <section aria-labelledby="con-h">
          <h2 id="con-h" className="mb-1 text-lg font-semibold">Planned comparisons</h2>
          <p className="mb-4 text-sm text-ink-3">Paired tests on the same questions, adjusted for multiple comparisons within each research question.</p>
          <Contrasts rows={contrasts} />
        </section>
      )}
    </div>
  );
}

export default function ResultsPage() {
  useTitle("Results");
  const { data, isLoading, error } = useQuery({ queryKey: ["results"], queryFn: api.results, staleTime: 30_000 });
  const [pick, setPick] = useState<string | null>(null);
  const stages = data?.stages ?? [];
  const stage = stages.find((s) => s.id === pick) ?? stages[0];
  return (
    <div className="mx-auto max-w-[1180px] px-4 pb-24 pt-10 sm:px-6">
      <header className="max-w-[68ch]">
        <h1 className="text-xl font-semibold sm:text-2xl">Study results</h1>
        <p className="mt-2 text-ink-3">
          Read from the analysis tables on this computer. Nothing here is final until the run is complete and the protocol's analysis has been done.
        </p>
      </header>
      <div className="mt-8">
        {isLoading && <Skeleton className="h-64 w-full" />}
        {error && <ErrorNote title="The results could not be read." detail={(error as Error).message} />}
        {data && stages.length === 0 && (
          <div className="rounded-xl border border-dashed border-rule-strong p-8">
            <p className="font-medium">No study runs on this computer yet.</p>
            <p className="mt-1 text-sm text-ink-3">Results appear here once results\v3_cse499 or results\v3_pilot exists.</p>
          </div>
        )}
        {stage && (
          <>
            <div className="mb-8 flex flex-wrap items-center gap-4">
              {stages.length > 1 && <Segmented label="Run" value={stage.id} onChange={setPick} options={stages.map((s) => ({ value: s.id, label: s.label }))} />}
              {stages.length === 1 && <span className="rounded-full bg-memory-soft px-2.5 py-0.5 text-xs font-medium text-memory">{stage.label}</span>}
              <span className="text-sm text-ink-3">
                {fmtInt(stage.rows)} answers recorded
                {stage.analysed_at && `, analysed ${new Date(stage.analysed_at * 1000).toLocaleString()}`}
                {stage.analysed_at && stage.updated > stage.analysed_at + 60 && ". New answers since then; re-run the analysis to include them"}
              </span>
            </div>
            <StageView key={stage.id} stage={stage} />
          </>
        )}
      </div>
    </div>
  );
}
