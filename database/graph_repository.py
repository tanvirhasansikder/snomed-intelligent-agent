from database.neo4j_connection import Neo4jConnection


class GraphImporter:

    def __init__(self):
        self.db = Neo4jConnection()

    def save_concept(self, concept_id, label):

        query = """
        MERGE (c:Concept {concept_id: $id})
        SET c.name = $label
        """

        self.db.execute_query(
            query,
            {
                "id": concept_id,
                "label": label
            }
        )

        print(f"Saved: {label}")