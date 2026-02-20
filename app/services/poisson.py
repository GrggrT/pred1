from __future__ import annotations

import math
from decimal import Decimal
from typing import Tuple

from app.core.decimalutils import D, q_prob


def poisson_pmf(k: int, lam: Decimal) -> Decimal:
    if k < 0:
        return Decimal("0")
    lam_dec = D(lam)
    if lam_dec == 0:
        return Decimal("1") if k == 0 else Decimal("0")
    lam_dec = D(lam)
    return (lam_dec ** k) * (-lam_dec).exp() / Decimal(math.factorial(k))


def match_probs(lam_home: Decimal, lam_away: Decimal, k_max: int = 10) -> Tuple[Decimal, Decimal, Decimal]:
    lam_h = D(lam_home)
    lam_a = D(lam_away)

    p_home = Decimal("0")
    p_draw = Decimal("0")
    p_away = Decimal("0")

    for i in range(k_max + 1):
        p_i = poisson_pmf(i, lam_h)
        for j in range(k_max + 1):
            p_j = poisson_pmf(j, lam_a)
            prob = p_i * p_j
            if i > j:
                p_home += prob
            elif i == j:
                p_draw += prob
            else:
                p_away += prob

    total = p_home + p_draw + p_away
    if total > 0:
        factor = Decimal("1") / total
        p_home *= factor
        p_draw *= factor
        p_away *= factor

    return q_prob(p_home), q_prob(p_draw), q_prob(p_away)


def match_probs_dixon_coles(
    lam_home: Decimal,
    lam_away: Decimal,
    rho: Decimal = D("0.1"),
    k_max: int = 10,
) -> Tuple[Decimal, Decimal, Decimal]:
    lam_h = D(lam_home)
    lam_a = D(lam_away)
    rho = D(rho)

    p_home = Decimal("0")
    p_draw = Decimal("0")
    p_away = Decimal("0")

    for i in range(k_max + 1):
        p_i = poisson_pmf(i, lam_h)
        for j in range(k_max + 1):
            p_j = poisson_pmf(j, lam_a)
            # Canonical Dixon-Coles low-score correlation adjustment (tau).
            corr = Decimal("1")
            if i == 0 and j == 0:
                corr = Decimal("1") - (lam_h * lam_a * rho)
            elif i == 0 and j == 1:
                corr = Decimal("1") + (lam_h * rho)
            elif i == 1 and j == 0:
                corr = Decimal("1") + (lam_a * rho)
            elif i == 1 and j == 1:
                corr = Decimal("1") - rho
            if corr < 0:
                corr = Decimal("0")
            prob = p_i * p_j * corr
            if i > j:
                p_home += prob
            elif i == j:
                p_draw += prob
            else:
                p_away += prob

    total = p_home + p_draw + p_away
    if total > 0:
        factor = Decimal("1") / total
        p_home *= factor
        p_draw *= factor
        p_away *= factor

    return q_prob(p_home), q_prob(p_draw), q_prob(p_away)
