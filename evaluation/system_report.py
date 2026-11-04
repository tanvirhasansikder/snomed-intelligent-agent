"""
Report for protocol v2.0 (Sections 3, 7 and 8): reads
``<results_dir>/system_responses.jsonl`` written by evaluation/system_runner.py
and writes ``<results_dir>/analysis_systems/``:

* summary.csv                       per model x system x task (and "ALL")
* summary_by_sufficient.csv         the same, split by the `sufficient` label
* summary_by_answer_in_evidence.csv the same, split by `answer_in_evidence`
* hypotheses.csv                    H1-H4 tests, Holm-adjusted within each family
* verification_targets.csv          what S2 must remove: S1's unsupported claims
                                    split by the reference label (RQ4)
* tradeoffs.csv                     faithfulness vs completeness vs cost (RQ5)
* nondeterminism.json               agreement across repetitions
* table_systems.tex                 main LaTeX table

Conventions:
* Only repetition 0 enters the main analysis; repetitions only feed
  nondeterminism.
* Rows with an API error count as failures (``n_errors``) and are left out of
  the rates; nothing is dropped silently.
* Claim-level rates (unsupported, false, fill-in, citations) pool claims over
  items. Their 95% CIs come from a percentile bootstrap OVER ITEMS (claims
  of one answer stay together), 1,000 resamples, seed 0.
* Paired tests use only items answered by both systems.
"""

import json
import random
from collections import defaultdict
from pathlib import Path

from evaluation.report import _write_csv
from evaluation.stats import bootstrap_ci, cochran_armitage, holm, mcnemar_exact, wilcoxon_signed_rank

FALSE_LABELS = ("n_false_in_reference", "n_unknown_identifier")
SYSTEM_ORDER = ("R0-B1", "R0-S1", "B0", "B1", "S1", "S2")
BINARY_TASKS = ("T3", "T4", "T6")
DEPTH_BINS = ((0, 3), (4, 5), (6, 7), (8, 99))
N_RESAMPLES, SEED = 1000, 0


# ----------------------------------------------------------------------
# loading and per-item measures
# ----------------------------------------------------------------------

def load_system_rows(path):
    """Latest row per (system, model, item, repetition); a successful row wins over a failed one."""
    best = {}
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            key = (row["system"], row["model_name"], row["item_id"], row.get("repetition", 0))
            if key not in best or best[key].get("error") is not None or row.get("error") is None:
                best[key] = row
    return list(best.values())


def n_unsupported(r):
    return r.get("n_claims", 0) - r.get("n_supported_by_subgraph", 0)


def n_false(r):
    return sum(r.get(f, 0) for f in FALSE_LABELS)


def _mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def _rate(num, den):
    return num / den if den else None


def ratio_ci(rows, num, den, seed=SEED):
    """Pooled ratio sum(num)/sum(den) with a bootstrap CI over items."""
    pairs = [(num(r), den(r)) for r in rows]
    total_den = sum(d for _, d in pairs)
    if not total_den:
        return None, None, None
    rng, stats = random.Random(seed), []
    for _ in range(N_RESAMPLES):
        sample = [rng.choice(pairs) for _ in pairs]
        d = sum(x for _, x in sample)
        if d:
            stats.append(sum(n for n, _ in sample) / d)
    stats.sort()
    point = sum(n for n, _ in pairs) / total_den
    if not stats:
        return point, None, None
    return point, stats[int(0.025 * len(stats))], stats[min(len(stats) - 1, int(0.975 * len(stats)))]


