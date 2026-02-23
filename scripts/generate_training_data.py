"""
Generate walk-forward base model predictions for historical matches.

For each historical match (after warmup):
1. Fit DC on all matches BEFORE this one (walk-forward, periodic refit)
2. Compute Poisson predictions from rolling averages BEFORE this one
3. Compute Elo ratings at that point in time
4. Compute rest hours, odds (if available)
5. Save all predictions + features + outcome

Output: training dataset ready for stacking and calibration.

Usage:
    python scripts/generate_training_data.py --leagues 39
    python scripts/generate_training_data.py --leagues 39,78,140,135 --output results/training_data.json
    python scripts/generate_training_data.py --all-leagues --dc-refit 30
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Optional

import numpy as np
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.dixon_coles import (
    MatchData,
    fit_dixon_coles,
    predict_lambda_mu,
    tau_value,
)
from app.services.math_utils import (
    DEFAULT_ELO,
    ELO_K,
    elo_expected,
    match_probs_poisson,
    poisson_pmf,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("generate_training_data")


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------

def _get_conn(dsn: str):
    import psycopg2
    dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")
    return psycopg2.connect(dsn)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_league_matches(conn, league_id: int, from_date: str, to_date: str) -> list[dict]:
    """Load finished matches for one league from hist_fixtures."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT fixture_id, league_id, season, home_team_id, away_team_id,
                   match_date, goals_home, goals_away, xg_home, xg_away
            FROM hist_fixtures
            WHERE status = 'FT'
              AND league_id = %s
              AND match_date >= %s AND match_date < %s
              AND goals_home IS NOT NULL AND goals_away IS NOT NULL
            ORDER BY match_date ASC
            """,
            (league_id, from_date, to_date),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def load_hist_odds(conn, fixture_ids: list[int]) -> dict[int, dict]:
    """Load historical odds for fixtures. Returns {fixture_id: {fair_home, fair_draw, fair_away}}."""
    if not fixture_ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT fixture_id, odd_home, odd_draw, odd_away
            FROM hist_odds
            WHERE fixture_id = ANY(%s) AND market = '1X2'
            """,
            (fixture_ids,),
        )
        result = {}
        for row in cur.fetchall():
            fid, oh, od, oa = row
            if oh and od and oa:
                oh, od, oa = float(oh), float(od), float(oa)
                # Remove overround
                implied_sum = 1/oh + 1/od + 1/oa
                if implied_sum > 0:
                    result[fid] = {
                        "fair_home": round((1/oh) / implied_sum, 6),
                        "fair_draw": round((1/od) / implied_sum, 6),
                        "fair_away": round((1/oa) / implied_sum, 6),
                    }
        return result


# ---------------------------------------------------------------------------
# DC helpers
# ---------------------------------------------------------------------------

def _matches_to_dc_input(matches: list[dict]) -> list[MatchData]:
    result = []
    for m in matches:
        gh, ga = m.get("goals_home"), m.get("goals_away")
        if gh is None or ga is None:
            continue
        md = m.get("match_date")
        if md is None:
            continue
        if isinstance(md, datetime):
            d = md.date()
        elif isinstance(md, date):
            d = md
        else:
            d = datetime.strptime(str(md)[:10], "%Y-%m-%d").date()
        result.append(MatchData(
            home_id=m["home_team_id"],
            away_id=m["away_team_id"],
            home_goals=int(gh),
            away_goals=int(ga),
            date=d,
        ))
    return result


def _matches_to_dc_input_xg(matches: list[dict]) -> list[MatchData]:
    """Convert matches to MatchData with xG fields for DC-xG fitting."""
    result = []
    for m in matches:
        gh, ga = m.get("goals_home"), m.get("goals_away")
        if gh is None or ga is None:
            continue
        md = m.get("match_date")
        if md is None:
            continue
        if isinstance(md, datetime):
            d = md.date()
        elif isinstance(md, date):
            d = md
        else:
            d = datetime.strptime(str(md)[:10], "%Y-%m-%d").date()
        h_xg = float(m["xg_home"]) if m.get("xg_home") is not None else None
        a_xg = float(m["xg_away"]) if m.get("xg_away") is not None else None
        result.append(MatchData(
            home_id=m["home_team_id"],
            away_id=m["away_team_id"],
            home_goals=int(gh),
            away_goals=int(ga),
            date=d,
            home_xg=h_xg,
            away_xg=a_xg,
        ))
    return result


