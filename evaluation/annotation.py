"""
Human evaluation support (protocol Section 9).

Export a stratified random sample of T5 explanations for two annotators:

    python -m evaluation.annotation export --config configs/main.json --n 100

Each annotator fills their own copy of the CSV independently:

* extra_claim         yes/no - does the free text state a hierarchical claim
                                that is NOT in the structured claim list?
* contradicts_claims  yes/no - does the free text contradict the structured
                                claims or the answer (e.g. text says "is a
                                kind of", claims say it is not)?
* notes               free text

Both questions compare the model's text with its own structured output, so
they need no clinical expertise (protocol Amendment 1). Annotators are blind
to model, condition and format; those stay in sample_key.json.

Then compute agreement:

    python -m evaluation.annotation kappa --a annotator_A.csv --b annotator_B.csv
"""

import argparse
import csv
import json
import random
from collections import defaultdict

from evaluation.collect import load_config, resolve
from evaluation.report import load_rows
from evaluation.stats import cohen_kappa

FIELDS = ["annotation_id", "concept_id", "explanation", "structured_claims",
          "extra_claim", "contradicts_claims", "notes"]
QUESTIONS = ("extra_claim", "contradicts_claims")


def export(config, n, seed=0):
    rows = [r for r in load_rows(resolve(config, "results_dir") / "responses.jsonl")
            if r["task"] == "T5" and r.get("error") is None and r["model_name"] != "R0" and r.get("repetition", 0) == 0]
    strata = defaultdict(list)
    for r in rows:
        strata[(r["model_name"], r["condition"], r["format"])].append(r)
    rng = random.Random(seed)
    for group in strata.values():
        rng.shuffle(group)
    picked, keys = [], sorted(strata)
    while len(picked) < n and any(strata[k] for k in keys):
        for k in keys:
            if strata[k] and len(picked) < n:
                picked.append(strata[k].pop())
    rng.shuffle(picked)

    out = resolve(config, "results_dir") / "annotation"
    out.mkdir(parents=True, exist_ok=True)
    for annotator in ("A", "B"):
        with (out / f"annotator_{annotator}.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            for i, r in enumerate(picked, 1):
                claims = "; ".join(f"{c['relation']}({c['subject']}{', ' + c['object'] if c['object'] else ''})"
                                   for c in r.get("claims", []))
                writer.writerow({"annotation_id": i, "concept_id": r["concept_id"],
                                 "explanation": r.get("answer") or "", "structured_claims": claims,
                                 "extra_claim": "", "contradicts_claims": "", "notes": ""})
    key = [{"annotation_id": i, "item_id": r["item_id"], "model": r["model_name"], "condition": r["condition"],
            "format": r["format"]} for i, r in enumerate(picked, 1)]
    (out / "sample_key.json").write_text(json.dumps(key, indent=2), encoding="utf-8")
    return out, len(picked)


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return {row["annotation_id"]: row for row in csv.DictReader(handle)}


def agreement(path_a, path_b):
    a, b = _read(path_a), _read(path_b)
    ids = sorted(set(a) & set(b), key=int)
    result = {}
    for field in QUESTIONS:
        pairs = [(a[i][field].strip().lower(), b[i][field].strip().lower()) for i in ids
                 if a[i][field].strip() and b[i][field].strip()]
        if pairs:
            result[field] = {"n": len(pairs), "kappa": cohen_kappa([x for x, _ in pairs], [y for _, y in pairs]),
                             "raw_agreement": sum(x == y for x, y in pairs) / len(pairs),
                             "rate_yes_A": sum(x == "yes" for x, _ in pairs) / len(pairs),
                             "rate_yes_B": sum(y == "yes" for _, y in pairs) / len(pairs)}
    return result


def _main(argv=None):
    parser = argparse.ArgumentParser(description="Human annotation export and agreement.")
    sub = parser.add_subparsers(dest="command", required=True)
    e = sub.add_parser("export")
    e.add_argument("--config", required=True)
    e.add_argument("--n", type=int, default=100)
    k = sub.add_parser("kappa")
    k.add_argument("--a", required=True)
    k.add_argument("--b", required=True)
    args = parser.parse_args(argv)
    if args.command == "export":
        out, n = export(load_config(args.config), args.n)
        print(f"Exported {n} explanations to {out}")
    else:
        print(json.dumps(agreement(args.a, args.b), indent=2))


if __name__ == "__main__":
    _main()
