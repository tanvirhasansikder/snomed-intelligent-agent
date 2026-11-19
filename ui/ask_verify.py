"""
"Ask and verify" demo logic (Study 2 pipeline on the local SNOMED CT graph).

The Streamlit page (pages/1_Ask_and_verify.py) only draws what this module
returns, so the logic is testable without Streamlit. One question goes
through exactly the Study 2 code: retrieval (G-QA, L-RAG, D-RAG or none),
optional completeness mask, the frozen prompt (v3.0), the model, the
deterministic verifier (G-QA+V), and claim scoring against the International
view of the frozen release.

LLM calls are cached. The demo reads the study's response cache (read-only),
so a question asked exactly as in the study replays the recorded answer at no
cost, and writes new answers to its own cache file.
"""

import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path

from evaluation.completeness import evidence_format, mask_context
from evaluation.gold import lowest_common
from evaluation.scoring import score_answer
from evaluation.tasks import Item, answer_in_evidence, reference_answer, sufficient
from evaluation.text_retrieval import CARD_FORMAT, CardStore, card_context, entity_queries, lexical_retrieve
from explanation.explainer import (
    CONTRADICTED, FALSE_IN_REFERENCE, INSUFFICIENT, SUPPORTED, TRUE_NOT_RETRIEVED, UNKNOWN_IDENTIFIER,
)
from llm.agent import HierarchyAgent
from llm.client import ResponseCache
from llm.prompt_manager import STUDY2_PROMPT_VERSION

#: demo system -> (agent system, retrieval)
SYSTEMS = {
    "G-QA": ("S1", "S1"),
    "G-QA+V": ("S2", "S1"),
    "L-RAG": ("S1", "L-RAG"),
    "D-RAG": ("S1", "D-RAG"),
    "B0": ("B0", "C0"),
}
SYSTEM_HELP = {
    "G-QA": "Question-aware graph retrieval: follows exactly the IS_A links the question needs.",
    "G-QA+V": "G-QA plus deterministic verification: unsupported claims trigger one regeneration, then are removed.",
    "L-RAG": "Keyword (BM25) search over the database's concept cards.",
    "D-RAG": "Meaning-based (BGE-M3) search over the database's concept cards.",
    "B0": "No evidence: the model answers from memory.",
}
TASK_TEXT = {
    "T1": "Direct parents of X", "T2": "Direct children of X", "T3": "Is X a kind of Y?",
    "T4": "IS_A path from X up to Y", "T5": "Explain where X sits in the hierarchy",
    "T6": "Nearest common ancestor of X and Y",
}
NEEDS_TARGET = ("T3", "T4", "T6")
LABEL_TEXT = {
    SUPPORTED: ("supported", "stated in the retrieved evidence"),
    TRUE_NOT_RETRIEVED: ("true, not in evidence", "true in SNOMED CT, but the evidence did not show it (from memory)"),
    FALSE_IN_REFERENCE: ("false", "false in SNOMED CT"),
    CONTRADICTED: ("contradicted", "conflicts with complete evidence"),
    UNKNOWN_IDENTIFIER: ("unknown ID", "an identifier that is not in the release"),
    INSUFFICIENT: ("undecidable", "the reference cannot decide"),
}
DEMO_MASK_SEED = 2026111903          # the study's mask seed, so study questions get the study's masks
MAX_GOLD_CHILDREN = 100


class _Rows(list):
    def fetchone(self):
        return self[0] if self else None

    def fetchall(self):
        return list(self)


class _SharedConnection:
    """A read-only SQLite connection usable from Streamlit's threads, one query at a time."""

    def __init__(self, db_path):
        self.con = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True, check_same_thread=False)
        self.lock = threading.Lock()

    def execute(self, sql, args=()):
        with self.lock:
            return _Rows(self.con.execute(sql, args).fetchall())

    def close(self):
        self.con.close()


class ThreadSafeCardStore(CardStore):
    """The study's CardStore, unchanged, on a connection Streamlit's threads can share."""

    def __init__(self, db_path):
        super().__init__(db_path)
        self.con.close()
        self.con = _SharedConnection(db_path)


class DemoError(ValueError):
    """A question the demo cannot ask (shown to the user as a message)."""


# ----------------------------------------------------------------------
# the reference: the International view itself
# ----------------------------------------------------------------------

class _Lookup:
    def __init__(self, fn):
        self.fn = fn

    def get(self, key, default=()):
        values = self.fn(key)
        return set(values) if values else default


class _Everything:
    def __init__(self, graph):
        self.graph = graph

    def __contains__(self, concept_id):
        return concept_id in self.graph


