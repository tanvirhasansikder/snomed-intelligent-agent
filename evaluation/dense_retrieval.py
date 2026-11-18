"""
D-RAG: dense (BGE-M3) retrieval over the database's concept cards (Study 2, RQ4).

The database already stores a BGE-M3 vector for every card
(``concepts.embedding``: float32, 1024 dimensions, unit length;
``meta.embedder = bge:BAAI/bge-m3``). D-RAG needs the model only to encode
the queries, which are the same concept names L-RAG uses. So the work splits
into three offline steps, each a sub-command, and none of them calls an LLM:

1. ``verify``  encode a few cards' candidate texts (card text, term, name) and
   compare them with the stored vectors. The text whose vector matches
   (cosine near 1) is the text the database embedded. This is checked, not
   assumed, because querying with the wrong kind of text would weaken the
   baseline unfairly.
2. ``export``  copy the International-view vectors once into a float16 matrix
   (``data/snomed_db/dense/``, about 0.7 GB, Git-ignored), so searching never
   loads 1.7 GB of float32 into memory.
3. ``encode``  encode every query name the data set needs and save them
   (``query_vectors.npz``). Afterwards the experiment needs no model at all.

    python -m evaluation.dense_retrieval verify --db data\\snomed_db\\snomed.db
    python -m evaluation.dense_retrieval export --db data\\snomed_db\\snomed.db
    python -m evaluation.dense_retrieval encode --db data\\snomed_db\\snomed.db --dataset data\\datasets\\v3_pilot.json

The model is loaded only by ``verify`` and ``encode`` (``pip install
sentence-transformers``, CPU). Search itself is a dot product over the
float16 matrix in chunks: cosine similarity, because all vectors are unit length.
"""

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np

from crawler.local_client import split_fsn
from evaluation.text_retrieval import DEFAULT_K, entity_queries, merge_rankings, share_k

MODEL_NAME = "BAAI/bge-m3"
DIM = 1024
DENSE_VERSION = "1"
DEFAULT_DIR = Path("data/snomed_db/dense")
VERIFY_IDS = ("22298006", "73211009", "80146002", "404684003", "71388002")
CHUNK = 20000


# ----------------------------------------------------------------------
# the encoder (loaded only when needed)
# ----------------------------------------------------------------------

def load_encoder(model_name=MODEL_NAME):
    """BGE-M3 dense encoder on the CPU (sentence-transformers)."""
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:                      # pragma: no cover - depends on the laptop
        raise SystemExit("D-RAG needs: pip install sentence-transformers") from exc
    return SentenceTransformer(model_name, device="cpu")


def encode(texts, encoder, batch_size=16):
    """Unit-length float32 vectors, one row per text."""
    vectors = encoder.encode(list(texts), batch_size=batch_size, normalize_embeddings=True,
                             convert_to_numpy=True, show_progress_bar=False)
    vectors = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / np.where(norms == 0, 1, norms)


def stored_vector(con, concept_id):
    row = con.execute("SELECT embedding FROM concepts WHERE concept_id = ?", (concept_id,)).fetchone()
    if not row or row[0] is None:
        return None
    return np.frombuffer(row[0], dtype="<f4")


def _open(db):
    return sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro", uri=True)


# ----------------------------------------------------------------------
# 1. verify which text the database embedded
# ----------------------------------------------------------------------

def candidate_texts(con, concept_id):
    row = con.execute("SELECT term, synonyms, card_text FROM concepts WHERE concept_id = ?", (concept_id,)).fetchone()
    if not row:
        return {}
    term, synonyms, card = row
    name = split_fsn(term)[0] or term
    try:
        syns = json.loads(synonyms) if synonyms else []
    except json.JSONDecodeError:
        syns = []
    return {"card_text": card, "term (FSN)": term, "name": name,
            "name + synonyms": "; ".join([name] + list(syns))}


def verify(db, encoder, concept_ids=VERIFY_IDS):
    """{concept_id: {candidate: cosine with the stored vector}}."""
    con = _open(db)
    try:
        results = {}
        for concept_id in concept_ids:
            stored = stored_vector(con, concept_id)
            texts = candidate_texts(con, concept_id)
            if stored is None or not texts:
                continue
            vectors = encode(list(texts.values()), encoder)
            results[concept_id] = {name: float(v @ stored) for name, v in zip(texts, vectors)}
        return results
    finally:
        con.close()


# ----------------------------------------------------------------------
# 2. export the International-view matrix
# ----------------------------------------------------------------------

