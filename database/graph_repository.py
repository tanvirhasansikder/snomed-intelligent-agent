"""
Neo4j persistence for the on-demand SNOMED CT subgraph.

Graph model
-----------
(:Concept {concept_id, name})-[:IS_A]->(:Concept)   child -> parent

Retrieval provenance (written by the crawler / graph builder) is stored on
the concept node, one set of properties per direction:

    parents_status, parents_retrieved_at, parents_source,
    parents_source_version, parents_count, parents_error
    children_status, children_retrieved_at, ... (same fields)

status is one of: complete | partial | failed. A missing status means that
direction has never been retrieved for that concept, so an empty result in
that direction is *unknown*, not "none exist".
"""

import logging

from database.neo4j_connection import Neo4jConnection

logger = logging.getLogger(__name__)

#: Upper bound for variable-length traversals (literal in the query text).
MAX_TRAVERSAL_DEPTH = 20

_DIRECTION_PREFIX = {"up": "parents", "down": "children"}
_STATUSES = {"complete", "partial", "failed"}
_COVERAGE_FIELDS = ("status", "retrieved_at", "source", "source_version", "count", "error")


def _validate_depth(max_depth):
    if isinstance(max_depth, bool) or not isinstance(max_depth, int):
        raise TypeError("max_depth must be an integer")
    if not 1 <= max_depth <= MAX_TRAVERSAL_DEPTH:
        raise ValueError(f"max_depth must be between 1 and {MAX_TRAVERSAL_DEPTH}")
    return max_depth


