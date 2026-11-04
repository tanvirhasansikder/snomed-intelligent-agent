"""
Shared single-question pipeline (milestone M1.4; protocol v2.0, Section 6).

One question goes through:

    retrieve (per system) -> evidence v2 text with edge IDs -> prompt ->
    LLM -> parse -> run-time verification -> one result record

Both the benchmark and the Streamlit page use this module, so they behave
the same way.

Run-time verification uses only the RETRIEVED evidence, as a deployed system
would. Each claim gets one of:

* ``supported_by_subgraph``              the evidence entails it
* ``contradicted_by_complete_evidence``  the evidence is complete and excludes it
* ``insufficient_evidence``              the evidence cannot decide

Each claim also gets a citation status (``explanation.citations``).
Run-time verification never says "false": without the frozen reference
snapshot, a claim that is true but not retrieved cannot be told apart from a
false one. That three-way distinction is made later, in evaluation, against
the reference.

Systems:

* ``B0``  closed book: no retrieval
* ``B1``  upward-only retrieval, the app's current crawler (BFS depth 2, no
          children)
* ``S1``  question-aware retrieval with a request budget
          (query_engine/retrieval_planner.py)
* ``S2``  S1, plus deterministic verification: if the first answer has
          unsupported claims, bad citations or unlisted IDs in its text, ONE
          regeneration request gives the exact feedback; then the final
          filter keeps only evidence-supported claims
          (explanation/verification.py). The first-pass fields of an S2
          result are identical to S1's answer, so S1 and S2 pair exactly.
Generated claims are never written to any graph.

Command line (one question; prints the result):

    python -m llm.agent --synthetic --task T1 --system B1
    python -m llm.agent --live --concept 233604007 --task T1 --system B1 --model gemini-3.5-flash-lite

``--live`` queries BioPortal and sends the retrieved SNOMED CT labels to the
LLM provider. ``--synthetic`` uses the built-in synthetic ontology, so no
SNOMED CT content is sent anywhere.
"""

import argparse
import json
import time
from dataclasses import asdict, dataclass, field

from evaluation.conditions import build_context
from evaluation.tasks import TWO_CONCEPT_TASKS, Item, sufficient
from explanation.citations import check_citation
from explanation.explainer import SUPPORTED, HierarchyClaim, verify_claim
from explanation.verification import finalise, find_problems, has_problems
from llm.client import LLMError
from llm.prompt_manager import (
    PROMPT_VERSION,
    REGENERATION_VERSION,
    build_messages,
    build_regeneration_messages,
)
from llm.reasoner import parse_answer
from query_engine.retrieval_planner import DEFAULT_BUDGET, build_s1_context

#: Retrieval setting per system: keys of evaluation.conditions.CONDITIONS, or
#: "S1" for the question-aware planner.
RETRIEVAL = {"B0": "C0", "B1": "U2", "S1": "S1", "S2": "S1"}
SYSTEMS = tuple(RETRIEVAL)

INVALID_CLAIM = "invalid_claim"


@dataclass
class AgentResult:
    """Everything about one answered question, ready to log or display."""

    system: str
    item_id: str
    task: str
    concept_id: str
    target_id: str = None
    retrieval: str = None
    evidence_text: str = ""
    edge_index: dict = field(default_factory=dict)
    evidence_sufficient: bool = None
    retrieval_requests: int = 0
    retrieval_seconds: float = None
    prompt_version: str = PROMPT_VERSION
    model: str = None
    prompt_tokens: int = None
    completion_tokens: int = None
    llm_latency_s: float = None
    finish_reason: str = None
    raw_text: str = None
    error: str = None
    parsed: dict = None
    claims: list = field(default_factory=list)  # parsed claims + "runtime_label" + "citation"
    n_supported: int = 0
    n_not_supported: int = 0
    n_bad_citations: int = 0
    # S2 only. The fields above describe the first pass (identical to S1).
    problems: dict = None             # what the verifier found in the first pass
    regenerated: bool = False
    regeneration: dict = None         # second call: model, tokens, latency, raw text, error, problems left
    final: dict = None                # answer, insufficient_evidence, claims, removed_claims, ...
    total_prompt_tokens: int = None
    total_completion_tokens: int = None
    total_llm_latency_s: float = None

    def to_dict(self):
        data = asdict(self)
        data["edge_index"] = {k: list(v) for k, v in self.edge_index.items()}
        return data


