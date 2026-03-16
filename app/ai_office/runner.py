"""AI Office runner — main entry point for Docker service.

Runs:
1. APScheduler with monitor, analyst, scout, content, and news agents on cron schedules
2. Telegram bot polling loop
"""

from __future__ import annotations

import asyncio
import signal
import sys

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import settings
from app.core.db import SessionLocal, init_db
from app.core.http import init_http_clients, close_http_clients
from app.core.logger import get_logger

log = get_logger("ai_office.runner")


async def _run_monitor() -> None:
    """Wrapper to run monitor agent with its own DB session."""
    from app.ai_office.agents.monitor import run as monitor_run

    log.info("scheduled_monitor_start")
    try:
        async with SessionLocal() as session:
            result = await monitor_run(session)
            log.info("scheduled_monitor_done result=%s", result)
    except Exception:
        log.exception("scheduled_monitor_error")


async def _run_analyst() -> None:
    """Wrapper to run analyst agent with its own DB session."""
    from app.ai_office.agents.analyst import run as analyst_run

    log.info("scheduled_analyst_start")
    try:
        async with SessionLocal() as session:
            result = await analyst_run(session)
            log.info("scheduled_analyst_done result=%s", result)
    except Exception:
        log.exception("scheduled_analyst_error")


async def _run_scout() -> None:
    """Wrapper to run scout agent with its own DB session."""
    from app.ai_office.agents.scout import run as scout_run

    log.info("scheduled_scout_start")
    try:
        async with SessionLocal() as session:
            result = await scout_run(session)
            log.info("scheduled_scout_done result=%s", result)
    except Exception:
        log.exception("scheduled_scout_error")


async def _run_content_picks() -> None:
    """Wrapper to run content picks agent with its own DB session."""
    from app.ai_office.agents.content import run as content_run

    log.info("scheduled_content_picks_start")
    try:
        async with SessionLocal() as session:
            result = await content_run(session)
            log.info("scheduled_content_picks_done result=%s", result)
    except Exception:
        log.exception("scheduled_content_picks_error")


async def _run_news() -> None:
    """Wrapper to run news agent with its own DB session."""
    from app.ai_office.agents.news import run as news_run

    log.info("scheduled_news_start")
    try:
        async with SessionLocal() as session:
            result = await news_run(session)
            log.info("scheduled_news_done result=%s", result)
    except Exception:
        log.exception("scheduled_news_error")


async def _run_researcher() -> None:
    """Wrapper to run researcher agent with its own DB session."""
    from app.ai_office.agents.researcher import run as researcher_run

    log.info("scheduled_researcher_start")
    try:
        async with SessionLocal() as session:
            result = await researcher_run(session)
            log.info("scheduled_researcher_done result=%s", result)
    except Exception:
        log.exception("scheduled_researcher_error")


async def _run_cleanup() -> None:
    """Wrapper to run TTL cleanup for old reports/news."""
    from app.ai_office.queries import (
        cleanup_old_reports, cleanup_old_scout_reports, cleanup_old_news,
    )

    log.info("scheduled_cleanup_start")
    try:
        async with SessionLocal() as session:
            r1 = await cleanup_old_reports(session, days=90)
            r2 = await cleanup_old_scout_reports(session, days=90)
            r3 = await cleanup_old_news(session, days=90)
            log.info("scheduled_cleanup_done reports=%d scout=%d news=%d", r1, r2, r3)
    except Exception:
        log.exception("scheduled_cleanup_error")


