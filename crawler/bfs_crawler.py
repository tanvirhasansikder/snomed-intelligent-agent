"""
Breadth-first crawler that expands the SNOMED CT hierarchy UPWARD
(concept -> parents -> grandparents ...) from a start concept.

Depth semantics (unchanged): the start concept is depth 0. A concept at
depth < max_depth has its parents retrieved; a concept at depth == max_depth
is saved but not expanded and is reported as a *frontier* node (it may have
more ancestors that were not retrieved).

Repairs compared with the original:

* API failures are recorded (``report.failed`` and ``parents_status = failed``
  on the node) instead of being treated as "no parents".
* Self-loops and records without a numeric SNOMED CT ID are skipped.
* A concept whose parent list was already retrieved completely is not
  fetched again (``use_cache=True``, default); its stored parents are used.
  Pass ``use_cache=False`` to force a refresh.
* Labels learnt from a parent record are reused, so each concept is not
  fetched a second time just to read its name.
* Client and repository can be injected (needed for tests).
"""

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging

from config.settings import DATA_SOURCE
from crawler.bioportal_client import BioPortalClient, BioPortalError
from database.graph_repository import GraphRepository

logger = logging.getLogger(__name__)


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class CrawlReport:
    """What a crawl did; every field reflects an observed event."""

    start_concepts: list = field(default_factory=list)
    max_depth: int = 0
    processed: int = 0
    edges_saved: int = 0
    cache_hits: int = 0
    malformed_records: int = 0
    frontier: set = field(default_factory=set)
    failed: dict = field(default_factory=dict)     # concept_id -> error message
    missing: list = field(default_factory=list)    # IDs BioPortal does not know
    started_at: str = None
    finished_at: str = None
    api_requests: int = None                       # HTTP attempts, if the client counts them

    def to_dict(self):
        return {
            "start_concepts": list(self.start_concepts),
            "max_depth": self.max_depth,
            "processed": self.processed,
            "edges_saved": self.edges_saved,
            "cache_hits": self.cache_hits,
            "malformed_records": self.malformed_records,
            "frontier": sorted(self.frontier),
            "failed": dict(self.failed),
            "missing": list(self.missing),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "api_requests": self.api_requests,
        }


