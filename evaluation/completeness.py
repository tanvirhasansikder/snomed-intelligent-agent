"""
Controlled completeness (Study 2, RQ1-RQ3): withhold a seeded fraction of the
edges a question's gold answer needs from an evidence context.

Why: Study 1 could only observe incompleteness where the crawler happened to
stop. Here the local graph is complete, so incompleteness can be *set*: the
same item is shown with 0, 25, 50 and 75 % of its answer-relevant edges
withheld, and each item serves as its own control.

How one item is masked
----------------------
1. ``relevant_edges(item, context)`` lists the edges in the evidence that the
   gold answer depends on (per task, see that function).
2. The relevant edges are put in a fixed order with a seeded shuffle; the seed
   is ``f"{seed}:{item_id}"``, so the order depends only on the item, not on
   which other items are in the run.
3. Level ``f`` withholds the first ``round_half_up(f * n)`` edges of that
   order. Removals are therefore nested: whatever is missing at 25 % is also
   missing at 50 % and 75 %, so a change between levels is caused only by the
   extra removals.
4. Only those edges are removed. Concepts stay in the evidence unless the
   removal leaves them with no edge at all (then they are dropped, because a
   crawler that never saw the edge would not have seen the concept either).
   The focal concept, and the second concept of a T6 question, always stay.
5. Every list that lost an edge and had been retrieved completely is
   downgraded from ``complete`` to ``partial``: the parent list of the
   edge's child and the child list of the edge's parent. This is what a
   budgeted crawler knows when it stops early.

Marked versus unmarked (RQ2)
----------------------------
The masked context is the same in both variants; only the text the model sees
differs. ``marked`` renders protocol v2.0 evidence (edges plus coverage lines,
now including PARTIAL); ``unmarked`` renders the same edges with no coverage
information (format ``v2-nocov``). The system's own deterministic parts
(sufficiency labels, R0 and the S2 verifier) always use the true coverage of
the masked context, so they behave identically in both variants.
"""

import math
import random
from dataclasses import replace

LEVELS = (0.0, 0.25, 0.5, 0.75)
VARIANTS = {"marked": "v2", "unmarked": "v2-nocov"}
MASK_VERSION = "1"


def evidence_format(variant):
    """Render format for a variant: ``marked`` -> v2, ``unmarked`` -> v2-nocov."""
    if variant not in VARIANTS:
        raise ValueError(f"variant must be one of {list(VARIANTS)}")
    return VARIANTS[variant]


def n_withheld(n_relevant, fraction):
    """Number of edges withheld at ``fraction``: f * n rounded half up (monotone in f, so levels nest)."""
    if not 0.0 <= fraction <= 1.0:
        raise ValueError("fraction must be between 0 and 1")
    return min(n_relevant, math.floor(fraction * n_relevant + 0.5))


# ----------------------------------------------------------------------
# answer-relevant edges
# ----------------------------------------------------------------------

def _with_ancestors(context, concepts):
    found = set()
    for concept in concepts:
        if concept in context.nodes:
            found |= {concept} | context.ancestors_of(concept)
    return found


def _upward_dag(context, sources, targets):
    """Edges on any upward path from a source to a target, inside the context."""
    below = _with_ancestors(context, sources)
    targets = set(targets)
    return {(c, p) for c, p in context.edges
            if c in below and (p in targets or targets & context.ancestors_of(p))}


def _closure(context, concepts):
    """Every upward edge from the concepts and their ancestors (what certifies a negative)."""
    below = _with_ancestors(context, concepts)
    return {(c, p) for c, p in context.edges if c in below}


