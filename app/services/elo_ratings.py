from __future__ import annotations

import math
from datetime import datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.decimalutils import D, q_money, safe_div
from app.core.logger import get_logger

log = get_logger("services.elo")
DEFAULT_RATING = D("1500")
FINAL_STATUSES = ("FT", "AET", "PEN")


def _expected_score(
    rating: Decimal,
    opponent_rating: Decimal,
    is_home: bool = False,
    home_advantage: int = 0,
) -> Decimal:
    """Standard Elo expectation with optional home-advantage boost."""
    adj = D(home_advantage) if is_home else D(0)
    return D(1) / (D(1) + (D(10) ** safe_div(opponent_rating - rating - adj, D(400))))


def _goal_diff_multiplier(home_goals: int, away_goals: int) -> Decimal:
    """K-factor multiplier based on goal difference: max(1, ln(|diff| + 1))."""
    diff = abs(home_goals - away_goals)
    if diff <= 1:
        return D(1)
    return D(str(round(math.log(diff + 1), 6)))


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
    is_home: bool = False,
    home_advantage: int = 0,
    goal_diff_mult: Decimal = D(1),
) -> Decimal:
    rating = await get_team_rating(session, team_id)
    opp_rating = await get_team_rating(session, opponent_id)
    k = D(k_factor)
    k_eff = q_money(k * goal_diff_mult)
    expected = _expected_score(rating, opp_rating, is_home=is_home, home_advantage=home_advantage)
    new_rating = q_money(rating + k_eff * (result - expected))
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
        "elo_update team=%s opp=%s result=%.2f expected=%.3f old=%.3f new=%.3f k_eff=%.1f",
        team_id,
        opponent_id,
        float(result),
        float(expected),
        float(rating),
        float(new_rating),
        float(k_eff),
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


def _detect_season_change(prev_kickoff: datetime | None, curr_kickoff: datetime) -> bool:
    """Detect season boundary: gap > 45 days between consecutive fixtures."""
    if prev_kickoff is None:
        return False
    gap = (curr_kickoff - prev_kickoff).days
    return gap > 45


async def _regress_ratings(
    session: AsyncSession,
    team_ids: set[int],
    ratings: dict[int, Decimal],
    factor: Decimal,
) -> None:
    """Regress all known ratings towards DEFAULT_RATING by factor."""
    for tid in team_ids:
        old = ratings.get(tid, DEFAULT_RATING)
        ratings[tid] = q_money(DEFAULT_RATING + factor * (old - DEFAULT_RATING))
    log.info("elo_season_regression applied factor=%.2f teams=%d", float(factor), len(team_ids))


async def apply_elo_from_fixtures(
    session: AsyncSession,
    *,
    league_ids: list[int] | None = None,
    cutoff: datetime | None = None,
    batch_limit: int = 5000,
    force_recompute: bool = False,
    k_factor: int = 20,
    home_advantage: int = 65,
    regression_factor: Decimal = D("0.67"),
) -> dict:
    """
    Incrementally apply Elo updates for ALL finished fixtures in DB (not only those with bets).

    Uses fixtures.elo_processed to ensure idempotency. If we detect that an older (kickoff) fixture
    arrived after later ones have already been processed, we automatically rebuild Elo from scratch
    for the selected leagues to keep chronological correctness.

    Improvements over baseline:
    - Home advantage: home team gets +home_advantage rating bonus in expectation
    - Goal-diff K-factor: k_eff = K * max(1, ln(goal_diff + 1))
    - Season regression: when gap > 45 days detected, regress ratings towards 1500

    Optional cutoff limits processing to fixtures with kickoff before the cutoff (useful for backtests).
    """
    from app.core.config import settings as _settings

    ha = _settings.elo_home_advantage if home_advantage == 65 else home_advantage
    k = _settings.elo_k_factor if k_factor == 20 else k_factor
    reg = _settings.elo_regression_factor if regression_factor == D("0.67") else regression_factor

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
    season_regressions = 0
    prev_kickoff: datetime | None = None

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
        k_dec = D(k)
        for row in rows:
            home_id = int(row.home_team_id)
            away_id = int(row.away_team_id)

            # Season regression detection
            if _detect_season_change(prev_kickoff, row.kickoff):
                all_known = set(ratings.keys())
                if all_known:
                    await _regress_ratings(session, all_known, ratings, D(reg))
                    touched.update(all_known)
                    season_regressions += 1

            prev_kickoff = row.kickoff

            home_rating = ratings.get(home_id, DEFAULT_RATING)
            away_rating = ratings.get(away_id, DEFAULT_RATING)

            # Goal-diff multiplier
            gdm = _goal_diff_multiplier(int(row.home_goals), int(row.away_goals))
            k_eff = q_money(k_dec * gdm)

            expected_home = _expected_score(home_rating, away_rating, is_home=True, home_advantage=ha)
            expected_away = _expected_score(away_rating, home_rating, is_home=False, home_advantage=0)
            home_result, away_result = _result_from_score(int(row.home_goals), int(row.away_goals))

            ratings[home_id] = q_money(home_rating + k_eff * (home_result - expected_home))
            ratings[away_id] = q_money(away_rating + k_eff * (away_result - expected_away))
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

    return {"processed": processed, "batches": batches, "rebuild": rebuild, "season_regressions": season_regressions}
