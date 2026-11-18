"""
Load the IS-A hierarchy from the local SNOMED CT database (``snomed.db``) and
check it (research protocol v3, Study 2).

The database is the project's final dataset: an International Edition build
(supervisor, 2026-11-18) whose International content matches the 2026-02-01
release (checked against tx.fhir.org), plus extension concepts. It has the
tables ``concepts(concept_id, term, semantic_tag, synonyms, card_text,
embedding)`` and ``edges(parent_id, child_id)``; there is no active flag,
effectiveTime or relationship type.

Two views of the hierarchy:

* ``international`` (primary): only concepts with an International (core)
  identifier, i.e. partition ``00``. The file's hierarchy was classified with
  extension concepts included, so an extension concept can sit between two
  International ones (P -> extension X -> C). Simply dropping X would orphan
  C, so the view *projects* the hierarchy instead: each International concept's
  parents are its nearest International ancestors reached through extension
  concepts, keeping only the most specific ones (transitive reduction);
* ``international-filtered`` (diagnostic only): International concepts and only
  the edges directly between them, which shows how many links ran through
  extensions;
* ``full`` (sensitivity check): every concept and edge in the file.

The file holds 17 top-level hierarchies: "Special concept" (370115009) and
"SNOMED CT Model Component" (900000000000441003) are not in it.

Run it once to check the file and write the data manifest (no SNOMED CT
content, safe to commit):

    python -m knowledge_graph.snomed_db_loader --db data\\snomed_db\\snomed.db

Nothing in the database is changed.
"""

import argparse
import datetime
import hashlib
import json
import sqlite3
import subprocess
import sys
from collections import Counter, deque
from pathlib import Path

ROOT_ID = "138875005"
VIEWS = ("international", "international-filtered", "full")
DEFAULT_MANIFEST = Path("data") / "manifests" / "snomed_db_manifest.json"
RELEASE_NOTE = ("International Edition build (supervisor, 2026-11-18); International content matches the "
                "2026-02-01 release: newest core concepts carry effectiveTime 2026-02-01 and concepts first "
                "published 2026-03-01 are absent (tx.fhir.org, checked 2026-11-18)")


def namespace(concept_id):
    """'international' for a core identifier (partition 00), else the 7-digit extension namespace."""
    text = str(concept_id)
    part = text[-3:-1] if len(text) >= 6 else None
    if part == "00":
        return "international"
    if part == "10" and len(text) >= 11:
        return text[-10:-3]
    return f"partition-{part}"


def is_international(concept_id):
    return namespace(concept_id) == "international"


class SnomedGraph:
    """An IS-A hierarchy held in memory: parents, children and names by concept ID."""

    def __init__(self, names, edges, view, source=None, projection=None):
        self.view = view
        self.source = source
        self.projection = projection or {}
        self.names = dict(names)
        self.parents = {}
        self.children = {}
        for parent, child in edges:
            self.parents.setdefault(child, []).append(parent)
            self.children.setdefault(parent, []).append(child)
        for table in (self.parents, self.children):
            for key in table:
                table[key].sort()
        self._ancestors = {}

    # -- lookups ---------------------------------------------------------------
    def __contains__(self, concept_id):
        return concept_id in self.names

    def name(self, concept_id):
        return self.names.get(concept_id)

    def parents_of(self, concept_id):
        return list(self.parents.get(concept_id, ()))

    def children_of(self, concept_id):
        return list(self.children.get(concept_id, ()))

    def ancestors(self, concept_id):
        """All ancestors (transitive parents), excluding the concept itself."""
        if concept_id not in self._ancestors:
            seen, stack = set(), list(self.parents.get(concept_id, ()))
            while stack:
                node = stack.pop()
                if node not in seen:
                    seen.add(node)
                    stack.extend(self.parents.get(node, ()))
            self._ancestors[concept_id] = frozenset(seen)
        return self._ancestors[concept_id]

    def top_level_concepts(self):
        return self.children_of(ROOT_ID)

    def edge_count(self):
        return sum(len(v) for v in self.children.values())

    # -- whole-graph analyses ------------------------------------------------------
    def roots(self):
        """Concepts that have children but no parents (a complete hierarchy has exactly one)."""
        return sorted(c for c in self.children if not self.parents.get(c))

    def isolated(self):
        """Concepts with no parents and no children."""
        return sorted(c for c in self.names if c not in self.parents and c not in self.children)

    def dangling_edge_ends(self):
        """Edge endpoints with no row in ``concepts``."""
        ends = set(self.parents) | set(self.children)
        return sorted(c for c in ends if c not in self.names)

    def find_cycle_nodes(self):
        """Concepts on or behind a cycle (Kahn's algorithm leaves them unprocessed); empty for a DAG."""
        indegree = {c: len(p) for c, p in self.parents.items()}
        queue = deque(c for c in set(self.children) | set(self.parents) if indegree.get(c, 0) == 0)
        processed = set()
        while queue:
            node = queue.popleft()
            processed.add(node)
            for child in self.children.get(node, ()):
                indegree[child] -= 1
                if indegree[child] == 0:
                    queue.append(child)
        return sorted(c for c in indegree if c not in processed)

    def depths(self, root=ROOT_ID):
        """(shortest, longest) number of IS-A steps from ``root`` for every concept reachable from it."""
        shortest, order, queue = {root: 0}, [], deque([root])
        indegree = {}
        reachable, stack = {root}, [root]
        while stack:
            node = stack.pop()
            for child in self.children.get(node, ()):
                indegree[child] = indegree.get(child, 0) + 1
                if child not in reachable:
                    reachable.add(child)
                    stack.append(child)
        while queue:  # topological order over the reachable part: every parent is final before its child
            node = queue.popleft()
            order.append(node)
            for child in self.children.get(node, ()):
                shortest[child] = min(shortest.get(child, shortest[node] + 1), shortest[node] + 1)
                indegree[child] -= 1
                if indegree[child] == 0:
                    queue.append(child)
        longest = {root: 0}
        for node in order:
            for child in self.children.get(node, ()):
                longest[child] = max(longest.get(child, 0), longest[node] + 1)
        return {c: (shortest[c], longest[c]) for c in order}

    def descendant_count(self, concept_id):
        seen, stack = set(), list(self.children.get(concept_id, ()))
        while stack:
            node = stack.pop()
            if node not in seen:
                seen.add(node)
                stack.extend(self.children.get(node, ()))
        return len(seen)


