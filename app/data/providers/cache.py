from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def get_cached_payload(session: AsyncSession, cache_key: str) -> dict | None:
    res = await session.execute(
        text(
            """
            SELECT payload FROM api_cache
            WHERE cache_key=:k AND expires_at > now()
            """
        ),
        {"k": cache_key},
    )
    row = res.first()
    return row[0] if row else None


async def set_cached_payload(session: AsyncSession, cache_key: str, payload: dict, ttl_seconds: int) -> None:
    expires = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
    payload_json = payload
    if payload is not None and not isinstance(payload, str):
        payload_json = json.dumps(payload, ensure_ascii=False)
    await session.execute(
        text(
            """
            INSERT INTO api_cache(cache_key, payload, expires_at)
            VALUES(:k, CAST(:p AS jsonb), :e)
            ON CONFLICT (cache_key)
            DO UPDATE SET payload=CAST(:p AS jsonb), expires_at=:e
            """
        ),
        {"k": cache_key, "p": payload_json, "e": expires},
    )
