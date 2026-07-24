from database.neo4j_connection import Neo4jConnection


class GraphManager:
    def __init__(self):
        self.db = Neo4jConnection()

    def create_concept(self, concept_id, name):
        query = """
        CREATE (c:Concept {
            concept_id: $concept_id,
            name: $name
        })
        RETURN c
        """

        return self.db.execute_query(
            query,
            {
                "concept_id": concept_id,
                "name": name
            }
        )

    def get_all_concepts(self):
        query = """
        MATCH (c:Concept)
        RETURN c
        """

        return self.db.execute_query(query)

    def close(self):
        self.db.close()


if __name__ == "__main__":
    graph = GraphManager()

    graph.create_concept(
        "233604007",
        "Pneumonia"
    )

    result = graph.get_all_concepts()

    print(result)

    graph.close()