def runtime_verify(claims, context):
    """
    Label each parsed claim against the retrieved evidence only, and check
    its citation. Returns new claim dicts with ``runtime_label`` and
    ``citation`` added.
    """
    evidence = context.evidence()
    edge_index = context.edge_index()
    checked = []
    for claim in claims:
        try:
            hc = HierarchyClaim(claim["subject"], claim["relation"], claim.get("object"))
            label = verify_claim(hc, evidence)  # no reference: evidence-only labels
        except (ValueError, KeyError):
            label = INVALID_CLAIM
        checked.append(dict(claim, runtime_label=label, citation=check_citation(claim, edge_index)))
    return checked


class HierarchyAgent:
    """
    ``llm``: an LLM client with ``complete(messages)`` (llm.client).
    ``retrieval_client``: anything the crawler accepts: the live
    BioPortalClient, the offline SnapshotClient, or the SyntheticOntology.
    ``label_of``: optional concept ID -> label function; by default, labels
    come from the evidence or from ``retrieval_client.get_concept``.
    """

    def __init__(self, llm, retrieval_client, label_of=None, evidence_format="v2", child_display_cap=100,
                 request_budget=DEFAULT_BUDGET):
        self.request_budget = request_budget
        self.llm = llm
        self.retrieval_client = retrieval_client
        self._label_of = label_of
        self.evidence_format = evidence_format
        self.child_display_cap = child_display_cap

    # ------------------------------------------------------------------
    def retrieve(self, item, system):
        if system not in RETRIEVAL:
            raise ValueError(f"Unknown or not yet implemented system {system!r}; available: {SYSTEMS}")
        if RETRIEVAL[system] == "S1":
            return build_s1_context(self.retrieval_client, item, budget=self.request_budget,
                                    child_display_cap=self.child_display_cap)
        also = (item.target_id,) if item.task in TWO_CONCEPT_TASKS and item.target_id else ()
        return build_context(self.retrieval_client, item.concept_id, RETRIEVAL[system],
                             child_display_cap=self.child_display_cap, also_expand=also)

    def label(self, concept_id, context=None):
        if context is not None and concept_id in context.nodes:
            return context.nodes[concept_id]
        if self._label_of is not None:
            return self._label_of(concept_id)
        try:
            concept = self.retrieval_client.get_concept(concept_id)
        except Exception:  # noqa: BLE001 - a missing label must not stop the pipeline
            concept = None
        return (concept or {}).get("prefLabel") or concept_id

    # ------------------------------------------------------------------
    def answer(self, item, system):
        """Run one question end to end and return an AgentResult."""
        result = AgentResult(system=system, item_id=item.item_id, task=item.task,
                             concept_id=item.concept_id, target_id=item.target_id,
                             retrieval=RETRIEVAL.get(system))

        before = getattr(self.retrieval_client, "request_count", 0)
        started = time.perf_counter()
        context = self.retrieve(item, system)
        result.retrieval_seconds = round(time.perf_counter() - started, 3)
        result.retrieval_requests = getattr(self.retrieval_client, "request_count", 0) - before

        result.evidence_text = context.render(self.evidence_format)
        result.edge_index = context.edge_index()
        result.evidence_sufficient = sufficient(item, context)

        label_of = lambda cid: self.label(cid, context)  # noqa: E731
        messages = build_messages(item, result.evidence_text, label_of)
        try:
            response = self.llm.complete(messages)
        except LLMError as exc:
            result.error = str(exc)
            return result

        self._record_first_pass(result, response, item, context)
        if system == "S2":
            self._verify_and_regenerate(result, item, context, messages, label_of)
        return result

    def _record_first_pass(self, result, response, item, context):
        result.model = response.model
        result.prompt_tokens = response.prompt_tokens
        result.completion_tokens = response.completion_tokens
        result.llm_latency_s = response.latency_s
        result.finish_reason = response.finish_reason
        result.raw_text = response.text

        parsed = parse_answer(response.text, item.task)
        result.parsed = {k: v for k, v in parsed.items() if k != "claims"}
        result.claims = runtime_verify(parsed["claims"], context)
        result.n_supported = sum(1 for c in result.claims if c["runtime_label"] == SUPPORTED)
        result.n_not_supported = len(result.claims) - result.n_supported
        result.n_bad_citations = sum(1 for c in result.claims
                                     if c["citation"] in ("not_entailed", "invalid_edge_id"))
        result.total_prompt_tokens = response.prompt_tokens
        result.total_completion_tokens = response.completion_tokens
        result.total_llm_latency_s = response.latency_s

    def _verify_and_regenerate(self, result, item, context, messages, label_of):
        """S2: at most one regeneration with exact feedback, then the final filter."""
        first = dict(result.parsed, claims=result.claims)
        problems = find_problems(item, first, result.claims)
        result.problems = {k: [_claim_summary(c) if isinstance(c, dict) else c for c in v]
                           for k, v in problems.items()}
        chosen_parsed, chosen_claims = first, result.claims

        if has_problems(problems):
            result.regenerated = True
            follow_up = build_regeneration_messages(messages, result.raw_text, problems)
            regen = {"version": REGENERATION_VERSION}
            try:
                response = self.llm.complete(follow_up)
            except LLMError as exc:
                regen["error"] = str(exc)  # keep the first answer; the final filter still applies
            else:
                parsed = parse_answer(response.text, item.task)
                claims = runtime_verify(parsed["claims"], context)
                left = find_problems(item, dict(parsed, claims=claims), claims)
                regen.update({
                    "model": response.model, "prompt_tokens": response.prompt_tokens,
                    "completion_tokens": response.completion_tokens, "latency_s": response.latency_s,
                    "finish_reason": response.finish_reason, "raw_text": response.text,
                    "parse_error": parsed["parse_error"],
                    "problems_left": {k: len(v) for k, v in left.items()},
                })
                if parsed["parse_error"] is None:
                    chosen_parsed, chosen_claims = dict(parsed, claims=claims), claims
                result.total_prompt_tokens = _add(result.total_prompt_tokens, response.prompt_tokens)
                result.total_completion_tokens = _add(result.total_completion_tokens, response.completion_tokens)
                result.total_llm_latency_s = _add(result.total_llm_latency_s, response.latency_s)
            result.regeneration = regen

        result.final = finalise(item, chosen_parsed, chosen_claims, context, label_of)


