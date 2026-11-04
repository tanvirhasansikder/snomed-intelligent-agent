"""S1 question-aware retrieval planner (query_engine/retrieval_planner.py)."""

import json

import pytest

from evaluation.conditions import build_context
from evaluation.synthetic import SyntheticOntology
from evaluation.tasks import Item
from llm.agent import HierarchyAgent, _deep_synthetic_concept
from llm.client import LLMResponse
from query_engine.retrieval_planner import PLANS, build_s1_context


@pytest.fixture
def world():
    ontology = SyntheticOntology(seed=0)
    return ontology, _deep_synthetic_concept(ontology)


def true_ancestors(ontology, concept_id):
    seen, stack = set(), [concept_id]
    while stack:
        for parent in ontology.parents.get(stack.pop(), []):
            if parent not in seen:
                seen.add(parent)
                stack.append(parent)
    return seen


def item(task, x, target=None):
    return Item(f"i:{task}", task, x, None, target_id=target)


def test_every_question_type_has_a_plan():
    assert set(PLANS) == {"T1", "T2", "T3", "T4", "T5", "T6"}


def test_t1_fetches_only_parents(world):
    ontology, x = world
    context = build_s1_context(ontology, item("T1", x))
    assert set(context.parents_of(x)) == set(ontology.parents[x])
    assert context.parents_complete(x) and x not in context.children_status
    assert all(c == x for c, _ in context.edges)


def test_t2_fetches_only_children(world):
    ontology, _ = world
    parent = next(n for n in sorted(ontology.children) if len(ontology.children[n]) >= 2)
    context = build_s1_context(ontology, item("T2", parent))
    assert set(context.children_of(parent)) == set(ontology.children[parent])
    assert context.children_complete(parent)
    assert all(p == parent for _, p in context.edges)      # no upward edges


@pytest.mark.parametrize("task", ["T3", "T4"])
def test_closure_tasks_get_the_full_upward_closure(world, task):
    ontology, x = world
    top = sorted(ontology.top_level_names())[0]
    s1 = build_s1_context(ontology, item(task, x, top))
    b1 = build_context(ontology, x, "U2")
    assert s1.closure_complete(x) and s1.ancestors_of(x) == true_ancestors(ontology, x)
    assert not b1.closure_complete(x)                       # the contrast S1 vs B1 is real here


def test_t5_combines_parents_and_children(world):
    ontology, _ = world
    node = next(n for n in sorted(ontology.children) if ontology.parents.get(n))
    context = build_s1_context(ontology, item("T5", node))
    assert context.parents_complete(node) and context.children_complete(node)
    assert set(context.children_of(node)) == set(ontology.children[node])


def test_t6_expands_both_concepts(world):
    ontology, x = world
    other = next(n for n in sorted(ontology.labels) if n not in true_ancestors(ontology, x) and n != x
                 and ontology.parents.get(n))
    context = build_s1_context(ontology, item("T6", x, other))
    assert context.closure_complete(x) and context.closure_complete(other)
    assert other in context.nodes


def test_budget_stops_retrieval_truthfully(world):
    ontology, x = world
    top = sorted(ontology.top_level_names())[0]
    before = ontology.request_count
    context = build_s1_context(ontology, item("T4", x, top), budget=3)
    assert ontology.request_count - before == 3 == context.requests_used
    assert context.budget_stopped and not context.failed
    assert not context.closure_complete(x)
    assert all(context.parents_status.get(c) != "failed" for c in context.budget_stopped)
    text = context.render("v2")
    assert "request budget (3 requests) was used up" in text and "RETRIEVAL FAILED" not in text


def test_unbudgeted_context_has_no_budget_line(world):
    ontology, x = world
    context = build_s1_context(ontology, item("T1", x))
    assert not context.budget_stopped and "request budget" not in context.render("v2")


def test_agent_runs_s1(world):
    ontology, x = world
    top = sorted(ontology.top_level_names())[0]

    class LLM:
        def complete(self, messages):
            return LLMResponse(text=json.dumps({"answer": "yes", "claims": [], "insufficient_evidence": False}),
                               model="fake", prompt_tokens=1, completion_tokens=1, latency_s=0.0,
                               finish_reason="stop")

    result = HierarchyAgent(LLM(), ontology).answer(Item("i", "T3", x, True, target_id=top, polarity="pos"), "S1")
    assert result.retrieval == "S1" and result.error is None
    assert result.retrieval_requests > 0