async def main() -> None:
    """Main entry point: init DB, schedule agents, run bot polling."""
    log.info("ai_office_starting")

    # Validate required config
    if not settings.telegram_bot_token:
        log.error("TELEGRAM_BOT_TOKEN is required for AI Office")
        sys.exit(1)

    if not settings.telegram_owner_id:
        log.warning("TELEGRAM_OWNER_ID not set — bot will ignore all messages")

    # Init infrastructure
    await init_db()
    await init_http_clients()
    log.info("ai_office_infra_ready")

    # Setup scheduler
    scheduler = AsyncIOScheduler()

    cron_expr = settings.ai_office_monitor_cron
    try:
        trigger = CronTrigger.from_crontab(cron_expr)
        scheduler.add_job(
            _run_monitor,
            trigger=trigger,
            id="ai_office_monitor",
            max_instances=1,
            coalesce=True,
            name="AI Office Monitor",
        )
        log.info("scheduler_job_added job=monitor cron=%s", cron_expr)
    except Exception:
        log.exception("scheduler_cron_parse_failed cron=%s", cron_expr)
        sys.exit(1)

    # Analyst agent — daily settled predictions report
    analyst_cron = settings.ai_office_analyst_cron
    try:
        trigger = CronTrigger.from_crontab(analyst_cron)
        scheduler.add_job(
            _run_analyst,
            trigger=trigger,
            id="ai_office_analyst",
            max_instances=1,
            coalesce=True,
            name="AI Office Analyst",
        )
        log.info("scheduler_job_added job=analyst cron=%s", analyst_cron)
    except Exception:
        log.exception("scheduler_cron_parse_failed cron=%s", analyst_cron)

    # Scout agent — contextual match analysis
    scout_cron = settings.ai_office_scout_cron
    try:
        trigger = CronTrigger.from_crontab(scout_cron)
        scheduler.add_job(
            _run_scout,
            trigger=trigger,
            id="ai_office_scout",
            max_instances=1,
            coalesce=True,
            name="AI Office Scout",
        )
        log.info("scheduler_job_added job=scout cron=%s", scout_cron)
    except Exception:
        log.exception("scheduler_cron_parse_failed cron=%s", scout_cron)

    # Content picks agent — daily predictions post
    content_cron = settings.ai_office_content_picks_cron
    try:
        trigger = CronTrigger.from_crontab(content_cron)
        scheduler.add_job(
            _run_content_picks,
            trigger=trigger,
            id="ai_office_content_picks",
            max_instances=1,
            coalesce=True,
            name="AI Office Content Picks",
        )
        log.info("scheduler_job_added job=content_picks cron=%s", content_cron)
    except Exception:
        log.exception("scheduler_cron_parse_failed cron=%s", content_cron)

    # News agent — RSS feed parsing and article generation
    news_cron = settings.ai_office_news_cron
    try:
        trigger = CronTrigger.from_crontab(news_cron)
        scheduler.add_job(
            _run_news,
            trigger=trigger,
            id="ai_office_news",
            max_instances=1,
            coalesce=True,
            name="AI Office News",
        )
        log.info("scheduler_job_added job=news cron=%s", news_cron)
    except Exception:
        log.exception("scheduler_cron_parse_failed cron=%s", news_cron)

    # Researcher agent — weekly research report
    researcher_cron = settings.ai_office_researcher_cron
    try:
        trigger = CronTrigger.from_crontab(researcher_cron)
        scheduler.add_job(
            _run_researcher,
            trigger=trigger,
            id="ai_office_researcher",
            max_instances=1,
            coalesce=True,
            name="AI Office Researcher",
        )
        log.info("scheduler_job_added job=researcher cron=%s", researcher_cron)
    except Exception:
        log.exception("scheduler_cron_parse_failed cron=%s", researcher_cron)

    # TTL cleanup — daily at 03:00 UTC
    try:
        trigger = CronTrigger.from_crontab("0 3 * * *")
        scheduler.add_job(
            _run_cleanup,
            trigger=trigger,
            id="ai_office_cleanup",
            max_instances=1,
            coalesce=True,
            name="AI Office Cleanup",
        )
        log.info("scheduler_job_added job=cleanup cron=0 3 * * *")
    except Exception:
        log.exception("scheduler_cron_parse_failed cron=0 3 * * *")

    scheduler.start()
    log.info("scheduler_started jobs=%d", len(scheduler.get_jobs()))

    # Build and run Telegram bot
    from app.ai_office.telegram_bot import build_bot

    bot_app = build_bot()

    # Graceful shutdown handler
    shutdown_event = asyncio.Event()

    def _signal_handler(sig, frame):
        log.info("shutdown_signal_received sig=%s", sig)
        shutdown_event.set()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    log.info("ai_office_ready — bot polling starting")

    try:
        # Initialize and start polling
        await bot_app.initialize()
        await bot_app.start()
        await bot_app.updater.start_polling(drop_pending_updates=True)

        log.info("bot_polling_started")

        # Wait for shutdown signal
        await shutdown_event.wait()
    except Exception:
        log.exception("bot_polling_error")
    finally:
        log.info("ai_office_shutting_down")
        scheduler.shutdown(wait=False)

        try:
            await bot_app.updater.stop()
            await bot_app.stop()
            await bot_app.shutdown()
        except Exception:
            log.exception("bot_shutdown_error")

        await close_http_clients()
        log.info("ai_office_stopped")


if __name__ == "__main__":
    asyncio.run(main())
