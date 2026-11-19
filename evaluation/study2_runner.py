"""
Study 2 runner (protocol v3): retrieval systems x completeness conditions on
the local SNOMED CT graph, through the same single-question pipeline as
Study 1 (llm/agent.py). Study 1's runner (evaluation/system_runner.py) is not
changed; this module extends it.

Selected by evaluation.runner when the configuration's ``experiment`` section
has ``"study": 2``. Conditions are named

    <system>[@<percent withheld>][:random][:unmarked]

* systems: ``B0`` (no evidence), ``G-UP`` (Study 1's B1: unplanned upward
  crawl), ``G-QA`` (Study 1's S1: question-aware graph retrieval),
  ``G-QA+V`` (Study 1's S2: G-QA plus deterministic verification),
  ``L-RAG`` and ``D-RAG`` (text retrieval over concept cards).
* ``@50`` withholds 50 % of the answer-relevant edges (evaluation/completeness.py);
  only G-QA and G-QA+V can be masked. ``@0`` or no ``@`` = complete evidence.
* ``:random`` uses the random-removal control (same count, edges the answer
  does not need). Items whose control cannot be matched are skipped for that
  condition (no model call) and listed in the run summary.
* ``:unmarked`` renders the same evidence without coverage lines.

R0 (deterministic reference answers, no model) is scored on every distinct
evidence context and named ``R0|<condition>``.

G-QA retrieval reads the frozen database file directly
(crawler/local_client.py); gold answers come from the collected data set, as
in Study 1. Rows go to ``<results_dir>/system_responses.jsonl``.
"""

import json
import logging
import random
import re
import time
from dataclasses import dataclass
from pathlib import Path

from crawler.local_client import LocalTerminologyClient
from evaluation.completeness import MASK_VERSION, evidence_format, mask_context
from evaluation.collect import PROJECT_ROOT
from evaluation.runner import _gold_answer_for_mock, call_cost
from evaluation.scoring import score_answer
from evaluation.system_runner import SystemExperiment, _RunLLM, model_settings
from evaluation.tasks import answer_in_evidence, reference_answer, sufficient
from evaluation.text_retrieval import (
    CARD_FORMAT, TEXT_RETRIEVAL_VERSION, CardStore, card_context, lexical_retrieve, retrieval_record,
)
from llm.agent import HierarchyAgent
from llm.client import CachedClient, estimate_tokens
from llm.prompt_manager import PROMPT_VERSION, REGENERATION_VERSION, build_messages
from llm.providers import build_client

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "3.0"
#: base system -> (agent system that answers, retrieval kind)
BASES = {"B0": ("B0", "C0"), "G-UP": ("B1", "U2"), "G-QA": ("S1", "S1"), "G-QA+V": ("S2", "S1"),
         "L-RAG": ("S1", "L-RAG"), "D-RAG": ("S1", "D-RAG")}
MASKABLE = ("G-QA", "G-QA+V")
_NAME = re.compile(r"^(?P<base>[A-Z0-9+\-]+?)(?:@(?P<pct>\d{1,2}))?(?P<flags>(?::(?:random|unmarked))*)$")


@dataclass(frozen=True)
class Condition:
    name: str
    base: str
    fraction: float = 0.0
    pool: str = "relevant"
    variant: str = "marked"

    @property
    def agent_system(self):
        return BASES[self.base][0]

    @property
    def retrieval(self):
        return BASES[self.base][1]

    @property
    def masked(self):
        return self.base in MASKABLE

    @property
    def evidence_key(self):
        """Conditions with the same key see the same evidence context (marked/unmarked share it)."""
        if self.masked:
            return (self.retrieval, self.fraction, self.pool)
        return (self.retrieval, 0.0, "relevant")

    @property
    def render_format(self):
        if self.retrieval in ("L-RAG", "D-RAG"):
            return CARD_FORMAT
        return evidence_format(self.variant)

    @property
    def reference_name(self):
        if self.retrieval == "C0":
            return None
        if self.masked:
            level = f"@{round(self.fraction * 100)}" if self.fraction else ""
            return f"R0|G-QA{level}{':random' if self.pool == 'random' else ''}"
        return f"R0|{self.base}"


def _project_path(value):
    path = Path(value)
    return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def parse_condition(name):
    match = _NAME.match(name or "")
    if not match or match.group("base") not in BASES:
        raise ValueError(f"Unknown condition {name!r}; systems: {', '.join(BASES)}")
    base = match.group("base")
    pct = int(match.group("pct") or 0)
    flags = set(filter(None, (match.group("flags") or "").split(":")))
    if (pct or "random" in flags) and base not in MASKABLE:
        raise ValueError(f"{name}: only {', '.join(MASKABLE)} can be masked")
    if "unmarked" in flags and base not in ("G-QA", "G-QA+V"):
        raise ValueError(f"{name}: only graph evidence has coverage marks to remove")
    if "random" in flags and pct == 0:
        raise ValueError(f"{name}: the random control needs a level above 0, e.g. @50:random")
    return Condition(name=name, base=base, fraction=pct / 100, pool="random" if "random" in flags else "relevant",
                     variant="unmarked" if "unmarked" in flags else "marked")


