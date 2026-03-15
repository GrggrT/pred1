"""SQL queries for AI Office agents — health checks, settled, upcoming picks."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logger import get_logger
from .config import MONITOR_THRESHOLDS

log = get_logger("ai_office.queries")


async def check_sync_freshness(session: AsyncSession) -> dict[str, Any]:
    """Check #1: How long ago did sync_data run successfully?"""
    row = await session.execute(text("""
        SELECT job_name, status, started_at,
               EXTRACT(EPOCH FROM now() - started_at) / 3600 AS hours_ago
        FROM job_runs
        WHERE job_name = 'sync_data'
        ORDER BY started_at DESC
        LIMIT 1
    """))
    r = row.mappings().first()
    if not r:
        return {
            "name": "sync_freshness",
            "label": "Sync Data",
            "value": None,
            "threshold": MONITOR_THRESHOLDS["sync_hours_warn"],
            "severity": "high",
            "ok": False,
            "detail": "No sync_data runs found",
        }
    hours = float(r["hours_ago"]) if r["hours_ago"] is not None else 999
    threshold = MONITOR_THRESHOLDS["sync_hours_warn"]
    return {
        "name": "sync_freshness",
        "label": "Sync Data",
        "value": round(hours, 1),
        "threshold": threshold,
        "severity": "medium",
        "ok": hours <= threshold,
        "detail": f"Last sync {hours:.1f}h ago, status={r['status']}",
    }


async def check_upcoming_predictions(session: AsyncSession) -> dict[str, Any]:
    """Check #2: Are there predictions for upcoming matches (next 48h)?"""
    row = await session.execute(text("""
        SELECT COUNT(*) AS cnt
        FROM predictions p
        JOIN fixtures f ON f.id = p.fixture_id
        WHERE f.status = 'NS'
          AND f.kickoff BETWEEN now() AND now() + interval '48 hours'
    """))
    cnt = row.scalar() or 0
    threshold = MONITOR_THRESHOLDS["upcoming_predictions_min"]
    return {
        "name": "upcoming_predictions",
        "label": "Upcoming Predictions",
        "value": cnt,
        "threshold": threshold,
        "severity": "high",
        "ok": cnt >= threshold,
        "detail": f"{cnt} predictions for matches in next 48h",
    }


async def check_unsettled(session: AsyncSession) -> dict[str, Any]:
    """Check #3: Unsettled predictions for finished matches (>3h ago)."""
    row = await session.execute(text("""
        SELECT COUNT(*) AS cnt
        FROM fixtures f
        JOIN predictions p ON p.fixture_id = f.id
        WHERE f.status = 'FT'
          AND p.status = 'PENDING'
          AND f.kickoff < now() - interval '3 hours'
    """))
    cnt = row.scalar() or 0
    threshold = MONITOR_THRESHOLDS["unsettled_warn"]
    return {
        "name": "unsettled",
        "label": "Unsettled Matches",
        "value": cnt,
        "threshold": threshold,
        "severity": "medium",
        "ok": cnt <= threshold,
        "detail": f"{cnt} unsettled finished predictions",
    }


async def check_api_usage(session: AsyncSession) -> dict[str, Any]:
    """Check #4: Active API cache entries (proxy for API usage)."""
    row = await session.execute(text("""
        SELECT COUNT(*) AS cnt
        FROM api_cache
        WHERE expires_at > now()
    """))
    cnt = row.scalar() or 0
    daily_limit = settings.api_football_daily_limit
    pct = cnt / max(daily_limit, 1)
    threshold = MONITOR_THRESHOLDS["api_cache_24h_warn"]
    return {
        "name": "api_usage",
        "label": "API Usage (24h)",
        "value": cnt,
        "threshold": f"{threshold * 100:.0f}% of {daily_limit}",
        "severity": "medium",
        "ok": pct <= threshold,
        "detail": f"{cnt} API calls ({pct * 100:.1f}% of {daily_limit} limit)",
    }


async def check_pinnacle_odds(session: AsyncSession) -> dict[str, Any]:
    """Check #5: Pinnacle odds synced in last 24h."""
    row = await session.execute(text("""
        SELECT COUNT(*) AS cnt
        FROM odds
        WHERE bookmaker_id = 4
          AND fetched_at > now() - interval '24 hours'
    """))
    cnt = row.scalar() or 0
    threshold = MONITOR_THRESHOLDS["pinnacle_24h_min"]
    return {
        "name": "pinnacle_odds",
        "label": "Pinnacle Odds (24h)",
        "value": cnt,
        "threshold": threshold,
        "severity": "medium",
        "ok": cnt >= threshold,
        "detail": f"{cnt} Pinnacle odds updated in 24h",
    }


