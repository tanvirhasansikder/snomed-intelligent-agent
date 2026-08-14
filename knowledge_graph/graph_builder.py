from database.graph_repository import GraphRepository
from crawler.bfs_crawler import BFSCrawler


class GraphBuilder:
    """
    Coordinates SNOMED CT graph expansion.

    The GraphBuilder provides a higher-level interface between
    the future intelligent agent, the BFS crawler, and Neo4j.
    """

    def __init__(self, default_depth=2):
        self.repository = GraphRepository()
        self.default_depth = default_depth

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
    # Expand Concept
    # ========================================================

    def expand_concept(self, concept_id, depth=None):
        """
        Dynamically expand a SNOMED CT concept.

        Parameters
        ----------
        concept_id : str
            SNOMED CT concept ID.

        depth : int, optional
            Maximum number of hierarchy levels to expand.

        Returns
        -------
        bool
            True when the expansion completes successfully.
        """

        if depth is None:
            depth = self.default_depth

        print("\n" + "=" * 60)
        print("SNOMED Graph Builder")
        print("=" * 60)

        print(f"Starting Concept : {concept_id}")
        print(f"Expansion Depth  : {depth}")

        # ----------------------------------------------------
        # Check whether concept already exists
        # ----------------------------------------------------

        if self.repository.concept_exists(concept_id):

            print(
                f"\n✅ Concept already exists in Neo4j: "
                f"{concept_id}"
            )

        else:

            print(
                "\n🔍 Concept not found in Neo4j."
            )

            print(
                "Starting dynamic SNOMED expansion..."
            )

        # ----------------------------------------------------
        # Create BFS crawler
        # ----------------------------------------------------

        crawler = BFSCrawler(
            max_depth=depth
        )

        # ----------------------------------------------------
        # Add starting concept
        # ----------------------------------------------------

        crawler.add_start_concept(
            concept_id
        )

        # ----------------------------------------------------
        # Start crawling
        # ----------------------------------------------------

        crawler.crawl()

        print(
            "\n✅ Graph expansion completed."
        )

        return True

    # ========================================================
    # Get Concept Information
    # ========================================================

    def get_concept(self, concept_id):
        """
        Retrieve a concept and its direct parents from Neo4j.

        DISTINCT is used to prevent duplicate parent entries.
        """

        query = """
        MATCH (c:Concept {concept_id: $concept_id})

        OPTIONAL MATCH
            (c)-[:IS_A]->(parent:Concept)

        RETURN
            c.concept_id AS concept_id,
            c.name AS name,

            collect(DISTINCT {
                concept_id: parent.concept_id,
                name: parent.name
            }) AS parents
        """

        result = self.repository.db.execute_query(
            query,
            {
                "concept_id": concept_id
            }
        )

        if not result:
            return None

        return result[0]

    # ========================================================
    # Get Parent Concepts
    # ========================================================

    def get_parents(self, concept_id):
        """
        Retrieve direct parent concepts from Neo4j.
        """

        query = """
        MATCH
            (c:Concept {concept_id: $concept_id})
            -[:IS_A]->
            (parent:Concept)

        RETURN DISTINCT
            parent.concept_id AS concept_id,
            parent.name AS name
        """

        return self.repository.db.execute_query(
            query,
            {
                "concept_id": concept_id
            }
        )

    # ========================================================
    # Get Child Concepts
    # ========================================================

    def get_children(self, concept_id):
        """
        Retrieve direct child concepts from Neo4j.

        Because IS_A relationships are stored as:

            child -[:IS_A]-> parent

        we traverse the relationship in reverse to find children.
        """

        query = """
        MATCH
            (child:Concept)
            -[:IS_A]->
            (parent:Concept {
                concept_id: $concept_id
            })

        RETURN DISTINCT
            child.concept_id AS concept_id,
            child.name AS name
        """

        return self.repository.db.execute_query(
            query,
            {
                "concept_id": concept_id
            }
        )

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
# Test
# ============================================================

if __name__ == "__main__":

    print("=" * 60)
    print("Testing SNOMED Graph Builder")
    print("=" * 60)

    # --------------------------------------------------------
    # Starting SNOMED CT concept
    # --------------------------------------------------------

    START_CONCEPT = "233604007"

    # --------------------------------------------------------
    # Create Graph Builder
    # --------------------------------------------------------

    builder = GraphBuilder(
        default_depth=2
    )

    # --------------------------------------------------------
    # Expand concept
    # --------------------------------------------------------

    builder.expand_concept(
        START_CONCEPT
    )

    # --------------------------------------------------------
    # Retrieve concept information
    # --------------------------------------------------------

    concept = builder.get_concept(
        START_CONCEPT
    )

    print("\n" + "=" * 60)
    print("Concept Information")
    print("=" * 60)

    if concept:

        print(
            f"Concept ID : {concept['concept_id']}"
        )

        print(
            f"Name       : {concept['name']}"
        )

        print("\nParents:")

        for parent in concept["parents"]:

            # Neo4j can return a null parent when
            # OPTIONAL MATCH finds no parent.
            if parent["concept_id"] is not None:

                print(
                    f"- {parent['name']} "
                    f"({parent['concept_id']})"
                )

    else:

        print("Concept not found.")

    # --------------------------------------------------------
    # Retrieve parents separately
    # --------------------------------------------------------

    parents = builder.get_parents(
        START_CONCEPT
    )

    print("\n" + "=" * 60)
    print("Direct Parents")
    print("=" * 60)

    if parents:

        for parent in parents:

            print(
                f"- {parent['name']} "
                f"({parent['concept_id']})"
            )

    else:

        print("No parents found.")

    # --------------------------------------------------------
    # Retrieve children
    # --------------------------------------------------------

    children = builder.get_children(
        START_CONCEPT
    )

    print("\n" + "=" * 60)
    print("Direct Children")
    print("=" * 60)

    if children:

        for child in children:

            print(
                f"- {child['name']} "
                f"({child['concept_id']})"
            )

    else:

        print("No children found.")

    # --------------------------------------------------------
    # Graph statistics
    # --------------------------------------------------------

    stats = builder.get_graph_statistics()

    print("\n" + "=" * 60)
    print("Graph Statistics")
    print("=" * 60)

    print(
        f"Total Concepts      : {stats['concepts']}"
    )

    print(
        f"Total IS_A Relations: {stats['relationships']}"
    )

    print("=" * 60)