class GraphReference:
    """The claim verifier's reference interface, served from a SnomedGraph (no copy of the edges)."""

    def __init__(self, graph):
        self.graph = graph
        self.nodes = _Everything(graph)
        self.parents = _Lookup(graph.parents_of)
        self.children = _Lookup(graph.children_of)
        self.parents_complete = _Everything(graph)        # a complete release: every list is complete
        self.children_complete = _Everything(graph)

    def has_edge(self, child, parent):
        return parent in self.graph.parents_of(child)

    def is_ancestor(self, concept_id, candidate):
        return candidate in self.graph.ancestors(concept_id)


# ----------------------------------------------------------------------
# a question
# ----------------------------------------------------------------------

def make_item(graph, task, concept_id, target_id=None):
    """A study-style Item with its gold answer from the graph; DemoError if it cannot be asked."""
    if task not in TASK_TEXT:
        raise DemoError(f"Unknown question type {task}")
    if concept_id not in graph:
        raise DemoError(f"{concept_id} is not in the International view")
    if task in NEEDS_TARGET:
        if not target_id:
            raise DemoError("This question needs a second concept (Y)")
        if target_id not in graph:
            raise DemoError(f"{target_id} is not in the International view")
        if target_id == concept_id:
            raise DemoError("X and Y must be different concepts")
    x, y = concept_id, target_id
    if task == "T1":
        return Item(f"T1:{x}", "T1", x, sorted(graph.parents_of(x)))
    if task == "T2":
        children = sorted(graph.children_of(x))
        if len(children) > MAX_GOLD_CHILDREN:
            raise DemoError(f"X has {len(children)} children; the study asks T2 only up to {MAX_GOLD_CHILDREN}")
        return Item(f"T2:{x}", "T2", x, children)
    if task == "T3":
        positive = y in graph.ancestors(x)
        return Item(f"T3:{x}:{y}", "T3", x, "yes" if positive else "no", target_id=y,
                    polarity="pos" if positive else "neg")
    if task == "T4":
        if y not in graph.ancestors(x):
            raise DemoError("For a path question, Y must be an ancestor of X")
        return Item(f"T4:{x}", "T4", x, None, target_id=y)
    if task == "T5":
        return Item(f"T5:{x}", "T5", x, None)
    ancestors_x, ancestors_y = graph.ancestors(x), graph.ancestors(y)
    if y in ancestors_x or x in ancestors_y:
        raise DemoError("For a common-ancestor question, neither concept may be an ancestor of the other")
    lcas = lowest_common(ancestors_x, ancestors_y, graph.ancestors)
    return Item(f"T6:{x}:{y}", "T6", x, lcas, target_id=y)


@dataclass
class DemoResult:
    item: Item
    system: str
    fraction: float
    marked: bool
    evidence_text: str
    render_format: str
    sufficient: bool
    answer_in_evidence: bool
    mask: dict = None
    text_record: dict = None
    reference_answer: dict = None
    error: str = None
    answer: object = None
    abstained: bool = None
    raw_text: str = None
    claims: list = field(default_factory=list)
    first_answer: object = None
    first_claims: list = field(default_factory=list)
    regenerated: bool = False
    removed_claims: list = field(default_factory=list)
    score: dict = None
    cached: bool = None
    tokens: tuple = None
    latency_s: float = None


class ReadThroughCache:
    """Look up the study's cache first (never written), then the demo's own; new answers go to the demo's."""

    def __init__(self, demo_path, study_path=None):
        self.demo = ResponseCache(demo_path)
        self.study = ResponseCache(study_path) if study_path else None

    def get(self, key):
        hit = self.study.get(key) if self.study is not None else None
        return hit if hit is not None else self.demo.get(key)

    def put(self, key, fingerprint, response):
        self.demo.put(key, fingerprint, response)


class _Calls:
    """Adapter the agent calls as llm.complete(messages); records the responses."""

    def __init__(self, client):
        self.client, self.responses = client, []

    def complete(self, messages):
        response = self.client.complete(messages)
        self.responses.append(response)
        return response


