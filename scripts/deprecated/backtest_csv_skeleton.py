"""
Deprecated CSV skeleton backtest.

Ожидает CSV football-data.co.uk в формате:
Date, HomeTeam, AwayTeam, FTHG, FTAG, HY, AY, HR, AR, B365H, B365D, B365A (или closing odds)
"""
import pandas as pd
from datetime import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from scripts.deprecated.domain.indices import (
    PastMatch,
    compute_fatigue_features,
    compute_fatigue_raw,
    compute_chaos_features,
    compute_chaos_raw,
)
from scripts.deprecated.domain.signals import build_signals


def run_backtest(csv_path: str):
    df = pd.read_csv(csv_path)
    df["Date"] = pd.to_datetime(df["Date"], dayfirst=True)

    history: dict[str, list[PastMatch]] = {}
    results = []

    for _, row in df.sort_values("Date").iterrows():
        dt = row["Date"].to_pydatetime()
        home = row["HomeTeam"]
        away = row["AwayTeam"]
        hg = int(row["FTHG"])
        ag = int(row["FTAG"])
        hy = int(row.get("HY", 0))
        ay = int(row.get("AY", 0))
        hr = int(row.get("HR", 0))
        ar = int(row.get("AR", 0))

        home_past = history.get(home, [])
        away_past = history.get(away, [])

        home_feat = compute_fatigue_features(home_past, dt)
        away_feat = compute_fatigue_features(away_past, dt)
        home_raw = compute_fatigue_raw(home_feat, None)
        away_raw = compute_fatigue_raw(away_feat, None)

        home_chaos_feat = compute_chaos_features(home_past[:10])
        away_chaos_feat = compute_chaos_features(away_past[:10])
        chaos_match = (compute_chaos_raw(home_chaos_feat) + compute_chaos_raw(away_chaos_feat)) / 2

        sig = build_signals(home, away, home_raw, away_raw, chaos_match, fav_side="home")
        if sig:
            main_pick = sig.picks[0]
            odd = row.get("B365D") if main_pick in ("X2", "1X") else row.get("B365H")
            if pd.notna(odd) and float(odd) >= 1.80:
                win = False
                if main_pick == "X2":
                    win = hg <= ag
                if main_pick == "1X":
                    win = hg >= ag
                if main_pick.endswith("win"):
                    if main_pick.startswith(home):
                        win = hg > ag
                    else:
                        win = ag > hg

                results.append({"date": dt, "pick": main_pick, "odd": float(odd), "win": win})

        history.setdefault(home, []).insert(0, PastMatch(dt, True, hg, ag, hy, hr))
        history.setdefault(away, []).insert(0, PastMatch(dt, False, ag, hg, ay, ar))

    res_df = pd.DataFrame(results)
    if res_df.empty:
        print("No signals")
        return

    res_df["profit"] = res_df.apply(lambda r: (r.odd - 1) if r.win else -1, axis=1)
    roi = res_df.profit.sum() / len(res_df)
    hit = res_df.win.mean()
    print("Bets:", len(res_df), "ROI:", round(roi * 100, 2), "%", "HitRate:", round(hit * 100, 2), "%")


if __name__ == "__main__":
    run_backtest("data/E0.csv")
