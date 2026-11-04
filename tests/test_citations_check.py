"""Citation verification and metrics (protocol v2.0, Section 7)."""

import pytest

from evaluation.conditions import Context
from evaluation.scoring import score_answer
from evaluation.tasks import Item, reference_answer
from explanation.citations import (
    ENTAILS, INVALID_EDGE_ID, NO_CITATION, NOT_APPLICABLE, NOT_ENTAILED,
    check_citation, cite_for, citation_counts, supporting_path,
)
from explanation.explainer import ReferenceHierarchy

# X(700) -> P1(500) -> TOP(100); X -> P2(510) -> TOP; CHILD(800) -> X
EDGES = {("700", "500"), ("700", "510"), ("500", "100"), ("510", "100"), ("800", "700")}
LABELS = {"700": "X", "500": "P1", "510": "P2", "100": "Top", "800": "Child"}


def context():
    return Context(condition="FULL", focal_id="700", nodes=dict(LABELS), edges=set(EDGES),
                   parents_status={c: "complete" for c in ("700", "500", "510", "100", "800")},
                   children_status={"700": "complete"})


def reference():
    return ReferenceHierarchy(EDGES, nodes=set(LABELS), children_complete={"700", "800"},
                              parents_complete=set(LABELS))


def ids(ctx):
    """edge -> ID, for writing claims in tests."""
    return {edge: eid for eid, edge in ctx.edge_index().items()}


def claim(s, rel, o=None, cites=()):
    return {"subject": s, "relation": rel, "object": o, "source": "evidence", "cites": list(cites)}


def test_supporting_path():
    assert supporting_path("700", "100", EDGES) in ([("700", "500"), ("500", "100")],
                                                    [("700", "510"), ("510", "100")])
    assert supporting_path("700", "800", EDGES) is None   # never downward
    assert supporting_path("700", "700", EDGES) is None


def test_citation_statuses():
    ctx = context()
    index, e = ctx.edge_index(), ids(context())
    assert check_citation(claim("700", "direct_parent", "500", [e[("700", "500")]]), index) == ENTAILS
    assert check_citation(claim("700", "direct_parent", "500", [e[("700", "510")]]), index) == NOT_ENTAILED
    assert check_citation(claim("700", "direct_parent", "500", [e[("700", "500")], "E99"]), index) == INVALID_EDGE_ID
    assert check_citation(claim("700", "direct_parent", "500"), index) == NO_CITATION
    assert check_citation(claim("700", "no_children"), index) == NOT_APPLICABLE


def test_ancestor_needs_a_complete_cited_chain():
    index, e = context().edge_index(), ids(context())
    chain = [e[("700", "500")], e[("500", "100")]]
    assert check_citation(claim("700", "ancestor", "100", chain), index) == ENTAILS
    assert check_citation(claim("700", "ancestor", "100", chain[:1]), index) == NOT_ENTAILED
    mixed = [e[("700", "500")], e[("510", "100")]]  # broken chain
    assert check_citation(claim("700", "ancestor", "100", mixed), index) == NOT_ENTAILED


def test_cite_for_gives_minimal_support():
    index = context().edge_index()
    two = cite_for(claim("700", "ancestor", "100"), index)
    assert len(two) == 2 and check_citation(claim("700", "ancestor", "100", two), index) == ENTAILS
    assert cite_for(claim("700", "direct_parent", "999"), index) == []
    assert cite_for(claim("700", "no_children"), index) == []


@pytest.mark.parametrize("task, gold, target", [("T1", ["500", "510"], None), ("T2", ["800"], None),
                                               ("T3", True, "100"), ("T4", None, "100"), ("T5", None, None)])
def test_reference_answers_have_perfect_citations(task, gold, target):
    ctx = context()
    item = Item(f"t:{task}", task, "700", gold, target_id=target, polarity="pos" if task == "T3" else None)
    parsed = dict(reference_answer(item, ctx), parse_error=None, dropped_claims=0)
    row = score_answer(item, parsed, ctx, reference())
    assert row["n_cite_attempted"] == row["n_cite_entails"]
    assert row["n_supported_citable"] == row["n_supported_cited_ok"] > 0


def test_wrong_citations_lower_precision_and_recall():
    ctx = context()
    e = ids(ctx)
    claims = [claim("700", "direct_parent", "500", [e[("700", "500")]]),   # correct
              claim("700", "direct_parent", "510", [e[("700", "500")]]),   # wrong edge
              claim("700", "ancestor", "100", ["E42"]),                    # fabricated ID
              claim("800", "direct_parent", "700")]                        # supported, uncited
    parsed = {"answer": ["500", "510"], "claims": claims, "insufficient_evidence": False,
              "parse_error": None, "dropped_claims": 0}
    row = score_answer(Item("t:T1", "T1", "700", ["500", "510"]), parsed, ctx, reference())
    assert row["n_cite_attempted"] == 3 and row["n_cite_entails"] == 1
    assert row["n_cite_not_entailed"] == 1 and row["n_cite_invalid_edge_id"] == 1
    assert row["n_cite_missing"] == 1
    assert row["n_supported_citable"] == 4 and row["n_supported_cited_ok"] == 1
    assert {c["citation"] for c in row["claims"]} == {ENTAILS, NOT_ENTAILED, INVALID_EDGE_ID, NO_CITATION}


def test_citation_counts_ignore_absence_claims():
    counts = citation_counts([{"citation": NOT_APPLICABLE, "label": "supported_by_subgraph"}])
    assert counts["n_cite_citable"] == counts["n_supported_citable"] == 0
