"""
Production monitoring report for stacking model.

Analyzes settled predictions made with prob_source='stacking'.
Run after accumulating 50+ settled predictions for meaningful analysis.

Usage:
    python scripts/production_monitor.py [--min-settled 20]
    python scripts/production_monitor.py --detailed --output results/production_report.json
"""

import asyncio
import argparse
import json
import math
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

# ── project imports ──────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.db import SessionLocal, init_db  # noqa: E402
from app.core.decimalutils import D, q_prob  # noqa: E402
from app.services.metrics import (  # noqa: E402
    ranked_probability_score,
    brier_score,
    log_loss_score,
)

# ── thresholds ───────────────────────────────────────────────────
BACKTEST_RPS = Decimal("0.196")  # expected from ablation
CALIB_GOOD = Decimal("0.03")
CALIB_OK = Decimal("0.05")
RPS_GOOD = Decimal("0.200")
RPS_OK = Decimal("0.210")


# ── data loading ─────────────────────────────────────────────────
async def load_stacking_predictions(session) -> list[dict]:
    """Load settled stacking predictions with full feature_flags."""
    from sqlalchemy import text

    from app.core.config import settings

    res = await session.execute(
        text("""
            SELECT
                p.fixture_id,
                p.selection_code,
                p.initial_odd,
                p.confidence,
                p.status,
                p.profit,
                p.feature_flags,
                f.home_goals,
                f.away_goals,
                f.league_id,
                f.kickoff,
                o.home_win AS close_home,
                o.draw AS close_draw,
                o.away_win AS close_away
            FROM predictions p
            JOIN fixtures f ON f.id = p.fixture_id
            LEFT JOIN LATERAL (
                SELECT home_win, draw, away_win
                FROM odds_snapshots os
                WHERE os.fixture_id = f.id
                  AND os.bookmaker_id = :bid
                  AND os.fetched_at < f.kickoff
                ORDER BY os.fetched_at DESC
                LIMIT 1
            ) o ON TRUE
            WHERE p.status IN ('WIN', 'LOSS')
              AND p.feature_flags IS NOT NULL
              AND p.feature_flags->>'prob_source' = 'stacking'
            ORDER BY f.kickoff
        """),
        {"bid": settings.bookmaker_id},
    )
    rows = res.fetchall()
    preds = []
    for r in rows:
        flags = r.feature_flags if isinstance(r.feature_flags, dict) else {}
        # Pick closing odd matching the selection
        sel = r.selection_code
        if sel == "HOME_WIN":
            closing = r.close_home
        elif sel == "DRAW":
            closing = r.close_draw
        else:
            closing = r.close_away
        preds.append({
            "fixture_id": r.fixture_id,
            "selection": sel,
            "odd": float(r.initial_odd) if r.initial_odd else 0.0,
            "confidence": float(r.confidence) if r.confidence else 0.0,
            "status": r.status,
            "profit": float(r.profit) if r.profit is not None else 0.0,
            "closing_odd": float(closing) if closing else None,
            "home_goals": r.home_goals,
            "away_goals": r.away_goals,
            "league_id": r.league_id,
            "kickoff": str(r.kickoff),
            "flags": flags,
        })
    return preds


async def print_prediction_status(session):
    """Print breakdown of all prediction statuses by prob_source."""
    from sqlalchemy import text

    res = await session.execute(
        text("""
            SELECT
                COALESCE(feature_flags->>'prob_source', 'unknown') as src,
                status,
                count(*)
            FROM predictions
            GROUP BY 1, 2
            ORDER BY 1, 2
        """)
    )
    print("\n  Prediction breakdown:")
    for row in res.fetchall():
        print(f"    {row[0]:20s} {row[1]:10s} {row[2]:>6d}")


# ── metrics computation ──────────────────────────────────────────
def _outcome_index(hg: int, ag: int) -> int:
    if hg > ag:
        return 0
    elif hg == ag:
        return 1
    else:
        return 2


