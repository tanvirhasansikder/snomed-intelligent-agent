"""Neo4jConnection configuration tests. No driver ever connects to a server."""

import pytest

import database.neo4j_connection as conn_module
from database.neo4j_connection import Neo4jConnection


class FakeResult(list):
    pass


class FakeRecord:
    def __init__(self, data):
        self._data = data

    def data(self):
        return dict(self._data)


class FakeNeoSession:
    def __init__(self, owner, database):
        self.owner = owner
        self.database = database

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def run(self, query, parameters):
        self.owner.runs.append((query, parameters, self.database))
        return [FakeRecord({"ok": 1})]


class FakeDriver:
    def __init__(self):
        self.runs = []
        self.closed = False

    def session(self, database=None):
        return FakeNeoSession(self, database)

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def _reset_shared_drivers():
    if hasattr(conn_module, "_DRIVERS"):
        conn_module._DRIVERS.clear()
    yield
    if hasattr(conn_module, "_DRIVERS"):
        conn_module._DRIVERS.clear()


@pytest.mark.parametrize("missing", ["NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD"])
def test_missing_setting_is_named_in_error_without_values(monkeypatch, missing):
    """Defect: a missing NEO4J_URI produced 'URI scheme b'' is not supported'."""
    monkeypatch.delenv(missing, raising=False)

    with pytest.raises(conn_module.Neo4jConfigurationError) as excinfo:
        Neo4jConnection()

    message = str(excinfo.value)
    assert missing in message
    assert "unit-test-password" not in message


def test_driver_is_created_lazily_and_shared(monkeypatch):
    created = []

    def factory(uri, auth):
        created.append((uri, auth[0]))
        return FakeDriver()

    a = Neo4jConnection(driver_factory=factory)
    b = Neo4jConnection(driver_factory=factory)
    assert created == []

    a.execute_query("RETURN 1 AS ok")
    b.execute_query("RETURN 1 AS ok")

    assert created == [("bolt://unit-test-invalid-host:7687", "unit-test-user")]


def test_execute_query_returns_record_dicts_and_passes_parameters():
    driver = FakeDriver()
    db = Neo4jConnection(driver_factory=lambda uri, auth: driver)

    rows = db.execute_query("RETURN $x AS ok", {"x": 1})

    assert rows == [{"ok": 1}]
    assert driver.runs[0][:2] == ("RETURN $x AS ok", {"x": 1})


def test_optional_database_name_is_used(monkeypatch):
    monkeypatch.setenv("NEO4J_DATABASE", "snomed")
    driver = FakeDriver()
    db = Neo4jConnection(driver_factory=lambda uri, auth: driver)

    db.execute_query("RETURN 1 AS ok")

    assert driver.runs[0][2] == "snomed"


def test_close_closes_driver_and_next_query_reconnects():
    drivers = []

    def factory(uri, auth):
        drivers.append(FakeDriver())
        return drivers[-1]

    db = Neo4jConnection(driver_factory=factory)
    db.execute_query("RETURN 1 AS ok")
    db.close()
    db.execute_query("RETURN 1 AS ok")

    assert drivers[0].closed is True
    assert len(drivers) == 2


def test_repr_hides_password():
    db = Neo4jConnection(driver_factory=lambda uri, auth: FakeDriver())
    assert "unit-test-password" not in repr(db)
