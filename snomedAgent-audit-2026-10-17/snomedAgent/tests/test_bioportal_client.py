"""
BioPortal client tests. All HTTP traffic goes to FakeSession; nothing is sent
to BioPortal. Expected URLs follow the BioPortal REST documentation
(https://data.bioontology.org/documentation): class endpoints are
/ontologies/{acronym}/classes/{url-encoded class IRI}[/parents|/children].
"""

import logging

import pytest
import requests

from crawler.bioportal_client import BioPortalClient
from tests.fakes import (
    SNOMED_PREFIX,
    BioPortalRequestError,
    FakeResponse,
    FakeSession,
    bioportal_class,
)

PNEUMONIA = "233604007"
ENCODED_PNEUMONIA = "http%3A%2F%2Fpurl.bioontology.org%2Fontology%2FSNOMEDCT%2F233604007"


def make_client(handler):
    client = BioPortalClient()
    client.session = FakeSession(handler)
    if hasattr(client, "sleep"):
        client.sleep = lambda seconds: None  # no real waiting in tests
    return client


def always(response):
    return lambda url, params: response


# ----------------------------------------------------------------------
# URI construction and ID extraction
# ----------------------------------------------------------------------

def test_build_class_uri_uses_bioportal_snomed_purl():
    client = make_client(always(FakeResponse(200, {})))
    assert client.build_class_uri(PNEUMONIA) == SNOMED_PREFIX + PNEUMONIA


@pytest.mark.parametrize(
    "item, expected",
    [
        ({"@id": SNOMED_PREFIX + "73211009"}, "73211009"),
        ({"id": SNOMED_PREFIX + "73211009"}, "73211009"),
        ({"links": {"self": SNOMED_PREFIX + "73211009"}}, "73211009"),
        ({"nested": {"@id": SNOMED_PREFIX + "73211009"}}, "73211009"),
        (SNOMED_PREFIX + "73211009", "73211009"),
        (SNOMED_PREFIX + "73211009#fragment", "73211009"),
        ({"@id": "http://example.org/other/123456"}, None),
        ({"@id": SNOMED_PREFIX + "not-a-number"}, None),
        ({}, None),
        (None, None),
        (12345678, None),
    ],
)
def test_extract_concept_id_supported_and_unsupported_formats(item, expected):
    client = make_client(always(FakeResponse(200, {})))
    assert client.extract_concept_id(item) == expected


# ----------------------------------------------------------------------
# get_concept
# ----------------------------------------------------------------------

def test_get_concept_success_requests_encoded_class_url_with_timeout():
    client = make_client(always(FakeResponse(200, {"prefLabel": "Pneumonia"})))

    result = client.get_concept(PNEUMONIA)

    assert result["prefLabel"] == "Pneumonia"
    call = client.session.calls[0]
    assert call["url"] == (
        "https://data.bioontology.org/ontologies/SNOMEDCT/classes/" + ENCODED_PNEUMONIA
    )
    assert call["timeout"] is not None


def test_get_concept_not_found_is_none_not_an_error():
    client = make_client(always(FakeResponse(404, {"errors": ["not found"]})))

    assert client.get_concept("999999999") is None
    assert client.get_concept("999999999", raise_errors=True) is None


def test_get_concept_server_error_raises_in_strict_mode():
    """Defect: the original returned None for both 'not found' and 'server error'."""
    client = make_client(always(FakeResponse(500, text="boom")))

    with pytest.raises(BioPortalRequestError):
        client.get_concept(PNEUMONIA, raise_errors=True)


def test_get_concept_server_error_default_mode_records_last_error():
    client = make_client(always(FakeResponse(500, text="boom")))

    assert client.get_concept(PNEUMONIA) is None
    assert client.last_error is not None


def test_invalid_json_body_does_not_crash_default_mode():
    """Defect: response.json() ValueError escaped the RequestException handler."""
    client = make_client(always(FakeResponse(200, invalid_json=True)))

    assert client.get_concept(PNEUMONIA) is None
    with pytest.raises(BioPortalRequestError):
        client.get_concept(PNEUMONIA, raise_errors=True)


# ----------------------------------------------------------------------
# Timeouts, retries and backoff
# ----------------------------------------------------------------------

