"""
A seeded SYNTHETIC ontology with the BioPortal client interface.

It exists only to test and dry-run the research pipeline without network
access. Its concepts, labels and hierarchy are generated; nothing here is
SNOMED CT content, and results on it are not findings.

Shape: a root with 6 top-level concepts; each top-level subtree is grown to
depth 9 with random branching (0-4 children) and about 8% of non-top nodes
given a second parent at the level above, so the graph is a polyhierarchy
like SNOMED CT.
"""

import random
from collections import deque

from crawler.bioportal_client import BioPortalNotFoundError
from evaluation.sctid import make_concept_sctid

ROOT_ID = "138875005"


class SyntheticOntology:

    def __init__(self, seed=0, top_level=6, max_depth=9, max_branching=4, second_parent_rate=0.08):
        rng = random.Random(seed)
        self.labels = {ROOT_ID: "Synthetic root"}
        self.parents = {ROOT_ID: []}
        counter = [1000]

        def new_id():
            counter[0] += 1
            return make_concept_sctid(counter[0])

        self.top_level = []
        levels = {}
        for t in range(top_level):
            cid = new_id()
            self.labels[cid] = f"Synthetic hierarchy {t + 1}"
            self.parents[cid] = [ROOT_ID]
            self.top_level.append(cid)
            levels.setdefault(1, []).append(cid)

        frontier = deque((cid, 1) for cid in self.top_level)
        while frontier:
            node, depth = frontier.popleft()
            if depth >= max_depth:
                continue
            branching = rng.randint(0, max_branching) if depth > 1 else rng.randint(3, max_branching)
            for b in range(branching):
                cid = new_id()
                self.labels[cid] = f"{self.labels[node]} / type {b + 1}"
                parents = [node]
                same_level = [n for n in levels.get(depth, []) if n != node]
                if same_level and rng.random() < second_parent_rate:
                    parents.append(rng.choice(same_level))
                self.parents[cid] = parents
                levels.setdefault(depth + 1, []).append(cid)
                frontier.append((cid, depth + 1))

        self.children = {}
        for child, parents in self.parents.items():
            for parent in parents:
                self.children.setdefault(parent, []).append(child)
        self.request_count = 0

    # ------------------------------------------------------------------
    # BioPortalClient-compatible interface (strict mode semantics)
    # ------------------------------------------------------------------

    def _record(self, concept_id):
        return {"concept_id": concept_id, "name": self.labels[concept_id]}

    def top_level_names(self):
        return {cid: self.labels[cid] for cid in self.top_level}

    def get_concept(self, concept_id, raise_errors=False):
        self.request_count += 1
        if concept_id not in self.labels:
            return None
        return {"prefLabel": self.labels[concept_id]}

    def get_parents(self, concept_id, raise_errors=False):
        self.request_count += 1
        if concept_id not in self.labels:
            raise BioPortalNotFoundError(f"unknown class {concept_id}", status_code=404)
        return [self._record(p) for p in self.parents[concept_id]]

    def fetch_children(self, concept_id, max_pages=5, pagesize=100):
        self.request_count += 1
        if concept_id not in self.labels:
            raise BioPortalNotFoundError(f"unknown class {concept_id}", status_code=404)
        children = [self._record(c) for c in sorted(self.children.get(concept_id, []))]
        cap = max_pages * pagesize
        return {"children": children[:cap], "complete": len(children) <= cap, "pages_fetched": 1}

    def get_ancestors(self, concept_id, raise_errors=False, max_pages=20):
        self.request_count += 1
        seen, queue = [], deque(self.parents.get(concept_id, []))
        while queue:
            node = queue.popleft()
            if node not in seen:
                seen.append(node)
                queue.extend(self.parents.get(node, []))
        return [self._record(a) for a in seen]

    def get_ontology_version(self, raise_errors=False):
        return {"version": "SYNTHETIC", "released": None}
