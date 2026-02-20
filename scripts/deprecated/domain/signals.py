"""Deprecated module (legacy signal rules).

Kept for reference by `scripts/deprecated/backtest_csv_skeleton.py`.
Not used by the production pipeline.
"""

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class SignalResult:
    picks: List[str]
    confidence: float
    summary: str


def build_signals(
    home: str,
    away: str,
    fatigue_home: float,
    fatigue_away: float,
    chaos_match: float,
    fav_side: Optional[str] = None,  # "home"/"away"/None
) -> Optional[SignalResult]:
    if fav_side not in ("home", "away"):
        fav_side = "home"

    fav_fatigue = fatigue_home if fav_side == "home" else fatigue_away
    dog_fatigue = fatigue_away if fav_side == "home" else fatigue_home
    fatigue_diff_fav_vs_dog = fav_fatigue - dog_fatigue

    if fav_fatigue >= 60 and fatigue_diff_fav_vs_dog >= 20 and chaos_match >= 55:
        if fav_side == "home":
            picks = ["X2", f"{away} +1.5"]
        else:
            picks = ["1X", f"{home} +1.5"]
        return SignalResult(
            picks=picks,
            confidence=0.62,
            summary=(
                f"Фаворит перегружен ({fav_fatigue:.0f}/100) при высокой хаотичности матча "
                f"({chaos_match:.0f}/100). Риск апсета выше рынка."
            ),
        )

    if fav_fatigue <= 35 and chaos_match <= 40:
        picks = [f"{home} win" if fav_side == "home" else f"{away} win", "Under 3.5"]
        return SignalResult(
            picks=picks,
            confidence=0.60,
            summary=(
                f"Фаворит свежий ({fav_fatigue:.0f}/100), матч низкохаотичный "
                f"({chaos_match:.0f}/100). Сценарий под контролем фаворита."
            ),
        )

    return None
