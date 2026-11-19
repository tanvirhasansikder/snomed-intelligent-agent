"""Study 2 error review: sampling, hints, the workbook (blind and not) and the scoring, on the fake database."""

import json

import pytest

pytest.importorskip("openpyxl")
from openpyxl import load_workbook  # noqa: E402

from evaluation import study2_error_review as er  # noqa: E402
from tests.test_study2_report import _row  # noqa: E402
from tests.test_text_retrieval import AMI, IHD, MI, NEC, TOP, db  # noqa: E402,F401  (fixture)


def _rows():
    rows = []
    for k in range(10):
        rows.append(_row(system="G-QA@50", item_id=f"T1:{k}", concept_id=MI, task="T1", answer=[IHD], score=0.5,
                         claims=[{"subject": MI, "relation": "direct_parent", "object": IHD, "source": "evidence",
                                  "cites": ["E1"], "label": "supported_by_subgraph"}], n_claims=1, n_supported_by_subgraph=1))
        rows.append(_row(system="G-QA", item_id=f"T1:{k}", concept_id=MI, task="T1", answer=[IHD, NEC], score=1.0,
                         sufficient=True))
    rows.append(_row(system="B0", item_id="T1:x", concept_id=MI, task="T1", answer=["999"], score=0.0, n_unknown_identifier=1,
                     claims=[{"subject": MI, "relation": "direct_parent", "object": "999", "source": "model_knowledge",
                              "cites": [], "label": "unknown_identifier"}]))
    rows.append(_row(system="G-QA", item_id="T1:y", concept_id=AMI, task="T1", answer=[IHD], score=0.0, sufficient=True,
                     answer_in_evidence=True, n_false_in_reference=1,
                     claims=[{"subject": AMI, "relation": "direct_parent", "object": IHD, "source": "evidence",
                              "cites": [], "label": "false_in_reference"}]))
    rows.append(_row(model_name="R0", system="R0|G-QA", item_id="T1:z"))
    return rows


def test_flags_and_hints(db):
    from knowledge_graph.snomed_db_loader import load_graph
    graph = load_graph(db)
    rows = _rows()
    assert er.hint(rows[0]) == "E2"                                        # partial list, every claim supported
    assert er.flags(rows[1]) == [] and er.hint(rows[1]) == "OK"
    assert er.hint(rows[20]) == "E3"                                       # invented ID
    assert er.hint(rows[21], graph) == "E4"                                # grandparent named as direct parent
    assert er.hint(_row(abstained=True, sufficient=True)) == "E7"


def test_selection_is_reproducible_and_capped():
    rows = _rows()
    a, b = er.select(rows, per_condition=3, controls=2), er.select(rows, per_condition=3, controls=2)
    assert [r["item_id"] + r["system"] for r in a] == [r["item_id"] + r["system"] for r in b]
    assert sum(r["system"] == "G-QA@50" for r in a) == 3                   # capped
    assert sum(r["system"] == "G-QA" for r in a) == 3                      # 1 flagged + 2 controls
    assert all(r["model_name"] != "R0" for r in a)


def test_workbooks_and_scoring(db, tmp_path):
    out = tmp_path / "res"
    out.mkdir()
    (out / "system_responses.jsonl").write_text("".join(json.dumps(r) + "\n" for r in _rows()), encoding="utf-8")
    info = er.write_workbook(out, db, per_condition=3, controls=2)
    assert info["names"] and info["rows"] == 7
    ws = load_workbook(info["path"])["Review"]
    head = [c.value for c in ws[1]]
    first = dict(zip(head, [c.value for c in ws[2]]))
    assert "Myocardial infarction" in first["question"] and first["suggested_code"] in er.CODE_IDS
    blind = er.write_workbook(out, db, per_condition=3, controls=2, blind=True)
    bhead = [c.value for c in load_workbook(blind["path"])["Review"][1]]
    assert not {"condition", "item_id", "auto_flags", "suggested_code", "outcome"} & set(bhead)
    gold = [c.value for c in load_workbook(blind["path"])["Review"]["D"]][1:]
    assert any(v and "Ischemic heart disease" in v for v in gold)           # reference answers from the graph

    # two annotators: same codes except one row
    for path, codes in ((info["path"], ["E2", "OK", "E3", "OK", "E2", "E4", "OK"]),
                        (blind["path"], ["E2", "OK", "E3", "OK", "E2", "E5", "OK"])):
        wb = load_workbook(path)
        ws = wb["Review"]
        col = [c.value for c in ws[1]].index("primary_code") + 1
        for i, code in enumerate(codes, 2):
            ws.cell(i, col, code)
        wb.save(path)
    result = er.score(out, info["path"], blind["path"])
    assert result["coded"] == 7 and result["both"] == 7 and abs(result["agreement"] - 6 / 7) < 1e-9
    assert 0.7 < result["kappa"] < 1
    assert result["out"].read_text(encoding="utf-8").startswith("condition,n,E1")


def test_kappa():
    assert er.cohen_kappa(["a", "b", "a"], ["a", "b", "a"]) == 1.0
    assert er.cohen_kappa([], []) is None
    assert abs(er.cohen_kappa(["a", "a", "b", "b"], ["a", "b", "a", "b"])) < 1e-9
