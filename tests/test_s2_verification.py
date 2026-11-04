"""S2: deterministic verification + one controlled regeneration (contribution C-B)."""

import json

import pytest

from evaluation.synthetic import SyntheticOntology
from evaluation.tasks import Item
from explanation.verification import find_problems, ids_in_text
from llm.agent import HierarchyAgent, _deep_synthetic_concept
from llm.client import LLMError, LLMResponse
from llm.prompt_manager import REGENERATION_INSTRUCTION


class ScriptedLLM:
    """Returns the scripted replies in order; an LLMError entry is raised."""

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def complete(self, messages):
        self.calls.append(messages)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return LLMResponse(text=reply, model="fake", prompt_tokens=100 * len(self.calls),
                           completion_tokens=10, latency_s=1.0, finish_reason="stop")


def answer(ans, claims, insufficient=False):
    return json.dumps({"answer": ans, "claims": claims, "insufficient_evidence": insufficient})


def ev(s, rel, o=None, cites=(), source="evidence"):
    return {"subject": s, "relation": rel, "object": o, "source": source, "cites": list(cites)}


@pytest.fixture
def world():
    ontology = SyntheticOntology(seed=0)
    x = _deep_synthetic_concept(ontology)
    parents = sorted(ontology.parents[x])
    return ontology, x, parents


def edge_id(result, child, parent):
    return next(k for k, v in result.edge_index.items() if tuple(v) == (child, parent))


def run(world, *replies, task="T1", system="S2", gold=None):
    ontology, x, parents = world
    llm = ScriptedLLM(*replies)
    item = Item(f"t:{task}", task, x, gold if gold is not None else parents)
    return HierarchyAgent(llm, ontology).answer(item, system), llm


def test_clean_answer_is_not_regenerated(world):
    ontology, x, parents = world
    probe, _ = run(world, answer([], []), system="S1")
    e = [edge_id(probe, x, p) for p in parents]
    result, llm = run(world, answer(parents, [ev(x, "direct_parent", p, [c]) for p, c in zip(parents, e)]))
    assert len(llm.calls) == 1 and not result.regenerated
    assert result.final["answer"] == parents and len(result.final["claims"]) == len(parents)
    assert result.final["removed_claims"] == [] and result.final["citations_repaired"] == 0


def test_one_regeneration_with_exact_feedback(world):
    ontology, x, parents = world
    first = answer(parents + ["1000004"], [ev(x, "direct_parent", "1000004", source="model_knowledge")])
    second = answer(parents, [])
    result, llm = run(world, first, second)
    assert len(llm.calls) == 2 and result.regenerated
    feedback = llm.calls[1][-1]["content"]
    assert "direct_parent(" + x + ", 1000004)" in feedback and REGENERATION_INSTRUCTION in feedback
    assert llm.calls[1][-2] == {"role": "assistant", "content": first}
    assert result.final["answer"] == parents                       # the fabricated parent is gone
    assert result.total_prompt_tokens == 100 + 200 and result.total_llm_latency_s == 2.0
    assert result.regeneration["problems_left"] == {"unsupported": 0, "bad_citations": 0, "unlisted_ids": 0}


def test_final_filter_removes_what_regeneration_did_not(world):
    ontology, x, parents = world
    bad = answer(parents + ["1000004"], [ev(x, "direct_parent", "1000004")])
    result, llm = run(world, bad, bad)                              # the model ignores the feedback
    assert len(llm.calls) == 2                                      # never more than one regeneration
    assert result.final["answer"] == parents
    assert [c["object"] for c in result.final["removed_claims"]] == ["1000004"]


def test_failed_or_unparseable_regeneration_keeps_first_answer_filtered(world):
    ontology, x, parents = world
    bad = answer(parents + ["1000004"], [ev(x, "direct_parent", "1000004")])
    for second in (LLMError("HTTP 503"), "not json"):
        result, _ = run(world, bad, second)
        assert result.regenerated and result.final["answer"] == parents
        assert result.final["removed_claims"]


def test_citation_is_repaired_and_flagged(world):
    ontology, x, parents = world
    probe, _ = run(world, answer([], []), system="S1")
    wrong = edge_id(probe, x, parents[1]) if len(parents) > 1 else "E99"
    first = answer(parents[:1], [ev(x, "direct_parent", parents[0], [wrong])])
    result, llm = run(world, first, first)
    assert result.problems["bad_citations"]
    kept = result.final["claims"][0]
    assert kept["citation_repaired"] and kept["cites"] == [edge_id(probe, x, parents[0])]


def test_explanation_with_unverifiable_text_is_replaced(world):
    ontology, x, parents = world
    probe, _ = run(world, answer([], []), system="S1")
    cite = edge_id(probe, x, parents[0])
    text = f"Concept {x} is a kind of {parents[0]} and also of 1000004."
    reply = answer(text, [ev(x, "direct_parent", parents[0], [cite])])
    result, llm = run(world, reply, reply, task="T5", gold=None)
    assert result.problems["unlisted_ids"] == ["1000004"] and len(llm.calls) == 2
    assert result.final["answer_replaced"]
    assert "1000004" not in result.final["answer"] and parents[0] in result.final["answer"]
    assert cite in result.final["answer"]


def test_s2_first_pass_equals_s1(world):
    ontology, x, parents = world
    reply = answer(parents, [ev(x, "direct_parent", p) for p in parents])
    s1, _ = run(world, reply, system="S1")
    s2, _ = run(world, reply, reply, system="S2")
    assert s1.raw_text == s2.raw_text and s1.claims == s2.claims and s1.evidence_text == s2.evidence_text
    assert s1.final is None and s2.final is not None


def test_find_problems_and_ids_in_text():
    item = Item("t", "T5", "700000", None)
    claims = [{"subject": "700000", "relation": "direct_parent", "object": "500000",
               "runtime_label": "supported_by_subgraph", "citation": "no_citation"}]
    problems = find_problems(item, {"answer": "700000 IS_A 500000, unlike 123456789."}, claims)
    assert problems == {"unsupported": [], "bad_citations": [], "unlisted_ids": ["123456789"]}
    assert ids_in_text("a 1234567 b 1234567 c 12345") == ["1234567"]
