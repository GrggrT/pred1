"""Stacking meta-model for combining base model predictions.

Supports two model types:
- logistic: Softmax regression (numpy-only inference, no sklearn needed)
- xgboost: Gradient-boosted trees (requires xgboost library)

Training is done offline via scripts/train_stacking.py.
"""
from __future__ import annotations

import json
import os
import tempfile
from decimal import Decimal
from typing import Optional, Union

import numpy as np
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.decimalutils import D, q_prob
from app.core.logger import get_logger

log = get_logger("services.stacking")


def _to_probs_decimal(probs: np.ndarray) -> tuple[Decimal, Decimal, Decimal]:
    """Clamp, normalize, convert to Decimal triple."""
    eps = 0.0001
    probs = np.clip(probs, eps, 1.0 - eps)
    probs = probs / probs.sum()
    return (
        q_prob(D(str(round(probs[0], 6)))),
        q_prob(D(str(round(probs[1], 6)))),
        q_prob(D(str(round(probs[2], 6)))),
    )


def _apply_scaler(x: np.ndarray, scaler_mean, scaler_scale) -> np.ndarray:
    """Apply StandardScaler if available (backward-compatible)."""
    if scaler_mean is not None and scaler_scale is not None:
        return (x - scaler_mean) / np.maximum(scaler_scale, 1e-10)
    return x


class StackingModel:
    """Meta-model for combining base model predictions via softmax regression."""

    def __init__(
        self,
        coefficients: np.ndarray,
        intercept: np.ndarray,
        feature_names: list[str],
        temperature: float = 1.0,
        scaler_mean: Optional[np.ndarray] = None,
        scaler_scale: Optional[np.ndarray] = None,
    ):
        self.coefficients = np.asarray(coefficients, dtype=np.float64)
        self.intercept = np.asarray(intercept, dtype=np.float64)
        self.feature_names = list(feature_names)
        self.temperature = max(temperature, 0.01)
        self.scaler_mean = np.asarray(scaler_mean, dtype=np.float64) if scaler_mean is not None else None
        self.scaler_scale = np.asarray(scaler_scale, dtype=np.float64) if scaler_scale is not None else None

    def predict(self, features: dict[str, float]) -> tuple[Decimal, Decimal, Decimal]:
        """Predict 1X2 probabilities from feature dict."""
        x = np.array([features.get(name, 0.0) for name in self.feature_names], dtype=np.float64)
        x = _apply_scaler(x, self.scaler_mean, self.scaler_scale)

        # Linear → temperature scaling → softmax (numerically stable)
        logits = self.coefficients @ x + self.intercept
        logits = logits / self.temperature
        logits -= logits.max()
        exp_logits = np.exp(logits)
        probs = exp_logits / exp_logits.sum()

        return _to_probs_decimal(probs)


class XGBoostStackingModel:
    """Meta-model using gradient-boosted trees (XGBoost).

    Requires xgboost library at inference time.
    Model stored as JSON in model_params metadata.
    """

    def __init__(
        self,
        xgb_model_json: str,
        feature_names: list[str],
        temperature: float = 1.0,
        scaler_mean: Optional[np.ndarray] = None,
        scaler_scale: Optional[np.ndarray] = None,
    ):
        import xgboost as xgb

        self.feature_names = list(feature_names)
        self.temperature = max(temperature, 0.01)
        self.scaler_mean = np.asarray(scaler_mean, dtype=np.float64) if scaler_mean is not None else None
        self.scaler_scale = np.asarray(scaler_scale, dtype=np.float64) if scaler_scale is not None else None

        # Load booster from JSON string via temp file
        fd, path = tempfile.mkstemp(suffix=".json")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(xgb_model_json)
            self.booster = xgb.Booster()
            self.booster.load_model(path)
        finally:
            os.unlink(path)

    def predict(self, features: dict[str, float]) -> tuple[Decimal, Decimal, Decimal]:
        """Predict 1X2 probabilities from feature dict."""
        import xgboost as xgb

        x = np.array([features.get(name, 0.0) for name in self.feature_names], dtype=np.float64)
        x = _apply_scaler(x, self.scaler_mean, self.scaler_scale)

        dmatrix = xgb.DMatrix(x.reshape(1, -1), feature_names=self.feature_names)

        # Get raw margins (logits) for temperature scaling
        margins = self.booster.predict(dmatrix, output_margin=True)[0]  # shape (3,)
        logits = margins / self.temperature
        logits -= logits.max()
        exp_logits = np.exp(logits)
        probs = exp_logits / exp_logits.sum()

        return _to_probs_decimal(probs)


# Union type for both model types
AnyStackingModel = Union[StackingModel, XGBoostStackingModel]


