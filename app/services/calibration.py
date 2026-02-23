"""
Dirichlet calibration for multiclass probability vectors.

Implements the method from Kull et al. (2019):
"Beyond temperature scaling: Obtaining well-calibrated multi-class
probabilities with Dirichlet calibration"

The calibration map: log(p) → W @ log(p) + b → softmax
where W is a 3x3 matrix and b is a 3-vector.
With L2 regularization, this generalizes temperature scaling
while preventing overfitting on small calibration sets.

No external calibration packages needed — uses only numpy + scipy.
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

log = get_logger("services.calibration")

_EPS = 1e-12
_MIN_SAMPLES = 30


def _softmax(logits: np.ndarray) -> np.ndarray:
    """Numerically stable softmax (works for 1-D or 2-D arrays)."""
    if logits.ndim == 1:
        shifted = logits - logits.max()
        e = np.exp(shifted)
        return e / e.sum()
    shifted = logits - logits.max(axis=1, keepdims=True)
    e = np.exp(shifted)
    return e / e.sum(axis=1, keepdims=True)


class DirichletCalibrator:
    """Multiclass calibrator based on Dirichlet calibration.

    Core logic: log(p) → W @ log(p) + b → softmax,
    trained via L-BFGS-B on log-loss with L2 penalty.
    """

    def __init__(self, reg_lambda: float = 1e-2, reg_mu: Optional[float] = None):
        """
        Args:
            reg_lambda: L2 regularization for off-diagonal elements of W.
                        Larger → closer to temperature scaling.
            reg_mu: L2 regularization for diagonal elements of W.
                    If None — diagonals are not regularized.
        """
        self.reg_lambda = reg_lambda
        self.reg_mu = reg_mu
        self.W: Optional[np.ndarray] = None  # shape (3, 3)
        self.b: Optional[np.ndarray] = None  # shape (3,)
        self.is_fitted = False

    def fit(self, probs: np.ndarray, labels: np.ndarray) -> DirichletCalibrator:
        """Train calibrator on probability vectors and true labels.

        Args:
            probs: shape (N, 3) — uncalibrated probabilities [p_home, p_draw, p_away]
            labels: shape (N,) — outcome indices (0=home, 1=draw, 2=away)

        Returns:
            self (for chaining)
        """
        n = len(probs)
        if n < _MIN_SAMPLES:
            log.warning(
                "calibration fit: only %d samples (min %d). Using identity.",
                n,
                _MIN_SAMPLES,
            )
            self.W = np.eye(3)
            self.b = np.zeros(3)
            self.is_fitted = True
            return self

        K = 3
        log_probs = np.log(np.clip(probs, _EPS, 1.0))  # (N, 3)

        # One-hot encode labels
        Y = np.zeros((n, K))
        Y[np.arange(n), labels.astype(int)] = 1.0

        # Initial params: W = I, b = 0 → identity mapping
        w0 = np.eye(K).ravel()  # 9
        b0 = np.zeros(K)  # 3
        x0 = np.concatenate([w0, b0])  # 12

        reg_lambda = self.reg_lambda
        reg_mu = self.reg_mu

        def objective(x):
            W = x[:9].reshape(K, K)
            b = x[9:]
            logits = log_probs @ W.T + b  # (N, K)
            p_cal = _softmax(logits)
            p_cal = np.clip(p_cal, _EPS, 1.0)

            # NLL
            nll = -np.sum(Y * np.log(p_cal)) / n

            # L2 on off-diagonal
            mask_off = ~np.eye(K, dtype=bool)
            penalty = reg_lambda * np.sum(W[mask_off] ** 2)

            # L2 on diagonal (regularize towards 1)
            if reg_mu is not None:
                penalty += reg_mu * np.sum((np.diag(W) - 1.0) ** 2)

            return nll + penalty

        def gradient(x):
            W = x[:9].reshape(K, K)
            b = x[9:]
            logits = log_probs @ W.T + b  # (N, K)
            p_cal = _softmax(logits)

            # dL/dlogits = p_cal - Y (softmax + cross-entropy gradient)
            diff = (p_cal - Y) / n  # (N, K)

            # dL/dW = diff.T @ log_probs
            dW = diff.T @ log_probs  # (K, K)
            db = diff.sum(axis=0)  # (K,)

            # Regularization gradient
            mask_off = ~np.eye(K, dtype=bool)
            dW[mask_off] += 2 * reg_lambda * W[mask_off]
            if reg_mu is not None:
                diag_idx = np.arange(K)
                dW[diag_idx, diag_idx] += 2 * reg_mu * (np.diag(W) - 1.0)

            return np.concatenate([dW.ravel(), db])

        result = minimize(
            objective,
            x0,
            jac=gradient,
            method="L-BFGS-B",
            options={"maxiter": 500, "ftol": 1e-10},
        )

        self.W = result.x[:9].reshape(K, K)
        self.b = result.x[9:]
        self.is_fitted = True

        log.info(
            "calibration fitted n=%d nll=%.4f W_diag=[%.3f, %.3f, %.3f]",
            n,
            result.fun,
            self.W[0, 0],
            self.W[1, 1],
            self.W[2, 2],
        )
        return self

    def calibrate(self, probs: np.ndarray) -> np.ndarray:
        """Apply calibration to probability vectors.

        Args:
            probs: shape (N, 3) or (3,) — uncalibrated probabilities

        Returns:
            calibrated probs, same shape, each row sums to 1.0

        Raises:
            RuntimeError: if calibrator has not been fitted
        """
        if not self.is_fitted:
            raise RuntimeError("DirichletCalibrator has not been fitted. Call fit() first.")

        single = probs.ndim == 1
        if single:
            probs = probs.reshape(1, -1)

        log_probs = np.log(np.clip(probs, _EPS, 1.0))
        logits = log_probs @ self.W.T + self.b
        cal = _softmax(logits)
        cal = np.clip(cal, 1e-6, 1.0)
        cal = cal / cal.sum(axis=1, keepdims=True)

        return cal[0] if single else cal

    def calibrate_single(
        self, p_home: Decimal, p_draw: Decimal, p_away: Decimal
    ) -> tuple[Decimal, Decimal, Decimal]:
        """Calibrate a single probability triple (Decimal interface for pipeline)."""
        probs = np.array([float(p_home), float(p_draw), float(p_away)])
        cal = self.calibrate(probs)
        return (
            q_prob(D(str(round(cal[0], 6)))),
            q_prob(D(str(round(cal[1], 6)))),
            q_prob(D(str(round(cal[2], 6)))),
        )

    def to_dict(self) -> dict:
        """Serialize for storage in model_params."""
        if not self.is_fitted:
            raise RuntimeError("Cannot serialize unfitted calibrator.")
        return {
            "W": self.W.tolist(),
            "b": self.b.tolist(),
            "reg_lambda": self.reg_lambda,
            "reg_mu": self.reg_mu,
        }

    @classmethod
    def from_dict(cls, data: dict) -> DirichletCalibrator:
        """Deserialize from model_params."""
        cal = cls(
            reg_lambda=data.get("reg_lambda", 1e-2),
            reg_mu=data.get("reg_mu"),
        )
        cal.W = np.array(data["W"], dtype=np.float64)
        cal.b = np.array(data["b"], dtype=np.float64)
        cal.is_fitted = True
        return cal


async def load_calibrator(
    session: AsyncSession, league_id: Optional[int] = None
) -> Optional[DirichletCalibrator]:
    """Load trained calibrator from model_params (scope='calibration').

    Tries league-specific first, then falls back to global.
    """
    for lid in ([league_id, None] if league_id is not None else [None]):
        try:
            if lid is not None:
                res = await session.execute(
                    text(
                        "SELECT metadata FROM model_params "
                        "WHERE scope='calibration' AND param_name='model' AND league_id=:lid"
                    ),
                    {"lid": lid},
                )
            else:
                res = await session.execute(
                    text(
                        "SELECT metadata FROM model_params "
                        "WHERE scope='calibration' AND param_name='model' AND league_id IS NULL"
                    ),
                )
            row = res.first()
            if row and row.metadata:
                meta = row.metadata if isinstance(row.metadata, dict) else json.loads(row.metadata)
                cal = DirichletCalibrator.from_dict(meta)
                log.info("calibrator loaded league_id=%s", lid)
                return cal
        except Exception as e:
            log.warning("calibrator load failed league_id=%s: %s", lid, e)
            continue
    return None


async def save_calibrator(
    session: AsyncSession,
    calibrator: DirichletCalibrator,
    league_id: Optional[int] = None,
    metrics: Optional[dict] = None,
) -> None:
    """Save calibrator to model_params (scope='calibration')."""
    meta = calibrator.to_dict()
    if metrics:
        meta["metrics"] = metrics
    if league_id is not None:
        await session.execute(
            text(
                """
                INSERT INTO model_params(scope, league_id, param_name, param_value, metadata, trained_at)
                VALUES('calibration', :lid, 'model', 0, CAST(:meta AS jsonb), now())
                ON CONFLICT (scope, league_id, param_name) DO UPDATE SET
                  param_value=0, metadata=CAST(:meta AS jsonb), trained_at=now()
                """
            ),
            {"lid": league_id, "meta": json.dumps(meta)},
        )
    else:
        await session.execute(
            text(
                """
                INSERT INTO model_params(scope, league_id, param_name, param_value, metadata, trained_at)
                VALUES('calibration', NULL, 'model', 0, CAST(:meta AS jsonb), now())
                ON CONFLICT (scope, league_id, param_name) DO UPDATE SET
                  param_value=0, metadata=CAST(:meta AS jsonb), trained_at=now()
                """
            ),
            {"meta": json.dumps(meta)},
        )
    log.info("calibrator saved league_id=%s", league_id)
