"""
Deterministic verification of hierarchy claims (no LLM involved).

A *claim* says that ``object_id`` is a direct parent or an ancestor of
``subject_id`` (i.e. subject IS_A object, directly or transitively). The
verifier labels each claim against

* the retrieved subgraph (``SubgraphEvidence``), which may be incomplete, and
* optionally a reference hierarchy (``ReferenceHierarchy``, e.g. a frozen
  gold snapshot), used only for evaluation.

Labels
------
supported_by_subgraph            the retrieved edges entail the claim
true_not_retrieved               not in the subgraph, but true in the reference
false_in_reference               not in the subgraph and false in the reference
contradicted_by_complete_evidence  no reference given, but the subject's
                                 parents (or full ancestor closure) were
                                 retrieved completely and do not include it
insufficient_evidence            cannot be decided from the evidence
unknown_identifier               an ID that does not exist in the reference

Without a reference the verifier never claims a statement is false unless the
relevant evidence is known to be complete. This is the rule that keeps an
incomplete local graph from being read as proof of absence.
"""

from collections import deque
from dataclasses import dataclass

DIRECT_PARENT = "direct_parent"
ANCESTOR = "ancestor"
RELATIONS = (DIRECT_PARENT, ANCESTOR)

SUPPORTED = "supported_by_subgraph"
TRUE_NOT_RETRIEVED = "true_not_retrieved"
FALSE_IN_REFERENCE = "false_in_reference"
CONTRADICTED = "contradicted_by_complete_evidence"
INSUFFICIENT = "insufficient_evidence"
UNKNOWN_IDENTIFIER = "unknown_identifier"

LABELS = (SUPPORTED, TRUE_NOT_RETRIEVED, FALSE_IN_REFERENCE, CONTRADICTED, INSUFFICIENT, UNKNOWN_IDENTIFIER)


def _is_concept_id(value):
    return isinstance(value, str) and value.isdigit()


@dataclass(frozen=True)
class HierarchyClaim:
    subject_id: str
    relation: str
    object_id: str

    def __post_init__(self):
        if self.relation not in RELATIONS:
            raise ValueError(f"relation must be one of {RELATIONS}")
        if not _is_concept_id(self.subject_id) or not _is_concept_id(self.object_id):
            raise ValueError("subject_id and object_id must be numeric concept ID strings")

    def as_dict(self):
        return {"subject": self.subject_id, "relation": self.relation, "object": self.object_id}


def parse_claims(records):
    """
    Build claims from dicts {'subject', 'relation', 'object'} (for example a
    structured LLM output). Raises ValueError on the first invalid record.
    """
    claims = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise ValueError(f"claim {index} is not an object")
        try:
            claims.append(HierarchyClaim(str(record["subject"]), record["relation"], str(record["object"])))
        except KeyError as exc:
            raise ValueError(f"claim {index} is missing {exc}") from None
    return claims


class _Hierarchy:
    """Shared reachability over (child, parent) edges."""

    def __init__(self, edges):
        self.parents = {}
        for child, parent in edges:
            self.parents.setdefault(child, set()).add(parent)

    def has_edge(self, child, parent):
        return parent in self.parents.get(child, ())

    def ancestors(self, concept_id):
        seen, queue = set(), deque([concept_id])
        while queue:
            for parent in self.parents.get(queue.popleft(), ()):
                if parent not in seen:
                    seen.add(parent)
                    queue.append(parent)
        return seen


class SubgraphEvidence(_Hierarchy):
    """
    What was actually retrieved.

    nodes             concept_id -> label
    edges             (child_id, parent_id) IS_A edges
    parents_complete  IDs whose full direct-parent list was retrieved
    """

    def __init__(self, nodes, edges, parents_complete=()):
        edges = set(edges)
        super().__init__(edges)
        self.nodes = dict(nodes)
        self.edges = edges
        self.parents_complete = set(parents_complete)

    def closure_complete(self, concept_id):
        """True if every concept on every upward path has a complete parent list."""
        if concept_id not in self.parents_complete:
            return False
        return all(a in self.parents_complete for a in self.ancestors(concept_id))

    @classmethod
    def from_query_result(cls, result):
        """
        Evidence from a QueryProcessor result: the focal concept, its direct
        parents and children. Ancestors beyond direct parents are *not* turned
        into edges, because the result does not say which edge links them.
        """
        concept = result.get("concept") or {}
        focal = concept.get("concept_id")
        nodes = {focal: concept.get("name")} if focal else {}
        edges = set()
        for parent in result.get("parents") or []:
            nodes[parent["concept_id"]] = parent.get("name")
            edges.add((focal, parent["concept_id"]))
        for child in result.get("children") or []:
            nodes[child["concept_id"]] = child.get("name")
            edges.add((child["concept_id"], focal))
        complete = set()
        parents_coverage = (result.get("coverage") or {}).get("parents") or {}
        if focal and parents_coverage.get("status") == "complete":
            complete.add(focal)
        return cls(nodes, edges, complete)


class ReferenceHierarchy(_Hierarchy):
    """
    A trusted hierarchy used for evaluation (e.g. a frozen gold snapshot).

    ``ancestor_sets`` optionally gives the full ancestor set of some concepts
    (as returned by BioPortal /ancestors), so their closure does not depend on
    every intermediate parent list being present.
    """

    def __init__(self, edges, nodes=None, ancestor_sets=None):
        edges = set(edges)
        super().__init__(edges)
        self.ancestor_sets = {k: set(v) for k, v in (ancestor_sets or {}).items()}
        known = set(nodes or ())
        for child, parent in edges:
            known.update((child, parent))
        for concept_id, ancestors in self.ancestor_sets.items():
            known.add(concept_id)
            known.update(ancestors)
        self.nodes = known

    def is_ancestor(self, concept_id, candidate):
        if concept_id in self.ancestor_sets:
            return candidate in self.ancestor_sets[concept_id]
        return candidate in self.ancestors(concept_id)


def verify_claim(claim, evidence, reference=None):
    """Return one label from LABELS for ``claim``."""

    if reference is not None and (claim.subject_id not in reference.nodes or claim.object_id not in reference.nodes):
        return UNKNOWN_IDENTIFIER

    if claim.relation == DIRECT_PARENT:
        supported = evidence.has_edge(claim.subject_id, claim.object_id)
    else:
        supported = claim.object_id in evidence.ancestors(claim.subject_id)

    if supported:
        return SUPPORTED

    if reference is not None:
        if claim.relation == DIRECT_PARENT:
            true = reference.has_edge(claim.subject_id, claim.object_id)
        else:
            true = reference.is_ancestor(claim.subject_id, claim.object_id)
        return TRUE_NOT_RETRIEVED if true else FALSE_IN_REFERENCE

    if claim.subject_id not in evidence.nodes:
        return INSUFFICIENT

    if claim.relation == DIRECT_PARENT and claim.subject_id in evidence.parents_complete:
        return CONTRADICTED

    if claim.relation == ANCESTOR and evidence.closure_complete(claim.subject_id):
        return CONTRADICTED

    return INSUFFICIENT


def verify_claims(claims, evidence, reference=None):
    """Return a list of {'claim': {...}, 'label': ...} in input order."""
    return [{"claim": c.as_dict(), "label": verify_claim(c, evidence, reference)} for c in claims]


def filter_supported(claims, evidence):
    """
    Keep only claims supported by the retrieved subgraph. Intended as the
    guard between a future LLM layer and anything shown or stored as fact.
    """
    kept, removed = [], []
    for claim in claims:
        (kept if verify_claim(claim, evidence) == SUPPORTED else removed).append(claim)
    return kept, removed