def _add(a, b):
    return None if a is None and b is None else (a or 0) + (b or 0)


def _claim_summary(claim):
    return {k: claim.get(k) for k in ("subject", "relation", "object", "source", "cites", "runtime_label", "citation")}


# ----------------------------------------------------------------------
# Command line
# ----------------------------------------------------------------------

def _print_result(result):
    print(f"System {result.system} (retrieval {result.retrieval}), task {result.task}, concept {result.concept_id}")
    print(f"Retrieval: {result.retrieval_requests} requests, {result.retrieval_seconds}s, "
          f"evidence sufficient: {result.evidence_sufficient}")
    print("--- evidence shown to the model ---")
    print(result.evidence_text)
    print("--- model ---")
    if result.error:
        print(f"ERROR: {result.error}")
        return
    print(f"{result.model}: tokens {result.prompt_tokens}/{result.completion_tokens}, "
          f"latency {result.llm_latency_s}s, finish {result.finish_reason}")
    print(f"Answer: {result.parsed.get('answer')!r}; insufficient_evidence={result.parsed.get('insufficient_evidence')}; "
          f"parse_error={result.parsed.get('parse_error')}")
    print("--- claims (run-time verification against retrieved evidence only) ---")
    for c in result.claims:
        obj = f", {c['object']}" if c.get("object") else ""
        print(f"  {c['relation']}({c['subject']}{obj}) source={c['source']} cites={c.get('cites')} "
              f"-> {c['runtime_label']}, citation {c['citation']}")
    if not result.claims:
        print("  (no claims)")
    print(f"Supported: {result.n_supported}, not supported: {result.n_not_supported}, "
          f"bad citations: {result.n_bad_citations}")
    if result.system == "S2":
        print("--- S2 verification ---")
        found = {k: len(v) for k, v in (result.problems or {}).items()}
        print(f"Problems in first answer: {found}")
        if result.regenerated:
            regen = result.regeneration or {}
            if regen.get("error"):
                print(f"Regeneration FAILED ({regen['error']}); first answer kept for the final filter")
            else:
                print(f"Regenerated once: tokens {regen.get('prompt_tokens')}/{regen.get('completion_tokens')}, "
                      f"latency {regen.get('latency_s')}s, problems left {regen.get('problems_left')}")
        else:
            print("No problems: no regeneration needed")
        final = result.final
        print(f"Final answer: {final['answer']!r}; insufficient_evidence={final['insufficient_evidence']}")
        print(f"Final claims kept: {len(final['claims'])}, removed: {len(final['removed_claims'])}, "
              f"citations repaired: {final['citations_repaired']}, text replaced: {final['answer_replaced']}")
        print(f"Total tokens {result.total_prompt_tokens}/{result.total_completion_tokens}, "
              f"total LLM latency {result.total_llm_latency_s}s")


