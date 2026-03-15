"""Monitor agent — runs health checks, sends Telegram alerts on failures."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logger import get_logger
from app.data.providers.groq import generate_text

from ..config import MONITOR_SYSTEM_PROMPT
from ..queries import run_all_checks, save_report

log = get_logger("ai_office.monitor")


def _format_checks_for_llm(checks: list[dict[str, Any]]) -> str:
    """Format health check results into a prompt for the LLM."""
    lines = [
        f"Время: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "Результаты health checks:",
    ]
    for c in checks:
        status = "✅ OK" if c["ok"] else "❌ FAIL"
        lines.append(f"- {c['label']}: {status} | Значение: {c['value']} | Порог: {c['threshold']} | {c['detail']}")
    return "\n".join(lines)


def _format_checks_simple(checks: list[dict[str, Any]]) -> str:
    """Format health check results as a simple text report (fallback without LLM)."""
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    failed = [c for c in checks if not c["ok"]]

    if not failed:
        return f"✅ Система в норме\n🕐 {now_str}"

    lines = [f"🛡️ Мониторинг {now_str}", ""]
    for c in failed:
        emoji = "🔴" if c["severity"] == "high" else "⚠️"
        lines.append(f"{emoji} <b>{c['label']}</b>: {c['detail']}")
    return "\n".join(lines)


async def send_to_owner(text_msg: str) -> bool:
    """Send a message to the bot owner via Telegram API."""
    owner_id = (settings.telegram_owner_id or "").strip()
    if not owner_id:
        log.warning("TELEGRAM_OWNER_ID not set, cannot send alert")
        return False

    token = (settings.telegram_bot_token or "").strip()
    if not token:
        log.warning("TELEGRAM_BOT_TOKEN not set, cannot send alert")
        return False

    try:
        from app.core.http import telegram_client

        client = telegram_client()
        resp = await client.post(
            "/sendMessage",
            json={
                "chat_id": owner_id,
                "text": text_msg,
                "parse_mode": "HTML",
            },
        )
        if resp.status_code == 200:
            log.info("telegram_alert_sent to=%s len=%d", owner_id, len(text_msg))
            return True
        log.error("telegram_alert_failed status=%s body=%s", resp.status_code, resp.text[:300])
        return False
    except Exception:
        log.exception("telegram_alert_error")
        return False


async def run(session: AsyncSession) -> dict[str, Any]:
    """Execute the monitor agent: run checks → alert if needed.

    Returns summary dict with status and check count.
    """
    log.info("monitor_run_start")
    checks = await run_all_checks(session)
    failed = [c for c in checks if not c["ok"]]

    if not failed:
        log.info("monitor_all_ok checks=%d", len(checks))
        return {"status": "ok", "checks": len(checks), "failed": 0}

    log.warning("monitor_issues_found checks=%d failed=%d", len(checks), len(failed))

    # Try to generate AI alert text via Groq
    alert_text = None
    if settings.groq_enabled and settings.groq_api_key:
        try:
            prompt = _format_checks_for_llm(checks)
            alert_text = await generate_text(
                session,
                prompt,
                system_prompt=MONITOR_SYSTEM_PROMPT,
                temperature=0.3,
                max_tokens=512,
                use_cache=False,  # Always fresh for alerts
            )
        except Exception:
            log.exception("monitor_groq_failed, using simple format")

    # Fallback to simple format
    if not alert_text:
        alert_text = _format_checks_simple(checks)

    # Send Telegram alert
    sent = await send_to_owner(alert_text)

    # Save report to DB
    metadata = {
        "total_checks": len(checks),
        "failed_checks": len(failed),
        "failed_names": [c["name"] for c in failed],
    }
    await save_report(
        session,
        agent="monitor",
        report_type="health_check",
        report_text=alert_text,
        metadata=metadata,
        telegram_sent=sent,
    )

    return {"status": "alert", "checks": len(checks), "failed": len(failed), "telegram_sent": sent}
