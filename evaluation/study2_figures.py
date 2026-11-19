"""
Study 2 figures and one exploratory table, from the analysis tables (no API calls).

    python -m evaluation.study2_report  results\\v3_cse499     # first: the tables
    python -m evaluation.study2_figures results\\v3_cse499     # then: the figures

Writes ``analysis_study2/figures/`` (PNG at 200 dpi and SVG) and
``analysis_study2/exploratory_refusal_by_answer_presence.csv``:

* ``fig1_outcomes``       correct / declined / wrong per condition, split by
  whether the evidence was sufficient (the protocol's three-way outcome).
* ``fig2_claims``         what the claims rest on: supported by the evidence,
  true but from memory, unknown identifier, false (share of all claims).
* ``fig3_contrasts``      the pre-specified paired comparisons (RQ1 to RQ4):
  share of questions with an unsupported claim, and share declined, A vs B.
* ``fig4_withheld``       withheld-fact restatement and grounded-refusal F1 for
  the conditions that withhold links.

The exploratory table is NOT part of frozen protocol v3.0. It splits the
"evidence insufficient" questions by whether the answer was still in the
evidence: for card retrieval, "insufficient" can mean "the answer is present
but its completeness cannot be certified", which grounded-refusal F1 alone
does not separate. Report it as post hoc.

Colours follow the web app: one hue per claim verdict, validated for colour
vision deficiency in the order supported, from memory, unknown ID, false.
"""

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

from evaluation.study2_report import CONDITION_ORDER

SUPPORTED, MEMORY, UNKNOWN, FALSE = "#0E8A6A", "#B7791F", "#6B4FBB", "#C2413B"
DECLINED, OTHER, INK, MUTED, RULE = "#8A93A6", "#B4BCC9", "#14213D", "#5E6982", "#DDE2E9"

SYSTEM_NAME = {"B0": "No evidence", "L-RAG": "Keyword search", "D-RAG": "Meaning search", "G-UP": "Graph (upward)",
               "G-QA": "Graph", "G-QA+V": "Graph + verify"}


def condition_label(condition):
    """'G-QA@50:unmarked' -> 'Graph, 50% withheld, no marks'."""
    base, rest = condition, ""
    for sep in ("@", ":"):
        if sep in base:
            i = base.index(sep)
            base, rest = base[:i], base[i:] + rest
            break
    parts = [SYSTEM_NAME.get(base, base)]
    if "@" in rest:
        parts.append(f"{rest.split('@')[1].split(':')[0]}% withheld")
    if ":random" in rest:
        parts.append("random links")
    if ":unmarked" in rest:
        parts.append("no marks")
    return ", ".join(parts)


def _order(condition):
    return CONDITION_ORDER.index(condition) if condition in CONDITION_ORDER else len(CONDITION_ORDER)


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _read(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _style(plt):
    plt.rcParams.update({
        "font.family": "sans-serif", "font.sans-serif": ["IBM Plex Sans", "DejaVu Sans", "Arial"], "font.size": 9,
        "axes.edgecolor": RULE, "axes.labelcolor": MUTED, "xtick.color": MUTED, "ytick.color": INK,
        "axes.spines.top": False, "axes.spines.right": False, "axes.titlesize": 10, "axes.titleweight": "semibold",
        "axes.titlecolor": INK, "axes.titlelocation": "left", "legend.frameon": False, "svg.fonttype": "none",
    })


def _save(fig, out, name):
    fig.savefig(out / f"{name}.png", dpi=200, bbox_inches="tight", facecolor="white")
    fig.savefig(out / f"{name}.svg", bbox_inches="tight", facecolor="white")


def _stacked(ax, labels, series, title):
    """Horizontal 100% bars; segments separated by a white gap; values >= 8% labelled inside."""
    y = list(range(len(labels)))[::-1]
    left = [0.0] * len(labels)
    for name, colour, values in series:
        ax.barh(y, values, left=left, color=colour, height=0.62, edgecolor="white", linewidth=1.2, label=name)
        for yi, l, v in zip(y, left, values):
            if v >= 0.08:
                ax.text(l + v / 2, yi, f"{v:.0%}", ha="center", va="center", color="white", fontsize=7.5)
        left = [l + v for l, v in zip(left, values)]
    ax.set_yticks(y, labels)
    ax.set_xlim(0, 1)
    ax.xaxis.set_major_formatter(lambda v, _: f"{v:.0%}")
    ax.tick_params(axis="y", length=0)
    ax.set_title(title)


def fig_outcomes(plt, outcomes, model, out):
    rows = [r for r in outcomes if r["model"] == model and r["task"] == "ALL"]
    panels = [("True", "Evidence sufficient: answer it"), ("False", "Evidence insufficient: decline")]
    present = [(k, t) for k, t in panels if any(r["evidence_sufficient"] == k for r in rows)]
    if not present:
        return None
    counts = [sum(1 for r in rows if r["evidence_sufficient"] == k) for k, _ in present]
    fig, axes = plt.subplots(len(present), 1, figsize=(7.2, 0.36 * sum(counts) + 1.6 * len(present)), squeeze=False,
                             gridspec_kw={"height_ratios": counts})
    for ax, (key, title) in zip(axes[:, 0], present):
        part = sorted((r for r in rows if r["evidence_sufficient"] == key), key=lambda r: _order(r["system"]))
        labels = [f"{condition_label(r['system'])}  (n={r['n']})" for r in part]
        series = [(name, colour, [_num(r[f"{k}_rate"]) or 0 for r in part])
                  for name, colour, k in (("Correct", SUPPORTED, "correct"), ("Declined", DECLINED, "abstained"),
                                          ("Wrong", FALSE, "wrong"))]
        _stacked(ax, labels, series, title)
    fig.subplots_adjust(hspace=0.45)
    axes[-1][0].legend(loc="upper center", bbox_to_anchor=(0.5, -0.25), ncol=3)
    _save(fig, out, "fig1_outcomes")
    return fig


def fig_claims(plt, summary, model, out):
    rows = sorted((r for r in summary if r["model"] == model and r["task"] == "ALL" and (_num(r.get("claims_per_item")) or 0) > 0),
                  key=lambda r: _order(r["system"]))
    if not rows:
        return None
    shares = []
    for r in rows:
        unsupported = _num(r["unsupported_claim_rate"]) or 0
        memory, unknown, false = (_num(r.get(k)) or 0 for k in ("fill_in_rate", "unknown_id_rate", "false_claim_rate"))
        shares.append((1 - unsupported, memory, unknown, false, max(0.0, unsupported - memory - unknown - false)))
    series = [(name, colour, [s[i] for s in shares]) for i, (name, colour) in enumerate(
        (("Supported by the evidence", SUPPORTED), ("True, from memory", MEMORY), ("Unknown ID", UNKNOWN),
         ("False", FALSE), ("Other unsupported", OTHER)))]
    series = [s for s in series if any(v > 0 for v in s[2])]
    fig, ax = plt.subplots(figsize=(7.2, 0.36 * len(rows) + 1.2))
    _stacked(ax, [condition_label(r["system"]) for r in rows], series, "What the claims rest on (share of all claims)")
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, -0.3), ncol=len(series))
    _save(fig, out, "fig2_claims")
    return fig


