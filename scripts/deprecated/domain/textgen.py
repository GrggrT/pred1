"""Deprecated module (legacy text generation).

Kept for reference by legacy scripts; not used by the production pipeline.
"""

from datetime import datetime
from typing import List, Optional


def build_prediction_text(
    kickoff: datetime,
    home: str,
    away: str,
    picks: List[str],
    summary: str,
    fatigue_home: float,
    fatigue_away: float,
    chaos_match: float,
    odd: Optional[float] = None,
) -> str:
    date_str = kickoff.strftime("%d.%m.%Y %H:%M")
    main_pick = picks[0]

    coef_str = f"{odd:.2f}" if odd else "—"

    lines = [
        f"**{date_str}**",
        f"**{home} – {away}**",
        f"**Прогноз:** {main_pick}",
        f"**Коэффициент:** {coef_str}",
        "**Аналитика:**",
        f"- Усталость: {home} {fatigue_home:.0f}/100 vs {away} {fatigue_away:.0f}/100.",
        f"- Хаос матча: {chaos_match:.0f}/100.",
        f"- {summary}",
        f"**Ставка:** {main_pick}",
    ]
    return "\n".join(lines)
