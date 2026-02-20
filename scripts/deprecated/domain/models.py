"""Deprecated module (legacy pydantic models).

Kept for reference by legacy scripts; not used by the production pipeline.
"""

from pydantic import BaseModel
from datetime import datetime
from typing import Optional, List


class Team(BaseModel):
    id: int
    name: str
    league_id: int


class Fixture(BaseModel):
    id: int
    league_id: int
    season: int
    kickoff: datetime
    home_team_id: int
    away_team_id: int
    status: str
    home_goals: Optional[int] = None
    away_goals: Optional[int] = None
    home_red: int = 0
    away_red: int = 0
    home_yellow: int = 0
    away_yellow: int = 0


class MatchIndices(BaseModel):
    fixture_id: int
    fatigue_home: float
    fatigue_away: float
    fatigue_diff: float
    chaos_home: float
    chaos_away: float
    chaos_match: float


class Prediction(BaseModel):
    fixture_id: int
    model_summary: str
    picks: List[str]
    confidence: float
