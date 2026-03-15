"""Content writer agent (Role A) — daily picks Telegram post."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logger import get_logger
from app.data.providers.groq import generate_text

from ..config import CONTENT_PICKS_SYSTEM_PROMPT
from ..queries import (
    fetch_upcoming_picks,
    fetch_upcoming_totals,
    fetch_red_fixture_ids,
    save_report,
)
from .monitor import send_to_owner

log = get_logger("ai_office.content")


def _confidence_label(conf: float | None) -> str:
    """Map confidence to H/M/L label."""
    if conf is None:
        return "?"
    if conf > 0.55:
        return "H"
    if conf > 0.45:
        return "M"
    return "L"


def _calc_ev(confidence: float | None, odd: float | None) -> float | None:
    """Calculate EV percentage: (confidence * odd - 1) * 100."""
    if confidence is None or odd is None:
        return None
    ev = (confidence * odd - 1.0) * 100.0
    return round(ev, 1)


def _format_picks_for_llm(
    picks_1x2: list[dict[str, Any]],
    picks_totals: list[dict[str, Any]],
) -> str:
    """Format upcoming picks into a prompt for the LLM."""
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [f"Дата: {now_str}", ""]

    if picks_1x2:
        lines.append("Прогнозы 1X2:")
        for p in picks_1x2:
            odd = float(p["initial_odd"]) if p["initial_odd"] is not None else 0.0
            conf = float(p["confidence"]) if p["confidence"] is not None else None
            ev = _calc_ev(conf, odd)
            ev_str = f"{ev:.1f}%" if ev is not None else "N/A"
            kickoff = p["kickoff"]
            time_str = kickoff.strftime("%H:%M") if kickoff else "?"
            lines.append(
                f"- {p['league']} | {time_str} UTC "
                f"{p['home_team']} — {p['away_team']} | "
                f"Pick: {p['selection']} @ {odd:.2f} | "
                f"EV: {ev_str} | Confidence: {_confidence_label(conf)}"
            )
        lines.append("")

    if picks_totals:
        lines.append("Прогнозы по тоталам/BTTS:")
        for p in picks_totals:
            odd = float(p["initial_odd"]) if p["initial_odd"] is not None else 0.0
            conf = float(p["confidence"]) if p["confidence"] is not None else None
            ev = _calc_ev(conf, odd)
            ev_str = f"{ev:.1f}%" if ev is not None else "N/A"
            kickoff = p["kickoff"]
            time_str = kickoff.strftime("%H:%M") if kickoff else "?"
            lines.append(
                f"- {p['league']} | {time_str} UTC "
                f"{p['home_team']} — {p['away_team']} | "
                f"{p['market']} {p['selection']} @ {odd:.2f} | "
                f"EV: {ev_str} | Confidence: {_confidence_label(conf)}"
            )

    return "\n".join(lines)


def _format_picks_simple(
    picks_1x2: list[dict[str, Any]],
    picks_totals: list[dict[str, Any]],
) -> str:
    """Format picks as a simple text post (fallback without LLM)."""
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [f"⚽ <b>Прогнозы на {now_str}</b>", ""]

    # Group by league
    all_picks = []
    for p in picks_1x2:
        all_picks.append({**p, "_type": "1X2"})
    for p in picks_totals:
        all_picks.append({**p, "_type": "total"})

    leagues: dict[str, list] = {}
    for p in all_picks:
        league = p.get("league", "Unknown")
        leagues.setdefault(league, []).append(p)

    for league, picks in leagues.items():
        lines.append(f"🏆 <b>{league}</b>")
        for p in picks:
            odd = float(p["initial_odd"]) if p["initial_odd"] is not None else 0.0
            conf = float(p["confidence"]) if p["confidence"] is not None else None
            kickoff = p["kickoff"]
            time_str = kickoff.strftime("%H:%M") if kickoff else "?"
            ev = _calc_ev(conf, odd)
            ev_str = f"{ev:.1f}%" if ev is not None else "N/A"

            if p["_type"] == "1X2":
                sel = p["selection"]
            else:
                sel = f"{p['market']} {p['selection']}"

            lines.append(
                f"{time_str} {p['home_team']} — {p['away_team']}\n"
                f"📌 {sel} @ {odd:.2f} | "
                f"EV: {ev_str} | {_confidence_label(conf)}"
            )
        lines.append("")

    lines.append(
        "⚠️ Прогнозы основаны на математической модели. "
        "Ставки — ваша ответственность."
    )
    return "\n".join(lines)


async def run(session: AsyncSession) -> dict[str, Any]:
    """Execute the content agent: fetch upcoming picks → AI post → Telegram.

    Returns summary dict with status and pick count.
    """
    log.info("content_run_start")

    picks_1x2 = await fetch_upcoming_picks(session)
    picks_totals = await fetch_upcoming_totals(session)

    # Filter out fixtures with RED scout verdicts (not overridden)
    red_ids = await fetch_red_fixture_ids(session)
    filtered_count = 0
    if red_ids:
        before_1x2 = len(picks_1x2)
        before_totals = len(picks_totals)
        picks_1x2 = [p for p in picks_1x2 if p["fixture_id"] not in red_ids]
        picks_totals = [p for p in picks_totals if p["fixture_id"] not in red_ids]
        filtered_count = (before_1x2 - len(picks_1x2)) + (before_totals - len(picks_totals))
        if filtered_count:
            log.info("content_filtered_red count=%d fixture_ids=%s",
                     filtered_count, red_ids)

    total_count = len(picks_1x2) + len(picks_totals)

    if total_count == 0:
        log.info("content_no_picks — skipping")
        reason = "no upcoming picks"
        if filtered_count:
            reason += f" ({filtered_count} filtered by scout RED)"
        return {"status": "skipped", "reason": reason}

    log.info("content_picks found=%d (1x2=%d, totals=%d, filtered_red=%d)",
             total_count, len(picks_1x2), len(picks_totals), filtered_count)

    # Try AI post via Groq
    post = None
    if settings.groq_enabled and settings.groq_api_key:
        try:
            prompt = _format_picks_for_llm(picks_1x2, picks_totals)
            post = await generate_text(
                session,
                prompt,
                system_prompt=CONTENT_PICKS_SYSTEM_PROMPT,
                temperature=0.5,
                max_tokens=1200,
                use_cache=False,
            )
        except Exception:
            log.exception("content_groq_failed, using simple format")

    # Fallback to simple format
    if not post:
        post = _format_picks_simple(picks_1x2, picks_totals)

    # Send Telegram post
    sent = await send_to_owner(post)

    # Save to DB
    await save_report(
        session,
        agent="content",
        report_type="daily_picks",
        report_text=post,
        metadata={"picks_1x2": len(picks_1x2), "picks_totals": len(picks_totals)},
        telegram_sent=sent,
    )

    return {"status": "sent", "picks": total_count}
