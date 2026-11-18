"""
Check the completeness mask on real data before any model sees it (read-only, no API calls).

    python -m evaluation.completeness_check --db data\\snomed_db\\snomed.db --dataset data\\datasets\\v3_pilot.json

For every item it builds the G-QA (S1) evidence and reports, per task: the
number of answer-relevant edges, how many are withheld at each level, how
often the masked evidence is still sufficient or still contains the answer
(an alternative path survived), and whether the random-removal control can
be matched (enough edges the answer does not need). Prints counts only, so
the output contains no SNOMED CT terms.
"""

import argparse
import json
import logging
import statistics
import sys
from collections import defaultdict
from pathlib import Path

from crawler.local_client import LocalTerminologyClient
from evaluation.bioportal_cache import CallStore
from evaluation.completeness import CONTROL_LEVEL, LEVELS, mask_context
from evaluation.gold import GoldIndex
from evaluation.tasks import answer_in_evidence, generate_items, sufficient
from query_engine.retrieval_planner import build_s1_context

SEED = 2026111903


def main(argv=None):
    parser = argparse.ArgumentParser(description="Check the completeness mask on real data (no API calls)")
    parser.add_argument("--db", default="data/snomed_db/snomed.db")
    parser.add_argument("--dataset", default="data/datasets/v3_pilot.json")
    parser.add_argument("--cache", default="data/local_cache/v3_calls.jsonl")
    parser.add_argument("--budget", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args(argv)

    logging.getLogger("crawler").setLevel(logging.ERROR)
    client = LocalTerminologyClient.from_db(args.db)
    dataset = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    items = generate_items(GoldIndex(CallStore(args.cache), dataset), seed=0)
    print(f"{len(items)} items from {len(dataset['samples'])} concepts; mask seed {args.seed}\n")

    rows = defaultdict(list)
    for item in items:
        context = build_s1_context(client, item, budget=args.budget)
        row = {"n": None, "withheld": {}, "suff": {}, "aie": {}}
        for f in LEVELS:
            masked, record = mask_context(context, item, f, args.seed)
            row["n"] = record["n_relevant"]
            row["withheld"][f] = record["n_withheld"]
            row["suff"][f] = sufficient(item, masked)
            row["aie"][f] = answer_in_evidence(item, masked)
        ctrl, crec = mask_context(context, item, CONTROL_LEVEL, args.seed, pool="random")
        row["ctrl_matched"] = crec["n_short"] == 0 and crec["n_withheld"] > 0
        row["ctrl_aie"] = answer_in_evidence(item, ctrl)
        rows[item.task].append(row)

    head = "task  n   relevant edges (median)  withheld at " + "/".join(f"{int(f * 100)}%" for f in LEVELS)
    head += "   sufficient at each level   answer still in evidence   control matched (answer kept)"
    print(head)
    for task in sorted(rows):
        r = rows[task]
        n = len(r)
        withheld = "/".join(f"{statistics.median(x['withheld'][f] for x in r):g}" for f in LEVELS)
        suff = "/".join(str(sum(x["suff"][f] for x in r)) for f in LEVELS)
        aie = "/".join(str(sum(x["aie"][f] for x in r)) for f in LEVELS)
        matched = [x for x in r if x["ctrl_matched"]]
        print(f"{task:4}  {n:<3} {statistics.median(x['n'] for x in r):>8g}               {withheld:>14}"
              f"      {suff:>16}           {aie:>16}          {len(matched)}/{n} ({sum(x['ctrl_aie'] for x in matched)})")
    print("\nCounts only; no terms. 'control matched' = the random control could withhold the same number of edges "
          "without touching the answer's edges; in brackets, how many of those still contain the answer.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
