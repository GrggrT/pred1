"""Tests for Dirichlet calibration (app/services/calibration.py)."""
from decimal import Decimal

import numpy as np
import pytest

from app.services.calibration import DirichletCalibrator
from app.core.decimalutils import D


def _synthetic_data(n=500, seed=42, bias=0.0):
    """Generate synthetic calibration data.

    Args:
        bias: shift applied to home_win probability (positive = model overestimates home)
    """
    rng = np.random.RandomState(seed)
    # True class distribution roughly: 45% home, 27% draw, 28% away
    true_probs = np.array([0.45, 0.27, 0.28])
    labels = rng.choice(3, size=n, p=true_probs)

    # Generate model predictions: noisy version of one-hot + prior
    probs = np.zeros((n, 3))
    for i in range(n):
        # Start with base distribution, add signal towards true class
        p = true_probs.copy() + rng.dirichlet([2, 2, 2])
        p[labels[i]] += 0.3  # signal
        p[0] += bias  # systematic bias
        p = np.clip(p, 0.01, None)
        p /= p.sum()
        probs[i] = p

    return probs, labels


class TestDirichletCalibrator:
    def test_fitted_produces_valid_W(self):
        """Fitted calibrator has a valid W matrix with positive diagonal."""
        probs, labels = _synthetic_data(n=500, bias=0.0)
        cal = DirichletCalibrator(reg_lambda=0.1)
        cal.fit(probs, labels)
        assert cal.is_fitted
        assert cal.W.shape == (3, 3)
        assert cal.b.shape == (3,)
        # Diagonal should be positive (sharpening/smoothing)
        for i in range(3):
            assert cal.W[i, i] > 0

    def test_calibrated_probs_sum_to_one(self):
        """Calibrated probabilities always sum to ~1.0."""
        probs, labels = _synthetic_data(n=300)
        cal = DirichletCalibrator(reg_lambda=0.01)
        cal.fit(probs, labels)
        calibrated = cal.calibrate(probs)
        for i in range(len(calibrated)):
            assert abs(calibrated[i].sum() - 1.0) < 1e-6

    def test_all_probs_in_zero_one(self):
        """All calibrated probabilities ∈ (0, 1)."""
        probs, labels = _synthetic_data(n=300)
        cal = DirichletCalibrator(reg_lambda=0.01)
        cal.fit(probs, labels)
        calibrated = cal.calibrate(probs)
        assert np.all(calibrated > 0)
        assert np.all(calibrated < 1)

    def test_calibration_changes_biased_predictions(self):
        """Calibrator modifies predictions on biased data."""
        probs, labels = _synthetic_data(n=500, bias=0.15)
        cal = DirichletCalibrator(reg_lambda=0.01)
        cal.fit(probs, labels)
        calibrated = cal.calibrate(probs)
        # Calibration should change the probabilities
        diff = np.abs(calibrated - probs).mean()
        assert diff > 0.001

    def test_serialization_roundtrip(self):
        """to_dict → from_dict → same calibrate() output."""
        probs, labels = _synthetic_data(n=200)
        cal = DirichletCalibrator(reg_lambda=0.05)
        cal.fit(probs, labels)

        data = cal.to_dict()
        cal2 = DirichletCalibrator.from_dict(data)

        test_input = probs[:5]
        out1 = cal.calibrate(test_input)
        out2 = cal2.calibrate(test_input)
        np.testing.assert_array_almost_equal(out1, out2)

    def test_calibrate_single_decimal(self):
        """calibrate_single returns Decimal, sum ≈ 1.0."""
        probs, labels = _synthetic_data(n=200)
        cal = DirichletCalibrator(reg_lambda=0.01)
        cal.fit(probs, labels)

        p_h, p_d, p_a = cal.calibrate_single(D("0.45"), D("0.28"), D("0.27"))
        assert isinstance(p_h, Decimal)
        assert isinstance(p_d, Decimal)
        assert isinstance(p_a, Decimal)
        assert abs(p_h + p_d + p_a - D(1)) < D("0.01")

    def test_high_regularization_near_identity(self):
        """Very high reg_lambda → W stays near identity."""
        probs, labels = _synthetic_data(n=200, bias=0.1)
        cal = DirichletCalibrator(reg_lambda=100.0, reg_mu=100.0)
        cal.fit(probs, labels)
        # High regularization keeps W close to I
        for i in range(3):
            assert abs(cal.W[i, i] - 1.0) < 0.1
        # Off-diagonal should be near 0
        for i in range(3):
            for j in range(3):
                if i != j:
                    assert abs(cal.W[i, j]) < 0.1

    def test_unfitted_calibrator_raises(self):
        """calibrate() before fit() raises RuntimeError."""
        cal = DirichletCalibrator()
        with pytest.raises(RuntimeError, match="not been fitted"):
            cal.calibrate(np.array([0.4, 0.3, 0.3]))

    def test_small_sample_uses_identity(self):
        """Fit with < 30 samples → identity (no crash)."""
        probs = np.array([[0.5, 0.3, 0.2]] * 10)
        labels = np.array([0] * 10)
        cal = DirichletCalibrator()
        cal.fit(probs, labels)  # should not crash
        assert cal.is_fitted
        # Should be near identity
        np.testing.assert_array_almost_equal(cal.W, np.eye(3))
