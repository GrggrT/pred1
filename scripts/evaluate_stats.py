import asyncio
from decimal import Decimal

from sqlalchemy import text

from app.core.db import SessionLocal, init_db


QUERY = """
SELECT
  COUNT(*) FILTER (WHERE selection_code!='SKIP' AND status!='VOID') AS total_bets,
  COUNT(*) FILTER (WHERE selection_code!='SKIP' AND status='WIN') AS wins,
  COUNT(*) FILTER (WHERE selection_code!='SKIP' AND status='LOSS') AS losses,
  COALESCE(SUM(profit) FILTER (WHERE selection_code!='SKIP' AND status IN ('WIN','LOSS')), 0) AS profit,
  COALESCE(SUM(profit * signal_score) FILTER (WHERE selection_code!='SKIP' AND status IN ('WIN','LOSS') AND signal_score IS NOT NULL), 0) AS weighted_profit,
  COALESCE(SUM(signal_score) FILTER (WHERE selection_code!='SKIP' AND status IN ('WIN','LOSS') AND signal_score IS NOT NULL), 0) AS weight_sum,
  COALESCE(SUM(CASE WHEN status='WIN' THEN signal_score ELSE 0 END) FILTER (WHERE selection_code!='SKIP' AND status IN ('WIN','LOSS') AND signal_score IS NOT NULL), 0) AS weighted_wins,
  AVG(signal_score) FILTER (WHERE selection_code!='SKIP' AND signal_score IS NOT NULL) AS avg_signal,
  COUNT(*) FILTER (WHERE selection_code!='SKIP' AND signal_score >= 0.7) AS strong_signals
FROM predictions
"""


async def main():
    await init_db()
    async with SessionLocal() as session:
        res = await session.execute(text(QUERY))
        row = res.first()
        total_bets = int(row.total_bets or 0)
        wins = int(row.wins or 0)
        losses = int(row.losses or 0)
        settled = wins + losses
        profit = Decimal(row.profit or 0)
        roi = float(profit / Decimal(settled)) if settled else 0.0
        win_rate = float(Decimal(wins) / Decimal(settled)) if settled else 0.0
        weight_sum = Decimal(row.weight_sum or 0)
        weighted_profit = Decimal(row.weighted_profit or 0)
        weighted_wins = Decimal(row.weighted_wins or 0)
        weighted_roi = float(weighted_profit / weight_sum) if weight_sum else 0.0
        weighted_win_rate = float(weighted_wins / weight_sum) if weight_sum else 0.0
        avg_signal = float(row.avg_signal or 0)
        strong_signals = int(row.strong_signals or 0)

        print("Total bets:", total_bets)
        print("Wins/Losses:", wins, losses)
        print("ROI:", round(roi * 100, 2))
        print("Win rate:", round(win_rate * 100, 2))
        print("Weighted ROI:", round(weighted_roi * 100, 2))
        print("Weighted Win rate:", round(weighted_win_rate * 100, 2))
        print("Avg signal:", round(avg_signal, 3))
        print("Strong signals (>=0.7):", strong_signals)


if __name__ == "__main__":
    asyncio.run(main())
