from neo4j import GraphDatabase
from dotenv import load_dotenv
import os

load_dotenv()


class Neo4jConnection:
    def __init__(self):
        self.driver = GraphDatabase.driver(
            os.getenv("NEO4J_URI"),
            auth=(
                os.getenv("NEO4J_USERNAME"),
                os.getenv("NEO4J_PASSWORD"),
            ),
        )

    def close(self):
        self.driver.close()

    def execute_query(self, query, parameters=None):
        with self.driver.session() as session:
            result = session.run(query, parameters or {})
            return [record.data() for record in result]


if __name__ == "__main__":
    db = Neo4jConnection()

    result = db.execute_query(
        "RETURN 'Connected to Neo4j successfully!' AS message"
    )

    print(result)

    db.close()