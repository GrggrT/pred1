"""
Train Pinnacle calibration model.

Maps model probabilities → Pinnacle closing probabilities (devigged).
Requires Pinnacle odds (bookmaker_id from PINNACLE_BOOKMAKER_ID) to be
present in the odds table for settled fixtures.

Usage:
    python scripts/train_pinnacle_calibrator.py [--league-id 39] [--min-samples 50] [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import numpy as np

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.db import SessionLocal, init_db  # noqa: E402
from app.core.logger import get_logger  # noqa: E402
from app.services.pinnacle_calibration import (  # noqa: E402
    PinnacleCalibrator,
    devig_power,
    save_pinnacle_calibrator,
)

log = get_logger("scripts.train_pinnacle_calibrator")


async def load_training_pairs(session, league_id: int | None, pinnacle_bid: int):
    """Load (model_probs, pinnacle_closing_probs) pairs from settled predictions.

    Joins predictions.feature_flags (model probs) with odds table (Pinnacle odds).
    """
    from sqlalchemy import text

    league_filter = ""
    params: dict = {"pin_bid": pinnacle_bid}
    if league_id is not None:
        league_filter = "AND f.league_id = :lid"
        params["lid"] = league_id

    res = await session.execute(
        text(
            f"""
            SELECT
                p.fixture_id,
                p.feature_flags ->> 'p_home' AS p_home,
                p.feature_flags ->> 'p_draw' AS p_draw,
                p.feature_flags ->> 'p_away' AS p_away,
                pin.home_win  AS pin_home_odd,
                pin.draw      AS pin_draw_odd,
                pin.away_win  AS pin_away_odd,
                f.home_goals,
                f.away_goals
            FROM predictions p
            JOIN fixtures f ON f.id = p.fixture_id
            JOIN odds pin ON pin.fixture_id = f.id AND pin.bookmaker_id = :pin_bid
            WHERE p.status IN ('WIN', 'LOSS')
              AND p.selection_code != 'SKIP'
              AND p.feature_flags IS NOT NULL
              AND p.feature_flags ->> 'p_home' IS NOT NULL
              AND f.home_goals IS NOT NULL
              AND pin.home_win IS NOT NULL
              AND pin.draw IS NOT NULL
              AND pin.away_win IS NOT NULL
              {league_filter}
            ORDER BY f.kickoff ASC
            """
        ),
        params,
    )
    rows = res.fetchall()

    model_probs = []
    pinnacle_odds_list = []
    skipped = 0

    for row in rows:
        # Get model probabilities from feature_flags JSONB
        p_h = float(row.p_home) if row.p_home else None
        p_d = float(row.p_draw) if row.p_draw else None
        p_a = float(row.p_away) if row.p_away else None

        if p_h is None or p_d is None or p_a is None:
            skipped += 1
            continue

        # Normalize
        total = p_h + p_d + p_a
        if total < 0.01:
            skipped += 1
            continue
        p_h /= total
        p_d /= total
        p_a /= total

        pin_h = float(row.pin_home_odd)
        pin_d = float(row.pin_draw_odd)
        pin_a = float(row.pin_away_odd)

        if pin_h < 1.01 or pin_d < 1.01 or pin_a < 1.01:
            skipped += 1
            continue

        model_probs.append([p_h, p_d, p_a])
        pinnacle_odds_list.append([pin_h, pin_d, pin_a])

    log.info(
        "training data loaded=%d skipped=%d league=%s",
        len(model_probs), skipped, league_id or "all",
    )

    if not model_probs:
        return None, None

    model_arr = np.array(model_probs)
    pin_odds_arr = np.array(pinnacle_odds_list)

    # Devig Pinnacle odds to get fair probabilities
    pin_probs = devig_power(pin_odds_arr)

    return model_arr, pin_probs


async def main(args):
    await init_db()

    from app.core.config import settings

    pin_bid = args.pinnacle_bid or settings.pinnacle_bookmaker_id

    async with SessionLocal() as session:
        model_probs, pin_probs = await load_training_pairs(
            session, args.league_id, pin_bid
        )

    if model_probs is None or len(model_probs) < args.min_samples:
        n = 0 if model_probs is None else len(model_probs)
        log.error(
            "Not enough data: %d pairs (min %d). "
            "Ensure Pinnacle odds (bid=%d) are synced.",
            n, args.min_samples, pin_bid,
        )
        return

    # Chronological split
    n_total = len(model_probs)
    split_idx = int(n_total * 0.8)
    X_train, X_val = model_probs[:split_idx], model_probs[split_idx:]
    y_train, y_val = pin_probs[:split_idx], pin_probs[split_idx:]

    log.info("Dataset: %d total, train=%d, val=%d", n_total, len(X_train), len(X_val))

    # Train calibrator
    calibrator = PinnacleCalibrator(reg_lambda=args.reg_lambda)
    calibrator.fit(X_train, y_train)

    # Evaluate on validation
    cal_val = calibrator.calibrate(X_val)
    mse_val = np.mean(np.sum((cal_val - y_val) ** 2, axis=1))
    max_err_val = np.max(np.abs(cal_val - y_val))

    # Baseline: uncalibrated model vs Pinnacle
    mse_base = np.mean(np.sum((X_val - y_val) ** 2, axis=1))
    max_err_base = np.max(np.abs(X_val - y_val))

    # Per-outcome calibration error
    cal_err = np.abs(cal_val.mean(axis=0) - y_val.mean(axis=0))
    base_err = np.abs(X_val.mean(axis=0) - y_val.mean(axis=0))

    print("\n=== Pinnacle Calibration Results ===")
    print(f"Training samples:   {len(X_train)}")
    print(f"Validation samples: {len(X_val)}")
    print(f"Pinnacle BID:       {pin_bid}")
    print(f"League ID:          {args.league_id or 'global'}")
    print(f"Regularization:     {args.reg_lambda}")
    print()
    print(f"Baseline MSE:       {mse_base:.6f}")
    print(f"Calibrated MSE:     {mse_val:.6f}  ({(1 - mse_val/max(mse_base, 1e-10))*100:+.1f}%)")
    print(f"Baseline max err:   {max_err_base:.4f}")
    print(f"Calibrated max err: {max_err_val:.4f}")
    print()
    print("Per-outcome calibration error:")
    print(f"  Home  — base: {base_err[0]:.4f}  cal: {cal_err[0]:.4f}")
    print(f"  Draw  — base: {base_err[1]:.4f}  cal: {cal_err[1]:.4f}")
    print(f"  Away  — base: {base_err[2]:.4f}  cal: {cal_err[2]:.4f}")
    print()
    print(f"W diagonal: [{calibrator.W[0,0]:.4f}, {calibrator.W[1,1]:.4f}, {calibrator.W[2,2]:.4f}]")
    print(f"b:          [{calibrator.b[0]:.4f}, {calibrator.b[1]:.4f}, {calibrator.b[2]:.4f}]")

    if args.dry_run:
        log.info("Dry run — not saving.")
        return

    metrics = {
        "mse_val": float(mse_val),
        "mse_base": float(mse_base),
        "max_err_val": float(max_err_val),
        "n_train": len(X_train),
        "n_val": len(X_val),
        "improvement_pct": float((1 - mse_val / max(mse_base, 1e-10)) * 100),
    }

    async with SessionLocal() as session:
        await save_pinnacle_calibrator(
            session, calibrator, league_id=args.league_id, metrics=metrics
        )
        await session.commit()

    log.info("Pinnacle calibrator saved to model_params.")
    print("\n✓ Calibrator saved. Enable with USE_PINNACLE_CALIB=true")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Train Pinnacle calibration model"
    )
    parser.add_argument("--league-id", type=int, default=None)
    parser.add_argument("--min-samples", type=int, default=50)
    parser.add_argument("--reg-lambda", type=float, default=0.01)
    parser.add_argument("--pinnacle-bid", type=int, default=None,
                        help="Pinnacle bookmaker ID (default: from config)")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(main(args))
