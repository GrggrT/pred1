"""Job: fit Dixon-Coles model per league.

Estimates latent attack/defense parameters for each active league
and persists snapshots to team_strength_params and dc_global_params.

Supports dual-mode: always fits DC-goals, optionally also fits DC-xG
(when DC_USE_XG=true). Both sets of params are stored with param_source
='goals' / 'xg' respectively.
"""

from __future__ import annotations

import time
from datetime import date, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logger import get_logger
from app.core.timeutils import utcnow
from app.services.dixon_coles import MatchData, fit_dixon_coles

log = get_logger("jobs.fit_dixon_coles")

MIN_MATCHES = 30
DEFAULT_XI = 0.005


async def _persist_dc_params(
    session: AsyncSession,
    params,
    lid: int,
    season: int,
    as_of: date,
    fit_time: float,
    param_source: str,
) -> None:
    """Persist DC params (team strength + global) with param_source tag."""
    for team_id, att_val in params.attack.items():
        def_val = params.defense[team_id]
        await session.execute(
            text("""
                INSERT INTO team_strength_params
                    (team_id, league_id, season, as_of_date, attack, defense, param_source)
                VALUES (:tid, :lid, :season, :as_of, :att, :def, :src)
                ON CONFLICT (team_id, league_id, season, as_of_date, param_source)
                DO UPDATE SET attack = :att, defense = :def, created_at = now()
            """),
            {
                "tid": team_id,
                "lid": lid,
                "season": season,
                "as_of": as_of,
                "att": att_val,
                "def": def_val,
                "src": param_source,
            },
        )

    await session.execute(
        text("""
            INSERT INTO dc_global_params
                (league_id, season, as_of_date, home_advantage, rho, xi,
                 log_likelihood, n_matches, n_teams, fit_seconds, param_source)
            VALUES (:lid, :season, :as_of, :ha, :rho, :xi,
                    :ll, :nm, :nt, :fs, :src)
            ON CONFLICT (league_id, season, as_of_date, param_source)
            DO UPDATE SET
                home_advantage = :ha, rho = :rho, xi = :xi,
                log_likelihood = :ll, n_matches = :nm, n_teams = :nt,
                fit_seconds = :fs, created_at = now()
        """),
        {
            "lid": lid,
            "season": season,
            "as_of": as_of,
            "ha": params.home_advantage,
            "rho": params.rho,
            "xi": params.xi,
            "ll": params.log_likelihood,
            "nm": params.n_matches,
            "nt": params.n_teams,
            "fs": round(fit_time, 2),
            "src": param_source,
        },
    )


async def run(session: AsyncSession) -> dict:
    """Fit Dixon-Coles model for each active league.

    For each league:
    1. Load completed fixtures of the current season.
    2. Skip if < MIN_MATCHES.
    3. Fit DC-goals (always).
    4. Fit DC-xG (if DC_USE_XG=true and enough xG data).
    5. Persist results to team_strength_params and dc_global_params.

    Returns:
        Summary dict with per-league results.
    """
    as_of = utcnow().date()
    league_ids = settings.league_ids
    season = settings.season
    use_xg = settings.dc_use_xg
    summary: dict[int, dict] = {}

    for lid in league_ids:
        t_start = time.monotonic()

        # Load completed fixtures
        res = await session.execute(
            text("""
                SELECT home_team_id, away_team_id, home_goals, away_goals,
                       home_xg, away_xg,
                       kickoff::date AS match_date
                FROM fixtures
                WHERE league_id = :lid
                  AND season = :season
                  AND status IN ('FT', 'AET', 'PEN')
                  AND kickoff::date < :as_of
                ORDER BY kickoff ASC
            """),
            {"lid": lid, "season": season, "as_of": as_of},
        )
        rows = res.fetchall()

        if len(rows) < MIN_MATCHES:
            log.info(
                "fit_dc skip league=%d season=%d matches=%d (need %d)",
                lid, season, len(rows), MIN_MATCHES,
            )
            summary[lid] = {"skipped": True, "n_matches": len(rows)}
            continue

        matches = [
            MatchData(
                home_id=int(r.home_team_id),
                away_id=int(r.away_team_id),
                home_goals=int(r.home_goals),
                away_goals=int(r.away_goals),
                date=r.match_date if isinstance(r.match_date, date) else r.match_date.date(),
                home_xg=float(r.home_xg) if r.home_xg is not None else None,
                away_xg=float(r.away_xg) if r.away_xg is not None else None,
            )
            for r in rows
            if r.home_goals is not None and r.away_goals is not None
        ]

        if len(matches) < MIN_MATCHES:
            log.info("fit_dc skip league=%d usable_matches=%d", lid, len(matches))
            summary[lid] = {"skipped": True, "n_matches": len(matches)}
            continue

        league_summary: dict = {}

        # --- DC-goals (always) ---
        try:
            params_goals = fit_dixon_coles(matches, ref_date=as_of, xi=DEFAULT_XI, use_xg=False)
        except Exception as exc:
            log.error("fit_dc goals failed league=%d: %s", lid, exc)
            summary[lid] = {"error": str(exc)}
            continue

        fit_time_goals = time.monotonic() - t_start
        await _persist_dc_params(session, params_goals, lid, season, as_of, fit_time_goals, "goals")

        log.info(
            "fit_dc goals done league=%d n_teams=%d n_matches=%d HA=%.4f rho=%.4f "
            "xi=%.4f ll=%.2f time=%.1fs",
            lid, params_goals.n_teams, params_goals.n_matches, params_goals.home_advantage,
            params_goals.rho, params_goals.xi, params_goals.log_likelihood, fit_time_goals,
        )

        league_summary.update({
            "n_teams": params_goals.n_teams,
            "n_matches": params_goals.n_matches,
            "home_advantage": params_goals.home_advantage,
            "rho": params_goals.rho,
            "xi": params_goals.xi,
            "fit_seconds_goals": round(fit_time_goals, 2),
        })

        # --- DC-xG (optional) ---
        if use_xg:
            t_xg_start = time.monotonic()
            n_with_xg = sum(1 for m in matches if m.home_xg is not None and m.away_xg is not None)
            if n_with_xg >= MIN_MATCHES:
                try:
                    params_xg = fit_dixon_coles(matches, ref_date=as_of, xi=DEFAULT_XI, use_xg=True)
                    fit_time_xg = time.monotonic() - t_xg_start
                    await _persist_dc_params(session, params_xg, lid, season, as_of, fit_time_xg, "xg")

                    log.info(
                        "fit_dc xG done league=%d n_teams=%d n_matches=%d HA=%.4f "
                        "rho=%.4f ll=%.2f time=%.1fs",
                        lid, params_xg.n_teams, params_xg.n_matches,
                        params_xg.home_advantage, params_xg.rho,
                        params_xg.log_likelihood, fit_time_xg,
                    )
                    league_summary["fit_seconds_xg"] = round(fit_time_xg, 2)
                    league_summary["n_matches_xg"] = params_xg.n_matches
                except Exception as exc:
                    log.warning("fit_dc xG failed league=%d: %s", lid, exc)
                    league_summary["xg_error"] = str(exc)
            else:
                log.info("fit_dc xG skip league=%d n_with_xg=%d (need %d)", lid, n_with_xg, MIN_MATCHES)
                league_summary["xg_skipped"] = True

        await session.commit()
        summary[lid] = league_summary

    log.info("fit_dixon_coles done leagues=%d dc_use_xg=%s", len(summary), use_xg)
    return summary
