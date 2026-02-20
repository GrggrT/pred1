from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, Iterable, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.decimalutils import q_money
from app.core.logger import get_logger
from app.core.timeutils import utcnow

log = get_logger("jobs.compute_indices")
FINAL_STATUSES = ("FT", "AET", "PEN")
SHORT_WINDOW = 5
LONG_WINDOW = 15
VENUE_WINDOW = 5
PREFETCH_LIMIT = 50


@dataclass
class MatchSample:
    kickoff: datetime
    is_home: bool
    val_for: Optional[Decimal]
    val_against: Optional[Decimal]


def _coalesce_metric(primary: Optional[float], fallback: Optional[int]) -> Optional[Decimal]:
    if primary is not None:
        return q_money(primary)
    if fallback is not None:
        return q_money(fallback)
    return None


def _average(values: List[Decimal]) -> Optional[Decimal]:
    if not values:
        return None
    total = sum(values)
    return q_money(total / Decimal(len(values)))


def _compute_window(samples: List[MatchSample], size: int) -> Tuple[Optional[Decimal], Optional[Decimal]]:
    window = samples[:size]
    return _average([s.val_for for s in window if s.val_for is not None]), _average(
        [s.val_against for s in window if s.val_against is not None]
    )


def _rest_hours(samples: List[MatchSample], target_kickoff: datetime) -> Optional[int]:
    if not samples:
        return None
    last_kickoff = samples[0].kickoff
    delta = target_kickoff - last_kickoff
    return int(delta.total_seconds() // 3600)


def _with_fallback(value: Optional[Decimal], fallback: Decimal) -> Decimal:
    return value if value is not None else fallback


async def _prefetch_team_history(
    session: AsyncSession,
    team_ids: List[int],
    cutoff_end: datetime,
    limit: int = PREFETCH_LIMIT,
) -> Dict[int, List[MatchSample]]:
    if not team_ids:
        return {}
    res = await session.execute(
        text(
            """
            WITH team_matches AS (
              SELECT
                f.kickoff AS kickoff,
                f.home_team_id AS team_id,
                TRUE AS is_home,
                f.home_goals AS goals_for,
                f.away_goals AS goals_against,
                f.home_xg AS xg_for,
                f.away_xg AS xg_against
              FROM fixtures f
              WHERE f.status IN ('FT', 'AET', 'PEN')
                AND f.kickoff < :cutoff_end
                AND f.home_team_id IN (SELECT unnest(CAST(:team_ids AS integer[])))

              UNION ALL

              SELECT
                f.kickoff AS kickoff,
                f.away_team_id AS team_id,
                FALSE AS is_home,
                f.away_goals AS goals_for,
                f.home_goals AS goals_against,
                f.away_xg AS xg_for,
                f.home_xg AS xg_against
              FROM fixtures f
              WHERE f.status IN ('FT', 'AET', 'PEN')
                AND f.kickoff < :cutoff_end
                AND f.away_team_id IN (SELECT unnest(CAST(:team_ids AS integer[])))
            ),
            ranked AS (
              SELECT
                kickoff, team_id, is_home, goals_for, goals_against, xg_for, xg_against,
                ROW_NUMBER() OVER (PARTITION BY team_id ORDER BY kickoff DESC) AS rn
              FROM team_matches
            )
            SELECT kickoff, team_id, is_home, goals_for, goals_against, xg_for, xg_against
            FROM ranked
            WHERE rn <= :lim
            ORDER BY team_id, kickoff DESC
            """
        ),
        {
            "statuses": list(FINAL_STATUSES),
            "cutoff_end": cutoff_end,
            "team_ids": team_ids,
            "lim": limit,
        },
    )
    out: Dict[int, List[MatchSample]] = {}
    for r in res.fetchall():
        val_for = _coalesce_metric(r.xg_for, r.goals_for)
        val_against = _coalesce_metric(r.xg_against, r.goals_against)
        out.setdefault(r.team_id, []).append(MatchSample(r.kickoff, bool(r.is_home), val_for, val_against))
    return out


def _before_cutoff(
    samples: List[MatchSample],
    cutoff: datetime,
    limit: int,
    venue: Optional[str] = None,
) -> List[MatchSample]:
    out: List[MatchSample] = []
    want_home = venue == "home"
    want_away = venue == "away"
    for s in samples:
        if s.kickoff >= cutoff:
            continue
        if want_home and not s.is_home:
            continue
        if want_away and s.is_home:
            continue
        out.append(s)
        if len(out) >= limit:
            break
    return out


async def _target_fixtures(session: AsyncSession) -> List:
    now_utc = utcnow()
    horizon = now_utc + (timedelta(days=1) if settings.backtest_mode else timedelta(days=7))
    res = await session.execute(
        text(
            """
            SELECT id, league_id, season, kickoff, home_team_id, away_team_id
            FROM fixtures
            WHERE (:bt = true OR status='NS')
              AND league_id IN (SELECT unnest(CAST(:lids AS integer[])))
              AND kickoff >= :start AND kickoff < :end
            ORDER BY kickoff ASC
            """
        ),
        {"start": now_utc, "end": horizon, "lids": settings.league_ids, "bt": settings.backtest_mode},
    )
    return res.fetchall()


async def _fetch_team_history(
    session: AsyncSession,
    team_id: int,
    cutoff: datetime,
    limit: int,
    venue: Optional[str] = None,
) -> List[MatchSample]:
    conditions = [
        "kickoff < :cutoff",
        "status IN ('FT', 'AET', 'PEN')",
    ]
    params = {"cutoff": cutoff, "tid": team_id, "lim": limit}
    if venue == "home":
        conditions.append("home_team_id = :tid")
    elif venue == "away":
        conditions.append("away_team_id = :tid")
    else:
        conditions.append("(home_team_id = :tid OR away_team_id = :tid)")

    res = await session.execute(
        text(
            f"""
            SELECT kickoff, home_team_id, away_team_id,
                   home_goals, away_goals, home_xg, away_xg
            FROM fixtures
            WHERE {' AND '.join(conditions)}
            ORDER BY kickoff DESC
            LIMIT :lim
            """
        ),
        params,
    )
    samples: List[MatchSample] = []
    for row in res.fetchall():
        is_home = row.home_team_id == team_id
        val_for = _coalesce_metric(row.home_xg if is_home else row.away_xg, row.home_goals if is_home else row.away_goals)
        val_against = _coalesce_metric(
            row.away_xg if is_home else row.home_xg, row.away_goals if is_home else row.home_goals
        )
        samples.append(MatchSample(row.kickoff, is_home, val_for, val_against))
    return samples


async def _league_baseline_cache(
    session: AsyncSession,
    cache: Dict[Tuple[int, int, datetime], Tuple[Decimal, Decimal]],
    league_id: int,
    season: int,
    cutoff: datetime,
) -> Tuple[Decimal, Decimal]:
    bucket = cutoff.date()
    key = (league_id, season, bucket)
    if key in cache:
        return cache[key]
    res = await session.execute(
        text(
            """
            SELECT
              AVG(COALESCE(home_xg, home_goals)) AS home_avg,
              AVG(COALESCE(away_xg, away_goals)) AS away_avg
            FROM fixtures
            WHERE league_id=:lid
              AND season=:season
              AND status IN ('FT', 'AET', 'PEN')
              AND kickoff < :cutoff
            """
        ),
        {"lid": league_id, "season": season, "cutoff": cutoff},
    )
    row = res.first()
    home_avg = q_money(row.home_avg) if row and row.home_avg is not None else q_money(1)
    away_avg = q_money(row.away_avg) if row and row.away_avg is not None else q_money(1)
    cache[key] = (home_avg, away_avg)
    return home_avg, away_avg


async def _upsert_indices(
    session: AsyncSession,
    fixture_id: int,
    indices: dict,
):
    await session.execute(
        text(
            """
            INSERT INTO match_indices(
              fixture_id,
              home_form_for, home_form_against,
              away_form_for, away_form_against,
              home_class_for, home_class_against,
              away_class_for, away_class_against,
              home_venue_for, home_venue_against,
              away_venue_for, away_venue_against,
              home_rest_hours, away_rest_hours,
              created_at
            )
            VALUES(
              :fid, :hff, :hfa, :aff, :afa, :hcf, :hca, :acf, :aca,
              :hvf, :hva, :avf, :ava, :hrh, :arh, now()
            )
            ON CONFLICT (fixture_id) DO UPDATE SET
              home_form_for=:hff, home_form_against=:hfa,
              away_form_for=:aff, away_form_against=:afa,
              home_class_for=:hcf, home_class_against=:hca,
              away_class_for=:acf, away_class_against=:aca,
              home_venue_for=:hvf, home_venue_against=:hva,
              away_venue_for=:avf, away_venue_against=:ava,
              home_rest_hours=:hrh, away_rest_hours=:arh,
              created_at=now()
            """
        ),
        {"fid": fixture_id, **indices},
    )


async def run(session: AsyncSession):
    fixtures = await _target_fixtures(session)
    if not fixtures:
        log.info("compute_indices no fixtures to process")
        return {"processed": 0, "backtest": bool(settings.backtest_mode), "backtest_day": settings.backtest_current_date}

    log.info("compute_indices fixtures=%s", len(fixtures))
    league_cache: Dict[Tuple[int, int, datetime], Tuple[Decimal, Decimal]] = {}
    cutoff_end = max(r.kickoff for r in fixtures)
    team_ids = sorted({int(r.home_team_id) for r in fixtures} | {int(r.away_team_id) for r in fixtures})
    hist_all = await _prefetch_team_history(session, team_ids, cutoff_end, limit=PREFETCH_LIMIT)
    processed = 0

    for row in fixtures:
        home_all = hist_all.get(int(row.home_team_id), [])
        away_all = hist_all.get(int(row.away_team_id), [])
        home_history = _before_cutoff(home_all, row.kickoff, LONG_WINDOW)
        away_history = _before_cutoff(away_all, row.kickoff, LONG_WINDOW)
        home_venue_hist = _before_cutoff(home_all, row.kickoff, VENUE_WINDOW, venue="home")
        away_venue_hist = _before_cutoff(away_all, row.kickoff, VENUE_WINDOW, venue="away")

        home_form_for, home_form_against = _compute_window(home_history, SHORT_WINDOW)
        away_form_for, away_form_against = _compute_window(away_history, SHORT_WINDOW)

        home_class_for, home_class_against = _compute_window(home_history, LONG_WINDOW)
        away_class_for, away_class_against = _compute_window(away_history, LONG_WINDOW)

        home_venue_for, home_venue_against = _compute_window(home_venue_hist, VENUE_WINDOW)
        away_venue_for, away_venue_against = _compute_window(away_venue_hist, VENUE_WINDOW)

        home_rest_hours = _rest_hours(home_history, row.kickoff)
        away_rest_hours = _rest_hours(away_history, row.kickoff)

        league_home_avg, league_away_avg = await _league_baseline_cache(
            session, league_cache, row.league_id, row.season, row.kickoff
        )
        indices = {
            "hff": _with_fallback(home_form_for, league_home_avg),
            "hfa": _with_fallback(home_form_against, league_away_avg),
            "aff": _with_fallback(away_form_for, league_away_avg),
            "afa": _with_fallback(away_form_against, league_home_avg),
            "hcf": _with_fallback(home_class_for, league_home_avg),
            "hca": _with_fallback(home_class_against, league_away_avg),
            "acf": _with_fallback(away_class_for, league_away_avg),
            "aca": _with_fallback(away_class_against, league_home_avg),
            "hvf": _with_fallback(home_venue_for, league_home_avg),
            "hva": _with_fallback(home_venue_against, league_away_avg),
            "avf": _with_fallback(away_venue_for, league_away_avg),
            "ava": _with_fallback(away_venue_against, league_home_avg),
            "hrh": home_rest_hours,
            "arh": away_rest_hours,
        }

        await _upsert_indices(session, row.id, indices)
        processed += 1

    await session.commit()
    log.info("compute_indices done processed=%s", processed)
    return {"processed": processed, "backtest": bool(settings.backtest_mode), "backtest_day": settings.backtest_current_date}
