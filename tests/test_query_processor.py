"""
QueryProcessor tests with fakes only.

The SCTID check-digit test uses real, well-known SNOMED CT concept IDs. SNOMED
CT identifiers end in a Verhoeff check digit, so every real ID must validate
and every single-digit change must fail -- an expectation that comes from the
SNOMED CT identifier specification, not from this code.
"""

import pytest
from neo4j.exceptions import ServiceUnavailable

from agent.query_processor import QueryProcessor
from knowledge_graph.graph_builder import GraphBuilder
from tests.fakes import BioPortalRequestError, FakeOntologyClient, InMemoryRepository, SYNTHETIC_LABELS as LABEL

REAL_SCTIDS = [
    "138875005",  # SNOMED CT Concept (root)
    "404684003",  # Clinical finding
    "233604007",  # Pneumonia
    "73211009",   # Diabetes mellitus
    "195967001",  # Asthma
    "22298006",   # Myocardial infarction
    "38341003",   # Hypertensive disorder
]


def mutate_last_digit(sctid):
    return sctid[:-1] + str((int(sctid[-1]) + 1) % 10)


def make_processor(client=None, repo=None, depth=2, fetch_children=False):
    client = client or FakeOntologyClient()
    repo = repo or InMemoryRepository()
    builder = GraphBuilder(default_depth=depth, repository=repo, client=client)
    return QueryProcessor(
        expansion_depth=depth,
        repository=repo,
        graph_builder=builder,
        bioportal=client,
        fetch_children=fetch_children,
    )


# ----------------------------------------------------------------------
# Identifier validation
# ----------------------------------------------------------------------

@pytest.mark.parametrize("sctid", REAL_SCTIDS)
def test_real_sctids_pass_check_digit(sctid):
    assert QueryProcessor.is_valid_sctid(sctid) is True


@pytest.mark.parametrize("sctid", [mutate_last_digit(s) for s in REAL_SCTIDS])
def test_mutated_sctids_fail_check_digit(sctid):
    assert QueryProcessor.is_valid_sctid(sctid) is False


@pytest.mark.parametrize("text", ["12345", "1" * 19, "abc123456", "", "  "])
def test_non_identifiers_are_not_sctids(text):
    assert QueryProcessor.is_valid_sctid(text) is False


def test_invalid_check_digit_is_rejected_without_api_calls():
    client = FakeOntologyClient()
    result = make_processor(client=client).process_query(mutate_last_digit("233604007"))

    assert result["success"] is False
    assert result["error_type"] == "invalid_identifier"
    assert client.request_count == 0


# ----------------------------------------------------------------------
# Input validation
# ----------------------------------------------------------------------

@pytest.mark.parametrize("query", ["", "   ", "x" * 201, None, 42])
def test_invalid_input_is_rejected_without_api_calls(query):
    client = FakeOntologyClient()
    result = make_processor(client=client).process_query(query)

    assert result["success"] is False
    assert result["error_type"] == "invalid_input"
    assert client.request_count == 0


# ----------------------------------------------------------------------
# Resolution
# ----------------------------------------------------------------------

def test_resolution_by_id_already_in_local_graph():
    repo = InMemoryRepository()
    repo.save_concept("233604007", "Pneumonia")
    client = FakeOntologyClient(parents={"233604007": []}, labels={"233604007": "Pneumonia"})

    result = make_processor(client=client, repo=repo).process_query("233604007")

    assert result["success"] is True
    assert result["resolution"]["method"] == "local_id"
    assert client.calls["search_concept"] == []


def test_resolution_by_id_from_bioportal():
    client = FakeOntologyClient(parents={"233604007": []}, labels={"233604007": "Pneumonia"})

    result = make_processor(client=client).process_query("233604007")

    assert result["concept"]["name"] == "Pneumonia"
    assert result["resolution"]["method"] == "bioportal_id"


def test_unknown_valid_id_is_not_found():
    result = make_processor().process_query("233604007")

    assert result["success"] is False
    assert result["error_type"] == "not_found"


def test_resolution_by_exact_local_name_is_case_insensitive():
    repo = InMemoryRepository()
    repo.save_concept("700", "Disorder AB type 1 variant")
    client = FakeOntologyClient()

    result = make_processor(client=client, repo=repo).process_query("disorder ab TYPE 1 variant")

    assert result["concept"]["concept_id"] == "700"
    assert result["resolution"]["method"] == "local_name"
    assert client.calls["search_concept"] == []


def test_bioportal_exact_label_preferred_over_first_result():
    """Defect: the original always took results[0]."""
    client = FakeOntologyClient(search_results={"disorder ab": [
        {"concept_id": "500", "name": "Disorder AB type 1"},
        {"concept_id": "400", "name": "Disorder AB"},
    ]})

    result = make_processor(client=client).process_query("Disorder AB")

    assert result["concept"]["concept_id"] == "400"
    assert result["resolution"]["method"] == "bioportal_exact_label"
    assert result["resolution"]["ambiguous"] is False
    assert [c["concept_id"] for c in result["candidates"]] == ["500", "400"]


def test_no_exact_label_takes_top_result_and_flags_ambiguity():
    client = FakeOntologyClient(search_results={"disorder": [
        {"concept_id": "500", "name": "Disorder AB type 1"},
        {"concept_id": "600", "name": "Disorder AB type 2"},
    ]})

    result = make_processor(client=client).process_query("disorder")

    assert result["concept"]["concept_id"] == "500"
    assert result["resolution"]["method"] == "bioportal_top_result"
    assert result["resolution"]["ambiguous"] is True


