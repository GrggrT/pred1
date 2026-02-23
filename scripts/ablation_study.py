"""
scripts/ablation_study.py
=========================
Ablation study: sequentially run walk-forward evaluation with different
model configurations and compare proper scoring metrics (RPS, LogLoss, Brier).

Configs:
  0  — Baseline: rolling xG L5 + Elo adj → Poisson (same as backtest.py)
  1  — DC core: Dixon-Coles latent params → τ-corrected Poisson
  10 — DC + Rest: DC core + fatigue adjustment from rest hours (config "1b")
  2  — DC + Stacking: meta-model combining DC, Poisson, Elo, odds
  3  — Full pipeline: DC + Stacking + Dirichlet calibration

Usage:
    python scripts/ablation_study.py
    python scripts/ablation_study.py --leagues 39,78
    python scripts/ablation_study.py --configs 0,1,2,3
    python scripts/ablation_study.py --from-date 2023-01-01 --to-date 2025-12-31
    python scripts/ablation_study.py --output results/ablation_2025.json
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
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
    elo_expected as _elo_expected,
    match_probs_poisson as _match_probs_poisson,
    poisson_pmf as _poisson_pmf,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("ablation")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DC_REFIT_INTERVAL = 50   # refit DC every N matches
DC_MIN_MATCHES = 30      # minimum matches to fit DC


def _get_conn(dsn: str):
    import psycopg2
    dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")
    return psycopg2.connect(dsn)


def compute_outcome(goals_home: int, goals_away: int) -> int:
    """Return outcome index: 0=home win, 1=draw, 2=away win."""
    if goals_home > goals_away:
        return 0
    elif goals_home == goals_away:
        return 1
    else:
        return 2


def matches_to_dc_input(matches: list[dict]) -> list[MatchData]:
    """Convert hist_fixtures dicts to MatchData for dixon_coles.fit_dixon_coles."""
    result = []
    for m in matches:
        gh = m.get("goals_home")
        ga = m.get("goals_away")
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


def matches_to_dc_input_xg(matches: list[dict]) -> list[MatchData]:
    """Convert hist_fixtures dicts to MatchData with xG for DC-xG fitting."""
    result = []
    for m in matches:
        gh = m.get("goals_home")
        ga = m.get("goals_away")
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


def load_finished_matches(
    conn,
    leagues: list[int],
    from_date: str,
    to_date: str,
) -> list[dict]:
    """Load finished matches from hist_fixtures, ordered by match_date ASC."""
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
            (*leagues, from_date, to_date),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def load_hist_odds(conn, fixture_ids: list[int]) -> dict[int, dict]:
    """Load historical odds. Returns {fixture_id: {fair_home, fair_draw, fair_away}}."""
    if not fixture_ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT fixture_id, odd_home, odd_draw, odd_away "
            "FROM hist_odds WHERE fixture_id = ANY(%s) AND market = '1X2'",
            (fixture_ids,),
        )
        result = {}
        for fid, oh, od, oa in cur.fetchall():
            if oh and od and oa:
                oh, od, oa = float(oh), float(od), float(oa)
                implied_sum = 1 / oh + 1 / od + 1 / oa
                if implied_sum > 0:
                    result[fid] = {
                        "fair_home": round((1 / oh) / implied_sum, 6),
                        "fair_draw": round((1 / od) / implied_sum, 6),
                        "fair_away": round((1 / oa) / implied_sum, 6),
                    }
        return result


def load_stacking_model_sync(conn) -> Optional[dict]:
    """Load stacking model coefficients from model_params (sync)."""
    import json as _json
    with conn.cursor() as cur:
        cur.execute(
            "SELECT metadata FROM model_params "
            "WHERE scope='stacking' AND param_name='model' AND league_id IS NULL"
        )
        row = cur.fetchone()
        if row and row[0]:
            meta = row[0] if isinstance(row[0], dict) else _json.loads(row[0])
            return {
                "coefficients": np.array(meta["coefficients"], dtype=np.float64),
                "intercept": np.array(meta["intercept"], dtype=np.float64),
                "feature_names": meta["feature_names"],
            }
    return None


STACKING_FEATURE_NAMES = [
    "p_home_poisson", "p_draw_poisson", "p_away_poisson",
    "p_home_dc", "p_draw_dc", "p_away_dc",
    "p_home_dc_xg", "p_draw_dc_xg", "p_away_dc_xg",
    "elo_diff",
    "fair_home", "fair_draw", "fair_away",
]


def _stacking_predict(features: dict[str, float], model: dict) -> tuple[float, float, float]:
    """Apply stacking model: logits → softmax → probs (float version)."""
    x = np.array([features.get(name, 0.0) for name in model["feature_names"]], dtype=np.float64)
    logits = model["coefficients"] @ x + model["intercept"]
    logits -= logits.max()
    exp_logits = np.exp(logits)
    probs = exp_logits / exp_logits.sum()
    probs = np.clip(probs, 1e-4, 1.0 - 1e-4)
    probs = probs / probs.sum()
    return float(probs[0]), float(probs[1]), float(probs[2])


# ---------------------------------------------------------------------------
# Poisson helpers (shared with backtest.py)
# ---------------------------------------------------------------------------

def _match_probs_dc(lam: float, mu: float, rho: float, k_max: int = 8) -> tuple[float, float, float]:
    """Dixon-Coles 1X2 probs with tau correction."""
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


def _fatigue_factor(rest_hours: float | None) -> float:
    """Piecewise linear fatigue multiplier (float version of build_predictions._fatigue_factor).

    rest_hours < 72h  → 0.90..0.95  (congested schedule)
    72..120h          → 0.95..1.00  (slightly short rest)
    120..192h         → 1.00..1.02  (optimal / slight freshness boost)
    >= 192h           → 1.00        (long rest, no extra boost)
    """
    if rest_hours is None:
        return 1.0
    if rest_hours < 72:
        return 0.90 + 0.05 * (rest_hours / 72.0)
    elif rest_hours < 120:
        return 0.95 + 0.05 * ((rest_hours - 72.0) / 48.0)
    elif rest_hours < 192:
        return 1.00 + 0.02 * ((rest_hours - 120.0) / 72.0)
    else:
        return 1.0


# ---------------------------------------------------------------------------
# Config 0: Baseline (Poisson + rolling xG + Elo adj)
# ---------------------------------------------------------------------------

def _predict_baseline(
    match: dict,
    state: dict,
) -> tuple[float, float, float]:
    """Predict 1X2 using baseline model: rolling xG L5 + Elo adjustment."""
    h = match["home_team_id"]
    a = match["away_team_id"]

    elo_h = state["ratings"].get(h, DEFAULT_ELO)
    elo_a = state["ratings"].get(a, DEFAULT_ELO)
    elo_diff = elo_h - elo_a

    # Rolling xG L5
    h_xg_for = state["xg_for"].get(h, [])
    a_xg_for = state["xg_for"].get(a, [])
    h_xg_l5 = np.mean(h_xg_for[-5:]) if len(h_xg_for) >= 3 else None
    a_xg_l5 = np.mean(a_xg_for[-5:]) if len(a_xg_for) >= 3 else None

    h_def = state["xg_against"].get(h, [])
    a_def = state["xg_against"].get(a, [])
    h_def_l5 = np.mean(h_def[-5:]) if len(h_def) >= 3 else None
    a_def_l5 = np.mean(a_def[-5:]) if len(a_def) >= 3 else None

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

    return _match_probs_poisson(lam_h, lam_a)


def _update_baseline_state(match: dict, state: dict) -> None:
    """Update Elo, xG history after observing match result."""
    h = match["home_team_id"]
    a = match["away_team_id"]
    gh = match["goals_home"]
    ga = match["goals_away"]

    # Elo update
    elo_h = state["ratings"].get(h, DEFAULT_ELO)
    elo_a = state["ratings"].get(a, DEFAULT_ELO)
    exp_h = _elo_expected(elo_h, elo_a)
    sh = 1.0 if gh > ga else (0.5 if gh == ga else 0.0)
    state["ratings"][h] = elo_h + ELO_K * (sh - exp_h)
    state["ratings"][a] = elo_a + ELO_K * ((1.0 - sh) - (1.0 - exp_h))

    # xG history
    h_xg = float(match["xg_home"]) if match.get("xg_home") is not None else float(gh)
    a_xg = float(match["xg_away"]) if match.get("xg_away") is not None else float(ga)
    state["xg_for"].setdefault(h, []).append(h_xg)
    state["xg_against"].setdefault(h, []).append(a_xg)
    state["xg_for"].setdefault(a, []).append(a_xg)
    state["xg_against"].setdefault(a, []).append(h_xg)


# ---------------------------------------------------------------------------
# Config 1: DC Core
# ---------------------------------------------------------------------------

def _predict_dc(
    match: dict,
    state: dict,
) -> Optional[tuple[float, float, float]]:
    """Predict 1X2 using Dixon-Coles model.

    Returns None if DC params not available for this match (fallback to baseline).
    """
    dc_params = state.get("dc_params")
    if dc_params is None:
        return None

    h = match["home_team_id"]
    a = match["away_team_id"]

    att_h = dc_params.attack.get(h)
    def_h = dc_params.defense.get(h)
    att_a = dc_params.attack.get(a)
    def_a = dc_params.defense.get(a)

    if att_h is None or def_h is None or att_a is None or def_a is None:
        return None

    lam, mu = predict_lambda_mu(att_h, def_h, att_a, def_a, dc_params.home_advantage)
    lam = max(0.01, min(10.0, lam))
    mu = max(0.01, min(10.0, mu))

    return _match_probs_dc(lam, mu, dc_params.rho)


def _maybe_refit_dc(state: dict, match_date) -> None:
    """Refit DC if enough new matches since last fit."""
    history = state["dc_history"]
    last_fit_count = state.get("dc_last_fit_count", 0)

    if len(history) < DC_MIN_MATCHES:
        return

    if len(history) - last_fit_count < DC_REFIT_INTERVAL and state.get("dc_params") is not None:
        return

    if isinstance(match_date, datetime):
        ref = match_date.date()
    elif isinstance(match_date, date):
        ref = match_date
    else:
        ref = datetime.strptime(str(match_date)[:10], "%Y-%m-%d").date()

    dc_input = matches_to_dc_input(history)
    try:
        params = fit_dixon_coles(dc_input, ref_date=ref, xi=0.005, rho_grid_steps=21)
        state["dc_params"] = params
        state["dc_last_fit_count"] = len(history)
        log.debug("DC refit: %d matches, %d teams, rho=%.4f",
                  params.n_matches, params.n_teams, params.rho)
    except ValueError as e:
        log.debug("DC refit skipped: %s", e)


def _maybe_refit_dc_xg(state: dict, match_date) -> None:
    """Refit DC-xG if enough new matches since last fit."""
    history = state["dc_xg_history"]
    last_fit_count = state.get("dc_xg_last_fit_count", 0)

    if len(history) < DC_MIN_MATCHES:
        return

    if len(history) - last_fit_count < DC_REFIT_INTERVAL and state.get("dc_xg_params") is not None:
        return

    if isinstance(match_date, datetime):
        ref = match_date.date()
    elif isinstance(match_date, date):
        ref = match_date
    else:
        ref = datetime.strptime(str(match_date)[:10], "%Y-%m-%d").date()

    dc_input = matches_to_dc_input_xg(history)
    try:
        params = fit_dixon_coles(dc_input, ref_date=ref, xi=0.005,
                                 rho_grid_steps=1, use_xg=True)
        state["dc_xg_params"] = params
        state["dc_xg_last_fit_count"] = len(history)
        log.debug("DC-xG refit: %d matches, %d teams, HA=%.4f",
                  params.n_matches, params.n_teams, params.home_advantage)
    except ValueError as e:
        log.debug("DC-xG refit skipped: %s", e)


def _predict_dc_xg(
    match: dict,
    state: dict,
) -> Optional[tuple[float, float, float]]:
    """Predict 1X2 using DC-xG model (rho=0, no tau correction).

    Returns None if DC-xG params not available for this match.
    """
    dc_xg_params = state.get("dc_xg_params")
    if dc_xg_params is None:
        return None

    h = match["home_team_id"]
    a = match["away_team_id"]

    att_h = dc_xg_params.attack.get(h)
    def_h = dc_xg_params.defense.get(h)
    att_a = dc_xg_params.attack.get(a)
    def_a = dc_xg_params.defense.get(a)

    if att_h is None or def_h is None or att_a is None or def_a is None:
        return None

    lam, mu = predict_lambda_mu(att_h, def_h, att_a, def_a, dc_xg_params.home_advantage)
    lam = max(0.01, min(10.0, lam))
    mu = max(0.01, min(10.0, mu))

    return _match_probs_dc(lam, mu, 0.0)  # rho=0 for xG mode


# ---------------------------------------------------------------------------
# Scoring (float-based for speed)
# ---------------------------------------------------------------------------

def _rps(p_h: float, p_d: float, p_a: float, outcome: int) -> float:
    """Ranked Probability Score for 1X2."""
    actual = [0.0, 0.0, 0.0]
    actual[outcome] = 1.0
    cum_pred_1 = p_h
    cum_pred_2 = p_h + p_d
    cum_act_1 = actual[0]
    cum_act_2 = actual[0] + actual[1]
    return 0.5 * ((cum_pred_1 - cum_act_1) ** 2 + (cum_pred_2 - cum_act_2) ** 2)


def _brier(p_h: float, p_d: float, p_a: float, outcome: int) -> float:
    """Multiclass Brier score."""
    actual = [0.0, 0.0, 0.0]
    actual[outcome] = 1.0
    return sum((p - y) ** 2 for p, y in zip([p_h, p_d, p_a], actual))


def _logloss(p_h: float, p_d: float, p_a: float, outcome: int) -> float:
    """Multiclass log-loss."""
    eps = 1e-15
    probs = [max(p_h, eps), max(p_d, eps), max(p_a, eps)]
    return -math.log(probs[outcome])


# ---------------------------------------------------------------------------
# Walk-forward evaluation
# ---------------------------------------------------------------------------

def walk_forward_evaluate(
    fixtures: list[dict],
    config_id: int,
    warmup: int = 50,
    odds_map: dict[int, dict] | None = None,
    stacking_model: dict | None = None,
) -> list[dict]:
    """Walk-forward evaluation for a given config.

    First `warmup` matches are used to build state (Elo, xG history, DC fit).
    Scoring starts after warmup.

    Args:
        fixtures: Sorted by match_date ASC.
        config_id: 0=baseline, 1=DC core, 10=DC+rest, 2=DC+stacking, 3=DC+stacking+dirichlet.
        warmup: Number of matches to skip before scoring.
        odds_map: {fixture_id: {fair_home, fair_draw, fair_away}} for stacking.
        stacking_model: {coefficients, intercept, feature_names} for configs 2,3.

    Returns:
        List of per-match result dicts with p_h, p_d, p_a, outcome, rps, etc.
    """
    state = {
        "ratings": {},
        "xg_for": {},
        "xg_against": {},
        "last_match_dt": {},  # team_id → datetime of last match (for rest hours)
    }

    if config_id in (1, 10, 2, 3):
        state["dc_history"] = []
        state["dc_params"] = None
        state["dc_last_fit_count"] = 0

    if config_id in (11, 2, 3):
        state["dc_xg_history"] = []
        state["dc_xg_params"] = None
        state["dc_xg_last_fit_count"] = 0

    if odds_map is None:
        odds_map = {}

    results = []
    use_rest = (config_id == 10)

    for idx, match in enumerate(fixtures):
        gh = match.get("goals_home")
        ga = match.get("goals_away")
        if gh is None or ga is None:
            continue

        outcome = compute_outcome(int(gh), int(ga))

        # --- Prediction phase (before observing result) ---
        if idx >= warmup:
            if config_id == 0:
                p_h, p_d, p_a = _predict_baseline(match, state)

            elif config_id in (1, 10):
                # Try DC, fallback to baseline
                _maybe_refit_dc(state, match["match_date"])
                dc_probs = _predict_dc(match, state)
                if dc_probs is not None:
                    p_h, p_d, p_a = dc_probs

                    # Config 10: apply fatigue adjustment to DC lambda/mu
                    if use_rest:
                        h = match["home_team_id"]
                        a = match["away_team_id"]
                        md = match["match_date"]
                        if isinstance(md, str):
                            md = datetime.strptime(md[:19], "%Y-%m-%d %H:%M:%S" if len(md) > 10 else "%Y-%m-%d")

                        h_rest = None
                        a_rest = None
                        h_last = state["last_match_dt"].get(h)
                        a_last = state["last_match_dt"].get(a)
                        if h_last is not None:
                            delta = md - h_last
                            h_rest = delta.total_seconds() / 3600.0
                        if a_last is not None:
                            delta = md - a_last
                            a_rest = delta.total_seconds() / 3600.0

                        h_fatigue = _fatigue_factor(h_rest)
                        a_fatigue = _fatigue_factor(a_rest)

                        if h_fatigue != 1.0 or a_fatigue != 1.0:
                            dc_params = state.get("dc_params")
                            if dc_params is not None:
                                att_h = dc_params.attack.get(h)
                                def_h = dc_params.defense.get(h)
                                att_a = dc_params.attack.get(a)
                                def_a = dc_params.defense.get(a)
                                if all(v is not None for v in (att_h, def_h, att_a, def_a)):
                                    lam, mu = predict_lambda_mu(
                                        att_h, def_h, att_a, def_a,
                                        dc_params.home_advantage,
                                    )
                                    lam = max(0.01, min(10.0, lam * h_fatigue))
                                    mu = max(0.01, min(10.0, mu * a_fatigue))
                                    p_h, p_d, p_a = _match_probs_dc(lam, mu, dc_params.rho)
                else:
                    p_h, p_d, p_a = _predict_baseline(match, state)

            elif config_id == 11:
                # DC-xG: fit on xG, rho=0
                _maybe_refit_dc_xg(state, match["match_date"])
                dc_xg_probs = _predict_dc_xg(match, state)
                if dc_xg_probs is not None:
                    p_h, p_d, p_a = dc_xg_probs
                else:
                    p_h, p_d, p_a = _predict_baseline(match, state)

            elif config_id in (2, 3):
                # DC + Stacking (and optionally Dirichlet for config 3, applied post-hoc)
                _maybe_refit_dc(state, match["match_date"])
                _maybe_refit_dc_xg(state, match["match_date"])

                # Get DC probs (goals + xG)
                dc_probs = _predict_dc(match, state)
                dc_xg_probs = _predict_dc_xg(match, state)
                # Get Poisson probs
                pois_probs = _predict_baseline(match, state)
                # Elo diff
                h = match["home_team_id"]
                a = match["away_team_id"]
                elo_h = state["ratings"].get(h, DEFAULT_ELO)
                elo_a = state["ratings"].get(a, DEFAULT_ELO)
                elo_diff = elo_h - elo_a
                # Fair odds
                fid = match["fixture_id"]
                odds = odds_map.get(fid, {})

                if dc_probs is not None and stacking_model is not None:
                    # DC-xG: fallback to DC-goals if unavailable
                    dc_xg = dc_xg_probs if dc_xg_probs is not None else dc_probs
                    features = {
                        "p_home_poisson": pois_probs[0],
                        "p_draw_poisson": pois_probs[1],
                        "p_away_poisson": pois_probs[2],
                        "p_home_dc": dc_probs[0],
                        "p_draw_dc": dc_probs[1],
                        "p_away_dc": dc_probs[2],
                        "p_home_dc_xg": dc_xg[0],
                        "p_draw_dc_xg": dc_xg[1],
                        "p_away_dc_xg": dc_xg[2],
                        "elo_diff": elo_diff,
                        "fair_home": odds.get("fair_home", 0.0),
                        "fair_draw": odds.get("fair_draw", 0.0),
                        "fair_away": odds.get("fair_away", 0.0),
                    }
                    p_h, p_d, p_a = _stacking_predict(features, stacking_model)
                elif dc_probs is not None:
                    p_h, p_d, p_a = dc_probs
                else:
                    p_h, p_d, p_a = pois_probs

            else:
                p_h, p_d, p_a = _predict_baseline(match, state)

            rps = _rps(p_h, p_d, p_a, outcome)
            brier = _brier(p_h, p_d, p_a, outcome)
            logloss = _logloss(p_h, p_d, p_a, outcome)

            results.append({
                "fixture_id": match["fixture_id"],
                "league_id": match["league_id"],
                "match_date": str(match["match_date"])[:10],
                "outcome": outcome,
                "p_h": p_h,
                "p_d": p_d,
                "p_a": p_a,
                "rps": rps,
                "brier": brier,
                "logloss": logloss,
            })

        # --- Update phase (observe result) ---
        _update_baseline_state(match, state)

        if config_id in (1, 10, 2, 3):
            state["dc_history"].append(match)
        if config_id in (11, 2, 3):
            state["dc_xg_history"].append(match)

        # Track last match datetime for rest hours
        h = match["home_team_id"]
        a = match["away_team_id"]
        md = match["match_date"]
        if isinstance(md, str):
            md = datetime.strptime(md[:19], "%Y-%m-%d %H:%M:%S" if len(md) > 10 else "%Y-%m-%d")
        state["last_match_dt"][h] = md
        state["last_match_dt"][a] = md

    return results


def apply_dirichlet_calibration(results: list[dict], calib_split: float = 0.7) -> list[dict]:
    """Apply Dirichlet calibration to stacking results (config 3).

    Trains calibrator on first `calib_split` fraction, applies to the rest.
    Returns only the calibrated portion (for fair eval on unseen data).
    """
    from app.services.calibration import DirichletCalibrator

    n = len(results)
    split_idx = int(n * calib_split)
    if split_idx < 50 or n - split_idx < 20:
        log.warning("Config 3: not enough data for Dirichlet split (n=%d, split=%d). Skipping.", n, split_idx)
        return results

    # Train portion
    probs_train = np.array([[r["p_h"], r["p_d"], r["p_a"]] for r in results[:split_idx]])
    labels_train = np.array([r["outcome"] for r in results[:split_idx]])

    calibrator = DirichletCalibrator(reg_lambda=0.01)
    calibrator.fit(probs_train, labels_train)

    log.info("Config 3 Dirichlet: trained on %d, applying to %d. W_diag=[%.3f, %.3f, %.3f]",
             split_idx, n - split_idx,
             calibrator.W[0, 0], calibrator.W[1, 1], calibrator.W[2, 2])

    # Apply to test portion
    probs_test = np.array([[r["p_h"], r["p_d"], r["p_a"]] for r in results[split_idx:]])
    probs_cal = calibrator.calibrate(probs_test)

    calibrated_results = []
    for i, r in enumerate(results[split_idx:]):
        p_h, p_d, p_a = float(probs_cal[i, 0]), float(probs_cal[i, 1]), float(probs_cal[i, 2])
        outcome = r["outcome"]
        calibrated_results.append({
            **r,
            "p_h": p_h,
            "p_d": p_d,
            "p_a": p_a,
            "rps": _rps(p_h, p_d, p_a, outcome),
            "brier": _brier(p_h, p_d, p_a, outcome),
            "logloss": _logloss(p_h, p_d, p_a, outcome),
        })

    return calibrated_results


# ---------------------------------------------------------------------------
# Aggregation & comparison
# ---------------------------------------------------------------------------

CONFIG_NAMES = {
    0: "Baseline (Poisson+xG+Elo)",
    1: "DC Core",
    10: "DC + Rest (fatigue adj)",
    11: "DC-xG Core",
    2: "DC + Stacking",
    3: "Full Pipeline (DC+Stack+Dirichlet)",
}

# Alias map: "1b" -> 10, "1x" -> 11
CONFIG_ALIASES = {"1b": 10, "1x": 11}


def aggregate_metrics(results: list[dict]) -> dict:
    """Compute aggregate metrics from per-match results."""
    if not results:
        return {"n": 0, "rps": None, "brier": None, "logloss": None}

    n = len(results)
    return {
        "n": n,
        "rps": sum(r["rps"] for r in results) / n,
        "brier": sum(r["brier"] for r in results) / n,
        "logloss": sum(r["logloss"] for r in results) / n,
    }


def build_comparison_table(
    all_metrics: dict[int, dict],
) -> list[dict]:
    """Build comparison table with ΔRPS vs baseline."""
    baseline = all_metrics.get(0)
    baseline_rps = baseline["rps"] if baseline and baseline["rps"] is not None else None

    rows = []
    for cfg_id in sorted(all_metrics.keys()):
        m = all_metrics[cfg_id]
        delta_rps = None
        if baseline_rps is not None and m["rps"] is not None:
            delta_rps = m["rps"] - baseline_rps

        rows.append({
            "config_id": cfg_id,
            "config_name": CONFIG_NAMES.get(cfg_id, f"Config {cfg_id}"),
            "n_matches": m["n"],
            "rps": m["rps"],
            "brier": m["brier"],
            "logloss": m["logloss"],
            "delta_rps": delta_rps,
        })

    return rows


def print_comparison_table(rows: list[dict]) -> None:
    """Pretty-print comparison table."""
    header = f"{'Config':<35} {'N':>6} {'RPS':>8} {'Brier':>8} {'LogLoss':>8} {'ΔRPS':>8}"
    log.info("")
    log.info("=" * len(header))
    log.info("ABLATION STUDY RESULTS")
    log.info("=" * len(header))
    log.info(header)
    log.info("-" * len(header))

    for row in rows:
        rps_s = f"{row['rps']:.4f}" if row["rps"] is not None else "N/A"
        brier_s = f"{row['brier']:.4f}" if row["brier"] is not None else "N/A"
        ll_s = f"{row['logloss']:.4f}" if row["logloss"] is not None else "N/A"
        delta_s = f"{row['delta_rps']:+.4f}" if row["delta_rps"] is not None else "—"
        log.info(f"{row['config_name']:<35} {row['n_matches']:>6} {rps_s:>8} {brier_s:>8} {ll_s:>8} {delta_s:>8}")

    log.info("=" * len(header))
    log.info("Lower RPS/Brier/LogLoss is better. Negative ΔRPS = improvement over baseline.")
    log.info("")


def save_results(rows: list[dict], output_path: str) -> None:
    """Save results to JSON file."""
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w") as f:
        json.dump({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "results": rows,
        }, f, indent=2, default=str)
    log.info("Results saved to %s", output_path)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Ablation study: compare model configurations")
    parser.add_argument("--leagues", help="Comma-separated league IDs")
    parser.add_argument("--from-date", default="2022-01-01")
    parser.add_argument("--to-date", default="2026-02-20")
    parser.add_argument("--configs", default="0,1",
                        help="Comma-separated config IDs to run (default: 0,1)")
    parser.add_argument("--warmup", type=int, default=50,
                        help="Number of warmup matches before scoring (default: 50)")
    parser.add_argument("--output", default="results/ablation_latest.json",
                        help="Output JSON path")
    parser.add_argument("--per-league", action="store_true", default=True,
                        help="Run ablation per league and aggregate (default: true)")
    parser.add_argument("--no-per-league", dest="per_league", action="store_false",
                        help="Run all leagues in single walk-forward (slower for DC)")
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

    config_ids = []
    for x in args.configs.split(","):
        x = x.strip()
        if x in CONFIG_ALIASES:
            config_ids.append(CONFIG_ALIASES[x])
        elif x.isdigit():
            config_ids.append(int(x))
    if not config_ids:
        log.error("No valid config IDs specified")
        sys.exit(1)

    conn = _get_conn(database_url)

    # Load stacking model if needed
    stacking_model_data: dict | None = None
    if any(c in config_ids for c in (2, 3)):
        stacking_model_data = load_stacking_model_sync(conn)
        if stacking_model_data is not None:
            log.info("Stacking model loaded (%d features)", len(stacking_model_data["feature_names"]))
        else:
            log.warning("Stacking model not found in DB! Configs 2,3 will fallback to DC.")

    if args.per_league:
        # Run per-league and aggregate
        all_results: dict[int, dict[int, list[dict]]] = {cfg: {} for cfg in config_ids}
        per_league_metrics: dict[int, dict[int, dict]] = {cfg: {} for cfg in config_ids}

        for lid in leagues:
            log.info("\n===== League %d =====", lid)
            league_fixtures = load_finished_matches(conn, [lid], args.from_date, args.to_date)
            log.info("  Loaded %d fixtures", len(league_fixtures))
            if not league_fixtures:
                continue

            odds_map: dict[int, dict] = {}
            if any(c in config_ids for c in (2, 3)):
                fids = [m["fixture_id"] for m in league_fixtures]
                odds_map = load_hist_odds(conn, fids)
                log.info("  Loaded odds for %d fixtures", len(odds_map))

            for cfg_id in config_ids:
                name = CONFIG_NAMES.get(cfg_id, f"Config {cfg_id}")
                log.info("  Running config %d: %s ...", cfg_id, name)
                results = walk_forward_evaluate(
                    league_fixtures, cfg_id, warmup=args.warmup,
                    odds_map=odds_map, stacking_model=stacking_model_data,
                )
                if cfg_id == 3:
                    results = apply_dirichlet_calibration(results)

                all_results[cfg_id][lid] = results
                metrics = aggregate_metrics(results)
                per_league_metrics[cfg_id][lid] = metrics
                log.info("    Config %d: %d matches, RPS=%.4f",
                         cfg_id, metrics["n"], metrics["rps"] or 0)

        conn.close()

        # Aggregate across leagues (weighted by N)
        all_metrics: dict[int, dict] = {}
        for cfg_id in config_ids:
            combined_results = []
            for lid in leagues:
                combined_results.extend(all_results[cfg_id].get(lid, []))
            all_metrics[cfg_id] = aggregate_metrics(combined_results)

        # Print per-league table
        log.info("\n")
        log.info("=" * 80)
        log.info("PER-LEAGUE RESULTS")
        log.info("=" * 80)
        header = f"{'League':<10} {'Config':<35} {'N':>6} {'RPS':>8} {'ΔRPS':>8}"
        log.info(header)
        log.info("-" * len(header))
        for lid in leagues:
            baseline_rps = per_league_metrics.get(0, {}).get(lid, {}).get("rps")
            for cfg_id in config_ids:
                m = per_league_metrics.get(cfg_id, {}).get(lid, {})
                if not m or m.get("n", 0) == 0:
                    continue
                delta = (m["rps"] - baseline_rps) if baseline_rps is not None and m["rps"] is not None else None
                delta_s = f"{delta:+.4f}" if delta is not None else "—"
                log.info(f"  {lid:<8} {CONFIG_NAMES.get(cfg_id, str(cfg_id)):<35} {m['n']:>6} {m['rps']:.4f} {delta_s:>8}")
            log.info("")

    else:
        # Original: all leagues together
        log.info("Loading fixtures for leagues %s, %s to %s", leagues, args.from_date, args.to_date)
        fixtures = load_finished_matches(conn, leagues, args.from_date, args.to_date)
        log.info("Loaded %d fixtures", len(fixtures))

        odds_map = {}
        if any(c in config_ids for c in (2, 3)):
            fids = [m["fixture_id"] for m in fixtures]
            odds_map = load_hist_odds(conn, fids)
            log.info("Loaded odds for %d fixtures", len(odds_map))

        conn.close()

        if not fixtures:
            log.error("No fixtures found")
            sys.exit(1)

        all_metrics = {}
        for cfg_id in config_ids:
            name = CONFIG_NAMES.get(cfg_id, f"Config {cfg_id}")
            log.info("\nRunning config %d: %s ...", cfg_id, name)
            results = walk_forward_evaluate(
                fixtures, cfg_id, warmup=args.warmup,
                odds_map=odds_map, stacking_model=stacking_model_data,
            )
            if cfg_id == 3:
                results = apply_dirichlet_calibration(results)
            metrics = aggregate_metrics(results)
            all_metrics[cfg_id] = metrics
            log.info("  Config %d: %d scored matches, RPS=%.4f, Brier=%.4f, LogLoss=%.4f",
                     cfg_id, metrics["n"],
                     metrics["rps"] or 0, metrics["brier"] or 0, metrics["logloss"] or 0)

    # Global comparison table
    rows = build_comparison_table(all_metrics)
    print_comparison_table(rows)

    # Save
    save_results(rows, args.output)


if __name__ == "__main__":
    main()