def compute_report(predictions: list[dict]) -> dict:
    """Compute comprehensive production metrics report."""
    n = len(predictions)

    # ── A. Overall metrics ──
    rps_sum = Decimal(0)
    brier_sum = Decimal(0)
    logloss_sum = Decimal(0)
    rps_count = 0

    # ── B. Calibration bins ──
    calib_bins = defaultdict(lambda: {"count": 0, "wins": 0, "prob_sum": 0.0})

    # ── C. Per-league ──
    league_metrics = defaultdict(lambda: {
        "rps_sum": Decimal(0), "count": 0,
        "wins": 0, "profit": 0.0,
    })

    # ── D. By prob_source (sanity) ──
    source_counts = defaultdict(int)

    # ── F. Base model comparison ──
    rps_dc_sum = Decimal(0)
    rps_poisson_sum = Decimal(0)
    dc_count = 0
    poisson_count = 0

    # ── G. Financial ──
    total_profit = 0.0
    wins = 0
    kelly_eligible = 0

    for p in predictions:
        flags = p["flags"]
        hg, ag = p["home_goals"], p["away_goals"]
        if hg is None or ag is None:
            continue

        oi = _outcome_index(hg, ag)

        # Full distribution from feature_flags
        p_h = D(str(flags.get("p_home", 0)))
        p_d = D(str(flags.get("p_draw", 0)))
        p_a = D(str(flags.get("p_away", 0)))

        if p_h + p_d + p_a > 0:
            rps = ranked_probability_score((p_h, p_d, p_a), oi)
            rps_sum += rps
            rps_count += 1

            # Brier for the selected outcome
            sel_prob = D(str(p["confidence"]))
            is_win = 1 if p["status"] == "WIN" else 0
            brier_sum += brier_score(sel_prob, is_win)
            logloss_sum += log_loss_score(sel_prob, is_win)

            # Calibration bins (by confidence decile)
            prob_val = p["confidence"]
            bin_idx = min(int(prob_val * 10), 9)  # 0-9
            calib_bins[bin_idx]["count"] += 1
            calib_bins[bin_idx]["wins"] += is_win
            calib_bins[bin_idx]["prob_sum"] += prob_val

            # Per-league
            lid = p["league_id"]
            league_metrics[lid]["rps_sum"] += rps
            league_metrics[lid]["count"] += 1
            league_metrics[lid]["wins"] += is_win
            league_metrics[lid]["profit"] += p["profit"]

        # prob_source sanity
        source_counts[flags.get("prob_source", "unknown")] += 1

        # Base model comparison — DC-only RPS
        p_h_dc = D(str(flags.get("p_home_dc", 0)))
        p_d_dc = D(str(flags.get("p_draw_dc", 0)))
        p_a_dc = D(str(flags.get("p_away_dc", 0)))
        if p_h_dc + p_d_dc + p_a_dc > 0:
            rps_dc_sum += ranked_probability_score((p_h_dc, p_d_dc, p_a_dc), oi)
            dc_count += 1

        # Base model comparison — Poisson RPS
        p_h_p = D(str(flags.get("p_home_poisson", 0)))
        p_d_p = D(str(flags.get("p_draw_poisson", 0)))
        p_a_p = D(str(flags.get("p_away_poisson", 0)))
        if p_h_p + p_d_p + p_a_p > 0:
            rps_poisson_sum += ranked_probability_score((p_h_p, p_d_p, p_a_p), oi)
            poisson_count += 1

        # Financial
        total_profit += p["profit"]
        if p["status"] == "WIN":
            wins += 1

        # Kelly eligibility (would kelly_fraction > 0?)
        sel_prob = p["confidence"]
        odd = p["odd"]
        if odd > 0 and sel_prob * odd > 1.0:
            kelly_eligible += 1

    # ── Aggregate ──
    avg_rps = rps_sum / rps_count if rps_count else Decimal(0)
    avg_brier = brier_sum / rps_count if rps_count else Decimal(0)
    avg_logloss = logloss_sum / rps_count if rps_count else Decimal(0)
    avg_rps_dc = rps_dc_sum / dc_count if dc_count else Decimal(0)
    avg_rps_poisson = rps_poisson_sum / poisson_count if poisson_count else Decimal(0)
    roi = total_profit / n if n else 0.0
    win_rate = wins / n if n else 0.0

    # ── E. CLV analysis ──
    clv_values = []
    for p in predictions:
        if p["closing_odd"] and p["odd"] > 0:
            # CLV = closing_implied - opening_implied
            # Positive means we got better price than closing
            open_imp = 1.0 / p["odd"]
            close_imp = 1.0 / p["closing_odd"]
            clv = open_imp - close_imp  # positive = we bet at better price
            clv_values.append(clv)
    mean_clv = sum(clv_values) / len(clv_values) if clv_values else 0.0

    # ── Calibration error ──
    calib_errors = []
    calib_data = []
    for bin_idx in sorted(calib_bins.keys()):
        b = calib_bins[bin_idx]
        if b["count"] < 2:
            continue
        expected = b["prob_sum"] / b["count"]
        actual = b["wins"] / b["count"]
        calib_errors.append(abs(expected - actual))
        calib_data.append({
            "bin": f"{bin_idx * 10}-{(bin_idx + 1) * 10}%",
            "count": b["count"],
            "expected": round(expected, 4),
            "actual": round(actual, 4),
            "error": round(abs(expected - actual), 4),
        })
    calib_error = Decimal(str(sum(calib_errors) / len(calib_errors))) if calib_errors else Decimal("1")

    # ── Per-league ──
    league_data = {}
    for lid, m in league_metrics.items():
        league_data[lid] = {
            "count": m["count"],
            "avg_rps": float(m["rps_sum"] / m["count"]) if m["count"] else 0,
            "win_rate": m["wins"] / m["count"] if m["count"] else 0,
            "roi": m["profit"] / m["count"] if m["count"] else 0,
        }

    return {
        "n_predictions": n,
        "n_scored": rps_count,
        "avg_rps": float(avg_rps),
        "avg_brier": float(avg_brier),
        "avg_logloss": float(avg_logloss),
        "avg_rps_dc_only": float(avg_rps_dc),
        "avg_rps_poisson_only": float(avg_rps_poisson),
        "calibration_error": float(calib_error),
        "calibration_bins": calib_data,
        "mean_clv": mean_clv,
        "clv_count": len(clv_values),
        "roi": roi,
        "win_rate": win_rate,
        "kelly_eligible": kelly_eligible,
        "total_profit": total_profit,
        "source_counts": dict(source_counts),
        "per_league": league_data,
    }


