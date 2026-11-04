"""
System runner for protocol v2.0 (Section 6): R0, B0, B1, S1 and S2 through
the shared single-question pipeline (llm/agent.py), so the benchmark measures
exactly what the demo does.

Selected automatically by evaluation.runner when the configuration's
``experiment`` section lists ``"systems"``, e.g.

    "experiment": {"systems": ["R0", "B0", "B1", "S1", "S2"],
                   "tasks": ["T1", "T2", "T3", "T4", "T5", "T6"],
                   "request_budget": 60, "item_seed": 0,
                   "repeat_subset_fraction": 0.1, "repeat_count": 3}

* R0 is scored on B1's evidence (``R0-B1``) and on S1's (``R0-S1``).
* B0, B1 and S1 are scored on their single answer.
* S2 is scored on its FINAL answer (after at most one regeneration and the
  deterministic filter). Its first call is the same cached request as S1's,
  so S1 and S2 pair exactly and S1 is never paid for twice.

Rows go to ``<results_dir>/system_responses.jsonl``, one per
(system, model, item, repetition). A row that failed (API error) is retried
on the next run; a finished one never is.
"""

import json
import logging
import time

from evaluation.runner import Experiment, _gold_answer_for_mock, call_cost
from evaluation.scoring import score_answer
from evaluation.tasks import answer_in_evidence, reference_answer, sufficient
from llm.agent import RETRIEVAL, HierarchyAgent
from llm.client import CachedClient, estimate_tokens
from llm.prompt_manager import PROMPT_VERSION, REGENERATION_VERSION, build_messages
from llm.providers import build_client
from query_engine.retrieval_planner import DEFAULT_BUDGET

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "2.0"
LLM_SYSTEMS = ("B0", "B1", "S1", "S2")
#: R0 variants: system name -> evidence it answers from
R0_VARIANTS = {"R0-B1": "B1", "R0-S1": "S1"}


class _RunLLM:
    """
    Adapter the pipeline calls as ``llm.complete(messages)``. It adds the
    repetition number to the cache key, passes the MockLLM its metadata,
    and records every response (an S2 question may make two calls).
    """

    def __init__(self, client, metadata, repetition):
        self.client, self.metadata, self.repetition = client, metadata, repetition
        self.responses = []

    def complete(self, messages):
        response = self.client.complete(messages, metadata=self.metadata, repetition=self.repetition)
        self.responses.append(response)
        return response


