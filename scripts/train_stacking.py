"""
Train stacking meta-model for 1X2 predictions.

Usage:
    python scripts/train_stacking.py [--league-id 39] [--min-samples 100] [--c 1.0]
    python scripts/train_stacking.py --from-file results/training_data.json [--dry-run]

Reads settled predictions from DB or from generated JSON file,
extracts base model probs + features,
trains LogisticRegression meta-model, saves coefficients to model_params.

Walk-forward safety: uses predictions already made BEFORE each match
(stored in feature_flags by build_predictions.py). This is Variant A (simple OOS).
--from-file uses walk-forward generated data from generate_training_data.py.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from decimal import Decimal
from pathlib import Path

import numpy as np

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.db import SessionLocal, init_db  # noqa: E402
from app.core.logger import get_logger  # noqa: E402
from app.services.metrics import ranked_probability_score, brier_score, log_loss_score  # noqa: E402
from app.core.decimalutils import D  # noqa: E402

log = get_logger("scripts.train_stacking")

# Feature names expected in feature_flags (written by build_predictions.py)
# v5: 17 features — Poisson(3) + DC-goals(3) + DC-xG(3) + ELO(1) + fair_delta + xG_momentum(2) + rest + league_pos + H2H(2)
STACKING_FEATURE_NAMES = [
    "p_home_poisson",
    "p_draw_poisson",
    "p_away_poisson",
    "p_home_dc",
    "p_draw_dc",
    "p_away_dc",
    "p_home_dc_xg",
    "p_draw_dc_xg",
    "p_away_dc_xg",
    "elo_diff",
    "fair_delta",
    "xg_momentum_home",
    "xg_momentum_away",
    "rest_advantage",
    "league_pos_delta",
    "h2h_draw_rate",
    "h2h_goals_avg",
]

# v3: 34 features — v5(17) + CMP(4) + Market(7) + Performance(4) + Context(3) — NOT USED YET
STACKING_FEATURE_NAMES_V3 = STACKING_FEATURE_NAMES + [
    # CMP-DC features (Phase 2)
    "p_home_cmp",
    "p_draw_cmp",
    "p_away_cmp",
    "cmp_nu",
    # Market features (Phase 3)
    "odds_movement_home",
    "odds_movement_draw",
    "odds_movement_away",
    "overround",
    "disagree_home",
    "disagree_draw",
    "disagree_away",
    # Performance features (Phase 3)
    "xg_overperf_home",
    "xg_overperf_away",
    "form_trend_home",
    "form_trend_away",
    # Context features (Phase 3)
    "standings_pts_diff",
    "standings_rank_diff",
    "rest_diff",
]

# Default: use v3 for new training, but support --v2 flag for compatibility
STACKING_FEATURE_NAMES = STACKING_FEATURE_NAMES_V3


async def load_training_data(session, league_id: int | None, min_samples: int,
                             min_hours_before_kickoff: float = 2.0):
    """Load settled predictions with base model probabilities from feature_flags.

    Args:
        min_hours_before_kickoff: Exclude predictions made less than this many hours
            before kickoff to prevent target leakage from near-closing odds (default: 2h).
    """
    from sqlalchemy import text

    league_filter = ""
    params: dict = {"min_hours": min_hours_before_kickoff}
    if league_id is not None:
        league_filter = "AND f.league_id = :lid"
        params["lid"] = league_id

    res = await session.execute(
        text(
            f"""
            SELECT
                p.fixture_id,
                p.feature_flags,
                p.status,
                f.home_goals,
                f.away_goals,
                f.league_id,
                f.kickoff,
                p.created_at
            FROM predictions p
            JOIN fixtures f ON f.id = p.fixture_id
            WHERE p.status IN ('WIN', 'LOSS')
              AND p.selection_code != 'SKIP'
              AND p.feature_flags IS NOT NULL
              AND f.home_goals IS NOT NULL
              AND f.away_goals IS NOT NULL
              AND (f.kickoff - p.created_at) >= make_interval(hours => :min_hours)
              {league_filter}
            ORDER BY f.kickoff ASC
            """
        ),
        params,
    )
    rows = res.fetchall()

    features_list = []
    labels_list = []
    skipped = 0

    for row in rows:
        flags = row.feature_flags if isinstance(row.feature_flags, dict) else {}

        # Check that base model probs are available
        has_poisson = "p_home_poisson" in flags

        if not has_poisson:
            skipped += 1
            continue

        # Fallback: if DC-xG is missing, use DC-goals values
        if flags.get("p_home_dc_xg") is None:
            flags["p_home_dc_xg"] = flags.get("p_home_dc")
            flags["p_draw_dc_xg"] = flags.get("p_draw_dc")
            flags["p_away_dc_xg"] = flags.get("p_away_dc")

        # Build feature vector
        fv = []
        for fname in STACKING_FEATURE_NAMES:
            val = flags.get(fname)
            if val is None:
                val = 0.0
            fv.append(float(val))
        features_list.append(fv)

        # Label: 0=home, 1=draw, 2=away
        hg, ag = int(row.home_goals), int(row.away_goals)
        if hg > ag:
            labels_list.append(0)
        elif hg == ag:
            labels_list.append(1)
        else:
            labels_list.append(2)

    log.info(
        "training_data loaded=%d skipped=%d (missing base probs)",
        len(features_list),
        skipped,
    )

    if len(features_list) < min_samples:
        log.warning(
            "insufficient samples: %d < %d (min_samples). "
            "Run build_predictions with base model probs in feature_flags first.",
            len(features_list),
            min_samples,
        )
        return None, None

    return np.array(features_list), np.array(labels_list)


def load_training_data_from_file(filepath: str, league_id: int | None, min_samples: int):
    """Load training data from JSON file generated by generate_training_data.py."""
    with open(filepath) as f:
        payload = json.load(f)

    data = payload.get("data", [])
    if league_id is not None:
        data = [d for d in data if d.get("league_id") == league_id]

    features_list = []
    labels_list = []
    skipped = 0

    for row in data:
        # Require at least Poisson probs
        if row.get("p_home_poisson") is None:
            skipped += 1
            continue

        # Fallback: if DC-xG is missing, use DC-goals values (better than 0.0)
        if row.get("p_home_dc_xg") is None:
            row["p_home_dc_xg"] = row.get("p_home_dc")
            row["p_draw_dc_xg"] = row.get("p_draw_dc")
            row["p_away_dc_xg"] = row.get("p_away_dc")

        fv = []
        for fname in STACKING_FEATURE_NAMES:
            val = row.get(fname)
            if val is None:
                val = 0.0
            fv.append(float(val))
        features_list.append(fv)
        labels_list.append(int(row["outcome"]))

    log.info(
        "from_file loaded=%d skipped=%d (missing base probs) league=%s",
        len(features_list), skipped, league_id or "all",
    )

    if len(features_list) < min_samples:
        log.warning("insufficient samples: %d < %d", len(features_list), min_samples)
        return None, None

    return np.array(features_list), np.array(labels_list)


async def save_stacking_params(
    session, model, scaler, league_id: int | None, n_train: int, metrics: dict,
    model_type: str = "logistic", xgb_model_json: str | None = None,
):
    """Save trained model + scaler to DB via stacking service."""
    from app.services.stacking import save_stacking_model

    kwargs = dict(
        feature_names=STACKING_FEATURE_NAMES,
        league_id=league_id,
        n_samples=n_train,
        val_rps=metrics.get("rps", 0.0),
        val_logloss=metrics.get("logloss", 0.0),
        model_type=model_type,
        scaler_mean=scaler.mean_ if scaler is not None else None,
        scaler_scale=scaler.scale_ if scaler is not None else None,
    )
    if model_type == "logistic":
        kwargs["coefficients"] = model.coef_
        kwargs["intercept"] = model.intercept_
    elif model_type == "xgboost":
        kwargs["xgb_model_json"] = xgb_model_json

    await save_stacking_model(session, **kwargs)
    await session.commit()


def evaluate_predictions(probs: np.ndarray, labels: np.ndarray) -> dict:
    """Compute RPS, Brier, LogLoss on validation set."""
    n = len(labels)
    if n == 0:
        return {"rps": 0.0, "brier": 0.0, "logloss": 0.0, "n": 0}

    rps_sum = 0.0
    brier_sum = 0.0
    logloss_sum = 0.0

    for i in range(n):
        p_home = D(str(round(probs[i, 0], 6)))
        p_draw = D(str(round(probs[i, 1], 6)))
        p_away = D(str(round(probs[i, 2], 6)))
        outcome_idx = int(labels[i])

        rps_sum += float(ranked_probability_score((p_home, p_draw, p_away), outcome_idx))

        # Brier: per-class average
        for cls in range(3):
            outcome = 1 if cls == outcome_idx else 0
            brier_sum += float(brier_score(D(str(round(probs[i, cls], 6))), outcome))

        # LogLoss: on predicted prob for actual class
        p_actual = max(probs[i, outcome_idx], 1e-15)
        logloss_sum += -np.log(p_actual)

    return {
        "rps": rps_sum / n,
        "brier": brier_sum / (n * 3),
        "logloss": logloss_sum / n,
        "n": n,
    }


def _train_logistic(X_train, y_train, args):
    """Train LogisticRegression model."""
    try:
        from sklearn.linear_model import LogisticRegression
    except ImportError:
        log.error("scikit-learn not installed. Run: pip install scikit-learn>=1.4.0")
        return None

    model = LogisticRegression(
        C=args.c,
        penalty="l2",
        max_iter=1000,
        solver="lbfgs",
    )
    model.fit(X_train, y_train)
    log.info("LogisticRegression trained. Classes: %s", model.classes_.tolist())
    log.info("Coefficients shape: %s", model.coef_.shape)
    return model


def _train_xgboost(X_train, y_train, X_val, y_val, args):
    """Train XGBoost model. Returns (model, model_json_string)."""
    try:
        from xgboost import XGBClassifier
    except ImportError:
        log.error("xgboost not installed. Run: pip install xgboost>=2.0.0")
        return None, None

    import os
    import tempfile

    model = XGBClassifier(
        objective="multi:softprob",
        num_class=3,
        n_estimators=args.n_estimators,
        max_depth=args.max_depth,
        learning_rate=args.learning_rate,
        min_child_weight=5,
        subsample=0.8,
        colsample_bytree=0.8,
        reg_alpha=0.1,
        reg_lambda=1.0,
        eval_metric="mlogloss",
        random_state=42,
        verbosity=0,
    )
    model.fit(
        X_train, y_train,
        eval_set=[(X_val, y_val)],
        verbose=False,
    )
    log.info(
        "XGBoost trained. n_estimators=%d max_depth=%d best_iteration=%s",
        args.n_estimators, args.max_depth,
        getattr(model, "best_iteration", "N/A"),
    )

    # Serialize booster to JSON for DB storage
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        model.get_booster().save_model(path)
        with open(path) as f:
            xgb_model_json = f.read()
    finally:
        os.unlink(path)

    log.info("XGBoost model serialized: %d chars", len(xgb_model_json))
    return model, xgb_model_json


async def main(args):
    global STACKING_FEATURE_NAMES
    if args.v2:
        STACKING_FEATURE_NAMES = STACKING_FEATURE_NAMES_V2
        log.info("Using v2 feature set (13 features)")
    else:
        STACKING_FEATURE_NAMES = STACKING_FEATURE_NAMES_V3
        log.info("Using v3 feature set (%d features)", len(STACKING_FEATURE_NAMES_V3))

    # 1. Load data
    if args.from_file:
        features, labels = load_training_data_from_file(
            args.from_file, args.league_id, args.min_samples
        )
    else:
        await init_db()
        async with SessionLocal() as session:
            features, labels = await load_training_data(session, args.league_id, args.min_samples)

    if features is None:
        log.error("Not enough data to train. Exiting.")
        return

    log.info("Dataset: %d samples, %d features", len(features), features.shape[1])

    # 2. Chronological split (NOT random!)
    split_idx = int(len(features) * 0.8)
    X_train, X_val = features[:split_idx], features[split_idx:]
    y_train, y_val = labels[:split_idx], labels[split_idx:]

    log.info("Split: train=%d val=%d", len(X_train), len(X_val))

    if len(X_train) < 30:
        log.error("Training set too small (%d). Need at least 30.", len(X_train))
        return

    # 3. Feature scaling (StandardScaler) — normalizes elo_diff, rest_advantage etc.
    try:
        from sklearn.preprocessing import StandardScaler
    except ImportError:
        log.error("scikit-learn not installed. Run: pip install scikit-learn>=1.4.0")
        return

    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_val_scaled = scaler.transform(X_val)
    log.info(
        "StandardScaler: mean range [%.3f, %.3f], scale range [%.3f, %.3f]",
        scaler.mean_.min(), scaler.mean_.max(),
        scaler.scale_.min(), scaler.scale_.max(),
    )

    model_type = getattr(args, "model_type", "logistic")
    xgb_model_json = None

    # 4. Train
    if model_type == "xgboost":
        model, xgb_model_json = _train_xgboost(X_train_scaled, y_train, X_val_scaled, y_val, args)
        if model is None:
            return
    else:
        model = _train_logistic(X_train_scaled, y_train, args)
        if model is None:
            return

    # 5. Evaluate on validation
    probs_val = model.predict_proba(X_val_scaled)
    metrics_val = evaluate_predictions(probs_val, y_val)
    log.info(
        "Validation: RPS=%.4f Brier=%.4f LogLoss=%.4f n=%d",
        metrics_val["rps"],
        metrics_val["brier"],
        metrics_val["logloss"],
        metrics_val["n"],
    )

    # Also evaluate on train for comparison
    probs_train = model.predict_proba(X_train_scaled)
    metrics_train = evaluate_predictions(probs_train, y_train)
    log.info(
        "Train:      RPS=%.4f Brier=%.4f LogLoss=%.4f n=%d",
        metrics_train["rps"],
        metrics_train["brier"],
        metrics_train["logloss"],
        metrics_train["n"],
    )

    # 6. Print feature importance
    print(f"\n=== Feature Importance ({model_type}) ===")
    if model_type == "logistic":
        for i, name in enumerate(STACKING_FEATURE_NAMES):
            coefs = model.coef_[:, i]
            print(f"  {name:25s}  H={coefs[0]:+.4f}  D={coefs[1]:+.4f}  A={coefs[2]:+.4f}")
        print(f"\nIntercept:  H={model.intercept_[0]:+.4f}  D={model.intercept_[1]:+.4f}  A={model.intercept_[2]:+.4f}")
    elif model_type == "xgboost":
        importance = model.get_booster().get_score(importance_type="gain")
        sorted_imp = sorted(importance.items(), key=lambda x: -x[1])
        for fname, gain in sorted_imp:
            print(f"  {fname:25s}  gain={gain:.2f}")
        if not sorted_imp:
            print("  (no feature importance available)")

    # 7. Save
    if args.dry_run:
        log.info("Dry run — not saving to DB.")
    else:
        if args.from_file:
            await init_db()
        async with SessionLocal() as session:
            await save_stacking_params(
                session,
                model,
                scaler,
                league_id=args.league_id,
                n_train=len(X_train),
                metrics=metrics_val,
                model_type=model_type,
                xgb_model_json=xgb_model_json,
            )
            log.info("Model saved to model_params (scope='stacking', type=%s).", model_type)

    # 8. Print summary
    print("\n=== Summary ===")
    print(f"Model type:         {model_type}")
    print(f"Training samples:   {len(X_train)}")
    print(f"Validation samples: {len(X_val)}")
    print(f"Validation RPS:     {metrics_val['rps']:.4f}")
    print(f"Validation LogLoss: {metrics_val['logloss']:.4f}")
    print(f"Validation Brier:   {metrics_val['brier']:.4f}")
    print(f"League ID:          {args.league_id or 'global'}")
    if model_type == "logistic":
        print(f"Regularization C:   {args.c}")
    elif model_type == "xgboost":
        print(f"n_estimators:       {args.n_estimators}")
        print(f"max_depth:          {args.max_depth}")
    print(f"Source:             {'file: ' + args.from_file if args.from_file else 'database'}")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Train stacking meta-model for 1X2 predictions"
    )
    parser.add_argument(
        "--from-file",
        type=str,
        default=None,
        help="Load training data from JSON file (from generate_training_data.py)",
    )
    parser.add_argument(
        "--league-id",
        type=int,
        default=None,
        help="Train per-league model (default: global across all leagues)",
    )
    parser.add_argument(
        "--min-samples",
        type=int,
        default=100,
        help="Minimum settled predictions required (default: 100)",
    )
    parser.add_argument(
        "--model-type",
        type=str,
        default="logistic",
        choices=["logistic", "xgboost"],
        help="Model type: logistic (softmax regression) or xgboost (gradient-boosted trees)",
    )
    parser.add_argument(
        "--c",
        type=float,
        default=1.0,
        help="Regularization parameter C for LogisticRegression (default: 1.0)",
    )
    # XGBoost hyperparameters
    parser.add_argument("--n-estimators", type=int, default=100,
                        help="Number of boosting rounds for XGBoost (default: 100)")
    parser.add_argument("--max-depth", type=int, default=3,
                        help="Max tree depth for XGBoost (default: 3)")
    parser.add_argument("--learning-rate", type=float, default=0.1,
                        help="Learning rate for XGBoost (default: 0.1)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Train and evaluate but don't save to DB",
    )
    parser.add_argument(
        "--v2",
        action="store_true",
        help="Use v2 feature set (13 features) instead of v5 (17 features)",
    )
    parser.add_argument(
        "--batch-leagues",
        type=str,
        default=None,
        help="Train global + per-league models in one run. Comma-separated league IDs "
             "(e.g. '39,78,140,135,61'). Requires --from-file.",
    )
    return parser.parse_args()


async def batch_train(args):
    """Train global model + per-league models in a single run."""
    if not args.from_file:
        log.error("--batch-leagues requires --from-file")
        return

    league_ids = [int(x.strip()) for x in args.batch_leagues.split(",")]
    log.info("Batch training: global + %d leagues: %s", len(league_ids), league_ids)

    # Train global model first
    print("\n" + "=" * 60)
    print("=== Training GLOBAL model ===")
    print("=" * 60)
    args_copy = argparse.Namespace(**vars(args))
    args_copy.league_id = None
    args_copy.batch_leagues = None
    await main(args_copy)

    # Train per-league models
    for lid in league_ids:
        print("\n" + "=" * 60)
        print(f"=== Training league {lid} model ===")
        print("=" * 60)
        args_copy = argparse.Namespace(**vars(args))
        args_copy.league_id = lid
        args_copy.batch_leagues = None
        await main(args_copy)


if __name__ == "__main__":
    args = parse_args()
    if args.batch_leagues:
        asyncio.run(batch_train(args))
    else:
        asyncio.run(main(args))
