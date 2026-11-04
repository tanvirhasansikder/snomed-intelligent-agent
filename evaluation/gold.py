"""
Reference (gold) hierarchy derived from the frozen BioPortal call store.

Everything here is computed from stored API responses only:

* ``parents[c]``   direct parents of every concept whose parents were fetched
* ``children[c]``  direct children (with completeness) where they were fetched
* ``ancestors(c)`` transitive closure over ``parents``
* depth            shortest / longest number of IS_A steps to the root
"""

from collections import deque
from functools import lru_cache

from evaluation.collect import ROOT_ID
from explanation.explainer import ReferenceHierarchy


class GoldIndex:

    def __init__(self, store, dataset):
        self.dataset = dataset
        self.labels = {}
        self.parents = {}
        self.children = {}
        self.children_complete = set()
        self.api_ancestors = {}

        for record in store.records.values():
            method, arg, result = record["method"], record["arg"], record["result"]
            if method == "get_concept" and result:
                self.labels[arg] = result["name"]
            elif method == "get_parents":
                self.parents[arg] = sorted(p["concept_id"] for p in result)
                for p in result:
                    self.labels.setdefault(p["concept_id"], p.get("name") or p["concept_id"])
            elif method == "fetch_children":
                self.children[arg] = sorted(c["concept_id"] for c in result["children"])
                if result["complete"]:
                    self.children_complete.add(arg)
                for c in result["children"]:
                    self.labels.setdefault(c["concept_id"], c.get("name") or c["concept_id"])
            elif method == "get_ancestors":
                self.api_ancestors[arg] = sorted(a["concept_id"] for a in result)

        self.samples = {s["concept_id"]: s for s in dataset["samples"]}

    def label(self, concept_id):
        return self.labels.get(concept_id, concept_id)

    @lru_cache(maxsize=None)
    def ancestors(self, concept_id):
        seen, queue = set(), deque(self.parents.get(concept_id, []))
        while queue:
            node = queue.popleft()
            if node not in seen:
                seen.add(node)
                queue.extend(self.parents.get(node, []))
        return frozenset(seen)

    def lowest_common_ancestors(self, concept_a, concept_b):
        """
        Most specific shared ancestors of two concepts: common ancestors that
        are not an ancestor of another common ancestor (several are possible
        in a polyhierarchy).
        """
        return lowest_common(self.ancestors(concept_a), self.ancestors(concept_b), self.ancestors)

    def distance_to_ancestors(self, concept_id):
        """Shortest number of IS_A steps from concept to each ancestor."""
        dist, queue = {concept_id: 0}, deque([concept_id])
        while queue:
            node = queue.popleft()
            for parent in self.parents.get(node, []):
                if parent not in dist:
                    dist[parent] = dist[node] + 1
                    queue.append(parent)
        dist.pop(concept_id)
        return dist

    def depth(self, concept_id):
        """(shortest, longest) IS_A path length to the root; None if unreachable."""
        shortest = self.distance_to_ancestors(concept_id).get(ROOT_ID)

        @lru_cache(maxsize=None)
        def longest(node):
            if node == ROOT_ID:
                return 0
            options = [longest(p) for p in self.parents.get(node, [])]
            options = [o for o in options if o is not None]
            return (max(options) + 1) if options else None

        return shortest, longest(concept_id)

    def closure_nodes(self, concept_id):
        nodes = {concept_id} | set(self.ancestors(concept_id))
        nodes.update(self.children.get(concept_id, []))
        for parent in self.parents.get(concept_id, []):
            nodes.update(self.children.get(parent, []))
        return nodes

    def reference(self):
        """ReferenceHierarchy over everything stored (for the claim verifier)."""
        edges = {(c, p) for c, ps in self.parents.items() for p in ps}
        for parent, kids in self.children.items():
            edges.update((child, parent) for child in kids)
        ancestor_sets = {c: self.ancestors(c) for c in self.parents}
        return ReferenceHierarchy(
            edges,
            nodes=set(self.labels) | {c for e in edges for c in e},
            ancestor_sets=ancestor_sets,
            children_complete=self.children_complete,
            parents_complete=set(self.parents),
        )

    def top_level_of(self, concept_id):
        return self.samples[concept_id]["hierarchy_id"] if concept_id in self.samples else None


def lowest_common(ancestors_a, ancestors_b, ancestors_of):
    """Common ancestors not more general than another common ancestor."""
    common = set(ancestors_a) & set(ancestors_b)
    more_general = set()
    for node in common:
        more_general |= set(ancestors_of(node)) & common
    return sorted(common - more_general)
