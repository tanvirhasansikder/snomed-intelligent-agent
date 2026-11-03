"""SNOMED CT identifier helpers (Verhoeff check digit, concept partition)."""

from agent.query_processor import _VERHOEFF_D, _VERHOEFF_P, QueryProcessor

_VERHOEFF_INV = (0, 4, 3, 2, 1, 5, 6, 7, 8, 9)


def check_digit(digits):
    """Verhoeff check digit for a string of digits (without the check digit)."""
    c = 0
    for i, d in enumerate(reversed(digits)):
        c = _VERHOEFF_D[c][_VERHOEFF_P[(i + 1) % 8][int(d)]]
    return str(_VERHOEFF_INV[c])


def make_concept_sctid(item_identifier):
    """Build a valid International-namespace concept SCTID: item + '00' + check digit."""
    body = f"{int(item_identifier)}00"
    return body + check_digit(body)


def is_valid_sctid(value):
    return QueryProcessor.is_valid_sctid(value)


def is_concept_sctid(value):
    return QueryProcessor.is_valid_sctid(value) and QueryProcessor.is_concept_partition(value)
