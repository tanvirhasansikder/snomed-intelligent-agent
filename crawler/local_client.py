"""
A terminology client over the local SNOMED CT graph (Study 2).

``LocalTerminologyClient`` answers the same calls as ``BioPortalClient``
(get_concept, get_parents, fetch_children, get_children, get_ancestors,
get_ontology_version, search_concept) with the same return shapes, so the
crawler, the S1 retrieval planner, the agent and the Streamlit app can run on
the local graph unchanged. It never touches the network.

Labels: the database stores the fully specified name (FSN), e.g.
"Myocardial infarction (disorder)". The client's ``prefLabel``/``name`` is the
FSN without its semantic tag ("Myocardial infarction"), applied the same way
to every concept; the FSN itself is available as ``fsn``. This is close to,
but not always identical with, SNOMED CT's preferred term.

    from crawler.local_client import LocalTerminologyClient
    client = LocalTerminologyClient.from_db("data/snomed_db/snomed.db")
    client.get_parents("22298006")
"""

import re
import sqlite3
from pathlib import Path

from knowledge_graph.snomed_db_loader import load_graph

RELEASE = "2026-02-01"
_TAG = re.compile(r"\s*\(([^()]*)\)\s*$")
_WORD = re.compile(r"[\w-]+", re.UNICODE)


class ConceptNotFound(LookupError):
    """The concept is not in the local graph (raised only with ``raise_errors=True``)."""


def split_fsn(fsn):
    """('Myocardial infarction', 'disorder') from 'Myocardial infarction (disorder)'."""
    if not fsn:
        return fsn, None
    match = _TAG.search(fsn)
    if not match:
        return fsn.strip(), None
    return fsn[:match.start()].strip(), match.group(1)


