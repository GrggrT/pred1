from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from math import log
from typing import Callable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import STATS_EPOCH, settings
from app.core.decimalutils import D
from app.core.logger import get_logger
from app.core.timeutils import utcnow
from app.services.metrics import ranked_probability_score

logger = get_logger("jobs.quality_report")

ODDS_BUCKETS = ("1.0-1.49", "1.5-1.99", "2.0-2.99", "3.0-4.99", "5.0+")
TIME_BUCKETS = ("<6h", "6-12h", "12-24h", "1-3d", "3-7d", "7d+", "unknown")
QUALITY_REPORT_CACHE_KEY = "quality_report"
SHADOW_FILTERS = {
    "1x2": [
        {
            "id": "exclude_league_39",
            "label": "Exclude league_id 39 (Premier League)",
            "exclude_league_ids": [39],
        },
        {
            "id": "exclude_odds_2_0_2_99",
            "label": "Exclude odds bucket 2.0-2.99",
            "exclude_odds_buckets": ["2.0-2.99"],
        },
    ],
    "total": [
        {
            "id": "exclude_league_94_140",
            "label": "Exclude league_id 94 and 140 (Primeira, La Liga)",
            "exclude_league_ids": [94, 140],
        },
        {
            "id": "exclude_odds_2_0_2_99",
            "label": "Exclude odds bucket 2.0-2.99",
            "exclude_odds_buckets": ["2.0-2.99"],
        },
    ],
}


@dataclass
class BetRow:
    fixture_id: int
    league_id: int
    league_name: str
    kickoff: object
    created_at: object
    selection: str
    odd: Decimal
    prob: Decimal
    status: str
    profit: Decimal
    closing_odd: Decimal | None
    feature_flags: dict | None = None
    home_goals: int | None = None
    away_goals: int | None = None
    market: str = "1X2"


def _odds_bucket(odd: Decimal) -> str:
    o = float(odd)
    if o < 1.5:
        return "1.0-1.49"
    if o < 2.0:
        return "1.5-1.99"
    if o < 3.0:
        return "2.0-2.99"
    if o < 5.0:
        return "3.0-4.99"
    return "5.0+"


def _time_bucket(kickoff, created_at) -> str:
    if kickoff is None or created_at is None:
        return "unknown"
    delta_hours = (kickoff - created_at).total_seconds() / 3600
    if delta_hours < 6:
        return "<6h"
    if delta_hours < 12:
        return "6-12h"
    if delta_hours < 24:
        return "12-24h"
    if delta_hours < 72:
        return "1-3d"
    if delta_hours < 168:
        return "3-7d"
    return "7d+"


def _safe_prob(prob: Decimal) -> float:
    val = float(prob)
    if val <= 1e-6:
        return 1e-6
    if val >= 1 - 1e-6:
        return 1 - 1e-6
    return val


def _clv_pct(closing_odd: Decimal | None, initial_odd: Decimal | None) -> float:
    if closing_odd is None or initial_odd is None:
        return 0.0
    if initial_odd == 0:
        return 0.0
    return float((Decimal(closing_odd) / Decimal(initial_odd)) - Decimal(1)) * 100


async def get_cached(session: AsyncSession) -> dict | None:
    res = await session.execute(
        text(
            """
            SELECT payload FROM api_cache
            WHERE cache_key=:k AND expires_at > now()
            """
        ),
        {"k": QUALITY_REPORT_CACHE_KEY},
    )
    row = res.first()
    return row[0] if row else None


async def save_cached(session: AsyncSession, report: dict, ttl_seconds: int) -> None:
    expires = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
    await session.execute(
        text(
            """
            INSERT INTO api_cache(cache_key, payload, expires_at)
            VALUES(:k, CAST(:p AS jsonb), :e)
            ON CONFLICT (cache_key)
            DO UPDATE SET payload=CAST(:p AS jsonb), expires_at=:e
            """
        ),
        {
            "k": QUALITY_REPORT_CACHE_KEY,
            "p": json.dumps(report, ensure_ascii=False),
            "e": expires,
        },
    )