class AskVerify:
    """One question through the Study 2 pipeline."""

    def __init__(self, local_client, db_path, dense_dir=None, query_vectors=None, encoder_loader=None,
                 prompt_version=STUDY2_PROMPT_VERSION, request_budget=1000, mask_seed=DEMO_MASK_SEED, text_k=10):
        self.local = local_client
        self.graph = local_client.graph
        self.store = ThreadSafeCardStore(db_path)
        self.reference = GraphReference(self.graph)
        self.dense_dir = dense_dir
        self.query_vectors = dict(query_vectors or {})
        self.encoder_loader = encoder_loader
        self._index = None
        self._encoder = None
        self.prompt_version = prompt_version
        self.request_budget = request_budget
        self.mask_seed = mask_seed
        self.text_k = text_k

    def label(self, concept_id):
        return self.local.label(concept_id)

    # -- evidence ---------------------------------------------------------
    def _dense_vector(self, name):
        if name not in self.query_vectors:
            if self.encoder_loader is None:
                raise DemoError("D-RAG has no vector for this concept name and no encoder is available")
            if self._encoder is None:
                self._encoder = self.encoder_loader()
            from evaluation.dense_retrieval import encode
            self.query_vectors[name] = encode([name], self._encoder)[0]
        return self.query_vectors[name]

    def _dense_ids(self, item):
        from evaluation.dense_retrieval import DenseIndex
        from evaluation.text_retrieval import merge_rankings, share_k
        if self._index is None:
            if not self.dense_dir:
                raise DemoError("D-RAG needs the exported card vectors (python -m evaluation.dense_retrieval export)")
            self._index = DenseIndex(self.dense_dir)
        queries = entity_queries(item, self.label)
        rankings = [self._index.search(self._dense_vector(q), self.text_k) for q in queries]
        return merge_rankings(rankings, share_k(self.text_k, len(queries)), self.text_k), queries

    def evidence(self, item, system, fraction=0.0, marked=True):
        """(context, render format, mask record, text record) for one question and system."""
        agent_system, retrieval = SYSTEMS[system]
        mask = text_record = None
        if retrieval in ("L-RAG", "D-RAG"):
            if retrieval == "L-RAG":
                ids, queries = lexical_retrieve(item, self.store, self.label, k=self.text_k, keep=self.graph)
            else:
                ids, queries = self._dense_ids(item)
            context = card_context(item, ids, self.store, retrieval, view=self.graph)
            text_record = {"queries": queries, "cards": ids}
            fmt = CARD_FORMAT
        else:
            retriever = HierarchyAgent(llm=None, retrieval_client=self.local, label_of=self.label,
                                       request_budget=self.request_budget)
            context = retriever.retrieve(item, agent_system)
            if retrieval == "S1" and fraction > 0:
                context, mask = mask_context(context, item, fraction, self.mask_seed)
            fmt = evidence_format("marked" if marked else "unmarked")
        return context, fmt, mask, text_record

    # -- the whole question -------------------------------------------------
    def ask(self, item, system, llm_client, fraction=0.0, marked=True):
        """Retrieve, (mask), answer, verify, score. ``llm_client``: a CachedClient (or test double)."""
        if system not in SYSTEMS:
            raise DemoError(f"Unknown system {system}")
        if fraction and system not in ("G-QA", "G-QA+V"):
            raise DemoError("Only graph evidence (G-QA, G-QA+V) can be masked")
        context, fmt, mask, text_record = self.evidence(item, system, fraction, marked)
        agent_system = SYSTEMS[system][0]
        rendered = context.render(fmt) if agent_system != "B0" else "(no evidence: closed-book)"
        result = DemoResult(item=item, system=system, fraction=fraction, marked=marked, evidence_text=rendered,
                            render_format=fmt, sufficient=sufficient(item, context),
                            answer_in_evidence=answer_in_evidence(item, context), mask=mask,
                            text_record=text_record,
                            reference_answer=reference_answer(item, context) if agent_system != "B0" else None)
        calls = _Calls(llm_client)
        agent = HierarchyAgent(calls, self.local, label_of=self.label, evidence_format=fmt,
                               request_budget=self.request_budget, prompt_version=self.prompt_version)
        answered = agent.answer(item, agent_system, context=context)
        result.cached = all(getattr(r, "cached", False) for r in calls.responses) if calls.responses else None
        if answered.error is not None:
            result.error = answered.error
            return result
        result.raw_text = answered.raw_text
        result.tokens = (answered.total_prompt_tokens, answered.total_completion_tokens)
        result.latency_s = answered.total_llm_latency_s
        first = dict(answered.parsed, claims=answered.claims)
        if agent_system == "S2":
            final = answered.final
            parsed = {"answer": final["answer"], "claims": final["claims"],
                      "insufficient_evidence": final["insufficient_evidence"],
                      "parse_error": first["parse_error"], "dropped_claims": first.get("dropped_claims", 0)}
            result.first_answer = first["answer"]
            result.first_claims = score_answer(item, first, context, self.reference)["claims"]
            result.regenerated = bool(answered.regenerated)
            result.removed_claims = list(final["removed_claims"])
        else:
            parsed = first
        score = score_answer(item, parsed, context, self.reference)
        result.claims = score.pop("claims")
        result.score = score
        result.answer = parsed["answer"]
        result.abstained = score["abstained"]
        return result


def describe_label(label):
    return LABEL_TEXT.get(label, (label, label))