class LocalTerminologyClient:
    """BioPortalClient-compatible reads from a :class:`knowledge_graph.snomed_db_loader.SnomedGraph`."""

    def __init__(self, graph, db_path=None):
        self.graph = graph
        self.db_path = Path(db_path) if db_path else None
        self.request_count = 0
        self.last_error = None

    @classmethod
    def from_db(cls, db_path, view="international"):
        return cls(load_graph(db_path, view), db_path=db_path)

    # -- helpers -------------------------------------------------------------------
    def _count(self):
        self.request_count += 1

    def _known(self, concept_id, raise_errors):
        if concept_id in self.graph:
            return True
        self.last_error = ConceptNotFound(f"{concept_id} is not in the local graph ({self.graph.view} view)")
        if raise_errors:
            raise self.last_error
        return False

    def label(self, concept_id):
        return split_fsn(self.graph.name(concept_id))[0] or concept_id

    def _records(self, ids):
        return [{"concept_id": c, "name": self.label(c)} for c in ids]

    # -- the BioPortalClient interface ---------------------------------------------------
    def get_concept(self, concept_id, raise_errors=False):
        """{'prefLabel', 'label', 'fsn', 'semantic_tag', 'concept_id'}, or None if unknown."""
        self._count()
        if not self._known(concept_id, raise_errors):
            return None
        fsn = self.graph.name(concept_id)
        label, tag = split_fsn(fsn)
        return {"concept_id": concept_id, "prefLabel": label or concept_id, "label": label or concept_id,
                "fsn": fsn, "semantic_tag": tag}

    def get_parents(self, concept_id, raise_errors=False):
        self._count()
        if not self._known(concept_id, raise_errors):
            return []
        return self._records(self.graph.parents_of(concept_id))

    def fetch_children(self, concept_id, max_pages=5, pagesize=100):
        """
        Direct children, with the same page cap as the BioPortal app: more than
        ``max_pages * pagesize`` children are cut and reported as incomplete.
        Unknown concepts raise :class:`ConceptNotFound` (BioPortal's
        fetch_children is always strict too).
        """
        self._count()
        self._known(concept_id, raise_errors=True)
        children = self.graph.children_of(concept_id)
        cap = max_pages * pagesize if max_pages is not None and pagesize is not None else None
        complete = cap is None or len(children) <= cap
        if not complete:
            children = children[:cap]
        pages = max(1, -(-len(children) // pagesize)) if pagesize else 1
        return {"children": self._records(children), "complete": complete, "pages_fetched": pages}

    def get_children(self, concept_id, raise_errors=False, max_pages=10):
        try:
            return self.fetch_children(concept_id, max_pages=max_pages)["children"]
        except ConceptNotFound:
            if raise_errors:
                raise
            return []

    def get_ancestors(self, concept_id, raise_errors=False):
        self._count()
        if not self._known(concept_id, raise_errors):
            return []
        return self._records(sorted(self.graph.ancestors(concept_id)))

    def get_ontology_version(self, raise_errors=False):
        return {"version": f"SNOMED CT International Edition {RELEASE} (local snomed.db, {self.graph.view} view)",
                "released": RELEASE}

    def search_concept(self, search_term, raise_errors=False, pagesize=10):
        """
        Concepts whose names match all words of ``search_term``. Uses the
        database's full-text index (``names_fts``) when the database file is
        available, else a slower scan of the loaded names. Results are limited
        to concepts in this client's view, best match first.
        """
        self._count()
        words = [w.lower() for w in _WORD.findall(search_term or "")]
        if not words:
            return []
        try:
            ids = self._search_fts(words, pagesize) if self.db_path else None
        except sqlite3.Error as exc:
            self.last_error = exc
            if raise_errors:
                raise
            ids = None
        if ids is None:
            ids = self._search_scan(words, pagesize)
        return self._records(ids)

    def _search_fts(self, words, limit):
        query = " ".join('"' + w.replace('"', '""') + '"' for w in words)
        con = sqlite3.connect(f"file:{self.db_path.as_posix()}?mode=ro", uri=True)
        try:
            rows = con.execute("SELECT concept_id FROM names_fts WHERE names_fts MATCH ? ORDER BY rank LIMIT ?",
                               (query, limit * 20))
            found = []
            for (concept_id,) in rows:
                if concept_id in self.graph and concept_id not in found:
                    found.append(concept_id)
                if len(found) == limit:
                    break
            return found
        finally:
            con.close()

    def _search_scan(self, words, limit):
        scored = []
        for concept_id, fsn in self.graph.names.items():
            label = (split_fsn(fsn)[0] or "").lower()
            if all(w in label for w in words):
                scored.append((label != " ".join(words), len(label), concept_id))
        return [c for _, _, c in sorted(scored)[:limit]]


def _demo(argv=None):
    """Print what the client returns for one concept and one search (read-only)."""
    import argparse
    import time
    parser = argparse.ArgumentParser(description="Try the local terminology client on snomed.db")
    parser.add_argument("--db", required=True)
    parser.add_argument("--concept", default="22298006", help="concept ID (default: myocardial infarction)")
    parser.add_argument("--search", default="myocardial infarction")
    parser.add_argument("--view", default="international")
    args = parser.parse_args(argv)
    started = time.time()
    client = LocalTerminologyClient.from_db(args.db, view=args.view)
    print(f"Loaded the {args.view} view in {time.time() - started:.1f} s: {client.get_ontology_version()['version']}")
    concept = client.get_concept(args.concept)
    if concept is None:
        print(f"{args.concept} is not in this view")
        return 1
    started = time.time()
    parents = client.get_parents(args.concept)
    children = client.fetch_children(args.concept)
    ancestors = client.get_ancestors(args.concept)
    lookup_ms = (time.time() - started) * 1000
    print(f"{args.concept}: {concept['prefLabel']} [{concept['semantic_tag']}]")
    print(f"  parents ({len(parents)}): " + "; ".join(p["name"] for p in parents))
    print(f"  children: {len(children['children'])} (complete: {children['complete']})")
    print(f"  ancestors: {len(ancestors)}; three lookups took {lookup_ms:.1f} ms")
    hits = client.search_concept(args.search, pagesize=5)
    print(f"Search '{args.search}': " + "; ".join(f"{h['concept_id']} {h['name']}" for h in hits))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_demo())
