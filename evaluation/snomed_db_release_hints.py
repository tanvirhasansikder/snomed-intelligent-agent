"""
Read-only clues to which SNOMED CT release and editions a local snomed.db holds.

    python -m evaluation.snomed_db_release_hints data\\snomed_db\\snomed.db

The database has no effectiveTime column, so the release cannot be read
directly. This prints:

1. how many concepts come from each namespace (International core, UK, US, ...);
2. the newest International concepts (highest item numbers) with their names,
   so their release dates can be looked up in a public SNOMED CT browser.

Nothing is changed or sent anywhere. Output contains SNOMED CT terms: keep it private.
"""

import argparse
import sqlite3
import sys
from collections import Counter
from pathlib import Path

TOP_N = 15


def partition(concept_id):
    return concept_id[-3:-1] if len(concept_id) >= 6 else None


def namespace(concept_id):
    """7-digit namespace of an extension concept ID ('10' partition), or 'international' ('00')."""
    part = partition(concept_id)
    if part == "00":
        return "international"
    if part == "10" and len(concept_id) >= 11:
        return concept_id[-10:-3]
    return f"partition-{part}"


def item_number(concept_id):
    """The item identifier of an International (core) concept ID: the digits before partition and check digit."""
    return int(concept_id[:-3])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("path")
    args = parser.parse_args(argv)
    path = Path(args.path)
    if not path.is_file():
        print(f"File not found: {path}")
        return 1
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    ids = [row[0] for row in con.execute("SELECT concept_id FROM concepts")]
    counts = Counter(namespace(c) for c in ids)
    print(f"Concepts: {len(ids):,}")
    print("By namespace (largest first):")
    for name, n in counts.most_common(15):
        print(f"   {name}: {n:,}")

    core = sorted((c for c in ids if namespace(c) == "international"), key=item_number, reverse=True)
    print(f"\nNewest {TOP_N} International concepts (highest item numbers):")
    for cid in core[:TOP_N]:
        term = con.execute("SELECT term FROM concepts WHERE concept_id = ?", (cid,)).fetchone()[0]
        print(f"   {cid}  {term}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