def test_timeouts_are_retried_a_bounded_number_of_times():
    client = make_client(lambda url, params: requests.exceptions.Timeout("slow"))

    with pytest.raises(BioPortalRequestError):
        client.get_parents(PNEUMONIA, raise_errors=True)

    assert 2 <= len(client.session.calls) <= 4


def test_rate_limit_then_success_is_retried():
    responses = [
        FakeResponse(429, text="slow down", headers={"Retry-After": "1"}),
        FakeResponse(200, [bioportal_class("205237003", "Pneumonitis")]),
    ]
    client = make_client(lambda url, params: responses.pop(0))

    parents = client.get_parents(PNEUMONIA)

    assert parents == [{"concept_id": "205237003", "name": "Pneumonitis"}]
    assert len(client.session.calls) == 2


@pytest.mark.parametrize("status", [400, 401, 403])
def test_client_errors_are_not_retried(status):
    client = make_client(always(FakeResponse(status, text="denied")))

    with pytest.raises(BioPortalRequestError):
        client.get_parents(PNEUMONIA, raise_errors=True)

    assert len(client.session.calls) == 1


def test_parent_request_failure_is_distinguishable_from_no_parents():
    failing = make_client(always(FakeResponse(503, text="unavailable")))
    root = make_client(always(FakeResponse(200, [])))

    with pytest.raises(BioPortalRequestError):
        failing.get_parents(PNEUMONIA, raise_errors=True)
    assert root.get_parents("138875005", raise_errors=True) == []


# ----------------------------------------------------------------------
# Search
# ----------------------------------------------------------------------

def test_search_sends_query_restricted_to_snomedct():
    client = make_client(always(FakeResponse(200, {"collection": []})))

    assert client.search_concept("asthma") == []
    params = client.session.calls[0]["params"]
    assert params["q"] == "asthma"
    assert params["ontologies"] == "SNOMEDCT"


def test_search_skips_invalid_entries_and_keeps_valid_ones():
    collection = [
        bioportal_class("195967001", "Asthma"),
        {"prefLabel": "entry without id"},
        {"@id": "http://example.org/not-snomed/1", "prefLabel": "other ontology"},
        "garbage",
        None,
    ]
    client = make_client(always(FakeResponse(200, {"collection": collection})))

    assert client.search_concept("asthma") == [{"concept_id": "195967001", "name": "Asthma"}]


def test_search_missing_collection_key_returns_empty_list():
    client = make_client(always(FakeResponse(200, {"unexpected": True})))
    assert client.search_concept("asthma") == []


def test_search_label_falls_back_to_concept_id():
    client = make_client(always(FakeResponse(200, {"collection": [bioportal_class("195967001", "", with_label=False)]})))
    assert client.search_concept("asthma") == [{"concept_id": "195967001", "name": "195967001"}]


# ----------------------------------------------------------------------
# Parents
# ----------------------------------------------------------------------

@pytest.mark.parametrize("wrap", ["list", "collection"])
def test_get_parents_accepts_list_and_collection_formats(wrap):
    items = [bioportal_class("205237003", "Pneumonitis"), bioportal_class("50417007", "Lower respiratory tract infection")]
    body = items if wrap == "list" else {"collection": items}
    client = make_client(always(FakeResponse(200, body)))

    parents = client.get_parents(PNEUMONIA)

    assert {p["concept_id"] for p in parents} == {"205237003", "50417007"}
    assert client.session.calls[0]["url"].endswith(ENCODED_PNEUMONIA + "/parents")


def test_get_parents_looks_up_missing_labels():
    def handler(url, params):
        if url.endswith("/parents"):
            return FakeResponse(200, [bioportal_class("205237003", "", with_label=False)])
        return FakeResponse(200, {"prefLabel": "Pneumonitis"})

    client = make_client(handler)

    assert client.get_parents(PNEUMONIA) == [{"concept_id": "205237003", "name": "Pneumonitis"}]


def test_get_parents_skips_malformed_records():
    body = [bioportal_class("205237003", "Pneumonitis"), {"prefLabel": "no id"}, "junk", None]
    client = make_client(always(FakeResponse(200, body)))

    assert client.get_parents(PNEUMONIA) == [{"concept_id": "205237003", "name": "Pneumonitis"}]


# ----------------------------------------------------------------------
# Children (direct children, paginated)
# ----------------------------------------------------------------------

