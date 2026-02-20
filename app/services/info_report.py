from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.types import Integer, DateTime as SADateTime

from app.core.timeutils import utcnow

INFO_MARKETS: dict[str, dict[str, Any]] = {
    "INFO_BTTS": {"label": "BTTS", "yes": "BTTS_YES", "no": "BTTS_NO"},
    "INFO_OU_1_5": {"label": "O/U 1.5", "over": "OVER_1_5", "under": "UNDER_1_5", "threshold": 1.5},
    "INFO_OU_2_5": {"label": "O/U 2.5", "over": "OVER_2_5", "under": "UNDER_2_5", "threshold": 2.5},
    "INFO_OU_3_5": {"label": "O/U 3.5", "over": "OVER_3_5", "under": "UNDER_3_5", "threshold": 3.5},
}

INFO_MARKET_ORDER = list(INFO_MARKETS.keys())
INFO_FIXTURE_MARKETS = ["1X2", "TOTAL", *INFO_MARKET_ORDER]

FINAL_STATUSES = ("FT", "AET", "PEN")


def _as_dict(payload: Any) -> dict | None:
    if payload is None:
        return None
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str):
        try:
            val = json.loads(payload)
            return val if isinstance(val, dict) else None
        except Exception:
            return None
    return None


def _safe_prob(value: float | None) -> float | None:
    if value is None:
        return None
    if value <= 1e-6:
        return 1e-6
    if value >= 1 - 1e-6:
        return 1 - 1e-6
    return float(value)


def _candidates_map(payload: dict) -> dict[str, float]:
    out: dict[str, float] = {}
    candidates = payload.get("candidates") or []
    if not isinstance(candidates, list):
        return out
    for entry in candidates:
        if not isinstance(entry, dict):
            continue
        sel = entry.get("selection")
        prob = entry.get("prob")
        if sel is None or prob is None:
            continue
        try:
            out[str(sel)] = float(prob)
        except Exception:
            continue
    return out


def _best_selection(cmap: dict[str, float]) -> str | None:
    if not cmap:
        return None
    return max(cmap, key=cmap.get)


def _resolve_actual(market: str, home_goals: int, away_goals: int) -> str | None:
    info = INFO_MARKETS.get(market)
    if not info:
        return None
    if market == "INFO_BTTS":
        return info["yes"] if home_goals > 0 and away_goals > 0 else info["no"]
    total = home_goals + away_goals
    threshold = float(info.get("threshold") or 0)
    cutoff = int(math.floor(threshold)) + 1
    return info["over"] if total >= cutoff else info["under"]


@dataclass
class InfoBucket:
    total: int = 0
    wins: int = 0
    brier_sum: float = 0.0
    logloss_sum: float = 0.0

    def add(self, *, p_yes: float, y: int, win: bool) -> None:
        self.total += 1
        if win:
            self.wins += 1
        self.brier_sum += (p_yes - y) ** 2
        self.logloss_sum += -(y * math.log(p_yes) + (1 - y) * math.log(1 - p_yes))

    def summary(self) -> dict[str, float | int]:
        if not self.total:
            return {"bets": 0, "wins": 0, "win_rate": 0.0, "roi_even_pct": 0.0, "brier": 0.0, "logloss": 0.0}
        wins = int(self.wins)
        losses = int(self.total - self.wins)
        win_rate = wins / self.total
        roi_even_pct = ((wins - losses) / self.total) * 100
        return {
            "bets": int(self.total),
            "wins": wins,
            "win_rate": win_rate,
            "roi_even_pct": roi_even_pct,
            "brier": self.brier_sum / self.total,
            "logloss": self.logloss_sum / self.total,
        }