async def check_errors_24h(session: AsyncSession) -> dict[str, Any]:
    """Check #6: Job errors in last 24 hours."""
    row = await session.execute(text("""
        SELECT COUNT(*) AS cnt
        FROM job_runs
        WHERE status = 'ERROR'
          AND started_at > now() - interval '24 hours'
    """))
    cnt = row.scalar() or 0
    threshold = MONITOR_THRESHOLDS["errors_24h_warn"]
    return {
        "name": "errors_24h",
        "label": "Errors (24h)",
        "value": cnt,
        "threshold": threshold,
        "severity": "high",
        "ok": cnt <= threshold,
        "detail": f"{cnt} job errors in last 24h",
    }


async def run_all_checks(session: AsyncSession) -> list[dict[str, Any]]:
    """Run all 6 health checks and return results."""
    checks = []
    for check_fn in [
        check_sync_freshness,
        check_upcoming_predictions,
        check_unsettled,
        check_api_usage,
        check_pinnacle_odds,
        check_errors_24h,
    ]:
        try:
            result = await check_fn(session)
            checks.append(result)
        except Exception as exc:
            log.exception("health_check_failed fn=%s", check_fn.__name__)
            await session.rollback()
            checks.append({
                "name": check_fn.__name__.replace("check_", ""),
                "label": check_fn.__name__,
                "value": None,
                "threshold": None,
                "severity": "high",
                "ok": False,
                "detail": f"Check failed: {exc}",
            })
    return checks


async def save_report(
    session: AsyncSession,
    agent: str,
    report_type: str,
    report_text: str,
    metadata: dict | None = None,
    telegram_sent: bool = False,
) -> int:
    """Save an agent report to ai_office_reports. Returns report id."""
    import json

    result = await session.execute(
        text("""
            INSERT INTO ai_office_reports (agent, report_type, report_text, metadata, telegram_sent)
            VALUES (:agent, :report_type, :report_text, CAST(:metadata AS jsonb), :telegram_sent)
            RETURNING id
        """),
        {
            "agent": agent,
            "report_type": report_type,
            "report_text": report_text,
            "metadata": json.dumps(metadata or {}),
            "telegram_sent": telegram_sent,
        },
    )
    await session.commit()
    report_id = result.scalar()
    log.info("report_saved agent=%s type=%s id=%s", agent, report_type, report_id)
    return report_id


# ---------------------------------------------------------------------------
# Analyst queries
# ---------------------------------------------------------------------------

async def fetch_settled_24h(session: AsyncSession) -> list[dict[str, Any]]:
    """Fetch settled predictions from the last 24 hours (1X2 + totals)."""
    result = await session.execute(text("""
        SELECT '1X2' as market, p.selection_code as selection, p.status,
               p.initial_odd, p.profit, p.confidence,
               p.feature_flags,
               ht.name as home_team, att.name as away_team,
               f.home_goals, f.away_goals, f.kickoff,
               l.name as league
        FROM predictions p
        JOIN fixtures f ON f.id = p.fixture_id
        JOIN teams ht ON ht.id = f.home_team_id
        JOIN teams att ON att.id = f.away_team_id
        JOIN leagues l ON l.id = f.league_id
        WHERE p.settled_at > now() - interval '24 hours'
          AND p.status IN ('WON', 'LOSS')
          AND p.selection_code != 'SKIP'

        UNION ALL

        SELECT pt.market, pt.selection, pt.status,
               pt.initial_odd, pt.profit, pt.confidence,
               NULL::jsonb as feature_flags,
               ht.name as home_team, att.name as away_team,
               f.home_goals, f.away_goals, f.kickoff,
               l.name as league
        FROM predictions_totals pt
        JOIN fixtures f ON f.id = pt.fixture_id
        JOIN teams ht ON ht.id = f.home_team_id
        JOIN teams att ON att.id = f.away_team_id
        JOIN leagues l ON l.id = f.league_id
        WHERE pt.settled_at > now() - interval '24 hours'
          AND pt.status IN ('WON', 'LOSS')

        ORDER BY league, kickoff
    """))
    return [dict(r) for r in result.mappings().all()]


# ---------------------------------------------------------------------------
# Scout queries
# ---------------------------------------------------------------------------

async def fetch_scout_matches(session: AsyncSession) -> list[dict[str, Any]]:
    """Fetch upcoming 1X2 predictions for scout analysis (next 36 hours)."""
    result = await session.execute(text("""
        SELECT p.id as prediction_id, '1X2' as market,
               p.selection_code as selection, p.initial_odd as odd,
               p.confidence,
               p.feature_flags,
               f.id as fixture_id, f.kickoff,
               ht.name as home_team, att.name as away_team,
               l.name as league
        FROM predictions p
        JOIN fixtures f ON f.id = p.fixture_id
        JOIN teams ht ON ht.id = f.home_team_id
        JOIN teams att ON att.id = f.away_team_id
        JOIN leagues l ON l.id = f.league_id
        WHERE f.status = 'NS'
          AND f.kickoff BETWEEN now() AND now() + interval '36 hours'
          AND p.selection_code != 'SKIP'
        ORDER BY f.kickoff
    """))
    return [dict(r) for r in result.mappings().all()]


