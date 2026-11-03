"""
In-memory implementation of the GraphRepository interface.

Used by the experiments (evaluation/) so that the project's real crawler and
graph builder can run deterministically, many thousands of times, without a
Neo4j server. Behaviour mirrors database/graph_repository.py:

* (child)-[:IS_A]->(parent) edges; save_relationship needs both endpoints.
* Coverage records per concept and direction ("up" = parents, "down" =
  children) with status complete | partial | failed. No record means that
  direction was never retrieved.
"""

from collections import deque

_DIRECTIONS = {"up": "parents", "down": "children"}
_STATUSES = {"complete", "partial", "failed"}
_FIELDS = ("status", "retrieved_at", "source", "source_version", "count", "error")


class InMemoryGraphRepository:

    def __init__(self):
        self.concepts = {}          # concept_id -> name
        self.edges = set()          # (child_id, parent_id)
        self._coverage = {}         # (concept_id, "parents"|"children") -> dict

    # ------------------------------------------------------------------
    # concepts
    # ------------------------------------------------------------------

    def save_concept(self, concept_id, name):
        self.concepts[concept_id] = name
        return True

    def get_concept(self, concept_id):
        if concept_id not in self.concepts:
            return None
        return {"concept_id": concept_id, "name": self.concepts[concept_id]}

    def concept_exists(self, concept_id):
        return concept_id in self.concepts

    def find_concept_by_exact_name(self, name):
        target = name.strip().lower()
        for concept_id in sorted(self.concepts):
            label = self.concepts[concept_id]
            if isinstance(label, str) and label.lower() == target:
                return {"concept_id": concept_id, "name": label}
        return None

    def search_concepts(self, keyword):
        needle = keyword.lower()
        hits = [
            {"concept_id": cid, "name": name}
            for cid, name in self.concepts.items()
            if isinstance(name, str) and needle in name.lower()
        ]
        return sorted(hits, key=lambda r: r["name"])[:20]

    # ------------------------------------------------------------------
    # relationships
    # ------------------------------------------------------------------

    def save_relationship(self, child_id, parent_id):
        if child_id not in self.concepts or parent_id not in self.concepts:
            return False
        self.edges.add((child_id, parent_id))
        return True

    def save_children(self, parent_id, children):
        if parent_id not in self.concepts:
            return 0
        for child in children:
            self.concepts[child["concept_id"]] = child["name"]
            self.edges.add((child["concept_id"], parent_id))
        return sum(1 for c, p in self.edges if p == parent_id)

    def _rows(self, ids):
        return sorted(({"concept_id": i, "name": self.concepts.get(i)} for i in ids),
                      key=lambda r: (str(r["name"]), r["concept_id"]))

    def get_parents(self, concept_id):
        return self._rows(p for c, p in self.edges if c == concept_id)

    def get_children(self, concept_id):
        return self._rows(c for c, p in self.edges if p == concept_id)

    def _walk(self, concept_id, max_depth, upward):
        if isinstance(max_depth, bool) or not isinstance(max_depth, int) or not 1 <= max_depth <= 20:
            raise ValueError("max_depth must be an integer between 1 and 20")
        seen, queue = set(), deque([(concept_id, 0)])
        while queue:
            current, depth = queue.popleft()
            if depth >= max_depth:
                continue
            for child, parent in self.edges:
                nxt = parent if upward and child == current else child if not upward and parent == current else None
                if nxt is not None and nxt not in seen:
                    seen.add(nxt)
                    queue.append((nxt, depth + 1))
        seen.discard(concept_id)
        return self._rows(seen)

    def get_ancestors(self, concept_id, max_depth=10):
        return self._walk(concept_id, max_depth, upward=True)

    def get_descendants(self, concept_id, max_depth=10):
        return self._walk(concept_id, max_depth, upward=False)

    def find_upward_path(self, descendant_id, ancestor_id, max_depth=20):
        if descendant_id == ancestor_id:
            concept = self.get_concept(descendant_id)
            return [concept] if concept else None
        previous, queue = {descendant_id: None}, deque([(descendant_id, 0)])
        while queue:
            current, depth = queue.popleft()
            if current == ancestor_id:
                path = []
                while current is not None:
                    path.append({"concept_id": current, "name": self.concepts.get(current)})
                    current = previous[current]
                return list(reversed(path))
            if depth >= max_depth:
                continue
            for child, parent in sorted(self.edges):
                if child == current and parent not in previous:
                    previous[parent] = current
                    queue.append((parent, depth + 1))
        return None

    # ------------------------------------------------------------------
    # coverage
    # ------------------------------------------------------------------

    def mark_expansion(self, concept_id, direction, status, retrieved_at=None, source=None,
                       source_version=None, count=None, error=None):
        if direction not in _DIRECTIONS:
            raise ValueError(f"direction must be one of {sorted(_DIRECTIONS)}")
        if status not in _STATUSES:
            raise ValueError(f"status must be one of {sorted(_STATUSES)}")
        if concept_id not in self.concepts:
            return False
        self._coverage[(concept_id, _DIRECTIONS[direction])] = {
            "status": status, "retrieved_at": retrieved_at, "source": source,
            "source_version": source_version, "count": count,
            "error": (str(error)[:300] if error else None),
        }
        return True

    def get_coverage(self, concept_id):
        return {
            "parents": dict(self._coverage[(concept_id, "parents")]) if (concept_id, "parents") in self._coverage else None,
            "children": dict(self._coverage[(concept_id, "children")]) if (concept_id, "children") in self._coverage else None,
        }

    def get_expansion_status(self, concept_id, direction):
        if direction not in _DIRECTIONS:
            raise ValueError(f"direction must be one of {sorted(_DIRECTIONS)}")
        return self.get_coverage(concept_id)[_DIRECTIONS[direction]]

    # ------------------------------------------------------------------
    # statistics / management
    # ------------------------------------------------------------------

    def count_concepts(self):
        return len(self.concepts)

    def count_relationships(self):
        return len(self.edges)

    def ensure_constraints(self):
        return None

    def clear_database(self, confirm=False):
        if confirm is not True:
            raise ValueError("clear_database() deletes all data; call clear_database(confirm=True) to proceed")
        self.concepts.clear()
        self.edges.clear()
        self._coverage.clear()

    def close(self):
        return None
