"""Quick calibration test using bookmaker 8 data (soft book, devigged)."""
import asyncio
import sys
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.db import SessionLocal, init_db
from app.services.pinnacle_calibration import PinnacleCalibrator, devig_power
import app.services.pinnacle_calibration as pc


async def run():
    await init_db()
    from sqlalchemy import text

    async with SessionLocal() as session:
        res = await session.execute(text("""
            SELECT
                p.feature_flags ->> 'p_home' AS p_home,
                p.feature_flags ->> 'p_draw' AS p_draw,
                p.feature_flags ->> 'p_away' AS p_away,
                o.home_win, o.draw, o.away_win
            FROM predictions p
            JOIN fixtures f ON f.id = p.fixture_id
            JOIN odds o ON o.fixture_id = f.id AND o.bookmaker_id = 8
            WHERE p.status IN ('WIN','LOSS')
              AND p.selection_code != 'SKIP'
              AND p.feature_flags ->> 'p_home' IS NOT NULL
              AND o.home_win IS NOT NULL AND o.draw IS NOT NULL AND o.away_win IS NOT NULL
            ORDER BY f.kickoff
        """))
        rows = res.fetchall()

    model_p, bookie_odds = [], []
    for r in rows:
        ph, pd, pa = float(r[0]), float(r[1]), float(r[2])
        t = ph + pd + pa
        if t < 0.01:
            continue
        model_p.append([ph / t, pd / t, pa / t])
        bookie_odds.append([float(r[3]), float(r[4]), float(r[5])])

    X = np.array(model_p)
    odds_arr = np.array(bookie_odds)
    Y = devig_power(odds_arr)

    n = len(X)
    split = int(n * 0.8)
    X_tr, X_val = X[:split], X[split:]
    Y_tr, Y_val = Y[:split], Y[split:]

    print(f"Total: {n}, Train: {split}, Val: {n - split}")
    print(f"Avg overround: {(1 / odds_arr).sum(axis=1).mean():.4f}")

    # Temporarily lower _MIN_SAMPLES to allow training
    old_min = pc._MIN_SAMPLES
    pc._MIN_SAMPLES = 10

    cal = PinnacleCalibrator(reg_lambda=0.01)
    cal.fit(X_tr, Y_tr)

    pc._MIN_SAMPLES = old_min

    # Evaluate
    cal_val = cal.calibrate(X_val)
    mse_cal = np.mean(np.sum((cal_val - Y_val) ** 2, axis=1))
    mse_base = np.mean(np.sum((X_val - Y_val) ** 2, axis=1))
    max_err_cal = np.max(np.abs(cal_val - Y_val))
    max_err_base = np.max(np.abs(X_val - Y_val))

    cal_err = np.abs(cal_val.mean(axis=0) - Y_val.mean(axis=0))
    base_err = np.abs(X_val.mean(axis=0) - Y_val.mean(axis=0))

    improv = (1 - mse_cal / max(mse_base, 1e-10)) * 100

    print()
    print("=== Calibration: Model -> Bookmaker 8 (devigged) ===")
    print(f"Baseline MSE:       {mse_base:.6f}")
    print(f"Calibrated MSE:     {mse_cal:.6f}  ({improv:+.1f}%)")
    print(f"Baseline max err:   {max_err_base:.4f}")
    print(f"Calibrated max err: {max_err_cal:.4f}")
    print()
    print("Per-outcome calibration error:")
    print(f"  Home  -- base: {base_err[0]:.4f}  cal: {cal_err[0]:.4f}")
    print(f"  Draw  -- base: {base_err[1]:.4f}  cal: {cal_err[1]:.4f}")
    print(f"  Away  -- base: {base_err[2]:.4f}  cal: {cal_err[2]:.4f}")
    print()
    print(f"W diagonal: [{cal.W[0,0]:.4f}, {cal.W[1,1]:.4f}, {cal.W[2,2]:.4f}]")
    print(f"W off-diag: H-D={cal.W[0,1]:.4f} H-A={cal.W[0,2]:.4f} D-H={cal.W[1,0]:.4f} D-A={cal.W[1,2]:.4f} A-H={cal.W[2,0]:.4f} A-D={cal.W[2,1]:.4f}")
    print(f"b:          [{cal.b[0]:.4f}, {cal.b[1]:.4f}, {cal.b[2]:.4f}]")

    # Sample comparison
    print()
    print("Sample (val set):")
    print(f"  {'Model H/D/A':>25s}  {'Calibrated H/D/A':>25s}  {'Bookie devig H/D/A':>25s}")
    for i in range(min(5, len(X_val))):
        m = X_val[i]
        c = cal_val[i]
        b = Y_val[i]
        print(f"  [{m[0]:.3f}, {m[1]:.3f}, {m[2]:.3f}]  [{c[0]:.3f}, {c[1]:.3f}, {c[2]:.3f}]  [{b[0]:.3f}, {b[1]:.3f}, {b[2]:.3f}]")

    # Also show: what if we DON'T calibrate and just compare model vs bookie on full set
    print()
    full_mse = np.mean(np.sum((X - Y) ** 2, axis=1))
    full_rps = np.mean(np.sum((X - Y) ** 2, axis=1))  # simplified
    print(f"Full dataset model-vs-bookie MSE: {full_mse:.6f}")
    print(f"Model mean probs:  H={X.mean(axis=0)[0]:.4f} D={X.mean(axis=0)[1]:.4f} A={X.mean(axis=0)[2]:.4f}")
    print(f"Bookie mean probs: H={Y.mean(axis=0)[0]:.4f} D={Y.mean(axis=0)[1]:.4f} A={Y.mean(axis=0)[2]:.4f}")
    print(f"Bias (model-bookie): H={X.mean(axis=0)[0]-Y.mean(axis=0)[0]:+.4f} D={X.mean(axis=0)[1]-Y.mean(axis=0)[1]:+.4f} A={X.mean(axis=0)[2]-Y.mean(axis=0)[2]:+.4f}")


asyncio.run(run())
