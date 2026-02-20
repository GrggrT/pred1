import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.core.config import settings
from app.core.db import SessionLocal, init_db
from app.core.http import init_http_clients, close_http_clients
from app.data.providers.api_football import (
    get_fixtures_by_season,
    set_force_refresh,
    reset_force_refresh,
)
from app.jobs.sync_data import _upsert_fixture


def _parse_league_ids(raw: str | None, fallback: list[int]) -> list[int]:
    if not raw:
        return list(fallback)
    items = [x.strip() for x in raw.split(",") if x.strip()]
    return [int(x) for x in items]


async def backfill_season(
    *,
    season: int,
    league_ids: list[int],
    force_refresh: bool,
    sleep_ms: int,
) -> None:
    if (settings.api_football_key or "").strip() in {"", "YOUR_KEY", "your_paid_key"}:
        raise RuntimeError("API_FOOTBALL_KEY is not configured; cannot backfill fixtures")

    await init_db()
    await init_http_clients()
    token = set_force_refresh(force_refresh)
    try:
        total = 0
        async with SessionLocal() as session:
            for idx, league_id in enumerate(league_ids, 1):
                data = await get_fixtures_by_season(session, league_id, season)
                items = list(data.get("response") or [])
                for item in items:
                    await _upsert_fixture(session, item)
                await session.commit()
                total += len(items)
                print(
                    f"league={league_id} season={season} fixtures_upserted={len(items)}"
                )
                if sleep_ms > 0 and idx < len(league_ids):
                    await asyncio.sleep(sleep_ms / 1000)
        print(f"backfill done season={season} leagues={len(league_ids)} fixtures={total}")
    finally:
        reset_force_refresh(token)
        await close_http_clients()


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill fixtures for the current season")
    parser.add_argument(
        "--season",
        type=int,
        default=None,
        help="Season year (defaults to settings.SEASON)",
    )
    parser.add_argument(
        "--league-ids",
        default="",
        help="Comma-separated league IDs (defaults to settings.LEAGUE_IDS)",
    )
    parser.add_argument(
        "--use-cache",
        action="store_true",
        help="Allow api_cache hits to reduce API usage",
    )
    parser.add_argument(
        "--sleep-ms",
        type=int,
        default=int(getattr(settings, "backfill_rate_ms", 0) or 0),
        help="Delay between leagues in milliseconds",
    )
    args = parser.parse_args()

    season = int(args.season or settings.season)
    league_ids = _parse_league_ids(args.league_ids, settings.league_ids)
    if not league_ids:
        raise SystemExit("No leagues provided (LEAGUE_IDS is empty)")

    asyncio.run(
        backfill_season(
            season=season,
            league_ids=league_ids,
            force_refresh=not args.use_cache,
            sleep_ms=int(args.sleep_ms or 0),
        )
    )


if __name__ == "__main__":
    main()
