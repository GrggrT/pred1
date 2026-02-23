"""Утилиты для работы с букмекерскими коэффициентами."""

from __future__ import annotations

from decimal import Decimal
from typing import Tuple

from app.core.decimalutils import D, q_prob, safe_div


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
