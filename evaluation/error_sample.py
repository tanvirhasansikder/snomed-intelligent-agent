"""Draw the 30-item error-analysis sample (Protocol Amendment 6).

Reads results/main/system_responses.jsonl and never modifies it. Without
--write it only prints the stratum sizes. With --write it creates
results/main/error_analysis/sample.csv and refuses to overwrite it.
"""
import csv
import json
import random
import sys
from pathlib import Path

SEED = 2026111301
SRC = Path("results/main/system_responses.jsonl")
OUT = Path("results/main/error_analysis/sample.csv")
QUOTAS = {"A_S1_false_claim": 10, "B_S1_imperfect_no_false": 10,
          "C_S1_perfect": 5, "D_S2_regenerated": 5}


def load():
    with SRC.open(encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    return [r for r in rows if r.get("repetition") == 0 and r.get("error") is None]


def has_false(row):
    return (row.get("n_false_in_reference") or 0) > 0 or (row.get("n_unknown_identifier") or 0) > 0


def strata(rows):
    s1 = [r for r in rows if r["system"] == "S1"]
    s2 = [r for r in rows if r["system"] == "S2"]
    scored = [r for r in s1 if not has_false(r) and r.get("score") is not None]
    out = {
        "A_S1_false_claim": [r for r in s1 if has_false(r)],
        "B_S1_imperfect_no_false": [r for r in scored if r["score"] < 1],
        "C_S1_perfect": [r for r in scored if r["score"] >= 1],
        "D_S2_regenerated": [r for r in s2 if (r.get("n_llm_calls") or 0) > 1],
    }
    return {name: sorted(items, key=lambda r: r["item_id"]) for name, items in out.items()}


def main():
    groups = strata(load())
    print("Eligible items per stratum (repetition 0):")
    for name, items in groups.items():
        print(f"  {name}: {len(items)} (quota {QUOTAS[name]})")
    if "--write" not in sys.argv:
        print("Dry run only. Add --write to draw the sample.")
        return
    if OUT.exists():
        sys.exit(f"{OUT} already exists; not overwriting.")
    rng = random.Random(SEED)
    fields = ["stratum", "system", "item_id", "task", "concept_id", "target_id", "score",
              "abstained", "n_false_in_reference", "n_unknown_identifier", "n_llm_calls",
              "answer", "claims", "reviewer_verdict", "failure_category",
              "verifier_label_correct", "notes"]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for name, items in groups.items():
            for row in rng.sample(items, min(QUOTAS[name], len(items))):
                record = {k: row.get(k) for k in fields[1:11]}
                record.update(stratum=name, answer=json.dumps(row.get("answer")),
                              claims=json.dumps(row.get("claims")))
                writer.writerow(record)
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