class BFSCrawler:
    """
    Breadth-First Search crawler for dynamically expanding
    relevant portions of the SNOMED CT hierarchy.

    Compatible with GraphBuilder.
    """

    def __init__(self, max_depth=2, client=None, repository=None, use_cache=True, source_version=None):

        if isinstance(max_depth, bool) or not isinstance(max_depth, int):
            raise TypeError("max_depth must be an integer")
        if max_depth < 0:
            raise ValueError("max_depth must be >= 0")

        self.client = client if client is not None else BioPortalClient()
        self.repository = repository if repository is not None else GraphRepository()

        self.max_depth = max_depth
        self.use_cache = use_cache
        self.source_version = source_version

        # Queue stores: (concept_id, depth)
        self.queue = deque()

        # Concepts already processed
        self.visited = set()

        # Labels learnt from parent records: concept_id -> name
        self._known_names = {}

        self.report = CrawlReport(max_depth=max_depth)

    # ========================================================
    # ADD START CONCEPT
    # ========================================================

    def add_start_concept(
        self,
        concept_id,
        depth=0
    ):
        """
        Add a starting concept to the BFS queue.

        This method is used by GraphBuilder.
        """

        if not concept_id:
            return

        # Avoid duplicate start concepts
        if concept_id in self.visited:
            return

        self.report.start_concepts.append(concept_id)

        self.queue.append(
            (
                concept_id,
                depth
            )
        )

    # ========================================================
    # Helpers
    # ========================================================

    def _resolve_name(self, concept_id):
        """
        Return (name, ok). Uses a label already known (from a parent record
        or the local graph) before asking BioPortal.
        """

        if concept_id in self._known_names:
            return self._known_names[concept_id], True

        if self.use_cache:
            stored = self.repository.get_concept(concept_id)
            if stored and stored.get("name"):
                return stored["name"], True

        try:
            concept = self.client.get_concept(concept_id, raise_errors=True)
        except BioPortalError as exc:
            self.report.failed[concept_id] = str(exc)
            logger.warning("Could not retrieve concept %s: %s", concept_id, exc)
            return None, False

        if not concept:
            self.report.missing.append(concept_id)
            logger.warning("BioPortal has no SNOMEDCT class %s", concept_id)
            return None, False

        name = concept.get("prefLabel") or concept.get("label") or concept_id
        return name, True

    def _valid_parent(self, concept_id, parent):
        parent_id = parent.get("concept_id") if isinstance(parent, dict) else None
        if not isinstance(parent_id, str) or not parent_id.isdigit():
            return None
        if parent_id == concept_id:
            return None
        return parent_id

    def _retrieve_parents(self, concept_id):
        """
        Return (parent_records, from_cache), or (None, False) on failure.
        Uses the stored parent list when it was previously retrieved completely.
        """

        if self.use_cache:
            status = self.repository.get_expansion_status(concept_id, "up")
            if status and status.get("status") == "complete":
                self.report.cache_hits += 1
                return self.repository.get_parents(concept_id), True

        try:
            return self.client.get_parents(concept_id, raise_errors=True), False
        except BioPortalError as exc:
            self.report.failed[concept_id] = str(exc)
            self.repository.mark_expansion(
                concept_id, "up", "failed",
                retrieved_at=utc_now(), source=DATA_SOURCE,
                source_version=self.source_version, error=str(exc),
            )
            logger.warning("Parent retrieval failed for %s: %s", concept_id, exc)
            return None, False

    # ========================================================
    # CRAWL
    # ========================================================

    def crawl(self, start_concept_id=None):
        """
        Run BFS expansion.

        If start_concept_id is provided, it will be added
        to the queue automatically.

        Returns:
            set: concepts processed by the crawler.
        """

        if start_concept_id:

            self.add_start_concept(
                start_concept_id,
                0
            )

        self.report.started_at = self.report.started_at or utc_now()
        requests_before = getattr(self.client, "request_count", None)

        while self.queue:

            concept_id, depth = self.queue.popleft()

            # Prevent duplicate processing
            if concept_id in self.visited:
                continue

            self.visited.add(concept_id)
            self.report.processed += 1

            # ------------------------------------------------
            # Name (and existence check)
            # ------------------------------------------------

            concept_name, ok = self._resolve_name(concept_id)

            if not ok:
                continue

            self.repository.save_concept(concept_id, concept_name)

            # ------------------------------------------------
            # Depth limit: saved but not expanded
            # ------------------------------------------------

            if depth >= self.max_depth:
                self.report.frontier.add(concept_id)
                logger.debug("Reached maximum depth at %s", concept_id)
                continue

            # ------------------------------------------------
            # Parents
            # ------------------------------------------------

            parents, from_cache = self._retrieve_parents(concept_id)

            if parents is None:
                continue

            valid_parents = 0

            for parent in parents:

                parent_id = self._valid_parent(concept_id, parent)

                if parent_id is None:
                    self.report.malformed_records += 1
                    logger.warning("Skipped malformed parent record for %s: %r", concept_id, parent)
                    continue

                parent_name = parent.get("name") or parent_id
                valid_parents += 1

                if not from_cache:
                    self.repository.save_concept(parent_id, parent_name)
                    if self.repository.save_relationship(concept_id, parent_id):
                        self.report.edges_saved += 1

                self._known_names.setdefault(parent_id, parent_name)

                if parent_id not in self.visited:
                    self.queue.append((parent_id, depth + 1))

            if not from_cache:
                self.repository.mark_expansion(
                    concept_id, "up", "complete",
                    retrieved_at=utc_now(), source=DATA_SOURCE,
                    source_version=self.source_version, count=valid_parents,
                )

        self.report.finished_at = utc_now()
        requests_after = getattr(self.client, "request_count", None)
        if isinstance(requests_before, int) and isinstance(requests_after, int):
            self.report.api_requests = (self.report.api_requests or 0) + requests_after - requests_before

        logger.info(
            "BFS crawl finished: processed=%d edges_saved=%d cache_hits=%d failed=%d missing=%d frontier=%d",
            self.report.processed, self.report.edges_saved, self.report.cache_hits,
            len(self.report.failed), len(self.report.missing), len(self.report.frontier),
        )

        return self.visited


# ============================================================
# STANDALONE CHECK (LIVE: calls BioPortal and WRITES to Neo4j)
# ============================================================

if __name__ == "__main__":

    logging.basicConfig(level=logging.INFO)

    crawler = BFSCrawler(max_depth=2)
    crawler.add_start_concept("233604007", 0)
    crawler.crawl()

    print(crawler.report.to_dict())