def paired_ratio_diff_ci(pairs, num, den, seed=SEED):
    """
    Difference of pooled ratios (b - a) over paired items, with a paired
    bootstrap CI (items resampled together for both systems).
    """
    if not pairs:
        return None, None, None

    def diff(sample):
        da, db = sum(den(a) for a, _ in sample), sum(den(b) for _, b in sample)
        if not da or not db:
            return None
        return sum(num(b) for _, b in sample) / db - sum(num(a) for a, _ in sample) / da

    point = diff(pairs)
    rng, stats = random.Random(seed), []
    for _ in range(N_RESAMPLES):
        value = diff([rng.choice(pairs) for _ in pairs])
        if value is not None:
            stats.append(value)
    stats.sort()
    if point is None or not stats:
        return point, None, None
    return point, stats[int(0.025 * len(stats))], stats[min(len(stats) - 1, int(0.975 * len(stats)))]


def unpaired_ratio_diff_ci(group_a, group_b, num, den, seed=SEED):
    """Pooled ratio of group_a minus group_b, each resampled within itself (independent groups)."""
    def pooled(rows):
        d = sum(den(r) for r in rows)
        return sum(num(r) for r in rows) / d if d else None

    point_a, point_b = pooled(group_a), pooled(group_b)
    if point_a is None or point_b is None:
        return None, None, None
    rng, stats = random.Random(seed), []
    for _ in range(N_RESAMPLES):
        a = pooled([rng.choice(group_a) for _ in group_a])
        b = pooled([rng.choice(group_b) for _ in group_b])
        if a is not None and b is not None:
            stats.append(a - b)
    stats.sort()
    if not stats:
        return point_a - point_b, None, None
    return point_a - point_b, stats[int(0.025 * len(stats))], stats[min(len(stats) - 1, int(0.975 * len(stats)))]


def _score_ci(rows):
    ci = bootstrap_ci([float(r["score"]) for r in rows if r.get("score") is not None], N_RESAMPLES, 0.95, SEED)
    return (None, None) if ci is None else (ci["lower"], ci["upper"])


# ----------------------------------------------------------------------
# summaries
# ----------------------------------------------------------------------

def _summary_row(key_fields, rows):
    ok = [r for r in rows if r.get("error") is None]
    claims = lambda r: r.get("n_claims", 0)  # noqa: E731
    unsup, unsup_lo, unsup_hi = ratio_ci(ok, n_unsupported, claims)
    false, false_lo, false_hi = ratio_ci(ok, n_false, claims)
    score_lo, score_hi = _score_ci(ok)
    s2 = [r for r in ok if r["system"] == "S2"]
    return {
        **key_fields,
        "n_items": len(rows),
        "n_errors": len(rows) - len(ok),
        "parse_ok_rate": _rate(sum(1 for r in ok if r.get("parse_ok", True)), len(ok)),
        "score": _mean([r.get("score") for r in ok]), "score_ci_low": score_lo, "score_ci_high": score_hi,
        "abstention_rate": _rate(sum(1 for r in ok if r.get("abstained")), len(ok)),
        "claims_per_item": _mean([claims(r) for r in ok]),
        "unsupported_claim_rate": unsup, "unsupported_ci_low": unsup_lo, "unsupported_ci_high": unsup_hi,
        "false_claim_rate": false, "false_ci_low": false_lo, "false_ci_high": false_hi,
        "fill_in_rate": ratio_ci(ok, lambda r: r.get("n_true_not_retrieved", 0), claims)[0],
        "misattribution_rate": ratio_ci(ok, lambda r: r.get("n_misattributed", 0), claims)[0],
        "unknown_id_rate": ratio_ci(ok, lambda r: r.get("n_unknown_identifier", 0), claims)[0],
        "items_with_false_claim": _rate(sum(1 for r in ok if n_false(r) > 0), len(ok)),
        "items_with_false_absence": _rate(sum(1 for r in ok if r.get("n_false_absence", 0) > 0), len(ok)),
        "citation_precision": _rate(sum(r.get("n_cite_entails", 0) for r in ok), sum(r.get("n_cite_attempted", 0) for r in ok)),
        "citation_recall": _rate(sum(r.get("n_supported_cited_ok", 0) for r in ok), sum(r.get("n_supported_citable", 0) for r in ok)),
        "sufficient_rate": _rate(sum(1 for r in ok if r.get("sufficient")), len(ok)),
        "answer_in_evidence_rate": _rate(sum(1 for r in ok if r.get("answer_in_evidence")), len(ok)),
        "mean_retrieval_requests": _mean([r.get("retrieval_requests") for r in ok]),
        "mean_prompt_tokens": _mean([r.get("prompt_tokens") for r in ok]),
        "mean_completion_tokens": _mean([r.get("completion_tokens") for r in ok]),
        "mean_llm_latency_s": _mean([r.get("latency_s") for r in ok]),
        "mean_llm_calls": _mean([r.get("n_llm_calls") for r in ok]),
        "regeneration_rate": _rate(sum(1 for r in s2 if r.get("regenerated")), len(s2)) if s2 else None,
        "removed_claims_per_item": _mean([r.get("n_removed_claims") for r in s2]) if s2 else None,
        "text_replaced_rate": _rate(sum(1 for r in s2 if r.get("answer_replaced")), len(s2)) if s2 else None,
        "cost_usd": round(sum(r.get("cost_usd") or 0.0 for r in ok), 6),
    }


