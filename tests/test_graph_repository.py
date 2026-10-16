"""
GraphRepository tests against FakeDB (records Cypher + parameters).
No Neo4j server is contacted. These tests check query construction,
parameterisation, direction and result handling; they cannot prove a query
runs on a real server (see tests/test_cypher_static.py and the optional
integration test for that).
"""

import re

import pytest

from database.graph_repository import GraphRepository
from tests.fakes import FakeDB

INJECTION = "123' OR 1=1 //"


def make_repo(results=None, error=None):
    db = FakeDB(results=results, error=error)
    try:
        repo = GraphRepository(db=db)
    except TypeError:  # original constructor had no injection point
        repo = GraphRepository.__new__(GraphRepository)
        repo.db = db
    return repo, db


def normalise(query):
    return re.sub(r"\s+", " ", query).strip()


def test_save_concept_merges_on_concept_id_with_parameters():
    repo, db = make_repo()

    assert repo.save_concept("233604007", "Pneumonia") is True

    query, params = db.calls[0]
    assert "MERGE (c:Concept {concept_id: $concept_id})" in normalise(query)
    assert params == {"concept_id": "233604007", "name": "Pneumonia"}


def test_save_relationship_is_child_to_parent_and_uses_merge():
    repo, db = make_repo(results=[[{"linked": 1}]])

    assert repo.save_relationship("233604007", "205237003") is True

    query, params = db.calls[0]
    assert "MERGE (child)-[:IS_A]->(parent)" in normalise(query)
    assert params == {"child_id": "233604007", "parent_id": "205237003"}


def test_save_relationship_reports_false_when_endpoint_missing():
    """Defect: the original returned True even when MATCH found no endpoints."""
    repo, db = make_repo(results=[[]])

    assert repo.save_relationship("233604007", "205237003") is False


def test_get_parents_and_children_use_opposite_directions():
    repo, db = make_repo(results=[[], []])

    repo.get_parents("233604007")
    repo.get_children("233604007")

    parents_q, children_q = normalise(db.calls[0][0]), normalise(db.calls[1][0])
    assert "(child:Concept {concept_id: $concept_id}) -[:IS_A]-> (parent:Concept)" in parents_q
    assert "(child:Concept) -[:IS_A]-> (parent:Concept {concept_id: $concept_id})" in children_q


@pytest.mark.parametrize(
    "method, args",
    [
        ("save_concept", (INJECTION, INJECTION)),
        ("get_concept", (INJECTION,)),
        ("concept_exists", (INJECTION,)),
        ("get_parents", (INJECTION,)),
        ("get_children", (INJECTION,)),
        ("get_ancestors", (INJECTION, 3)),
        ("get_descendants", (INJECTION, 3)),
        ("search_concepts", (INJECTION,)),
    ],
)
def test_user_values_are_passed_as_parameters_never_interpolated(method, args):
    repo, db = make_repo(results=[[{"count": 0}]])

    getattr(repo, method)(*args)

    query, params = db.calls[0]
    assert INJECTION not in query
    assert INJECTION in params.values()


def test_get_concept_returns_none_for_empty_result():
    repo, _ = make_repo(results=[[]])
    assert repo.get_concept("233604007") is None


def test_concept_exists_reads_count():
    repo, _ = make_repo(results=[[{"count": 0}], [{"count": 1}]])
    assert repo.concept_exists("1") is False
    assert repo.concept_exists("2") is True


def test_counts_read_total():
    repo, _ = make_repo(results=[[{"total": 3}], [{"total": 5}]])
    assert repo.count_concepts() == 3
    assert repo.count_relationships() == 5


def test_database_errors_propagate_to_caller():
    repo, _ = make_repo(error=RuntimeError("database unavailable"))

    with pytest.raises(RuntimeError, match="database unavailable"):
        repo.save_concept("1", "x")


VARIABLE_LENGTH_PARAMETER = re.compile(r"\*\s*\d*\s*\.\.\s*\$")


@pytest.mark.parametrize("method", ["get_ancestors", "get_descendants"])
def test_traversal_depth_is_a_literal_bound_not_a_parameter(method):
    """Defect: '[:IS_A*1..$max_depth]' is rejected by Cypher (parameters are
    not allowed as variable-length bounds)."""
    repo, db = make_repo()

    getattr(repo, method)("233604007", 4)

    query, params = db.calls[0]
    assert not VARIABLE_LENGTH_PARAMETER.search(query)
    assert "*1..4]" in query.replace(" ", "")
    assert "max_depth" not in params