def load_graph(db_path, view="international"):
    """Read ``snomed.db`` (read-only) into a :class:`SnomedGraph` for the given view."""
    if view not in VIEWS:
        raise ValueError(f"view must be one of {VIEWS}, not {view!r}")
    path = Path(db_path)
    if not path.is_file():
        raise FileNotFoundError(f"Database not found: {path}")
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        all_names = dict(con.execute("SELECT concept_id, term FROM concepts"))
        all_edges = list(con.execute("SELECT parent_id, child_id FROM edges"))
    finally:
        con.close()
    if view == "full":
        return SnomedGraph(all_names, all_edges, view, source=str(path))
    names = {c: t for c, t in all_names.items() if is_international(c)}
    if view == "international-filtered":
        edges = [(p, c) for p, c in all_edges if is_international(p) and is_international(c)]
        return SnomedGraph(names, edges, view, source=str(path))
    edges, stats = project_international(all_edges)
    return SnomedGraph(names, edges, view, source=str(path), projection=stats)


def project_international(edges):
    """
    Project a hierarchy classified with extension concepts onto International concepts.

    For each International concept, its projected parents are the nearest
    International ancestors: a direct International parent, or the first
    International concepts reached upward through extension-only paths. A
    candidate that is an ancestor of another candidate is dropped (transitive
    reduction), so only the most specific parents remain. Returns the edges
    as (parent, child) pairs and counts describing what changed.
    """
    parents = {}
    for parent, child in edges:
        parents.setdefault(child, []).append(parent)
    nearest = {}

    def nearest_international(node):
        """International concepts reached upward from an extension concept through extension-only paths."""
        if node not in nearest:
            nearest[node] = frozenset()   # guards against a cycle among extension concepts
            found, stack, seen = set(), list(parents.get(node, ())), set()
            while stack:
                up = stack.pop()
                if up in seen:
                    continue
                seen.add(up)
                if is_international(up):
                    found.add(up)
                else:
                    stack.extend(parents.get(up, ()))
            nearest[node] = frozenset(found)
        return nearest[node]

    ancestors_cache = {}

    def ancestors(node):
        if node not in ancestors_cache:
            seen, stack = set(), list(parents.get(node, ()))
            while stack:
                up = stack.pop()
                if up not in seen:
                    seen.add(up)
                    stack.extend(parents.get(up, ()))
            ancestors_cache[node] = seen
        return ancestors_cache[node]

    projected, bridged_children, reduced = [], 0, 0
    for child, ups in parents.items():
        if not is_international(child):
            continue
        direct = {p for p in ups if is_international(p)}
        via = set()
        for p in ups:
            if not is_international(p):
                via |= nearest_international(p)
        candidates = direct | via
        if via - direct:
            bridged_children += 1
            keep = {c for c in candidates if not any(c in ancestors(o) for o in candidates if o != c)}
            reduced += len(candidates) - len(keep)
            candidates = keep
        projected.extend((p, child) for p in sorted(candidates))
    original = sum(1 for p, c in edges if is_international(p) and is_international(c))
    stats = {"edges_between_international_concepts": original, "projected_edges": len(projected),
             "children_bridged_over_extensions": bridged_children,
             "redundant_parents_removed": reduced}
    return projected, stats


