"""The local terminology client matches the BioPortalClient interface (offline, small fake database)."""

import sqlite3

import pytest

from crawler.local_client import ConceptNotFound, LocalTerminologyClient, split_fsn
from knowledge_graph.snomed_db_loader import ROOT_ID
from query_engine.retrieval_planner import BudgetedClient, RequestBudgetExhausted

TOP, IHD, MI, AMI, OLD_MI, UK = "404684003", "414545008", "22298006", "57054005", "1755008", "782751000000103"


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "snomed.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE concepts (concept_id TEXT PRIMARY KEY, term TEXT)")
    con.execute("CREATE TABLE edges (parent_id TEXT, child_id TEXT, PRIMARY KEY (parent_id, child_id))")
    terms = {ROOT_ID: "SNOMED CT Concept (SNOMED RT+CTV3)", TOP: "Clinical finding (finding)",
             IHD: "Ischemic heart disease (disorder)", MI: "Myocardial infarction (disorder)",
             AMI: "Acute myocardial infarction (disorder)", OLD_MI: "Old myocardial infarction (disorder)",
             UK: "Myocardial infarction UK grouper (finding)"}
    con.executemany("INSERT INTO concepts VALUES (?, ?)", terms.items())
    con.executemany("INSERT INTO edges VALUES (?, ?)", [
        (ROOT_ID, TOP), (TOP, IHD), (IHD, MI), (MI, AMI), (MI, OLD_MI), (MI, UK)])
    con.execute("CREATE VIRTUAL TABLE names_fts USING fts5(name, concept_id)")
    con.executemany("INSERT INTO names_fts VALUES (?, ?)", [
        ("Myocardial infarction", MI), ("Heart attack", MI), ("Acute myocardial infarction", AMI),
        ("Old myocardial infarction", OLD_MI), ("Myocardial infarction UK grouper", UK)])
    con.commit()
    con.close()
    return path


@pytest.fixture
def client(db):
    return LocalTerminologyClient.from_db(db)


def test_split_fsn():
    assert split_fsn("Myocardial infarction (disorder)") == ("Myocardial infarction", "disorder")
    assert split_fsn("No tag here") == ("No tag here", None)
    assert split_fsn("Tumor (morphologic abnormality) of skin (disorder)") == (
        "Tumor (morphologic abnormality) of skin", "disorder")


def test_get_concept_shape(client):
    concept = client.get_concept(MI)
    assert concept["prefLabel"] == "Myocardial infarction" and concept["semantic_tag"] == "disorder"
    assert concept["fsn"] == "Myocardial infarction (disorder)"
    assert client.get_concept("999999999") is None
    with pytest.raises(ConceptNotFound):
        client.get_concept("999999999", raise_errors=True)


def test_parents_children_ancestors(client):
    assert client.get_parents(MI) == [{"concept_id": IHD, "name": "Ischemic heart disease"}]
    result = client.fetch_children(MI)
    assert [c["concept_id"] for c in result["children"]] == sorted([AMI, OLD_MI])   # UK child not in this view
    assert result["complete"] is True and result["pages_fetched"] == 1
    assert {a["concept_id"] for a in client.get_ancestors(AMI)} == {MI, IHD, TOP, ROOT_ID}
    assert client.get_parents(ROOT_ID) == []


def test_child_page_cap_marks_incomplete(client):
    capped = client.fetch_children(MI, max_pages=1, pagesize=1)
    assert len(capped["children"]) == 1 and capped["complete"] is False


def test_unknown_concepts(client):
    assert client.get_parents("999999999") == [] and client.get_children("999999999") == []
    with pytest.raises(ConceptNotFound):
        client.fetch_children("999999999")


def test_full_view_includes_extensions(db):
    full = LocalTerminologyClient.from_db(db, view="full")
    assert UK in {c["concept_id"] for c in full.fetch_children(MI)["children"]}


def test_search_uses_fts_and_stays_in_view(client):
    hits = [h["concept_id"] for h in client.search_concept("myocardial infarction")]
    assert MI in hits and AMI in hits and UK not in hits
    assert [h["concept_id"] for h in client.search_concept("heart attack")] == [MI]   # synonym via FTS
    assert client.search_concept("") == []


def test_search_falls_back_to_a_scan_without_the_database(db):
    from knowledge_graph.snomed_db_loader import load_graph
    offline = LocalTerminologyClient(load_graph(db))
    hits = [h["concept_id"] for h in offline.search_concept("myocardial infarction")]
    assert hits[0] == MI and set(hits) == {MI, AMI, OLD_MI}


def test_request_count_and_budget_wrapper(client):
    before = client.request_count
    client.get_parents(MI)
    client.fetch_children(MI)
    assert client.request_count == before + 2
    budgeted = BudgetedClient(client, budget=1)
    budgeted.get_parents(MI)
    with pytest.raises(RequestBudgetExhausted):
        budgeted.fetch_children(MI)
    assert budgeted.refused_down == {MI}


def test_version(client):
    version = client.get_ontology_version()
    assert version["released"] == "2026-02-01" and "international view" in version["version"]


# ----------------------------------------------------------------------
# Study 1's retrieval code runs unchanged on the local client
# ----------------------------------------------------------------------

from evaluation.tasks import Item  # noqa: E402
from query_engine.retrieval_planner import build_s1_context  # noqa: E402


def test_s1_planner_runs_on_the_local_client(client):
    t4 = build_s1_context(client, Item("T4:AMI", "T4", AMI, None))          # closure up to the root
    assert {MI, IHD, TOP} <= set(t4.nodes)
    assert (AMI, MI) in t4.edges and (MI, IHD) in t4.edges
    t2 = build_s1_context(client, Item("T2:MI", "T2", MI, None))           # children of the concept
    assert {AMI, OLD_MI} <= set(t2.nodes) and t2.children_status.get(MI) == "complete"
    assert t4.requests_used > 0 and t4.budget_stopped == set()
