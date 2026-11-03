"""
Retrieval conditions (protocol Section 5).

Each context is produced by the project's own GraphBuilder + BFSCrawler,
replaying frozen BioPortal responses through SnapshotClient into a fresh
in-memory graph. The resulting subgraph, its coverage records and the
crawler's frontier are captured in a ``Context`` and serialised for prompts:

* ``plain``     IS_A edges only
* ``annotated`` the same edges plus per-concept coverage (complete /
                truncated / failed / not retrieved) and the depth frontier
"""

from collections import deque
from dataclasses import dataclass, field

from database.memory_repository import InMemoryGraphRepository
from explanation.explainer import SubgraphEvidence
from knowledge_graph.graph_builder import GraphBuilder

CONDITIONS = {
    "C0": None,
    "U1": {"depth": 1, "children": False},
    "U2": {"depth": 2, "children": False},
    "U2D": {"depth": 2, "children": True},
    "FULL": {"depth": 20, "children": True},
}
FORMATS = ("plain", "annotated")

#: App default page cap for children (5 pages x 100) and the cap used for FULL.
APP_CHILD_PAGES = 5
FULL_CHILD_PAGES = 20


@dataclass
class Context:
    condition: str
    focal_id: str
    nodes: dict = field(default_factory=dict)            # id -> label
    edges: set = field(default_factory=set)              # (child, parent)
    parents_status: dict = field(default_factory=dict)   # id -> complete|failed
    children_status: dict = field(default_factory=dict)  # id -> complete|partial|failed
    children_total: dict = field(default_factory=dict)   # id -> number retrieved before display cap
    frontier: set = field(default_factory=set)
    failed: set = field(default_factory=set)

    # ------------------------------------------------------------------
    # derived views
    # ------------------------------------------------------------------

    def parents_of(self, concept_id):
        return sorted(p for c, p in self.edges if c == concept_id)

    def children_of(self, concept_id):
        return sorted(c for c, p in self.edges if p == concept_id)

    def ancestors_of(self, concept_id):
        seen, queue = set(), deque([concept_id])
        while queue:
            node = queue.popleft()
            for parent in self.parents_of(node):
                if parent not in seen:
                    seen.add(parent)
                    queue.append(parent)
        return seen

    def upward_path(self, start, target):
        """Shortest IS_A chain start -> ... -> target inside the context, or None."""
        previous, queue = {start: None}, deque([start])
        while queue:
            node = queue.popleft()
            if node == target:
                path = []
                while node is not None:
                    path.append(node)
                    node = previous[node]
                return list(reversed(path))
            for parent in self.parents_of(node):
                if parent not in previous:
                    previous[parent] = node
                    queue.append(parent)
        return None

    def parents_complete(self, concept_id):
        return self.parents_status.get(concept_id) == "complete"

    def children_complete(self, concept_id):
        return self.children_status.get(concept_id) == "complete"

    def closure_complete(self, concept_id):
        """True if every concept on every upward path has a complete parent list."""
        if not self.parents_complete(concept_id):
            return False
        return all(self.parents_complete(a) for a in self.ancestors_of(concept_id))

    def evidence(self):
        return SubgraphEvidence(
            self.nodes, self.edges,
            parents_complete={c for c, s in self.parents_status.items() if s == "complete"},
            children_complete={c for c, s in self.children_status.items() if s == "complete"},
        )

    # ------------------------------------------------------------------
    # serialisation
    # ------------------------------------------------------------------

    def _label(self, concept_id):
        return f"{self.nodes.get(concept_id) or concept_id} [{concept_id}]"

    def _distance_order(self):
        """Concepts ordered by IS_A distance from the focal concept (parents first, then children)."""
        order, seen, queue = [], {self.focal_id}, deque([self.focal_id])
        while queue:
            node = queue.popleft()
            order.append(node)
            for parent in self.parents_of(node):
                if parent not in seen:
                    seen.add(parent)
                    queue.append(parent)
        for child in self.children_of(self.focal_id):
            if child not in seen:
                seen.add(child)
                order.append(child)
        order.extend(sorted(set(self.nodes) - seen))
        return order

    def render(self, fmt):
        if fmt not in FORMATS:
            raise ValueError(f"format must be one of {FORMATS}")
        if self.condition == "C0" or not self.nodes:
            return "No evidence was retrieved."

        rank = {cid: i for i, cid in enumerate(self._distance_order())}
        edges = sorted(self.edges, key=lambda e: (rank.get(e[0], 1e9), rank.get(e[1], 1e9)))
        lines = ["Retrieved SNOMED CT IS_A relationships (child IS_A parent):"]
        lines += [f"- {self._label(c)} IS_A {self._label(p)}" for c, p in edges]
        if not edges:
            lines.append("- (none)")

        if fmt == "annotated":
            lines.append("")
            lines.append("Retrieval coverage for each concept in the evidence:")
            for concept_id in sorted(self.nodes, key=lambda c: rank.get(c, 1e9)):
                lines.append(f"- {self._label(concept_id)}: parents {self._parents_note(concept_id)}; "
                             f"children {self._children_note(concept_id)}")
            if self.frontier:
                frontier = ", ".join(self._label(c) for c in sorted(self.frontier, key=lambda c: rank.get(c, 1e9)))
                lines.append(f"Retrieval stopped at the depth limit at: {frontier}. "
                             "Their more general ancestors were not retrieved.")
        return "\n".join(lines)

    def _parents_note(self, concept_id):
        status = self.parents_status.get(concept_id)
        n = len(self.parents_of(concept_id))
        if status == "complete":
            return f"COMPLETE ({n} listed)" if n else "COMPLETE (none: top of the hierarchy)"
        if status == "failed":
            return f"RETRIEVAL FAILED ({n} listed)"
        return "NOT RETRIEVED" + (f" ({n} listed incidentally)" if n else "")

    def _children_note(self, concept_id):
        status = self.children_status.get(concept_id)
        n = len(self.children_of(concept_id))
        if status == "complete":
            return f"COMPLETE ({n} listed)" if n else "COMPLETE (none: no subtypes)"
        if status == "partial":
            total = self.children_total.get(concept_id, n)
            return f"PARTIAL ({n} listed; at least {total} retrieved, more may exist)"
        if status == "failed":
            return "RETRIEVAL FAILED"
        return "NOT RETRIEVED" + (f" ({n} listed incidentally)" if n else "")


