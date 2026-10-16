"""
BioPortal REST client for SNOMED CT (ontology acronym ``SNOMEDCT``).

Endpoints follow the BioPortal REST documentation
(https://data.bioontology.org/documentation):

    GET /search?q=...&ontologies=SNOMEDCT
    GET /ontologies/SNOMEDCT/classes/{encoded class IRI}
    GET /ontologies/SNOMEDCT/classes/{encoded class IRI}/parents
    GET /ontologies/SNOMEDCT/classes/{encoded class IRI}/children   (paged)
    GET /ontologies/SNOMEDCT/classes/{encoded class IRI}/ancestors
    GET /ontologies/SNOMEDCT/latest_submission

Error handling
--------------
Every public method accepts ``raise_errors``:

* ``raise_errors=False`` (default, same as the original behaviour): failures
  return ``None`` / ``[]`` and the exception is kept in ``self.last_error``.
* ``raise_errors=True``: failures raise ``BioPortalRequestError``, so callers
  can tell "the request failed" apart from "BioPortal returned nothing".

A 404 for a single concept is not an error: ``get_concept`` returns ``None``.
Timeouts, connection errors, HTTP 429 and HTTP 5xx are retried a bounded
number of times with exponential backoff; other 4xx errors are not retried.

The API key is sent only in the ``Authorization`` header and never appears in
URLs, log messages, exception messages or ``repr``.
"""

import logging
import os
import time
from urllib.parse import parse_qs, urlparse

import requests

from config.settings import BASE_DIR  # noqa: F401  (loads .env from the project root)

logger = logging.getLogger(__name__)


class BioPortalError(Exception):
    """Base class for BioPortal client errors."""


class BioPortalRequestError(BioPortalError):
    """A BioPortal request failed (network, timeout, HTTP error, bad body)."""

    def __init__(self, message, status_code=None, endpoint=None):
        super().__init__(message)
        self.status_code = status_code
        self.endpoint = endpoint


class BioPortalNotFoundError(BioPortalRequestError):
    """BioPortal answered 404 for a request that requires the class to exist."""


class BioPortalConfigurationError(ValueError):
    """BIOPORTAL_API_KEY (or another client setting) is missing or invalid."""


