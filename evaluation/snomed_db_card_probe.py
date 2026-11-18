"""
Read-only probe of the concept cards and search indexes in snomed.db (Study 2, task 5).

    python -m evaluation.snomed_db_card_probe --db data\\snomed_db\\snomed.db

The text-retrieval baselines (L-RAG: keyword/BM25 search; D-RAG: BGE-M3
vector search) search the database's own concept cards, so their design
depends on what a card contains and how the indexes are built. This prints:

* the CREATE statements of every table, index and full-text table;
* any small table that may describe the database (metadata, model name);
* three full example cards (fixed concepts), with their names and synonyms;
* the embedding format: bytes per vector, and the vector length and norm
  when read as float32 and as float16;
* a keyword (BM25) search on each full-text table, with timings;
* how often a card mentions its concept's parents and children in the graph.

It opens the file read-only and writes the same report to
data/snomed_db/card_probe.txt (Git-ignored: it contains SNOMED CT terms).
"""

import argparse
import math
import sqlite3
import struct
import sys
import time
from pathlib import Path

EXAMPLES = ("22298006", "73211009", "80146002")   # myocardial infarction, diabetes mellitus, appendectomy
QUERY = "myocardial infarction"
SAMPLE = 300


def _q(name):
    return '"' + name.replace('"', '""') + '"'


def _vector_stats(blob, fmt, size):
    n = len(blob) // size
    if n == 0 or len(blob) % size:
        return None
    values = struct.unpack(f"<{n}{fmt}", blob[:n * size])
    if any(math.isnan(v) or math.isinf(v) for v in values):
        return f"{n} values, contains NaN/inf"
    norm = math.sqrt(sum(v * v for v in values))
    return f"{n} values, L2 norm {norm:.4f}, first {', '.join(f'{v:.4f}' for v in values[:4])}"


