"""
Record-and-replay layer for BioPortal, used to freeze the research data.

* ``RecordingClient`` wraps a live ``BioPortalClient``. Every successful call
  (concept, parents, children, ancestors) is appended to a JSON Lines file
  with its timestamp, and served from that file on later calls. Failed calls
  are not stored, so an interrupted collection can simply be re-run.
* ``SnapshotClient`` replays the same file with no network access. It has the
  methods the crawler and graph builder use, so the project's real retrieval
  code can be run against frozen data. A request that is not in the file
  raises ``SnapshotMissError`` (a ``BioPortalRequestError``), which the
  crawler records as a failed retrieval.

The cache file contains SNOMED CT content (UMLS licence): keep it out of Git.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from crawler.bioportal_client import BioPortalRequestError

logger = logging.getLogger(__name__)

METHODS = ("get_concept", "get_parents", "fetch_children", "get_ancestors", "get_ontology_version")


class SnapshotMissError(BioPortalRequestError):
    """The requested call is not present in the frozen snapshot."""


def _utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _key(method, arg):
    return f"{method}:{arg}"


class CallStore:
    """Append-only JSONL store of BioPortal call results."""

    def __init__(self, path):
        self.path = Path(path)
        self.records = {}
        if self.path.exists():
            with self.path.open(encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        logger.warning("Skipping unreadable line %d in %s", line_number, self.path)
                        continue
                    self.records[_key(record["method"], record["arg"])] = record

    def __contains__(self, item):
        return _key(*item) in self.records

    def __len__(self):
        return len(self.records)

    def get(self, method, arg):
        record = self.records.get(_key(method, arg))
        return None if record is None else record["result"]

    def put(self, method, arg, result, meta=None):
        record = {"method": method, "arg": arg, "result": result, "retrieved_at": _utc_now()}
        if meta:
            record["meta"] = meta
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        self.records[_key(method, arg)] = record
        return result

    def retrieval_window(self):
        times = sorted(r["retrieved_at"] for r in self.records.values() if r.get("retrieved_at"))
        return (times[0], times[-1]) if times else (None, None)


class RecordingClient:
    """Live client with a write-through cache. All calls are strict (raise on failure)."""

    def __init__(self, live_client, store, max_child_pages=20, child_pagesize=100):
        self.live = live_client
        self.store = store
        self.max_child_pages = max_child_pages
        self.child_pagesize = child_pagesize
        self.cache_hits = 0

    @property
    def request_count(self):
        return getattr(self.live, "request_count", 0)

    def _cached(self, method, arg, fetch):
        if (method, arg) in self.store:
            self.cache_hits += 1
            return self.store.get(method, arg)
        return self.store.put(method, arg, fetch())

    def get_concept(self, concept_id, raise_errors=True):
        def fetch():
            data = self.live.get_concept(concept_id, raise_errors=True)
            if data is None:
                return None
            return {"concept_id": concept_id, "name": data.get("prefLabel") or data.get("label") or concept_id}
        return self._cached("get_concept", concept_id, fetch)

    def get_parents(self, concept_id, raise_errors=True):
        return self._cached("get_parents", concept_id,
                            lambda: self.live.get_parents(concept_id, raise_errors=True))

    def fetch_children(self, concept_id, max_pages=None, pagesize=None):
        return self._cached("fetch_children", concept_id, lambda: self.live.fetch_children(
            concept_id, max_pages=self.max_child_pages, pagesize=self.child_pagesize))

    def get_ancestors(self, concept_id, raise_errors=True):
        return self._cached("get_ancestors", concept_id,
                            lambda: self.live.get_ancestors(concept_id, raise_errors=True, max_pages=50))

    def get_ontology_version(self, raise_errors=True):
        return self._cached("get_ontology_version", "SNOMEDCT",
                            lambda: self.live.get_ontology_version(raise_errors=True))


class SnapshotClient:
    """
    Offline replay of a CallStore with the BioPortalClient interface used by
    BFSCrawler and GraphBuilder. ``max_child_pages`` simulates the app's page
    cap: if the stored child list is longer than ``max_child_pages *
    pagesize`` it is truncated and reported as incomplete.
    """

    def __init__(self, store):
        self.store = store
        self.request_count = 0
        self.last_error = None

    def _get(self, method, arg):
        self.request_count += 1
        if (method, arg) not in self.store:
            raise SnapshotMissError(f"{method}({arg}) is not in the frozen snapshot")
        return self.store.get(method, arg)

    def _guard(self, method, arg, raise_errors, fallback):
        try:
            return self._get(method, arg)
        except SnapshotMissError as exc:
            self.last_error = exc
            if raise_errors:
                raise
            return fallback

    def get_concept(self, concept_id, raise_errors=False):
        record = self._guard("get_concept", concept_id, raise_errors, None)
        if record is None:
            return None
        return {"prefLabel": record["name"]}

    def get_parents(self, concept_id, raise_errors=False):
        return list(self._guard("get_parents", concept_id, raise_errors, []))

    def fetch_children(self, concept_id, max_pages=5, pagesize=100):
        stored = self._get("fetch_children", concept_id)
        children = list(stored["children"])
        complete = bool(stored["complete"])
        cap = max_pages * pagesize
        if len(children) > cap:
            children, complete = children[:cap], False
        return {"children": children, "complete": complete, "pages_fetched": stored.get("pages_fetched")}

    def get_children(self, concept_id, raise_errors=False, max_pages=10):
        try:
            return self.fetch_children(concept_id, max_pages=max_pages)["children"]
        except SnapshotMissError as exc:
            self.last_error = exc
            if raise_errors:
                raise
            return []

    def get_ancestors(self, concept_id, raise_errors=False):
        return list(self._guard("get_ancestors", concept_id, raise_errors, []))

    def get_ontology_version(self, raise_errors=False):
        return self._guard("get_ontology_version", "SNOMEDCT", raise_errors, None)

    def search_concept(self, search_term, raise_errors=False):
        if raise_errors:
            raise SnapshotMissError("search is not part of the frozen snapshot")
        return []
