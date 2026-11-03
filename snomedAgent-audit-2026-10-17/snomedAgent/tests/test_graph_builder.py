"""GraphBuilder tests with the in-memory repository and fake ontology client."""


from knowledge_graph.graph_builder import GraphBuilder
from tests.fakes import FakeOntologyClient, InMemoryRepository


def make_builder(client=None, repo=None, depth=2, **kwargs):
    return GraphBuilder(
        default_depth=depth,
        repository=repo or InMemoryRepository(),
        client=client or FakeOntologyClient(),
        **kwargs,
    )


def test_expand_concept_saves_parent_chain_and_reports():
    builder = make_builder()

    assert builder.expand_concept("700") is True

    assert builder.repository.edges == {("700", "500"), ("500", "400")}
    assert builder.last_report.processed == 3


def test_expand_concept_uses_explicit_depth_over_default():
    builder = make_builder(depth=1)

    builder.expand_concept("700", depth=3)

    assert ("400", "200") in builder.repository.edges


def test_expand_concept_returns_false_when_start_concept_unavailable():
    builder = make_builder(client=FakeOntologyClient(fail_concepts={"700"}))

    assert builder.expand_concept("700") is False
    assert builder.repository.concepts == {}


def test_expanding_existing_concept_again_makes_no_api_calls():
    repo = InMemoryRepository()
    make_builder(repo=repo).expand_concept("700")
    client = FakeOntologyClient()

    make_builder(client=client, repo=repo).expand_concept("700")

    assert client.request_count == 0


def test_expanding_twice_creates_no_duplicate_relationships():
    builder = make_builder()

    builder.expand_concept("700")
    builder.expand_concept("700", depth=3)

    edges = sorted(builder.repository.edges)
    assert len(edges) == len(set(edges))


def test_partial_failure_keeps_successful_edges_and_reports_failure():
    builder = make_builder(client=FakeOntologyClient(fail_parents={"500"}))

    assert builder.expand_concept("700", depth=3) is True
    assert builder.repository.edges == {("700", "500")}
    assert "500" in builder.last_report.failed


def test_expand_children_saves_direct_children_child_to_parent():
    builder = make_builder()
    builder.expand_concept("400", depth=1)

    coverage = builder.expand_children("400")

    assert coverage["status"] == "complete"
    assert {("500", "400"), ("600", "400")} <= builder.repository.edges
    assert [c["concept_id"] for c in builder.get_children("400")] == ["500", "600"]


def test_expand_children_reports_partial_when_truncated():
    builder = make_builder(client=FakeOntologyClient(children_page_limit=1))
    builder.expand_concept("400", depth=1)

    coverage = builder.expand_children("400")

    assert coverage["status"] == "partial"
    assert len(builder.get_children("400")) == 1


def test_expand_children_failure_is_reported_not_raised():
    builder = make_builder(client=FakeOntologyClient(fail_children={"400"}))
    builder.expand_concept("400", depth=1)

    coverage = builder.expand_children("400")

    assert coverage["status"] == "failed"
    assert coverage["error"]


def test_leaf_with_no_children_is_a_confirmed_negative():
    builder = make_builder()
    builder.expand_concept("700", depth=1)

    coverage = builder.expand_children("700")

    assert coverage["status"] == "complete"
    assert coverage["count"] == 0


def test_completed_child_list_is_reused():
    repo = InMemoryRepository()
    first = make_builder(repo=repo)
    first.expand_concept("400", depth=1)
    first.expand_children("400")
    client = FakeOntologyClient()

    coverage = make_builder(client=client, repo=repo).expand_children("400")

    assert client.calls["fetch_children"] == []
    assert coverage["from_cache"] is True


def test_get_concept_has_no_null_parent_placeholder_for_root():
    """Defect: OPTIONAL MATCH + collect({...}) returned [{concept_id: None, name: None}]."""
    builder = make_builder()
    builder.expand_concept("100", depth=1)

    concept = builder.get_concept("100")

    assert concept["concept_id"] == "100"
    assert concept["parents"] == []


def test_get_concept_returns_none_when_absent():
    assert make_builder().get_concept("999") is None


def test_graph_statistics():
    builder = make_builder()
    builder.expand_concept("700")

    assert builder.get_graph_statistics() == {"concepts": 3, "relationships": 2}


def test_builder_shares_one_repository_with_its_crawler():
    builder = make_builder()
    builder.expand_concept("700")
    assert builder.repository.count_concepts() == 3
