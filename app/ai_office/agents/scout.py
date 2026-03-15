"""Scout agent — contextual match analysis via Gemini with web search."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logger import get_logger
from app.data.providers.gemini import generate_with_search

from ..config import SCOUT_SYSTEM_PROMPT
from ..queries import fetch_scout_matches, save_scout_report, save_report
from .monitor import send_to_owner

log = get_logger("ai_office.scout")


def _format_matches_for_scout(matches: list[dict[str, Any]]) -> str:
    """Format upcoming predictions into a prompt for Gemini scout."""
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [
        f"Дата: {now_str}",
        f"Количество матчей: {len(matches)}",
        "",
        "Матчи для анализа:",
        "",
    ]
    for i, m in enumerate(matches, 1):
        odd = float(m["odd"]) if m["odd"] is not None else 0.0
        conf = float(m["confidence"]) if m["confidence"] is not None else 0.0
        kickoff = m["kickoff"]
        time_str = kickoff.strftime("%H:%M") if kickoff else "?"
        lines.append(
            f"{i}. [fixture_id={m['fixture_id']}] {m['league']} | {time_str} UTC"
        )
        lines.append(
            f"   {m['home_team']} — {m['away_team']} | "
            f"Ставка: {m['selection']} @ {odd:.2f} | Confidence: {conf:.2f}"
        )
        lines.append("")

    return "\n".join(lines)


def _parse_verdicts(
    raw: str, matches: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Parse JSON verdicts from Gemini response.

    Returns list of dicts with fixture_id, verdict, report, factors,
    plus match info (home_team, away_team, league, selection, odd).

    Fallback: if JSON is invalid, assign GREEN to all matches.
    """
    # Build lookup by fixture_id
    match_map = {m["fixture_id"]: m for m in matches}
    valid_ids = set(match_map.keys())

    # Try to extract JSON from response (may have markdown fences)
    cleaned = raw.strip()
    # Remove markdown code fences if present
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    cleaned = cleaned.strip()

    parsed = []
    try:
        data = json.loads(cleaned)
        if not isinstance(data, list):
            raise ValueError("Expected JSON array")

        for item in data:
            fid = item.get("fixture_id")
            if fid not in valid_ids:
                continue

            verdict = str(item.get("verdict", "green")).lower().strip()
            if verdict not in ("green", "yellow", "red"):
                verdict = "green"

            report = str(item.get("report", ""))
            factors = item.get("factors", {})
            if not isinstance(factors, dict):
                factors = {}

            m = match_map[fid]
            parsed.append({
                "fixture_id": fid,
                "prediction_id": m.get("prediction_id"),
                "verdict": verdict,
                "report": report,
                "factors": factors,
                "home_team": m["home_team"],
                "away_team": m["away_team"],
                "league": m["league"],
                "selection": m["selection"],
                "odd": float(m["odd"]) if m["odd"] is not None else 0.0,
            })

    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        log.warning("scout_parse_failed err=%s, defaulting all GREEN", exc)
        # Fallback: all GREEN
        for m in matches:
            parsed.append({
                "fixture_id": m["fixture_id"],
                "prediction_id": m.get("prediction_id"),
                "verdict": "green",
                "report": "Автоматический вердикт (ошибка парсинга Gemini)",
                "factors": {},
                "home_team": m["home_team"],
                "away_team": m["away_team"],
                "league": m["league"],
                "selection": m["selection"],
                "odd": float(m["odd"]) if m["odd"] is not None else 0.0,
            })

    # Add any matches that Gemini didn't mention — default GREEN
    seen_ids = {v["fixture_id"] for v in parsed}
    for m in matches:
        if m["fixture_id"] not in seen_ids:
            parsed.append({
                "fixture_id": m["fixture_id"],
                "prediction_id": m.get("prediction_id"),
                "verdict": "green",
                "report": "Не проанализирован скаутом — дефолт GREEN",
                "factors": {},
                "home_team": m["home_team"],
                "away_team": m["away_team"],
                "league": m["league"],
                "selection": m["selection"],
                "odd": float(m["odd"]) if m["odd"] is not None else 0.0,
            })

    return parsed


_VERDICT_EMOJI = {"green": "🟢", "yellow": "🟡", "red": "🔴"}
_MOTIVATION_MAP = {-1: "-1", 0: "0", 1: "+1"}


