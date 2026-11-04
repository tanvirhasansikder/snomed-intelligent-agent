"""
Analysis of experiment results (protocol Section 8).

    python -m evaluation.report --config configs/pilot.json

Reads ``<results_dir>/responses.jsonl`` and writes to ``<results_dir>/analysis/``:

* summary.csv           per model x condition x format x task
* summary_by_sufficiency.csv
* hypotheses.csv        pre-registered tests with Holm-adjusted p-values
* rq4_verification.csv  what deterministic verification removes and costs
* table_main.tex        LaTeX table for the paper
* fig_*.png             figures (only if matplotlib is installed)

Nothing here changes results; it only aggregates rows written by the runner.
"""

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

from evaluation.collect import load_config, resolve
from evaluation.stats import bootstrap_ci, cochran_armitage, holm, mcnemar_exact, wilcoxon_signed_rank

CONDITION_ORDER = ["C0", "U1", "U2", "U2D", "FULL"]
INCOMPLETE = ["U1", "U2", "U2D"]
FALSE_LABELS = ("n_false_in_reference", "n_unknown_identifier")


def load_rows(path):
    """Latest row per (model, item, condition, format, repetition); successful rows win."""
    best = {}
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            key = (row["model_name"], row["item_id"], row["condition"], row["format"], row.get("repetition", 0))
            if key not in best or best[key].get("error") is not None or row.get("error") is None:
                best[key] = row
    return list(best.values())


def _mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def _rate(numerator, denominator):
    return numerator / denominator if denominator else None


def _ci(values):
    ci = bootstrap_ci([float(v) for v in values if v is not None])
    return (None, None) if ci is None else (ci["lower"], ci["upper"])


def summarise(rows, by_sufficiency=False):
    groups = defaultdict(list)
    for r in rows:
        if r.get("repetition", 0) != 0:
            continue
        key = (r["model_name"], r["condition"], r["format"], r["task"])
        if by_sufficiency:
            key += (r.get("sufficient"),)
        groups[key].append(r)

    out = []
    for key, rs in sorted(groups.items(), key=lambda kv: [str(k) for k in kv[0]]):
        ok = [r for r in rs if r.get("error") is None]
        claims = sum(r.get("n_claims", 0) for r in ok)
        supported = sum(r.get("n_supported_by_subgraph", 0) for r in ok)
        false_claims = sum(sum(r.get(f, 0) for f in FALSE_LABELS) for r in ok)
        fill_in = sum(r.get("n_true_not_retrieved", 0) for r in ok)
        scores = [r.get("score") for r in ok]
        lo, hi = _ci(scores)
        row = {
            "model": key[0], "condition": key[1], "format": key[2], "task": key[3],
            "n_items": len(rs), "n_errors": len(rs) - len(ok),
            "parse_ok_rate": _rate(sum(1 for r in ok if r.get("parse_ok")), len(ok)),
            "score": _mean(scores), "score_ci_low": lo, "score_ci_high": hi,
            "verified_score": _mean([r.get("v_score") for r in ok]),
            "abstention_rate": _rate(sum(1 for r in ok if r.get("abstained")), len(ok)),
            "verified_abstention_rate": _rate(sum(1 for r in ok if r.get("v_abstained")), len(ok)),
            "claims": claims,
            "unsupported_claim_rate": _rate(claims - supported, claims),
            "false_claim_rate": _rate(false_claims, claims),
            "fill_in_rate": _rate(fill_in, claims),
            "misattribution_rate": _rate(sum(r.get("n_misattributed", 0) for r in ok), claims),
            "items_with_false_absence": _rate(sum(1 for r in ok if r.get("n_false_absence", 0) > 0), len(ok)),
            "invalid_sctid_rate": _rate(sum(r.get("n_invalid_sctid", 0) for r in ok),
                                        sum(r.get("n_cited_ids", 0) for r in ok)),
            "citation_precision": _rate(sum(r.get("n_cite_entails", 0) for r in ok),
                                        sum(r.get("n_cite_attempted", 0) for r in ok)),
            "citation_recall": _rate(sum(r.get("n_supported_cited_ok", 0) for r in ok),
                                     sum(r.get("n_supported_citable", 0) for r in ok)),
            "mean_prompt_tokens": _mean([r.get("prompt_tokens") for r in ok]),
            "mean_latency_s": _mean([r.get("latency_s") for r in ok]),
            "cost_usd": round(sum(r.get("cost_usd") or 0.0 for r in ok), 6),
        }
        if by_sufficiency:
            row["sufficient"] = key[4]
        out.append(row)
    return out


