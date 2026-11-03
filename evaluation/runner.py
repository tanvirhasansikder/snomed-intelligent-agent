"""
Experiment runner (protocol Sections 5, 6 and 10).

    python -m evaluation.runner --config configs/pilot.json --dry-run   # count calls and tokens, no API use
    python -m evaluation.runner --config configs/pilot.json             # run (resumes where it stopped)

Every model response is appended to ``<results_dir>/responses.jsonl`` with
its full context: condition, format, sufficiency, raw text, parsed answer,
labelled claims and scores. LLM responses are also cached by exact request
in ``llm_cache_path``, so an interrupted or repeated run never pays twice.
The deterministic reference system R0 is scored alongside the models.
"""

import argparse
import json
import logging
import random
import time

from evaluation.bioportal_cache import CallStore, SnapshotClient
from evaluation.collect import PROJECT_ROOT, git_revision, load_config, resolve
from evaluation.conditions import CONDITIONS, FORMATS, build_context
from evaluation.gold import GoldIndex
from evaluation.scoring import score_answer
from evaluation.tasks import TASKS, generate_items, reference_answer, sufficient
from llm.client import CachedClient, LLMError, ResponseCache, estimate_tokens
from llm.prompt_manager import PROMPT_VERSION, build_messages
from llm.providers import build_client
from llm.reasoner import parse_answer

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "1.0"


def _gold_answer_for_mock(item, gold):
    """Correct answer in response format; used only by the MockLLM."""
    x = item.concept_id
    claims = []
    if item.task == "T1":
        answer = sorted(gold.parents.get(x, []))
        claims = [{"subject": x, "relation": "direct_parent", "object": p, "source": "model_knowledge"} for p in answer]
    elif item.task == "T2":
        answer = list(item.gold)
        claims = [{"subject": c, "relation": "direct_parent", "object": x, "source": "model_knowledge"} for c in answer]
    elif item.task == "T3":
        answer = "yes" if item.gold else "no"
        if item.gold:
            claims = [{"subject": x, "relation": "ancestor", "object": item.target_id, "source": "model_knowledge"}]
    elif item.task == "T4":
        return None
    else:
        answer = "Synthetic explanation."
        claims = [{"subject": x, "relation": "direct_parent", "object": p, "source": "model_knowledge"}
                  for p in sorted(gold.parents.get(x, []))]
    return {"answer": answer, "claims": claims, "insufficient_evidence": False}


