"""Deprecated job module (legacy fixtures+odds sync).

Replaced by `app/jobs/sync_data.py` in the production pipeline.
Kept for reference only.
"""

from datetime import datetime, timezone
from typing import Iterable, Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from app.core.config import settings
from app.core.logger import get_logger
from app.data.providers.api_football import (
    get_fixtures_by_season,
    get_odds_by_season,
    get_odds_by_fixture,
    get_odds_by_date,
)
from app.data.mappers import normalize_status

log = get_logger("jobs.sync_fixtures_odds")
BOOKMAKER_IDS = [1, 8]  # Bet365 / 1xBet


async def _has_season_data(session: AsyncSession, league_id: int, season: int) -> bool:
    res = await session.execute(
        text(
            "SELECT 1 FROM fixtures WHERE league_id=:lid AND season=:season LIMIT 1"
        ),
        {"lid": league_id, "season": season},
    )
    return res.first() is not None


def _parse_kickoff(raw_date: str) -> datetime:
    # API возвращает ISO8601, нормализуем в UTC-aware
    kickoff = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
    return kickoff.astimezone(timezone.utc)


async def _upsert_league(session: AsyncSession, league_json: dict):
    await session.execute(
        text(
            """
            INSERT INTO leagues(id, name, country, active)
            VALUES(:id, :name, :country, TRUE)
            ON CONFLICT (id) DO UPDATE
            SET name=:name, country=:country, active=TRUE
        """
        ),
        {
            "id": league_json.get("id"),
            "name": league_json.get("name"),
            "country": league_json.get("country"),
        },
    )


async def _upsert_team(session: AsyncSession, team_json: dict, league_id: int):
    await session.execute(
        text(
            """
            INSERT INTO teams(id, name, league_id, code, logo_url)
            VALUES(:id, :name, :league_id, :code, :logo)
            ON CONFLICT (id) DO UPDATE
            SET name=:name, league_id=:league_id, code=:code, logo_url=:logo
        """
        ),
        {
            "id": team_json.get("id"),
            "name": team_json.get("name"),
            "league_id": league_id,
            "code": team_json.get("code"),
            "logo": team_json.get("logo"),
        },
    )


async def _upsert_fixture(session: AsyncSession, item: dict):
    fixture = item["fixture"]
    league = item["league"]
    teams = item["teams"]
    goals = item.get("goals") or {}
    score = item.get("score") or {}

    kickoff = _parse_kickoff(fixture["date"])
    status = normalize_status(fixture.get("status", {}).get("short"))

    home_goals = goals.get("home")
    away_goals = goals.get("away")

    # Дополнительные источники голов, если матч завершен
    fulltime = score.get("fulltime") or {}
    home_goals = home_goals if home_goals is not None else fulltime.get("home")
    away_goals = away_goals if away_goals is not None else fulltime.get("away")

    await _upsert_league(session, league)
    await _upsert_team(session, teams["home"], league["id"])
    await _upsert_team(session, teams["away"], league["id"])

    await session.execute(
        text(
            """
            INSERT INTO fixtures(
              id, league_id, season, kickoff, home_team_id, away_team_id, status,
              home_goals, away_goals, home_red, away_red, home_yellow, away_yellow,
              processed_indices, updated_at
            )
            VALUES(
              :id, :league_id, :season, :kickoff, :home_id, :away_id, :status,
              :hg, :ag, 0, 0, 0, 0, FALSE, now()
            )
            ON CONFLICT (id) DO UPDATE SET
              status=:status,
              home_goals=:hg,
              away_goals=:ag,
              updated_at=now()
        """
        ),
        {
            "id": fixture["id"],
            "league_id": league["id"],
            "season": league["season"],
            "kickoff": kickoff,
            "home_id": teams["home"]["id"],
            "away_id": teams["away"]["id"],
            "status": status,
            "hg": home_goals,
            "ag": away_goals,
        },
    )


def _extract_1x2(bookmaker: dict) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    for bet in bookmaker.get("bets", []):
        if bet.get("id") == 1 or bet.get("name", "").lower() == "match winner":
            home = draw = away = None
            for val in bet.get("values", []):
                label = (val.get("value") or "").lower()
                odd_raw = val.get("odd")
                odd = float(odd_raw) if odd_raw is not None else None
                if label in {"home", "1"}:
                    home = odd
                elif label in {"draw", "x"}:
                    draw = odd
                elif label in {"away", "2"}:
                    away = odd
            return home, draw, away
    return None, None, None


