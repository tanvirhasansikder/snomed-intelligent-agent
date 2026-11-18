"""Study 2 collection: stratified sampling on the local graph, through Study 1's unchanged pipeline (offline)."""

import json
import random
import sqlite3
from collections import Counter

import pytest

from crawler.local_client import LocalTerminologyClient
from evaluation.bioportal_cache import CallStore
from evaluation.collect import collect, sample_concepts_stratified
from evaluation.gold import GoldIndex
from evaluation.tasks import generate_items
from knowledge_graph.snomed_db_loader import ROOT_ID, load_graph

TOPS = {"404684003": "Clinical finding", "71388002": "Procedure"}


def _make_db(path, seed=7):
    """Two hierarchies, each a random tree 9 levels deep with some multi-parent concepts."""
    rng = random.Random(seed)
    names, edges, counter = {ROOT_ID: "SNOMED CT Concept (root)"}, [], [100000]

    def new_id():
        counter[0] += 1
        return f"{counter[0]}001"            # partition 00: an International-style identifier

    for top, name in TOPS.items():
        names[top] = f"{name} (finding)"
        edges.append((ROOT_ID, top))
        level = [top]
        for depth in range(1, 10):
            nxt = []
            for parent in level:
                for _ in range(rng.choice([0, 1, 2, 3]) if depth > 1 else 3):
                    cid = new_id()
                    names[cid] = f"Concept {cid} (finding)"
                    edges.append((parent, cid))
                    nxt.append(cid)
            if len(nxt) > 3:                 # a few concepts with a second parent at the same level
                edges.append((rng.choice(level), nxt[-1]))
            level = nxt or level
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE concepts (concept_id TEXT PRIMARY KEY, term TEXT)")
    con.execute("CREATE TABLE edges (parent_id TEXT, child_id TEXT, PRIMARY KEY (parent_id, child_id))")
    con.executemany("INSERT INTO concepts VALUES (?, ?)", names.items())
    con.executemany("INSERT OR IGNORE INTO edges VALUES (?, ?)", edges)
    con.commit()
    con.close()
    return path


@pytest.fixture
def db(tmp_path):
    return _make_db(tmp_path / "snomed.db")


def _config(tmp_path, **data):
    base = {"sampler": "stratified", "view": "international", "per_stratum": 2, "seed": 11,
            "hierarchies": TOPS, "depth_bins": [["shallow", 1, 3], ["middle", 4, 6], ["deep", 7, 99]]}
    base.update(data)
    return {"name": "v3_test", "cache_path": str(tmp_path / "calls.jsonl"), "data": base}


def test_sampler_fills_every_stratum_and_gives_valid_paths(db):
    graph = load_graph(db)
    samples = sample_concepts_stratified(graph, TOPS, per_stratum=2, seed=11)
    strata = Counter((s["hierarchy_id"], s["depth_bin"], s["is_leaf"]) for s in samples)
    assert len(strata) == 12 and all(n == 2 for n in strata.values())      # 2 hierarchies x 3 bins x leaf/non-leaf
    for s in samples:
        path = s["descent_path"]
        assert path[0] == s["hierarchy_id"] and path[-1] == s["concept_id"]
        assert all(path[i] in graph.parents_of(path[i + 1]) for i in range(len(path) - 1))
        assert len(path) - 1 == s["depth_below_top"]                       # a shortest path
        assert s["is_leaf"] == (not graph.children_of(s["concept_id"]))
    assert len({s["concept_id"] for s in samples}) == len(samples)


def test_sampler_is_deterministic_and_respects_exclusions(db):
    graph = load_graph(db)
    first = sample_concepts_stratified(graph, TOPS, 2, seed=11)
    again = sample_concepts_stratified(graph, TOPS, 2, seed=11)
    assert first == again
    excluded = {s["concept_id"] for s in first[:5]}
    other = sample_concepts_stratified(graph, TOPS, 2, seed=11, exclude=excluded)
    assert not excluded & {s["concept_id"] for s in other}
    capped = sample_concepts_stratified(graph, TOPS, 1, seed=11, max_per_hierarchy=2)
    assert Counter(s["hierarchy_id"] for s in capped) == {h: 2 for h in TOPS}


def test_collect_records_a_snapshot_that_builds_gold_and_items(tmp_path, db):
    config = _config(tmp_path)
    store = CallStore(config["cache_path"])
    client = LocalTerminologyClient.from_db(db)
    dataset = collect(config, client, store)
    assert dataset["source"] == "local snomed.db (international view)"
    assert len(dataset["samples"]) == 24 and dataset["failures"] == {}
    assert all(s["ancestors_consistent"] for s in dataset["samples"])

    gold = GoldIndex(CallStore(config["cache_path"]), json.loads(json.dumps(dataset)))   # reloaded from disk
    graph = load_graph(db)
    sample = dataset["samples"][0]["concept_id"]
    assert gold.ancestors(sample) == graph.ancestors(sample)
    items = generate_items(gold, seed=0)
    tasks = Counter(i.task for i in items)
    assert {"T1", "T2", "T3", "T4", "T6"} <= set(tasks)
    for item in items:
        if item.task == "T2":                     # the T2 subject is the sample's parent on its path
            assert item.concept_id == next(s for s in dataset["samples"]
                                           if s["concept_id"] == item.meta["sampled_concept"])["descent_path"][-2]
            assert item.gold == graph.children_of(item.concept_id)


def test_stratified_sampler_needs_the_local_client(tmp_path):
    from evaluation.synthetic import SyntheticOntology
    config = _config(tmp_path)
    live = SyntheticOntology(seed=0)
    config["data"]["hierarchies"] = live.top_level_names()
    with pytest.raises(ValueError, match="--local"):
        collect(config, live, CallStore(config["cache_path"]))
