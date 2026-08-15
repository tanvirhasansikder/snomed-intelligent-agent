from database.graph_repository import GraphRepository
from knowledge_graph.graph_builder import GraphBuilder


class QueryProcessor:
    """
    Resolves a user query to a SNOMED CT concept and
    requests the relevant graph expansion.

    This is intentionally deterministic for now.
    The LLM layer will be added later.
    """

    def __init__(self, expansion_depth=2):
        self.repository = GraphRepository()
        self.graph_builder = GraphBuilder(
            default_depth=expansion_depth
        )

        self.expansion_depth = expansion_depth

    # ========================================================
    # Detect SNOMED Concept ID
    # ========================================================

    def is_concept_id(self, query):
        """
        Check whether the query looks like a SNOMED CT ID.
        """

        query = query.strip()

        return (
            query.isdigit()
            and len(query) >= 6
        )

    # ========================================================
    # Search Existing Neo4j Graph
    # ========================================================

    def search_existing_concept(self, name):
        """
        Search Neo4j for an already stored concept by name.
        """

        query = """
        MATCH (c:Concept)
        WHERE toLower(c.name) = toLower($name)

        RETURN
            c.concept_id AS concept_id,
            c.name AS name

        LIMIT 1
        """

        result = self.repository.db.execute_query(
            query,
            {
                "name": name.strip()
            }
        )

        if result:
            return result[0]

        return None

    # ========================================================
    # Search Concept by ID
    # ========================================================

    def get_concept_by_id(self, concept_id):
        """
        Retrieve a concept directly using its SNOMED CT ID.
        """

        query = """
        MATCH (c:Concept {
            concept_id: $concept_id
        })

        RETURN
            c.concept_id AS concept_id,
            c.name AS name

        LIMIT 1
        """

        result = self.repository.db.execute_query(
            query,
            {
                "concept_id": concept_id
            }
        )

        if result:
            return result[0]

        return None

    # ========================================================
    # Resolve Query
    # ========================================================

    def resolve_concept(self, query):
        """
        Resolve a user query to a SNOMED CT concept.

        Resolution order:

        1. SNOMED CT ID
        2. Existing concept name in Neo4j
        """

        query = query.strip()

        if not query:
            return None

        # ----------------------------------------------------
        # Case 1: User provided a SNOMED CT ID
        # ----------------------------------------------------

        if self.is_concept_id(query):

            concept = self.get_concept_by_id(
                query
            )

            if concept:
                return concept

            return {
                "concept_id": query,
                "name": None
            }

        # ----------------------------------------------------
        # Case 2: Search existing graph by name
        # ----------------------------------------------------

        concept = self.search_existing_concept(
            query
        )

        if concept:
            return concept

        return None

    # ========================================================
    # Process Query
    # ========================================================

    def process_query(self, query):
        """
        Process a user query.

        Returns a dictionary containing the
        resolved concept and graph information.
        """

        print("\n" + "=" * 60)
        print("SNOMED Query Processor")
        print("=" * 60)

        print(
            f"User Query: {query}"
        )

        # ----------------------------------------------------
        # Resolve concept
        # ----------------------------------------------------

        concept = self.resolve_concept(
            query
        )

        # ----------------------------------------------------
        # Concept not found
        # ----------------------------------------------------

        if concept is None:

            print(
                "\n❌ Concept not found in the current graph."
            )

            return {
                "success": False,
                "query": query,
                "concept": None
            }

        concept_id = concept["concept_id"]

        concept_name = concept.get(
            "name"
        )

        print(
            f"\n✅ Resolved Concept ID: {concept_id}"
        )

        if concept_name:
            print(
                f"✅ Concept Name: {concept_name}"
            )

        # ----------------------------------------------------
        # Expand the concept
        # ----------------------------------------------------

        print(
            "\n🔍 Expanding relevant SNOMED hierarchy..."
        )

        self.graph_builder.expand_concept(
            concept_id,
            depth=self.expansion_depth
        )

        # ----------------------------------------------------
        # Retrieve final graph information
        # ----------------------------------------------------

        concept_info = self.graph_builder.get_concept(
            concept_id
        )

        parents = self.graph_builder.get_parents(
            concept_id
        )

        children = self.graph_builder.get_children(
            concept_id
        )

        # ----------------------------------------------------
        # Result
        # ----------------------------------------------------

        return {
            "success": True,
            "query": query,
            "concept": concept_info,
            "parents": parents,
            "children": children
        }


# ============================================================
# Test
# ============================================================

if __name__ == "__main__":

    processor = QueryProcessor(
        expansion_depth=2
    )

    result = processor.process_query(
        "Pneumonia"
    )

    print("\n" + "=" * 60)
    print("QUERY RESULT")
    print("=" * 60)

    if result["success"]:

        concept = result["concept"]

        print(
            f"\nConcept: "
            f"{concept['name']} "
            f"({concept['concept_id']})"
        )

        print("\nParents:")

        for parent in result["parents"]:

            print(
                f"- {parent['name']} "
                f"({parent['concept_id']})"
            )

        print("\nChildren:")

        if result["children"]:

            for child in result["children"]:

                print(
                    f"- {child['name']} "
                    f"({child['concept_id']})"
                )

        else:

            print("- None")

    else:

        print(
            "\n❌ Could not resolve the query."
        )