def _index(rows):
    """(model, condition, format) -> {item_id: row} using repetition 0 and successful rows."""
    idx = defaultdict(dict)
    for r in rows:
        if r.get("error") is None and r.get("repetition", 0) == 0:
            idx[(r["model_name"], r["condition"], r["format"])][r["item_id"]] = r
    return idx


def _paired(idx, a, b, value):
    left, right = idx.get(a, {}), idx.get(b, {})
    common = sorted(set(left) & set(right))
    return [(value(left[i]), value(right[i])) for i in common]


def _models(rows):
    return sorted({r["model_name"] for r in rows if r["model_name"] != "R0"})


def _cell(condition, fmt):
    return "none" if condition == "C0" else fmt


def hypothesis_tests(rows):
    idx = _index(rows)
    tests = []

    def add(hypothesis, model, comparison, task, result, extra=None):
        record = {"hypothesis": hypothesis, "model": model, "comparison": comparison, "task": task,
                  "n": result.get("n_discordant", result.get("n")), "statistic": result.get("z"),
                  "p": result["p"], **(extra or {})}
        record.update({k: v for k, v in result.items() if k in ("b01", "b10", "w_plus", "w_minus")})
        tests.append(record)

    for model in _models(rows):
        # H1: closed-book false claims by depth (trend)
        c0 = [r for r in idx.get((model, "C0", "none"), {}).values() if r.get("meta_depth_min") is not None]
        bins = [(0, 3), (4, 5), (6, 7), (8, 99)]
        successes, totals = [], []
        for low, high in bins:
            group = [r for r in c0 if low <= r["meta_depth_min"] <= high]
            totals.append(len(group))
            successes.append(sum(1 for r in group if sum(r.get(f, 0) for f in FALSE_LABELS) > 0))
        if sum(totals) and sum(1 for t in totals if t) >= 2:
            result = cochran_armitage(successes, totals)
            add("H1", model, "false-claim presence by depth bin (<=3, 4-5, 6-7, >=8)", "all", result,
                {"groups": json.dumps(list(zip(successes, totals)))})

        # H2a: adjacent retrieval conditions (plain format), per task
        for task in ("T1", "T2", "T3", "T4", "T5"):
            for left, right in zip(CONDITION_ORDER, CONDITION_ORDER[1:]):
                a, b = (model, left, _cell(left, "plain")), (model, right, _cell(right, "plain"))
                pairs = _paired_task(idx, a, b, task, lambda r: r.get("score"))
                if not pairs:
                    continue
                if task in ("T3", "T4"):
                    result = mcnemar_exact([(x == 1.0, y == 1.0) for x, y in pairs])
                else:
                    result = wilcoxon_signed_rank(pairs)
                add("H2a", model, f"{left} -> {right}", task, result,
                    {"mean_left": _mean([x for x, _ in pairs]), "mean_right": _mean([y for _, y in pairs])})

        # H3: plain vs annotated under each retrieval condition
        for condition in INCOMPLETE + ["FULL"]:
            a, b = (model, condition, "plain"), (model, condition, "annotated")
            false_abs = _paired(idx, a, b, lambda r: r.get("n_false_absence", 0) > 0)
            if false_abs:
                add("H3a", model, f"{condition}: plain vs annotated (items with a false absence claim)", "all",
                    mcnemar_exact(false_abs), {"rate_plain": _mean([x for x, _ in false_abs]),
                                               "rate_annotated": _mean([y for _, y in false_abs])})
            misattr = _paired(idx, a, b, lambda r: r.get("n_misattributed", 0) > 0)
            if misattr:
                add("H3b", model, f"{condition}: plain vs annotated (items with a misattributed claim)", "all",
                    mcnemar_exact(misattr), {"rate_plain": _mean([x for x, _ in misattr]),
                                             "rate_annotated": _mean([y for _, y in misattr])})
            insufficient = [(x["abstained"], y["abstained"]) for x, y in _paired(idx, a, b, lambda r: r)
                            if not x.get("sufficient")]
            if insufficient:
                add("H3c", model, f"{condition}: abstention on insufficient items, plain vs annotated", "all",
                    mcnemar_exact(insufficient), {"rate_plain": _mean([x for x, _ in insufficient]),
                                                  "rate_annotated": _mean([y for _, y in insufficient])})
            sufficient_scores = [(x.get("score"), y.get("score")) for x, y in _paired(idx, a, b, lambda r: r)
                                 if x.get("sufficient") and x.get("score") is not None and y.get("score") is not None]
            if sufficient_scores:
                add("H3c", model, f"{condition}: score on sufficient items, plain vs annotated", "all",
                    wilcoxon_signed_rank(sufficient_scores),
                    {"mean_plain": _mean([x for x, _ in sufficient_scores]),
                     "mean_annotated": _mean([y for _, y in sufficient_scores])})

    # Holm within each hypothesis family per model
    families = defaultdict(list)
    for i, t in enumerate(tests):
        families[(t["hypothesis"], t["model"])].append(i)
    for members in families.values():
        adjusted = holm([tests[i]["p"] for i in members])
        for i, p in zip(members, adjusted):
            tests[i]["p_holm"] = p
    return tests


