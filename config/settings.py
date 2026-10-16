"""
Project configuration.

Loads ``.env`` from the project root (the folder containing main.py), so the
result no longer depends on the directory you start Python from. Values are
read from the environment; real values belong in ``.env``, which must never be
committed (see ``.env.example`` for the variable names).
"""

from pathlib import Path
import os

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"

# override=False: variables already set in the environment win over .env.
load_dotenv(ENV_FILE, override=False)

#: Label stored with every retrieval so the data source is recorded.
DATA_SOURCE = "BioPortal SNOMEDCT"


def get_setting(name, default=None):
    """Read a setting from the environment at call time (not import time)."""
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value


def missing_settings(*names):
    """Return the names (never the values) of settings that are not set."""
    return [name for name in names if get_setting(name) is None]


class Settings:
    """Snapshot of settings taken at import time (kept for compatibility)."""

    BIOPORTAL_API_KEY = os.getenv("BIOPORTAL_API_KEY")
    NEO4J_URI = os.getenv("NEO4J_URI")
    NEO4J_USERNAME = os.getenv("NEO4J_USERNAME")
    NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")
    NEO4J_DATABASE = os.getenv("NEO4J_DATABASE")

    def __repr__(self):
        return "Settings(<values hidden>)"


settings = Settings()
