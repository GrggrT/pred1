"""
scripts/backtest.py
===================
Backtest the prediction model on historical data.

Uses hist_fixtures + hist_odds to simulate predictions without writing to DB.
Computes ROI, hit rate, Brier score, and LogLoss per league.
Supports comparing old model (hardcoded coefficients) vs new model (trained params).

Usage:
    python scripts/backtest.py
    python scripts/backtest.py --leagues 39,78
    python scripts/backtest.py --from-date 2023-01-01 --to-date 2025-12-31
    python scripts/backtest.py --compare          # compare old vs new model
    python scripts/backtest.py --ev-threshold 0.08
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

import numpy as np
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.math_utils import (
    DEFAULT_ELO,
    ELO_K,
    elo_expected as _elo_expected,
    match_probs_poisson as _match_probs,
    poisson_pmf as _poisson_pmf,
    power_scale as _power_scale_list,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("backtest")


def _power_scale(probs: list[float], alpha: float) -> list[float]:
    return _power_scale_list(probs, alpha)


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------

def _get_conn(dsn: str):
    import psycopg2
    dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")
    return psycopg2.connect(dsn)


# ---------------------------------------------------------------------------
# Feature computation
# ---------------------------------------------------------------------------

def _compute_features_and_predict(
    fixtures: list[dict],
    odds_map: dict[int, dict],
    model_params: dict[str, float],
    ev_threshold: float = 0.08,
    use_trained: bool = True,
) -> list[dict]:
    """
    Simulate build_predictions logic on historical data.
    Returns list of prediction dicts with outcomes.
    """
    ratings: dict[int, float] = defaultdict(lambda: DEFAULT_ELO)
    team_xg_for: dict[int, list[float]] = defaultdict(list)   # xG scored
    team_xg_against: dict[int, list[float]] = defaultdict(list)  # xG conceded
    team_form: dict[int, list[float]] = defaultdict(list)

    # Load trained coefficients
    if use_trained and model_params:
        elo_coef = model_params.get("logistic_coef_home_win_elo_diff", 0.002)
        xpts_coef = model_params.get("logistic_coef_home_win_xpts_diff", 0.05)
        optimal_alpha = model_params.get("optimal_alpha", 1.0)
    else:
        elo_coef = 0.002
        xpts_coef = 0.05
        optimal_alpha = 1.0

    predictions = []

    for f in fixtures:
        fid = f["fixture_id"]
        h = f["home_team_id"]
        a = f["away_team_id"]
        gh = f["goals_home"]
        ga = f["goals_away"]

        if gh is None or ga is None:
            continue

        # Elo before match
        elo_h = ratings[h]
        elo_a = ratings[a]
        elo_diff = elo_h - elo_a

        # Rolling xG L5
        h_xg_for_hist = team_xg_for[h]
        a_xg_for_hist = team_xg_for[a]
        h_xg_l5 = np.mean(h_xg_for_hist[-5:]) if len(h_xg_for_hist) >= 3 else None
        a_xg_l5 = np.mean(a_xg_for_hist[-5:]) if len(a_xg_for_hist) >= 3 else None

        # Defense: opponent xG conceded
        h_def_hist = team_xg_against[h]
        a_def_hist = team_xg_against[a]
        h_def_l5 = np.mean(h_def_hist[-5:]) if len(h_def_hist) >= 3 else None
        a_def_l5 = np.mean(a_def_hist[-5:]) if len(a_def_hist) >= 3 else None

        # Lambda: blend of team's attack xG and opponent's defensive xG conceded
        if h_xg_l5 is not None and a_def_l5 is not None:
            lam_h = max(0.1, 0.6 * h_xg_l5 + 0.4 * a_def_l5)
        else:
            lam_h = 1.3  # league average home

        if a_xg_l5 is not None and h_def_l5 is not None:
            lam_a = max(0.1, 0.6 * a_xg_l5 + 0.4 * h_def_l5)
        else:
            lam_a = 1.1  # league average away

        # Elo adjustment
        adj = max(0.80, min(1.20, 1.0 + elo_diff / 1800.0))
        lam_h *= adj
        lam_a /= adj

        # Form L5
        h_form_hist = team_form[h]
        a_form_hist = team_form[a]
        h_form = np.mean(h_form_hist[-5:]) if len(h_form_hist) >= 3 else None
        a_form = np.mean(a_form_hist[-5:]) if len(a_form_hist) >= 3 else None

        # Poisson probs
        p_h, p_d, p_a = _match_probs(lam_h, lam_a)

        # Power scaling
        p_h, p_d, p_a = _power_scale([p_h, p_d, p_a], optimal_alpha)

        # Totals
        lam_total = lam_h + lam_a
        p_under_2_5 = sum(_poisson_pmf(k, lam_total) for k in range(3))
        p_over_2_5 = 1.0 - p_under_2_5

        # Actual outcome
        if gh > ga:
            outcome_1x2 = "HOME_WIN"
        elif gh == ga:
            outcome_1x2 = "DRAW"
        else:
            outcome_1x2 = "AWAY_WIN"

        outcome_total = "OVER_2_5" if (gh + ga) > 2 else "UNDER_2_5"

        # Odds
        odds = odds_map.get(fid, {})
        odd_h = odds.get("odd_home")
        odd_d = odds.get("odd_draw")
        odd_a = odds.get("odd_away")
        odd_over = odds.get("odd_over")
        odd_under = odds.get("odd_under")

        # Signal score filter: skip if model is not confident enough
        max_prob_1x2 = max(p_h, p_d, p_a)
        signal_score = max_prob_1x2  # simplified signal_score

        # 1X2 EV selection
        best_sel = None
        best_ev = None
        best_odd = None
        best_prob = None

        for sel, prob, odd in [
            ("HOME_WIN", p_h, odd_h),
            ("DRAW", p_d, odd_d),
            ("AWAY_WIN", p_a, odd_a),
        ]:
            if odd is None or odd < 1.5 or odd > 3.2:
                continue
            ev = prob * odd - 1.0
            if best_ev is None or ev > best_ev:
                best_sel = sel
                best_ev = ev
                best_odd = odd
                best_prob = prob

        if best_sel and best_ev is not None and best_ev > ev_threshold and signal_score >= 0.42:
            won = best_sel == outcome_1x2
            profit = (best_odd - 1.0) if won else -1.0
            predictions.append({
                "fixture_id": fid,
                "league_id": f["league_id"],
                "market": "1X2",
                "selection": best_sel,
                "prob": best_prob,
                "odd": best_odd,
                "ev": best_ev,
                "outcome": outcome_1x2,
                "won": won,
                "profit": profit,
                "p_h": p_h,
                "p_d": p_d,
                "p_a": p_a,
            })

        # Totals EV selection
        best_sel_t = None
        best_ev_t = None
        best_odd_t = None
        best_prob_t = None

        # Higher threshold for totals (less reliable model)
        totals_threshold = max(ev_threshold, 0.12)
        for sel, prob, odd in [
            ("OVER_2_5", p_over_2_5, odd_over),
            ("UNDER_2_5", p_under_2_5, odd_under),
        ]:
            if odd is None or odd < 1.5 or odd > 3.2:
                continue
            ev = prob * odd - 1.0
            if ev > totals_threshold:
                if best_ev_t is None or ev > best_ev_t:
                    best_sel_t = sel
                    best_ev_t = ev
                    best_odd_t = odd
                    best_prob_t = prob

        if best_sel_t and best_ev_t is not None:
            won_t = best_sel_t == outcome_total
            profit_t = (best_odd_t - 1.0) if won_t else -1.0
            predictions.append({
                "fixture_id": fid,
                "league_id": f["league_id"],
                "market": "TOTAL",
                "selection": best_sel_t,
                "prob": best_prob_t,
                "odd": best_odd_t,
                "ev": best_ev_t,
                "outcome": outcome_total,
                "won": won_t,
                "profit": profit_t,
                "p_h": p_over_2_5,
                "p_d": 0.0,
                "p_a": p_under_2_5,
            })

        # Update Elo
        exp_h = _elo_expected(elo_h, elo_a)
        if gh > ga:
            sh, sa = 1.0, 0.0
        elif gh == ga:
            sh, sa = 0.5, 0.5
        else:
            sh, sa = 0.0, 1.0
        ratings[h] += ELO_K * (sh - exp_h)
        ratings[a] += ELO_K * (sa - (1.0 - exp_h))

        # Update xG history (attack = xG scored, defense = xG conceded)
        h_xg = float(f["xg_home"]) if f.get("xg_home") is not None else float(gh)
        a_xg = float(f["xg_away"]) if f.get("xg_away") is not None else float(ga)
        team_xg_for[h].append(h_xg)
        team_xg_against[h].append(a_xg)
        team_xg_for[a].append(a_xg)
        team_xg_against[a].append(h_xg)

        # Update form
        if gh > ga:
            team_form[h].append(3.0)
            team_form[a].append(0.0)
        elif gh == ga:
            team_form[h].append(1.0)
            team_form[a].append(1.0)
        else:
            team_form[h].append(0.0)
            team_form[a].append(3.0)

    return predictions


def _accuracy_only(
    fixtures: list[dict],
    model_params: dict[str, float],
    use_trained: bool = True,
) -> list[dict]:
    """Evaluate pure model accuracy on all matches (no odds needed)."""
    ratings: dict[int, float] = defaultdict(lambda: DEFAULT_ELO)
    team_xg_for: dict[int, list[float]] = defaultdict(list)
    team_xg_against: dict[int, list[float]] = defaultdict(list)

    if use_trained and model_params:
        optimal_alpha = model_params.get("optimal_alpha", 1.0)
    else:
        optimal_alpha = 1.0

    results = []
    for f in fixtures:
        h = f["home_team_id"]
        a = f["away_team_id"]
        gh = f["goals_home"]
        ga = f["goals_away"]
        if gh is None or ga is None:
            continue

        elo_h = ratings[h]
        elo_a = ratings[a]
        elo_diff = elo_h - elo_a

        h_xg_for_hist = team_xg_for[h]
        a_xg_for_hist = team_xg_for[a]
        h_xg_l5 = np.mean(h_xg_for_hist[-5:]) if len(h_xg_for_hist) >= 3 else None
        a_xg_l5 = np.mean(a_xg_for_hist[-5:]) if len(a_xg_for_hist) >= 3 else None

        h_def_hist = team_xg_against[h]
        a_def_hist = team_xg_against[a]
        h_def_l5 = np.mean(h_def_hist[-5:]) if len(h_def_hist) >= 3 else None
        a_def_l5 = np.mean(a_def_hist[-5:]) if len(a_def_hist) >= 3 else None

        if h_xg_l5 is not None and a_def_l5 is not None:
            lam_h = max(0.1, 0.6 * h_xg_l5 + 0.4 * a_def_l5)
        else:
            lam_h = 1.3

        if a_xg_l5 is not None and h_def_l5 is not None:
            lam_a = max(0.1, 0.6 * a_xg_l5 + 0.4 * h_def_l5)
        else:
            lam_a = 1.1

        adj = max(0.80, min(1.20, 1.0 + elo_diff / 1800.0))
        lam_h *= adj
        lam_a /= adj

        p_h, p_d, p_a = _match_probs(lam_h, lam_a)
        p_h, p_d, p_a = _power_scale([p_h, p_d, p_a], optimal_alpha)

        predicted = max(
            [("HOME_WIN", p_h), ("DRAW", p_d), ("AWAY_WIN", p_a)],
            key=lambda x: x[1],
        )[0]

        if gh > ga:
            outcome = "HOME_WIN"
        elif gh == ga:
            outcome = "DRAW"
        else:
            outcome = "AWAY_WIN"

        results.append({
            "fixture_id": f["fixture_id"],
            "league_id": f["league_id"],
            "predicted": predicted,
            "outcome": outcome,
            "correct": predicted == outcome,
            "p_h": p_h,
            "p_d": p_d,
            "p_a": p_a,
        })

        # Update Elo
        exp_h = _elo_expected(elo_h, elo_a)
        sh = 1.0 if gh > ga else (0.5 if gh == ga else 0.0)
        ratings[h] += ELO_K * (sh - exp_h)
        ratings[a] += ELO_K * ((1.0 - sh) - (1.0 - exp_h))

        h_xg = float(f["xg_home"]) if f.get("xg_home") is not None else float(gh)
        a_xg = float(f["xg_away"]) if f.get("xg_away") is not None else float(ga)
        team_xg_for[h].append(h_xg)
        team_xg_against[h].append(a_xg)
        team_xg_for[a].append(a_xg)
        team_xg_against[a].append(h_xg)

    return results


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def compute_metrics(predictions: list[dict], label: str = "") -> dict:
    """Compute ROI, hit rate, Brier, LogLoss from predictions."""
    if not predictions:
        return {}

    total_profit = sum(p["profit"] for p in predictions)
    n = len(predictions)
    wins = sum(1 for p in predictions if p["won"])
    roi = (total_profit / n) * 100.0 if n > 0 else 0.0
    hit_rate = (wins / n) * 100.0 if n > 0 else 0.0

    # Brier & LogLoss (1X2 only)
    preds_1x2 = [p for p in predictions if p["market"] == "1X2"]
    brier = None
    logloss = None

    if preds_1x2:
        brier_sum = 0.0
        ll_sum = 0.0
        eps = 1e-15
        for p in preds_1x2:
            probs = [p["p_h"], p["p_d"], p["p_a"]]
            outcome = p["outcome"]
            y = [0, 0, 0]
            if outcome == "HOME_WIN":
                y[0] = 1
            elif outcome == "DRAW":
                y[1] = 1
            else:
                y[2] = 1

            brier_sum += sum((pi - yi) ** 2 for pi, yi in zip(probs, y))
            # LogLoss: -sum(y * log(p))
            ll_sum += -sum(yi * math.log(max(pi, eps)) for pi, yi in zip(probs, y))

        brier = brier_sum / len(preds_1x2)
        logloss = ll_sum / len(preds_1x2)

    return {
        "label": label,
        "n_bets": n,
        "wins": wins,
        "hit_rate": hit_rate,
        "total_profit": total_profit,
        "roi": roi,
        "brier": brier,
        "logloss": logloss,
    }


def print_metrics(metrics: dict):
    """Pretty-print metrics."""
    if not metrics:
        log.info("  No predictions")
        return
    log.info("  %s: %d bets, %d wins (%.1f%%), ROI=%.2f%%, Profit=%.2f units",
             metrics.get("label", ""),
             metrics["n_bets"], metrics["wins"], metrics["hit_rate"],
             metrics["roi"], metrics["total_profit"])
    if metrics.get("brier") is not None:
        log.info("    Brier=%.4f, LogLoss=%.4f", metrics["brier"], metrics["logloss"])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Backtest prediction model on historical data")
    parser.add_argument("--leagues", help="Comma-separated league IDs")
    parser.add_argument("--from-date", default="2022-01-01")
    parser.add_argument("--to-date", default="2026-02-20")
    parser.add_argument("--ev-threshold", type=float, default=0.08)
    parser.add_argument("--compare", action="store_true", help="Compare old vs new model")
    parser.add_argument("--accuracy-only", action="store_true", help="Skip EV/odds filter, evaluate pure model accuracy")
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        log.error("DATABASE_URL not set")
        sys.exit(1)

    if args.leagues:
        leagues = [int(x.strip()) for x in args.leagues.split(",") if x.strip().isdigit()]
    else:
        raw = os.environ.get("LEAGUE_IDS", "39,78,140,135")
        leagues = [int(x.strip()) for x in raw.split(",") if x.strip().isdigit()]

    conn = _get_conn(database_url)

    # Load fixtures
    log.info("Loading fixtures for leagues %s, %s to %s", leagues, args.from_date, args.to_date)
    with conn.cursor() as cur:
        placeholders = ",".join(["%s"] * len(leagues))
        cur.execute(
            f"""
            SELECT fixture_id, league_id, season, home_team_id, away_team_id,
                   match_date, goals_home, goals_away, xg_home, xg_away
            FROM hist_fixtures
            WHERE status = 'FT'
              AND league_id IN ({placeholders})
              AND match_date >= %s AND match_date < %s
              AND goals_home IS NOT NULL AND goals_away IS NOT NULL
            ORDER BY match_date ASC
            """,
            (*leagues, args.from_date, args.to_date),
        )
        cols = [d[0] for d in cur.description]
        fixtures = [dict(zip(cols, r)) for r in cur.fetchall()]
    log.info("Loaded %d fixtures", len(fixtures))

    # Load odds
    fixture_ids = [f["fixture_id"] for f in fixtures]
    odds_map: dict[int, dict] = {}
    if fixture_ids:
        with conn.cursor() as cur:
            # Batch load odds
            cur.execute(
                """
                SELECT fixture_id, market, line, odd_home, odd_draw, odd_away, odd_over, odd_under
                FROM hist_odds
                WHERE fixture_id = ANY(%s)
                """,
                (fixture_ids,),
            )
            for row in cur.fetchall():
                fid = row[0]
                market = row[1]
                if fid not in odds_map:
                    odds_map[fid] = {}
                if market == "1X2":
                    odds_map[fid]["odd_home"] = float(row[3]) if row[3] else None
                    odds_map[fid]["odd_draw"] = float(row[4]) if row[4] else None
                    odds_map[fid]["odd_away"] = float(row[5]) if row[5] else None
                elif market == "Over/Under" and row[2] == "2.5":
                    odds_map[fid]["odd_over"] = float(row[6]) if row[6] else None
                    odds_map[fid]["odd_under"] = float(row[7]) if row[7] else None
    log.info("Loaded odds for %d fixtures", len(odds_map))

    # Load model params
    model_params: dict[str, float] = {}
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT param_name, param_value FROM model_params WHERE scope='global' AND league_id IS NULL")
            for row in cur.fetchall():
                model_params[row[0]] = float(row[1])
        log.info("Loaded %d model params", len(model_params))
    except Exception:
        log.info("No model_params table found, using defaults")
        conn.rollback()

    # Run backtest with new model
    log.info("\n" + "=" * 60)
    log.info("BACKTEST: NEW MODEL (trained params, threshold=%.2f)", args.ev_threshold)
    log.info("=" * 60)

    preds_new = _compute_features_and_predict(
        fixtures, odds_map, model_params,
        ev_threshold=args.ev_threshold,
        use_trained=True,
    )

    # Overall metrics
    metrics_new = compute_metrics(preds_new, "NEW_ALL")
    print_metrics(metrics_new)

    # Per market
    preds_1x2 = [p for p in preds_new if p["market"] == "1X2"]
    preds_total = [p for p in preds_new if p["market"] == "TOTAL"]
    log.info("\n  --- 1X2 ---")
    print_metrics(compute_metrics(preds_1x2, "NEW_1X2"))
    log.info("  --- TOTAL ---")
    print_metrics(compute_metrics(preds_total, "NEW_TOTAL"))

    # Per league
    league_preds: dict[int, list] = defaultdict(list)
    for p in preds_new:
        league_preds[p["league_id"]].append(p)
    log.info("\n  --- Per League ---")
    for lid in sorted(league_preds.keys()):
        m = compute_metrics(league_preds[lid], f"League_{lid}")
        print_metrics(m)

    if args.compare:
        log.info("\n" + "=" * 60)
        log.info("BACKTEST: OLD MODEL (hardcoded, threshold=0.05)")
        log.info("=" * 60)

        preds_old = _compute_features_and_predict(
            fixtures, odds_map, {},
            ev_threshold=0.05,
            use_trained=False,
        )

        metrics_old = compute_metrics(preds_old, "OLD_ALL")
        print_metrics(metrics_old)

        preds_old_1x2 = [p for p in preds_old if p["market"] == "1X2"]
        preds_old_total = [p for p in preds_old if p["market"] == "TOTAL"]
        log.info("\n  --- 1X2 ---")
        print_metrics(compute_metrics(preds_old_1x2, "OLD_1X2"))
        log.info("  --- TOTAL ---")
        print_metrics(compute_metrics(preds_old_total, "OLD_TOTAL"))

        # Comparison summary
        log.info("\n" + "=" * 60)
        log.info("COMPARISON SUMMARY")
        log.info("=" * 60)
        log.info("  %-20s %10s %10s", "", "OLD", "NEW")
        log.info("  %-20s %10d %10d", "Bets", metrics_old["n_bets"], metrics_new["n_bets"])
        log.info("  %-20s %9.1f%% %9.1f%%", "Hit Rate", metrics_old["hit_rate"], metrics_new["hit_rate"])
        log.info("  %-20s %9.2f%% %9.2f%%", "ROI", metrics_old["roi"], metrics_new["roi"])
        log.info("  %-20s %10.2f %10.2f", "Profit", metrics_old["total_profit"], metrics_new["total_profit"])
        if metrics_old.get("brier") and metrics_new.get("brier"):
            log.info("  %-20s %10.4f %10.4f", "Brier", metrics_old["brier"], metrics_new["brier"])
            log.info("  %-20s %10.4f %10.4f", "LogLoss", metrics_old["logloss"], metrics_new["logloss"])

        # Per-market comparison
        m_old_1x2 = compute_metrics(preds_old_1x2, "OLD_1X2")
        m_new_1x2 = compute_metrics(preds_1x2, "NEW_1X2")
        m_old_total = compute_metrics(preds_old_total, "OLD_TOTAL")
        m_new_total = compute_metrics(preds_total, "NEW_TOTAL")
        log.info("")
        log.info("  --- Per Market ---")
        log.info("  %-20s %10s %10s", "1X2", "OLD", "NEW")
        if m_old_1x2 and m_new_1x2:
            log.info("  %-20s %10d %10d", "  Bets", m_old_1x2["n_bets"], m_new_1x2["n_bets"])
            log.info("  %-20s %9.1f%% %9.1f%%", "  Hit Rate", m_old_1x2["hit_rate"], m_new_1x2["hit_rate"])
            log.info("  %-20s %9.2f%% %9.2f%%", "  ROI", m_old_1x2["roi"], m_new_1x2["roi"])
        log.info("  %-20s %10s %10s", "TOTAL", "OLD", "NEW")
        if m_old_total and m_new_total:
            log.info("  %-20s %10d %10d", "  Bets", m_old_total["n_bets"], m_new_total["n_bets"])
            log.info("  %-20s %9.1f%% %9.1f%%", "  Hit Rate", m_old_total["hit_rate"], m_new_total["hit_rate"])
            log.info("  %-20s %9.2f%% %9.2f%%", "  ROI", m_old_total["roi"], m_new_total["roi"])

        # Per-league comparison
        log.info("")
        log.info("  --- Per League (NEW model) ---")
        league_preds_new: dict[int, list] = defaultdict(list)
        for p in preds_new:
            league_preds_new[p["league_id"]].append(p)
        for lid in sorted(league_preds_new.keys()):
            lp = league_preds_new[lid]
            lp_1x2 = [x for x in lp if x["market"] == "1X2"]
            lp_total = [x for x in lp if x["market"] == "TOTAL"]
            m_all = compute_metrics(lp, f"L{lid}")
            m_1x2 = compute_metrics(lp_1x2, f"L{lid}_1X2")
            log.info("  League %d: ALL %d bets ROI=%.2f%% | 1X2 %d bets ROI=%.2f%% | TOTAL %d bets ROI=%.2f%%",
                     lid, m_all["n_bets"], m_all["roi"],
                     m_1x2.get("n_bets", 0), m_1x2.get("roi", 0),
                     len(lp_total), compute_metrics(lp_total, "").get("roi", 0) if lp_total else 0)

    if args.accuracy_only or len(odds_map) < 100:
        log.info("\n" + "=" * 60)
        log.info("ACCURACY-ONLY (all matches, no EV/odds filter)")
        log.info("=" * 60)

        for label, use_trained_flag, alpha_override in [
            ("NEW", True, None),
            ("OLD", False, None),
        ] if args.compare else [("MODEL", True, None)]:
            acc_preds = _accuracy_only(
                fixtures, model_params if use_trained_flag else {},
                use_trained=use_trained_flag,
            )
            if acc_preds:
                n = len(acc_preds)
                correct = sum(1 for p in acc_preds if p["correct"])
                acc = correct / n * 100.0

                brier_sum = 0.0
                ll_sum = 0.0
                eps = 1e-15
                for p in acc_preds:
                    probs = [p["p_h"], p["p_d"], p["p_a"]]
                    y = [0, 0, 0]
                    if p["outcome"] == "HOME_WIN":
                        y[0] = 1
                    elif p["outcome"] == "DRAW":
                        y[1] = 1
                    else:
                        y[2] = 1
                    brier_sum += sum((pi - yi) ** 2 for pi, yi in zip(probs, y))
                    ll_sum += -sum(yi * math.log(max(pi, eps)) for pi, yi in zip(probs, y))
                brier = brier_sum / n
                logloss = ll_sum / n

                log.info("  %s: %d matches, Accuracy=%.1f%%, Brier=%.4f, LogLoss=%.4f",
                         label, n, acc, brier, logloss)

                # Per league
                league_acc: dict[int, list] = defaultdict(list)
                for p in acc_preds:
                    league_acc[p["league_id"]].append(p)
                for lid in sorted(league_acc.keys()):
                    lp = league_acc[lid]
                    ln = len(lp)
                    lc = sum(1 for x in lp if x["correct"])
                    lb = sum(
                        sum((pi - yi) ** 2
                            for pi, yi in zip(
                                [x["p_h"], x["p_d"], x["p_a"]],
                                [1 if x["outcome"] == "HOME_WIN" else 0,
                                 1 if x["outcome"] == "DRAW" else 0,
                                 1 if x["outcome"] == "AWAY_WIN" else 0],
                            ))
                        for x in lp
                    ) / ln
                    ll = sum(
                        -sum(yi * math.log(max(pi, 1e-15))
                             for pi, yi in zip(
                                 [x["p_h"], x["p_d"], x["p_a"]],
                                 [1 if x["outcome"] == "HOME_WIN" else 0,
                                  1 if x["outcome"] == "DRAW" else 0,
                                  1 if x["outcome"] == "AWAY_WIN" else 0],
                             ))
                        for x in lp
                    ) / ln
                    log.info("    League %d: %d matches, Acc=%.1f%%, Brier=%.4f, LogLoss=%.4f",
                             lid, ln, lc / ln * 100, lb, ll)

    conn.close()
    log.info("\nDone.")


if __name__ == "__main__":
    main()