def _paired_task(idx, a, b, task, value):
    left, right = idx.get(a, {}), idx.get(b, {})
    common = sorted(i for i in set(left) & set(right) if left[i]["task"] == task)
    pairs = [(value(left[i]), value(right[i])) for i in common]
    return [(x, y) for x, y in pairs if x is not None and y is not None]


def verification_table(rows):
    """RQ4: what LV removes, split into false vs true-but-not-retrieved, and its cost."""
    groups = defaultdict(list)
    for r in rows:
        if r.get("error") is None and r.get("repetition", 0) == 0 and r["model_name"] != "R0":
            groups[(r["model_name"], r["condition"], r["format"])].append(r)
    out = []
    for (model, condition, fmt), rs in sorted(groups.items()):
        claims = sum(r.get("n_claims", 0) for r in rs)
        removed = claims - sum(r.get("n_supported_by_subgraph", 0) for r in rs)
        out.append({
            "model": model, "condition": condition, "format": fmt, "items": len(rs), "claims": claims,
            "claims_removed": removed,
            "removed_true_not_retrieved": sum(r.get("n_true_not_retrieved", 0) for r in rs),
            "removed_false": sum(sum(r.get(f, 0) for f in FALSE_LABELS) for r in rs),
            "removed_other": sum(r.get("n_insufficient_evidence", 0) + r.get("n_contradicted_by_complete_evidence", 0)
                                 for r in rs),
            "score_L": _mean([r.get("score") for r in rs if r["task"] != "T5"]),
            "score_LV": _mean([r.get("v_score") for r in rs if r["task"] != "T5"]),
            "abstention_L": _rate(sum(1 for r in rs if r.get("abstained")), len(rs)),
            "abstention_LV": _rate(sum(1 for r in rs if r.get("v_abstained")), len(rs)),
        })
    return out


def nondeterminism(rows):
    """Agreement of repeated responses (same item, model, cell) on the primary score."""
    groups = defaultdict(list)
    for r in rows:
        if r.get("error") is None:
            groups[(r["model_name"], r["item_id"], r["condition"], r["format"])].append(r)
    repeated = [g for g in groups.values() if len(g) > 1]
    if not repeated:
        return None
    agree = sum(1 for g in repeated if len({json.dumps(x.get("score")) for x in g}) == 1)
    return {"repeated_items": len(repeated), "identical_score_share": agree / len(repeated)}


def _write_csv(path, rows):
    if not rows:
        return
    fields = list(rows[0].keys())
    for r in rows[1:]:
        fields += [k for k in r if k not in fields]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _fmt(value, digits=2):
    return "--" if value is None else f"{value:.{digits}f}"