# ── output ───────────────────────────────────────────────────────
def print_report(report: dict):
    n = report["n_predictions"]
    print(f"\n{'=' * 60}")
    print(f"  PRODUCTION STACKING MONITOR  ({n} settled predictions)")
    print(f"{'=' * 60}")

    print(f"\n--- A. Overall Metrics ---")
    print(f"  RPS (stacking):    {report['avg_rps']:.4f}  (backtest: {BACKTEST_RPS})")
    print(f"  RPS (DC-only):     {report['avg_rps_dc_only']:.4f}")
    print(f"  RPS (Poisson):     {report['avg_rps_poisson_only']:.4f}")
    print(f"  Brier:             {report['avg_brier']:.4f}")
    print(f"  LogLoss:           {report['avg_logloss']:.4f}")

    print(f"\n--- B. Calibration (mean error: {report['calibration_error']:.4f}) ---")
    if report["calibration_bins"]:
        print(f"  {'Bin':>10s}  {'N':>5s}  {'Expected':>8s}  {'Actual':>8s}  {'Error':>8s}")
        for b in report["calibration_bins"]:
            print(f"  {b['bin']:>10s}  {b['count']:>5d}  {b['expected']:>8.4f}  {b['actual']:>8.4f}  {b['error']:>8.4f}")
    else:
        print("  Not enough data for calibration bins.")

    print(f"\n--- C. Per-League ---")
    if report["per_league"]:
        print(f"  {'League':>8s}  {'N':>5s}  {'RPS':>8s}  {'WinRate':>8s}  {'ROI':>8s}")
        for lid, m in sorted(report["per_league"].items()):
            print(f"  {lid:>8d}  {m['count']:>5d}  {m['avg_rps']:>8.4f}  {m['win_rate']:>7.1%}  {m['roi']:>+7.1%}")

    print(f"\n--- D. Source Breakdown ---")
    for src, cnt in report["source_counts"].items():
        print(f"  {src}: {cnt}")

    print(f"\n--- E. CLV Analysis ---")
    if report["clv_count"]:
        print(f"  Mean CLV: {report['mean_clv']:+.4f} ({report['clv_count']} predictions with closing odds)")
    else:
        print("  No closing odds available for CLV analysis.")

    print(f"\n--- F. Base Model Comparison ---")
    rps_s = report["avg_rps"]
    rps_dc = report["avg_rps_dc_only"]
    rps_p = report["avg_rps_poisson_only"]
    if rps_dc > 0:
        delta_dc = (rps_s - rps_dc) / rps_dc * 100 if rps_dc else 0
        print(f"  Stacking vs DC-only:  {delta_dc:+.1f}%")
    if rps_p > 0:
        delta_p = (rps_s - rps_p) / rps_p * 100 if rps_p else 0
        print(f"  Stacking vs Poisson:  {delta_p:+.1f}%")

    print(f"\n--- G. Financial ---")
    print(f"  Win rate:       {report['win_rate']:.1%}")
    print(f"  ROI (flat):     {report['roi']:+.1%}")
    print(f"  Total profit:   {report['total_profit']:+.2f} units")
    print(f"  Kelly eligible: {report['kelly_eligible']}/{n}")


