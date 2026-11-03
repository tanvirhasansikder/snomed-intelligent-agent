"""
Resolves a user query to a SNOMED CT concept, expands the local subgraph
around it, and returns hierarchy information together with how complete
that information is.

Result dictionary
-----------------
Keys kept from the original: success, query, concept, parents, children.
Added keys:

    error_type  None | invalid_input | invalid_identifier | not_found |
                source_unavailable | database_error | configuration_error |
                unexpected_error
    error       short message (never contains credentials)
    resolution  {"method", "ambiguous"}; method is one of local_id,
                bioportal_id, local_name, bioportal_exact_label,
                bioportal_top_result
    candidates  BioPortal search results considered (name queries only)
    ancestors   locally stored ancestors within the expansion depth
    coverage    {"parents":   {"status": complete|failed|not_retrieved, ...},
                 "children":  {"status": complete|partial|failed|not_retrieved, ...},
                 "ancestors": {"status": complete_to_depth|partial, "depth", "frontier"}}
    warnings    human-readable notes about failed or partial retrieval
    crawl       the crawl report (counts, failures, API requests)

An empty parents/children list must be read together with coverage: only
status "complete" means "SNOMED CT (as served by BioPortal) has none".
"""

import logging

from neo4j.exceptions import DriverError, Neo4jError

from crawler.bioportal_client import BioPortalConfigurationError, BioPortalError
from database.neo4j_connection import Neo4jConfigurationError
from knowledge_graph.graph_builder import GraphBuilder

logger = logging.getLogger(__name__)

MAX_QUERY_LENGTH = 200
MAX_EXPANSION_DEPTH = 10
MAX_CANDIDATES = 10

# Verhoeff check-digit tables (used by SNOMED CT identifiers).
_VERHOEFF_D = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9), (1, 2, 3, 4, 0, 6, 7, 8, 9, 5),
    (2, 3, 4, 0, 1, 7, 8, 9, 5, 6), (3, 4, 0, 1, 2, 8, 9, 5, 6, 7),
    (4, 0, 1, 2, 3, 9, 5, 6, 7, 8), (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
    (6, 5, 9, 8, 7, 1, 0, 4, 3, 2), (7, 6, 5, 9, 8, 2, 1, 0, 4, 3),
    (8, 7, 6, 5, 9, 3, 2, 1, 0, 4), (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)
_VERHOEFF_P = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9), (1, 5, 7, 6, 2, 8, 3, 0, 9, 4),
    (5, 8, 0, 3, 7, 9, 6, 1, 4, 2), (8, 9, 1, 6, 0, 4, 3, 5, 2, 7),
    (9, 4, 5, 3, 1, 2, 6, 8, 7, 0), (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5), (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)
# Partition identifier (2nd and 3rd digits from the right): 00/10 = concept.
_CONCEPT_PARTITIONS = {"00", "10"}


class _QueryError(Exception):
    def __init__(self, error_type, message):
        super().__init__(message)
        self.error_type = error_type