async def fetch_info_picks(
    session: AsyncSession,
    *,
    date_from: datetime,
    date_to: datetime,
    league_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
    only_upcoming: bool = True,
) -> list[dict[str, Any]]:
    markets = INFO_MARKET_ORDER
    stmt = (
        text(
            """
            WITH fixture_rows AS (
              SELECT DISTINCT
                f.id AS fixture_id,
                f.kickoff AS kickoff,
                f.status AS fixture_status,
                f.home_goals AS home_goals,
                f.away_goals AS away_goals,
                f.league_id AS league_id,
                l.name AS league,
                l.logo_url AS league_logo_url,
                th.name AS home_name,
                ta.name AS away_name,
                th.logo_url AS home_logo_url,
                ta.logo_url AS away_logo_url
              FROM fixtures f
              JOIN prediction_decisions pd ON pd.fixture_id=f.id
              JOIN teams th ON th.id=f.home_team_id
              JOIN teams ta ON ta.id=f.away_team_id
              LEFT JOIN leagues l ON l.id=f.league_id
              WHERE pd.market IN (SELECT unnest(CAST(:markets AS text[])))
                AND f.kickoff >= :date_from
                AND f.kickoff <= :date_to
                AND (:league_id IS NULL OR f.league_id=:league_id)
                AND (:only_upcoming = false OR f.status='NS')
              ORDER BY f.kickoff ASC
              LIMIT :limit OFFSET :offset
            )
            SELECT fr.*, pd.market, pd.payload
            FROM fixture_rows fr
            JOIN prediction_decisions pd ON pd.fixture_id=fr.fixture_id
            WHERE pd.market IN (SELECT unnest(CAST(:markets AS text[])))
            ORDER BY fr.kickoff ASC, pd.market ASC
            """
        ).bindparams(
            bindparam("league_id", type_=Integer),
            bindparam("date_from", type_=SADateTime(timezone=True)),
            bindparam("date_to", type_=SADateTime(timezone=True)),
            bindparam("limit", type_=Integer),
            bindparam("offset", type_=Integer),
        )
    )
    res = await session.execute(
        stmt,
        {
            "markets": markets,
            "date_from": date_from,
            "date_to": date_to,
            "league_id": league_id,
            "limit": limit,
            "offset": offset,
            "only_upcoming": only_upcoming,
        },
    )
    grouped: dict[int, dict[str, Any]] = {}
    for row in res.fetchall():
        payload = _as_dict(row.payload)
        if not payload:
            continue
        entry = grouped.setdefault(
            int(row.fixture_id),
            {
                "fixture_id": int(row.fixture_id),
                "kickoff": row.kickoff.isoformat() if row.kickoff is not None else None,
                "fixture_status": row.fixture_status,
                "home_goals": row.home_goals,
                "away_goals": row.away_goals,
                "league_id": int(row.league_id) if row.league_id is not None else None,
                "league": row.league,
                "league_logo_url": row.league_logo_url,
                "home": row.home_name,
                "away": row.away_name,
                "home_logo_url": row.home_logo_url,
                "away_logo_url": row.away_logo_url,
                "markets": [],
            },
        )
        entry["markets"].append(
            {
                "market": row.market,
                "label": INFO_MARKETS.get(row.market, {}).get("label") if row.market in INFO_MARKETS else row.market,
                "selection": payload.get("selection"),
                "prob": payload.get("prob"),
                "candidates": payload.get("candidates") or [],
            }
        )
    out = list(grouped.values())
    for item in out:
        item["markets"].sort(key=lambda m: INFO_MARKET_ORDER.index(m["market"]) if m["market"] in INFO_MARKET_ORDER else 999)
    out.sort(key=lambda r: r.get("kickoff") or "")
    return out


async def fetch_info_fixtures(
    session: AsyncSession,
    *,
    date_from: datetime,
    date_to: datetime,
    league_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
    only_upcoming: bool = False,
) -> list[dict[str, Any]]:
    markets = INFO_FIXTURE_MARKETS
    stmt = (
        text(
            """
            WITH fixture_rows AS (
              SELECT DISTINCT
                f.id AS fixture_id,
                f.kickoff AS kickoff,
                f.status AS fixture_status,
                f.home_goals AS home_goals,
                f.away_goals AS away_goals,
                f.league_id AS league_id,
                l.name AS league,
                l.logo_url AS league_logo_url,
                th.name AS home_name,
                ta.name AS away_name,
                th.logo_url AS home_logo_url,
                ta.logo_url AS away_logo_url
              FROM fixtures f
              JOIN prediction_decisions pd ON pd.fixture_id=f.id
              JOIN teams th ON th.id=f.home_team_id
              JOIN teams ta ON ta.id=f.away_team_id
              LEFT JOIN leagues l ON l.id=f.league_id
              WHERE pd.market IN (SELECT unnest(CAST(:markets AS text[])))
                AND f.kickoff >= :date_from
                AND f.kickoff <= :date_to
                AND (:league_id IS NULL OR f.league_id=:league_id)
                AND (:only_upcoming = false OR f.status='NS')
              ORDER BY f.kickoff ASC
              LIMIT :limit OFFSET :offset
            )
            SELECT fr.*, pd.market, pd.payload
            FROM fixture_rows fr
            JOIN prediction_decisions pd ON pd.fixture_id=fr.fixture_id
            WHERE pd.market IN (SELECT unnest(CAST(:markets AS text[])))
            ORDER BY fr.kickoff ASC, pd.market ASC
            """
        ).bindparams(
            bindparam("league_id", type_=Integer),
            bindparam("date_from", type_=SADateTime(timezone=True)),
            bindparam("date_to", type_=SADateTime(timezone=True)),
            bindparam("limit", type_=Integer),
            bindparam("offset", type_=Integer),
        )
    )
    res = await session.execute(
        stmt,
        {
            "markets": markets,
            "date_from": date_from,
            "date_to": date_to,
            "league_id": league_id,
            "limit": limit,
            "offset": offset,
            "only_upcoming": only_upcoming,
        },
    )
    grouped: dict[int, dict[str, Any]] = {}
    for row in res.fetchall():
        payload = _as_dict(row.payload)
        entry = grouped.setdefault(
            int(row.fixture_id),
            {
                "fixture_id": int(row.fixture_id),
                "kickoff": row.kickoff.isoformat() if row.kickoff is not None else None,
                "fixture_status": row.fixture_status,
                "home_goals": row.home_goals,
                "away_goals": row.away_goals,
                "league_id": int(row.league_id) if row.league_id is not None else None,
                "league": row.league,
                "league_logo_url": row.league_logo_url,
                "home": row.home_name,
                "away": row.away_name,
                "home_logo_url": row.home_logo_url,
                "away_logo_url": row.away_logo_url,
                "decisions": {},
            },
        )
        if payload is not None:
            entry["decisions"][str(row.market)] = payload

    out = list(grouped.values())
    out.sort(key=lambda r: r.get("kickoff") or "")
    return out


