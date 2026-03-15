"""
Pinnacle-target calibration for multiclass probability vectors.

Instead of calibrating model probabilities against noisy match outcomes,
this calibrator uses Pinnacle closing lines (devigged) as the calibration
target.  Pinnacle closing has calibration error ~0.02% on 31,550+ matches
(Wheatcroft, 2020), making it a quasi-ground-truth for probability estimation.

Method: Dirichlet-style mapping  log(p_model) → W @ log(p_model) + b → softmax
Target: Pinnacle closing implied probabilities (devigged via power method)

Benefits:
1. Much less noisy than outcome-based calibration (1 match = 1 event)
2. Kelly criterion becomes safe when calibrated to sharp line
3. Value can be found in soft books (bet365, 1xBet) whose lines are less efficient

Usage:
    calibrator = PinnacleCalibrator()
    calibrator.fit(model_probs, pinnacle_probs)  # both shape (N, 3)
    calibrated = calibrator.calibrate(new_probs)
"""
from __future__ import annotations

import json
from decimal import Decimal
from typing import Optional

import numpy as np
from scipy.optimize import minimize
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.decimalutils import D, q_prob
from app.core.logger import get_logger

log = get_logger("services.pinnacle_calibration")

_EPS = 1e-12
_MIN_SAMPLES = 50


def _softmax(logits: np.ndarray) -> np.ndarray:
    """Numerically stable softmax."""
    if logits.ndim == 1:
        shifted = logits - logits.max()
        e = np.exp(shifted)
        return e / e.sum()
    shifted = logits - logits.max(axis=1, keepdims=True)
    e = np.exp(shifted)
    return e / e.sum(axis=1, keepdims=True)


def devig_power(odds: np.ndarray) -> np.ndarray:
    """Remove overround from 1X2 odds using the power method.

    The power method (Shin, 1993) solves for k such that
    sum( (1/odds_i)^k ) = 1, then fair_prob_i = (1/odds_i)^k.

    More accurate than multiplicative/additive methods for asymmetric markets.

    Args:
        odds: shape (N, 3) — raw odds [home, draw, away]

    Returns:
        fair_probs: shape (N, 3) — devigged probabilities, rows sum to 1
    """
    implied = 1.0 / np.clip(odds, 1.01, None)  # (N, 3)

    fair = np.empty_like(implied)
    for i in range(len(implied)):
        p = implied[i]
        total = p.sum()
        if total <= 1.0:
            # No overround — use as-is
            fair[i] = p / p.sum()
            continue

        # Binary search for power k
        lo, hi = 0.5, 2.0
        for _ in range(50):
            k = (lo + hi) / 2.0
            if np.sum(p ** k) > 1.0:
                hi = k
            else:
                lo = k

        k = (lo + hi) / 2.0
        devigged = p ** k
        fair[i] = devigged / devigged.sum()

    return fair


