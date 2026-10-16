"""
Neo4j connection handling.

* Settings come from NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD and the
  optional NEO4J_DATABASE (``.env`` in the project root is loaded by
  config.settings). A missing setting raises Neo4jConfigurationError that
  names the variable but never prints its value.
* One driver per (URI, user) is shared by every Neo4jConnection in the
  process, created on first use. The original created a new driver for every
  QueryProcessor, GraphBuilder and crawler and never closed them.
"""

import logging
import threading

from neo4j import GraphDatabase

from config.settings import get_setting

logger = logging.getLogger(__name__)

REQUIRED_SETTINGS = ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD")

_DRIVERS = {}
_LOCK = threading.Lock()


class Neo4jConfigurationError(RuntimeError):
    """Neo4j settings are missing or invalid."""


class Neo4jConnection:

    def __init__(self, uri=None, username=None, password=None, database=None, driver_factory=None):
        values = {
            "NEO4J_URI": uri or get_setting("NEO4J_URI"),
            "NEO4J_USERNAME": username or get_setting("NEO4J_USERNAME"),
            "NEO4J_PASSWORD": password or get_setting("NEO4J_PASSWORD"),
        }
        missing = [name for name in REQUIRED_SETTINGS if not values[name]]
        if missing:
            raise Neo4jConfigurationError(
                "Missing Neo4j setting(s): " + ", ".join(missing)
                + ". Add them to the .env file in the project root (see .env.example)."
            )

        self._uri = values["NEO4J_URI"]
        self._username = values["NEO4J_USERNAME"]
        self._password = values["NEO4J_PASSWORD"]
        self.database = database or get_setting("NEO4J_DATABASE")
        self._driver_factory = driver_factory or GraphDatabase.driver
        self._key = (self._uri, self._username)

    def __repr__(self):
        return f"Neo4jConnection(uri={self._uri!r}, user={self._username!r}, password=<hidden>)"

    @property
    def driver(self):
        with _LOCK:
            driver = _DRIVERS.get(self._key)
            if driver is None:
                driver = self._driver_factory(self._uri, auth=(self._username, self._password))
                _DRIVERS[self._key] = driver
            return driver

    def close(self):
        """Close the shared driver; the next query opens a new one."""
        with _LOCK:
            driver = _DRIVERS.pop(self._key, None)
        if driver is not None:
            driver.close()

    def execute_query(self, query, parameters=None):
        with self.driver.session(database=self.database) as session:
            result = session.run(query, parameters or {})
            return [record.data() for record in result]


def close_all_drivers():
    """Close every shared driver (useful at process shutdown and in tests)."""
    with _LOCK:
        drivers = list(_DRIVERS.values())
        _DRIVERS.clear()
    for driver in drivers:
        driver.close()


if __name__ == "__main__":
    db = Neo4jConnection()

    result = db.execute_query(
        "RETURN 'Connected to Neo4j successfully!' AS message"
    )

    print(result)

    db.close()
