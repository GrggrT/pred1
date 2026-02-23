"""Tests for app/services/kelly.py."""

from decimal import Decimal

import pytest

from app.services.kelly import kelly_fraction, kelly_stake
from app.core.decimalutils import D


class TestKellyFraction:
    def test_no_edge_returns_zero(self):
        """model_prob=0.3, odds=2.0 -> EV=0.6-1=-0.4 -> kelly=0."""
        assert kelly_fraction(D("0.3"), D("2.0")) == D("0")

    def test_positive_edge(self):
        """model_prob=0.6, odds=2.0 -> EV=0.2 -> full_kelly=0.2/1=0.2 -> quarter=0.05."""
        result = kelly_fraction(D("0.6"), D("2.0"))
        assert result == pytest.approx(D("0.05"), abs=D("0.001"))

    def test_max_fraction_cap(self):
        """Huge edge -> capped at max_fraction."""
        result = kelly_fraction(D("0.9"), D("5.0"), max_fraction=D("0.05"))
        assert result <= D("0.05")

    def test_quarter_less_than_full(self):
        """Quarter Kelly < Full Kelly."""
        full = kelly_fraction(D("0.6"), D("2.0"), fraction=D("1.0"), max_fraction=D("1.0"))
        quarter = kelly_fraction(D("0.6"), D("2.0"), fraction=D("0.25"), max_fraction=D("1.0"))
        assert quarter < full
        assert quarter == pytest.approx(full * D("0.25"), abs=D("0.001"))

    def test_odds_one_returns_zero(self):
        """Odds = 1.0 -> no profit possible -> kelly = 0."""
        assert kelly_fraction(D("0.9"), D("1.0")) == D("0")

    def test_prob_zero_returns_zero(self):
        assert kelly_fraction(D("0"), D("2.0")) == D("0")

    def test_negative_prob_returns_zero(self):
        assert kelly_fraction(D("-0.1"), D("2.0")) == D("0")

    def test_exact_breakeven_returns_zero(self):
        """model_prob=0.5, odds=2.0 -> EV=0 -> kelly=0."""
        assert kelly_fraction(D("0.5"), D("2.0")) == D("0")


class TestKellyStake:
    def test_with_bankroll(self):
        """bankroll=1000, positive edge -> meaningful stake."""
        stake = kelly_stake(D("1000"), D("0.6"), D("2.0"))
        assert stake > D("0")
        assert stake <= D("50.00")  # max 5% of 1000

    def test_tiny_edge_below_min_stake(self):
        """Tiny edge on small bankroll -> below min_stake -> return 0."""
        stake = kelly_stake(D("100"), D("0.51"), D("2.0"), min_stake=D("5.00"))
        assert stake == D("0")

    def test_rounding(self):
        """Stake should be rounded to 2 decimal places."""
        stake = kelly_stake(D("1000"), D("0.65"), D("2.5"))
        assert stake == stake.quantize(D("0.01"))
