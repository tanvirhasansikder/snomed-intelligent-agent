"""
S1: question-aware retrieval of a partial SNOMED CT subgraph (protocol v2.0,
Section 6; contribution C-A).

The fixed app retrieval (B1) always goes upward to depth 2 and never fetches
children, whatever the question. S1 decides what to fetch from the question
type, using the same crawler and graph builder as the app:

    T1 parents             X's parents
    T2 children            X's direct children (paged); X itself without parents
    T3 is-a (X, Y)         X's full upward closure
    T4 path to top level   X's full upward closure
    T5 explanation         X's parents to depth 2, plus X's direct children
    T6 common ancestor     the full upward closures of X and Z (Z = item.target_id)

Each question has a REQUEST BUDGET (default 60 BioPortal requests). When it
runs out, further requests are refused. The affected concepts are reported
as "not retrieved because the budget ran out", never as complete and never
as a BioPortal failure. S1 is therefore partial by design, and the evidence
says exactly where it is incomplete.

The planner only reads. Nothing it retrieves is written anywhere except the
temporary in-memory graph that becomes the evidence.

    context = build_s1_context(client, item, budget=60)
"""

from crawler.bioportal_client import BioPortalRequestError
from database.memory_repository import InMemoryGraphRepository
from evaluation.conditions import APP_CHILD_PAGES, Context, fill_context
from knowledge_graph.graph_builder import GraphBuilder

#: Upward closure depth used for "full" upward retrieval (deeper than any SNOMED CT path).
CLOSURE_DEPTH = 20
DEFAULT_BUDGET = 60

#: Retrieval plan per question type: upward depth, fetch X's children, also expand item.target_id.
PLANS = {
    "T1": {"up_depth": 1, "children": False, "expand_target": False},
    "T2": {"up_depth": 0, "children": True, "expand_target": False},
    "T3": {"up_depth": CLOSURE_DEPTH, "children": False, "expand_target": False},
    "T4": {"up_depth": CLOSURE_DEPTH, "children": False, "expand_target": False},
    "T5": {"up_depth": 2, "children": True, "expand_target": False},
    "T6": {"up_depth": CLOSURE_DEPTH, "children": False, "expand_target": True},
}


class RequestBudgetExhausted(BioPortalRequestError):
    """Raised by BudgetedClient once the per-question request budget is used up."""


class BudgetedClient:
    """
    Wraps a retrieval client and refuses requests beyond ``budget``. Records
    which concepts were refused, so the evidence can mark them as stopped by
    the budget rather than failed. Other attributes pass through.
    """

    COUNTED = ("get_concept", "get_parents", "fetch_children", "get_ancestors")

    def __init__(self, client, budget):
        self.client = client
        self.budget = budget
        self.used = 0
        self.refused_up = set()
        self.refused_down = set()

    def _call(self, name, concept_id, *args, **kwargs):
        if self.used >= self.budget:
            (self.refused_down if name == "fetch_children" else self.refused_up).add(concept_id)
            raise RequestBudgetExhausted(f"request budget of {self.budget} used up")
        self.used += 1
        return getattr(self.client, name)(concept_id, *args, **kwargs)

    def __getattr__(self, name):
        if name in self.COUNTED:
            return lambda concept_id, *a, **k: self._call(name, concept_id, *a, **k)
        return getattr(self.client, name)


def build_s1_context(client, item, budget=DEFAULT_BUDGET, child_display_cap=100):
    """Question-aware evidence for one item (condition label "S1")."""
    if item.task not in PLANS:
        raise ValueError(f"No S1 plan for task {item.task!r}")
    plan = PLANS[item.task]
    budgeted = BudgetedClient(client, budget)
    repo = InMemoryGraphRepository()
    builder = GraphBuilder(default_depth=plan["up_depth"], repository=repo, client=budgeted, use_cache=False)

    reports = []
    starts = [item.concept_id]
    if plan["expand_target"] and item.target_id:
        starts.append(item.target_id)
    for start in starts:
        builder.expand_concept(start, depth=plan["up_depth"])
        if builder.last_report:
            reports.append(builder.last_report)
    if plan["children"]:
        builder.expand_children(item.concept_id, max_pages=APP_CHILD_PAGES)

    context = Context(condition="S1", focal_id=item.concept_id, request_budget=budget)
    fill_context(context, repo, reports, item.concept_id, child_display_cap)

    # Concepts refused by the budget: "not retrieved (budget)", not "failed".
    for concept_id in budgeted.refused_up:
        if context.parents_status.get(concept_id) == "failed":
            del context.parents_status[concept_id]
        context.failed.discard(concept_id)
        context.budget_stopped.add(concept_id)
    for concept_id in budgeted.refused_down:
        if context.children_status.get(concept_id) == "failed":
            del context.children_status[concept_id]
        context.budget_stopped.add(concept_id)
    context.requests_used = budgeted.used
    return context