def select_balanced(items, per_task, seed):
    """
    A task-balanced subset: ``per_task`` items per task (all if fewer), T3
    split evenly between yes and no questions. Seeded and order-independent.
    """
    rng = random.Random(f"subset:{seed}")
    chosen = []
    by_task = {}
    for item in sorted(items, key=lambda i: i.item_id):
        by_task.setdefault(item.task, []).append(item)
    for task, group in sorted(by_task.items()):
        if task == "T3":
            pos = [i for i in group if i.polarity == "pos"]
            neg = [i for i in group if i.polarity != "pos"]
            half = per_task // 2
            take_neg = min(len(neg), per_task - min(len(pos), half))
            take_pos = min(len(pos), per_task - take_neg)
            chosen += rng.sample(pos, take_pos) + rng.sample(neg, take_neg)
        else:
            chosen += rng.sample(group, min(per_task, len(group)))
    keep = {i.item_id for i in chosen}
    return [i for i in items if i.item_id in keep]


class Study2Experiment(SystemExperiment):

    def __init__(self, config, local_client=None):
        self._study2_conditions = [parse_condition(n) for n in config["experiment"]["conditions"]]
        config = dict(config, experiment=dict(config["experiment"], systems=["R0"]))   # base class bookkeeping only
        super().__init__(config)
        self.conditions = self._study2_conditions
        names = [c.name for c in self.conditions]
        if len(set(names)) != len(names):
            raise ValueError("duplicate condition names")
        self.mask_seed = self.exp["mask_seed"]
        self.text_k = self.exp.get("text_k", 10)
        source = self.config["retrieval_source"]
        self.db_path = _project_path(source["local_db"])
        self.local = local_client or LocalTerminologyClient.from_db(self.db_path, view=source.get("view", "international"))
        self._keep = set(self.local.graph.names)
        self._card_store = None
        self._dense = None
        self._base_contexts = {}
        self._contexts2 = {}
        self.skipped = {}
        self._retriever = HierarchyAgent(llm=None, retrieval_client=self.local, label_of=self._label,
                                         request_budget=self.request_budget)

    # ------------------------------------------------------------------
    def _select_items(self):
        items = super()._select_items()
        subset = self.exp.get("subset")
        if subset:
            items = select_balanced(items, subset["per_task"], subset["seed"])
        return items

    @property
    def card_store(self):
        if self._card_store is None:
            self._card_store = CardStore(self.db_path)
        return self._card_store

    def _dense_parts(self):
        if self._dense is None:
            from evaluation.dense_retrieval import DenseIndex, load_query_vectors
            directory = _project_path(self.config["retrieval_source"].get("dense_dir", "data/snomed_db/dense"))
            vectors = load_query_vectors(directory / self.config["retrieval_source"]["query_vectors"])
            self._dense = (DenseIndex(directory), vectors)
        return self._dense

    def _base_context(self, item, retrieval):
        """(context, requests, text record) for one item and retrieval kind, built once."""
        key = (item.item_id, retrieval)
        if key not in self._base_contexts:
            text_record = None
            before = self.local.request_count
            if retrieval in ("L-RAG", "D-RAG"):
                if retrieval == "L-RAG":
                    ids, queries = lexical_retrieve(item, self.card_store, self.local.label, k=self.text_k,
                                                    keep=self._keep)
                else:
                    from evaluation.dense_retrieval import dense_retrieve
                    index, vectors = self._dense_parts()
                    ids, queries = dense_retrieve(item, index, vectors, self.local.label, k=self.text_k)
                context = card_context(item, ids, self.card_store, retrieval, view=self.local.graph)
                text_record = retrieval_record(item, retrieval, ids, queries, context)
                requests = 0
            else:
                system = {"C0": "B0", "U2": "B1", "S1": "S1"}[retrieval]
                context = self._retriever.retrieve(item, system)
                requests = self.local.request_count - before
            self._base_contexts[key] = (context, requests, text_record)
        return self._base_contexts[key]

    def condition_context(self, item, condition):
        """(context, requests, mask record, text record) for an item under a condition."""
        key = (item.item_id, condition.evidence_key)
        if key not in self._contexts2:
            context, requests, text_record = self._base_context(item, condition.retrieval)
            mask = None
            if condition.masked and (condition.fraction > 0):
                context, mask = mask_context(context, item, condition.fraction, self.mask_seed, condition.pool)
            self._contexts2[key] = (context, requests, mask, text_record)
        return self._contexts2[key]

    def eligible(self, item, condition):
        """False for a random control that cannot be matched (nothing withheld, or a shortfall)."""
        if condition.pool != "random":
            return True
        _, _, mask, _ = self.condition_context(item, condition)
        return mask["n_short"] == 0 and mask["n_withheld"] > 0

    # ------------------------------------------------------------------
    def _row2(self, item, name, condition, model_name, rep, context, requests, mask, text_record):
        row = {
            "protocol_version": PROTOCOL_VERSION, "prompt_version": PROMPT_VERSION,
            "regeneration_version": REGENERATION_VERSION, "mask_version": MASK_VERSION,
            "text_retrieval_version": TEXT_RETRIEVAL_VERSION,
            "experiment": self.config["name"], "system": name, "base_system": condition.base,
            "agent_system": condition.agent_system if model_name != "R0" else "R0",
            "retrieval": condition.retrieval, "level": condition.fraction, "pool": condition.pool,
            "variant": condition.variant, "evidence_format": condition.render_format,
            "model_name": model_name, "repetition": rep,
            "item_id": item.item_id, "task": item.task, "polarity": item.polarity,
            "concept_id": item.concept_id, "target_id": item.target_id,
            **{f"meta_{k}": v for k, v in item.meta.items()},
            "sufficient": sufficient(item, context), "answer_in_evidence": answer_in_evidence(item, context),
            "retrieval_requests": requests,
            "request_budget": self.request_budget if condition.retrieval == "S1" else None,
            "budget_stopped": len(getattr(context, "budget_stopped", ()) or ()),
            "context_nodes": len(context.nodes), "context_edges": len(context.edges),
            "evidence_chars": len(context.render(condition.render_format)) if condition.retrieval != "C0" else 0,
        }
        if mask:
            row.update({"mask_seed": mask["mask_seed"], "mask_n_relevant": mask["n_relevant"],
                        "mask_n_pool": mask["n_pool"], "mask_n_withheld": mask["n_withheld"],
                        "mask_n_short": mask["n_short"], "mask_realised_fraction": mask["realised_fraction"],
                        "mask_withheld_edges": mask["withheld_edges"],
                        "mask_nodes_dropped": len(mask["nodes_dropped"])})
        if text_record:
            row.update({"text_queries": text_record["queries"], "text_cards": text_record["cards"],
                        "text_focal_card_rank": text_record["focal_card_rank"],
                        "text_target_card_rank": text_record["target_card_rank"],
                        "text_unmatched_names": text_record["unmatched_names"],
                        "text_outside_view_names": text_record["outside_view_names"]})
        return row

    def run_reference(self):
        """R0 once per distinct evidence context (deterministic, no model)."""
        done, written, seen = self._done(), 0, set()
        for condition in self.conditions:
            name = condition.reference_name
            if name is None or name in seen:
                continue
            seen.add(name)
            for item in self.items:
                if (name, "R0", item.item_id, 0) in done or not self.eligible(item, condition):
                    continue
                context, requests, mask, text_record = self.condition_context(item, condition)
                parsed = dict(reference_answer(item, context), parse_error=None, dropped_claims=0)
                row = self._row2(item, name, condition, "R0", 0, context, requests, mask, text_record)
                row.update({"error": None, "answer": parsed["answer"],
                            "insufficient_evidence": parsed["insufficient_evidence"], "n_llm_calls": 0})
                row.update(score_answer(item, parsed, context, self.reference))
                self._write(row)
                written += 1
        return written

    def plan_systems(self, models):
        """
        (model, condition, item, repetition) still to run, interleaved by item.
        Conditions run in configuration order, so G-QA comes before G-QA+V and
        G-QA+V's first call is G-QA's cached call. Unmatchable controls are skipped.
        """
        done = self._done(models)
        self.skipped = {}
        plan = []
        for spec in models:
            for item in self.items:
                for rep in self.repetitions(item):
                    for condition in self.conditions:
                        if not self.eligible(item, condition):
                            self.skipped.setdefault(condition.name, set()).add(item.item_id)
                            continue
                        if (condition.name, spec["name"], item.item_id, rep) not in done:
                            plan.append((spec, condition, item, rep))
        return plan

    def _metadata2(self, item, condition, context):
        return {
            "item_id": item.item_id, "task": item.task, "concept_id": item.concept_id,
            "condition": condition.retrieval, "format": condition.render_format,
            "sufficient": sufficient(item, context), "reference_answer": reference_answer(item, context),
            "gold_answer": _gold_answer_for_mock(item, self.gold), "children_retrieved": item.concept_id in context.children_status,
        }

    def run_systems(self, models, llm_cache, max_calls=None, max_cost=None):
        clients = {spec["name"]: CachedClient(build_client(spec), llm_cache) for spec in models}
        plan = self.plan_systems(models)
        logger.info("%d condition answers to collect", len(plan))
        made, self.spent_usd, self.stopped_reason = 0, 0.0, None
        for spec, condition, item, rep in plan:
            if max_calls is not None and made >= max_calls:
                logger.info("Stopping after %d new calls (max_calls)", made)
                break
            if max_cost is not None and self.spent_usd >= max_cost:
                logger.info("Stopping: spending reached US$%.4f (max_cost %.2f)", self.spent_usd, max_cost)
                break
            context, requests, mask, text_record = self.condition_context(item, condition)
            llm = _RunLLM(clients[spec["name"]], self._metadata2(item, condition, context), rep)
            agent = HierarchyAgent(llm, self.local, label_of=self._label, evidence_format=condition.render_format,
                                   request_budget=self.request_budget)
            started = time.time()
            result = agent.answer(item, condition.agent_system, context=context, retrieval_requests=requests)

            row = self._row2(item, condition.name, condition, spec["name"], rep, context, requests, mask, text_record)
            row.update({"model_id": spec.get("model"), "provider": spec["provider"],
                        "temperature": spec.get("temperature", 0.0), "requested_at": started,
                        "model_settings": model_settings(spec)})
            new_calls = [r for r in llm.responses if not r.cached]
            cost = sum(call_cost(spec, r) or 0.0 for r in new_calls)
            row.update({"n_llm_calls": len(llm.responses), "n_new_calls": len(new_calls),
                        "cached": bool(llm.responses) and not new_calls, "cost_usd": cost if new_calls else None})
            made += len(new_calls)
            self.spent_usd += cost

            if result.error is not None:
                row["error"] = result.error
                self._write(row)
                logger.warning("%s/%s on %s: %s", spec["name"], condition.name, item.item_id, result.error)
                if "daily quota exhausted" in result.error:
                    self.stopped_reason = "daily quota exhausted"
                    logger.warning("Stopping: the provider's daily quota is used up. Run the same command again "
                                   "after it resets; finished answers are kept and never repeated.")
                    break
                continue

            row.update({
                "error": None, "response_model": result.model,
                "prompt_tokens": result.total_prompt_tokens, "completion_tokens": result.total_completion_tokens,
                "reasoning_tokens": result.total_reasoning_tokens, "latency_s": result.total_llm_latency_s,
                "finish_reason": result.finish_reason, "first_prompt_tokens": result.prompt_tokens,
                "first_completion_tokens": result.completion_tokens, "raw_text": result.raw_text,
            })
            first = dict(result.parsed, claims=result.claims)
            if condition.agent_system == "S2":
                final = result.final
                parsed = {"answer": final["answer"], "claims": final["claims"],
                          "insufficient_evidence": final["insufficient_evidence"],
                          "parse_error": first["parse_error"], "dropped_claims": first.get("dropped_claims", 0),
                          "dropped_citations": first.get("dropped_citations", 0)}
                regen = result.regeneration or {}
                row.update({
                    "regenerated": result.regenerated,
                    "problems_first": {k: len(v) for k, v in (result.problems or {}).items()},
                    "problems_left": regen.get("problems_left"), "regeneration_error": regen.get("error"),
                    "regeneration_raw_text": regen.get("raw_text"), "n_removed_claims": len(final["removed_claims"]),
                    "citations_repaired": final["citations_repaired"], "answer_replaced": final["answer_replaced"],
                })
            else:
                parsed = first
            row.update({"answer": parsed["answer"], "insufficient_evidence": parsed["insufficient_evidence"]})
            row.update(score_answer(item, parsed, context, self.reference))
            self._write(row)
        return made

    def estimate_systems(self, models):
        """New calls and rough prompt tokens still needed, per model and per condition."""
        summary = {}
        for spec, condition, item, rep in self.plan_systems(models):
            context, _, _, _ = self.condition_context(item, condition)
            text = context.render(condition.render_format) if condition.retrieval != "C0" else context.render("plain")
            messages = build_messages(item, text, self._label)
            tokens = estimate_tokens(" ".join(m["content"] for m in messages))
            entry = summary.setdefault(spec["name"], {"calls": 0, "prompt_tokens_est": 0, "max_completion_tokens": 0,
                                                      "by_condition": {}})
            regen = condition.agent_system == "S2"
            # G-QA+V's first call is G-QA's cached call; count at most one regeneration (about 1.5x the prompt)
            entry["calls"] += 1
            entry["prompt_tokens_est"] += int(tokens * (1.5 if regen else 1))
            entry["max_completion_tokens"] += spec.get("max_tokens", 8192)
            entry["by_condition"][condition.name] = entry["by_condition"].get(condition.name, 0) + 1
        for name, ids in self.skipped.items():
            logger.info("%s: %d items skipped (random control could not be matched)", name, len(ids))
        return summary
