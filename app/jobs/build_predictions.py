from __future__ import annotations

from datetime import datetime, timedelta
import json
import math
from decimal import Decimal
from typing import Dict, Tuple, List, Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.decimalutils import D, q_ev, q_money, q_prob, q_xg, safe_div
from app.core.logger import get_logger
from app.core.timeutils import utcnow
from app.services.poisson import match_probs, match_probs_dixon_coles, poisson_pmf
from app.services.elo_ratings import apply_elo_from_fixtures, get_team_rating, DEFAULT_RATING
from app.services.league_model_params import estimate_dixon_coles_rho, estimate_power_calibration_alpha

log = get_logger("jobs.build_predictions")
FINAL_STATUSES = ("FT", "AET", "PEN")

ELO_ADJ_MIN = D("0.75")
ELO_ADJ_MAX = D("1.25")
ELO_ADJ_K = D("1600")
LAMBDA_EPS = D("0.001")


def _clamp_decimal(value: Decimal, lo: Decimal, hi: Decimal) -> Decimal:
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value


def _power_scale_1x2(p_home: Decimal, p_draw: Decimal, p_away: Decimal, alpha: Decimal) -> tuple[Decimal, Decimal, Decimal]:
    """p_i' ∝ p_i ** alpha (temperature/power scaling)"""
    try:
        a = float(alpha)
    except Exception:
        return p_home, p_draw, p_away
    if not (0.1 < a < 5.0):
        return p_home, p_draw, p_away
    eps = 1e-15
    ph = max(eps, float(p_home))
    pd = max(eps, float(p_draw))
    pa = max(eps, float(p_away))
    ph_a = ph**a
    pd_a = pd**a
    pa_a = pa**a
    denom = ph_a + pd_a + pa_a
    if denom <= 0:
        return p_home, p_draw, p_away
    return q_prob(D(ph_a / denom)), q_prob(D(pd_a / denom)), q_prob(D(pa_a / denom))


def elo_adjust_factor(elo_diff: Decimal) -> Decimal:
    raw = D(1) + safe_div(D(elo_diff), ELO_ADJ_K, default=0)
    if raw < ELO_ADJ_MIN:
        raw = ELO_ADJ_MIN
    elif raw > ELO_ADJ_MAX:
        raw = ELO_ADJ_MAX
    return raw.quantize(D("0.001"))


def _standings_gap_score(home_points: int | None, away_points: int | None) -> Decimal:
    if home_points is None or away_points is None:
        return D(0)
    diff = abs(int(home_points) - int(away_points))
    # 0..0.10 roughly: 0 pts => 0, 30 pts => 0.10
    return min(D(diff) / D(300), D("0.10"))


def _best_ev_selection(
    probs: Dict[str, Decimal],
    odds_map: Dict[str, float | Decimal | None],
    min_odd: Decimal,
    max_odd: Decimal,
) -> Tuple[str | None, Decimal | None, Decimal | None]:
    best_sel: str | None = None
    best_ev: Decimal | None = None
    best_odd: Decimal | None = None
    for sel, prob in probs.items():
        odd_raw = odds_map.get(sel)
        if odd_raw is None:
            continue
        odd = q_money(odd_raw)
        if odd < min_odd or odd > max_odd:
            continue
        ev = q_ev(D(prob) * odd - D(1))
        if best_ev is None or ev > best_ev:
            best_sel = sel
            best_ev = ev
            best_odd = odd
    return best_sel, best_ev, best_odd


