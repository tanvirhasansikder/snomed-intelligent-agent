"""Study 2 completeness mask: nested, seeded removal of answer-relevant edges (offline, small fake database)."""

import sqlite3

import pytest

from crawler.local_client import LocalTerminologyClient
from evaluation.completeness import (
    LEVELS, evidence_format, mask_context, mask_levels, n_withheld, relevant_edges, removal_order,
)
from evaluation.tasks import Item, answer_in_evidence, reference_answer, sufficient
from knowledge_graph.snomed_db_loader import ROOT_ID
from query_engine.retrieval_planner import build_s1_context


def cid(n):
    return f"{100000 + n}001"          # International-style identifier (partition 00)


TOP = "404684003"
A, B, C, D, X, Y, K1, K2, K3 = (cid(n) for n in range(1, 10))
#  ROOT -> TOP -> A -> B -> X ;  A -> C -> X  (X has two parents, two upward paths)
#  TOP -> D (not an ancestor of X) ;  C -> Y (so the lowest common ancestor of X and Y is C)
#  X -> K1, K2, K3 (children)
EDGES = [(ROOT_ID, TOP), (TOP, A), (A, B), (B, X), (A, C), (C, X), (TOP, D), (C, Y), (X, K1), (X, K2), (X, K3)]
SEED = 2026111903


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    path = tmp_path_factory.mktemp("db") / "snomed.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE concepts (concept_id TEXT PRIMARY KEY, term TEXT)")
    con.execute("CREATE TABLE edges (parent_id TEXT, child_id TEXT, PRIMARY KEY (parent_id, child_id))")
    names = {ROOT_ID: "SNOMED CT Concept (SNOMED RT+CTV3)", TOP: "Clinical finding (finding)"}
    names.update({c: f"Concept {c} (disorder)" for c in (A, B, C, D, X, Y, K1, K2, K3)})
    con.executemany("INSERT INTO concepts VALUES (?, ?)", names.items())
    con.executemany("INSERT INTO edges VALUES (?, ?)", EDGES)
    con.commit()
    con.close()
    return LocalTerminologyClient.from_db(path)


ITEMS = {
    "T1": Item(f"T1:{X}", "T1", X, sorted([B, C])),
    "T2": Item(f"T2:{X}", "T2", X, sorted([K1, K2, K3])),
    "T3pos": Item(f"T3:{X}:{TOP}", "T3", X, "yes", target_id=TOP, polarity="pos"),
    "T3neg": Item(f"T3:{X}:{D}", "T3", X, "no", target_id=D, polarity="neg"),
    "T4": Item(f"T4:{X}", "T4", X, None, target_id=TOP),
    "T5": Item(f"T5:{X}", "T5", X, None),
    "T6": Item(f"T6:{X}:{Y}", "T6", X, [C], target_id=Y),
}


@pytest.fixture(scope="module")
def contexts(client):
    return {name: build_s1_context(client, item) for name, item in ITEMS.items()}


def test_rounding_is_half_up_and_monotone():
    assert [n_withheld(1, f) for f in LEVELS] == [0, 0, 1, 1]
    assert [n_withheld(2, f) for f in LEVELS] == [0, 1, 1, 2]
    assert [n_withheld(4, f) for f in LEVELS] == [0, 1, 2, 3]
    assert n_withheld(0, 0.75) == 0
    with pytest.raises(ValueError):
        n_withheld(3, 1.5)


def test_relevant_edges_per_task(contexts):
    assert relevant_edges(ITEMS["T1"], contexts["T1"]) == sorted([(X, B), (X, C)])
    assert relevant_edges(ITEMS["T2"], contexts["T2"]) == sorted([(K1, X), (K2, X), (K3, X)])
    up = {(X, B), (X, C), (B, A), (C, A), (A, TOP)}
    assert set(relevant_edges(ITEMS["T3pos"], contexts["T3pos"])) == up
    assert set(relevant_edges(ITEMS["T4"], contexts["T4"])) == up
    assert set(relevant_edges(ITEMS["T3neg"], contexts["T3neg"])) == up | {(TOP, ROOT_ID)}   # the whole closure
    t5 = set(relevant_edges(ITEMS["T5"], contexts["T5"]))
    assert {(X, B), (X, C)} <= t5 and all(X in e for e in t5)
    assert set(relevant_edges(ITEMS["T6"], contexts["T6"])) == {(X, C), (Y, C)}


