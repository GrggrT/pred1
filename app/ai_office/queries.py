"""SQL queries for AI Office agents — health checks, settled, upcoming picks, news."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logger import get_logger
from .config import MONITOR_THRESHOLDS, AGENT_EXPECTED_INTERVALS

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


async def check_agent_freshness(session: AsyncSession) -> dict[str, Any]:
    """Check #7: Are AI Office agents running on schedule?"""
    result = await session.execute(text("""
        SELECT agent,
               EXTRACT(EPOCH FROM now() - MAX(created_at)) / 3600 AS hours_ago
        FROM ai_office_reports
        GROUP BY agent
    """))
    rows = {r["agent"]: float(r["hours_ago"]) for r in result.mappings().all()}

    stale: list[str] = []
    for agent_name, max_hours in AGENT_EXPECTED_INTERVALS.items():
        hours = rows.get(agent_name)
        if hours is None:
            stale.append(f"{agent_name}(never)")
        elif hours > max_hours:
            stale.append(f"{agent_name}({hours:.0f}h)")

    if not stale:
        return {
            "name": "agent_freshness",
            "label": "AI Agents",
            "value": f"{len(rows)} active",
            "threshold": "per-agent interval",
            "severity": "medium",
            "ok": True,
            "detail": f"All {len(AGENT_EXPECTED_INTERVALS)} tracked agents on schedule",
        }
    return {
        "name": "agent_freshness",
        "label": "AI Agents",
        "value": f"{len(stale)} stale",
        "threshold": "per-agent interval",
        "severity": "medium",
        "ok": False,
        "detail": f"Stale agents: {', '.join(stale)}",
    }


