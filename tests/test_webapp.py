"""The web API on the small fake database (no model calls: the offline reference answerer)."""

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from tests.test_text_retrieval import AMI, IHD, MI, NEC, OLD, PROC, TOP, db  # noqa: E402,F401  (fixture)
from webapp import server  # noqa: E402


@pytest.fixture
def client(db, monkeypatch, tmp_path):
    monkeypatch.setattr(server, "DB", db)
    monkeypatch.setattr(server, "DENSE", tmp_path / "no-dense")
    monkeypatch.setattr(server, "RESULTS", tmp_path / "results")
    monkeypatch.setattr(server, "state", server.State())
    server.state.load()
    assert server.state.error is None
    return TestClient(server.app)


def test_status_lists_systems_tasks_and_counts(client):
    body = client.get("/api/status").json()
    assert body["ready"] and body["concepts"] > 5 and body["reference_model"] == "reference"
    assert {s["id"] for s in body["systems"]} == {"B0", "L-RAG", "D-RAG", "G-QA", "G-QA+V"}
    assert [t["id"] for t in body["tasks"] if t["needs_y"]] == ["T3", "T4", "T6"]
    top = next(h for h in body["hierarchies"] if h["id"] == TOP)
    assert top["size"] >= 5


def test_not_ready_and_load_error(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "state", server.State())
    c = TestClient(server.app)
    assert c.get("/api/search", params={"q": "heart"}).status_code == 503
    monkeypatch.setattr(server, "DB", tmp_path / "missing.db")
    server.state.load()
    assert "not at" in c.get("/api/status").json()["error"]
    assert c.get("/api/concept/1").status_code == 500


def test_search_by_name_and_id(client):
    hits = client.get("/api/search", params={"q": "myocardial infarction"}).json()["results"]
    assert MI in [h["id"] for h in hits]
    mi = next(h for h in hits if h["id"] == MI)
    assert mi["name"] == "Myocardial infarction" and mi["tag"] == "disorder" and mi["hierarchy"]["id"] == TOP
    assert [h["id"] for h in client.get("/api/search", params={"q": MI}).json()["results"]] == [MI]
    assert client.get("/api/search", params={"q": "999999"}).json()["results"] == []


def test_concept_detail(client):
    body = client.get(f"/api/concept/{MI}").json()
    assert {p["id"] for p in body["parents"]} == {IHD, NEC}
    assert {c["id"] for c in body["children"]} == {AMI, OLD} and body["children_count"] == 2
    assert body["depth"] == {"shortest": 3, "longest": 3} and body["ancestors_count"] == 4
    assert "Heart attack" in body["synonyms"]
    assert [n["id"] for n in body["path"]][0] == "138875005" and body["path"][-1]["id"] == MI and len(body["path"]) == 4
    assert {"name": "Finding site", "value": "Myocardium structure"} in body["attributes"]
    assert client.get("/api/concept/999").status_code == 404
    page = client.get(f"/api/concept/{MI}/children", params={"offset": 1, "limit": 1}).json()
    assert page["total"] == 2 and len(page["children"]) == 1


def test_lineage_is_the_ancestor_closure_plus_children(client):
    body = client.get(f"/api/concept/{MI}/lineage").json()
    roles = {n["id"]: n["role"] for n in body["nodes"]}
    assert roles[MI] == "focus" and roles[IHD] == "ancestor" and roles[AMI] == "child"
    assert PROC not in roles
    assert {"child": MI, "parent": IHD} in body["edges"] and {"child": AMI, "parent": MI} in body["edges"]
    assert body["children_total"] == 2


def test_ask_with_the_reference_answerer(client):
    body = client.post("/api/ask", json={"task": "T1", "x": MI, "system": "G-QA"}).json()
    assert body["outcome"] == "correct" and body["model"] == "reference"
    assert {a["id"] for a in body["answer"]} == {IHD, NEC}
    assert all(c["label"] == "supported_by_subgraph" for c in body["claims"])
    assert body["evidence"]["sufficient"] and body["evidence"]["edges"]
    masked = client.post("/api/ask", json={"task": "T1", "x": MI, "system": "G-QA", "withheld": 50}).json()
    assert masked["evidence"]["mask"]["n_withheld"] == 1 and masked["outcome"] == "abstained"
    t3 = client.post("/api/ask", json={"task": "T3", "x": AMI, "y": TOP, "system": "G-QA+V"}).json()
    assert t3["answer"] == "yes" and t3["verification"]["regenerated"] is False


def test_ask_rejects_bad_questions(client):
    assert client.post("/api/ask", json={"task": "T4", "x": MI, "y": PROC}).status_code == 400
    assert client.post("/api/ask", json={"task": "T1", "x": MI, "system": "L-RAG", "withheld": 25}).status_code == 400
    assert client.post("/api/ask", json={"task": "T1", "x": MI, "withheld": 30}).status_code == 400
    assert client.post("/api/ask", json={"task": "T9", "x": MI}).status_code == 422


def test_compare_skips_dense_without_vectors(client):
    body = client.post("/api/compare", json={"task": "T1", "x": MI}).json()
    assert [r["system"] for r in body["results"]] == ["B0", "L-RAG", "G-QA", "G-QA+V"]
    assert body["skipped"][0]["system"] == "D-RAG"


def test_results_and_frontend_fallback(client, tmp_path, monkeypatch):
    assert client.get("/api/results").json() == {"stages": []}
    folder = tmp_path / "results" / "v3_cse499"
    (folder / "analysis_study2").mkdir(parents=True)
    (folder / "system_responses.jsonl").write_text("{}\n{}\n", encoding="utf-8")
    (folder / "analysis_study2" / "summary.csv").write_text("condition,accuracy\nG-QA,0.9\n", encoding="utf-8")
    stage = client.get("/api/results").json()["stages"][0]
    assert stage["rows"] == 2 and stage["summary"] == [{"condition": "G-QA", "accuracy": "0.9"}]
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<html>app</html>", encoding="utf-8")
    monkeypatch.setattr(server, "STATIC", static)
    assert "app" in client.get("/explore/123").text
    assert client.get("/api/nope").status_code == 404
