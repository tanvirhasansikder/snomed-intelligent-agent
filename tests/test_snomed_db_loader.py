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
    assert len(manifest["sha256"]) == 64 and set(manifest["views"]) == {"international", "full"}
    assert manifest["views"]["international"]["root_is_unique"] is True
    assert "2026-02-01" in manifest["release"]
    assert "unique root: True" in capsys.readouterr().out
    assert build_manifest(db, views=("international",), hash_file=False)["sha256"] is None


def test_errors(tmp_path, db):
    with pytest.raises(ValueError):
        load_graph(db, "everything")
    with pytest.raises(FileNotFoundError):
        load_graph(tmp_path / "missing.db")
