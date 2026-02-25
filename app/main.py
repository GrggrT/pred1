import asyncio
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from contextlib import asynccontextmanager
from typing import Optional
import json
import traceback
import hashlib
import os
import time
import sys

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
import logging
from pathlib import Path
from fastapi import FastAPI, Depends, Query, Header, HTTPException, Response, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel
from sqlalchemy import text, bindparam
from sqlalchemy.types import Integer, DateTime as SADateTime, String as SAString
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import STATS_EPOCH, settings
from app.core.db import SessionLocal, get_session, init_db, engine
from app.core.http import init_http_clients, close_http_clients
from app.jobs import build_predictions, compute_indices, evaluate_results, sync_data, quality_report
from app.jobs import maintenance
from app.jobs import rebuild_elo
from app.jobs import fit_dixon_coles
from app.core.timeutils import utcnow, ensure_aware_utc
from app.services.elo_ratings import get_team_rating
from app.services.api_football_quota import api_football_usage_since, quota_guard_decision, utc_day_window
from app.services import info_report
from app.services import publishing
from app.data.providers.api_football import get_fixtures, set_force_refresh, reset_force_refresh

scheduler = AsyncIOScheduler()
logger = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parent
APP_STARTED_AT = utcnow()
_UI_SHA256: str | None = None
_UI_MTIME_ISO: str | None = None
_UI_CSS_SHA256: str | None = None
_UI_CSS_MTIME_ISO: str | None = None
_UI_JS_SHA256: str | None = None
_UI_JS_MTIME_ISO: str | None = None
JOB_LOCKS: dict[str, asyncio.Lock] = {}
PIPELINE_LOCK = asyncio.Lock()
JOB_STATUS: dict[str, dict] = {}
PIPELINE_STATUS: dict[str, object] = {}
RUN_NOW_RATE: dict[str, list[float]] = {}
RUN_NOW_LAST: dict[str, float] = {}
PUBLIC_API_RATE: dict[str, list[float]] = {}
RECENT_FIXTURE_REFRESH_LOCK = asyncio.Lock()
RECENT_FIXTURE_REFRESH_LAST_AT: datetime | None = None


