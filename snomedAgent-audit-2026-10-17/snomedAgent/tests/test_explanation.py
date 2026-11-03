"""
Claim verifier and template explanation tests on the synthetic hierarchy:

    100 ── 200 ─┐
       └── 300 ─┴─ 400 ─┬─ 500 ── 700
                        └─ 600
"""

import pytest

from agent.query_processor import QueryProcessor
from explanation.explainer import (
    ANCESTOR,
    CONTRADICTED,
    DIRECT_PARENT,
    FALSE_IN_REFERENCE,
    INSUFFICIENT,
    SUPPORTED,
    TRUE_NOT_RETRIEVED,
    UNKNOWN_IDENTIFIER,
    HierarchyClaim,
    ReferenceHierarchy,
    SubgraphEvidence,
    filter_supported,
    parse_claims,
    verify_claim,
    verify_claims,
)
from explanation.path_explainer import explain_position, explain_upward_path
from knowledge_graph.graph_builder import GraphBuilder
from tests.fakes import SYNTHETIC_LABELS, SYNTHETIC_PARENTS, FakeOntologyClient, InMemoryRepository

FULL_REFERENCE = ReferenceHierarchy(
    {(c, p) for c, ps in SYNTHETIC_PARENTS.items() for p in ps}, nodes=SYNTHETIC_LABELS.keys())

# Evidence after crawling 700 to depth 1: only 700 -> 500 retrieved,
# and 700's parent list is known to be complete.
PARTIAL = SubgraphEvidence({"700": "v", "500": "t1"}, {("700", "500")}, parents_complete={"700"})


def claim(subject, relation, obj):
    return HierarchyClaim(subject, relation, obj)


def test_retrieved_edge_is_supported():
    assert verify_claim(claim("700", DIRECT_PARENT, "500"), PARTIAL) == SUPPORTED


def test_wrong_direct_parent_contradicted_only_because_parent_list_complete():
    assert verify_claim(claim("700", DIRECT_PARENT, "400"), PARTIAL) == CONTRADICTED


def test_unretrieved_ancestor_is_insufficient_not_false_without_reference():
    """The key rule: an incomplete graph is not proof of absence."""
    assert verify_claim(claim("700", ANCESTOR, "400"), PARTIAL) == INSUFFICIENT


def test_subject_outside_subgraph_is_insufficient():
    assert verify_claim(claim("600", DIRECT_PARENT, "400"), PARTIAL) == INSUFFICIENT


def test_with_reference_true_but_not_retrieved():
    assert verify_claim(claim("700", ANCESTOR, "400"), PARTIAL, FULL_REFERENCE) == TRUE_NOT_RETRIEVED


def test_with_reference_false_claim():
    assert verify_claim(claim("700", ANCESTOR, "600"), PARTIAL, FULL_REFERENCE) == FALSE_IN_REFERENCE


def test_with_reference_unknown_identifier():
    assert verify_claim(claim("700", DIRECT_PARENT, "9999999"), PARTIAL, FULL_REFERENCE) == UNKNOWN_IDENTIFIER


def test_ancestor_contradicted_when_full_closure_complete():
    evidence = SubgraphEvidence(
        {"500": "", "400": "", "200": "", "300": "", "100": ""},
        {("500", "400"), ("400", "200"), ("400", "300"), ("200", "100"), ("300", "100")},
        parents_complete={"500", "400", "200", "300", "100"},
    )
    assert verify_claim(claim("500", ANCESTOR, "100"), evidence) == SUPPORTED
    assert verify_claim(claim("500", ANCESTOR, "600"), evidence) == CONTRADICTED


def test_verify_claims_preserves_order():
    results = verify_claims([claim("700", DIRECT_PARENT, "500"), claim("700", ANCESTOR, "400")], PARTIAL)
    assert [r["label"] for r in results] == [SUPPORTED, INSUFFICIENT]


def test_filter_supported_removes_unverified_claims():
    kept, removed = filter_supported([claim("700", DIRECT_PARENT, "500"), claim("700", ANCESTOR, "400")], PARTIAL)
    assert kept == [claim("700", DIRECT_PARENT, "500")]
    assert removed == [claim("700", ANCESTOR, "400")]


@pytest.mark.parametrize("record", [
    {"subject": "700", "relation": "sibling_of", "object": "600"},
    {"subject": "seven", "relation": DIRECT_PARENT, "object": "500"},
    {"subject": "700", "relation": DIRECT_PARENT},
    "not a dict",
])
def test_parse_claims_rejects_invalid_records(record):
    with pytest.raises(ValueError):
        parse_claims([record])


def test_parse_claims_accepts_valid_records():
    assert parse_claims([{"subject": 700, "relation": ANCESTOR, "object": "100"}]) == [claim("700", ANCESTOR, "100")]


# ----------------------------------------------------------------------
# Template explanation (R0)
# ----------------------------------------------------------------------

def query_result(label, fetch_children, depth=2):
    repo = InMemoryRepository()
    concept_id = next(k for k, v in SYNTHETIC_LABELS.items() if v == label)
    repo.save_concept(concept_id, label)
    client = FakeOntologyClient()
    builder = GraphBuilder(default_depth=depth, repository=repo, client=client)
    return QueryProcessor(expansion_depth=depth, repository=repo, graph_builder=builder,
                          bioportal=client, fetch_children=fetch_children).process_query(label)


def test_every_template_claim_is_supported_by_its_evidence():
    result = query_result("Disorder AB", fetch_children=True)

    explanation = explain_position(result)
    evidence = SubgraphEvidence.from_query_result(result)

    assert explanation["claims"]
    assert all(verify_claim(c, evidence) == SUPPORTED for c in explanation["claims"])
    assert set(explanation["evidence_edges"]) == {"400->200", "400->300", "500->400", "600->400"}


def test_template_states_leaf_only_when_children_complete():
    with_children = explain_position(query_result("Disorder AB type 1 variant", fetch_children=True))
    without = explain_position(query_result("Disorder AB type 1 variant", fetch_children=False))

    assert "leaf concept" in with_children["text"]
    assert "leaf concept" not in without["text"]
    assert any("not known to be complete" in c for c in without["caveats"])


def test_template_top_level_statement_for_root():
    explanation = explain_position(query_result("Root concept", fetch_children=False))
    assert "top of the hierarchy" in explanation["text"]


def test_explain_upward_path():
    path = [{"concept_id": "700", "name": "V"}, {"concept_id": "500", "name": "T1"}, {"concept_id": "400", "name": "AB"}]

    text, claims = explain_upward_path(path)

    assert text == "V (700) is a T1 (500), which is a AB (400)."
    assert claim("700", DIRECT_PARENT, "500") in claims
    assert claim("700", ANCESTOR, "400") in claims


def test_explain_upward_path_empty():
    text, claims = explain_upward_path(None)
    assert "No hierarchy path" in text and claims == []