def _summarize(rows: list[BetRow]) -> dict:
    total = len(rows)
    wins = sum(1 for r in rows if r.status == "WIN")
    pnl = sum(float(r.profit or 0) for r in rows)
    roi = pnl / total * 100 if total else 0.0
    avg_odd = sum(float(r.odd) for r in rows) / total if total else 0.0
    clv_rows = [r for r in rows if r.closing_odd is not None]
    clv_avg = (
        sum(_clv_pct(r.closing_odd, r.odd) for r in clv_rows) / len(clv_rows) if clv_rows else 0.0
    )
    clv_cov = len(clv_rows)
    clv_cov_pct = clv_cov / total * 100 if total else 0.0
    return {
        "bets": total,
        "win_rate": wins / total * 100 if total else 0.0,
        "roi": roi,
        "avg_odd": avg_odd,
        "clv_avg_pct": clv_avg,
        "clv_cov": clv_cov,
        "clv_cov_pct": clv_cov_pct,
    }


def _group(rows: list[BetRow], key_fn: Callable[[BetRow], object], order: tuple[str, ...] | None = None) -> list[dict]:
    buckets: dict[object, list[BetRow]] = defaultdict(list)
    for r in rows:
        buckets[key_fn(r)].append(r)
    if order:
        order_index = {key: idx for idx, key in enumerate(order)}
        keys = sorted(buckets.keys(), key=lambda k: order_index.get(k, len(order_index)))
    else:
        keys = sorted(buckets.keys())
    out = []
    for key in keys:
        stats = _summarize(buckets[key])
        stats["key"] = key
        out.append(stats)
    return out


def _apply_shadow_filters(
    rows: list[BetRow],
    *,
    exclude_league_ids: list[int] | None = None,
    exclude_odds_buckets: list[str] | None = None,
    exclude_time_buckets: list[str] | None = None,
) -> list[BetRow]:
    filtered = rows
    if exclude_league_ids:
        ids = {int(x) for x in exclude_league_ids}
        filtered = [r for r in filtered if int(r.league_id or 0) not in ids]
    if exclude_odds_buckets:
        buckets = {str(x) for x in exclude_odds_buckets}
        filtered = [r for r in filtered if _odds_bucket(r.odd) not in buckets]
    if exclude_time_buckets:
        buckets = {str(x) for x in exclude_time_buckets}
        filtered = [r for r in filtered if _time_bucket(r.kickoff, r.created_at) not in buckets]
    return filtered


def _build_shadow_filters(
    rows: list[BetRow],
    market_key: str,
    base_summary: dict,
    base_calibration: dict,
) -> list[dict]:
    defs = SHADOW_FILTERS.get(market_key, [])
    if not defs:
        return []
    out: list[dict] = []
    base_bets = int(base_summary.get("bets") or 0)
    base_roi = float(base_summary.get("roi") or 0.0)
    base_clv_avg = float(base_summary.get("clv_avg_pct") or 0.0)
    base_clv_cov = float(base_summary.get("clv_cov_pct") or 0.0)
    base_brier = float(base_calibration.get("brier") or 0.0)
    base_logloss = float(base_calibration.get("logloss") or 0.0)
    base_rps = float(base_calibration.get("rps") or 0.0)

    for item in defs:
        filters = {
            "exclude_league_ids": item.get("exclude_league_ids") or [],
            "exclude_odds_buckets": item.get("exclude_odds_buckets") or [],
            "exclude_time_buckets": item.get("exclude_time_buckets") or [],
        }
        filtered = _apply_shadow_filters(
            rows,
            exclude_league_ids=filters["exclude_league_ids"],
            exclude_odds_buckets=filters["exclude_odds_buckets"],
            exclude_time_buckets=filters["exclude_time_buckets"],
        )
        summary = _summarize(filtered)
        calibration = _calibration(filtered)
        delta = {
            "bets": int(summary.get("bets") or 0) - base_bets,
            "roi": float(summary.get("roi") or 0.0) - base_roi,
            "clv_avg_pct": float(summary.get("clv_avg_pct") or 0.0) - base_clv_avg,
            "clv_cov_pct": float(summary.get("clv_cov_pct") or 0.0) - base_clv_cov,
            "brier": float(calibration.get("brier") or 0.0) - base_brier,
            "logloss": float(calibration.get("logloss") or 0.0) - base_logloss,
            "rps": float(calibration.get("rps") or 0.0) - base_rps,
        }
        out.append(
            {
                "id": item.get("id") or "shadow",
                "label": item.get("label") or item.get("id") or "shadow",
                "filters": filters,
                "summary": summary,
                "calibration": calibration,
                "delta": delta,
            }
        )
    return out