def _rank_candidates(
    probs: Dict[str, Decimal],
    odds_map: Dict[str, float | Decimal | None],
    min_odd: Decimal,
    max_odd: Decimal,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for sel, prob in probs.items():
        odd_raw = odds_map.get(sel)
        if odd_raw is None:
            out.append(
                {
                    "selection": sel,
                    "prob": float(prob),
                    "odd": None,
                    "ev": None,
                    "in_range": False,
                }
            )
            continue
        odd = q_money(odd_raw)
        ev = q_ev(D(prob) * odd - D(1))
        out.append(
            {
                "selection": sel,
                "prob": float(prob),
                "odd": float(odd),
                "ev": float(ev),
                "in_range": bool(min_odd <= odd <= max_odd),
            }
        )
    out.sort(key=lambda r: (r["ev"] is not None, r["ev"] or float("-inf")), reverse=True)
    return out


async def _upsert_decision(session: AsyncSession, fixture_id: int, market: str, payload: dict):
    await session.execute(
        text(
            """
            INSERT INTO prediction_decisions(fixture_id, market, payload, created_at, updated_at)
            VALUES(:fid, :m, CAST(:p AS jsonb), now(), now())
            ON CONFLICT (fixture_id, market) DO UPDATE SET
              payload=CAST(:p AS jsonb),
              updated_at=now()
            """
        ),
        {"fid": fixture_id, "m": market, "p": json.dumps(payload)},
    )


async def _target_rows(session: AsyncSession):
    now_utc = utcnow()
    horizon = now_utc + (timedelta(days=1) if settings.backtest_mode else timedelta(days=7))
    bt_kind = (settings.backtest_kind or "pseudo").strip().lower()
    use_snapshots = bool(settings.backtest_mode) and bt_kind == "true"
    odds_join = "LEFT JOIN odds o ON o.fixture_id=f.id AND o.bookmaker_id=:bid"
    if use_snapshots:
        odds_join = """
        LEFT JOIN LATERAL (
          SELECT
            os.home_win, os.draw, os.away_win,
            os.over_2_5, os.under_2_5,
            os.market_avg_home_win, os.market_avg_draw, os.market_avg_away_win,
            os.market_avg_over_2_5, os.market_avg_under_2_5,
            os.fetched_at
          FROM odds_snapshots os
          WHERE os.fixture_id=f.id
            AND os.bookmaker_id=:bid
            AND os.fetched_at < f.kickoff
          ORDER BY os.fetched_at DESC
          LIMIT 1
        ) o ON TRUE
        """
    res = await session.execute(
        text(
            f"""
            SELECT f.id, f.league_id, f.season, f.kickoff,
                   f.home_team_id, f.away_team_id,
                   sh.rank AS home_rank, sh.points AS home_points,
                   sa.rank AS away_rank, sa.points AS away_points,
                   mi.home_form_for, mi.home_form_against,
                   mi.away_form_for, mi.away_form_against,
                   mi.home_class_for, mi.home_class_against,
                   mi.away_class_for, mi.away_class_against,
                   mi.home_venue_for, mi.home_venue_against,
                   mi.away_venue_for, mi.away_venue_against,
                   o.home_win, o.draw, o.away_win,
                   o.over_2_5, o.under_2_5,
                   o.market_avg_home_win, o.market_avg_draw, o.market_avg_away_win,
                   o.market_avg_over_2_5, o.market_avg_under_2_5,
                   o.fetched_at
            FROM fixtures f
            JOIN match_indices mi ON mi.fixture_id=f.id
            LEFT JOIN team_standings sh ON sh.team_id=f.home_team_id AND sh.league_id=f.league_id AND sh.season=f.season
            LEFT JOIN team_standings sa ON sa.team_id=f.away_team_id AND sa.league_id=f.league_id AND sa.season=f.season
            {odds_join}
            WHERE (:bt = true OR f.status='NS')
              AND f.league_id IN (SELECT unnest(CAST(:lids AS integer[])))
              AND f.kickoff >= :start AND f.kickoff < :end
              AND (
                :bt = false OR
                (:bt_kind = 'pseudo' AND (o.fetched_at IS NULL OR o.fetched_at < f.kickoff)) OR
                (:bt_kind = 'true' AND o.fetched_at < f.kickoff)
              )
              AND (:bt_kind != 'true' OR o.fetched_at IS NOT NULL)
            ORDER BY f.kickoff ASC
            """
        ),
        {
            "start": now_utc,
            "end": horizon,
            "bid": settings.bookmaker_id,
            "lids": settings.league_ids,
            "bt": settings.backtest_mode,
            "bt_kind": bt_kind,
        },
    )
    return res.fetchall()


async def _history_values(session: AsyncSession, team_id: int, cutoff: datetime, limit: int = 15, venue: str | None = None):
    filter_venue = ""
    if venue == "home":
        filter_venue = "AND f.home_team_id = :tid"
    elif venue == "away":
        filter_venue = "AND f.away_team_id = :tid"
    else:
        filter_venue = "AND (:tid = f.home_team_id OR :tid = f.away_team_id)"
    res = await session.execute(
        text(
            f"""
            SELECT COALESCE(
              CASE WHEN f.home_team_id = :tid THEN f.home_xg ELSE f.away_xg END,
              CASE WHEN f.home_team_id = :tid THEN f.home_goals ELSE f.away_goals END
            ) AS val
            FROM fixtures f
            WHERE f.status IN ('FT', 'AET', 'PEN')
              AND f.kickoff < :cutoff
              {filter_venue}
            ORDER BY f.kickoff DESC
            LIMIT :lim
            """
        ),
        {
            "tid": team_id,
            "cutoff": cutoff,
            "lim": limit,
        },
    )
    return [D(r.val) for r in res.fetchall() if r.val is not None]


async def _prefetch_team_values(
    session: AsyncSession,
    team_ids: List[int],
    cutoff_end: datetime,
    limit: int = 40,
) -> Dict[int, List[tuple[datetime, bool, Decimal]]]:
    if not team_ids:
        return {}
    res = await session.execute(
        text(
            """
            WITH team_matches AS (
              SELECT
                f.kickoff AS kickoff,
                f.home_team_id AS team_id,
                TRUE AS is_home,
                COALESCE(f.home_xg, f.home_goals)::numeric AS val
              FROM fixtures f
              WHERE f.status IN ('FT', 'AET', 'PEN')
                AND f.kickoff < :cutoff_end
                AND f.home_team_id IN (SELECT unnest(CAST(:team_ids AS integer[])))

              UNION ALL

              SELECT
                f.kickoff AS kickoff,
                f.away_team_id AS team_id,
                FALSE AS is_home,
                COALESCE(f.away_xg, f.away_goals)::numeric AS val
              FROM fixtures f
              WHERE f.status IN ('FT', 'AET', 'PEN')
                AND f.kickoff < :cutoff_end
                AND f.away_team_id IN (SELECT unnest(CAST(:team_ids AS integer[])))
            ),
            ranked AS (
              SELECT
                kickoff, team_id, is_home, val,
                ROW_NUMBER() OVER (PARTITION BY team_id ORDER BY kickoff DESC) AS rn
              FROM team_matches
              WHERE val IS NOT NULL
            )
            SELECT kickoff, team_id, is_home, val
            FROM ranked
            WHERE rn <= :lim
            ORDER BY team_id, kickoff DESC
            """
        ),
        {
            "cutoff_end": cutoff_end,
            "team_ids": team_ids,
            "lim": limit,
        },
    )
    out: Dict[int, List[tuple[datetime, bool, Decimal]]] = {}
    for r in res.fetchall():
        out.setdefault(r.team_id, []).append((r.kickoff, bool(r.is_home), D(r.val)))
    return out


def _vals_before(
    history: List[tuple[datetime, bool, Decimal]],
    cutoff: datetime,
    limit: int,
    venue: str | None = None,
) -> List[Decimal]:
    vals: List[Decimal] = []
    want_home = venue == "home"
    want_away = venue == "away"
    for kickoff, is_home, val in history:
        if kickoff >= cutoff:
            continue
        if want_home and not is_home:
            continue
        if want_away and is_home:
            continue
        vals.append(val)
        if len(vals) >= limit:
            break
    return vals


def _volatility_score(vals: list[Decimal]) -> Decimal:
    if not vals:
        return D(0)
    mean = sum(vals, D(0)) / D(len(vals))
    if mean <= 0:
        return D(0)
    variance = sum((v - mean) ** 2 for v in vals) / D(len(vals))
    std = variance.sqrt()
    volatility = safe_div(std, mean, default=0)
    score = max(D(0), D(1) - volatility)
    return q_prob(score * D("0.3"))


def _samples_score(short_n: int, long_n: int, venue_n: int) -> Decimal:
    total = short_n + long_n + venue_n
    return q_prob(min(D(total) / D(25), D(1)) * D("0.4"))


def _elo_gap_score(elo_diff: Decimal) -> Decimal:
    gap = abs(elo_diff)
    score = max(D(0), D(1) - safe_div(gap, D(400), default=0))
    return q_prob(score * D("0.3"))


def logistic_probs_from_features(elo_diff: Decimal, xpts_diff: Decimal, p_draw: Decimal) -> Tuple[Decimal, Decimal, Decimal]:
    z = 0.002 * float(elo_diff) + 0.05 * float(xpts_diff)
    p_home_win = 1 / (1 + math.exp(-z))
    p_draw_clamped = float(_clamp_decimal(D(p_draw), D("0.15"), D("0.35")))
    remaining = max(0.0, 1.0 - p_draw_clamped)
    p_home = remaining * p_home_win
    p_away = remaining * (1.0 - p_home_win)
    return (
        q_prob(D(p_home)),
        q_prob(D(p_draw_clamped)),
        q_prob(D(p_away)),
    )


async def _league_baseline_cache(
    session: AsyncSession,
    cache: Dict[Tuple[int, int, str], Tuple[Decimal, Decimal, Decimal, Decimal, Decimal]],
    league_id: int,
    season: int,
    cutoff: datetime,
) -> Tuple[Decimal, Decimal, Decimal, Decimal, Decimal]:
    key = (league_id, season, cutoff.date().isoformat())
    if key in cache:
        return cache[key]

    date_key = cutoff.date()
    row = (
        await session.execute(
            text(
                """
                SELECT avg_home_xg, avg_away_xg, draw_freq, dc_rho, calib_alpha
                FROM league_baselines
                WHERE league_id=:lid AND season=:season AND date_key=:dk
                """
            ),
            {"lid": league_id, "season": season, "dk": date_key},
        )
    ).first()

    if row:
        home_avg = q_money(row.avg_home_xg) if row.avg_home_xg is not None else q_money(1)
        away_avg = q_money(row.avg_away_xg) if row.avg_away_xg is not None else q_money(1)
        draw_freq = q_prob(row.draw_freq) if row.draw_freq is not None else q_prob(D("0.22"))
        dc_rho = q_prob(row.dc_rho) if row.dc_rho is not None else q_prob(D(0))
        calib_alpha = q_prob(row.calib_alpha) if row.calib_alpha is not None else q_prob(D(1))
        override = settings.calib_alpha_overrides.get(int(league_id))
        if override is not None:
            calib_alpha = override
        cache[key] = (home_avg, away_avg, draw_freq, dc_rho, calib_alpha)
        return cache[key]

    # Fallback: compute baseline directly from fixtures and also estimate rho/alpha if possible.
    res = await session.execute(
        text(
            """
            SELECT
              AVG(COALESCE(home_xg, home_goals)) AS home_avg,
              AVG(COALESCE(away_xg, away_goals)) AS away_avg,
              COUNT(*) FILTER (WHERE home_goals = away_goals) AS draws,
              COUNT(*) AS total,
              AVG((home_goals + away_goals)::numeric) AS avg_goals
            FROM fixtures
            WHERE league_id=:lid
              AND season=:season
              AND status IN ('FT', 'AET', 'PEN')
              AND kickoff::date < :dt
            """
        ),
        {"lid": league_id, "season": season, "dt": date_key},
    )
    r2 = res.first()
    home_avg = q_money(r2.home_avg) if r2 and r2.home_avg is not None else q_money(1)
    away_avg = q_money(r2.away_avg) if r2 and r2.away_avg is not None else q_money(1)
    draw_freq = q_prob(D(r2.draws or 0) / D(r2.total)) if r2 and r2.total else q_prob(D("0.22"))

    prob_source = (
        "hybrid"
        if settings.use_hybrid_probs
        else "logistic"
        if settings.use_logistic_probs
        else "dixon_coles"
        if settings.use_dixon_coles_probs
        else "poisson"
    )
    rho = await estimate_dixon_coles_rho(
        session,
        league_id=league_id,
        season=season,
        before_date=date_key,
        lam_home=home_avg,
        lam_away=away_avg,
    )
    alpha = await estimate_power_calibration_alpha(
        session,
        league_id=league_id,
        season=season,
        before_date=date_key,
        prob_source=prob_source,
    )
    dc_rho = rho if rho is not None else q_prob(D(0))
    calib_alpha = alpha if alpha is not None else q_prob(D(1))
    override = settings.calib_alpha_overrides.get(int(league_id))
    if override is not None:
        calib_alpha = override

    try:
        await session.execute(
            text(
                """
                INSERT INTO league_baselines(
                  league_id, season, date_key,
                  avg_home_xg, avg_away_xg, draw_freq, avg_goals,
                  dc_rho, calib_alpha
                )
                VALUES(:lid, :season, :dk, :ah, :aa, :df, :avg_goals, :rho, :alpha)
                ON CONFLICT (league_id, season, date_key) DO UPDATE SET
                  avg_home_xg=:ah,
                  avg_away_xg=:aa,
                  draw_freq=:df,
                  avg_goals=:avg_goals,
                  dc_rho=:rho,
                  calib_alpha=:alpha
                """
            ),
            {
                "lid": league_id,
                "season": season,
                "dk": date_key,
                "ah": home_avg,
                "aa": away_avg,
                "df": draw_freq,
                "avg_goals": r2.avg_goals if r2 else None,
                "rho": dc_rho,
                "alpha": calib_alpha,
            },
        )
    except Exception:
        # Non-fatal: baseline caching is optional.
        pass

    cache[key] = (home_avg, away_avg, draw_freq, dc_rho, calib_alpha)
    return cache[key]


def _weighted_attack(row, side: str) -> Tuple[Decimal, Decimal]:
    w_short, w_long, w_venue = settings.weights
    if side == "home":
        att = (
            (w_short * D(row.home_form_for) if settings.enable_form else D(0))
            + (w_long * D(row.home_class_for) if settings.enable_class else D(0))
            + (w_venue * D(row.home_venue_for) if settings.enable_venue else D(0))
        )
        defe = (
            (w_short * D(row.home_form_against) if settings.enable_form else D(0))
            + (w_long * D(row.home_class_against) if settings.enable_class else D(0))
            + (w_venue * D(row.home_venue_against) if settings.enable_venue else D(0))
        )
    else:
        att = (
            (w_short * D(row.away_form_for) if settings.enable_form else D(0))
            + (w_long * D(row.away_class_for) if settings.enable_class else D(0))
            + (w_venue * D(row.away_venue_for) if settings.enable_venue else D(0))
        )
        defe = (
            (w_short * D(row.away_form_against) if settings.enable_form else D(0))
            + (w_long * D(row.away_class_against) if settings.enable_class else D(0))
            + (w_venue * D(row.away_venue_against) if settings.enable_venue else D(0))
        )
    return att, defe


def _selection_from_probs(probs: Dict[str, Decimal]) -> str:
    return max(probs, key=probs.get)


def _info_payload(market: str, probs: Dict[str, Decimal], extra: dict[str, Any] | None = None) -> dict[str, Any]:
    selection = _selection_from_probs(probs)
    payload = {
        "market": market,
        "action": "INFO",
        "reason": "derived_probs",
        "selection": selection,
        "prob": float(probs[selection]),
        "candidates": [{"selection": sel, "prob": float(prob)} for sel, prob in probs.items()],
    }
    if extra:
        payload.update(extra)
    return payload


async def run(session: AsyncSession):
    # Keep Elo up to date for all finished fixtures before generating new predictions.
    elo_cutoff = utcnow() if settings.backtest_mode else None
    await apply_elo_from_fixtures(session, league_ids=settings.league_ids, cutoff=elo_cutoff)

    rows = await _target_rows(session)
    if not rows:
        log.info("build_predictions no fixtures")
        return

    log.info("build_predictions fixtures=%s", len(rows))
    # Avoid leakage from future standings/injuries in backtest calibration runs.
    use_standings = bool(settings.enable_standings) and not settings.backtest_mode
    use_injuries = bool(settings.enable_injuries) and not settings.backtest_mode
    league_cache: Dict[Tuple[int, int, str], Tuple[Decimal, Decimal, Decimal, Decimal, Decimal]] = {}
    elo_cache: Dict[int, Decimal] = {}
    horizon_end = max(r.kickoff for r in rows)
    team_ids = sorted({int(r.home_team_id) for r in rows} | {int(r.away_team_id) for r in rows})
    team_history = await _prefetch_team_values(session, team_ids, horizon_end, limit=40)
    injury_counts: dict[int, int] = {}
    if use_injuries and team_ids:
        cutoff_dt = utcnow() - timedelta(days=int(getattr(settings, "injuries_ttl_days", 30) or 30))
        res_inj = await session.execute(
            text(
                """
                SELECT team_id, COUNT(*) AS cnt
                FROM injuries
                WHERE team_id IN (SELECT unnest(CAST(:tids AS integer[])))
                  AND created_at >= :cutoff
                GROUP BY team_id
                """
            ),
            {"tids": team_ids, "cutoff": cutoff_dt},
        )
        for r in res_inj.fetchall():
            if r.team_id is None:
                continue
            injury_counts[int(r.team_id)] = int(r.cnt or 0)
    bets = skips = 0
    total_bets = total_skips = 0
    missing_odds = 0

    for row in rows:
        base_home, base_away, league_draw_freq, dc_rho, calib_alpha = await _league_baseline_cache(
            session, league_cache, row.league_id, row.season, row.kickoff
        )

        home_att, home_def = _weighted_attack(row, "home")
        away_att, away_def = _weighted_attack(row, "away")

        home_att_factor = safe_div(home_att, base_home, default=1)
        away_def_factor = safe_div(away_def, base_home, default=1)
        lam_home = q_money(base_home * home_att_factor * away_def_factor)

        away_att_factor = safe_div(away_att, base_away, default=1)
        home_def_factor = safe_div(home_def, base_away, default=1)
        lam_away = q_money(base_away * away_att_factor * home_def_factor)

        # Elo adjustment (±25% cap via quantize to 0.001)
        async def _elo(team_id: int) -> Decimal:
            cached = elo_cache.get(team_id)
            if cached is not None:
                return cached
            rating = await get_team_rating(session, team_id)
            elo_cache[team_id] = rating
            return rating

        elo_home = await _elo(row.home_team_id) if hasattr(row, "home_team_id") else DEFAULT_RATING
        elo_away = await _elo(row.away_team_id) if hasattr(row, "away_team_id") else DEFAULT_RATING
        elo_diff = elo_home - elo_away
        if settings.enable_elo:
            adj_factor = elo_adjust_factor(elo_diff)
        else:
            adj_factor = D(1)
        lam_home = max(LAMBDA_EPS, q_money(lam_home * adj_factor))
        lam_away = max(LAMBDA_EPS, q_money(lam_away / adj_factor))

        # Standings delta: small, symmetric nudges to λ (kept intentionally conservative).
        standings_delta = D(0)
        if use_standings and hasattr(row, "home_points") and hasattr(row, "away_points"):
            try:
                points_diff = D(int(row.home_points or 0) - int(row.away_points or 0))
                standings_delta = _clamp_decimal(points_diff / D(200), D("-0.05"), D("0.05"))
                lam_home = max(LAMBDA_EPS, q_money(lam_home * (D(1) + standings_delta)))
                lam_away = max(LAMBDA_EPS, q_money(lam_away * (D(1) - standings_delta)))
            except Exception:
                standings_delta = D(0)

        # Injuries: reduce expected goals a bit and increase uncertainty (via signal_score).
        home_inj = int(injury_counts.get(int(row.home_team_id), 0)) if use_injuries else 0
        away_inj = int(injury_counts.get(int(row.away_team_id), 0)) if use_injuries else 0
        inj_penalty_home = _clamp_decimal(D(home_inj) * D("0.01"), D(0), D("0.08"))
        inj_penalty_away = _clamp_decimal(D(away_inj) * D("0.01"), D(0), D("0.08"))
        if use_injuries:
            lam_home = max(LAMBDA_EPS, q_money(lam_home * (D(1) - inj_penalty_home)))
            lam_away = max(LAMBDA_EPS, q_money(lam_away * (D(1) - inj_penalty_away)))

        # Derived info probabilities (totals + BTTS) from lambda-based model.
        lam_total = q_money(lam_home + lam_away)
        p_total_0 = poisson_pmf(0, lam_total)
        p_total_1 = poisson_pmf(1, lam_total)
        p_total_2 = poisson_pmf(2, lam_total)
        p_total_3 = poisson_pmf(3, lam_total)
        p_under_1_5 = q_prob(_clamp_decimal(p_total_0 + p_total_1, D(0), D(1)))
        p_over_1_5 = q_prob(_clamp_decimal(D(1) - p_under_1_5, D(0), D(1)))
        p_under_2_5 = q_prob(_clamp_decimal(p_total_0 + p_total_1 + p_total_2, D(0), D(1)))
        p_over_2_5 = q_prob(_clamp_decimal(D(1) - p_under_2_5, D(0), D(1)))
        p_under_3_5 = q_prob(_clamp_decimal(p_total_0 + p_total_1 + p_total_2 + p_total_3, D(0), D(1)))
        p_over_3_5 = q_prob(_clamp_decimal(D(1) - p_under_3_5, D(0), D(1)))
        p_home_0 = poisson_pmf(0, lam_home)
        p_away_0 = poisson_pmf(0, lam_away)
        p_btts_yes = q_prob(_clamp_decimal(D(1) - p_home_0 - p_away_0 + (p_home_0 * p_away_0), D(0), D(1)))
        p_btts_no = q_prob(_clamp_decimal(D(1) - p_btts_yes, D(0), D(1)))

        # signal_score components
        home_h = team_history.get(int(row.home_team_id), [])
        away_h = team_history.get(int(row.away_team_id), [])
        home_short = _vals_before(home_h, row.kickoff, limit=5)
        away_short = _vals_before(away_h, row.kickoff, limit=5)
        home_long = _vals_before(home_h, row.kickoff, limit=15)
        away_long = _vals_before(away_h, row.kickoff, limit=15)
        home_venue = _vals_before(home_h, row.kickoff, limit=5, venue="home")
        away_venue = _vals_before(away_h, row.kickoff, limit=5, venue="away")
        samples_score = _samples_score(
            len(home_short) + len(away_short),
            len(home_long) + len(away_long),
            len(home_venue) + len(away_venue),
        )
        vol_vals = (home_short + away_short)[:10]
        volatility_score = _volatility_score(vol_vals)
        elo_gap_score = _elo_gap_score(elo_diff)
        standings_score = D(0)
        if use_standings and hasattr(row, "home_points") and hasattr(row, "away_points"):
            standings_score = _standings_gap_score(row.home_points, row.away_points)
        injury_uncertainty = _clamp_decimal(D(home_inj + away_inj) * D("0.01"), D(0), D("0.10")) if use_injuries else D(0)
        signal_score_raw = samples_score + volatility_score + elo_gap_score + standings_score - injury_uncertainty
        signal_score = q_prob(_clamp_decimal(signal_score_raw, D(0), D(1)))

        p_home_poisson, p_draw_poisson, p_away_poisson = match_probs(lam_home, lam_away, k_max=10)
        p_home_dc, p_draw_dc, p_away_dc = match_probs_dixon_coles(lam_home, lam_away, rho=dc_rho, k_max=10)
        xpts_home = (D(3) * p_home_poisson + p_draw_poisson).quantize(D("0.001"))
        xpts_away = (D(3) * p_away_poisson + p_draw_poisson).quantize(D("0.001"))
        xpts_diff = (xpts_home - xpts_away).quantize(D("0.001"))
        prob_source = "poisson"
        p_home, p_draw, p_away = p_home_poisson, p_draw_poisson, p_away_poisson

        probs_map = {
            "poisson": {"home": p_home_poisson, "draw": p_draw_poisson, "away": p_away_poisson},
            "dixon_coles": {"home": p_home_dc, "draw": p_draw_dc, "away": p_away_dc},
        }
        probs_map["logistic"] = None
        if settings.use_logistic_probs or settings.use_hybrid_probs:
            phl, pdl, pal = logistic_probs_from_features(elo_diff, xpts_diff, league_draw_freq)
            probs_map["logistic"] = {"home": phl, "draw": pdl, "away": pal}
        if settings.use_hybrid_probs:
            weights = settings.hybrid_weights
            final = {"home": D(0), "draw": D(0), "away": D(0)}
            for key, weight in weights.items():
                src_probs = probs_map.get(key)
                if not src_probs:
                    continue
                final["home"] += weight * D(src_probs["home"])
                final["draw"] += weight * D(src_probs["draw"])
                final["away"] += weight * D(src_probs["away"])
            total = final["home"] + final["draw"] + final["away"]
            if total > 0:
                for k in final:
                    final[k] = q_prob(final[k] / total)
            p_home, p_draw, p_away = final["home"], final["draw"], final["away"]
            prob_source = "hybrid"
        elif settings.use_logistic_probs:
            p_home, p_draw, p_away = probs_map["logistic"]["home"], probs_map["logistic"]["draw"], probs_map["logistic"]["away"]
            prob_source = "logistic"
        elif settings.use_dixon_coles_probs:
            p_home, p_draw, p_away = p_home_dc, p_draw_dc, p_away_dc
            prob_source = "dixon_coles"
        elif settings.use_hybrid_probs:
            prob_source = "hybrid"
        probs = {"HOME_WIN": p_home, "DRAW": p_draw, "AWAY_WIN": p_away}

        # League-specific probability calibration (power scaling) to improve logloss.
        p_home, p_draw, p_away = _power_scale_1x2(p_home, p_draw, p_away, calib_alpha)
        probs = {"HOME_WIN": p_home, "DRAW": p_draw, "AWAY_WIN": p_away}

        odds_map = {
            "HOME_WIN": row.home_win,
            "DRAW": row.draw,
            "AWAY_WIN": row.away_win,
        }
        market_map = {
            "HOME_WIN": row.market_avg_home_win,
            "DRAW": row.market_avg_draw,
            "AWAY_WIN": row.market_avg_away_win,
        }
        status = "PENDING"
        confidence = None
        value_index = None
        initial_odd = None
        market_diff = None
        feature_flags = {
            "elo": bool(settings.enable_elo),
            "venue": bool(settings.enable_venue),
            "xg": bool(settings.enable_xg),
            "form": bool(settings.enable_form),
            "class": bool(settings.enable_class),
            "standings": bool(use_standings),
            "lam_home": float(lam_home),
            "lam_away": float(lam_away),
            "lam_total": float(lam_total),
            "elo_home": float(elo_home),
            "elo_away": float(elo_away),
            "elo_diff": float(elo_diff),
            "adj_factor": float(adj_factor),
            "prob_source": prob_source,
            "league_draw_freq": float(league_draw_freq),
            "dc_rho": float(dc_rho),
            "calib_alpha": float(calib_alpha),
            "standings_delta": float(standings_delta),
            "samples_score": float(samples_score),
            "volatility_score": float(volatility_score),
            "elo_gap_score": float(elo_gap_score),
            "signal_score_raw": float(signal_score_raw),
            "signal_score": float(signal_score),
            "injuries_home": home_inj,
            "injuries_away": away_inj,
            "injury_penalty_home": float(inj_penalty_home),
            "injury_penalty_away": float(inj_penalty_away),
            "injury_uncertainty": float(injury_uncertainty),
            "xpts_diff": float(xpts_diff),
            "backtest": bool(settings.backtest_mode),
            "run_date": settings.backtest_current_date,
            "bt_kind": (settings.backtest_kind or "pseudo").strip().lower(),
        }
        if use_standings:
            try:
                feature_flags["standings_points_diff"] = int((row.home_points or 0) - (row.away_points or 0))
                feature_flags["standings_rank_diff"] = int((row.away_rank or 0) - (row.home_rank or 0))
            except Exception:
                pass
        goal_variance = q_xg(lam_home + lam_away)
        if abs(elo_diff) > D(100):
            goal_variance = q_xg(goal_variance * D("1.05"))
        feature_flags["goal_variance"] = float(goal_variance)

        base_threshold = settings.value_threshold_dec
        if signal_score < D("0.5"):
            effective_threshold = q_ev(base_threshold + D("0.05"))
        elif signal_score > D("0.8"):
            effective_threshold = q_ev(base_threshold - D("0.01"))
        else:
            effective_threshold = base_threshold
        feature_flags["effective_threshold"] = float(effective_threshold)

        info_extra = {
            "lam_home": float(lam_home),
            "lam_away": float(lam_away),
            "lam_total": float(lam_total),
            "prob_source": "poisson",
        }
        await _upsert_decision(
            session,
            row.id,
            "INFO_BTTS",
            _info_payload(
                "INFO_BTTS",
                {"BTTS_YES": p_btts_yes, "BTTS_NO": p_btts_no},
                info_extra,
            ),
        )
        await _upsert_decision(
            session,
            row.id,
            "INFO_OU_1_5",
            _info_payload(
                "INFO_OU_1_5",
                {"OVER_1_5": p_over_1_5, "UNDER_1_5": p_under_1_5},
                info_extra,
            ),
        )
        await _upsert_decision(
            session,
            row.id,
            "INFO_OU_2_5",
            _info_payload(
                "INFO_OU_2_5",
                {"OVER_2_5": p_over_2_5, "UNDER_2_5": p_under_2_5},
                info_extra,
            ),
        )
        await _upsert_decision(
            session,
            row.id,
            "INFO_OU_3_5",
            _info_payload(
                "INFO_OU_3_5",
                {"OVER_3_5": p_over_3_5, "UNDER_3_5": p_under_3_5},
                info_extra,
            ),
        )

        candidates = _rank_candidates(
            probs,
            odds_map,
            settings.min_odd_dec,
            settings.max_odd_dec,
        )
        feature_flags["candidates"] = candidates[:3]

        selection, ev, odd = _best_ev_selection(
            probs,
            odds_map,
            settings.min_odd_dec,
            settings.max_odd_dec,
        )
        any_odds = any(v is not None for v in odds_map.values())
        decision_payload: dict[str, Any] = {
            "market": "1X2",
            "prob_source": prob_source,
            "bt_kind": (settings.backtest_kind or "pseudo").strip().lower(),
            "effective_threshold": float(effective_threshold),
            "candidates": candidates[:3],
        }

        chosen_sel = selection
        if chosen_sel is None:
            decision_payload["action"] = "SKIP"
            decision_payload["reason"] = "no_odds" if not any_odds else "no_candidate_in_range"
            await _upsert_decision(session, row.id, "1X2", decision_payload)
            selection = "SKIP"
            status = "VOID"
            skips += 1
            if not any_odds:
                missing_odds += 1
        else:
            confidence = probs[chosen_sel]
            market_avg = market_map.get(selection)
            if market_avg:
                market_avg_dec = q_money(market_avg)
                if market_avg_dec > 0:
                    market_diff = q_ev(safe_div(odd - market_avg_dec, market_avg_dec, default=0))
                    feature_flags["market_diff"] = float(market_diff)
                    if abs(market_diff) > settings.market_diff_threshold:
                        log.warning(
                            "market_outlier fixture=%s sel=%s odd=%s market_avg=%s diff=%.3f",
                            row.id,
                            selection,
                            odd,
                            market_avg_dec,
                            float(market_diff),
                        )
            if confidence > D("0.60") and odd > D("3.0"):
                log.warning(
                    "high_prob_high_odd fixture=%s sel=%s prob=%.3f odd=%s lam_h=%s lam_a=%s",
                    row.id,
                    selection,
                    float(confidence),
                    odd,
                    lam_home,
                    lam_away,
                )
            if ev is not None and ev > effective_threshold:
                decision_payload.update(
                    {
                        "action": "BET",
                        "reason": "ev_above_threshold",
                        "selection": chosen_sel,
                        "prob": float(confidence),
                        "odd": float(odd) if odd is not None else None,
                        "ev": float(ev) if ev is not None else None,
                    }
                )
                await _upsert_decision(session, row.id, "1X2", decision_payload)
                value_index = ev
                initial_odd = odd
                bets += 1
                log.info(
                    "bet fixture=%s sel=%s prob=%.3f odd=%s ev=%s lam_h=%s lam_a=%s elo_h=%.1f elo_a=%.1f adj=%s signal=%.3f (samples=%.3f vol=%.3f elo_gap=%.3f) xpts_diff=%.3f var=%.3f thr=%.3f flags=%s",
                    row.id,
                    selection,
                    float(confidence),
                    odd,
                    ev,
                    lam_home,
                    lam_away,
                    float(elo_home),
                    float(elo_away),
                    adj_factor,
                    float(signal_score),
                    float(samples_score),
                    float(volatility_score),
                    float(elo_gap_score),
                    float(xpts_diff),
                    float(goal_variance),
                    float(effective_threshold),
                    feature_flags,
                )
                if signal_score < D("0.4"):
                    log.warning(
                        "low_confidence fixture=%s signal=%.3f (samples=%.3f vol=%.3f elo_gap=%.3f)",
                        row.id,
                        float(signal_score),
                        float(samples_score),
                        float(volatility_score),
                        float(elo_gap_score),
                    )
            else:
                decision_payload.update(
                    {
                        "action": "SKIP",
                        "reason": "ev_below_threshold",
                        "selection": chosen_sel,
                        "prob": float(confidence),
                        "odd": float(odd) if odd is not None else None,
                        "ev": float(ev) if ev is not None else None,
                    }
                )
                await _upsert_decision(session, row.id, "1X2", decision_payload)
                log.info(
                    "skip fixture=%s sel=%s prob=%.3f odd=%s ev=%s lam_h=%s lam_a=%s elo_h=%.1f elo_a=%.1f adj=%s signal=%.3f (samples=%.3f vol=%.3f elo_gap=%.3f) xpts_diff=%.3f var=%.3f thr=%.3f flags=%s",
                    row.id,
                    selection,
                    float(confidence),
                    odd,
                    ev,
                    lam_home,
                    lam_away,
                    float(elo_home),
                    float(elo_away),
                    adj_factor,
                    float(signal_score),
                    float(samples_score),
                    float(volatility_score),
                    float(elo_gap_score),
                    float(xpts_diff),
                    float(goal_variance),
                    float(effective_threshold),
                    feature_flags,
                )
                selection = "SKIP"
                status = "VOID"
                skips += 1

        await session.execute(
            text(
                """
                INSERT INTO predictions(
                  fixture_id, selection_code, confidence,
                  initial_odd, value_index, status, profit, signal_score, feature_flags, created_at
                )
                VALUES(
                  :fid, :sel, :conf,
                  :odd, :val, :status, NULL, :signal, :flags, now()
                )
                ON CONFLICT (fixture_id) DO UPDATE SET
                  selection_code=:sel,
                  confidence=:conf,
                  initial_odd=:odd,
                  value_index=:val,
                  status=:status,
                  signal_score=:signal,
                  feature_flags=:flags,
                  created_at=now()
                """
            ),
            {
                "fid": row.id,
                "sel": selection,
                "conf": confidence if selection != "SKIP" else None,
                "odd": initial_odd,
                "val": value_index,
                "status": status,
                "signal": signal_score if selection != "SKIP" else None,
                "flags": json.dumps(feature_flags),
            },
        )

        # Totals market (Over/Under 2.5) if odds available
        if row.over_2_5 is not None or row.under_2_5 is not None:
            best_selection = None
            best_ev = None
            best_prob = None
            best_odd = None

            if row.over_2_5 is not None:
                odd_over = q_money(row.over_2_5)
                ev_over = q_ev(p_over_2_5 * odd_over - D(1))
                if ev_over > settings.value_threshold_dec and settings.min_odd_dec <= odd_over <= settings.max_odd_dec:
                    best_selection = "OVER_2_5"
                    best_ev = ev_over
                    best_prob = p_over_2_5
                    best_odd = odd_over
            if row.under_2_5 is not None:
                odd_under = q_money(row.under_2_5)
                ev_under = q_ev(p_under_2_5 * odd_under - D(1))
                if ev_under > settings.value_threshold_dec and settings.min_odd_dec <= odd_under <= settings.max_odd_dec:
                    if best_ev is None or ev_under > best_ev:
                        best_selection = "UNDER_2_5"
                        best_ev = ev_under
                        best_prob = p_under_2_5
                        best_odd = odd_under

            if best_selection:
                await _upsert_decision(
                    session,
                    row.id,
                    "TOTAL",
                    {
                        "market": "TOTAL",
                        "action": "BET",
                        "reason": "ev_above_threshold",
                        "selection": best_selection,
                        "lam_total": float(lam_total),
                        "prob": float(best_prob) if best_prob is not None else None,
                        "odd": float(best_odd) if best_odd is not None else None,
                        "ev": float(best_ev) if best_ev is not None else None,
                        "effective_threshold": float(settings.value_threshold_dec),
                        "candidates": [
                            {
                                "selection": "OVER_2_5",
                                "prob": float(p_over_2_5),
                                "odd": float(q_money(row.over_2_5)) if row.over_2_5 is not None else None,
                                "ev": float(q_ev(p_over_2_5 * q_money(row.over_2_5) - D(1))) if row.over_2_5 is not None else None,
                            },
                            {
                                "selection": "UNDER_2_5",
                                "prob": float(p_under_2_5),
                                "odd": float(q_money(row.under_2_5)) if row.under_2_5 is not None else None,
                                "ev": float(q_ev(p_under_2_5 * q_money(row.under_2_5) - D(1))) if row.under_2_5 is not None else None,
                            },
                        ],
                    },
                )
                total_bets += 1
                await session.execute(
                    text(
                        """
                        INSERT INTO predictions_totals(
                          fixture_id, market, selection, confidence, initial_odd, value_index, created_at
                        ) VALUES (
                          :fid, 'TOTAL', :sel, :conf, :odd, :val, now()
                        )
                        ON CONFLICT (fixture_id, market) DO UPDATE SET
                          selection=:sel,
                          confidence=:conf,
                          initial_odd=:odd,
                          value_index=:val,
                          created_at=now()
                        """
                    ),
                    {
                        "fid": row.id,
                        "sel": best_selection,
                        "conf": best_prob,
                        "odd": best_odd,
                        "val": best_ev,
                    },
                )
                log.info(
                    "total fixture=%s sel=%s prob=%.3f odd=%s ev=%s lam_total=%s p_over=%.3f",
                    row.id,
                    best_selection,
                    float(best_prob),
                    best_odd,
                    best_ev,
                    lam_total,
                    float(p_over_2_5),
                )
            else:
                await _upsert_decision(
                    session,
                    row.id,
                    "TOTAL",
                    {
                        "market": "TOTAL",
                        "action": "SKIP",
                        "reason": "ev_below_threshold_or_out_of_range",
                        "lam_total": float(lam_total),
                        "effective_threshold": float(settings.value_threshold_dec),
                        "candidates": [
                            {
                                "selection": "OVER_2_5",
                                "prob": float(p_over_2_5),
                                "odd": float(q_money(row.over_2_5)) if row.over_2_5 is not None else None,
                                "ev": float(q_ev(p_over_2_5 * q_money(row.over_2_5) - D(1))) if row.over_2_5 is not None else None,
                            },
                            {
                                "selection": "UNDER_2_5",
                                "prob": float(p_under_2_5),
                                "odd": float(q_money(row.under_2_5)) if row.under_2_5 is not None else None,
                                "ev": float(q_ev(p_under_2_5 * q_money(row.under_2_5) - D(1))) if row.under_2_5 is not None else None,
                            },
                        ],
                    },
                )
                total_skips += 1

    await session.commit()
    log.info(
        "build_predictions done bets=%s skips=%s missing_odds=%s total_bets=%s total_skips=%s",
        bets,
        skips,
        missing_odds,
        total_bets,
        total_skips,
    )
    return {
        "bets": bets,
        "skips": skips,
        "missing_odds": missing_odds,
        "total_bets": total_bets,
        "total_skips": total_skips,
        "backtest": bool(settings.backtest_mode),
        "backtest_day": settings.backtest_current_date,
    }
