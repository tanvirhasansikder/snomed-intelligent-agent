"""
Read-only pilot checks (protocol v2.0, pilot review). Changes nothing.

    python -m evaluation.pilot_diagnostics --config configs/pilot.json

1. Children in the frozen reference for every sampled concept (why T2 items exist or not).
2. Repeated answers: do repetitions differ, or only in list order?
3. When each system ran (API latency depends on time of day, not only on the system).

Prints concept IDs and counts only, never labels or free text.
"""

import argparse
import datetime
import json
from collections import defaultdict

from evaluation.runner import Experiment, load_config
from evaluation.system_report import load_system_rows
from evaluation.tasks import MAX_GOLD_CHILDREN


def _children_report(experiment):
    print("1. Children of each sampled concept in the frozen reference")
    store = {(r["method"], r["arg"]): r["result"] for r in experiment.store.records.values()}
    for sample in experiment.dataset["samples"]:
        cid = sample["concept_id"]
        result = store.get(("fetch_children", cid))
        if result is None:
            print(f"   {cid}: children NOT COLLECTED")
            continue
        n = len(result.get("children") or [])
        status = "complete" if result.get("complete") else "INCOMPLETE"
        too_many = f" (more than {MAX_GOLD_CHILDREN})" if n > MAX_GOLD_CHILDREN else ""
        print(f"   {cid}: {n} children, {status}, pages_fetched={result.get('pages_fetched')}{too_many}")
    t2 = sum(1 for i in experiment.items if i.task == "T2")
    print(f"   T2 items generated: {t2}")


def _normalise(answer):
    if isinstance(answer, list):
        return json.dumps(sorted(json.dumps(a, sort_keys=True) for a in answer))
    return json.dumps(answer, sort_keys=True)


def _repeat_report(rows):
    print("\n2. Repeated answers (same system, item and model; temperature 0)")
    groups = defaultdict(list)
    for r in rows:
        if r.get("error") is None and r["model_name"] != "R0":
            groups[(r["system"], r["item_id"])].append(r)
    for (system, item), rs in sorted(groups.items()):
        if len(rs) < 2:
            continue
        exact = len({json.dumps(r.get("answer"), sort_keys=True) for r in rs}) == 1
        as_sets = len({_normalise(r.get("answer")) for r in rs}) == 1
        abstain = [bool(r.get("insufficient_evidence")) for r in rs]
        sizes = [len(r["answer"]) if isinstance(r.get("answer"), list) else "-" for r in rs]
        verdict = "identical" if exact else ("same set, different order" if as_sets else "DIFFERENT")
        print(f"   {system:3} {item}: {verdict}; answer sizes {sizes}; abstained {abstain}; "
              f"scores {[r.get('score') for r in rs]}")


def _timing_report(rows):
    print("\n3. When each system ran (local time) and its mean API latency")
    by_system = defaultdict(list)
    for r in rows:
        if r.get("requested_at") and r.get("error") is None and not r.get("cached"):
            by_system[r["system"]].append(r)
    for system, rs in sorted(by_system.items()):
        times = sorted(r["requested_at"] for r in rs)
        lat = [r["latency_s"] for r in rs if r.get("latency_s") is not None]
        fmt = lambda t: datetime.datetime.fromtimestamp(t).strftime("%H:%M")  # noqa: E731
        print(f"   {system:3}: {len(rs)} answers with a new API call, from {fmt(times[0])} to {fmt(times[-1])}, "
              f"mean latency {sum(lat) / len(lat):.1f} s")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    config = load_config(args.config)
    experiment = Experiment(config)
    _children_report(experiment)
    path = experiment.results_dir / "system_responses.jsonl"
    if path.exists():
        rows = load_system_rows(path)
        _repeat_report(rows)
        _timing_report(rows)


if __name__ == "__main__":
    main()
