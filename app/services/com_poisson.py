"""
Conway-Maxwell-Poisson (COM-Poisson / CMP) distribution.

The CMP distribution generalizes Poisson with a dispersion parameter nu:
  P(X=k) = lambda^k / (k!)^nu / Z(lambda, nu)

where Z(lambda, nu) = sum_{j=0}^{inf} lambda^j / (j!)^nu

- nu = 1: standard Poisson
- nu > 1: underdispersed (variance < mean) -- goals after conditioning on team strengths
- nu < 1: overdispersed (variance > mean)

Reference: Shmueli, Minka, Kadane, Borle, Boatwright (2005)
           "A useful distribution for fitting discrete data: revival of the CMP"
           Florez et al. (JQAS 2025): goals are underdispersed with nu ~ 1.1-1.3
"""

from __future__ import annotations

import math
from decimal import Decimal
from functools import lru_cache
from typing import Tuple

import numpy as np

from app.core.decimalutils import D, q_prob
from app.core.logger import get_logger

log = get_logger("services.com_poisson")

# ── Normalizing constant Z(lambda, nu) ─────────────────────────────────

# Maximum number of terms for truncating the infinite series
_TRUNC_MAX = 30  # Goals > 20 practically impossible in football

# Precompute log-factorials
_LOG_FACT = np.array([math.lgamma(k + 1) for k in range(_TRUNC_MAX + 1)])


def _log_factorial(k: int) -> float:
    k = int(k)
    if k < len(_LOG_FACT):
        return float(_LOG_FACT[k])
    return math.lgamma(k + 1)


def log_Z(lam: float, nu: float, trunc: int = _TRUNC_MAX) -> float:
    """Compute log of the normalizing constant Z(lambda, nu).

    Uses log-sum-exp for numerical stability:
      log Z = log sum_j exp(j * log(lam) - nu * log(j!))
    """
    if lam <= 0:
        return 0.0  # Z = 1 when lambda = 0

    log_lam = math.log(max(lam, 1e-15))
    terms = np.empty(trunc + 1)
    for j in range(trunc + 1):
        terms[j] = j * log_lam - nu * _log_factorial(j)

    # Log-sum-exp
    max_term = terms.max()
    return max_term + math.log(np.sum(np.exp(terms - max_term)))


def cmp_pmf(k: int, lam: float, nu: float) -> float:
    """COM-Poisson PMF: P(X=k) = lam^k / (k!)^nu / Z(lam, nu)."""
    k = int(k)
    if k < 0:
        return 0.0
    log_p = k * math.log(max(lam, 1e-15)) - nu * _log_factorial(k) - log_Z(lam, nu)
    return math.exp(log_p)


def cmp_pmf_array(lam: float, nu: float, k_max: int = 10) -> np.ndarray:
    """Compute CMP PMF for k=0..k_max as numpy array."""
    log_lam = math.log(max(lam, 1e-15))
    log_z = log_Z(lam, nu, trunc=max(k_max + 10, _TRUNC_MAX))
    log_probs = np.array([
        k * log_lam - nu * _log_factorial(k) - log_z
        for k in range(k_max + 1)
    ])
    return np.exp(log_probs)


# ── Vectorized CMP log-likelihood for fitting ──────────────────────────

def cmp_nll_vectorized(
    log_lam_home: np.ndarray,
    log_lam_away: np.ndarray,
    nu: float,
    home_goals: np.ndarray,
    away_goals: np.ndarray,
    weights: np.ndarray,
    log_fact_home: np.ndarray,
    log_fact_away: np.ndarray,
    rho: float,
) -> float:
    """Vectorized negative log-likelihood for CMP-DC model.

    log P(x,y) = x*log(lam_h) - nu*log(x!) - log(Z(lam_h,nu))
                + y*log(lam_a) - nu*log(y!) - log(Z(lam_a,nu))
                + log(tau(x,y,lam_h,lam_a,rho))
    """
    lam_h = np.exp(log_lam_home)
    lam_a = np.exp(log_lam_away)

    # Log-probabilities for home/away marginals
    log_p_home = home_goals * log_lam_home - nu * log_fact_home
    log_p_away = away_goals * log_lam_away - nu * log_fact_away

    # Normalizing constants (per-match)
    log_z_home = np.array([log_Z(l, nu) for l in lam_h])
    log_z_away = np.array([log_Z(l, nu) for l in lam_a])

    log_p = log_p_home - log_z_home + log_p_away - log_z_away

    # Extended tau correction (Dixon-Coles + Michels et al.)
    tau = _extended_tau_vectorized(home_goals, away_goals, lam_h, lam_a, rho)
    tau = np.clip(tau, 1e-8, None)
    log_p += np.log(tau)

    nll = -np.sum(weights * log_p)
    return float(nll)


# ── Extended tau correction ─────────────────────────────────────────────

