"""Tests for app/services/metrics.py — RPS, Brier, LogLoss."""
from decimal import Decimal

import pytest

from app.services.metrics import ranked_probability_score, brier_score, log_loss_score
from app.core.decimalutils import D


class TestRPS:
    def test_perfect_home(self):
        """Perfect prediction for home win → RPS = 0."""
        rps = ranked_probability_score((D(1), D(0), D(0)), outcome_index=0)
        assert rps == D(0)

    def test_perfect_draw(self):
        """Perfect prediction for draw → RPS = 0."""
        rps = ranked_probability_score((D(0), D(1), D(0)), outcome_index=1)
        assert rps == D(0)

    def test_perfect_away(self):
        """Perfect prediction for away win → RPS = 0."""
        rps = ranked_probability_score((D(0), D(0), D(1)), outcome_index=2)
        assert rps == D(0)

    def test_worst_case(self):
        """Worst: predict home=1.0 but result=away → RPS = 1.0."""
        rps = ranked_probability_score((D(1), D(0), D(0)), outcome_index=2)
        assert rps == D(1)

    def test_uniform_probs(self):
        """Uniform distribution (1/3, 1/3, 1/3) → RPS between 0 and 1."""
        p = D(1) / D(3)
        for oi in range(3):
            rps = ranked_probability_score((p, p, p), oi)
            assert D(0) < rps < D(1)

    def test_rps_ordering(self):
        """Better prediction → lower RPS."""
        good = ranked_probability_score((D("0.7"), D("0.2"), D("0.1")), outcome_index=0)
        bad = ranked_probability_score((D("0.1"), D("0.2"), D("0.7")), outcome_index=0)
        assert good < bad

    def test_invalid_outcome_raises(self):
        with pytest.raises(ValueError):
            ranked_probability_score((D(1), D(0), D(0)), outcome_index=3)


class TestBrier:
    def test_perfect_prediction(self):
        assert brier_score(D(1), 1) == D(0)
        assert brier_score(D(0), 0) == D(0)

    def test_worst_prediction(self):
        assert brier_score(D(0), 1) == D(1)
        assert brier_score(D(1), 0) == D(1)

    def test_mid_range(self):
        bs = brier_score(D("0.7"), 1)
        assert abs(bs - D("0.09")) < D("0.001")


class TestLogLoss:
    def test_confident_correct(self):
        """High prob + correct → low loss."""
        ll = log_loss_score(D("0.99"), 1)
        assert ll < D("0.02")

    def test_confident_wrong(self):
        """High prob + wrong → high loss."""
        ll = log_loss_score(D("0.99"), 0)
        assert ll > D("4")

    def test_symmetric(self):
        """log_loss(0.8, 1) == log_loss(0.2, 0)."""
        a = log_loss_score(D("0.8"), 1)
        b = log_loss_score(D("0.2"), 0)
        assert abs(a - b) < D("0.0001")
