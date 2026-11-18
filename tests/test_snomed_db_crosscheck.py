"""Cross-check of the frozen BioPortal reference against a local snomed.db (offline, fake data)."""

import sqlite3

from evaluation.bioportal_cache import CallStore
from evaluation.gold import GoldIndex
from evaluation.snomed_db_crosscheck import SnomedDB, crosscheck, namespace

# BioPortal (frozen):  TOP <- A <- S1 ;  TOP <- B <- S2 ;  A's children complete = [S1, X]
TOP, A, B, S1, S2, X, US = "138875005", "1000001", "2000002", "3000003", "4000004", "5000005", "442711000124106"


def _store(tmp_path):
    store = CallStore(tmp_path / "calls.jsonl")
    concept = lambda cid, name: {"concept_id": cid, "name": name}  # noqa: E731
    store.put("get_concept", S1, {"concept_id": S1, "name": "Sample one"})
    store.put("get_concept", S2, {"concept_id": S2, "name": "Sample two"})
    store.put("get_parents", S1, [concept(A, "Alpha")])
    store.put("get_parents", S2, [concept(B, "Beta"), concept(US, "US thing")])
    store.put("get_parents", A, [concept(TOP, "Top")])
    store.put("get_parents", B, [concept(TOP, "Top")])
    store.put("get_parents", US, [concept(TOP, "Top")])
    store.put("get_parents", TOP, [])
    store.put("fetch_children", A, {"children": [concept(S1, "Sample one"), concept(X, "Ex")], "complete": True,
                                    "pages_fetched": 1})
    dataset = {"samples": [{"concept_id": S1, "hierarchy_id": TOP}, {"concept_id": S2, "hierarchy_id": TOP}]}
    return GoldIndex(store, dataset)


def _db(tmp_path):
    path = tmp_path / "snomed.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE concepts (concept_id TEXT PRIMARY KEY, term TEXT, semantic_tag TEXT, synonyms TEXT, "
                "card_text TEXT, embedding BLOB)")
    con.execute("CREATE TABLE edges (parent_id TEXT, child_id TEXT, PRIMARY KEY (parent_id, child_id))")
    terms = {TOP: "Top (root)", A: "Alpha (finding)", B: "Beta (finding)", S1: "Sample one (finding)",
             S2: "Sample 2 (finding)", X: "Ex (finding)"}            # US concept absent: International edition
    con.executemany("INSERT INTO concepts (concept_id, term) VALUES (?, ?)", terms.items())
    # differences: S2 lacks the US parent; A has an extra child Y in the database
    con.executemany("INSERT INTO edges VALUES (?, ?)",
                    [(TOP, A), (TOP, B), (A, S1), (B, S2), (A, X), (A, "6000006")])
    con.commit()
    con.close()
    return SnomedDB(path)


def test_crosscheck_counts_agreement_and_differences(tmp_path):
    result = crosscheck(_store(tmp_path), _db(tmp_path))

    assert result["database"] == {"concepts": 6, "edges": 6, "roots": 1}
    assert result["coverage"]["absent_from_db"] == 1 and result["coverage"]["absent_examples"] == [US]
    assert result["coverage"]["main_samples_absent"] == []

    parents = result["direct_parents"]
    assert parents["compared"] == 5 and parents["identical"] == 4 and parents["missing_in_db"] == 1
    assert parents["examples"]["missing_in_db"][0] == {"concept": S2, "only_bioportal": [US], "only_db": []}

    children = result["direct_children_complete_lists"]
    assert children["compared"] == 1 and children["extra_in_db"] == 1
    assert children["examples"]["extra_in_db"][0]["only_db"] == ["6000006"]

    ancestors = result["sample_ancestors"]
    assert ancestors["compared"] == 2 and ancestors["identical"] == 1 and ancestors["missing_in_db"] == 1

    labels = result["labels"]
    assert labels["same_after_removing_tag"] < labels["compared"] and S2 in labels["different_examples"]

    assert result["us_extension"] == {"in_bioportal": 1, "of_these_in_db": 0, "examples": [US]}


def test_namespace_reads_extension_ids_only():
    assert namespace("442711000124106") == "1000124"
    assert namespace("22298006") is None
    assert namespace("138875005") is None
