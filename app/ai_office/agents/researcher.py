"""Researcher agent — weekly research via Gemini web search."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logger import get_logger
from app.data.providers.gemini import generate_with_search

from ..config import RESEARCHER_SYSTEM_PROMPT, RESEARCH_KEYWORDS
from ..queries import save_report
from .monitor import send_to_owner

log = get_logger("ai_office.researcher")


def _build_prompt(keywords: list[str]) -> str:
    """Build the research prompt from keywords."""
    now = datetime.now(timezone.utc)
    week_num = now.isocalendar()[1]
    year = now.year

    lines = [
        f"Неделя: {year}-W{week_num:02d}",
        f"Дата: {now.strftime('%Y-%m-%d')}",
        "",
        "Поиск по ключевым словам:",
    ]
    for i, kw in enumerate(keywords, 1):
        lines.append(f"{i}. {kw}")

    lines.append("")
    lines.append(
        "Найди свежие публикации, статьи, исследования или блог-посты "
        "по указанным темам. Для каждой находки укажи источник/URL."
    )
    return "\n".join(lines)


async def run(session: AsyncSession) -> dict[str, Any]:
    """Run weekly research agent.

    Uses Gemini with web search to find recent publications
    on football prediction topics.
    """
    if not settings.gemini_api_key:
        log.warning("researcher_skipped no GEMINI_API_KEY configured")
        return {"status": "skipped", "reason": "GEMINI_API_KEY not configured"}

    now = datetime.now(timezone.utc)
    week_num = now.isocalendar()[1]
    year = now.year
    week_label = f"{year}-W{week_num:02d}"

    log.info("researcher_start week=%s", week_label)

    prompt = _build_prompt(RESEARCH_KEYWORDS)

    try:
        raw_report = await generate_with_search(
            session,
            prompt,
            system_prompt=RESEARCHER_SYSTEM_PROMPT,
            temperature=0.5,
            max_tokens=2048,
            use_cache=False,
        )
    except Exception as exc:
        log.exception("researcher_gemini_error")
        return {"status": "error", "reason": str(exc)}

    if not raw_report or not raw_report.strip():
        log.warning("researcher_empty_response")
        return {"status": "skipped", "reason": "empty Gemini response"}

    report_text = raw_report.strip()
    log.info("researcher_report_generated len=%d", len(report_text))

    # Send to Telegram (send_to_owner auto-splits long messages)
    header = f"📚 <b>Weekly Research — {week_label}</b>\n\n"
    sent = await send_to_owner(header + report_text)

    # Save report once (after telegram attempt)
    await save_report(
        session,
        agent="researcher",
        report_type="weekly_research",
        report_text=report_text,
        metadata={"week": week_label, "keywords_count": len(RESEARCH_KEYWORDS)},
        telegram_sent=sent,
    )

    log.info("researcher_done week=%s sent=%s", week_label, sent)
    return {"status": "sent" if sent else "done", "week": week_label}
