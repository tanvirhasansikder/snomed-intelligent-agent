"""
Read-only cross-check of the frozen BioPortal reference against a local
SNOMED CT database (the supervisor-provided snomed.db: tables ``concepts``
and ``edges(parent_id, child_id)``).

    python -m evaluation.snomed_db_crosscheck --config configs/main.json --db data\\snomed_db\\snomed.db

It compares, using concept IDs only:

1. concept coverage: every concept in the frozen BioPortal cache, and the main sample;
2. direct parents of every concept whose parents were stored;
3. direct children of every concept whose child list was stored complete;
4. full ancestor sets of the sampled concepts;
5. labels (the database term without its "(semantic tag)" against the BioPortal name);
6. US-extension concepts (namespace 1000124), a hint at which edition the database holds;
7. the same parent, child and ancestor comparisons "root-adjusted": BioPortal shows the
   19 top-level hierarchies with no parent (Amendment 2), while the database links them to
   the SNOMED CT root 138875005, so the root is left out of both sides there;
8. which namespaces the database-only children come from, and the database's roots.

Nothing is changed. It prints counts and up to 10 example IDs per kind of
mismatch, and writes the same summary to ``<results_dir>/snomed_db_crosscheck.json``.
This is an exploratory check of the reference; it does not change the frozen
study (protocol v2.0).
"""

import argparse
import json
import re
import sqlite3
from collections import Counter
from pathlib import Path

from evaluation.bioportal_cache import CallStore
from evaluation.collect import load_config, resolve
from evaluation.gold import GoldIndex

US_NAMESPACE = "1000124"
ROOT = "138875005"
EXAMPLES = 10
_TAG = re.compile(r"\s*\([^()]*\)\s*$")


def namespace(concept_id):
    """The 7-digit namespace of an extension SCTID, or None for an International (core) ID."""
    text = str(concept_id)
    return text[-10:-3] if len(text) >= 11 and text[-3:-1] == "10" else None


class SnomedDB:
    def __init__(self, path):
        self.connection = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
        self._ancestors = {}

    def has(self, cid):
        return self.connection.execute("SELECT 1 FROM concepts WHERE concept_id = ?", (cid,)).fetchone() is not None

    def term(self, cid):
        row = self.connection.execute("SELECT term FROM concepts WHERE concept_id = ?", (cid,)).fetchone()
        return row[0] if row else None

    def parents(self, cid):
        return {r[0] for r in self.connection.execute("SELECT parent_id FROM edges WHERE child_id = ?", (cid,))}

    def children(self, cid):
        return {r[0] for r in self.connection.execute("SELECT child_id FROM edges WHERE parent_id = ?", (cid,))}

    def ancestors(self, cid):
        if cid not in self._ancestors:
            seen, stack = set(), list(self.parents(cid))
            while stack:
                node = stack.pop()
                if node not in seen:
                    seen.add(node)
                    stack.extend(self.parents(node))
            self._ancestors[cid] = seen
        return self._ancestors[cid]

    def roots(self):
        return [r[0] for r in self.connection.execute(
            "SELECT DISTINCT parent_id FROM edges WHERE parent_id NOT IN (SELECT child_id FROM edges) "
            "ORDER BY parent_id")]

    def counts(self):
        one = lambda q: self.connection.execute(q).fetchone()[0]  # noqa: E731
        return {"concepts": one("SELECT COUNT(*) FROM concepts"), "edges": one("SELECT COUNT(*) FROM edges"),
                "roots": one("SELECT COUNT(DISTINCT parent_id) FROM edges WHERE parent_id NOT IN "
                             "(SELECT child_id FROM edges)")}


def _compare_sets(pairs):
    """pairs: {concept: (bioportal_set, db_set)} -> summary with examples."""
    kinds = Counter()
    examples = {"missing_in_db": [], "extra_in_db": [], "both_differ": []}
    for cid, (bioportal, db) in sorted(pairs.items()):
        if bioportal == db:
            kinds["identical"] += 1
            continue
        missing, extra = bioportal - db, db - bioportal
        kind = "both_differ" if missing and extra else ("missing_in_db" if missing else "extra_in_db")
        kinds[kind] += 1
        if len(examples[kind]) < EXAMPLES:
            examples[kind].append({"concept": cid, "only_bioportal": sorted(missing)[:5], "only_db": sorted(extra)[:5]})
    total = sum(kinds.values())
    return {"compared": total, "identical": kinds["identical"],
            "identical_rate": round(kinds["identical"] / total, 4) if total else None,
            **{k: kinds[k] for k in examples}, "examples": {k: v for k, v in examples.items() if v}}


