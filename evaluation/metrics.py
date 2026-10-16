"""
Evaluation metrics (pure functions, no I/O).

Conventions for empty sets, fixed here so results are reproducible:
* precision = |pred ∩ gold| / |pred|; if pred is empty it is 1.0 when gold
  is also empty, else 0.0 (an empty answer to a non-empty question is wrong).
* recall    = |pred ∩ gold| / |gold|; if gold is empty it is 1.0 when pred
  is also empty, else 0.0.
* F1 is the harmonic mean, 0.0 when precision + recall == 0.
"""

import random
from collections import Counter

from explanation.explainer import (
    CONTRADICTED,
    FALSE_IN_REFERENCE,
    INSUFFICIENT,
    LABELS,
    SUPPORTED,
    TRUE_NOT_RETRIEVED,
    UNKNOWN_IDENTIFIER,
)


def set_prf(predicted, gold):
    """Precision, recall and F1 for two collections of hashable items."""
    pred, true = set(predicted), set(gold)
    hits = len(pred & true)
    if pred:
        precision = hits / len(pred)
    else:
        precision = 1.0 if not true else 0.0
    if true:
        recall = hits / len(true)
    else:
        recall = 1.0 if not pred else 0.0
    f1 = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)
    return {"precision": precision, "recall": recall, "f1": f1}


def macro_average(rows, keys=("precision", "recall", "f1")):
    """Mean of each key over a list of metric dicts (empty list -> None values)."""
    if not rows:
        return {k: None for k in keys}
    return {k: sum(r[k] for r in rows) / len(rows) for k in keys}


def accuracy(predicted, gold):
    """Share of positions where predicted == gold. Lengths must match."""
    if len(predicted) != len(gold):
        raise ValueError("predicted and gold must have the same length")
    if not gold:
        return None
    return sum(p == g for p, g in zip(predicted, gold)) / len(gold)


def path_edges(path):
    """[a, b, c] -> {(a, b), (b, c)}."""
    return {(path[i], path[i + 1]) for i in range(len(path) - 1)}


def path_edge_f1(predicted_path, gold_path):
    """Edge-level P/R/F1 between two hierarchy paths (lists of concept IDs)."""
    return set_prf(path_edges(predicted_path), path_edges(gold_path))


def exact_match(predicted, gold):
    return list(predicted) == list(gold)


def claim_label_rates(labels):
    """
    Rates over verifier labels (see explanation.explainer.LABELS).

    unsupported_rate = share of claims NOT supported by the retrieved subgraph.
    false_claim_rate = share of claims false in the reference
                       (false_in_reference + unknown_identifier).
    Rates are None when there are no claims.
    """
    counts = Counter(labels)
    unknown = set(counts) - set(LABELS)
    if unknown:
        raise ValueError(f"unknown labels: {sorted(unknown)}")
    total = sum(counts.values())
    if total == 0:
        return {"n_claims": 0, "unsupported_rate": None, "false_claim_rate": None,
                **{f"rate_{label}": None for label in LABELS}}
    rates = {f"rate_{label}": counts[label] / total for label in LABELS}
    return {
        "n_claims": total,
        "unsupported_rate": 1 - counts[SUPPORTED] / total,
        "false_claim_rate": (counts[FALSE_IN_REFERENCE] + counts[UNKNOWN_IDENTIFIER]) / total,
        **rates,
    }


def identifier_hallucination_rate(cited_ids, known_ids):
    """Share of cited concept IDs that are not in ``known_ids`` (None if none cited)."""
    cited = list(cited_ids)
    if not cited:
        return None
    known = set(known_ids)
    return sum(1 for cid in cited if cid not in known) / len(cited)


def citation_precision_recall(cited_edges, required_edges, retrieved_edges):
    """
    Edge-citation quality, adapted from ALCE-style citation metrics.

    precision: share of cited edges that exist in the retrieved evidence.
    recall:    share of required edges (those the claims depend on) that were cited.
    """
    cited, required, retrieved = set(cited_edges), set(required_edges), set(retrieved_edges)
    precision = (len(cited & retrieved) / len(cited)) if cited else None
    recall = (len(cited & required) / len(required)) if required else None
    return {"citation_precision": precision, "citation_recall": recall}


def bootstrap_ci(values, n_resamples=1000, confidence=0.95, seed=0):
    """Percentile bootstrap CI of the mean; deterministic for a given seed."""
    data = [v for v in values if v is not None]
    if not data:
        return None
    rng = random.Random(seed)
    means = sorted(
        sum(rng.choice(data) for _ in data) / len(data)
        for _ in range(n_resamples)
    )
    lower_index = int((1 - confidence) / 2 * n_resamples)
    upper_index = min(n_resamples - 1, int((1 + confidence) / 2 * n_resamples))
    return {"mean": sum(data) / len(data), "lower": means[lower_index], "upper": means[upper_index],
            "n": len(data), "seed": seed, "n_resamples": n_resamples}


# Re-exported so callers can reference labels without importing explanation.
CLAIM_LABELS = LABELS
__all__ = [
    "set_prf", "macro_average", "accuracy", "path_edges", "path_edge_f1", "exact_match",
    "claim_label_rates", "identifier_hallucination_rate", "citation_precision_recall",
    "bootstrap_ci", "CLAIM_LABELS", "SUPPORTED", "TRUE_NOT_RETRIEVED", "FALSE_IN_REFERENCE",
    "CONTRADICTED", "INSUFFICIENT", "UNKNOWN_IDENTIFIER",
]
