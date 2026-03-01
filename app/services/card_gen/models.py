"""Card Gen v2 — Data models.

Structured dataclasses that replace the freeform ``text`` parameter
of the legacy ``render_headline_image_html()`` function.
Every field from the legacy signature is covered here so that
no information is lost during migration.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TeamInfo:
    """Team data for one side of a match card."""

    name: str
    logo_bytes: bytes | None = None
    rank: int | None = None
    points: int | None = None
    played: int | None = None
    goal_diff: int | None = None
    form: str | None = None  # e.g. "WWDLW", max 8 chars


@dataclass
class PredictionCardData:
    """All data needed to render a pre-match prediction card.

    Maps 1-to-1 with legacy ``render_headline_image_html()`` parameters
    — see ``MIGRATION_NOTES.md`` section 1 for the full mapping.
    """

    card_type: str = "prediction"
    theme: str = "pro"  # "pro" | "viral"
    width: int = 1280   # CSS width (min 800, retina 2x → 2560px real)

    # Teams ----------------------------------------------------------------
    home: TeamInfo = field(default_factory=lambda: TeamInfo(name="HOME"))
    away: TeamInfo = field(default_factory=lambda: TeamInfo(name="AWAY"))

    # League / match context -----------------------------------------------
    league: str | None = None
    league_logo_bytes: bytes | None = None
    league_country: str | None = None
    league_round: str | None = None
    venue_name: str | None = None
    venue_city: str | None = None
    date_line: str | None = None  # e.g. "27 Feb 2026, 15:00 UTC"

    # Prediction / market --------------------------------------------------
    title: str | None = None          # e.g. "HOT PREDICTION"
    market: str | None = None         # "1X2", "TOTAL", etc.
    market_label: str | None = None   # human-readable market name
    bet_label: str | None = None      # recommendation heading
    pick: str | None = None           # "HOME_WIN", "OVER_2_5", etc.
    pick_display: str | None = None   # human-readable pick text shown on card
    odd: float | None = None
    confidence: float | None = None
    ev: float | None = None

    # Probabilities --------------------------------------------------------
    home_win_prob: float | None = None
    draw_prob: float | None = None
    away_win_prob: float | None = None

    # Signal / value indicators (top-left card) ----------------------------
    signal_title: str | None = None        # e.g. "VALUE INDICATORS"
    signal_lines: list[str] = field(default_factory=list)  # up to 3 lines

    # Legacy compat: raw text (used only by adapter from publishing.py) ----
    raw_text: str | None = None


@dataclass
class ResultCardData:
    """All data needed to render a post-match result card."""

    card_type: str = "result"
    theme: str = "pro"
    width: int = 1280

    # Teams ----------------------------------------------------------------
    home: TeamInfo = field(default_factory=lambda: TeamInfo(name="HOME"))
    away: TeamInfo = field(default_factory=lambda: TeamInfo(name="AWAY"))

    # League / match context -----------------------------------------------
    league: str | None = None
    league_logo_bytes: bytes | None = None
    league_country: str | None = None
    league_round: str | None = None
    venue_name: str | None = None
    venue_city: str | None = None
    date_line: str | None = None

    # Result ---------------------------------------------------------------
    home_goals: int = 0
    away_goals: int = 0

    # Original prediction that was made ------------------------------------
    market: str | None = None
    market_label: str | None = None
    pick: str | None = None
    pick_display: str | None = None
    odd: float | None = None

    # Outcome --------------------------------------------------------------
    status: str = "WIN"   # "WIN" | "LOSS"
    profit: float = 0.0   # e.g. +0.85 or -1.00 (in units)
