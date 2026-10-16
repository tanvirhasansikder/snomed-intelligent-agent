"""
Test doubles for BioPortal, the HTTP session and the Neo4j repository.

These fakes encode *independently specified* behaviour (a small hand-written
hierarchy), so tests do not simply repeat the implementation's assumptions.
All concept IDs in ``SYNTHETIC_*`` structures are synthetic test data.
"""

from collections import deque

try:  # Present after the repair; absent in the original snapshot.
    from crawler.bioportal_client import BioPortalRequestError
except ImportError:  # pragma: no cover - only used when testing the baseline
    class BioPortalRequestError(Exception):
        pass


SNOMED_PREFIX = "http://purl.bioontology.org/ontology/SNOMEDCT/"


# ----------------------------------------------------------------------
# HTTP layer
# ----------------------------------------------------------------------

class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text="", headers=None, invalid_json=False):
        self.status_code = status_code
        self._json = json_data
        self.text = text
        self.headers = headers or {}
        self._invalid_json = invalid_json

    def json(self):
        if self._invalid_json:
            raise ValueError("No JSON object could be decoded")
        return self._json


class FakeSession:
    """Records calls and answers them with a handler(url, params) function."""

    def __init__(self, handler):
        self.handler = handler
        self.calls = []
        self.headers = {}

    def get(self, url, params=None, timeout=None, **kwargs):
        self.calls.append({"url": url, "params": dict(params or {}), "timeout": timeout})
        result = self.handler(url, params or {})
        if isinstance(result, BaseException):
            raise result
        return result


def bioportal_class(concept_id, label, with_label=True):
    """A minimal BioPortal class object as returned by the REST API."""
    item = {
        "@id": SNOMED_PREFIX + concept_id,
        "@type": "http://www.w3.org/2002/07/owl#Class",
    }
    if with_label:
        item["prefLabel"] = label
    return item


# ----------------------------------------------------------------------
# Ontology fake used by crawler / builder / query-processor tests
# ----------------------------------------------------------------------

#: Hand-written synthetic hierarchy (child -> parents). Not real SNOMED data.
SYNTHETIC_PARENTS = {
    "100": [],                 # root
    "200": ["100"],
    "300": ["100"],
    "400": ["200", "300"],     # polyhierarchy: two parents
    "500": ["400"],
    "600": ["400"],
    "700": ["500"],
}

SYNTHETIC_LABELS = {
    "100": "Root concept",
    "200": "Finding A",
    "300": "Finding B",
    "400": "Disorder AB",
    "500": "Disorder AB type 1",
    "600": "Disorder AB type 2",
    "700": "Disorder AB type 1 variant",
}


class FakeOntologyClient:
    """
    In-memory stand-in for BioPortalClient.

    ``fail_parents`` / ``fail_concepts`` / ``fail_children`` simulate API
    failures. In strict mode (``raise_errors=True``) failures raise
    ``BioPortalRequestError``; otherwise they return empty values, which is
    what the original client did.
    """

    def __init__(self, parents=None, labels=None, fail_parents=(), fail_concepts=(),
                 fail_children=(), extra_parent_records=None, search_results=None,
                 children_page_limit=None):
        self.parents = {k: list(v) for k, v in (parents or SYNTHETIC_PARENTS).items()}
        self.labels = dict(labels or SYNTHETIC_LABELS)
        self.fail_parents = set(fail_parents)
        self.fail_concepts = set(fail_concepts)
        self.fail_children = set(fail_children)
        self.extra_parent_records = extra_parent_records or {}
        self.search_results = search_results or {}
        self.children_page_limit = children_page_limit
        self.calls = {"get_concept": [], "get_parents": [], "get_children": [],
                      "fetch_children": [], "search_concept": []}
        self.request_count = 0

    def _children_of(self, concept_id):
        return sorted(c for c, ps in self.parents.items() if concept_id in ps)

    def get_concept(self, concept_id, raise_errors=False):
        self.calls["get_concept"].append(concept_id)
        self.request_count += 1
        if concept_id in self.fail_concepts:
            if raise_errors:
                raise BioPortalRequestError(f"HTTP 503 for concept {concept_id}")
            return None
        if concept_id not in self.labels:
            return None
        return {"@id": SNOMED_PREFIX + concept_id, "prefLabel": self.labels[concept_id]}

    def get_parents(self, concept_id, raise_errors=False):
        self.calls["get_parents"].append(concept_id)
        self.request_count += 1
        if concept_id in self.fail_parents:
            if raise_errors:
                raise BioPortalRequestError(f"HTTP 503 for parents of {concept_id}")
            return []
        records = [
            {"concept_id": p, "name": self.labels.get(p, p)}
            for p in self.parents.get(concept_id, [])
        ]
        return records + list(self.extra_parent_records.get(concept_id, []))

    def fetch_children(self, concept_id, max_pages=5, pagesize=100):
        self.calls["fetch_children"].append(concept_id)
        self.request_count += 1
        if concept_id in self.fail_children:
            raise BioPortalRequestError(f"HTTP 503 for children of {concept_id}")
        children = [{"concept_id": c, "name": self.labels.get(c, c)} for c in self._children_of(concept_id)]
        complete = True
        if self.children_page_limit is not None and len(children) > self.children_page_limit:
            children = children[: self.children_page_limit]
            complete = False
        return {"children": children, "complete": complete, "pages_fetched": 1}

    def get_children(self, concept_id, raise_errors=False):
        self.calls["get_children"].append(concept_id)
        return self.fetch_children(concept_id)["children"]

    def get_ancestors(self, concept_id, raise_errors=False):
        self.request_count += 1
        if concept_id in self.fail_parents:
            if raise_errors:
                raise BioPortalRequestError(f"HTTP 503 for ancestors of {concept_id}")
            return []
        seen, queue = [], deque(self.parents.get(concept_id, []))
        while queue:
            parent = queue.popleft()
            if parent not in seen:
                seen.append(parent)
                queue.extend(self.parents.get(parent, []))
        return [{"concept_id": a, "name": self.labels.get(a, a)} for a in seen]

    def get_ontology_version(self, raise_errors=False):
        return {"version": "SYNTHETIC-TEST", "released": None}

    def search_concept(self, search_term, raise_errors=False):
        self.calls["search_concept"].append(search_term)
        self.request_count += 1
        value = self.search_results.get(search_term.lower(), [])
        if isinstance(value, BaseException):
            if raise_errors:
                raise value
            return []
        return list(value)


