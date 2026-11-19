// One answer: verdict, claims (each checked against the release), the evidence and its graph.
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ChevronDown, CircleSlash, RotateCcw } from "lucide-react";
import type { AskResult, Claim, Ref } from "../api/client";
import HierarchyGraph, { type GraphEdge, type GraphNode } from "./HierarchyGraph";
import { ConceptLink, Dot, OutcomeBadge, Pill, Sctid } from "./bits";
import {
  CITATION_TEXT, LABEL_TONE, LEGEND, RELATION_TEXT, SYSTEM_NAME, TONE_HEX, TONE_TEXT, type Tone,
} from "../utils/labels";

function Answer({ value, empty }: { value: Ref[] | string | null; empty: string }) {
  if (value == null) return <span className="text-ink-3">{empty}</span>;
  if (typeof value === "string") return <span className="font-medium">{value === "yes" ? "Yes" : value === "no" ? "No" : value}</span>;
  if (value.length === 0) return <span className="text-ink-3">An empty list</span>;
  return (
    <ul className="flex flex-col gap-1">
      {value.map((c, i) => (
        <li key={`${c.id}-${i}`} className="flex items-baseline justify-between gap-3">
          <ConceptLink c={c} /><span className="sctid shrink-0">{c.id}</span>
        </li>
      ))}
    </ul>
  );
}

export function ClaimRow({ claim, active, onHover }: { claim: Claim; active?: boolean; onHover?: (ids: string[] | null) => void }) {
  const tone: Tone = LABEL_TONE[claim.label] ?? "neutral";
  return (
    <li
      onMouseEnter={() => onHover?.(claim.cites)} onMouseLeave={() => onHover?.(null)}
      onFocus={() => onHover?.(claim.cites)} onBlur={() => onHover?.(null)} tabIndex={0}
      className={`relative grid gap-1.5 rounded-lg border bg-surface py-2.5 pl-4 pr-3 outline-none transition-colors ${active ? "border-ink/40" : "border-rule"}`}
    >
      <span aria-hidden className="absolute bottom-2 left-0 top-2 w-[3px] rounded-r" style={{ background: TONE_HEX[tone] }} />
      <div className="text-[14.5px] leading-6">
        <span className="font-medium">{claim.subject.name}</span>{" "}
        <span className="text-ink-3">{RELATION_TEXT[claim.relation] ?? claim.relation}</span>
        {claim.object && <> <span className="font-medium">{claim.object.name}</span></>}
      </div>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
        <span className={`flex items-center gap-1.5 font-medium ${TONE_TEXT[tone]}`} title={claim.label_long}>
          <Dot color={TONE_HEX[tone]} />{claim.label_short === "supported" ? "Supported by the evidence" : claim.label_long[0].toUpperCase() + claim.label_long.slice(1)}
        </span>
        <span className="text-ink-3">{claim.source === "model_knowledge" ? "Model says: from its own knowledge" : "Model says: from the evidence"}</span>
        {claim.cites.length > 0 && (
          <span className="flex items-center gap-1">
            {claim.cites.map((c) => <span key={c} className="rounded bg-wash px-1.5 py-px font-mono text-[11px] text-ink-2">{c}</span>)}
          </span>
        )}
        {claim.citation && claim.citation !== "no_citation" && (
          <span className={claim.citation === "entails" ? "text-ink-3" : "text-false"}>{CITATION_TEXT[claim.citation] ?? claim.citation}</span>
        )}
      </div>
    </li>
  );
}

function buildGraph(r: AskResult) {
  const nodes = new Map<string, GraphNode>();
  const put = (c: Ref, variant: GraphNode["variant"]) => {
    const old = nodes.get(c.id);
    if (!old || (old.variant === "evidence" && variant !== "evidence")) nodes.set(c.id, { id: c.id, name: c.name, tag: c.tag, variant });
  };
  const edges = new Map<string, GraphEdge>();
  r.evidence.edges.forEach((e) => {
    put(e.child, "evidence"); put(e.parent, "evidence");
    edges.set(`${e.child.id}>${e.parent.id}`, { child: e.child.id, parent: e.parent.id, variant: "evidence", label: e.id });
  });
  r.evidence.mask?.withheld_edges.forEach((e) => {
    put(e.child, "withheld"); put(e.parent, "withheld");
    edges.set(`${e.child.id}>${e.parent.id}`, { child: e.child.id, parent: e.parent.id, variant: "withheld", label: "withheld" });
  });
  r.claims.forEach((c) => {
    if (!c.object || !["direct_parent", "ancestor"].includes(c.relation)) return;
    const tone = LABEL_TONE[c.label] ?? "neutral";
    put(c.subject, nodes.has(c.subject.id) ? nodes.get(c.subject.id)!.variant : "outside");
    put(c.object, nodes.has(c.object.id) ? nodes.get(c.object.id)!.variant : "outside");
    const key = `${c.subject.id}>${c.object.id}`;
    const old = edges.get(key);
    edges.set(key, { child: c.subject.id, parent: c.object.id, variant: tone === "neutral" ? "muted" : tone, label: old?.label });
  });
  const x = r.question.x;
  put(x, "focus");
  nodes.set(x.id, { ...nodes.get(x.id)!, variant: "focus" });
  if (r.question.y) { put(r.question.y, "target"); nodes.set(r.question.y.id, { ...nodes.get(r.question.y.id)!, variant: "target" }); }
  return { nodes: [...nodes.values()], edges: [...edges.values()] };
}

