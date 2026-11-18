"""Loader and checks for the local SNOMED CT database (offline, small fake database)."""

import json
import sqlite3

import pytest

from knowledge_graph.snomed_db_loader import (
    ROOT_ID, build_manifest, check_graph, is_international, load_graph, main, namespace,
)

# International (partition 00) IDs with valid-looking shapes; UK extension IDs end in ...1000000 1 0 x
TOP_A, TOP_B = "404684003", "71388002"
A1, A2, A3, DEEP = "22298006", "57054005", "414545008", "1388458000"
UK, UK_ORPHAN_PARENT, UK_CHILD = "782751000000103", "199331000000106", "1025491000000103"


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "snomed.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE concepts (concept_id TEXT PRIMARY KEY, term TEXT, semantic_tag TEXT, synonyms TEXT, "
                "card_text TEXT, embedding BLOB)")
    con.execute("CREATE TABLE edges (parent_id TEXT, child_id TEXT, PRIMARY KEY (parent_id, child_id))")
    terms = {ROOT_ID: "SNOMED CT Concept (SNOMED RT+CTV3)", TOP_A: "Clinical finding (finding)",
             TOP_B: "Procedure (procedure)", A1: "Myocardial infarction (disorder)",
             A2: "Acute myocardial infarction (disorder)", A3: "Ischemic heart disease (disorder)",
             DEEP: "Immersion pulmonary edema (disorder)", UK: "UK finding (finding)",
             UK_ORPHAN_PARENT: "UK grouper (finding)", UK_CHILD: "UK child (finding)"}
    con.executemany("INSERT INTO concepts (concept_id, term) VALUES (?, ?)", terms.items())
    # root -> TOP_A -> A3 -> A1 -> A2 ; TOP_A -> A2 (a shortcut: two parents, shortest 2, longest 4)
    # root -> TOP_B (leaf) ; TOP_A -> DEEP ; A1 -> UK (extension child) ; orphan UK subtree (no parent)
    con.executemany("INSERT INTO edges VALUES (?, ?)", [
        (ROOT_ID, TOP_A), (ROOT_ID, TOP_B), (TOP_A, A3), (A3, A1), (A1, A2), (TOP_A, A2), (TOP_A, DEEP),
        (A1, UK), (UK_ORPHAN_PARENT, UK_CHILD)])
    con.commit()
    con.close()
    return path


def test_namespace_and_international_ids():
    assert namespace(A1) == "international" and is_international(DEEP)
    assert namespace(UK) == "1000000" and not is_international(UK)
    assert namespace("442711000124106") == "1000124"


def test_international_view_drops_extensions_and_keeps_one_root(db):
    graph = load_graph(db, "international")
    assert UK not in graph and UK_CHILD not in graph and A1 in graph
    assert graph.roots() == [ROOT_ID]
    assert graph.children_of(A1) == [A2]
    assert graph.parents_of(A2) == sorted([A1, TOP_A])
    assert graph.ancestors(A2) == {A1, A3, TOP_A, ROOT_ID}
    assert graph.top_level_concepts() == sorted([TOP_A, TOP_B])
    assert graph.name(DEEP) == "Immersion pulmonary edema (disorder)"


def test_full_view_shows_the_extension_and_its_orphan_root(db):
    graph = load_graph(db, "full")
    assert UK in graph and graph.children_of(A1) == sorted([A2, UK])
    assert graph.roots() == sorted([ROOT_ID, UK_ORPHAN_PARENT])


def test_depths_take_the_shortest_and_longest_path(db):
    depths = load_graph(db, "international").depths()
    assert depths[ROOT_ID] == (0, 0)
    assert depths[A2] == (2, 4)       # root-TOP_A-A2 and root-TOP_A-A3-A1-A2
    assert depths[DEEP] == (2, 2)


