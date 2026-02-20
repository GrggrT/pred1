from __future__ import annotations

from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.decimalutils import D, q_money, q_prob
from app.core.logger import get_logger
from app.core.timeutils import utcnow
from app.services.league_model_params import estimate_dixon_coles_rho, estimate_power_calibration_alpha

log = get_logger("jobs.maintenance")


async def _cleanup_api_cache(session: AsyncSession) -> dict:
    res_err = await session.execute(
        text(
            """
            DELETE FROM api_cache
            WHERE (payload ? 'errors')
              AND payload->'errors' IS NOT NULL
              AND payload->'errors'::text NOT IN ('{}','[]','null')
            """
        )
    )
    deleted_error = int(res_err.rowcount or 0)

    res = await session.execute(text("DELETE FROM api_cache WHERE expires_at <= now()"))
    deleted_expired = int(res.rowcount or 0)

    max_rows = int(getattr(settings, "api_cache_max_rows", 0) or 0)
    deleted_overflow = 0
    if max_rows > 0:
        cnt_row = (await session.execute(text("SELECT COUNT(*) AS cnt FROM api_cache"))).first()
        total = int(cnt_row.cnt or 0) if cnt_row else 0
        overflow = total - max_rows
        if overflow > 0:
            res2 = await session.execute(
                text(
                    """
                    DELETE FROM api_cache
                    WHERE cache_key IN (
                      SELECT cache_key
                      FROM api_cache
                      ORDER BY expires_at ASC
                      LIMIT :lim
                    )
                    """
                ),
                {"lim": int(overflow)},
            )
            deleted_overflow = int(res2.rowcount or 0)

    return {
        "api_cache_deleted_error": deleted_error,
        "api_cache_deleted_expired": deleted_expired,
        "api_cache_deleted_overflow": deleted_overflow,
    }


async def _cleanup_job_runs(session: AsyncSession) -> dict:
    days = int(getattr(settings, "job_runs_retention_days", 90) or 90)
    if days <= 0:
        return {"job_runs_deleted": 0}
    cutoff = utcnow() - timedelta(days=days)
    res = await session.execute(
        text(
            """
            DELETE FROM job_runs
            WHERE finished_at IS NOT NULL
              AND finished_at < :cutoff
            """
        ),
        {"cutoff": cutoff},
    )
    return {"job_runs_deleted": int(res.rowcount or 0)}


async def _cleanup_odds_snapshots(session: AsyncSession) -> dict:
    days = int(getattr(settings, "odds_snapshots_retention_days", 0) or 0)
    if days <= 0:
        return {"odds_snapshots_deleted": 0}
    cutoff = utcnow() - timedelta(days=days)
    res = await session.execute(
        text("DELETE FROM odds_snapshots WHERE fetched_at < :cutoff"),
        {"cutoff": cutoff},
    )
    return {"odds_snapshots_deleted": int(res.rowcount or 0)}


async def _refresh_league_model_params(session: AsyncSession) -> dict:
    if not getattr(settings, "enable_league_baselines", True):
        return {"league_baselines_refreshed": 0}

    today = utcnow().date()
    season = int(getattr(settings, "season", 0) or 0)
    if not season:
        return {"league_baselines_refreshed": 0}

    prob_source = (
        "hybrid"
        if settings.use_hybrid_probs
        else "logistic"
        if settings.use_logistic_probs
        else "dixon_coles"
        if settings.use_dixon_coles_probs
        else "poisson"
    )

    refreshed = 0
    for lid in settings.league_ids:
        row = (
            await session.execute(
                text(
                    """
                    SELECT avg_home_xg, avg_away_xg, draw_freq, avg_goals
                    FROM league_baselines
                    WHERE league_id=:lid AND season=:season AND date_key=:dk
                    """
                ),
                {"lid": int(lid), "season": season, "dk": today},
            )
        ).first()

        if row:
            base_home = q_money(row.avg_home_xg) if row.avg_home_xg is not None else q_money(1)
            base_away = q_money(row.avg_away_xg) if row.avg_away_xg is not None else q_money(1)
            draw_freq = q_prob(row.draw_freq) if row.draw_freq is not None else q_prob(D("0.22"))
            avg_goals = row.avg_goals
        else:
            # Fallback: compute from fixtures (DB-only).
            stats = (
                await session.execute(
                    text(
                        """
                        SELECT
                          AVG(COALESCE(home_xg, home_goals)) AS avg_home_xg,
                          AVG(COALESCE(away_xg, away_goals)) AS avg_away_xg,
                          COUNT(*) FILTER (WHERE home_goals = away_goals) AS draws,
                          COUNT(*) AS total,
                          AVG((home_goals + away_goals)::numeric) AS avg_goals
                        FROM fixtures
                        WHERE league_id=:lid AND season=:season
                          AND status IN ('FT','AET','PEN')
                          AND kickoff::date < :dt
                        """
                    ),
                    {"lid": int(lid), "season": season, "dt": today},
                )
            ).first()
            if not stats or not stats.total:
                continue
            base_home = q_money(stats.avg_home_xg) if stats.avg_home_xg is not None else q_money(1)
            base_away = q_money(stats.avg_away_xg) if stats.avg_away_xg is not None else q_money(1)
            draw_freq = q_prob(D(stats.draws or 0) / D(stats.total))
            avg_goals = stats.avg_goals

        rho = await estimate_dixon_coles_rho(
            session,
            league_id=int(lid),
            season=season,
            before_date=today,
            lam_home=base_home,
            lam_away=base_away,
        )
        alpha = await estimate_power_calibration_alpha(
            session,
            league_id=int(lid),
            season=season,
            before_date=today,
            prob_source=prob_source,
        )
        override = settings.calib_alpha_overrides.get(int(lid))
        if override is not None:
            alpha = override
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
                "lid": int(lid),
                "season": season,
                "dk": today,
                "ah": base_home,
                "aa": base_away,
                "df": draw_freq,
                "avg_goals": avg_goals,
                "rho": rho if rho is not None else q_prob(D(0)),
                "alpha": alpha if alpha is not None else q_prob(D(1)),
            },
        )
        refreshed += 1

    return {"league_baselines_refreshed": refreshed}


async def run(session: AsyncSession) -> dict:
    log.info("maintenance start")
    out: dict = {}
    out.update(await _cleanup_api_cache(session))
    out.update(await _cleanup_job_runs(session))
    out.update(await _cleanup_odds_snapshots(session))
    out.update(await _refresh_league_model_params(session))
    await session.commit()
    log.info("maintenance done %s", out)
    return out