function EvidenceGraph({ r }: { r: AskResult }) {
  const navigate = useNavigate();
  const g = useMemo(() => buildGraph(r), [r]);
  if (g.edges.length === 0) return null;
  return (
    <div className="overflow-hidden rounded-xl border border-rule bg-[#F7F9FB]">
      <HierarchyGraph nodes={g.nodes} edges={g.edges} height={Math.min(560, 160 + g.nodes.length * 34)}
        onOpen={(id) => navigate(`/concept/${id}`)} fitKey={`${r.system}-${r.withheld}-${r.question.x.id}`} />
      <div className="flex flex-wrap gap-x-5 gap-y-1.5 border-t border-rule bg-surface px-3 py-2 text-xs text-ink-3">
        <span className="flex items-center gap-1.5"><svg width="22" height="6"><line x1="0" y1="3" x2="22" y2="3" stroke="#8D97A9" strokeWidth="1.5" /></svg>Link in the evidence</span>
        {r.evidence.mask && r.evidence.mask.n_withheld > 0 && (
          <span className="flex items-center gap-1.5"><svg width="22" height="6"><line x1="0" y1="3" x2="22" y2="3" stroke="#B4BCC9" strokeWidth="1.5" strokeDasharray="2 4" /></svg>Withheld from the model</span>
        )}
        {LEGEND.map((l) => (
          <span key={l.tone} className="flex items-center gap-1.5">
            <svg width="22" height="6"><line x1="0" y1="3" x2="22" y2="3" stroke={TONE_HEX[l.tone]} strokeWidth="2.6" strokeDasharray={l.tone === "supported" ? undefined : "6 4"} /></svg>
            Claim: {l.title.toLowerCase()}
          </span>
        ))}
      </div>
    </div>
  );
}

