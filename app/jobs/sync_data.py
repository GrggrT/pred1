from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable, Optional, Tuple
from statistics import mean
import hashlib

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from asyncio import sleep
from app.core.config import settings
from app.core.decimalutils import D, q_money, q_prob, q_xg
from app.core.logger import get_logger
from app.core.timeutils import ensure_aware_utc, utcnow
from app.data.mappers import normalize_status
from app.data.providers.api_football import (
    get_fixtures,
    get_fixture_statistics,
    get_odds_by_date_paged,
    get_odds_by_fixture,
    get_injuries,
    get_standings,
    set_run_cache_miss_budget,
    set_force_refresh,
    reset_force_refresh,
    ApiFootballBudgetExceeded,
    reset_api_metrics,
    get_api_metrics,
)
from app.services.league_model_params import estimate_dixon_coles_rho, estimate_power_calibration_alpha
from app.services.api_football_quota import is_api_football_quota_error, quota_guard_decision

log = get_logger("jobs.sync_data")


async def _rate_limit():
    delay = max(settings.fetch_rate_ms, 0) / 1000
    if delay:
        await sleep(delay)


def _parse_kickoff(raw_date: str) -> datetime:
    kickoff = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
    return ensure_aware_utc(kickoff)


async def _upsert_league(session: AsyncSession, league_json: dict):
    await session.execute(
        text(
            """
            INSERT INTO leagues(id, name, country, active, logo_url)
            VALUES(:id, :name, :country, TRUE, :logo)
            ON CONFLICT (id) DO UPDATE
            SET name=:name, country=:country, active=TRUE,
                logo_url=COALESCE(EXCLUDED.logo_url, leagues.logo_url)
        """
        ),
        {
            "id": league_json.get("id"),
            "name": league_json.get("name"),
            "country": league_json.get("country"),
            "logo": league_json.get("logo"),
        },
    )


async def _upsert_team(session: AsyncSession, team_json: dict, league_id: int):
    await session.execute(
        text(
            """
            INSERT INTO teams(id, name, league_id, code, logo_url)
            VALUES(:id, :name, :league_id, :code, :logo)
            ON CONFLICT (id) DO UPDATE
            SET name=:name, league_id=:league_id, code=:code, logo_url=:logo
        """
        ),
        {
            "id": team_json.get("id"),
            "name": team_json.get("name"),
            "league_id": league_id,
            "code": team_json.get("code"),
            "logo": team_json.get("logo"),
        },
    )


async def _upsert_fixture(session: AsyncSession, item: dict):
    fixture = item["fixture"]
    league = item["league"]
    teams = item["teams"]
    goals = item.get("goals") or {}
    score = item.get("score") or {}

    kickoff = _parse_kickoff(fixture["date"])
    raw_status = fixture.get("status", {}).get("short")
    status = normalize_status(raw_status)

    home_goals = goals.get("home")
    away_goals = goals.get("away")
    fulltime = score.get("fulltime") or {}
    home_goals = home_goals if home_goals is not None else fulltime.get("home")
    away_goals = away_goals if away_goals is not None else fulltime.get("away")

    # Auto-detect finished matches: if we have goals and kickoff was >3 hours ago, assume FT
    now_utc = utcnow()
    if (status in {"NS", "UNK"} and
        raw_status and raw_status.upper() == "PENDING" and
        kickoff and kickoff < now_utc - timedelta(hours=3) and
        home_goals is not None and away_goals is not None):
        status = "FT"

    await _upsert_league(session, league)
    await _upsert_team(session, teams["home"], league["id"])
    await _upsert_team(session, teams["away"], league["id"])

    await session.execute(
        text(
            """
            INSERT INTO fixtures(
              id, league_id, season, kickoff, home_team_id, away_team_id, status,
              home_goals, away_goals, has_odds, stats_downloaded, updated_at
            )
            VALUES(
              :id, :league_id, :season, :kickoff, :home_id, :away_id, :status,
              :hg, :ag, FALSE, FALSE, now()
            )
            ON CONFLICT (id) DO UPDATE SET
              league_id=:league_id,
              season=:season,
              kickoff=:kickoff,
              status=:status,
              home_goals=:hg,
              away_goals=:ag,
              updated_at=now()
        """
        ),
        {
            "id": fixture["id"],
            "league_id": league["id"],
            "season": league["season"],
            "kickoff": kickoff,
            "home_id": teams["home"]["id"],
            "away_id": teams["away"]["id"],
            "status": status,
            "hg": home_goals,
            "ag": away_goals,
        },
    )


def _extract_1x2(bookmaker: dict) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    for bet in bookmaker.get("bets", []):
        if bet.get("id") == 1 or (bet.get("name") or "").lower() == "match winner":
            home = draw = away = None
            for val in bet.get("values", []):
                label = (val.get("value") or "").lower()
                odd_raw = val.get("odd")
                odd = float(odd_raw) if odd_raw is not None else None
                if label in {"home", "1"}:
                    home = odd
                elif label in {"draw", "x"}:
                    draw = odd
                elif label in {"away", "2"}:
                    away = odd
            return home, draw, away
    return None, None, None


