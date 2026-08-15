from database.graph_repository import GraphRepository
from knowledge_graph.graph_builder import GraphBuilder
from crawler.bioportal_client import BioPortalClient


class QueryProcessor:
    """
    Resolves user queries to SNOMED CT concepts.

    Resolution strategy:

    1. Check whether the query is a SNOMED CT ID.
    2. Search Neo4j for an existing concept.
    3. If not found, search BioPortal.
    4. Use the discovered concept ID.
    5. Dynamically expand the requested concept.
    """

    def __init__(self, expansion_depth=2):

        self.repository = GraphRepository()

        self.graph_builder = GraphBuilder(
            default_depth=expansion_depth
        )

        self.bioportal = BioPortalClient()

        self.expansion_depth = expansion_depth

    # ========================================================
    # Detect Concept ID
    # ========================================================

    def is_concept_id(self, query):

        query = query.strip()

        return (
            query.isdigit()
            and len(query) >= 6
        )

    # ========================================================
    # Search Existing Neo4j Graph
    # ========================================================

    def search_existing_concept(self, name):

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
    # Get Concept by ID from Neo4j
    # ========================================================

    def get_concept_by_id(self, concept_id):

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
    # Resolve Using BioPortal
    # ========================================================

    def resolve_from_bioportal(self, query):

        print(
            "\n🔎 Concept not found in Neo4j."
        )

        print(
            "Searching BioPortal..."
        )

        results = self.bioportal.search_concept(
            query
        )

        if not results:

            print(
                "\n❌ No BioPortal results found."
            )

            return None

        print(
            f"\nFound {len(results)} BioPortal result(s)."
        )

        # ----------------------------------------------------
        # First result for now
        # ----------------------------------------------------

        best_result = results[0]

        print(
            "\nSelected Concept:"
        )

        print(
            "Name:",
            best_result["name"]
        )

        print(
            "ID:",
            best_result["concept_id"]
        )

        return best_result

    # ========================================================
    # Resolve Concept
    # ========================================================

    def resolve_concept(self, query):

        query = query.strip()

        if not query:

            return None

        # ----------------------------------------------------
        # Case 1: SNOMED ID
        # ----------------------------------------------------

        if self.is_concept_id(query):

            concept = self.get_concept_by_id(
                query
            )

            if concept:

                return concept

            print(
                "\nConcept ID not currently in Neo4j."
            )

            print(
                "Checking BioPortal..."
            )

            concept_data = self.bioportal.get_concept(
                query
            )

            if not concept_data:

                return None

            return {
                "concept_id": query,
                "name": (
                    concept_data.get(
                        "prefLabel"
                    )
                    or query
                )
            }

        # ----------------------------------------------------
        # Case 2: Existing Neo4j concept
        # ----------------------------------------------------

        concept = self.search_existing_concept(
            query
        )

        if concept:

            print(
                "\n✅ Concept found in Neo4j."
            )

            return concept

        # ----------------------------------------------------
        # Case 3: Search BioPortal
        # ----------------------------------------------------

        return self.resolve_from_bioportal(
            query
        )

    # ========================================================
    # Process Query
    # ========================================================

    def process_query(self, query):

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

        if concept is None:

            print(
                "\n❌ Unable to resolve concept."
            )

            return {
                "success": False,
                "query": query,
                "concept": None,
                "parents": [],
                "children": []
            }

        concept_id = concept[
            "concept_id"
        ]

        concept_name = concept.get(
            "name"
        )

        print(
            "\n✅ Resolved Concept"
        )

        print(
            "Concept ID :",
            concept_id
        )

        print(
            "Concept Name:",
            concept_name
        )

        # ----------------------------------------------------
        # Expand graph
        # ----------------------------------------------------

        print(
            "\n🔍 Expanding relevant SNOMED hierarchy..."
        )

        self.graph_builder.expand_concept(
            concept_id,
            depth=self.expansion_depth
        )

        # ----------------------------------------------------
        # Retrieve graph information
        # ----------------------------------------------------

        concept_info = (
            self.graph_builder.get_concept(
                concept_id
            )
        )

        parents = (
            self.graph_builder.get_parents(
                concept_id
            )
        )

        children = (
            self.graph_builder.get_children(
                concept_id
            )
        )

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

    # --------------------------------------------------------
    # Test with a concept that may NOT already exist
    # --------------------------------------------------------

    result = processor.process_query(
        "Asthma"
    )

    print("\n" + "=" * 60)
    print("QUERY RESULT")
    print("=" * 60)

    if result["success"]:

        concept = result["concept"]

        print(
            "\nConcept:",
            concept["name"],
            f"({concept['concept_id']})"
        )

        print("\nParents:")

        if result["parents"]:

            for parent in result["parents"]:

                print(
                    f"- {parent['name']} "
                    f"({parent['concept_id']})"
                )

        else:

            print("- None")

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
            "\n❌ Query could not be resolved."
        )