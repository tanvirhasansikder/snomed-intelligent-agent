"""
Template-based (deterministic, no LLM) explanation of where a concept sits
in the retrieved hierarchy. Every sentence is generated from stored edges
and carries the claims it makes, so it can be verified with
explanation.explainer. This also serves as the "R0" reference system in the
evaluation protocol (docs/EVALUATION_PROTOCOL.md).
"""

from explanation.explainer import ANCESTOR, DIRECT_PARENT, HierarchyClaim


def _label(item):
    return f"{item.get('name') or item['concept_id']} ({item['concept_id']})"


def _join(items):
    labels = [_label(i) for i in items]
    if len(labels) <= 1:
        return "".join(labels)
    return ", ".join(labels[:-1]) + " and " + labels[-1]


def explain_position(result, max_children=10):
    """
    Build an explanation from a successful QueryProcessor result.

    Returns {"sentences": [...], "text": str, "claims": [HierarchyClaim],
             "evidence_edges": ["child->parent", ...], "caveats": [...]}.
    """

    concept = result.get("concept") or {}
    concept_id = concept.get("concept_id")
    if not concept_id:
        raise ValueError("result has no resolved concept")

    parents = result.get("parents") or []
    children = result.get("children") or []
    coverage = result.get("coverage") or {}

    sentences, claims, edges, caveats = [], [], [], []
    subject = _label(concept)

    if parents:
        sentences.append(f"{subject} is a direct subtype of {_join(parents)}.")
        for parent in parents:
            claims.append(HierarchyClaim(concept_id, DIRECT_PARENT, parent["concept_id"]))
            edges.append(f"{concept_id}->{parent['concept_id']}")
    elif (coverage.get("parents") or {}).get("status") == "complete":
        sentences.append(f"{subject} has no parent concepts: it is at the top of the hierarchy.")
    else:
        caveats.append("Parents were not retrieved completely, so no parent statement is made.")

    shown = children[:max_children]
    if shown:
        more = len(children) - len(shown)
        tail = f" (and {more} more)" if more > 0 else ""
        sentences.append(f"Its direct subtypes include {_join(shown)}{tail}.")
        for child in shown:
            claims.append(HierarchyClaim(child["concept_id"], DIRECT_PARENT, concept_id))
            edges.append(f"{child['concept_id']}->{concept_id}")

    children_status = (coverage.get("children") or {}).get("status")
    if children_status == "complete" and not children:
        sentences.append(f"{subject} has no subtypes: it is a leaf concept.")
    elif children_status != "complete":
        caveats.append("The child list is not known to be complete.")

    ancestors_coverage = coverage.get("ancestors") or {}
    if ancestors_coverage.get("frontier") or ancestors_coverage.get("status") == "partial":
        caveats.append(f"Ancestors were retrieved only up to {ancestors_coverage.get('depth')} level(s).")

    return {
        "sentences": sentences,
        "text": " ".join(sentences),
        "claims": claims,
        "evidence_edges": edges,
        "caveats": caveats,
    }


def explain_upward_path(path):
    """
    Explain a hierarchy path [concept, parent, grandparent, ...] as returned
    by GraphRepository.find_upward_path(). Returns (text, claims).
    """

    if not path:
        return "No hierarchy path was found in the retrieved subgraph.", []
    if len(path) == 1:
        return f"{_label(path[0])} is the same concept.", []

    steps = [_label(path[0])] + [f"which is a {_label(node)}" for node in path[1:]]
    claims = [HierarchyClaim(path[i]["concept_id"], DIRECT_PARENT, path[i + 1]["concept_id"])
              for i in range(len(path) - 1)]
    claims.append(HierarchyClaim(path[0]["concept_id"], ANCESTOR, path[-1]["concept_id"]))
    text = steps[0] + " is a " + ", ".join(steps[1:])[len("which is a "):] + "."
    return text, claims