def print_recommendations(report: dict):
    print(f"\n{'=' * 60}")
    print(f"  RECOMMENDATIONS")
    print(f"{'=' * 60}")

    rps = Decimal(str(report["avg_rps"]))
    ce = Decimal(str(report["calibration_error"]))
    clv = report["mean_clv"]
    rps_dc = report["avg_rps_dc_only"]
    rps_s = report["avg_rps"]

    # Calibration
    if ce < CALIB_GOOD:
        print(f"\n  [OK] Calibration error {ce:.4f} < {CALIB_GOOD}. Kelly activation safe.")
    elif ce < CALIB_OK:
        print(f"\n  [~~] Calibration error {ce:.4f} < {CALIB_OK}. Quarter-Kelly conservative option.")
    else:
        print(f"\n  [!!] Calibration error {ce:.4f} > {CALIB_OK}. DO NOT activate Kelly. Investigate.")

    # RPS vs backtest
    if rps < RPS_GOOD:
        print(f"  [OK] Production RPS {rps:.4f} in line with backtest ({BACKTEST_RPS}). Model working.")
    elif rps < RPS_OK:
        print(f"  [~~] Production RPS {rps:.4f} slightly worse than backtest. Normal variance.")
    else:
        print(f"  [!!] Production RPS {rps:.4f} significantly worse than backtest. Investigate.")

    # CLV
    if report["clv_count"] > 5:
        if clv > 0:
            print(f"  [OK] Positive CLV ({clv:+.4f}). Model adds value over market.")
        else:
            print(f"  [~~] Negative CLV ({clv:+.4f}). Model may not have real edge.")
    else:
        print(f"  [--] Not enough closing odds for CLV analysis.")

    # Stacking vs base models
    if rps_dc > 0 and rps_s < rps_dc:
        print(f"  [OK] Stacking outperforms DC-only in production.")
    elif rps_dc > 0:
        print(f"  [!!] Stacking worse than DC-only. Investigate.")
    else:
        print(f"  [--] DC-only comparison not available.")

    print()


# ── main ─────────────────────────────────────────────────────────
async def run(args):
    await init_db()
    async with SessionLocal() as session:
        predictions = await load_stacking_predictions(session)

        if len(predictions) < args.min_settled:
            print(f"\nOnly {len(predictions)} settled stacking predictions (need {args.min_settled}).")
            print("Wait for more data.")
            await print_prediction_status(session)
            return

        report = compute_report(predictions)
        print_report(report)
        print_recommendations(report)

        if args.output:
            out_path = Path(args.output)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            # Convert Decimals for JSON
            def default_ser(obj):
                if isinstance(obj, Decimal):
                    return float(obj)
                return str(obj)
            out_path.write_text(json.dumps(report, indent=2, default=default_ser))
            print(f"Report saved to {out_path}")


def main():
    parser = argparse.ArgumentParser(description="Production stacking monitor")
    parser.add_argument("--min-settled", type=int, default=20,
                        help="Minimum settled predictions required (default: 20)")
    parser.add_argument("--detailed", action="store_true",
                        help="Include detailed per-prediction data")
    parser.add_argument("--output", type=str, default="",
                        help="Save JSON report to path (e.g. results/production_report.json)")
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
