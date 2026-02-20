from __future__ import annotations

from decimal import Decimal, getcontext, ROUND_HALF_UP
from typing import Union

# Configure global context for financial calculations.
getcontext().prec = 28
getcontext().rounding = ROUND_HALF_UP

NumberLike = Union[str, float, int, Decimal]


def D(value: NumberLike) -> Decimal:
    """Safe Decimal constructor using string conversion to avoid float artifacts."""
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def q_money(value: NumberLike) -> Decimal:
    return D(value).quantize(Decimal("0.001"))


def q_prob(value: NumberLike) -> Decimal:
    return D(value).quantize(Decimal("0.0001"))


def q_ev(value: NumberLike) -> Decimal:
    return D(value).quantize(Decimal("0.0001"))


def q_xg(value: NumberLike) -> Decimal:
    return D(value).quantize(Decimal("0.01"))


def safe_div(numerator: NumberLike, denominator: NumberLike, default: NumberLike = 0) -> Decimal:
    denom_dec = D(denominator)
    if denom_dec == 0:
        return D(default)
    return D(numerator) / denom_dec
