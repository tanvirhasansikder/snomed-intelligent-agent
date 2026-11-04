"""
Task items (protocol Section 4), deterministic sufficiency labels, and the
evidence-only reference system R0.

Every item's gold answer comes from the frozen BioPortal data (GoldIndex).
``sufficient(item, context)`` says whether the retrieved context alone
contains enough to answer the item correctly; it is computed from the
context's edges and coverage records, never by an LLM.
"""

import random
from dataclasses import asdict, dataclass, field

from evaluation.collect import ROOT_ID
from explanation.citations import cite_for

TASKS = ("T1", "T2", "T3", "T4", "T5")
MAX_GOLD_CHILDREN = 100


@dataclass
class Item:
    item_id: str
    task: str
    concept_id: str
    gold: object
    target_id: str = None
    polarity: str = None          # T3: "pos" | "neg"
    meta: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        return cls(**data)


def generate_items(gold, tasks=TASKS, seed=0):
    """Deterministic items for every sampled concept with a usable closure."""

    items = []
    for concept_id in sorted(gold.samples):
        sample = gold.samples[concept_id]
        rng = random.Random(f"{seed}:{concept_id}")
        shortest, longest = gold.depth(concept_id)
        meta = {
            "hierarchy_id": sample["hierarchy_id"],
            "hierarchy_name": sample["hierarchy_name"],
            "depth_min": shortest,
            "depth_max": longest,
            "n_parents": len(gold.parents.get(concept_id, [])),
            "n_ancestors": len(gold.ancestors(concept_id)),
        }
        parents = gold.parents.get(concept_id)
        if parents is None:
            continue

        if "T1" in tasks:
            items.append(Item(f"T1:{concept_id}", "T1", concept_id, sorted(parents), meta=dict(meta)))

        children = gold.children.get(concept_id)
        if ("T2" in tasks and children is not None and concept_id in gold.children_complete
                and len(children) <= MAX_GOLD_CHILDREN):
            items.append(Item(f"T2:{concept_id}", "T2", concept_id, sorted(children),
                              meta=dict(meta, n_children=len(children))))

        if "T3" in tasks:
            distances = gold.distance_to_ancestors(concept_id)
            far = sorted(a for a, d in distances.items() if d >= 2 and a != ROOT_ID)
            if far:
                target = rng.choice(far)
                items.append(Item(f"T3+:{concept_id}", "T3", concept_id, True, target_id=target, polarity="pos",
                                  meta=dict(meta, distance=distances[target])))
            ancestors = gold.ancestors(concept_id)
            siblings = sorted({
                s for p in parents for s in gold.children.get(p, [])
                if s != concept_id and s not in ancestors
            })
            if siblings:
                target = rng.choice(siblings)
                items.append(Item(f"T3-:{concept_id}", "T3", concept_id, False, target_id=target, polarity="neg",
                                  meta=dict(meta, negative_type="sibling")))

        if "T4" in tasks and sample["hierarchy_id"] in gold.ancestors(concept_id):
            items.append(Item(f"T4:{concept_id}", "T4", concept_id, None, target_id=sample["hierarchy_id"],
                              meta=dict(meta)))

        if "T5" in tasks:
            items.append(Item(f"T5:{concept_id}", "T5", concept_id, None, meta=dict(meta)))

    return items


def sufficient(item, context):
    """True if the context alone determines the gold answer (deterministic)."""

    x = item.concept_id
    if context.condition == "C0":
        return False
    if item.task == "T1":
        return context.parents_complete(x)
    if item.task == "T2":
        return context.children_complete(x)
    if item.task == "T3":
        if item.polarity == "pos":
            return item.target_id in context.ancestors_of(x)
        return context.closure_complete(x)
    if item.task == "T4":
        return context.upward_path(x, item.target_id) is not None
    if item.task == "T5":
        return context.parents_complete(x) and context.children_complete(x)
    raise ValueError(item.task)


def reference_answer(item, context):
    """
    R0: deterministic, evidence-only answer in the same JSON shape the LLM is
    asked for. Abstains whenever the context is insufficient.
    """

    x = item.concept_id
    claims, answer, insufficient = [], None, False

    def claim(subject, relation, obj=None):
        claims.append({"subject": subject, "relation": relation, "object": obj, "source": "evidence"})

    if item.task == "T1":
        answer = context.parents_of(x)
        for p in answer:
            claim(x, "direct_parent", p)
        if not context.parents_complete(x):
            insufficient = True
        elif not answer:
            claim(x, "no_parents")
    elif item.task == "T2":
        answer = context.children_of(x)
        for c in answer:
            claim(c, "direct_parent", x)
        if not context.children_complete(x):
            insufficient = True
        elif not answer:
            claim(x, "no_children")
    elif item.task == "T3":
        path = context.upward_path(x, item.target_id)
        if path:
            answer = "yes"
            claim(x, "ancestor", item.target_id)
        elif context.closure_complete(x):
            answer = "no"
        else:
            answer, insufficient = "unknown", True
    elif item.task == "T4":
        path = context.upward_path(x, item.target_id)
        if path:
            answer = path
            for a, b in zip(path, path[1:]):
                claim(a, "direct_parent", b)
        else:
            answer, insufficient = [], True
    elif item.task == "T5":
        for p in context.parents_of(x):
            claim(x, "direct_parent", p)
        for a in sorted(context.ancestors_of(x) - set(context.parents_of(x))):
            claim(x, "ancestor", a)
        for c in context.children_of(x):
            claim(c, "direct_parent", x)
        if context.children_complete(x) and not context.children_of(x):
            claim(x, "no_children")
        answer = "Template explanation generated from the retrieved evidence."
        insufficient = not sufficient(item, context)

    edge_index = context.edge_index()
    for c in claims:  # R0 cites exactly the evidence edges that support each claim
        c["cites"] = cite_for(c, edge_index)
    return {"answer": answer, "claims": claims, "insufficient_evidence": insufficient}
