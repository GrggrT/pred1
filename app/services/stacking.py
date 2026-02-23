"""Stacking meta-model for combining base model predictions.

Uses only numpy for inference (no scikit-learn dependency in app/).
Training is done offline via scripts/train_stacking.py.
"""
from __future__ import annotations

import json
from decimal import Decimal
from typing import Optional

import numpy as np
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.decimalutils import D, q_prob
from app.core.logger import get_logger

log = get_logger("services.stacking")


class StackingModel:
    """Meta-model for combining base model predictions via softmax regression."""

    def __init__(
        self,
        coefficients: np.ndarray,
        intercept: np.ndarray,
        feature_names: list[str],
    ):
        """
        Args:
            coefficients: shape (3, n_features) — for 3 classes (H/D/A)
            intercept: shape (3,) — bias per class
            feature_names: ordered feature names (for mapping dict → vector)
        """
        self.coefficients = np.asarray(coefficients, dtype=np.float64)
        self.intercept = np.asarray(intercept, dtype=np.float64)
        self.feature_names = list(feature_names)

    def predict(self, features: dict[str, float]) -> tuple[Decimal, Decimal, Decimal]:
        """Predict 1X2 probabilities from feature dict.

        Args:
            features: dict with keys from feature_names. Missing keys default to 0.0.

        Returns:
            (p_home, p_draw, p_away) — Decimal, sum ≈ 1.0
        """
        x = np.array([features.get(name, 0.0) for name in self.feature_names], dtype=np.float64)

        # Linear → softmax (numerically stable)
        logits = self.coefficients @ x + self.intercept
        logits -= logits.max()
        exp_logits = np.exp(logits)
        probs = exp_logits / exp_logits.sum()

        # Clamp to [0.0001, 0.9998] and re-normalize
        eps = 0.0001
        probs = np.clip(probs, eps, 1.0 - eps)
        probs = probs / probs.sum()

        return (
            q_prob(D(str(round(probs[0], 6)))),
            q_prob(D(str(round(probs[1], 6)))),
            q_prob(D(str(round(probs[2], 6)))),
        )


async def load_stacking_model(
    session: AsyncSession,
    league_id: Optional[int] = None,
) -> Optional[StackingModel]:
    """Load trained stacking meta-model from model_params.

    Tries league-specific first, then falls back to global (league_id IS NULL).

    Returns:
        StackingModel or None if not trained.
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
                coefficients = np.array(meta["coefficients"], dtype=np.float64)
                intercept = np.array(meta["intercept"], dtype=np.float64)
                feature_names = meta["feature_names"]
                log.info(
                    "stacking_model loaded league_id=%s features=%d",
                    lid,
                    len(feature_names),
                )
                return StackingModel(coefficients, intercept, feature_names)
        except Exception as e:
            log.warning("stacking_model load failed league_id=%s: %s", lid, e)
            continue
    return None


async def save_stacking_model(
    session: AsyncSession,
    coefficients: np.ndarray,
    intercept: np.ndarray,
    feature_names: list[str],
    league_id: Optional[int] = None,
    n_samples: int = 0,
    val_rps: float = 0.0,
    val_logloss: float = 0.0,
) -> None:
    """Save stacking model coefficients to model_params (metadata JSONB).

    Uses a single row: scope='stacking', param_name='model'.
    Complex data (arrays) stored in metadata JSONB column.
    """
    meta = {
        "coefficients": coefficients.tolist(),
        "intercept": intercept.tolist(),
        "feature_names": feature_names,
        "n_samples": n_samples,
        "val_rps": val_rps,
        "val_logloss": val_logloss,
    }
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
        "stacking_model saved league_id=%s features=%d n_samples=%d val_rps=%.4f",
        league_id,
        len(feature_names),
        n_samples,
        val_rps,
    )