def _extract_total_25(bookmaker: dict) -> Tuple[Optional[float], Optional[float]]:
    def _label_matches(label: str, prefix: str) -> bool:
        return label.startswith(prefix) or label.startswith(prefix.replace(".", ","))

    best_over = best_under = None
    best_rank = 99
    for bet in bookmaker.get("bets", []):
        name = (bet.get("name") or "").lower()
        bet_id = bet.get("id")
        if bet_id == 5 or name == "goals over/under":
            rank = 0
        elif "over/under" in name and not any(kw in name for kw in ("first half", "1st half", "second half", "2nd half")):
            rank = 1
        else:
            continue
        over = under = None
        for val in bet.get("values", []):
            label = (val.get("value") or "").lower()
            odd_raw = val.get("odd")
            odd = float(odd_raw) if odd_raw is not None else None
            if _label_matches(label, "over 2.5") or _label_matches(label, "o 2.5"):
                over = odd
            elif _label_matches(label, "under 2.5") or _label_matches(label, "u 2.5"):
                under = odd
        if over is None and under is None:
            continue
        if rank < best_rank:
            best_over, best_under = over, under
            best_rank = rank
            if rank == 0:
                break
    return best_over, best_under


async def _upsert_odds(
    session: AsyncSession,
    data: dict,
    fetched_at: datetime,
    allowed_fixture_ids: Optional[set[int]] = None,
):
    fixtures_with_odds: set[int] = set()
    snapshots_saved = 0
    for entry in data.get("response", []):
        fixture = entry.get("fixture") or {}
        fixture_id = fixture.get("id")
        if not fixture_id:
            continue
        if allowed_fixture_ids is not None and fixture_id not in allowed_fixture_ids:
            continue

        bet365_home = bet365_draw = bet365_away = None
        bet365_over = bet365_under = None
        home_list: list[float] = []
        draw_list: list[float] = []
        away_list: list[float] = []
        over_list: list[float] = []
        under_list: list[float] = []

        for bookmaker in entry.get("bookmakers", []):
            if bookmaker.get("id") != settings.bookmaker_id:
                pass
            home, draw, away = _extract_1x2(bookmaker)
            over_25, under_25 = _extract_total_25(bookmaker)
            if home is not None:
                home_list.append(home)
            if draw is not None:
                draw_list.append(draw)
            if away is not None:
                away_list.append(away)
            if over_25 is not None:
                over_list.append(over_25)
            if under_25 is not None:
                under_list.append(under_25)
            if bookmaker.get("id") == settings.bookmaker_id:
                bet365_home, bet365_draw, bet365_away = home, draw, away
                bet365_over, bet365_under = over_25, under_25

        if not any([bet365_home, bet365_draw, bet365_away, bet365_over, bet365_under]):
            # no odds from target bookmaker; skip
            continue

        def avg(lst: list[float]) -> Optional[float]:
            return float(mean(lst)) if lst else None

        market_home = avg(home_list)
        market_draw = avg(draw_list)
        market_away = avg(away_list)
        market_over = avg(over_list)
        market_under = avg(under_list)

        await session.execute(
            text(
                """
                INSERT INTO odds(fixture_id, bookmaker_id, home_win, draw, away_win, over_2_5, under_2_5,
                                 market_avg_home_win, market_avg_draw, market_avg_away_win,
                                 market_avg_over_2_5, market_avg_under_2_5, fetched_at)
                VALUES(:fid, :bid, :h, :d, :a, :ov, :un, :mah, :mad, :maa, :mover, :munder, :fetched_at)
                ON CONFLICT (fixture_id, bookmaker_id) DO UPDATE SET
                  home_win=:h, draw=:d, away_win=:a,
                  over_2_5=:ov, under_2_5=:un,
                  market_avg_home_win=:mah, market_avg_draw=:mad, market_avg_away_win=:maa,
                  market_avg_over_2_5=:mover, market_avg_under_2_5=:munder,
                  fetched_at=:fetched_at
            """
            ),
            {
                "fid": fixture_id,
                "bid": settings.bookmaker_id,
                "h": q_money(bet365_home) if bet365_home is not None else None,
                "d": q_money(bet365_draw) if bet365_draw is not None else None,
                "a": q_money(bet365_away) if bet365_away is not None else None,
                "ov": q_money(bet365_over) if bet365_over is not None else None,
                "un": q_money(bet365_under) if bet365_under is not None else None,
                "mah": q_money(market_home) if market_home is not None else None,
                "mad": q_money(market_draw) if market_draw is not None else None,
                "maa": q_money(market_away) if market_away is not None else None,
                "mover": q_money(market_over) if market_over is not None else None,
                "munder": q_money(market_under) if market_under is not None else None,
                "fetched_at": fetched_at,
            },
        )
        # Keep a historical record for true-backtest / analysis.
        # Important: do NOT write snapshots in backtest_mode, otherwise we create "fake" historical odds
        # with simulated timestamps (BACKTEST_CURRENT_DATE affects utcnow()).
        if not settings.backtest_mode:
            res_snap = await session.execute(
                text(
                    """
                    INSERT INTO odds_snapshots(
                      fixture_id, bookmaker_id, fetched_at,
                      home_win, draw, away_win, over_2_5, under_2_5,
                      market_avg_home_win, market_avg_draw, market_avg_away_win,
                      market_avg_over_2_5, market_avg_under_2_5
                    )
                    VALUES(
                      :fid, :bid, :fetched_at,
                      :h, :d, :a, :ov, :un,
                      :mah, :mad, :maa,
                      :mover, :munder
                    )
                    ON CONFLICT DO NOTHING
                    """
                ),
                {
                    "fid": fixture_id,
                    "bid": settings.bookmaker_id,
                    "fetched_at": fetched_at,
                    "h": q_money(bet365_home) if bet365_home is not None else None,
                    "d": q_money(bet365_draw) if bet365_draw is not None else None,
                    "a": q_money(bet365_away) if bet365_away is not None else None,
                    "ov": q_money(bet365_over) if bet365_over is not None else None,
                    "un": q_money(bet365_under) if bet365_under is not None else None,
                    "mah": q_money(market_home) if market_home is not None else None,
                    "mad": q_money(market_draw) if market_draw is not None else None,
                    "maa": q_money(market_away) if market_away is not None else None,
                    "mover": q_money(market_over) if market_over is not None else None,
                    "munder": q_money(market_under) if market_under is not None else None,
                },
            )
            try:
                snapshots_saved += int(res_snap.rowcount or 0)
            except Exception:
                snapshots_saved += 0
        fixtures_with_odds.add(fixture_id)
        await _rate_limit()
    return fixtures_with_odds, snapshots_saved