class Experiment:

    def __init__(self, config):
        self.config = config
        self.exp = config["experiment"]
        self.store = CallStore(resolve(config, "cache_path"))
        dataset_path = resolve(config, "dataset_path")
        if not dataset_path.exists():
            raise FileNotFoundError(f"Data set not found: {dataset_path}. Run evaluation.collect first.")
        self.dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
        self.gold = GoldIndex(self.store, self.dataset)
        self.reference = self.gold.reference()
        self.snapshot = SnapshotClient(self.store)
        self.items = self._select_items()
        self.conditions = self.exp.get("conditions", list(CONDITIONS))
        self.formats = self.exp.get("formats", list(FORMATS))
        self._contexts = {}
        self.results_dir = resolve(config, "results_dir")
        self.responses_path = self.results_dir / "responses.jsonl"

    def _select_items(self):
        items = generate_items(self.gold, tuple(self.exp.get("tasks", TASKS)), seed=self.exp.get("item_seed", 0))
        limit = self.exp.get("limit_concepts")
        if limit:
            concepts = sorted({i.concept_id for i in items})
            keep = set(random.Random(self.exp.get("item_seed", 0)).sample(concepts, min(limit, len(concepts))))
            items = [i for i in items if i.concept_id in keep]
        return items

    def context(self, concept_id, condition):
        key = (concept_id, condition)
        if key not in self._contexts:
            self._contexts[key] = build_context(self.snapshot, concept_id, condition,
                                                child_display_cap=self.exp.get("child_display_cap", 100))
        return self._contexts[key]

    def repetitions(self, item):
        fraction = self.exp.get("repeat_subset_fraction", 0.0)
        count = self.exp.get("repeat_count", 1)
        if count > 1 and fraction > 0:
            roll = random.Random(f"repeat:{self.exp.get('item_seed', 0)}:{item.item_id}").random()
            if roll < fraction:
                return range(count)
        return range(1)

    def cells(self):
        """Yield (condition, format) pairs; C0 has no context and uses format 'none'."""
        for condition in self.conditions:
            if CONDITIONS[condition] is None:
                yield condition, "none"
            else:
                for fmt in self.formats:
                    yield condition, fmt

    def _label(self, concept_id):
        return self.gold.label(concept_id)

    def _base_row(self, item, condition, fmt, context):
        return {
            "protocol_version": PROTOCOL_VERSION,
            "prompt_version": PROMPT_VERSION,
            "experiment": self.config["name"],
            "item_id": item.item_id,
            "task": item.task,
            "polarity": item.polarity,
            "concept_id": item.concept_id,
            "target_id": item.target_id,
            **{f"meta_{k}": v for k, v in item.meta.items()},
            "condition": condition,
            "format": fmt,
            "sufficient": sufficient(item, context),
            "context_nodes": len(context.nodes),
            "context_edges": len(context.edges),
        }

    def plan(self, models):
        """List of (model_spec, item, condition, fmt, repetition) still to run."""
        done = self._done_keys()
        plan = []
        for spec in models:
            for condition, fmt in self.cells():
                for item in self.items:
                    for rep in self.repetitions(item):
                        key = (spec["name"], item.item_id, condition, fmt, rep)
                        if key not in done:
                            plan.append((spec, item, condition, fmt, rep))
        return plan

    def _done_keys(self):
        done = set()
        if self.responses_path.exists():
            with self.responses_path.open(encoding="utf-8") as handle:
                for line in handle:
                    try:
                        r = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if r.get("error") is None:
                        done.add((r["model_name"], r["item_id"], r["condition"], r["format"], r["repetition"]))
        return done

    def _write(self, row):
        self.results_dir.mkdir(parents=True, exist_ok=True)
        with self.responses_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

    # ------------------------------------------------------------------

    def run_reference(self):
        """Score R0 (deterministic, evidence-only) for every item and cell."""
        done = self._done_keys()
        written = 0
        for condition, fmt in self.cells():
            for item in self.items:
                if ("R0", item.item_id, condition, fmt, 0) in done:
                    continue
                context = self.context(item.concept_id, condition)
                parsed = dict(reference_answer(item, context), parse_error=None, dropped_claims=0)
                row = self._base_row(item, condition, fmt, context)
                row.update({"model_name": "R0", "model_id": "deterministic-reference", "provider": "none",
                            "repetition": 0, "error": None, "raw_text": None, "answer": parsed["answer"],
                            "insufficient_evidence": parsed["insufficient_evidence"]})
                row.update(score_answer(item, parsed, context, self.reference))
                self._write(row)
                written += 1
        return written

    def run_models(self, models, llm_cache, max_calls=None, max_cost=None):
        clients = {spec["name"]: CachedClient(build_client(spec), llm_cache) for spec in models}
        plan = self.plan(models)
        logger.info("%d responses to collect", len(plan))
        made = 0
        self.spent_usd = 0.0
        for spec, item, condition, fmt, rep in plan:
            if max_calls is not None and made >= max_calls:
                logger.info("Stopping after %d calls (max_calls)", made)
                break
            if max_cost is not None and self.spent_usd >= max_cost:
                logger.info("Stopping: spending reached US$%.4f (max_cost %.2f)", self.spent_usd, max_cost)
                break
            context = self.context(item.concept_id, condition)
            text = context.render(fmt if fmt != "none" else "plain")
            messages = build_messages(item, text, self._label)
            reference = reference_answer(item, context)
            metadata = {
                "item_id": item.item_id, "task": item.task, "concept_id": item.concept_id,
                "condition": condition, "format": fmt, "sufficient": sufficient(item, context),
                "reference_answer": reference, "gold_answer": _gold_answer_for_mock(item, self.gold),
                "children_retrieved": item.concept_id in context.children_status,
            }
            row = self._base_row(item, condition, fmt, context)
            client = clients[spec["name"]]
            row.update({"model_name": spec["name"], "model_id": spec.get("model"), "provider": spec["provider"],
                        "temperature": spec.get("temperature", 0.0), "repetition": rep})
            started = time.time()
            try:
                response = client.complete(messages, metadata=metadata, repetition=rep)
            except LLMError as exc:
                row.update({"error": str(exc), "requested_at": started})
                self._write(row)
                logger.warning("%s on %s/%s/%s: %s", spec["name"], item.item_id, condition, fmt, exc)
                continue
            parsed = parse_answer(response.text, item.task)
            row.update({
                "error": None, "requested_at": started, "response_model": response.model,
                "prompt_tokens": response.prompt_tokens, "completion_tokens": response.completion_tokens,
                "latency_s": response.latency_s, "finish_reason": response.finish_reason,
                "cached": response.cached, "served_by": response.served_by,
                "cost_usd": None if response.cached else call_cost(spec, response),
                "raw_text": response.text, "answer": parsed["answer"],
                "insufficient_evidence": parsed["insufficient_evidence"],
            })
            row.update(score_answer(item, parsed, context, self.reference))
            self._write(row)
            if not response.cached:
                made += 1
                self.spent_usd += row["cost_usd"] or 0.0
        return made

    def estimate(self, models):
        """Calls and rough token counts still needed, per model (no API use)."""
        from llm.client import LLMConfigurationError

        llm_cache = ResponseCache(resolve(self.config, "llm_cache_path"))
        fingerprints = {}
        for spec in models:
            try:
                fingerprints[spec["name"]] = build_client(spec).request_fingerprint()
            except LLMConfigurationError:
                fingerprints[spec["name"]] = None  # key not set yet: cannot check the cache
        summary = {}
        for spec, item, condition, fmt, rep in self.plan(models):
            context = self.context(item.concept_id, condition)
            messages = build_messages(item, context.render(fmt if fmt != "none" else "plain"), self._label)
            entry = summary.setdefault(spec["name"], {"calls": 0, "prompt_tokens_est": 0,
                                                      "max_completion_tokens": 0, "already_cached": 0})
            base = fingerprints[spec["name"]]
            fingerprint = dict(base, repetition=rep) if base else None
            if fingerprint and llm_cache.get(ResponseCache.key(fingerprint, messages)):
                entry["already_cached"] += 1
                continue
            entry["calls"] += 1
            entry["prompt_tokens_est"] += estimate_tokens(" ".join(m["content"] for m in messages))
            entry["max_completion_tokens"] += spec.get("max_tokens", 800)
        for spec in models:
            entry = summary.get(spec["name"])
            price = spec.get("price_per_mtok")
            if entry and price:
                typical_out = entry["calls"] * spec.get("typical_completion_tokens", 300)
                entry["cost_typical_usd"] = (entry["prompt_tokens_est"] * price[0] + typical_out * price[1]) / 1e6
                entry["cost_worst_usd"] = (entry["prompt_tokens_est"] * price[0]
                                           + entry["max_completion_tokens"] * price[1]) / 1e6
        return summary


