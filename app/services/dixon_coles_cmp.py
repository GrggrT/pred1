"""
CMP-Dixon-Coles model: enhanced Dixon-Coles with COM-Poisson marginals.

Improvements over standard DC:
1. COM-Poisson marginals (nu > 1 = underdispersion for goals)
2. nu as function of competitive balance
3. Team-specific home advantage (hierarchical, regularized to mean)
4. Extended tau correction for scores up to (2,2)

This module provides fit_cmp_dixon_coles() which returns CMPDCParams.
The existing fit_dixon_coles() remains as fallback.
"""

from __future__ import annotations

import math
import time
from datetime import date
from typing import NamedTuple

import numpy as np
from scipy.optimize import minimize

from app.core.logger import get_logger
from app.services.com_poisson import (
    log_Z,
    _extended_tau_vectorized,
    _log_factorial,
)

log = get_logger("services.dixon_coles_cmp")

# Precompute log-factorials
_LOG_FACT = np.array([math.lgamma(k + 1) for k in range(16)])


class CMPDCParams(NamedTuple):
    """Result of CMP-Dixon-Coles fitting."""
    attack: dict[int, float]       # team_id -> att_i
    defense: dict[int, float]      # team_id -> def_i
    home_advantage: float          # global HA (mean)
    team_ha: dict[int, float]      # team_id -> team-specific HA
    rho: float                     # correlation parameter
    nu0: float                     # base dispersion
    nu1: float                     # competitive-balance sensitivity
    xi: float                      # time-decay rate
    log_likelihood: float
    n_matches: int
    n_teams: int


class MatchData(NamedTuple):
    """Input data for a single match."""
    home_id: int
    away_id: int
    home_goals: int
    away_goals: int
    date: date
    home_xg: float | None = None
    away_xg: float | None = None


# ── Parameter unpacking ─────────────────────────────────────────────────

def _unpack_params(
    params_flat: np.ndarray,
    n_teams: int,
    fit_team_ha: bool = True,
) -> tuple[np.ndarray, np.ndarray, float, np.ndarray, float, float]:
    """Unpack flat parameter vector.

    Layout (with team HA):
      [att_0..att_{N-2}, def_0..def_{N-2}, HA_global,
       ha_team_0..ha_team_{N-1}, nu0, nu1]

    Layout (without team HA):
      [att_0..att_{N-2}, def_0..def_{N-2}, HA_global, nu0, nu1]

    Returns: (att, def_, ha_global, ha_team, nu0, nu1)
    """
    n_free = n_teams - 1
    att_free = params_flat[:n_free]
    def_free = params_flat[n_free:2 * n_free]
    ha_global = params_flat[2 * n_free]

    att = np.empty(n_teams)
    att[:n_free] = att_free
    att[n_free] = -att_free.sum()

    def_ = np.empty(n_teams)
    def_[:n_free] = def_free
    def_[n_free] = -def_free.sum()

    if fit_team_ha:
        ha_team = params_flat[2 * n_free + 1: 2 * n_free + 1 + n_teams]
        nu0 = params_flat[2 * n_free + 1 + n_teams]
        nu1 = params_flat[2 * n_free + 2 + n_teams]
    else:
        ha_team = np.zeros(n_teams)
        nu0 = params_flat[2 * n_free + 1]
        nu1 = params_flat[2 * n_free + 2]

    return att, def_, float(ha_global), ha_team, float(nu0), float(nu1)


# ── Negative log-likelihood ─────────────────────────────────────────────

