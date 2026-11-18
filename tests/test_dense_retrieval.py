"""D-RAG offline steps with a fake encoder and a small fake database (no model download)."""

import sqlite3

import numpy as np
import pytest

from evaluation import dense_retrieval as dr
from evaluation.tasks import Item
from evaluation.text_retrieval import dense_search

DIM = dr.DIM
IDS = [f"{100000 + i}001" for i in range(30)]
EXTENSION = "782751000000103"


def _unit(rng):
    v = rng.normal(size=DIM).astype(np.float32)
    return v / np.linalg.norm(v)


class FakeEncoder:
    """Maps each known text to a fixed unit vector (unknown text: a seeded random one)."""

    def __init__(self, table):
        self.table = table

    def encode(self, texts, batch_size=16, normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False):
        out = []
        for t in texts:
            if t in self.table:
                out.append(self.table[t])
            else:
                out.append(_unit(np.random.default_rng(abs(hash(t)) % (2 ** 32))))
        return np.stack(out)


@pytest.fixture
def setup(tmp_path):
    rng = np.random.default_rng(3)
    path = tmp_path / "snomed.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE concepts (concept_id TEXT PRIMARY KEY, term TEXT, semantic_tag TEXT, synonyms TEXT, "
                "card_text TEXT, embedding BLOB)")
    vectors, table = {}, {}
    for cid in IDS + [EXTENSION]:
        v = _unit(rng)
        vectors[cid] = v
        card = f"Term: Concept {cid}\nType: finding"
        table[card] = v                                    # the database embedded the card text
        con.execute("INSERT INTO concepts VALUES (?, ?, 'finding', '[\"Syn\"]', ?, ?)",
                    (cid, f"Concept {cid} (finding)", card, v.astype("<f4").tobytes()))
    con.commit()
    con.close()
    return path, vectors, FakeEncoder(table)


def test_verify_finds_the_embedded_text(setup):
    path, _, encoder = setup
    results = dr.verify(path, encoder, concept_ids=IDS[:3])
    assert set(results) == set(IDS[:3])
    for scores in results.values():
        assert max(scores, key=scores.get) == "card_text"
        assert scores["card_text"] == pytest.approx(1.0, abs=1e-5)
        assert scores["name"] < 0.5


def test_export_keeps_only_the_view_and_search_matches_brute_force(setup, tmp_path):
    path, vectors, _ = setup
    n, out = dr.export_matrix(path, IDS, tmp_path / "dense")
    assert n == len(IDS)
    index = dr.DenseIndex(out)
    assert EXTENSION not in index.ids and index.ids == sorted(IDS)
    assert index.matrix.dtype == np.float16
    matrix32 = np.stack([vectors[c] for c in index.ids]).astype(np.float16).astype(np.float32)
    query = vectors[IDS[7]]
    old_chunk, dr.CHUNK = dr.CHUNK, 7                       # force several chunks
    try:
        got = index.search(query, k=5)
    finally:
        dr.CHUNK = old_chunk
    assert got == dense_search(query, matrix32, index.ids, k=5)
    assert got[0] == IDS[7]                                # a card is nearest to its own vector


def test_query_vectors_round_trip_and_dense_retrieve(setup, tmp_path):
    path, vectors, _ = setup
    _, out = dr.export_matrix(path, IDS, tmp_path / "dense")
    index = dr.DenseIndex(out)
    names = {IDS[0]: "Alpha", IDS[1]: "Beta"}
    qfile = tmp_path / "q.npz"
    dr.save_query_vectors(qfile, ["Alpha", "Beta"], np.stack([vectors[IDS[0]], vectors[IDS[1]]]))
    qv = dr.load_query_vectors(qfile)
    assert set(qv) == {"Alpha", "Beta"}
    t3 = Item(f"T3:{IDS[0]}:{IDS[1]}", "T3", IDS[0], "yes", target_id=IDS[1], polarity="pos")
    ids, queries = dr.dense_retrieve(t3, index, qv, names.get, k=4)
    assert queries == ["Alpha", "Beta"] and ids[:2] == [IDS[0], IDS[1]] and len(ids) == 4
    with pytest.raises(KeyError, match="encode"):
        dr.dense_retrieve(Item("T1:x", "T1", IDS[2], []), index, qv, lambda c: "Gamma")


def test_encode_returns_unit_vectors(setup):
    _, _, encoder = setup
    out = dr.encode(["a", "b", "c"], encoder)
    assert out.shape == (3, DIM) and np.allclose(np.linalg.norm(out, axis=1), 1.0, atol=1e-5)
