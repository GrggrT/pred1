"""Deprecated module (legacy fatigue/chaos features).

Kept for reference by `scripts/deprecated/backtest_csv_skeleton.py`.
Not used by the production pipeline.
"""

from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
import numpy as np


@dataclass
class PastMatch:
    kickoff: datetime
    is_home: bool
    goals_for: int
    goals_against: int
    yellow: int
    red: int
    went_overtime: bool = False
    late_goal_for: bool = False
    late_goal_against: bool = False


@dataclass
class FatigueFeatures:
    games14: int
    rest_days: int
    starter_minutes7: int
    red_last7: int
    overtime14: int
    consecutive_away: int  # 0/1


def compute_fatigue_features(past: List[PastMatch], now: datetime) -> FatigueFeatures:
    last14 = [m for m in past if m.kickoff >= now - timedelta(days=14)]
    last7 = [m for m in past if m.kickoff >= now - timedelta(days=7)]

    games14 = len(last14)
    red_last7 = sum(m.red for m in last7)
    overtime14 = sum(1 for m in last14 if m.went_overtime)

    rest_days = 7
    if past:
        last_game = max(past, key=lambda x: x.kickoff)
        rest_days = max(0, (now - last_game.kickoff).days)

    past_sorted = sorted(past, key=lambda x: x.kickoff, reverse=True)
    away_streak = 0
    for match in past_sorted:
        if not match.is_home:
            away_streak += 1
        else:
            break
    consecutive_away = 1 if away_streak >= 2 else 0

    starter_minutes7 = 0

    return FatigueFeatures(
        games14=games14,
        rest_days=rest_days,
        starter_minutes7=starter_minutes7,
        red_last7=red_last7,
        overtime14=overtime14,
        consecutive_away=consecutive_away,
    )


def compute_fatigue_raw(feat: FatigueFeatures, lineup_minutes7: Optional[int] = None) -> float:
    starter_minutes7 = lineup_minutes7 if lineup_minutes7 is not None else feat.games14 * 85

    fatigue_raw = (
        12 * feat.games14
        + 0.015 * starter_minutes7
        + 6 * feat.red_last7
        + 8 * feat.overtime14
        - 5 * feat.rest_days
        + 5 * feat.consecutive_away
    )
    return float(fatigue_raw)


def rolling_percentile_normalize(raw_by_team: Dict[int, List[float]], current_raw: Dict[int, float]) -> Dict[int, float]:
    normalized = {}
    for team_id, raw in current_raw.items():
        hist = raw_by_team.get(team_id, [])
        dist = hist + [raw]
        if len(dist) < 5:
            normalized[team_id] = float(np.clip(raw, 0, 100))
            continue
        arr = np.array(dist)
        rank = (arr < raw).mean()
        normalized[team_id] = float(np.clip(rank * 100, 0, 100))
    return normalized


@dataclass
class ChaosFeatures:
    avg_for: float
    avg_against: float
    var_total: float
    late_rate: float
    cards_pg: float


def compute_chaos_features(past10: List[PastMatch]) -> ChaosFeatures:
    if not past10:
        return ChaosFeatures(0, 0, 0, 0, 0)

    gf = np.array([m.goals_for for m in past10], dtype=float)
    ga = np.array([m.goals_against for m in past10], dtype=float)
    total = gf + ga

    avg_for = gf.mean()
    avg_against = ga.mean()
    var_total = float(total.var(ddof=0))
    late_events = sum(1 for m in past10 if (m.late_goal_for or m.late_goal_against))
    late_rate = late_events / len(past10)
    cards_pg = sum(m.yellow + m.red for m in past10) / len(past10)

    return ChaosFeatures(avg_for, avg_against, var_total, late_rate, cards_pg)


def compute_chaos_raw(feat: ChaosFeatures) -> float:
    return float(
        20 * (feat.avg_for + feat.avg_against)
        + 15 * feat.var_total
        + 25 * feat.late_rate
        + 10 * feat.cards_pg
    )


def normalize_minmax(raw_by_team: Dict[int, float]) -> Dict[int, float]:
    vals = list(raw_by_team.values())
    if not vals:
        return {}
    mn, mx = min(vals), max(vals)
    if mx - mn < 1e-6:
        return {k: 50.0 for k in raw_by_team}
    return {k: float((v - mn) / (mx - mn) * 100) for k, v in raw_by_team.items()}


def weather_boost(weather_json: Optional[dict]) -> float:
    if not weather_json:
        return 0.0
    wind = weather_json.get("wind", {}).get("speed", 0)
    temp = weather_json.get("main", {}).get("temp", 15)
    conds = [w.get("main", "").lower() for w in weather_json.get("weather", [])]

    boost = 0.0
    if wind >= 9:
        boost += 8
    if any(c in ("rain", "snow", "thunderstorm") for c in conds):
        boost += 8
    if temp <= -2 or temp >= 30:
        boost += 5
    return boost


def compute_match_chaos(chaos_home: float, chaos_away: float, w_boost: float) -> float:
    return float(np.clip((chaos_home + chaos_away) / 2 + w_boost, 0, 100))


def points_from_result(goals_for: int, goals_against: int) -> int:
    if goals_for > goals_against:
        return 3
    if goals_for == goals_against:
        return 1
    return 0


def compute_form_points(past: List[PastMatch], window: int, min_games: int) -> Optional[float]:
    sample = past[:window]
    if len(sample) < min_games:
        return None
    pts = sum(points_from_result(m.goals_for, m.goals_against) for m in sample)
    # нормируем на полный размер окна, чтобы форма оставалась сопоставимой
    return pts / window


def compute_goals_avgs(past: List[PastMatch], window: int, min_games: int) -> Tuple[Optional[float], Optional[float]]:
    sample = past[:window]
    if len(sample) < min_games:
        return None, None
    games = len(sample)
    gf = sum(m.goals_for for m in sample) / games
    ga = sum(m.goals_against for m in sample) / games
    return float(gf), float(ga)


def compute_rest_days(past: List[PastMatch], kickoff: datetime) -> Optional[float]:
    if not past:
        return None
    last_match = past[0]
    delta = kickoff - last_match.kickoff
    return float(delta.days)
