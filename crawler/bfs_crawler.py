from collections import deque

from crawler.bioportal_client import BioPortalClient
from database.graph_repository import GraphRepository


class BFSCrawler:
    """
    Breadth-First Search crawler for dynamically expanding
    the SNOMED CT hierarchy.

    The crawler starts from a SNOMED CT concept, retrieves
    its parents from BioPortal, stores them in Neo4j, creates
    IS_A relationships, and continues expanding until the
    configured maximum depth is reached.
    """

    def __init__(self, max_depth=2):

        self.client = BioPortalClient()

        self.repository = GraphRepository()

        self.max_depth = max_depth

        # Queue contains:
        #
        # (concept_id, depth)
        #
        self.queue = deque()

        # Concepts already processed
        self.visited = set()

    # ========================================================
    # Add Starting Concept
    # ========================================================

    def add_start_concept(self, concept_id):
        """
        Add the starting SNOMED concept to the BFS queue.
        """

        self.queue.append(
            (concept_id, 0)
        )

    # ========================================================
    # Extract Parent ID
    # ========================================================

    def extract_parent_id(self, parent):
        """
        Extract the SNOMED CT ID from a BioPortal parent
        response.

        BioPortal may return:

            @id

        or:

            id

        Example:

            http://purl.bioontology.org/ontology/SNOMEDCT/205237003

        becomes:

            205237003
        """

        if not isinstance(parent, dict):
            return None

        parent_uri = (
            parent.get("@id")
            or parent.get("id")
        )

        if not parent_uri:
            return None

        return (
            parent_uri
            .rstrip("/")
            .split("/")
            [-1]
        )

    # ========================================================
    # Extract Parent Name
    # ========================================================

    def extract_parent_name(self, parent):
        """
        Extract the preferred label of a parent concept.
        """

        if not isinstance(parent, dict):
            return "Unknown"

        return (
            parent.get("prefLabel")
            or parent.get("name")
            or "Unknown"
        )

    # ========================================================
    # Crawl
    # ========================================================

    def crawl(self):

        processed = 0

        print("\nStarting SNOMED BFS Crawler")
        print("=" * 60)

        while self.queue:

            # ------------------------------------------------
            # Get next concept
            # ------------------------------------------------

            concept_id, depth = self.queue.popleft()

            # ------------------------------------------------
            # Prevent duplicate processing
            # ------------------------------------------------

            if concept_id in self.visited:
                continue

            self.visited.add(
                concept_id
            )

            processed += 1

            # ------------------------------------------------
            # Processing information
            # ------------------------------------------------

            print("\n" + "=" * 60)

            print(
                f"Processed : {processed}"
            )

            print(
                f"Queue Size: {len(self.queue)}"
            )

            print(
                f"Depth     : {depth}"
            )

            print(
                f"Concept   : {concept_id}"
            )

            # ------------------------------------------------
            # Retrieve concept from BioPortal
            # ------------------------------------------------

            concept = self.client.get_concept(
                concept_id
            )

            if not concept:

                print(
                    f"⚠️ Could not retrieve concept "
                    f"{concept_id}"
                )

                continue

            # ------------------------------------------------
            # Get preferred label
            # ------------------------------------------------

            concept_name = (
                concept.get("prefLabel")
                or concept.get("name")
                or "Unknown"
            )

            # ------------------------------------------------
            # Save current concept
            # ------------------------------------------------

            self.repository.save_concept(
                concept_id,
                concept_name
            )

            # ------------------------------------------------
            # Maximum depth check
            # ------------------------------------------------

            if depth >= self.max_depth:

                print(
                    f"Reached maximum depth "
                    f"({self.max_depth})."
                )

                print(
                    f"Not expanding: "
                    f"{concept_name}"
                )

                continue

            # ------------------------------------------------
            # Retrieve direct parents
            # ------------------------------------------------

            parents = self.client.get_parents(
                concept_id
            )

            if not parents:

                print(
                    f"No parents found for "
                    f"{concept_name}."
                )

                continue

            print(
                f"Found {len(parents)} parent(s)."
            )

            # ------------------------------------------------
            # Process every parent
            # ------------------------------------------------

            for parent in parents:

                # --------------------------------------------
                # Extract parent ID
                # --------------------------------------------

                parent_id = self.extract_parent_id(
                    parent
                )

                if not parent_id:

                    print(
                        "⚠️ Could not determine "
                        "parent concept ID."
                    )

                    continue

                # --------------------------------------------
                # Extract parent name
                # --------------------------------------------

                parent_name = self.extract_parent_name(
                    parent
                )

                # --------------------------------------------
                # Save parent node
                # --------------------------------------------

                self.repository.save_concept(
                    parent_id,
                    parent_name
                )

                # --------------------------------------------
                # Create IS_A relationship
                # --------------------------------------------

                self.repository.save_relationship(
                    concept_id,
                    parent_id
                )

                print(
                    f"🔗 {concept_name} "
                    f"--> {parent_name}"
                )

                # --------------------------------------------
                # Add parent to BFS queue
                # --------------------------------------------

                if parent_id not in self.visited:

                    self.queue.append(
                        (
                            parent_id,
                            depth + 1
                        )
                    )

        # ====================================================
        # Crawling Complete
        # ====================================================

        print("\n" + "=" * 60)

        print(
            "BFS Crawling Completed"
        )

        print("=" * 60)

        print(
            f"Total Concepts Processed: "
            f"{processed}"
        )

        print(
            f"Total Concepts in Neo4j: "
            f"{self.repository.count_concepts()}"
        )

        print(
            f"Total IS_A Relationships: "
            f"{self.repository.count_relationships()}"
        )


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":

    # --------------------------------------------------------
    # Starting SNOMED CT Concept
    # --------------------------------------------------------

    START_CONCEPT = "233604007"

    # --------------------------------------------------------
    # Maximum BFS depth
    #
    # 0 = Pneumonia only
    #
    # 1 = Pneumonia + direct parents
    #
    # 2 = Pneumonia + parents + grandparents
    #
    # 3 = three levels
    # --------------------------------------------------------

    MAX_DEPTH = 2

    # --------------------------------------------------------
    # Create crawler
    # --------------------------------------------------------

    crawler = BFSCrawler(
        max_depth=MAX_DEPTH
    )

    # --------------------------------------------------------
    # Add starting concept
    # --------------------------------------------------------

    crawler.add_start_concept(
        START_CONCEPT
    )

    # --------------------------------------------------------
    # Start crawling
    # --------------------------------------------------------

    crawler.crawl()