def _main_rows(rows):
    return [r for r in rows if r.get("repetition", 0) == 0]


def _sort_key(item):
    (model, system, task, *rest), _ = item
    return (model, SYSTEM_ORDER.index(system) if system in SYSTEM_ORDER else 99, task, [str(x) for x in rest])


def summarise(rows, stratify_by=None):
    groups = defaultdict(list)
    for r in _main_rows(rows):
        extra = (r.get(stratify_by),) if stratify_by else ()
        groups[(r["model_name"], r["system"], r["task"], *extra)].append(r)
        groups[(r["model_name"], r["system"], "ALL", *extra)].append(r)
    out = []
    for key, rs in sorted(groups.items(), key=_sort_key):
        fields = {"model": key[0], "system": key[1], "task": key[2]}
        if stratify_by:
            fields[stratify_by] = key[3]
        out.append(_summary_row(fields, rs))
    return out


# ----------------------------------------------------------------------
# hypothesis tests (protocol Section 8)
# ----------------------------------------------------------------------

def _by_item(rows, model, system):
    return {r["item_id"]: r for r in _main_rows(rows)
            if r["model_name"] == model and r["system"] == system and r.get("error") is None}


def _llm_models(rows):
    return sorted({r["model_name"] for r in rows if r["model_name"] != "R0"})


def _paired(rows, model, a, b, keep=lambda x, y: True):
    left, right = _by_item(rows, model, a), _by_item(rows, model, b)
    return [(left[i], right[i]) for i in sorted(set(left) & set(right)) if keep(left[i], right[i])]