async def _upsert_odds(session: AsyncSession, data: dict, allowed_fixture_ids: Optional[set[int]] = None):
    for entry in data.get("response", []):
        fixture = entry.get("fixture") or {}
        fixture_id = fixture.get("id")
        if not fixture_id:
            continue
        if allowed_fixture_ids is not None and fixture_id not in allowed_fixture_ids:
            continue

        for bookmaker in entry.get("bookmakers", []):
            bm_id = bookmaker.get("id")
            if bm_id not in BOOKMAKER_IDS:
                continue
            home, draw, away = _extract_1x2(bookmaker)
            if not any([home, draw, away]):
                continue
            await session.execute(
                text(
                    """
                    INSERT INTO odds(fixture_id, bookmaker_id, home_win, draw, away_win, fetched_at)
                    VALUES(:fid,:bid,:h,:d,:a, now())
                    ON CONFLICT (fixture_id, bookmaker_id) DO UPDATE SET
                      home_win=:h, draw=:d, away_win=:a, fetched_at=now()
                """
                ),
                {"fid": fixture_id, "bid": bm_id, "h": home, "d": draw, "a": away},
            )


async def _fetch_season(session: AsyncSession, league_id: int, season: int, skip_if_exists: bool):
    if skip_if_exists and await _has_season_data(session, league_id, season):
        log.info("sync_fixtures_odds skip league=%s season=%s (already in DB)", league_id, season)
        return

    fixtures_json = await get_fixtures_by_season(session, league_id, season)
    total_fixtures = 0
    for item in fixtures_json.get("response", []):
        await _upsert_fixture(session, item)
        total_fixtures += 1
    log.info("sync_fixtures_odds upsert fixtures league=%s season=%s count=%s", league_id, season, total_fixtures)

    odds_json = await get_odds_by_season(session, league_id, season, BOOKMAKER_IDS)
    await _upsert_odds(session, odds_json)


async def _refresh_live_odds(session: AsyncSession):
    # берем ближайшие матчи (NS) и тянем odds по каждому fixture; если пусто, пробуем по дате
    res = await session.execute(
        text(
            """
            SELECT id FROM fixtures
            WHERE status='NS'
              AND kickoff BETWEEN now() AND now() + interval '7 days'
            """
        )
    )
    rows = res.fetchall()
    fixture_ids = [row.id for row in rows]
    allowed = set(fixture_ids)
    if not fixture_ids:
        return

    dates = set()
    for fid in fixture_ids:
        data = await get_odds_by_fixture(session, fid, BOOKMAKER_IDS)
        await _upsert_odds(session, data, allowed_fixture_ids=allowed)
        if not data.get("response"):
            data_any = await get_odds_by_fixture(session, fid, [])
            await _upsert_odds(session, data_any, allowed_fixture_ids=allowed)
        res_date = await session.execute(text("SELECT kickoff::date as d FROM fixtures WHERE id=:fid"), {"fid": fid})
        drow = res_date.first()
        if drow:
            dates.add(drow.d)

    for d in sorted(dates):
        data = await get_odds_by_date(session, d.isoformat(), BOOKMAKER_IDS)
        await _upsert_odds(session, data, allowed_fixture_ids=allowed)
        if not data.get("response"):
            data_any = await get_odds_by_date(session, d.isoformat(), [])
            await _upsert_odds(session, data_any, allowed_fixture_ids=allowed)


def _target_leagues() -> Iterable[int]:
    if settings.is_historical and settings.historical_leagues:
        return settings.historical_leagues
    return settings.league_ids


def _target_season() -> int:
    if settings.is_historical:
        return settings.historical_season
    return settings.season


async def run(session: AsyncSession):
    mode = "historical" if settings.is_historical else "live"
    leagues = list(_target_leagues())
    season = _target_season()
    prev_season = season - 1
    log.info("sync_fixtures_odds mode=%s leagues=%s season=%s", mode, len(leagues), season)

    for league_id in leagues:
        await _fetch_season(session, league_id, prev_season, skip_if_exists=True)
        await _fetch_season(session, league_id, season, skip_if_exists=False)

    if settings.is_live:
        await _refresh_live_odds(session)

    await session.commit()
    log.info("sync_fixtures_odds done")
