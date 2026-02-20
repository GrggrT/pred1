"""Deprecated job module (legacy fixtures fetch).

Replaced by `app/jobs/sync_data.py` in the production pipeline.
Kept for reference only.
"""

from datetime import datetime, timedelta, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from app.core.config import settings
from app.data.providers.api_football import get_fixtures
from app.data.mappers import normalize_status
from app.core.logger import get_logger

log = get_logger("jobs.fetch_fixtures")


async def run(session: AsyncSession):
    log.info("fetch_fixtures start leagues=%s", len(settings.league_ids))
    if settings.historical_mode and settings.historical_from and settings.historical_to:
        date_from = datetime.fromisoformat(settings.historical_from).replace(tzinfo=timezone.utc)
        date_to = datetime.fromisoformat(settings.historical_to).replace(tzinfo=timezone.utc)
        log.info("fetch_fixtures historical window %s -> %s", date_from, date_to)
    else:
        now = datetime.now(timezone.utc)
        date_from = now
        date_to = now + timedelta(days=10)
        log.info("fetch_fixtures live window %s -> %s", date_from, date_to)

    total_fixtures = 0
    for league_id in settings.league_ids:
        data = await get_fixtures(session, league_id, settings.season, date_from, date_to)
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
                {"id": home_id, "name": home_name, "league": league_id},
            )
            await session.execute(
                text(
                    """
                    INSERT INTO teams(id, name, league_id)
                    VALUES(:id,:name,:league)
                    ON CONFLICT (id) DO UPDATE SET name=:name, league_id=:league
                """
                ),
                {"id": away_id, "name": away_name, "league": league_id},
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
                    "league": league_id,
                    "season": league["season"],
                    "kickoff": kickoff,
                    "home": home_id,
                    "away": away_id,
                    "status": status,
                    "hg": goals.get("home"),
                    "ag": goals.get("away"),
                },
            )

            total_fixtures += 1

    await session.commit()
    log.info("fetch_fixtures done fixtures=%s", total_fixtures)