class BioPortalClient:
    """
    Client for retrieving SNOMED CT concepts and hierarchy information from
    BioPortal.
    """

    BASE_URL = "https://data.bioontology.org"
    ONTOLOGY = "SNOMEDCT"
    CLASS_PREFIX = "http://purl.bioontology.org/ontology/SNOMEDCT/"

    #: (connect timeout, read timeout) in seconds.
    DEFAULT_TIMEOUT = (5, 30)
    RETRY_STATUS = frozenset({429, 500, 502, 503, 504})

    # ========================================================
    # Initialization
    # ========================================================

    def __init__(
        self,
        api_key=None,
        session=None,
        timeout=DEFAULT_TIMEOUT,
        max_retries=2,
        backoff_seconds=1.0,
        max_backoff_seconds=10.0,
        sleep=None,
    ):
        self.api_key = api_key or os.getenv("BIOPORTAL_API_KEY")

        if not self.api_key:
            raise BioPortalConfigurationError(
                "BIOPORTAL_API_KEY not found in .env"
            )

        if not isinstance(max_retries, int) or max_retries < 0:
            raise BioPortalConfigurationError("max_retries must be a non-negative integer")

        self.session = session or requests.Session()

        self.session.headers.update({
            "Authorization": f"apikey token={self.api_key}",
            "Accept": "application/json",
            "User-Agent": "SNOMED-Intelligent-Agent/1.0"
        })

        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self.max_backoff_seconds = max_backoff_seconds
        self.sleep = sleep or time.sleep

        #: Number of HTTP attempts made (for latency / cost measurement).
        self.request_count = 0
        #: Last error swallowed in non-strict mode (None if the last call succeeded).
        self.last_error = None
        #: Records skipped by the last parse because they were malformed.
        self.last_skipped_records = 0

    def __repr__(self):
        return f"BioPortalClient(ontology={self.ONTOLOGY!r}, api_key=<hidden>)"

    # ========================================================
    # Build SNOMED URI
    # ========================================================

    def build_class_uri(self, concept_id):

        return f"{self.CLASS_PREFIX}{concept_id}"

    def _class_url(self, concept_id, suffix=""):
        encoded_uri = requests.utils.quote(
            self.build_class_uri(concept_id),
            safe=""
        )
        url = (
            f"{self.BASE_URL}/ontologies/"
            f"{self.ONTOLOGY}/classes/"
            f"{encoded_uri}"
        )
        return f"{url}/{suffix}" if suffix else url

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
            # Case 3: nested object
            # ------------------------------------------------

            for value in item.values():

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

        # Normal SNOMED URI
        if uri.startswith(self.CLASS_PREFIX):

            concept_id = uri[len(self.CLASS_PREFIX):]

            if concept_id.isdigit():

                return concept_id

        # Sometimes BioPortal may return an IRI with a fragment or query.
        if "SNOMEDCT/" in uri:

            concept_id = uri.split(
                "SNOMEDCT/"
            )[-1]

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
    # HTTP core: bounded retries, typed errors
    # ========================================================

    @staticmethod
    def _endpoint_label(url):
        """A short, secret-free label for messages (no query string)."""
        return url.split("?", 1)[0].replace("https://data.bioontology.org", "")

    def _retry_delay(self, attempt, response=None):
        delay = self.backoff_seconds * (2 ** attempt)
        if response is not None:
            retry_after = response.headers.get("Retry-After") if response.headers else None
            if retry_after is not None:
                try:
                    delay = float(retry_after)
                except (TypeError, ValueError):
                    pass
        return max(0.0, min(delay, self.max_backoff_seconds))

    def _request_json(self, url, params=None, allow_404=False):
        """
        GET ``url`` and return the decoded JSON body.

        Returns None only when ``allow_404`` is True and BioPortal answers 404.
        Raises BioPortalRequestError for every other failure.
        """

        endpoint = self._endpoint_label(url)
        attempts = self.max_retries + 1
        last_problem = None

        for attempt in range(attempts):

            try:
                response = self.session.get(
                    url,
                    params=params,
                    timeout=self.timeout
                )
                self.request_count += 1

            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
                self.request_count += 1
                last_problem = f"{type(exc).__name__}"
                if attempt < attempts - 1:
                    delay = self._retry_delay(attempt)
                    logger.warning("BioPortal %s on %s; retrying in %.1fs", last_problem, endpoint, delay)
                    self.sleep(delay)
                    continue
                raise BioPortalRequestError(
                    f"BioPortal request failed after {attempts} attempt(s): {last_problem} ({endpoint})",
                    endpoint=endpoint,
                ) from None

            except requests.exceptions.RequestException as exc:
                self.request_count += 1
                raise BioPortalRequestError(
                    f"BioPortal request failed: {type(exc).__name__} ({endpoint})",
                    endpoint=endpoint,
                ) from None

            status = response.status_code

            if status == 200:
                try:
                    return response.json()
                except ValueError:
                    raise BioPortalRequestError(
                        f"BioPortal returned a non-JSON body ({endpoint})",
                        status_code=status,
                        endpoint=endpoint,
                    ) from None

            if status == 404:
                if allow_404:
                    return None
                raise BioPortalNotFoundError(
                    f"BioPortal returned HTTP 404 ({endpoint})",
                    status_code=status,
                    endpoint=endpoint,
                )

            if status in self.RETRY_STATUS and attempt < attempts - 1:
                delay = self._retry_delay(attempt, response)
                logger.warning("BioPortal HTTP %s on %s; retrying in %.1fs", status, endpoint, delay)
                self.sleep(delay)
                last_problem = f"HTTP {status}"
                continue

            raise BioPortalRequestError(
                f"BioPortal returned HTTP {status} ({endpoint})",
                status_code=status,
                endpoint=endpoint,
            )

        # Defensive: the loop always returns or raises.
        raise BioPortalRequestError(f"BioPortal request failed: {last_problem} ({endpoint})", endpoint=endpoint)

    def _guard(self, func, raise_errors, fallback):
        """Run ``func``; in non-strict mode convert errors to ``fallback``."""
        self.last_error = None
        try:
            return func()
        except BioPortalError as exc:
            self.last_error = exc
            if raise_errors:
                raise
            logger.warning("%s", exc)
            return fallback

    # ========================================================
    # Response parsing
    # ========================================================

    def _parse_classes(self, collection):
        """Convert BioPortal class objects to {'concept_id', 'name'} dicts."""
        records = []
        skipped = 0
        for item in collection:
            concept_id = self.extract_concept_id(item) if isinstance(item, (dict, str)) else None
            if not concept_id:
                skipped += 1
                continue
            label = None
            if isinstance(item, dict):
                for key in ("prefLabel", "label", "name"):
                    value = item.get(key)
                    if isinstance(value, str) and value.strip():
                        label = value
                        break
            records.append({"concept_id": concept_id, "name": label})
        self.last_skipped_records = skipped
        if skipped:
            logger.debug("Skipped %d malformed BioPortal record(s)", skipped)
        return records

    @staticmethod
    def _as_collection(data, endpoint):
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            if isinstance(data.get("collection"), list):
                return data["collection"]
            if isinstance(data.get("results"), list):
                return data["results"]
        raise BioPortalRequestError(f"Unexpected BioPortal response format ({endpoint})", endpoint=endpoint)

    def _collect_paged(self, url, max_pages, pagesize=None):
        """
        Read a possibly paged collection.

        Returns (items, complete, pages_fetched). ``complete`` is False when
        ``max_pages`` was reached while BioPortal still reported a next page.
        """
        if not isinstance(max_pages, int) or max_pages < 1:
            raise ValueError("max_pages must be a positive integer")

        items, page, pages_fetched = [], 1, 0
        endpoint = self._endpoint_label(url)

        while pages_fetched < max_pages:
            params = {"page": page}
            if pagesize:
                params["pagesize"] = pagesize
            data = self._request_json(url, params=params)
            pages_fetched += 1

            if isinstance(data, list):
                return items + data, True, pages_fetched

            if not isinstance(data, dict) or not isinstance(data.get("collection"), list):
                raise BioPortalRequestError(f"Unexpected BioPortal response format ({endpoint})", endpoint=endpoint)

            items.extend(data["collection"])
            next_page = self._next_page_number(data)
            if next_page is None:
                return items, True, pages_fetched
            if next_page <= page:
                logger.warning("BioPortal pagination did not advance on %s; stopping", endpoint)
                return items, False, pages_fetched
            page = next_page

        return items, False, pages_fetched

    @staticmethod
    def _next_page_number(data):
        """
        Next page number from a paged response, or None on the last page.

        BioPortal documents ``nextPage`` as a hypermedia link to follow; the
        exact form (page number or URL, top level or under ``links``) was not
        verified against the live API here, so all of them are accepted.
        """
        candidates = [data.get("nextPage")]
        links = data.get("links")
        if isinstance(links, dict):
            candidates.append(links.get("nextPage"))
        for value in candidates:
            if value is None or value is False or value == "":
                continue
            if isinstance(value, int) and not isinstance(value, bool):
                return value
            if isinstance(value, str):
                if value.isdigit():
                    return int(value)
                numbers = parse_qs(urlparse(value).query).get("page", [])
                if numbers and numbers[0].isdigit():
                    return int(numbers[0])
        return None

    # ========================================================
    # Get Concept
    # ========================================================

    def get_concept(self, concept_id, raise_errors=False):
        """
        Fetch a SNOMED CT concept using its concept ID.

        Returns the BioPortal class JSON, or None if BioPortal has no such
        class (HTTP 404). Other failures: see module docstring.
        """

        url = self._class_url(concept_id)
        return self._guard(lambda: self._request_json(url, allow_404=True), raise_errors, None)

    # ========================================================
    # Search Concept
    # ========================================================

    def search_concept(self, search_term, raise_errors=False, pagesize=10):
        """
        Search BioPortal for SNOMED CT concepts by name.

        Returns a list of {'concept_id', 'name'} in BioPortal's ranking order.
        """

        url = f"{self.BASE_URL}/search"

        params = {
            "q": search_term,
            "ontologies": self.ONTOLOGY,
            "include": "prefLabel,synonym",
            "pagesize": pagesize
        }

        def run():
            data = self._request_json(url, params=params)
            collection = data.get("collection", []) if isinstance(data, dict) else []
            if not isinstance(collection, list):
                collection = []
            results = []
            for record in self._parse_classes(collection):
                results.append({
                    "concept_id": record["concept_id"],
                    "name": record["name"] or record["concept_id"],
                })
            return results

        return self._guard(run, raise_errors, [])

    # ========================================================
    # Get Parents
    # ========================================================

    def get_parents(self, concept_id, raise_errors=False):
        """
        Retrieve direct parent concepts from BioPortal.

        An empty list in strict mode means BioPortal reported no parents
        (e.g. the root concept); a failure raises instead.
        """

        url = self._class_url(concept_id, "parents")

        def run():
            data = self._request_json(url)
            records = self._parse_classes(self._as_collection(data, self._endpoint_label(url)))
            for record in records:
                if not record["name"]:
                    # Label not included in the parent response:
                    # look it up (non-strict: fall back to the ID).
                    parent_concept = self.get_concept(record["concept_id"])
                    label = None
                    if isinstance(parent_concept, dict):
                        label = parent_concept.get("prefLabel") or parent_concept.get("label")
                    record["name"] = label or record["concept_id"]
            return records

        return self._guard(run, raise_errors, [])

    # ========================================================
    # Get Children (direct children only, paged)
    # ========================================================

    def fetch_children(self, concept_id, max_pages=5, pagesize=100):
        """
        Retrieve DIRECT children (``/children``), following pagination.

        Always strict: raises BioPortalRequestError on failure.

        Returns ``{"children": [...], "complete": bool, "pages_fetched": int}``.
        ``complete`` is False if more pages existed than ``max_pages`` allowed.
        """

        url = self._class_url(concept_id, "children")
        items, complete, pages = self._collect_paged(url, max_pages, pagesize)
        children = [
            {"concept_id": r["concept_id"], "name": r["name"] or r["concept_id"]}
            for r in self._parse_classes(items)
        ]
        return {"children": children, "complete": complete, "pages_fetched": pages}

    def get_children(self, concept_id, raise_errors=False, max_pages=10):
        """
        Retrieve direct child concepts.

        Fixed: the original called ``/descendants`` (every level below the
        concept, first page only) although it was documented as children.
        """

        return self._guard(
            lambda: self.fetch_children(concept_id, max_pages=max_pages)["children"],
            raise_errors,
            [],
        )

    # ========================================================
    # Ancestors and ontology version (used to build gold data)
    # ========================================================

    def get_ancestors(self, concept_id, raise_errors=False, max_pages=20):
        """
        Retrieve all ancestors (transitive IS_A) of a concept.

        The response format of ``/ancestors`` has not been checked against the
        live API in this project; both a plain list and a paged collection are
        accepted.
        """

        url = self._class_url(concept_id, "ancestors")

        def run():
            items, complete, _ = self._collect_paged(url, max_pages)
            if not complete:
                raise BioPortalRequestError(
                    f"Ancestor list truncated after {max_pages} page(s)", endpoint=self._endpoint_label(url))
            return [
                {"concept_id": r["concept_id"], "name": r["name"] or r["concept_id"]}
                for r in self._parse_classes(items)
            ]

        return self._guard(run, raise_errors, [])

    def get_ontology_version(self, raise_errors=False):
        """
        Return {'version': ..., 'released': ...} for the latest SNOMEDCT
        submission on BioPortal, or None on failure.

        Field names follow BioPortal's submission objects; not verified
        against the live API in this project.
        """

        url = f"{self.BASE_URL}/ontologies/{self.ONTOLOGY}/latest_submission"

        def run():
            data = self._request_json(url, params={"display": "version,released"})
            if not isinstance(data, dict):
                raise BioPortalRequestError("Unexpected submission format", endpoint=self._endpoint_label(url))
            return {"version": data.get("version"), "released": data.get("released")}

        return self._guard(run, raise_errors, None)


# ============================================================
# Standalone check (makes a few LIVE requests to BioPortal)
# ============================================================

if __name__ == "__main__":

    logging.basicConfig(level=logging.INFO)

    client = BioPortalClient()

    for concept_id in ("233604007", "73211009"):
        concept = client.get_concept(concept_id, raise_errors=True)
        print(concept_id, concept.get("prefLabel") if concept else None)
        for parent in client.get_parents(concept_id, raise_errors=True):
            print("  parent:", parent["concept_id"], parent["name"])

    for result in client.search_concept("Diabetes mellitus", raise_errors=True):
        print("search:", result["concept_id"], result["name"])

    print("Version:", client.get_ontology_version())
    print("HTTP requests made:", client.request_count)
