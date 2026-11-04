"""
S2 deterministic verification (protocol v2.0, Section 6; contribution C-B).

No LLM is involved. Everything here uses only the RETRIEVED evidence (a
``Context``), so it can run in the deployed system. The frozen reference is
used later, in evaluation only.

* ``find_problems``    what must be fixed in an answer: claims not supported
                       by the evidence (including the model's own-knowledge
                       claims), wrong or fabricated citations, and concept IDs
                       in an explanation's text that are missing from its
                       claim list
* ``filter_answer``    the evidence-only answer: keep only answer parts the
                       evidence supports; abstain when nothing is left (also
                       used by evaluation for the verified "LV" view)
* ``finalise``         the S2 output after at most one regeneration: remove
                       unsupported claims, repair the citation of supported
                       claims to the exact supporting edges (flagged), and
                       for explanations replace text that is still
                       unverifiable with a template built from verified
                       claims only (flagged)
"""

import re

from explanation.citations import INVALID_EDGE_ID, NOT_ENTAILED, cite_for
from explanation.explainer import SUPPORTED

_ID_IN_TEXT = re.compile(r"(?<!\d)\d{6,18}(?!\d)")
BAD_CITATIONS = (NOT_ENTAILED, INVALID_EDGE_ID)


def ids_in_text(text):
    """Concept-ID-like numbers mentioned in free text, in order, without duplicates."""
    seen = []
    for match in _ID_IN_TEXT.findall(str(text or "")):
        if match not in seen:
            seen.append(match)
    return seen


def find_problems(item, parsed, checked_claims):
    """
    ``checked_claims``: claims with ``runtime_label`` and ``citation``
    (llm.agent.runtime_verify). Returns lists of problems; all empty means
    nothing to fix.
    """
    unsupported = [c for c in checked_claims if c["runtime_label"] != SUPPORTED]
    bad_citations = [c for c in checked_claims
                     if c["runtime_label"] == SUPPORTED and c["citation"] in BAD_CITATIONS]
    unlisted = []
    if item.task == "T5" and isinstance(parsed.get("answer"), str):
        listed = {item.concept_id, item.target_id}
        for c in checked_claims:
            listed.update({c.get("subject"), c.get("object")})
        unlisted = [i for i in ids_in_text(parsed["answer"]) if i not in listed]
    return {"unsupported": unsupported, "bad_citations": bad_citations, "unlisted_ids": unlisted}


def has_problems(problems):
    return any(problems.values())


def filter_answer(item, answer, context):
    """Evidence-only answer and whether it abstains (deterministic)."""
    x = item.concept_id
    if item.task == "T1":
        kept = [p for p in (answer or []) if (x, p) in context.edges]
        return kept, not kept and not (context.parents_complete(x) and not context.parents_of(x))
    if item.task == "T2":
        kept = [c for c in (answer or []) if (c, x) in context.edges]
        leaf_confirmed = context.children_complete(x) and not context.children_of(x)
        return kept, not kept and not leaf_confirmed
    if item.task == "T3":
        if answer == "yes" and item.target_id in context.ancestors_of(x):
            return "yes", False
        if answer == "no" and context.closure_complete(x) and item.target_id not in context.ancestors_of(x):
            return "no", False
        return "unknown", True
    if item.task == "T4":
        chain = list(answer or [])
        ok = (len(chain) >= 2 and chain[0] == x and chain[-1] == item.target_id
              and all((a, b) in context.edges for a, b in zip(chain, chain[1:])))
        return (chain, False) if ok else ([], True)
    if item.task == "T6":
        z = item.target_id
        common = context.ancestors_of(x) & context.ancestors_of(z)
        kept = [a for a in (answer or []) if a in common]
        return kept, not kept
    return answer, False


def template_explanation(item, claims, label_of):
    """A T5 explanation built only from verified claims, citing their edges."""
    x = item.concept_id

    def ref(cid):
        return f"{label_of(cid)} [{cid}]"

    def cites(claim):
        return f" ({', '.join(claim['cites'])})" if claim.get("cites") else ""

    parents = [c for c in claims if c["relation"] == "direct_parent" and c["subject"] == x]
    ancestors = [c for c in claims if c["relation"] == "ancestor" and c["subject"] == x]
    children = [c for c in claims if c["relation"] == "direct_parent" and c.get("object") == x]
    leaf = any(c["relation"] == "no_children" and c["subject"] == x for c in claims)

    sentences = []
    if parents:
        sentences.append(f"{ref(x)} is a direct subtype of "
                         + "; ".join(ref(c["object"]) + cites(c) for c in parents) + ".")
    if ancestors:
        sentences.append("It is also a kind of " + "; ".join(ref(c["object"]) + cites(c) for c in ancestors) + ".")
    if children:
        sentences.append("Its retrieved subtypes include " + "; ".join(ref(c["subject"]) + cites(c) for c in children) + ".")
    elif leaf:
        sentences.append("The retrieved evidence shows it has no subtypes.")
    if not sentences:
        return f"The retrieved evidence is insufficient to explain where {ref(x)} sits in the hierarchy."
    sentences.append("(Generated from verified evidence only.)")
    return " ".join(sentences)


def finalise(item, parsed, checked_claims, context, label_of):
    """
    S2 final output from one (possibly regenerated) answer. Returns a dict:
    answer, insufficient_evidence, claims (kept, with exact citations),
    removed_claims, citations_repaired, answer_replaced.
    """
    edge_index = context.edge_index()
    kept, removed, repaired = [], [], 0
    for claim in checked_claims:
        if claim["runtime_label"] != SUPPORTED:
            removed.append(claim)
            continue
        if claim["citation"] != "entails" and claim["relation"] in ("direct_parent", "ancestor"):
            claim = dict(claim, cites=cite_for(claim, edge_index), citation="entails", citation_repaired=True)
            repaired += 1
        kept.append(claim)

    answer, abstained = filter_answer(item, parsed.get("answer"), context)
    replaced = False
    if item.task == "T5":
        problems = find_problems(item, parsed, checked_claims)
        if removed or problems["unlisted_ids"] or not isinstance(answer, str) or not answer.strip():
            answer, replaced = template_explanation(item, kept, label_of), True
        abstained = not kept
    return {
        "answer": answer,
        "insufficient_evidence": bool(abstained),
        "claims": kept,
        "removed_claims": removed,
        "citations_repaired": repaired,
        "answer_replaced": replaced,
    }