# ----------------------------------------------------------------------
# Repository fake (in-memory graph with the same public methods)
# ----------------------------------------------------------------------

class InMemoryRepository:
    def __init__(self):
        self.concepts = {}
        self.edges = set()          # (child_id, parent_id)
        self.expansion = {}         # (concept_id, direction) -> dict
        self.write_log = []

    # concept operations
    def save_concept(self, concept_id, name):
        self.concepts[concept_id] = name
        self.write_log.append(("concept", concept_id))
        return True

    def get_concept(self, concept_id):
        if concept_id not in self.concepts:
            return None
        return {"concept_id": concept_id, "name": self.concepts[concept_id]}

    def concept_exists(self, concept_id):
        return concept_id in self.concepts

    def find_concept_by_exact_name(self, name):
        target = name.strip().lower()
        for cid, label in sorted(self.concepts.items()):
            if isinstance(label, str) and label.lower() == target:
                return {"concept_id": cid, "name": label}
        return None

    # relationship operations
    def save_relationship(self, child_id, parent_id):
        if child_id not in self.concepts or parent_id not in self.concepts:
            return False
        self.edges.add((child_id, parent_id))
        self.write_log.append(("edge", child_id, parent_id))
        return True

    def save_children(self, parent_id, children):
        if parent_id not in self.concepts:
            return 0
        for child in children:
            self.concepts[child["concept_id"]] = child["name"]
            self.edges.add((child["concept_id"], parent_id))
        return len(children)

    def get_parents(self, concept_id):
        return sorted(
            ({"concept_id": p, "name": self.concepts[p]} for c, p in self.edges if c == concept_id),
            key=lambda r: r["name"],
        )

    def get_children(self, concept_id):
        return sorted(
            ({"concept_id": c, "name": self.concepts[c]} for c, p in self.edges if p == concept_id),
            key=lambda r: r["name"],
        )

    def get_ancestors(self, concept_id, max_depth=10):
        seen, frontier = {}, deque([(concept_id, 0)])
        while frontier:
            cid, d = frontier.popleft()
            if d >= max_depth:
                continue
            for c, p in self.edges:
                if c == cid and p not in seen:
                    seen[p] = d + 1
                    frontier.append((p, d + 1))
        return sorted(({"concept_id": a, "name": self.concepts[a]} for a in seen), key=lambda r: r["name"])

    # provenance
    def mark_expansion(self, concept_id, direction, status, **fields):
        record = {"status": status}
        record.update(fields)
        self.expansion[(concept_id, direction)] = record
        return concept_id in self.concepts

    def get_expansion_status(self, concept_id, direction):
        return self.expansion.get((concept_id, direction))

    def get_coverage(self, concept_id):
        return {
            "parents": self.expansion.get((concept_id, "up")),
            "children": self.expansion.get((concept_id, "down")),
        }

    # statistics
    def count_concepts(self):
        return len(self.concepts)

    def count_relationships(self):
        return len(self.edges)

    def close(self):
        pass


# ----------------------------------------------------------------------
# Neo4j database fake for repository query tests
# ----------------------------------------------------------------------

class FakeDB:
    """Records Cypher text and parameters; returns scripted results."""

    def __init__(self, results=None, error=None):
        self.calls = []
        self.results = list(results or [])
        self.error = error
        self.closed = False

    def execute_query(self, query, parameters=None):
        self.calls.append((query, dict(parameters or {})))
        if self.error is not None:
            raise self.error
        if self.results:
            return self.results.pop(0)
        return []

    def close(self):
        self.closed = True
