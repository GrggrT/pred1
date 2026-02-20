from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.decimalutils import D, q_money, safe_div
from app.core.logger import get_logger

log = get_logger("services.elo")
DEFAULT_RATING = D("1500")
FINAL_STATUSES = ("FT", "AET", "PEN")


def _expected_score(rating: Decimal, opponent_rating: Decimal) -> Decimal:
    # Standard Elo expectation.
    return D(1) / (D(1) + (D(10) ** safe_div(opponent_rating - rating, D(400))))


def _result_from_score(home_goals: int, away_goals: int) -> tuple[Decimal, Decimal]:
    if home_goals > away_goals:
        return D(1), D(0)
    if home_goals == away_goals:
        return D("0.5"), D("0.5")
    return D(0), D(1)


async def get_team_rating(session: AsyncSession, team_id: int) -> Decimal:
    res = await session.execute(
        text("SELECT rating FROM team_elo_ratings WHERE team_id=:tid"),
        {"tid": team_id},
    )
    row = res.first()
    if row and row.rating is not None:
        return D(row.rating)
    await session.execute(
        text(
            """
            INSERT INTO team_elo_ratings(team_id, rating, updated_at)
            VALUES(:tid, :rating, now())
            ON CONFLICT (team_id) DO NOTHING
            """
        ),
        {"tid": team_id, "rating": DEFAULT_RATING},
    )
    return DEFAULT_RATING


async def update_elo_rating(
    session: AsyncSession,
    team_id: int,
    opponent_id: int,
    result: Decimal,
    k_factor: Decimal | int = 20,
) -> Decimal:
    rating = await get_team_rating(session, team_id)
    opp_rating = await get_team_rating(session, opponent_id)
    k = D(k_factor)
    expected = _expected_score(rating, opp_rating)
    new_rating = q_money(rating + k * (result - expected))
    await session.execute(
        text(
            """
            INSERT INTO team_elo_ratings(team_id, rating, updated_at)
            VALUES(:tid, :rating, now())
            ON CONFLICT (team_id) DO UPDATE SET
              rating = EXCLUDED.rating,
              updated_at = now()
            """
        ),
        {"tid": team_id, "rating": new_rating},
    )
    log.debug(
        "elo_update team=%s opp=%s result=%.2f expected=%.3f old=%.3f new=%.3f",
        team_id,
        opponent_id,
        float(result),
        float(expected),
        float(rating),
        float(new_rating),
    )
    return new_rating


async def update_after_fixture(session: AsyncSession, fixture_row) -> None:
    """Update both teams' Elo after a finished fixture."""
    home_goals = fixture_row.home_goals
    away_goals = fixture_row.away_goals
    if home_goals is None or away_goals is None:
        return
    home_result, away_result = _result_from_score(home_goals, away_goals)

    home_team = fixture_row.home_team_id
    away_team = fixture_row.away_team_id
    await update_elo_rating(session, home_team, away_team, home_result)
    await update_elo_rating(session, away_team, home_team, away_result)
    log.debug(
        "elo fixture=%s home=%s away=%s score=%s-%s",
        getattr(fixture_row, "fixture_id", getattr(fixture_row, "id", None)),
        home_team,
        away_team,
        home_goals,
        away_goals,
    )


