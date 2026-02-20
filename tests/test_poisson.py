from decimal import Decimal

from app.services.poisson import match_probs, poisson_pmf


def test_poisson_pmf_zero_lambda():
    assert poisson_pmf(0, Decimal("0")) == Decimal("1")
    assert poisson_pmf(3, Decimal("0")) == Decimal("0")


def test_match_probs_sum_to_one():
    p_home, p_draw, p_away = match_probs(Decimal("1.2"), Decimal("1.2"))
    total = p_home + p_draw + p_away
    assert abs(total - Decimal("1")) < Decimal("0.0001")


def test_match_probs_asymmetry():
    p_home, _, p_away = match_probs(Decimal("2.0"), Decimal("1.0"))
    assert p_home > p_away
