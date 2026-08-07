from pathlib import Path
import os
import requests
from dotenv import load_dotenv

# Load .env from the project root
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


class BioPortalClient:
    BASE_URL = "https://data.bioontology.org"

    def __init__(self):
        self.api_key = os.getenv("BIOPORTAL_API_KEY")

        if not self.api_key:
            raise ValueError("BIOPORTAL_API_KEY not found in .env")

        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"apikey token={self.api_key}",
            "Accept": "application/json",
            "User-Agent": "SNOMED-Intelligent-Agent/1.0"
        })

    def get_concept(self, concept_id):
        """
        Fetch a SNOMED CT concept from BioPortal.

        Parameters:
            concept_id (str): SNOMED CT Concept ID

        Returns:
            dict | None: JSON response if found, otherwise None
        """

        class_uri = f"http://purl.bioontology.org/ontology/SNOMEDCT/{concept_id}"

        url = (
            f"{self.BASE_URL}/ontologies/SNOMEDCT/classes/"
            f"{requests.utils.quote(class_uri, safe='')}"
        )

        try:
            response = self.session.get(url, timeout=30)

            print(f"Status Code: {response.status_code}")

            if response.status_code == 200:
                return response.json()

            print("Error:", response.text)
            return None

        except requests.exceptions.RequestException as e:
            print(f"Request Error: {e}")
            return None


if __name__ == "__main__":

    client = BioPortalClient()

    concept = client.get_concept("233604007")

    if concept:
        print("\nPreferred Label:", concept.get("prefLabel"))

if __name__ == "__main__":
    client = BioPortalClient()

    concept = client.get_concept("233604007")

    print("\nPreferred Label:", concept.get("prefLabel"))