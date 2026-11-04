"""
Citation checks for hierarchical claims (protocol v2.0, Sections 6-7).

The model cites evidence edges by the IDs shown in the ``v2`` evidence
(E1, E2, ...); ``Context.edge_index()`` maps those IDs to (child, parent)
edges. These checks are deterministic and use only the retrieved evidence,
so S2 can apply them at run time.

A claim's citation status is one of:

* ``entails``         every cited ID exists, and the cited edges alone entail
                      the claim
* ``not_entailed``    every cited ID exists, but the cited edges do not entail it
* ``invalid_edge_id`` at least one cited ID is not in the evidence (a
                      fabricated reference counts as an error even if the
                      other cited edges would suffice)
* ``no_citation``     the claim cites nothing
* ``not_applicable``  absence claims (``no_children``, ``no_parents``): they
                      rest on coverage information, not on an edge

Entailment:

* ``direct_parent(X, Y)``: the edge X IS_A Y is among the cited edges
* ``ancestor(X, Y)``:      the cited edges contain an IS_A chain from X up to Y

Claim-level precision and recall follow the statement-level citation
metrics of Gao et al. (EMNLP 2023), with graph entailment in place of an NLI
model (see ``citation_counts``).
"""

from collections import defaultdict, deque

ENTAILS = "entails"
NOT_ENTAILED = "not_entailed"
INVALID_EDGE_ID = "invalid_edge_id"
NO_CITATION = "no_citation"
NOT_APPLICABLE = "not_applicable"
CITATION_STATUSES = (ENTAILS, NOT_ENTAILED, INVALID_EDGE_ID, NO_CITATION, NOT_APPLICABLE)

CITABLE_RELATIONS = ("direct_parent", "ancestor")


def supporting_path(subject, obj, edges):
    """
    Shortest IS_A chain from ``subject`` up to ``obj`` using only ``edges``
    ((child, parent) pairs), as a list of edges; None if there is none.
    """
    up = defaultdict(list)
    for child, parent in edges:
        up[child].append(parent)
    previous = {subject: None}
    queue = deque([subject])
    while queue:
        node = queue.popleft()
        if node == obj and node != subject:
            break
        for parent in sorted(up.get(node, ())):
            if parent not in previous:
                previous[parent] = node
                queue.append(parent)
    if obj not in previous or obj == subject:
        return None
    path, node = [], obj
    while previous[node] is not None:
        path.append((previous[node], node))
        node = previous[node]
    return list(reversed(path))


def check_citation(claim, edge_index):
    """Citation status of one parsed claim (see module docstring)."""
    if claim.get("relation") not in CITABLE_RELATIONS:
        return NOT_APPLICABLE
    cites = claim.get("cites") or []
    if not cites:
        return NO_CITATION
    if any(c not in edge_index for c in cites):
        return INVALID_EDGE_ID
    cited = {edge_index[c] for c in cites}
    subject, obj = claim.get("subject"), claim.get("object")
    if claim["relation"] == "direct_parent":
        return ENTAILS if (subject, obj) in cited else NOT_ENTAILED
    return ENTAILS if supporting_path(subject, obj, cited) else NOT_ENTAILED


def cite_for(claim, edge_index):
    """
    Minimal correct citation for a claim the evidence supports (used by the
    deterministic R0 reference): the IDs of the edges on a shortest
    supporting chain, or [] if the evidence does not support it.
    """
    if claim.get("relation") not in CITABLE_RELATIONS:
        return []
    id_of = {edge: eid for eid, edge in edge_index.items()}
    if claim["relation"] == "direct_parent":
        edge = (claim.get("subject"), claim.get("object"))
        return [id_of[edge]] if edge in id_of else []
    path = supporting_path(claim.get("subject"), claim.get("object"), edge_index.values())
    return [id_of[edge] for edge in path] if path else []


def citation_counts(claims, supported_label="supported_by_subgraph"):
    """
    Per-answer counts behind the citation metrics. ``claims`` are labelled
    claims carrying a ``citation`` status and a verifier ``label``.

    * precision = n_cite_entails / n_cite_attempted
      (claims that cite anything, excluding absence claims)
    * recall    = n_supported_cited_ok / n_supported_citable
      (claims supported by the evidence that are entailed by their citation)
    """
    citable = [c for c in claims if c.get("citation") != NOT_APPLICABLE]
    attempted = [c for c in citable if c.get("citation") != NO_CITATION]
    supported = [c for c in citable if c.get("label") == supported_label]
    return {
        "n_cite_citable": len(citable),
        "n_cite_attempted": len(attempted),
        "n_cite_entails": sum(1 for c in attempted if c["citation"] == ENTAILS),
        "n_cite_not_entailed": sum(1 for c in attempted if c["citation"] == NOT_ENTAILED),
        "n_cite_invalid_edge_id": sum(1 for c in attempted if c["citation"] == INVALID_EDGE_ID),
        "n_cite_missing": len(citable) - len(attempted),
        "n_supported_citable": len(supported),
        "n_supported_cited_ok": sum(1 for c in supported if c.get("citation") == ENTAILS),
    }
