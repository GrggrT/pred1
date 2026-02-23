"""Shared float-based math utilities for scripts and ablation.

These functions are the canonical float implementations of Poisson, Elo,
and scoring functions. Scripts (backtest, train_model, ablation_study)
should import from here instead of duplicating the logic.

Note: app/services/poisson.py has Decimal-based versions for production
(build_predictions). This module provides float-based equivalents for
offline analysis where Decimal precision is unnecessary.
"""

from __future__ import annotations

import math


# ---------------------------------------------------------------------------
# Elo
# ---------------------------------------------------------------------------

DEFAULT_ELO = 1500.0
ELO_K = 20.0


def elo_expected(rating: float, opp_rating: float) -> float:
    """Expected score for rating vs opp_rating."""
    return 1.0 / (1.0 + 10.0 ** ((opp_rating - rating) / 400.0))


# ---------------------------------------------------------------------------
# Poisson
# ---------------------------------------------------------------------------

def poisson_pmf(k: int, lam: float) -> float:
    """Poisson probability mass function P(X=k | λ)."""
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return (lam ** k) * math.exp(-lam) / math.factorial(k)


def match_probs_poisson(
    lam_h: float,
    lam_a: float,
    k_max: int = 8,
) -> tuple[float, float, float]:
    """Standard Poisson 1X2 probabilities (no tau correction), normalized."""
    p_h, p_d, p_a = 0.0, 0.0, 0.0
    for i in range(k_max + 1):
        pi = poisson_pmf(i, lam_h)
        for j in range(k_max + 1):
            pj = poisson_pmf(j, lam_a)
            prob = pi * pj
            if i > j:
                p_h += prob
            elif i == j:
                p_d += prob
            else:
                p_a += prob
    total = p_h + p_d + p_a
    if total > 0:
        p_h /= total
        p_d /= total
        p_a /= total
    return p_h, p_d, p_a


def power_scale(probs: list[float], alpha: float) -> list[float]:
    """Power-scale probabilities and renormalize."""
    eps = 1e-15
    scaled = [max(eps, p) ** alpha for p in probs]
    total = sum(scaled)
    return [s / total for s in scaled] if total > 0 else probs
