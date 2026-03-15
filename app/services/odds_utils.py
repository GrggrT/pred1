"""Утилиты для работы с букмекерскими коэффициентами."""

from __future__ import annotations

import math
from decimal import Decimal
from typing import Tuple

from app.core.decimalutils import D, q_prob, safe_div


def remove_overround_shin(
    odd_home: Decimal | None,
    odd_draw: Decimal | None,
    odd_away: Decimal | None,
    max_iter: int = 50,
    tol: float = 1e-10,
) -> Tuple[Decimal | None, Decimal | None, Decimal | None]:
    """Remove overround using Shin's method (1991, 1993).

    Accounts for non-uniform margin allocation: bookmakers typically
    apply higher margins to popular outcomes (favorites) and lower
    margins to longshots. This gives more accurate fair probabilities
    than simple normalization, especially for draws.

    The method models the market as having a fraction z of "insider"
    bettors and (1-z) of "noise" bettors, then inverts to find true
    probabilities.

    Args:
        odd_home: Bookmaker odds for home win
        odd_draw: Bookmaker odds for draw
        odd_away: Bookmaker odds for away win
        max_iter: Maximum Newton-Raphson iterations
        tol: Convergence tolerance

    Returns:
        (prob_home, prob_draw, prob_away) with sum = 1.0
    """
    if odd_home is None or odd_draw is None or odd_away is None:
        return (None, None, None)

    oh = float(odd_home)
    od = float(odd_draw)
    oa = float(odd_away)

    if oh <= 0 or od <= 0 or oa <= 0:
        raise ValueError(f"Odds must be > 0, got: home={oh}, draw={od}, away={oa}")

    # Implied probabilities
    pi = [1.0 / oh, 1.0 / od, 1.0 / oa]
    n = len(pi)
    s = sum(pi)  # overround (> 1)

    if s <= 1.0 + 1e-12:
        # No overround — return as-is (normalized)
        return (
            q_prob(D(str(round(pi[0] / s, 6)))),
            q_prob(D(str(round(pi[1] / s, 6)))),
            q_prob(D(str(round(pi[2] / s, 6)))),
        )

    # Solve for z using Newton-Raphson
    # Equation: sum_i sqrt(z^2 + 4*(1-z)*pi_i^2 / s) = 2 + z*(n-2)
    # But more practically, use the analytic formula for 3-way:
    # z = (s - 1) / (n - 1) as initial guess, then iterate
    z = (s - 1.0) / s  # initial estimate

    for _ in range(max_iter):
        # Shin's individual true probability:
        # p_i = (sqrt(z^2 + 4*(1-z)*pi_i^2/s) - z) / (2*(1-z))
        terms = []
        dterms = []  # derivatives w.r.t. z for Newton step
        for pi_i in pi:
            inner = z * z + 4.0 * (1.0 - z) * pi_i * pi_i / s
            if inner < 0:
                inner = 0.0
            sqrt_inner = math.sqrt(inner)
            p_i = (sqrt_inner - z) / (2.0 * (1.0 - z)) if (1.0 - z) > 1e-15 else pi_i / s
            terms.append(p_i)

            # Derivative of p_i w.r.t. z (for Newton-Raphson on constraint sum(p_i)=1)
            if sqrt_inner > 1e-15 and (1.0 - z) > 1e-15:
                d_inner = 2.0 * z - 4.0 * pi_i * pi_i / s
                d_sqrt = d_inner / (2.0 * sqrt_inner)
                num = sqrt_inner - z
                denom = 2.0 * (1.0 - z)
                dp = (d_sqrt - 1.0) / denom + num / (2.0 * (1.0 - z) ** 2)
                dterms.append(dp)
            else:
                dterms.append(0.0)

        f_val = sum(terms) - 1.0
        f_deriv = sum(dterms)

        if abs(f_val) < tol:
            break
        if abs(f_deriv) < 1e-15:
            break

        z -= f_val / f_deriv
        z = max(0.0, min(z, 1.0 - 1e-10))  # keep z in [0, 1)

    # Compute final Shin probabilities
    probs = []
    for pi_i in pi:
        inner = z * z + 4.0 * (1.0 - z) * pi_i * pi_i / s
        if inner < 0:
            inner = 0.0
        sqrt_inner = math.sqrt(inner)
        p_i = (sqrt_inner - z) / (2.0 * (1.0 - z)) if (1.0 - z) > 1e-15 else pi_i / s
        probs.append(max(p_i, 1e-6))

    # Renormalize (should be very close to 1 already)
    total = sum(probs)
    return (
        q_prob(D(str(round(probs[0] / total, 6)))),
        q_prob(D(str(round(probs[1] / total, 6)))),
        q_prob(D(str(round(probs[2] / total, 6)))),
    )


def remove_overround_basic(
    odd_home: Decimal | None,
    odd_draw: Decimal | None,
    odd_away: Decimal | None,
) -> Tuple[Decimal | None, Decimal | None, Decimal | None]:
    """Удаление overround через базовую нормализацию.

    Букмекерские коэффициенты содержат маржу (overround), из-за чего
    сумма implied probabilities > 1. Эта функция нормализует implied
    probabilities к сумме = 1.

    Args:
        odd_home: Коэффициент на победу хозяев
        odd_draw: Коэффициент на ничью
        odd_away: Коэффициент на победу гостей

    Returns:
        Tuple (prob_home, prob_draw, prob_away) с суммой = 1.0

    Raises:
        ValueError: Если любой коэффициент <= 0
    """
    if odd_home is None or odd_draw is None or odd_away is None:
        return (None, None, None)

    odd_h = D(odd_home)
    odd_d = D(odd_draw)
    odd_a = D(odd_away)

    if odd_h <= 0 or odd_d <= 0 or odd_a <= 0:
        raise ValueError(f"Odds must be > 0, got: home={odd_h}, draw={odd_d}, away={odd_a}")

    imp_h = safe_div(1, odd_h)
    imp_d = safe_div(1, odd_d)
    imp_a = safe_div(1, odd_a)
    total = imp_h + imp_d + imp_a

    return (
        q_prob(safe_div(imp_h, total)),
        q_prob(safe_div(imp_d, total)),
        q_prob(safe_div(imp_a, total)),
    )


def remove_overround_binary(
    odd_a: Decimal | None,
    odd_b: Decimal | None,
) -> Tuple[Decimal | None, Decimal | None]:
    """Удаление overround для бинарного рынка (например, Over/Under).

    Args:
        odd_a: Коэффициент на первый исход (например, Over)
        odd_b: Коэффициент на второй исход (например, Under)

    Returns:
        Tuple (prob_a, prob_b) с суммой = 1.0

    Raises:
        ValueError: Если любой коэффициент <= 0
    """
    if odd_a is None or odd_b is None:
        return (None, None)

    oa = D(odd_a)
    ob = D(odd_b)

    if oa <= 0 or ob <= 0:
        raise ValueError(f"Odds must be > 0, got: a={oa}, b={ob}")

    imp_a = safe_div(1, oa)
    imp_b = safe_div(1, ob)
    total = imp_a + imp_b

    return (
        q_prob(safe_div(imp_a, total)),
        q_prob(safe_div(imp_b, total)),
    )