async def apply_elo_from_fixtures(
    session: AsyncSession,
    *,
    league_ids: list[int] | None = None,
    cutoff: datetime | None = None,
    batch_limit: int = 5000,
    force_recompute: bool = False,
    k_factor: int = 20,
) -> dict:
    """
    Incrementally apply Elo updates for ALL finished fixtures in DB (not only those with bets).

    Uses fixtures.elo_processed to ensure idempotency. If we detect that an older (kickoff) fixture
    arrived after later ones have already been processed, we automatically rebuild Elo from scratch
    for the selected leagues to keep chronological correctness.

    Optional cutoff limits processing to fixtures with kickoff before the cutoff (useful for backtests).
    """

    league_filter = ""
    cutoff_filter = ""
    params: dict = {
        "lids": league_ids or [],
        "lim": int(batch_limit),
    }
    if league_ids:
        league_filter = "AND league_id IN (SELECT unnest(CAST(:lids AS integer[])))"
    if cutoff is not None:
        cutoff_filter = "AND kickoff < :cutoff"
        params["cutoff"] = cutoff

    max_processed = (
        await session.execute(
            text(
                f"""
                SELECT MAX(kickoff) AS max_kickoff
                FROM fixtures
                WHERE status IN ('FT','AET','PEN')
                  AND home_goals IS NOT NULL AND away_goals IS NOT NULL
                  AND elo_processed = TRUE
                  {league_filter}
                  {cutoff_filter}
                """
            ),
            params,
        )
    ).first()
    max_processed_kickoff = max_processed.max_kickoff if max_processed else None

    min_unprocessed = (
        await session.execute(
            text(
                f"""
                SELECT MIN(kickoff) AS min_kickoff
                FROM fixtures
                WHERE status IN ('FT','AET','PEN')
                  AND home_goals IS NOT NULL AND away_goals IS NOT NULL
                  AND COALESCE(elo_processed, FALSE) = FALSE
                  {league_filter}
                  {cutoff_filter}
                """
            ),
            params,
        )
    ).first()
    min_unprocessed_kickoff = min_unprocessed.min_kickoff if min_unprocessed else None

    out_of_order = (
        max_processed_kickoff is not None
        and min_unprocessed_kickoff is not None
        and min_unprocessed_kickoff < max_processed_kickoff
    )
    rebuild = bool(force_recompute or out_of_order)

    if rebuild:
        # Rebuild requires resetting ratings to a clean state.
        await session.execute(text("DELETE FROM team_elo_ratings"))
        await session.execute(
            text(
                f"""
                UPDATE fixtures
                SET elo_processed=FALSE, elo_processed_at=NULL
                WHERE status IN ('FT','AET','PEN')
                  {league_filter}
                  {cutoff_filter}
                """
            ),
            params,
        )

    processed = 0
    batches = 0
    while True:
        res = await session.execute(
            text(
                f"""
                SELECT id, kickoff, home_team_id, away_team_id, home_goals, away_goals
                FROM fixtures
                WHERE status IN ('FT','AET','PEN')
                  AND home_goals IS NOT NULL AND away_goals IS NOT NULL
                  AND COALESCE(elo_processed, FALSE) = FALSE
                  {league_filter}
                  {cutoff_filter}
                ORDER BY kickoff ASC, id ASC
                LIMIT :lim
                """
            ),
            params,
        )
        rows = res.fetchall()
        if not rows:
            break

        batches += 1
        fixture_ids: list[int] = [int(r.id) for r in rows]
        team_ids = sorted({int(r.home_team_id) for r in rows} | {int(r.away_team_id) for r in rows})
        ratings: dict[int, Decimal] = {}
        if not rebuild and team_ids:
            cur = await session.execute(
                text(
                    """
                    SELECT team_id, rating
                    FROM team_elo_ratings
                    WHERE team_id IN (SELECT unnest(CAST(:tids AS integer[])))
                    """
                ),
                {"tids": team_ids},
            )
            for r in cur.fetchall():
                if r.team_id is None or r.rating is None:
                    continue
                ratings[int(r.team_id)] = D(r.rating)

        touched: set[int] = set()
        k = D(k_factor)
        for row in rows:
            home_id = int(row.home_team_id)
            away_id = int(row.away_team_id)
            home_rating = ratings.get(home_id, DEFAULT_RATING)
            away_rating = ratings.get(away_id, DEFAULT_RATING)

            expected_home = _expected_score(home_rating, away_rating)
            expected_away = D(1) - expected_home
            home_result, away_result = _result_from_score(int(row.home_goals), int(row.away_goals))

            ratings[home_id] = q_money(home_rating + k * (home_result - expected_home))
            ratings[away_id] = q_money(away_rating + k * (away_result - expected_away))
            touched.add(home_id)
            touched.add(away_id)

        if touched:
            await session.execute(
                text(
                    """
                    INSERT INTO team_elo_ratings(team_id, rating, updated_at)
                    VALUES(:tid, :rating, now())
                    ON CONFLICT (team_id) DO UPDATE SET
                      rating = EXCLUDED.rating,
                      updated_at = now()
                    """
                ),
                [{"tid": tid, "rating": ratings[tid]} for tid in sorted(touched)],
            )

        await session.execute(
            text(
                """
                UPDATE fixtures
                SET elo_processed=TRUE, elo_processed_at=now()
                WHERE id IN (SELECT unnest(CAST(:ids AS integer[])))
                """
            ),
            {"ids": fixture_ids},
        )

        processed += len(rows)
        if len(rows) < int(batch_limit):
            break

    return {"processed": processed, "batches": batches, "rebuild": rebuild}
