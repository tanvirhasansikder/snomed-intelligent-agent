"""Text-retrieval baselines over concept cards (offline, small fake database in the real schema)."""

import json
import sqlite3

import pytest

from evaluation.tasks import Item, sufficient
from evaluation.text_retrieval import (
    CARD_FORMAT, CardStore, card_context, dense_search, entity_queries, lexical_retrieve, link_card, merge_rankings,
    parse_card, query_concepts, retrieval_record, share_k, split_list,
)
from explanation.citations import ENTAILS, check_citation
from knowledge_graph.snomed_db_loader import ROOT_ID, load_graph

TOP, IHD, NEC, MI, AMI, OLD, UK, PROC = (
    "404684003", "414545008", "251314000", "22298006", "57054005", "1755008", "782751000000103", "71388002")
TERMS = {ROOT_ID: "SNOMED CT Concept (SNOMED RT+CTV3)", TOP: "Clinical finding (finding)",
         IHD: "Ischemic heart disease (disorder)", NEC: "Myocardial necrosis (finding)",
         MI: "Myocardial infarction (disorder)", AMI: "Acute myocardial infarction (disorder)",
         OLD: "Old myocardial infarction (disorder)", UK: "Myocardial infarction UK grouper (finding)",
         PROC: "Procedure (procedure)"}
EDGES = [(ROOT_ID, TOP), (ROOT_ID, PROC), (TOP, IHD), (TOP, NEC), (IHD, MI), (NEC, MI), (MI, AMI), (MI, OLD),
         (MI, UK)]
CARDS = {
    MI: ("Term: Myocardial infarction\nType: disorder\nSynonyms: Heart attack; Cardiac infarction\n"
         "Parent concepts: Ischemic heart disease; Myocardial necrosis\n"
         "Hierarchy: Clinical finding > Ischemic heart disease > Myocardial infarction\n"
         "Finding site: Myocardium structure\n"
         "Includes: Acute myocardial infarction; Old myocardial infarction (+1 more)"),
    AMI: ("Term: Acute myocardial infarction\nType: disorder\nParent concepts: Myocardial infarction\n"
          "Hierarchy: Clinical finding > Ischemic heart disease > Myocardial infarction > Acute myocardial infarction"),
    IHD: ("Term: Ischemic heart disease\nType: disorder\nParent concepts: Clinical finding\n"
          "Hierarchy: Clinical finding > Ischemic heart disease\nIncludes: Myocardial infarction; Not a real child"),
    UK: ("Term: Myocardial infarction UK grouper\nType: finding\nParent concepts: Myocardial infarction\n"
         "Hierarchy: Clinical finding > Ischemic heart disease > Myocardial infarction > Myocardial infarction UK grouper"),
    PROC: "Term: Procedure\nType: procedure\nParent concepts: SNOMED CT Concept\nHierarchy: Procedure",
}


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "snomed.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE concepts (concept_id TEXT PRIMARY KEY, term TEXT NOT NULL, semantic_tag TEXT, "
                "synonyms TEXT, card_text TEXT NOT NULL, embedding BLOB)")
    con.execute("CREATE TABLE edges (parent_id TEXT NOT NULL, child_id TEXT NOT NULL, PRIMARY KEY (parent_id, child_id))")
    con.execute("CREATE INDEX edges_child ON edges(child_id)")
    con.execute("CREATE VIRTUAL TABLE concepts_fts USING fts5(concept_id UNINDEXED, term, synonyms, card_text)")
    for cid, term in TERMS.items():
        card = CARDS.get(cid, f"Term: {term.split(' (')[0]}")
        con.execute("INSERT INTO concepts VALUES (?, ?, NULL, '[]', ?, NULL)", (cid, term, card))
        con.execute("INSERT INTO concepts_fts VALUES (?, ?, '[]', ?)", (cid, term, card))
    con.executemany("INSERT INTO edges VALUES (?, ?)", EDGES)
    con.commit()
    con.close()
    return path


@pytest.fixture
def store(db):
    s = CardStore(db)
    yield s
    s.close()


def test_parse_card_and_lists():
    fields = parse_card(CARDS[MI])
    assert [f for f, _ in fields] == ["Term", "Type", "Synonyms", "Parent concepts", "Hierarchy", "Finding site",
                                      "Includes"]
    assert split_list("A; B; C (+3 more)") == (["A", "B", "C"], 3)
    assert split_list("A") == (["A"], 0)
    assert dict(parse_card("Synonyms: EKG: myocardial infarction"))["Synonyms"] == "EKG: myocardial infarction"


def test_link_card_matches_names_to_the_concepts_real_neighbours(store):
    card = link_card(MI, CARDS[MI], store)
    assert card["parents"] == [("Ischemic heart disease", IHD), ("Myocardial necrosis", NEC)]
    assert card["children"] == [("Acute myocardial infarction", AMI), ("Old myocardial infarction", OLD)]
    assert card["more"] == 1
    assert card["path"] == [("Clinical finding", TOP), ("Ischemic heart disease", IHD), ("Myocardial infarction", MI)]
    assert card["edges"] == {(MI, IHD), (MI, NEC), (AMI, MI), (OLD, MI), (IHD, TOP)}
    assert card["unmatched"] == 0
    assert link_card(IHD, CARDS[IHD], store)["unmatched"] == 1            # "Not a real child" has no ID


