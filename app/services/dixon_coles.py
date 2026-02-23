"""
Dixon-Coles model for football match prediction.

Estimates latent attack/defense parameters per team via weighted
maximum likelihood with time-decay, solving strength-of-schedule
confounding that plagues rolling-average approaches.

Reference: Dixon & Coles (1997), "Modelling Association Football
Scores and Inefficiencies in the Football Betting Market"
"""

from __future__ import annotations

import math
import time
from datetime import date
from typing import NamedTuple

import numpy as np
from scipy.optimize import minimize

from app.core.logger import get_logger

log = get_logger("services.dixon_coles")

# Precompute log-factorials for goals 0..15 (matches rarely exceed this)
_LOG_FACT = np.array([math.lgamma(k + 1) for k in range(16)])


def _log_factorial(k: int) -> float:
    """Log-factorial with precomputed cache for common values."""
    if k < len(_LOG_FACT):
        return _LOG_FACT[k]
    return math.lgamma(k + 1)


class DCParams(NamedTuple):
    """Result of Dixon-Coles fitting."""
    attack: dict[int, float]      # team_id -> att_i
    defense: dict[int, float]     # team_id -> def_i
    home_advantage: float         # HA
    rho: float                    # rho
    xi: float                     # xi (time-decay rate used)
    log_likelihood: float         # final log-likelihood
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


def tau_value(x: int, y: int, lam: float, mu: float, rho: float) -> float:
    """Dixon-Coles tau correction factor for low-scoring matches.

    Adjusts joint probability of (x, y) goals to account for
    dependency between home and away scores at 0-0, 0-1, 1-0, 1-1.
    """
    if x == 0 and y == 0:
        return 1.0 - lam * mu * rho
    if x == 0 and y == 1:
        return 1.0 + lam * rho
    if x == 1 and y == 0:
        return 1.0 + mu * rho
    if x == 1 and y == 1:
        return 1.0 - rho
    return 1.0


def _unpack_params(
    params_flat: np.ndarray,
    n_teams: int,
) -> tuple[np.ndarray, np.ndarray, float]:
    """Unpack flat parameter vector into attack, defense, HA.

    Layout: [att_0..att_{N-2}, def_0..def_{N-2}, HA]
    Last team params = -sum(others) for sum-to-zero constraint.
    """
    n_free = n_teams - 1
    att_free = params_flat[:n_free]
    def_free = params_flat[n_free:2 * n_free]
    ha = params_flat[2 * n_free]

    att = np.empty(n_teams)
    att[:n_free] = att_free
    att[n_free] = -att_free.sum()

    def_ = np.empty(n_teams)
    def_[:n_free] = def_free
    def_[n_free] = -def_free.sum()

    return att, def_, float(ha)


def _neg_log_likelihood(
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
) -> float:
    """Negative weighted log-likelihood of the Dixon-Coles model."""
    att, def_, ha = _unpack_params(params_flat, n_teams)

    # Vectorized lambda/mu computation
    log_lam = ha + att[home_idx] + def_[away_idx]
    log_mu = att[away_idx] + def_[home_idx]

    # Clamp to avoid numerical issues
    log_lam = np.clip(log_lam, np.log(0.01), np.log(10.0))
    log_mu = np.clip(log_mu, np.log(0.01), np.log(10.0))

    lam = np.exp(log_lam)
    mu = np.exp(log_mu)

    # Poisson log-probability (vectorized)
    log_p = (home_goals * log_lam - lam - log_fact_home
             + away_goals * log_mu - mu - log_fact_away)

    # Tau correction (only for low scores)
    tau = np.ones(len(home_goals))
    m00 = (home_goals == 0) & (away_goals == 0)
    m01 = (home_goals == 0) & (away_goals == 1)
    m10 = (home_goals == 1) & (away_goals == 0)
    m11 = (home_goals == 1) & (away_goals == 1)

    tau[m00] = 1.0 - lam[m00] * mu[m00] * rho
    tau[m01] = 1.0 + lam[m01] * rho
    tau[m10] = 1.0 + mu[m10] * rho
    tau[m11] = 1.0 - rho

    # Clamp tau to avoid log(0)
    tau = np.clip(tau, 1e-6, None)

    log_tau = np.log(tau)
    nll = -np.sum(weights * (log_p + log_tau))

    return float(nll)