class GraphRepository:
    """
    Handles all Neo4j database operations for the SNOMED Knowledge Graph.
    """

    def __init__(self, db=None):
        self.db = db or Neo4jConnection()

    # ==========================================================
    # CONCEPT OPERATIONS
    # ==========================================================

    def save_concept(self, concept_id, name):
        """
        Create or update a SNOMED concept node.
        """

        query = """
        MERGE (c:Concept {concept_id: $concept_id})
        SET c.name = $name
        """

        self.db.execute_query(
            query,
            {
                "concept_id": concept_id,
                "name": name
            }
        )

        logger.debug("Saved concept %s (%s)", concept_id, name)

        return True

    def get_concept(self, concept_id):
        """
        Retrieve a concept by SNOMED CT ID.
        """

        query = """
        MATCH (c:Concept {concept_id: $concept_id})
        RETURN c.concept_id AS concept_id,
               c.name AS name
        """

        result = self.db.execute_query(
            query,
            {
                "concept_id": concept_id
            }
        )

        if not result:
            return None

        return result[0]

    def find_concept_by_exact_name(self, name):
        """
        Case-insensitive exact match on the stored preferred label.
        Returns one concept or None. Synonyms are not stored, so they do not match.
        """

        query = """
        MATCH (c:Concept)
        WHERE toLower(c.name) = toLower($name)
        RETURN c.concept_id AS concept_id,
               c.name AS name
        ORDER BY c.concept_id
        LIMIT 1
        """

        result = self.db.execute_query(query, {"name": name.strip()})

        return result[0] if result else None

    def search_concepts(self, keyword):
        """
        Search concepts by name.

        Uses case-insensitive partial matching.
        """

        query = """
        MATCH (c:Concept)
        WHERE toLower(c.name) CONTAINS toLower($keyword)
        RETURN c.concept_id AS concept_id,
               c.name AS name
        ORDER BY c.name
        LIMIT 20
        """

        return self.db.execute_query(
            query,
            {
                "keyword": keyword
            }
        )

    def concept_exists(self, concept_id):
        """
        Check whether a concept already exists.
        """

        query = """
        MATCH (c:Concept {concept_id: $concept_id})
        RETURN COUNT(c) AS count
        """

        result = self.db.execute_query(
            query,
            {
                "concept_id": concept_id
            }
        )

        return bool(result) and result[0]["count"] > 0

    # ==========================================================
    # RELATIONSHIP OPERATIONS
    # ==========================================================

    def save_relationship(self, child_id, parent_id):
        """
        Create an IS_A relationship (child)-[:IS_A]->(parent).

        Returns True if both concepts exist and the edge now exists,
        False if either endpoint is missing (nothing is written then).
        """

        query = """
        MATCH (child:Concept {concept_id: $child_id})
        MATCH (parent:Concept {concept_id: $parent_id})
        MERGE (child)-[:IS_A]->(parent)
        RETURN count(*) AS linked
        """

        result = self.db.execute_query(
            query,
            {
                "child_id": child_id,
                "parent_id": parent_id
            }
        )

        linked = bool(result) and result[0].get("linked", 0) > 0

        if not linked:
            logger.warning("IS_A edge %s -> %s not saved: endpoint missing", child_id, parent_id)

        return linked

    def save_children(self, parent_id, children):
        """
        Save direct children of ``parent_id`` in one batched query.

        ``children`` is a list of {'concept_id', 'name'}. Returns how many
        child edges exist afterwards (0 if the parent node is missing).
        """

        query = """
        MATCH (parent:Concept {concept_id: $parent_id})
        UNWIND $children AS child_row
        MERGE (child:Concept {concept_id: child_row.concept_id})
        SET child.name = child_row.name
        MERGE (child)-[:IS_A]->(parent)
        RETURN count(child) AS linked
        """

        rows = [{"concept_id": c["concept_id"], "name": c["name"]} for c in children]

        result = self.db.execute_query(query, {"parent_id": parent_id, "children": rows})

        return result[0].get("linked", 0) if result else 0

    # ==========================================================
    # PARENT / CHILD OPERATIONS (direct neighbours in the LOCAL graph)
    # ==========================================================

    def get_parents(self, concept_id):
        """
        Get direct parents of a concept that are stored locally.

        Relationship:
            Concept -> IS_A -> Parent
        """

        query = """
        MATCH (child:Concept {concept_id: $concept_id})
              -[:IS_A]->
              (parent:Concept)

        RETURN parent.concept_id AS concept_id,
               parent.name AS name
        ORDER BY parent.name
        """

        return self.db.execute_query(
            query,
            {
                "concept_id": concept_id
            }
        )

    def get_children(self, concept_id):
        """
        Get direct children of a concept that are stored locally.
        Check get_coverage() before treating an empty list as "no children".

        Relationship:
            Child -> IS_A -> Concept
        """

        query = """
        MATCH (child:Concept)
              -[:IS_A]->
              (parent:Concept {concept_id: $concept_id})

        RETURN child.concept_id AS concept_id,
               child.name AS name
        ORDER BY child.name
        """

        return self.db.execute_query(
            query,
            {
                "concept_id": concept_id
            }
        )

    # ==========================================================
    # HIERARCHY OPERATIONS
    # ==========================================================

    def get_ancestors(self, concept_id, max_depth=10):
        """
        Get locally stored ancestors within ``max_depth`` IS_A steps.

        Fixed: the original used ``[:IS_A*1..$max_depth]``; Cypher does not
        accept a parameter as a variable-length bound. The depth is now a
        validated integer written into the query text.
        """

        depth = _validate_depth(max_depth)

        query = f"""
        MATCH (start:Concept {{concept_id: $concept_id}})
              -[:IS_A*1..{depth}]->
              (ancestor:Concept)

        RETURN DISTINCT
               ancestor.concept_id AS concept_id,
               ancestor.name AS name
        ORDER BY name
        """

        return self.db.execute_query(
            query,
            {
                "concept_id": concept_id
            }
        )

    def get_descendants(self, concept_id, max_depth=10):
        """
        Get locally stored descendants within ``max_depth`` IS_A steps.
        The local graph usually holds only some descendants.
        """

        depth = _validate_depth(max_depth)

        query = f"""
        MATCH (descendant:Concept)
              -[:IS_A*1..{depth}]->
              (start:Concept {{concept_id: $concept_id}})

        RETURN DISTINCT
               descendant.concept_id AS concept_id,
               descendant.name AS name
        ORDER BY name
        """

        return self.db.execute_query(
            query,
            {
                "concept_id": concept_id
            }
        )

    # ==========================================================
    # PATH OPERATIONS
    # ==========================================================

    def find_upward_path(self, descendant_id, ancestor_id, max_depth=MAX_TRAVERSAL_DEPTH):
        """
        Shortest *hierarchy* path descendant -> ... -> ancestor following IS_A
        upward only. Returns a list of {'concept_id', 'name'} or None if the
        local graph contains no such path (which does not prove that SNOMED CT
        has none).
        """

        depth = _validate_depth(max_depth)

        if descendant_id == ancestor_id:
            concept = self.get_concept(descendant_id)
            return [concept] if concept else None

        query = f"""
        MATCH (start:Concept {{concept_id: $start_id}})
        MATCH (end:Concept {{concept_id: $end_id}})
        MATCH path = shortestPath((start)-[:IS_A*1..{depth}]->(end))
        RETURN [node IN nodes(path) |
                {{concept_id: node.concept_id, name: node.name}}] AS path
        """

        result = self.db.execute_query(query, {"start_id": descendant_id, "end_id": ancestor_id})

        if not result:
            return None

        return result[0]["path"]

    def find_shortest_path(self, start_id, end_id):
        """
        Shortest connection between two concepts ignoring edge direction.

        Note: this can go up and then down (via a shared ancestor), so it is a
        "related through the graph" path, NOT a hierarchy path. Use
        find_upward_path() for "X is a kind of Y" questions.
        """

        if start_id == end_id:
            concept = self.get_concept(start_id)
            return [concept] if concept else None

        query = """
        MATCH (start:Concept {concept_id: $start_id})
        MATCH (end:Concept {concept_id: $end_id})

        MATCH path = shortestPath(
            (start)-[:IS_A*]-(end)
        )

        RETURN
            [node IN nodes(path) |
                {
                    concept_id: node.concept_id,
                    name: node.name
                }
            ] AS path
        """

        result = self.db.execute_query(
            query,
            {
                "start_id": start_id,
                "end_id": end_id
            }
        )

        if not result:
            return None

        return result[0]["path"]

    # ==========================================================
    # RETRIEVAL PROVENANCE / COVERAGE
    # ==========================================================

    def mark_expansion(self, concept_id, direction, status, retrieved_at=None, source=None,
                       source_version=None, count=None, error=None):
        """
        Record the outcome of retrieving a concept's parents ("up") or
        children ("down"). Property names come from a fixed whitelist.
        Returns True if the concept node exists.
        """

        if direction not in _DIRECTION_PREFIX:
            raise ValueError(f"direction must be one of {sorted(_DIRECTION_PREFIX)}")
        if status not in _STATUSES:
            raise ValueError(f"status must be one of {sorted(_STATUSES)}")

        prefix = _DIRECTION_PREFIX[direction]

        query = f"""
        MATCH (c:Concept {{concept_id: $concept_id}})
        SET c.{prefix}_status = $status,
            c.{prefix}_retrieved_at = $retrieved_at,
            c.{prefix}_source = $source,
            c.{prefix}_source_version = $source_version,
            c.{prefix}_count = $count,
            c.{prefix}_error = $error
        RETURN count(c) AS n
        """

        result = self.db.execute_query(query, {
            "concept_id": concept_id,
            "status": status,
            "retrieved_at": retrieved_at,
            "source": source,
            "source_version": source_version,
            "count": count,
            "error": (str(error)[:300] if error else None),
        })

        return bool(result) and result[0].get("n", 0) > 0

    def get_coverage(self, concept_id):
        """
        Return {'parents': {...} | None, 'children': {...} | None}.
        None means that direction was never retrieved for this concept.
        """

        returns = ",\n               ".join(
            f"c.{prefix}_{field} AS {prefix}_{field}"
            for prefix in ("parents", "children")
            for field in _COVERAGE_FIELDS
        )

        query = f"""
        MATCH (c:Concept {{concept_id: $concept_id}})
        RETURN {returns}
        """

        result = self.db.execute_query(query, {"concept_id": concept_id})

        coverage = {"parents": None, "children": None}
        if not result:
            return coverage

        row = result[0]
        for prefix in ("parents", "children"):
            if row.get(f"{prefix}_status") is not None:
                coverage[prefix] = {field: row.get(f"{prefix}_{field}") for field in _COVERAGE_FIELDS}

        return coverage

    def get_expansion_status(self, concept_id, direction):
        """Coverage record for one direction ("up" or "down"), or None."""

        if direction not in _DIRECTION_PREFIX:
            raise ValueError(f"direction must be one of {sorted(_DIRECTION_PREFIX)}")

        return self.get_coverage(concept_id)[_DIRECTION_PREFIX[direction]]

    # ==========================================================
    # DATABASE STATISTICS
    # ==========================================================

    def count_concepts(self):
        """
        Count Concept nodes.
        """

        query = """
        MATCH (c:Concept)
        RETURN COUNT(c) AS total
        """

        result = self.db.execute_query(query)

        return result[0]["total"]

    def count_relationships(self):
        """
        Count IS_A relationships.
        """

        query = """
        MATCH ()-[r:IS_A]->()
        RETURN COUNT(r) AS total
        """

        result = self.db.execute_query(query)

        return result[0]["total"]

    # ==========================================================
    # DATABASE MANAGEMENT
    # ==========================================================

    def ensure_constraints(self):
        """
        Create a uniqueness constraint on Concept.concept_id if it does not
        exist. Not destructive, but it fails if duplicate concept_id values
        are already stored. Never called automatically; run it yourself
        (see README).
        """

        query = """
        CREATE CONSTRAINT concept_id_unique IF NOT EXISTS
        FOR (c:Concept) REQUIRE c.concept_id IS UNIQUE
        """

        self.db.execute_query(query)

    def clear_database(self, confirm=False):
        """
        Delete EVERY node and relationship in the configured database.

        Requires ``confirm=True`` (added as a safety guard; the original
        deleted everything on a bare call).
        """

        if confirm is not True:
            raise ValueError("clear_database() deletes all data; call clear_database(confirm=True) to proceed")

        query = """
        MATCH (n)
        DETACH DELETE n
        """

        self.db.execute_query(query)

        logger.warning("Database cleared.")

    def close(self):
        """
        Close Neo4j connection.
        """

        self.db.close()


# ==============================================================
# Standalone check (reads from your configured Neo4j; no writes)
# ==============================================================

if __name__ == "__main__":

    repo = GraphRepository()

    print("Concept:", repo.get_concept("233604007"))
    print("Parents:", repo.get_parents("233604007"))
    print("Children (local only):", repo.get_children("233604007"))
    print("Ancestors (local, 5 levels):", repo.get_ancestors("233604007", max_depth=5))
    print("Coverage:", repo.get_coverage("233604007"))
    print("Concepts:", repo.count_concepts(), "IS_A edges:", repo.count_relationships())

    repo.close()