def hypothesis_tests(rows):
    tests = []

    def add(family, model, contrast, scope, result, **extra):
        tests.append({"family": family, "model": model, "contrast": contrast, "scope": scope,
                      "n": result.get("n", result.get("n_discordant")),
                      "statistic": result.get("z", result.get("b01")), "p": result.get("p"), **extra})

    claims = lambda r: r.get("n_claims", 0)  # noqa: E731
    for model in _llm_models(rows):
        # H1: closed-book false claims rise with depth
        b0 = list(_by_item(rows, model, "B0").values())
        successes, totals = [], []
        for low, high in DEPTH_BINS:
            group = [r for r in b0 if r.get("meta_depth_min") is not None and low <= r["meta_depth_min"] <= high]
            totals.append(len(group))
            successes.append(sum(1 for r in group if n_false(r) > 0))
        if sum(1 for t in totals if t) >= 2:
            add("H1", model, "B0: items with a false claim, by depth bin", "all", cochran_armitage(successes, totals),
                groups=json.dumps(list(zip(successes, totals))))

        # H2: S1 vs B1 per task, and the unsupported-claim rate
        for task in ("T1", "T2", "T3", "T4", "T5", "T6"):
            pairs = [(a, b) for a, b in _paired(rows, model, "B1", "S1") if a["task"] == task
                     and a.get("score") is not None and b.get("score") is not None]
            if not pairs:
                continue
            if task in BINARY_TASKS:
                result = mcnemar_exact([(a["score"] == 1.0, b["score"] == 1.0) for a, b in pairs])
            else:
                result = wilcoxon_signed_rank([(a["score"], b["score"]) for a, b in pairs])
            add("H2", model, "score B1 -> S1", task, result,
                mean_a=_mean([a["score"] for a, _ in pairs]), mean_b=_mean([b["score"] for _, b in pairs]))
        pairs = _paired(rows, model, "B1", "S1")
        if pairs:
            d, lo, hi = paired_ratio_diff_ci(pairs, n_unsupported, claims)
            result = mcnemar_exact([(n_unsupported(a) > 0, n_unsupported(b) > 0) for a, b in pairs])
            add("H2", model, "items with an unsupported claim B1 -> S1", "all", result,
                rate_diff=d, rate_diff_ci_low=lo, rate_diff_ci_high=hi)

        # H3: insufficient vs sufficient evidence, under both labels, within B1 and within S1
        for system in ("B1", "S1"):
            items = list(_by_item(rows, model, system).values())
            for label in ("sufficient", "answer_in_evidence"):
                yes = [r for r in items if r.get(label)]
                no = [r for r in items if not r.get(label)]
                for measure, fn in (("unsupported", n_unsupported), ("false", n_false)):
                    r_no, lo_no, hi_no = ratio_ci(no, fn, claims)
                    r_yes, lo_yes, hi_yes = ratio_ci(yes, fn, claims)
                    diff, d_lo, d_hi = unpaired_ratio_diff_ci(no, yes, fn, claims)
                    tests.append({"family": "H3", "model": model,
                                  "contrast": f"{system}: {measure} rate, not {label} minus {label}", "scope": "all",
                                  "n": f"{len(no)}/{len(yes)}", "statistic": None, "p": None,
                                  "rate_diff": diff, "rate_diff_ci_low": d_lo, "rate_diff_ci_high": d_hi,
                                  "rate_not": r_no, "rate_not_ci": f"[{lo_no}, {hi_no}]",
                                  "rate_yes": r_yes, "rate_yes_ci": f"[{lo_yes}, {hi_yes}]"})
        gain = _paired(rows, model, "B1", "S1", keep=lambda a, b: not a.get("sufficient") and b.get("sufficient"))
        if gain:
            add("H3", model, "items with an unsupported claim B1 -> S1, where only S1 is sufficient", "all",
                mcnemar_exact([(n_unsupported(a) > 0, n_unsupported(b) > 0) for a, b in gain]))

        # H4: S2 vs S1
        pairs = _paired(rows, model, "S1", "S2")
        if pairs:
            add("H4", model, "items with a false claim S1 -> S2", "all",
                mcnemar_exact([(n_false(a) > 0, n_false(b) > 0) for a, b in pairs]),
                rate_a=_rate(sum(1 for a, _ in pairs if n_false(a) > 0), len(pairs)),
                rate_b=_rate(sum(1 for _, b in pairs if n_false(b) > 0), len(pairs)))
            scored = [(a["score"], b["score"]) for a, b in pairs
                      if a.get("score") is not None and b.get("score") is not None]
            if scored:
                add("H4", model, "score S1 -> S2 (descriptive: the cost of strictness)", "all",
                    wilcoxon_signed_rank(scored), mean_a=_mean([a for a, _ in scored]),
                    mean_b=_mean([b for _, b in scored]))

    families = defaultdict(list)
    for i, t in enumerate(tests):
        if t["p"] is not None:
            families[(t["family"], t["model"])].append(i)
    for indices in families.values():
        for i, adjusted in zip(indices, holm([tests[i]["p"] for i in indices])):
            tests[i]["p_holm"] = adjusted
    return tests


# ----------------------------------------------------------------------
# RQ4 / RQ5 / nondeterminism
# ----------------------------------------------------------------------