async def _refresh_odds(session: AsyncSession, fixture_ids: list[int]) -> tuple[set[int], int]:
    allowed = set(fixture_ids)
    if not allowed:
        return set(), 0

    fixtures_with_odds: set[int] = set()
    snapshots_saved = 0

    # Fetch odds by date first (covers many fixtures per request); then fall back to per-fixture for remaining gaps.
    res_dates = await session.execute(
        text(
            """
            SELECT id, league_id, season, kickoff::date AS d
            FROM fixtures
            WHERE id IN (SELECT unnest(CAST(:ids AS integer[])))
            """
        ),
        {"ids": list(allowed)},
    )
    fixture_ids_by_group: dict[tuple[object, int, int], set[int]] = {}
    fid_to_league: dict[int, int] = {}
    for row in res_dates.fetchall():
        fid = getattr(row, "id", None)
        d = getattr(row, "d", None)
        league_id = getattr(row, "league_id", None)
        season = getattr(row, "season", None)
        if fid is None or d is None:
            continue
        if league_id is None or season is None:
            continue
        try:
            fid_to_league[int(fid)] = int(league_id)
        except Exception:
            pass
        try:
            key = (d, int(league_id), int(season))
            fixture_ids_by_group.setdefault(key, set()).add(int(fid))
        except Exception:
            continue
    groups_sorted = sorted(fixture_ids_by_group.keys())

    for d, league_id, season in groups_sorted:
        stop_ids = fixture_ids_by_group.get((d, league_id, season)) or set()
        fetched_at = utcnow()
        data = await get_odds_by_date_paged(
            session,
            d.isoformat(),
            [settings.bookmaker_id],
            league_id=league_id,
            season=season,
            stop_fixture_ids=set(stop_ids),
        )
        fx, snaps = await _upsert_odds(session, data, fetched_at, allowed_fixture_ids=allowed)
        fixtures_with_odds |= fx
        snapshots_saved += snaps
        if not data.get("response"):
            fetched_at = utcnow()
            data_any = await get_odds_by_date_paged(
                session,
                d.isoformat(),
                [],
                league_id=league_id,
                season=season,
                stop_fixture_ids=set(stop_ids),
            )
            fx, snaps = await _upsert_odds(session, data_any, fetched_at, allowed_fixture_ids=allowed)
            fixtures_with_odds |= fx
            snapshots_saved += snaps

    remaining = [fid for fid in fixture_ids if fid not in fixtures_with_odds]
    for fid in remaining:
        metric_lid = fid_to_league.get(int(fid)) if fid is not None else None
        fetched_at = utcnow()
        data = await get_odds_by_fixture(session, fid, [settings.bookmaker_id], metric_league_id=metric_lid)
        fx, snaps = await _upsert_odds(session, data, fetched_at, allowed_fixture_ids=allowed)
        fixtures_with_odds |= fx
        snapshots_saved += snaps
        if not data.get("response"):
            fetched_at = utcnow()
            data_any = await get_odds_by_fixture(session, fid, [], metric_league_id=metric_lid)
            fx, snaps = await _upsert_odds(session, data_any, fetched_at, allowed_fixture_ids=allowed)
            fixtures_with_odds |= fx
            snapshots_saved += snaps

    if fixtures_with_odds:
        await session.execute(
            text(
                """
                UPDATE fixtures
                SET has_odds=TRUE, updated_at=now()
                WHERE id IN (SELECT unnest(CAST(:ids AS integer[])))
                """
            ),
            {"ids": list(fixtures_with_odds)},
        )
    return fixtures_with_odds, snapshots_saved


async def _filter_missing_odds(
    session: AsyncSession,
    fixture_ids: list[int],
    freshness_hours: int = 3,
    now_ref: Optional[datetime] = None,
) -> tuple[list[int], set[int]]:
    """Return fixture ids that need odds fetch; also return fixtures that already have fresh odds."""
    if not fixture_ids:
        return [], set()
    now_ref = now_ref or utcnow()
    res = await session.execute(
        text(
            """
            SELECT fixture_id
            FROM odds
            WHERE fixture_id IN (SELECT unnest(CAST(:ids AS integer[])))
              AND fetched_at >= (CAST(:now_ref AS timestamptz)) - (cast(:hours as int) * interval '1 hour')
              AND bookmaker_id = :bid
            """
        ),
        {"ids": fixture_ids, "hours": freshness_hours, "bid": settings.bookmaker_id, "now_ref": now_ref},
    )
    fresh = {row.fixture_id for row in res.fetchall()}
    missing = [fid for fid in fixture_ids if fid not in fresh]
    if fresh:
        await session.execute(
            text(
                "UPDATE fixtures SET has_odds=TRUE, updated_at=now() WHERE id IN (SELECT unnest(CAST(:ids AS integer[])))"
            ),
            {"ids": list(fresh)},
        )
    return missing, fresh


