"""Answer schema with edge citations (protocol v2.0, Sections 5-7)."""

import json

from llm.prompt_manager import CLAIM_HELP, PROMPT_VERSION, SYSTEM_PROMPT
from llm.reasoner import parse_answer


def answer(claims):
    return json.dumps({"answer": ["500"], "claims": claims, "insufficient_evidence": False})


def claim(**extra):
    return dict({"subject": "233604007", "relation": "direct_parent", "object": "205237003",
                 "source": "evidence"}, **extra)


def test_prompt_asks_for_edge_citations():
    assert PROMPT_VERSION == "v2.0"
    assert '"cites"' in CLAIM_HELP
    assert "cite the IDs of the evidence edges" in SYSTEM_PROMPT
    assert "never cite an edge ID that is not shown" in SYSTEM_PROMPT


def test_citations_are_parsed_and_normalised():
    parsed = parse_answer(answer([claim(cites=["E1", " e2 ", "E1", 3, "edge 4", None, True])]), "T1")
    assert parsed["claims"][0]["cites"] == ["E1", "E2"]
    assert parsed["dropped_citations"] == 4  # 3, "edge 4", None, True; the duplicate E1 is merged


def test_single_string_and_missing_citations():
    parsed = parse_answer(answer([claim(cites="E7"), claim()]), "T1")
    assert [c["cites"] for c in parsed["claims"]] == [["E7"], []]
    assert parsed["dropped_citations"] == 0


def test_malformed_cites_field_does_not_drop_the_claim():
    parsed = parse_answer(answer([claim(cites={"edge": "E1"})]), "T1")
    assert len(parsed["claims"]) == 1 and parsed["claims"][0]["cites"] == []
    assert parsed["dropped_citations"] == 1


def test_unparseable_answer_reports_zero_citations():
    parsed = parse_answer("not json", "T1")
    assert parsed["parse_error"] and parsed["dropped_citations"] == 0
