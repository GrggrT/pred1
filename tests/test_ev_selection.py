from decimal import Decimal

from app.jobs.build_predictions import _best_ev_selection


def test_best_ev_selection_picks_max_ev_not_max_prob():
    probs = {"HOME_WIN": Decimal("0.60"), "DRAW": Decimal("0.20"), "AWAY_WIN": Decimal("0.20")}
    odds = {"HOME_WIN": Decimal("1.50"), "DRAW": Decimal("6.00"), "AWAY_WIN": Decimal("5.00")}
    sel, ev, odd = _best_ev_selection(probs, odds, min_odd=Decimal("1.0"), max_odd=Decimal("10.0"))
    assert sel == "DRAW"
    assert ev is not None and ev > 0
    assert odd == Decimal("6.000")


def test_best_ev_selection_respects_odd_limits():
    probs = {"HOME_WIN": Decimal("0.40"), "DRAW": Decimal("0.30"), "AWAY_WIN": Decimal("0.30")}
    odds = {"HOME_WIN": Decimal("1.20"), "DRAW": Decimal("1.90"), "AWAY_WIN": Decimal("100.00")}
    sel, ev, odd = _best_ev_selection(probs, odds, min_odd=Decimal("1.50"), max_odd=Decimal("3.20"))
    assert sel == "DRAW"
    assert ev is not None
    assert odd == Decimal("1.900")