function EvidenceList({ r, highlight }: { r: AskResult; highlight: string[] | null }) {
  const [raw, setRaw] = useState(false);
  const e = r.evidence;
  return (
    <div>
      {e.mask && (
        <p className="mb-2 text-sm text-ink-3">
          {e.mask.n_withheld} of the {e.mask.n_relevant} links the answer needs were withheld from the model.
        </p>
      )}
      {e.text_search && <p className="mb-2 text-sm text-ink-3">Searched for: {e.text_search.queries.map((q) => `“${q}”`).join(", ")}. {e.text_search.cards.length} cards returned.</p>}
      {e.edges.length > 0 && (
        <ul className="max-h-72 overflow-auto rounded-lg border border-rule bg-surface text-sm">
          {e.edges.map((ed) => {
            const on = highlight?.includes(ed.id);
            return (
              <li key={ed.id} className={`grid grid-cols-[44px_minmax(0,1fr)] gap-2 border-b border-rule px-3 py-1.5 last:border-b-0 ${on ? "bg-supported-soft" : ""}`}>
                <span className={`font-mono text-xs leading-5 ${on ? "font-medium text-supported" : "text-ink-3"}`}>{ed.id}</span>
                <span className="min-w-0"><span className="text-ink">{ed.child.name}</span> <span className="text-ink-3">is a</span> <span className="text-ink">{ed.parent.name}</span></span>
              </li>
            );
          })}
        </ul>
      )}
      {r.system === "B0" && <p className="text-sm text-ink-3">None. This system answers from the model's memory alone.</p>}
      <button type="button" onClick={() => setRaw((v) => !v)} aria-expanded={raw} className="mt-3 flex items-center gap-1 text-sm text-ink-2 hover:text-ink">
        <ChevronDown size={15} className={`transition-transform ${raw ? "rotate-180" : ""}`} />Exactly what the model was shown
      </button>
      {raw && <pre className="mt-2 max-h-96 overflow-auto whitespace-pre-wrap rounded-lg bg-ink p-4 font-mono text-[12px] leading-5 text-white/90">{e.text}</pre>}
    </div>
  );
}

export function questionTitle(r: AskResult) {
  const x = r.question.x.name;
  const y = r.question.y?.name ?? "Y";
  switch (r.question.task) {
    case "T1": return `Direct parents of ${x}`;
    case "T2": return `Direct children of ${x}`;
    case "T3": return `Is ${x} a kind of ${y}?`;
    case "T4": return `IS_A path from ${x} up to ${y}`;
    case "T5": return `Where ${x} sits in the hierarchy`;
    case "T6": return `Nearest common ancestor of ${x} and ${y}`;
    default: return r.question.text;
  }
}

export default function ResultView({ r }: { r: AskResult }) {
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);
  const hover = hoverIdx == null ? null : r.claims[hoverIdx]?.cites ?? null;
  const isList = Array.isArray(r.question.gold);
  const notInEvidence = r.claims.filter((c) => c.label !== "supported_by_subgraph").length;
  if (r.error) {
    return <div role="alert" className="rounded-lg border border-false/30 bg-false-soft px-4 py-3 text-sm"><span className="font-medium text-false">The model call failed.</span> <span className="text-ink-2">{r.error}</span></div>;
  }
  return (
    <div className="animate-rise space-y-8">
      <h2 className="text-lg font-semibold">{questionTitle(r)}</h2>
      <div className="grid gap-6 rounded-xl border border-rule bg-surface p-5 sm:p-6 md:grid-cols-2">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <OutcomeBadge outcome={r.outcome} />
            {!r.evidence.sufficient && r.system !== "B0" && <Pill tone="memory">Evidence incomplete</Pill>}
            {r.score?.score != null && isList && !r.abstained && <span className="text-xs text-ink-3 tnum">score {r.score.score.toFixed(2)}</span>}
          </div>
          <h3 className="mt-4 text-sm font-medium text-ink-3">The model answered</h3>
          <div className="mt-1.5 text-[15px]">
            {r.abstained ? <span className="flex items-center gap-2 text-ink-2"><CircleSlash size={16} />It said the evidence was not enough to answer.</span>
              : <Answer value={r.answer} empty="No answer" />}
          </div>
        </div>
        <div className="border-t border-rule pt-5 md:border-l md:border-t-0 md:pl-6 md:pt-0">
          <h3 className="text-sm font-medium text-ink-3">SNOMED CT says</h3>
          <div className="mt-1.5 text-[15px]">
            {r.question.task === "T5" ? <span className="text-ink-3">An explanation has no single right answer; its claims are checked one by one.</span>
              : <Answer value={r.question.gold} empty="Not decidable" />}
          </div>
        </div>
      </div>

      <div className="grid gap-8 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <section aria-labelledby="claims-h">
          <div className="mb-3 flex items-baseline justify-between gap-3">
            <h3 id="claims-h" className="text-md font-semibold">Claims, checked</h3>
            <span className="text-sm text-ink-3 tnum">{r.claims.length === 0 ? "none" : `${notInEvidence} of ${r.claims.length} not in the evidence`}</span>
          </div>
          {r.claims.length === 0 ? <p className="text-sm text-ink-3">The answer makes no checkable claims.</p> : (
            <ul className="space-y-2">{r.claims.map((c, i) => <ClaimRow key={i} claim={c} onHover={(ids) => setHoverIdx(ids ? i : null)} active={hoverIdx === i} />)}</ul>
          )}
          {r.verification && (
            <div className="mt-5 rounded-lg border border-rule bg-paper p-4 text-sm">
              <div className="flex items-center gap-2 font-medium"><RotateCcw size={15} />Verification</div>
              {r.verification.regenerated || r.verification.removed > 0 ? (
                <div className="mt-2 space-y-3 text-ink-2">
                  <p>
                    The first draft had claims the evidence does not support, so the model was asked once more
                    {r.verification.removed > 0 ? `, and ${r.verification.removed} unsupported claim${r.verification.removed > 1 ? "s were" : " was"} then removed.` : "."}
                  </p>
                  <details className="group">
                    <summary className="cursor-pointer text-ink-2 hover:text-ink">The first draft ({r.verification.first_claims.length} claims)</summary>
                    <ul className="mt-2 space-y-2">{r.verification.first_claims.map((c, i) => <ClaimRow key={i} claim={c} />)}</ul>
                  </details>
                </div>
              ) : <p className="mt-1 text-ink-3">Every claim in the first draft was supported. Nothing changed.</p>}
            </div>
          )}
        </section>
        <section aria-labelledby="ev-h">
          <h3 id="ev-h" className="mb-3 text-md font-semibold">Evidence given to the model</h3>
          <EvidenceList r={r} highlight={hover} />
        </section>
      </div>

      <section aria-labelledby="graph-h">
        <h3 id="graph-h" className="mb-3 text-md font-semibold">Claims on the evidence graph</h3>
        {r.evidence.edges.length === 0 && !r.claims.some((c) => c.object) ? <p className="text-sm text-ink-3">Nothing to draw.</p> : <EvidenceGraph r={r} />}
      </section>

      <footer className="flex flex-wrap gap-x-5 gap-y-1 border-t border-rule pt-4 text-xs text-ink-3">
        <span>System: {SYSTEM_NAME[r.system]} ({r.system})</span>
        <span>Model: {r.model === "reference" ? "reference answerer, no model" : r.model}</span>
        {r.tokens && r.model !== "reference" && <span className="tnum">Tokens in {r.tokens[0]?.toLocaleString()}, out {r.tokens[1]?.toLocaleString()}</span>}
        {r.latency_s != null && r.model !== "reference" && <span className="tnum">Model time {r.latency_s.toFixed(1)} s</span>}
        {r.cached && r.model !== "reference" && <span>Replayed from cache, no quota used</span>}
        <span>Question about <Sctid id={r.question.x.id} /></span>
      </footer>
    </div>
  );
}