def _neg_log_likelihood_cmp(
    params_flat: np.ndarray,
    home_idx: np.ndarray,
    away_idx: np.ndarray,
    home_goals: np.ndarray,
    away_goals: np.ndarray,
    weights: np.ndarray,
    rho: float,
    n_teams: int,
    log_fact_home: np.ndarray,
    log_fact_away: np.ndarray,
    fit_team_ha: bool,
    sigma_ha: float,   # regularization for team-specific HA
    sigma_nu: float,   # regularization for nu params
) -> float:
    """Negative weighted log-likelihood for CMP-DC model."""
    att, def_, ha_global, ha_team, nu0, nu1 = _unpack_params(
        params_flat, n_teams, fit_team_ha,
    )

    # Effective HA per match = global HA + team-specific HA for home team
    if fit_team_ha:
        effective_ha = ha_global + ha_team[home_idx]
    else:
        effective_ha = ha_global

    # Expected goals (log-space)
    log_lam = effective_ha + att[home_idx] + def_[away_idx]
    log_mu = att[away_idx] + def_[home_idx]

    # Clamp
    log_lam = np.clip(log_lam, np.log(0.01), np.log(10.0))
    log_mu = np.clip(log_mu, np.log(0.01), np.log(10.0))

    lam = np.exp(log_lam)
    mu = np.exp(log_mu)

    # Competitive-balance dispersion: nu = nu0 + nu1 * |strength_diff|
    strength_diff = np.abs(
        (att[home_idx] + def_[away_idx]) - (att[away_idx] + def_[home_idx])
    )
    nu = np.clip(nu0 + nu1 * strength_diff, 0.8, 2.0)

    # CMP log-probability (per-match)
    # log P(x) = x*log(lam) - nu*log(x!) - log(Z(lam, nu))
    log_p_home = home_goals * log_lam - nu * log_fact_home
    log_p_away = away_goals * log_mu - nu * log_fact_away

    # Normalizing constants (per-match, depends on lam AND nu)
    log_z_home = np.array([log_Z(l, n) for l, n in zip(lam, nu)])
    log_z_away = np.array([log_Z(m, n) for m, n in zip(mu, nu)])

    log_p = log_p_home - log_z_home + log_p_away - log_z_away

    # Extended tau correction
    tau = _extended_tau_vectorized(home_goals, away_goals, lam, mu, rho)
    tau = np.clip(tau, 1e-8, None)
    log_p += np.log(tau)

    nll = -np.sum(weights * log_p)

    # Regularization: penalize team-HA deviation from 0 (hierarchical prior)
    if fit_team_ha and sigma_ha > 0:
        nll += np.sum(ha_team ** 2) / (2 * sigma_ha ** 2)

    # Regularization: soft prior on nu0 ~ N(1.1, sigma_nu) and nu1 ~ N(0.1, sigma_nu)
    if sigma_nu > 0:
        nll += (nu0 - 1.1) ** 2 / (2 * sigma_nu ** 2)
        nll += (nu1 - 0.1) ** 2 / (2 * sigma_nu ** 2)

    return float(nll)


# ── Main fitting function ───────────────────────────────────────────────