def crosscheck(gold, db):
    samples = sorted(gold.samples)
    known = set(gold.labels) | set(gold.parents) | {p for ps in gold.parents.values() for p in ps} \
        | {c for cs in gold.children.values() for c in cs}
    absent = sorted(c for c in known if not db.has(c))
    result = {"database": db.counts()}
    result["coverage"] = {
        "bioportal_concepts": len(known), "absent_from_db": len(absent), "absent_examples": absent[:EXAMPLES],
        "main_samples": len(samples), "main_samples_absent": sorted(c for c in samples if not db.has(c)),
    }
    drop = lambda values: set(values) - {ROOT}  # noqa: E731
    result["direct_parents_root_adjusted"] = _compare_sets(
        {c: (drop(ps), drop(db.parents(c))) for c, ps in gold.parents.items() if db.has(c) and c != ROOT})
    result["direct_children_root_adjusted"] = _compare_sets(
        {c: (set(gold.children[c]), db.children(c)) for c in gold.children_complete if db.has(c) and c != ROOT})
    result["sample_ancestors_root_adjusted"] = _compare_sets(
        {c: (drop(gold.ancestors(c)), drop(db.ancestors(c))) for c in samples if db.has(c)})
    extra_children = Counter()
    for c in gold.children_complete:
        if db.has(c) and c != ROOT:
            for child in db.children(c) - set(gold.children[c]):
                extra_children[namespace(child) or "international"] += 1
    result["db_only_children_by_namespace"] = dict(extra_children.most_common())
    result["db_roots"] = db.roots()
    result["absent_by_namespace"] = dict(Counter(namespace(c) or "international" for c in absent).most_common())
    result["direct_parents"] = _compare_sets(
        {c: (set(ps), db.parents(c)) for c, ps in gold.parents.items() if db.has(c)})
    result["direct_children_complete_lists"] = _compare_sets(
        {c: (set(gold.children[c]), db.children(c)) for c in gold.children_complete if db.has(c)})
    result["sample_ancestors"] = _compare_sets(
        {c: (set(gold.ancestors(c)), db.ancestors(c)) for c in samples if db.has(c)})

    agree = compared = 0
    disagree = []
    for cid, name in sorted(gold.labels.items()):
        term = db.term(cid)
        if term is None or name == cid:
            continue
        compared += 1
        if _TAG.sub("", term).strip().lower() == name.strip().lower():
            agree += 1
        elif len(disagree) < EXAMPLES:
            disagree.append(cid)
    result["labels"] = {"compared": compared, "same_after_removing_tag": agree,
                        "rate": round(agree / compared, 4) if compared else None, "different_examples": disagree}

    us = sorted(c for c in known if namespace(c) == US_NAMESPACE)
    result["us_extension"] = {"in_bioportal": len(us), "of_these_in_db": sum(1 for c in us if db.has(c)),
                              "examples": us[:EXAMPLES]}
    return result


def _print(result):
    d, cov = result["database"], result["coverage"]
    print(f"Database: {d['concepts']:,} concepts, {d['edges']:,} parent-child edges, {d['roots']} root(s)")
    print(f"Coverage: {cov['bioportal_concepts']:,} concepts in the BioPortal cache; "
          f"{cov['absent_from_db']:,} absent from the database {cov['absent_examples']}")
    print(f"          main samples absent: {len(cov['main_samples_absent'])} of {cov['main_samples']} "
          f"{cov['main_samples_absent'][:EXAMPLES]}")
    for key, title in (("direct_parents", "Direct parents"), ("direct_children_complete_lists", "Direct children"),
                       ("sample_ancestors", "Sample ancestors"),
                       ("direct_parents_root_adjusted", "Direct parents (root-adjusted)"),
                       ("direct_children_root_adjusted", "Direct children (root-adjusted)"),
                       ("sample_ancestors_root_adjusted", "Sample ancestors (root-adjusted)")):
        s = result[key]
        print(f"{title}: {s['identical']}/{s['compared']} identical ({s['identical_rate']}); "
              f"missing in db {s['missing_in_db']}, extra in db {s['extra_in_db']}, both differ {s['both_differ']}")
        for kind, rows in s["examples"].items():
            for row in rows[:3]:
                print(f"   {kind}: {row}")
    lab, us = result["labels"], result["us_extension"]
    print(f"Labels: {lab['same_after_removing_tag']}/{lab['compared']} the same ({lab['rate']}); "
          f"examples of different: {lab['different_examples']}")
    print(f"US extension (namespace {US_NAMESPACE}): {us['in_bioportal']} in the BioPortal cache, "
          f"{us['of_these_in_db']} of them in the database")
    print(f"Database-only children by namespace: {result['db_only_children_by_namespace']}")
    print(f"Concepts absent from the database by namespace: {result['absent_by_namespace']}")
    print(f"Database roots ({len(result['db_roots'])}): {result['db_roots']}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--config", required=True)
    parser.add_argument("--db", required=True)
    args = parser.parse_args(argv)
    config = load_config(args.config)
    store = CallStore(resolve(config, "cache_path"))
    dataset = json.loads(resolve(config, "dataset_path").read_text(encoding="utf-8"))
    result = crosscheck(GoldIndex(store, dataset), SnomedDB(args.db))
    _print(result)
    out = resolve(config, "results_dir") / "snomed_db_crosscheck.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"Written to {out}")


if __name__ == "__main__":
    main()
