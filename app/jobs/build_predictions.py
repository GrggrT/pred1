from __future__ import annotations

from datetime import datetime, timedelta
import json
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
from app.services.league_model_params import estimate_dixon_coles_rho
from app.services.odds_utils import remove_overround_basic, remove_overround_binary
from app.services.dixon_coles import predict_lambda_mu as dc_predict_lambda_mu
from app.services.stacking import load_stacking_model
from app.services.calibration import load_calibrator

log = get_logger("jobs.build_predictions")

LAMBDA_EPS = D("0.001")

# Hardcoded value_threshold for 1X2 market (raised from 0.05 to 0.08 per CONTEXT.md)
VALUE_THRESHOLD_1X2 = D("0.08")

# Signal score threshold: below this → forced SKIP for 1X2
SIGNAL_SCORE_SKIP_THRESHOLD = D("0.6")

# Market configurations for all totals/BTTS/DC markets
MARKET_CONFIGS = [
    {
        "market": "TOTAL",
        "selections": [
            {"code": "OVER_2_5", "prob_key": "p_over_2_5", "odds_col": "over_2_5"},
            {"code": "UNDER_2_5", "prob_key": "p_under_2_5", "odds_col": "under_2_5"},
        ],
        "enabled_attr": "enable_total_bets",
        "threshold_attr": "value_threshold_total_dec",
        "is_binary": True,
        "group": "goals",
    },
    {
        "market": "TOTAL_1_5",
        "selections": [
            {"code": "OVER_1_5", "prob_key": "p_over_1_5", "odds_col": "over_1_5"},
            {"code": "UNDER_1_5", "prob_key": "p_under_1_5", "odds_col": "under_1_5"},
        ],
        "enabled_attr": "enable_total_1_5_bets",
        "threshold_attr": "value_threshold_total_1_5_dec",
        "is_binary": True,
        "group": "goals",
    },
    {
        "market": "TOTAL_3_5",
        "selections": [
            {"code": "OVER_3_5", "prob_key": "p_over_3_5", "odds_col": "over_3_5"},
            {"code": "UNDER_3_5", "prob_key": "p_under_3_5", "odds_col": "under_3_5"},
        ],
        "enabled_attr": "enable_total_3_5_bets",
        "threshold_attr": "value_threshold_total_3_5_dec",
        "is_binary": True,
        "group": "goals",
    },
    {
        "market": "BTTS",
        "selections": [
            {"code": "BTTS_YES", "prob_key": "p_btts_yes", "odds_col": "btts_yes"},
            {"code": "BTTS_NO", "prob_key": "p_btts_no", "odds_col": "btts_no"},
        ],
        "enabled_attr": "enable_btts_bets",
        "threshold_attr": "value_threshold_btts_dec",
        "is_binary": True,
        "group": "goals",
    },
    {
        "market": "DOUBLE_CHANCE",
        "selections": [
            {"code": "DC_1X", "prob_key": "p_dc_1x", "odds_col": "dc_1x"},
            {"code": "DC_X2", "prob_key": "p_dc_x2", "odds_col": "dc_x2"},
            {"code": "DC_12", "prob_key": "p_dc_12", "odds_col": "dc_12"},
        ],
        "enabled_attr": "enable_double_chance_bets",
        "threshold_attr": "value_threshold_double_chance_dec",
        "is_binary": False,
        "group": "double_chance",
    },
]


async def _load_dc_team_params(
    session: AsyncSession,
    league_id: int,
    season: int,
    as_of_date,
    param_source: str = "goals",
) -> Dict[int, Tuple[float, float]]:
    """Load DC attack/defense params for all teams in a league.

    Returns dict: team_id -> (attack, defense).
    Uses the latest as_of_date <= given date.
    """
    res = await session.execute(
        text("""
            SELECT team_id, attack, defense
            FROM team_strength_params
            WHERE league_id = :lid AND season = :season AND param_source = :src
              AND as_of_date = (
                  SELECT MAX(as_of_date) FROM team_strength_params
                  WHERE league_id = :lid AND season = :season
                    AND param_source = :src AND as_of_date <= :dt
              )
        """),
        {"lid": league_id, "season": season, "dt": as_of_date, "src": param_source},
    )
    return {int(r.team_id): (float(r.attack), float(r.defense)) for r in res.fetchall()}