def _extract_xg(stat_json: dict) -> Optional[float]:
    for stat in stat_json.get("statistics", []):
        t = (stat.get("type") or "").lower()
        if t in {"expected goals", "expected_goals"}:
            val = stat.get("value")
            try:
                return float(val) if val is not None else None
            except (TypeError, ValueError):
                return None
    return None


async def _fetch_injuries(session: AsyncSession, team_ids: list[int], league_by_team: dict[int, int] | None = None):
    seen = 0
    for tid in team_ids:
        league_id = (league_by_team or {}).get(int(tid))
        data = await get_injuries(session, tid, metric_league_id=int(league_id) if league_id is not None else None)
        for entry in data.get("response", []):
            player = entry.get("player") or {}
            team = entry.get("team") or {}
            fixture = entry.get("fixture") or {}
            injury = entry.get("injury") or entry.get("information") or entry
            if not isinstance(injury, dict):
                injury = entry

            player_name = player.get("name") or injury.get("player_name") or injury.get("name")
            reason = player.get("reason") or injury.get("reason") or injury.get("description")
            injury_type = player.get("type") or injury.get("type")
            status = player.get("status") or injury.get("status")

            team_id = team.get("id") or tid
            entry_league = entry.get("league") or {}
            entry_league_id = entry_league.get("id") if isinstance(entry_league, dict) else None
            fixture_id = fixture.get("id")
            fp_raw = "|".join(
                [
                    str(player_name or ""),
                    str(team_id or ""),
                    str(entry_league_id or league_id or ""),
                    str(fixture_id or ""),
                    str(reason or ""),
                    str(injury_type or ""),
                    str(status or ""),
                ]
            )
            fingerprint = hashlib.sha1(fp_raw.encode("utf-8")).hexdigest()
            await session.execute(
                text(
                    """
                    INSERT INTO injuries(fingerprint, player_name, team_id, league_id, fixture_id, reason, type, status, created_at)
                    VALUES(:fp, :pname, :tid, :lid, :fid, :reason, :type, :status, now())
                    ON CONFLICT DO NOTHING
                    """
                ),
                {
                    "fp": fingerprint,
                    "pname": player_name,
                    "tid": team_id,
                    "lid": entry_league_id or league_id,
                    "fid": fixture_id,
                    "reason": reason,
                    "type": injury_type,
                    "status": status,
                },
            )
            seen += 1
        await _rate_limit()
    if seen:
        log.info("sync_data injuries saved=%s", seen)


async def _cleanup_injuries(session: AsyncSession):
    ttl_days = int(getattr(settings, "injuries_ttl_days", 30) or 30)
    if ttl_days <= 0:
        return
    await session.execute(
        text(
            """
            DELETE FROM injuries
            WHERE created_at < now() - (cast(:days as int) * interval '1 day')
            """
        ),
        {"days": ttl_days},
    )


async def _sync_standings(session: AsyncSession) -> int:
    if not settings.enable_standings:
        return 0
    updated = 0
    for lid in settings.league_ids:
        data = await get_standings(session, lid, settings.season)
        for resp in data.get("response", []):
            league = resp.get("league") or {}
            standings = league.get("standings") or []
            if not isinstance(standings, list):
                continue
            groups = standings
            for group in groups:
                if not isinstance(group, list):
                    continue
                for row in group:
                    if not isinstance(row, dict):
                        continue
                    team = row.get("team") or {}
                    team_id = team.get("id")
                    if not team_id:
                        continue
                    rank = row.get("rank")
                    points = row.get("points")
                    goals_diff = row.get("goalsDiff") or row.get("goals_diff")
                    form = row.get("form")
                    all_stats = row.get("all") or {}
                    played = all_stats.get("played")
                    goals_for = (all_stats.get("goals") or {}).get("for") if isinstance(all_stats.get("goals"), dict) else None
                    goals_against = (all_stats.get("goals") or {}).get("against") if isinstance(all_stats.get("goals"), dict) else None
                    await session.execute(
                        text(
                            """
                            INSERT INTO team_standings(
                              team_id, league_id, season,
                              rank, points, played,
                              goals_for, goals_against, goal_diff,
                              form, updated_at
                            )
                            VALUES(:tid, :lid, :season, :rank, :points, :played, :gf, :ga, :gd, :form, now())
                            ON CONFLICT (team_id, league_id, season) DO UPDATE SET
                              rank=:rank, points=:points, played=:played,
                              goals_for=:gf, goals_against=:ga, goal_diff=:gd,
                              form=:form, updated_at=now()
                            """
                        ),
                        {
                            "tid": int(team_id),
                            "lid": int(lid),
                            "season": int(settings.season),
                            "rank": int(rank) if rank is not None else None,
                            "points": int(points) if points is not None else None,
                            "played": int(played) if played is not None else None,
                            "gf": int(goals_for) if goals_for is not None else None,
                            "ga": int(goals_against) if goals_against is not None else None,
                            "gd": int(goals_diff) if goals_diff is not None else None,
                            "form": str(form) if form is not None else None,
                        },
                    )
                    updated += 1
        await _rate_limit()
    if updated:
        log.info("sync_data standings upserted=%s", updated)
    return updated