def fit_cmp_dixon_coles(
    matches: list[MatchData],
    ref_date: date,
    xi: float = 0.005,
    rho_grid_steps: int = 31,
    fit_team_ha: bool = True,
    sigma_ha: float = 0.15,
    sigma_nu: float = 0.3,
) -> CMPDCParams:
    """Fit CMP-Dixon-Coles model on historical matches.

    Args:
        matches: Completed matches.
        ref_date: Reference date for time-decay.
        xi: Time-decay rate.
        rho_grid_steps: Grid search steps for rho in [-0.35, 0.35].
        fit_team_ha: If True, estimate per-team home advantage.
        sigma_ha: Regularization strength for team HA (smaller = stronger).
        sigma_nu: Regularization strength for nu parameters.

    Returns:
        CMPDCParams with optimal parameters including nu and team HA.
    """
    if not matches:
        raise ValueError("No matches provided for CMP-DC fitting")

    valid = [m for m in matches if m.date < ref_date]
    if len(valid) < 20:
        raise ValueError(f"Too few matches ({len(valid)}) for CMP-DC fitting; need >= 20")

    # Build team index mapping
    team_ids = sorted({m.home_id for m in valid} | {m.away_id for m in valid})
    team_to_idx = {tid: i for i, tid in enumerate(team_ids)}
    n_teams = len(team_ids)

    if n_teams < 4:
        raise ValueError(f"Too few teams ({n_teams}); need >= 4")

    n = len(valid)
    home_idx = np.array([team_to_idx[m.home_id] for m in valid], dtype=np.int32)
    away_idx = np.array([team_to_idx[m.away_id] for m in valid], dtype=np.int32)
    hg = np.array([m.home_goals for m in valid], dtype=np.float64)
    ag = np.array([m.away_goals for m in valid], dtype=np.float64)

    # Time-decay weights
    days_ago = np.array([(ref_date - m.date).days for m in valid], dtype=np.float64)
    weights = np.exp(-xi * days_ago)

    # Log-factorials
    log_fact_home = np.array([_log_factorial(int(g)) for g in hg])
    log_fact_away = np.array([_log_factorial(int(g)) for g in ag])

    # Parameter vector layout
    n_free = n_teams - 1
    if fit_team_ha:
        # att(N-1) + def(N-1) + HA_global + ha_team(N) + nu0 + nu1
        n_params = 2 * n_free + 1 + n_teams + 2
    else:
        # att(N-1) + def(N-1) + HA_global + nu0 + nu1
        n_params = 2 * n_free + 1 + 2

    x0 = np.zeros(n_params)
    x0[2 * n_free] = 0.25          # HA_global initial
    if fit_team_ha:
        # team HA: start at 0 (deviation from global)
        x0[2 * n_free + 1 + n_teams] = 1.1    # nu0 initial
        x0[2 * n_free + 2 + n_teams] = 0.1    # nu1 initial
    else:
        x0[2 * n_free + 1] = 1.1   # nu0 initial
        x0[2 * n_free + 2] = 0.1   # nu1 initial

    # Bounds: att/def unbounded, HA [-1, 1], team_ha [-0.5, 0.5], nu0 [0.8, 2.0], nu1 [0, 0.5]
    bounds = [(-2.0, 2.0)] * n_free  # attack
    bounds += [(-2.0, 2.0)] * n_free  # defense
    bounds += [(-1.0, 1.0)]            # HA_global
    if fit_team_ha:
        bounds += [(-0.5, 0.5)] * n_teams  # team HA deviations
    bounds += [(0.8, 2.0)]   # nu0
    bounds += [(0.0, 0.5)]   # nu1

    t_start = time.monotonic()

    # Grid search over rho
    rho_values = np.linspace(-0.30, 0.30, rho_grid_steps)
    best_nll = float("inf")
    best_params = x0.copy()
    best_rho = 0.0

    for rho_candidate in rho_values:
        try:
            result = minimize(
                _neg_log_likelihood_cmp,
                x0,
                args=(
                    home_idx, away_idx, hg, ag, weights,
                    float(rho_candidate), n_teams,
                    log_fact_home, log_fact_away,
                    fit_team_ha, sigma_ha, sigma_nu,
                ),
                method="L-BFGS-B",
                bounds=bounds,
                options={"maxiter": 500, "ftol": 1e-8},
            )
        except Exception as exc:
            log.debug("CMP-DC rho=%.3f failed: %s", rho_candidate, exc)
            continue

        if result.fun < best_nll:
            best_nll = result.fun
            best_params = result.x.copy()
            best_rho = float(rho_candidate)
            x0 = result.x.copy()

    fit_time = time.monotonic() - t_start

    # Unpack best parameters
    att, def_, ha_global, ha_team, nu0, nu1 = _unpack_params(
        best_params, n_teams, fit_team_ha,
    )

    attack = {team_ids[i]: float(att[i]) for i in range(n_teams)}
    defense = {team_ids[i]: float(def_[i]) for i in range(n_teams)}
    team_ha_dict = {team_ids[i]: float(ha_team[i]) for i in range(n_teams)}

    log.info(
        "fit_cmp_dc done n=%d teams=%d rho=%.4f HA=%.4f nu0=%.3f nu1=%.3f "
        "nll=%.2f time=%.1fs",
        n, n_teams, best_rho, ha_global, nu0, nu1, best_nll, fit_time,
    )

    return CMPDCParams(
        attack=attack,
        defense=defense,
        home_advantage=float(ha_global),
        team_ha=team_ha_dict,
        rho=best_rho,
        nu0=float(nu0),
        nu1=float(nu1),
        xi=xi,
        log_likelihood=-best_nll,
        n_matches=n,
        n_teams=n_teams,
    )
