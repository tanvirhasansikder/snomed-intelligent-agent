"""
Coordinates on-demand expansion of the local SNOMED CT subgraph.

* expand_concept()  - upward BFS (parents, grandparents, ...) via BFSCrawler
* expand_children() - one level downward: the concept's DIRECT children,
                      paged, with completeness recorded
* get_*()           - read what is stored locally

The builder, its crawler and the query processor share one repository and
one BioPortal client, so a query opens at most one Neo4j driver.
"""

import logging

from config.settings import DATA_SOURCE
from crawler.bfs_crawler import BFSCrawler, utc_now
from crawler.bioportal_client import BioPortalClient, BioPortalError
from database.graph_repository import GraphRepository

logger = logging.getLogger(__name__)


class GraphBuilder:
    """
    Coordinates SNOMED CT graph expansion.

    The GraphBuilder provides a higher-level interface between
    the query processor, the BFS crawler, and Neo4j.
    """

    def __init__(self, default_depth=2, repository=None, client=None, use_cache=True, source_version=None):
        self.repository = repository if repository is not None else GraphRepository()
        self.default_depth = default_depth
        self.use_cache = use_cache
        self.source_version = source_version
        self._client = client
        self.last_report = None

    @property
    def client(self):
        """BioPortal client, created on first use (not needed for reads)."""
        if self._client is None:
            self._client = BioPortalClient()
        return self._client

    # ========================================================
    # Check Whether Concept Exists
    # ========================================================

    def concept_exists(self, concept_id):
        """
        Check whether a SNOMED CT concept already exists in Neo4j.
        """

        return self.repository.concept_exists(
            concept_id
        )

    # ========================================================
    # Expand Concept (upward)
    # ========================================================

    def expand_concept(self, concept_id, depth=None):
        """
        Dynamically expand a SNOMED CT concept upward.

        Parameters
        ----------
        concept_id : str
            SNOMED CT concept ID.

        depth : int, optional
            Maximum number of hierarchy levels to expand.

        Returns
        -------
        bool
            True if the start concept was retrieved (or already stored) and
            saved. Failures further up are recorded in ``self.last_report``.
            False if the start concept could not be retrieved.
        """

        if depth is None:
            depth = self.default_depth

        crawler = BFSCrawler(
            max_depth=depth,
            client=self.client,
            repository=self.repository,
            use_cache=self.use_cache,
            source_version=self.source_version,
        )

        crawler.add_start_concept(concept_id)
        crawler.crawl()

        self.last_report = crawler.report

        start_failed = concept_id in crawler.report.failed and not self.repository.concept_exists(concept_id)
        start_missing = concept_id in crawler.report.missing

        return not (start_failed or start_missing)

    # ========================================================
    # Expand direct children (one level downward)
    # ========================================================

    def expand_children(self, concept_id, max_pages=5, pagesize=100):
        """
        Retrieve and store the DIRECT children of ``concept_id``.

        Returns a coverage dict:
            status     complete | partial | failed
            count      children stored by this retrieval
            from_cache True if a previous complete retrieval was reused
            error      message when status == failed
        """

        if self.use_cache:
            previous = self.repository.get_expansion_status(concept_id, "down")
            if previous and previous.get("status") == "complete":
                return dict(previous, from_cache=True)

        try:
            fetched = self.client.fetch_children(concept_id, max_pages=max_pages, pagesize=pagesize)
        except BioPortalError as exc:
            self.repository.mark_expansion(
                concept_id, "down", "failed", retrieved_at=utc_now(), source=DATA_SOURCE,
                source_version=self.source_version, error=str(exc),
            )
            logger.warning("Child retrieval failed for %s: %s", concept_id, exc)
            return {"status": "failed", "count": 0, "from_cache": False, "error": str(exc)}

        children = [
            c for c in fetched["children"]
            if isinstance(c.get("concept_id"), str) and c["concept_id"].isdigit() and c["concept_id"] != concept_id
        ]

        if children:
            self.repository.save_children(concept_id, children)

        status = "complete" if fetched["complete"] else "partial"
        record = {
            "status": status,
            "retrieved_at": utc_now(),
            "source": DATA_SOURCE,
            "source_version": self.source_version,
            "count": len(children),
            "error": None,
        }
        self.repository.mark_expansion(concept_id, "down", status, **{k: v for k, v in record.items() if k != "status"})

        return dict(record, from_cache=False)

    # ========================================================
    # Reads (local graph)
    # ========================================================

    def get_concept(self, concept_id):
        """
        Retrieve a concept and its locally stored direct parents.

        Fixed: the original OPTIONAL MATCH + collect({...}) returned
        [{concept_id: None, name: None}] for a concept with no parents.
        """

        concept = self.repository.get_concept(concept_id)

        if concept is None:
            return None

        return dict(concept, parents=self.get_parents(concept_id))

    def get_parents(self, concept_id):
        """
        Retrieve direct parent concepts from Neo4j.
        """

        return self.repository.get_parents(concept_id)

    def get_children(self, concept_id):
        """
        Retrieve direct child concepts stored in Neo4j.

        Only children that have been retrieved are stored; check
        get_coverage(concept_id)["children"] before treating an empty list
        as "this concept has no children".
        """

        return self.repository.get_children(concept_id)

    def get_ancestors(self, concept_id, max_depth):
        """Locally stored ancestors within max_depth IS_A steps."""

        return self.repository.get_ancestors(concept_id, max_depth=max_depth)

    def get_coverage(self, concept_id):
        """Retrieval provenance for parents and children of a concept."""

        return self.repository.get_coverage(concept_id)

    # ========================================================
    # Count Graph
    # ========================================================

    def get_graph_statistics(self):
        """
        Return basic statistics about the current SNOMED graph.
        """

        concepts = self.repository.count_concepts()
        relationships = self.repository.count_relationships()

        return {
            "concepts": concepts,
            "relationships": relationships
        }


# ============================================================
# STANDALONE CHECK (LIVE: calls BioPortal and WRITES to Neo4j)
# ============================================================

if __name__ == "__main__":

    logging.basicConfig(level=logging.INFO)

    builder = GraphBuilder(default_depth=2)

    print("Expanded:", builder.expand_concept("233604007"))
    print("Report:", builder.last_report.to_dict())
    print("Children:", builder.expand_children("233604007"))
    print("Concept:", builder.get_concept("233604007"))
    print("Coverage:", builder.get_coverage("233604007"))
    print("Statistics:", builder.get_graph_statistics())