async def _backfill_snapshots_from_odds(session: AsyncSession, fixture_ids: list[int]) -> int:
    if not fixture_ids:
        return 0
    res = await session.execute(
        text(
            """
            INSERT INTO odds_snapshots(
              fixture_id, bookmaker_id, fetched_at,
              home_win, draw, away_win, over_2_5, under_2_5,
              market_avg_home_win, market_avg_draw, market_avg_away_win,
              market_avg_over_2_5, market_avg_under_2_5
            )
            SELECT
              o.fixture_id, o.bookmaker_id, o.fetched_at,
              o.home_win, o.draw, o.away_win, o.over_2_5, o.under_2_5,
              o.market_avg_home_win, o.market_avg_draw, o.market_avg_away_win,
              o.market_avg_over_2_5, o.market_avg_under_2_5
            FROM odds o
            JOIN fixtures f ON f.id=o.fixture_id
            WHERE o.fixture_id IN (SELECT unnest(CAST(:ids AS integer[])))
              AND o.bookmaker_id = :bid
              AND o.fetched_at IS NOT NULL
              AND f.kickoff IS NOT NULL
              AND o.fetched_at < f.kickoff
            ON CONFLICT DO NOTHING
            """
        ),
        {"ids": fixture_ids, "bid": settings.bookmaker_id},
    )
    try:
        return int(res.rowcount or 0)
    except Exception:
        return 0


async def _compute_league_baselines(session: AsyncSession, league_id: int, season: int, as_of: datetime):
    if not settings.enable_league_baselines:
        return
    date_key = as_of.date()
    res = await session.execute(
        text(
            """
            SELECT
              AVG(COALESCE(home_xg, home_goals)) AS avg_home_xg,
              AVG(COALESCE(away_xg, away_goals)) AS avg_away_xg,
              COUNT(*) FILTER (WHERE home_goals = away_goals) AS draws,
              COUNT(*) AS total,
              AVG((home_goals + away_goals)::numeric) AS avg_goals
            FROM fixtures
            WHERE league_id=:lid AND season=:season AND status IN ('FT','AET','PEN') AND kickoff::date < :dt
            """
        ),
        {"lid": league_id, "season": season, "dt": date_key},
    )
    row = res.first()
    if not row or not row.total:
        return
    draw_freq = q_prob(D(row.draws or 0) / D(row.total))

    # League-specific params (rho + probability calibration) are computed from DB only.
    # Keep these light: fall back to safe defaults when data is insufficient.
    base_home = q_money(row.avg_home_xg) if row.avg_home_xg is not None else q_money(1)
    base_away = q_money(row.avg_away_xg) if row.avg_away_xg is not None else q_money(1)
    rho = await estimate_dixon_coles_rho(
        session,
        league_id=league_id,
        season=season,
        before_date=date_key,
        lam_home=base_home,
        lam_away=base_away,
    )
    prob_source = (
        "hybrid"
        if settings.use_hybrid_probs
        else "logistic"
        if settings.use_logistic_probs
        else "dixon_coles"
        if settings.use_dixon_coles_probs
        else "poisson"
    )
    alpha = await estimate_power_calibration_alpha(
        session,
        league_id=league_id,
        season=season,
        before_date=date_key,
        prob_source=prob_source,
    )
    rho_val = rho if rho is not None else q_prob(D(0))
    alpha_val = alpha if alpha is not None else q_prob(D(1))
    override = settings.calib_alpha_overrides.get(int(league_id))
    if override is not None:
        alpha_val = override

    await session.execute(
        text(
            """
            INSERT INTO league_baselines(
              league_id, season, date_key,
              avg_home_xg, avg_away_xg, draw_freq, avg_goals,
              dc_rho, calib_alpha
            )
            VALUES(:lid, :season, :dk, :ah, :aa, :df, :avg_goals, :rho, :alpha)
            ON CONFLICT (league_id, season, date_key) DO UPDATE SET
              avg_home_xg=:ah,
              avg_away_xg=:aa,
              draw_freq=:df,
              avg_goals=:avg_goals,
              dc_rho=:rho,
              calib_alpha=:alpha
            """
        ),
        {
            "lid": league_id,
            "season": season,
            "dk": date_key,
            "ah": row.avg_home_xg,
            "aa": row.avg_away_xg,
            "df": draw_freq,
            "avg_goals": row.avg_goals,
            "rho": rho_val,
            "alpha": alpha_val,
        },
    )
    log.info("sync_data league_baselines cached lid=%s season=%s date=%s", league_id, season, date_key)


async def _update_fixture_stats(session: AsyncSession, fixture_id: int, home_xg: Optional[float], away_xg: Optional[float]):
    max_attempts = int(getattr(settings, "stats_max_attempts", 6) or 6)
    hxg = q_xg(home_xg) if home_xg is not None else None
    axg = q_xg(away_xg) if away_xg is not None else None
    # Only set stats_downloaded when both xG values are present.
    await session.execute(
        text(
            """
            UPDATE fixtures
            SET home_xg = COALESCE(:hxg, home_xg),
                away_xg = COALESCE(:axg, away_xg),
                stats_attempted_at = now(),
                stats_attempts = COALESCE(stats_attempts, 0) + 1,
                stats_error = CASE
                  WHEN (COALESCE(:hxg, home_xg) IS NOT NULL AND COALESCE(:axg, away_xg) IS NOT NULL) THEN NULL
                  ELSE 'missing_xg'
                END,
                stats_downloaded = CASE
                  WHEN (COALESCE(:hxg, home_xg) IS NOT NULL AND COALESCE(:axg, away_xg) IS NOT NULL) THEN TRUE
                  ELSE FALSE
                END,
                stats_gave_up = CASE
                  WHEN (COALESCE(:hxg, home_xg) IS NOT NULL AND COALESCE(:axg, away_xg) IS NOT NULL) THEN FALSE
                  WHEN (COALESCE(stats_attempts, 0) + 1) >= :max_attempts THEN TRUE
                  ELSE COALESCE(stats_gave_up, FALSE)
                END,
                updated_at = now()
            WHERE id=:fid
            """
        ),
        {"hxg": hxg, "axg": axg, "fid": fixture_id, "max_attempts": max_attempts},
    )


