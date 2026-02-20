import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import settings
from app.core.db import init_db
from app.core.http import init_http_clients, close_http_clients
from app.jobs import build_predictions, compute_indices, evaluate_results, sync_data, quality_report
from app.jobs import maintenance
from app.main import _run_job, _snapshot_autofill_tick, _validate_runtime_config

logger = logging.getLogger(__name__)


async def _scheduled_sync_data():
    await _run_job("sync_data", sync_data.run, triggered_by="scheduler")


async def _scheduled_compute_indices():
    await _run_job("compute_indices", compute_indices.run, triggered_by="scheduler")


async def _scheduled_build_predictions():
    await _run_job("build_predictions", build_predictions.run, triggered_by="scheduler")


async def _scheduled_evaluate_results():
    await _run_job("evaluate_results", evaluate_results.run, triggered_by="scheduler")


async def _scheduled_maintenance():
    await _run_job("maintenance", maintenance.run, triggered_by="scheduler")

async def _scheduled_quality_report():
    await _run_job("quality_report", quality_report.run, triggered_by="scheduler")


async def main() -> None:
    await init_db()
    await init_http_clients()
    _validate_runtime_config(for_scheduler=True)

    if not settings.scheduler_enabled:
        logger.warning("SCHEDULER_ENABLED=false; scheduler runner exiting")
        return

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        _scheduled_sync_data,
        CronTrigger.from_crontab(settings.sync_data_cron),
        id="sync_data",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
    )
    scheduler.add_job(
        _scheduled_compute_indices,
        CronTrigger.from_crontab(settings.job_compute_indices_cron),
        id="compute_indices",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
    )
    scheduler.add_job(
        _scheduled_build_predictions,
        CronTrigger.from_crontab(settings.job_build_predictions_cron),
        id="build_predictions",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
    )
    scheduler.add_job(
        _scheduled_evaluate_results,
        CronTrigger.from_crontab(settings.job_evaluate_results_cron),
        id="evaluate_results",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
    )
    scheduler.add_job(
        _scheduled_maintenance,
        CronTrigger.from_crontab(settings.job_maintenance_cron),
        id="maintenance",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
    )
    scheduler.add_job(
        _scheduled_quality_report,
        CronTrigger.from_crontab(settings.job_quality_report_cron),
        id="quality_report",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
    )

    if settings.snapshot_autofill_enabled:
        scheduler.add_job(
            _snapshot_autofill_tick,
            IntervalTrigger(minutes=int(settings.snapshot_autofill_interval_minutes or 10)),
            id="snapshot_autofill",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=60,
        )

    scheduler.start()
    logger.info("scheduler_runner_started")
    try:
        await asyncio.Event().wait()
    finally:
        scheduler.shutdown(wait=False)
        try:
            await close_http_clients()
        except Exception:
            logger.exception("http_client_close_failed")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