def test_zero_level_changes_nothing(contexts):
    for name, item in ITEMS.items():
        masked, record = mask_context(contexts[name], item, 0.0, SEED)
        assert record["n_withheld"] == 0
        assert masked.render("v2") == contexts[name].render("v2")
        assert sufficient(item, masked) == sufficient(item, contexts[name])


def test_levels_are_nested_and_seeded(contexts):
    for name, item in ITEMS.items():
        levels = mask_levels(contexts[name], item, SEED)
        withheld = [set(map(tuple, levels[f][1]["withheld_edges"])) for f in LEVELS]
        assert all(a <= b for a, b in zip(withheld, withheld[1:]))                 # 0 < 25 < 50 < 75 %
        again = mask_levels(contexts[name], item, SEED)
        assert [again[f][1] for f in LEVELS] == [levels[f][1] for f in LEVELS]   # deterministic
        for f in LEVELS:
            masked, record = levels[f]
            assert not set(map(tuple, record["withheld_edges"])) & masked.edges
            assert record["n_withheld"] == n_withheld(record["n_relevant"], f)
    assert removal_order(ITEMS["T4"], contexts["T4"], SEED) != removal_order(ITEMS["T4"], contexts["T4"], SEED + 1) \
        or len(relevant_edges(ITEMS["T4"], contexts["T4"])) < 2


def test_the_input_context_is_not_changed(contexts):
    before = contexts["T4"].render("v2")
    mask_context(contexts["T4"], ITEMS["T4"], 0.75, SEED)
    assert contexts["T4"].render("v2") == before


def test_t1_mask_marks_the_parent_list_partial(contexts):
    item, context = ITEMS["T1"], contexts["T1"]
    assert sufficient(item, context) and answer_in_evidence(item, context)
    masked, record = mask_context(context, item, 0.5, SEED)
    assert record["n_withheld"] == 1 and record["parents_marked_partial"] == [X]
    assert masked.parents_status[X] == "partial"
    assert not sufficient(item, masked) and not answer_in_evidence(item, masked)
    assert reference_answer(item, masked)["insufficient_evidence"] is True     # R0 abstains
    text = masked.render(evidence_format("marked"))
    assert "parents PARTIAL (1 listed; more may exist)" in text


def test_t2_mask_drops_isolated_children_and_marks_the_child_list(contexts):
    item, context = ITEMS["T2"], contexts["T2"]
    masked, record = mask_context(context, item, 0.75, SEED)
    assert record["n_withheld"] == 2 and record["children_marked_partial"] == [X]
    gone = {c for c, _ in record["withheld_edges"]}
    assert set(record["nodes_dropped"]) == gone and not gone & set(masked.nodes)
    assert masked.children_status[X] == "partial" and masked.children_total[X] == 1
    assert "children PARTIAL (1 listed; at least 1 retrieved, more may exist)" in masked.render("v2")
    assert not sufficient(item, masked)


def test_t3_negative_needs_the_whole_closure(contexts):
    item, context = ITEMS["T3neg"], contexts["T3neg"]
    assert sufficient(item, context)
    for f in LEVELS[1:]:
        masked, record = mask_context(context, item, f, SEED)
        assert record["n_withheld"] >= 1 and not sufficient(item, masked)


def test_unmarked_evidence_has_the_same_edges_and_no_coverage(contexts):
    masked, _ = mask_context(contexts["T4"], ITEMS["T4"], 0.5, SEED)
    marked = masked.render(evidence_format("marked"))
    unmarked = masked.render(evidence_format("unmarked"))
    assert marked.startswith(unmarked)                                            # same numbered edges
    for word in ("coverage", "COMPLETE", "PARTIAL", "NOT RETRIEVED", "budget", "depth limit"):
        assert word not in unmarked
    assert "Retrieval coverage" in marked
    with pytest.raises(ValueError):
        evidence_format("hidden")


def test_focal_and_partner_concepts_always_stay(contexts):
    for name in ("T1", "T6"):
        item = ITEMS[name]
        masked, _ = mask_context(contexts[name], item, 0.75, SEED)
        assert item.concept_id in masked.nodes
        if item.task == "T6":
            assert item.target_id in masked.nodes