def _outcome_index(home_goals: int | None, away_goals: int | None) -> int | None:
    """Determine outcome index from final score. 0=home, 1=draw, 2=away."""
    if home_goals is None or away_goals is None:
        return None
    if home_goals > away_goals:
        return 0
    elif home_goals == away_goals:
        return 1
    else:
        return 2


def _calibration(rows: list[BetRow]) -> dict:
    if not rows:
        return {"brier": 0.0, "logloss": 0.0, "rps": 0.0, "bins": []}
    brier_sum = 0.0
    logloss_sum = 0.0
    rps_sum = 0.0
    rps_count = 0
    bins: dict[int, list[BetRow]] = defaultdict(list)
    for r in rows:
        p = _safe_prob(r.prob)
        y = 1.0 if r.status == "WIN" else 0.0
        brier_sum += (p - y) ** 2
        logloss_sum += -(y * log(p) + (1 - y) * log(1 - p))
        # RPS from full distribution in feature_flags + actual outcome from goals
        ff = r.feature_flags
        oi = _outcome_index(r.home_goals, r.away_goals)
        if ff and oi is not None and "p_home" in ff and "p_draw" in ff and "p_away" in ff:
            p_h = D(str(ff["p_home"]))
            p_d = D(str(ff["p_draw"]))
            p_a = D(str(ff["p_away"]))
            if p_h + p_d + p_a > 0:
                rps_sum += float(ranked_probability_score((p_h, p_d, p_a), oi))
                rps_count += 1
        bin_idx = min(int(p * 10), 9)
        bins[bin_idx].append(r)
    out_bins = []
    for idx in range(10):
        group = bins.get(idx, [])
        if not group:
            continue
        avg_prob = sum(_safe_prob(r.prob) for r in group) / len(group)
        win_rate = sum(1 for r in group if r.status == "WIN") / len(group)
        out_bins.append(
            {
                "bin": f"{idx/10:.1f}-{(idx+1)/10:.1f}",
                "bets": len(group),
                "avg_prob": avg_prob,
                "win_rate": win_rate,
            }
        )
    return {
        "brier": brier_sum / len(rows),
        "logloss": logloss_sum / len(rows),
        "rps": rps_sum / rps_count if rps_count else 0.0,
        "bins": out_bins,
    }


