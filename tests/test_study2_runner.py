"""Study 2 runner end to end on a fake database with the SYNTHETIC mock model (no API calls)."""

import json
import sqlite3
from collections import Counter

import numpy as np
import pytest

from crawler.local_client import LocalTerminologyClient
from evaluation.bioportal_cache import CallStore
from evaluation.collect import collect
from evaluation.study2_runner import Study2Experiment, parse_condition, select_balanced
from llm.client import ResponseCache
from tests.test_stratified_sampling import _config, _make_db

CONDITIONS = ["B0", "G-UP", "L-RAG", "D-RAG", "G-QA", "G-QA:unmarked", "G-QA@50", "G-QA@50:unmarked",
              "G-QA@50:random", "G-QA+V", "G-QA+V@50", "G-QA+V@50:random"]
MOCK = {"name": "mock", "provider": "mock", "model": "mock-1"}


def _add_cards_and_vectors(db, dense_dir):
    """Give the fake database cards, a card index and unit vectors; export the matrix."""
    from evaluation import dense_retrieval as dr
    from knowledge_graph.snomed_db_loader import load_graph
    con = sqlite3.connect(db)
    terms = dict(con.execute("SELECT concept_id, term FROM concepts"))
    parents, children = {}, {}
    for p, c in con.execute("SELECT parent_id, child_id FROM edges"):
        parents.setdefault(c, []).append(p)
        children.setdefault(p, []).append(c)
    con.execute("DROP TABLE concepts")
    con.execute("CREATE TABLE concepts (concept_id TEXT PRIMARY KEY, term TEXT NOT NULL, semantic_tag TEXT, "
                "synonyms TEXT, card_text TEXT NOT NULL, embedding BLOB)")
    con.execute("CREATE VIRTUAL TABLE concepts_fts USING fts5(concept_id UNINDEXED, term, synonyms, card_text)")
    label = lambda c: terms[c].split(" (")[0]
    rng = np.random.default_rng(0)
    for cid, term in terms.items():
        card = f"Term: {label(cid)}\nType: finding\nParent concepts: " + "; ".join(label(p) for p in sorted(parents.get(cid, [])))
        path = [cid]
        while parents.get(path[-1]):
            path.append(sorted(parents[path[-1]])[0])
        card += "\nHierarchy: " + " > ".join(label(x) for x in reversed(path[:-1]))
        if children.get(cid):
            card += "\nIncludes: " + "; ".join(label(c) for c in sorted(children[cid]))
        v = rng.normal(size=dr.DIM).astype("<f4")
        v /= np.linalg.norm(v)
        con.execute("INSERT INTO concepts VALUES (?, ?, 'finding', '[]', ?, ?)", (cid, term, card, v.tobytes()))
        con.execute("INSERT INTO concepts_fts VALUES (?, ?, '[]', ?)", (cid, term, card))
    con.commit()
    con.close()
    dr.export_matrix(db, load_graph(db).names, dense_dir)


