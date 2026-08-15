from pathlib import Path
import os
import requests
from dotenv import load_dotenv


# ============================================================
# Project Configuration
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


class BioPortalClient:
    """
    Client for retrieving SNOMED CT concepts and hierarchy
    information from BioPortal.
    """

    BASE_URL = "https://data.bioontology.org"
    ONTOLOGY = "SNOMEDCT"

    # ========================================================
    # Initialization
    # ========================================================

    def __init__(self):

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

    def build_class_uri(self, concept_id):

        return (
            "http://purl.bioontology.org/"
            f"ontology/SNOMEDCT/{concept_id}"
        )

    # ========================================================
    # Extract Concept ID
    # ========================================================

    def extract_concept_id(self, item):
        """
        Extract a SNOMED CT concept ID from a BioPortal object.

        Handles several possible BioPortal response formats.
        """

        if not item:
            return None

        # ----------------------------------------------------
        # Case 1: dictionary containing @id
        # ----------------------------------------------------

        if isinstance(item, dict):

            possible_fields = [
                "@id",
                "id",
                "uri",
                "@about"
            ]

            for field in possible_fields:

                value = item.get(field)

                if isinstance(value, str):

                    concept_id = self.extract_id_from_uri(
                        value
                    )

                    if concept_id:
                        return concept_id

            # ------------------------------------------------
            # Case 2: nested links
            # ------------------------------------------------

            links = item.get("links")

            if isinstance(links, dict):

                for value in links.values():

                    if isinstance(value, str):

                        concept_id = (
                            self.extract_id_from_uri(
                                value
                            )
                        )

                        if concept_id:
                            return concept_id

            # ------------------------------------------------
            # Case 3: nested prefLabel object
            # ------------------------------------------------

            for key, value in item.items():

                if isinstance(value, dict):

                    concept_id = (
                        self.extract_concept_id(
                            value
                        )
                    )

                    if concept_id:
                        return concept_id

        # ----------------------------------------------------
        # Case 4: raw string URI
        # ----------------------------------------------------

        if isinstance(item, str):

            return self.extract_id_from_uri(
                item
            )

        return None

    # ========================================================
    # Extract ID from URI
    # ========================================================

    def extract_id_from_uri(self, uri):

        if not isinstance(uri, str):
            return None

        prefix = (
            "http://purl.bioontology.org/"
            "ontology/SNOMEDCT/"
        )

        # Normal SNOMED URI
        if uri.startswith(prefix):

            concept_id = uri[len(prefix):]

            if concept_id.isdigit():

                return concept_id

        # Sometimes BioPortal may return encoded URI
        if "SNOMEDCT/" in uri:

            concept_id = uri.split(
                "SNOMEDCT/"
            )[-1]

            # Remove possible URL fragments
            concept_id = concept_id.split(
                "#"
            )[0]

            concept_id = concept_id.split(
                "?"
            )[0]

            if concept_id.isdigit():

                return concept_id

        return None

    # ========================================================
    # Get Concept
    # ========================================================

    def get_concept(self, concept_id):
        """
        Fetch a SNOMED CT concept using its concept ID.
        """

        class_uri = self.build_class_uri(
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

            print(
                "Error:",
                response.text
            )

            return None

        except requests.exceptions.RequestException as e:

            print(
                f"Request Error: {e}"
            )

            return None

    # ========================================================
    # Search Concept
    # ========================================================

    def search_concept(self, search_term):
        """
        Search BioPortal for SNOMED CT concepts by name.
        """

        url = (
            f"{self.BASE_URL}/search"
        )

        params = {
            "q": search_term,
            "ontologies": self.ONTOLOGY,
            "include": "prefLabel,synonym",
            "pagesize": 10
        }

        try:

            response = self.session.get(
                url,
                params=params,
                timeout=30
            )

            print(
                f"Search Status Code: "
                f"{response.status_code}"
            )

            if response.status_code != 200:

                print(
                    "Search Error:",
                    response.text
                )

                return []

            data = response.json()

            collection = data.get(
                "collection",
                []
            )

            results = []

            for item in collection:

                concept_id = (
                    self.extract_concept_id(
                        item
                    )
                )

                if not concept_id:
                    continue

                label = (
                    item.get("prefLabel")
                    or item.get("label")
                    or concept_id
                )

                results.append({
                    "concept_id": concept_id,
                    "name": label
                })

            return results

        except requests.exceptions.RequestException as e:

            print(
                f"Search Request Error: {e}"
            )

            return []

    # ========================================================
    # Get Parents
    # ========================================================

    def get_parents(self, concept_id):
        """
        Retrieve direct parent concepts from BioPortal.
        """

        class_uri = self.build_class_uri(
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

            if response.status_code != 200:

                print(
                    "Parent Error:",
                    response.text
                )

                return []

            data = response.json()

            # ------------------------------------------------
            # BioPortal commonly returns a list directly.
            # ------------------------------------------------

            if isinstance(data, list):

                collection = data

            # ------------------------------------------------
            # Some responses are wrapped in "collection".
            # ------------------------------------------------

            elif isinstance(data, dict):

                collection = data.get(
                    "collection",
                    []
                )

                # Some BioPortal responses may use
                # "results".
                if not collection:

                    collection = data.get(
                        "results",
                        []
                    )

            else:

                collection = []

            parents = []

            for parent in collection:

                # --------------------------------------------
                # Extract concept ID
                # --------------------------------------------

                parent_id = (
                    self.extract_concept_id(
                        parent
                    )
                )

                # --------------------------------------------
                # If extraction failed, try the parent
                # object's @id directly.
                # --------------------------------------------

                if not parent_id:

                    if isinstance(parent, dict):

                        raw_id = parent.get(
                            "@id"
                        )

                        if raw_id:

                            parent_id = (
                                self.extract_id_from_uri(
                                    raw_id
                                )
                            )

                if not parent_id:

                    print(
                        "⚠️ Could not determine "
                        "parent concept ID."
                    )

                    continue

                # --------------------------------------------
                # Extract label
                # --------------------------------------------

                label = None

                if isinstance(parent, dict):

                    label = (
                        parent.get("prefLabel")
                        or parent.get("label")
                        or parent.get("name")
                    )

                if not label:

                    # If label isn't included in the
                    # parent response, retrieve it
                    # directly using the ID.

                    parent_concept = (
                        self.get_concept(
                            parent_id
                        )
                    )

                    if parent_concept:

                        label = (
                            parent_concept.get(
                                "prefLabel"
                            )
                            or parent_concept.get(
                                "label"
                            )
                        )

                if not label:

                    label = parent_id

                parents.append({
                    "concept_id": parent_id,
                    "name": label
                })

            return parents

        except requests.exceptions.RequestException as e:

            print(
                f"Parent Request Error: {e}"
            )

            return []

    # ========================================================
    # Get Children
    # ========================================================

    def get_children(self, concept_id):
        """
        Retrieve direct child concepts.

        This method is included for future graph exploration.
        """

        class_uri = self.build_class_uri(
            concept_id
        )

        encoded_uri = requests.utils.quote(
            class_uri,
            safe=""
        )

        url = (
            f"{self.BASE_URL}/ontologies/"
            f"{self.ONTOLOGY}/classes/"
            f"{encoded_uri}/descendants"
        )

        try:

            response = self.session.get(
                url,
                timeout=30
            )

            if response.status_code != 200:

                print(
                    f"Children Status Code: "
                    f"{response.status_code}"
                )

                return []

            data = response.json()

            if isinstance(data, list):

                collection = data

            elif isinstance(data, dict):

                collection = data.get(
                    "collection",
                    []
                )

            else:

                collection = []

            children = []

            for child in collection:

                child_id = (
                    self.extract_concept_id(
                        child
                    )
                )

                if not child_id:
                    continue

                if isinstance(child, dict):

                    label = (
                        child.get("prefLabel")
                        or child.get("label")
                        or child_id
                    )

                else:

                    label = child_id

                children.append({
                    "concept_id": child_id,
                    "name": label
                })

            return children

        except requests.exceptions.RequestException as e:

            print(
                f"Children Request Error: {e}"
            )

            return []


# ============================================================
# Standalone Test
# ============================================================

if __name__ == "__main__":

    print("=" * 60)
    print("Testing BioPortal Client")
    print("=" * 60)

    client = BioPortalClient()

    # --------------------------------------------------------
    # Test Concept
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
            concept.get(
                "prefLabel"
            )
        )

    # --------------------------------------------------------
    # Test Parents
    # --------------------------------------------------------

    print(
        f"\nFetching parents of: {concept_id}"
    )

    parents = client.get_parents(
        concept_id
    )

    print(
        f"\nFound {len(parents)} parent(s)."
    )

    for parent in parents:

        print("\n---")

        print(
            "Label :",
            parent["name"]
        )

        print(
            "ID    :",
            parent["concept_id"]
        )

    # --------------------------------------------------------
    # Test Diabetes
    # --------------------------------------------------------

    diabetes_id = "73211009"

    print("\n" + "=" * 60)
    print("Testing Diabetes mellitus")
    print("=" * 60)

    print(
        f"\nFetching parents of: {diabetes_id}"
    )

    diabetes_parents = client.get_parents(
        diabetes_id
    )

    print(
        f"\nFound "
        f"{len(diabetes_parents)} parent(s)."
    )

    for parent in diabetes_parents:

        print("\n---")

        print(
            "Label :",
            parent["name"]
        )

        print(
            "ID    :",
            parent["concept_id"]
        )

    # --------------------------------------------------------
    # Test Search
    # --------------------------------------------------------

    print("\n" + "=" * 60)
    print("Testing Concept Search")
    print("=" * 60)

    search_term = "Diabetes mellitus"

    print(
        f"\nSearching for: {search_term}"
    )

    results = client.search_concept(
        search_term
    )

    print(
        f"\nFound {len(results)} result(s)."
    )

    for result in results:

        print("\n---")

        print(
            "Label :",
            result["name"]
        )

        print(
            "ID    :",
            result["concept_id"]
        )