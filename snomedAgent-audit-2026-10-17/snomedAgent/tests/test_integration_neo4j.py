"""
OPTIONAL integration test against a DISPOSABLE Neo4j database.

Not run by default (marked ``integration``). It was NOT executed during the
audit that added it, because no Neo4j server was available there.

Run only against an empty, throw-away database:

    $env:NEO4J_TEST_URI="bolt://localhost:7687"
    $env:NEO4J_TEST_USERNAME="neo4j"
    $env:NEO4J_TEST_PASSWORD="..."
    $env:NEO4J_TEST_DATABASE="snomedtest"      # a separate database
    python -m pytest -m integration

Safety: the test refuses to run if the target database already contains any
node, and it deletes only the nodes it created.
"""

import os

import pytest

pytestmark = pytest.mark.integration

REQUIRED = ("NEO4J_TEST_URI", "NEO4J_TEST_USERNAME", "NEO4J_TEST_PASSWORD", "NEO4J_TEST_DATABASE")
TEST_IDS = ["990000101", "990000201", "990000301"]


@pytest.fixture
def repo():
    missing = [name for name in REQUIRED if not os.getenv(name)]
    if missing:
        pytest.skip("integration settings missing: " + ", ".join(missing))

    from database.graph_repository import GraphRepository
    from database.neo4j_connection import Neo4jConnection

    db = Neo4jConnection(
        uri=os.environ["NEO4J_TEST_URI"],
        username=os.environ["NEO4J_TEST_USERNAME"],
        password=os.environ["NEO4J_TEST_PASSWORD"],
        database=os.environ["NEO4J_TEST_DATABASE"],
    )
    total = db.execute_query("MATCH (n) RETURN count(n) AS total")[0]["total"]
    if total != 0:
        db.close()
        pytest.skip("NEO4J_TEST_DATABASE is not empty; refusing to run")

    repository = GraphRepository(db=db)
    yield repository
    db.execute_query("MATCH (c:Concept) WHERE c.concept_id IN $ids DETACH DELETE c", {"ids": TEST_IDS})
    db.close()


def test_round_trip_on_real_neo4j(repo):
    child, parent, grandparent = TEST_IDS
    for cid, name in zip(TEST_IDS, ["child", "parent", "grandparent"]):
        repo.save_concept(cid, name)

    assert repo.save_relationship(child, parent) is True
    assert repo.save_relationship(parent, grandparent) is True
    assert repo.save_relationship(child, "990000999") is False

    assert [p["concept_id"] for p in repo.get_parents(child)] == [parent]
    assert [c["concept_id"] for c in repo.get_children(parent)] == [child]
    assert {a["concept_id"] for a in repo.get_ancestors(child, 3)} == {parent, grandparent}
    assert {d["concept_id"] for d in repo.get_descendants(grandparent, 3)} == {parent, child}
    assert [n["concept_id"] for n in repo.find_upward_path(child, grandparent)] == TEST_IDS

    assert repo.mark_expansion(child, "up", "complete", count=1) is True
    assert repo.get_coverage(child)["parents"]["status"] == "complete"
    assert repo.get_coverage(child)["children"] is None
