"""
BFS crawler tests on a hand-written synthetic hierarchy (tests/fakes.py):

    100 (root)
    ├── 200 ──┐
    └── 300 ──┴── 400 ──┬── 500 ── 700
                        └── 600

Expected edges below are derived by hand from this picture, not from the code.
Depth semantics (unchanged from the original): the start concept is depth 0;
a concept at depth < max_depth has its parents fetched; a concept at
depth == max_depth is saved but not expanded.
"""

import pytest

import crawler.bfs_crawler as bfs_module
from crawler.bfs_crawler import BFSCrawler
from tests.fakes import FakeOntologyClient, InMemoryRepository


@pytest.fixture
def build_crawler(monkeypatch):
    def _build(max_depth, client, repository, **kwargs):
        try:
            return BFSCrawler(max_depth=max_depth, client=client, repository=repository, **kwargs)
        except TypeError:
            # Original constructor has no injection points.
            monkeypatch.setattr(bfs_module, "BioPortalClient", lambda: client)
            monkeypatch.setattr(bfs_module, "GraphRepository", lambda: repository)
            return BFSCrawler(max_depth=max_depth)
    return _build


def run(build_crawler, start, depth, client=None, repo=None, **kwargs):
    client = client or FakeOntologyClient()
    repo = repo or InMemoryRepository()
    crawler = build_crawler(depth, client, repo, **kwargs)
    crawler.add_start_concept(start)
    visited = crawler.crawl()
    return crawler, client, repo, visited


def test_depth_two_from_leaf_follows_parent_chain(build_crawler):
    crawler, client, repo, visited = run(build_crawler, "700", 2)

    assert repo.edges == {("700", "500"), ("500", "400")}
    assert set(repo.concepts) == {"700", "500", "400"}
    assert visited == {"700", "500", "400"}


def test_depth_zero_saves_start_only_and_fetches_no_parents(build_crawler):
    crawler, client, repo, _ = run(build_crawler, "700", 0)

    assert set(repo.concepts) == {"700"}
    assert repo.edges == set()
    assert client.calls["get_parents"] == []


def test_polyhierarchy_expands_both_parents_once(build_crawler):
    crawler, client, repo, _ = run(build_crawler, "500", 3)

    assert repo.edges == {
        ("500", "400"),
        ("400", "200"),
        ("400", "300"),
        ("200", "100"),
        ("300", "100"),
    }
    # Each concept below max depth has its parents fetched exactly once,
    # even though 100 is reachable through two paths.
    assert sorted(client.calls["get_parents"]) == ["200", "300", "400", "500"]


def test_cycle_in_source_data_terminates_and_processes_each_node_once(build_crawler):
    client = FakeOntologyClient(parents={"111": ["222"], "222": ["111"]},
                                labels={"111": "A", "222": "B"})

    crawler, client, repo, visited = run(build_crawler, "111", 10, client=client)

    assert visited == {"111", "222"}
    assert repo.edges == {("111", "222"), ("222", "111")}
    assert sorted(client.calls["get_parents"]) == ["111", "222"]


def test_relationship_direction_is_child_to_parent(build_crawler):
    crawler, client, repo, _ = run(build_crawler, "700", 1)

    assert repo.edges == {("700", "500")}
    assert [p["concept_id"] for p in repo.get_parents("700")] == ["500"]
    assert repo.get_children("700") == []


def test_unknown_start_concept_saves_nothing_and_is_reported(build_crawler):
    crawler, client, repo, _ = run(build_crawler, "999", 2)

    assert repo.concepts == {}
    assert "999" in crawler.report.missing


def test_malformed_parent_records_and_self_loops_are_skipped(build_crawler):
    """Defect: the original saved a self-loop IS_A edge if the source returned one."""
    client = FakeOntologyClient(extra_parent_records={
        "700": [{"name": "record without id"}, {"concept_id": "", "name": "empty id"},
                {"concept_id": "700", "name": "self reference"},
                {"concept_id": "not-digits", "name": "bad id"}],
    })

    crawler, client, repo, _ = run(build_crawler, "700", 1, client=client)

    assert repo.edges == {("700", "500")}
    assert crawler.report.malformed_records == 4


def test_parent_fetch_error_is_recorded_not_treated_as_no_parents(build_crawler):
    """Defect: the original turned an API failure into 'no parents' silently."""
    client = FakeOntologyClient(fail_parents={"500"})

    crawler, client, repo, _ = run(build_crawler, "700", 3, client=client)

    assert repo.edges == {("700", "500")}
    assert "500" in crawler.report.failed
    assert repo.get_expansion_status("500", "up")["status"] == "failed"
    assert repo.get_expansion_status("700", "up")["status"] == "complete"


def test_concept_fetch_error_is_recorded_as_failure_not_missing(build_crawler):
    client = FakeOntologyClient(fail_concepts={"700"})

    crawler, client, repo, _ = run(build_crawler, "700", 2, client=client)

    assert "700" in crawler.report.failed
    assert "700" not in crawler.report.missing


def test_frontier_nodes_are_reported(build_crawler):
    crawler, client, repo, _ = run(build_crawler, "700", 2)

    assert crawler.report.frontier == {"400"}


def test_successful_expansion_records_provenance(build_crawler):
    crawler, client, repo, _ = run(build_crawler, "700", 1)

    status = repo.get_expansion_status("700", "up")
    assert status["status"] == "complete"
    assert status["source"] == "BioPortal SNOMEDCT"
    assert status["retrieved_at"]
    assert status["count"] == 1


def test_repeat_crawl_reuses_completed_parent_lists(build_crawler):
    """Defect: every query re-fetched every parent list from BioPortal."""
    repo = InMemoryRepository()
    first, client1, repo, _ = run(build_crawler, "700", 2, repo=repo)
    second, client2, repo, visited = run(build_crawler, "700", 2, repo=repo)

    assert client2.calls["get_parents"] == []
    assert client2.calls["get_concept"] == []
    assert visited == {"700", "500", "400"}
    assert second.report.cache_hits == 2


def test_refresh_bypasses_cache(build_crawler):
    repo = InMemoryRepository()
    run(build_crawler, "700", 2, repo=repo)
    second, client2, _, _ = run(build_crawler, "700", 2, repo=repo, use_cache=False)

    assert sorted(client2.calls["get_parents"]) == ["500", "700"]


def test_crawling_twice_with_same_instance_does_not_repeat_work(build_crawler):
    client = FakeOntologyClient()
    repo = InMemoryRepository()
    crawler = build_crawler(2, client, repo)
    crawler.add_start_concept("700")
    crawler.crawl()
    calls_after_first = list(client.calls["get_parents"])

    crawler.add_start_concept("700")
    crawler.crawl()

    assert client.calls["get_parents"] == calls_after_first


def test_known_start_concept_label_is_not_refetched(build_crawler):
    repo = InMemoryRepository()
    repo.save_concept("700", "Disorder AB type 1 variant")

    _, client, _, _ = run(build_crawler, "700", 1, repo=repo)

    assert client.calls["get_concept"] == []


@pytest.mark.parametrize("bad_depth", [-1, "2", 2.5, None])
def test_invalid_max_depth_is_rejected(bad_depth):
    with pytest.raises((ValueError, TypeError)):
        BFSCrawler(max_depth=bad_depth, client=FakeOntologyClient(), repository=InMemoryRepository())