def _main(argv=None):
    parser = argparse.ArgumentParser(description="Answer one hierarchy question with the shared pipeline.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--live", action="store_true", help="Retrieve from BioPortal (sends SNOMED CT labels to the LLM)")
    source.add_argument("--synthetic", action="store_true", help="Use the synthetic ontology (no SNOMED CT content)")
    parser.add_argument("--concept", help="Concept ID (default with --synthetic: a deep synthetic concept)")
    parser.add_argument("--target", help="Second concept ID (T3: candidate ancestor; T4: top-level concept; "
                                         "T6: the other concept)")
    parser.add_argument("--budget", type=int, default=DEFAULT_BUDGET, help="S1 request budget per question")
    parser.add_argument("--task", required=True, choices=["T1", "T2", "T3", "T4", "T5", "T6"])
    parser.add_argument("--system", default="B1", choices=SYSTEMS)
    parser.add_argument("--provider", default="gemini")
    parser.add_argument("--model", default="gemini-3.5-flash-lite")
    parser.add_argument("--reasoning-effort", default="low")
    parser.add_argument("--json", action="store_true", help="Print the full result record as JSON")
    args = parser.parse_args(argv)

    from pathlib import Path

    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    except ImportError:
        pass

    from llm.providers import build_client

    spec = {"name": f"agent-{args.provider}", "provider": args.provider, "model": args.model,
            "temperature": 0.0, "max_tokens": 800, "json_mode": True, "rpm": 10, "max_retries": 1}
    if args.reasoning_effort and args.provider != "mock":
        spec["extra_body"] = {"reasoning_effort": args.reasoning_effort}
    llm = build_client(spec)

    if args.live:
        from crawler.bioportal_client import BioPortalClient
        retrieval_client = BioPortalClient()
        if not args.concept:
            parser.error("--concept is required with --live")
        concept_id = args.concept
        print("Live mode: BioPortal is queried and retrieved SNOMED CT labels are sent to the LLM provider.")
    else:
        from evaluation.synthetic import SyntheticOntology
        retrieval_client = SyntheticOntology(seed=0)
        concept_id = args.concept or _deep_synthetic_concept(retrieval_client)

    item = Item(f"cli:{args.task}:{concept_id}", args.task, concept_id, None, target_id=args.target,
                polarity="pos" if args.task == "T3" else None)
    if args.task in ("T3", "T4", "T6") and not args.target:
        parser.error("--target is required for T3, T4 and T6")

    result = HierarchyAgent(llm, retrieval_client, request_budget=args.budget).answer(item, args.system)
    if args.json:
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    else:
        _print_result(result)


def _deep_synthetic_concept(ontology):
    """A fixed synthetic concept a few levels down, for a quick demo."""
    node = sorted(ontology.top_level_names())[0]
    for _ in range(4):
        children = sorted(ontology.children.get(node, []))
        if not children:
            break
        node = children[0]
    return node


if __name__ == "__main__":
    _main()
