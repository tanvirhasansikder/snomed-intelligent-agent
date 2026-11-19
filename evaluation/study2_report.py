"""
Study 2 analysis (protocol v3): faithfulness under controlled evidence loss.

    python -m evaluation.study2_report results\\v3_pilot

Reads ``system_responses.jsonl`` written by evaluation/study2_runner.py and
writes ``analysis_study2/``:

* ``summary.csv``       per model x condition x task (and ALL): Study 1's
  measures (score, unsupported/false/fill-in rates with bootstrap CIs,
  abstention, citations, cost) plus the Study 2 measures below.
* ``outcomes.csv``      the three-way outcome (correct / abstained / wrong)
  by evidence sufficiency (after Joren et al., ICLR 2025).
* ``contrasts.csv``     the pre-specified paired tests for RQ1-RQ4, Holm-
  adjusted within each research question and model.

Study 2 measures (all deterministic, against the exact reference):

* **withheld-fact restatement** (RQ1): of the gold parents (T1) or children
  (T2) whose edge was withheld, the share the model still names. Restating a
  withheld fact is answering from memory, not from the evidence.
* **grounded refusal** (after Song et al., ICLR 2025): treating "evidence
  insufficient" as the positive class, the precision, recall and F1 of the
  model's abstentions. Our labels are exact, not NLI-judged.
* **over-closure / under-closure** (after Min et al., 2026): on T3 questions,
  answering "no" although the evidence cannot certify it (over), or not
  answering "no" although complete evidence certifies it (under).
* **invented IDs**: claims with an identifier unknown to the release, and
  cited identifiers that are not valid SNOMED CT IDs.

Pilot results are for checking the pipeline, never findings.
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from evaluation.report import _write_csv
from evaluation.stats import holm, mcnemar_exact, wilcoxon_signed_rank
from evaluation.system_report import (
    _mean, _rate, _summary_row, load_system_rows, n_false, n_unsupported, paired_ratio_diff_ci, ratio_ci,
)

CONDITION_ORDER = ("R0|G-QA", "R0|G-QA@50", "R0|G-QA@50:random", "R0|L-RAG", "R0|D-RAG", "R0|G-UP",
                   "B0", "G-UP", "L-RAG", "D-RAG", "G-QA", "G-QA:unmarked", "G-QA@25", "G-QA@50",
                   "G-QA@50:unmarked", "G-QA@50:random", "G-QA@75", "G-QA+V", "G-QA+V@25", "G-QA+V@50",
                   "G-QA+V@50:random", "G-QA+V@75")
BINARY_TASKS = ("T3", "T4", "T6")

#: (family, condition A, condition B, what the contrast asks) — pre-specified in protocol v3
CONTRASTS = (
    ("RQ1", "G-QA", "G-QA@50", "withholding half the needed links (marked evidence)"),
    ("RQ1", "G-QA@50:random", "G-QA@50", "answer-relevant vs random removal, same count (matched items)"),
    ("RQ1", "G-QA", "G-QA@50:random", "random removal of links the answer does not need"),
    ("RQ2", "G-QA@50", "G-QA@50:unmarked", "coverage marks removed, 50% withheld"),
    ("RQ2", "G-QA", "G-QA:unmarked", "coverage marks removed, complete evidence"),
    ("RQ3", "G-QA@50", "G-QA+V@50", "deterministic verification, 50% withheld"),
    ("RQ3", "G-QA", "G-QA+V", "deterministic verification, complete evidence"),
    ("RQ4", "L-RAG", "G-QA", "graph vs lexical card retrieval"),
    ("RQ4", "D-RAG", "G-QA", "graph vs dense card retrieval"),
    ("RQ4", "B0", "G-QA", "graph retrieval vs no evidence"),
)


# ----------------------------------------------------------------------
# per-row measures
# ----------------------------------------------------------------------

def outcome(row):
    """correct / abstained / wrong (an answer that is not fully correct)."""
    if row.get("error") is not None:
        return None
    if row.get("abstained"):
        return "abstained"
    score = row.get("score")
    return "correct" if score is not None and score >= 1.0 - 1e-9 else "wrong"


def withheld_answer_facts(row):
    """(gold facts withheld, of those the model still named) for T1 and T2; (0, 0) otherwise."""
    edges = row.get("mask_withheld_edges") or []
    x = row.get("concept_id")
    if row.get("task") == "T1":
        withheld = {p for c, p in edges if c == x}
    elif row.get("task") == "T2":
        withheld = {c for c, p in edges if p == x}
    else:
        return 0, 0
    answer = row.get("answer") if isinstance(row.get("answer"), list) else []
    return len(withheld), len(withheld & set(answer))


def closure_flags(row):
    """(over-closure, under-closure) for a T3 row, or (None, None)."""
    if row.get("task") != "T3" or row.get("error") is not None:
        return None, None
    said_no = row.get("answer") == "no" and not row.get("abstained")
    over = said_no and not row.get("sufficient")
    under = (row.get("sufficient") and row.get("polarity") != "pos" and not said_no)
    return bool(over), bool(under)


def grounded_refusal(rows):
    """Precision, recall and F1 of abstention with 'evidence insufficient' as the positive class."""
    ok = [r for r in rows if r.get("error") is None]
    tp = sum(1 for r in ok if r.get("abstained") and not r.get("sufficient"))
    fp = sum(1 for r in ok if r.get("abstained") and r.get("sufficient"))
    fn = sum(1 for r in ok if not r.get("abstained") and not r.get("sufficient"))
    precision = _rate(tp, tp + fp)
    recall = _rate(tp, tp + fn)
    f1 = (2 * precision * recall / (precision + recall)) if precision and recall else (0.0 if tp == 0 and (fp or fn) else None)
    return precision, recall, f1


def study2_measures(rows):
    ok = [r for r in rows if r.get("error") is None]
    withheld = [withheld_answer_facts(r) for r in ok]
    t3 = [closure_flags(r) for r in ok if r.get("task") == "T3"]
    t3_insufficient = [r for r in ok if r.get("task") == "T3" and not r.get("sufficient")]
    t3_certified_no = [r for r in ok if r.get("task") == "T3" and r.get("sufficient") and r.get("polarity") != "pos"]
    precision, recall, f1 = grounded_refusal(ok)
    restated, restated_lo, restated_hi = ratio_ci(ok, lambda r: withheld_answer_facts(r)[1],
                                                 lambda r: withheld_answer_facts(r)[0])
    return {
        "withheld_answer_facts": sum(w for w, _ in withheld),
        "withheld_restated": sum(s for _, s in withheld),
        "withheld_restated_rate": restated, "withheld_restated_ci_low": restated_lo,
        "withheld_restated_ci_high": restated_hi,
        "refusal_precision": precision, "refusal_recall": recall, "refusal_f1": f1,
        "answered_when_sufficient": _rate(sum(1 for r in ok if r.get("sufficient") and not r.get("abstained")),
                                          sum(1 for r in ok if r.get("sufficient"))),
        "over_closure_rate": _rate(sum(1 for o, _ in t3 if o), len(t3_insufficient)),
        "under_closure_rate": _rate(sum(1 for _, u in t3 if u), len(t3_certified_no)),
        "invalid_id_rate": _rate(sum(r.get("n_invalid_sctid", 0) for r in ok), sum(r.get("n_cited_ids", 0) for r in ok)),
        "items_with_unsupported_claim": _rate(sum(1 for r in ok if n_unsupported(r) > 0), len(ok)),
        "mean_evidence_chars": _mean([r.get("evidence_chars") for r in ok]),
        "mean_withheld_edges": _mean([r.get("mask_n_withheld") for r in ok if r.get("mask_n_withheld") is not None]),
    }


# ----------------------------------------------------------------------
# tables
# ----------------------------------------------------------------------

def _order(system):
    return CONDITION_ORDER.index(system) if system in CONDITION_ORDER else len(CONDITION_ORDER)


def summarise(rows):
    groups = defaultdict(list)
    for r in rows:
        if r.get("repetition", 0) != 0:
            continue
        groups[(r["model_name"], r["system"], r["task"])].append(r)
        groups[(r["model_name"], r["system"], "ALL")].append(r)
    out = []
    for (model, system, task), rs in sorted(groups.items(), key=lambda kv: (kv[0][0], _order(kv[0][1]), kv[0][1], kv[0][2])):
        row = _summary_row({"model": model, "system": system, "task": task}, rs)
        verified = [r for r in rs if r.get("agent_system") == "S2" and r.get("error") is None]
        if verified:
            row["regeneration_rate"] = _rate(sum(1 for r in verified if r.get("regenerated")), len(verified))
            row["removed_claims_per_item"] = _mean([r.get("n_removed_claims") for r in verified])
        row.update(study2_measures(rs))
        out.append(row)
    return out


def outcomes(rows):
    counts = defaultdict(lambda: {"correct": 0, "abstained": 0, "wrong": 0})
    for r in rows:
        o = outcome(r)
        if o is None or r.get("repetition", 0) != 0:
            continue
        for task in (r["task"], "ALL"):
            counts[(r["model_name"], r["system"], task, bool(r.get("sufficient")))][o] += 1
    out = []
    for (model, system, task, sufficient), c in sorted(counts.items(), key=lambda kv: (kv[0][0], _order(kv[0][1]), kv[0][1], kv[0][2], kv[0][3])):
        n = sum(c.values())
        out.append({"model": model, "system": system, "task": task, "evidence_sufficient": sufficient, "n": n,
                    **{f"{k}_rate": v / n for k, v in c.items()}, **c})
    return out


def _by_item(rows, model, system):
    return {r["item_id"]: r for r in rows if r["model_name"] == model and r["system"] == system
            and r.get("error") is None and r.get("repetition", 0) == 0}


def contrasts(rows):
    tests = []
    models = sorted({r["model_name"] for r in rows if r["model_name"] != "R0"})
    for model in models:
        for family, a, b, question in CONTRASTS:
            left, right = _by_item(rows, model, a), _by_item(rows, model, b)
            ids = sorted(set(left) & set(right))
            if not ids:
                continue
            pairs = [(left[i], right[i]) for i in ids]

            def add(measure, scope, result, **extra):
                tests.append({"family": family, "model": model, "a": a, "b": b, "question": question,
                              "measure": measure, "scope": scope,
                              "n": result.get("n", result.get("n_discordant")),
                              "statistic": result.get("z", result.get("b01")), "p": result.get("p"), **extra})

            flag = lambda r: n_unsupported(r) > 0          # noqa: E731
            claims = lambda r: r.get("n_claims", 0)        # noqa: E731
            d, lo, hi = paired_ratio_diff_ci(pairs, n_unsupported, claims)
            add("items with an unsupported claim", "all", mcnemar_exact([(flag(x), flag(y)) for x, y in pairs]),
                rate_a=_rate(sum(flag(x) for x, _ in pairs), len(pairs)),
                rate_b=_rate(sum(flag(y) for _, y in pairs), len(pairs)),
                claim_rate_diff=d, claim_rate_diff_ci_low=lo, claim_rate_diff_ci_high=hi)
            add("abstention", "all", mcnemar_exact([(bool(x.get("abstained")), bool(y.get("abstained"))) for x, y in pairs]),
                rate_a=_rate(sum(bool(x.get("abstained")) for x, _ in pairs), len(pairs)),
                rate_b=_rate(sum(bool(y.get("abstained")) for _, y in pairs), len(pairs)))
            add("items with a false claim", "all", mcnemar_exact([(n_false(x) > 0, n_false(y) > 0) for x, y in pairs]),
                rate_a=_rate(sum(n_false(x) > 0 for x, _ in pairs), len(pairs)),
                rate_b=_rate(sum(n_false(y) > 0 for _, y in pairs), len(pairs)))
            for task in sorted({x["task"] for x, _ in pairs}):
                scored = [(x["score"], y["score"]) for x, y in pairs
                          if x["task"] == task and x.get("score") is not None and y.get("score") is not None]
                if not scored:
                    continue
                if task in BINARY_TASKS:
                    result = mcnemar_exact([(s >= 1.0, t >= 1.0) for s, t in scored])
                else:
                    result = wilcoxon_signed_rank(scored)
                add("score", task, result, mean_a=_mean([s for s, _ in scored]), mean_b=_mean([t for _, t in scored]))
            t3 = [(x, y) for x, y in pairs if x["task"] == "T3"]
            if t3 and family == "RQ2":
                over = [(closure_flags(x)[0], closure_flags(y)[0]) for x, y in t3]
                add("T3 over-closure", "T3", mcnemar_exact(over),
                    rate_a=_rate(sum(o for o, _ in over), len(over)), rate_b=_rate(sum(o for _, o in over), len(over)))
    families = defaultdict(list)
    for i, t in enumerate(tests):
        if t["p"] is not None:
            families[(t["family"], t["model"])].append(i)
    for indices in families.values():
        for i, adjusted in zip(indices, holm([tests[i]["p"] for i in indices])):
            tests[i]["p_holm"] = adjusted
    return tests


def write_report(results_dir):
    rows = load_system_rows(Path(results_dir) / "system_responses.jsonl")
    out = Path(results_dir) / "analysis_study2"
    out.mkdir(parents=True, exist_ok=True)
    summary = summarise(rows)
    _write_csv(out / "summary.csv", summary)
    _write_csv(out / "outcomes.csv", outcomes(rows))
    tests = contrasts(rows)
    _write_csv(out / "contrasts.csv", tests)
    synthetic = any(r.get("provider") == "mock" for r in rows)
    return {"rows": len(rows), "summary_cells": len(summary), "tests": len(tests), "out": out, "synthetic": synthetic}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Study 2 analysis tables (no API calls)")
    parser.add_argument("results_dir", nargs="?", default="results/v3_pilot")
    args = parser.parse_args(argv)
    info = write_report(args.results_dir)
    print(f"{info['rows']} rows -> {info['summary_cells']} summary cells, {info['tests']} contrasts in {info['out']}")
    if info["synthetic"]:
        print("NOTE: contains mock-model rows (synthetic, never findings).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