def relevant_edges(item, context):
    """
    The edges of ``context`` that the gold answer of ``item`` depends on,
    as a sorted list of (child, parent):

    * T1  the concept's edges to its gold parents
    * T2  the gold children's edges to the concept
    * T3  positive: every edge on an upward path from the concept to the target;
          negative: the concept's whole upward closure (the answer "no" is
          certified only by a complete closure)
    * T4  every edge on an upward path from the concept to the target
    * T5  the concept's parent edges and child edges
    * T6  every edge on an upward path from either concept to a gold lowest
          common ancestor
    """
    x, edges = item.concept_id, context.edges
    if item.task == "T1":
        found = {(x, p) for p in item.gold or []} & edges
    elif item.task == "T2":
        found = {(c, x) for c in item.gold or []} & edges
    elif item.task == "T3":
        found = (_upward_dag(context, [x], [item.target_id]) if item.polarity == "pos"
                 else _closure(context, [x]))
    elif item.task == "T4":
        found = _upward_dag(context, [x], [item.target_id])
    elif item.task == "T5":
        found = {(c, p) for c, p in edges if c == x or p == x}
    elif item.task == "T6":
        found = _upward_dag(context, [x, item.target_id], item.gold or [])
    else:
        raise ValueError(item.task)
    return sorted(found)


def removal_order(item, context, seed):
    """Relevant edges in the seeded order in which they are withheld."""
    order = relevant_edges(item, context)
    random.Random(f"{seed}:{item.item_id}").shuffle(order)
    return order


# ----------------------------------------------------------------------
# the mask
# ----------------------------------------------------------------------

def _copy(context):
    return replace(context, nodes=dict(context.nodes), edges=set(context.edges),
                   parents_status=dict(context.parents_status), children_status=dict(context.children_status),
                   children_total=dict(context.children_total), frontier=set(context.frontier),
                   failed=set(context.failed), budget_stopped=set(context.budget_stopped))


def mask_context(context, item, fraction, seed):
    """
    (masked copy of ``context``, record). The input context is not changed.

    The record holds what an analysis needs per item and level: the number of
    relevant and withheld edges, the realised fraction (n_withheld / n_relevant,
    which differs from ``fraction`` for small n), the withheld edges, the lists
    marked partial and the concepts dropped.
    """
    order = removal_order(item, context, seed)
    k = n_withheld(len(order), fraction)
    withheld = order[:k]
    masked = _copy(context)
    masked.edges -= set(withheld)

    keep = {item.concept_id, context.focal_id}
    if item.target_id and item.task == "T6":
        keep.add(item.target_id)
    touched = {c for edge in withheld for c in edge}
    linked = {c for edge in masked.edges for c in edge}
    dropped = sorted(c for c in touched if c not in linked and c not in keep and c in masked.nodes)
    for concept in dropped:
        masked.nodes.pop(concept, None)
        masked.parents_status.pop(concept, None)
        masked.children_status.pop(concept, None)
        masked.children_total.pop(concept, None)
        masked.frontier.discard(concept)
        masked.failed.discard(concept)
        masked.budget_stopped.discard(concept)

    parents_marked, children_marked = set(), set()
    for child, parent in withheld:
        if masked.parents_status.get(child) == "complete":
            masked.parents_status[child] = "partial"
            parents_marked.add(child)
        if masked.children_status.get(parent) == "complete":
            masked.children_status[parent] = "partial"
            children_marked.add(parent)
    for parent in {p for _, p in withheld if masked.children_status.get(p) == "partial"}:
        masked.children_total[parent] = len(masked.children_of(parent))   # what the shortened list now holds

    record = {
        "mask_version": MASK_VERSION,
        "mask_seed": seed,
        "fraction": fraction,
        "n_relevant": len(order),
        "n_withheld": k,
        "realised_fraction": (k / len(order)) if order else None,
        "withheld_edges": [list(e) for e in withheld],
        "parents_marked_partial": sorted(parents_marked),
        "children_marked_partial": sorted(children_marked),
        "nodes_dropped": dropped,
    }
    return masked, record


def mask_levels(context, item, seed, levels=LEVELS):
    """{fraction: (masked context, record)} for every level, all from one removal order."""
    return {f: mask_context(context, item, f, seed) for f in levels}