@pytest.mark.parametrize("method", ["get_ancestors", "get_descendants"])
@pytest.mark.parametrize("bad_depth", [0, -1, 100, "5; MATCH (n) DETACH DELETE n", 2.5, None])
def test_traversal_depth_is_validated(method, bad_depth):
    repo, db = make_repo()

    with pytest.raises((ValueError, TypeError)):
        getattr(repo, method)("233604007", bad_depth)
    assert db.calls == []


def test_ancestors_traverse_upward_descendants_downward():
    repo, db = make_repo(results=[[], []])

    repo.get_ancestors("1", 2)
    repo.get_descendants("1", 2)

    up, down = normalise(db.calls[0][0]), normalise(db.calls[1][0])
    assert re.search(r"\(start:Concept \{concept_id: \$concept_id\}\) -\[:IS_A\*1\.\.2\]-> \(ancestor:Concept\)", up)
    assert re.search(r"\(descendant:Concept\) -\[:IS_A\*1\.\.2\]-> \(start:Concept \{concept_id: \$concept_id\}\)", down)


def test_upward_path_is_directed():
    repo, db = make_repo(results=[[{"path": [{"concept_id": "1"}, {"concept_id": "2"}]}]])

    path = repo.find_upward_path("1", "2")

    assert [n["concept_id"] for n in path] == ["1", "2"]
    assert "-[:IS_A*1..20]->" in normalise(db.calls[0][0]).replace(" ", "")


def test_upward_path_returns_none_when_not_connected_in_local_graph():
    repo, _ = make_repo(results=[[]])
    assert repo.find_upward_path("1", "2") is None


def test_clear_database_refuses_without_explicit_confirmation():
    """Safety: a single call used to delete every node in the database."""
    repo, db = make_repo()

    with pytest.raises(ValueError):
        repo.clear_database()
    assert db.calls == []


def test_ensure_constraints_is_idempotent_unique_constraint():
    repo, db = make_repo()

    repo.ensure_constraints()

    query = normalise(db.calls[0][0])
    assert query.startswith("CREATE CONSTRAINT")
    assert "IF NOT EXISTS" in query
    assert "REQUIRE c.concept_id IS UNIQUE" in query


def test_mark_expansion_writes_whitelisted_properties():
    repo, db = make_repo(results=[[{"n": 1}]])

    repo.mark_expansion("1", "up", "complete", retrieved_at="2026-10-17T00:00:00+00:00",
                        source="BioPortal SNOMEDCT", count=2)

    query, params = db.calls[0]
    assert "c.parents_status = $status" in normalise(query)
    assert params["status"] == "complete"
    assert params["count"] == 2


@pytest.mark.parametrize("direction", ["sideways", "parents_status = 'x' //", ""])
def test_mark_expansion_rejects_unknown_direction(direction):
    repo, db = make_repo()

    with pytest.raises(ValueError):
        repo.mark_expansion("1", direction, "complete")
    assert db.calls == []


def test_mark_expansion_rejects_unknown_status():
    repo, db = make_repo()

    with pytest.raises(ValueError):
        repo.mark_expansion("1", "up", "done-ish")


def test_get_coverage_returns_none_for_unrecorded_directions():
    repo, _ = make_repo(results=[[{
        "parents_status": "complete", "parents_retrieved_at": "t", "parents_source": "s",
        "parents_source_version": None, "parents_count": 2, "parents_error": None,
        "children_status": None, "children_retrieved_at": None, "children_source": None,
        "children_source_version": None, "children_count": None, "children_error": None,
    }]])

    coverage = repo.get_coverage("1")

    assert coverage["parents"]["status"] == "complete"
    assert coverage["parents"]["count"] == 2
    assert coverage["children"] is None


def test_save_children_uses_single_parameterised_batch():
    repo, db = make_repo(results=[[{"linked": 2}]])

    linked = repo.save_children("400", [{"concept_id": "500", "name": "A"}, {"concept_id": "600", "name": "B"}])

    query, params = db.calls[0]
    assert linked == 2
    assert "UNWIND $children AS child_row" in normalise(query)
    assert "MERGE (child)-[:IS_A]->(parent)" in normalise(query)
    assert params["parent_id"] == "400"
