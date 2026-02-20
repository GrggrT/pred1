from decimal import Decimal

from app.jobs.build_predictions import logistic_probs_from_features


def test_logistic_probs_sum_to_one():
    p_home, p_draw, p_away = logistic_probs_from_features(Decimal("0"), Decimal("0"), Decimal("0.22"))
    total = p_home + p_draw + p_away
    assert abs(total - Decimal("1")) < Decimal("0.001")


def test_logistic_probs_react_to_elo():
    p_home_hi, _, _ = logistic_probs_from_features(Decimal("200"), Decimal("0"), Decimal("0.22"))
    p_home_lo, _, _ = logistic_probs_from_features(Decimal("-200"), Decimal("0"), Decimal("0.22"))
    assert p_home_hi > p_home_lo


def test_logistic_probs_bounds():
    p_home, p_draw, p_away = logistic_probs_from_features(Decimal("0"), Decimal("0"), Decimal("0.22"))
    for p in (p_home, p_draw, p_away):
        assert p >= 0
        assert p <= 1


def test_logistic_probs_extremes_still_normalized():
    for elo in (Decimal("-800"), Decimal("800")):
        p_home, p_draw, p_away = logistic_probs_from_features(elo, Decimal("0"), Decimal("0.22"))
        total = p_home + p_draw + p_away
        assert abs(total - Decimal("1")) < Decimal("0.001")
        assert p_draw > 0
