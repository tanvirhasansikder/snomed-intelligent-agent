from database.neo4j_connection import Neo4jConnection


class GraphRepository:
    """
    Handles all Neo4j database operations for the SNOMED Knowledge Graph.
    """

    def __init__(self):
        self.db = Neo4jConnection()

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

        print(f"✅ Saved Concept: {name} ({concept_id})")

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

        return result[0]["count"] > 0

    # ==========================================================
    # RELATIONSHIP OPERATIONS
    # ==========================================================

    def save_relationship(self, child_id, parent_id):
        """
        Create an IS_A relationship between two concepts.
        """

        query = """
        MATCH (child:Concept {concept_id: $child_id})
        MATCH (parent:Concept {concept_id: $parent_id})

        MERGE (child)-[:IS_A]->(parent)
        """

        self.db.execute_query(
            query,
            {
                "child_id": child_id,
                "parent_id": parent_id
            }
        )

        print(
            f"✅ Created Relationship: "
            f"{child_id} --> {parent_id}"
        )

        return True

    # ==========================================================
    # PARENT / CHILD OPERATIONS
    # ==========================================================

    def get_parents(self, concept_id):
        """
        Get direct parents of a concept.

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
        Get direct children of a concept.

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
        Get ancestors of a concept.

        Traverses upward through IS_A relationships.
        """

        query = """
        MATCH (start:Concept {concept_id: $concept_id})
              -[:IS_A*1..$max_depth]->
              (ancestor:Concept)

        RETURN DISTINCT
               ancestor.concept_id AS concept_id,
               ancestor.name AS name
        ORDER BY name
        """

        return self.db.execute_query(
            query,
            {
                "concept_id": concept_id,
                "max_depth": max_depth
            }
        )

    def get_descendants(self, concept_id, max_depth=10):
        """
        Get descendants of a concept.

        Traverses downward through IS_A relationships.
        """

        query = """
        MATCH (descendant:Concept)
              -[:IS_A*1..$max_depth]->
              (start:Concept {concept_id: $concept_id})

        RETURN DISTINCT
               descendant.concept_id AS concept_id,
               descendant.name AS name
        ORDER BY name
        """

        return self.db.execute_query(
            query,
            {
                "concept_id": concept_id,
                "max_depth": max_depth
            }
        )

    # ==========================================================
    # PATH OPERATIONS
    # ==========================================================

    def find_shortest_path(self, start_id, end_id):
        """
        Find the shortest hierarchical path between
        two SNOMED concepts.
        """

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

    def clear_database(self):
        """
        Delete everything in Neo4j.
        """

        query = """
        MATCH (n)
        DETACH DELETE n
        """

        self.db.execute_query(query)

        print("🗑 Database cleared.")

    def close(self):
        """
        Close Neo4j connection.
        """

        self.db.close()


# ==============================================================
# TEST
# ==============================================================

if __name__ == "__main__":

    repo = GraphRepository()

    print("\n========== GET CONCEPT ==========")

    concept = repo.get_concept("233604007")

    print(concept)

    print("\n========== SEARCH ==========")

    results = repo.search_concepts("Pneumonia")

    for result in results:
        print(result)

    print("\n========== PARENTS ==========")

    parents = repo.get_parents("233604007")

    for parent in parents:
        print(parent)

    print("\n========== CHILDREN ==========")

    children = repo.get_children("233604007")

    for child in children:
        print(child)

    print("\n========== ANCESTORS ==========")

    ancestors = repo.get_ancestors(
        "233604007",
        max_depth=5
    )

    for ancestor in ancestors:
        print(ancestor)

    print("\n========== DESCENDANTS ==========")

    descendants = repo.get_descendants(
        "233604007",
        max_depth=5
    )

    for descendant in descendants:
        print(descendant)

    print("\n========== DATABASE STATISTICS ==========")

    print(
        "Total Concepts:",
        repo.count_concepts()
    )

    print(
        "Total Relationships:",
        repo.count_relationships()
    )

    repo.close()