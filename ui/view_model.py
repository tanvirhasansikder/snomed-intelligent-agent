"""
Presentation logic for main.py, kept free of Streamlit so it can be tested.

The first five helpers were moved unchanged from main.py. The rest turn the
query result's ``coverage`` into messages that never present "not retrieved"
as "none exist".
"""

import html
import os


# ============================================================
# Helpers moved from main.py (behaviour unchanged)
# ============================================================

def safe_list(value):
    """
    Make sure a result field is always returned as a list.
    """
    if value is None:
        return []

    if isinstance(value, list):
        return value

    return []


def extract_concept_id(result):
    """
    Robustly extract the SNOMED CT concept ID.

    Supports several possible result structures.
    """

    if not isinstance(result, dict):
        return "Unknown"

    possible_keys = [
        "concept_id",
        "id",
        "snomed_id",
        "snomed_ct_id",
        "conceptId",
    ]

    for key in possible_keys:
        value = result.get(key)

        if value not in [None, "", "Unknown"]:
            return str(value)

    concept = result.get("concept")

    if isinstance(concept, dict):

        for key in possible_keys:

            value = concept.get(key)

            if value not in [None, "", "Unknown"]:
                return str(value)

    return "Unknown"


def extract_concept_name(result):
    """
    Robustly extract the concept name.
    """

    if not isinstance(result, dict):
        return "Unknown"

    possible_keys = [
        "concept_name",
        "name",
        "preferred_label",
        "label",
    ]

    for key in possible_keys:

        value = result.get(key)

        if value not in [None, "", "Unknown"]:
            return str(value)

    concept = result.get("concept")

    if isinstance(concept, dict):

        for key in possible_keys:

            value = concept.get(key)

            if value not in [None, "", "Unknown"]:
                return str(value)

    return "Unknown"


def extract_relationships(result):
    """
    Extract relationship information if available.
    """

    if not isinstance(result, dict):
        return []

    relationships = result.get("relationships")

    if isinstance(relationships, list):
        return relationships

    return []


def normalize_concepts(items):
    """
    Normalize parent/child concept objects.
    """

    normalized = []

    if not isinstance(items, list):
        return normalized

    seen = set()

    for item in items:

        if isinstance(item, dict):

            concept_id = (
                item.get("concept_id")
                or item.get("id")
                or item.get("snomed_id")
                or item.get("snomed_ct_id")
                or "Unknown"
            )

            name = (
                item.get("name")
                or item.get("concept_name")
                or item.get("preferred_label")
                or item.get("label")
                or "Unknown"
            )

        else:

            concept_id = "Unknown"
            name = str(item)

        unique_key = f"{concept_id}|{name}"

        if unique_key in seen:
            continue

        seen.add(unique_key)

        normalized.append(
            {
                "concept_id": str(concept_id),
                "name": str(name),
            }
        )

    return normalized


# ============================================================
# Safety
# ============================================================

def escape(value):
    """HTML-escape a value before inserting it into unsafe_allow_html markup."""
    if value is None:
        return ""
    return html.escape(str(value))


_SECRET_SETTINGS = ("BIOPORTAL_API_KEY", "NEO4J_PASSWORD")


def redact(text):
    """Remove the values of secret settings from a message."""
    text = "" if text is None else str(text)
    for name in _SECRET_SETTINGS:
        secret = os.getenv(name)
        if secret and len(secret) >= 4:
            text = text.replace(secret, "[hidden]")
    return text


# ============================================================
# Coverage-aware notices
# ============================================================

def _coverage_status(result, section):
    coverage = result.get("coverage") if isinstance(result, dict) else None
    section_coverage = coverage.get(section) if isinstance(coverage, dict) else None
    if isinstance(section_coverage, dict) and section_coverage.get("status"):
        return section_coverage["status"]
    return "not_retrieved"


