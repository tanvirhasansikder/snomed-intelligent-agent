// The colour system: one hue per claim verdict, used everywhere a claim appears.

export type Tone = "supported" | "memory" | "false" | "unknown" | "neutral";

export const LABEL_TONE: Record<string, Tone> = {
  supported_by_subgraph: "supported",
  true_not_retrieved: "memory",
  false_in_reference: "false",
  contradicted_by_complete_evidence: "false",
  unknown_identifier: "unknown",
  insufficient_evidence: "neutral",
};

export const TONE_HEX: Record<Tone, string> = {
  supported: "#0E8A6A", memory: "#B7791F", false: "#C2413B", unknown: "#6B4FBB", neutral: "#8A93A6",
};

export const TONE_TEXT: Record<Tone, string> = {
  supported: "text-supported", memory: "text-memory", false: "text-false", unknown: "text-unknown", neutral: "text-ink-3",
};

export const TONE_SOFT: Record<Tone, string> = {
  supported: "bg-supported-soft text-supported", memory: "bg-memory-soft text-memory",
  false: "bg-false-soft text-false", unknown: "bg-unknown-soft text-unknown", neutral: "bg-wash text-ink-3",
};

export const LEGEND: { tone: Tone; title: string; text: string }[] = [
  { tone: "supported", title: "Supported", text: "Stated in the evidence the model was given." },
  { tone: "memory", title: "True, from memory", text: "True in SNOMED CT, but not in the evidence." },
  { tone: "false", title: "False", text: "Contradicted by the release." },
  { tone: "unknown", title: "Unknown ID", text: "An identifier the release does not contain." },
];

export const OUTCOME_TONE: Record<string, Tone> = {
  correct: "supported", "partly correct": "memory", wrong: "false", abstained: "neutral", answered: "neutral",
  error: "false",
};

export const OUTCOME_TEXT: Record<string, string> = {
  correct: "Correct", "partly correct": "Partly correct", wrong: "Wrong", abstained: "Declined to answer",
  answered: "Answered", error: "Error",
};

export const RELATION_TEXT: Record<string, string> = {
  direct_parent: "is a direct child of", ancestor: "is a kind of", no_parents: "has no parents",
  no_children: "has no children", not_ancestor: "is not a kind of", common_ancestor: "is a common ancestor of",
  lowest_common_ancestor: "is the nearest common ancestor of",
};

export const CITATION_TEXT: Record<string, string> = {
  entails: "Citation checks out", not_entailed: "Cited links do not prove it",
  invalid_edge_id: "Cites a link that is not in the evidence", no_citation: "No citation",
  not_applicable: "Rests on coverage, no link to cite",
};

export const SYSTEM_NAME: Record<string, string> = {
  "G-QA": "Graph", "G-QA+V": "Graph + verify", "L-RAG": "Keyword search", "D-RAG": "Meaning search", B0: "No evidence",
};

export function fmtInt(n: number | undefined | null) {
  return n == null ? "–" : n.toLocaleString("en-US");
}

export function fmtRate(v: string | number | undefined | null, digits = 0) {
  if (v === "" || v == null) return "–";
  const n = typeof v === "string" ? Number(v) : v;
  return Number.isFinite(n) ? `${(n * 100).toFixed(digits)}%` : "–";
}
