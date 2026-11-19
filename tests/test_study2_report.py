"""Study 2 analysis measures on hand-made rows, and the report end to end on a mock run."""

import json

import pytest

from evaluation import study2_report as rep


def _row(**kw):
    base = {"model_name": "m", "system": "G-QA@50", "task": "T1", "item_id": "i1", "concept_id": "x",
            "repetition": 0, "error": None, "abstained": False, "sufficient": False, "score": 1.0,
            "n_claims": 0, "n_supported_by_subgraph": 0, "answer": []}
    base.update(kw)
    return base


def test_outcome():
    assert rep.outcome(_row(score=1.0)) == "correct"
    assert rep.outcome(_row(score=0.5)) == "wrong"
    assert rep.outcome(_row(abstained=True, score=0.0)) == "abstained"
    assert rep.outcome(_row(error="boom")) is None


def test_withheld_fact_restatement():
    r = _row(task="T1", answer=["p1", "p2"], mask_withheld_edges=[["x", "p1"], ["x", "p3"], ["a", "b"]])
    assert rep.withheld_answer_facts(r) == (2, 1)                 # p1 restated, p3 not; (a, b) not an answer edge
    r = _row(task="T2", answer=["c1"], mask_withheld_edges=[["c1", "x"], ["c2", "x"]])
    assert rep.withheld_answer_facts(r) == (2, 1)
    assert rep.withheld_answer_facts(_row(task="T4", mask_withheld_edges=[["x", "p"]])) == (0, 0)


def test_closure_flags():
    assert rep.closure_flags(_row(task="T3", answer="no", sufficient=False)) == (True, False)       # over
    assert rep.closure_flags(_row(task="T3", answer="yes", sufficient=True, polarity="neg")) == (False, True)
    assert rep.closure_flags(_row(task="T3", answer="no", sufficient=True, polarity="neg")) == (False, False)
    assert rep.closure_flags(_row(task="T1")) == (None, None)


def test_grounded_refusal():
    rows = [_row(abstained=True, sufficient=False), _row(abstained=True, sufficient=False),
            _row(abstained=True, sufficient=True), _row(abstained=False, sufficient=False),
            _row(abstained=False, sufficient=True)]
    precision, recall, f1 = rep.grounded_refusal(rows)
    assert precision == pytest.approx(2 / 3) and recall == pytest.approx(2 / 3) and f1 == pytest.approx(2 / 3)


def test_contrasts_are_paired_and_holm_adjusted():
    rows = []
    for i in range(12):
        rows.append(_row(system="G-QA", item_id=f"i{i}", score=1.0, sufficient=True))
        rows.append(_row(system="G-QA@50", item_id=f"i{i}", score=0.0, abstained=i < 8,
                         n_claims=2, n_supported_by_subgraph=0 if i >= 8 else 2))
    tests = rep.contrasts(rows)
    unsup = next(t for t in tests if t["a"] == "G-QA" and t["b"] == "G-QA@50"
                 and t["measure"] == "items with an unsupported claim")
    assert unsup["rate_a"] == 0 and unsup["rate_b"] == pytest.approx(4 / 12)
    assert all("p_holm" in t for t in tests if t["p"] is not None)
    assert all(t["p_holm"] >= t["p"] for t in tests if t["p"] is not None)


def test_report_runs_on_a_mock_study2_run(tmp_path):
    from tests.test_study2_runner import MOCK  # noqa: F401  (same mock model spec)
    rows = [_row(system=s, item_id=f"i{k}", task=t, model_name=m, provider="mock")
            for s in ("G-QA", "G-QA@50", "L-RAG") for k, t in enumerate(("T1", "T3", "T5")) for m in ("m", "R0")]
    out = tmp_path / "res"
    out.mkdir()
    with open(out / "system_responses.jsonl", "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    info = rep.write_report(out)
    assert info["synthetic"] and (info["out"] / "summary.csv").exists() and (info["out"] / "contrasts.csv").exists()
    assert (info["out"] / "outcomes.csv").read_text(encoding="utf-8").startswith("model,")