def _file_sha256_hex(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except Exception:
        return None


def _file_mtime_iso(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
    except Exception:
        return None


def _advisory_key(name: str) -> int:
    digest = hashlib.blake2b(f"pred1:{name}".encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") & 0x7FFF_FFFF_FFFF_FFFF


async def _try_advisory_lock(conn, key: int) -> bool:
    try:
        row = (await conn.execute(text("SELECT pg_try_advisory_lock(:k) AS ok"), {"k": int(key)})).first()
        return bool(row.ok) if row else False
    except Exception as e:
        logger.warning(f"Failed to acquire advisory lock {key}: {e}")
        return False


async def _advisory_unlock(conn, key: int) -> None:
    # Use a new connection for advisory unlock to avoid isolation level conflicts
    try:
        await conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": int(key)})
    except Exception as e:
        logger.warning(f"Failed to release advisory lock {key}: {e}")
        # In case of failure, try with new connection
        try:
            async with engine.begin() as new_conn:
                await new_conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": int(key)})
        except Exception:
            logger.error(f"Could not release advisory lock {key} with new connection")


def _require_admin(x_admin_token: str | None = Header(default=None, alias="X-Admin-Token")):
    token = (settings.admin_token or "").strip()
    if not token:
        raise HTTPException(status_code=403, detail="Admin token is not configured")
    if x_admin_token != token:
        raise HTTPException(status_code=403, detail="Forbidden")


def _check_public_rate(request: Request):
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    cutoff = now - 60
    hits = PUBLIC_API_RATE.get(ip, [])
    PUBLIC_API_RATE[ip] = [t for t in hits if t > cutoff]
    if len(PUBLIC_API_RATE[ip]) >= 60:
        raise HTTPException(status_code=429, detail="Rate limit exceeded", headers={"Retry-After": "60"})
    PUBLIC_API_RATE[ip].append(now)


def _invalid_api_key() -> bool:
    v = (settings.api_football_key or "").strip()
    return v in {"", "YOUR_KEY", "your_paid_key"}


def _validate_runtime_config(*, for_scheduler: bool) -> None:
    env = (settings.app_env or "dev").strip().lower()
    if env in {"prod", "production"}:
        if not (settings.admin_token or "").strip():
            raise RuntimeError("ADMIN_TOKEN is required in prod")
        if _invalid_api_key():
            raise RuntimeError("API_FOOTBALL_KEY is required in prod")
    else:
        if for_scheduler and _invalid_api_key():
            logger.warning("API_FOOTBALL_KEY is not configured; sync_data will fail")


def _get_lock(name: str) -> asyncio.Lock:
    lock = JOB_LOCKS.get(name)
    if lock is None:
        lock = asyncio.Lock()
        JOB_LOCKS[name] = lock
    return lock


def _set_status(store: dict, key: str, **values):
    cur = store.get(key) or {}
    cur.update(values)
    store[key] = cur


def _serialize_status(store: dict) -> dict:
    out: dict = {}
    for k, v in store.items():
        row = dict(v)
        for ts_key in ("started_at", "finished_at"):
            ts = row.get(ts_key)
            if isinstance(ts, datetime):
                row[ts_key] = ts.isoformat()
        out[k] = row
    return out


def _create_task(coro, *, label: str):
    task = asyncio.create_task(coro)

    def _done(t: asyncio.Task):
        try:
            t.result()
        except Exception:
            logger.exception("background_task_failed label=%s", label)

    task.add_done_callback(_done)
    return task


async def _auto_settle_finished_bets(session: AsyncSession) -> dict[str, int]:
    """Settle pending bets for finished/cancelled fixtures to avoid stale history lag."""
    settled_1x2 = 0
    settled_totals = 0
    try:
        res_1x2 = await session.execute(
            text(
                """
                UPDATE predictions p
                SET
                  status = CASE
                    WHEN f.status IN ('CANC', 'ABD', 'AWD', 'WO') THEN 'VOID'
                    WHEN f.home_goals IS NULL OR f.away_goals IS NULL THEN 'VOID'
                    WHEN (
                      (f.home_goals > f.away_goals AND p.selection_code = 'HOME_WIN')
                      OR (f.home_goals = f.away_goals AND p.selection_code = 'DRAW')
                      OR (f.home_goals < f.away_goals AND p.selection_code = 'AWAY_WIN')
                    ) THEN 'WIN'
                    ELSE 'LOSS'
                  END,
                  profit = CASE
                    WHEN f.status IN ('CANC', 'ABD', 'AWD', 'WO') THEN 0::numeric
                    WHEN f.home_goals IS NULL OR f.away_goals IS NULL OR p.initial_odd IS NULL THEN 0::numeric
                    WHEN (
                      (f.home_goals > f.away_goals AND p.selection_code = 'HOME_WIN')
                      OR (f.home_goals = f.away_goals AND p.selection_code = 'DRAW')
                      OR (f.home_goals < f.away_goals AND p.selection_code = 'AWAY_WIN')
                    ) THEN (p.initial_odd - 1)
                    ELSE (-1)::numeric
                  END,
                  settled_at = now()
                FROM fixtures f
                WHERE f.id = p.fixture_id
                  AND p.selection_code != 'SKIP'
                  AND p.status = 'PENDING'
                  AND f.status IN ('FT', 'AET', 'PEN', 'CANC', 'ABD', 'AWD', 'WO')
                """
            )
        )
        settled_1x2 = int(getattr(res_1x2, "rowcount", 0) or 0)

        res_totals = await session.execute(
            text(
                """
                UPDATE predictions_totals pt
                SET
                  status = CASE
                    WHEN f.status IN ('CANC', 'ABD', 'AWD', 'WO') THEN 'VOID'
                    WHEN f.home_goals IS NULL OR f.away_goals IS NULL THEN 'VOID'
                    WHEN (
                      (pt.selection = 'OVER_2_5' AND (f.home_goals + f.away_goals) >= 3)
                      OR (pt.selection = 'UNDER_2_5' AND (f.home_goals + f.away_goals) <= 2)
                    ) THEN 'WIN'
                    WHEN pt.selection IN ('OVER_2_5', 'UNDER_2_5') THEN 'LOSS'
                    ELSE 'VOID'
                  END,
                  profit = CASE
                    WHEN f.status IN ('CANC', 'ABD', 'AWD', 'WO') THEN 0::numeric
                    WHEN f.home_goals IS NULL OR f.away_goals IS NULL OR pt.initial_odd IS NULL THEN 0::numeric
                    WHEN (
                      (pt.selection = 'OVER_2_5' AND (f.home_goals + f.away_goals) >= 3)
                      OR (pt.selection = 'UNDER_2_5' AND (f.home_goals + f.away_goals) <= 2)
                    ) THEN (pt.initial_odd - 1)
                    WHEN pt.selection IN ('OVER_2_5', 'UNDER_2_5') THEN (-1)::numeric
                    ELSE 0::numeric
                  END,
                  settled_at = now()
                FROM fixtures f
                WHERE f.id = pt.fixture_id
                  AND pt.market = 'TOTAL'
                  AND COALESCE(pt.status, 'PENDING') = 'PENDING'
                  AND f.status IN ('FT', 'AET', 'PEN', 'CANC', 'ABD', 'AWD', 'WO')
                """
            )
        )
        settled_totals = int(getattr(res_totals, "rowcount", 0) or 0)

        if settled_1x2 or settled_totals:
            await session.commit()
            logger.info("auto_settle_finished_bets settled_1x2=%s settled_totals=%s", settled_1x2, settled_totals)
    except Exception:
        await session.rollback()
        logger.exception("auto_settle_finished_bets_failed")
    return {"settled_1x2": settled_1x2, "settled_totals": settled_totals}


def _has_valid_api_football_key() -> bool:
    key = (settings.api_football_key or "").strip()
    return key not in {"", "YOUR_KEY", "your_paid_key"}


async def _recent_active_league_ids(
    session: AsyncSession,
    *,
    window_start: datetime,
    window_end: datetime,
    limit: int = 20,
) -> list[int]:
    stmt = (
        text(
            """
            SELECT DISTINCT f.league_id
            FROM fixtures f
            WHERE f.league_id IS NOT NULL
              AND f.kickoff >= :window_start
              AND f.kickoff <= :window_end
              AND COALESCE(f.status, 'UNK') NOT IN ('FT', 'AET', 'PEN', 'CANC', 'ABD', 'AWD', 'WO')
              AND (
                EXISTS (
                  SELECT 1
                  FROM predictions p
                  WHERE p.fixture_id = f.id
                    AND p.selection_code != 'SKIP'
                    AND COALESCE(p.status, 'PENDING') = 'PENDING'
                )
                OR EXISTS (
                  SELECT 1
                  FROM predictions_totals pt
                  WHERE pt.fixture_id = f.id
                    AND pt.market = 'TOTAL'
                    AND COALESCE(pt.status, 'PENDING') = 'PENDING'
                )
              )
            ORDER BY f.league_id
            LIMIT :limit
            """
        ).bindparams(
            bindparam("window_start", type_=SADateTime(timezone=True)),
            bindparam("window_end", type_=SADateTime(timezone=True)),
            bindparam("limit", type_=Integer),
        )
    )
    res = await session.execute(
        stmt,
        {
            "window_start": window_start,
            "window_end": window_end,
            "limit": int(limit),
        },
    )
    out: list[int] = []
    for row in res.fetchall():
        try:
            lid = int(row.league_id)
        except Exception:
            continue
        out.append(lid)
    return out


def _season_candidates_for_now(now_utc: datetime) -> list[int]:
    out: list[int] = []
    raw = [int(getattr(settings, "season", 0) or 0), now_utc.year - 1, now_utc.year, now_utc.year + 1]
    for item in raw:
        try:
            v = int(item)
        except Exception:
            continue
        if v < 2000:
            continue
        if v not in out:
            out.append(v)
    return out


async def _refresh_recent_fixture_statuses(session: AsyncSession) -> dict[str, int | bool]:
    """
    Refresh near-now fixtures with force-refresh (short throttled cadence) so UI doesn't
    lag on NS/PENDING cache when matches are already live/finished.
    """
    global RECENT_FIXTURE_REFRESH_LAST_AT
    if not _has_valid_api_football_key():
        return {"refreshed": False, "leagues": 0, "fixtures_upserted": 0}

    now_utc = utcnow()
    min_interval_seconds = 90
    window_start = now_utc - timedelta(hours=8)
    window_end = now_utc + timedelta(hours=8)

    if RECENT_FIXTURE_REFRESH_LAST_AT is not None:
        age = (now_utc - RECENT_FIXTURE_REFRESH_LAST_AT).total_seconds()
        if age < min_interval_seconds:
            return {"refreshed": False, "leagues": 0, "fixtures_upserted": 0}

    async with RECENT_FIXTURE_REFRESH_LOCK:
        now_utc = utcnow()
        if RECENT_FIXTURE_REFRESH_LAST_AT is not None:
            age = (now_utc - RECENT_FIXTURE_REFRESH_LAST_AT).total_seconds()
            if age < min_interval_seconds:
                return {"refreshed": False, "leagues": 0, "fixtures_upserted": 0}

        league_ids = await _recent_active_league_ids(
            session,
            window_start=window_start,
            window_end=window_end,
            limit=20,
        )
        if not league_ids:
            RECENT_FIXTURE_REFRESH_LAST_AT = now_utc
            return {"refreshed": False, "leagues": 0, "fixtures_upserted": 0}

        token = set_force_refresh(True)
        upserted = 0
        seasons = _season_candidates_for_now(now_utc)
        try:
            for lid in league_ids:
                data = None
                for season in seasons:
                    probe = await get_fixtures(session, lid, season, window_start, window_end)
                    if probe.get("response"):
                        data = probe
                        break
                if data is None:
                    data = {"response": []}
                for item in data.get("response", []):
                    await sync_data._upsert_fixture(session, item)
                    upserted += 1
            await session.commit()
            RECENT_FIXTURE_REFRESH_LAST_AT = now_utc
            logger.info("recent_fixture_status_refresh leagues=%s fixtures_upserted=%s", len(league_ids), upserted)
            return {"refreshed": True, "leagues": len(league_ids), "fixtures_upserted": upserted}
        except Exception:
            await session.rollback()
            logger.exception("recent_fixture_status_refresh_failed")
            return {"refreshed": False, "leagues": 0, "fixtures_upserted": 0}
        finally:
            reset_force_refresh(token)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    await init_http_clients()
    _validate_runtime_config(for_scheduler=bool(settings.scheduler_enabled))

    if settings.scheduler_enabled:
        workers_raw = os.getenv("UVICORN_WORKERS") or os.getenv("WEB_CONCURRENCY") or "1"
        try:
            workers = int(workers_raw)
        except Exception:
            workers = 1
        env = (settings.app_env or "dev").strip().lower()
        if workers > 1:
            logger.error("scheduler_refuse_multiworker workers=%s", workers)
            raise RuntimeError("scheduler is not allowed with UVICORN_WORKERS/WEB_CONCURRENCY > 1; run a separate scheduler service")
        if env in {"prod", "production"} and not settings.allow_web_scheduler:
            logger.error("scheduler_refuse_in_web_process env=%s", env)
            raise RuntimeError("scheduler in web process is disabled in prod; set ALLOW_WEB_SCHEDULER=true or run a separate scheduler service")

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

        async def _scheduled_fit_dixon_coles():
            await _run_job("fit_dixon_coles", fit_dixon_coles.run, triggered_by="scheduler")

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
        scheduler.add_job(
            _scheduled_fit_dixon_coles,
            CronTrigger.from_crontab(settings.job_fit_dixon_coles_cron),
            id="fit_dixon_coles",
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
    try:
        yield
    finally:
        if settings.scheduler_enabled:
            scheduler.shutdown(wait=False)
        try:
            await close_http_clients()
        except Exception:
            logger.exception("http_client_close_failed")
        try:
            await engine.dispose()
        except Exception:
            logger.exception("engine_dispose_failed")


app = FastAPI(title="Fatigue & Chaos MVP", lifespan=lifespan)

# CORS — allow public API from any origin (read-only)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
    expose_headers=["X-Total-Count"],
)


# Security headers middleware
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        # CSP for new frontends
        if path == "/" or path.startswith("/public") or path.startswith("/shared"):
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self'; "
                "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                "font-src 'self' https://fonts.gstatic.com; "
                "img-src 'self' https://media.api-sports.io data:; "
                "connect-src 'self'; "
                "frame-ancestors 'none'"
            )
        elif path.startswith("/admin"):
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self'; "
                "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                "font-src 'self' https://fonts.gstatic.com; "
                "img-src 'self' https://media.api-sports.io data:; "
                "connect-src 'self'; "
                "frame-ancestors 'none'"
            )
        # Common security headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response


app.add_middleware(SecurityHeadersMiddleware)


async def _snapshot_autofill_tick():
    if not settings.snapshot_autofill_enabled:
        return
    if settings.backtest_mode:
        return

    now_utc = utcnow()
    window_hours = int(settings.snapshot_autofill_window_hours or 12)
    window_end = now_utc + timedelta(hours=window_hours)
    cooldown_min = int(settings.snapshot_autofill_min_interval_minutes or 10)
    urgent_min = int(settings.snapshot_autofill_urgent_minutes or 60)
    trigger_before_min = int(settings.snapshot_autofill_trigger_before_minutes or 360)
    accel_threshold = int(settings.snapshot_autofill_accel_due_gaps_threshold or 20)
    accel_trigger_before_min = int(settings.snapshot_autofill_accel_trigger_before_minutes or 120)

    run_id = await _db_job_run_start(
        "snapshot_autofill",
        triggered_by="scheduler",
        meta={
            "window_hours": window_hours,
            "cooldown_minutes": cooldown_min,
            "urgent_minutes": urgent_min,
            "trigger_before_minutes": trigger_before_min,
            "accel_due_gaps_threshold": accel_threshold,
            "accel_trigger_before_minutes": accel_trigger_before_min,
        },
    )
    _set_status(JOB_STATUS, "snapshot_autofill", status="running", started_at=now_utc, finished_at=None, error=None)
    try:
        async with SessionLocal() as session:
            # Use base trigger_before first for stats; may be accelerated later.
            res = await session.execute(
                text(
                    """
                    SELECT COUNT(*) AS cnt, MIN(kickoff) AS soonest,
                           COUNT(*) FILTER (WHERE kickoff < (CAST(:now_utc AS timestamptz) + (CAST(:urgent_min AS int) * interval '1 minute'))) AS urgent_cnt,
                           COUNT(*) FILTER (WHERE kickoff < (CAST(:now_utc AS timestamptz) + (CAST(:trigger_before_min AS int) * interval '1 minute'))) AS due_cnt
                    FROM fixtures f
                    WHERE f.status='NS'
                      AND f.league_id IN (SELECT unnest(CAST(:lids AS integer[])))
                      AND f.kickoff >= :now_utc
                      AND f.kickoff < :end_utc
                      AND NOT EXISTS (
                        SELECT 1
                        FROM odds_snapshots os
                        WHERE os.fixture_id=f.id
                          AND os.bookmaker_id=:bid
                          AND os.fetched_at < f.kickoff
                      )
                    """
                ),
                {
                    "lids": settings.league_ids,
                    "now_utc": now_utc,
                    "end_utc": window_end,
                    "bid": settings.bookmaker_id,
                    "urgent_min": urgent_min,
                    "trigger_before_min": trigger_before_min,
                },
            )
            row = res.first()
            gaps = int(row.cnt or 0) if row else 0
            soonest = row.soonest if row else None
            urgent_cnt = int(row.urgent_cnt or 0) if row else 0
            due_cnt = int(row.due_cnt or 0) if row else 0

            last_sync = (
                await session.execute(
                    text(
                        """
                        SELECT started_at, status, triggered_by
                        FROM job_runs
                        WHERE job_name='sync_data'
                        ORDER BY started_at DESC
                        LIMIT 1
                        """
                    )
                )
            ).first()
            last_started = ensure_aware_utc(last_sync.started_at) if last_sync and last_sync.started_at else None
            minutes_since = None
            if last_started:
                try:
                    minutes_since = (now_utc - last_started).total_seconds() / 60.0
                except Exception:
                    minutes_since = None

            soon_mins = None
            if soonest:
                try:
                    soon_mins = int((ensure_aware_utc(soonest) - now_utc).total_seconds() // 60)
                except Exception:
                    soon_mins = None

        payload = {
            "gaps": gaps,
            "urgent_gaps": urgent_cnt,
            "due_gaps": due_cnt,
            "window_end": window_end.isoformat(),
            "soonest_kickoff": ensure_aware_utc(soonest).isoformat() if soonest else None,
            "soonest_minutes": soon_mins,
            "last_sync_started_at": last_started.isoformat() if last_started else None,
            "minutes_since_last_sync": minutes_since,
            "trigger_before_minutes": trigger_before_min,
            "accel_due_gaps_threshold": accel_threshold,
            "accel_trigger_before_minutes": accel_trigger_before_min,
        }

        if gaps <= 0:
            payload["triggered"] = False
            payload["reason"] = "no_gaps"
            _set_status(JOB_STATUS, "snapshot_autofill", status="ok", finished_at=utcnow(), error=None)
            await _db_job_run_finish(run_id, "ok", None, meta=payload)
            return

        if _get_lock("sync_data").locked():
            payload["triggered"] = False
            payload["reason"] = "sync_data_running"
            _set_status(JOB_STATUS, "snapshot_autofill", status="ok", finished_at=utcnow(), error=None)
            await _db_job_run_finish(run_id, "ok", None, meta=payload)
            return

        effective_trigger_before = trigger_before_min
        accelerated = False
        if due_cnt >= accel_threshold and accel_trigger_before_min > 0:
            effective_trigger_before = min(trigger_before_min, accel_trigger_before_min)
            accelerated = effective_trigger_before != trigger_before_min
        payload["effective_trigger_before_minutes"] = effective_trigger_before
        payload["accelerated"] = accelerated

        is_urgent = urgent_cnt > 0 or (soon_mins is not None and soon_mins <= urgent_min)
        is_due = due_cnt > 0 or (soon_mins is not None and soon_mins <= effective_trigger_before)

        if not is_due and not is_urgent:
            payload["triggered"] = False
            payload["reason"] = "too_early"
            _set_status(JOB_STATUS, "snapshot_autofill", status="ok", finished_at=utcnow(), error=None)
            await _db_job_run_finish(run_id, "ok", None, meta=payload)
            return

        if minutes_since is not None and minutes_since < cooldown_min and not is_urgent:
            payload["triggered"] = False
            payload["reason"] = "cooldown"
            _set_status(JOB_STATUS, "snapshot_autofill", status="ok", finished_at=utcnow(), error=None)
            await _db_job_run_finish(run_id, "ok", None, meta=payload)
            return

        payload["triggered"] = True
        payload["reason"] = "urgent" if is_urgent else "gaps_detected"
        logger.warning(
            "snapshot_autofill_trigger gaps=%s soonest=%s soon_mins=%s",
            gaps,
            payload.get("soonest_kickoff"),
            soon_mins,
        )
        await _run_job(
            "sync_data",
            sync_data.run,
            triggered_by="autofill",
            meta={
                "trigger": "snapshot_autofill",
                "gaps": gaps,
                "window_hours": window_hours,
                "soonest_kickoff": payload.get("soonest_kickoff"),
                "soonest_minutes": soon_mins,
            },
        )
        _set_status(JOB_STATUS, "snapshot_autofill", status="ok", finished_at=utcnow(), error=None)
        await _db_job_run_finish(run_id, "ok", None, meta=payload)
    except Exception:
        logger.exception("snapshot_autofill_failed")
        tb = traceback.format_exc(limit=50)
        _set_status(JOB_STATUS, "snapshot_autofill", status="failed", finished_at=utcnow(), error="exception")
        await _db_job_run_finish(run_id, "failed", tb[-8000:], meta={})


def _base_job_meta(job_name: str) -> dict:
    return {
        "job": job_name,
        "app_env": settings.app_env,
        "app_mode": settings.app_mode,
        "season": settings.season,
        "league_ids": list(settings.league_ids or []),
        "bookmaker_id": settings.bookmaker_id,
        "backtest": bool(settings.backtest_mode),
        "backtest_day": settings.backtest_current_date,
        "backtest_kind": (settings.backtest_kind or "pseudo").strip().lower(),
    }


async def _db_job_run_start(
    job_name: str,
    triggered_by: str | None,
    meta: Optional[dict] = None,
    *,
    session: AsyncSession | None = None,
) -> int | None:
    try:
        meta_obj = _base_job_meta(job_name)
        if meta:
            meta_obj.update(meta)
        if session is None:
            async with SessionLocal() as session2:
                res = await session2.execute(
                    text(
                        """
                        INSERT INTO job_runs(job_name, status, triggered_by, started_at, meta)
                        VALUES(:job, 'running', :by, now(), CAST(:meta AS jsonb))
                        RETURNING id
                        """
                    ),
                    {"job": job_name, "by": triggered_by, "meta": json.dumps(meta_obj)},
                )
                rid = res.scalar_one()
                await session2.commit()
                return int(rid)
        res = await session.execute(
            text(
                """
                INSERT INTO job_runs(job_name, status, triggered_by, started_at, meta)
                VALUES(:job, 'running', :by, now(), CAST(:meta AS jsonb))
                RETURNING id
                """
            ),
            {"job": job_name, "by": triggered_by, "meta": json.dumps(meta_obj)},
        )
        rid = res.scalar_one()
        await session.commit()
        return int(rid)
    except Exception:
        logger.exception("job_runs_start_failed job=%s", job_name)
        return None


async def _db_job_run_finish(
    run_id: int | None,
    status: str,
    error: str | None = None,
    meta: Optional[dict] = None,
    *,
    session: AsyncSession | None = None,
):
    if run_id is None:
        return
    try:
        meta_json = json.dumps(meta or {})
        if session is None:
            async with SessionLocal() as session2:
                await session2.execute(
                    text(
                        """
                        UPDATE job_runs
                        SET status=:status, finished_at=now(), error=:error,
                            meta = COALESCE(meta, '{}'::jsonb) || CAST(:meta AS jsonb)
                        WHERE id=:id
                        """
                    ),
                    {"id": run_id, "status": status, "error": error, "meta": meta_json},
                )
                await session2.commit()
                return
        await session.execute(
            text(
                """
                UPDATE job_runs
                SET status=:status, finished_at=now(), error=:error,
                    meta = COALESCE(meta, '{}'::jsonb) || CAST(:meta AS jsonb)
                WHERE id=:id
                """
            ),
            {"id": run_id, "status": status, "error": error, "meta": meta_json},
        )
        await session.commit()
    except Exception:
        logger.exception("job_runs_finish_failed id=%s status=%s", run_id, status)


async def _run_job(job_name: str, job_fn, triggered_by: str | None = None, meta: Optional[dict] = None):
    lock = _get_lock(job_name)
    if lock.locked():
        logger.warning("job_skip_already_running job=%s", job_name)
        return
    async with lock:
        key = _advisory_key("global")
        async with engine.connect() as lock_conn:
            if not await _try_advisory_lock(lock_conn, key):
                logger.warning("job_skip_global_lock job=%s", job_name)
                return
            try:
                async with SessionLocal() as session:
                    run_id = await _db_job_run_start(job_name, triggered_by, meta=meta, session=session)
                    _set_status(
                        JOB_STATUS,
                        job_name,
                        status="running",
                        started_at=utcnow(),
                        finished_at=None,
                        error=None,
                    )
                    t0 = time.perf_counter()
                    try:
                        if job_name == "sync_data" and triggered_by == "scheduler":
                            result = await job_fn(session, force_refresh=True)
                        else:
                            result = await job_fn(session)
                        if job_name == "quality_report" and isinstance(result, dict):
                            ttl = int(getattr(settings, "quality_report_cache_ttl_seconds", 0) or 0)
                            if ttl > 0:
                                await quality_report.save_cached(session, result, ttl)
                                await session.commit()
                        dur_ms = int((time.perf_counter() - t0) * 1000)
                        _set_status(JOB_STATUS, job_name, status="ok", finished_at=utcnow(), error=None)
                        await _db_job_run_finish(
                            run_id,
                            "ok",
                            None,
                            meta={"duration_ms": dur_ms, "result": result} if isinstance(result, dict) else {"duration_ms": dur_ms},
                            session=session,
                        )
                    except Exception:
                        logger.exception("job_failed job=%s", job_name)
                        tb = traceback.format_exc(limit=50)
                        dur_ms = int((time.perf_counter() - t0) * 1000)
                        _set_status(JOB_STATUS, job_name, status="failed", finished_at=utcnow(), error="exception")
                        try:
                            await session.rollback()
                        except Exception:
                            pass
                        failure_meta: dict[str, object] = {"duration_ms": dur_ms}
                        if job_name == "sync_data":
                            try:
                                from app.data.providers.api_football import get_api_metrics

                                api_metrics = get_api_metrics()
                                if api_metrics:
                                    failure_meta["result"] = {"api_football": api_metrics}
                            except Exception:
                                pass
                        await _db_job_run_finish(run_id, "failed", tb[-8000:], meta=failure_meta, session=session)
            finally:
                await _advisory_unlock(lock_conn, key)


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/api/v1/meta")
async def api_meta(_: None = Depends(_require_admin)):
    global _UI_SHA256, _UI_MTIME_ISO, _UI_CSS_SHA256, _UI_CSS_MTIME_ISO, _UI_JS_SHA256, _UI_JS_MTIME_ISO
    ui_path = BASE_DIR / "ui" / "index.html"
    ui_css_path = BASE_DIR / "ui" / "ui.css"
    ui_js_path = BASE_DIR / "ui" / "ui.js"
    if _UI_SHA256 is None:
        _UI_SHA256 = _file_sha256_hex(ui_path)
    if _UI_MTIME_ISO is None:
        _UI_MTIME_ISO = _file_mtime_iso(ui_path)
    if _UI_CSS_SHA256 is None:
        _UI_CSS_SHA256 = _file_sha256_hex(ui_css_path)
    if _UI_CSS_MTIME_ISO is None:
        _UI_CSS_MTIME_ISO = _file_mtime_iso(ui_css_path)
    if _UI_JS_SHA256 is None:
        _UI_JS_SHA256 = _file_sha256_hex(ui_js_path)
    if _UI_JS_MTIME_ISO is None:
        _UI_JS_MTIME_ISO = _file_mtime_iso(ui_js_path)

    return {
        "ok": True,
        "app_started_at": APP_STARTED_AT.isoformat(),
        "server_time": utcnow().isoformat(),
        "pid": os.getpid(),
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "ui_index": {
            "sha256": _UI_SHA256,
            "mtime": _UI_MTIME_ISO,
        },
        "ui_css": {
            "sha256": _UI_CSS_SHA256,
            "mtime": _UI_CSS_MTIME_ISO,
        },
        "ui_js": {
            "sha256": _UI_JS_SHA256,
            "mtime": _UI_JS_MTIME_ISO,
        },
    }


async def _run_pipeline(triggered_by: str | None = None, meta: Optional[dict] = None):
    """Run full pipeline once: sync_data -> compute_indices -> build_predictions -> evaluate_results."""
    if PIPELINE_LOCK.locked():
        logger.warning("pipeline_skip_already_running")
        return
    async with PIPELINE_LOCK:
        key = _advisory_key("global")
        async with engine.connect() as lock_conn:
            if not await _try_advisory_lock(lock_conn, key):
                logger.warning("pipeline_skip_global_lock")
                return
            try:
                async with SessionLocal() as session:
                    run_id = await _db_job_run_start("full", triggered_by, meta=meta, session=session)
                    _set_status(PIPELINE_STATUS, "full", status="running", started_at=utcnow(), finished_at=None, error=None)
                    try:
                        meta = {}
                        t0 = time.perf_counter()
                        st = time.perf_counter()
                        meta["sync_data"] = {"result": await sync_data.run(session), "duration_ms": int((time.perf_counter() - st) * 1000)}
                        st = time.perf_counter()
                        meta["compute_indices"] = {"result": await compute_indices.run(session), "duration_ms": int((time.perf_counter() - st) * 1000)}
                        st = time.perf_counter()
                        meta["fit_dixon_coles"] = {"result": await fit_dixon_coles.run(session), "duration_ms": int((time.perf_counter() - st) * 1000)}
                        st = time.perf_counter()
                        meta["build_predictions"] = {"result": await build_predictions.run(session), "duration_ms": int((time.perf_counter() - st) * 1000)}
                        st = time.perf_counter()
                        meta["evaluate_results"] = {"result": await evaluate_results.run(session), "duration_ms": int((time.perf_counter() - st) * 1000)}
                        meta["duration_ms"] = int((time.perf_counter() - t0) * 1000)

                        obs = {}
                        try:
                            start = utcnow() - timedelta(days=2)
                            end = utcnow() + timedelta(days=7)
                            gaps_row = (
                                await session.execute(
                                    text(
                                        """
                                        SELECT COUNT(*) AS cnt
                                        FROM fixtures f
                                        WHERE f.status='NS'
                                          AND f.kickoff BETWEEN :start AND :end
                                          AND NOT EXISTS (
                                            SELECT 1
                                            FROM odds_snapshots os
                                            WHERE os.fixture_id=f.id
                                              AND os.bookmaker_id=:bid
                                              AND os.fetched_at < f.kickoff
                                          )
                                        """
                                    ),
                                    {"start": start, "end": end, "bid": settings.bookmaker_id},
                                )
                            ).first()
                            obs["snapshot_gaps_next_window"] = int(gaps_row.cnt or 0) if gaps_row else 0

                            xg_row = (
                                await session.execute(
                                    text(
                                        """
                                        SELECT
                                          COUNT(*) FILTER (WHERE (stats_downloaded IS NOT TRUE AND stats_gave_up IS NOT TRUE)) AS pending,
                                          COUNT(*) FILTER (WHERE (stats_gave_up IS TRUE AND stats_downloaded IS NOT TRUE)) AS gave_up
                                        FROM fixtures
                                        WHERE status IN ('FT','AET','PEN')
                                        """
                                    )
                                )
                            ).first()
                            if xg_row:
                                obs["xg_pending"] = int(xg_row.pending or 0)
                                obs["xg_gave_up"] = int(xg_row.gave_up or 0)
                        except Exception:
                            logger.exception("pipeline_observability_query_failed")
                        meta["observability"] = obs
                        _set_status(PIPELINE_STATUS, "full", status="ok", finished_at=utcnow(), error=None)
                        await _db_job_run_finish(run_id, "ok", None, meta={"stages": meta}, session=session)
                    except Exception:
                        logger.exception("pipeline_failed")
                        _set_status(PIPELINE_STATUS, "full", status="failed", finished_at=utcnow(), error="exception")
                        tb = traceback.format_exc(limit=50)
                        try:
                            await session.rollback()
                        except Exception:
                            pass
                        await _db_job_run_finish(run_id, "failed", tb[-8000:], meta={}, session=session)
            finally:
                await _advisory_unlock(lock_conn, key)


async def _run_single(job_name: str, triggered_by: str | None = None, meta: Optional[dict] = None):
    jobs = {
        "sync_data": sync_data.run,
        "compute_indices": compute_indices.run,
        "build_predictions": build_predictions.run,
        "evaluate_results": evaluate_results.run,
        "maintenance": maintenance.run,
        "rebuild_elo": rebuild_elo.run,
        "quality_report": quality_report.run,
        "fit_dixon_coles": fit_dixon_coles.run,
    }
    job_fn = jobs.get(job_name)
    if not job_fn:
        raise ValueError(f"Unknown job {job_name}")
    await _run_job(job_name, job_fn, triggered_by=triggered_by, meta=meta)


@app.post("/api/v1/run-now")
async def api_run_now(
    request: Request,
    job: str = Query(
        "full",
        description="full | sync_data | compute_indices | build_predictions | evaluate_results | maintenance | rebuild_elo | quality_report",
    ),
    _: None = Depends(_require_admin),
    x_admin_actor: str | None = Header(default=None, alias="X-Admin-Actor"),
):
    """Trigger pipeline or a single job on demand."""
    token_key = (settings.admin_token or "").strip()
    now_ts = time.time()
    # rate limit per token (simple in-memory sliding window)
    last = RUN_NOW_LAST.get(token_key)
    min_interval = int(getattr(settings, "run_now_min_interval_seconds", 3) or 3)
    if last is not None and min_interval > 0 and (now_ts - last) < float(min_interval):
        raise HTTPException(status_code=429, detail="Too Many Requests (min interval)")
    RUN_NOW_LAST[token_key] = now_ts

    window = RUN_NOW_RATE.get(token_key) or []
    window = [t for t in window if (now_ts - t) <= 60.0]
    window.append(now_ts)
    RUN_NOW_RATE[token_key] = window
    max_per_min = int(getattr(settings, "run_now_max_per_minute", 20) or 20)
    if max_per_min > 0 and len(window) > max_per_min:
        raise HTTPException(status_code=429, detail="Too Many Requests (per minute)")

    actor = (x_admin_actor or "").strip() or "unknown"
    client_ip = None
    try:
        client_ip = request.client.host if request and request.client else None
    except Exception:
        client_ip = None
    audit_meta = {"actor": actor, "client_ip": client_ip}
    skipped = False
    if job == "full":
        if PIPELINE_LOCK.locked():
            skipped = True
        else:
            _create_task(_run_pipeline(triggered_by=f"manual:{actor}", meta=audit_meta), label="run-now:full")
        started = "full"
    else:
        if _get_lock(job).locked():
            skipped = True
        else:
            _create_task(_run_single(job, triggered_by=f"manual:{actor}", meta=audit_meta), label=f"run-now:{job}")
        started = job
    logger.info("Triggered run-now for %s", started)
    return {"ok": True, "started": started, "skipped": skipped}


@app.get("/", include_in_schema=False)
async def public_root():
    path = BASE_DIR / "public_site" / "index.html"
    return FileResponse(path, headers={"Cache-Control": "no-store"})


@app.get("/api/v1/picks")
async def api_picks(
    league_id: Optional[int] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    min_signal_score: float = 0.0,
    sort: str = Query("kickoff_desc", description="kickoff_desc | ev_desc | signal_desc | profit_desc"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: None = Depends(_require_admin),
    *,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    now_utc = utcnow()
    try:
        await _refresh_recent_fixture_statuses(session)
    except Exception:
        logger.exception("api_picks_recent_refresh_failed")
    live_lookback_hours = 8
    if date_from is None:
        date_from = now_utc - timedelta(hours=live_lookback_hours)
    if date_to is None:
        date_to = now_utc + timedelta(days=7)
    stale_ns_hours = int(getattr(settings, "stale_ns_hide_hours", 6) or 0)

    sort = (sort or "kickoff_desc").lower()
    if sort not in {"kickoff_desc", "ev_desc", "signal_desc", "profit_desc"}:
        raise HTTPException(status_code=400, detail="sort must be one of: kickoff_desc, ev_desc, signal_desc, profit_desc")

    order_by = "f.kickoff DESC"
    if sort == "ev_desc":
        order_by = "(p.confidence * p.initial_odd - 1) DESC NULLS LAST, f.kickoff DESC"
    elif sort == "signal_desc":
        order_by = "p.signal_score DESC NULLS LAST, f.kickoff DESC"
    elif sort == "profit_desc":
        order_by = "p.profit DESC NULLS LAST, f.kickoff DESC"

    count_stmt = (
        text(
            """
        SELECT COUNT(*) AS cnt
        FROM predictions p
        JOIN fixtures f ON f.id=p.fixture_id
        WHERE p.selection_code != 'SKIP'
          AND (:league_id IS NULL OR f.league_id=:league_id)
          AND (:date_from IS NULL OR f.kickoff >= :date_from)
          AND (:date_to IS NULL OR f.kickoff <= :date_to)
          AND COALESCE(f.status, 'UNK') NOT IN ('FT', 'AET', 'PEN', 'CANC', 'ABD', 'AWD', 'WO')
          AND (
            CAST(:stale_ns_hours AS int) <= 0
            OR COALESCE(f.status, 'UNK') <> 'NS'
            OR f.kickoff >= (CAST(:now_utc AS timestamptz) - (CAST(:stale_ns_hours AS int) * interval '1 hour'))
          )
          AND (p.signal_score IS NULL OR p.signal_score >= :min_signal)
          AND COALESCE(p.status, 'PENDING') = 'PENDING'
        """
        ).bindparams(
            bindparam("league_id", type_=Integer),
            bindparam("date_from", type_=SADateTime(timezone=True)),
            bindparam("date_to", type_=SADateTime(timezone=True)),
            bindparam("now_utc", type_=SADateTime(timezone=True)),
            bindparam("stale_ns_hours", type_=Integer),
            bindparam("min_signal"),
        )
    )
    cnt_row = (
        await session.execute(
            count_stmt,
            {
                "league_id": league_id,
                "date_from": date_from,
                "date_to": date_to,
                "now_utc": now_utc,
                "stale_ns_hours": stale_ns_hours,
                "min_signal": min_signal_score,
            },
        )
    ).first()
    response.headers["X-Total-Count"] = str(int(cnt_row.cnt or 0) if cnt_row else 0)

    stmt = (
        text(
            f"""
        SELECT p.fixture_id, f.kickoff, th.name as home_name, ta.name as away_name,
               th.logo_url AS home_logo_url, ta.logo_url AS away_logo_url,
               f.league_id, l.name as league, l.logo_url AS league_logo_url,
               f.status AS fixture_status,
               CASE
                 WHEN COALESCE(f.status, 'UNK') IN ('LIVE', '1H', 'HT', '2H', 'ET', 'BT', 'P', 'INT')
                   THEN GREATEST(
                     0,
                     CAST(EXTRACT(EPOCH FROM (CAST(:now_utc AS timestamptz) - f.kickoff)) / 60 AS int)
                   )
                 ELSE NULL
               END AS fixture_minute,
               f.home_goals, f.away_goals,
               p.selection_code, p.initial_odd, p.confidence, p.value_index, p.status AS bet_status, p.profit,
               elh.rating AS elo_home, ela.rating AS elo_away, p.signal_score,
               o.market_avg_home_win, o.market_avg_draw, o.market_avg_away_win,
               p.feature_flags
        FROM predictions p
        JOIN fixtures f ON f.id=p.fixture_id
        JOIN teams th ON th.id=f.home_team_id
        JOIN teams ta ON ta.id=f.away_team_id
        LEFT JOIN leagues l ON l.id=f.league_id
        LEFT JOIN team_elo_ratings elh ON elh.team_id=f.home_team_id
        LEFT JOIN team_elo_ratings ela ON ela.team_id=f.away_team_id
        LEFT JOIN odds o ON o.fixture_id = f.id AND o.bookmaker_id=:bid
        WHERE p.selection_code != 'SKIP'
          AND (:league_id IS NULL OR f.league_id=:league_id)
          AND (:date_from IS NULL OR f.kickoff >= :date_from)
          AND (:date_to IS NULL OR f.kickoff <= :date_to)
          AND COALESCE(f.status, 'UNK') NOT IN ('FT', 'AET', 'PEN', 'CANC', 'ABD', 'AWD', 'WO')
          AND (
            CAST(:stale_ns_hours AS int) <= 0
            OR COALESCE(f.status, 'UNK') <> 'NS'
            OR f.kickoff >= (CAST(:now_utc AS timestamptz) - (CAST(:stale_ns_hours AS int) * interval '1 hour'))
          )
          AND (p.signal_score IS NULL OR p.signal_score >= :min_signal)
          AND COALESCE(p.status, 'PENDING') = 'PENDING'
        ORDER BY {order_by}
        LIMIT :limit OFFSET :offset
            """
        ).bindparams(
            bindparam("league_id", type_=Integer),
            bindparam("date_from", type_=SADateTime(timezone=True)),
            bindparam("date_to", type_=SADateTime(timezone=True)),
            bindparam("now_utc", type_=SADateTime(timezone=True)),
            bindparam("stale_ns_hours", type_=Integer),
            bindparam("min_signal"),
            bindparam("limit", type_=Integer),
            bindparam("offset", type_=Integer),
            bindparam("bid", type_=Integer),
        )
    )
    res = await session.execute(
        stmt,
        {
            "league_id": league_id,
            "date_from": date_from,
            "date_to": date_to,
            "now_utc": now_utc,
            "stale_ns_hours": stale_ns_hours,
            "min_signal": min_signal_score,
            "limit": limit,
            "offset": offset,
            "bid": settings.bookmaker_id,
        },
    )
    out = []
    for row in res.fetchall():
        score = None
        if row.home_goals is not None and row.away_goals is not None:
            score = f"{row.home_goals}-{row.away_goals}"
        ev = None
        if row.confidence is not None and row.initial_odd is not None:
            try:
                ev = float(Decimal(row.confidence) * Decimal(row.initial_odd) - Decimal(1))
            except Exception:
                ev = None
        market_avg = None
        if row.selection_code == "HOME_WIN":
            market_avg = row.market_avg_home_win
        elif row.selection_code == "DRAW":
            market_avg = row.market_avg_draw
        elif row.selection_code == "AWAY_WIN":
            market_avg = row.market_avg_away_win
        market_diff = None
        if market_avg and row.initial_odd:
            try:
                market_diff = (float(row.initial_odd) - float(market_avg)) / float(market_avg)
            except ZeroDivisionError:
                market_diff = None
        out.append(
            {
                "fixture_id": row.fixture_id,
                "kickoff": row.kickoff.isoformat(),
                "teams": f"{row.home_name} vs {row.away_name}",
                "home": row.home_name,
                "away": row.away_name,
                "home_logo_url": row.home_logo_url if getattr(row, "home_logo_url", None) is not None else None,
                "away_logo_url": row.away_logo_url if getattr(row, "away_logo_url", None) is not None else None,
                "score": score,
                "league_id": int(row.league_id) if getattr(row, "league_id", None) is not None else None,
                "league": row.league if getattr(row, "league", None) is not None else None,
                "league_logo_url": row.league_logo_url if getattr(row, "league_logo_url", None) is not None else None,
                "fixture_status": row.fixture_status,
                "fixture_minute": int(row.fixture_minute) if getattr(row, "fixture_minute", None) is not None else None,
                "pick": row.selection_code,
                "odd": float(row.initial_odd) if row.initial_odd is not None else None,
                "confidence": float(row.confidence) if row.confidence is not None else None,
                "value": float(row.value_index) if row.value_index is not None else None,
                "ev": ev,
                "status": row.bet_status,
                "profit": float(row.profit) if row.profit is not None else None,
                "elo_home": float(row.elo_home) if row.elo_home is not None else None,
                "elo_away": float(row.elo_away) if row.elo_away is not None else None,
                "signal_score": float(row.signal_score) if hasattr(row, "signal_score") and row.signal_score is not None else None,
                "market_diff": market_diff,
                "prob_source": (row.feature_flags or {}).get("prob_source") if hasattr(row, "feature_flags") and isinstance(row.feature_flags, dict) else None,
                "xpts_diff": (row.feature_flags or {}).get("xpts_diff") if hasattr(row, "feature_flags") and isinstance(row.feature_flags, dict) else None,
                "goal_variance": (row.feature_flags or {}).get("goal_variance") if hasattr(row, "feature_flags") and isinstance(row.feature_flags, dict) else None,
                "value_threshold": (row.feature_flags or {}).get("effective_threshold") if hasattr(row, "feature_flags") and isinstance(row.feature_flags, dict) else None,
                "feature_flags": row.feature_flags if hasattr(row, "feature_flags") else None,
            }
        )
    return out


@app.get("/api/v1/picks/totals")
async def api_picks_totals(
    league_id: Optional[int] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    market: Optional[str] = Query(None, description="TOTAL|TOTAL_1_5|TOTAL_3_5|BTTS|DOUBLE_CHANCE; omit for all"),
    sort: str = Query("kickoff_desc", description="kickoff_desc | ev_desc | profit_desc"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: None = Depends(_require_admin),
    *,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    now_utc = utcnow()
    try:
        await _refresh_recent_fixture_statuses(session)
    except Exception:
        logger.exception("api_picks_totals_recent_refresh_failed")
    live_lookback_hours = 8
    if date_from is None:
        date_from = now_utc - timedelta(hours=live_lookback_hours)
    if date_to is None:
        date_to = now_utc + timedelta(days=7)
    stale_ns_hours = int(getattr(settings, "stale_ns_hide_hours", 6) or 0)

    sort = (sort or "kickoff_desc").lower()
    if sort not in {"kickoff_desc", "ev_desc", "profit_desc"}:
        raise HTTPException(status_code=400, detail="sort must be one of: kickoff_desc, ev_desc, profit_desc")

    VALID_TOTALS_MARKETS = {'TOTAL', 'TOTAL_1_5', 'TOTAL_3_5', 'BTTS', 'DOUBLE_CHANCE'}
    if market is not None:
        market = market.upper()
        if market not in VALID_TOTALS_MARKETS:
            raise HTTPException(status_code=400, detail=f"market must be one of: {', '.join(sorted(VALID_TOTALS_MARKETS))}")

    order_by = "f.kickoff DESC"
    if sort == "ev_desc":
        order_by = "(pt.confidence * pt.initial_odd - 1) DESC NULLS LAST, f.kickoff DESC"
    elif sort == "profit_desc":
        order_by = "pt.profit DESC NULLS LAST, f.kickoff DESC"

    count_stmt = (
        text(
            """
        SELECT COUNT(*) AS cnt
        FROM predictions_totals pt
        JOIN fixtures f ON f.id=pt.fixture_id
        WHERE (:market IS NULL OR pt.market = :market)
          AND (:league_id IS NULL OR f.league_id=:league_id)
          AND (:date_from IS NULL OR f.kickoff >= :date_from)
          AND (:date_to IS NULL OR f.kickoff <= :date_to)
          AND COALESCE(f.status, 'UNK') NOT IN ('FT', 'AET', 'PEN', 'CANC', 'ABD', 'AWD', 'WO')
          AND (
            CAST(:stale_ns_hours AS int) <= 0
            OR COALESCE(f.status, 'UNK') <> 'NS'
            OR f.kickoff >= (CAST(:now_utc AS timestamptz) - (CAST(:stale_ns_hours AS int) * interval '1 hour'))
          )
          AND COALESCE(pt.status, 'PENDING') = 'PENDING'
        """
        ).bindparams(
            bindparam("market", type_=SAString),
            bindparam("league_id", type_=Integer),
            bindparam("date_from", type_=SADateTime(timezone=True)),
            bindparam("date_to", type_=SADateTime(timezone=True)),
            bindparam("now_utc", type_=SADateTime(timezone=True)),
            bindparam("stale_ns_hours", type_=Integer),
        )
    )
    cnt_row = (
        await session.execute(
            count_stmt,
            {
                "market": market,
                "league_id": league_id,
                "date_from": date_from,
                "date_to": date_to,
                "now_utc": now_utc,
                "stale_ns_hours": stale_ns_hours,
            },
        )
    ).first()
    response.headers["X-Total-Count"] = str(int(cnt_row.cnt or 0) if cnt_row else 0)

    stmt = (
        text(
            f"""
        SELECT pt.fixture_id, f.kickoff, th.name as home_name, ta.name as away_name,
               th.logo_url AS home_logo_url, ta.logo_url AS away_logo_url,
               f.league_id, l.name as league, l.logo_url AS league_logo_url,
               f.status AS fixture_status,
               CASE
                 WHEN COALESCE(f.status, 'UNK') IN ('LIVE', '1H', 'HT', '2H', 'ET', 'BT', 'P', 'INT')
                   THEN GREATEST(
                     0,
                     CAST(EXTRACT(EPOCH FROM (CAST(:now_utc AS timestamptz) - f.kickoff)) / 60 AS int)
                   )
                 ELSE NULL
               END AS fixture_minute,
               f.home_goals, f.away_goals,
               pt.market AS market_code,
               pt.selection, pt.initial_odd, pt.confidence, pt.value_index,
               COALESCE(pt.status, 'PENDING') AS status, pt.profit,
               o.market_avg_over_2_5, o.market_avg_under_2_5
        FROM predictions_totals pt
        JOIN fixtures f ON f.id=pt.fixture_id
        JOIN teams th ON th.id=f.home_team_id
        JOIN teams ta ON ta.id=f.away_team_id
        LEFT JOIN leagues l ON l.id=f.league_id
        LEFT JOIN odds o ON o.fixture_id = f.id AND o.bookmaker_id=:bid
        WHERE (:market IS NULL OR pt.market = :market)
          AND (:league_id IS NULL OR f.league_id=:league_id)
          AND (:date_from IS NULL OR f.kickoff >= :date_from)
          AND (:date_to IS NULL OR f.kickoff <= :date_to)
          AND COALESCE(f.status, 'UNK') NOT IN ('FT', 'AET', 'PEN', 'CANC', 'ABD', 'AWD', 'WO')
          AND (
            CAST(:stale_ns_hours AS int) <= 0
            OR COALESCE(f.status, 'UNK') <> 'NS'
            OR f.kickoff >= (CAST(:now_utc AS timestamptz) - (CAST(:stale_ns_hours AS int) * interval '1 hour'))
          )
          AND COALESCE(pt.status, 'PENDING') = 'PENDING'
        ORDER BY {order_by}
        LIMIT :limit OFFSET :offset
            """
        ).bindparams(
            bindparam("market", type_=SAString),
            bindparam("league_id", type_=Integer),
            bindparam("date_from", type_=SADateTime(timezone=True)),
            bindparam("date_to", type_=SADateTime(timezone=True)),
            bindparam("now_utc", type_=SADateTime(timezone=True)),
            bindparam("stale_ns_hours", type_=Integer),
            bindparam("limit", type_=Integer),
            bindparam("offset", type_=Integer),
            bindparam("bid", type_=Integer),
        )
    )
    res = await session.execute(
        stmt,
        {
            "market": market,
            "league_id": league_id,
            "date_from": date_from,
            "date_to": date_to,
            "now_utc": now_utc,
            "stale_ns_hours": stale_ns_hours,
            "limit": limit,
            "offset": offset,
            "bid": settings.bookmaker_id,
        },
    )
    out = []
    for row in res.fetchall():
        score = None
        if row.home_goals is not None and row.away_goals is not None:
            score = f"{row.home_goals}-{row.away_goals}"
        ev = None
        if row.confidence is not None and row.initial_odd is not None:
            try:
                ev = float(Decimal(row.confidence) * Decimal(row.initial_odd) - Decimal(1))
            except Exception:
                ev = None
        market_avg = None
        if row.selection == "OVER_2_5":
            market_avg = row.market_avg_over_2_5
        elif row.selection == "UNDER_2_5":
            market_avg = row.market_avg_under_2_5
        market_diff = None
        if market_avg and row.initial_odd:
            try:
                market_diff = (float(row.initial_odd) - float(market_avg)) / float(market_avg)
            except ZeroDivisionError:
                market_diff = None
        out.append(
            {
                "fixture_id": row.fixture_id,
                "kickoff": row.kickoff.isoformat(),
                "teams": f"{row.home_name} vs {row.away_name}",
                "home": row.home_name,
                "away": row.away_name,
                "home_logo_url": row.home_logo_url if getattr(row, "home_logo_url", None) is not None else None,
                "away_logo_url": row.away_logo_url if getattr(row, "away_logo_url", None) is not None else None,
                "score": score,
                "league_id": int(row.league_id) if getattr(row, "league_id", None) is not None else None,
                "league": row.league if getattr(row, "league", None) is not None else None,
                "league_logo_url": row.league_logo_url if getattr(row, "league_logo_url", None) is not None else None,
                "fixture_status": row.fixture_status,
                "fixture_minute": int(row.fixture_minute) if getattr(row, "fixture_minute", None) is not None else None,
                "market": row.market_code,
                "pick": row.selection,
                "odd": float(row.initial_odd) if row.initial_odd is not None else None,
                "confidence": float(row.confidence) if row.confidence is not None else None,
                "value": float(row.value_index) if row.value_index is not None else None,
                "ev": ev,
                "market_diff": market_diff,
                "status": row.status,
                "profit": float(row.profit) if row.profit is not None else None,
            }
        )
    return out


@app.get("/api/v1/picks/info")
async def api_picks_info(
    league_id: Optional[int] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    only_upcoming: bool = Query(True, description="If true, include only fixtures with status NS"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: None = Depends(_require_admin),
    *,
    session: AsyncSession = Depends(get_session),
):
    if date_from is None:
        date_from = utcnow()
    if date_to is None:
        date_to = utcnow() + timedelta(days=7)
    return await info_report.fetch_info_picks(
        session,
        date_from=date_from,
        date_to=date_to,
        league_id=league_id,
        limit=limit,
        offset=offset,
        only_upcoming=only_upcoming,
    )


@app.get("/api/v1/info/fixtures")
async def api_info_fixtures(
    league_id: Optional[int] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    only_upcoming: bool = Query(False, description="If true, include only fixtures with status NS"),
    limit: int = Query(80, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: None = Depends(_require_admin),
    *,
    session: AsyncSession = Depends(get_session),
):
    if date_to is None:
        date_to = utcnow() + timedelta(days=7)
    if date_from is None:
        date_from = date_to - timedelta(days=14)
    return await info_report.fetch_info_fixtures(
        session,
        date_from=date_from,
        date_to=date_to,
        league_id=league_id,
        limit=limit,
        offset=offset,
        only_upcoming=only_upcoming,
    )


@app.get("/api/v1/bets/history")
async def api_bets_history(
    market: str = Query("all", description="all | 1x2 | totals"),
    league_id: Optional[int] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    all_time: bool = Query(False, description="If true, do not apply default date_from/date_to window"),
    settled_only: bool = Query(False, description="If true and status is not set, include only WIN/LOSS"),
    completed_only: bool = Query(False, description="If true and status is not set, include WIN/LOSS/VOID"),
    status: Optional[str] = Query(None, description="optional: WIN | LOSS | PENDING | VOID"),
    team: Optional[str] = Query(None, description="optional: substring match on home/away team name"),
    sort: str = Query("kickoff_desc", description="kickoff_desc | ev_desc | profit_desc | signal_desc"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: None = Depends(_require_admin),
    *,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    try:
        await _refresh_recent_fixture_statuses(session)
    except Exception:
        logger.exception("api_bets_history_recent_refresh_failed")

    if not all_time:
        if date_to is None:
            date_to = utcnow()
        if date_from is None:
            date_from = date_to - timedelta(days=30)

    market = (market or "all").lower()
    VALID_HISTORY_MARKETS = {"all", "1x2", "totals", "total_1_5", "total_3_5", "btts", "double_chance"}
    if market not in VALID_HISTORY_MARKETS:
        raise HTTPException(status_code=400, detail=f"market must be one of: {', '.join(sorted(VALID_HISTORY_MARKETS))}")

    status = status.upper() if status else None
    if status is not None and status not in {"WIN", "LOSS", "PENDING", "VOID"}:
        raise HTTPException(status_code=400, detail="status must be one of: WIN, LOSS, PENDING, VOID")
    if completed_only:
        settled_only = False

    # Keep dashboard/history views fresh: settle finished fixtures immediately,
    # without waiting for the scheduled evaluate_results cron.
    await _auto_settle_finished_bets(session)

    sort = (sort or "kickoff_desc").lower()
    if sort not in {"kickoff_desc", "ev_desc", "profit_desc", "signal_desc"}:
        raise HTTPException(status_code=400, detail="sort must be one of: kickoff_desc, ev_desc, profit_desc, signal_desc")

    team_like = None
    if team:
        t = team.strip().lower()
        if t:
            team_like = f"%{t}%"

    order_by = "kickoff DESC"
    if sort == "ev_desc":
        order_by = "ev DESC NULLS LAST, kickoff DESC"
    elif sort == "profit_desc":
        order_by = "profit DESC NULLS LAST, kickoff DESC"
    elif sort == "signal_desc":
        order_by = "signal_score DESC NULLS LAST, kickoff DESC"

    params_common = {
        "league_id": league_id,
        "date_from": date_from,
        "date_to": date_to,
        "settled_only": settled_only,
        "completed_only": completed_only,
        "status": status,
        "team_like": team_like,
        "limit": limit,
        "offset": offset,
    }
    params_count = {
        "league_id": league_id,
        "date_from": date_from,
        "date_to": date_to,
        "settled_only": settled_only,
        "completed_only": completed_only,
        "status": status,
        "team_like": team_like,
    }

    if market in {"1x2", "totals", "total_1_5", "total_3_5", "btts", "double_chance"}:
        if market == "1x2":
            order_sql = "f.kickoff DESC"
            if sort == "ev_desc":
                order_sql = "((p.confidence * p.initial_odd) - 1) DESC NULLS LAST, f.kickoff DESC"
            elif sort == "profit_desc":
                order_sql = "p.profit DESC NULLS LAST, f.kickoff DESC"
            elif sort == "signal_desc":
                order_sql = "p.signal_score DESC NULLS LAST, f.kickoff DESC"

            count_stmt = (
                text(
                    """
                    SELECT COUNT(*) AS cnt
                    FROM predictions p
                    JOIN fixtures f ON f.id=p.fixture_id
                    JOIN teams th ON th.id=f.home_team_id
                    JOIN teams ta ON ta.id=f.away_team_id
                    WHERE p.selection_code != 'SKIP'
                      AND (:league_id IS NULL OR f.league_id=:league_id)
                      AND (:date_from IS NULL OR f.kickoff >= :date_from)
                      AND (:date_to IS NULL OR f.kickoff < :date_to)
                      AND (:status IS NULL OR p.status = :status)
                      AND (
                        :status IS NOT NULL
                        OR (:settled_only = false AND :completed_only = false)
                        OR (:settled_only = true AND p.status IN ('WIN', 'LOSS'))
                        OR (:completed_only = true AND p.status IN ('WIN', 'LOSS', 'VOID'))
                      )
                      AND (:team_like IS NULL OR lower(th.name) LIKE :team_like OR lower(ta.name) LIKE :team_like)
                    """
                ).bindparams(
                    bindparam("league_id", type_=Integer),
                    bindparam("date_from", type_=SADateTime(timezone=True)),
                    bindparam("date_to", type_=SADateTime(timezone=True)),
                    bindparam("settled_only"),
                    bindparam("completed_only"),
                    bindparam("status", type_=SAString),
                    bindparam("team_like", type_=SAString),
                )
            )
            row_cnt = (await session.execute(count_stmt, params_count)).first()
            response.headers["X-Total-Count"] = str(int(row_cnt.cnt or 0) if row_cnt else 0)

            data_stmt = (
                text(
                    f"""
                    SELECT
                      '1X2'::text AS market,
                      p.fixture_id::int AS fixture_id,
                      f.kickoff AS kickoff,
                      l.name AS league,
                      l.logo_url AS league_logo_url,
                      th.name AS home,
                      th.logo_url AS home_logo_url,
                      ta.name AS away,
                      ta.logo_url AS away_logo_url,
                      f.status AS fixture_status,
                      f.home_goals AS home_goals,
                      f.away_goals AS away_goals,
                      p.selection_code AS pick,
                      p.initial_odd AS odd,
                      p.confidence AS confidence,
                      p.value_index AS value,
                      ((p.confidence * p.initial_odd) - 1) AS ev,
                      p.status AS status,
                      p.profit AS profit,
                      p.signal_score AS signal_score,
                      p.created_at AS created_at,
                      p.settled_at AS settled_at
                    FROM predictions p
                    JOIN fixtures f ON f.id=p.fixture_id
                    JOIN teams th ON th.id=f.home_team_id
                    JOIN teams ta ON ta.id=f.away_team_id
                    LEFT JOIN leagues l ON l.id=f.league_id
                    WHERE p.selection_code != 'SKIP'
                      AND (:league_id IS NULL OR f.league_id=:league_id)
                      AND (:date_from IS NULL OR f.kickoff >= :date_from)
                      AND (:date_to IS NULL OR f.kickoff < :date_to)
                      AND (:status IS NULL OR p.status = :status)
                      AND (
                        :status IS NOT NULL
                        OR (:settled_only = false AND :completed_only = false)
                        OR (:settled_only = true AND p.status IN ('WIN', 'LOSS'))
                        OR (:completed_only = true AND p.status IN ('WIN', 'LOSS', 'VOID'))
                      )
                      AND (:team_like IS NULL OR lower(th.name) LIKE :team_like OR lower(ta.name) LIKE :team_like)
                    ORDER BY {order_sql}
                    LIMIT :limit OFFSET :offset
                    """
                ).bindparams(
                    bindparam("league_id", type_=Integer),
                    bindparam("date_from", type_=SADateTime(timezone=True)),
                    bindparam("date_to", type_=SADateTime(timezone=True)),
                    bindparam("settled_only"),
                    bindparam("completed_only"),
                    bindparam("status", type_=SAString),
                    bindparam("team_like", type_=SAString),
                    bindparam("limit", type_=Integer),
                    bindparam("offset", type_=Integer),
                )
            )
            res = await session.execute(data_stmt, params_common)
        else:
            # predictions_totals does not have signal_score; fall back to kickoff sorting if requested.
            MARKET_DB_MAP = {'totals': 'TOTAL', 'total_1_5': 'TOTAL_1_5', 'total_3_5': 'TOTAL_3_5', 'btts': 'BTTS', 'double_chance': 'DOUBLE_CHANCE'}
            market_name = MARKET_DB_MAP.get(market, 'TOTAL')

            order_sql = "f.kickoff DESC"
            if sort == "ev_desc":
                order_sql = "((pt.confidence * pt.initial_odd) - 1) DESC NULLS LAST, f.kickoff DESC"
            elif sort == "profit_desc":
                order_sql = "pt.profit DESC NULLS LAST, f.kickoff DESC"

            params_count["market_name"] = market_name
            params_common["market_name"] = market_name

            count_stmt = (
                text(
                    """
                    SELECT COUNT(*) AS cnt
                    FROM predictions_totals pt
                    JOIN fixtures f ON f.id=pt.fixture_id
                    JOIN teams th ON th.id=f.home_team_id
                    JOIN teams ta ON ta.id=f.away_team_id
                    WHERE pt.market = :market_name
                      AND (:league_id IS NULL OR f.league_id=:league_id)
                      AND (:date_from IS NULL OR f.kickoff >= :date_from)
                      AND (:date_to IS NULL OR f.kickoff < :date_to)
                      AND (:status IS NULL OR COALESCE(pt.status, 'PENDING') = :status)
                      AND (
                        :status IS NOT NULL
                        OR (:settled_only = false AND :completed_only = false)
                        OR (:settled_only = true AND COALESCE(pt.status, 'PENDING') IN ('WIN', 'LOSS'))
                        OR (:completed_only = true AND COALESCE(pt.status, 'PENDING') IN ('WIN', 'LOSS', 'VOID'))
                      )
                      AND (:team_like IS NULL OR lower(th.name) LIKE :team_like OR lower(ta.name) LIKE :team_like)
                    """
                ).bindparams(
                    bindparam("market_name", type_=SAString),
                    bindparam("league_id", type_=Integer),
                    bindparam("date_from", type_=SADateTime(timezone=True)),
                    bindparam("date_to", type_=SADateTime(timezone=True)),
                    bindparam("settled_only"),
                    bindparam("completed_only"),
                    bindparam("status", type_=SAString),
                    bindparam("team_like", type_=SAString),
                )
            )
            row_cnt = (await session.execute(count_stmt, params_count)).first()
            response.headers["X-Total-Count"] = str(int(row_cnt.cnt or 0) if row_cnt else 0)

            data_stmt = (
                text(
                    f"""
                    SELECT
                      pt.market::text AS market,
                      pt.fixture_id::int AS fixture_id,
                      f.kickoff AS kickoff,
                      l.name AS league,
                      l.logo_url AS league_logo_url,
                      th.name AS home,
                      th.logo_url AS home_logo_url,
                      ta.name AS away,
                      ta.logo_url AS away_logo_url,
                      f.status AS fixture_status,
                      f.home_goals AS home_goals,
                      f.away_goals AS away_goals,
                      pt.selection AS pick,
                      pt.initial_odd AS odd,
                      pt.confidence AS confidence,
                      pt.value_index AS value,
                      ((pt.confidence * pt.initial_odd) - 1) AS ev,
                      COALESCE(pt.status, 'PENDING') AS status,
                      pt.profit AS profit,
                      NULL::numeric AS signal_score,
                      pt.created_at AS created_at,
                      pt.settled_at AS settled_at
                    FROM predictions_totals pt
                    JOIN fixtures f ON f.id=pt.fixture_id
                    JOIN teams th ON th.id=f.home_team_id
                    JOIN teams ta ON ta.id=f.away_team_id
                    LEFT JOIN leagues l ON l.id=f.league_id
                    WHERE pt.market = :market_name
                      AND (:league_id IS NULL OR f.league_id=:league_id)
                      AND (:date_from IS NULL OR f.kickoff >= :date_from)
                      AND (:date_to IS NULL OR f.kickoff < :date_to)
                      AND (:status IS NULL OR COALESCE(pt.status, 'PENDING') = :status)
                      AND (
                        :status IS NOT NULL
                        OR (:settled_only = false AND :completed_only = false)
                        OR (:settled_only = true AND COALESCE(pt.status, 'PENDING') IN ('WIN', 'LOSS'))
                        OR (:completed_only = true AND COALESCE(pt.status, 'PENDING') IN ('WIN', 'LOSS', 'VOID'))
                      )
                      AND (:team_like IS NULL OR lower(th.name) LIKE :team_like OR lower(ta.name) LIKE :team_like)
                    ORDER BY {order_sql}
                    LIMIT :limit OFFSET :offset
                    """
                ).bindparams(
                    bindparam("market_name", type_=SAString),
                    bindparam("league_id", type_=Integer),
                    bindparam("date_from", type_=SADateTime(timezone=True)),
                    bindparam("date_to", type_=SADateTime(timezone=True)),
                    bindparam("settled_only"),
                    bindparam("completed_only"),
                    bindparam("status", type_=SAString),
                    bindparam("team_like", type_=SAString),
                    bindparam("limit", type_=Integer),
                    bindparam("offset", type_=Integer),
                )
            )
            res = await session.execute(data_stmt, params_common)
    else:
        # Market=all still uses UNION for correct cross-market pagination/sort.
        base_cte = """
        WITH hist AS (
          SELECT
            '1X2'::text AS market,
            f.league_id AS league_id,
            p.fixture_id::int AS fixture_id,
            f.kickoff AS kickoff,
            l.name AS league,
            l.logo_url AS league_logo_url,
            th.name AS home,
            th.logo_url AS home_logo_url,
            ta.name AS away,
            ta.logo_url AS away_logo_url,
            f.status AS fixture_status,
            f.home_goals AS home_goals,
            f.away_goals AS away_goals,
            p.selection_code AS pick,
            p.initial_odd AS odd,
            p.confidence AS confidence,
            p.value_index AS value,
            ((p.confidence * p.initial_odd) - 1) AS ev,
            p.status AS status,
            p.profit AS profit,
            p.signal_score AS signal_score,
            p.created_at AS created_at,
            p.settled_at AS settled_at
          FROM predictions p
          JOIN fixtures f ON f.id=p.fixture_id
          JOIN teams th ON th.id=f.home_team_id
          JOIN teams ta ON ta.id=f.away_team_id
          LEFT JOIN leagues l ON l.id=f.league_id
          WHERE p.selection_code != 'SKIP'
          UNION ALL
          SELECT
            pt.market::text AS market,
            f.league_id AS league_id,
            pt.fixture_id::int AS fixture_id,
            f.kickoff AS kickoff,
            l.name AS league,
            l.logo_url AS league_logo_url,
            th.name AS home,
            th.logo_url AS home_logo_url,
            ta.name AS away,
            ta.logo_url AS away_logo_url,
            f.status AS fixture_status,
            f.home_goals AS home_goals,
            f.away_goals AS away_goals,
            pt.selection AS pick,
            pt.initial_odd AS odd,
            pt.confidence AS confidence,
            pt.value_index AS value,
            ((pt.confidence * pt.initial_odd) - 1) AS ev,
            COALESCE(pt.status, 'PENDING') AS status,
            pt.profit AS profit,
            NULL::numeric AS signal_score,
            pt.created_at AS created_at,
            pt.settled_at AS settled_at
          FROM predictions_totals pt
          JOIN fixtures f ON f.id=pt.fixture_id
          JOIN teams th ON th.id=f.home_team_id
          JOIN teams ta ON ta.id=f.away_team_id
          LEFT JOIN leagues l ON l.id=f.league_id
        )
        """

        where_sql = """
        WHERE (:league_id IS NULL OR league_id = :league_id)
          AND (:date_from IS NULL OR kickoff >= :date_from)
          AND (:date_to IS NULL OR kickoff < :date_to)
          AND (
            :status IS NOT NULL
            OR (:settled_only = false AND :completed_only = false)
            OR (:settled_only = true AND status IN ('WIN', 'LOSS'))
            OR (:completed_only = true AND status IN ('WIN', 'LOSS', 'VOID'))
          )
          AND (:status IS NULL OR status = :status)
          AND (:team_like IS NULL OR lower(home) LIKE :team_like OR lower(away) LIKE :team_like)
        """

        count_stmt = (
            text(
                base_cte
                + """
                SELECT COUNT(*) AS cnt
                FROM hist
                """
                + where_sql
            ).bindparams(
                bindparam("league_id", type_=Integer),
                bindparam("date_from", type_=SADateTime(timezone=True)),
                bindparam("date_to", type_=SADateTime(timezone=True)),
                bindparam("settled_only"),
                bindparam("completed_only"),
                bindparam("status", type_=SAString),
                bindparam("team_like", type_=SAString),
            )
        )
        row_cnt = (await session.execute(count_stmt, params_count)).first()
        response.headers["X-Total-Count"] = str(int(row_cnt.cnt or 0) if row_cnt else 0)

        data_stmt = (
            text(
                base_cte
                + """
                SELECT *
                FROM hist
                """
                + where_sql
                + f"""
                ORDER BY {order_by}
                LIMIT :limit OFFSET :offset
                """
            ).bindparams(
                bindparam("league_id", type_=Integer),
                bindparam("date_from", type_=SADateTime(timezone=True)),
                bindparam("date_to", type_=SADateTime(timezone=True)),
                bindparam("settled_only"),
                bindparam("completed_only"),
                bindparam("status", type_=SAString),
                bindparam("team_like", type_=SAString),
                bindparam("limit", type_=Integer),
                bindparam("offset", type_=Integer),
            )
        )
        res = await session.execute(data_stmt, params_common)

    out: list[dict] = []
    for row in res.fetchall():
        score = None
        if row.home_goals is not None and row.away_goals is not None:
            score = f"{row.home_goals}-{row.away_goals}"
        out.append(
            {
                "market": row.market,
                "fixture_id": int(row.fixture_id),
                "kickoff": row.kickoff.isoformat() if row.kickoff is not None else None,
                "league": row.league,
                "league_logo_url": row.league_logo_url if getattr(row, "league_logo_url", None) is not None else None,
                "home": row.home,
                "home_logo_url": row.home_logo_url if getattr(row, "home_logo_url", None) is not None else None,
                "away": row.away,
                "away_logo_url": row.away_logo_url if getattr(row, "away_logo_url", None) is not None else None,
                "score": score,
                "fixture_status": row.fixture_status,
                "pick": row.pick,
                "odd": float(row.odd) if row.odd is not None else None,
                "confidence": float(row.confidence) if row.confidence is not None else None,
                "value": float(row.value) if row.value is not None else None,
                "ev": float(row.ev) if row.ev is not None else None,
                "status": row.status,
                "profit": float(row.profit) if row.profit is not None else None,
                "signal_score": float(row.signal_score) if getattr(row, "signal_score", None) is not None else None,
                "created_at": row.created_at.isoformat() if row.created_at is not None else None,
                "settled_at": row.settled_at.isoformat() if row.settled_at is not None else None,
            }
        )
    return out


@app.get("/api/v1/fixtures/{fixture_id}/details")
async def api_fixture_details(
    fixture_id: int,
    _: None = Depends(_require_admin),
    session: AsyncSession = Depends(get_session),
):
    fixture_row = (
        await session.execute(
            text(
                """
                SELECT
                  f.id,
                  f.league_id,
                  l.name AS league_name,
                  l.logo_url AS league_logo_url,
                  f.season,
                  f.kickoff,
                  f.status,
                  f.home_goals,
                  f.away_goals,
                  f.home_xg,
                  f.away_xg,
                  f.has_odds,
                  f.stats_downloaded,
                  th.id AS home_team_id,
                  th.name AS home_name,
                  th.logo_url AS home_logo_url,
                  ta.id AS away_team_id,
                  ta.name AS away_name,
                  ta.logo_url AS away_logo_url
                FROM fixtures f
                JOIN teams th ON th.id=f.home_team_id
                JOIN teams ta ON ta.id=f.away_team_id
                LEFT JOIN leagues l ON l.id=f.league_id
                WHERE f.id=:fid
                """
            ),
            {"fid": fixture_id},
        )
    ).first()
    if not fixture_row:
        raise HTTPException(status_code=404, detail="fixture not found")

    def _to_float(v):
        if v is None:
            return None
        try:
            return float(v)
        except Exception:
            return None

    odds_row = (
        await session.execute(
            text(
                """
                SELECT
                  bookmaker_id, fetched_at,
                  home_win, draw, away_win,
                  over_2_5, under_2_5,
                  market_avg_home_win, market_avg_draw, market_avg_away_win,
                  market_avg_over_2_5, market_avg_under_2_5
                FROM odds
                WHERE fixture_id=:fid AND bookmaker_id=:bid
                """
            ),
            {"fid": fixture_id, "bid": settings.bookmaker_id},
        )
    ).first()

    odds_pre_row = (
        await session.execute(
            text(
                """
                SELECT
                  bookmaker_id, fetched_at,
                  home_win, draw, away_win,
                  over_2_5, under_2_5,
                  market_avg_home_win, market_avg_draw, market_avg_away_win,
                  market_avg_over_2_5, market_avg_under_2_5
                FROM odds_snapshots
                WHERE fixture_id=:fid AND bookmaker_id=:bid AND fetched_at < :kickoff
                ORDER BY fetched_at DESC
                LIMIT 1
                """
            ),
            {"fid": fixture_id, "bid": settings.bookmaker_id, "kickoff": fixture_row.kickoff},
        )
    ).first()

    pred_row = (
        await session.execute(
            text(
                """
                SELECT
                  selection_code, confidence, initial_odd, value_index,
                  status, profit, signal_score, feature_flags, created_at, settled_at
                FROM predictions
                WHERE fixture_id=:fid
                """
            ),
            {"fid": fixture_id},
        )
    ).first()

    totals_row = (
        await session.execute(
            text(
                """
                SELECT
                  selection, confidence, initial_odd, value_index,
                  COALESCE(status,'PENDING') AS status, profit,
                  created_at, settled_at
                FROM predictions_totals
                WHERE fixture_id=:fid AND market='TOTAL'
                """
            ),
            {"fid": fixture_id},
        )
    ).first()

    indices_row = (
        await session.execute(
            text(
                """
                SELECT *
                FROM match_indices
                WHERE fixture_id=:fid
                """
            ),
            {"fid": fixture_id},
        )
    ).first()

    dec_rows = await session.execute(
        text(
            """
            SELECT market, payload, updated_at
            FROM prediction_decisions
            WHERE fixture_id=:fid
            """
        ),
        {"fid": fixture_id},
    )
    decisions: dict[str, object] = {}
    for r in dec_rows.fetchall():
        decisions[str(r.market)] = r.payload

    inj_rows = await session.execute(
        text(
            """
            SELECT player_name, reason, type, status, created_at
            FROM injuries
            WHERE team_id IN (SELECT unnest(CAST(:tids AS integer[])))
            ORDER BY created_at DESC
            LIMIT 30
            """
        ),
        {"tids": [fixture_row.home_team_id, fixture_row.away_team_id]},
    )

    def _ev(conf, odd):
        if conf is None or odd is None:
            return None
        try:
            return float(Decimal(conf) * Decimal(odd) - Decimal(1))
        except Exception:
            return None

    out = {
        "fixture": {
            "id": int(fixture_row.id),
            "league_id": int(fixture_row.league_id) if fixture_row.league_id is not None else None,
            "league": fixture_row.league_name,
            "league_logo_url": fixture_row.league_logo_url if getattr(fixture_row, "league_logo_url", None) is not None else None,
            "season": int(fixture_row.season) if fixture_row.season is not None else None,
            "kickoff": fixture_row.kickoff.isoformat() if fixture_row.kickoff is not None else None,
            "status": fixture_row.status,
            "home_team_id": int(fixture_row.home_team_id),
            "away_team_id": int(fixture_row.away_team_id),
            "home": fixture_row.home_name,
            "away": fixture_row.away_name,
            "home_logo_url": fixture_row.home_logo_url if getattr(fixture_row, "home_logo_url", None) is not None else None,
            "away_logo_url": fixture_row.away_logo_url if getattr(fixture_row, "away_logo_url", None) is not None else None,
            "home_goals": fixture_row.home_goals,
            "away_goals": fixture_row.away_goals,
            "home_xg": _to_float(fixture_row.home_xg),
            "away_xg": _to_float(fixture_row.away_xg),
            "has_odds": bool(fixture_row.has_odds),
            "stats_downloaded": bool(fixture_row.stats_downloaded),
        },
        "odds": None,
        "odds_pre_kickoff": None,
        "decisions": decisions,
        "prediction_1x2": None,
        "prediction_totals": None,
        "match_indices": dict(indices_row._mapping) if indices_row else None,
        "injuries": [
            {
                "player_name": r.player_name,
                "reason": r.reason,
                "type": r.type,
                "status": r.status,
                "created_at": r.created_at.isoformat() if r.created_at is not None else None,
            }
            for r in inj_rows.fetchall()
        ],
    }

    if odds_row:
        out["odds"] = {
            "bookmaker_id": int(odds_row.bookmaker_id),
            "fetched_at": odds_row.fetched_at.isoformat() if odds_row.fetched_at is not None else None,
            "home_win": _to_float(odds_row.home_win),
            "draw": _to_float(odds_row.draw),
            "away_win": _to_float(odds_row.away_win),
            "over_2_5": _to_float(odds_row.over_2_5),
            "under_2_5": _to_float(odds_row.under_2_5),
            "market_avg_home_win": _to_float(odds_row.market_avg_home_win),
            "market_avg_draw": _to_float(odds_row.market_avg_draw),
            "market_avg_away_win": _to_float(odds_row.market_avg_away_win),
            "market_avg_over_2_5": _to_float(odds_row.market_avg_over_2_5),
            "market_avg_under_2_5": _to_float(odds_row.market_avg_under_2_5),
        }
    if odds_pre_row:
        out["odds_pre_kickoff"] = {
            "bookmaker_id": int(odds_pre_row.bookmaker_id),
            "fetched_at": odds_pre_row.fetched_at.isoformat() if odds_pre_row.fetched_at is not None else None,
            "home_win": _to_float(odds_pre_row.home_win),
            "draw": _to_float(odds_pre_row.draw),
            "away_win": _to_float(odds_pre_row.away_win),
            "over_2_5": _to_float(odds_pre_row.over_2_5),
            "under_2_5": _to_float(odds_pre_row.under_2_5),
            "market_avg_home_win": _to_float(odds_pre_row.market_avg_home_win),
            "market_avg_draw": _to_float(odds_pre_row.market_avg_draw),
            "market_avg_away_win": _to_float(odds_pre_row.market_avg_away_win),
            "market_avg_over_2_5": _to_float(odds_pre_row.market_avg_over_2_5),
            "market_avg_under_2_5": _to_float(odds_pre_row.market_avg_under_2_5),
        }

    if pred_row:
        out["prediction_1x2"] = {
            "pick": pred_row.selection_code,
            "odd": _to_float(pred_row.initial_odd),
            "confidence": _to_float(pred_row.confidence),
            "value": _to_float(pred_row.value_index),
            "ev": _ev(pred_row.confidence, pred_row.initial_odd),
            "status": pred_row.status,
            "profit": _to_float(pred_row.profit),
            "signal_score": _to_float(pred_row.signal_score),
            "feature_flags": pred_row.feature_flags if isinstance(pred_row.feature_flags, dict) else pred_row.feature_flags,
            "created_at": pred_row.created_at.isoformat() if pred_row.created_at is not None else None,
            "settled_at": pred_row.settled_at.isoformat() if pred_row.settled_at is not None else None,
        }

    if totals_row:
        out["prediction_totals"] = {
            "pick": totals_row.selection,
            "odd": _to_float(totals_row.initial_odd),
            "confidence": _to_float(totals_row.confidence),
            "value": _to_float(totals_row.value_index),
            "ev": _ev(totals_row.confidence, totals_row.initial_odd),
            "status": totals_row.status,
            "profit": _to_float(totals_row.profit),
            "created_at": totals_row.created_at.isoformat() if totals_row.created_at is not None else None,
            "settled_at": totals_row.settled_at.isoformat() if totals_row.settled_at is not None else None,
        }

    return out


@app.get("/api/v1/stats")
async def api_stats(
    metrics_limit: int = Query(5000, ge=1, le=50000),
    metrics_offset: int = Query(0, ge=0),
    metrics_unbounded: bool = Query(
        False,
        description="Allow full scan for brier/logloss (use with care)",
    ),
    _: None = Depends(_require_admin),
    session: AsyncSession = Depends(get_session),
):
    res = await session.execute(
        text(
            """
        SELECT
          COUNT(*) FILTER (WHERE selection_code!='SKIP' AND status!='VOID') AS total_bets,
          COUNT(*) FILTER (WHERE selection_code!='SKIP' AND status='WIN') AS wins,
          COUNT(*) FILTER (WHERE selection_code!='SKIP' AND status='LOSS') AS losses,
          COUNT(*) FILTER (WHERE selection_code!='SKIP' AND status='PENDING') AS pending,
          COALESCE(SUM(profit) FILTER (WHERE selection_code!='SKIP' AND status IN ('WIN','LOSS')), 0) AS profit,
          COALESCE(SUM(profit * signal_score) FILTER (WHERE selection_code!='SKIP' AND status IN ('WIN','LOSS') AND signal_score IS NOT NULL), 0) AS weighted_profit,
          COALESCE(SUM(signal_score) FILTER (WHERE selection_code!='SKIP' AND status IN ('WIN','LOSS') AND signal_score IS NOT NULL), 0) AS weight_sum,
          COALESCE(SUM(CASE WHEN status='WIN' THEN signal_score ELSE 0 END) FILTER (WHERE selection_code!='SKIP' AND status IN ('WIN','LOSS') AND signal_score IS NOT NULL), 0) AS weighted_wins,
          COUNT(*) FILTER (WHERE selection_code!='SKIP' AND signal_score >= 0.7) AS strong_signals,
          AVG(signal_score) FILTER (WHERE selection_code!='SKIP' AND signal_score IS NOT NULL) AS avg_signal
        FROM predictions
        """
        )
    )
    row = res.first()
    total_bets = int(row.total_bets or 0)
    wins = int(row.wins or 0)
    losses = int(row.losses or 0)
    pending = int(row.pending or 0)
    settled = wins + losses
    settled_dec = Decimal(settled) if settled else Decimal(0)
    profit = Decimal(row.profit or 0)
    roi = float(profit / settled_dec) if settled else 0.0
    win_rate = float(Decimal(wins) / settled_dec) if settled else 0.0

    weight_sum = Decimal(row.weight_sum or 0)
    weighted_profit = Decimal(row.weighted_profit or 0)
    weighted_wins = Decimal(row.weighted_wins or 0)
    weighted_roi = float(weighted_profit / weight_sum) if weight_sum else 0.0
    weighted_win_rate = float(weighted_wins / weight_sum) if weight_sum else 0.0
    avg_signal = float(row.avg_signal or 0)
    strong_signals = int(row.strong_signals or 0)

    bins = [
        ("0.0-0.5", 0.0, 0.5),
        ("0.5-0.75", 0.5, 0.75),
        ("0.75-1.0", 0.75, 1.01),
    ]
    bin_results = []
    for label, lo, hi in bins:
        res_bin = await session.execute(
            text(
                """
                SELECT COUNT(*) AS total, COALESCE(SUM(profit),0) AS pnl
                FROM predictions
                WHERE selection_code!='SKIP'
                  AND status IN ('WIN','LOSS')
                  AND signal_score IS NOT NULL
                  AND signal_score >= :lo AND signal_score < :hi
                """
            ),
            {"lo": lo, "hi": hi},
        )
        brow = res_bin.first()
        btotal = int(brow.total or 0)
        bpnl = Decimal(brow.pnl or 0)
        broi = float(bpnl / Decimal(btotal)) * 100 if btotal else 0.0
        bin_results.append({"bin": label, "roi": broi, "bets": btotal})

    # Metrics per prob_source: brier/logloss
    # metrics from settled predictions: brier/logloss by prob_source
    metrics_params: dict = {}
    metrics_limit_clause = ""
    if not metrics_unbounded:
        metrics_limit_clause = " ORDER BY created_at DESC NULLS LAST LIMIT :limit OFFSET :offset"
        metrics_params = {"limit": metrics_limit, "offset": metrics_offset}
    res_preds = await session.execute(
        text(
            f"""
            SELECT confidence, status, feature_flags
            FROM predictions
            WHERE selection_code!='SKIP'
              AND status IN ('WIN','LOSS')
              AND confidence IS NOT NULL
            {metrics_limit_clause}
            """
        ),
        metrics_params,
    )
    metrics_map = {}
    total_brier = Decimal(0)
    total_logloss = Decimal(0)
    total_n = 0
    eps_dec = Decimal("1e-15")
    for rowm in res_preds.fetchall():
        prob = Decimal(rowm.confidence)
        prob = max(min(prob, Decimal(1) - eps_dec), eps_dec)
        outcome = 1 if rowm.status == "WIN" else 0
        brier_val = (prob - Decimal(outcome)) ** 2
        logloss_val = -Decimal(outcome) * prob.ln() - Decimal(1 - outcome) * (Decimal(1) - prob).ln()
        flags = rowm.feature_flags if isinstance(rowm.feature_flags, dict) else {}
        src = flags.get("prob_source", "unknown")
        bucket = metrics_map.setdefault(src, {"brier": Decimal(0), "logloss": Decimal(0), "n": 0})
        bucket["brier"] += brier_val
        bucket["logloss"] += logloss_val
        bucket["n"] += 1
        total_brier += brier_val
        total_logloss += logloss_val
        total_n += 1

    metrics = []
    by_prob_source = {}
    for src, vals in metrics_map.items():
        n = vals["n"]
        if not n:
            continue
        brier_avg = (vals["brier"] / Decimal(n)).quantize(Decimal("0.001"))
        logloss_avg = (vals["logloss"] / Decimal(n)).quantize(Decimal("0.001"))
        metrics.append(
            {"prob_source": src, "brier": float(brier_avg), "logloss": float(logloss_avg), "n": n}
        )
        by_prob_source[src] = {"brier": float(brier_avg), "log_loss": float(logloss_avg), "n": n}

    avg_brier = float((total_brier / Decimal(total_n)).quantize(Decimal("0.001"))) if total_n else 0.0
    avg_logloss = float((total_logloss / Decimal(total_n)).quantize(Decimal("0.001"))) if total_n else 0.0
    if total_n:
        import logging
        logging.getLogger("api.stats").info(
            "stats metrics overall brier=%.3f logloss=%.3f n=%s", avg_brier, avg_logloss, total_n
        )
    if by_prob_source:
        import logging
        for src, vals in by_prob_source.items():
            logging.getLogger("api.stats").info(
                "stats metrics prob_source=%s brier=%.3f logloss=%.3f n=%s",
                src,
                vals.get("brier", 0.0),
                vals.get("log_loss", 0.0),
                vals.get("n", 0),
            )

    return {
        "total_bets": total_bets,
        "wins": wins,
        "losses": losses,
        "pending": pending,
        "win_rate": win_rate * 100 if settled else 0.0,
        "roi": roi * 100 if settled else 0.0,
        "total_profit": float(profit),
        "weighted_roi": weighted_roi * 100 if weight_sum else 0.0,
        "weighted_win_rate": weighted_win_rate * 100 if weight_sum else 0.0,
        "avg_signal_score": avg_signal,
        "strong_signals": strong_signals,
        "bins": bin_results,
        "prob_source_metrics": metrics,
        "avg_brier": avg_brier,
        "avg_log_loss": avg_logloss,
        "by_prob_source": by_prob_source,
        "metrics_sample": {
            "unbounded": metrics_unbounded,
            "limit": None if metrics_unbounded else int(metrics_limit),
            "offset": None if metrics_unbounded else int(metrics_offset),
            "rows": int(total_n),
        },
    }


@app.get("/api/v1/quality_report")
async def api_quality_report(
    refresh: bool = Query(False, description="If true, recompute and refresh cache"),
    _: None = Depends(_require_admin),
    session: AsyncSession = Depends(get_session),
):
    ttl = int(getattr(settings, "quality_report_cache_ttl_seconds", 0) or 0)
    if not refresh and ttl > 0:
        cached = await quality_report.get_cached(session)
        if cached is not None:
            return {
                "cached": True,
                "report": cached,
                "cache_ttl_seconds": ttl,
                "cron": settings.job_quality_report_cron,
            }
    report = await quality_report.run(session)
    if ttl > 0:
        await quality_report.save_cached(session, report, ttl)
        await session.commit()
    return {
        "cached": False,
        "report": report,
        "cache_ttl_seconds": ttl,
        "cron": settings.job_quality_report_cron,
    }


class PublishRequest(BaseModel):
    fixture_id: int
    force: bool = False
    dry_run: bool = False
    image_theme: Optional[str] = None


@app.get("/api/v1/publish/preview")
async def api_publish_preview(
    fixture_id: int,
    _: None = Depends(_require_admin),
    session: AsyncSession = Depends(get_session),
):
    try:
        return await publishing.build_preview(session, fixture_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="fixture not found")


@app.get("/api/v1/publish/post_preview")
async def api_publish_post_preview(
    fixture_id: int,
    image_theme: Optional[str] = None,
    lang: Optional[str] = None,
    _: None = Depends(_require_admin),
    session: AsyncSession = Depends(get_session),
):
    try:
        return await publishing.build_post_preview(
            session,
            fixture_id,
            image_theme=image_theme,
            lang=lang,
        )
    except ValueError:
        raise HTTPException(status_code=404, detail="fixture not found")


@app.post("/api/v1/publish")
async def api_publish(
    request: Request,
    req: PublishRequest,
    _: None = Depends(_require_admin),
    session: AsyncSession = Depends(get_session),
    x_admin_actor: str | None = Header(default=None, alias="X-Admin-Actor"),
):
    actor = (x_admin_actor or "").strip() or "unknown"
    client_ip = None
    try:
        client_ip = request.client.host if request and request.client else None
    except Exception:
        pass
    logger.info(
        "publish_action fixture=%s dry_run=%s actor=%s ip=%s",
        req.fixture_id, req.dry_run, actor, client_ip,
    )
    return await publishing.publish_fixture(
        session,
        req.fixture_id,
        force=bool(req.force),
        dry_run=bool(req.dry_run),
        image_theme=req.image_theme,
    )


def _normalize_publish_payload(payload_raw):
    if payload_raw is None:
        return {}
    if isinstance(payload_raw, dict):
        return payload_raw
    if isinstance(payload_raw, str):
        raw = payload_raw.strip()
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


@app.get("/api/v1/publish/history")
async def api_publish_history(
    fixture_id: int,
    limit: int = Query(50, ge=1, le=500),
    _: None = Depends(_require_admin),
    session: AsyncSession = Depends(get_session),
):
    stmt = text(
        """
        SELECT
          id, fixture_id, market, language, channel_id, status,
          experimental, headline_message_id, analysis_message_id,
          payload, error, created_at, published_at
        FROM prediction_publications
        WHERE fixture_id=:fid
        ORDER BY created_at DESC
        LIMIT :limit
        """
    ).bindparams(
        bindparam("fid", type_=Integer),
        bindparam("limit", type_=Integer),
    )
    res = await session.execute(stmt, {"fid": fixture_id, "limit": limit})
    rows = []
    for r in res.fetchall():
        payload = _normalize_publish_payload(getattr(r, "payload", None))
        reason_raw = payload.get("reason")
        reason = str(reason_raw).strip() if reason_raw is not None else None
        if reason == "":
            reason = None
        reasons_raw = payload.get("reasons")
        reasons = []
        if isinstance(reasons_raw, list):
            for item in reasons_raw:
                text_item = str(item).strip()
                if text_item:
                    reasons.append(text_item)
        rows.append(
            {
                "id": int(r.id),
                "fixture_id": int(r.fixture_id),
                "market": r.market,
                "language": r.language,
                "channel_id": int(r.channel_id),
                "status": r.status,
                "experimental": bool(r.experimental),
                "headline_message_id": r.headline_message_id,
                "analysis_message_id": r.analysis_message_id,
                "reason": reason,
                "reasons": reasons,
                "error": r.error,
                "created_at": r.created_at.isoformat() if r.created_at is not None else None,
                "published_at": r.published_at.isoformat() if r.published_at is not None else None,
            }
        )
    return rows


@app.get("/api/v1/publish/history/global")
async def api_publish_history_global(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    status: str | None = Query(None),
    hours: int = Query(48, ge=1, le=720),
    _: None = Depends(_require_admin),
    session: AsyncSession = Depends(get_session),
):
    count_stmt = text(
        """
        SELECT count(*) FROM prediction_publications pp
        WHERE pp.created_at >= now() - make_interval(hours => :hours)
          AND (:status IS NULL OR pp.status = :status)
        """
    ).bindparams(
        bindparam("hours", type_=Integer),
        bindparam("status", type_=SAString),
    )
    count_res = await session.execute(count_stmt, {"hours": hours, "status": status})
    total = count_res.scalar() or 0

    stmt = text(
        """
        SELECT
          pp.id, pp.fixture_id, pp.market, pp.language, pp.channel_id,
          pp.status, pp.error, pp.created_at, pp.published_at,
          f.kickoff,
          th.name AS home, ta.name AS away
        FROM prediction_publications pp
        LEFT JOIN fixtures f ON f.id = pp.fixture_id
        LEFT JOIN teams th ON th.id = f.home_team_id
        LEFT JOIN teams ta ON ta.id = f.away_team_id
        WHERE pp.created_at >= now() - make_interval(hours => :hours)
          AND (:status IS NULL OR pp.status = :status)
        ORDER BY pp.created_at DESC
        LIMIT :limit OFFSET :offset
        """
    ).bindparams(
        bindparam("hours", type_=Integer),
        bindparam("status", type_=SAString),
        bindparam("limit", type_=Integer),
        bindparam("offset", type_=Integer),
    )
    res = await session.execute(stmt, {"hours": hours, "status": status, "limit": limit, "offset": offset})
    rows = [
        {
            "id": int(r.id),
            "fixture_id": int(r.fixture_id),
            "home": r.home or "",
            "away": r.away or "",
            "kickoff": r.kickoff.isoformat() if r.kickoff else None,
            "market": r.market,
            "language": r.language,
            "channel_id": int(r.channel_id) if r.channel_id else None,
            "status": r.status,
            "error": r.error,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "published_at": r.published_at.isoformat() if r.published_at else None,
        }
        for r in res.fetchall()
    ]
    return JSONResponse(content=rows, headers={"X-Total-Count": str(total)})


@app.get("/api/v1/publish/metrics")
async def api_publish_metrics(
    hours: int | None = Query(None, ge=1, le=24 * 14),
    _: None = Depends(_require_admin),
    session: AsyncSession = Depends(get_session),
):
    window_hours = int(hours or settings.publish_metrics_window_hours or 24)
    stmt = text(
        """
        SELECT status, payload, created_at
        FROM prediction_publications
        WHERE created_at >= (now() - make_interval(hours => :hours))
        ORDER BY created_at DESC
        """
    ).bindparams(bindparam("hours", type_=Integer))
    res = await session.execute(stmt, {"hours": window_hours})
    rows = res.fetchall()

    published_statuses = {"ok", "published"}
    send_failed_statuses = {"failed", "send_failed"}

    status_counts: dict[str, int] = {}
    html_attempts = 0
    html_failures = 0
    html_fallback_to_text = 0
    telegram_attempts = 0
    telegram_failures = 0
    render_times: list[int] = []

    for row in rows:
        status = str(getattr(row, "status", "") or "").strip().lower()
        status_counts[status] = status_counts.get(status, 0) + 1
        payload = _normalize_publish_payload(getattr(row, "payload", None))

        if status in published_statuses or status in send_failed_statuses:
            telegram_attempts += 1
            if status in send_failed_statuses:
                telegram_failures += 1

        html_attempted = bool(payload.get("html_attempted"))
        html_render_failed = bool(payload.get("html_render_failed"))
        fallback_reason = str(payload.get("headline_image_fallback") or "").strip()
        render_time_raw = payload.get("render_time_ms")

        if html_attempted:
            html_attempts += 1
            if html_render_failed:
                html_failures += 1
            if fallback_reason:
                html_fallback_to_text += 1
            try:
                rt = int(render_time_raw)
                if rt >= 0:
                    render_times.append(rt)
            except Exception:
                pass

    html_fail_rate = (html_failures / html_attempts) if html_attempts else 0.0
    telegram_fail_rate = (telegram_failures / telegram_attempts) if telegram_attempts else 0.0
    fallback_rate = (html_fallback_to_text / html_attempts) if html_attempts else 0.0
    avg_render_time_ms = (sum(render_times) / len(render_times)) if render_times else 0.0
    p95_render_time_ms = 0.0
    if render_times:
        sorted_times = sorted(render_times)
        p95_idx = max(0, int(len(sorted_times) * 0.95) - 1)
        p95_render_time_ms = float(sorted_times[p95_idx])

    threshold_pct = float(settings.publish_html_fallback_alert_pct or Decimal("15"))
    threshold_ratio = threshold_pct / 100.0
    alert_triggered = fallback_rate > threshold_ratio

    return {
        "window_hours": window_hours,
        "rows_total": int(len(rows)),
        "status_counts": status_counts,
        "render_time_ms": {
            "avg": round(float(avg_render_time_ms), 2),
            "p95": round(float(p95_render_time_ms), 2),
            "samples": int(len(render_times)),
        },
        "html_fail_rate": round(float(html_fail_rate), 4),
        "telegram_fail_rate": round(float(telegram_fail_rate), 4),
        "html_fallback_rate": round(float(fallback_rate), 4),
        "alert": {
            "triggered": bool(alert_triggered),
            "metric": "html_fallback_rate",
            "threshold_pct": threshold_pct,
        },
    }


@app.get("/api/v1/stats/totals")
async def api_stats_totals(_: None = Depends(_require_admin), session: AsyncSession = Depends(get_session)):
    res = await session.execute(
        text(
            """
        SELECT
          COUNT(*) FILTER (WHERE COALESCE(status,'PENDING')!='VOID') AS total_bets,
          COUNT(*) FILTER (WHERE COALESCE(status,'PENDING')='WIN') AS wins,
          COUNT(*) FILTER (WHERE COALESCE(status,'PENDING')='LOSS') AS losses,
          COUNT(*) FILTER (WHERE COALESCE(status,'PENDING')='PENDING') AS pending,
          COALESCE(SUM(profit) FILTER (WHERE COALESCE(status,'PENDING') IN ('WIN','LOSS')), 0) AS profit
        FROM predictions_totals
        WHERE market='TOTAL'
        """
        )
    )
    row = res.first()
    total_bets = int(row.total_bets or 0)
    wins = int(row.wins or 0)
    losses = int(row.losses or 0)
    pending = int(row.pending or 0)
    settled = wins + losses
    profit = Decimal(row.profit or 0)
    roi = float(profit / Decimal(settled)) if settled else 0.0
    win_rate = float(Decimal(wins) / Decimal(settled)) if settled else 0.0
    return {
        "total_bets": total_bets,
        "wins": wins,
        "losses": losses,
        "pending": pending,
        "win_rate": win_rate * 100 if settled else 0.0,
        "roi": roi * 100 if settled else 0.0,
        "total_profit": float(profit),
    }


@app.get("/api/v1/market-stats")
async def api_market_stats(
    days: int = Query(0, ge=0, le=3650, description="0 = all-time"),
    _: None = Depends(_require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Per-market performance metrics for all markets (1X2 + totals)."""
    cutoff = None
    if days > 0:
        cutoff = utcnow() - timedelta(days=days)
    res = await session.execute(
        text(
            """
            WITH combined AS (
              SELECT '1X2'::text AS market, p.status, p.profit, p.settled_at
              FROM predictions p
              WHERE p.selection_code != 'SKIP'
              UNION ALL
              SELECT pt.market::text, COALESCE(pt.status,'PENDING'), pt.profit, pt.settled_at
              FROM predictions_totals pt
            )
            SELECT market,
              COUNT(*) FILTER (WHERE status NOT IN ('VOID','PENDING')) AS total_bets,
              COUNT(*) FILTER (WHERE status='WIN') AS wins,
              COUNT(*) FILTER (WHERE status='LOSS') AS losses,
              COUNT(*) FILTER (WHERE status IN ('VOID','PENDING') OR status IS NULL) AS pending,
              COALESCE(SUM(profit) FILTER (WHERE status IN ('WIN','LOSS')), 0) AS profit
            FROM combined
            WHERE (:cutoff IS NULL OR settled_at >= :cutoff)
            GROUP BY market
            ORDER BY market
            """
        ).bindparams(bindparam("cutoff", type_=SADateTime(timezone=True))),
        {"cutoff": cutoff},
    )
    markets = {}
    for row in res.fetchall():
        mkt = str(row.market or "")
        total_bets = int(row.total_bets or 0)
        wins = int(row.wins or 0)
        losses = int(row.losses or 0)
        pending = int(row.pending or 0)
        settled = wins + losses
        profit = Decimal(row.profit or 0)
        roi = float(profit / Decimal(settled)) if settled else 0.0
        win_rate = float(Decimal(wins) / Decimal(settled)) if settled else 0.0
        markets[mkt] = {
            "total_bets": total_bets,
            "wins": wins,
            "losses": losses,
            "pending": pending,
            "settled": settled,
            "win_rate": round(win_rate * 100, 2) if settled else 0.0,
            "roi": round(roi * 100, 2) if settled else 0.0,
            "total_profit": float(profit),
        }
    return {"data": markets}


@app.get("/api/v1/info/stats")
async def api_info_stats(
    league_id: Optional[int] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    all_time: bool = Query(False, description="If true, do not apply default date window"),
    _: None = Depends(_require_admin),
    session: AsyncSession = Depends(get_session),
):
    if not all_time:
        if date_to is None:
            date_to = utcnow()
        if date_from is None:
            date_from = date_to - timedelta(days=90)
    return await info_report.compute_info_stats(
        session,
        date_from=date_from,
        date_to=date_to,
        league_id=league_id,
    )

def _cutoff_days(days: int) -> datetime:
    return utcnow() - timedelta(days=int(days))


def _rate(wins: int, losses: int, profit: Decimal) -> tuple[float, float, int]:
    settled = int(wins) + int(losses)
    if settled <= 0:
        return 0.0, 0.0, 0
    settled_dec = Decimal(settled)
    roi = float(profit / settled_dec) * 100
    win_rate = float(Decimal(int(wins)) / settled_dec) * 100
    return win_rate, roi, settled


@app.get("/api/v1/stats/combined/window")
async def api_stats_combined_window(
    days: int = Query(30, ge=1, le=3650),
    league_id: Optional[int] = Query(None),
    _: None = Depends(_require_admin),
    session: AsyncSession = Depends(get_session),
):
    cutoff = _cutoff_days(days)
    res = await session.execute(
        text(
            """
            WITH bets AS (
              SELECT f.league_id AS league_id, p.status AS status, p.profit AS profit
              FROM predictions p
              JOIN fixtures f ON f.id=p.fixture_id
              WHERE p.selection_code!='SKIP'
                AND p.status IN ('WIN','LOSS')
                AND p.settled_at IS NOT NULL
                AND p.settled_at >= :cutoff
                AND (:league_id IS NULL OR f.league_id = :league_id)
              UNION ALL
              SELECT f.league_id AS league_id, COALESCE(pt.status,'PENDING') AS status, pt.profit AS profit
              FROM predictions_totals pt
              JOIN fixtures f ON f.id=pt.fixture_id
              WHERE pt.market='TOTAL'
                AND COALESCE(pt.status,'PENDING') IN ('WIN','LOSS')
                AND pt.settled_at IS NOT NULL
                AND pt.settled_at >= :cutoff
                AND (:league_id IS NULL OR f.league_id = :league_id)
            )
            SELECT
              COUNT(*) AS total_bets,
              COUNT(*) FILTER (WHERE status='WIN') AS wins,
              COUNT(*) FILTER (WHERE status='LOSS') AS losses,
              COALESCE(SUM(profit), 0) AS profit
            FROM bets
            """
        ).bindparams(
            bindparam("cutoff", type_=SADateTime(timezone=True)),
            bindparam("league_id", type_=Integer),
        ),
        {"cutoff": cutoff, "league_id": league_id},
    )
    row = res.first()
    wins = int(row.wins or 0) if row else 0
    losses = int(row.losses or 0) if row else 0
    profit = Decimal(row.profit or 0) if row else Decimal(0)
    win_rate, roi, settled = _rate(wins, losses, profit)
    return {
        "market": "combined",
        "days": int(days),
        "cutoff": cutoff.isoformat(),
        "league_id": league_id,
        "total_bets": int(row.total_bets or 0) if row else 0,
        "wins": wins,
        "losses": losses,
        "settled": settled,
        "win_rate": win_rate,
        "roi": roi,
        "total_profit": float(profit),
    }


@app.get("/api/v1/stats/combined/leagues")
async def api_stats_combined_leagues(
    days: int = Query(90, ge=1, le=3650),
    _: None = Depends(_require_admin),
    session: AsyncSession = Depends(get_session),
):
    cutoff = _cutoff_days(days)
    res = await session.execute(
        text(
            """
            WITH bets AS (
              SELECT f.league_id AS league_id, p.status AS status, p.profit AS profit
              FROM predictions p
              JOIN fixtures f ON f.id=p.fixture_id
              WHERE p.selection_code!='SKIP'
                AND p.status IN ('WIN','LOSS')
                AND p.settled_at IS NOT NULL
                AND p.settled_at >= :cutoff
              UNION ALL
              SELECT f.league_id AS league_id, COALESCE(pt.status,'PENDING') AS status, pt.profit AS profit
              FROM predictions_totals pt
              JOIN fixtures f ON f.id=pt.fixture_id
              WHERE pt.market='TOTAL'
                AND COALESCE(pt.status,'PENDING') IN ('WIN','LOSS')
                AND pt.settled_at IS NOT NULL
                AND pt.settled_at >= :cutoff
            )
            SELECT
              b.league_id AS league_id,
              l.name AS league,
              COUNT(*) AS total_bets,
              COUNT(*) FILTER (WHERE b.status='WIN') AS wins,
              COUNT(*) FILTER (WHERE b.status='LOSS') AS losses,
              COALESCE(SUM(b.profit), 0) AS profit
            FROM bets b
            LEFT JOIN leagues l ON l.id=b.league_id
            GROUP BY b.league_id, l.name
            ORDER BY total_bets DESC, profit DESC
            """
        ),
        {"cutoff": cutoff},
    )
    rows = res.fetchall()
    out = []
    for r in rows:
        wins = int(r.wins or 0)
        losses = int(r.losses or 0)
        profit = Decimal(r.profit or 0)
        win_rate, roi, settled = _rate(wins, losses, profit)
        out.append(
            {
                "league_id": int(r.league_id) if r.league_id is not None else None,
                "league": r.league,
                "days": int(days),
                "cutoff": cutoff.isoformat(),
                "total_bets": int(r.total_bets or 0),
                "wins": wins,
                "losses": losses,
                "settled": settled,
                "win_rate": win_rate,
                "roi": roi,
                "total_profit": float(profit),
            }
        )
    return out


@app.get("/api/v1/dashboard")
async def api_dashboard(
    days: int = Query(30, ge=1, le=365),
    _: None = Depends(_require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Enhanced dashboard metrics with trends and KPIs"""
    cutoff = max(utcnow() - timedelta(days=days), STATS_EPOCH)
    prev_cutoff = max(cutoff - timedelta(days=days), STATS_EPOCH)

    # Current period metrics
    current_stats = await session.execute(
        text(
            """
            WITH combined_bets AS (
              SELECT p.status, p.profit, p.created_at, f.league_id, 'prediction' as bet_type
              FROM predictions p
              JOIN fixtures f ON f.id = p.fixture_id
              WHERE p.selection_code != 'SKIP'
                AND p.status IN ('WIN', 'LOSS')
                AND p.settled_at >= :cutoff
              UNION ALL
              SELECT COALESCE(pt.status, 'PENDING') as status, pt.profit, pt.created_at, f.league_id, 'totals' as bet_type
              FROM predictions_totals pt
              JOIN fixtures f ON f.id = pt.fixture_id
              WHERE pt.selection != 'SKIP'
                AND COALESCE(pt.status, 'PENDING') IN ('WIN', 'LOSS')
                AND pt.settled_at >= :cutoff
            )
            SELECT
              COUNT(*) as total_bets,
              COUNT(*) FILTER (WHERE status = 'WIN') as wins,
              COUNT(*) FILTER (WHERE status = 'LOSS') as losses,
              COALESCE(SUM(profit), 0) as total_profit,
              COALESCE(AVG(profit), 0) as avg_profit,
              MAX(profit) as max_win,
              MIN(profit) as max_loss,
              COUNT(DISTINCT league_id) as active_leagues
            FROM combined_bets
            """
        ).bindparams(
            bindparam("cutoff", type_=SADateTime(timezone=True)),
        ),
        {"cutoff": cutoff},
    )
    current = current_stats.first()

    # Previous period for comparison
    prev_stats = await session.execute(
        text(
            """
            WITH combined_bets AS (
              SELECT p.status, p.profit, p.created_at, f.league_id
              FROM predictions p
              JOIN fixtures f ON f.id = p.fixture_id
              WHERE p.selection_code != 'SKIP'
                AND p.status IN ('WIN', 'LOSS')
                AND p.settled_at >= :prev_cutoff
                AND p.settled_at < :cutoff
              UNION ALL
              SELECT COALESCE(pt.status, 'PENDING') as status, pt.profit, pt.created_at, f.league_id
              FROM predictions_totals pt
              JOIN fixtures f ON f.id = pt.fixture_id
              WHERE pt.selection != 'SKIP'
                AND COALESCE(pt.status, 'PENDING') IN ('WIN', 'LOSS')
                AND pt.settled_at >= :prev_cutoff
                AND pt.settled_at < :cutoff
            )
            SELECT
              COUNT(*) as total_bets,
              COUNT(*) FILTER (WHERE status = 'WIN') as wins,
              COALESCE(SUM(profit), 0) as total_profit
            FROM combined_bets
            """
        ).bindparams(
            bindparam("prev_cutoff", type_=SADateTime(timezone=True)),
            bindparam("cutoff", type_=SADateTime(timezone=True)),
        ),
        {"prev_cutoff": prev_cutoff, "cutoff": cutoff},
    )
    prev = prev_stats.first()

    # Calculate KPIs
    def safe_percentage(numerator, denominator):
        return round((numerator / denominator) * 100, 1) if denominator > 0 else 0.0

    def safe_trend(current_val, prev_val):
        if prev_val == 0:
            return 0.0
        return round(((current_val - prev_val) / abs(prev_val)) * 100, 1)

    current_total = int(current.total_bets or 0)
    current_wins = int(current.wins or 0)
    current_profit = float(current.total_profit or 0)

    prev_total = int(prev.total_bets or 0)
    prev_wins = int(prev.wins or 0)
    prev_profit = float(prev.total_profit or 0)

    current_win_rate = safe_percentage(current_wins, current_total)
    prev_win_rate = safe_percentage(prev_wins, prev_total)

    current_roi = safe_percentage(current_profit, current_total)
    prev_roi = safe_percentage(prev_profit, prev_total)

    profit_factor = None
    profit_factor_note = None
    if current.max_loss is None:
        profit_factor_note = "no_losses"
    else:
        denom = current_profit - abs(float(current.max_loss))
        if denom == 0:
            profit_factor_note = "zero_denominator"
        else:
            profit_factor = round(abs(current_profit / denom), 2)

    return {
        "period_days": days,
        "kpis": {
            "total_bets": {
                "value": current_total,
                "label": "Total Bets",
                "trend": safe_trend(current_total, prev_total),
                "format": "integer"
            },
            "win_rate": {
                "value": current_win_rate,
                "label": "Win Rate",
                "trend": round(current_win_rate - prev_win_rate, 1),
                "format": "percentage"
            },
            "roi": {
                "value": current_roi,
                "label": "ROI",
                "trend": round(current_roi - prev_roi, 1),
                "format": "percentage"
            },
            "total_profit": {
                "value": current_profit,
                "label": "Total Profit",
                "trend": safe_trend(current_profit, prev_profit),
                "format": "currency"
            },
            "avg_bet": {
                "value": round(float(current.avg_profit or 0), 2),
                "label": "Avg Bet Profit",
                "trend": 0.0,  # Could calculate if needed
                "format": "currency"
            },
            "active_leagues": {
                "value": int(current.active_leagues or 0),
                "label": "Active Leagues",
                "trend": 0.0,
                "format": "integer"
            }
        },
        "risk_metrics": {
            "max_win": round(float(current.max_win or 0), 2),
            "max_loss": round(float(current.max_loss or 0), 2),
            "profit_factor": profit_factor,
            "profit_factor_note": profit_factor_note,
        }
    }


@app.get("/api/v1/freshness")
async def api_freshness(_: None = Depends(_require_admin), session: AsyncSession = Depends(get_session)):
    now_utc = utcnow()

    def iso(dt: datetime | None) -> str | None:
        if dt is None:
            return None
        try:
            return ensure_aware_utc(dt).isoformat()
        except Exception:
            try:
                return dt.isoformat()
            except Exception:
                return None

    async def max_ts(sql: str, params: dict | None = None) -> datetime | None:
        try:
            row = (await session.execute(text(sql), params or {})).first()
            if not row:
                return None
            return getattr(row, "ts", None)
        except Exception:
            return None

    last_ok = (
        await session.execute(
            text(
                """
                SELECT started_at, finished_at, triggered_by, meta
                FROM job_runs
                WHERE job_name='sync_data' AND status='ok'
                ORDER BY started_at DESC
                LIMIT 1
                """
            )
        )
    ).first()
    last_any = (
        await session.execute(
            text(
                """
                SELECT started_at, finished_at, status, triggered_by, error
                FROM job_runs
                WHERE job_name='sync_data'
                ORDER BY started_at DESC
                LIMIT 1
                """
            )
        )
    ).first()

    fixtures_updated_at = await max_ts("SELECT MAX(updated_at) AS ts FROM fixtures")
    odds_fetched_at = await max_ts(
        "SELECT MAX(fetched_at) AS ts FROM odds WHERE bookmaker_id=:bid",
        {"bid": int(settings.bookmaker_id or 1)},
    )
    standings_updated_at = await max_ts("SELECT MAX(updated_at) AS ts FROM team_standings")
    injuries_created_at = await max_ts("SELECT MAX(created_at) AS ts FROM injuries")
    indices_updated_at = await max_ts("SELECT MAX(updated_at) AS ts FROM match_indices")
    predictions_created_at = await max_ts("SELECT MAX(created_at) AS ts FROM predictions WHERE selection_code != 'SKIP'")
    totals_created_at = await max_ts("SELECT MAX(created_at) AS ts FROM predictions_totals WHERE market='TOTAL'")

    last_ok_api = None
    if last_ok and isinstance(getattr(last_ok, "meta", None), dict):
        try:
            last_ok_api = (((last_ok.meta or {}).get("result") or {}).get("api_football")) or None
        except Exception:
            last_ok_api = None

    return {
        "server_time": iso(now_utc),
        "sync_data": {
            "last_ok": {
                "started_at": iso(last_ok.started_at) if last_ok else None,
                "finished_at": iso(last_ok.finished_at) if last_ok else None,
                "triggered_by": (last_ok.triggered_by if last_ok else None),
                "api_football": last_ok_api,
            },
            "last_any": {
                "started_at": iso(last_any.started_at) if last_any else None,
                "finished_at": iso(last_any.finished_at) if last_any else None,
                "status": (last_any.status if last_any else None),
                "triggered_by": (last_any.triggered_by if last_any else None),
                "error": (last_any.error if last_any else None),
            },
        },
        "max": {
            "fixtures_updated_at": iso(fixtures_updated_at),
            "odds_fetched_at": iso(odds_fetched_at),
            "standings_updated_at": iso(standings_updated_at),
            "injuries_created_at": iso(injuries_created_at),
            "match_indices_updated_at": iso(indices_updated_at),
            "predictions_created_at": iso(predictions_created_at),
            "predictions_totals_created_at": iso(totals_created_at),
        },
        "config": {
            "sync_data_cron": settings.sync_data_cron,
            "season": int(settings.season or 0),
            "league_ids": list(settings.league_ids or []),
            "bookmaker_id": int(settings.bookmaker_id or 0),
            "odds_lookahead_hours": int(getattr(settings, "sync_data_odds_lookahead_hours", 0) or 0),
            "api_football_fixtures_ttl_recent_seconds": int(settings.api_football_fixtures_ttl_recent_seconds or 0),
            "api_football_odds_ttl_seconds": int(settings.api_football_odds_ttl_seconds or 0),
            "api_football_injuries_ttl_seconds": int(getattr(settings, "api_football_injuries_ttl_seconds", 0) or 0),
            "api_football_standings_ttl_seconds": int(getattr(settings, "api_football_standings_ttl_seconds", 0) or 0),
            "api_football_fixture_stats_ttl_seconds": int(getattr(settings, "api_football_fixture_stats_ttl_seconds", 0) or 0),
            "enable_xg": bool(settings.enable_xg),
            "enable_injuries": bool(settings.enable_injuries),
            "enable_standings": bool(settings.enable_standings),
        },
    }


@app.get("/api/v1/model/status")
async def api_model_status(_: None = Depends(_require_admin), session: AsyncSession = Depends(get_session)):
    now_utc = utcnow()

    def iso(dt: datetime | None) -> str | None:
        if dt is None:
            return None
        try:
            return ensure_aware_utc(dt).isoformat()
        except Exception:
            try:
                return dt.isoformat()
            except Exception:
                return None

    def to_float(v) -> float | None:
        if v is None:
            return None
        try:
            return float(v)
        except Exception:
            return None

    league_ids = [int(x) for x in (settings.league_ids or [])]
    season = int(getattr(settings, "season", 0) or 0)
    league_filter = ""
    params: dict = {"lids": league_ids, "season": season}
    if league_ids:
        league_filter = "AND league_id IN (SELECT unnest(CAST(:lids AS integer[])))"

    elo_row = (
        await session.execute(
            text(
                f"""
                SELECT
                  COUNT(*) AS finished_total,
                  COUNT(*) FILTER (WHERE elo_processed IS TRUE) AS processed_total,
                  COUNT(*) FILTER (WHERE COALESCE(elo_processed, FALSE) IS FALSE) AS unprocessed_total,
                  MAX(elo_processed_at) AS last_processed_at,
                  MAX(kickoff) FILTER (WHERE elo_processed IS TRUE) AS max_processed_kickoff,
                  MIN(kickoff) FILTER (WHERE COALESCE(elo_processed, FALSE) IS FALSE) AS min_unprocessed_kickoff
                FROM fixtures
                WHERE status IN ('FT','AET','PEN')
                  AND home_goals IS NOT NULL AND away_goals IS NOT NULL
                  {league_filter}
                """
            ),
            params,
        )
    ).first()

    ratings_row = (await session.execute(text("SELECT COUNT(*) AS cnt FROM team_elo_ratings"))).first()
    teams_row = (
        await session.execute(
            text(
                f"""
                SELECT COUNT(DISTINCT team_id) AS cnt
                FROM (
                  SELECT home_team_id AS team_id
                  FROM fixtures
                  WHERE home_team_id IS NOT NULL
                    {league_filter}
                  UNION
                  SELECT away_team_id AS team_id
                  FROM fixtures
                  WHERE away_team_id IS NOT NULL
                    {league_filter}
                ) t
                """
            ),
            params,
        )
    ).first()

    max_processed_kickoff = getattr(elo_row, "max_processed_kickoff", None) if elo_row else None
    min_unprocessed_kickoff = getattr(elo_row, "min_unprocessed_kickoff", None) if elo_row else None
    rebuild_needed = (
        max_processed_kickoff is not None
        and min_unprocessed_kickoff is not None
        and ensure_aware_utc(min_unprocessed_kickoff) < ensure_aware_utc(max_processed_kickoff)
    )

    league_counts: dict[int, dict] = {}
    if league_ids and season:
        res = await session.execute(
            text(
                """
                SELECT
                  league_id,
                  COUNT(*) FILTER (WHERE status IN ('FT','AET','PEN') AND home_goals IS NOT NULL AND away_goals IS NOT NULL) AS finished_total,
                  COUNT(*) FILTER (WHERE status IN ('FT','AET','PEN') AND home_xg IS NOT NULL AND away_xg IS NOT NULL) AS xg_total
                FROM fixtures
                WHERE season=:season
                  AND league_id IN (SELECT unnest(CAST(:lids AS integer[])))
                GROUP BY league_id
                """
            ),
            params,
        )
        for r in res.fetchall():
            if r.league_id is None:
                continue
            league_counts[int(r.league_id)] = {
                "finished_total": int(r.finished_total or 0),
                "xg_total": int(r.xg_total or 0),
            }

    decision_counts: dict[int, dict] = {}
    if league_ids and season:
        res = await session.execute(
            text(
                """
                SELECT
                  f.league_id,
                  COUNT(DISTINCT pd.fixture_id) FILTER (WHERE pd.market='1X2') AS decisions_1x2,
                  COUNT(DISTINCT pd.fixture_id) FILTER (WHERE pd.market='TOTAL') AS decisions_total
                FROM prediction_decisions pd
                JOIN fixtures f ON f.id=pd.fixture_id
                WHERE f.season=:season
                  AND f.status IN ('FT','AET','PEN')
                  AND f.league_id IN (SELECT unnest(CAST(:lids AS integer[])))
                GROUP BY f.league_id
                """
            ),
            params,
        )
        for r in res.fetchall():
            if r.league_id is None:
                continue
            decision_counts[int(r.league_id)] = {
                "decisions_1x2": int(r.decisions_1x2 or 0),
                "decisions_total": int(r.decisions_total or 0),
            }

    league_rows = []
    if league_ids and season:
        res = await session.execute(
            text(
                """
                SELECT
                  lid.league_id AS league_id,
                  l.name AS league_name,
                  lb.date_key AS date_key,
                  lb.avg_home_xg AS avg_home_xg,
                  lb.avg_away_xg AS avg_away_xg,
                  lb.draw_freq AS draw_freq,
                  lb.avg_goals AS avg_goals,
                  lb.dc_rho AS dc_rho,
                  lb.calib_alpha AS calib_alpha
                FROM (SELECT unnest(CAST(:lids AS integer[])) AS league_id) lid
                LEFT JOIN leagues l ON l.id = lid.league_id
                LEFT JOIN LATERAL (
                  SELECT date_key, avg_home_xg, avg_away_xg, draw_freq, avg_goals, dc_rho, calib_alpha
                  FROM league_baselines
                  WHERE league_id = lid.league_id AND season = :season
                  ORDER BY date_key DESC
                  LIMIT 1
                ) lb ON TRUE
                ORDER BY lid.league_id ASC
                """
            ),
            params,
        )
        league_rows = res.fetchall()

    prob_source = "stacking"

    day_start, reset_at = utc_day_window(now_utc)
    guard = await quota_guard_decision(
        session,
        now=now_utc,
        daily_limit=int(getattr(settings, "api_football_daily_limit", 7500) or 7500),
        guard_margin=int(getattr(settings, "api_football_guard_margin", 100) or 100),
    )
    api_usage = {
        "daily_limit": int(getattr(settings, "api_football_daily_limit", 7500) or 7500),
        "guard_margin": int(getattr(settings, "api_football_guard_margin", 100) or 100),
        "run_budget_cache_misses": int(getattr(settings, "api_football_run_budget_cache_misses", 0) or 0),
        "reset_at": iso(ensure_aware_utc(reset_at)),
        "blocked": bool(guard.get("blocked")),
        "blocked_reason": guard.get("reason"),
        "remaining_cache_misses": guard.get("remaining_cache_misses"),
        "today": await api_football_usage_since(session, start=day_start),
        "last_24h": await api_football_usage_since(session, start=now_utc - timedelta(hours=24)),
        "last_quota_error": guard.get("last_quota_error"),
    }

    last_runs = await session.execute(
        text(
            """
            SELECT job_name, status, started_at, finished_at, meta
            FROM job_runs
            WHERE job_name IN ('sync_data','full')
            ORDER BY started_at DESC
            LIMIT 30
            """
        )
    )
    last_any_run = None
    last_with_calls = None
    for r in last_runs.fetchall():
        meta = r.meta if isinstance(getattr(r, "meta", None), dict) else {}
        if r.job_name == "full":
            result = (((meta.get("stages") or {}).get("sync_data") or {}).get("result")) or {}
        else:
            result = meta.get("result") or {}
        api_metrics = result.get("api_football")
        skipped = bool(result.get("skipped")) if isinstance(result, dict) else False
        skip_reason = result.get("skip_reason") if isinstance(result, dict) else None
        row = {
            "job_name": r.job_name,
            "status": r.status,
            "started_at": iso(r.started_at),
            "finished_at": iso(r.finished_at),
            "skipped": skipped,
            "skip_reason": skip_reason,
            "api_football": api_metrics,
        }
        if last_any_run is None:
            last_any_run = row
        if last_with_calls is None and isinstance(api_metrics, dict):
            try:
                if int(api_metrics.get("cache_misses") or 0) > 0 or int(api_metrics.get("requests") or 0) > 0:
                    last_with_calls = row
            except Exception:
                pass
        if last_any_run is not None and last_with_calls is not None:
            break
    api_usage["last_run"] = last_any_run
    api_usage["last_run_with_calls"] = last_with_calls

    leagues_out: list[dict] = []
    for r in league_rows:
        lid = int(r.league_id)
        counts = league_counts.get(lid) or {}
        decs = decision_counts.get(lid) or {}
        leagues_out.append(
            {
                "league_id": lid,
                "league_name": r.league_name,
                "season": season,
                "date_key": r.date_key.isoformat() if getattr(r, "date_key", None) is not None else None,
                "avg_home_xg": to_float(r.avg_home_xg),
                "avg_away_xg": to_float(r.avg_away_xg),
                "draw_freq": to_float(r.draw_freq),
                "avg_goals": to_float(r.avg_goals),
                "dc_rho": to_float(r.dc_rho),
                "calib_alpha": to_float(r.calib_alpha),
                "finished_total": int(counts.get("finished_total") or 0),
                "xg_total": int(counts.get("xg_total") or 0),
                "decisions_1x2": int(decs.get("decisions_1x2") or 0),
                "decisions_total": int(decs.get("decisions_total") or 0),
            }
        )

    return {
        "generated_at": iso(now_utc),
        "config": {
            "season": season,
            "league_ids": league_ids,
            "prob_source": prob_source,
        },
        "api_football": api_usage,
        "elo": {
            "finished_total": int(getattr(elo_row, "finished_total", 0) or 0) if elo_row else 0,
            "processed_total": int(getattr(elo_row, "processed_total", 0) or 0) if elo_row else 0,
            "unprocessed_total": int(getattr(elo_row, "unprocessed_total", 0) or 0) if elo_row else 0,
            "last_processed_at": iso(getattr(elo_row, "last_processed_at", None)) if elo_row else None,
            "max_processed_kickoff": iso(getattr(elo_row, "max_processed_kickoff", None)) if elo_row else None,
            "min_unprocessed_kickoff": iso(getattr(elo_row, "min_unprocessed_kickoff", None)) if elo_row else None,
            "rebuild_needed": bool(rebuild_needed),
            "teams_with_elo": int(getattr(ratings_row, "cnt", 0) or 0) if ratings_row else 0,
            "teams_in_fixtures": int(getattr(teams_row, "cnt", 0) or 0) if teams_row else 0,
        },
        "leagues": leagues_out,
    }


@app.get("/api/v1/coverage")
async def api_coverage(_: None = Depends(_require_admin), session: AsyncSession = Depends(get_session)):
    start = utcnow() - timedelta(days=2)
    end = utcnow() + timedelta(days=7)
    # Coverage for NS fixtures
    coverage_ns = await session.execute(
        text(
            """
        SELECT
          COUNT(*) AS total,
          COUNT(*) FILTER (WHERE has_odds) AS with_odds,
          COUNT(*) FILTER (WHERE EXISTS (
              SELECT 1 FROM odds o
              WHERE o.fixture_id=f.id
                AND o.bookmaker_id=:bid
                AND o.fetched_at >= now() - interval '3 hour'
          )) AS with_fresh_odds,
          COUNT(*) FILTER (WHERE EXISTS (
              SELECT 1 FROM odds_snapshots os
              WHERE os.fixture_id=f.id
                AND os.bookmaker_id=:bid
          )) AS with_any_snapshot,
          COUNT(*) FILTER (WHERE EXISTS (
              SELECT 1 FROM odds_snapshots os
              WHERE os.fixture_id=f.id
                AND os.bookmaker_id=:bid
                AND os.fetched_at < f.kickoff
          )) AS with_pre_kickoff_snapshot,
          COUNT(*) FILTER (WHERE mi.fixture_id IS NOT NULL) AS with_indices,
          COUNT(*) FILTER (WHERE p.fixture_id IS NOT NULL) AS with_predictions,
          COUNT(*) FILTER (WHERE mi.fixture_id IS NOT NULL AND (NOT has_odds OR NOT EXISTS (
              SELECT 1 FROM odds o WHERE o.fixture_id=f.id AND o.bookmaker_id=:bid
          ))) AS indices_no_odds
        FROM fixtures f
        LEFT JOIN match_indices mi ON mi.fixture_id = f.id
        LEFT JOIN predictions p ON p.fixture_id = f.id
        WHERE f.status='NS'
          AND f.kickoff BETWEEN :start AND :end
        """
        ),
        {"bid": settings.bookmaker_id, "start": start, "end": end},
    )
    cov_ns = coverage_ns.first()

    # Coverage for finished fixtures and xG
    coverage_ft = await session.execute(
        text(
            """
        SELECT
          COUNT(*) AS total_finished,
          COUNT(*) FILTER (WHERE home_xg IS NOT NULL OR away_xg IS NOT NULL) AS with_xg,
          COUNT(*) FILTER (WHERE (stats_downloaded IS NOT TRUE AND stats_gave_up IS NOT TRUE)) AS xg_pending,
          COUNT(*) FILTER (WHERE (stats_gave_up IS TRUE AND stats_downloaded IS NOT TRUE)) AS xg_gave_up
        FROM fixtures
        WHERE status IN ('FT','AET','PEN')
        """
        )
    )
    cov_ft = coverage_ft.first()

    # Finished fixtures readiness for true-backtest (need pre-kickoff odds snapshot).
    readiness_days = 30
    readiness_res = await session.execute(
        text(
            """
            SELECT
              COUNT(*) AS total_finished_recent,
              COUNT(*) FILTER (WHERE EXISTS (
                SELECT 1 FROM odds_snapshots os
                WHERE os.fixture_id=f.id
                  AND os.bookmaker_id=:bid
                  AND os.fetched_at < f.kickoff
              )) AS with_pre_kickoff_snapshot_recent
            FROM fixtures f
            WHERE f.status IN ('FT','AET','PEN')
              AND f.kickoff >= (now() - (CAST(:days AS int) * interval '1 day'))
            """
        ),
        {"bid": settings.bookmaker_id, "days": readiness_days},
    )
    readiness_row = readiness_res.first()

    total_ns = cov_ns.total or 0
    return {
        "ns_total": total_ns,
        "ns_with_odds": int(cov_ns.with_odds or 0),
        "ns_with_fresh_odds": int(cov_ns.with_fresh_odds or 0),
        "ns_with_any_snapshot": int(cov_ns.with_any_snapshot or 0),
        "ns_with_pre_kickoff_snapshot": int(cov_ns.with_pre_kickoff_snapshot or 0),
        "ns_with_indices": int(cov_ns.with_indices or 0),
        "ns_with_predictions": int(cov_ns.with_predictions or 0),
        "ns_indices_no_odds": int(cov_ns.indices_no_odds or 0),
        "ns_odds_pct": (cov_ns.with_odds or 0) / total_ns * 100 if total_ns else 0.0,
        "ns_fresh_odds_pct": (cov_ns.with_fresh_odds or 0) / total_ns * 100 if total_ns else 0.0,
        "ns_any_snapshot_pct": (cov_ns.with_any_snapshot or 0) / total_ns * 100 if total_ns else 0.0,
        "ns_pre_kickoff_snapshot_pct": (cov_ns.with_pre_kickoff_snapshot or 0) / total_ns * 100 if total_ns else 0.0,
        "ns_indices_pct": (cov_ns.with_indices or 0) / total_ns * 100 if total_ns else 0.0,
        "ns_predictions_pct": (cov_ns.with_predictions or 0) / total_ns * 100 if total_ns else 0.0,
        "finished_total": int(cov_ft.total_finished or 0),
        "finished_with_xg": int(cov_ft.with_xg or 0),
        "finished_xg_pending": int(cov_ft.xg_pending or 0),
        "finished_xg_gave_up": int(cov_ft.xg_gave_up or 0),
        "finished_xg_pct": (cov_ft.with_xg or 0) / (cov_ft.total_finished or 1) * 100 if cov_ft.total_finished else 0.0,
        "finished_recent_days": readiness_days,
        "finished_recent_total": int(readiness_row.total_finished_recent or 0) if readiness_row else 0,
        "finished_recent_with_pre_kickoff_snapshot": int(readiness_row.with_pre_kickoff_snapshot_recent or 0) if readiness_row else 0,
        "finished_recent_pre_kickoff_snapshot_pct": (
            (readiness_row.with_pre_kickoff_snapshot_recent or 0) / (readiness_row.total_finished_recent or 1) * 100
            if readiness_row and readiness_row.total_finished_recent
            else 0.0
        ),
    }


@app.get("/api/v1/elo")
async def api_elo(
    team_id: Optional[int] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    _: None = Depends(_require_admin),
    session: AsyncSession = Depends(get_session),
):
    if team_id is not None:
        rating = await get_team_rating(session, team_id)
        return {"team_id": team_id, "rating": float(rating)}

    res = await session.execute(
        text(
            """
            SELECT ter.team_id, ter.rating, t.name
            FROM team_elo_ratings ter
            LEFT JOIN teams t ON t.id = ter.team_id
            ORDER BY ter.rating DESC
            LIMIT :limit
            """
        ),
        {"limit": limit},
    )
    rows = res.fetchall()
    return {
        "count": len(rows),
        "rows": [
            {"team_id": int(r.team_id), "name": r.name, "rating": float(r.rating)}
            for r in rows
        ],
    }


@app.get("/api/v1/standings")
async def api_standings(
    league_id: Optional[int] = None,
    season: Optional[int] = None,
    sort: str = Query("rank_asc", description="rank_asc | points_desc | updated_desc"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: None = Depends(_require_admin),
    *,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    season_val = int(season or settings.season)
    sort = (sort or "rank_asc").lower()
    if sort not in {"rank_asc", "points_desc", "updated_desc"}:
        raise HTTPException(status_code=400, detail="sort must be one of: rank_asc, points_desc, updated_desc")

    order_by = "ts.rank ASC NULLS LAST, ts.points DESC NULLS LAST, t.name ASC"
    if sort == "points_desc":
        order_by = "ts.points DESC NULLS LAST, ts.rank ASC NULLS LAST, t.name ASC"
    elif sort == "updated_desc":
        order_by = "ts.updated_at DESC NULLS LAST, ts.points DESC NULLS LAST, t.name ASC"

    count_stmt = (
        text(
            """
            SELECT COUNT(*) AS cnt
            FROM team_standings ts
            WHERE ts.season=:season
              AND (:league_id IS NULL OR ts.league_id=:league_id)
            """
        ).bindparams(
            bindparam("season", type_=Integer),
            bindparam("league_id", type_=Integer),
        )
    )
    cnt_row = (
        await session.execute(count_stmt, {"season": season_val, "league_id": league_id})
    ).first()
    response.headers["X-Total-Count"] = str(int(cnt_row.cnt or 0) if cnt_row else 0)

    stmt = (
        text(
            f"""
            SELECT
              ts.team_id,
              t.name AS team_name,
              ts.league_id,
              l.name AS league_name,
              ts.season,
              ts.rank,
              ts.points,
              ts.played,
              ts.goals_for,
              ts.goals_against,
              ts.goal_diff,
              ts.form,
              ts.updated_at
            FROM team_standings ts
            JOIN teams t ON t.id=ts.team_id
            LEFT JOIN leagues l ON l.id=ts.league_id
            WHERE ts.season=:season
              AND (:league_id IS NULL OR ts.league_id=:league_id)
            ORDER BY {order_by}
            LIMIT :limit OFFSET :offset
            """
        ).bindparams(
            bindparam("season", type_=Integer),
            bindparam("league_id", type_=Integer),
            bindparam("limit", type_=Integer),
            bindparam("offset", type_=Integer),
        )
    )
    res = await session.execute(
        stmt,
        {
            "season": season_val,
            "league_id": league_id,
            "limit": limit,
            "offset": offset,
        },
    )
    out = []
    for r in res.fetchall():
        out.append(
            {
                "team_id": int(r.team_id),
                "team_name": r.team_name,
                "league_id": int(r.league_id),
                "league_name": r.league_name,
                "season": int(r.season),
                "rank": int(r.rank) if r.rank is not None else None,
                "points": int(r.points) if r.points is not None else None,
                "played": int(r.played) if r.played is not None else None,
                "goals_for": int(r.goals_for) if r.goals_for is not None else None,
                "goals_against": int(r.goals_against) if r.goals_against is not None else None,
                "goal_diff": int(r.goal_diff) if r.goal_diff is not None else None,
                "form": r.form,
                "updated_at": r.updated_at.isoformat() if r.updated_at is not None else None,
            }
        )
    return out


@app.get("/api/v1/snapshots/gaps")
async def api_snapshots_gaps(
    league_id: Optional[int] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    only_future: bool = Query(True, description="If true, only fixtures with kickoff >= now()"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: None = Depends(_require_admin),
    *,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    """
    List upcoming NS fixtures that do NOT have a pre-kickoff odds snapshot yet.
    This is used to monitor true-backtest data coverage.
    """
    if date_from is None:
        date_from = utcnow()
    if date_to is None:
        date_to = utcnow() + timedelta(days=7)

    now_utc = utcnow()

    count_stmt = (
        text(
            """
            SELECT COUNT(*) AS cnt
            FROM fixtures f
            WHERE f.status='NS'
              AND (:league_id IS NULL OR f.league_id=:league_id)
              AND (:date_from IS NULL OR f.kickoff >= :date_from)
              AND (:date_to IS NULL OR f.kickoff < :date_to)
              AND (:only_future = false OR f.kickoff >= :now_utc)
              AND NOT EXISTS (
                SELECT 1
                FROM odds_snapshots os
                WHERE os.fixture_id=f.id
                  AND os.bookmaker_id=:bid
                  AND os.fetched_at < f.kickoff
              )
            """
        ).bindparams(
            bindparam("league_id", type_=Integer),
            bindparam("date_from", type_=SADateTime(timezone=True)),
            bindparam("date_to", type_=SADateTime(timezone=True)),
            bindparam("only_future"),
            bindparam("now_utc", type_=SADateTime(timezone=True)),
            bindparam("bid", type_=Integer),
        )
    )
    cnt_row = (
        await session.execute(
            count_stmt,
            {
                "league_id": league_id,
                "date_from": date_from,
                "date_to": date_to,
                "only_future": bool(only_future),
                "now_utc": now_utc,
                "bid": settings.bookmaker_id,
            },
        )
    ).first()
    response.headers["X-Total-Count"] = str(int(cnt_row.cnt or 0) if cnt_row else 0)

    stmt = (
        text(
            """
            SELECT
              f.id AS fixture_id,
              f.kickoff AS kickoff,
              l.name AS league,
              th.name AS home,
              ta.name AS away,
              f.has_odds AS has_odds,
              o.fetched_at AS odds_fetched_at,
              snap_any.cnt_any AS snapshots_any,
              snap_any.last_any AS last_snapshot_any
            FROM fixtures f
            JOIN teams th ON th.id=f.home_team_id
            JOIN teams ta ON ta.id=f.away_team_id
            LEFT JOIN leagues l ON l.id=f.league_id
            LEFT JOIN odds o ON o.fixture_id=f.id AND o.bookmaker_id=:bid
            LEFT JOIN LATERAL (
              SELECT COUNT(*)::int AS cnt_any, MAX(fetched_at) AS last_any
              FROM odds_snapshots os
              WHERE os.fixture_id=f.id AND os.bookmaker_id=:bid
            ) snap_any ON TRUE
            WHERE f.status='NS'
              AND (:league_id IS NULL OR f.league_id=:league_id)
              AND (:date_from IS NULL OR f.kickoff >= :date_from)
              AND (:date_to IS NULL OR f.kickoff < :date_to)
              AND (:only_future = false OR f.kickoff >= :now_utc)
              AND NOT EXISTS (
                SELECT 1
                FROM odds_snapshots os
                WHERE os.fixture_id=f.id
                  AND os.bookmaker_id=:bid
                  AND os.fetched_at < f.kickoff
              )
            ORDER BY f.kickoff ASC
            LIMIT :limit OFFSET :offset
            """
        ).bindparams(
            bindparam("league_id", type_=Integer),
            bindparam("date_from", type_=SADateTime(timezone=True)),
            bindparam("date_to", type_=SADateTime(timezone=True)),
            bindparam("only_future"),
            bindparam("now_utc", type_=SADateTime(timezone=True)),
            bindparam("limit", type_=Integer),
            bindparam("offset", type_=Integer),
            bindparam("bid", type_=Integer),
        )
    )
    res = await session.execute(
        stmt,
        {
            "league_id": league_id,
            "date_from": date_from,
            "date_to": date_to,
            "only_future": bool(only_future),
            "now_utc": now_utc,
            "limit": limit,
            "offset": offset,
            "bid": settings.bookmaker_id,
        },
    )
    out = []
    for r in res.fetchall():
        mins_to_kickoff = None
        if r.kickoff is not None:
            try:
                mins_to_kickoff = int((r.kickoff - now_utc).total_seconds() // 60)
            except Exception:
                mins_to_kickoff = None
        out.append(
            {
                "fixture_id": int(r.fixture_id),
                "kickoff": r.kickoff.isoformat() if r.kickoff is not None else None,
                "mins_to_kickoff": mins_to_kickoff,
                "league": r.league,
                "home": r.home,
                "away": r.away,
                "has_odds": bool(r.has_odds),
                "odds_fetched_at": r.odds_fetched_at.isoformat() if r.odds_fetched_at is not None else None,
                "snapshots_any": int(r.snapshots_any or 0) if r.snapshots_any is not None else 0,
                "last_snapshot_any": r.last_snapshot_any.isoformat() if r.last_snapshot_any is not None else None,
            }
        )
    return out


@app.get("/health/debug")
async def health_debug(
    _: None = Depends(_require_admin),
    session: AsyncSession = Depends(get_session),
):
    uptime = None
    if settings.scheduler_enabled and hasattr(scheduler, "start_time"):
        uptime = (utcnow() - datetime.fromtimestamp(scheduler.start_time / 1000, tz=timezone.utc)).total_seconds()
    counts = await session.execute(
        text(
            """
        SELECT
          (SELECT COUNT(*) FROM fixtures) AS fixtures,
          (SELECT COUNT(*) FROM odds) AS odds,
          (SELECT COUNT(*) FROM match_indices) AS indices,
          (SELECT COUNT(*) FROM predictions) AS predictions
        """
        )
    )
    row = counts.first()
    env_snapshot = {
        "app_env": settings.app_env,
        "app_mode": settings.app_mode,
        "season": settings.season,
        "league_ids": settings.league_ids,
        "bookmaker_id": settings.bookmaker_id,
        "scheduler_enabled": bool(settings.scheduler_enabled),
        "backtest": bool(settings.backtest_mode),
        "backtest_day": settings.backtest_current_date,
        "backtest_kind": (settings.backtest_kind or "pseudo").strip().lower(),
        "snapshot_autofill_enabled": bool(settings.snapshot_autofill_enabled),
        "snapshot_autofill_interval_minutes": int(settings.snapshot_autofill_interval_minutes or 0),
        "snapshot_autofill_window_hours": int(settings.snapshot_autofill_window_hours or 0),
        "snapshot_autofill_min_interval_minutes": int(settings.snapshot_autofill_min_interval_minutes or 0),
        "snapshot_autofill_urgent_minutes": int(settings.snapshot_autofill_urgent_minutes or 0),
        "snapshot_autofill_trigger_before_minutes": int(settings.snapshot_autofill_trigger_before_minutes or 0),
        "snapshot_autofill_accel_due_gaps_threshold": int(settings.snapshot_autofill_accel_due_gaps_threshold or 0),
        "snapshot_autofill_accel_trigger_before_minutes": int(settings.snapshot_autofill_accel_trigger_before_minutes or 0),
    }
    return {
        "ok": True,
        "uptime_seconds": uptime,
        "counts": {
            "fixtures": row.fixtures,
            "odds": row.odds,
            "indices": row.indices,
            "predictions": row.predictions,
        },
        "env": env_snapshot,
    }


@app.get("/api/v1/db/browse")
async def api_db_browse(
    table: str = Query(
        "fixtures",
        description="fixtures|odds|odds_snapshots|prediction_decisions|match_indices|predictions|prediction_publications|job_runs",
    ),
    fixture_id: Optional[int] = Query(None),
    league_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: None = Depends(_require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Simple read-only browse for key tables."""
    table = table.lower()
    queries = {
        "fixtures": {
            "sql": """
                SELECT id, league_id, season, kickoff, status, home_team_id, away_team_id,
                       home_goals, away_goals, home_xg, away_xg, has_odds, stats_downloaded, updated_at
                FROM fixtures
                WHERE 1=1
                {fixture_filter}
                {league_filter}
                {status_filter}
                ORDER BY kickoff DESC
                LIMIT :limit OFFSET :offset
            """,
            "supports": {"fixture": True, "league": True, "status": True},
            "fixture_col": "id",
        },
        "odds": {
            "sql": """
                SELECT fixture_id, bookmaker_id, home_win, draw, away_win, fetched_at
                FROM odds
                WHERE 1=1
                {fixture_filter}
                ORDER BY fetched_at DESC
                LIMIT :limit OFFSET :offset
            """,
            "supports": {"fixture": True, "league": False, "status": False},
        },
        "odds_snapshots": {
            "sql": """
                SELECT fixture_id, bookmaker_id, home_win, draw, away_win, fetched_at
                FROM odds_snapshots
                WHERE 1=1
                {fixture_filter}
                ORDER BY fetched_at DESC
                LIMIT :limit OFFSET :offset
            """,
            "supports": {"fixture": True, "league": False, "status": False},
        },
        "prediction_decisions": {
            "sql": """
                SELECT fixture_id, market, updated_at, payload
                FROM prediction_decisions
                WHERE 1=1
                {fixture_filter}
                ORDER BY updated_at DESC
                LIMIT :limit OFFSET :offset
            """,
            "supports": {"fixture": True, "league": False, "status": False},
        },
        "match_indices": {
            "sql": """
                SELECT fixture_id, home_form_for, home_form_against, away_form_for, away_form_against,
                       home_class_for, home_class_against, away_class_for, away_class_against,
                       home_venue_for, home_venue_against, away_venue_for, away_venue_against,
                       home_rest_hours, away_rest_hours, created_at
                FROM match_indices
                WHERE 1=1
                {fixture_filter}
                ORDER BY created_at DESC
                LIMIT :limit OFFSET :offset
            """,
            "supports": {"fixture": True, "league": False, "status": False},
        },
        "predictions": {
            "sql": """
                SELECT fixture_id, selection_code, confidence, initial_odd, value_index,
                       status, profit, created_at
                FROM predictions
                WHERE 1=1
                {fixture_filter}
                {status_filter}
                ORDER BY created_at DESC
                LIMIT :limit OFFSET :offset
            """,
            "supports": {"fixture": True, "league": False, "status": True},
        },
        "job_runs": {
            "sql": """
                SELECT id, job_name, status, triggered_by, started_at, finished_at, error
                FROM job_runs
                WHERE 1=1
                {status_filter}
                ORDER BY started_at DESC
                LIMIT :limit OFFSET :offset
            """,
            "supports": {"fixture": False, "league": False, "status": True},
        },
        "prediction_publications": {
            "sql": """
                SELECT id, fixture_id, market, language, channel_id, status,
                       experimental, headline_message_id, analysis_message_id,
                       created_at, published_at
                FROM prediction_publications
                WHERE 1=1
                {fixture_filter}
                {status_filter}
                ORDER BY created_at DESC
                LIMIT :limit OFFSET :offset
            """,
            "supports": {"fixture": True, "league": False, "status": True},
        },
    }
    if table not in queries:
        return {"error": "unsupported table"}

    qcfg = queries[table]
    fixture_col = qcfg.get("fixture_col", "fixture_id")
    fixture_filter = (
        f"AND {fixture_col} = :fixture_id" if fixture_id is not None and qcfg["supports"]["fixture"] else ""
    )
    league_filter = "AND league_id = :league_id" if league_id is not None and qcfg["supports"]["league"] else ""
    status_filter = "AND status = :status" if status is not None and qcfg["supports"]["status"] else ""

    sql = qcfg["sql"].format(
        fixture_filter=fixture_filter,
        league_filter=league_filter,
        status_filter=status_filter,
    )
    res = await session.execute(
        text(sql),
        {
            "fixture_id": fixture_id,
            "league_id": league_id,
            "status": status,
            "limit": limit,
            "offset": offset,
        },
    )
    rows = res.fetchall()
    return {"table": table, "count": len(rows), "rows": [dict(r._mapping) for r in rows]}


@app.get("/ui", include_in_schema=False)
async def ui_root():
    path = BASE_DIR / "ui" / "index.html"
    return FileResponse(path, headers={"Cache-Control": "no-store"})


@app.get("/ui/ui.css", include_in_schema=False)
async def ui_css():
    path = BASE_DIR / "ui" / "ui.css"
    return FileResponse(path, media_type="text/css", headers={"Cache-Control": "no-store"})


@app.get("/ui/ui.js", include_in_schema=False)
async def ui_js():
    path = BASE_DIR / "ui" / "ui.js"
    return FileResponse(path, media_type="application/javascript", headers={"Cache-Control": "no-store"})


# ---------- New static file routes ----------

@app.get("/public.css", include_in_schema=False)
async def public_css():
    path = BASE_DIR / "public_site" / "public.css"
    return FileResponse(path, media_type="text/css", headers={"Cache-Control": "no-store"})


@app.get("/public.js", include_in_schema=False)
async def public_js():
    path = BASE_DIR / "public_site" / "public.js"
    return FileResponse(path, media_type="application/javascript", headers={"Cache-Control": "no-store"})


@app.get("/shared/tokens.css", include_in_schema=False)
async def shared_tokens_css():
    path = BASE_DIR / "shared" / "tokens.css"
    return FileResponse(path, media_type="text/css", headers={"Cache-Control": "no-store"})


@app.get("/admin", include_in_schema=False)
async def admin_root():
    path = BASE_DIR / "admin" / "index.html"
    return FileResponse(path, headers={"Cache-Control": "no-store"})


@app.get("/admin/admin.css", include_in_schema=False)
async def admin_panel_css():
    path = BASE_DIR / "admin" / "admin.css"
    return FileResponse(path, media_type="text/css", headers={"Cache-Control": "no-store"})


@app.get("/admin/admin.js", include_in_schema=False)
async def admin_panel_js():
    path = BASE_DIR / "admin" / "admin.js"
    return FileResponse(path, media_type="application/javascript", headers={"Cache-Control": "no-store"})


# ---------- Public API (no auth) ----------

@app.get("/api/public/v1/leagues")
async def public_leagues(
    _rate: None = Depends(_check_public_rate),
    session: AsyncSession = Depends(get_session),
):
    league_ids = settings.league_ids or []
    if not league_ids:
        return []
    placeholders = ", ".join(str(lid) for lid in league_ids)
    res = await session.execute(
        text(f"""
            SELECT DISTINCT l.id, l.name, l.country, l.logo_url
            FROM leagues l
            WHERE l.id IN ({placeholders})
            ORDER BY l.name
        """)
    )
    rows = res.fetchall()
    if not rows:
        res2 = await session.execute(
            text(f"""
                SELECT DISTINCT f.league_id AS id, l.name, l.country, l.logo_url
                FROM fixtures f
                LEFT JOIN leagues l ON l.id = f.league_id
                WHERE f.league_id IN ({placeholders})
                GROUP BY f.league_id, l.name, l.country, l.logo_url
                ORDER BY l.name NULLS LAST
            """)
        )
        rows = res2.fetchall()
    return [
        {"id": int(r.id), "name": r.name or f"League {r.id}", "country": r.country or "", "logo_url": r.logo_url or ""}
        for r in rows
    ]


@app.get("/api/public/v1/stats")
async def public_stats(
    days: int = Query(90, ge=1, le=365),
    _rate: None = Depends(_check_public_rate),
    session: AsyncSession = Depends(get_session),
):
    cutoff = max(utcnow() - timedelta(days=days), STATS_EPOCH)
    res = await session.execute(
        text("""
            WITH combined AS (
              SELECT p.status, p.profit
              FROM predictions p
              WHERE p.selection_code != 'SKIP' AND p.status IN ('WIN','LOSS')
                AND p.settled_at >= :cutoff
              UNION ALL
              SELECT COALESCE(pt.status,'PENDING'), pt.profit
              FROM predictions_totals pt
              WHERE COALESCE(pt.status,'PENDING') IN ('WIN','LOSS')
                AND pt.settled_at >= :cutoff
            )
            SELECT
              COUNT(*) AS total_bets,
              COUNT(*) FILTER (WHERE status='WIN') AS wins,
              COUNT(*) FILTER (WHERE status='LOSS') AS losses,
              COALESCE(SUM(profit),0) AS total_profit
            FROM combined
        """).bindparams(bindparam("cutoff", type_=SADateTime(timezone=True))),
        {"cutoff": cutoff},
    )
    row = res.first()
    total = int(row.total_bets or 0)
    wins = int(row.wins or 0)
    losses = int(row.losses or 0)
    profit = float(row.total_profit or 0)
    settled = wins + losses
    return {
        "period_days": days,
        "total_bets": total,
        "wins": wins,
        "losses": losses,
        "win_rate": round((wins / settled) * 100, 1) if settled else 0.0,
        "roi": round((profit / settled) * 100, 1) if settled else 0.0,
        "total_profit": round(profit, 2),
    }


@app.get("/api/public/v1/matches")
async def public_matches(
    league_id: Optional[int] = None,
    days_ahead: int = Query(7, ge=1, le=30),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    _rate: None = Depends(_check_public_rate),
    *,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    now_utc = utcnow()
    date_from = now_utc - timedelta(hours=8)
    date_to = now_utc + timedelta(days=days_ahead)
    stale_ns_hours = int(getattr(settings, "stale_ns_hide_hours", 6) or 0)
    bid = settings.bookmaker_id

    cnt_res = await session.execute(
        text("""
            WITH combined AS (
              SELECT p.fixture_id FROM predictions p
              JOIN fixtures f ON f.id = p.fixture_id
              WHERE p.selection_code != 'SKIP'
                AND (:league_id IS NULL OR f.league_id = :league_id)
                AND f.kickoff >= :date_from AND f.kickoff <= :date_to
                AND COALESCE(f.status,'UNK') NOT IN ('FT','AET','PEN','CANC','ABD','AWD','WO')
                AND (CAST(:stale_ns_hours AS int) <= 0
                     OR COALESCE(f.status,'UNK') <> 'NS'
                     OR f.kickoff >= (CAST(:now_utc AS timestamptz) - (CAST(:stale_ns_hours AS int) * interval '1 hour')))
                AND COALESCE(p.status,'PENDING') = 'PENDING'
              UNION ALL
              SELECT pt.fixture_id FROM predictions_totals pt
              JOIN fixtures f ON f.id = pt.fixture_id
              WHERE (:league_id IS NULL OR f.league_id = :league_id)
                AND f.kickoff >= :date_from AND f.kickoff <= :date_to
                AND COALESCE(f.status,'UNK') NOT IN ('FT','AET','PEN','CANC','ABD','AWD','WO')
                AND (CAST(:stale_ns_hours AS int) <= 0
                     OR COALESCE(f.status,'UNK') <> 'NS'
                     OR f.kickoff >= (CAST(:now_utc AS timestamptz) - (CAST(:stale_ns_hours AS int) * interval '1 hour')))
                AND COALESCE(pt.status,'PENDING') = 'PENDING'
            )
            SELECT COUNT(*) AS cnt FROM combined
        """).bindparams(
            bindparam("league_id", type_=Integer),
            bindparam("date_from", type_=SADateTime(timezone=True)),
            bindparam("date_to", type_=SADateTime(timezone=True)),
            bindparam("now_utc", type_=SADateTime(timezone=True)),
            bindparam("stale_ns_hours", type_=Integer),
        ),
        {"league_id": league_id, "date_from": date_from, "date_to": date_to, "now_utc": now_utc, "stale_ns_hours": stale_ns_hours},
    )
    cnt_row = cnt_res.first()
    response.headers["X-Total-Count"] = str(int(cnt_row.cnt or 0) if cnt_row else 0)
    response.headers["Cache-Control"] = "public, max-age=120"

    res = await session.execute(
        text("""
            WITH combined AS (
              SELECT '1X2'::text AS market, p.fixture_id, f.kickoff, th.name AS home, ta.name AS away,
                     th.logo_url AS home_logo_url, ta.logo_url AS away_logo_url,
                     f.league_id, l.name AS league, l.logo_url AS league_logo_url,
                     f.status AS fixture_status, f.home_goals, f.away_goals,
                     p.selection_code AS pick, p.initial_odd, p.confidence
              FROM predictions p
              JOIN fixtures f ON f.id = p.fixture_id
              JOIN teams th ON th.id = f.home_team_id
              JOIN teams ta ON ta.id = f.away_team_id
              LEFT JOIN leagues l ON l.id = f.league_id
              WHERE p.selection_code != 'SKIP'
                AND (:league_id IS NULL OR f.league_id = :league_id)
                AND f.kickoff >= :date_from AND f.kickoff <= :date_to
                AND COALESCE(f.status,'UNK') NOT IN ('FT','AET','PEN','CANC','ABD','AWD','WO')
                AND (CAST(:stale_ns_hours AS int) <= 0
                     OR COALESCE(f.status,'UNK') <> 'NS'
                     OR f.kickoff >= (CAST(:now_utc AS timestamptz) - (CAST(:stale_ns_hours AS int) * interval '1 hour')))
                AND COALESCE(p.status,'PENDING') = 'PENDING'
              UNION ALL
              SELECT pt.market::text AS market, pt.fixture_id, f.kickoff, th.name AS home, ta.name AS away,
                     th.logo_url AS home_logo_url, ta.logo_url AS away_logo_url,
                     f.league_id, l.name AS league, l.logo_url AS league_logo_url,
                     f.status AS fixture_status, f.home_goals, f.away_goals,
                     pt.selection AS pick, pt.initial_odd, pt.confidence
              FROM predictions_totals pt
              JOIN fixtures f ON f.id = pt.fixture_id
              JOIN teams th ON th.id = f.home_team_id
              JOIN teams ta ON ta.id = f.away_team_id
              LEFT JOIN leagues l ON l.id = f.league_id
              WHERE (:league_id IS NULL OR f.league_id = :league_id)
                AND f.kickoff >= :date_from AND f.kickoff <= :date_to
                AND COALESCE(f.status,'UNK') NOT IN ('FT','AET','PEN','CANC','ABD','AWD','WO')
                AND (CAST(:stale_ns_hours AS int) <= 0
                     OR COALESCE(f.status,'UNK') <> 'NS'
                     OR f.kickoff >= (CAST(:now_utc AS timestamptz) - (CAST(:stale_ns_hours AS int) * interval '1 hour')))
                AND COALESCE(pt.status,'PENDING') = 'PENDING'
            )
            SELECT * FROM combined
            ORDER BY kickoff ASC
            LIMIT :limit OFFSET :offset
        """).bindparams(
            bindparam("league_id", type_=Integer),
            bindparam("date_from", type_=SADateTime(timezone=True)),
            bindparam("date_to", type_=SADateTime(timezone=True)),
            bindparam("now_utc", type_=SADateTime(timezone=True)),
            bindparam("stale_ns_hours", type_=Integer),
            bindparam("limit", type_=Integer),
            bindparam("offset", type_=Integer),
        ),
        {"league_id": league_id, "date_from": date_from, "date_to": date_to,
         "now_utc": now_utc, "stale_ns_hours": stale_ns_hours, "limit": limit, "offset": offset},
    )
    out = []
    for r in res.fetchall():
        score = f"{r.home_goals}-{r.away_goals}" if r.home_goals is not None and r.away_goals is not None else None
        ev = None
        if r.confidence is not None and r.initial_odd is not None:
            try:
                ev = round(float(Decimal(str(r.confidence)) * Decimal(str(r.initial_odd)) - 1), 4)
            except Exception:
                ev = None
        out.append({
            "fixture_id": int(r.fixture_id),
            "kickoff": r.kickoff.isoformat() if r.kickoff else None,
            "home": r.home,
            "away": r.away,
            "home_logo_url": r.home_logo_url,
            "away_logo_url": r.away_logo_url,
            "league_id": int(r.league_id) if r.league_id else None,
            "league": r.league,
            "league_logo_url": r.league_logo_url,
            "fixture_status": r.fixture_status,
            "score": score,
            "market": r.market,
            "pick": r.pick,
            "odd": float(r.initial_odd) if r.initial_odd else None,
            "confidence": round(float(r.confidence), 4) if r.confidence is not None else None,
            "ev": ev,
        })
    return out


@app.get("/api/public/v1/matches/{fixture_id}")
async def public_match_detail(
    fixture_id: int,
    _rate: None = Depends(_check_public_rate),
    session: AsyncSession = Depends(get_session),
):
    bid = settings.bookmaker_id
    # Base fixture data
    fix_res = await session.execute(
        text("""
            SELECT f.id, f.kickoff, f.status, f.home_goals, f.away_goals,
                   th.name AS home, ta.name AS away,
                   th.logo_url AS home_logo_url, ta.logo_url AS away_logo_url,
                   f.league_id, l.name AS league, l.logo_url AS league_logo_url,
                   o.home_win AS odds_home, o.draw AS odds_draw, o.away_win AS odds_away,
                   o.over_2_5 AS odds_over, o.under_2_5 AS odds_under
            FROM fixtures f
            JOIN teams th ON th.id = f.home_team_id
            JOIN teams ta ON ta.id = f.away_team_id
            LEFT JOIN leagues l ON l.id = f.league_id
            LEFT JOIN odds o ON o.fixture_id = f.id AND o.bookmaker_id = :bid
            WHERE f.id = :fid
        """).bindparams(
            bindparam("fid", type_=Integer),
            bindparam("bid", type_=Integer),
        ),
        {"fid": fixture_id, "bid": bid},
    )
    r = fix_res.first()
    if not r:
        raise HTTPException(status_code=404, detail="Match not found")
    score = f"{r.home_goals}-{r.away_goals}" if r.home_goals is not None and r.away_goals is not None else None

    # Gather predictions from all markets
    preds_res = await session.execute(
        text("""
            SELECT '1X2'::text AS market, p.selection_code AS pick,
                   p.initial_odd, p.confidence, p.status, p.profit
            FROM predictions p WHERE p.fixture_id = :fid AND p.selection_code != 'SKIP'
            UNION ALL
            SELECT pt.market, pt.selection AS pick,
                   pt.initial_odd, pt.confidence, pt.status, pt.profit
            FROM predictions_totals pt WHERE pt.fixture_id = :fid
        """).bindparams(bindparam("fid", type_=Integer)),
        {"fid": fixture_id},
    )
    predictions = []
    first_pred = None
    for pr in preds_res.fetchall():
        ev = None
        if pr.confidence is not None and pr.initial_odd is not None:
            try:
                ev = round(float(Decimal(str(pr.confidence)) * Decimal(str(pr.initial_odd)) - 1), 4)
            except Exception:
                ev = None
        pred_obj = {
            "market": pr.market,
            "pick": pr.pick,
            "odd": float(pr.initial_odd) if pr.initial_odd else None,
            "confidence": round(float(pr.confidence), 4) if pr.confidence is not None else None,
            "ev": ev,
            "status": pr.status or "PENDING",
            "profit": round(float(pr.profit), 2) if pr.profit is not None else None,
        }
        predictions.append(pred_obj)
        if first_pred is None:
            first_pred = pred_obj

    return {
        "fixture_id": int(r.id),
        "kickoff": r.kickoff.isoformat() if r.kickoff else None,
        "status": r.status,
        "home": r.home,
        "away": r.away,
        "home_logo_url": r.home_logo_url,
        "away_logo_url": r.away_logo_url,
        "league_id": int(r.league_id) if r.league_id else None,
        "league": r.league,
        "league_logo_url": r.league_logo_url,
        "score": score,
        "prediction": first_pred,
        "predictions": predictions,
        "odds": {
            "home_win": float(r.odds_home) if r.odds_home else None,
            "draw": float(r.odds_draw) if r.odds_draw else None,
            "away_win": float(r.odds_away) if r.odds_away else None,
            "over_2_5": float(r.odds_over) if r.odds_over else None,
            "under_2_5": float(r.odds_under) if r.odds_under else None,
        } if r.odds_home else None,
    }


@app.get("/api/public/v1/results")
async def public_results(
    league_id: Optional[int] = None,
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _rate: None = Depends(_check_public_rate),
    *,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    cutoff = max(utcnow() - timedelta(days=days), STATS_EPOCH)
    cnt_res = await session.execute(
        text("""
            WITH combined AS (
              SELECT p.fixture_id FROM predictions p
              JOIN fixtures f ON f.id = p.fixture_id
              WHERE p.selection_code != 'SKIP'
                AND p.status IN ('WIN','LOSS')
                AND p.settled_at >= :cutoff
                AND (:league_id IS NULL OR f.league_id = :league_id)
              UNION ALL
              SELECT pt.fixture_id FROM predictions_totals pt
              JOIN fixtures f ON f.id = pt.fixture_id
              WHERE COALESCE(pt.status,'PENDING') IN ('WIN','LOSS')
                AND pt.settled_at >= :cutoff
                AND (:league_id IS NULL OR f.league_id = :league_id)
            )
            SELECT COUNT(*) AS cnt FROM combined
        """).bindparams(
            bindparam("cutoff", type_=SADateTime(timezone=True)),
            bindparam("league_id", type_=Integer),
        ),
        {"cutoff": cutoff, "league_id": league_id},
    )
    cnt_row = cnt_res.first()
    response.headers["X-Total-Count"] = str(int(cnt_row.cnt or 0) if cnt_row else 0)
    response.headers["Cache-Control"] = "public, max-age=120"

    res = await session.execute(
        text("""
            WITH combined AS (
              SELECT '1X2'::text AS market, p.fixture_id, f.kickoff, th.name AS home, ta.name AS away,
                     th.logo_url AS home_logo_url, ta.logo_url AS away_logo_url,
                     f.league_id, l.name AS league, l.logo_url AS league_logo_url,
                     f.home_goals, f.away_goals,
                     p.selection_code AS pick, p.initial_odd, p.confidence,
                     p.status AS bet_status, p.profit
              FROM predictions p
              JOIN fixtures f ON f.id = p.fixture_id
              JOIN teams th ON th.id = f.home_team_id
              JOIN teams ta ON ta.id = f.away_team_id
              LEFT JOIN leagues l ON l.id = f.league_id
              WHERE p.selection_code != 'SKIP'
                AND p.status IN ('WIN','LOSS')
                AND p.settled_at >= :cutoff
                AND (:league_id IS NULL OR f.league_id = :league_id)
              UNION ALL
              SELECT pt.market::text AS market, pt.fixture_id, f.kickoff, th.name AS home, ta.name AS away,
                     th.logo_url AS home_logo_url, ta.logo_url AS away_logo_url,
                     f.league_id, l.name AS league, l.logo_url AS league_logo_url,
                     f.home_goals, f.away_goals,
                     pt.selection AS pick, pt.initial_odd, pt.confidence,
                     COALESCE(pt.status,'PENDING') AS bet_status, pt.profit
              FROM predictions_totals pt
              JOIN fixtures f ON f.id = pt.fixture_id
              JOIN teams th ON th.id = f.home_team_id
              JOIN teams ta ON ta.id = f.away_team_id
              LEFT JOIN leagues l ON l.id = f.league_id
              WHERE COALESCE(pt.status,'PENDING') IN ('WIN','LOSS')
                AND pt.settled_at >= :cutoff
                AND (:league_id IS NULL OR f.league_id = :league_id)
            )
            SELECT * FROM combined
            ORDER BY kickoff DESC
            LIMIT :limit OFFSET :offset
        """).bindparams(
            bindparam("cutoff", type_=SADateTime(timezone=True)),
            bindparam("league_id", type_=Integer),
            bindparam("limit", type_=Integer),
            bindparam("offset", type_=Integer),
        ),
        {"cutoff": cutoff, "league_id": league_id, "limit": limit, "offset": offset},
    )
    out = []
    for r in res.fetchall():
        score = f"{r.home_goals}-{r.away_goals}" if r.home_goals is not None and r.away_goals is not None else None
        ev = None
        if r.confidence is not None and r.initial_odd is not None:
            try:
                ev = round(float(Decimal(str(r.confidence)) * Decimal(str(r.initial_odd)) - 1), 4)
            except Exception:
                ev = None
        out.append({
            "fixture_id": int(r.fixture_id),
            "kickoff": r.kickoff.isoformat() if r.kickoff else None,
            "home": r.home,
            "away": r.away,
            "home_logo_url": r.home_logo_url,
            "away_logo_url": r.away_logo_url,
            "league_id": int(r.league_id) if r.league_id else None,
            "league": r.league,
            "league_logo_url": r.league_logo_url,
            "score": score,
            "market": r.market,
            "pick": r.pick,
            "odd": float(r.initial_odd) if r.initial_odd else None,
            "ev": ev,
            "status": r.bet_status,
            "profit": round(float(r.profit), 2) if r.profit is not None else None,
        })
    return out


@app.get("/api/public/v1/standings")
async def public_standings(
    league_id: int = Query(...),
    season: Optional[int] = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    _rate: None = Depends(_check_public_rate),
    *,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    season_val = int(season or settings.season)
    cnt_row = (await session.execute(
        text("SELECT COUNT(*) AS cnt FROM team_standings WHERE season=:s AND league_id=:lid").bindparams(
            bindparam("s", type_=Integer), bindparam("lid", type_=Integer)),
        {"s": season_val, "lid": league_id},
    )).first()
    response.headers["X-Total-Count"] = str(int(cnt_row.cnt or 0) if cnt_row else 0)
    response.headers["Cache-Control"] = "public, max-age=300"

    res = await session.execute(
        text("""
            SELECT ts.team_id, t.name AS team_name, t.logo_url AS team_logo_url,
                   ts.rank, ts.points, ts.played, ts.goals_for, ts.goals_against,
                   ts.goal_diff, ts.form
            FROM team_standings ts
            JOIN teams t ON t.id = ts.team_id
            WHERE ts.season = :s AND ts.league_id = :lid
            ORDER BY ts.rank ASC NULLS LAST, ts.points DESC NULLS LAST
            LIMIT :limit OFFSET :offset
        """).bindparams(
            bindparam("s", type_=Integer), bindparam("lid", type_=Integer),
            bindparam("limit", type_=Integer), bindparam("offset", type_=Integer),
        ),
        {"s": season_val, "lid": league_id, "limit": limit, "offset": offset},
    )
    return [
        {
            "team_id": int(r.team_id), "team_name": r.team_name, "team_logo_url": getattr(r, 'team_logo_url', None),
            "rank": int(r.rank) if r.rank is not None else None,
            "points": int(r.points) if r.points is not None else None,
            "played": int(r.played) if r.played is not None else None,
            "goals_for": int(r.goals_for) if r.goals_for is not None else None,
            "goals_against": int(r.goals_against) if r.goals_against is not None else None,
            "goal_diff": int(r.goal_diff) if r.goal_diff is not None else None,
            "form": r.form,
        }
        for r in res.fetchall()
    ]


@app.get("/api/v1/league_baselines")
async def api_league_baselines(
    league_id: int = Query(...),
    season: int = Query(...),
    date_key: Optional[datetime] = None,
    _: None = Depends(_require_admin),
    session: AsyncSession = Depends(get_session),
):
    dk = date_key.date() if date_key else None
    if dk:
        res = await session.execute(
            text(
                """
                SELECT * FROM league_baselines
                WHERE league_id=:lid AND season=:season AND date_key=:dk
                """
            ),
            {"lid": league_id, "season": season, "dk": dk},
        )
    else:
        res = await session.execute(
            text(
                """
                SELECT * FROM league_baselines
                WHERE league_id=:lid AND season=:season
                ORDER BY date_key DESC
                LIMIT 1
                """
            ),
            {"lid": league_id, "season": season},
        )
    row = res.first()
    if not row:
        return {"detail": "not found"}
    return {
        "league_id": row.league_id,
        "season": row.season,
        "date_key": row.date_key.isoformat(),
        "avg_home_xg": float(row.avg_home_xg) if row.avg_home_xg is not None else None,
        "avg_away_xg": float(row.avg_away_xg) if row.avg_away_xg is not None else None,
        "draw_freq": float(row.draw_freq) if row.draw_freq is not None else None,
        "avg_goals": float(row.avg_goals) if row.avg_goals is not None else None,
        "dc_rho": float(getattr(row, "dc_rho", None)) if getattr(row, "dc_rho", None) is not None else None,
        "calib_alpha": float(getattr(row, "calib_alpha", None)) if getattr(row, "calib_alpha", None) is not None else None,
    }


@app.get("/api/v1/jobs/status")
async def api_jobs_status(_: None = Depends(_require_admin)):
    return {
        "jobs": _serialize_status(JOB_STATUS),
        "pipeline": _serialize_status(PIPELINE_STATUS),
    }


@app.get("/api/v1/jobs/runs")
async def api_job_runs(
    job_name: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: None = Depends(_require_admin),
    *,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    status_norm = None
    if status:
        status_norm = status.strip().lower()
        if status_norm not in {"running", "ok", "failed"}:
            raise HTTPException(status_code=400, detail="status must be one of: running, ok, failed")

    count_row = (
        await session.execute(
            text(
                """
                SELECT COUNT(*) AS cnt
                FROM job_runs
                WHERE (:job IS NULL OR job_name = :job)
                  AND (:status IS NULL OR lower(status) = :status)
                """
            ).bindparams(
                bindparam("job", type_=SAString),
                bindparam("status", type_=SAString),
            ),
            {"job": job_name, "status": status_norm},
        )
    ).first()
    response.headers["X-Total-Count"] = str(int(count_row.cnt or 0) if count_row else 0)

    res = await session.execute(
        text(
            """
            SELECT id, job_name, status, triggered_by, started_at, finished_at, error
                 , meta
            FROM job_runs
            WHERE (:job IS NULL OR job_name = :job)
              AND (:status IS NULL OR lower(status) = :status)
            ORDER BY started_at DESC
            LIMIT :limit OFFSET :offset
            """
        ).bindparams(
            bindparam("job", type_=SAString),
            bindparam("status", type_=SAString),
            bindparam("limit", type_=Integer),
            bindparam("offset", type_=Integer),
        ),
        {"job": job_name, "status": status_norm, "limit": limit, "offset": offset},
    )
    out = []
    for row in res.fetchall():
        duration = None
        if row.started_at and row.finished_at:
            try:
                duration = (row.finished_at - row.started_at).total_seconds()
            except Exception:
                duration = None
        out.append(
            {
                "id": int(row.id),
                "job_name": row.job_name,
                "status": row.status,
                "triggered_by": row.triggered_by,
                "started_at": row.started_at.isoformat() if row.started_at is not None else None,
                "finished_at": row.finished_at.isoformat() if row.finished_at is not None else None,
                "duration_seconds": duration,
                "error": row.error,
                "meta": row.meta if isinstance(row.meta, dict) else row.meta,
            }
        )
    return out