def test_no_search_results_is_not_found():
    result = make_processor().process_query("nonexistent concept")

    assert result["success"] is False
    assert result["error_type"] == "not_found"


def test_search_failure_is_source_unavailable_not_not_found():
    """Defect: the original reported an outage as 'unable to resolve'."""
    client = FakeOntologyClient(search_results={"asthma": BioPortalRequestError("HTTP 503")})

    result = make_processor(client=client).process_query("asthma")

    assert result["success"] is False
    assert result["error_type"] == "source_unavailable"


def test_database_failure_is_reported_not_raised():
    class BrokenRepository(InMemoryRepository):
        def find_concept_by_exact_name(self, name):
            raise ServiceUnavailable("Couldn't connect to localhost:7687")

    result = make_processor(repo=BrokenRepository()).process_query("asthma")

    assert result["success"] is False
    assert result["error_type"] == "database_error"


def test_legacy_resolve_concept_still_returns_dict_or_none():
    repo = InMemoryRepository()
    repo.save_concept("700", "Disorder AB type 1 variant")
    processor = make_processor(repo=repo)

    assert processor.resolve_concept("Disorder AB type 1 variant")["concept_id"] == "700"
    assert processor.resolve_concept("nothing like this") is None


# ----------------------------------------------------------------------
# Hierarchy answers and completeness
# ----------------------------------------------------------------------

def test_result_keeps_original_keys():
    """main.py and other callers rely on these five keys."""
    result = make_processor(repo=_repo_with("700")).process_query(LABEL["700"])
    for key in ("success", "query", "concept", "parents", "children"):
        assert key in result


def _repo_with(*ids):
    repo = InMemoryRepository()
    for cid in ids:
        repo.save_concept(cid, LABEL[cid])
    return repo


def test_parents_and_ancestors_from_expansion():
    result = make_processor(repo=_repo_with("700"), depth=3).process_query(LABEL["700"])

    assert [p["concept_id"] for p in result["parents"]] == ["500"]
    assert {a["concept_id"] for a in result["ancestors"]} == {"500", "400", "200", "300"}
    assert result["coverage"]["parents"]["status"] == "complete"


def test_children_not_fetched_are_not_reported_as_absent():
    """Defect: an empty local child list was shown as 'no direct children'."""
    result = make_processor(repo=_repo_with("400"), fetch_children=False).process_query(LABEL["400"])

    assert result["children"] == []
    assert result["coverage"]["children"]["status"] == "not_retrieved"


def test_children_fetched_when_enabled():
    result = make_processor(repo=_repo_with("400"), fetch_children=True).process_query(LABEL["400"])

    assert [c["concept_id"] for c in result["children"]] == ["500", "600"]
    assert result["coverage"]["children"]["status"] == "complete"


def test_leaf_with_fetched_children_is_confirmed_empty():
    result = make_processor(repo=_repo_with("700"), fetch_children=True).process_query(LABEL["700"])

    assert result["children"] == []
    assert result["coverage"]["children"]["status"] == "complete"
    assert result["coverage"]["children"]["count"] == 0


def test_root_has_confirmed_no_parents():
    result = make_processor(repo=_repo_with("100")).process_query(LABEL["100"])

    assert result["parents"] == []
    assert result["coverage"]["parents"]["status"] == "complete"


def test_parent_retrieval_failure_is_visible():
    client = FakeOntologyClient(fail_parents={"700"})

    result = make_processor(client=client, repo=_repo_with("700")).process_query(LABEL["700"])

    assert result["success"] is True
    assert result["parents"] == []
    assert result["coverage"]["parents"]["status"] == "failed"
    assert result["warnings"]


def test_ancestor_coverage_flags_frontier_beyond_depth():
    result = make_processor(repo=_repo_with("700"), depth=1).process_query(LABEL["700"])

    assert result["coverage"]["ancestors"]["status"] == "complete_to_depth"
    assert result["coverage"]["ancestors"]["depth"] == 1
    assert result["coverage"]["ancestors"]["frontier"] == ["500"]


def test_ancestor_coverage_partial_when_a_level_failed():
    client = FakeOntologyClient(fail_parents={"500"})

    result = make_processor(client=client, repo=_repo_with("700"), depth=3).process_query(LABEL["700"])

    assert result["coverage"]["ancestors"]["status"] == "partial"


@pytest.mark.parametrize("depth", [0, -1, 11, "2"])
def test_invalid_expansion_depth_rejected(depth):
    with pytest.raises((ValueError, TypeError)):
        QueryProcessor(expansion_depth=depth, repository=InMemoryRepository(),
                       bioportal=FakeOntologyClient())


def test_missing_bioportal_key_is_configuration_error(monkeypatch):
    monkeypatch.delenv("BIOPORTAL_API_KEY", raising=False)
    repo = InMemoryRepository()
    processor = QueryProcessor(expansion_depth=2, repository=repo)  # real client, created lazily

    result = processor.process_query("asthma")

    assert result["success"] is False
    assert result["error_type"] == "configuration_error"
    assert "BIOPORTAL_API_KEY" in result["error"]