def verification_targets(rows):
    """S1's claims not supported by its evidence, split by what the reference says they are."""
    out = []
    for model in _llm_models(rows):
        s1 = list(_by_item(rows, model, "S1").values())
        unsupported = sum(n_unsupported(r) for r in s1)
        row = {"model": model, "items": len(s1), "unsupported_claims": unsupported}
        for field in ("n_true_not_retrieved", "n_false_in_reference", "n_unknown_identifier", "n_insufficient_evidence"):
            count = sum(r.get(field, 0) for r in s1)
            row[field[2:]] = count
            row[f"{field[2:]}_share"] = _rate(count, unsupported)
        out.append(row)
    return out


def tradeoffs(summary):
    keep = ("model", "system", "score", "unsupported_claim_rate", "false_claim_rate", "abstention_rate",
            "citation_precision", "mean_retrieval_requests", "mean_prompt_tokens", "mean_completion_tokens",
            "mean_llm_latency_s", "mean_llm_calls", "cost_usd")
    return [{k: s.get(k) for k in keep} for s in summary if s["task"] == "ALL"]


def nondeterminism(rows):
    groups = defaultdict(list)
    for r in rows:
        if r.get("error") is None and r["model_name"] != "R0":
            groups[(r["model_name"], r["system"], r["item_id"])].append(r)
    out = defaultdict(lambda: {"items_repeated": 0, "items_identical_answer": 0})
    for (model, system, _), rs in groups.items():
        if len(rs) < 2:
            continue
        entry = out[f"{model}/{system}"]
        entry["items_repeated"] += 1
        answers = {json.dumps(r.get("answer"), sort_keys=True) for r in rs}
        entry["items_identical_answer"] += int(len(answers) == 1)
    for entry in out.values():
        entry["agreement"] = _rate(entry["items_identical_answer"], entry["items_repeated"])
    return dict(out)


def _fmt(value, digits=2):
    return "--" if value is None else f"{value:.{digits}f}"


def latex_table(summary):
    lines = [r"\begin{tabular}{llrrrrrr}", r"\toprule",
             r"Model & System & Score & Unsupported & False & Cit.\ prec. & Tokens & Latency (s) \\", r"\midrule"]
    for s in summary:
        if s["task"] != "ALL":
            continue
        lines.append(f"{s['model']} & {s['system']} & {_fmt(s['score'])} & {_fmt(s['unsupported_claim_rate'])} & "
                     f"{_fmt(s['false_claim_rate'])} & {_fmt(s['citation_precision'])} & "
                     f"{_fmt(s['mean_prompt_tokens'], 0)} & {_fmt(s['mean_llm_latency_s'], 1)} \\\\")
    lines += [r"\bottomrule", r"\end{tabular}"]
    return "\n".join(lines) + "\n"


def write_report(results_dir):
    rows = load_system_rows(Path(results_dir) / "system_responses.jsonl")
    out = Path(results_dir) / "analysis_systems"
    out.mkdir(parents=True, exist_ok=True)
    summary = summarise(rows)
    _write_csv(out / "summary.csv", summary)
    _write_csv(out / "summary_by_sufficient.csv", summarise(rows, "sufficient"))
    _write_csv(out / "summary_by_answer_in_evidence.csv", summarise(rows, "answer_in_evidence"))
    tests = hypothesis_tests(rows)
    _write_csv(out / "hypotheses.csv", tests)
    _write_csv(out / "verification_targets.csv", verification_targets(rows))
    _write_csv(out / "tradeoffs.csv", tradeoffs(summary))
    (out / "nondeterminism.json").write_text(json.dumps(nondeterminism(rows), indent=2), encoding="utf-8")
    (out / "table_systems.tex").write_text(latex_table(summary), encoding="utf-8")
    synthetic = any(r.get("provider") == "mock" for r in rows)
    return {"rows": len(rows), "summary_cells": len(summary), "tests": len(tests), "out": out, "synthetic": synthetic}