async def _fetch_1x2(session: AsyncSession) -> list[BetRow]:
    res = await session.execute(
        text(
            """
            SELECT
              f.id AS fixture_id,
              f.league_id AS league_id,
              COALESCE(l.name, '') AS league_name,
              f.kickoff AS kickoff,
              f.home_goals AS home_goals,
              f.away_goals AS away_goals,
              p.created_at AS created_at,
              p.selection_code AS selection,
              p.initial_odd AS odd,
              p.confidence AS prob,
              p.status AS status,
              p.profit AS profit,
              p.feature_flags AS feature_flags,
              o.home_win AS close_home,
              o.draw AS close_draw,
              o.away_win AS close_away
            FROM predictions p
            JOIN fixtures f ON f.id=p.fixture_id
            LEFT JOIN leagues l ON l.id=f.league_id
            LEFT JOIN LATERAL (
              SELECT home_win, draw, away_win
              FROM odds_snapshots os
              WHERE os.fixture_id=f.id
                AND os.bookmaker_id=:bid
                AND os.fetched_at < f.kickoff
              ORDER BY os.fetched_at DESC
              LIMIT 1
            ) o ON TRUE
            WHERE p.selection_code IN ('HOME_WIN','DRAW','AWAY_WIN')
              AND p.status IN ('WIN','LOSS')
              AND p.initial_odd IS NOT NULL
              AND p.settled_at >= :epoch
            ORDER BY f.kickoff DESC
            """
        ),
        {"bid": settings.bookmaker_id, "epoch": STATS_EPOCH},
    )
    rows: list[BetRow] = []
    for r in res.fetchall():
        if r.selection == "HOME_WIN":
            closing = r.close_home
        elif r.selection == "DRAW":
            closing = r.close_draw
        else:
            closing = r.close_away
        ff = r.feature_flags if isinstance(r.feature_flags, dict) else None
        rows.append(
            BetRow(
                fixture_id=int(r.fixture_id),
                league_id=int(r.league_id) if r.league_id is not None else 0,
                league_name=str(r.league_name or ""),
                kickoff=r.kickoff,
                created_at=r.created_at,
                selection=r.selection,
                odd=Decimal(r.odd),
                prob=Decimal(r.prob) if r.prob is not None else Decimal(0),
                status=r.status,
                profit=Decimal(r.profit) if r.profit is not None else Decimal(0),
                closing_odd=Decimal(closing) if closing is not None else None,
                feature_flags=ff,
                home_goals=r.home_goals,
                away_goals=r.away_goals,
            )
        )
    return rows


async def _fetch_totals(session: AsyncSession) -> list[BetRow]:
    res = await session.execute(
        text(
            """
            SELECT
              f.id AS fixture_id,
              f.league_id AS league_id,
              COALESCE(l.name, '') AS league_name,
              f.kickoff AS kickoff,
              pt.created_at AS created_at,
              pt.market AS market,
              pt.selection AS selection,
              pt.initial_odd AS odd,
              pt.confidence AS prob,
              pt.status AS status,
              pt.profit AS profit,
              o.over_2_5 AS close_over,
              o.under_2_5 AS close_under,
              o.over_1_5 AS close_over_1_5,
              o.under_1_5 AS close_under_1_5,
              o.over_3_5 AS close_over_3_5,
              o.under_3_5 AS close_under_3_5,
              o.btts_yes AS close_btts_yes,
              o.btts_no AS close_btts_no,
              o.dc_1x AS close_dc_1x,
              o.dc_x2 AS close_dc_x2,
              o.dc_12 AS close_dc_12
            FROM predictions_totals pt
            JOIN fixtures f ON f.id=pt.fixture_id
            LEFT JOIN leagues l ON l.id=f.league_id
            LEFT JOIN LATERAL (
              SELECT over_2_5, under_2_5,
                     over_1_5, under_1_5, over_3_5, under_3_5,
                     btts_yes, btts_no,
                     dc_1x, dc_x2, dc_12
              FROM odds_snapshots os
              WHERE os.fixture_id=f.id
                AND os.bookmaker_id=:bid
                AND os.fetched_at < f.kickoff
              ORDER BY os.fetched_at DESC
              LIMIT 1
            ) o ON TRUE
            WHERE pt.status IN ('WIN','LOSS')
              AND pt.initial_odd IS NOT NULL
              AND pt.settled_at >= :epoch
            ORDER BY f.kickoff DESC
            """
        ),
        {"bid": settings.bookmaker_id, "epoch": STATS_EPOCH},
    )
    _closing_map = {
        "OVER_2_5": "close_over", "UNDER_2_5": "close_under",
        "OVER_1_5": "close_over_1_5", "UNDER_1_5": "close_under_1_5",
        "OVER_3_5": "close_over_3_5", "UNDER_3_5": "close_under_3_5",
        "BTTS_YES": "close_btts_yes", "BTTS_NO": "close_btts_no",
        "DC_1X": "close_dc_1x", "DC_X2": "close_dc_x2", "DC_12": "close_dc_12",
    }
    rows: list[BetRow] = []
    for r in res.fetchall():
        col = _closing_map.get(r.selection)
        closing = getattr(r, col, None) if col else None
        rows.append(
            BetRow(
                fixture_id=int(r.fixture_id),
                league_id=int(r.league_id) if r.league_id is not None else 0,
                league_name=str(r.league_name or ""),
                kickoff=r.kickoff,
                created_at=r.created_at,
                selection=r.selection,
                odd=Decimal(r.odd),
                prob=Decimal(r.prob) if r.prob is not None else Decimal(0),
                status=r.status,
                profit=Decimal(r.profit) if r.profit is not None else Decimal(0),
                closing_odd=Decimal(closing) if closing is not None else None,
                market=r.market,
            )
        )
    return rows


