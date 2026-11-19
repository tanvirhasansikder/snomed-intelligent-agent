"""The "Ask and verify" demo logic on a small fake database with a scripted model (no API calls)."""

import json

import pytest

from crawler.local_client import LocalTerminologyClient
from explanation.explainer import FALSE_IN_REFERENCE, SUPPORTED, TRUE_NOT_RETRIEVED
from llm.client import LLMResponse
from tests.test_text_retrieval import AMI, IHD, MI, NEC, OLD, PROC, TOP, db  # noqa: F401  (fixture)
from ui.ask_verify import AskVerify, DemoError, GraphReference, ReadThroughCache, make_item


class ScriptedLLM:
    """Returns the queued JSON replies in order; records the prompts."""

    def __init__(self, *replies):
        self.replies, self.prompts = list(replies), []

    def complete(self, messages):
        self.prompts.append(messages)
        return LLMResponse(text=json.dumps(self.replies.pop(0)), model="scripted", prompt_tokens=100,
                           completion_tokens=20, latency_s=0.1)


def claim(subject, relation, obj, cites=(), source="evidence"):
    return {"subject": subject, "relation": relation, "object": obj, "source": source, "cites": list(cites)}


@pytest.fixture
def demo(db):
    return AskVerify(LocalTerminologyClient.from_db(db), db)


def test_make_item_takes_gold_from_the_graph(demo):
    graph = demo.graph
    assert make_item(graph, "T1", MI).gold == sorted([IHD, NEC])
    assert make_item(graph, "T2", MI).gold == sorted([AMI, OLD])           # the UK child is outside the view
    t3 = make_item(graph, "T3", AMI, TOP)
    assert (t3.gold, t3.polarity) == ("yes", "pos")
    assert make_item(graph, "T3", MI, PROC).gold == "no"
    assert make_item(graph, "T6", IHD, NEC).gold == [TOP]
    with pytest.raises(DemoError):
        make_item(graph, "T4", MI, PROC)                                   # Procedure is not an ancestor of MI
    with pytest.raises(DemoError):
        make_item(graph, "T3", MI)                                         # needs Y
    with pytest.raises(DemoError):
        make_item(graph, "T6", MI, IHD)                                    # IHD is an ancestor of MI


def test_reference_from_the_graph(demo):
    ref = GraphReference(demo.graph)
    assert ref.has_edge(MI, IHD) and not ref.has_edge(MI, TOP)
    assert ref.is_ancestor(AMI, TOP) and MI in ref.nodes and "999" not in ref.nodes
    assert ref.parents.get(MI) == {IHD, NEC} and ref.children.get(AMI, set()) == set()


def test_g_qa_answer_is_scored_claim_by_claim(demo):
    item = make_item(demo.graph, "T1", MI)
    context, fmt, _, _ = demo.evidence(item, "G-QA")
    eid = {edge: e for e, edge in context.edge_index().items()}
    llm = ScriptedLLM({"answer": [IHD, TOP], "insufficient_evidence": False,
                       "claims": [claim(MI, "direct_parent", IHD, [eid[(MI, IHD)]]),
                                  claim(MI, "direct_parent", TOP, source="model_knowledge")]})
    result = demo.ask(item, "G-QA", llm)
    assert result.error is None and result.sufficient
    labels = [c["label"] for c in result.claims]
    assert labels == [SUPPORTED, FALSE_IN_REFERENCE]                       # MI is not a direct child of TOP
    assert 0 < result.score["score"] < 1
    assert "v3.0" not in result.evidence_text and "E1" in result.evidence_text
    assert "never write a claim" not in llm.prompts[0][1]["content"]       # T1 text is unchanged in v3.0


def test_masked_and_unmarked_evidence(demo):
    item = make_item(demo.graph, "T1", MI)
    _, fmt, mask, _ = demo.evidence(item, "G-QA", fraction=0.5, marked=False)
    assert fmt == "v2-nocov" and mask["n_withheld"] == 1
    llm = ScriptedLLM({"answer": [NEC, IHD], "insufficient_evidence": False,
                       "claims": [claim(MI, "direct_parent", IHD, source="model_knowledge"),
                                  claim(MI, "direct_parent", NEC, source="model_knowledge")]})
    result = demo.ask(item, "G-QA", llm, fraction=0.5, marked=False)
    assert not result.sufficient and result.mask["n_withheld"] == 1
    assert TRUE_NOT_RETRIEVED in [c["label"] for c in result.claims]       # the withheld parent, from memory
    with pytest.raises(DemoError):
        demo.ask(item, "L-RAG", llm, fraction=0.5)


def test_verification_removes_the_unsupported_claim(demo):
    item = make_item(demo.graph, "T1", MI)
    context, _, _, _ = demo.evidence(item, "G-QA+V")
    eid = {edge: e for e, edge in context.edge_index().items()}
    bad = {"answer": [IHD, NEC, TOP], "insufficient_evidence": False,
           "claims": [claim(MI, "direct_parent", IHD, [eid[(MI, IHD)]]), claim(MI, "direct_parent", NEC, [eid[(MI, NEC)]]),
                      claim(MI, "direct_parent", TOP, ["E99"])]}
    llm = ScriptedLLM(bad, bad)                                            # the regeneration repeats the mistake
    result = demo.ask(item, "G-QA+V", llm)
    assert result.regenerated and len(llm.prompts) == 2
    assert FALSE_IN_REFERENCE in [c["label"] for c in result.first_claims]
    assert all(c["label"] == SUPPORTED for c in result.claims) and result.removed_claims
    assert set(result.answer) == {IHD, NEC}


def test_text_retrieval_and_closed_book(demo):
    item = make_item(demo.graph, "T1", MI)
    llm = ScriptedLLM({"answer": [IHD], "claims": [], "insufficient_evidence": False},
                      {"answer": [], "claims": [], "insufficient_evidence": True})
    lrag = demo.ask(item, "L-RAG", llm)
    assert lrag.render_format == "cards" and MI in lrag.text_record["cards"]
    b0 = demo.ask(item, "B0", llm)
    assert b0.evidence_text.startswith("(no evidence") and b0.abstained
    with pytest.raises(DemoError):
        demo.ask(item, "D-RAG", llm)                                       # no exported vectors in this test


def test_read_through_cache_never_writes_the_study_cache(tmp_path):
    study = tmp_path / "study.jsonl"
    demo_path = tmp_path / "demo.jsonl"
    from llm.client import ResponseCache
    ResponseCache(study).put("k1", {"m": 1}, LLMResponse(text="{}", model="m"))
    before = study.read_text(encoding="utf-8")
    cache = ReadThroughCache(demo_path, study)
    assert cache.get("k1").text == "{}"
    cache.put("k2", {"m": 1}, LLMResponse(text="[]", model="m"))
    assert study.read_text(encoding="utf-8") == before and cache.get("k2").text == "[]"
