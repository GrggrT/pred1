from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from collections import defaultdict
import math
import json
from pathlib import Path

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.types import DateTime as SADateTime, String as SAString, ARRAY

from app.core.decimalutils import D, q_money
from app.core.config import settings
from app.core.logger import get_logger
from app.core.timeutils import ensure_aware_utc
from app.services.elo_ratings import apply_elo_from_fixtures

log = get_logger("jobs.evaluate_results")
FINAL_STATUSES = ("FT", "AET", "PEN")
CANCEL_STATUSES = ("CANC", "ABD", "AWD", "WO")


def brier_score(prob: Decimal, outcome: int) -> Decimal:
    return (prob - Decimal(outcome)) ** 2


def log_loss(prob: Decimal, outcome: int, eps=1e-15) -> Decimal:
    p = prob
    eps_dec = Decimal(str(eps))
    p = max(min(p, Decimal(1) - eps_dec), eps_dec)
    return -Decimal(outcome) * p.ln() - Decimal(1 - outcome) * (Decimal(1) - p).ln()


async def _pending_predictions(session: AsyncSession):
    res = await session.execute(
        text(
            """
            SELECT p.fixture_id, p.selection_code, p.initial_odd, p.confidence, p.feature_flags,
                   f.home_goals, f.away_goals,
                   f.home_team_id, f.away_team_id, f.kickoff
            FROM predictions p
            JOIN fixtures f ON f.id=p.fixture_id
            WHERE p.status='PENDING'
              AND f.status IN ('FT', 'AET', 'PEN')
            """
        ),
        {},
    )
    return res.fetchall()


def _resolve(selection: str, home_goals: int, away_goals: int) -> str:
    if home_goals is None or away_goals is None:
        return "VOID"
    if home_goals > away_goals:
        result = "HOME_WIN"
    elif home_goals == away_goals:
        result = "DRAW"
    else:
        result = "AWAY_WIN"
    return "WIN" if selection == result else "LOSS"


def _profit(status: str, odd: Decimal) -> Decimal:
    if status == "WIN":
        return q_money(odd - D(1))
    if status == "LOSS":
        return q_money(-1)
    return q_money(0)


def _resolve_totals(selection: str, home_goals: int, away_goals: int) -> str:
    if home_goals is None or away_goals is None:
        return "VOID"
    total = home_goals + away_goals
    if selection == "OVER_2_5":
        return "WIN" if total >= 3 else "LOSS"
    if selection == "UNDER_2_5":
        return "WIN" if total <= 2 else "LOSS"
    return "VOID"


async def _pending_totals(session: AsyncSession):
    res = await session.execute(
        text(
            """
            SELECT pt.fixture_id, pt.selection, pt.initial_odd, pt.confidence,
                   f.home_goals, f.away_goals, f.kickoff
            FROM predictions_totals pt
            JOIN fixtures f ON f.id=pt.fixture_id
            WHERE COALESCE(pt.status, 'PENDING') = 'PENDING'
              AND f.status IN ('FT', 'AET', 'PEN')
            """
        ),
        {},
    )
    return res.fetchall()

def _backtest_day_window() -> tuple[datetime | None, datetime | None]:
    if not settings.backtest_mode or not settings.backtest_current_date:
        return None, None
    try:
        day_start = datetime.fromisoformat(settings.backtest_current_date)
        day_start = ensure_aware_utc(day_start) if day_start.tzinfo else day_start.replace(tzinfo=timezone.utc)
        return day_start, day_start + timedelta(days=1)
    except Exception:
        return None, None


async def _void_cancelled_predictions(session: AsyncSession, *, day_start: datetime | None, day_end: datetime | None) -> int:
    stmt = (
        text(
            """
            UPDATE predictions p
            SET status='VOID', profit=0, settled_at=now()
            FROM fixtures f
            WHERE f.id = p.fixture_id
              AND p.selection_code != 'SKIP'
              AND p.status = 'PENDING'
              AND f.status IN ('CANC', 'ABD', 'AWD', 'WO')
              AND (:day_start IS NULL OR f.kickoff >= :day_start)
              AND (:day_end IS NULL OR f.kickoff < :day_end)
            """
        ).bindparams(
            bindparam("day_start", type_=SADateTime(timezone=True)),
            bindparam("day_end", type_=SADateTime(timezone=True)),
        )
    )
    res = await session.execute(
        stmt,
        {"day_start": day_start, "day_end": day_end},
    )
    return int(getattr(res, "rowcount", 0) or 0)


