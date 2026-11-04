"""T6: nearest common ancestor (protocol v2.0, Section 5)."""

import json

import pytest

from evaluation.bioportal_cache import CallStore, SnapshotClient
from evaluation.collect import collect
from evaluation.conditions import build_context
from evaluation.gold import GoldIndex, lowest_common
from evaluation.scoring import score_answer
from evaluation.synthetic import SyntheticOntology
from evaluation.tasks import generate_items, reference_answer, sufficient
from llm.prompt_manager import build_messages
from llm.reasoner import parse_answer
from query_engine.retrieval_planner import build_s1_context

PARENTS = {"a": ["m"], "b": ["m", "n"], "m": ["top"], "n": ["top"], "top": []}


def ancestors(node):
    seen, stack = set(), list(PARENTS[node])
    while stack:
        p = stack.pop()
        if p not in seen:
            seen.add(p)
            stack.extend(PARENTS[p])
    return seen


def test_lowest_common_excludes_more_general_ancestors():
    assert lowest_common(ancestors("a"), ancestors("b"), ancestors) == ["m"]   # top is more general
    assert lowest_common({"m", "n", "top"}, {"m", "n", "top"}, ancestors) == ["m", "n"]  # polyhierarchy: two


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("t6")
    live = SyntheticOntology(seed=0)
    config = {"name": "t6", "synthetic": True, "data": {"per_hierarchy": 6, "seed": 3, "min_depth": 2,
                                                        "max_depth": 6, "hierarchies": live.top_level_names()}}
    store = CallStore(tmp / "calls.jsonl")
    dataset = collect(config, live, store)
    gold = GoldIndex(store, dataset)
    items = [i for i in generate_items(gold, tasks=("T6",), seed=0)]
    return gold, SnapshotClient(store), items


def test_t6_items_are_valid_pairs(world):
    gold, _, items = world
    assert items
    for item in items:
        x, z = item.concept_id, item.target_id
        assert x != z and z not in gold.ancestors(x) and x not in gold.ancestors(z)
        assert gold.samples[x]["hierarchy_id"] == gold.samples[z]["hierarchy_id"]
        assert item.gold == gold.lowest_common_ancestors(x, z) and item.gold
        common = gold.ancestors(x) & gold.ancestors(z)
        assert set(item.gold) <= common


def test_t6_generation_is_deterministic(world):
    gold, _, items = world
    assert [i.item_id for i in generate_items(gold, tasks=("T6",), seed=0)] == [i.item_id for i in items]


def test_r0_is_perfect_with_both_closures_and_abstains_without(world):
    gold, snapshot, items = world
    reference = gold.reference()
    item = items[0]
    full = build_s1_context(snapshot, item)
    assert sufficient(item, full)
    parsed = dict(reference_answer(item, full), parse_error=None, dropped_claims=0)
    row = score_answer(item, parsed, full, reference)
    assert row["score"] == 1.0 and row["lca_f1"] == 1.0
    assert row["n_cite_attempted"] == row["n_cite_entails"] > 0

    one_side = build_context(snapshot, item.concept_id, "FULL")      # partner not retrieved
    assert not sufficient(item, one_side)
    assert reference_answer(item, one_side)["insufficient_evidence"] is True


def test_b1_baseline_expands_both_concepts(world):
    _, snapshot, items = world
    item = items[0]
    b1 = build_context(snapshot, item.concept_id, "U2", also_expand=(item.target_id,))
    assert item.target_id in b1.nodes and b1.parents_complete(item.target_id)


def test_t6_scoring_hit_and_f1(world):
    gold, snapshot, items = world
    item, reference = items[0], gold.reference()
    context = build_s1_context(snapshot, item)
    wrong = sorted(gold.ancestors(item.concept_id) - set(item.gold))[:1]

    def score(answer):
        text = json.dumps({"answer": answer, "claims": [], "insufficient_evidence": False})
        return score_answer(item, parse_answer(text, "T6"), context, reference)

    assert score(item.gold)["score"] == 1.0
    if wrong:
        partial = score(item.gold[:1] + wrong)
        assert partial["score"] == 1.0 and partial["lca_f1"] < 1.0
        assert score(wrong)["score"] == 0.0


def test_t6_prompt_names_both_concepts(world):
    _, _, items = world
    item = items[0]
    messages = build_messages(item, "evidence", lambda c: f"Label {c}")
    assert f"Label {item.concept_id}" in messages[1]["content"]
    assert f"Label {item.target_id}" in messages[1]["content"]
    assert "nearest common ancestor" in messages[1]["content"]