def _neg_log_likelihood_xg(
    params_flat: np.ndarray,
    home_idx: np.ndarray,
    away_idx: np.ndarray,
    home_xg: np.ndarray,
    away_xg: np.ndarray,
    weights: np.ndarray,
    n_teams: int,
) -> float:
    """Negative weighted quasi-Poisson log-likelihood for xG mode.

    Uses y*log(λ) - λ without log-factorial (undefined for fractional y)
    and without rho/tau correction (meaningless for non-integer scores).
    """
    att, def_, ha = _unpack_params(params_flat, n_teams)

    log_lam = ha + att[home_idx] + def_[away_idx]
    log_mu = att[away_idx] + def_[home_idx]

    log_lam = np.clip(log_lam, np.log(0.01), np.log(10.0))
    log_mu = np.clip(log_mu, np.log(0.01), np.log(10.0))

    lam = np.exp(log_lam)
    mu = np.exp(log_mu)

    # Quasi-Poisson kernel: y*log(λ) - λ  (no log-factorial, no tau)
    log_p = (home_xg * log_lam - lam
             + away_xg * log_mu - mu)

    nll = -np.sum(weights * log_p)
    return float(nll)


def fit_dixon_coles(
    matches: list[MatchData],
    ref_date: date,
    xi: float = 0.005,
    rho_grid_steps: int = 71,
    use_xg: bool = False,
) -> DCParams:
    """Fit Dixon-Coles model on historical matches.

    Args:
        matches: Completed matches (only with date < ref_date used).
        ref_date: Reference date for time-decay weight calculation.
        xi: Decay rate (days^-1). 0.005 ~ half-life of ~140 days.
        rho_grid_steps: Number of grid search steps for rho in [-0.35, 0.35].
        use_xg: If True, fit on xG values using quasi-Poisson kernel
                 (no rho/tau, no grid search). Matches without xG are skipped.

    Returns:
        DCParams with optimal parameters.
    """
    mode = "xG" if use_xg else "goals"

    if not matches:
        raise ValueError("No matches provided for fitting")

    # Filter to matches before ref_date
    valid = [m for m in matches if m.date < ref_date]

    # In xG mode, additionally filter out matches without xG data
    if use_xg:
        valid = [m for m in valid if m.home_xg is not None and m.away_xg is not None]

    if len(valid) < 10:
        raise ValueError(f"Too few matches ({len(valid)}) for fitting (mode={mode}); need at least 10")

    # Build team index mapping
    team_ids = sorted({m.home_id for m in valid} | {m.away_id for m in valid})
    team_to_idx = {tid: i for i, tid in enumerate(team_ids)}
    n_teams = len(team_ids)

    if n_teams < 4:
        raise ValueError(f"Too few teams ({n_teams}) for fitting; need at least 4")

    # Prepare arrays
    n = len(valid)
    home_idx = np.array([team_to_idx[m.home_id] for m in valid], dtype=np.int32)
    away_idx = np.array([team_to_idx[m.away_id] for m in valid], dtype=np.int32)

    if use_xg:
        hg = np.array([m.home_xg for m in valid], dtype=np.float64)
        ag = np.array([m.away_xg for m in valid], dtype=np.float64)
    else:
        hg = np.array([m.home_goals for m in valid], dtype=np.float64)
        ag = np.array([m.away_goals for m in valid], dtype=np.float64)

    # Time-decay weights
    days_ago = np.array([(ref_date - m.date).days for m in valid], dtype=np.float64)
    weights = np.exp(-xi * days_ago)

    # Initial parameters
    n_free = n_teams - 1
    n_params = 2 * n_free + 1  # att(N-1) + def(N-1) + HA
    x0 = np.zeros(n_params)
    x0[-1] = 0.25  # HA initial ~ exp(0.25) ~ 1.28

    t_start = time.monotonic()

    if use_xg:
        # xG mode: single optimization, rho=0 (tau undefined for fractional scores)
        opt_args = (home_idx, away_idx, hg, ag, weights, n_teams)
        result = minimize(
            _neg_log_likelihood_xg,
            x0,
            args=opt_args,
            method="L-BFGS-B",
            options={"maxiter": 500, "ftol": 1e-8},
        )
        best_nll = result.fun
        best_params = result.x.copy()
        best_rho = 0.0
    else:
        # Goals mode: grid search over rho with tau correction
        # Precompute log-factorials
        log_fact_home = np.array([_log_factorial(int(g)) for g in hg])
        log_fact_away = np.array([_log_factorial(int(g)) for g in ag])

        rho_values = np.linspace(-0.35, 0.35, rho_grid_steps)
        best_nll = float("inf")
        best_params = x0.copy()
        best_rho = 0.0

        for rho_candidate in rho_values:
            opt_args = (home_idx, away_idx, hg, ag, weights, float(rho_candidate),
                        n_teams, log_fact_home, log_fact_away)

            result = minimize(
                _neg_log_likelihood,
                x0,
                args=opt_args,
                method="L-BFGS-B",
                options={"maxiter": 500, "ftol": 1e-8},
            )

            if result.fun < best_nll:
                best_nll = result.fun
                best_params = result.x.copy()
                best_rho = float(rho_candidate)
                # Warm-start next iteration from current best
                x0 = result.x.copy()

    fit_time = time.monotonic() - t_start

    # Unpack best parameters
    att, def_, ha = _unpack_params(best_params, n_teams)

    attack = {team_ids[i]: float(att[i]) for i in range(n_teams)}
    defense = {team_ids[i]: float(def_[i]) for i in range(n_teams)}

    log.info(
        "fit_dixon_coles done mode=%s n_matches=%d n_teams=%d rho=%.4f HA=%.4f "
        "nll=%.2f time=%.1fs",
        mode, n, n_teams, best_rho, ha, best_nll, fit_time,
    )

    return DCParams(
        attack=attack,
        defense=defense,
        home_advantage=float(ha),
        rho=best_rho,
        xi=xi,
        log_likelihood=-best_nll,
        n_matches=n,
        n_teams=n_teams,
    )