async def save_scout_report(
    session: AsyncSession,
    *,
    fixture_id: int,
    prediction_id: int | None,
    verdict: str,
    report_text: str,
    factors: dict | None = None,
    model_selection: str | None = None,
    model_odd: float | None = None,
) -> int:
    """Upsert a scout report into scout_reports. Returns report id."""
    import json

    result = await session.execute(
        text("""
            INSERT INTO scout_reports
                (fixture_id, prediction_id, verdict, report_text, factors,
                 model_selection, model_odd)
            VALUES (:fixture_id, :prediction_id, :verdict, :report_text,
                    CAST(:factors AS jsonb), :model_selection, :model_odd)
            ON CONFLICT (fixture_id) DO UPDATE SET
                verdict = EXCLUDED.verdict,
                report_text = EXCLUDED.report_text,
                factors = EXCLUDED.factors,
                prediction_id = EXCLUDED.prediction_id,
                model_selection = EXCLUDED.model_selection,
                model_odd = EXCLUDED.model_odd,
                created_at = now()
            RETURNING id
        """),
        {
            "fixture_id": fixture_id,
            "prediction_id": prediction_id,
            "verdict": verdict,
            "report_text": report_text,
            "factors": json.dumps(factors or {}),
            "model_selection": model_selection,
            "model_odd": model_odd,
        },
    )
    await session.commit()
    report_id = result.scalar()
    log.info(
        "scout_report_saved fixture=%s verdict=%s id=%s",
        fixture_id, verdict, report_id,
    )
    return report_id


async def fetch_existing_scout_fixture_ids(session: AsyncSession) -> set[int]:
    """Fetch fixture IDs that already have a scout report (< 24h old)."""
    result = await session.execute(text("""
        SELECT fixture_id FROM scout_reports
        WHERE created_at > now() - interval '24 hours'
    """))
    return {row[0] for row in result.fetchall()}


async def fetch_red_fixture_ids(session: AsyncSession) -> set[int]:
    """Fetch fixture IDs with active RED scout verdicts (no override)."""
    result = await session.execute(text("""
        SELECT fixture_id FROM scout_reports
        WHERE verdict = 'red'
          AND override_verdict IS NULL
          AND created_at > now() - interval '48 hours'
    """))
    return {row[0] for row in result.fetchall()}


async def override_scout_verdict(
    session: AsyncSession,
    fixture_id: int,
    new_verdict: str,
    reason: str,
) -> bool:
    """Override a scout verdict. Returns True if a row was updated."""
    result = await session.execute(
        text("""
            UPDATE scout_reports
            SET override_verdict = :verdict,
                override_reason = :reason
            WHERE fixture_id = :fixture_id
        """),
        {"fixture_id": fixture_id, "verdict": new_verdict, "reason": reason},
    )
    await session.commit()
    updated = result.rowcount > 0
    if updated:
        log.info(
            "scout_override fixture=%s new_verdict=%s reason=%s",
            fixture_id, new_verdict, reason,
        )
    return updated


# ---------------------------------------------------------------------------
# Content queries
# ---------------------------------------------------------------------------

async def fetch_upcoming_picks(session: AsyncSession) -> list[dict[str, Any]]:
    """Fetch 1X2 predictions for upcoming matches (next 36 hours)."""
    result = await session.execute(text("""
        SELECT '1X2' as market,
               p.selection_code as selection, p.initial_odd, p.confidence,
               p.feature_flags,
               f.kickoff, f.id as fixture_id,
               ht.name as home_team, att.name as away_team,
               l.name as league
        FROM predictions p
        JOIN fixtures f ON f.id = p.fixture_id
        JOIN teams ht ON ht.id = f.home_team_id
        JOIN teams att ON att.id = f.away_team_id
        JOIN leagues l ON l.id = f.league_id
        WHERE f.status = 'NS'
          AND f.kickoff BETWEEN now() AND now() + interval '36 hours'
          AND p.selection_code != 'SKIP'
        ORDER BY f.kickoff
    """))
    return [dict(r) for r in result.mappings().all()]


async def fetch_upcoming_totals(session: AsyncSession) -> list[dict[str, Any]]:
    """Fetch totals/BTTS/DC predictions for upcoming matches (next 36 hours)."""
    result = await session.execute(text("""
        SELECT pt.market, pt.selection, pt.initial_odd, pt.confidence,
               NULL::jsonb as feature_flags,
               f.kickoff, f.id as fixture_id,
               ht.name as home_team, att.name as away_team,
               l.name as league
        FROM predictions_totals pt
        JOIN fixtures f ON f.id = pt.fixture_id
        JOIN teams ht ON ht.id = f.home_team_id
        JOIN teams att ON att.id = f.away_team_id
        JOIN leagues l ON l.id = f.league_id
        WHERE f.status = 'NS'
          AND f.kickoff BETWEEN now() AND now() + interval '36 hours'
        ORDER BY f.kickoff
    """))
    return [dict(r) for r in result.mappings().all()]