def fig_contrasts(plt, contrasts, model, out):
    measures = ("items with an unsupported claim", "abstention")
    rows = [r for r in contrasts if r["model"] == model and r["scope"] == "all" and r["measure"] in measures]
    if not rows:
        return None
    pairs = []
    for r in rows:
        key = (r["family"], r["a"], r["b"])
        if key not in [p[0] for p in pairs]:
            pairs.append((key, r["question"]))
    fig, axes = plt.subplots(1, 2, figsize=(9.4, 0.42 * len(pairs) + 1.3), sharey=True)
    y = list(range(len(pairs)))[::-1]
    for ax, measure in zip(axes, measures):
        for yi, (key, _) in zip(y, pairs):
            r = next((x for x in rows if (x["family"], x["a"], x["b"]) == key and x["measure"] == measure), None)
            if not r or _num(r.get("rate_a")) is None:
                continue
            a, b, p = _num(r["rate_a"]), _num(r["rate_b"]), _num(r.get("p_holm"))
            ax.plot([a, b], [yi, yi], color=RULE, linewidth=2, zorder=1)
            ax.scatter([a], [yi], s=42, color="white", edgecolor=INK, linewidth=1.4, zorder=2)
            ax.scatter([b], [yi], s=42, color=INK, zorder=3)
            if p is not None:
                ax.text(1.02, yi, "p<0.001" if p < 0.001 else f"p={p:.3f}", va="center", fontsize=7,
                        color=INK if p < 0.05 else MUTED, transform=ax.get_yaxis_transform())
        ax.set_xlim(-0.02, 1.02)
        ax.xaxis.set_major_formatter(lambda v, _: f"{v:.0%}")
        ax.set_title("Questions with an unsupported claim" if measure == measures[0] else "Questions declined")
        ax.grid(axis="x", color=RULE, linewidth=0.6)
        ax.set_axisbelow(True)
    n_of = {(r["family"], r["a"], r["b"]): r["n"] for r in rows if r["measure"] == measures[0]}
    axes[0].set_yticks(y, [f"{k[0]}: {condition_label(k[1])} vs {condition_label(k[2])}  (n={n_of.get(k, '?')})" for k, _ in pairs])
    axes[0].tick_params(axis="y", length=0)
    handles = [plt.Line2D([], [], marker="o", linestyle="", markerfacecolor="white", markeredgecolor=INK, label="first condition (A)"),
               plt.Line2D([], [], marker="o", linestyle="", color=INK, label="second condition (B)")]
    axes[1].legend(handles=handles, loc="lower center", bbox_to_anchor=(0.5, -0.32), ncol=2)
    fig.text(0.99, -0.02, "p: exact McNemar, Holm-adjusted within each research question", ha="right", fontsize=7, color=MUTED)
    _save(fig, out, "fig3_contrasts")
    return fig