async def compute_info_stats(
    session: AsyncSession,
    *,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    league_id: int | None = None,
) -> dict[str, Any]:
    markets = INFO_MARKET_ORDER
    stmt = (
        text(
            """
            SELECT
              pd.fixture_id,
              pd.market,
              pd.payload,
              f.league_id,
              COALESCE(l.name, '') AS league_name,
              f.home_goals,
              f.away_goals,
              f.kickoff
            FROM prediction_decisions pd
            JOIN fixtures f ON f.id=pd.fixture_id
            LEFT JOIN leagues l ON l.id=f.league_id
            WHERE pd.market IN (SELECT unnest(CAST(:markets AS text[])))
              AND f.status IN ('FT','AET','PEN')
              AND f.home_goals IS NOT NULL
              AND f.away_goals IS NOT NULL
              AND (:league_id IS NULL OR f.league_id=:league_id)
              AND (:date_from IS NULL OR f.kickoff >= :date_from)
              AND (:date_to IS NULL OR f.kickoff < :date_to)
            ORDER BY f.kickoff DESC
            """
        ).bindparams(
            bindparam("league_id", type_=Integer),
            bindparam("date_from", type_=SADateTime(timezone=True)),
            bindparam("date_to", type_=SADateTime(timezone=True)),
        )
    )
    res = await session.execute(
        stmt,
        {
            "markets": markets,
            "league_id": league_id,
            "date_from": date_from,
            "date_to": date_to,
        },
    )
    buckets: dict[str, InfoBucket] = {m: InfoBucket() for m in markets}
    league_buckets: dict[tuple[str, int, str], InfoBucket] = defaultdict(InfoBucket)
    for row in res.fetchall():
        payload = _as_dict(row.payload)
        if not payload:
            continue
        market = str(row.market)
        info = INFO_MARKETS.get(market)
        if not info:
            continue
        cmap = _candidates_map(payload)
        if not cmap:
            continue
        actual = _resolve_actual(market, int(row.home_goals), int(row.away_goals))
        if actual is None:
            continue
        pred_sel = payload.get("selection") or _best_selection(cmap)
        if pred_sel is not None and pred_sel not in cmap:
            pred_sel = _best_selection(cmap)
        if pred_sel is None:
            continue
        if market == "INFO_BTTS":
            p_yes = _safe_prob(cmap.get(info["yes"]))
            y = 1 if actual == info["yes"] else 0
        else:
            p_yes = _safe_prob(cmap.get(info["over"]))
            y = 1 if actual == info["over"] else 0
        if p_yes is None:
            continue
        win = pred_sel == actual
        buckets[market].add(p_yes=p_yes, y=y, win=win)
        lid = int(row.league_id or 0)
        lname = str(row.league_name or "")
        league_buckets[(market, lid, lname)].add(p_yes=p_yes, y=y, win=win)

    summary = []
    for market in markets:
        stats = buckets[market].summary()
        summary.append(
            {
                "market": market,
                "label": INFO_MARKETS.get(market, {}).get("label", market),
                **stats,
            }
        )

    by_league = []
    for (market, lid, lname), bucket in league_buckets.items():
        stats = bucket.summary()
        by_league.append(
            {
                "market": market,
                "label": INFO_MARKETS.get(market, {}).get("label", market),
                "league_id": lid,
                "league_name": lname,
                **stats,
            }
        )

    return {
        "generated_at": utcnow().isoformat(),
        "summary": summary,
        "by_league": by_league,
        "filters": {
            "date_from": date_from.isoformat() if date_from is not None else None,
            "date_to": date_to.isoformat() if date_to is not None else None,
            "league_id": league_id,
        },
    }