def test_checks_report_structure_without_terms(db):
    report = check_graph(load_graph(db, "full"))
    assert report["root_is_unique"] is False and report["cycle_nodes"] == 0
    assert report["not_reachable_from_root"] == 2          # the orphan UK subtree
    assert report["concepts_with_several_parents"] == 1    # A2
    assert report["descendants_per_top_level_concept"][TOP_A] == 5   # A3, A1, A2, DEEP, UK
    assert report["namespaces"] == {"international": 7, "1000000": 3}
    text = json.dumps(report)
    assert "infarction" not in text and "Immersion" not in text   # IDs and counts only


def test_cycle_is_detected(tmp_path, db):
    con = sqlite3.connect(db)
    con.execute("INSERT INTO edges VALUES (?, ?)", (A2, A3))   # A3 -> A1 -> A2 -> A3
    con.commit()
    con.close()
    report = check_graph(load_graph(db, "international"))
    assert report["cycle_nodes"] >= 3 and set(report["cycle_examples"]) >= {A1, A2, A3}


def test_manifest_and_cli(tmp_path, db, capsys):
    out = tmp_path / "manifest.json"
    assert main(["--db", str(db), "--out", str(out)]) == 0
    manifest = json.loads(out.read_text(encoding="utf-8"))
    assert len(manifest["sha256"]) == 64
    assert set(manifest["views"]) == {"international", "international-filtered", "full"}
    assert manifest["views"]["international"]["root_is_unique"] is True
    assert "2026-02-01" in manifest["release"]
    assert "unique root: True" in capsys.readouterr().out
    assert build_manifest(db, views=("international",), hash_file=False)["sha256"] is None


def test_errors(tmp_path, db):
    with pytest.raises(ValueError):
        load_graph(db, "everything")
    with pytest.raises(FileNotFoundError):
        load_graph(tmp_path / "missing.db")


def _bridged_db(tmp_path):
    """ROOT -> TOP_A -> A3 -> (UK X) -> A1, plus TOP_A -> (UK Y) -> A1, and an International leaf under an orphan."""
    path = tmp_path / "bridged.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE concepts (concept_id TEXT PRIMARY KEY, term TEXT)")
    con.execute("CREATE TABLE edges (parent_id TEXT, child_id TEXT, PRIMARY KEY (parent_id, child_id))")
    uk_x, uk_y, orphan_int = "1240631000000102", "1373761000000103", "102449007"
    terms = {ROOT_ID: "root", TOP_A: "top", A3: "a3", A1: "a1", A2: "a2", uk_x: "uk x", uk_y: "uk y",
             UK_ORPHAN_PARENT: "uk orphan", orphan_int: "orphan international"}
    con.executemany("INSERT INTO concepts VALUES (?, ?)", terms.items())
    con.executemany("INSERT INTO edges VALUES (?, ?)", [
        (ROOT_ID, TOP_A), (TOP_A, A3), (A3, uk_x), (uk_x, A1), (TOP_A, uk_y), (uk_y, A1), (A1, A2),
        (UK_ORPHAN_PARENT, orphan_int)])
    con.commit()
    con.close()
    return path, orphan_int


def test_projection_bridges_over_extension_concepts(tmp_path):
    path, orphan_int = _bridged_db(tmp_path)
    filtered = load_graph(path, "international-filtered")
    assert filtered.parents_of(A1) == []                      # dropping the UK concepts orphans A1
    projected = load_graph(path, "international")
    assert projected.parents_of(A1) == [A3]                   # TOP_A is dropped: it is an ancestor of A3
    assert projected.ancestors(A2) == {A1, A3, TOP_A, ROOT_ID}
    assert projected.projection["children_bridged_over_extensions"] == 1
    assert projected.projection["redundant_parents_removed"] == 1
    report = check_graph(projected)
    assert report["roots"] == [ROOT_ID]
    assert report["not_reachable_from_root"] == 1 and report["not_reachable_examples"] == [orphan_int]


def test_full_view_has_no_projection_stats(db):
    assert load_graph(db, "full").projection == {}