def test_card_context_states_only_the_cards_links_and_their_completeness(store):
    item = Item(f"T1:{MI}", "T1", MI, sorted([IHD, NEC]))
    context = card_context(item, [MI, AMI], store, "L-RAG")
    assert (MI, IHD) in context.edges and (AMI, MI) in context.edges
    assert context.parents_status[MI] == "complete"
    assert context.children_status[MI] == "partial"                       # "(+1 more)"
    assert context.children_total[MI] == 3
    assert sufficient(item, context)
    assert not sufficient(Item(f"T2:{MI}", "T2", MI, sorted([AMI, OLD, UK])), context)


def test_render_shows_ids_and_edge_ids_that_citations_check(store):
    item = Item(f"T1:{MI}", "T1", MI, sorted([IHD, NEC]))
    context = card_context(item, [MI], store, "L-RAG")
    text = context.render(CARD_FORMAT)
    index = context.edge_index()
    eid = {edge: e for e, edge in index.items()}
    assert f"Card 1: Myocardial infarction [{MI}]" in text
    assert f"Ischemic heart disease [{IHD}] ({eid[(MI, IHD)]})" in text
    assert f"Acute myocardial infarction [{AMI}] ({eid[(AMI, MI)]}); " in text and "(+1 more)" in text
    assert f"Clinical finding [{TOP}] > Ischemic heart disease [{IHD}] ({eid[(IHD, TOP)]}) > " in text
    assert "Finding site: Myocardium structure" in text and "coverage" not in text.lower()
    claim = {"subject": MI, "relation": "direct_parent", "object": IHD, "source": "evidence",
             "cites": [eid[(MI, IHD)]]}
    assert check_citation(claim, index) == ENTAILS
    assert card_context(item, [], store, "L-RAG").render(CARD_FORMAT) == "No evidence was retrieved."


def test_queries_are_the_concept_names():
    label = lambda c: TERMS[c].split(" (")[0]
    t4 = Item(f"T4:{MI}", "T4", MI, None, target_id=TOP)
    assert query_concepts(t4) == [MI, TOP]
    assert entity_queries(t4, label) == ["Myocardial infarction", "Clinical finding"]
    assert entity_queries(Item(f"T1:{MI}", "T1", MI, [IHD]), label) == ["Myocardial infarction"]


def test_k_is_shared_and_rankings_interleave():
    assert share_k(10, 1) == [10] and share_k(10, 2) == [5, 5] and share_k(5, 2) == [3, 2]
    assert merge_rankings([["a", "b", "c"], ["x", "a", "y"]], [2, 2], 4) == ["a", "x", "b", "y"]
    assert merge_rankings([["a"], ["x", "y", "z"]], [2, 2], 4) == ["a", "x", "y", "z"]      # short query: others fill
    assert merge_rankings([[], []], [5, 5], 10) == []


def test_lexical_search_ranks_the_named_concept_first_and_keeps_to_the_view(db, store):
    keep = set(load_graph(db, "international").names)
    item = Item(f"T1:{MI}", "T1", MI, sorted([IHD, NEC]))
    ids, queries = lexical_retrieve(item, store, store.label, k=3, keep=keep)
    assert UK not in ids and len(ids) == 3
    everything, _ = lexical_retrieve(item, store, store.label, k=20)
    assert UK in everything
    assert MI in ids and queries == ["Myocardial infarction"]            # ranks on 5 cards mean little
    record = retrieval_record(item, "L-RAG", ids, queries, card_context(item, ids, store, "L-RAG"))
    assert record["focal_card_rank"] == ids.index(MI) + 1 and record["cards"] == ids
    assert json.dumps(record)                                             # serialisable for the results row


def test_dense_search_orders_by_cosine_and_breaks_ties_by_id():
    np = pytest.importorskip("numpy")
    ids = ["c", "a", "b", "d"]
    matrix = np.array([[1, 0], [0.6, 0.8], [0.6, 0.8], [0, 1]], dtype=np.float32)
    assert dense_search([1, 0], matrix, ids, k=3) == ["c", "a", "b"]
    assert dense_search([0, 1], matrix, ids, k=2) == ["d", "a"]
    assert dense_search([0, 1], matrix, ids, k=10) == ["d", "a", "b", "c"]


def test_card_evidence_stays_inside_the_evaluation_view(store, db):
    from evaluation.tasks import reference_answer
    from evaluation.text_retrieval import restrict_to_view
    view = load_graph(db, "international")
    text = CARDS[MI].replace("Old myocardial infarction (+1 more)",
                             "Old myocardial infarction; Myocardial infarction UK grouper")
    full = link_card(MI, text, store)
    assert (UK, MI) in full["edges"]                                   # the whole database knows the UK child
    inside, outside = restrict_to_view(full, MI, view)
    assert outside == 1 and inside["outside_view"] == 1
    assert ("Myocardial infarction UK grouper", None) in inside["children"]   # name kept, no ID
    assert (UK, MI) not in inside["edges"] and (AMI, MI) in inside["edges"]

    store.card = lambda cid: text if cid == MI else CARDS.get(cid)
    item = Item(f"T2:{MI}", "T2", MI, sorted([AMI, OLD]))
    context = card_context(item, [MI], store, "L-RAG", view=view)
    assert UK not in context.nodes
    assert context.children_status[MI] == "complete"                   # complete against the International view
    rendered = context.render(CARD_FORMAT)
    assert "Myocardial infarction UK grouper" in rendered and UK not in rendered
    assert set(reference_answer(item, context)["answer"]) == {AMI, OLD}