def _format_scout_telegram(verdicts: list[dict[str, Any]]) -> str:
    """Format scout verdicts into a Telegram-friendly report."""
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"🔍 <b>Скаут-отчёт</b> {now_str}", ""]

    # Group by league
    leagues: dict[str, list] = {}
    for v in verdicts:
        league = v.get("league", "Unknown")
        leagues.setdefault(league, []).append(v)

    for league, items in leagues.items():
        lines.append(f"🏆 <b>{league}</b>")
        for v in items:
            emoji = _VERDICT_EMOJI.get(v["verdict"], "⚪")
            lines.append(
                f"{v['home_team']} — {v['away_team']} | "
                f"{v['selection']} @ {v['odd']:.2f}"
            )
            lines.append(f"{emoji} {v['report']}")

            factors = v.get("factors", {})
            if factors:
                inj = "да" if factors.get("injuries") else "нет"
                mot = _MOTIVATION_MAP.get(factors.get("motivation", 0), "0")
                derby = "да" if factors.get("derby") else "нет"
                parts = [f"травмы={inj}", f"мотивация={mot}", f"дерби={derby}"]
                if factors.get("manager_change"):
                    parts.append("тренер=новый")
                if factors.get("congested"):
                    parts.append("график=плотный")
                lines.append(f"Факторы: {', '.join(parts)}")
            lines.append("")

    # Summary
    greens = sum(1 for v in verdicts if v["verdict"] == "green")
    yellows = sum(1 for v in verdicts if v["verdict"] == "yellow")
    reds = sum(1 for v in verdicts if v["verdict"] == "red")
    lines.append(f"Итого: {greens} 🟢, {yellows} 🟡, {reds} 🔴")

    return "\n".join(lines)


async def run(session: AsyncSession) -> dict[str, Any]:
    """Execute the scout agent: fetch matches → Gemini search → verdicts → DB + Telegram.

    Returns summary dict with status and counts.
    """
    log.info("scout_run_start")

    matches = await fetch_scout_matches(session)

    if not matches:
        log.info("scout_no_matches — skipping")
        return {"status": "skipped", "reason": "no upcoming matches"}

    log.info("scout_matches found=%d", len(matches))

    # Check Gemini API key
    if not settings.gemini_api_key:
        log.warning("scout_no_gemini_key — skipping")
        return {"status": "skipped", "reason": "GEMINI_API_KEY not configured"}

    # Build prompt and call Gemini with web search
    prompt = _format_matches_for_scout(matches)

    try:
        raw = await generate_with_search(
            session,
            prompt,
            system_prompt=SCOUT_SYSTEM_PROMPT,
            temperature=0.3,
            max_tokens=2048,
            use_cache=False,
        )
    except Exception:
        log.exception("scout_gemini_failed")
        return {"status": "error", "reason": "Gemini API call failed"}

    # Parse verdicts
    verdicts = _parse_verdicts(raw, matches)
    log.info(
        "scout_verdicts parsed=%d greens=%d yellows=%d reds=%d",
        len(verdicts),
        sum(1 for v in verdicts if v["verdict"] == "green"),
        sum(1 for v in verdicts if v["verdict"] == "yellow"),
        sum(1 for v in verdicts if v["verdict"] == "red"),
    )

    # Save each verdict to scout_reports
    for v in verdicts:
        try:
            await save_scout_report(
                session,
                fixture_id=v["fixture_id"],
                prediction_id=v.get("prediction_id"),
                verdict=v["verdict"],
                report_text=v["report"],
                factors=v.get("factors"),
                model_selection=v.get("selection"),
                model_odd=v.get("odd"),
            )
        except Exception:
            log.exception("scout_save_failed fixture=%s", v["fixture_id"])

    # Format and send Telegram report
    report = _format_scout_telegram(verdicts)
    sent = await send_to_owner(report)

    # Save overall report to ai_office_reports
    reds = [v for v in verdicts if v["verdict"] == "red"]
    await save_report(
        session,
        agent="scout",
        report_type="scout_report",
        report_text=report,
        metadata={
            "matches": len(matches),
            "verdicts": len(verdicts),
            "greens": sum(1 for v in verdicts if v["verdict"] == "green"),
            "yellows": sum(1 for v in verdicts if v["verdict"] == "yellow"),
            "reds": len(reds),
        },
        telegram_sent=sent,
    )

    return {
        "status": "sent",
        "matches": len(matches),
        "verdicts": len(verdicts),
        "reds": len(reds),
    }