def test_get_children_uses_children_endpoint_not_descendants():
    """Defect: the original called /descendants (all levels) and named it children."""
    client = make_client(always(FakeResponse(200, {"page": 1, "pageCount": 1, "nextPage": None, "collection": []})))

    client.get_children(PNEUMONIA)

    assert client.session.calls[0]["url"].endswith(ENCODED_PNEUMONIA + "/children")


def _paged_handler(pages):
    def handler(url, params):
        page = int(params.get("page", 1))
        return FakeResponse(200, pages[page - 1])
    return handler


TWO_PAGES = [
    {"page": 1, "pageCount": 2, "nextPage": 2, "collection": [bioportal_class("111111001", "Child one")]},
    {"page": 2, "pageCount": 2, "nextPage": None, "collection": [bioportal_class("222222002", "Child two")]},
]


def test_get_children_follows_pagination():
    client = make_client(_paged_handler(TWO_PAGES))

    children = client.get_children(PNEUMONIA)

    assert [c["concept_id"] for c in children] == ["111111001", "222222002"]
    assert len(client.session.calls) == 2


def test_fetch_children_reports_truncation_when_page_cap_reached():
    client = make_client(_paged_handler(TWO_PAGES))

    result = client.fetch_children(PNEUMONIA, max_pages=1)

    assert [c["concept_id"] for c in result["children"]] == ["111111001"]
    assert result["complete"] is False


def test_fetch_children_complete_when_all_pages_read():
    client = make_client(_paged_handler(TWO_PAGES))

    result = client.fetch_children(PNEUMONIA, max_pages=5)

    assert result["complete"] is True


# ----------------------------------------------------------------------
# Credentials
# ----------------------------------------------------------------------

def test_missing_api_key_raises_clear_error(monkeypatch):
    monkeypatch.delenv("BIOPORTAL_API_KEY", raising=False)

    with pytest.raises(ValueError, match="BIOPORTAL_API_KEY"):
        BioPortalClient()


def test_api_key_sent_as_authorization_header(fake_api_key):
    client = BioPortalClient()
    assert client.session.headers["Authorization"] == f"apikey token={fake_api_key}"


def test_api_key_not_exposed_in_repr_errors_or_logs(fake_api_key, caplog):
    client = make_client(always(FakeResponse(500, text="internal error")))

    with caplog.at_level(logging.DEBUG):
        client.get_concept(PNEUMONIA)
        with pytest.raises(BioPortalRequestError) as excinfo:
            client.get_concept(PNEUMONIA, raise_errors=True)

    assert fake_api_key not in repr(client)
    assert fake_api_key not in str(excinfo.value)
    assert fake_api_key not in caplog.text
    for call in client.session.calls:
        assert fake_api_key not in call["url"]
        assert fake_api_key not in str(call["params"])


@pytest.mark.parametrize("next_page", [
    2,
    "2",
    "https://data.bioontology.org/ontologies/SNOMEDCT/classes/x/children?page=2&pagesize=100",
])
def test_pagination_accepts_number_or_url_for_next_page(next_page):
    pages = [
        {"page": 1, "pageCount": 2, "nextPage": next_page, "collection": [bioportal_class("111111001", "One")]},
        {"page": 2, "pageCount": 2, "nextPage": None, "collection": [bioportal_class("222222002", "Two")]},
    ]
    client = make_client(_paged_handler(pages))

    result = client.fetch_children(PNEUMONIA)

    assert [c["concept_id"] for c in result["children"]] == ["111111001", "222222002"]
    assert result["complete"] is True


def test_pagination_reads_next_page_from_links():
    pages = [
        {"page": 1, "pageCount": 2, "links": {"nextPage": "https://x/children?page=2"},
         "collection": [bioportal_class("111111001", "One")]},
        {"page": 2, "pageCount": 2, "links": {"nextPage": None}, "collection": [bioportal_class("222222002", "Two")]},
    ]
    client = make_client(_paged_handler(pages))

    assert len(client.fetch_children(PNEUMONIA)["children"]) == 2


def test_pagination_that_does_not_advance_stops_and_is_partial():
    stuck = {"page": 1, "pageCount": 5, "nextPage": 1, "collection": [bioportal_class("111111001", "One")]}
    client = make_client(lambda url, params: FakeResponse(200, stuck))

    result = client.fetch_children(PNEUMONIA, max_pages=5)

    assert result["complete"] is False
    assert len(client.session.calls) == 1
