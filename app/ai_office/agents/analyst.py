"""Analyst agent — daily settled predictions report."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logger import get_logger
from app.data.providers.groq import generate_text

from ..config import ANALYST_SYSTEM_PROMPT
from ..queries import fetch_settled_24h, save_report
from .monitor import send_to_owner

log = get_logger("ai_office.analyst")


def _build_summary(settled: list[dict[str, Any]]) -> dict[str, Any]:
    """Calculate summary stats from settled predictions."""
    wins = [s for s in settled if s["status"] == "WON"]
    losses = [s for s in settled if s["status"] == "LOSS"]
    total_profit = sum(
        float(s["profit"]) for s in settled if s["profit"] is not None
    )
    count = len(settled)
    roi = (total_profit / count * 100) if count > 0 else 0.0
    return {
        "total": count,
        "wins": len(wins),
        "losses": len(losses),
        "profit": round(total_profit, 2),
        "roi": round(roi, 1),
    }


def _format_settled_for_llm(
    settled: list[dict[str, Any]], summary: dict[str, Any]
) -> str:
    """Format settled predictions into a prompt for the LLM."""
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [
        f"Дата: {now_str}",
        f"Итого: {summary['total']} ставок, {summary['wins']} побед, "
        f"{summary['losses']} поражений",
        f"Profit: {summary['profit']:+.2f} units, ROI: {summary['roi']:.1f}%",
        "",
        "Ставки:",
    ]
    for s in settled:
        odd = float(s["initial_odd"]) if s["initial_odd"] is not None else 0.0
        profit = float(s["profit"]) if s["profit"] is not None else 0.0
        score = ""
        if s["home_goals"] is not None and s["away_goals"] is not None:
            score = f" ({s['home_goals']}:{s['away_goals']})"
        lines.append(
            f"- {s['league']} | {s['home_team']} vs {s['away_team']}{score} | "
            f"{s['market']}: {s['selection']} @ {odd:.2f} → {s['status']}, "
            f"profit={profit:+.2f}"
        )
    return "\n".join(lines)


def _format_settled_simple(
    settled: list[dict[str, Any]], summary: dict[str, Any]
) -> str:
    """Format settled predictions as a simple text report (fallback without LLM)."""
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [
        f"📊 <b>Дневной отчёт {now_str}</b>",
        "",
        f"Итого: {summary['total']} ставок, {summary['wins']} ✅, "
        f"{summary['losses']} ❌",
        f"Profit: {summary['profit']:+.2f} units | "
        f"ROI: {summary['roi']:.1f}%",
        "",
    ]

    wins = [s for s in settled if s["status"] == "WON"]
    losses = [s for s in settled if s["status"] == "LOSS"]

    if wins:
        lines.append("✅ <b>Выигранные:</b>")
        for s in wins:
            odd = float(s["initial_odd"]) if s["initial_odd"] else 0.0
            lines.append(
                f"• {s['home_team']} vs {s['away_team']} | "
                f"{s['market']}: {s['selection']} @ {odd:.2f}"
            )
        lines.append("")

    if losses:
        lines.append("❌ <b>Проигранные:</b>")
        for s in losses:
            odd = float(s["initial_odd"]) if s["initial_odd"] else 0.0
            lines.append(
                f"• {s['home_team']} vs {s['away_team']} | "
                f"{s['market']}: {s['selection']} @ {odd:.2f}"
            )

    return "\n".join(lines)


async def run(session: AsyncSession) -> dict[str, Any]:
    """Execute the analyst agent: fetch settled → AI report → Telegram.

    Returns summary dict with status and stats.
    """
    log.info("analyst_run_start")

    settled = await fetch_settled_24h(session)

    if not settled:
        log.info("analyst_no_settled — skipping")
        return {"status": "skipped", "reason": "no settled predictions"}

    summary = _build_summary(settled)
    log.info(
        "analyst_settled total=%d wins=%d losses=%d profit=%.2f",
        summary["total"], summary["wins"], summary["losses"], summary["profit"],
    )

    # Try AI report via Groq
    report = None
    if settings.groq_enabled and settings.groq_api_key:
        try:
            prompt = _format_settled_for_llm(settled, summary)
            report = await generate_text(
                session,
                prompt,
                system_prompt=ANALYST_SYSTEM_PROMPT,
                temperature=0.4,
                max_tokens=800,
                use_cache=False,
            )
        except Exception:
            log.exception("analyst_groq_failed, using simple format")

    # Fallback to simple format
    if not report:
        report = _format_settled_simple(settled, summary)

    # Send Telegram report
    sent = await send_to_owner(report)

    # Save to DB
    await save_report(
        session,
        agent="analyst",
        report_type="daily_report",
        report_text=report,
        metadata=summary,
        telegram_sent=sent,
    )

    return {"status": "sent", "bets": len(settled), **summary}
