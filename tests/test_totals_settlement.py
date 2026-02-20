from decimal import Decimal

from app.jobs.evaluate_results import _resolve_totals, _profit


def test_resolve_totals_over_under():
    assert _resolve_totals("OVER_2_5", 2, 1) == "WIN"
    assert _resolve_totals("OVER_2_5", 1, 1) == "LOSS"
    assert _resolve_totals("UNDER_2_5", 1, 1) == "WIN"
    assert _resolve_totals("UNDER_2_5", 2, 1) == "LOSS"


def test_profit_totals():
    assert _profit("WIN", Decimal("2.5")) == Decimal("1.500")
    assert _profit("LOSS", Decimal("2.5")) == Decimal("-1.000")