def _league_group(rows: list[BetRow]) -> list[dict]:
    buckets: dict[tuple[int, str], list[BetRow]] = defaultdict(list)
    for r in rows:
        buckets[(r.league_id, r.league_name)].append(r)
    out = []
    for (league_id, league_name) in sorted(buckets.keys()):
        stats = _summarize(buckets[(league_id, league_name)])
        stats["league_id"] = league_id
        stats["league_name"] = league_name
        out.append(stats)
    return out


async def run(session: AsyncSession) -> dict:
    one_x2 = await _fetch_1x2(session)
    all_totals = await _fetch_totals(session)

    summary_1x2 = _summarize(one_x2)
    calibration_1x2 = _calibration(one_x2)

    # Group totals by market for per-market reporting
    totals_by_market: dict[str, list[BetRow]] = {}
    for r in all_totals:
        totals_by_market.setdefault(r.market, []).append(r)

    # Legacy "total" key: only TOTAL market rows (backward compat)
    totals = totals_by_market.get("TOTAL", [])
    summary_total = _summarize(totals)
    calibration_total = _calibration(totals)

    report = {
        "generated_at": utcnow().isoformat(),
        "bookmaker_id": int(settings.bookmaker_id),
        "1x2": {
            "summary": summary_1x2,
            "by_league": _league_group(one_x2),
            "by_odds_bucket": _group(one_x2, lambda r: _odds_bucket(r.odd), order=ODDS_BUCKETS),
            "by_time_to_match": _group(one_x2, lambda r: _time_bucket(r.kickoff, r.created_at), order=TIME_BUCKETS),
            "calibration": calibration_1x2,
            "shadow_filters": _build_shadow_filters(one_x2, "1x2", summary_1x2, calibration_1x2),
        },
        "total": {
            "summary": summary_total,
            "by_league": _league_group(totals),
            "by_odds_bucket": _group(totals, lambda r: _odds_bucket(r.odd), order=ODDS_BUCKETS),
            "by_time_to_match": _group(totals, lambda r: _time_bucket(r.kickoff, r.created_at), order=TIME_BUCKETS),
            "calibration": calibration_total,
            "shadow_filters": _build_shadow_filters(totals, "total", summary_total, calibration_total),
        },
    }

    # Add per-market sections for new markets
    for market_name in ["TOTAL_1_5", "TOTAL_3_5", "BTTS", "DOUBLE_CHANCE"]:
        mkt_rows = totals_by_market.get(market_name, [])
        if mkt_rows:
            mkt_summary = _summarize(mkt_rows)
            mkt_cal = _calibration(mkt_rows)
            report[market_name.lower()] = {
                "summary": mkt_summary,
                "by_league": _league_group(mkt_rows),
                "by_odds_bucket": _group(mkt_rows, lambda r: _odds_bucket(r.odd), order=ODDS_BUCKETS),
                "calibration": mkt_cal,
            }

    logger.info(
        "quality_report summary_1x2=%s summary_total=%s",
        summary_1x2,
        summary_total,
    )
    return report
