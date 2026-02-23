"""Tests for app/services/stacking.py — StackingModel meta-model."""
from decimal import Decimal

import numpy as np
import pytest

from app.services.stacking import StackingModel
from app.core.decimalutils import D


FEATURE_NAMES = [
    "p_home_poisson",
    "p_draw_poisson",
    "p_away_poisson",
    "p_home_dc",
    "p_draw_dc",
    "p_away_dc",
    "elo_diff",
]


def _make_model(seed=42):
    """Create a StackingModel with deterministic random coefficients."""
    rng = np.random.RandomState(seed)
    coef = rng.randn(3, len(FEATURE_NAMES))
    intercept = rng.randn(3)
    return StackingModel(coef, intercept, FEATURE_NAMES)


class TestStackingPredict:
    def test_probabilities_sum_to_one(self):
        """predict() returns probabilities that sum to ~1.0."""
        model = _make_model()
        features = {
            "p_home_poisson": 0.45,
            "p_draw_poisson": 0.28,
            "p_away_poisson": 0.27,
            "p_home_dc": 0.42,
            "p_draw_dc": 0.30,
            "p_away_dc": 0.28,
            "elo_diff": 50.0,
        }
        p_home, p_draw, p_away = model.predict(features)
        total = p_home + p_draw + p_away
        assert abs(total - D(1)) < D("0.01")

    def test_all_probs_in_zero_one(self):
        """All predicted probabilities are in (0, 1)."""
        model = _make_model()
        features = {
            "p_home_poisson": 0.45,
            "p_draw_poisson": 0.28,
            "p_away_poisson": 0.27,
            "p_home_dc": 0.42,
            "p_draw_dc": 0.30,
            "p_away_dc": 0.28,
            "elo_diff": 50.0,
        }
        p_home, p_draw, p_away = model.predict(features)
        for p in (p_home, p_draw, p_away):
            assert p > D(0)
            assert p < D(1)

    def test_base_models_agree_home_strong(self):
        """If base models agree home is strong → meta-model p_home > p_away."""
        # Use identity-like coefficients (positive weight on home features)
        coef = np.zeros((3, len(FEATURE_NAMES)))
        # Class 0 (home) gets positive weight from home probs
        coef[0, 0] = 2.0  # p_home_poisson
        coef[0, 3] = 2.0  # p_home_dc
        # Class 2 (away) gets positive weight from away probs
        coef[2, 2] = 2.0  # p_away_poisson
        coef[2, 5] = 2.0  # p_away_dc
        # Class 1 (draw) gets weight from draw probs
        coef[1, 1] = 2.0  # p_draw_poisson
        coef[1, 4] = 2.0  # p_draw_dc
        intercept = np.zeros(3)
        model = StackingModel(coef, intercept, FEATURE_NAMES)

        features = {
            "p_home_poisson": 0.70,
            "p_draw_poisson": 0.15,
            "p_away_poisson": 0.15,
            "p_home_dc": 0.65,
            "p_draw_dc": 0.18,
            "p_away_dc": 0.17,
            "elo_diff": 100.0,
        }
        p_home, p_draw, p_away = model.predict(features)
        assert p_home > p_away
        assert p_home > p_draw

    def test_missing_feature_defaults_to_zero(self):
        """Missing features in dict → default 0.0, no crash."""
        model = _make_model()
        features = {"p_home_poisson": 0.5}  # only 1 of 7 features
        p_home, p_draw, p_away = model.predict(features)
        total = p_home + p_draw + p_away
        assert abs(total - D(1)) < D("0.01")

    def test_feature_order_from_names(self):
        """Features dict is mapped by name, not insertion order."""
        model = _make_model()
        # Same values, different dict construction order
        features_a = {
            "elo_diff": 50.0,
            "p_home_poisson": 0.45,
            "p_away_dc": 0.28,
            "p_draw_poisson": 0.28,
            "p_away_poisson": 0.27,
            "p_home_dc": 0.42,
            "p_draw_dc": 0.30,
        }
        features_b = {
            "p_home_poisson": 0.45,
            "p_draw_poisson": 0.28,
            "p_away_poisson": 0.27,
            "p_home_dc": 0.42,
            "p_draw_dc": 0.30,
            "p_away_dc": 0.28,
            "elo_diff": 50.0,
        }
        result_a = model.predict(features_a)
        result_b = model.predict(features_b)
        assert result_a == result_b

    def test_returns_decimal(self):
        """predict() returns Decimal values."""
        model = _make_model()
        features = {"p_home_poisson": 0.5, "p_draw_poisson": 0.3, "p_away_poisson": 0.2}
        p_home, p_draw, p_away = model.predict(features)
        assert isinstance(p_home, Decimal)
        assert isinstance(p_draw, Decimal)
        assert isinstance(p_away, Decimal)
