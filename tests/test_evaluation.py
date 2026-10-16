"""
Evaluation framework tests. Everything here uses the SYNTHETIC hierarchy
(tests/fakes.py); none of it is a real-world or clinical evaluation.

Hand-derived facts about the synthetic hierarchy used below:
    ancestors(700) = {500, 400, 200, 300, 100}
    children(400)  = {500, 600}
    children(600)  = {}  (leaf)
"""

import json
from pathlib import Path

import pytest

from crawler.bfs_crawler import BFSCrawler
from evaluation import benchmark as bm
from evaluation import metrics as m
from explanation.explainer import FALSE_IN_REFERENCE, INSUFFICIENT, SUPPORTED, UNKNOWN_IDENTIFIER
from tests.fakes import SYNTHETIC_LABELS, FakeOntologyClient, InMemoryRepository

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "synthetic_gold_snapshot.json"
ALL_IDS = sorted(SYNTHETIC_LABELS)


# ----------------------------------------------------------------------
# Metrics
# ----------------------------------------------------------------------

@pytest.mark.parametrize(
    "pred, gold, expected",
    [
        ({"a", "b"}, {"a", "b"}, (1.0, 1.0, 1.0)),
        ({"a"}, {"a", "b"}, (1.0, 0.5, 2 / 3)),
        ({"a", "c"}, {"a", "b"}, (0.5, 0.5, 0.5)),
        (set(), set(), (1.0, 1.0, 1.0)),
        (set(), {"a"}, (0.0, 0.0, 0.0)),
        ({"a"}, set(), (0.0, 0.0, 0.0)),
    ],
)
def test_set_prf_including_empty_set_conventions(pred, gold, expected):
    result = m.set_prf(pred, gold)
    assert (result["precision"], result["recall"], result["f1"]) == pytest.approx(expected)


def test_path_edge_f1_and_exact_match():
    assert m.path_edge_f1(["7", "5", "4"], ["7", "5", "4"])["f1"] == 1.0
    assert m.path_edge_f1(["7", "5"], ["7", "5", "4"])["recall"] == pytest.approx(0.5)
    assert m.exact_match(["7", "5"], ["7", "5"]) is True


def test_accuracy_requires_equal_lengths():
    assert m.accuracy([True, False], [True, True]) == 0.5
    assert m.accuracy([], []) is None
    with pytest.raises(ValueError):
        m.accuracy([True], [True, False])


def test_claim_label_rates():
    rates = m.claim_label_rates([SUPPORTED, SUPPORTED, INSUFFICIENT, FALSE_IN_REFERENCE, UNKNOWN_IDENTIFIER])
    assert rates["n_claims"] == 5
    assert rates["unsupported_rate"] == pytest.approx(0.6)
    assert rates["false_claim_rate"] == pytest.approx(0.4)
    assert m.claim_label_rates([])["unsupported_rate"] is None
    with pytest.raises(ValueError):
        m.claim_label_rates(["made_up_label"])


def test_identifier_hallucination_and_citation_metrics():
    assert m.identifier_hallucination_rate(["1", "2", "9"], {"1", "2"}) == pytest.approx(1 / 3)
    assert m.identifier_hallucination_rate([], {"1"}) is None
    cites = m.citation_precision_recall({"a", "z"}, {"a", "b"}, {"a", "b"})
    assert cites == {"citation_precision": 0.5, "citation_recall": 0.5}


def test_bootstrap_is_deterministic_and_brackets_the_mean():
    values = [0.0, 0.5, 1.0, 1.0, 0.25]
    first = m.bootstrap_ci(values, seed=7)
    second = m.bootstrap_ci(values, seed=7)
    assert first == second
    assert first["lower"] <= first["mean"] <= first["upper"]
    assert m.bootstrap_ci([None, None]) is None


# ----------------------------------------------------------------------
# Gold snapshot
# ----------------------------------------------------------------------

def snapshot(**client_kwargs):
    return bm.build_gold_snapshot(FakeOntologyClient(**client_kwargs), ALL_IDS)


def test_gold_snapshot_matches_hand_derived_hierarchy():
    snap = snapshot()

    assert set(snap.concepts) == set(ALL_IDS)
    assert set(snap.concepts["700"].ancestors) == {"500", "400", "200", "300", "100"}
    assert snap.concepts["400"].children == ["500", "600"]
    assert snap.concepts["400"].children_complete is True
    assert snap.version == "SYNTHETIC-TEST"
    assert snap.retrieved_at


def test_failed_concepts_are_recorded_not_partially_stored():
    snap = snapshot(fail_parents={"500"})

    assert "500" not in snap.concepts
    assert "500" in snap.failures


def test_truncated_children_are_flagged():
    snap = snapshot(children_page_limit=1)
    assert snap.concepts["400"].children_complete is False