async def _load_dc_global_params(
    session: AsyncSession,
    league_id: int,
    season: int,
    as_of_date,
    param_source: str = "goals",
) -> Dict[str, float] | None:
    """Load DC global params (HA, rho, xi) for a league.

    Returns dict with keys: home_advantage, rho, xi, or None if not found.
    """
    res = await session.execute(
        text("""
            SELECT home_advantage, rho, xi
            FROM dc_global_params
            WHERE league_id = :lid AND season = :season AND param_source = :src
              AND as_of_date = (
                  SELECT MAX(as_of_date) FROM dc_global_params
                  WHERE league_id = :lid AND season = :season
                    AND param_source = :src AND as_of_date <= :dt
              )
        """),
        {"lid": league_id, "season": season, "dt": as_of_date, "src": param_source},
    )
    row = res.first()
    if not row:
        return None
    return {
        "home_advantage": float(row.home_advantage),
        "rho": float(row.rho),
        "xi": float(row.xi),
    }


def _clamp_decimal(value: Decimal, lo: Decimal, hi: Decimal) -> Decimal:
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value


def _fatigue_factor(rest_hours: float) -> Decimal:
    """Fatigue multiplier for expected goals based on rest hours.

    Returns multiplier in [0.90, 1.02]:
      rest < 72h  (3 days):   0.90-0.95 (significant fatigue)
      rest 72-120h (3-5 days): 0.95-1.00 (moderate)
      rest 120-192h (5-8 days): 1.00-1.02 (optimal rest, slight bonus)
      rest > 192h (8+ days):  1.00 (no bonus for excessive rest)
    """
    if rest_hours < 72:
        factor = 0.90 + 0.05 * (rest_hours / 72.0)
    elif rest_hours < 120:
        factor = 0.95 + 0.05 * ((rest_hours - 72.0) / 48.0)
    elif rest_hours < 192:
        factor = 1.00 + 0.02 * ((rest_hours - 120.0) / 72.0)
    else:
        factor = 1.00
    return D(str(round(factor, 4)))


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
            os.over_1_5, os.under_1_5, os.over_3_5, os.under_3_5,
            os.btts_yes, os.btts_no,
            os.dc_1x, os.dc_x2, os.dc_12,
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
                   mi.home_rest_hours, mi.away_rest_hours,
                   o.home_win, o.draw, o.away_win,
                   o.over_2_5, o.under_2_5,
                   o.over_1_5, o.under_1_5, o.over_3_5, o.under_3_5,
                   o.btts_yes, o.btts_no,
                   o.dc_1x, o.dc_x2, o.dc_12,
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


