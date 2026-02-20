"""Deprecated job module (legacy history fetch).

Replaced by `app/jobs/sync_data.py` in the production pipeline.
Kept for reference only.
"""

from datetime import datetime, timedelta, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from app.core.config import settings
from app.data.providers.api_football import get_team_last_matches
from app.data.mappers import normalize_status
from app.core.logger import get_logger

log = get_logger("jobs.fetch_history")


async def _collect_team_ids(session: AsyncSession, start_ts, end_ts):
    res = await session.execute(
        text(
            """
        SELECT DISTINCT home_team_id AS team_id FROM fixtures
        WHERE kickoff BETWEEN :start AND :end
        UNION
        SELECT DISTINCT away_team_id AS team_id FROM fixtures
        WHERE kickoff BETWEEN :start AND :end
    """
        ),
        {"start": start_ts, "end": end_ts},
    )
    return [row[0] for row in res.fetchall()]


async def run(session: AsyncSession):
    if settings.historical_mode and settings.historical_from and settings.historical_to:
        start_aware = datetime.fromisoformat(settings.historical_from).replace(tzinfo=timezone.utc)
        end_aware = datetime.fromisoformat(settings.historical_to).replace(tzinfo=timezone.utc)
        start_ts = start_aware
        end_ts = end_aware
        mode = "historical"
        last_matches = 20
        log.info("fetch_history historical window %s -> %s", start_aware, end_aware)
    else:
        aware_now = datetime.now(timezone.utc)
        start_ts = aware_now
        end_ts = aware_now + timedelta(days=3)
        mode = "live"
        last_matches = 15
        log.info("fetch_history live window %s -> %s", aware_now, aware_now + timedelta(days=3))

    team_ids = await _collect_team_ids(session, start_ts, end_ts)
    log.info("fetch_history mode=%s teams=%s", mode, len(team_ids))

    fixtures_upserted = 0
    for team_id in team_ids:
        data = await get_team_last_matches(session, team_id, settings.season, n=last_matches)
        for item in data.get("response", []):
            fixture = item["fixture"]
            league = item["league"]
            teams = item["teams"]
            goals = item.get("goals") or {}

            fixture_id = fixture["id"]
            kickoff = datetime.fromisoformat(fixture["date"].replace("Z", "+00:00"))
            kickoff = kickoff.astimezone(timezone.utc)
            status = normalize_status(fixture["status"]["short"])

            home_id = teams["home"]["id"]
            away_id = teams["away"]["id"]
            home_name = teams["home"]["name"]
            away_name = teams["away"]["name"]

            await session.execute(
                text(
                    """
                    INSERT INTO teams(id, name, league_id)
                    VALUES(:id,:name,:league)
                    ON CONFLICT (id) DO UPDATE SET name=:name, league_id=:league
                """
                ),
                {"id": home_id, "name": home_name, "league": league["id"]},
            )
            await session.execute(
                text(
                    """
                    INSERT INTO teams(id, name, league_id)
                    VALUES(:id,:name,:league)
                    ON CONFLICT (id) DO UPDATE SET name=:name, league_id=:league
                """
                ),
                {"id": away_id, "name": away_name, "league": league["id"]},
            )

            await session.execute(
                text(
                    """
                    INSERT INTO fixtures(
                      id, league_id, season, kickoff, home_team_id, away_team_id, status,
                      home_goals, away_goals
                    )
                    VALUES(
                      :id,:league,:season,:kickoff,:home,:away,:status,
                      :hg,:ag
                    )
                    ON CONFLICT (id) DO UPDATE SET
                      status=:status, home_goals=:hg, away_goals=:ag
                """
                ),
                {
                    "id": fixture_id,
                    "league": league["id"],
                    "season": league["season"],
                    "kickoff": kickoff,
                    "home": home_id,
                    "away": away_id,
                    "status": status,
                    "hg": goals.get("home"),
                    "ag": goals.get("away"),
                },
            )

            fixtures_upserted += 1

    await session.commit()
    log.info("fetch_history done fixtures=%s", fixtures_upserted)