@pytest.fixture(scope="module")
def setup(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("study2")
    db = _make_db(tmp / "snomed.db")
    collect_config = _config(tmp, per_stratum=1)
    dataset = collect(collect_config, LocalTerminologyClient.from_db(db), CallStore(collect_config["cache_path"]))
    (tmp / "ds.json").write_text(json.dumps(dataset), encoding="utf-8")
    dense = tmp / "dense"
    _add_cards_and_vectors(db, dense)
    client = LocalTerminologyClient.from_db(db)
    config = {
        "name": "v3_test", "cache_path": collect_config["cache_path"], "dataset_path": str(tmp / "ds.json"),
        "results_dir": str(tmp / "results"), "llm_cache_path": str(tmp / "llm_cache.jsonl"),
        "retrieval_source": {"local_db": str(db), "view": "international", "dense_dir": str(dense),
                             "query_vectors": "query_vectors_ds.npz"},
        "experiment": {"study": 2, "conditions": CONDITIONS, "tasks": ["T1", "T2", "T3", "T4", "T5", "T6"],
                       "request_budget": 1000, "item_seed": 0, "mask_seed": 7, "text_k": 6},
        "models": [MOCK],
    }
    # query vectors for every name the items need (random here; real ones come from BGE-M3)
    from evaluation import dense_retrieval as dr
    from evaluation.text_retrieval import entity_queries
    experiment = Study2Experiment(config, local_client=client)
    names = sorted({q for item in experiment.items for q in entity_queries(item, client.label)})
    rng = np.random.default_rng(1)
    vectors = rng.normal(size=(len(names), dr.DIM)).astype(np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    dr.save_query_vectors(dense / "query_vectors_ds.npz", names, vectors)
    return config, client


def _rows(config):
    path = config["results_dir"] + "/system_responses.jsonl"
    return [json.loads(line) for line in open(path, encoding="utf-8")]


def test_parse_condition():
    c = parse_condition("G-QA+V@50:random:unmarked")
    assert (c.base, c.fraction, c.pool, c.variant, c.agent_system) == ("G-QA+V", 0.5, "random", "unmarked", "S2")
    assert parse_condition("L-RAG").render_format == "cards"
    assert parse_condition("G-QA@50:unmarked").render_format == "v2-nocov"
    assert parse_condition("G-QA@50").evidence_key == parse_condition("G-QA+V@50:unmarked").evidence_key
    for bad in ("L-RAG@50", "B0:unmarked", "G-QA:random", "X-RAG", "G-QA@500"):
        with pytest.raises(ValueError):
            parse_condition(bad)


def test_balanced_subset_is_seeded_and_balanced(setup):
    config, client = setup
    items = Study2Experiment(config, local_client=client).items
    subset = select_balanced(items, per_task=4, seed=3)
    counts = Counter(i.task for i in subset)
    assert all(n == min(4, sum(1 for i in items if i.task == t)) for t, n in counts.items())
    t3 = Counter(i.polarity for i in subset if i.task == "T3")
    assert abs(t3["pos"] - t3["neg"]) <= 1
    assert [i.item_id for i in select_balanced(items, 4, 3)] == [i.item_id for i in subset]


def test_full_run_with_the_mock_model(setup):
    config, client = setup
    experiment = Study2Experiment(config, local_client=client)
    n_reference = experiment.run_reference()
    assert n_reference > 0
    made = experiment.run_systems([MOCK], ResponseCache(config["llm_cache_path"]))
    rows = _rows(config)
    by = Counter(r["system"] for r in rows)
    assert {"R0|G-QA", "R0|G-QA@50", "R0|G-QA@50:random", "R0|L-RAG", "R0|D-RAG", "R0|G-UP"} <= set(by)
    assert all(c in by for c in CONDITIONS)
    assert made > 0 and all(r["error"] is None for r in rows)

    # the random control is skipped where it cannot be matched (T1 and T2 evidence holds only answer edges)
    ctrl = [r for r in rows if r["system"] == "G-QA@50:random"]
    assert ctrl and {r["task"] for r in ctrl} <= {"T3", "T4", "T5", "T6"}
    assert all(r["mask_n_short"] == 0 and r["mask_n_withheld"] > 0 for r in ctrl)
    assert set(experiment.skipped["G-QA@50:random"]) >= {i.item_id for i in experiment.items if i.task in ("T1", "T2")}

    # masked rows record the mask; unmarked shares the marked evidence
    masked = {r["item_id"]: r for r in rows if r["system"] == "G-QA@50"}
    unmarked = {r["item_id"]: r for r in rows if r["system"] == "G-QA@50:unmarked"}
    for item_id, r in masked.items():
        assert r["mask_n_withheld"] >= 1 and r["evidence_format"] == "v2"
        assert unmarked[item_id]["evidence_format"] == "v2-nocov"
        assert unmarked[item_id]["context_edges"] == r["context_edges"]
        assert unmarked[item_id]["evidence_chars"] < r["evidence_chars"]

    # text systems record their retrieval; G-QA+V's first call is G-QA's cached call
    assert all(r["text_cards"] and r["evidence_format"] == "cards" for r in rows if r["system"] in ("L-RAG", "D-RAG"))
    for r in rows:
        if r["system"].startswith("G-QA+V") and r["model_name"] == "mock":
            assert r["n_new_calls"] <= 1                    # only a possible regeneration is new
    assert all(r["protocol_version"] == "3.0" for r in rows)

    # a second run collects nothing new
    again = Study2Experiment(config, local_client=client)
    assert again.run_reference() == 0
    assert again.run_systems([MOCK], ResponseCache(config["llm_cache_path"])) == 0


def test_dry_run_estimate_counts_conditions(setup):
    config, client = setup
    fresh = dict(config, results_dir=config["results_dir"] + "_estimate")
    experiment = Study2Experiment(fresh, local_client=client)
    summary = experiment.estimate_systems([MOCK])["mock"]
    assert summary["calls"] == sum(summary["by_condition"].values())
    assert summary["by_condition"]["B0"] == len(experiment.items)
    assert summary["by_condition"]["G-QA@50:random"] < len(experiment.items)