def call_cost(spec, response):
    """
    Cost of one call in US$: the provider's own figure when it reports one,
    otherwise tokens x the configured ``price_per_mtok`` [input, output].
    None when neither is available (e.g. a local model).
    """
    if response.cost_usd is not None:
        return float(response.cost_usd)
    price = spec.get("price_per_mtok")
    if not price or response.prompt_tokens is None:
        return None
    return (response.prompt_tokens * price[0] + (response.completion_tokens or 0) * price[1]) / 1e6


def _main(argv=None):
    parser = argparse.ArgumentParser(description="Run the experiment defined in a configuration file.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--dry-run", action="store_true", help="Only estimate calls and tokens")
    parser.add_argument("--models", nargs="*", help="Run only these model names from the configuration")
    parser.add_argument("--max-calls", type=int, default=None, help="Stop after this many new API calls")
    parser.add_argument("--max-cost", type=float, default=None,
                        help="Stop once this run has spent this many US dollars (provider-reported or estimated)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    for noisy in ("crawler", "knowledge_graph", "database"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    try:
        from dotenv import load_dotenv
        load_dotenv(PROJECT_ROOT / ".env")
    except ImportError:
        pass

    config = load_config(args.config)
    models = [m for m in config.get("models", []) if not args.models or m["name"] in args.models]
    experiment = Experiment(config)
    print(f"Items: {len(experiment.items)} | concepts: {len({i.concept_id for i in experiment.items})} | "
          f"cells: {len(list(experiment.cells()))} | models: {[m['name'] for m in models]}")

    if args.dry_run:
        for name, entry in experiment.estimate(models).items():
            print(f"{name}: {entry['calls']} new calls, ~{entry['prompt_tokens_est']:,} prompt tokens, "
                  f"<= {entry['max_completion_tokens']:,} completion tokens, {entry['already_cached']} cached")
            if "cost_typical_usd" in entry:
                print(f"    estimated cost: ~US${entry['cost_typical_usd']:.2f} typical, "
                      f"<= US${entry['cost_worst_usd']:.2f} if every answer used max_tokens")
        return

    run_info = {"config": {k: v for k, v in config.items() if not k.startswith("_")},
                "code_revision": git_revision(), "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "dataset_release": experiment.dataset.get("release"),
                "dataset_synthetic": experiment.dataset.get("synthetic")}
    experiment.results_dir.mkdir(parents=True, exist_ok=True)
    with (experiment.results_dir / "runs.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(run_info, default=str) + "\n")

    print(f"R0 rows written: {experiment.run_reference()}")
    llm_cache = ResponseCache(resolve(config, "llm_cache_path"))
    made = experiment.run_models(models, llm_cache, max_calls=args.max_calls, max_cost=args.max_cost)
    print(f"New API calls: {made}. Spent this run: ~US${experiment.spent_usd:.4f}. "
          f"Results: {experiment.responses_path}")


if __name__ == "__main__":
    _main()
