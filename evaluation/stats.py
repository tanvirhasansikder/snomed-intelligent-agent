"""
Statistical tests used in the protocol (Section 8), implemented without
third-party dependencies so results are reproducible on any laptop.

* mcnemar_exact          paired binary outcomes (two-sided exact binomial)
* wilcoxon_signed_rank   paired continuous outcomes (normal approximation
                         with tie and continuity correction; zeros dropped)
* cochran_armitage       trend in proportions over ordered groups
* holm                   Holm-Bonferroni adjusted p-values
* bootstrap_ci           re-exported from evaluation.metrics
"""

import math

from evaluation.metrics import bootstrap_ci

__all__ = ["mcnemar_exact", "wilcoxon_signed_rank", "cochran_armitage", "holm", "cohen_kappa", "bootstrap_ci"]


def _normal_two_sided(z):
    return math.erfc(abs(z) / math.sqrt(2))


def mcnemar_exact(pairs):
    """
    ``pairs``: iterable of (a, b) booleans for the same items under two
    conditions. Returns b01 (a false, b true), b10, and the exact two-sided p.
    """
    b01 = b10 = 0
    for a, b in pairs:
        if bool(a) and not bool(b):
            b10 += 1
        elif bool(b) and not bool(a):
            b01 += 1
    n = b01 + b10
    if n == 0:
        return {"b01": 0, "b10": 0, "n_discordant": 0, "p": 1.0}
    k = min(b01, b10)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2 ** n)
    return {"b01": b01, "b10": b10, "n_discordant": n, "p": min(1.0, 2 * tail)}


def wilcoxon_signed_rank(pairs):
    """
    ``pairs``: iterable of (x, y) numbers; tests the median of y - x.
    Returns W+ , W-, n (non-zero differences), z and two-sided p.
    """
    diffs = [y - x for x, y in pairs if x is not None and y is not None and (y - x) != 0]
    n = len(diffs)
    if n == 0:
        return {"w_plus": 0.0, "w_minus": 0.0, "n": 0, "z": 0.0, "p": 1.0}

    order = sorted(range(n), key=lambda i: abs(diffs[i]))
    ranks = [0.0] * n
    tie_term = 0.0
    i = 0
    while i < n:
        j = i
        while j + 1 < n and abs(diffs[order[j + 1]]) == abs(diffs[order[i]]):
            j += 1
        rank = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = rank
        t = j - i + 1
        tie_term += t ** 3 - t
        i = j + 1

    w_plus = sum(r for r, d in zip(ranks, diffs) if d > 0)
    w_minus = sum(r for r, d in zip(ranks, diffs) if d < 0)
    mean = n * (n + 1) / 4
    var = n * (n + 1) * (2 * n + 1) / 24 - tie_term / 48
    if var <= 0:
        return {"w_plus": w_plus, "w_minus": w_minus, "n": n, "z": 0.0, "p": 1.0}
    stat = w_plus - mean
    z = (abs(stat) - 0.5) / math.sqrt(var)
    z = math.copysign(max(z, 0.0), stat)
    return {"w_plus": w_plus, "w_minus": w_minus, "n": n, "z": z, "p": _normal_two_sided(z)}


def cochran_armitage(successes, totals, scores=None):
    """Two-sided Cochran-Armitage test for trend over ordered groups."""
    if len(successes) != len(totals) or not totals:
        raise ValueError("successes and totals must be non-empty and the same length")
    scores = scores or list(range(len(totals)))
    n = sum(totals)
    if n == 0:
        return {"z": 0.0, "p": 1.0}
    p_bar = sum(successes) / n
    t = sum(s * (x - t_i * p_bar) for s, x, t_i in zip(scores, successes, totals))
    var = p_bar * (1 - p_bar) * (
        sum(t_i * s * s for s, t_i in zip(scores, totals)) - (sum(t_i * s for s, t_i in zip(scores, totals)) ** 2) / n
    )
    if var <= 0:
        return {"z": 0.0, "p": 1.0}
    z = t / math.sqrt(var)
    return {"z": z, "p": _normal_two_sided(z)}


def holm(pvalues):
    """Holm-Bonferroni adjusted p-values, same order as the input."""
    m = len(pvalues)
    order = sorted(range(m), key=lambda i: pvalues[i])
    adjusted = [0.0] * m
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (m - rank) * pvalues[i]))
        adjusted[i] = running
    return adjusted


def cohen_kappa(labels_a, labels_b):
    """Cohen's kappa for two annotators over the same items."""
    if len(labels_a) != len(labels_b) or not labels_a:
        raise ValueError("label lists must be non-empty and the same length")
    n = len(labels_a)
    categories = set(labels_a) | set(labels_b)
    observed = sum(a == b for a, b in zip(labels_a, labels_b)) / n
    expected = sum((labels_a.count(c) / n) * (labels_b.count(c) / n) for c in categories)
    if expected == 1:
        return 1.0
    return (observed - expected) / (1 - expected)