def coverage_notice(result, section):
    """
    Return (level, message) explaining what an empty or partial list means,
    or None when the list is complete and non-empty. ``level`` is one of
    "info" or "warning". ``section`` is "parents" or "children".
    """

    items = safe_list(result.get(section)) if isinstance(result, dict) else []
    status = _coverage_status(result, section)

    if section == "parents":
        if items:
            if status == "complete":
                return None
            if status == "failed":
                return ("warning", "The latest parent retrieval failed; showing parents stored earlier.")
            return ("info", "Showing parents stored in the local graph; the full parent list was not retrieved.")
        if status == "complete":
            return ("info", "BioPortal reports no parent concepts: this is a top-level concept.")
        if status == "failed":
            return ("warning", "Parent retrieval from BioPortal failed. This concept may have parents that are not shown.")
        return ("info", "Parents were not retrieved for this concept, so none are shown. "
                        "This does not mean it has no parents.")

    if section == "children":
        if items:
            if status == "complete":
                return None
            if status == "partial":
                return ("warning", f"Showing {len(items)} children; BioPortal has more that were not retrieved "
                                   "(page limit reached).")
            if status == "failed":
                return ("warning", "Child retrieval failed; showing only children already stored in the local graph.")
            return ("info", "Showing only children already stored in the local graph; "
                            "the full child list was not retrieved.")
        if status == "complete":
            return ("info", "BioPortal reports no direct children: this is a leaf concept.")
        if status == "partial":
            return ("warning", "Child retrieval stopped early; children may exist that are not shown.")
        if status == "failed":
            return ("warning", "Child retrieval from BioPortal failed. This concept may have children that are not shown.")
        return ("info", "Children were not retrieved from BioPortal for this query. "
                        "An empty list here does not mean the concept has no children.")

    raise ValueError("section must be 'parents' or 'children'")


def count_label(result, section):
    """Statistic-card label; marks counts that may be incomplete."""
    base = "Direct Parents" if section == "parents" else "Direct Children"
    return base if _coverage_status(result, section) == "complete" else f"{base} (stored locally)"


def ancestor_note(result):
    """One sentence about how far the ancestor view reaches."""
    coverage = (result.get("coverage") or {}).get("ancestors") if isinstance(result, dict) else None
    if not isinstance(coverage, dict):
        return None
    depth = coverage.get("depth")
    if coverage.get("status") == "partial":
        return (f"Ancestors up to {depth} level(s) were requested, but some levels could not be retrieved; "
                "the list below is incomplete.")
    if coverage.get("frontier"):
        return (f"Ancestors are shown up to {depth} level(s). Concepts at the top of this view may have "
                "further ancestors that were not retrieved.")
    return f"All ancestors were retrieved (the hierarchy ends within {depth} level(s))."


def resolution_note(result):
    """Explain how the query was matched to a concept, or None."""
    resolution = result.get("resolution") if isinstance(result, dict) else None
    if not isinstance(resolution, dict):
        return None
    notes = {
        "local_id": "Matched by SNOMED CT ID in the local graph.",
        "bioportal_id": "Matched by SNOMED CT ID on BioPortal.",
        "local_name": "Matched by exact preferred label in the local graph.",
        "bioportal_exact_label": "Matched by exact preferred label on BioPortal.",
        "bioportal_top_result": "No exact label match: the top-ranked BioPortal search result was used.",
    }
    return notes.get(resolution.get("method"))


# ============================================================
# Error messages
# ============================================================

_ERROR_MESSAGES = {
    "invalid_input": "Please enter a SNOMED CT concept name or identifier.",
    "invalid_identifier": "That number is not a valid SNOMED CT concept identifier "
                          "(check digit or partition does not match).",
    "not_found": "No SNOMED CT concept matched your query on BioPortal.",
    "source_unavailable": "BioPortal could not be reached or returned an error, so the hierarchy could not "
                          "be retrieved. Please try again later.",
    "database_error": "The local Neo4j database could not be reached. Check that Neo4j is running and that "
                      "the NEO4J_* settings in .env are correct.",
    "configuration_error": "Configuration is incomplete. Check the .env file in the project root "
                           "(see .env.example).",
    "unexpected_error": "An unexpected error occurred. Details were written to the console log.",
}


def error_message(result):
    """User-facing message for a failed result. Never echoes raw error text."""
    error_type = result.get("error_type") if isinstance(result, dict) else None
    return _ERROR_MESSAGES.get(error_type, _ERROR_MESSAGES["unexpected_error"])


def error_details(result):
    """Raw error text with secret setting values removed (for an expander)."""
    return redact(result.get("error") if isinstance(result, dict) else "")
