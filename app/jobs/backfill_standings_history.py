"""Backfill team_standings_history from finished fixtures.

Reconstructs league standings as-of each match date by replaying all
finished fixtures in chronological order.  For every date on which at
least one match in a (league, season) was played, a row per team is
written with cumulative W/D/L/GF/GA/GD/Pts and intra-league rank.

Safe to re-run: uses INSERT ... ON CONFLICT DO UPDATE (full upsert).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logger import get_logger

log = get_logger("jobs.backfill_standings_history")


# ── helpers ────────────────────────────────────────────────────────────


class _TeamRecord:
    """Mutable accumulator for a single team's season record."""

    __slots__ = ("played", "won", "drawn", "lost", "gf", "ga")

    def __init__(self) -> None:
        self.played = 0
        self.won = 0
        self.drawn = 0
        self.lost = 0
        self.gf = 0
        self.ga = 0

    def update(self, goals_scored: int, goals_conceded: int) -> None:
        self.played += 1
        self.gf += goals_scored
        self.ga += goals_conceded
        if goals_scored > goals_conceded:
            self.won += 1
        elif goals_scored == goals_conceded:
            self.drawn += 1
        else:
            self.lost += 1

    @property
    def points(self) -> int:
        return self.won * 3 + self.drawn

    @property
    def goal_diff(self) -> int:
        return self.gf - self.ga

    @property
    def ppg(self) -> float:
        return round(self.points / self.played, 3) if self.played else 0.0

    def snapshot(self) -> dict[str, Any]:
        return {
            "played": self.played,
            "won": self.won,
            "drawn": self.drawn,
            "lost": self.lost,
            "goals_for": self.gf,
            "goals_against": self.ga,
            "goal_diff": self.goal_diff,
            "points": self.points,
            "ppg": self.ppg,
        }


def _rank_teams(
    records: dict[int, _TeamRecord],
) -> dict[int, int]:
    """Return {team_id: rank} sorted by points DESC, goal_diff DESC, gf DESC."""
    ordered = sorted(
        records.keys(),
        key=lambda tid: (
            records[tid].points,
            records[tid].goal_diff,
            records[tid].gf,
        ),
        reverse=True,
    )
    return {tid: pos + 1 for pos, tid in enumerate(ordered)}


# ── main ───────────────────────────────────────────────────────────────

_UPSERT_SQL = text("""
    INSERT INTO team_standings_history
        (team_id, league_id, season, as_of_date,
         played, won, drawn, lost,
         goals_for, goals_against, goal_diff, points, rank, ppg)
    VALUES
        (:team_id, :league_id, :season, :as_of_date,
         :played, :won, :drawn, :lost,
         :goals_for, :goals_against, :goal_diff, :points, :rank, :ppg)
    ON CONFLICT (team_id, league_id, season, as_of_date) DO UPDATE SET
        played      = EXCLUDED.played,
        won         = EXCLUDED.won,
        drawn       = EXCLUDED.drawn,
        lost        = EXCLUDED.lost,
        goals_for   = EXCLUDED.goals_for,
        goals_against = EXCLUDED.goals_against,
        goal_diff   = EXCLUDED.goal_diff,
        points      = EXCLUDED.points,
        rank        = EXCLUDED.rank,
        ppg         = EXCLUDED.ppg
""")


async def run(session: AsyncSession) -> dict:
    """Replay finished fixtures and populate team_standings_history."""
    log.info("backfill_standings_history: start")

    league_ids = settings.league_ids
    if not league_ids:
        log.warning("No league_ids configured")
        return {"status": "skip", "reason": "no leagues"}

    # Fetch all finished fixtures ordered chronologically
    lid_list = ",".join(str(lid) for lid in league_ids)
    rows = (
        await session.execute(
            text(f"""
                SELECT id, league_id, season,
                       home_team_id, away_team_id,
                       home_goals, away_goals,
                       (kickoff AT TIME ZONE 'UTC')::date AS match_date
                FROM fixtures
                WHERE status IN ('FT', 'AET', 'PEN')
                  AND league_id IN ({lid_list})
                  AND home_goals IS NOT NULL
                  AND away_goals IS NOT NULL
                ORDER BY kickoff ASC, id ASC
            """)
        )
    ).fetchall()

    log.info("backfill_standings_history: %d finished fixtures to process", len(rows))
    if not rows:
        return {"status": "ok", "fixtures": 0, "rows_written": 0}

    # ── replay by (league, season) ─────────────────────────────────────
    # Group fixtures by (league_id, season) first, then process chronologically.
    league_season_fixtures: dict[tuple[int, int], list] = defaultdict(list)
    for r in rows:
        key = (r.league_id, r.season)
        league_season_fixtures[key].append(r)

    total_written = 0

    for (lid, season), fixtures in league_season_fixtures.items():
        records: dict[int, _TeamRecord] = defaultdict(_TeamRecord)
        # Track which dates have matches (we snapshot after all matches on a date)
        date_fixtures: dict[date, list] = defaultdict(list)

        for f in fixtures:
            date_fixtures[f.match_date].append(f)

        # Process dates in order
        sorted_dates = sorted(date_fixtures.keys())
        batch_params: list[dict] = []

        for match_date in sorted_dates:
            # Apply all matches on this date
            for f in date_fixtures[match_date]:
                records[f.home_team_id].update(f.home_goals, f.away_goals)
                records[f.away_team_id].update(f.away_goals, f.home_goals)

            # Compute ranks after all matches on this date
            ranks = _rank_teams(records)

            # Snapshot all teams that have played at least 1 match
            for tid, rec in records.items():
                if rec.played == 0:
                    continue
                snap = rec.snapshot()
                snap.update(
                    team_id=tid,
                    league_id=lid,
                    season=season,
                    as_of_date=match_date,
                    rank=ranks.get(tid, 99),
                )
                batch_params.append(snap)

            # Flush in batches of 2000 to avoid memory pressure
            if len(batch_params) >= 2000:
                await session.execute(_UPSERT_SQL, batch_params)
                total_written += len(batch_params)
                batch_params = []

        # Flush remaining
        if batch_params:
            await session.execute(_UPSERT_SQL, batch_params)
            total_written += len(batch_params)

        log.info(
            "backfill_standings_history: league=%s season=%s teams=%d dates=%d",
            lid, season, len(records), len(sorted_dates),
        )

    await session.commit()
    log.info("backfill_standings_history: done, total rows=%d", total_written)

    return {
        "status": "ok",
        "fixtures": len(rows),
        "rows_written": total_written,
        "leagues": len(league_season_fixtures),
    }
