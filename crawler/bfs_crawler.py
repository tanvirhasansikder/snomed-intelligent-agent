from collections import deque

from crawler.bioportal_client import BioPortalClient
from database.graph_repository import GraphRepository


class BFSCrawler:
    """
    Breadth-First Search crawler for dynamically expanding
    relevant portions of the SNOMED CT hierarchy.

    Compatible with GraphBuilder.

    The crawler:
        1. Starts from a requested SNOMED concept.
        2. Retrieves its parents from BioPortal.
        3. Saves concepts to Neo4j.
        4. Creates IS_A relationships.
        5. Continues expanding until max_depth is reached.
    """

    def __init__(self, max_depth=2):

        self.client = BioPortalClient()
        self.repository = GraphRepository()

        self.max_depth = max_depth

        # Queue stores:
        # (concept_id, depth)
        self.queue = deque()

        # Concepts already processed
        self.visited = set()

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

        self.queue.append(
            (
                concept_id,
                depth
            )
        )

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

        # ----------------------------------------------------
        # Allow standalone usage:
        #
        # crawler.crawl("233604007")
        # ----------------------------------------------------

        if start_concept_id:

            self.add_start_concept(
                start_concept_id,
                0
            )

        print()
        print("Starting SNOMED BFS Crawler")
        print("=" * 60)

        processed_count = 0

        # ====================================================
        # BFS LOOP
        # ====================================================

        while self.queue:

            concept_id, depth = self.queue.popleft()

            # ------------------------------------------------
            # Prevent duplicate processing
            # ------------------------------------------------

            if concept_id in self.visited:
                continue

            self.visited.add(
                concept_id
            )

            processed_count += 1

            print()
            print("=" * 60)

            print(
                f"Processed : {processed_count}"
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

            # =================================================
            # FETCH CONCEPT
            # =================================================

            concept = self.client.get_concept(
                concept_id
            )

            if not concept:

                print(
                    f"⚠️ Could not retrieve "
                    f"concept: {concept_id}"
                )

                continue

            # ------------------------------------------------
            # Extract preferred label
            # ------------------------------------------------

            concept_name = (
                concept.get("prefLabel")
                or concept.get("label")
                or concept_id
            )

            # =================================================
            # SAVE CONCEPT
            # =================================================

            self.repository.save_concept(
                concept_id,
                concept_name
            )

            # =================================================
            # MAX DEPTH CHECK
            # =================================================

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

            # =================================================
            # GET PARENTS
            # =================================================

            parents = self.client.get_parents(
                concept_id
            )

            print(
                f"Found {len(parents)} parent(s)."
            )

            # =================================================
            # PROCESS PARENTS
            # =================================================

            for parent in parents:

                # ------------------------------------------------
                # BioPortalClient returns:
                #
                # {
                #     "concept_id": "...",
                #     "name": "..."
                # }
                # ------------------------------------------------

                parent_id = parent.get(
                    "concept_id"
                )

                parent_name = parent.get(
                    "name"
                )

                # ------------------------------------------------
                # Validate parent ID
                # ------------------------------------------------

                if not parent_id:

                    print(
                        "⚠️ Could not determine "
                        "parent concept ID."
                    )

                    continue

                # ------------------------------------------------
                # Fallback name
                # ------------------------------------------------

                if not parent_name:

                    parent_name = parent_id

                # =================================================
                # SAVE PARENT
                # =================================================

                self.repository.save_concept(
                    parent_id,
                    parent_name
                )

                # =================================================
                # CREATE IS_A RELATIONSHIP
                # =================================================

                self.repository.save_relationship(
                    concept_id,
                    parent_id
                )

                print(
                    f"🔗 {concept_name} "
                    f"--> {parent_name}"
                )

                # =================================================
                # ADD PARENT TO QUEUE
                # =================================================

                if parent_id not in self.visited:

                    self.queue.append(
                        (
                            parent_id,
                            depth + 1
                        )
                    )

        # =====================================================
        # COMPLETED
        # =====================================================

        print()
        print("=" * 60)
        print("BFS Crawling Completed")
        print("=" * 60)

        print(
            f"Total Concepts Processed: "
            f"{processed_count}"
        )

        print(
            f"Total Concepts in Neo4j: "
            f"{self.repository.count_concepts()}"
        )

        print(
            f"Total IS_A Relationships: "
            f"{self.repository.count_relationships()}"
        )

        return self.visited


# ============================================================
# STANDALONE TEST
# ============================================================

if __name__ == "__main__":

    print()
    print("=" * 60)
    print("Testing SNOMED BFS Crawler")
    print("=" * 60)

    START_CONCEPT = "233604007"

    crawler = BFSCrawler(
        max_depth=2
    )

    crawler.add_start_concept(
        START_CONCEPT,
        0
    )

    crawler.crawl()