async def load_stacking_model(
    session: AsyncSession,
    league_id: Optional[int] = None,
    temperature: float = 1.0,
) -> Optional[AnyStackingModel]:
    """Load trained stacking meta-model from model_params.

    Tries league-specific first, then falls back to global (league_id IS NULL).
    Automatically detects model type (logistic or xgboost) from metadata.
    """
    for lid in ([league_id, None] if league_id is not None else [None]):
        try:
            if lid is not None:
                res = await session.execute(
                    text(
                        "SELECT metadata FROM model_params "
                        "WHERE scope='stacking' AND param_name='model' AND league_id=:lid"
                    ),
                    {"lid": lid},
                )
            else:
                res = await session.execute(
                    text(
                        "SELECT metadata FROM model_params "
                        "WHERE scope='stacking' AND param_name='model' AND league_id IS NULL"
                    ),
                )
            row = res.first()
            if row and row.metadata:
                meta = row.metadata if isinstance(row.metadata, dict) else json.loads(row.metadata)
                feature_names = meta["feature_names"]
                scaler_mean = meta.get("scaler_mean")
                scaler_scale = meta.get("scaler_scale")
                model_type = meta.get("model_type", "logistic")

                if model_type == "xgboost":
                    try:
                        import xgboost  # noqa: F401
                    except ImportError:
                        log.warning(
                            "xgboost model found (league_id=%s) but xgboost not installed; skipping",
                            lid,
                        )
                        continue
                    log.info(
                        "stacking_model loaded league_id=%s type=xgboost features=%d temperature=%.2f",
                        lid, len(feature_names), temperature,
                    )
                    return XGBoostStackingModel(
                        meta["xgb_model_json"], feature_names,
                        temperature=temperature,
                        scaler_mean=scaler_mean,
                        scaler_scale=scaler_scale,
                    )
                else:
                    coefficients = np.array(meta["coefficients"], dtype=np.float64)
                    intercept = np.array(meta["intercept"], dtype=np.float64)
                    log.info(
                        "stacking_model loaded league_id=%s type=logistic features=%d temperature=%.2f",
                        lid, len(feature_names), temperature,
                    )
                    return StackingModel(
                        coefficients, intercept, feature_names,
                        temperature=temperature,
                        scaler_mean=scaler_mean,
                        scaler_scale=scaler_scale,
                    )
        except Exception as e:
            log.warning("stacking_model load failed league_id=%s: %s", lid, e)
            continue
    return None


async def save_stacking_model(
    session: AsyncSession,
    feature_names: list[str],
    league_id: Optional[int] = None,
    n_samples: int = 0,
    val_rps: float = 0.0,
    val_logloss: float = 0.0,
    model_type: str = "logistic",
    # Logistic-specific
    coefficients: Optional[np.ndarray] = None,
    intercept: Optional[np.ndarray] = None,
    scaler_mean: Optional[np.ndarray] = None,
    scaler_scale: Optional[np.ndarray] = None,
    # XGBoost-specific
    xgb_model_json: Optional[str] = None,
) -> None:
    """Save stacking model to model_params (metadata JSONB).

    Supports both logistic (coefficients/intercept) and xgboost (JSON model) formats.
    """
    meta = {
        "model_type": model_type,
        "feature_names": feature_names,
        "n_samples": n_samples,
        "val_rps": val_rps,
        "val_logloss": val_logloss,
    }
    if model_type == "logistic":
        meta["coefficients"] = coefficients.tolist() if coefficients is not None else []
        meta["intercept"] = intercept.tolist() if intercept is not None else []
    elif model_type == "xgboost":
        meta["xgb_model_json"] = xgb_model_json
    if scaler_mean is not None:
        meta["scaler_mean"] = scaler_mean.tolist()
    if scaler_scale is not None:
        meta["scaler_scale"] = scaler_scale.tolist()

    if league_id is not None:
        await session.execute(
            text(
                """
                INSERT INTO model_params(scope, league_id, param_name, param_value, metadata, trained_at)
                VALUES('stacking', :lid, 'model', 0, CAST(:meta AS jsonb), now())
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
                VALUES('stacking', NULL, 'model', 0, CAST(:meta AS jsonb), now())
                ON CONFLICT (scope, league_id, param_name) DO UPDATE SET
                  param_value=0, metadata=CAST(:meta AS jsonb), trained_at=now()
                """
            ),
            {"meta": json.dumps(meta)},
        )
    log.info(
        "stacking_model saved league_id=%s type=%s features=%d n_samples=%d val_rps=%.4f",
        league_id,
        model_type,
        len(feature_names),
        n_samples,
        val_rps,
    )
