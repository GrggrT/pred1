"""Card Gen v2 — Legacy compatibility adapter.

Converts the arguments used in ``publishing.py`` call sites
into a :class:`PredictionCardData` instance for the v2 renderer.

This module exists solely to bridge the gap between the legacy
``render_headline_image_html(text, **kwargs)`` interface and the
new structured ``render_card(PredictionCardData(...))`` API.

Usage::

    from app.services.card_gen.compat import build_prediction_card

    card = build_prediction_card(
        fixture=fixture,
        image_visual_context=ctx,
        image_text=headline,
        html_image_kwargs=kwargs,
        home_win_prob=0.45,
        draw_prob=0.28,
        away_win_prob=0.27,
        indicator_title="VALUE INDICATORS",
        indicator_lines=["Line 1", "Line 2"],
    )
"""

from __future__ import annotations

import re
from typing import Any

from .models import PredictionCardData, TeamInfo


def _extract_title_from_text(image_text: str) -> str:
    """Extract the card title (first non-empty line) from legacy image text."""
    for line in (image_text or "").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return "HOT PREDICTION"


def _extract_pick_from_text(image_text: str, bet_label: str | None) -> str:
    """Extract the main pick/recommendation line from legacy image text.

    Looks for lines after the bet_label heading, or falls back to the last
    meaningful line.
    """
    lines = [ln.strip() for ln in (image_text or "").splitlines() if ln.strip()]
    if not lines:
        return "BET"

    # Try to find lines after bet_label
    bet = (bet_label or "").strip().lower()
    if bet:
        for i, ln in enumerate(lines):
            if ln.lower() == bet and i + 1 < len(lines):
                return lines[i + 1]

    # Fallback: find recommendation-like headings
    for i, ln in enumerate(lines):
        if ln.upper() in {"BET OF THE DAY", "RECOMMENDATION", "РЕКОМЕНДАЦИЯ"}:
            if i + 1 < len(lines):
                return lines[i + 1]

    # Last resort: return the last line that isn't a date or title
    if len(lines) > 2:
        return lines[-1]
    return lines[-1] if lines else "BET"


def _extract_odd_from_text(image_text: str) -> float | None:
    """Extract odds value from legacy text (looks for @ prefix or standalone number)."""
    for line in (image_text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("@"):
            m = re.search(r"\d+(?:[.,]\d+)?", stripped)
            if m:
                return float(m.group(0).replace(",", "."))
        # Also check lines that look like standalone odds
        m = re.match(r"^\s*@?\s*(\d+(?:[.,]\d+)?)\s*$", stripped)
        if m:
            return float(m.group(1).replace(",", "."))
    return None


def build_prediction_card(
    *,
    fixture: Any,
    image_visual_context: Any,
    image_text: str,
    html_image_kwargs: dict[str, Any],
    home_win_prob: float | None = None,
    draw_prob: float | None = None,
    away_win_prob: float | None = None,
    indicator_title: str | None = None,
    indicator_lines: list[str | None] | None = None,
) -> PredictionCardData:
    """Build a :class:`PredictionCardData` from legacy publishing.py arguments.

    Parameters
    ----------
    fixture:
        The fixture ORM object (has ``home_name``, ``away_name``, ``league_name``,
        ``kickoff``, etc.).
    image_visual_context:
        An ``ImageVisualContext`` instance with standings, form, venue data.
    image_text:
        The headline text that was passed to legacy ``render_headline_image_html()``.
    html_image_kwargs:
        The keyword arguments dict that was unpacked into the legacy function call.
    home_win_prob, draw_prob, away_win_prob:
        1X2 probabilities (0.0–1.0).
    indicator_title:
        Signal block title (e.g. "VALUE INDICATORS").
    indicator_lines:
        List of signal metric lines (up to 3).
    """
    # Team names from fixture (structured, reliable)
    home_name = str(getattr(fixture, "home_name", "") or "HOME")
    away_name = str(getattr(fixture, "away_name", "") or "AWAY")

    # Logo bytes from kwargs
    home_logo = html_image_kwargs.get("home_logo")
    away_logo = html_image_kwargs.get("away_logo")
    league_logo = html_image_kwargs.get("league_logo")

    # Theme
    theme = html_image_kwargs.get("style_variant", "pro")

    # League
    league = html_image_kwargs.get("league_label") or str(
        getattr(fixture, "league_name", "") or ""
    )

    # Market / bet
    market_label = html_image_kwargs.get("market_label")
    bet_label = html_image_kwargs.get("bet_label")

    # Title from text (first line)
    title = _extract_title_from_text(image_text)

    # Pick from text
    pick_display = _extract_pick_from_text(image_text, bet_label)

    # Odd — try to parse from text, or from prediction data
    odd = _extract_odd_from_text(image_text)

    # Date from kickoff
    kickoff = getattr(fixture, "kickoff", None)
    date_line = ""
    if kickoff:
        try:
            date_line = kickoff.strftime("%d %b %Y, %H:%M UTC")
        except Exception:
            date_line = str(kickoff)

    # Signal lines — filter Nones
    signal_lines = [
        str(ln) for ln in (indicator_lines or []) if ln is not None and str(ln).strip()
    ]

    # Build visual context fields
    ivc = image_visual_context

    return PredictionCardData(
        theme=theme,
        # Teams
        home=TeamInfo(
            name=home_name,
            logo_bytes=home_logo,
            rank=getattr(ivc, "home_rank", None),
            points=getattr(ivc, "home_points", None),
            played=getattr(ivc, "home_played", None),
            goal_diff=getattr(ivc, "home_goal_diff", None),
            form=getattr(ivc, "home_form", None),
        ),
        away=TeamInfo(
            name=away_name,
            logo_bytes=away_logo,
            rank=getattr(ivc, "away_rank", None),
            points=getattr(ivc, "away_points", None),
            played=getattr(ivc, "away_played", None),
            goal_diff=getattr(ivc, "away_goal_diff", None),
            form=getattr(ivc, "away_form", None),
        ),
        # League / match context
        league=league,
        league_logo_bytes=league_logo,
        league_country=getattr(ivc, "league_country", None),
        league_round=getattr(ivc, "league_round", None),
        venue_name=getattr(ivc, "venue_name", None),
        venue_city=getattr(ivc, "venue_city", None),
        date_line=date_line,
        # Prediction
        title=title,
        market=market_label,
        market_label=market_label,
        bet_label=bet_label,
        pick_display=pick_display,
        odd=odd,
        # Probabilities
        home_win_prob=home_win_prob,
        draw_prob=draw_prob,
        away_win_prob=away_win_prob,
        # Signal
        signal_title=indicator_title,
        signal_lines=signal_lines,
        # Legacy compat
        raw_text=image_text,
    )
