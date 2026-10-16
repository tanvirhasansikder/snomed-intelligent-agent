"""
Headless Streamlit tests for main.py using streamlit.testing.v1.AppTest.
QueryProcessor is replaced by a fake, so no BioPortal or Neo4j access occurs.
These tests check behaviour (messages shown, no crash), not visual layout.
"""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import agent.query_processor as qp_module
from tests.conftest import FAKE_API_KEY

MAIN = str(Path(__file__).resolve().parent.parent / "main.py")


class FakeProcessor:
    result = None
    instances = []

    def __init__(self, expansion_depth=2, **kwargs):
        self.kwargs = dict(expansion_depth=expansion_depth, **kwargs)
        FakeProcessor.instances.append(self)

    def process_query(self, query):
        return dict(FakeProcessor.result, query=query)


@pytest.fixture
def fake_processor(monkeypatch):
    FakeProcessor.instances = []
    monkeypatch.setattr(qp_module, "QueryProcessor", FakeProcessor)
    return FakeProcessor


def all_text(at):
    parts = [m.value for m in at.markdown] + [i.value for i in at.info] + \
            [w.value for w in at.warning] + [e.value for e in at.error] + \
            [s.value for s in at.success] + [c.value for c in at.caption]
    return "\n".join(str(p) for p in parts)


def run_query(text):
    at = AppTest.from_file(MAIN, default_timeout=30).run()
    at.text_input[0].input(text)
    at.button[0].click().run()
    return at


def test_app_starts_without_exceptions():
    at = AppTest.from_file(MAIN, default_timeout=30).run()
    assert len(at.exception) == 0


def test_app_states_llm_explanations_are_not_implemented():
    at = AppTest.from_file(MAIN, default_timeout=30).run()
    assert "not yet implemented" in all_text(at).lower()


def test_empty_query_shows_warning(fake_processor):
    at = run_query("   ")
    assert any("enter" in w.value.lower() for w in at.warning)
    assert fake_processor.instances == []


SUCCESS = {
    "success": True,
    "concept": {"concept_id": "195967001", "name": "Asthma", "parents": []},
    "parents": [{"concept_id": "50043002", "name": "Disorder of respiratory system"}],
    "children": [],
    "ancestors": [{"concept_id": "50043002", "name": "Disorder of respiratory system"}],
    "coverage": {
        "parents": {"status": "complete", "count": 1},
        "children": {"status": "not_retrieved"},
        "ancestors": {"status": "complete_to_depth", "depth": 2, "frontier": []},
    },
    "resolution": {"method": "bioportal_exact_label", "ambiguous": False},
    "candidates": [],
    "warnings": [],
    "error": None,
    "error_type": None,
}


def test_successful_query_shows_concept_and_children_not_retrieved(fake_processor):
    fake_processor.result = SUCCESS

    at = run_query("asthma")
    text = all_text(at)

    assert len(at.exception) == 0
    assert "Asthma" in text
    assert "Disorder of respiratory system" in text
    assert "does not mean" in text.lower()


def test_failed_query_shows_error_without_traceback_or_success(fake_processor):
    fake_processor.result = {
        "success": False, "concept": None, "parents": [], "children": [],
        "error_type": "source_unavailable", "error": f"HTTP 503 token {FAKE_API_KEY}",
        "warnings": [], "coverage": {}, "candidates": [], "resolution": None,
    }

    at = run_query("asthma")

    assert len(at.exception) == 0
    assert len(at.error) == 1
    assert len(at.success) == 0
    assert FAKE_API_KEY not in all_text(at)


def test_child_retrieval_toggle_is_passed_to_processor(fake_processor):
    fake_processor.result = SUCCESS

    run_query("asthma")

    assert fake_processor.instances[-1].kwargs.get("fetch_children") is True


def test_labels_are_html_escaped(fake_processor):
    fake_processor.result = dict(SUCCESS, concept={"concept_id": "1", "name": "<b>bold</b>"})

    at = run_query("x")

    assert "<b>bold</b>" not in all_text(at)
    assert "&lt;b&gt;bold&lt;/b&gt;" in all_text(at)


def test_missing_neo4j_settings_show_configuration_message(monkeypatch):
    """Uses the real QueryProcessor; construction fails before any network call."""
    monkeypatch.delenv("NEO4J_URI", raising=False)

    at = run_query("asthma")

    assert len(at.exception) == 0
    assert len(at.error) == 1
    assert ".env" in at.error[0].value
    assert "NEO4J_URI" in all_text(at)