async def run_all_checks(session: AsyncSession) -> list[dict[str, Any]]:
    """Run all 7 health checks and return results."""
    checks = []
    for check_fn in [
        check_sync_freshness,
        check_upcoming_predictions,
        check_unsettled,
        check_api_usage,
        check_pinnacle_odds,
        check_errors_24h,
        check_agent_freshness,
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


async def fetch_ask_context(session: AsyncSession) -> str:
    """Build a summary context string for the /ask command.

    Aggregates key metrics from the database to provide Groq with
    enough context to answer free-form questions.
    """
    parts = []

    # 1. Overall prediction stats (last 30 days)
    try:
        r = await session.execute(text("""
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE p.status = 'WON') as won,
                COUNT(*) FILTER (WHERE p.status = 'LOSS') as lost,
                ROUND(COALESCE(SUM(p.profit), 0)::numeric, 2) as profit,
                ROUND(CASE WHEN COUNT(*) > 0
                    THEN (SUM(p.profit) / COUNT(*) * 100)::numeric
                    ELSE 0 END, 1) as roi
            FROM predictions p
            JOIN fixtures f ON f.id = p.fixture_id
            WHERE p.status IN ('WON', 'LOSS')
              AND f.kickoff > now() - interval '30 days'
        """))
        row = r.mappings().first()
        if row:
            parts.append(
                f"1X2 за 30 дней: {row['total']} ставок, "
                f"{row['won']} W / {row['lost']} L, "
                f"profit={row['profit']}u, ROI={row['roi']}%"
            )
    except Exception:
        pass

    # 2. Per-league breakdown
    try:
        r = await session.execute(text("""
            SELECT l.name as league,
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE p.status = 'WON') as won,
                ROUND(COALESCE(SUM(p.profit), 0)::numeric, 2) as profit
            FROM predictions p
            JOIN fixtures f ON f.id = p.fixture_id
            JOIN leagues l ON l.id = f.league_id
            WHERE p.status IN ('WON', 'LOSS')
              AND f.kickoff > now() - interval '30 days'
            GROUP BY l.name
            ORDER BY total DESC
            LIMIT 6
        """))
        rows = r.mappings().all()
        if rows:
            lines = ["По лигам (30 дней):"]
            for rr in rows:
                lines.append(f"  {rr['league']}: {rr['total']} ставок, {rr['won']}W, profit={rr['profit']}u")
            parts.append("\n".join(lines))
    except Exception:
        pass

    # 3. Recent settled (last 48h)
    try:
        r = await session.execute(text("""
            SELECT ht.name || ' — ' || att.name as match,
                   p.selection_code, p.initial_odd, p.status, p.profit
            FROM predictions p
            JOIN fixtures f ON f.id = p.fixture_id
            JOIN teams ht ON ht.id = f.home_team_id
            JOIN teams att ON att.id = f.away_team_id
            WHERE p.status IN ('WON', 'LOSS')
              AND f.kickoff > now() - interval '48 hours'
            ORDER BY f.kickoff DESC
            LIMIT 10
        """))
        rows = r.mappings().all()
        if rows:
            lines = ["Последние settled (48ч):"]
            for rr in rows:
                emoji = "✅" if rr['status'] == 'WON' else "❌"
                lines.append(f"  {emoji} {rr['match']} | {rr['selection_code']} @ {rr['initial_odd']} | {rr['profit']}u")
            parts.append("\n".join(lines))
    except Exception:
        pass

    # 4. Upcoming predictions count
    try:
        r = await session.execute(text("""
            SELECT COUNT(*) as cnt
            FROM predictions p
            JOIN fixtures f ON f.id = p.fixture_id
            WHERE f.status = 'NS'
              AND f.kickoff BETWEEN now() AND now() + interval '48 hours'
              AND p.selection_code != 'SKIP'
        """))
        row = r.mappings().first()
        if row:
            parts.append(f"Upcoming predictions (48ч): {row['cnt']}")
    except Exception:
        pass

    # 5. Last job runs
    try:
        r = await session.execute(text("""
            SELECT DISTINCT ON (job_name) job_name, status, started_at,
                   ROUND(EXTRACT(EPOCH FROM now() - started_at)/3600) as hours_ago
            FROM job_runs
            ORDER BY job_name, started_at DESC
            LIMIT 10
        """))
        rows = r.mappings().all()
        if rows:
            lines = ["Последние джобы:"]
            for rr in rows:
                lines.append(f"  {rr['job_name']}: {rr['status']} ({rr['hours_ago']}ч назад)")
            parts.append("\n".join(lines))
    except Exception:
        pass

    return "\n\n".join(parts) if parts else "Нет данных в БД."


async def cleanup_old_reports(session: AsyncSession, days: int = 90) -> int:
    """Delete reports older than N days. Returns count deleted."""
    result = await session.execute(text("""
        DELETE FROM ai_office_reports
        WHERE created_at < now() - make_interval(days => :days)
    """), {"days": days})
    await session.commit()
    return result.rowcount or 0


async def cleanup_old_scout_reports(session: AsyncSession, days: int = 90) -> int:
    """Delete scout reports older than N days. Returns count deleted."""
    result = await session.execute(text("""
        DELETE FROM scout_reports
        WHERE created_at < now() - make_interval(days => :days)
    """), {"days": days})
    await session.commit()
    return result.rowcount or 0


async def cleanup_old_news(session: AsyncSession, days: int = 90) -> int:
    """Delete news articles + sources older than N days. Returns count deleted."""
    # Delete articles first (references sources)
    r1 = await session.execute(text("""
        DELETE FROM news_articles
        WHERE created_at < now() - make_interval(days => :days)
    """), {"days": days})
    r2 = await session.execute(text("""
        DELETE FROM news_sources
        WHERE fetched_at < now() - make_interval(days => :days)
    """), {"days": days})
    await session.commit()
    return (r1.rowcount or 0) + (r2.rowcount or 0)


# ---------------------------------------------------------------------------
# Scout queries
# ---------------------------------------------------------------------------

async def fetch_scout_matches(session: AsyncSession) -> list[dict[str, Any]]:
    """Fetch upcoming predictions for scout analysis (next 36 hours).

    Includes both 1X2 (non-SKIP) and totals/DC predictions.
    """
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

        UNION ALL

        SELECT NULL::integer as prediction_id, pt.market,
               pt.selection, pt.initial_odd as odd,
               pt.confidence,
               NULL::jsonb as feature_flags,
               f.id as fixture_id, f.kickoff,
               ht.name as home_team, att.name as away_team,
               l.name as league
        FROM predictions_totals pt
        JOIN fixtures f ON f.id = pt.fixture_id
        JOIN teams ht ON ht.id = f.home_team_id
        JOIN teams att ON att.id = f.away_team_id
        JOIN leagues l ON l.id = f.league_id
        WHERE f.status = 'NS'
          AND f.kickoff BETWEEN now() AND now() + interval '36 hours'

        ORDER BY kickoff, market
    """))
    return [dict(r) for r in result.mappings().all()]


# ---------------------------------------------------------------------------
# News queries
# ---------------------------------------------------------------------------

def _make_slug(title: str) -> str:
    """Generate a URL-friendly slug from a title."""
    import re
    import time

    slug = re.sub(r"[^\w\s-]", "", title.lower())
    slug = re.sub(r"[\s_]+", "-", slug).strip("-")
    if not slug or len(slug) < 3:
        slug = f"news-{int(time.time())}"
    return slug[:280]


async def save_news_source(
    session: AsyncSession,
    url: str,
    source_name: str,
    title: str,
    raw_text: str | None = None,
) -> int | None:
    """Insert RSS item into news_sources. Skip if URL exists. Return id or None."""
    result = await session.execute(
        text("""
            INSERT INTO news_sources (url, source_name, title, raw_text)
            VALUES (:url, :source_name, :title, :raw_text)
            ON CONFLICT (url) DO NOTHING
            RETURNING id
        """),
        {"url": url, "source_name": source_name, "title": title, "raw_text": raw_text},
    )
    await session.commit()
    row = result.fetchone()
    return row[0] if row else None


async def fetch_unprocessed_sources(
    session: AsyncSession, limit: int = 30
) -> list[dict[str, Any]]:
    """Fetch unprocessed news_sources (not yet linked to article)."""
    result = await session.execute(
        text("""
            SELECT id, url, source_name, title, raw_text
            FROM news_sources
            WHERE processed = false
              AND fetched_at > now() - interval '48 hours'
            ORDER BY fetched_at DESC
            LIMIT :limit
        """),
        {"limit": limit},
    )
    return [dict(r) for r in result.mappings().all()]


async def mark_sources_processed(
    session: AsyncSession,
    source_ids: list[int],
    article_id: int | None = None,
) -> None:
    """Mark news_sources as processed, optionally link to article."""
    if not source_ids:
        return
    placeholders = ", ".join(f":id_{i}" for i in range(len(source_ids)))
    params: dict[str, Any] = {f"id_{i}": sid for i, sid in enumerate(source_ids)}
    params["article_id"] = article_id
    await session.execute(
        text(f"""
            UPDATE news_sources
            SET processed = true, article_id = :article_id
            WHERE id IN ({placeholders})
        """),
        params,
    )
    await session.commit()


async def save_news_article(
    session: AsyncSession,
    title: str,
    body: str,
    summary: str,
    category: str,
    sources: list[str],
    league_id: int | None = None,
    status: str = "published",
) -> int:
    """Insert a news article and return id."""
    import json

    slug = _make_slug(title)
    is_published = status == "published"
    result = await session.execute(
        text("""
            INSERT INTO news_articles
                (title, slug, body, summary, category, sources, league_id,
                 status, published_at)
            VALUES
                (:title, :slug, :body, :summary, :category,
                 CAST(:sources AS jsonb), :league_id, :status,
                 CASE WHEN :is_published THEN now() ELSE NULL END)
            RETURNING id
        """),
        {
            "title": title,
            "slug": slug,
            "body": body,
            "summary": summary,
            "category": category,
            "sources": json.dumps(sources),
            "league_id": league_id,
            "status": status,
            "is_published": is_published,
        },
    )
    await session.commit()
    article_id = result.scalar()
    log.info("news_article_saved title=%s category=%s id=%s", title[:50], category, article_id)
    return article_id


async def fetch_recent_news(
    session: AsyncSession, limit: int = 10, category: str | None = None
) -> list[dict[str, Any]]:
    """Fetch recent published news articles."""
    where_cat = "AND category = :category" if category else ""
    result = await session.execute(
        text(f"""
            SELECT id, title, summary, category, published_at
            FROM news_articles
            WHERE status = 'published'
              {where_cat}
            ORDER BY published_at DESC
            LIMIT :limit
        """),
        {"limit": limit, "category": category},
    )
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