def _match_probs_dc(lam: float, mu: float, rho: float, k_max: int = 8) -> tuple[float, float, float]:
    """Dixon-Coles 1X2 probs with tau correction (float)."""
    p_h, p_d, p_a = 0.0, 0.0, 0.0
    log_lam = math.log(max(lam, 0.01))
    log_mu = math.log(max(mu, 0.01))
    for i in range(k_max + 1):
        log_pi = i * log_lam - lam - math.lgamma(i + 1)
        for j in range(k_max + 1):
            log_pj = j * log_mu - mu - math.lgamma(j + 1)
            p_ij = math.exp(log_pi + log_pj) * tau_value(i, j, lam, mu, rho)
            if p_ij < 0:
                p_ij = 0.0
            if i > j:
                p_h += p_ij
            elif i == j:
                p_d += p_ij
            else:
                p_a += p_ij
    total = p_h + p_d + p_a
    if total > 0:
        p_h /= total
        p_d /= total
        p_a /= total
    return p_h, p_d, p_a


# ---------------------------------------------------------------------------
# Walk-forward generation
# ---------------------------------------------------------------------------

def generate_for_league(
    matches: list[dict],
    odds_map: dict[int, dict],
    warmup: int = 50,
    dc_refit_interval: int = 30,
    dc_min_matches: int = 30,
) -> list[dict]:
    """Walk-forward generation of training data for one league.

    Args:
        matches: Chronologically sorted finished matches.
        odds_map: {fixture_id: {fair_home, fair_draw, fair_away}}.
        warmup: Skip first N matches (build state only).
        dc_refit_interval: Refit DC every N matches.
        dc_min_matches: Minimum matches to fit DC.

    Returns:
        List of training examples with base model predictions.
    """
    ratings: dict[int, float] = {}
    xg_for: dict[int, list[float]] = defaultdict(list)
    xg_against: dict[int, list[float]] = defaultdict(list)
    last_match_dt: dict[int, datetime] = {}

    dc_params = None
    dc_last_fit_idx = -999

    dc_xg_params = None
    dc_xg_last_fit_idx = -999

    training_data = []

    for idx, match in enumerate(matches):
        gh = match.get("goals_home")
        ga = match.get("goals_away")
        if gh is None or ga is None:
            continue

        h = match["home_team_id"]
        a = match["away_team_id"]
        fid = match["fixture_id"]
        md = match["match_date"]

        # Parse date
        if isinstance(md, str):
            md_parsed = datetime.strptime(md[:19], "%Y-%m-%d %H:%M:%S" if len(md) > 10 else "%Y-%m-%d")
        else:
            md_parsed = md
        if isinstance(md_parsed, date) and not isinstance(md_parsed, datetime):
            md_parsed = datetime(md_parsed.year, md_parsed.month, md_parsed.day)

        # Outcome
        gh_i, ga_i = int(gh), int(ga)
        if gh_i > ga_i:
            outcome = 0
        elif gh_i == ga_i:
            outcome = 1
        else:
            outcome = 2

        # --- Prediction phase (before observing result) ---
        if idx >= warmup:
            # DC refit
            if idx - dc_last_fit_idx >= dc_refit_interval and idx >= dc_min_matches:
                dc_input = _matches_to_dc_input(matches[:idx])
                if len(dc_input) >= dc_min_matches:
                    ref = md_parsed.date() if isinstance(md_parsed, datetime) else md_parsed
                    try:
                        dc_params = fit_dixon_coles(dc_input, ref_date=ref, xi=0.005, rho_grid_steps=21)
                        dc_last_fit_idx = idx
                    except ValueError:
                        pass

            # DC predict (goals)
            p_home_dc = p_draw_dc = p_away_dc = None
            dc_att_h = dc_def_h = dc_att_a = dc_def_a = None
            dc_ha = dc_rho = None
            if dc_params is not None:
                att_h = dc_params.attack.get(h)
                def_h = dc_params.defense.get(h)
                att_a = dc_params.attack.get(a)
                def_a = dc_params.defense.get(a)
                if all(v is not None for v in (att_h, def_h, att_a, def_a)):
                    lam_dc, mu_dc = predict_lambda_mu(att_h, def_h, att_a, def_a, dc_params.home_advantage)
                    lam_dc = max(0.01, min(10.0, lam_dc))
                    mu_dc = max(0.01, min(10.0, mu_dc))
                    p_home_dc, p_draw_dc, p_away_dc = _match_probs_dc(lam_dc, mu_dc, dc_params.rho)
                    dc_att_h, dc_def_h = att_h, def_h
                    dc_att_a, dc_def_a = att_a, def_a
                    dc_ha = dc_params.home_advantage
                    dc_rho = dc_params.rho

            # DC-xG refit
            if idx - dc_xg_last_fit_idx >= dc_refit_interval and idx >= dc_min_matches:
                dc_xg_input = _matches_to_dc_input_xg(matches[:idx])
                if len([m for m in dc_xg_input if m.home_xg is not None]) >= dc_min_matches:
                    ref = md_parsed.date() if isinstance(md_parsed, datetime) else md_parsed
                    try:
                        dc_xg_params = fit_dixon_coles(dc_xg_input, ref_date=ref, xi=0.005,
                                                       rho_grid_steps=1, use_xg=True)
                        dc_xg_last_fit_idx = idx
                    except ValueError:
                        pass

            # DC-xG predict (rho=0 → no tau correction)
            p_home_dc_xg = p_draw_dc_xg = p_away_dc_xg = None
            if dc_xg_params is not None:
                att_h = dc_xg_params.attack.get(h)
                def_h = dc_xg_params.defense.get(h)
                att_a = dc_xg_params.attack.get(a)
                def_a = dc_xg_params.defense.get(a)
                if all(v is not None for v in (att_h, def_h, att_a, def_a)):
                    lam_xg, mu_xg = predict_lambda_mu(att_h, def_h, att_a, def_a, dc_xg_params.home_advantage)
                    lam_xg = max(0.01, min(10.0, lam_xg))
                    mu_xg = max(0.01, min(10.0, mu_xg))
                    p_home_dc_xg, p_draw_dc_xg, p_away_dc_xg = _match_probs_dc(lam_xg, mu_xg, 0.0)

            # Poisson from rolling averages
            elo_h = ratings.get(h, DEFAULT_ELO)
            elo_a = ratings.get(a, DEFAULT_ELO)
            elo_diff = elo_h - elo_a

            h_xg_hist = xg_for.get(h, [])
            a_xg_hist = xg_for.get(a, [])
            h_xg_l5 = float(np.mean(h_xg_hist[-5:])) if len(h_xg_hist) >= 3 else None
            a_xg_l5 = float(np.mean(a_xg_hist[-5:])) if len(a_xg_hist) >= 3 else None

            h_def = xg_against.get(h, [])
            a_def = xg_against.get(a, [])
            h_def_l5 = float(np.mean(h_def[-5:])) if len(h_def) >= 3 else None
            a_def_l5 = float(np.mean(a_def[-5:])) if len(a_def) >= 3 else None

            if h_xg_l5 is not None and a_def_l5 is not None:
                lam_pois = max(0.1, 0.6 * h_xg_l5 + 0.4 * a_def_l5)
            else:
                lam_pois = 1.3
            if a_xg_l5 is not None and h_def_l5 is not None:
                mu_pois = max(0.1, 0.6 * a_xg_l5 + 0.4 * h_def_l5)
            else:
                mu_pois = 1.1

            adj = max(0.80, min(1.20, 1.0 + elo_diff / 1800.0))
            lam_pois *= adj
            mu_pois /= adj

            p_home_pois, p_draw_pois, p_away_pois = match_probs_poisson(lam_pois, mu_pois)

            # Elo-based probability (with home advantage)
            elo_diff_ha = elo_diff + 65  # home advantage
            p_home_elo = 1.0 / (1.0 + 10.0 ** (-elo_diff_ha / 400.0))

            # Rest hours
            h_rest = None
            a_rest = None
            h_last = last_match_dt.get(h)
            a_last = last_match_dt.get(a)
            if h_last is not None:
                delta = md_parsed - h_last
                h_rest = round(delta.total_seconds() / 3600.0, 1)
            if a_last is not None:
                delta = md_parsed - a_last
                a_rest = round(delta.total_seconds() / 3600.0, 1)

            # Odds
            odds = odds_map.get(fid, {})

            example = {
                "fixture_id": fid,
                "league_id": match["league_id"],
                "season": match.get("season"),
                "kickoff": str(md)[:10],
                "home_id": h,
                "away_id": a,
                "home_goals": gh_i,
                "away_goals": ga_i,
                "outcome": outcome,

                # Poisson predictions (rolling avg + Elo adj)
                "p_home_poisson": round(p_home_pois, 6),
                "p_draw_poisson": round(p_draw_pois, 6),
                "p_away_poisson": round(p_away_pois, 6),
                "lam_home_poisson": round(lam_pois, 4),
                "lam_away_poisson": round(mu_pois, 4),

                # DC predictions (goals)
                "p_home_dc": round(p_home_dc, 6) if p_home_dc is not None else None,
                "p_draw_dc": round(p_draw_dc, 6) if p_draw_dc is not None else None,
                "p_away_dc": round(p_away_dc, 6) if p_away_dc is not None else None,

                # DC-xG predictions
                "p_home_dc_xg": round(p_home_dc_xg, 6) if p_home_dc_xg is not None else None,
                "p_draw_dc_xg": round(p_draw_dc_xg, 6) if p_draw_dc_xg is not None else None,
                "p_away_dc_xg": round(p_away_dc_xg, 6) if p_away_dc_xg is not None else None,

                # Elo
                "elo_home": round(elo_h, 1),
                "elo_away": round(elo_a, 1),
                "elo_diff": round(elo_diff, 1),
                "p_home_elo": round(p_home_elo, 6),

                # Rest hours
                "rest_hours_home": h_rest,
                "rest_hours_away": a_rest,

                # Fair implied probs (overround-removed)
                "fair_home": odds.get("fair_home"),
                "fair_draw": odds.get("fair_draw"),
                "fair_away": odds.get("fair_away"),
                "has_odds": bool(odds),

                # Standings delta (not available in hist_fixtures, default 0)
                "standings_delta": 0.0,
            }
            training_data.append(example)

        # --- Update phase (observe result) ---
        # Elo update
        exp_h = elo_expected(ratings.get(h, DEFAULT_ELO), ratings.get(a, DEFAULT_ELO))
        if gh_i > ga_i:
            sh = 1.0
        elif gh_i == ga_i:
            sh = 0.5
        else:
            sh = 0.0
        ratings[h] = ratings.get(h, DEFAULT_ELO) + ELO_K * (sh - exp_h)
        ratings[a] = ratings.get(a, DEFAULT_ELO) + ELO_K * ((1.0 - sh) - (1.0 - exp_h))

        # xG update
        h_xg = float(match["xg_home"]) if match.get("xg_home") is not None else float(gh_i)
        a_xg = float(match["xg_away"]) if match.get("xg_away") is not None else float(ga_i)
        xg_for[h].append(h_xg)
        xg_against[h].append(a_xg)
        xg_for[a].append(a_xg)
        xg_against[a].append(h_xg)

        # Last match datetime
        last_match_dt[h] = md_parsed
        last_match_dt[a] = md_parsed

    return training_data


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Generate walk-forward training data for stacking/calibration")
    parser.add_argument("--leagues", help="Comma-separated league IDs (default: from LEAGUE_IDS)")
    parser.add_argument("--from-date", default="2022-01-01")
    parser.add_argument("--to-date", default="2026-12-31")
    parser.add_argument("--warmup", type=int, default=50)
    parser.add_argument("--dc-refit", type=int, default=30, help="Refit DC every N matches (default: 30)")
    parser.add_argument("--output", default="results/training_data.json")
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
    all_training_data = []
    total_time = 0.0

    for lid in leagues:
        log.info("=== League %d ===", lid)
        t0 = time.perf_counter()

        matches = load_league_matches(conn, lid, args.from_date, args.to_date)
        log.info("  Loaded %d matches", len(matches))

        if not matches:
            continue

        # Load odds for this league's fixtures
        fids = [m["fixture_id"] for m in matches]
        odds_map = load_hist_odds(conn, fids)
        log.info("  Loaded odds for %d fixtures", len(odds_map))

        data = generate_for_league(
            matches,
            odds_map,
            warmup=args.warmup,
            dc_refit_interval=args.dc_refit,
        )

        elapsed = time.perf_counter() - t0
        total_time += elapsed

        n_dc = sum(1 for x in data if x["p_home_dc"] is not None)
        n_dc_xg = sum(1 for x in data if x["p_home_dc_xg"] is not None)
        n_odds = sum(1 for x in data if x["has_odds"])
        log.info("  Generated %d examples (%d with DC, %d with DC-xG, %d with odds) in %.1fs",
                 len(data), n_dc, n_dc_xg, n_odds, elapsed)

        all_training_data.extend(data)

    conn.close()

    # Save
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w") as f:
        json.dump({
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "params": {"warmup": args.warmup, "dc_refit": args.dc_refit, "leagues": leagues},
            "n_total": len(all_training_data),
            "data": all_training_data,
        }, f, indent=2, default=str)

    # Summary
    n_dc = sum(1 for x in all_training_data if x["p_home_dc"] is not None)
    n_dc_xg = sum(1 for x in all_training_data if x["p_home_dc_xg"] is not None)
    n_odds = sum(1 for x in all_training_data if x["has_odds"])
    leagues_set = sorted(set(x["league_id"] for x in all_training_data))

    log.info("")
    log.info("=" * 60)
    log.info("SUMMARY")
    log.info("=" * 60)
    log.info("  Total examples: %d", len(all_training_data))
    log.info("  With DC:        %d (%.1f%%)", n_dc, 100 * n_dc / max(1, len(all_training_data)))
    log.info("  With DC-xG:     %d (%.1f%%)", n_dc_xg, 100 * n_dc_xg / max(1, len(all_training_data)))
    log.info("  With odds:      %d (%.1f%%)", n_odds, 100 * n_odds / max(1, len(all_training_data)))
    log.info("  Leagues:        %s", leagues_set)
    log.info("  Total time:     %.1fs", total_time)
    log.info("  Output:         %s", args.output)


if __name__ == "__main__":
    main()