def predict_lambda_mu(
    att_home: float,
    def_home: float,
    att_away: float,
    def_away: float,
    home_advantage: float,
) -> tuple[float, float]:
    """Compute expected goals (lambda, mu) from DC parameters.

    Returns:
        (lambda_home, mu_away) - expected goals for home and away.
    """
    lam = math.exp(home_advantage + att_home + def_away)
    mu = math.exp(att_away + def_home)
    return lam, mu


def tune_xi(
    matches: list[MatchData],
    ref_date: date,
    xi_range: tuple[float, float, float] = (0.001, 0.012, 0.001),
) -> tuple[float, float]:
    """Find optimal xi via walk-forward validation.

    Splits matches into train (first 70%) and validation (last 30%).
    For each xi: fit on train, evaluate log-loss on validation.

    Args:
        matches: All available matches.
        ref_date: Reference date.
        xi_range: (start, stop, step) for xi grid.

    Returns:
        (best_xi, best_log_loss)
    """
    valid = sorted([m for m in matches if m.date < ref_date], key=lambda m: m.date)
    if len(valid) < 50:
        log.warning("tune_xi: too few matches (%d), returning default xi=0.005", len(valid))
        return 0.005, float("inf")

    split_idx = int(len(valid) * 0.7)
    train = valid[:split_idx]
    val = valid[split_idx:]

    if not val:
        return 0.005, float("inf")

    val_ref = val[-1].date
    xi_start, xi_stop, xi_step = xi_range
    xi_values = np.arange(xi_start, xi_stop + xi_step / 2, xi_step)

    best_xi = 0.005
    best_loss = float("inf")

    for xi_candidate in xi_values:
        try:
            params = fit_dixon_coles(
                train, ref_date=val[0].date, xi=float(xi_candidate), rho_grid_steps=11,
            )
        except ValueError:
            continue

        # Evaluate on validation set
        total_loss = 0.0
        count = 0
        for m in val:
            att_h = params.attack.get(m.home_id)
            att_a = params.attack.get(m.away_id)
            def_h = params.defense.get(m.home_id)
            def_a = params.defense.get(m.away_id)
            if att_h is None or att_a is None or def_h is None or def_a is None:
                continue

            lam, mu = predict_lambda_mu(att_h, def_h, att_a, def_a, params.home_advantage)
            lam = max(0.01, min(10.0, lam))
            mu = max(0.01, min(10.0, mu))

            # Compute 1X2 probabilities via Poisson + tau
            p_home = 0.0
            p_draw = 0.0
            p_away = 0.0
            for i in range(8):
                for j in range(8):
                    p_ij = (math.exp(i * math.log(lam) - lam - _log_factorial(i)
                                     + j * math.log(mu) - mu - _log_factorial(j))
                            * tau_value(i, j, lam, mu, params.rho))
                    if i > j:
                        p_home += p_ij
                    elif i == j:
                        p_draw += p_ij
                    else:
                        p_away += p_ij

            total_p = p_home + p_draw + p_away
            if total_p <= 0:
                continue
            p_home /= total_p
            p_draw /= total_p
            p_away /= total_p

            # Actual outcome
            if m.home_goals > m.away_goals:
                p_actual = max(p_home, 1e-15)
            elif m.home_goals == m.away_goals:
                p_actual = max(p_draw, 1e-15)
            else:
                p_actual = max(p_away, 1e-15)

            total_loss -= math.log(p_actual)
            count += 1

        if count > 0:
            avg_loss = total_loss / count
            if avg_loss < best_loss:
                best_loss = avg_loss
                best_xi = float(xi_candidate)

    log.info("tune_xi best_xi=%.4f best_logloss=%.4f", best_xi, best_loss)
    return best_xi, best_loss
