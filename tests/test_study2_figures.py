"""Study 2 figures run end to end on a mock run's analysis tables (no API calls)."""

import csv
import json

import pytest

pytest.importorskip("matplotlib")
from evaluation import study2_figures as figs  # noqa: E402
from evaluation import study2_report as rep  # noqa: E402
from tests.test_study2_report import _row  # noqa: E402


def _write(out, rows):
    out.mkdir()
    with open(out / "system_responses.jsonl", "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def test_condition_labels():
    assert figs.condition_label("G-QA") == "Graph"
    assert figs.condition_label("G-QA@50:unmarked") == "Graph, 50% withheld, no marks"
    assert figs.condition_label("G-QA+V@50:random") == "Graph + verify, 50% withheld, random links"
    assert figs.condition_label("B0") == "No evidence"


def test_figures_and_exploratory_table(tmp_path):
    rows = []
    for k in range(6):
        sufficient = k % 2 == 0
        for system in ("G-QA", "G-QA@50", "L-RAG"):
            rows.append(_row(system=system, item_id=f"i{k}", task="T1", sufficient=system == "G-QA" or sufficient,
                             answer_in_evidence=k % 3 == 0, abstained=(k == 1 and system == "G-QA@50"),
                             score=1.0 if k % 2 == 0 else 0.5, n_claims=2, n_supported_by_subgraph=2 - (k % 2),
                             n_true_not_retrieved=k % 2, provider="mock"))
    out = tmp_path / "res"
    _write(out, rows)
    rep.write_report(out)
    info = figs.write_figures(out)
    assert info["model"] == "m" and {"fig1_outcomes", "fig2_claims", "fig3_contrasts"} <= set(info["figures"])
    for name in info["figures"]:
        assert (info["out"] / f"{name}.png").stat().st_size > 1000 and (info["out"] / f"{name}.svg").exists()
    table = list(csv.DictReader(open(out / "analysis_study2" / "exploratory_refusal_by_answer_presence.csv", encoding="utf-8")))
    assert {r["system"] for r in table} == {"G-QA@50", "L-RAG"}           # complete-evidence rows are left out
    assert all(0 <= float(r["declined_rate"]) <= 1 for r in table)


def test_figures_need_the_tables_first(tmp_path):
    out = tmp_path / "res"
    _write(out, [_row()])
    with pytest.raises(FileNotFoundError):
        figs.write_figures(out)
