"""v2 report (evaluation/system_report.py)."""

import json

import pytest

from evaluation import report
from evaluation.system_report import (
    hypothesis_tests, load_system_rows, n_unsupported, paired_ratio_diff_ci, ratio_ci, summarise,
    unpaired_ratio_diff_ci, verification_targets,
)
from tests.test_system_runner import data, run  # noqa: F401  (pytest fixture + helper)


def row(system, item, model="m", claims=2, supported=2, false=0, score=1.0, task="T1", **extra):
    return {"system": system, "model_name": model, "item_id": item, "repetition": 0, "task": task, "error": None,
            "n_claims": claims, "n_supported_by_subgraph": supported, "n_false_in_reference": false,
            "n_unknown_identifier": 0, "score": score, **extra}


def test_ratio_ci_pools_claims_and_brackets_the_point():
    rows = [row("S1", f"i{k}", claims=4, supported=3) for k in range(20)]
    point, low, high = ratio_ci(rows, n_unsupported, lambda r: r["n_claims"])
    assert point == pytest.approx(0.25) and low <= point <= high
    assert ratio_ci([row("S1", "i", claims=0, supported=0)], n_unsupported, lambda r: r["n_claims"]) == (None, None, None)


def test_paired_and_unpaired_differences():
    a = [row("B1", f"i{k}", claims=2, supported=1) for k in range(30)]
    b = [row("S1", f"i{k}", claims=2, supported=2) for k in range(30)]
    d, lo, hi = paired_ratio_diff_ci(list(zip(a, b)), n_unsupported, lambda r: r["n_claims"])
    assert d == pytest.approx(-0.5) and lo == pytest.approx(-0.5) and hi == pytest.approx(-0.5)
    d, lo, hi = unpaired_ratio_diff_ci(a, b, n_unsupported, lambda r: r["n_claims"])
    assert d == pytest.approx(0.5)


def test_load_rows_prefers_success(tmp_path):
    path = tmp_path / "system_responses.jsonl"
    failed, ok = dict(row("S1", "i1"), error="HTTP 503"), row("S1", "i1")
    path.write_text("\n".join(json.dumps(r) for r in (failed, ok, failed)), encoding="utf-8")
    rows = load_system_rows(path)
    assert len(rows) == 1 and rows[0]["error"] is None


def test_summary_has_all_rows_and_counts_errors():
    rows = [row("S1", "i1"), row("S1", "i2", task="T2"), dict(row("S1", "i3"), error="x")]
    summary = summarise(rows)
    overall = next(s for s in summary if s["task"] == "ALL")
    assert overall["n_items"] == 3 and overall["n_errors"] == 1 and overall["unsupported_claim_rate"] == 0


def test_hypotheses_and_holm():
    rows = []
    for k in range(25):
        rows += [row("B1", f"i{k}", supported=1 if k % 2 else 2, score=0.0, task="T3", sufficient=k % 2 == 0),
                 row("S1", f"i{k}", supported=2, score=1.0, task="T3", sufficient=True),
                 row("S2", f"i{k}", supported=2, score=1.0, task="T3"),
                 row("B0", f"i{k}", false=1, meta_depth_min=2 + k % 8)]
    tests = hypothesis_tests(rows)
    h2 = next(t for t in tests if t["family"] == "H2" and t["scope"] == "T3")
    assert h2["p"] < 0.001 and h2["mean_b"] == 1.0
    assert all("p_holm" in t for t in tests if t["p"] is not None)
    assert any(t["family"] == "H3" and t.get("rate_diff") is not None for t in tests)
    assert any(t["family"] == "H4" for t in tests)


def test_end_to_end_report_from_runner(data, tmp_path, capsys):  # noqa: F811
    run(data)
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    report._main(["--config", str(path)])
    out_dir = tmp_path / "results" / "analysis_systems"
    for name in ("summary.csv", "summary_by_sufficient.csv", "summary_by_answer_in_evidence.csv",
                 "hypotheses.csv", "verification_targets.csv", "tradeoffs.csv", "table_systems.tex"):
        assert (out_dir / name).exists(), name
    assert "not findings" in capsys.readouterr().out
    rows = load_system_rows(tmp_path / "results" / "system_responses.jsonl")
    assert verification_targets(rows)[0]["items"] > 0
