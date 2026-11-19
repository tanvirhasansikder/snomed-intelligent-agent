// Typed calls to the FastAPI server (webapp/server.py).

export type Ref = { id: string; name: string; tag: string | null };
export type Hierarchy = { id: string; name: string; size?: number };

export type Status = {
  ready: boolean; error: string | null; release: string; view: string; models: string[];
  reference_model: string; systems: { id: string; help: string }[];
  tasks: { id: string; text: string; needs_y: boolean }[]; loading_seconds: number;
  concepts?: number; edges?: number; hierarchies?: Hierarchy[]; dense_available?: boolean;
};

export type SearchHit = Ref & { hierarchy: Hierarchy | null };

export type Concept = Ref & {
  fsn: string; hierarchy: Hierarchy | null; is_root: boolean; parents: Ref[]; children: Ref[];
  children_count: number; ancestors_count: number; depth: { shortest: number; longest: number };
  path: Ref[]; synonyms: string[]; attributes: { name: string; value: string }[]; card_text: string;
};

export type LineageNode = Ref & { role: "focus" | "ancestor" | "child"; leaf?: boolean };
export type Lineage = {
  focus: string; nodes: LineageNode[]; edges: { child: string; parent: string }[];
  children_total: number; children_shown: number;
};

export type Claim = {
  subject: Ref; relation: string; object: Ref | null; source: string | null; cites: string[];
  label: string; label_short: string; label_long: string; citation: string | null;
};

export type AskResult = {
  system: string; system_help: string; withheld: number; marked: boolean; model: string;
  question: { task: string; text: string; x: Ref; y: Ref | null; gold: Ref[] | string | null };
  evidence: {
    format: string; text: string; edges: { id: string; child: Ref; parent: Ref }[];
    sufficient: boolean; answer_in_evidence: boolean;
    mask: { n_relevant: number; n_withheld: number; withheld_edges: { child: Ref; parent: Ref }[] } | null;
    text_search: { queries: string[]; cards: string[] } | null;
  };
  error: string | null; outcome: string; answer: Ref[] | string | null; abstained: boolean | null;
  claims: Claim[];
  verification: { regenerated: boolean; first_answer: Ref[] | string | null; first_claims: Claim[]; removed: number } | null;
  score: { score: number | null; precision: number | null; recall: number | null; abstained: boolean } | null;
  cached: boolean | null; tokens: [number, number] | null; latency_s: number | null; raw_text: string | null;
};

export type AskRequest = {
  task: string; x: string; y?: string | null; system: string; withheld: number; marked: boolean; model: string;
};

export type Stage = {
  id: string; label: string; rows: number; updated: number; analysed_at: number | null;
  summary: Record<string, string>[]; contrasts: Record<string, string>[]; outcomes: Record<string, string>[];
};

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...init });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch { /* not JSON */ }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

export const api = {
  status: () => call<Status>("/api/status"),
  search: (q: string, limit = 12) =>
    call<{ results: SearchHit[] }>(`/api/search?q=${encodeURIComponent(q)}&limit=${limit}`).then((r) => r.results),
  concept: (id: string) => call<Concept>(`/api/concept/${id}`),
  children: (id: string, offset: number, limit = 50) =>
    call<{ total: number; children: Ref[] }>(`/api/concept/${id}/children?offset=${offset}&limit=${limit}`),
  lineage: (id: string) => call<Lineage>(`/api/concept/${id}/lineage`),
  ask: (req: AskRequest) => call<AskResult>("/api/ask", { method: "POST", body: JSON.stringify(req) }),
  compare: (req: { task: string; x: string; y?: string | null; model: string }) =>
    call<{ results: AskResult[]; skipped: { system: string; reason: string }[] }>("/api/compare", {
      method: "POST", body: JSON.stringify(req),
    }),
  results: () => call<{ stages: Stage[] }>("/api/results"),
};
