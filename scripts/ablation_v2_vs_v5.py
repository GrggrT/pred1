"""Ablation study: v2 (13 features) vs v5 (17 features) vs per-league models.

Usage:
    python scripts/ablation_v2_vs_v5.py --from-file results/training_data_v5_full.json

Trains v2 and v5 on SAME 80/20 chronological split and compares metrics.
Also compares global vs per-league models.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.logger import get_logger
from app.services.metrics import ranked_probability_score, brier_score

log = get_logger("scripts.ablation")

# Feature sets
FEATURES_V2 = [
    "p_home_poisson", "p_draw_poisson", "p_away_poisson",
    "p_home_dc", "p_draw_dc", "p_away_dc",
    "p_home_dc_xg", "p_draw_dc_xg", "p_away_dc_xg",
    "elo_diff", "fair_home", "fair_draw", "fair_away",
]

FEATURES_V5 = [
    "p_home_poisson", "p_draw_poisson", "p_away_poisson",
    "p_home_dc", "p_draw_dc", "p_away_dc",
    "p_home_dc_xg", "p_draw_dc_xg", "p_away_dc_xg",
    "elo_diff", "fair_delta",
    "xg_momentum_home", "xg_momentum_away",
    "rest_advantage", "league_pos_delta",
    "h2h_draw_rate", "h2h_goals_avg",
]

# Intermediate: v2 base (9 probs + elo_diff) + fair_delta only
FEATURES_V2_DELTA = [
    "p_home_poisson", "p_draw_poisson", "p_away_poisson",
    "p_home_dc", "p_draw_dc", "p_away_dc",
    "p_home_dc_xg", "p_draw_dc_xg", "p_away_dc_xg",
    "elo_diff", "fair_delta",
]


def load_data(filepath: str, feature_names: list[str], league_id: int | None = None):
    """Load training data sorted by kickoff globally, extract features."""
    with open(filepath) as f:
        payload = json.load(f)

    data = payload.get("data", [])

    # Sort by kickoff globally (critical for correct chronological split!)
    data.sort(key=lambda r: r.get("kickoff", ""))

    if league_id is not None:
        data = [d for d in data if d.get("league_id") == league_id]

    features_list = []
    labels_list = []
    league_ids = []
    kickoffs = []

    for row in data:
        if row.get("p_home_poisson") is None:
            continue

        # DC-xG fallback
        if row.get("p_home_dc_xg") is None:
            row["p_home_dc_xg"] = row.get("p_home_dc")
            row["p_draw_dc_xg"] = row.get("p_draw_dc")
            row["p_away_dc_xg"] = row.get("p_away_dc")

        fv = []
        for fname in feature_names:
            val = row.get(fname)
            if val is None:
                val = 0.0
            fv.append(float(val))
        features_list.append(fv)
        labels_list.append(int(row["outcome"]))
        league_ids.append(int(row["league_id"]))
        kickoffs.append(row.get("kickoff", ""))

    if kickoffs:
        print(f"  Kickoff range: {kickoffs[0]} → {kickoffs[-1]}")
        split_ko = kickoffs[int(len(kickoffs) * 0.8)]
        print(f"  Split at: {split_ko}")

    return np.array(features_list), np.array(labels_list), np.array(league_ids)


def evaluate(probs: np.ndarray, labels: np.ndarray) -> dict:
    """Compute RPS, Brier, LogLoss."""
    n = len(labels)
    if n == 0:
        return {"rps": 0.0, "brier": 0.0, "logloss": 0.0, "n": 0}

    rps_sum = 0.0
    brier_sum = 0.0
    logloss_sum = 0.0

    for i in range(n):
        p = [Decimal(str(round(probs[i, c], 6))) for c in range(3)]
        outcome_idx = int(labels[i])
        rps_sum += float(ranked_probability_score(tuple(p), outcome_idx))
        for cls in range(3):
            outcome = 1 if cls == outcome_idx else 0
            brier_sum += float(brier_score(p[cls], outcome))
        p_actual = max(float(probs[i, outcome_idx]), 1e-15)
        logloss_sum += -np.log(p_actual)

    return {
        "rps": round(rps_sum / n, 6),
        "brier": round(brier_sum / (n * 3), 6),
        "logloss": round(logloss_sum / n, 6),
        "n": n,
    }


def train_and_eval(X_train, y_train, X_val, y_val, C=1.0):
    """Train LogisticRegression, return val probs and metrics."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler

    scaler = StandardScaler()
    X_tr = scaler.fit_transform(X_train)
    X_va = scaler.transform(X_val)

    model = LogisticRegression(C=C, penalty="l2", max_iter=1000, solver="lbfgs")
    model.fit(X_tr, y_train)

    probs_val = model.predict_proba(X_va)
    probs_train = model.predict_proba(X_tr)

    metrics_val = evaluate(probs_val, y_val)
    metrics_train = evaluate(probs_train, y_train)

    return metrics_val, metrics_train, model, scaler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-file", required=True)
    parser.add_argument("--c", type=float, default=1.0)
    args = parser.parse_args()

    print("=" * 70)
    print("ABLATION STUDY: v2 (13) vs v2+delta (11) vs v5 (17)")
    print("=" * 70)

    results = {}

    for name, feature_names in [
        ("v2 (13 features)", FEATURES_V2),
        ("v2+delta (11 features)", FEATURES_V2_DELTA),
        ("v5 (17 features)", FEATURES_V5),
    ]:
        print(f"\n{'─' * 60}")
        print(f"  {name}")
        print(f"{'─' * 60}")

        X, y, lids = load_data(args.from_file, feature_names)
        split_idx = int(len(X) * 0.8)
        X_train, X_val = X[:split_idx], X[split_idx:]
        y_train, y_val = y[:split_idx], y[split_idx:]
        lids_val = lids[split_idx:]

        print(f"  Data: {len(X)} total, train={len(X_train)}, val={len(X_val)}")

        # Global model
        m_val, m_train, model, scaler = train_and_eval(X_train, y_train, X_val, y_val, C=args.c)
        print(f"  GLOBAL val:   RPS={m_val['rps']:.4f}  Brier={m_val['brier']:.4f}  LogLoss={m_val['logloss']:.4f}")
        print(f"  GLOBAL train: RPS={m_train['rps']:.4f}  Brier={m_train['brier']:.4f}  LogLoss={m_train['logloss']:.4f}")
        results[f"{name}_global"] = m_val

        # Per-league models (val metrics aggregated)
        unique_leagues = sorted(set(lids))
        per_league_rps = []
        per_league_n = []
        print(f"\n  Per-league breakdown (val set):")

        for lid in unique_leagues:
            mask_train = (lids[:split_idx] == lid)
            mask_val = (lids[split_idx:] == lid)

            n_train_l = int(mask_train.sum())
            n_val_l = int(mask_val.sum())

            if n_val_l == 0:
                print(f"    League {lid}: n_val=   0  (skipped, no val data)")
                continue

            if n_train_l < 30 or n_val_l < 5:
                # Use global model predictions for this league
                m_league = evaluate(
                    model.predict_proba(scaler.transform(X_val[mask_val])),
                    y_val[mask_val],
                )
                print(f"    League {lid}: n_val={n_val_l:4d}  RPS={m_league['rps']:.4f} (global fallback, train={n_train_l})")
            else:
                m_league_val, _, _, _ = train_and_eval(
                    X_train[mask_train], y_train[mask_train],
                    X_val[mask_val], y_val[mask_val], C=args.c,
                )
                m_league = m_league_val
                print(f"    League {lid}: n_val={n_val_l:4d}  RPS={m_league['rps']:.4f} (per-league, train={n_train_l})")

            per_league_rps.append(m_league["rps"] * n_val_l)
            per_league_n.append(n_val_l)

        weighted_rps = sum(per_league_rps) / sum(per_league_n) if sum(per_league_n) > 0 else 0
        print(f"  PER-LEAGUE weighted RPS: {weighted_rps:.4f}")
        results[f"{name}_per_league"] = {"rps": weighted_rps, "n": sum(per_league_n)}

    # Summary comparison
    print(f"\n{'=' * 70}")
    print("SUMMARY")
    print(f"{'=' * 70}")
    print(f"{'Model':<35} {'Global RPS':>12} {'PerLeague RPS':>14}")
    print(f"{'─' * 63}")
    for name in ["v2 (13 features)", "v2+delta (11 features)", "v5 (17 features)"]:
        g = results.get(f"{name}_global", {})
        p = results.get(f"{name}_per_league", {})
        print(f"{name:<35} {g.get('rps', 0):.4f}       {p.get('rps', 0):.4f}")

    print(f"\nLower RPS = better. Diff < 0.002 is noise.")


if __name__ == "__main__":
    main()
