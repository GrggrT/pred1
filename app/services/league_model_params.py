from __future__ import annotations

import json
import math
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.decimalutils import D, q_prob
from app.core.logger import get_logger

log = get_logger("services.league_model_params")

FINAL_STATUSES = ("FT", "AET", "PEN")


def _clamp_decimal(value: Decimal, lo: Decimal, hi: Decimal) -> Decimal:
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value


def _safe_float(value: Any, default: float | None = None) -> float | None:
    if value is None:
        return default
    try:
        return float(value)
    except Exception:
        return default


def _as_dict(payload: Any) -> dict | None:
    if payload is None:
        return None
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str):
        try:
            v = json.loads(payload)
            return v if isinstance(v, dict) else None
        except Exception:
            return None
    return None


def _outcome_1x2(home_goals: int | None, away_goals: int | None) -> str | None:
    if home_goals is None or away_goals is None:
        return None
    if home_goals > away_goals:
        return "HOME_WIN"
    if home_goals == away_goals:
        return "DRAW"
    return "AWAY_WIN"


async def estimate_dixon_coles_rho(
    session: AsyncSession,
    *,
    league_id: int,
    season: int,
    before_date: date,
    lam_home: Decimal,
    lam_away: Decimal,
    min_matches: int = 60,
) -> Decimal | None:
    """
    Estimate league/season Dixon-Coles rho using only (0,0), (0,1), (1,0), (1,1) score frequencies.

    We use the canonical tau correction:
      tau(0,0)=1 - lam_h*lam_a*rho
      tau(0,1)=1 + lam_h*rho
      tau(1,0)=1 + lam_a*rho
      tau(1,1)=1 - rho
    and maximize sum(count * log(tau)).
    """
    lam_h = max(D("0.05"), D(lam_home))
    lam_a = max(D("0.05"), D(lam_away))
    res = await session.execute(
        text(
            """
            SELECT
              COUNT(*) FILTER (WHERE home_goals=0 AND away_goals=0) AS n00,
              COUNT(*) FILTER (WHERE home_goals=0 AND away_goals=1) AS n01,
              COUNT(*) FILTER (WHERE home_goals=1 AND away_goals=0) AS n10,
              COUNT(*) FILTER (WHERE home_goals=1 AND away_goals=1) AS n11,
              COUNT(*) AS total
            FROM fixtures
            WHERE league_id=:lid
              AND season=:season
              AND status IN ('FT','AET','PEN')
              AND kickoff::date < :dt
              AND home_goals IS NOT NULL
              AND away_goals IS NOT NULL
            """
        ),
        {"lid": int(league_id), "season": int(season), "dt": before_date},
    )
    row = res.first()
    if not row or int(row.total or 0) < int(min_matches):
        return None

    n00 = int(row.n00 or 0)
    n01 = int(row.n01 or 0)
    n10 = int(row.n10 or 0)
    n11 = int(row.n11 or 0)

    # Bounds that keep tau strictly positive.
    rho_lo = max(-0.5, float((-D("0.99") / lam_h)), float((-D("0.99") / lam_a)))
    rho_hi = min(0.5, float(D("0.99") / (lam_h * lam_a)), 0.99)
    if rho_hi <= rho_lo:
        return None

    best_rho = None
    best_ll = None
    steps = 401
    for i in range(steps):
        rho = rho_lo + (rho_hi - rho_lo) * (i / (steps - 1))
        tau00 = 1.0 - float(lam_h * lam_a) * rho
        tau01 = 1.0 + float(lam_h) * rho
        tau10 = 1.0 + float(lam_a) * rho
        tau11 = 1.0 - rho
        if tau00 <= 0 or tau01 <= 0 or tau10 <= 0 or tau11 <= 0:
            continue
        ll = 0.0
        if n00:
            ll += n00 * math.log(tau00)
        if n01:
            ll += n01 * math.log(tau01)
        if n10:
            ll += n10 * math.log(tau10)
        if n11:
            ll += n11 * math.log(tau11)
        if best_ll is None or ll > best_ll:
            best_ll = ll
            best_rho = rho

    if best_rho is None:
        return None
    return q_prob(D(best_rho))


async def estimate_power_calibration_alpha(
    session: AsyncSession,
    *,
    league_id: int,
    season: int,
    before_date: date,
    prob_source: str | None = None,
    limit: int = 1500,
    min_samples: int = 120,
) -> Decimal | None:
    """
    Temperature/power scaling for 1X2 probabilities:
      p_i' ∝ p_i ** alpha
    Choose alpha that minimizes multi-class logloss on historical decisions.
    """
    extra = ""
    params: dict[str, Any] = {"lid": int(league_id), "season": int(season), "dt": before_date, "lim": int(limit)}
    if prob_source:
        extra = "AND pd.payload->>'prob_source' = :src"
        params["src"] = str(prob_source)

    res = await session.execute(
        text(
            f"""
            SELECT f.id, f.home_goals, f.away_goals, pd.payload
            FROM fixtures f
            JOIN prediction_decisions pd ON pd.fixture_id=f.id AND pd.market='1X2'
            WHERE f.league_id=:lid
              AND f.season=:season
              AND f.status IN ('FT','AET','PEN')
              AND f.kickoff::date < :dt
              AND f.home_goals IS NOT NULL AND f.away_goals IS NOT NULL
              {extra}
            ORDER BY f.kickoff DESC
            LIMIT :lim
            """
        ),
        params,
    )
    samples: list[tuple[float, float, float, int]] = []
    for r in res.fetchall():
        payload = _as_dict(r.payload)
        if not payload:
            continue
        outcome_sel = _outcome_1x2(r.home_goals, r.away_goals)
        if outcome_sel is None:
            continue

        cand = payload.get("candidates") or []
        if not isinstance(cand, list) or not cand:
            continue
        pmap: dict[str, float] = {}
        for entry in cand:
            if not isinstance(entry, dict):
                continue
            sel = entry.get("selection")
            prob = _safe_float(entry.get("prob"))
            if not sel or prob is None:
                continue
            pmap[str(sel)] = float(prob)
        ph = pmap.get("HOME_WIN")
        pd = pmap.get("DRAW")
        pa = pmap.get("AWAY_WIN")
        if ph is None or pd is None or pa is None:
            continue
        s = ph + pd + pa
        if s <= 0:
            continue
        ph /= s
        pd /= s
        pa /= s
        # idx: 0=H, 1=D, 2=A
        y = 0 if outcome_sel == "HOME_WIN" else 1 if outcome_sel == "DRAW" else 2
        samples.append((ph, pd, pa, y))

    if len(samples) < int(min_samples):
        return None

    def logloss(alpha: float) -> float:
        eps = 1e-15
        total = 0.0
        for ph, pd, pa, y in samples:
            a0 = max(eps, ph) ** alpha
            a1 = max(eps, pd) ** alpha
            a2 = max(eps, pa) ** alpha
            denom = a0 + a1 + a2
            if denom <= 0:
                total += 50.0
                continue
            if y == 0:
                p = a0 / denom
            elif y == 1:
                p = a1 / denom
            else:
                p = a2 / denom
            total += -math.log(max(eps, p))
        return total / float(len(samples))

    best_alpha = None
    best_ll = None
    # Typical stable range. alpha<1 flattens, alpha>1 sharpens.
    lo, hi = 0.5, 2.0
    steps = 61
    for i in range(steps):
        a = lo + (hi - lo) * (i / (steps - 1))
        ll = logloss(a)
        if best_ll is None or ll < best_ll:
            best_ll = ll
            best_alpha = a

    if best_alpha is None:
        return None
    return q_prob(D(best_alpha))