class SystemExperiment(Experiment):

    def __init__(self, config):
        super().__init__(config)
        self.systems = list(self.exp["systems"])
        unknown = [s for s in self.systems if s != "R0" and s not in LLM_SYSTEMS]
        if unknown:
            raise ValueError(f"Unknown systems {unknown}; choose from R0, {', '.join(LLM_SYSTEMS)}")
        self.request_budget = self.exp.get("request_budget", DEFAULT_BUDGET)
        self.responses_path = self.results_dir / "system_responses.jsonl"
        self._system_contexts = {}
        self._retriever = HierarchyAgent(llm=None, retrieval_client=self.snapshot, label_of=self._label,
                                         request_budget=self.request_budget)

    # ------------------------------------------------------------------
    def system_context(self, item, system):
        """(context, BioPortal requests it took); one retrieval per item and retrieval kind."""
        retrieval = RETRIEVAL[system]
        key = (item.item_id, retrieval)
        if key not in self._system_contexts:
            before = self.snapshot.request_count
            context = self._retriever.retrieve(item, system)
            self._system_contexts[key] = (context, self.snapshot.request_count - before)
        return self._system_contexts[key]

    def _row(self, item, system, model_name, rep, context, requests):
        return {
            "protocol_version": PROTOCOL_VERSION,
            "prompt_version": PROMPT_VERSION,
            "regeneration_version": REGENERATION_VERSION,
            "experiment": self.config["name"],
            "system": system,
            "retrieval": RETRIEVAL.get(system) if system in RETRIEVAL else RETRIEVAL[R0_VARIANTS[system]],
            "model_name": model_name,
            "repetition": rep,
            "item_id": item.item_id,
            "task": item.task,
            "polarity": item.polarity,
            "concept_id": item.concept_id,
            "target_id": item.target_id,
            **{f"meta_{k}": v for k, v in item.meta.items()},
            "sufficient": sufficient(item, context),
            "answer_in_evidence": answer_in_evidence(item, context),
            "retrieval_requests": requests,
            "request_budget": self.request_budget if RETRIEVAL.get(system, "") == "S1" or system == "R0-S1" else None,
            "budget_stopped": len(getattr(context, "budget_stopped", ()) or ()),
            "context_nodes": len(context.nodes),
            "context_edges": len(context.edges),
        }

    def _done(self):
        done = set()
        if self.responses_path.exists():
            with self.responses_path.open(encoding="utf-8") as handle:
                for line in handle:
                    try:
                        r = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if r.get("error") is None:
                        done.add((r["system"], r["model_name"], r["item_id"], r["repetition"]))
        return done

    # ------------------------------------------------------------------
    def run_reference(self):
        """R0 on B1's and on S1's evidence (deterministic, no model)."""
        if "R0" not in self.systems:
            return 0
        done, written = self._done(), 0
        for name, evidence_system in R0_VARIANTS.items():
            for item in self.items:
                if (name, "R0", item.item_id, 0) in done:
                    continue
                context, requests = self.system_context(item, evidence_system)
                parsed = dict(reference_answer(item, context), parse_error=None, dropped_claims=0)
                row = self._row(item, name, "R0", 0, context, requests)
                row.update({"error": None, "answer": parsed["answer"],
                            "insufficient_evidence": parsed["insufficient_evidence"], "n_llm_calls": 0})
                row.update(score_answer(item, parsed, context, self.reference))
                self._write(row)
                written += 1
        return written

    def plan_systems(self, models):
        """(model_spec, system, item, repetition) still to run, S1 before S2 so S2 reuses S1's call."""
        done = self._done()
        order = [s for s in LLM_SYSTEMS if s in self.systems]
        return [(spec, system, item, rep)
                for spec in models for system in order for item in self.items
                for rep in self.repetitions(item)
                if (system, spec["name"], item.item_id, rep) not in done]

    def _metadata(self, item, system, context):
        return {
            "item_id": item.item_id, "task": item.task, "concept_id": item.concept_id,
            "condition": RETRIEVAL[system], "format": "v2", "sufficient": sufficient(item, context),
            "reference_answer": reference_answer(item, context),
            "gold_answer": _gold_answer_for_mock(item, self.gold),
            "children_retrieved": item.concept_id in context.children_status,
        }

    def run_systems(self, models, llm_cache, max_calls=None, max_cost=None):
        clients = {spec["name"]: CachedClient(build_client(spec), llm_cache) for spec in models}
        plan = self.plan_systems(models)
        logger.info("%d system answers to collect", len(plan))
        made, self.spent_usd = 0, 0.0
        for spec, system, item, rep in plan:
            if max_calls is not None and made >= max_calls:
                logger.info("Stopping after %d new calls (max_calls)", made)
                break
            if max_cost is not None and self.spent_usd >= max_cost:
                logger.info("Stopping: spending reached US$%.4f (max_cost %.2f)", self.spent_usd, max_cost)
                break
            context, requests = self.system_context(item, system)
            llm = _RunLLM(clients[spec["name"]], self._metadata(item, system, context), rep)
            agent = HierarchyAgent(llm, self.snapshot, label_of=self._label, request_budget=self.request_budget)
            started = time.time()
            result = agent.answer(item, system, context=context, retrieval_requests=requests)

            row = self._row(item, system, spec["name"], rep, context, requests)
            row.update({"model_id": spec.get("model"), "provider": spec["provider"],
                        "temperature": spec.get("temperature", 0.0), "requested_at": started})
            new_calls = [r for r in llm.responses if not r.cached]
            cost = sum(call_cost(spec, r) or 0.0 for r in new_calls)
            row.update({"n_llm_calls": len(llm.responses), "n_new_calls": len(new_calls),
                        "cached": bool(llm.responses) and not new_calls, "cost_usd": cost if new_calls else None})
            made += len(new_calls)
            self.spent_usd += cost

            if result.error is not None:
                row["error"] = result.error
                self._write(row)
                logger.warning("%s/%s on %s: %s", spec["name"], system, item.item_id, result.error)
                continue

            row.update({
                "error": None, "response_model": result.model,
                "prompt_tokens": result.total_prompt_tokens, "completion_tokens": result.total_completion_tokens,
                "latency_s": result.total_llm_latency_s, "finish_reason": result.finish_reason,
                "first_prompt_tokens": result.prompt_tokens, "first_completion_tokens": result.completion_tokens,
                "raw_text": result.raw_text,
            })
            first = dict(result.parsed, claims=result.claims)
            if system == "S2":
                final = result.final
                parsed = {"answer": final["answer"], "claims": final["claims"],
                          "insufficient_evidence": final["insufficient_evidence"],
                          "parse_error": first["parse_error"], "dropped_claims": first.get("dropped_claims", 0),
                          "dropped_citations": first.get("dropped_citations", 0)}
                regen = result.regeneration or {}
                row.update({
                    "regenerated": result.regenerated,
                    "problems_first": {k: len(v) for k, v in (result.problems or {}).items()},
                    "problems_left": regen.get("problems_left"),
                    "regeneration_error": regen.get("error"),
                    "regeneration_raw_text": regen.get("raw_text"),
                    "n_removed_claims": len(final["removed_claims"]),
                    "citations_repaired": final["citations_repaired"],
                    "answer_replaced": final["answer_replaced"],
                })
            else:
                parsed = first
            row.update({"answer": parsed["answer"], "insufficient_evidence": parsed["insufficient_evidence"]})
            row.update(score_answer(item, parsed, context, self.reference))
            self._write(row)
        return made

    def estimate_systems(self, models):
        """Calls and rough prompt tokens still needed (S2 counted as one possible regeneration)."""
        summary = {}
        for spec, system, item, rep in self.plan_systems(models):
            context, _ = self.system_context(item, system)
            messages = build_messages(item, context.render("v2"), self._label)
            entry = summary.setdefault(spec["name"], {"calls": 0, "prompt_tokens_est": 0, "max_completion_tokens": 0,
                                                      "already_cached": 0})
            tokens = estimate_tokens(" ".join(m["content"] for m in messages))
            if system == "S2":   # first call = S1's cached call; at most one regeneration (about 1.5x the prompt)
                entry["calls"] += 1
                entry["prompt_tokens_est"] += int(tokens * 1.5)
            else:
                entry["calls"] += 1
                entry["prompt_tokens_est"] += tokens
            entry["max_completion_tokens"] += spec.get("max_tokens", 800)
        return summary