def probe(db, out):
    lines = []

    def say(text=""):
        print(text)
        lines.append(text)

    con = sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro", uri=True)
    try:
        say(f"Database: {db}")
        say("\n== Schema (CREATE statements)")
        objects = con.execute("SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL "
                              "AND name NOT LIKE 'sqlite_%' ORDER BY type, name").fetchall()
        tables = []
        for kind, name, sql in objects:
            say(f"-- {kind} {name}\n{sql}")
            if kind == "table":
                tables.append(name)

        say("\n== Small tables (possible metadata)")
        for name in tables:
            if any(name.startswith(f"{fts}_") for fts in ("names_fts", "names_stem_fts", "concepts_fts")):
                continue
            try:
                count = con.execute(f"SELECT COUNT(*) FROM {_q(name)}").fetchone()[0]
            except sqlite3.Error as exc:
                say(f"{name}: {exc}")
                continue
            say(f"{name}: {count:,} rows")
            if count <= 20:
                for row in con.execute(f"SELECT * FROM {_q(name)}"):
                    say("   " + " | ".join(str(v)[:200] for v in row))

        columns = [r[1] for r in con.execute("PRAGMA table_info(concepts)")]
        say(f"\n== concepts columns: {columns}")
        text_cols = [c for c in columns if c != "embedding"]
        for concept_id in EXAMPLES:
            row = con.execute(f"SELECT {', '.join(_q(c) for c in text_cols)} FROM concepts WHERE concept_id = ?",
                              (concept_id,)).fetchone()
            say(f"\n-- example card {concept_id}")
            if row is None:
                say("   (not in the database)")
                continue
            for col, value in zip(text_cols, row):
                say(f"[{col}]\n{value}")
            names = con.execute("SELECT * FROM concept_names WHERE concept_id = ? LIMIT 12", (concept_id,)).fetchall()
            say(f"[concept_names rows] {names}")

        if "embedding" in columns:
            say("\n== Embedding format")
            blob = con.execute("SELECT embedding FROM concepts WHERE concept_id = ?", (EXAMPLES[0],)).fetchone()
            blob = blob[0] if blob else None
            if blob is None:
                say("embedding is NULL for the example")
            else:
                say(f"type {type(blob).__name__}, {len(blob)} bytes")
                if isinstance(blob, (bytes, bytearray)):
                    say(f"as float32: {_vector_stats(blob, 'f', 4)}")
                    say(f"as float16: {_vector_stats(blob, 'e', 2)}")
                else:
                    say(f"first 200 characters: {str(blob)[:200]}")
            nulls = con.execute("SELECT COUNT(*) FROM concepts WHERE embedding IS NULL").fetchone()[0]
            lengths = con.execute("SELECT length(embedding), COUNT(*) FROM concepts GROUP BY 1 ORDER BY 2 DESC LIMIT 5"
                                  ).fetchall()
            say(f"NULL embeddings: {nulls:,}; byte lengths (length: count): {lengths}")

        say(f"\n== Keyword search for '{QUERY}' (BM25, top 5)")
        for fts in ("concepts_fts", "names_fts", "names_stem_fts"):
            if fts not in tables:
                say(f"{fts}: not present")
                continue
            fts_cols = [r[1] for r in con.execute(f"PRAGMA table_info({_q(fts)})")]
            started = time.perf_counter()
            try:
                rows = con.execute(f"SELECT *, bm25({_q(fts)}) FROM {_q(fts)} WHERE {_q(fts)} MATCH ? "
                                   f"ORDER BY bm25({_q(fts)}) LIMIT 5", (QUERY,)).fetchall()
            except sqlite3.Error as exc:
                say(f"{fts}: {exc}")
                continue
            ms = (time.perf_counter() - started) * 1000
            say(f"{fts} columns {fts_cols}: {ms:.0f} ms")
            for row in rows:
                say("   " + " | ".join(str(v)[:120] for v in row))

        say(f"\n== Do cards mention the concept's graph neighbours? (first {SAMPLE} concepts by ID)")
        if "card_text" in columns:
            rows = con.execute("SELECT concept_id, card_text FROM concepts ORDER BY concept_id LIMIT ?",
                               (SAMPLE,)).fetchall()
            term = {cid: t for cid, t in con.execute("SELECT concept_id, term FROM concepts")}
            stats = {"cards": 0, "parent_id": 0, "parent_term": 0, "child_id": 0, "child_term": 0, "chars": 0}
            for concept_id, card in rows:
                card = card or ""
                parents = [p for (p,) in con.execute("SELECT parent_id FROM edges WHERE child_id = ?", (concept_id,))]
                children = [c for (c,) in con.execute("SELECT child_id FROM edges WHERE parent_id = ? LIMIT 20",
                                                      (concept_id,))]
                stats["cards"] += 1
                stats["chars"] += len(card)
                stats["parent_id"] += any(p in card for p in parents)
                stats["parent_term"] += any((term.get(p) or "\0").split(" (")[0] in card for p in parents)
                stats["child_id"] += any(c in card for c in children)
                stats["child_term"] += any((term.get(c) or "\0").split(" (")[0] in card for c in children)
            n = max(stats["cards"], 1)
            say(f"cards: {stats['cards']}, mean length {stats['chars'] / n:.0f} characters")
            for key in ("parent_id", "parent_term", "child_id", "child_term"):
                say(f"  mentions a {key.replace('_', ' ')}: {stats[key]} ({100 * stats[key] / n:.0f}%)")
    finally:
        con.close()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nSaved to {out} (private: do not commit)")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--db", default="data/snomed_db/snomed.db")
    parser.add_argument("--out", default="data/snomed_db/card_probe.txt")
    args = parser.parse_args(argv)
    if not Path(args.db).is_file():
        print(f"File not found: {args.db}")
        return 1
    return probe(args.db, Path(args.out))


if __name__ == "__main__":
    sys.exit(main())
