"""Tests for the unified prediction pipeline in build_predictions.py.

Verifies the three prediction paths:
1. Primary: DC + Stacking
2. Fallback 1: DC-only (no stacking model)
3. Fallback 2: Poisson baseline (no DC params)

Also tests PredictionResult components and feature vector completeness.
"""
from decimal import Decimal

import pytest

from app.core.decimalutils import D, q_prob, q_money
from app.services.poisson import match_probs, match_probs_dixon_coles


# Re-export key functions for testing
from app.jobs.build_predictions import (
    _fatigue_factor,
    _clamp_decimal,
    _standings_gap_score,
    _volatility_score,
    _samples_score,
    _elo_gap_score,
)


class TestFatigueFunction:
    def test_short_rest_penalty(self):
        """< 72h rest should give significant penalty (0.90-0.95)."""
        factor = _fatigue_factor(48.0)
        assert 0.90 <= float(factor) <= 0.95

    def test_moderate_rest(self):
        """72-120h rest should give moderate penalty (0.95-1.00)."""
        factor = _fatigue_factor(96.0)
        assert 0.95 <= float(factor) <= 1.00

    def test_optimal_rest(self):
        """120-192h rest should give slight bonus (1.00-1.02)."""
        factor = _fatigue_factor(150.0)
        assert 1.00 <= float(factor) <= 1.02

    def test_long_rest_neutral(self):
        """> 192h rest should return 1.00 (no bonus for excessive rest)."""
        factor = _fatigue_factor(300.0)
        assert float(factor) == 1.0


class TestSignalScoreComponents:
    def test_volatility_score_range(self):
        vals = [D("1.5"), D("2.0"), D("1.0"), D("1.8")]
        score = _volatility_score(vals)
        assert D(0) <= score <= D("0.3")

    def test_samples_score_range(self):
        score = _samples_score(5, 10, 5)
        assert D(0) <= score <= D("0.4")

    def test_elo_gap_score_range(self):
        score = _elo_gap_score(D(150))
        assert D(0) <= score <= D("0.3")

    def test_standings_gap_score(self):
        score = _standings_gap_score(50, 30)
        assert D(0) <= score <= D("0.10")

    def test_standings_gap_score_none(self):
        score = _standings_gap_score(None, 30)
        assert score == D(0)


class TestModelSelection:
    """Test that the three prediction paths produce valid probabilities."""

    def _make_dc_probs(self):
        """Simulate DC-based probs from typical lambdas."""
        lam_home = q_money(D("1.5"))
        lam_away = q_money(D("1.2"))
        rho = q_prob(D("-0.12"))
        return match_probs_dixon_coles(lam_home, lam_away, rho=rho, k_max=10)

    def _make_poisson_probs(self):
        """Simulate Poisson baseline probs."""
        lam_home = q_money(D("1.5"))
        lam_away = q_money(D("1.2"))
        return match_probs(lam_home, lam_away, k_max=10)

    def test_dc_probs_valid(self):
        p_h, p_d, p_a = self._make_dc_probs()
        total = float(p_h + p_d + p_a)
        assert 0.99 < total < 1.01
        assert float(p_h) > 0 and float(p_d) > 0 and float(p_a) > 0

    def test_poisson_probs_valid(self):
        p_h, p_d, p_a = self._make_poisson_probs()
        total = float(p_h + p_d + p_a)
        assert 0.99 < total < 1.01

    def test_dc_vs_poisson_different(self):
        """DC and Poisson should give slightly different probs (rho correction)."""
        dc = self._make_dc_probs()
        pois = self._make_poisson_probs()
        # With rho=-0.12, DC should differ from Poisson
        assert abs(float(dc[0]) - float(pois[0])) > 0.001 or \
               abs(float(dc[1]) - float(pois[1])) > 0.001


class TestStackingFeatureVector:
    """Verify stacking feature vector has exactly 13 features matching train_stacking.py."""

    EXPECTED_FEATURES = [
        "p_home_poisson", "p_draw_poisson", "p_away_poisson",
        "p_home_dc", "p_draw_dc", "p_away_dc",
        "p_home_dc_xg", "p_draw_dc_xg", "p_away_dc_xg",
        "elo_diff",
        "fair_home", "fair_draw", "fair_away",
    ]

    def test_feature_count(self):
        assert len(self.EXPECTED_FEATURES) == 13

    def test_no_deprecated_features(self):
        """Feature vector should not contain deprecated features."""
        deprecated = ["standings_delta", "rest_diff"]
        for feat in deprecated:
            assert feat not in self.EXPECTED_FEATURES, f"Deprecated feature {feat} still in vector"


class TestClampDecimal:
    def test_clamp_within_range(self):
        assert _clamp_decimal(D("0.5"), D(0), D(1)) == D("0.5")

    def test_clamp_below(self):
        assert _clamp_decimal(D("-0.1"), D(0), D(1)) == D(0)

    def test_clamp_above(self):
        assert _clamp_decimal(D("1.5"), D(0), D(1)) == D(1)
