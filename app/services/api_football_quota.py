from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timeutils import ensure_aware_utc, utcnow

_QUOTA_ERROR_NEEDLES = (
    "reached the request limit for the day",
    "request limit for the day",
    "reached the request limit",
)


def is_api_football_quota_error(exc: BaseException) -> bool:
    msg = str(exc or "").lower()
    return any(needle in msg for needle in _QUOTA_ERROR_NEEDLES)


def utc_day_window(now: datetime | None = None) -> tuple[datetime, datetime]:
    now_utc = ensure_aware_utc(now) if now is not None else utcnow()
    day_start = now_utc.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return day_start, day_start + timedelta(days=1)


async def api_football_usage_since(session: AsyncSession, *, start: datetime) -> dict[str, int]:
    start_utc = ensure_aware_utc(start)
    row = (
        await session.execute(
            text(
                """
                WITH rows AS (
                  SELECT
                    COALESCE((meta->'result'->'api_football'->>'cache_hits')::int, 0) AS cache_hits,
                    COALESCE((meta->'result'->'api_football'->>'cache_misses')::int, 0) AS cache_misses,
                    COALESCE((meta->'result'->'api_football'->>'errors')::int, 0) AS errors,
                    status AS status
                  FROM job_runs
                  WHERE job_name='sync_data'
                    AND started_at >= :start
                  UNION ALL
                  SELECT
                    COALESCE((meta->'stages'->'sync_data'->'result'->'api_football'->>'cache_hits')::int, 0) AS cache_hits,
                    COALESCE((meta->'stages'->'sync_data'->'result'->'api_football'->>'cache_misses')::int, 0) AS cache_misses,
                    COALESCE((meta->'stages'->'sync_data'->'result'->'api_football'->>'errors')::int, 0) AS errors,
                    status AS status
                  FROM job_runs
                  WHERE job_name='full'
                    AND started_at >= :start
                )
                SELECT
                  COALESCE(SUM(cache_hits), 0) AS cache_hits,
                  COALESCE(SUM(cache_misses), 0) AS cache_misses,
                  COALESCE(SUM(errors), 0) AS errors,
                  COUNT(*) FILTER (WHERE status='ok') AS ok_runs,
                  COUNT(*) FILTER (WHERE status='failed') AS failed_runs,
                  COUNT(*) AS total_runs
                FROM rows
                """
            ),
            {"start": start_utc},
        )
    ).first()

    if not row:
        return {
            "requests": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "errors": 0,
            "ok_runs": 0,
            "failed_runs": 0,
            "total_runs": 0,
        }

    cache_hits = int(row.cache_hits or 0)
    cache_misses = int(row.cache_misses or 0)
    return {
        "requests": int(cache_hits + cache_misses),
        "cache_hits": cache_hits,
        "cache_misses": cache_misses,
        "errors": int(row.errors or 0),
        "ok_runs": int(row.ok_runs or 0),
        "failed_runs": int(row.failed_runs or 0),
        "total_runs": int(row.total_runs or 0),
    }


async def last_quota_error_since(session: AsyncSession, *, start: datetime) -> dict | None:
    start_utc = ensure_aware_utc(start)
    row = (
        await session.execute(
            text(
                """
                SELECT
                  job_name,
                  status,
                  started_at,
                  error,
                  COALESCE(
                    meta->'result'->>'quota_blocked_until',
                    meta->'stages'->'sync_data'->'result'->>'quota_blocked_until'
                  ) AS quota_blocked_until
                FROM job_runs
                WHERE started_at >= :start
                  AND job_name IN ('sync_data','full')
                  AND (
                    (error IS NOT NULL AND (
                      lower(error) LIKE '%request limit for the day%'
                      OR lower(error) LIKE '%reached the request limit%'
                      OR lower(error) LIKE '%reached the request limit for the day%'
                    ))
                    OR COALESCE((meta->'result'->>'quota_exhausted')::boolean, FALSE) = TRUE
                    OR COALESCE((meta->'stages'->'sync_data'->'result'->>'quota_exhausted')::boolean, FALSE) = TRUE
                  )
                ORDER BY started_at DESC
                LIMIT 1
                """
            ),
            {"start": start_utc},
        )
    ).first()
    if not row:
        return None
    err = str(row.error or "")
    err_short = err[-500:] if len(err) > 500 else err
    return {
        "job_name": row.job_name,
        "status": row.status,
        "started_at": ensure_aware_utc(row.started_at).isoformat() if row.started_at is not None else None,
        "error_tail": err_short,
        "quota_blocked_until": str(row.quota_blocked_until) if getattr(row, "quota_blocked_until", None) else None,
    }


async def quota_guard_decision(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    daily_limit: int = 7500,
    guard_margin: int = 100,
) -> dict:
    now_utc = ensure_aware_utc(now) if now is not None else utcnow()
    day_start, reset_at = utc_day_window(now_utc)
    usage = await api_football_usage_since(session, start=day_start)
    quota_error = await last_quota_error_since(session, start=day_start)

    limit = int(daily_limit or 0)
    margin = max(int(guard_margin or 0), 0)
    used = int(usage.get("cache_misses") or 0)
    remaining = max(limit - used, 0) if limit > 0 else None

    blocked = False
    reason: str | None = None
    if quota_error:
        blocked = True
        reason = "quota_error_seen"
    elif limit > 0 and used >= limit:
        blocked = True
        reason = "daily_limit_reached"
    elif limit > 0 and margin > 0 and used >= max(limit - margin, 0):
        blocked = True
        reason = "guard_margin"

    return {
        "now": now_utc.isoformat(),
        "day_start": day_start.isoformat(),
        "reset_at": reset_at.isoformat(),
        "daily_limit": limit,
        "guard_margin": margin,
        "used_cache_misses": used,
        "remaining_cache_misses": remaining,
        "blocked": blocked,
        "reason": reason,
        "usage": usage,
        "last_quota_error": quota_error,
    }