def test_live_size_guard():
    with pytest.raises(ValueError):
        bm.build_gold_snapshot(FakeOntologyClient(), [str(100000 + i) for i in range(bm.MAX_LIVE_CONCEPTS + 1)])


def test_snapshot_round_trip(tmp_path):
    snap = snapshot()
    path = snap.save(tmp_path / "gold.json")
    assert bm.GoldSnapshot.load(path).to_dict() == snap.to_dict()


def test_snapshot_schema_version_checked(tmp_path):
    data = snapshot().to_dict()
    data["schema_version"] = "0.1"
    path = tmp_path / "old.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        bm.GoldSnapshot.load(path)


def test_reference_hierarchy_from_snapshot():
    reference = snapshot().reference_hierarchy()
    assert reference.is_ancestor("700", "100") is True
    assert reference.is_ancestor("700", "600") is False
    assert reference.has_edge("500", "400") is True


# ----------------------------------------------------------------------
# Items, R0 system and scoring
# ----------------------------------------------------------------------

def test_items_are_deterministic_for_a_seed():
    snap = snapshot()
    first = [i.to_dict() for i in bm.generate_items(snap, seed=3)]
    second = [i.to_dict() for i in bm.generate_items(snap, seed=3)]
    assert first == second


def test_t2_only_for_complete_child_lists():
    items = bm.generate_items(snapshot(children_page_limit=1), seed=0)
    t2_ids = {i.concept_id for i in items if i.task == "T2_direct_children"}
    assert "400" not in t2_ids          # 400 has 2 children, truncated to 1
    assert "600" in t2_ids              # leaf: empty list, complete


def test_t3_negatives_are_not_ancestors():
    snap = snapshot()
    for item in bm.generate_items(snap, seed=1):
        if item.task == "T3_is_a" and item.gold is False:
            assert item.target_id not in snap.concepts[item.concept_id].ancestors
            assert item.concept_id not in (snap.concepts[item.target_id].ancestors or [])


def crawled_graph(depth):
    repo = InMemoryRepository()
    for start in ALL_IDS:
        crawler = BFSCrawler(max_depth=depth, client=FakeOntologyClient(), repository=repo)
        crawler.add_start_concept(start)
        crawler.crawl()
    return repo


def test_full_retrieval_gives_perfect_r0_scores_on_t1_and_t3():
    snap = snapshot()
    items = [i for i in bm.generate_items(snap, seed=0) if i.task != "T2_direct_children"]

    report = bm.evaluate(items, bm.answer_from_graph(items, crawled_graph(depth=10)))

    assert report["T1_direct_parents"]["f1"] == 1.0
    assert report["T3_is_a"]["accuracy"] == 1.0


def per_query_predictions(items, depth):
    """Each item answered from a fresh subgraph crawled only from its own concept."""
    predictions = {}
    for item in items:
        repo = InMemoryRepository()
        crawler = BFSCrawler(max_depth=depth, client=FakeOntologyClient(), repository=repo)
        crawler.add_start_concept(item.concept_id)
        crawler.crawl()
        predictions.update(bm.answer_from_graph([item], repo))
    return predictions


def test_shallow_per_query_retrieval_lowers_is_a_accuracy():
    """Synthetic data only: depth-1 subgraphs miss distant ancestors, so true
    is-a pairs are answered False. Negatives stay correct."""
    snap = snapshot()
    items = [i for i in bm.generate_items(snap, seed=0) if i.task == "T3_is_a"]

    deep = bm.evaluate(items, per_query_predictions(items, depth=10))
    shallow = bm.evaluate(items, per_query_predictions(items, depth=1))

    assert deep["T3_is_a"]["accuracy"] == 1.0
    assert shallow["T3_is_a"]["accuracy"] < 1.0
    negatives = [i for i in items if i.gold is False]
    shallow_predictions = per_query_predictions(negatives, depth=1)
    assert all(shallow_predictions[i.item_id] is False for i in negatives)


def test_missing_predictions_are_counted_and_scored_as_wrong():
    items = bm.generate_items(snapshot(), seed=0)
    report = bm.evaluate(items, {})
    assert report["T1_direct_parents"]["missing"] == report["T1_direct_parents"]["n"]
    assert report["T3_is_a"]["accuracy"] < 1.0


def test_committed_fixture_is_synthetic_and_loads():
    snap = bm.GoldSnapshot.load(FIXTURE)
    assert snap.synthetic is True
    assert "SYNTHETIC" in snap.notes
    assert set(snap.concepts) == set(ALL_IDS)


def test_cli_refuses_without_live_flag(tmp_path):
    ids = tmp_path / "ids.txt"
    ids.write_text("233604007\n")
    with pytest.raises(SystemExit):
        bm._main(["--ids", str(ids), "--out", str(tmp_path / "x.json")])