class QueryProcessor:
    """
    Resolves user queries to SNOMED CT concepts.

    Resolution strategy:

    1. If the query is a SNOMED CT identifier (6-18 digits, valid check
       digit, concept partition): look it up locally, then on BioPortal.
    2. Otherwise: exact (case-insensitive) label match in Neo4j.
    3. Otherwise: BioPortal search; prefer an exact label match, else take
       the top-ranked result and flag the resolution as ambiguous.
    4. Expand the hierarchy upward to ``expansion_depth`` levels and,
       if ``fetch_children`` is True, retrieve the direct children.
    """

    def __init__(self, expansion_depth=2, repository=None, graph_builder=None, bioportal=None,
                 fetch_children=False, max_child_pages=5):

        if isinstance(expansion_depth, bool) or not isinstance(expansion_depth, int):
            raise TypeError("expansion_depth must be an integer")
        if not 1 <= expansion_depth <= MAX_EXPANSION_DEPTH:
            raise ValueError(f"expansion_depth must be between 1 and {MAX_EXPANSION_DEPTH}")

        if graph_builder is None:
            graph_builder = GraphBuilder(
                default_depth=expansion_depth,
                repository=repository,
                client=bioportal,
            )

        self.graph_builder = graph_builder
        self.repository = repository if repository is not None else graph_builder.repository
        self._bioportal = bioportal
        self.expansion_depth = expansion_depth
        self.fetch_children = fetch_children
        self.max_child_pages = max_child_pages

    @property
    def bioportal(self):
        """BioPortal client, shared with the graph builder and created on first use."""
        if self._bioportal is None:
            self._bioportal = self.graph_builder.client
        return self._bioportal

    # ========================================================
    # Identifier checks
    # ========================================================

    def is_concept_id(self, query):
        """True for a string of 6-18 digits (the SCTID length range)."""

        if not isinstance(query, str):
            return False

        query = query.strip()

        return query.isdigit() and 6 <= len(query) <= 18

    @staticmethod
    def is_valid_sctid(value):
        """6-18 digits with a valid Verhoeff check digit."""

        if not isinstance(value, str):
            return False
        value = value.strip()
        if not (value.isdigit() and 6 <= len(value) <= 18):
            return False

        check = 0
        for position, digit in enumerate(reversed(value)):
            check = _VERHOEFF_D[check][_VERHOEFF_P[position % 8][int(digit)]]
        return check == 0

    @staticmethod
    def is_concept_partition(value):
        """True if the SCTID's partition identifier denotes a concept."""

        return isinstance(value, str) and value[-3:-1] in _CONCEPT_PARTITIONS

    # ========================================================
    # Local lookups (kept from the original API)
    # ========================================================

    def search_existing_concept(self, name):
        """Exact, case-insensitive label match in the local graph."""

        return self.repository.find_concept_by_exact_name(name)

    def get_concept_by_id(self, concept_id):
        """Concept stored locally, or None."""

        return self.repository.get_concept(concept_id)

    # ========================================================
    # Resolution
    # ========================================================

    def _resolve(self, text):
        """Return a resolution dict or raise _QueryError / source errors."""

        if self.is_concept_id(text):

            if not self.is_valid_sctid(text):
                raise _QueryError(
                    "invalid_identifier",
                    f"'{text}' is not a valid SNOMED CT identifier (check digit does not match).",
                )
            if not self.is_concept_partition(text):
                raise _QueryError(
                    "invalid_identifier",
                    f"'{text}' is a SNOMED CT identifier but not a concept ID "
                    "(it identifies a description or relationship).",
                )

            local = self.get_concept_by_id(text)
            if local:
                return {"concept": local, "method": "local_id", "ambiguous": False, "candidates": []}

            remote = self.bioportal.get_concept(text, raise_errors=True)
            if not remote:
                raise _QueryError("not_found", f"No SNOMED CT concept with ID {text} on BioPortal.")

            concept = {"concept_id": text, "name": remote.get("prefLabel") or text}
            return {"concept": concept, "method": "bioportal_id", "ambiguous": False, "candidates": []}

        local = self.search_existing_concept(text)
        if local:
            return {"concept": local, "method": "local_name", "ambiguous": False, "candidates": []}

        results = self.bioportal.search_concept(text, raise_errors=True)
        if not results:
            raise _QueryError("not_found", f"No SNOMED CT concept matched '{text}'.")

        candidates = results[:MAX_CANDIDATES]
        wanted = text.casefold()
        exact = next(
            (r for r in candidates if isinstance(r.get("name"), str) and r["name"].casefold() == wanted),
            None,
        )
        if exact is not None:
            return {"concept": dict(exact), "method": "bioportal_exact_label",
                    "ambiguous": False, "candidates": candidates}

        return {"concept": dict(candidates[0]), "method": "bioportal_top_result",
                "ambiguous": len(candidates) > 1, "candidates": candidates}

    def resolve_from_bioportal(self, query):
        """Top BioPortal match (exact label preferred) or None. Kept for compatibility."""

        try:
            results = self.bioportal.search_concept(query, raise_errors=True)
        except BioPortalError:
            return None
        if not results:
            return None
        wanted = query.strip().casefold()
        return next((r for r in results if str(r.get("name", "")).casefold() == wanted), results[0])

    def resolve_concept(self, query):
        """Resolved concept dict or None (original signature)."""

        if not isinstance(query, str) or not query.strip():
            return None
        try:
            return self._resolve(query.strip())["concept"]
        except (_QueryError, BioPortalError):
            return None

    # ========================================================
    # Process Query
    # ========================================================

    def _empty_result(self, query):
        return {
            "success": False,
            "query": query,
            "concept": None,
            "parents": [],
            "children": [],
            "ancestors": [],
            "error": None,
            "error_type": None,
            "resolution": None,
            "candidates": [],
            "coverage": {},
            "warnings": [],
            "crawl": None,
        }

    def process_query(self, query):

        result = self._empty_result(query)

        try:
            if not isinstance(query, str) or not query.strip():
                raise _QueryError("invalid_input", "Please enter a concept name or SNOMED CT ID.")

            text = query.strip()

            if len(text) > MAX_QUERY_LENGTH:
                raise _QueryError("invalid_input", f"Query is longer than {MAX_QUERY_LENGTH} characters.")

            resolution = self._resolve(text)
            result["resolution"] = {"method": resolution["method"], "ambiguous": resolution["ambiguous"]}
            result["candidates"] = resolution["candidates"]

            concept = resolution["concept"]
            concept_id = concept["concept_id"]

            # Store a label learnt during resolution so the crawler does not
            # fetch the same concept again.
            if resolution["method"].startswith("bioportal") and concept.get("name"):
                self.repository.save_concept(concept_id, concept["name"])

            expanded = self.graph_builder.expand_concept(concept_id, depth=self.expansion_depth)
            report = self.graph_builder.last_report
            result["crawl"] = report.to_dict() if report is not None else None

            if not expanded and not self.repository.concept_exists(concept_id):
                if report is not None and concept_id in report.failed:
                    raise _QueryError("source_unavailable", report.failed[concept_id])
                raise _QueryError("not_found", f"No SNOMED CT concept with ID {concept_id} on BioPortal.")

            children_coverage = None
            if self.fetch_children:
                children_coverage = self.graph_builder.expand_children(
                    concept_id, max_pages=self.max_child_pages)

            result["concept"] = self.graph_builder.get_concept(concept_id)
            result["parents"] = self.graph_builder.get_parents(concept_id)
            result["children"] = self.graph_builder.get_children(concept_id)
            result["ancestors"] = self.graph_builder.get_ancestors(concept_id, self.expansion_depth)
            result["coverage"] = self._build_coverage(concept_id, report, children_coverage)
            result["warnings"] = self._warnings(result["coverage"], result["resolution"])
            result["success"] = True

        except _QueryError as exc:
            result["error_type"], result["error"] = exc.error_type, str(exc)
        except BioPortalError as exc:
            result["error_type"], result["error"] = "source_unavailable", str(exc)
        except (Neo4jConfigurationError, BioPortalConfigurationError) as exc:
            result["error_type"], result["error"] = "configuration_error", str(exc)
        except (Neo4jError, DriverError) as exc:
            result["error_type"] = "database_error"
            result["error"] = f"{type(exc).__name__}: {exc}"
        except Exception as exc:  # keep the UI responsive; details go to the log
            logger.exception("Unexpected error while processing query")
            result["error_type"], result["error"] = "unexpected_error", type(exc).__name__

        if result["error_type"]:
            logger.info("Query %r failed: %s", query, result["error_type"])

        return result

    # ========================================================
    # Coverage reporting
    # ========================================================

    def _build_coverage(self, concept_id, report, children_coverage):
        stored = self.graph_builder.get_coverage(concept_id) or {}

        parents = stored.get("parents") or {"status": "not_retrieved"}

        if children_coverage is not None:
            children = dict(children_coverage)
        else:
            children = stored.get("children") or {"status": "not_retrieved"}

        failed = dict(report.failed) if report is not None else {}
        missing = list(report.missing) if report is not None else []
        ancestors = {
            "status": "partial" if (failed or missing) else "complete_to_depth",
            "depth": self.expansion_depth,
            "frontier": sorted(report.frontier) if report is not None else [],
            "failed": sorted(failed),
        }

        return {"parents": parents, "children": children, "ancestors": ancestors}

    @staticmethod
    def _warnings(coverage, resolution):
        notes = []
        parents = coverage.get("parents", {})
        children = coverage.get("children", {})
        ancestors = coverage.get("ancestors", {})

        if parents.get("status") == "failed":
            notes.append("Parent retrieval from BioPortal failed; the concept may have parents that are not shown.")
        if children.get("status") == "failed":
            notes.append("Child retrieval from BioPortal failed; children shown (if any) are only those stored earlier.")
        if children.get("status") == "partial":
            notes.append("Only the first pages of children were retrieved; more children exist.")
        if ancestors.get("status") == "partial":
            notes.append("Some ancestor levels could not be retrieved: " + ", ".join(ancestors.get("failed", [])))
        if resolution and resolution.get("ambiguous"):
            notes.append("No exact label match; the top BioPortal result was used. Check the candidates.")
        return notes


# ============================================================
# STANDALONE CHECK (LIVE: calls BioPortal and WRITES to Neo4j)
# ============================================================

if __name__ == "__main__":

    import json

    logging.basicConfig(level=logging.INFO)

    processor = QueryProcessor(expansion_depth=2, fetch_children=True)
    outcome = processor.process_query("Asthma")
    print(json.dumps({k: outcome[k] for k in ("success", "error_type", "concept", "parents", "coverage")},
                     indent=2, default=str))
