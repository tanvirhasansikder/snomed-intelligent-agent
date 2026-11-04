"""
Scoring of one model answer (protocol Section 7).

Two views are scored for every answer:

* **L**  the answer as given;
* **LV** the answer after deterministic verification: only content entailed
  by the retrieved context is kept, otherwise the item becomes an abstention.

Claim labels come from explanation.explainer.verify_claim against the
context (evidence) and the frozen reference hierarchy.
"""

from collections import Counter

from evaluation.metrics import set_prf
from evaluation.sctid import is_valid_sctid
from explanation.citations import check_citation, citation_counts
from explanation.explainer import (
    CONTRADICTED,
    FALSE_IN_REFERENCE,
    LABELS,
    NO_CHILDREN,
    NO_PARENTS,
    SUPPORTED,
    TRUE_NOT_RETRIEVED,
    UNKNOWN_IDENTIFIER,
    HierarchyClaim,
    verify_claim,
)


def _task_score(item, answer, reference, context, abstained=False):
    """Primary score. An abstention scores 0 (an empty abstention is not a correct "none")."""
    x = item.concept_id
    if item.task in ("T1", "T2"):
        if abstained:
            return {"score": 0.0, "precision": None, "recall": 0.0 if item.gold else None, "answered": False}
        prf = set_prf(answer or [], item.gold)
        return {"score": prf["f1"], "precision": prf["precision"], "recall": prf["recall"], "answered": True}
    if item.task == "T3":
        if abstained:
            return {"score": 0.0, "answered": False}
        if answer in ("yes", "no"):
            return {"score": float((answer == "yes") == item.gold), "answered": True}
        return {"score": 0.0, "answered": False}
    if item.task == "T4":
        if abstained:
            return {"score": 0.0, "edge_precision": None, "answered": False}
        chain = list(answer or [])
        steps = list(zip(chain, chain[1:]))
        valid_steps = [reference.has_edge(a, b) for a, b in steps]
        ok = (len(chain) >= 2 and chain[0] == x and chain[-1] == item.target_id and all(valid_steps))
        return {"score": float(ok), "edge_precision": (sum(valid_steps) / len(steps)) if steps else None,
                "answered": bool(chain)}
    if item.task == "T6":
        if abstained or not answer:
            return {"score": 0.0, "lca_f1": 0.0 if item.gold else None, "answered": False}
        prf = set_prf(answer, item.gold)
        return {"score": float(bool(set(answer) & set(item.gold))),  # hit: a correct LCA was given
                "lca_f1": prf["f1"], "precision": prf["precision"], "recall": prf["recall"], "answered": True}
    return {}


def _verified_answer(item, parsed, context):
    """Deterministically verified answer (LV) and whether it abstains."""
    x, answer = item.concept_id, parsed["answer"]
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


def _label_claims(claims, evidence, reference):
    labelled = []
    for claim in claims:
        try:
            hc = HierarchyClaim(claim["subject"], claim["relation"], claim["object"])
        except ValueError:
            labelled.append(dict(claim, label=UNKNOWN_IDENTIFIER))
            continue
        labelled.append(dict(claim, label=verify_claim(hc, evidence, reference)))
    return labelled


def score_answer(item, parsed, context, reference):
    """Return a flat dict of metrics for one answer."""

    evidence = context.evidence()
    claims = _label_claims(parsed["claims"], evidence, reference)
    edge_index = context.edge_index()
    for c in claims:
        c["citation"] = check_citation(c, edge_index)
    labels = Counter(c["label"] for c in claims)
    n = len(claims)

    cited = set()
    if isinstance(parsed["answer"], list):
        cited.update(parsed["answer"])
    for c in claims:
        cited.add(c["subject"])
        if c["object"]:
            cited.add(c["object"])

    misattributed = [c for c in claims if c["source"] == "evidence" and c["label"] != SUPPORTED]
    knowledge = [c for c in claims if c["source"] == "model_knowledge"]
    absence = [c for c in claims if c["relation"] in (NO_CHILDREN, NO_PARENTS)]
    false_absence = [c for c in absence if c["label"] in (FALSE_IN_REFERENCE, CONTRADICTED)]

    abstained = bool(parsed["insufficient_evidence"]) or (item.task == "T3" and parsed["answer"] == "unknown")

    row = {
        "parse_ok": parsed["parse_error"] is None,
        "parse_error": parsed["parse_error"],
        "dropped_claims": parsed.get("dropped_claims", 0),
        "dropped_citations": parsed.get("dropped_citations", 0),
        "abstained": abstained,
        "n_claims": n,
        **{f"n_{label}": labels.get(label, 0) for label in LABELS},
        "n_misattributed": len(misattributed),
        "n_model_knowledge": len(knowledge),
        "n_model_knowledge_true": sum(1 for c in knowledge if c["label"] in (SUPPORTED, TRUE_NOT_RETRIEVED)),
        "n_absence_claims": len(absence),
        "n_false_absence": len(false_absence),
        "n_cited_ids": len(cited),
        "n_invalid_sctid": sum(1 for i in cited if not is_valid_sctid(i)),
        **citation_counts(claims, SUPPORTED),
        "claims": claims,
    }

    if item.task == "T5":
        correct = labels.get(SUPPORTED, 0) + labels.get(TRUE_NOT_RETRIEVED, 0)
        gold_parents = set(reference.parents.get(item.concept_id, set()))
        stated = {c["object"] for c in claims if c["relation"] == "direct_parent" and c["subject"] == item.concept_id}
        row["score"] = (correct / n) if n else None
        row["parent_recall"] = (len(stated & gold_parents) / len(gold_parents)) if gold_parents else None
        supported = labels.get(SUPPORTED, 0)
        row["v_score"] = 1.0 if supported else None
        row["v_n_claims"] = supported
        row["v_abstained"] = supported == 0
    else:
        row.update(_task_score(item, parsed["answer"], reference, context, abstained=abstained))
        v_answer, v_abstained = _verified_answer(item, parsed, context)
        v_abstained = v_abstained or abstained
        v = _task_score(item, v_answer, reference, context, abstained=v_abstained)
        row["v_score"] = v.get("score")
        row["v_abstained"] = v_abstained
        if item.task in ("T1", "T2"):
            row["v_precision"], row["v_recall"] = v["precision"], v["recall"]

    return row