async def _void_cancelled_totals(session: AsyncSession, *, day_start: datetime | None, day_end: datetime | None) -> int:
    stmt = (
        text(
            """
            UPDATE predictions_totals pt
            SET status='VOID', profit=0, settled_at=now()
            FROM fixtures f
            WHERE f.id = pt.fixture_id
              AND pt.market = 'TOTAL'
              AND COALESCE(pt.status, 'PENDING') = 'PENDING'
              AND f.status IN ('CANC', 'ABD', 'AWD', 'WO')
              AND (:day_start IS NULL OR f.kickoff >= :day_start)
              AND (:day_end IS NULL OR f.kickoff < :day_end)
            """
        ).bindparams(
            bindparam("day_start", type_=SADateTime(timezone=True)),
            bindparam("day_end", type_=SADateTime(timezone=True)),
        )
    )
    res = await session.execute(
        stmt,
        {"day_start": day_start, "day_end": day_end},
    )
    return int(getattr(res, "rowcount", 0) or 0)


async def _settle_totals(session: AsyncSession):
    rows = await _pending_totals(session)
    day_start, day_end_exclusive = _backtest_day_window()
    elo_cutoff = day_end_exclusive if settings.backtest_mode and day_end_exclusive else None
    if day_start and day_end_exclusive:
        rows = [
            r
            for r in rows
            if getattr(r, "kickoff", None) and day_start <= ensure_aware_utc(r.kickoff) < day_end_exclusive
        ]
    if not rows:
        return 0

    updated = 0
    for row in rows:
        settlement = _resolve_totals(row.selection, row.home_goals, row.away_goals)
        if settlement == "VOID" or row.initial_odd is None:
            await session.execute(
                text(
                    """
                    UPDATE predictions_totals
                    SET status='VOID', settled_at=now()
                    WHERE fixture_id=:fid AND market='TOTAL'
                    """
                ),
                {"fid": row.fixture_id},
            )
            continue
        profit = _profit(settlement, D(row.initial_odd))
        await session.execute(
            text(
                """
                UPDATE predictions_totals
                SET status=:status, profit=:profit, settled_at=now()
                WHERE fixture_id=:fid AND market='TOTAL'
                """
            ),
            {"status": settlement, "profit": profit, "fid": row.fixture_id},
        )
        updated += 1
    return updated


async def _log_roi(session: AsyncSession):
    res = await session.execute(
        text(
            """
            SELECT COUNT(*) as total, COALESCE(SUM(profit), 0) as pnl
            FROM predictions
            WHERE selection_code!='SKIP' AND status IN ('WIN','LOSS')
            """
        )
    )
    row = res.first()
    total = row.total or 0
    if not total:
        log.info("evaluate_results roi: no settled bets yet")
        return
    pnl = D(row.pnl or 0)
    roi = pnl / D(total)
    log.info("evaluate_results roi bets=%s pnl=%.3f roi=%.2f%%", total, pnl, float(roi * 100))

    # Binned ROI by signal_score
    bins = [
        ("[0.0,0.4)", 0.0, 0.4),
        ("[0.4,0.6)", 0.4, 0.6),
        ("[0.6,0.8)", 0.6, 0.8),
        ("[0.8,1.0]", 0.8, 1.01),
    ]
    for label, lo, hi in bins:
        res_bin = await session.execute(
            text(
                """
                SELECT COUNT(*) AS total, COALESCE(SUM(profit),0) AS pnl,
                       COUNT(*) FILTER (WHERE status='WIN') AS wins
                FROM predictions
                WHERE selection_code!='SKIP'
                  AND status IN ('WIN','LOSS')
                  AND signal_score >= :lo AND signal_score < :hi
                """
            ),
            {"lo": lo, "hi": hi},
        )
        brow = res_bin.first()
        btotal = brow.total or 0
        if not btotal:
            continue
        bpnl = D(brow.pnl or 0)
        broi = bpnl / D(btotal)
        bwins = D(brow.wins or 0)
        bwinr = bwins / D(btotal)
        log.info(
            "evaluate_results bin=%s bets=%s pnl=%.3f roi=%.2f%% win_rate=%.2f%%",
            label,
            btotal,
            bpnl,
            float(broi * 100),
            float(bwinr * 100),
        )


