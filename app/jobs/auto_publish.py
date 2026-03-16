"""Drip-feed auto-publish predictions to Telegram channels.

Publishes at most ONE prediction per invocation, with dynamically
calculated intervals.  Designed to run frequently (e.g. every 2 min)
via APScheduler cron.

Algorithm
---------
1. Honour quiet-hours (default: nothing before 06:00 UTC / 08:00 CET).
2. Find all unpublished fixtures within the look-ahead window.
3. Calculate a *hard deadline*  = earliest_kickoff − buffer (default 1 h).
4. Derive the posting interval:
   • ideal (default 1 h) if there is enough time,
   • shrunk proportionally otherwise,
   • with a safety floor (default 60 s).
5. Respect a cooldown since the last successful publication.
6. Publish exactly **one** fixture, then exit.  The next cron tick
   re-evaluates everything with fresh data.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.timeutils import utcnow

log = logging.getLogger(__name__)


def _ensure_utc(dt: datetime) -> datetime:
    """Make a datetime tz-aware in UTC if it isn't already."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


async def run(session: AsyncSession):
    """Drip-feed: publish at most one unpublished prediction per run."""

    # ── Guard: bot token ──────────────────────────────────────────
    if not settings.telegram_bot_token:
        log.warning("auto_publish: TELEGRAM_BOT_TOKEN not configured, skipping")
        return {"published": 0, "skipped": 0, "errors": 0, "reason": "no_bot_token"}

    # ── Guard: channels ───────────────────────────────────────────
    channels = settings.telegram_channels
    if not channels:
        log.warning("auto_publish: no Telegram channels configured, skipping")
        return {"published": 0, "skipped": 0, "errors": 0, "reason": "no_channels"}

    # ── Guard: publish mode ───────────────────────────────────────
    mode = (settings.publish_mode or "manual").strip().lower()
    if mode != "auto":
        log.info("auto_publish: PUBLISH_MODE=%s (not 'auto'), skipping", mode)
        return {"published": 0, "skipped": 0, "errors": 0, "reason": f"mode_{mode}"}

    now = utcnow()

    # ── Guard: quiet hours (no posts before 08:00 CET = 06:00 UTC) ─
    quiet_hour = int(settings.publish_quiet_before_utc_hour or 6)
    if now.hour < quiet_hour:
        log.info(
            "auto_publish: quiet hours (before %02d:00 UTC), skipping",
            quiet_hour,
        )
        return {"published": 0, "skipped": 0, "errors": 0, "reason": "quiet_hours"}

    # ── Import here to avoid circular imports ─────────────────────
    from app.services.publishing import publish_fixture
    from app.core.db import SessionLocal

    window_hours = int(settings.auto_publish_window_hours or 48)
    cutoff = now + timedelta(hours=window_hours)

    # ── Step 1: find unpublished fixtures ─────────────────────────
    # Check BOTH predictions (1X2) and predictions_totals (TOTAL only).
    # DOUBLE_CHANCE is excluded because publish_fixture doesn't support it yet.
    result = await session.execute(
        text("""
            SELECT DISTINCT f.id, f.kickoff
            FROM fixtures f
            WHERE f.kickoff > :now
              AND f.kickoff < :cutoff
              AND f.status IN ('NS', 'TBD')
              AND (
                EXISTS (
                  SELECT 1 FROM predictions p
                  WHERE p.fixture_id = f.id
                    AND p.selection_code IS NOT NULL
                    AND p.selection_code != 'SKIP'
                )
                OR EXISTS (
                  SELECT 1 FROM predictions_totals pt
                  WHERE pt.fixture_id = f.id
                    AND pt.market = 'TOTAL'
                    AND pt.selection IS NOT NULL
                    AND pt.selection != 'SKIP'
                )
              )
              AND NOT EXISTS (
                SELECT 1 FROM prediction_publications pp
                WHERE pp.fixture_id = f.id
                  AND pp.status IN ('ok', 'published')
              )
            ORDER BY f.kickoff ASC
        """),
        {"now": now, "cutoff": cutoff},
    )
    rows = result.fetchall()

    if not rows:
        log.info("auto_publish: no unpublished fixtures found")
        return {"published": 0, "skipped": 0, "errors": 0, "pending": 0}

    n_pending = len(rows)
    fixture_ids = [r[0] for r in rows]
    earliest_kickoff = _ensure_utc(rows[0][1])

    # ── Step 2: calculate dynamic interval ────────────────────────
    ideal_interval = int(settings.publish_ideal_interval_seconds or 3600)
    deadline_buffer = int(settings.publish_deadline_buffer_seconds or 3600)
    min_interval = int(settings.publish_min_interval_seconds or 60)

    hard_deadline = earliest_kickoff - timedelta(seconds=deadline_buffer)

    # If deadline is before quiet-hours end today, shift effective
    # start to quiet-hours end so we don't count sleeping time.
    today_quiet_end = now.replace(
        hour=quiet_hour, minute=0, second=0, microsecond=0,
    )
    effective_start = max(now, today_quiet_end)
    time_available = (hard_deadline - effective_start).total_seconds()

    urgent = False
    if time_available <= 0:
        # Past the hard deadline – urgent bulk drain
        effective_interval = min_interval
        urgent = True
        log.warning(
            "auto_publish: URGENT past deadline by %.0fs, N=%d, "
            "using min_interval=%ds",
            abs(time_available), n_pending, min_interval,
        )
    else:
        needed = n_pending * ideal_interval
        if needed <= time_available:
            effective_interval = ideal_interval
        else:
            effective_interval = time_available / n_pending
            effective_interval = max(effective_interval, min_interval)

    log.info(
        "auto_publish: N=%d earliest_kickoff=%s deadline=%s "
        "time_avail=%.0fs interval=%.0fs urgent=%s",
        n_pending,
        earliest_kickoff.strftime("%Y-%m-%d %H:%M"),
        hard_deadline.strftime("%Y-%m-%d %H:%M"),
        max(time_available, 0),
        effective_interval,
        urgent,
    )

    # ── Step 3: cooldown check ────────────────────────────────────
    last_pub_row = await session.execute(
        text("""
            SELECT MAX(published_at) AS last_pub
            FROM prediction_publications
            WHERE status IN ('ok', 'published')
        """)
    )
    last_pub = last_pub_row.scalar()

    if last_pub is not None:
        last_pub = _ensure_utc(last_pub)
        elapsed = (now - last_pub).total_seconds()
        if elapsed < effective_interval:
            remaining = effective_interval - elapsed
            log.info(
                "auto_publish: cooldown %.0fs remaining "
                "(elapsed=%.0fs, interval=%.0fs, pending=%d)",
                remaining, elapsed, effective_interval, n_pending,
            )
            return {
                "published": 0,
                "skipped": 0,
                "errors": 0,
                "pending": n_pending,
                "reason": "cooldown",
                "remaining_seconds": round(remaining),
                "interval_seconds": round(effective_interval),
            }

    # ── Step 4: publish ONE fixture ───────────────────────────────
    # Use a FRESH session for publish_fixture because it runs for 2+ min
    # (AI generation + Telegram sends) and the original session may timeout.
    next_fid = fixture_ids[0]
    log.info(
        "auto_publish: publishing fixture=%s (1 of %d, interval=%.0fs)",
        next_fid, n_pending, effective_interval,
    )

    try:
        async with SessionLocal() as pub_session:
            pub_result = await publish_fixture(pub_session, next_fid)
        results_list = pub_result.get("results", [])
        any_ok = any(
            r.get("status") in ("ok", "published", "sent")
            for r in results_list
        )
        all_skipped = all(r.get("status") == "skipped" for r in results_list)

        if any_ok:
            log.info("auto_publish: fixture=%s published OK", next_fid)
            return {
                "published": 1,
                "skipped": 0,
                "errors": 0,
                "pending": n_pending - 1,
                "interval_seconds": round(effective_interval),
                "fixture_id": next_fid,
            }
        elif all_skipped:
            log.info(
                "auto_publish: fixture=%s all skipped (%s)",
                next_fid, results_list,
            )
            return {
                "published": 0,
                "skipped": 1,
                "errors": 0,
                "pending": n_pending,
                "fixture_id": next_fid,
            }
        else:
            log.warning(
                "auto_publish: fixture=%s partial (%s)",
                next_fid, results_list,
            )
            return {
                "published": 0,
                "skipped": 1,
                "errors": 0,
                "pending": n_pending,
                "fixture_id": next_fid,
            }
    except Exception:
        log.exception("auto_publish: fixture=%s failed", next_fid)
        return {
            "published": 0,
            "skipped": 0,
            "errors": 1,
            "pending": n_pending,
            "fixture_id": next_fid,
        }
