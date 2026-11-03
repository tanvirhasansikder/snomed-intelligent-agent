"""Evidence format v2 (protocol v2.0, Section 6): stable edge IDs plus coverage."""

import re

import pytest

from evaluation.conditions import Context


def make_context(**overrides):
    # X -> P1 -> TOP, X -> P2 (frontier), CHILD -> X
    values = dict(
        condition="U2", focal_id="700",
        nodes={"700": "Concept X", "500": "Parent one", "510": "Parent two", "100": "Top", "800": "Child"},
        edges={("700", "500"), ("700", "510"), ("500", "100"), ("800", "700")},
        parents_status={"700": "complete", "500": "complete"},
        children_status={"700": "partial"}, children_total={"700": 3},
        frontier={"510"},
    )
    values.update(overrides)
    return Context(**values)


def test_edge_ids_are_sequential_and_cover_every_edge():
    context = make_context()
    index = context.edge_index()
    assert list(index) == ["E1", "E2", "E3", "E4"]
    assert set(index.values()) == context.edges


def test_rendered_ids_match_the_index():
    context = make_context()
    text = context.render("v2")
    rows = re.findall(r"^(E\d+): .*?\[(\d+)\] IS_A .*?\[(\d+)\]$", text, re.M)
    shown = {eid: (child, parent) for eid, child, parent in rows}
    assert shown == context.edge_index()


def test_ids_are_stable_and_parents_of_focal_come_first():
    first = make_context().edge_index()
    again = make_context().edge_index()
    assert first == again
    assert first["E1"][0] == "700" and first["E2"][0] == "700"


def test_coverage_and_frontier_are_reported():
    text = make_context().render("v2")
    assert "Cite edges by their IDs" in text
    assert "Concept X [700]: parents COMPLETE (2 listed); children PARTIAL" in text
    assert "Parent two [510]: parents NOT RETRIEVED" in text
    assert "Retrieval stopped at the depth limit at: Parent two [510]" in text


def test_empty_and_closed_book_contexts():
    assert make_context(condition="C0").render("v2") == "No evidence was retrieved."
    lonely = make_context(nodes={"700": "Concept X"}, edges=set(), frontier=set(),
                          parents_status={"700": "failed"}, children_status={})
    text = lonely.render("v2")
    assert "(no edges retrieved)" in text and "RETRIEVAL FAILED" in text
    assert lonely.edge_index() == {}


def test_existing_formats_are_unchanged():
    context = make_context()
    assert not re.search(r"^E\d+:", context.render("plain"), re.M)
    assert not re.search(r"^E\d+:", context.render("annotated"), re.M)
    with pytest.raises(ValueError):
        context.render("v3")
