from pathlib import Path
import os

import requests
from dotenv import load_dotenv


# ============================================================
# Project Configuration
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


# ============================================================
# BioPortal Client
# ============================================================

class BioPortalClient:
    """
    Client for retrieving SNOMED CT concepts and hierarchy
    information from the BioPortal REST API.
    """

    BASE_URL = "https://data.bioontology.org"
    ONTOLOGY = "SNOMEDCT"

    def __init__(self):
        """
        Initialize the BioPortal API client.
        """

        self.api_key = os.getenv("BIOPORTAL_API_KEY")

        if not self.api_key:
            raise ValueError(
                "BIOPORTAL_API_KEY not found in .env"
            )

        self.session = requests.Session()

        self.session.headers.update({
            "Authorization": f"apikey token={self.api_key}",
            "Accept": "application/json",
            "User-Agent": "SNOMED-Intelligent-Agent/1.0"
        })

    # ========================================================
    # Build SNOMED URI
    # ========================================================

    def _get_class_uri(self, concept_id):
        """
        Convert a SNOMED CT concept ID into its BioPortal URI.
        """

        return (
            f"http://purl.bioontology.org/ontology/"
            f"SNOMEDCT/{concept_id}"
        )

    # ========================================================
    # Get Concept
    # ========================================================

    def get_concept(self, concept_id):
        """
        Retrieve a SNOMED CT concept from BioPortal.

        Returns:
            dict | None
        """

        class_uri = self._get_class_uri(
            concept_id
        )

        encoded_uri = requests.utils.quote(
            class_uri,
            safe=""
        )

        url = (
            f"{self.BASE_URL}/ontologies/"
            f"{self.ONTOLOGY}/classes/"
            f"{encoded_uri}"
        )

        try:

            response = self.session.get(
                url,
                timeout=30
            )

            print(
                f"Status Code: {response.status_code}"
            )

            if response.status_code == 200:
                return response.json()

            if response.status_code == 404:

                print(
                    f"⚠️ Concept not found: {concept_id}"
                )

                return None

            print(
                "BioPortal Error:",
                response.text
            )

            return None

        except requests.exceptions.RequestException as e:

            print(
                f"Request Error: {e}"
            )

            return None

    # ========================================================
    # Get Parents
    # ========================================================

    def get_parents(self, concept_id):
        """
        Retrieve direct parent concepts of a SNOMED CT
        concept.

        IMPORTANT:
        BioPortal's /parents endpoint returns a LIST directly.

        Example:

        [
            {
                "@id": "...",
                "prefLabel": "Pneumonitis"
            },
            {
                "@id": "...",
                "prefLabel": "Infectious disease of lung"
            }
        ]

        Returns:
            list
        """

        class_uri = self._get_class_uri(
            concept_id
        )

        encoded_uri = requests.utils.quote(
            class_uri,
            safe=""
        )

        url = (
            f"{self.BASE_URL}/ontologies/"
            f"{self.ONTOLOGY}/classes/"
            f"{encoded_uri}/parents"
        )

        try:

            response = self.session.get(
                url,
                timeout=30
            )

            print(
                f"Parent Status Code: "
                f"{response.status_code}"
            )

            # ------------------------------------------------
            # Successful request
            # ------------------------------------------------

            if response.status_code == 200:

                data = response.json()

                # BioPortal is returning a list directly.
                if isinstance(data, list):
                    return data

                # ------------------------------------------------
                # Extra protection in case BioPortal returns
                # a dictionary in another response format.
                # ------------------------------------------------

                if isinstance(data, dict):

                    collection = data.get(
                        "collection",
                        []
                    )

                    if isinstance(collection, list):
                        return collection

                print(
                    "⚠️ Unexpected parent response format:"
                )

                print(data)

                return []

            # ------------------------------------------------
            # Not found
            # ------------------------------------------------

            if response.status_code == 404:

                print(
                    f"⚠️ Parents not found for: "
                    f"{concept_id}"
                )

                return []

            # ------------------------------------------------
            # Other API error
            # ------------------------------------------------

            print(
                "BioPortal Parent Error:",
                response.text
            )

            return []

        except requests.exceptions.RequestException as e:

            print(
                f"Parent Request Error: {e}"
            )

            return []

    # ========================================================
    # Extract Concept ID
    # ========================================================

    def extract_concept_id(self, uri):
        """
        Extract the SNOMED CT concept ID from a BioPortal URI.

        Example:

        http://purl.bioontology.org/ontology/SNOMEDCT/205237003

        becomes:

        205237003
        """

        if not uri:
            return None

        return uri.rstrip("/").split("/")[-1]

    # ========================================================
    # Get Simplified Parent Information
    # ========================================================

    def get_parent_concepts(self, concept_id):
        """
        Retrieve direct parents and return them in a
        simplified format.

        Returns:

        [
            {
                "id": "205237003",
                "name": "Pneumonitis"
            }
        ]
        """

        parents = self.get_parents(
            concept_id
        )

        simplified_parents = []

        for parent in parents:

            if not isinstance(parent, dict):
                continue

            # BioPortal commonly uses @id.
            parent_uri = (
                parent.get("@id")
                or parent.get("id")
            )

            parent_id = self.extract_concept_id(
                parent_uri
            )

            if not parent_id:
                continue

            parent_name = (
                parent.get("prefLabel")
                or parent.get("name")
                or "Unknown"
            )

            simplified_parents.append({
                "id": parent_id,
                "name": parent_name
            })

        return simplified_parents


# ============================================================
# Standalone Test
# ============================================================

if __name__ == "__main__":

    print("=" * 60)
    print("Testing BioPortal Client")
    print("=" * 60)

    client = BioPortalClient()

    # --------------------------------------------------------
    # Test concept
    # --------------------------------------------------------

    concept_id = "233604007"

    print(
        f"\nFetching concept: {concept_id}"
    )

    concept = client.get_concept(
        concept_id
    )

    if concept:

        print(
            "\nPreferred Label:"
        )

        print(
            concept.get("prefLabel")
        )

    else:

        print(
            "\n❌ Concept could not be retrieved."
        )

    # --------------------------------------------------------
    # Test parents
    # --------------------------------------------------------

    print(
        "\n" + "=" * 60
    )

    print(
        f"Fetching parents of: {concept_id}"
    )

    parents = client.get_parents(
        concept_id
    )

    print(
        f"\nFound {len(parents)} parent(s)."
    )

    for parent in parents:

        if not isinstance(parent, dict):
            continue

        parent_uri = (
            parent.get("@id")
            or parent.get("id")
        )

        parent_id = client.extract_concept_id(
            parent_uri
        )

        parent_name = (
            parent.get("prefLabel")
            or parent.get("name")
            or "Unknown"
        )

        print("\n---")

        print(
            f"Label : {parent_name}"
        )

        print(
            f"ID    : {parent_id}"
        )