def build_context(snapshot_client, concept_id, condition, child_display_cap=100):
    """Run the real retrieval code for one concept under one condition."""

    if condition not in CONDITIONS:
        raise ValueError(f"condition must be one of {list(CONDITIONS)}")
    spec = CONDITIONS[condition]
    context = Context(condition=condition, focal_id=concept_id)
    if spec is None:
        return context

    repo = InMemoryGraphRepository()
    builder = GraphBuilder(default_depth=spec["depth"], repository=repo, client=snapshot_client, use_cache=False)
    builder.expand_concept(concept_id, depth=spec["depth"])
    report = builder.last_report

    if spec["children"]:
        pages = FULL_CHILD_PAGES if condition == "FULL" else APP_CHILD_PAGES
        builder.expand_children(concept_id, max_pages=pages)

    context.nodes = dict(repo.concepts)
    context.edges = set(repo.edges)
    for cid in repo.concepts:
        coverage = repo.get_coverage(cid)
        if coverage["parents"]:
            context.parents_status[cid] = coverage["parents"]["status"]
        if coverage["children"]:
            context.children_status[cid] = coverage["children"]["status"]
    context.frontier = set(report.frontier) if report else set()
    context.failed = set(report.failed) if report else set()

    # Display cap for very long child lists: keep the first N by label and
    # mark the list partial so evidence and annotations stay truthful.
    kids = context.children_of(concept_id)
    if len(kids) > child_display_cap:
        keep = set(sorted(kids, key=lambda c: (str(context.nodes.get(c)), c))[:child_display_cap])
        for child in kids:
            if child not in keep:
                context.edges.discard((child, concept_id))
                if not any(child in e for e in context.edges):
                    context.nodes.pop(child, None)
        context.children_total[concept_id] = len(kids)
        context.children_status[concept_id] = "partial"
    return context
