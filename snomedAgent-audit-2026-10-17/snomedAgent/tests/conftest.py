"""
Shared pytest configuration.

Safety rules enforced for every test unless it is marked ``integration``:

* real HTTP requests are blocked (BioPortal is never contacted);
* real Neo4j drivers cannot be created (your database is never touched);
* BioPortal/Neo4j environment variables are replaced with fake values, so a
  real ``.env`` on your machine is never used by unit tests.
"""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


FAKE_API_KEY = "test-api-key-DO-NOT-LEAK-1234"


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: needs a disposable Neo4j database (skipped by default)",
    )


@pytest.fixture(autouse=True)
def _isolate_environment(monkeypatch, request):
    """Replace credentials and block real network / database access."""

    if request.node.get_closest_marker("integration"):
        yield
        return

    monkeypatch.setenv("BIOPORTAL_API_KEY", FAKE_API_KEY)
    monkeypatch.setenv("NEO4J_URI", "bolt://unit-test-invalid-host:7687")
    monkeypatch.setenv("NEO4J_USERNAME", "unit-test-user")
    monkeypatch.setenv("NEO4J_PASSWORD", "unit-test-password")

    import requests

    def _blocked_send(self, *args, **kwargs):
        raise RuntimeError("Real network access is disabled in unit tests")

    monkeypatch.setattr(requests.sessions.Session, "send", _blocked_send)

    import neo4j

    def _blocked_driver(*args, **kwargs):
        raise RuntimeError("Real Neo4j drivers are disabled in unit tests")

    monkeypatch.setattr(neo4j.GraphDatabase, "driver", _blocked_driver)

    yield


@pytest.fixture
def fake_api_key():
    return FAKE_API_KEY