def fig_withheld(plt, summary, model, out):
    rows = sorted((r for r in summary if r["model"] == model and r["task"] == "ALL" and "@" in r["system"]
                   and ":random" not in r["system"]), key=lambda r: _order(r["system"]))
    if not rows:
        return None
    labels = [condition_label(r["system"]) for r in rows]
    fig, axes = plt.subplots(1, 2, figsize=(9.0, 0.42 * len(rows) + 1.2), sharey=True)
    y = list(range(len(rows)))[::-1]
    rate = [_num(r.get("withheld_restated_rate")) for r in rows]
    lo = [_num(r.get("withheld_restated_ci_low")) for r in rows]
    hi = [_num(r.get("withheld_restated_ci_high")) for r in rows]
    for yi, v, a, b in zip(y, rate, lo, hi):
        if v is None:
            continue
        axes[0].barh(yi, v, color=MEMORY, height=0.5)
        if a is not None and b is not None:
            axes[0].plot([a, b], [yi, yi], color=INK, linewidth=1)
        axes[0].text(max(v, b or 0) + 0.02, yi, f"{v:.0%}", va="center", fontsize=7.5, color=INK)
    axes[0].set_title("Withheld facts the model still named")
    for yi, r in zip(y, rows):
        f1 = _num(r.get("refusal_f1"))
        if f1 is not None:
            axes[1].barh(yi, f1, color=INK, height=0.5)
            axes[1].text(f1 + 0.02, yi, f"{f1:.2f}", va="center", fontsize=7.5, color=INK)
    axes[1].set_title("Grounded refusal F1 (higher is better)")
    for ax in axes:
        ax.set_xlim(0, 1.12)
        ax.tick_params(axis="y", length=0)
    axes[0].xaxis.set_major_formatter(lambda v, _: f"{v:.0%}")
    axes[0].set_yticks(y, labels)
    _save(fig, out, "fig4_withheld")
    return fig


def refusal_by_answer_presence(rows):
    """Post hoc: among 'evidence insufficient' questions, decline and correct rates by whether the answer was in the evidence."""
    groups = defaultdict(lambda: {"n": 0, "declined": 0, "correct": 0})
    for r in rows:
        if r.get("repetition", 0) != 0 or r.get("error") is not None or r.get("sufficient"):
            continue
        g = groups[(r["model_name"], r["system"], bool(r.get("answer_in_evidence")))]
        g["n"] += 1
        g["declined"] += bool(r.get("abstained"))
        g["correct"] += (not r.get("abstained")) and (r.get("score") or 0) >= 0.999
    out = []
    for (model, system, present), g in sorted(groups.items(), key=lambda kv: (kv[0][0], _order(kv[0][1]), kv[0][2])):
        out.append({"model": model, "system": system, "answer_in_evidence": present, "n": g["n"],
                    "declined_rate": g["declined"] / g["n"], "correct_rate": g["correct"] / g["n"]})
    return out


def write_figures(results_dir, model=None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    results_dir = Path(results_dir)
    analysis = results_dir / "analysis_study2"
    if not (analysis / "summary.csv").exists():
        raise FileNotFoundError(f"No analysis tables in {analysis}. Run: python -m evaluation.study2_report {results_dir}")
    summary, outcomes = _read(analysis / "summary.csv"), _read(analysis / "outcomes.csv")
    contrasts = _read(analysis / "contrasts.csv") if (analysis / "contrasts.csv").exists() else []
    models = sorted({r["model"] for r in summary if r["model"] != "R0"})
    if not models:
        raise ValueError("No model rows (only the reference answerer) in the analysis tables")
    model = model or models[0]
    out = analysis / "figures"
    out.mkdir(exist_ok=True)
    _style(plt)
    made = []
    for name, fn, table in (("fig1_outcomes", fig_outcomes, outcomes), ("fig2_claims", fig_claims, summary),
                            ("fig3_contrasts", fig_contrasts, contrasts), ("fig4_withheld", fig_withheld, summary)):
        fig = fn(plt, table, model, out)
        if fig is not None:
            plt.close(fig)
            made.append(name)
    rows = [json.loads(line) for line in open(results_dir / "system_responses.jsonl", encoding="utf-8")]
    table = refusal_by_answer_presence(rows)
    if table:
        with open(analysis / "exploratory_refusal_by_answer_presence.csv", "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(table[0]))
            writer.writeheader()
            writer.writerows(table)
    return {"model": model, "figures": made, "out": out, "exploratory_rows": len(table)}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Study 2 figures (no API calls)")
    parser.add_argument("results_dir", nargs="?", default="results/v3_cse499")
    parser.add_argument("--model", help="model name as in the tables (default: the first non-reference model)")
    args = parser.parse_args(argv)
    info = write_figures(args.results_dir, args.model)
    print(f"{info['model']}: {', '.join(info['figures']) or 'no figures'} in {info['out']}")
    print(f"exploratory table: {info['exploratory_rows']} rows (post hoc, not in protocol v3.0)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