async def _mark_stats_attempt_failed(session: AsyncSession, fixture_id: int, error: str):
    max_attempts = int(getattr(settings, "stats_max_attempts", 6) or 6)
    await session.execute(
        text(
            """
            UPDATE fixtures
            SET stats_attempted_at = now(),
                stats_attempts = COALESCE(stats_attempts, 0) + 1,
                stats_error = :err,
                stats_gave_up = CASE
                  WHEN (COALESCE(stats_attempts, 0) + 1) >= :max_attempts THEN TRUE
                  ELSE COALESCE(stats_gave_up, FALSE)
                END,
                updated_at = now()
            WHERE id=:fid
            """
        ),
        {"fid": fixture_id, "err": (error or "")[:500], "max_attempts": max_attempts},
    )


async def _fetch_xg_for_fixture(
    session: AsyncSession,
    fixture_id: int,
    home_team_id: int,
    away_team_id: int,
    *,
    metric_league_id: int | None = None,
) -> Tuple[Optional[float], Optional[float]]:
    data = await get_fixture_statistics(session, fixture_id, metric_league_id=metric_league_id)
    home_xg = away_xg = None
    for entry in data.get("response", []):
        team = entry.get("team") or {}
        tid = team.get("id")
        if tid is None:
            continue
        stats = _extract_xg(entry)
        if tid == home_team_id:
            home_xg = stats
        elif tid == away_team_id:
            away_xg = stats
    return home_xg, away_xg


async def _sync_stats(session: AsyncSession, league_ids: Iterable[int]) -> Tuple[int, int]:
    cutoff = utcnow() - timedelta(days=settings.backfill_days)
    res = await session.execute(
        text(
            """
            SELECT id, league_id, home_team_id, away_team_id, stats_attempts, stats_attempted_at
            FROM fixtures
            WHERE stats_downloaded IS NOT TRUE
              AND stats_gave_up IS NOT TRUE
              AND status IN ('FT','AET','PEN')
              AND league_id IN (SELECT unnest(CAST(:leagues AS integer[])))
              AND kickoff >= :cutoff
            ORDER BY kickoff DESC
            LIMIT :lim
            """
        ),
        {"leagues": list(league_ids), "lim": settings.stats_batch_limit, "cutoff": cutoff},
    )
    rows = res.fetchall()
    updated = missing = 0
    missing_ids = []
    batch = 0
    now_ref = utcnow()
    base_min = int(getattr(settings, "stats_retry_base_minutes", 30) or 30)
    max_min = int(getattr(settings, "stats_retry_max_minutes", 720) or 720)
    max_attempts = int(getattr(settings, "stats_max_attempts", 6) or 6)
    for row in rows:
        attempts = int(row.stats_attempts or 0)
        if attempts >= max_attempts:
            continue
        attempted_at = ensure_aware_utc(row.stats_attempted_at) if getattr(row, "stats_attempted_at", None) else None
        if attempted_at is not None and base_min > 0:
            wait = min(max_min, base_min * (2 ** max(0, attempts - 1)))
            if (now_ref - attempted_at) < timedelta(minutes=wait):
                continue
        try:
            home_xg, away_xg = await _fetch_xg_for_fixture(
                session,
                row.id,
                row.home_team_id,
                row.away_team_id,
                metric_league_id=int(row.league_id) if getattr(row, "league_id", None) is not None else None,
            )
            await _update_fixture_stats(session, row.id, home_xg, away_xg)
        except Exception as e:
            await _mark_stats_attempt_failed(session, row.id, f"fetch_failed: {e}")
            missing += 1
            missing_ids.append(row.id)
            batch += 1
            continue
        if home_xg is None or away_xg is None:
            missing += 1
            missing_ids.append(row.id)
        else:
            updated += 1
        batch += 1
        if batch % 50 == 0:
            await session.commit()
            await sleep(max(settings.backfill_rate_ms, 0) / 1000)
    if batch % 50:
        await session.commit()
    if missing_ids:
        log.info("sync_data xg missing fixtures=%s sample=%s", missing, missing_ids[:10])
    return updated, missing


async def _select_ns_fixtures(session: AsyncSession, start: datetime, end: datetime) -> list[tuple[int, datetime | None, datetime | None]]:
    res = await session.execute(
        text(
            """
            SELECT f.id, f.kickoff, o.fetched_at
            FROM fixtures f
            LEFT JOIN odds o ON o.fixture_id=f.id AND o.bookmaker_id=:bid
            WHERE status='NS'
              AND f.league_id IN (SELECT unnest(CAST(:lids AS integer[])))
              AND f.kickoff >= :start AND f.kickoff < :end
            """
        ),
        {"start": start, "end": end, "lids": settings.league_ids, "bid": settings.bookmaker_id},
    )
    return [(int(row.id), row.kickoff, row.fetched_at) for row in res.fetchall()]


