"""The Streamlit "Ask and verify" page renders and answers with the mock model (no API calls)."""

import json
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from tests.test_text_retrieval import MI, db  # noqa: F401,E402  (fixture)

PAGE = str(Path(__file__).resolve().parents[1] / "pages" / "1_Ask_and_verify.py")


@pytest.fixture
def page(db, tmp_path, monkeypatch):
    config = tmp_path / "demo.json"
    config.write_text(json.dumps({"models": [{"name": "mock", "provider": "mock", "model": "mock-1"}]}),
                      encoding="utf-8")
    monkeypatch.setenv("SNOMED_DB", str(db))
    monkeypatch.setenv("SNOMED_DENSE_DIR", str(tmp_path / "no_dense"))
    monkeypatch.setenv("SNOMED_DEMO_CONFIG", str(config))
    monkeypatch.setenv("SNOMED_DEMO_CACHE", str(tmp_path / "cache.jsonl"))
    app = AppTest.from_file(PAGE, default_timeout=60)
    app.run()
    return app


def test_page_loads_and_asks(page):
    assert not page.exception
    assert page.title[0].value.startswith("🔎 Ask and verify")
    page.text_input(key="x_q").input(MI).run()                   # a concept ID
    assert not page.exception
    page.button[0].click().run()                                  # Ask (G-QA, task T1)
    assert not page.exception
    texts = " ".join(m.value for m in page.markdown)
    assert "SNOMED CT says:" in texts
    assert any(m.label == "Evidence sufficient" for m in page.metric)


def test_compare_all_systems(page):
    page.text_input(key="x_q").input("myocardial infarction").run()
    assert not page.exception
    page.button[1].click().run()                                  # Compare all systems
    assert not page.exception
    subheaders = [s.value for s in page.subheader]
    assert "Summary" in subheaders and any(s.startswith("G-QA+V") for s in subheaders)
