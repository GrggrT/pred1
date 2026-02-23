"""
Train Dirichlet calibrator on historical predictions.

Usage:
    python scripts/train_calibrator.py [--league-id 39] [--min-samples 200] [--reg-lambda 0.01]
    python scripts/train_calibrator.py --from-file results/training_data.json --prob-source dc [--dry-run]

Loads settled predictions from DB or from generated JSON file,
trains calibrator, saves to model_params.
Reports before/after metrics (log-loss, RPS, Brier).

--prob-source: which probabilities to calibrate (dc, poisson, or stacking).
  Default: dc (uses p_home_dc/p_draw_dc/p_away_dc from the file).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from decimal import Decimal
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.db import SessionLocal, init_db  # noqa: E402
from app.core.logger import get_logger  # noqa: E402
from app.services.calibration import DirichletCalibrator, save_calibrator  # noqa: E402
from app.services.metrics import ranked_probability_score, brier_score, log_loss_score  # noqa: E402
from app.core.decimalutils import D  # noqa: E402

log = get_logger("scripts.train_calibrator")


async def load_calibration_data(session, league_id: int | None, min_samples: int):
    """Load settled predictions with 1X2 probabilities from feature_flags."""
    from sqlalchemy import text

    league_filter = ""
    params: dict = {}
    if league_id is not None:
        league_filter = "AND f.league_id = :lid"
        params["lid"] = league_id

    res = await session.execute(
        text(
            f"""
            SELECT
                p.feature_flags,
                f.home_goals,
                f.away_goals
            FROM predictions p
            JOIN fixtures f ON f.id = p.fixture_id
            WHERE p.status IN ('WIN', 'LOSS')
              AND p.selection_code != 'SKIP'
              AND p.feature_flags IS NOT NULL
              AND f.home_goals IS NOT NULL
              AND f.away_goals IS NOT NULL
              {league_filter}
            ORDER BY f.kickoff ASC
            """
        ),
        params,
    )
    rows = res.fetchall()

    probs_list = []
    labels_list = []
    skipped = 0

    for row in rows:
        flags = row.feature_flags if isinstance(row.feature_flags, dict) else {}

        # Get final model probabilities
        ph = flags.get("p_home")
        pd = flags.get("p_draw")
        pa = flags.get("p_away")

        if ph is None or pd is None or pa is None:
            skipped += 1
            continue

        ph, pd, pa = float(ph), float(pd), float(pa)
        s = ph + pd + pa
        if s <= 0:
            skipped += 1
            continue
        ph /= s
        pd /= s
        pa /= s

        probs_list.append([ph, pd, pa])

        hg, ag = int(row.home_goals), int(row.away_goals)
        if hg > ag:
            labels_list.append(0)
        elif hg == ag:
            labels_list.append(1)
        else:
            labels_list.append(2)

    log.info("calibration_data loaded=%d skipped=%d", len(probs_list), skipped)

    if len(probs_list) < min_samples:
        log.warning(
            "insufficient samples: %d < %d. "
            "Run build_predictions with p_home/p_draw/p_away in feature_flags first.",
            len(probs_list),
            min_samples,
        )
        return None, None

    return np.array(probs_list), np.array(labels_list)


PROB_SOURCE_MAP = {
    "dc": ("p_home_dc", "p_draw_dc", "p_away_dc"),
    "poisson": ("p_home_poisson", "p_draw_poisson", "p_away_poisson"),
    # "stacking" is handled specially — requires applying stacking model first
}


def _load_stacking_model_sync():
    """Load stacking model from DB (sync via psycopg2)."""
    import os
    import psycopg2
    dsn = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
    if not dsn:
        return None
    try:
        conn = psycopg2.connect(dsn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT metadata FROM model_params "
                "WHERE scope='stacking' AND param_name='model' AND league_id IS NULL"
            )
            row = cur.fetchone()
        conn.close()
        if row and row[0]:
            meta = row[0] if isinstance(row[0], dict) else json.loads(row[0])
            return {
                "coefficients": np.array(meta["coefficients"], dtype=np.float64),
                "intercept": np.array(meta["intercept"], dtype=np.float64),
                "feature_names": meta["feature_names"],
            }
    except Exception as e:
        log.warning("Failed to load stacking model: %s", e)
    return None


def _apply_stacking(row: dict, model: dict) -> tuple[float, float, float] | None:
    """Apply stacking model to a training data row."""
    x = np.array([row.get(name, 0.0) or 0.0 for name in model["feature_names"]], dtype=np.float64)
    logits = model["coefficients"] @ x + model["intercept"]
    logits -= logits.max()
    exp_logits = np.exp(logits)
    probs = exp_logits / exp_logits.sum()
    probs = np.clip(probs, 1e-4, 1.0 - 1e-4)
    probs = probs / probs.sum()
    return float(probs[0]), float(probs[1]), float(probs[2])


def load_calibration_data_from_file(
    filepath: str, league_id: int | None, min_samples: int, prob_source: str = "dc"
):
    """Load calibration data from JSON file generated by generate_training_data.py."""
    with open(filepath) as f:
        payload = json.load(f)

    data = payload.get("data", [])
    if league_id is not None:
        data = [d for d in data if d.get("league_id") == league_id]

    # Handle stacking source: apply stacking model to features
    if prob_source == "stacking":
        model = _load_stacking_model_sync()
        if model is None:
            log.error("Cannot use prob_source=stacking: model not found in DB")
            return None, None

        probs_list = []
        labels_list = []
        skipped = 0
        for row in data:
            # Need DC probs for stacking
            if row.get("p_home_dc") is None:
                skipped += 1
                continue
            ph, pd_val, pa = _apply_stacking(row, model)
            probs_list.append([ph, pd_val, pa])
            labels_list.append(int(row["outcome"]))

        log.info(
            "from_file loaded=%d skipped=%d prob_source=stacking league=%s",
            len(probs_list), skipped, league_id or "all",
        )
        if len(probs_list) < min_samples:
            log.warning("insufficient samples: %d < %d", len(probs_list), min_samples)
            return None, None
        return np.array(probs_list), np.array(labels_list)

    # Standard source (dc, poisson)
    p_keys = PROB_SOURCE_MAP.get(prob_source)
    if p_keys is None:
        raise ValueError(f"Unknown prob_source: {prob_source}. Use: dc, poisson, stacking")

    ph_key, pd_key, pa_key = p_keys

    probs_list = []
    labels_list = []
    skipped = 0

    for row in data:
        ph = row.get(ph_key)
        pd_val = row.get(pd_key)
        pa = row.get(pa_key)

        if ph is None or pd_val is None or pa is None:
            skipped += 1
            continue

        ph, pd_val, pa = float(ph), float(pd_val), float(pa)
        s = ph + pd_val + pa
        if s <= 0:
            skipped += 1
            continue
        ph /= s
        pd_val /= s
        pa /= s

        probs_list.append([ph, pd_val, pa])
        labels_list.append(int(row["outcome"]))

    log.info(
        "from_file loaded=%d skipped=%d prob_source=%s league=%s",
        len(probs_list), skipped, prob_source, league_id or "all",
    )

    if len(probs_list) < min_samples:
        log.warning("insufficient samples: %d < %d", len(probs_list), min_samples)
        return None, None

    return np.array(probs_list), np.array(labels_list)


def evaluate_probs(probs: np.ndarray, labels: np.ndarray) -> dict:
    """Compute RPS, Brier, LogLoss."""
    n = len(labels)
    if n == 0:
        return {"rps": 0.0, "brier": 0.0, "logloss": 0.0, "n": 0}

    rps_sum = 0.0
    logloss_sum = 0.0
    brier_sum = 0.0

    for i in range(n):
        p_h = D(str(round(probs[i, 0], 6)))
        p_d = D(str(round(probs[i, 1], 6)))
        p_a = D(str(round(probs[i, 2], 6)))
        oi = int(labels[i])

        rps_sum += float(ranked_probability_score((p_h, p_d, p_a), oi))
        p_actual = max(float(probs[i, oi]), 1e-15)
        logloss_sum += -np.log(p_actual)
        for cls in range(3):
            outcome = 1 if cls == oi else 0
            brier_sum += float(brier_score(D(str(round(probs[i, cls], 6))), outcome))

    return {
        "rps": rps_sum / n,
        "logloss": logloss_sum / n,
        "brier": brier_sum / (n * 3),
        "n": n,
    }


async def main(args):
    # 1. Load data
    if args.from_file:
        probs, labels = load_calibration_data_from_file(
            args.from_file, args.league_id, args.min_samples, args.prob_source
        )
    else:
        await init_db()
        async with SessionLocal() as session:
            probs, labels = await load_calibration_data(session, args.league_id, args.min_samples)

    if probs is None:
        log.error("Not enough data. Exiting.")
        return

    # Chronological split
    split_idx = int(len(probs) * 0.8)
    probs_train, probs_val = probs[:split_idx], probs[split_idx:]
    labels_train, labels_val = labels[:split_idx], labels[split_idx:]

    log.info("Split: train=%d val=%d", len(probs_train), len(probs_val))

    # Fit
    calibrator = DirichletCalibrator(reg_lambda=args.reg_lambda)
    calibrator.fit(probs_train, labels_train)

    # Evaluate before/after on validation
    metrics_before = evaluate_probs(probs_val, labels_val)
    probs_cal_val = calibrator.calibrate(probs_val)
    metrics_after = evaluate_probs(probs_cal_val, labels_val)

    # Print comparison table
    print("\n=== Calibration Results (Validation Set) ===")
    print(f"{'Metric':<10} {'Before':>10} {'After':>10} {'Δ':>10} {'Δ%':>10}")
    print("-" * 52)
    for metric in ("rps", "logloss", "brier"):
        before = metrics_before[metric]
        after = metrics_after[metric]
        delta = after - before
        delta_pct = (delta / before * 100) if before != 0 else 0
        marker = "✓" if delta < 0 else "✗"
        print(f"{metric:<10} {before:10.4f} {after:10.4f} {delta:+10.4f} {delta_pct:+9.1f}% {marker}")

    print(f"\nW diagonal: [{calibrator.W[0,0]:.4f}, {calibrator.W[1,1]:.4f}, {calibrator.W[2,2]:.4f}]")
    print(f"b:          [{calibrator.b[0]:.4f}, {calibrator.b[1]:.4f}, {calibrator.b[2]:.4f}]")

    # Check if calibration improved
    improved = metrics_after["logloss"] < metrics_before["logloss"]
    if not improved:
        log.warning("Calibration did NOT improve logloss on validation set!")
        if not args.force and not args.dry_run:
            print("\nNot saving (use --force to override).")
            return

    # Save
    if args.dry_run:
        log.info("Dry run — not saving.")
    else:
        if args.from_file:
            await init_db()
        async with SessionLocal() as session:
            await save_calibrator(
                session,
                calibrator,
                league_id=args.league_id,
                metrics={
                    "before": metrics_before,
                    "after": metrics_after,
                    "n_train": len(probs_train),
                    "n_val": len(probs_val),
                    "prob_source": args.prob_source if args.from_file else "model",
                },
            )
            await session.commit()
            log.info("Calibrator saved (scope='calibration').")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Train Dirichlet calibrator on historical predictions"
    )
    parser.add_argument(
        "--from-file",
        type=str,
        default=None,
        help="Load data from JSON file (from generate_training_data.py)",
    )
    parser.add_argument(
        "--prob-source",
        type=str,
        default="dc",
        choices=["dc", "poisson", "stacking"],
        help="Which probabilities to calibrate when using --from-file (default: dc). "
             "'stacking' applies stacking model to features first, then calibrates output.",
    )
    parser.add_argument("--league-id", type=int, default=None)
    parser.add_argument("--min-samples", type=int, default=200)
    parser.add_argument("--reg-lambda", type=float, default=0.01)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="Save even if metrics worsen")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(main(args))