def latex_table(summary):
    """Main results table: score per task, plus faithfulness rates pooled over tasks."""
    by_cell = defaultdict(dict)
    for s in summary:
        by_cell[(s["model"], s["condition"], s["format"])][s["task"]] = s
    lines = [
        "\\begin{tabular}{lllrrrrrrr}",
        "\\toprule",
        "Model & Cond. & Context & T1 F1 & T2 F1 & T3 acc & T4 valid & Misattr. & False abs. & Abstain \\\\",
        "\\midrule",
    ]
    for (model, condition, fmt), tasks in sorted(by_cell.items(),
                                                  key=lambda kv: (kv[0][0], CONDITION_ORDER.index(kv[0][1])
                                                                  if kv[0][1] in CONDITION_ORDER else 99, kv[0][2])):
        claims = sum(t["claims"] for t in tasks.values())
        misattr = _rate(sum((t["misattribution_rate"] or 0) * t["claims"] for t in tasks.values()), claims)
        items = sum(t["n_items"] - t["n_errors"] for t in tasks.values())
        false_abs = _rate(sum((t["items_with_false_absence"] or 0) * (t["n_items"] - t["n_errors"])
                              for t in tasks.values()), items)
        abstain = _rate(sum((t["abstention_rate"] or 0) * (t["n_items"] - t["n_errors"]) for t in tasks.values()), items)
        cells = [_fmt(tasks.get(t, {}).get("score")) for t in ("T1", "T2", "T3", "T4")]
        name = model.replace("_", "\\_")
        lines.append(f"{name} & {condition} & {fmt} & " + " & ".join(cells)
                     + f" & {_fmt(misattr)} & {_fmt(false_abs)} & {_fmt(abstain)} \\\\")
    lines += ["\\bottomrule", "\\end{tabular}"]
    return "\n".join(lines)


def figures(summary, out_dir):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return []
    written = []
    models = sorted({s["model"] for s in summary})
    for task in ("T1", "T3", "T4"):
        fig, ax = plt.subplots(figsize=(6, 3.5))
        for model in models:
            points = []
            for condition in CONDITION_ORDER:
                fmt = "none" if condition == "C0" else "plain"
                match = [s for s in summary if s["model"] == model and s["condition"] == condition
                         and s["format"] == fmt and s["task"] == task and s["score"] is not None]
                if match:
                    points.append((condition, match[0]["score"]))
            if points:
                ax.plot([p[0] for p in points], [p[1] for p in points], marker="o", label=model)
        ax.set_ylim(0, 1)
        ax.set_xlabel("Retrieval condition (plain context)")
        ax.set_ylabel({"T1": "F1", "T3": "Accuracy", "T4": "Valid chain"}[task])
        ax.set_title(f"{task}: score by retrieval completeness")
        ax.legend(fontsize=7)
        fig.tight_layout()
        path = out_dir / f"fig_{task}_by_condition.png"
        fig.savefig(path, dpi=200)
        plt.close(fig)
        written.append(path)
    return written


def _main(argv=None):
    parser = argparse.ArgumentParser(description="Aggregate experiment results.")
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    config = load_config(args.config)
    results_dir = resolve(config, "results_dir")
    rows = load_rows(results_dir / "responses.jsonl")
    out = results_dir / "analysis"
    out.mkdir(parents=True, exist_ok=True)

    summary = summarise(rows)
    _write_csv(out / "summary.csv", summary)
    _write_csv(out / "summary_by_sufficiency.csv", summarise(rows, by_sufficiency=True))
    tests = hypothesis_tests(rows)
    _write_csv(out / "hypotheses.csv", tests)
    _write_csv(out / "rq4_verification.csv", verification_table(rows))
    (out / "table_main.tex").write_text(latex_table(summary), encoding="utf-8")
    nd = nondeterminism(rows)
    (out / "nondeterminism.json").write_text(json.dumps(nd, indent=2), encoding="utf-8")
    written = figures(summary, out)

    synthetic = any(r.get("model_name") == "mock" or r.get("provider") == "mock" for r in rows)
    print(f"Rows: {len(rows)} | summary cells: {len(summary)} | tests: {len(tests)} | figures: {len(written)}")
    print(f"Written to {out}")
    if synthetic:
        print("NOTE: results include the MockLLM. They test the pipeline only and are not findings.")


if __name__ == "__main__":
    _main()