def _odds_freshness_delta(kickoff: datetime | None, now_ref: datetime) -> timedelta:
    close_within = int(getattr(settings, "odds_freshness_close_within_minutes", 120) or 120)
    close_minutes = int(getattr(settings, "odds_freshness_close_minutes", 5) or 5)
    soon_within = int(getattr(settings, "odds_freshness_soon_within_minutes", 720) or 720)
    soon_minutes = int(getattr(settings, "odds_freshness_soon_minutes", 15) or 15)
    default_hours = int(getattr(settings, "odds_freshness_default_hours", 3) or 3)

    if kickoff is None:
        return timedelta(hours=max(default_hours, 1))
    try:
        kickoff_utc = ensure_aware_utc(kickoff)
        mins_to = int((kickoff_utc - now_ref).total_seconds() // 60)
    except Exception:
        return timedelta(hours=max(default_hours, 1))

    if mins_to <= close_within:
        return timedelta(minutes=max(close_minutes, 1))
    if mins_to <= soon_within:
        return timedelta(minutes=max(soon_minutes, 1))
    return timedelta(hours=max(default_hours, 1))


async def _filter_missing_odds_dynamic(
    session: AsyncSession,
    ns_rows: list[tuple[int, datetime | None, datetime | None]],
    *,
    now_ref: datetime,
) -> tuple[list[int], set[int]]:
    if not ns_rows:
        return [], set()

    missing: list[int] = []
    fresh: set[int] = set()
    for fid, kickoff, fetched_at in ns_rows:
        if fetched_at is None:
            missing.append(fid)
            continue
        delta = _odds_freshness_delta(kickoff, now_ref)
        try:
            fa = ensure_aware_utc(fetched_at)
        except Exception:
            fa = fetched_at
        if fa is not None and fa >= (now_ref - delta):
            fresh.add(fid)
        else:
            missing.append(fid)

    if fresh:
        await session.execute(
            text("UPDATE fixtures SET has_odds=TRUE, updated_at=now() WHERE id IN (SELECT unnest(CAST(:ids AS integer[])))"),
            {"ids": list(fresh)},
        )
    return missing, fresh

async def _select_fixtures_by_kickoff(session: AsyncSession, start: datetime, end: datetime) -> list[int]:
    res = await session.execute(
        text(
            """
            SELECT id FROM fixtures
            WHERE league_id IN (SELECT unnest(CAST(:lids AS integer[])))
              AND kickoff >= :start AND kickoff < :end
            """
        ),
        {"start": start, "end": end, "lids": settings.league_ids},
    )
    return [row.id for row in res.fetchall()]


async def _fixtures_with_pre_kickoff_odds(session: AsyncSession, fixture_ids: list[int]) -> set[int]:
    if not fixture_ids:
        return set()
    res = await session.execute(
        text(
            """
            SELECT os.fixture_id
            FROM odds_snapshots os
            JOIN fixtures f ON f.id=os.fixture_id
            WHERE os.fixture_id IN (SELECT unnest(CAST(:ids AS integer[])))
              AND os.bookmaker_id = :bid
              AND os.fetched_at < f.kickoff
            """
        ),
        {"ids": fixture_ids, "bid": settings.bookmaker_id},
    )
    return {row.fixture_id for row in res.fetchall()}


async def run(session: AsyncSession, force_refresh: bool = False):
    if (settings.api_football_key or "").strip() in {"", "YOUR_KEY", "your_paid_key"}:
        raise RuntimeError("API_FOOTBALL_KEY is not configured; cannot run sync_data")
    token = set_force_refresh(force_refresh)
    try:
        reset_api_metrics()
        set_run_cache_miss_budget(int(getattr(settings, "api_football_run_budget_cache_misses", 0) or 0))
        now_utc = utcnow()

        quota = None
        if bool(getattr(settings, "api_football_guard_enabled", True)):
            quota = await quota_guard_decision(
                session,
                now=now_utc,
                daily_limit=int(getattr(settings, "api_football_daily_limit", 7500) or 7500),
                guard_margin=int(getattr(settings, "api_football_guard_margin", 100) or 100),
            )
            if quota.get("blocked"):
                reason = quota.get("reason") or "quota_guard"
                quota_exhausted = reason in {"quota_error_seen", "daily_limit_reached"}
                log.warning(
                    "sync_data skipped by quota guard reason=%s used_cache_misses=%s limit=%s reset_at=%s",
                    reason,
                    quota.get("used_cache_misses"),
                    quota.get("daily_limit"),
                    quota.get("reset_at"),
                )
                return {
                    "skipped": True,
                    "skip_reason": reason,
                    "quota_exhausted": bool(quota_exhausted),
                    "quota_blocked_until": quota.get("reset_at") if quota_exhausted else None,
                    "quota_guard": quota,
                    "api_football": get_api_metrics(),
                    "backtest": bool(settings.backtest_mode),
                    "backtest_day": settings.backtest_current_date,
                }

        log.info("sync_data start leagues=%s season=%s", len(settings.league_ids), settings.season)
        window_start = now_utc - timedelta(days=2)
        window_end = now_utc + timedelta(days=7)
        odds_lookahead_hours = int(getattr(settings, "sync_data_odds_lookahead_hours", 7 * 24) or 7 * 24)
        odds_window_end = window_end
        if odds_lookahead_hours <= 0:
            odds_window_end = now_utc
        else:
            cap = now_utc + timedelta(hours=odds_lookahead_hours)
            if cap < odds_window_end:
                odds_window_end = cap

        fixtures_upserted = 0
        snapshots_saved = 0
        stats_updated = stats_missing = 0
        standings_upserted = 0
        quota_blocked_until = None
        for lid in settings.league_ids:
            data = await get_fixtures(session, lid, settings.season, window_start, window_end)
            for item in data.get("response", []):
                await _upsert_fixture(session, item)
                fixtures_upserted += 1
            await _rate_limit()

        if settings.backtest_mode:
            bt_kind = (settings.backtest_kind or "pseudo").strip().lower()
            day_start = now_utc
            day_end = now_utc + timedelta(days=1)
            target_fixture_ids = await _select_fixtures_by_kickoff(session, day_start, day_end)
            safe_odds = await _fixtures_with_pre_kickoff_odds(session, target_fixture_ids)
            missing_odds = [fid for fid in target_fixture_ids if fid not in safe_odds]
            if safe_odds:
                await session.execute(
                    text("UPDATE fixtures SET has_odds=TRUE, updated_at=now() WHERE id IN (SELECT unnest(CAST(:ids AS integer[])))"),
                    {"ids": list(safe_odds)},
                )
            if bt_kind == "true":
                fixtures_with_odds = safe_odds
            else:
                refreshed, snapshots_saved = await _refresh_odds(session, missing_odds)
                fixtures_with_odds = safe_odds | refreshed
            ns_fixture_ids = []
        else:
            ns_rows = await _select_ns_fixtures(session, window_start, odds_window_end)
            ns_fixture_ids = [r[0] for r in ns_rows]
            if force_refresh:
                missing_odds = [r[0] for r in ns_rows]
                fresh_odds = set()
            else:
                missing_odds, fresh_odds = await _filter_missing_odds_dynamic(session, ns_rows, now_ref=now_utc)
            refreshed, snapshots_saved = await _refresh_odds(session, missing_odds)
            fixtures_with_odds = fresh_odds | refreshed
            # Ensure odds_snapshots has at least a baseline row for existing pre-kickoff odds.
            snapshots_saved += await _backfill_snapshots_from_odds(session, ns_fixture_ids)

        if settings.enable_xg and int(getattr(settings, "stats_batch_limit", 0) or 0) > 0:
            stats_updated, stats_missing = await _sync_stats(session, settings.league_ids)

        standings_upserted = await _sync_standings(session) if settings.enable_standings else 0

        if settings.enable_injuries and ns_fixture_ids:
            res = await session.execute(
                text(
                    """
                    SELECT DISTINCT home_team_id, away_team_id, league_id
                    FROM fixtures
                    WHERE id IN (SELECT unnest(CAST(:ids AS integer[])))
                    """
                ),
                {"ids": ns_fixture_ids},
            )
            teams: list[int] = []
            team_league: dict[int, int] = {}
            for r in res.fetchall():
                teams.extend([r.home_team_id, r.away_team_id])
                if r.home_team_id and r.league_id:
                    team_league[int(r.home_team_id)] = int(r.league_id)
                if r.away_team_id and r.league_id:
                    team_league[int(r.away_team_id)] = int(r.league_id)
            team_ids = list({t for t in teams if t})
            if team_ids:
                default_lid = settings.league_ids[0] if settings.league_ids else None
                if default_lid is not None:
                    for tid in team_ids:
                        team_league.setdefault(int(tid), int(default_lid))
                await _fetch_injuries(session, [int(t) for t in team_ids], team_league)
                await _cleanup_injuries(session)

        if settings.enable_league_baselines:
            for lid in settings.league_ids:
                await _compute_league_baselines(session, lid, settings.season, now_utc)
    except Exception as e:
        if isinstance(e, ApiFootballBudgetExceeded):
            log.warning("sync_data run budget exhausted; committing partial updates and stopping early: %s", e)
            await session.commit()
            return {
                "skipped": True,
                "skip_reason": "budget_exhausted",
                "budget_exhausted": True,
                "error": str(e),
                "progress": {
                    "fixtures_upserted": fixtures_upserted,
                    "odds_snapshots_saved": snapshots_saved,
                    "stats_updated": stats_updated,
                    "standings_upserted": standings_upserted,
                },
                "api_football": get_api_metrics(),
                "quota_guard": quota,
                "backtest": bool(settings.backtest_mode),
                "backtest_day": settings.backtest_current_date,
            }
        if is_api_football_quota_error(e):
            quota_now = await quota_guard_decision(
                session,
                now=now_utc,
                daily_limit=int(getattr(settings, "api_football_daily_limit", 7500) or 7500),
                guard_margin=int(getattr(settings, "api_football_guard_margin", 100) or 100),
            )
            quota_blocked_until = quota_now.get("reset_at")
            log.warning("sync_data quota exhausted; committing partial updates and skipping until %s", quota_blocked_until)
            await session.commit()
            return {
                "skipped": True,
                "skip_reason": "quota_exhausted",
                "quota_exhausted": True,
                "quota_blocked_until": quota_blocked_until,
                "quota_guard": quota_now,
                "error": str(e),
                "progress": {
                    "fixtures_upserted": fixtures_upserted,
                    "odds_snapshots_saved": snapshots_saved,
                    "stats_updated": stats_updated,
                    "standings_upserted": standings_upserted,
                },
                "api_football": get_api_metrics(),
                "backtest": bool(settings.backtest_mode),
                "backtest_day": settings.backtest_current_date,
            }
        raise
    else:
        await session.commit()
        log.info(
            "sync_data done fixtures=%s odds_fixtures=%s odds_snapshots_saved=%s stats_updated=%s stats_missing=%s standings=%s",
            fixtures_upserted,
            len(fixtures_with_odds),
            snapshots_saved,
            stats_updated,
            stats_missing,
            standings_upserted,
        )
        api_metrics = get_api_metrics()
        return {
            "fixtures_upserted": fixtures_upserted,
            "odds_fixtures": len(fixtures_with_odds),
            "odds_snapshots_saved": snapshots_saved,
            "stats_updated": stats_updated,
            "stats_missing": stats_missing,
            "standings_upserted": standings_upserted,
            "api_football": api_metrics,
            "quota_guard": quota,
            "backtest": bool(settings.backtest_mode),
            "backtest_day": settings.backtest_current_date,
        }
    finally:
        reset_force_refresh(token)
