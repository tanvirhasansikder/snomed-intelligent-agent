"""
Read-only first look at a local SNOMED CT database file (e.g. InteliLab/SNOMED_DB).

    python -m evaluation.inspect_snomed_db data\\snomed_db\\snomed.db

It opens the file read-only and prints: the file type, every table and view
with its columns, indexes and row count, two example rows per table, and the
range of any column that looks like an RF2 effectiveTime or a release/version
field. Nothing is changed, written or sent anywhere. Output can contain
SNOMED CT terms: keep it private (do not commit or publish it).
"""

import argparse
import sqlite3
import sys
from pathlib import Path

SQLITE_MAGIC = b"SQLite format 3\x00"
VERSION_HINTS = ("effectivetime", "effective_time", "release", "version", "edition", "moduleid", "module_id")
EXAMPLE_ROWS = 2
MAX_CELL = 60


def _cell(value):
    text = repr(value) if not isinstance(value, str) else value
    return text if len(text) <= MAX_CELL else text[:MAX_CELL - 3] + "..."


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def inspect(path, count_rows=True):
    path = Path(path)
    if not path.is_file():
        print(f"File not found: {path}")
        return 1
    with path.open("rb") as handle:
        magic = handle.read(16)
    size_gb = path.stat().st_size / 1e9
    print(f"File: {path} ({size_gb:.2f} GB)")
    if magic != SQLITE_MAGIC:
        print(f"Not an SQLite file (first bytes: {magic!r}). Tell Claude these bytes; the format needs another reader.")
        return 2
    print("Type: SQLite database")

    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        objects = connection.execute(
            "SELECT type, name, sql FROM sqlite_master WHERE type IN ('table', 'view') "
            "AND name NOT LIKE 'sqlite_%' ORDER BY type, name").fetchall()
        print(f"Tables and views: {len(objects)}\n")
        for kind, name, _ in objects:
            columns = connection.execute(f"PRAGMA table_info({_quote(name)})").fetchall()
            names = [c[1] for c in columns]
            print(f"== {kind} {name}")
            print("   columns: " + ", ".join(f"{c[1]} {c[2] or ''}".strip() for c in columns))
            if kind == "table":
                indexes = connection.execute(f"PRAGMA index_list({_quote(name)})").fetchall()
                if indexes:
                    print("   indexes: " + ", ".join(i[1] for i in indexes))
            if count_rows:
                n = connection.execute(f"SELECT COUNT(*) FROM {_quote(name)}").fetchone()[0]
                print(f"   rows: {n:,}")
            for row in connection.execute(f"SELECT * FROM {_quote(name)} LIMIT {EXAMPLE_ROWS}"):
                print("   example: " + " | ".join(f"{k}={_cell(v)}" for k, v in zip(names, row)))
            for column in names:
                if any(hint in column.lower() for hint in VERSION_HINTS):
                    low, high, distinct = connection.execute(
                        f"SELECT MIN({_quote(column)}), MAX({_quote(column)}), COUNT(DISTINCT {_quote(column)}) "
                        f"FROM {_quote(name)}").fetchone()
                    print(f"   {column}: min={_cell(low)} max={_cell(high)} distinct={distinct:,}")
            print()
    finally:
        connection.close()
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("path", help="path to the .db file")
    parser.add_argument("--no-counts", action="store_true", help="skip COUNT(*) (faster on very large tables)")
    args = parser.parse_args(argv)
    return inspect(args.path, count_rows=not args.no_counts)


if __name__ == "__main__":
    sys.exit(main())
