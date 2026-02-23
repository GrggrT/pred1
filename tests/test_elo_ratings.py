"""Tests for app/services/elo_ratings.py — Elo improvements (home advantage, goal-diff, regression)."""
from decimal import Decimal

from app.services.elo_ratings import (
    _expected_score,
    _goal_diff_multiplier,
    _result_from_score,
    _detect_season_change,
    DEFAULT_RATING,
)
from app.core.decimalutils import D


def test_expected_score_equal_ratings_no_ha():
    """Equal ratings, no home advantage → expected = 0.5."""
    e = _expected_score(D(1500), D(1500), is_home=False, home_advantage=0)
    assert abs(e - D("0.5")) < D("0.001")


def test_expected_score_equal_ratings_with_ha():
    """Equal ratings, home advantage = 65 → home expected > 0.5."""
    e_home = _expected_score(D(1500), D(1500), is_home=True, home_advantage=65)
    e_away = _expected_score(D(1500), D(1500), is_home=False, home_advantage=0)
    assert e_home > D("0.5")
    assert e_away == D("0.5")
    # Home should expect about ~0.59 with 65 advantage
    assert D("0.55") < e_home < D("0.65")


def test_expected_score_stronger_team():
    """Stronger team (higher rating) gets higher expected score."""
    e_strong = _expected_score(D(1600), D(1400), is_home=False, home_advantage=0)
    e_weak = _expected_score(D(1400), D(1600), is_home=False, home_advantage=0)
    assert e_strong > D("0.5")
    assert e_weak < D("0.5")
    assert abs(e_strong + e_weak - D(1)) < D("0.001")


def test_goal_diff_multiplier_one_goal():
    """Goal diff 0 or 1 → multiplier = 1."""
    assert _goal_diff_multiplier(1, 0) == D(1)
    assert _goal_diff_multiplier(0, 0) == D(1)
    assert _goal_diff_multiplier(2, 1) == D(1)


def test_goal_diff_multiplier_large_diff():
    """Goal diff >= 2 → multiplier = ln(diff+1) > 1."""
    m2 = _goal_diff_multiplier(3, 1)  # diff=2, ln(3)≈1.099
    m4 = _goal_diff_multiplier(5, 1)  # diff=4, ln(5)≈1.609
    assert m2 > D(1)
    assert m4 > m2
    assert abs(m2 - D("1.098612")) < D("0.001")


def test_detect_season_change():
    """Gap > 45 days → season change detected."""
    from datetime import datetime
    d1 = datetime(2024, 5, 15)
    d2 = datetime(2024, 8, 10)  # 87 days later
    d3 = datetime(2024, 5, 20)  # 5 days later
    assert _detect_season_change(d1, d2) is True
    assert _detect_season_change(d1, d3) is False
    assert _detect_season_change(None, d1) is False
