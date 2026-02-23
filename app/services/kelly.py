"""Kelly criterion for bet sizing."""

from __future__ import annotations

from decimal import Decimal

from app.core.decimalutils import D, safe_div


def kelly_fraction(
    model_prob: Decimal,
    odds: Decimal,
    fraction: Decimal = D("0.25"),
    max_fraction: Decimal = D("0.05"),
) -> Decimal:
    """Fractional Kelly criterion for bet sizing.

    Full Kelly: f* = (p*b - 1) / (b - 1)
    where p = model probability, b = decimal odds.

    Args:
        model_prob: Model probability of the outcome.
        odds: Decimal odds offered by bookmaker.
        fraction: Fraction of full Kelly (0.25 = quarter Kelly).
        max_fraction: Maximum bankroll fraction per bet (safety cap).

    Returns:
        Recommended bankroll fraction (0 if no edge, capped at max_fraction).
    """
    if model_prob <= D("0") or odds <= D("1"):
        return D("0")

    b = odds - D("1")  # net odds (profit per unit)
    edge = model_prob * odds - D("1")  # EV - 1

    if edge <= D("0"):
        return D("0")

    full_kelly = safe_div(edge, b)
    fractional = fraction * full_kelly

    return min(fractional, max_fraction)


def kelly_stake(
    bankroll: Decimal,
    model_prob: Decimal,
    odds: Decimal,
    fraction: Decimal = D("0.25"),
    max_fraction: Decimal = D("0.05"),
    min_stake: Decimal = D("1.00"),
) -> Decimal:
    """Compute stake in currency units.

    Args:
        bankroll: Current bankroll.
        model_prob: Model probability.
        odds: Decimal odds.
        fraction: Fraction of full Kelly.
        max_fraction: Max bankroll fraction per bet.
        min_stake: Minimum stake (below this, return 0).

    Returns:
        Stake amount (0 if Kelly < min_stake).
    """
    frac = kelly_fraction(model_prob, odds, fraction, max_fraction)
    stake = bankroll * frac

    if stake < min_stake:
        return D("0")

    return stake.quantize(D("0.01"))
