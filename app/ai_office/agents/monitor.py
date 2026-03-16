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
    """Send a message to the bot owner via Telegram API.

    Handles messages longer than 4096 chars by splitting into parts.
    Retries once on transient failures (5xx, timeout).
    """
    import asyncio

    owner_id = (settings.telegram_owner_id or "").strip()
    if not owner_id:
        log.warning("TELEGRAM_OWNER_ID not set, cannot send alert")
        return False

    token = (settings.telegram_bot_token or "").strip()
    if not token:
        log.warning("TELEGRAM_BOT_TOKEN not set, cannot send alert")
        return False

    TG_MAX = 4096
    parts = _split_message(text_msg, TG_MAX) if len(text_msg) > TG_MAX else [text_msg]

    try:
        from app.core.http import telegram_client

        client = telegram_client()
        all_ok = True

        for i, part in enumerate(parts):
            ok = await _send_one(client, owner_id, part)
            if not ok:
                # Retry once after 2s
                await asyncio.sleep(2)
                ok = await _send_one(client, owner_id, part)
            if not ok:
                all_ok = False
                log.error("telegram_send_failed part=%d/%d", i + 1, len(parts))

        if all_ok:
            log.info("telegram_alert_sent to=%s len=%d parts=%d", owner_id, len(text_msg), len(parts))
        return all_ok
    except Exception:
        log.exception("telegram_alert_error")
        return False


def _split_message(text: str, max_len: int = 4096) -> list[str]:
    """Split a long message into Telegram-safe chunks at line boundaries."""
    parts: list[str] = []
    while text:
        if len(text) <= max_len:
            parts.append(text)
            break
        # Find last newline within max_len
        split_at = text.rfind("\n", 0, max_len)
        if split_at <= 0:
            split_at = max_len
        parts.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return parts


async def _send_one(client, chat_id: str, text: str) -> bool:
    """Send a single Telegram message. Returns True on success."""
    try:
        resp = await client.post(
            "/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
            },
        )
        if resp.status_code == 200:
            return True
        log.error("telegram_send_error status=%s body=%s", resp.status_code, resp.text[:300])
        return False
    except Exception:
        log.exception("telegram_send_exception")
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
