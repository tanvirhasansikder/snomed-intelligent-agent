"""Tests for UI helper logic in ui/view_model.py (no Streamlit rendering)."""

import pytest

from tests.conftest import FAKE_API_KEY
from ui import view_model as vm


# ----------------------------------------------------------------------
# Helpers moved unchanged from main.py
# ----------------------------------------------------------------------

def test_extract_concept_id_from_nested_concept():
    assert vm.extract_concept_id({"concept": {"concept_id": "233604007"}}) == "233604007"
    assert vm.extract_concept_id({"concept": None}) == "Unknown"
    assert vm.extract_concept_id("not a dict") == "Unknown"


def test_extract_concept_name_from_nested_concept():
    assert vm.extract_concept_name({"concept": {"name": "Pneumonia"}}) == "Pneumonia"
    assert vm.extract_concept_name({}) == "Unknown"


def test_normalize_concepts_deduplicates_and_stringifies():
    items = [
        {"concept_id": "1", "name": "A"},
        {"concept_id": "1", "name": "A"},
        {"id": 2, "label": "B"},
        "loose string",
    ]
    assert vm.normalize_concepts(items) == [
        {"concept_id": "1", "name": "A"},
        {"concept_id": "2", "name": "B"},
        {"concept_id": "Unknown", "name": "loose string"},
    ]
    assert vm.normalize_concepts(None) == []


def test_safe_list():
    assert vm.safe_list(None) == []
    assert vm.safe_list([1]) == [1]
    assert vm.safe_list("x") == []


def test_escape_neutralises_html_in_labels():
    assert vm.escape("<script>alert(1)</script>") == "&lt;script&gt;alert(1)&lt;/script&gt;"
    assert vm.escape(None) == ""


# ----------------------------------------------------------------------
# Distinguishing 'not retrieved' from 'confirmed none'
# ----------------------------------------------------------------------

def result_with(section, status, items=(), count=None):
    coverage = {"status": status}
    if count is not None:
        coverage["count"] = count
    return {"success": True, section: list(items), "coverage": {section: coverage}}


def test_children_not_retrieved_and_empty_is_not_called_absent():
    level, text = vm.coverage_notice(result_with("children", "not_retrieved"), "children")

    assert level == "info"
    assert "not retrieved" in text.lower()
    assert "does not mean" in text.lower()


def test_children_complete_and_empty_is_a_confirmed_leaf():
    level, text = vm.coverage_notice(result_with("children", "complete", count=0), "children")

    assert "no direct children" in text.lower()
    assert "leaf" in text.lower()


def test_children_partial_warns_about_truncation():
    level, text = vm.coverage_notice(
        result_with("children", "partial", items=[{"concept_id": "1", "name": "A"}]), "children")

    assert level == "warning"
    assert "more" in text.lower()


def test_children_failed_warns():
    level, text = vm.coverage_notice(result_with("children", "failed"), "children")
    assert level == "warning"


def test_children_complete_with_items_needs_no_notice():
    assert vm.coverage_notice(
        result_with("children", "complete", items=[{"concept_id": "1", "name": "A"}]), "children") is None


def test_local_children_without_full_retrieval_are_labelled_partial_view():
    level, text = vm.coverage_notice(
        result_with("children", "not_retrieved", items=[{"concept_id": "1", "name": "A"}]), "children")

    assert "local graph" in text.lower()


def test_parents_complete_and_empty_is_top_level():
    level, text = vm.coverage_notice(result_with("parents", "complete", count=0), "parents")
    assert "top-level" in text.lower()


def test_parents_failed_and_empty_warns_parents_may_exist():
    level, text = vm.coverage_notice(result_with("parents", "failed"), "parents")
    assert level == "warning"
    assert "may have parents" in text.lower()


def test_missing_coverage_is_treated_as_not_retrieved():
    level, text = vm.coverage_notice({"success": True, "parents": []}, "parents")
    assert "not retrieved" in text.lower()


# ----------------------------------------------------------------------
# Error messages
# ----------------------------------------------------------------------

@pytest.mark.parametrize(
    "error_type, keyword",
    [
        ("invalid_input", "enter"),
        ("invalid_identifier", "check digit"),
        ("not_found", "no snomed ct concept"),
        ("source_unavailable", "bioportal"),
        ("database_error", "neo4j"),
        ("configuration_error", ".env"),
        ("unexpected_error", "unexpected"),
    ],
)
def test_error_message_per_type(error_type, keyword):
    message = vm.error_message({"success": False, "error_type": error_type, "error": "detail"})
    assert keyword in message.lower()


def test_error_message_never_contains_secret_values():
    message = vm.error_message({"success": False, "error_type": "source_unavailable",
                                "error": f"failed with token {FAKE_API_KEY}"})
    assert FAKE_API_KEY not in message
