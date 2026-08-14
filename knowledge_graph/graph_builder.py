from database.graph_repository import GraphRepository


class GraphBuilder:
    """
    Builds SNOMED concept nodes from BioPortal data.
    """

    def __init__(self):
        self.repository = GraphRepository()

    def build_concept(self, concept_json):
        """
        Extract concept information from BioPortal JSON
        and save it into Neo4j.

        Parameters
        ----------
        concept_json : dict
            JSON returned by BioPortal

        Returns
        -------
        dict
            Parsed concept information
        """

        concept_id = concept_json["@id"].split("/")[-1]

        concept_name = concept_json.get(
            "prefLabel",
            "Unknown"
        )

        self.repository.save_concept(
            concept_id,
            concept_name
        )

        return {
            "concept_id": concept_id,
            "name": concept_name
        }


if __name__ == "__main__":

    from crawler.bioportal_client import BioPortalClient

    client = BioPortalClient()

    concept = client.get_concept("233604007")

    builder = GraphBuilder()

    data = builder.build_concept(concept)

    print("\nReturned Data:")
    print(data)