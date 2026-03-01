"""Card Gen v2 — Modular image generation for Telegram publication.

Public API
----------
- ``render_card(card_data)`` — main entry point (async, returns JPEG bytes).
- ``PredictionCardData`` / ``ResultCardData`` / ``TeamInfo`` — input models.

Usage::

    from app.services.card_gen import render_card, PredictionCardData, TeamInfo

    card = PredictionCardData(
        home=TeamInfo(name="Arsenal"),
        away=TeamInfo(name="Chelsea"),
        odd=2.75,
        pick_display="Total Under 2.5",
    )
    jpeg_bytes = await render_card(card)
"""

from __future__ import annotations

from .models import PredictionCardData, ResultCardData, TeamInfo
from .renderer import render_card

__all__ = [
    "render_card",
    "PredictionCardData",
    "ResultCardData",
    "TeamInfo",
]