def _extended_tau(x: int, y: int, lam: float, mu: float, rho: float) -> float:
    """Extended Dixon-Coles tau correction.

    Standard DC only corrects (0,0), (0,1), (1,0), (1,1).
    Extended version (Michels et al., JRSS-C 2025) adds corrections
    for scores up to (2,2) using the same rho parameter with a decay
    factor for higher scores.
    """
    if x == 0 and y == 0:
        return 1.0 - lam * mu * rho
    if x == 0 and y == 1:
        return 1.0 + lam * rho
    if x == 1 and y == 0:
        return 1.0 + mu * rho
    if x == 1 and y == 1:
        return 1.0 - rho
    # Extended cells with decayed rho (0.25x strength)
    rho2 = rho * 0.25
    if x == 2 and y == 0:
        return 1.0 + mu * rho2
    if x == 0 and y == 2:
        return 1.0 + lam * rho2
    if x == 2 and y == 1:
        return 1.0 - rho2
    if x == 1 and y == 2:
        return 1.0 - rho2
    if x == 2 and y == 2:
        return 1.0 - rho2 * 0.5
    return 1.0


def _extended_tau_vectorized(
    hg: np.ndarray, ag: np.ndarray,
    lam: np.ndarray, mu: np.ndarray,
    rho: float,
) -> np.ndarray:
    """Vectorized extended tau for arrays of match scores."""
    tau = np.ones(len(hg))

    m00 = (hg == 0) & (ag == 0)
    m01 = (hg == 0) & (ag == 1)
    m10 = (hg == 1) & (ag == 0)
    m11 = (hg == 1) & (ag == 1)

    tau[m00] = 1.0 - lam[m00] * mu[m00] * rho
    tau[m01] = 1.0 + lam[m01] * rho
    tau[m10] = 1.0 + mu[m10] * rho
    tau[m11] = 1.0 - rho

    # Extended cells
    rho2 = rho * 0.25
    m20 = (hg == 2) & (ag == 0)
    m02 = (hg == 0) & (ag == 2)
    m21 = (hg == 2) & (ag == 1)
    m12 = (hg == 1) & (ag == 2)
    m22 = (hg == 2) & (ag == 2)

    tau[m20] = 1.0 + mu[m20] * rho2
    tau[m02] = 1.0 + lam[m02] * rho2
    tau[m21] = 1.0 - rho2
    tau[m12] = 1.0 - rho2
    tau[m22] = 1.0 - rho2 * 0.5

    return tau


# ── Match probabilities with CMP ───────────────────────────────────────

def match_probs_cmp(
    lam_home: float,
    lam_away: float,
    nu: float = 1.0,
    rho: float = 0.0,
    k_max: int = 10,
) -> Tuple[Decimal, Decimal, Decimal]:
    """1X2 probabilities using CMP marginals + extended tau.

    This is the CMP equivalent of match_probs_dixon_coles from poisson.py.
    When nu=1, this reduces to the standard DC model.
    """
    pmf_h = cmp_pmf_array(lam_home, nu, k_max)
    pmf_a = cmp_pmf_array(lam_away, nu, k_max)

    p_home = 0.0
    p_draw = 0.0
    p_away = 0.0

    for i in range(k_max + 1):
        for j in range(k_max + 1):
            tau = _extended_tau(i, j, lam_home, lam_away, rho)
            if tau < 0:
                tau = 0.0
            prob = pmf_h[i] * pmf_a[j] * tau
            if i > j:
                p_home += prob
            elif i == j:
                p_draw += prob
            else:
                p_away += prob

    total = p_home + p_draw + p_away
    if total > 0:
        p_home /= total
        p_draw /= total
        p_away /= total

    return q_prob(D(str(p_home))), q_prob(D(str(p_draw))), q_prob(D(str(p_away)))


def match_probs_cmp_dc(
    lam_home: Decimal,
    lam_away: Decimal,
    nu: float = 1.0,
    rho: Decimal = D("0.0"),
    k_max: int = 10,
) -> Tuple[Decimal, Decimal, Decimal]:
    """Decimal-API wrapper for match_probs_cmp (matches poisson.py interface)."""
    return match_probs_cmp(
        float(lam_home), float(lam_away),
        nu=nu, rho=float(rho), k_max=k_max,
    )


# ── Competitive-balance nu function ────────────────────────────────────

def nu_from_balance(
    att_diff: float,
    nu0: float = 1.05,
    nu1: float = 0.15,
) -> float:
    """Compute dispersion parameter as function of competitive balance.

    nu = nu0 + nu1 * |att_home + def_away - att_away - def_home|

    When teams are equal (diff=0): nu ~ nu0 (slight underdispersion)
    When mismatch: nu increases -> more underdispersion (favorites
    slow down when leading comfortably).

    Args:
        att_diff: Absolute difference in team strengths
        nu0: Base dispersion (> 1 means underdispersed)
        nu1: Sensitivity to competitive imbalance

    Returns:
        nu >= 1.0 (capped at 2.0 for numerical stability)
    """
    nu = nu0 + nu1 * abs(att_diff)
    return min(max(nu, 0.8), 2.0)