class PinnacleCalibrator:
    """Calibrates model probabilities towards Pinnacle closing lines.

    Maps: log(p_model) → W @ log(p_model) + b → softmax ≈ p_pinnacle
    Trained via MSE on probability space (not cross-entropy on outcomes).
    """

    def __init__(self, reg_lambda: float = 0.01):
        self.reg_lambda = reg_lambda
        self.W: Optional[np.ndarray] = None  # (3, 3)
        self.b: Optional[np.ndarray] = None  # (3,)
        self.is_fitted = False

    def fit(
        self,
        model_probs: np.ndarray,
        pinnacle_probs: np.ndarray,
    ) -> PinnacleCalibrator:
        """Train calibrator to map model_probs → pinnacle_probs.

        Args:
            model_probs: shape (N, 3) — our model probabilities
            pinnacle_probs: shape (N, 3) — Pinnacle closing (devigged)

        Returns:
            self
        """
        n = len(model_probs)
        if n < _MIN_SAMPLES:
            log.warning(
                "pinnacle_calibration: only %d samples (min %d). Identity.",
                n, _MIN_SAMPLES,
            )
            self.W = np.eye(3)
            self.b = np.zeros(3)
            self.is_fitted = True
            return self

        K = 3
        log_p = np.log(np.clip(model_probs, _EPS, 1.0))  # (N, 3)
        target = np.clip(pinnacle_probs, _EPS, 1.0)       # (N, 3)

        # Initial params: W = I, b = 0
        x0 = np.concatenate([np.eye(K).ravel(), np.zeros(K)])  # 12 params

        reg = self.reg_lambda

        def objective(x):
            W = x[:9].reshape(K, K)
            b = x[9:]
            logits = log_p @ W.T + b  # (N, K)
            p_cal = _softmax(logits)   # (N, K)

            # MSE loss against Pinnacle probs (more appropriate than cross-entropy
            # since target is a probability vector, not a one-hot label)
            mse = np.mean(np.sum((p_cal - target) ** 2, axis=1))

            # Regularization (keep W close to identity)
            mask_off = ~np.eye(K, dtype=bool)
            penalty = reg * np.sum(W[mask_off] ** 2)
            penalty += reg * 0.1 * np.sum((np.diag(W) - 1.0) ** 2)

            return mse + penalty

        result = minimize(
            objective,
            x0,
            method="L-BFGS-B",
            options={"maxiter": 1000, "ftol": 1e-10},
        )

        self.W = result.x[:9].reshape(K, K)
        self.b = result.x[9:]
        self.is_fitted = True

        # Evaluate calibration quality
        logits = log_p @ self.W.T + self.b
        p_cal = _softmax(logits)
        mse = np.mean(np.sum((p_cal - target) ** 2, axis=1))
        max_err = np.max(np.abs(p_cal - target))

        log.info(
            "pinnacle_calibration fitted n=%d mse=%.6f max_err=%.4f "
            "W_diag=[%.3f, %.3f, %.3f]",
            n, mse, max_err,
            self.W[0, 0], self.W[1, 1], self.W[2, 2],
        )
        return self

    def calibrate(self, probs: np.ndarray) -> np.ndarray:
        """Apply Pinnacle calibration to probability vectors."""
        if not self.is_fitted:
            raise RuntimeError("PinnacleCalibrator not fitted.")

        single = probs.ndim == 1
        if single:
            probs = probs.reshape(1, -1)

        log_p = np.log(np.clip(probs, _EPS, 1.0))
        logits = log_p @ self.W.T + self.b
        cal = _softmax(logits)
        cal = np.clip(cal, 1e-6, 1.0)
        cal = cal / cal.sum(axis=1, keepdims=True)

        return cal[0] if single else cal

    def calibrate_single(
        self, p_home: Decimal, p_draw: Decimal, p_away: Decimal,
    ) -> tuple[Decimal, Decimal, Decimal]:
        """Decimal interface for pipeline."""
        probs = np.array([float(p_home), float(p_draw), float(p_away)])
        cal = self.calibrate(probs)
        return (
            q_prob(D(str(round(cal[0], 6)))),
            q_prob(D(str(round(cal[1], 6)))),
            q_prob(D(str(round(cal[2], 6)))),
        )

    def to_dict(self) -> dict:
        if not self.is_fitted:
            raise RuntimeError("Cannot serialize unfitted calibrator.")
        return {
            "W": self.W.tolist(),
            "b": self.b.tolist(),
            "reg_lambda": self.reg_lambda,
            "type": "pinnacle",
        }

    @classmethod
    def from_dict(cls, data: dict) -> PinnacleCalibrator:
        cal = cls(reg_lambda=data.get("reg_lambda", 0.01))
        cal.W = np.array(data["W"], dtype=np.float64)
        cal.b = np.array(data["b"], dtype=np.float64)
        cal.is_fitted = True
        return cal


async def load_pinnacle_calibrator(
    session: AsyncSession,
    league_id: Optional[int] = None,
) -> Optional[PinnacleCalibrator]:
    """Load Pinnacle calibrator from model_params."""
    for lid in ([league_id, None] if league_id is not None else [None]):
        try:
            if lid is not None:
                res = await session.execute(
                    text(
                        "SELECT metadata FROM model_params "
                        "WHERE scope='pinnacle_calibration' AND param_name='model' "
                        "AND league_id=:lid"
                    ),
                    {"lid": lid},
                )
            else:
                res = await session.execute(
                    text(
                        "SELECT metadata FROM model_params "
                        "WHERE scope='pinnacle_calibration' AND param_name='model' "
                        "AND league_id IS NULL"
                    ),
                )
            row = res.first()
            if row and row.metadata:
                meta = row.metadata if isinstance(row.metadata, dict) else json.loads(row.metadata)
                if meta.get("type") == "pinnacle":
                    cal = PinnacleCalibrator.from_dict(meta)
                    log.info("pinnacle_calibrator loaded league_id=%s", lid)
                    return cal
        except Exception as e:
            log.warning("pinnacle_calibrator load failed league_id=%s: %s", lid, e)
    return None


async def save_pinnacle_calibrator(
    session: AsyncSession,
    calibrator: PinnacleCalibrator,
    league_id: Optional[int] = None,
    metrics: Optional[dict] = None,
) -> None:
    """Save Pinnacle calibrator to model_params."""
    meta = calibrator.to_dict()
    if metrics:
        meta["metrics"] = metrics
    if league_id is not None:
        await session.execute(
            text("""
                INSERT INTO model_params(scope, league_id, param_name, param_value, metadata, trained_at)
                VALUES('pinnacle_calibration', :lid, 'model', 0, CAST(:meta AS jsonb), now())
                ON CONFLICT (scope, league_id, param_name) DO UPDATE SET
                  param_value=0, metadata=CAST(:meta AS jsonb), trained_at=now()
            """),
            {"lid": league_id, "meta": json.dumps(meta)},
        )
    else:
        await session.execute(
            text("""
                INSERT INTO model_params(scope, league_id, param_name, param_value, metadata, trained_at)
                VALUES('pinnacle_calibration', NULL, 'model', 0, CAST(:meta AS jsonb), now())
                ON CONFLICT (scope, league_id, param_name) DO UPDATE SET
                  param_value=0, metadata=CAST(:meta AS jsonb), trained_at=now()
            """),
            {"meta": json.dumps(meta)},
        )
    log.info("pinnacle_calibrator saved league_id=%s", league_id)
