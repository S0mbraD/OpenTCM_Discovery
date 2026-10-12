"""Hypergeometric over-representation and Benjamini-Hochberg FDR.

The survival function is the textbook tail P(X >= k) for a draw of n items
from a population of N with K marked items. An impossible k (larger than both
the draw and the marked set) has probability 0, not 1.
"""
from __future__ import annotations

import math


def log_hypergeom_sf(k: int, K: int, n: int, N: int) -> float:
    """Natural log of P(X >= k). Returns 0 when the probability is 1."""
    if k <= 0:
        return 0.0
    highest = min(n, K)
    if k > highest:
        return float("-inf")

    def lnfact(x: int) -> float:
        return math.lgamma(x + 1)

    total = 0.0
    for i in range(k, highest + 1):
        lp = (
            lnfact(K) + lnfact(N - K) + lnfact(n) + lnfact(N - n)
            - lnfact(i) - lnfact(K - i) - lnfact(n - i)
            - lnfact(N - K - n + i) - lnfact(N)
        )
        total += math.exp(lp)
    if total <= 0:
        return float("-inf")
    if total >= 1:
        return 0.0
    return math.log(total)


def hypergeometric_p(k: int, K: int, n: int, N: int) -> float:
    """P(X >= k), clipped to the unit interval."""
    lp = log_hypergeom_sf(k, K, n, N)
    if lp == float("-inf"):
        return 0.0
    return min(1.0, math.exp(lp))


def bh_fdr(pvals: list[float]) -> list[float]:
    """Benjamini-Hochberg adjusted p-values, same order as the input."""
    m = len(pvals)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: pvals[i])
    adj = [0.0] * m
    prev = 1.0
    for rank in range(m, 0, -1):
        idx = order[rank - 1]
        val = min(prev, pvals[idx] * m / rank)
        adj[idx] = val
        prev = val
    return adj