async def run(session: AsyncSession):
    rows = await _pending_predictions(session)
    totals_rows = await _pending_totals(session)
    day_start, day_end_exclusive = _backtest_day_window()
    elo_cutoff = day_end_exclusive if settings.backtest_mode and day_end_exclusive else None
    if day_start and day_end_exclusive:
        rows = [
            r
            for r in rows
            if getattr(r, "kickoff", None) and day_start <= ensure_aware_utc(r.kickoff) < day_end_exclusive
        ]
        totals_rows = [
            r
            for r in totals_rows
            if getattr(r, "kickoff", None) and day_start <= ensure_aware_utc(r.kickoff) < day_end_exclusive
        ]
    if not rows and not totals_rows:
        log.info("evaluate_results nothing to settle")
        voided_cancelled = await _void_cancelled_predictions(session, day_start=day_start, day_end=day_end_exclusive)
        voided_cancelled_totals = await _void_cancelled_totals(session, day_start=day_start, day_end=day_end_exclusive)
        if voided_cancelled or voided_cancelled_totals:
            await session.commit()
            log.info(
                "evaluate_results voided_cancelled=%s voided_cancelled_totals=%s",
                voided_cancelled,
                voided_cancelled_totals,
            )
        elo_info = await apply_elo_from_fixtures(session, league_ids=settings.league_ids, cutoff=elo_cutoff)
        await session.commit()
        return {
            "settled": 0,
            "totals_settled": 0,
            "voided": 0,
            "voided_cancelled": voided_cancelled,
            "voided_cancelled_totals": voided_cancelled_totals,
            "elo": elo_info,
            "metrics": {},
            "backtest": bool(settings.backtest_mode),
            "backtest_day": settings.backtest_current_date,
        }

    updated = 0
    voided = 0
    metrics = defaultdict(lambda: {"brier": Decimal(0), "logloss": Decimal(0), "n": 0})

    if rows:
        for row in rows:
            settlement = _resolve(row.selection_code, row.home_goals, row.away_goals)
            if settlement == "VOID":
                await session.execute(
                    text("UPDATE predictions SET status='VOID', profit=0, settled_at=now() WHERE fixture_id=:fid"),
                    {"fid": row.fixture_id},
                )
                voided += 1
                continue
            if row.initial_odd is None:
                await session.execute(
                    text("UPDATE predictions SET status='VOID', profit=0, settled_at=now() WHERE fixture_id=:fid"),
                    {"fid": row.fixture_id},
                )
                voided += 1
                continue
            profit = _profit(settlement, D(row.initial_odd))
            await session.execute(
                text(
                    """
                    UPDATE predictions
                    SET status=:status, profit=:profit, settled_at=now()
                    WHERE fixture_id=:fid
                    """
                ),
                {"status": settlement, "profit": profit, "fid": row.fixture_id},
            )
            updated += 1

            if row.confidence is not None:
                outcome = 1 if settlement == "WIN" else 0
                prob = D(row.confidence)
                flags = row.feature_flags if isinstance(row.feature_flags, dict) else {}
                source = flags.get("prob_source", "unknown")
                metrics[source]["brier"] += brier_score(prob, outcome)
                metrics[source]["logloss"] += log_loss(prob, outcome)
                metrics[source]["n"] += 1

        await session.commit()
    # Update Elo for all finished fixtures (not only those with bets) in chronological order.
    # Idempotent via fixtures.elo_processed.
    elo_info = await apply_elo_from_fixtures(session, league_ids=settings.league_ids, cutoff=elo_cutoff)
    voided_cancelled = await _void_cancelled_predictions(session, day_start=day_start, day_end=day_end_exclusive)
    totals_updated = await _settle_totals(session)
    voided_cancelled_totals = await _void_cancelled_totals(session, day_start=day_start, day_end=day_end_exclusive)
    await session.commit()
    log.info(
        "evaluate_results settled=%s voided=%s totals_settled=%s voided_cancelled=%s voided_cancelled_totals=%s",
        updated,
        voided,
        totals_updated,
        voided_cancelled,
        voided_cancelled_totals,
    )
    await _log_roi(session)
    for src, vals in metrics.items():
        n = vals["n"]
        if not n:
            continue
        brier = (vals["brier"] / D(n)).quantize(Decimal("0.001"))
        ll = (vals["logloss"] / D(n)).quantize(Decimal("0.001"))
        log.info("metrics prob_source=%s brier=%s logloss=%s n=%s", src, brier, ll, n)
    # optional persist
    out = {
        src: {
            "brier": float((vals["brier"] / D(vals["n"])) if vals["n"] else 0),
            "logloss": float((vals["logloss"] / D(vals["n"])) if vals["n"] else 0),
            "n": vals["n"],
        }
        for src, vals in metrics.items()
    }
    if getattr(settings, "write_metrics_file", False):
        try:
            Path(str(getattr(settings, "metrics_output_path", "/tmp/metrics_eval.json"))).write_text(
                json.dumps(out, indent=2)
            )
        except Exception:
            pass
    return {
        "settled": updated,
        "totals_settled": totals_updated,
        "voided": voided,
        "voided_cancelled": voided_cancelled,
        "voided_cancelled_totals": voided_cancelled_totals,
        "elo": elo_info,
        "metrics": out,
        "backtest": bool(settings.backtest_mode),
        "backtest_day": settings.backtest_current_date,
    }