def export_matrix(db, keep_ids, out_dir=DEFAULT_DIR, dtype=np.float16):
    """
    Write ``embeddings.npy`` (rows = concepts in ``keep_ids`` order by ID) and
    ``ids.json``. Streams rows, so memory stays small. Returns (n, out_dir).
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    keep = set(keep_ids)
    con = _open(db)
    try:
        ids = [cid for (cid,) in con.execute("SELECT concept_id FROM concepts WHERE embedding IS NOT NULL "
                                             "ORDER BY concept_id") if cid in keep]
        matrix = np.lib.format.open_memmap(out_dir / "embeddings.npy", mode="w+", dtype=dtype, shape=(len(ids), DIM))
        position = {cid: i for i, cid in enumerate(ids)}
        for cid, blob in con.execute("SELECT concept_id, embedding FROM concepts WHERE embedding IS NOT NULL"):
            i = position.get(cid)
            if i is not None:
                vector = np.frombuffer(blob, dtype="<f4")
                if vector.shape[0] != DIM:
                    raise ValueError(f"{cid}: embedding has {vector.shape[0]} values, expected {DIM}")
                matrix[i] = vector
        matrix.flush()
        del matrix
    finally:
        con.close()
    (out_dir / "ids.json").write_text(json.dumps({"dense_version": DENSE_VERSION, "model": MODEL_NAME,
                                                   "dtype": np.dtype(dtype).name, "ids": ids}), encoding="utf-8")
    return len(ids), out_dir


class DenseIndex:
    """The exported matrix, memory-mapped; cosine search in chunks."""

    def __init__(self, directory=DEFAULT_DIR):
        directory = Path(directory)
        meta = json.loads((directory / "ids.json").read_text(encoding="utf-8"))
        self.ids = meta["ids"]
        self.model = meta["model"]
        self.matrix = np.load(directory / "embeddings.npy", mmap_mode="r")

    def search(self, query_vector, k=DEFAULT_K):
        """Top-``k`` IDs by cosine, ties broken by ID (rows are sorted by ID, so by row)."""
        q = np.asarray(query_vector, dtype=np.float32)
        best_scores, best_rows = np.empty(0, dtype=np.float32), np.empty(0, dtype=np.int64)
        for start in range(0, len(self.ids), CHUNK):
            scores = np.asarray(self.matrix[start:start + CHUNK], dtype=np.float32) @ q
            take = min(k, len(scores))
            top = np.argpartition(-scores, take - 1)[:take]
            best_scores = np.concatenate([best_scores, scores[top]])
            best_rows = np.concatenate([best_rows, top + start])
        order = np.lexsort((best_rows, -best_scores))[:k]
        return [self.ids[r] for r in best_rows[order]]


# ----------------------------------------------------------------------
# 3. query vectors and retrieval
# ----------------------------------------------------------------------

def save_query_vectors(path, texts, vectors):
    np.savez(path, texts=np.array(texts, dtype=object), vectors=vectors.astype(np.float32),
             model=np.array(MODEL_NAME), dense_version=np.array(DENSE_VERSION))


def load_query_vectors(path):
    data = np.load(path, allow_pickle=True)
    return {str(t): v for t, v in zip(data["texts"], data["vectors"])}


def dense_retrieve(item, index, query_vectors, label_of, k=DEFAULT_K):
    """(card IDs, queries) for D-RAG: cosine search per concept name, k shared as in L-RAG."""
    queries = entity_queries(item, label_of)
    missing = [q for q in queries if q not in query_vectors]
    if missing:
        raise KeyError(f"no query vector for {missing}; run the encode step for this data set")
    rankings = [index.search(query_vectors[q], k) for q in queries]
    return merge_rankings(rankings, share_k(k, len(queries)), k), queries


# ----------------------------------------------------------------------
# command line
# ----------------------------------------------------------------------

def _queries_for_dataset(db, dataset_path, cache_path):
    from crawler.local_client import LocalTerminologyClient
    from evaluation.bioportal_cache import CallStore
    from evaluation.gold import GoldIndex
    from evaluation.tasks import generate_items
    client = LocalTerminologyClient.from_db(db)
    dataset = json.loads(Path(dataset_path).read_text(encoding="utf-8"))
    items = generate_items(GoldIndex(CallStore(cache_path), dataset), seed=0)
    return sorted({q for item in items for q in entity_queries(item, client.label)}), len(items)


def main(argv=None):
    parser = argparse.ArgumentParser(description="D-RAG offline steps (no LLM calls)")
    sub = parser.add_subparsers(dest="step", required=True)
    for name in ("verify", "export", "encode"):
        p = sub.add_parser(name)
        p.add_argument("--db", default="data/snomed_db/snomed.db")
        p.add_argument("--out", default=str(DEFAULT_DIR))
        if name == "encode":
            p.add_argument("--dataset", default="data/datasets/v3_pilot.json")
            p.add_argument("--cache", default="data/local_cache/v3_calls.jsonl")
    args = parser.parse_args(argv)
    started = time.time()

    if args.step == "verify":
        encoder = load_encoder()
        lines = [f"Model {MODEL_NAME} loaded in {time.time() - started:.0f} s"]
        results = verify(args.db, encoder)
        for concept_id, scores in results.items():
            best = max(scores, key=scores.get)
            lines.append(f"{concept_id}: " + ", ".join(f"{k} {v:.4f}" for k, v in scores.items())
                         + f"  -> best: {best}")
        winners = {max(s, key=s.get) for s in results.values()}
        lines.append(f"\nText that matches the stored vectors: {', '.join(sorted(winners))}")
        report = "\n".join(lines)
        print(report)
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "verify_report.txt").write_text(report + "\n", encoding="utf-8")   # IDs and scores only
        print(f"Saved to {out / 'verify_report.txt'}")
        return 0

    if args.step == "export":
        from knowledge_graph.snomed_db_loader import load_graph
        graph = load_graph(args.db, "international")
        n, out = export_matrix(args.db, graph.names, args.out)
        print(f"Exported {n:,} International-view vectors to {out} in {time.time() - started:.0f} s")
        return 0

    queries, n_items = _queries_for_dataset(args.db, args.dataset, args.cache)
    encoder = load_encoder()
    vectors = encode(queries, encoder)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"query_vectors_{Path(args.dataset).stem}.npz"
    save_query_vectors(path, queries, vectors)
    print(f"Encoded {len(queries)} query names for {n_items} items in {time.time() - started:.0f} s -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