def check_graph(graph):
    """Structural checks on one view; returns a JSON-serialisable summary (IDs only, no terms)."""
    roots = graph.roots()
    cycles = graph.find_cycle_nodes()
    depths = graph.depths()
    reachable = set(depths)
    not_reachable = [c for c in graph.names if c not in reachable]
    shortest = Counter(d[0] for d in depths.values())
    longest = Counter(d[1] for d in depths.values())
    tops = graph.top_level_concepts()
    multi_parent = sum(1 for p in graph.parents.values() if len(p) > 1)
    leaves = sum(1 for c in reachable if c not in graph.children)
    return {
        "view": graph.view,
        "projection": graph.projection,
        "concepts": len(graph.names),
        "edges": graph.edge_count(),
        "roots": roots,
        "root_is_unique": roots == [ROOT_ID],
        "cycle_nodes": len(cycles),
        "cycle_examples": cycles[:10],
        "dangling_edge_ends": len(graph.dangling_edge_ends()),
        "isolated_concepts": len(graph.isolated()),
        "reachable_from_root": len(reachable),
        "not_reachable_from_root": len(not_reachable),
        "not_reachable_examples": sorted(not_reachable)[:10],
        "leaves_reachable": leaves,
        "concepts_with_several_parents": multi_parent,
        "depth_shortest_histogram": {str(k): v for k, v in sorted(shortest.items())},
        "depth_longest_histogram": {str(k): v for k, v in sorted(longest.items())},
        "top_level_concepts": len(tops),
        "descendants_per_top_level_concept": {t: graph.descendant_count(t) for t in tops},
        "namespaces": dict(Counter(namespace(c) for c in graph.names).most_common()),
    }


def sha256_of(path, chunk=1 << 20):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _git_revision():
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def build_manifest(db_path, views=VIEWS, hash_file=True):
    """Provenance and check results for the database file (concept IDs and counts only)."""
    path = Path(db_path)
    stat = path.stat()
    manifest = {
        "dataset": "snomed.db (supervisor-provided; Hugging Face bucket InteliLab/SNOMED_DB)",
        "release": RELEASE_NOTE,
        "file_bytes": stat.st_size,
        "file_modified_utc": datetime.datetime.fromtimestamp(stat.st_mtime, datetime.timezone.utc).isoformat(),
        "sha256": sha256_of(path) if hash_file else None,
        "manifest_created_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "code_revision": _git_revision(),
        "views": {},
    }
    for view in views:
        manifest["views"][view] = check_graph(load_graph(path, view))
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("--db", required=True, help="path to snomed.db")
    parser.add_argument("--out", default=str(DEFAULT_MANIFEST), help="where to write the manifest (JSON)")
    parser.add_argument("--no-hash", action="store_true", help="skip the SHA-256 of the file (faster)")
    args = parser.parse_args(argv)
    manifest = build_manifest(args.db, hash_file=not args.no_hash)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    for view, s in manifest["views"].items():
        print(f"[{view}] {s['concepts']:,} concepts, {s['edges']:,} edges; roots {s['roots'][:5]}"
              f"{' ...' if len(s['roots']) > 5 else ''} (unique root: {s['root_is_unique']}); "
              f"cycle nodes {s['cycle_nodes']}; not reachable from root {s['not_reachable_from_root']:,}; "
              f"dangling edge ends {s['dangling_edge_ends']}; top-level concepts {s['top_level_concepts']}; "
              f"longest depth {max(map(int, s['depth_longest_histogram']), default=0)}")
    stats = manifest["views"].get("international", {}).get("projection")
    if stats:
        print(f"[international] projection: {stats['edges_between_international_concepts']:,} direct edges -> "
              f"{stats['projected_edges']:,} projected; {stats['children_bridged_over_extensions']:,} concepts "
              f"bridged over extension concepts; {stats['redundant_parents_removed']:,} redundant parents removed")
    print(f"SHA-256: {manifest['sha256']}")
    print(f"Manifest written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
