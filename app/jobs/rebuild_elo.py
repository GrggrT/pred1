from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import get_logger
from app.services.elo_ratings import apply_elo_from_fixtures

log = get_logger("jobs.rebuild_elo")


async def run(session: AsyncSession) -> dict:
    log.info("rebuild_elo start")
    out = await apply_elo_from_fixtures(session, force_recompute=True)
    await session.commit()
    log.info("rebuild_elo done %s", out)
    return out

