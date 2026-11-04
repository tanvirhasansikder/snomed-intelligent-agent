"""Shared single-question pipeline (llm/agent.py, milestone M1.4)."""

import json

import pytest

from evaluation.synthetic import SyntheticOntology
from evaluation.tasks import Item
from explanation.explainer import CONTRADICTED, INSUFFICIENT, SUPPORTED
from llm.agent import HierarchyAgent, _deep_synthetic_concept
from llm.client import LLMError, LLMResponse


class FakeLLM:
    """Returns a fixed reply; ``reply`` may use the evidence via a callable."""

    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.messages = reply, error, None

    def complete(self, messages):
        self.messages = messages
        if self.error:
            raise self.error
        text = self.reply(messages) if callable(self.reply) else self.reply
        return LLMResponse(text=text, model="fake-1", prompt_tokens=100, completion_tokens=20,
                           latency_s=0.5, finish_reason="stop")


@pytest.fixture
def world():
    ontology = SyntheticOntology(seed=0)
    x = _deep_synthetic_concept(ontology)
    return ontology, x


def ev(subject, relation, obj=None, cites=()):
    return {"subject": subject, "relation": relation, "object": obj, "source": "evidence", "cites": list(cites)}


def test_b1_runtime_labels_and_citations(world):
    ontology, x = world
    parents = sorted(ontology.parents[x])
    other = next(c for c in sorted(ontology.labels) if c not in parents and c != x)
    unseen = "1000004"  # valid-format ID that is not in the evidence

    def reply(messages):
        # E1/E2 are the focal concept's parent edges in the v2 evidence (parents come first)
        return json.dumps({"answer": parents, "insufficient_evidence": False, "claims": [
            ev(x, "direct_parent", parents[0], ["E1"]),          # supported, correct citation
            ev(x, "direct_parent", other, ["E1"]),               # parents complete -> contradicted
            ev(unseen, "direct_parent", x, ["E9"]),              # not in evidence -> insufficient
        ]})

    agent = HierarchyAgent(FakeLLM(reply), ontology)
    result = agent.answer(Item("t1", "T1", x, parents), "B1")

    assert result.error is None and result.retrieval == "U2" and result.retrieval_requests > 0
    assert result.evidence_text.startswith("Retrieved SNOMED CT IS_A edges")
    assert result.edge_index["E1"][0] == x
    assert [c["runtime_label"] for c in result.claims] == [SUPPORTED, CONTRADICTED, INSUFFICIENT]
    assert result.claims[0]["citation"] == "entails"
    assert result.claims[2]["citation"] == "invalid_edge_id"
    assert result.n_supported == 1 and result.n_not_supported == 2 and result.n_bad_citations == 2
    assert result.evidence_sufficient is True
    assert result.prompt_tokens == 100 and result.model == "fake-1"


def test_b0_has_no_evidence(world):
    ontology, x = world
    reply = json.dumps({"answer": ["1000004"], "insufficient_evidence": False,
                        "claims": [ev(x, "direct_parent", "1000004")]})
    result = HierarchyAgent(FakeLLM(reply), ontology).answer(Item("t1", "T1", x, None), "B0")
    assert result.evidence_text == "No evidence was retrieved."
    assert result.edge_index == {} and result.evidence_sufficient is False
    assert result.claims[0]["runtime_label"] == INSUFFICIENT
    assert result.claims[0]["citation"] == "no_citation"


def test_runtime_verification_never_uses_a_reference(world):
    ontology, x = world
    reply = json.dumps({"answer": None, "claims": [ev(x, "ancestor", "1000004")]})
    result = HierarchyAgent(FakeLLM(reply), ontology).answer(Item("t5", "T5", x, None), "B1")
    allowed = {SUPPORTED, CONTRADICTED, INSUFFICIENT, "invalid_claim"}
    assert {c["runtime_label"] for c in result.claims} <= allowed  # never "false"/"true_not_retrieved"


def test_llm_error_is_recorded(world):
    ontology, x = world
    result = HierarchyAgent(FakeLLM(error=LLMError("HTTP 503")), ontology).answer(Item("t1", "T1", x, None), "B1")
    assert result.error == "HTTP 503" and result.claims == [] and result.evidence_text


def test_unparseable_reply_and_serialisation(world):
    ontology, x = world
    result = HierarchyAgent(FakeLLM("no json here"), ontology).answer(Item("t1", "T1", x, None), "B1")
    assert result.parsed["parse_error"]
    data = json.loads(json.dumps(result.to_dict()))
    assert data["edge_index"]["E1"][0] == x


def test_prompt_contains_v2_evidence_and_labels(world):
    ontology, x = world
    llm = FakeLLM(json.dumps({"answer": [], "claims": []}))
    HierarchyAgent(llm, ontology).answer(Item("t1", "T1", x, None), "B1")
    user = llm.messages[1]["content"]
    assert "E1:" in user and ontology.labels[x] in user


def test_unknown_system_is_rejected(world):
    ontology, x = world
    with pytest.raises(ValueError):
        HierarchyAgent(FakeLLM("{}"), ontology).answer(Item("t1", "T1", x, None), "S9")