async def _league_baseline_cache(
    session: AsyncSession,
    cache: Dict[Tuple[int, int, str], Tuple[Decimal, Decimal, Decimal, Decimal]],
    league_id: int,
    season: int,
    cutoff: datetime,
) -> Tuple[Decimal, Decimal, Decimal, Decimal]:
    """Return (home_avg, away_avg, draw_freq, dc_rho) for a league/season/date."""
    key = (league_id, season, cutoff.date().isoformat())
    if key in cache:
        return cache[key]

    date_key = cutoff.date()
    row = (
        await session.execute(
            text(
                """
                SELECT avg_home_xg, avg_away_xg, draw_freq, dc_rho
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
        cache[key] = (home_avg, away_avg, draw_freq, dc_rho)
        return cache[key]

    # Fallback: compute baseline directly from fixtures.
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

    rho = await estimate_dixon_coles_rho(
        session,
        league_id=league_id,
        season=season,
        before_date=date_key,
        lam_home=home_avg,
        lam_away=away_avg,
    )
    dc_rho = rho if rho is not None else q_prob(D(0))

    try:
        await session.execute(
            text(
                """
                INSERT INTO league_baselines(
                  league_id, season, date_key,
                  avg_home_xg, avg_away_xg, draw_freq, avg_goals,
                  dc_rho, calib_alpha
                )
                VALUES(:lid, :season, :dk, :ah, :aa, :df, :avg_goals, :rho, 1.0)
                ON CONFLICT (league_id, season, date_key) DO UPDATE SET
                  avg_home_xg=:ah,
                  avg_away_xg=:aa,
                  draw_freq=:df,
                  avg_goals=:avg_goals,
                  dc_rho=:rho
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
            },
        )
    except Exception:
        pass

    cache[key] = (home_avg, away_avg, draw_freq, dc_rho)
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


def _evaluate_market_cfg(
    mcfg: dict,
    probs_map: Dict[str, Decimal],
    row: Any,
    min_odd: Decimal,
    max_odd: Decimal,
) -> dict | None:
    """Evaluate a single market config, return result dict or None if no valid bet."""
    threshold = getattr(settings, mcfg["threshold_attr"])
    enabled = getattr(settings, mcfg["enabled_attr"])
    if not enabled:
        return None

    # Collect odds from row
    odds_values = []
    for sel in mcfg["selections"]:
        odd_raw = getattr(row, sel["odds_col"], None)
        odds_values.append(odd_raw)
    if not any(v is not None for v in odds_values):
        return None

    # For binary markets, compute fair odds via remove_overround_binary
    # For 3-way markets, use remove_overround_basic
    fair_odds = {}
    if mcfg["is_binary"] and len(mcfg["selections"]) == 2:
        raw_a = getattr(row, mcfg["selections"][0]["odds_col"], None)
        raw_b = getattr(row, mcfg["selections"][1]["odds_col"], None)
        fa, fb = remove_overround_binary(raw_a, raw_b)
        fair_odds[mcfg["selections"][0]["code"]] = fa
        fair_odds[mcfg["selections"][1]["code"]] = fb
    else:
        raw_vals = [getattr(row, s["odds_col"], None) for s in mcfg["selections"]]
        fair_vals = remove_overround_basic(*raw_vals)
        for i, s in enumerate(mcfg["selections"]):
            fair_odds[s["code"]] = fair_vals[i] if i < len(fair_vals) else None

    best_sel = None
    best_ev = None
    best_prob = None
    best_odd = None
    candidates = []

    for sel in mcfg["selections"]:
        prob = probs_map.get(sel["prob_key"])
        odd_raw = getattr(row, sel["odds_col"], None)
        if prob is None:
            candidates.append({"selection": sel["code"], "prob": None, "odd": None, "ev": None})
            continue
        if odd_raw is None:
            candidates.append({"selection": sel["code"], "prob": float(prob), "odd": None, "ev": None})
            continue
        odd = q_money(odd_raw)
        ev = q_ev(prob * odd - D(1))
        candidates.append({"selection": sel["code"], "prob": float(prob), "odd": float(odd), "ev": float(ev)})
        if ev > threshold and min_odd <= odd <= max_odd:
            if best_ev is None or ev > best_ev:
                best_sel = sel["code"]
                best_ev = ev
                best_prob = prob
                best_odd = odd

    return {
        "market": mcfg["market"],
        "group": mcfg["group"],
        "best_sel": best_sel,
        "best_ev": best_ev,
        "best_prob": best_prob,
        "best_odd": best_odd,
        "threshold": threshold,
        "candidates": candidates,
        "fair_odds": {k: float(v) if v is not None else None for k, v in fair_odds.items()},
    }


async def _process_secondary_markets(
    session: AsyncSession,
    row: Any,
    lam_total: Decimal,
    probs_map: Dict[str, Decimal],
    market_bets: Dict[str, int],
    market_skips: Dict[str, int],
):
    """Evaluate all secondary markets (totals, BTTS, DC) and create predictions."""
    results: list[dict] = []
    for mcfg in MARKET_CONFIGS:
        result = _evaluate_market_cfg(mcfg, probs_map, row, settings.min_odd_dec, settings.max_odd_dec)
        if result is not None:
            results.append(result)
        else:
            market_skips[mcfg["market"]] = market_skips.get(mcfg["market"], 0) + 1

    # Correlation filter: at most max_total_bets_per_fixture from "goals" group
    # (TOTAL 1.5/2.5/3.5 + BTTS are correlated — pick best EV)
    goals_group = [r for r in results if r["group"] == "goals" and r["best_sel"] is not None]
    other = [r for r in results if r["group"] != "goals" or r["best_sel"] is None]
    if len(goals_group) > settings.max_total_bets_per_fixture:
        goals_group.sort(key=lambda r: r["best_ev"] or D(0), reverse=True)
        kept = goals_group[:settings.max_total_bets_per_fixture]
        skipped_corr = goals_group[settings.max_total_bets_per_fixture:]
        for sk in skipped_corr:
            await _upsert_decision(session, row.id, sk["market"], {
                "market": sk["market"], "action": "SKIP",
                "reason": "correlated_market_better_ev",
                "candidates": sk["candidates"],
                "effective_threshold": float(sk["threshold"]),
            })
            market_skips[sk["market"]] = market_skips.get(sk["market"], 0) + 1
        results = kept + other
    else:
        results = goals_group + other

    for result in results:
        mkt = result["market"]
        if result["best_sel"] is not None:
            market_bets[mkt] = market_bets.get(mkt, 0) + 1
            decision_payload = {
                "market": mkt, "action": "BET", "reason": "ev_above_threshold",
                "selection": result["best_sel"], "lam_total": float(lam_total),
                "prob": float(result["best_prob"]),
                "odd": float(result["best_odd"]),
                "ev": float(result["best_ev"]),
                "effective_threshold": float(result["threshold"]),
                "candidates": result["candidates"],
                "fair_odds": result["fair_odds"],
            }
            # Kelly fraction (informational; stored in decision payload)
            if settings.enable_kelly and result["best_prob"] and result["best_odd"]:
                from app.services.kelly import kelly_fraction as _kelly_frac
                kf = _kelly_frac(
                    result["best_prob"], result["best_odd"],
                    fraction=D(settings.kelly_fraction),
                    max_fraction=D(settings.kelly_max_fraction),
                )
                decision_payload["kelly_fraction"] = float(kf)
            await _upsert_decision(session, row.id, mkt, decision_payload)
            await session.execute(
                text("""
                    INSERT INTO predictions_totals(fixture_id, market, selection, confidence, initial_odd, value_index, created_at)
                    VALUES (:fid, :mkt, :sel, :conf, :odd, :val, now())
                    ON CONFLICT (fixture_id, market) DO UPDATE SET selection=:sel, confidence=:conf, initial_odd=:odd, value_index=:val, created_at=now()
                """),
                {"fid": row.id, "mkt": mkt, "sel": result["best_sel"],
                 "conf": result["best_prob"], "odd": result["best_odd"], "val": result["best_ev"]},
            )
            log.info(
                "market_bet fixture=%s market=%s sel=%s prob=%.3f odd=%s ev=%s",
                row.id, mkt, result["best_sel"],
                float(result["best_prob"]), result["best_odd"], result["best_ev"],
            )
        else:
            market_skips[mkt] = market_skips.get(mkt, 0) + 1
            await _upsert_decision(session, row.id, mkt, {
                "market": mkt, "action": "SKIP",
                "reason": "ev_below_threshold_or_out_of_range",
                "lam_total": float(lam_total),
                "effective_threshold": float(result["threshold"]),
                "candidates": result["candidates"],
            })


async def run(session: AsyncSession):
    # Keep Elo up to date for all finished fixtures before generating new predictions.
    elo_cutoff = utcnow() if settings.backtest_mode else None
    await apply_elo_from_fixtures(session, league_ids=settings.league_ids, cutoff=elo_cutoff)

    # Load stacking meta-model (primary prediction path)
    stacking_model = None
    if settings.use_stacking:
        stacking_model = await load_stacking_model(session)
        if stacking_model is not None:
            log.info("build_predictions stacking model loaded (%d features)", len(stacking_model.feature_names))
        else:
            log.warning("build_predictions USE_STACKING=true but no trained model found; using DC-only fallback")

    # Load Dirichlet calibrator if enabled
    dirichlet_calibrator = None
    if settings.use_dirichlet_calib:
        dirichlet_calibrator = await load_calibrator(session)
        if dirichlet_calibrator is not None:
            log.info("build_predictions Dirichlet calibrator loaded")
        else:
            log.warning("build_predictions USE_DIRICHLET_CALIB=true but no trained calibrator found")

    # Log per-league controls
    league_1x2_list = settings.league_1x2_enabled
    league_ev_overrides = settings.league_ev_threshold_overrides
    if league_1x2_list:
        log.info("build_predictions 1X2 enabled for leagues: %s", league_1x2_list)
    if league_ev_overrides:
        log.info("build_predictions EV threshold overrides: %s", league_ev_overrides)
    enabled_markets = [m["market"] for m in MARKET_CONFIGS if getattr(settings, m["enabled_attr"])]
    log.info("build_predictions enabled secondary markets: %s", enabled_markets or "NONE")

    rows = await _target_rows(session)
    if not rows:
        log.info("build_predictions no fixtures")
        return

    log.info("build_predictions fixtures=%s", len(rows))
    # Avoid leakage from future standings/injuries in backtest calibration runs.
    use_standings = bool(settings.enable_standings) and not settings.backtest_mode
    use_injuries = bool(settings.enable_injuries) and not settings.backtest_mode
    league_cache: Dict[Tuple[int, int, str], Tuple[Decimal, Decimal, Decimal, Decimal]] = {}
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
    new_market_bets: Dict[str, int] = {}
    new_market_skips: Dict[str, int] = {}
    missing_odds = 0

    # Dixon-Coles: always enabled (DC is the core model)
    use_dc_xg = bool(settings.dc_use_xg)
    dc_team_cache: Dict[Tuple[int, int], Dict[int, Tuple[float, float]]] = {}
    dc_global_cache: Dict[Tuple[int, int], Dict[str, float] | None] = {}
    dc_xg_team_cache: Dict[Tuple[int, int], Dict[int, Tuple[float, float]]] = {}
    dc_xg_global_cache: Dict[Tuple[int, int], Dict[str, float] | None] = {}
    log.info("build_predictions DC core active (dc_use_xg=%s)", use_dc_xg)

    for row in rows:
        base_home, base_away, league_draw_freq, dc_rho = await _league_baseline_cache(
            session, league_cache, row.league_id, row.season, row.kickoff
        )

        # Elo
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

        # === DC lambda calculation (primary) or Poisson fallback ===
        dc_core_used = False
        dc_key = (int(row.league_id), int(row.season))
        if dc_key not in dc_team_cache:
            kickoff_date = row.kickoff.date() if hasattr(row.kickoff, "date") else row.kickoff
            dc_team_cache[dc_key] = await _load_dc_team_params(
                session, dc_key[0], dc_key[1], kickoff_date,
            )
            dc_global_cache[dc_key] = await _load_dc_global_params(
                session, dc_key[0], dc_key[1], kickoff_date,
            )
        if use_dc_xg and dc_key not in dc_xg_team_cache:
            dc_xg_team_cache[dc_key] = await _load_dc_team_params(
                session, dc_key[0], dc_key[1], kickoff_date, param_source="xg",
            )
            dc_xg_global_cache[dc_key] = await _load_dc_global_params(
                session, dc_key[0], dc_key[1], kickoff_date, param_source="xg",
            )

        dc_teams = dc_team_cache[dc_key]
        dc_globals = dc_global_cache[dc_key]
        home_dc = dc_teams.get(int(row.home_team_id))
        away_dc = dc_teams.get(int(row.away_team_id))

        if home_dc and away_dc and dc_globals:
            att_h, def_h = home_dc
            att_a, def_a = away_dc
            lam_f, mu_f = dc_predict_lambda_mu(
                att_h, def_h, att_a, def_a, dc_globals["home_advantage"],
            )
            lam_home = max(LAMBDA_EPS, q_money(D(lam_f)))
            lam_away = max(LAMBDA_EPS, q_money(D(mu_f)))
            dc_rho = q_prob(D(dc_globals["rho"]))
            dc_core_used = True
        else:
            # Fallback: rolling-average Poisson lambdas (no DC params available)
            log.warning("no_dc_params fixture=%s league=%s, using Poisson fallback", row.id, row.league_id)
            home_att, home_def = _weighted_attack(row, "home")
            away_att, away_def = _weighted_attack(row, "away")
            home_att_factor = safe_div(home_att, base_home, default=1)
            away_def_factor = safe_div(away_def, base_home, default=1)
            lam_home = q_money(base_home * home_att_factor * away_def_factor)
            away_att_factor = safe_div(away_att, base_away, default=1)
            home_def_factor = safe_div(home_def, base_away, default=1)
            lam_away = q_money(base_away * away_att_factor * home_def_factor)
            lam_home = max(LAMBDA_EPS, lam_home)
            lam_away = max(LAMBDA_EPS, lam_away)

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

        # Rest hours fatigue adjustment.
        home_fatigue = D(1)
        away_fatigue = D(1)
        if settings.enable_rest_adjustment:
            home_rest = getattr(row, "home_rest_hours", None)
            away_rest = getattr(row, "away_rest_hours", None)
            if home_rest is not None:
                home_fatigue = _fatigue_factor(float(home_rest))
                lam_home = max(LAMBDA_EPS, q_money(lam_home * home_fatigue))
            if away_rest is not None:
                away_fatigue = _fatigue_factor(float(away_rest))
                lam_away = max(LAMBDA_EPS, q_money(lam_away * away_fatigue))

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

        # Double Chance probabilities (from 1X2 probs — computed later after model selection)
        # Placeholder — will be set after p_home/p_draw/p_away are finalized
        p_dc_1x = p_dc_x2 = p_dc_12 = D(0)

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

        # === Step 1: Base model predictions ===
        p_home_poisson, p_draw_poisson, p_away_poisson = match_probs(lam_home, lam_away, k_max=10)
        p_home_dc, p_draw_dc, p_away_dc = match_probs_dixon_coles(lam_home, lam_away, rho=dc_rho, k_max=10)
        xpts_home = (D(3) * p_home_poisson + p_draw_poisson).quantize(D("0.001"))
        xpts_away = (D(3) * p_away_poisson + p_draw_poisson).quantize(D("0.001"))
        xpts_diff = (xpts_home - xpts_away).quantize(D("0.001"))

        # DC-xG predictions (fallback to DC-goals)
        p_home_dc_xg = p_home_dc
        p_draw_dc_xg = p_draw_dc
        p_away_dc_xg = p_away_dc
        dc_xg_available = False
        if use_dc_xg:
            dc_xg_teams = dc_xg_team_cache.get(dc_key, {})
            dc_xg_globals = dc_xg_global_cache.get(dc_key)
            home_dc_xg = dc_xg_teams.get(int(row.home_team_id))
            away_dc_xg = dc_xg_teams.get(int(row.away_team_id))
            if home_dc_xg and away_dc_xg and dc_xg_globals:
                att_h_xg, def_h_xg = home_dc_xg
                att_a_xg, def_a_xg = away_dc_xg
                lam_xg, mu_xg = dc_predict_lambda_mu(
                    att_h_xg, def_h_xg, att_a_xg, def_a_xg, dc_xg_globals["home_advantage"],
                )
                lam_xg_d = max(LAMBDA_EPS, q_money(D(lam_xg)))
                mu_xg_d = max(LAMBDA_EPS, q_money(D(mu_xg)))
                p_home_dc_xg, p_draw_dc_xg, p_away_dc_xg = match_probs_dixon_coles(
                    lam_xg_d, mu_xg_d, rho=q_prob(D(0)), k_max=10,
                )
                dc_xg_available = True

        # Fair odds (needed before stacking)
        fair_home, fair_draw, fair_away = remove_overround_basic(
            row.home_win, row.draw, row.away_win
        )

        # === Step 2: Model selection (Stacking → DC-only → Poisson fallback) ===
        if stacking_model is not None and dc_core_used:
            stacking_features = {
                "p_home_poisson": float(p_home_poisson),
                "p_draw_poisson": float(p_draw_poisson),
                "p_away_poisson": float(p_away_poisson),
                "p_home_dc": float(p_home_dc),
                "p_draw_dc": float(p_draw_dc),
                "p_away_dc": float(p_away_dc),
                "p_home_dc_xg": float(p_home_dc_xg),
                "p_draw_dc_xg": float(p_draw_dc_xg),
                "p_away_dc_xg": float(p_away_dc_xg),
                "elo_diff": float(elo_diff),
                "fair_home": float(fair_home) if fair_home is not None else 0.0,
                "fair_draw": float(fair_draw) if fair_draw is not None else 0.0,
                "fair_away": float(fair_away) if fair_away is not None else 0.0,
            }
            p_home, p_draw, p_away = stacking_model.predict(stacking_features)
            prob_source = "stacking"
        elif dc_core_used:
            # Fallback 1: DC-only (no stacking model)
            p_home, p_draw, p_away = p_home_dc, p_draw_dc, p_away_dc
            prob_source = "dc"
        else:
            # Fallback 2: Poisson baseline (no DC params)
            p_home, p_draw, p_away = p_home_poisson, p_draw_poisson, p_away_poisson
            prob_source = "poisson_fallback"

        # === Step 3: Calibration (optional Dirichlet) ===
        p_home_pre_calib = p_home
        p_draw_pre_calib = p_draw
        p_away_pre_calib = p_away
        calibration_method = "none"
        if settings.use_dirichlet_calib and dirichlet_calibrator is not None:
            p_home, p_draw, p_away = dirichlet_calibrator.calibrate_single(p_home, p_draw, p_away)
            calibration_method = "dirichlet"
        probs = {"HOME_WIN": p_home, "DRAW": p_draw, "AWAY_WIN": p_away}

        # Double Chance probabilities (computed from final 1X2 probs)
        p_dc_1x = q_prob(_clamp_decimal(p_home + p_draw, D(0), D(1)))
        p_dc_x2 = q_prob(_clamp_decimal(p_draw + p_away, D(0), D(1)))
        p_dc_12 = q_prob(_clamp_decimal(p_home + p_away, D(0), D(1)))

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
            "lam_home": float(lam_home),
            "lam_away": float(lam_away),
            "lam_total": float(lam_total),
            "elo_home": float(elo_home),
            "elo_away": float(elo_away),
            "elo_diff": float(elo_diff),
            "prob_source": prob_source,
            "dc_core": dc_core_used,
            "league_draw_freq": float(league_draw_freq),
            "dc_rho": float(dc_rho),
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
            "home_fatigue": float(home_fatigue),
            "away_fatigue": float(away_fatigue),
            "xpts_diff": float(xpts_diff),
            "p_home": float(p_home),
            "p_draw": float(p_draw),
            "p_away": float(p_away),
            "p_home_poisson": float(p_home_poisson),
            "p_draw_poisson": float(p_draw_poisson),
            "p_away_poisson": float(p_away_poisson),
            "p_home_dc": float(p_home_dc),
            "p_draw_dc": float(p_draw_dc),
            "p_away_dc": float(p_away_dc),
            "p_home_dc_xg": float(p_home_dc_xg),
            "p_draw_dc_xg": float(p_draw_dc_xg),
            "p_away_dc_xg": float(p_away_dc_xg),
            "dc_xg_available": dc_xg_available,
            "fair_home": float(fair_home) if fair_home is not None else None,
            "fair_draw": float(fair_draw) if fair_draw is not None else None,
            "fair_away": float(fair_away) if fair_away is not None else None,
            "calibration_method": calibration_method,
            "p_home_pre_calib": float(p_home_pre_calib),
            "p_draw_pre_calib": float(p_draw_pre_calib),
            "p_away_pre_calib": float(p_away_pre_calib),
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

        # Per-league EV threshold override (e.g. EPL=0.12, Ligue1=0.12)
        league_override = settings.league_ev_threshold_overrides.get(int(row.league_id))
        base_threshold = league_override if league_override is not None else VALUE_THRESHOLD_1X2

        if signal_score < D("0.5"):
            effective_threshold = q_ev(base_threshold + D("0.05"))
        elif signal_score > D("0.8"):
            effective_threshold = q_ev(base_threshold - D("0.01"))
        else:
            effective_threshold = base_threshold
        feature_flags["effective_threshold"] = float(effective_threshold)

        # Per-league 1X2 bet enablement check
        league_1x2_list = settings.league_1x2_enabled
        league_1x2_blocked = bool(league_1x2_list) and int(row.league_id) not in league_1x2_list

        # Probs map for all secondary markets (used by _process_secondary_markets)
        probs_map = {
            "p_over_2_5": p_over_2_5, "p_under_2_5": p_under_2_5,
            "p_over_1_5": p_over_1_5, "p_under_1_5": p_under_1_5,
            "p_over_3_5": p_over_3_5, "p_under_3_5": p_under_3_5,
            "p_btts_yes": p_btts_yes, "p_btts_no": p_btts_no,
            "p_dc_1x": p_dc_1x, "p_dc_x2": p_dc_x2, "p_dc_12": p_dc_12,
        }

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
        await _upsert_decision(
            session,
            row.id,
            "INFO_DC",
            _info_payload(
                "INFO_DC",
                {"DC_1X": p_dc_1x, "DC_X2": p_dc_x2, "DC_12": p_dc_12},
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

        # Forced SKIP for low signal_score (not enough data confidence)
        if signal_score < SIGNAL_SCORE_SKIP_THRESHOLD and selection is not None:
            decision_payload["action"] = "SKIP"
            decision_payload["reason"] = "signal_score_below_threshold"
            decision_payload["selection"] = selection
            decision_payload["signal_score"] = float(signal_score)
            await _upsert_decision(session, row.id, "1X2", decision_payload)
            selection = "SKIP"
            status = "VOID"
            skips += 1
            log.info(
                "skip_low_signal fixture=%s signal=%.3f < %.3f",
                row.id, float(signal_score), float(SIGNAL_SCORE_SKIP_THRESHOLD),
            )

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
                    "sel": "SKIP",
                    "conf": None,
                    "odd": None,
                    "val": None,
                    "status": "VOID",
                    "signal": signal_score,
                    "flags": json.dumps(feature_flags),
                },
            )
            # Still process all secondary markets below (totals, BTTS, DC)
            await _process_secondary_markets(
                session, row, lam_total, probs_map, new_market_bets, new_market_skips,
            )
            continue

        # Block 1X2 bets for leagues not in LEAGUE_1X2_ENABLED list
        if league_1x2_blocked and selection is not None:
            decision_payload["action"] = "SKIP"
            decision_payload["reason"] = "league_1x2_disabled"
            decision_payload["league_id"] = int(row.league_id)
            await _upsert_decision(session, row.id, "1X2", decision_payload)
            selection = "SKIP"
            status = "VOID"
            skips += 1
            log.info("skip_league_disabled fixture=%s league=%s", row.id, row.league_id)

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
                    "bet fixture=%s sel=%s prob=%.3f odd=%s ev=%s lam_h=%s lam_a=%s elo_h=%.1f elo_a=%.1f signal=%.3f (samples=%.3f vol=%.3f elo_gap=%.3f) xpts_diff=%.3f var=%.3f thr=%.3f flags=%s",
                    row.id,
                    selection,
                    float(confidence),
                    odd,
                    ev,
                    lam_home,
                    lam_away,
                    float(elo_home),
                    float(elo_away),
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
                    "skip fixture=%s sel=%s prob=%.3f odd=%s ev=%s lam_h=%s lam_a=%s elo_h=%.1f elo_a=%.1f signal=%.3f (samples=%.3f vol=%.3f elo_gap=%.3f) xpts_diff=%.3f var=%.3f thr=%.3f flags=%s",
                    row.id,
                    selection,
                    float(confidence),
                    odd,
                    ev,
                    lam_home,
                    lam_away,
                    float(elo_home),
                    float(elo_away),
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

        # Kelly fraction (informational; stored in feature_flags)
        if settings.enable_kelly and selection != "SKIP" and confidence and initial_odd:
            from app.services.kelly import kelly_fraction as _kelly_frac
            kf = _kelly_frac(
                confidence, initial_odd,
                fraction=D(settings.kelly_fraction),
                max_fraction=D(settings.kelly_max_fraction),
            )
            feature_flags["kelly_fraction"] = float(kf)

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

        # Process all secondary markets (totals, BTTS, DC)
        await _process_secondary_markets(
            session, row, lam_total, probs_map, new_market_bets, new_market_skips,
        )

    await session.commit()
    total_bets = sum(new_market_bets.values())
    total_skips = sum(new_market_skips.values())
    log.info(
        "build_predictions done bets=%s skips=%s missing_odds=%s total_bets=%s total_skips=%s market_bets=%s",
        bets,
        skips,
        missing_odds,
        total_bets,
        total_skips,
        new_market_bets,
    )
    return {
        "bets": bets,
        "skips": skips,
        "missing_odds": missing_odds,
        "total_bets": total_bets,
        "total_skips": total_skips,
        "market_bets": new_market_bets,
        "market_skips": new_market_skips,
        "backtest": bool(settings.backtest_mode),
        "backtest_day": settings.backtest_current_date,
    }
