"""Scoring metrics for probability calibration: RPS, Brier, LogLoss.

All functions accept Decimal inputs and return Decimal outputs for consistency
with the rest of the codebase.
"""
from __future__ import annotations

from decimal import Decimal

from app.core.decimalutils import D


def _clamp_prob(p: Decimal, eps: str = "1e-15") -> Decimal:
    """Clamp probability to [eps, 1 - eps]."""
    eps_dec = D(eps)
    if p < eps_dec:
        return eps_dec
    if p > D(1) - eps_dec:
        return D(1) - eps_dec
    return p


def ranked_probability_score(
    probs: tuple[Decimal, Decimal, Decimal],
    outcome_index: int,
) -> Decimal:
    """
    Ranked Probability Score for ordered 1X2 outcomes.

    Args:
        probs: (p_home, p_draw, p_away) — predicted probabilities, must sum to ~1.
        outcome_index: 0 = home win, 1 = draw, 2 = away win.

    Returns:
        RPS in [0, 1]. Lower is better.

    Formula: RPS = (1 / (K-1)) * sum_{k=1}^{K-1} (cum_pred_k - cum_actual_k)^2
    For K=3: RPS = 0.5 * [(F1 - O1)^2 + (F2 - O2)^2]
    """
    if outcome_index not in (0, 1, 2):
        raise ValueError(f"outcome_index must be 0, 1, or 2, got {outcome_index}")

    p_home, p_draw, p_away = probs

    # Cumulative predicted
    cum_pred_1 = p_home
    cum_pred_2 = p_home + p_draw

    # Cumulative actual (one-hot encoded, then cumulated)
    actual = [D(0)] * 3
    actual[outcome_index] = D(1)
    cum_act_1 = actual[0]
    cum_act_2 = actual[0] + actual[1]

    rps = D("0.5") * ((cum_pred_1 - cum_act_1) ** 2 + (cum_pred_2 - cum_act_2) ** 2)
    return rps


def brier_score(prob: Decimal, outcome: int) -> Decimal:
    """Brier score: (p - y)^2. outcome: 1 = event happened, 0 = not."""
    return (prob - D(outcome)) ** 2


def log_loss_score(prob: Decimal, outcome: int, eps: str = "1e-15") -> Decimal:
    """Log-loss (binary cross-entropy). outcome: 1 = event happened, 0 = not."""
    p = _clamp_prob(prob, eps)
    return -(D(outcome) * p.ln() + D(1 - outcome) * (D(1) - p).ln())
