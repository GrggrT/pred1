"""
scripts/train_model.py
======================
Train logistic regression model on historical data and save coefficients to model_params.

Features:
  - elo_diff        (home Elo - away Elo)
  - xpts_diff       (expected points diff from Poisson)
  - xg_diff_l5      (rolling xG diff last 5 matches)
  - home_advantage   (1.0 constant — captured by intercept)
  - form_index       (short-term form differential)

Workflow:
  1. Load hist_fixtures + hist_statistics + hist_odds
  2. Compute rolling xG L5/L10 per team
  3. Compute Elo ratings chronologically
  4. Build feature matrix
  5. Train LogisticRegression (multinomial, 3 classes: H/D/A)
  6. Find optimal power-scaling alpha via Brier score on val split
  7. Save coefficients + alpha to model_params table

Usage:
    python scripts/train_model.py
    python scripts/train_model.py --dry-run          # show stats without saving
    python scripts/train_model.py --leagues 39,78    # specific leagues
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

import numpy as np
from dotenv import load_dotenv

load_dotenv()

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("train_model")

# ---------------------------------------------------------------------------
# DB helpers (sync via psycopg2)
# ---------------------------------------------------------------------------

def _get_conn(dsn: str):
    import psycopg2
    dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")
    return psycopg2.connect(dsn)


# ---------------------------------------------------------------------------
# Elo computation (mirrors app/services/elo_ratings.py logic)
# ---------------------------------------------------------------------------

DEFAULT_ELO = 1500.0
ELO_K = 20.0


def _elo_expected(rating: float, opp_rating: float) -> float:
    return 1.0 / (1.0 + 10.0 ** ((opp_rating - rating) / 400.0))


def _compute_elo_ratings(fixtures: list[dict]) -> dict[int, float]:
    """Compute Elo ratings chronologically. Returns final ratings per team_id."""
    ratings: dict[int, float] = defaultdict(lambda: DEFAULT_ELO)
    for f in fixtures:
        h = f["home_team_id"]
        a = f["away_team_id"]
        gh = f["goals_home"]
        ga = f["goals_away"]
        if gh is None or ga is None:
            continue
        exp_h = _elo_expected(ratings[h], ratings[a])
        if gh > ga:
            sh, sa = 1.0, 0.0
        elif gh == ga:
            sh, sa = 0.5, 0.5
        else:
            sh, sa = 0.0, 1.0
        ratings[h] += ELO_K * (sh - exp_h)
        ratings[a] += ELO_K * (sa - (1.0 - exp_h))
    return dict(ratings)


def _compute_elo_at_each_match(fixtures: list[dict]) -> list[tuple[float, float]]:
    """Return (elo_home, elo_away) BEFORE each match, in order of fixtures."""
    ratings: dict[int, float] = defaultdict(lambda: DEFAULT_ELO)
    elo_pairs = []
    for f in fixtures:
        h = f["home_team_id"]
        a = f["away_team_id"]
        elo_pairs.append((ratings[h], ratings[a]))
        gh = f["goals_home"]
        ga = f["goals_away"]
        if gh is None or ga is None:
            continue
        exp_h = _elo_expected(ratings[h], ratings[a])
        if gh > ga:
            sh, sa = 1.0, 0.0
        elif gh == ga:
            sh, sa = 0.5, 0.5
        else:
            sh, sa = 0.0, 1.0
        ratings[h] += ELO_K * (sh - exp_h)
        ratings[a] += ELO_K * (sa - (1.0 - exp_h))
    return elo_pairs


# ---------------------------------------------------------------------------
# Rolling xG
# ---------------------------------------------------------------------------

def _compute_rolling_xg(fixtures: list[dict], window: int = 5) -> list[tuple[Optional[float], Optional[float]]]:
    """
    For each fixture (in chronological order), compute rolling mean xG
    for home and away team over their last `window` matches BEFORE this fixture.
    Returns list of (home_rolling_xg, away_rolling_xg).
    """
    team_xg_history: dict[int, list[float]] = defaultdict(list)
    result = []

    for f in fixtures:
        h = f["home_team_id"]
        a = f["away_team_id"]

        # Get rolling xG before this match
        h_hist = team_xg_history[h]
        a_hist = team_xg_history[a]

        h_rolling = np.mean(h_hist[-window:]) if len(h_hist) >= 3 else None
        a_rolling = np.mean(a_hist[-window:]) if len(a_hist) >= 3 else None

        result.append((h_rolling, a_rolling))

        # Update history with this match's xG (or goals as fallback)
        h_xg = f.get("xg_home")
        a_xg = f.get("xg_away")
        h_val = float(h_xg) if h_xg is not None else (float(f["goals_home"]) if f["goals_home"] is not None else None)
        a_val = float(a_xg) if a_xg is not None else (float(f["goals_away"]) if f["goals_away"] is not None else None)

        if h_val is not None:
            team_xg_history[h].append(h_val)
        if a_val is not None:
            team_xg_history[a].append(a_val)

    return result


# ---------------------------------------------------------------------------
# Form index (short-term results differential)
# ---------------------------------------------------------------------------

def _compute_form_index(fixtures: list[dict], window: int = 5) -> list[tuple[Optional[float], Optional[float]]]:
    """
    Rolling form index: average points per game over last `window` matches.
    Returns (home_form, away_form) for each fixture.
    """
    team_results: dict[int, list[float]] = defaultdict(list)
    result = []

    for f in fixtures:
        h = f["home_team_id"]
        a = f["away_team_id"]

        h_hist = team_results[h]
        a_hist = team_results[a]

        h_form = np.mean(h_hist[-window:]) if len(h_hist) >= 3 else None
        a_form = np.mean(a_hist[-window:]) if len(a_hist) >= 3 else None

        result.append((h_form, a_form))

        gh = f["goals_home"]
        ga = f["goals_away"]
        if gh is not None and ga is not None:
            if gh > ga:
                team_results[h].append(3.0)
                team_results[a].append(0.0)
            elif gh == ga:
                team_results[h].append(1.0)
                team_results[a].append(1.0)
            else:
                team_results[h].append(0.0)
                team_results[a].append(3.0)

    return result


# ---------------------------------------------------------------------------
# Poisson xPts
# ---------------------------------------------------------------------------

def _poisson_pmf(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return (lam ** k) * math.exp(-lam) / math.factorial(k)


def _match_probs_from_lambda(lam_h: float, lam_a: float, k_max: int = 8) -> tuple[float, float, float]:
    p_h, p_d, p_a = 0.0, 0.0, 0.0
    for i in range(k_max + 1):
        pi = _poisson_pmf(i, lam_h)
        for j in range(k_max + 1):
            pj = _poisson_pmf(j, lam_a)
            prob = pi * pj
            if i > j:
                p_h += prob
            elif i == j:
                p_d += prob
            else:
                p_a += prob
    total = p_h + p_d + p_a
    if total > 0:
        p_h /= total
        p_d /= total
        p_a /= total
    return p_h, p_d, p_a


def _compute_xpts_diff(fixtures: list[dict], rolling_xg: list[tuple]) -> list[Optional[float]]:
    """Compute xPts difference using Poisson model from rolling xG."""
    result = []
    for i, f in enumerate(fixtures):
        h_xg, a_xg = rolling_xg[i]
        if h_xg is None or a_xg is None:
            result.append(None)
            continue
        lam_h = max(0.1, h_xg)
        lam_a = max(0.1, a_xg)
        p_h, p_d, _ = _match_probs_from_lambda(lam_h, lam_a)
        xpts_h = 3.0 * p_h + p_d
        xpts_a = 3.0 * (1.0 - p_h - p_d) + p_d
        result.append(xpts_h - xpts_a)
    return result


# ---------------------------------------------------------------------------
# Power-scaling alpha optimization (Brier score)
# ---------------------------------------------------------------------------

def _brier_score(y_true: np.ndarray, y_probs: np.ndarray) -> float:
    """Multi-class Brier score."""
    n_classes = y_probs.shape[1]
    one_hot = np.zeros_like(y_probs)
    for i, y in enumerate(y_true):
        one_hot[i, y] = 1.0
    return float(np.mean(np.sum((y_probs - one_hot) ** 2, axis=1)))


def _apply_power_scaling(probs: np.ndarray, alpha: float) -> np.ndarray:
    eps = 1e-15
    scaled = np.power(np.maximum(probs, eps), alpha)
    row_sums = scaled.sum(axis=1, keepdims=True)
    return scaled / row_sums


def _find_optimal_alpha(y_true: np.ndarray, raw_probs: np.ndarray) -> tuple[float, float]:
    """Grid search for alpha minimizing Brier score. Returns (best_alpha, best_brier)."""
    best_alpha = 1.0
    best_brier = float("inf")

    for alpha_int in range(30, 201):  # 0.30 to 2.00 step 0.01
        alpha = alpha_int / 100.0
        scaled = _apply_power_scaling(raw_probs, alpha)
        brier = _brier_score(y_true, scaled)
        if brier < best_brier:
            best_brier = brier
            best_alpha = alpha

    return best_alpha, best_brier


# ---------------------------------------------------------------------------
# Main training logic
# ---------------------------------------------------------------------------

def load_fixtures(conn, leagues: list[int], min_date: str = "2022-01-01", max_date: str = "2026-02-20") -> list[dict]:
    """Load finished fixtures from hist_fixtures, ordered chronologically."""
    with conn.cursor() as cur:
        placeholders = ",".join(["%s"] * len(leagues))
        cur.execute(
            f"""
            SELECT fixture_id, league_id, season, home_team_id, away_team_id,
                   match_date, goals_home, goals_away, xg_home, xg_away
            FROM hist_fixtures
            WHERE status = 'FT'
              AND league_id IN ({placeholders})
              AND match_date >= %s AND match_date < %s
              AND goals_home IS NOT NULL AND goals_away IS NOT NULL
            ORDER BY match_date ASC
            """,
            (*leagues, min_date, max_date),
        )
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    log.info("Loaded %d fixtures from hist_fixtures", len(rows))
    return rows


def build_feature_matrix(fixtures: list[dict]) -> tuple[np.ndarray, np.ndarray, list[int]]:
    """
    Build feature matrix X and target y from fixtures.
    Features: [elo_diff, xpts_diff, xg_diff_l5, home_advantage, form_index]
    Target: 0=HOME_WIN, 1=DRAW, 2=AWAY_WIN
    Returns (X, y, valid_indices) — valid_indices maps back to fixtures list.
    """
    elo_pairs = _compute_elo_at_each_match(fixtures)
    rolling_xg_l5 = _compute_rolling_xg(fixtures, window=5)
    xpts_diffs = _compute_xpts_diff(fixtures, rolling_xg_l5)
    form_indices = _compute_form_index(fixtures, window=5)

    X_rows = []
    y_rows = []
    valid_idx = []

    for i, f in enumerate(fixtures):
        elo_h, elo_a = elo_pairs[i]
        xg_h, xg_a = rolling_xg_l5[i]
        xpts_d = xpts_diffs[i]
        form_h, form_a = form_indices[i]

        # Skip if any feature is missing
        if any(v is None for v in [xg_h, xg_a, xpts_d, form_h, form_a]):
            continue

        gh = f["goals_home"]
        ga = f["goals_away"]
        if gh is None or ga is None:
            continue

        elo_diff = elo_h - elo_a
        xg_diff_l5 = xg_h - xg_a
        home_adv = 1.0
        form_diff = form_h - form_a

        X_rows.append([elo_diff, xpts_d, xg_diff_l5, home_adv, form_diff])

        if gh > ga:
            y_rows.append(0)
        elif gh == ga:
            y_rows.append(1)
        else:
            y_rows.append(2)

        valid_idx.append(i)

    X = np.array(X_rows, dtype=np.float64)
    y = np.array(y_rows, dtype=np.int32)
    log.info("Feature matrix: %d samples, %d features", X.shape[0], X.shape[1])
    return X, y, valid_idx


def train_logistic(X_train: np.ndarray, y_train: np.ndarray, X_val: np.ndarray, y_val: np.ndarray) -> dict:
    """Train LogisticRegression and return results dict."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import log_loss, accuracy_score

    scaler = StandardScaler()
    X_train_s = scaler.fit_transform(X_train)
    X_val_s = scaler.transform(X_val)

    model = LogisticRegression(
        solver="lbfgs",
        max_iter=1000,
        C=1.0,
    )
    model.fit(X_train_s, y_train)

    # Predictions on validation set
    y_val_probs = model.predict_proba(X_val_s)
    y_val_pred = model.predict(X_val_s)

    val_accuracy = accuracy_score(y_val, y_val_pred)
    val_logloss = log_loss(y_val, y_val_probs)
    val_brier = _brier_score(y_val, y_val_probs)

    # Find optimal alpha on validation set
    optimal_alpha, brier_after_alpha = _find_optimal_alpha(y_val, y_val_probs)

    # Feature names for reporting
    feature_names = ["elo_diff", "xpts_diff", "xg_diff_l5", "home_advantage", "form_index"]

    # Raw coefficients (unscaled) for interpretability
    # coef_ shape: (3, n_features) for 3 classes
    # To get unscaled: coef_unscaled = coef / scale
    coefs_scaled = model.coef_  # (3, 5)
    intercepts = model.intercept_  # (3,)
    scale = scaler.scale_
    mean = scaler.mean_

    # For prediction: z = coef_scaled @ ((x - mean) / scale) + intercept
    # = (coef_scaled / scale) @ x - (coef_scaled @ mean / scale) + intercept
    # = coef_unscaled @ x + (intercept - coef_scaled @ mean / scale)
    coefs_unscaled = coefs_scaled / scale[np.newaxis, :]
    intercepts_unscaled = intercepts - np.sum(coefs_scaled * mean[np.newaxis, :] / scale[np.newaxis, :], axis=1)

    log.info("=== Training Results ===")
    log.info("Train samples: %d, Val samples: %d", len(X_train), len(X_val))
    log.info("Val accuracy: %.4f", val_accuracy)
    log.info("Val logloss:  %.4f", val_logloss)
    log.info("Val Brier:    %.4f (before alpha)", val_brier)
    log.info("Optimal alpha: %.2f", optimal_alpha)
    log.info("Val Brier:    %.4f (after alpha=%.2f)", brier_after_alpha, optimal_alpha)

    for cls_idx, cls_name in enumerate(["HOME_WIN", "DRAW", "AWAY_WIN"]):
        log.info("  %s coefficients:", cls_name)
        for j, fname in enumerate(feature_names):
            log.info("    %s: %.6f (unscaled: %.6f)", fname, coefs_scaled[cls_idx, j], coefs_unscaled[cls_idx, j])
        log.info("    intercept: %.6f (unscaled: %.6f)", intercepts[cls_idx], intercepts_unscaled[cls_idx])

    return {
        "feature_names": feature_names,
        "coefs_scaled": coefs_scaled.tolist(),
        "coefs_unscaled": coefs_unscaled.tolist(),
        "intercepts": intercepts.tolist(),
        "intercepts_unscaled": intercepts_unscaled.tolist(),
        "scaler_mean": mean.tolist(),
        "scaler_scale": scale.tolist(),
        "val_accuracy": val_accuracy,
        "val_logloss": val_logloss,
        "val_brier_raw": val_brier,
        "val_brier_calibrated": brier_after_alpha,
        "optimal_alpha": optimal_alpha,
        "classes": ["HOME_WIN", "DRAW", "AWAY_WIN"],
        "n_train": len(X_train),
        "n_val": len(X_val),
    }


def save_to_model_params(conn, results: dict, scope: str = "global", league_id: Optional[int] = None):
    """Save trained coefficients to model_params table."""
    params_to_save = []

    # Save logistic regression coefficients (unscaled for direct use)
    class_names = results["classes"]
    feature_names = results["feature_names"]
    coefs = results["coefs_unscaled"]
    intercepts = results["intercepts_unscaled"]

    for cls_idx, cls_name in enumerate(class_names):
        for feat_idx, feat_name in enumerate(feature_names):
            params_to_save.append((
                f"logistic_coef_{cls_name.lower()}_{feat_name}",
                coefs[cls_idx][feat_idx],
            ))
        params_to_save.append((
            f"logistic_intercept_{cls_name.lower()}",
            intercepts[cls_idx],
        ))

    # Save scaler params
    for feat_idx, feat_name in enumerate(feature_names):
        params_to_save.append((
            f"scaler_mean_{feat_name}",
            results["scaler_mean"][feat_idx],
        ))
        params_to_save.append((
            f"scaler_scale_{feat_name}",
            results["scaler_scale"][feat_idx],
        ))

    # Save optimal alpha
    params_to_save.append(("optimal_alpha", results["optimal_alpha"]))

    # Save metrics for reference
    params_to_save.append(("val_brier_raw", results["val_brier_raw"]))
    params_to_save.append(("val_brier_calibrated", results["val_brier_calibrated"]))
    params_to_save.append(("val_logloss", results["val_logloss"]))
    params_to_save.append(("val_accuracy", results["val_accuracy"]))

    metadata = json.dumps({
        "n_train": results["n_train"],
        "n_val": results["n_val"],
        "trained_at": datetime.now(timezone.utc).isoformat(),
    })

    with conn.cursor() as cur:
        for param_name, param_value in params_to_save:
            cur.execute(
                """
                INSERT INTO model_params (scope, league_id, param_name, param_value, metadata, trained_at)
                VALUES (%s, %s, %s, %s, %s, NOW())
                ON CONFLICT (scope, league_id, param_name) DO UPDATE SET
                    param_value = EXCLUDED.param_value,
                    metadata = EXCLUDED.metadata,
                    trained_at = NOW()
                """,
                (scope, league_id, param_name, round(param_value, 6), metadata),
            )
    conn.commit()
    log.info("Saved %d params to model_params (scope=%s, league_id=%s)", len(params_to_save), scope, league_id)


def main():
    parser = argparse.ArgumentParser(description="Train logistic model on historical data")
    parser.add_argument("--leagues", help="Comma-separated league IDs (default: from LEAGUE_IDS)")
    parser.add_argument("--from-date", default="2022-01-01", help="Start date (default: 2022-01-01)")
    parser.add_argument("--to-date", default="2026-02-20", help="End date (default: 2026-02-20)")
    parser.add_argument("--val-ratio", type=float, default=0.2, help="Validation split ratio (default: 0.2)")
    parser.add_argument("--dry-run", action="store_true", help="Show results without saving to DB")
    parser.add_argument("--per-league", action="store_true", help="Train separate model per league")
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        log.error("DATABASE_URL not set")
        sys.exit(1)

    if args.leagues:
        leagues = [int(x.strip()) for x in args.leagues.split(",") if x.strip().isdigit()]
    else:
        raw = os.environ.get("LEAGUE_IDS", "39,78,140,135")
        leagues = [int(x.strip()) for x in raw.split(",") if x.strip().isdigit()]

    if not leagues:
        log.error("No leagues specified")
        sys.exit(1)

    conn = _get_conn(database_url)

    # Ensure model_params table exists
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS model_params (
                id SERIAL PRIMARY KEY,
                scope TEXT NOT NULL DEFAULT 'global',
                league_id INTEGER,
                param_name TEXT NOT NULL,
                param_value NUMERIC(12,6) NOT NULL,
                metadata JSONB,
                trained_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(scope, league_id, param_name)
            )
        """)
    conn.commit()

    log.info("Leagues: %s", leagues)
    log.info("Date range: %s to %s", args.from_date, args.to_date)

    if args.per_league:
        for lid in leagues:
            log.info("\n=== Training league %d ===", lid)
            fixtures = load_fixtures(conn, [lid], args.from_date, args.to_date)
            if len(fixtures) < 100:
                log.warning("Not enough fixtures for league %d (%d), skipping", lid, len(fixtures))
                continue
            X, y, _ = build_feature_matrix(fixtures)
            if len(X) < 50:
                log.warning("Not enough valid samples for league %d (%d), skipping", lid, len(X))
                continue
            split = int(len(X) * (1 - args.val_ratio))
            X_train, X_val = X[:split], X[split:]
            y_train, y_val = y[:split], y[split:]
            results = train_logistic(X_train, y_train, X_val, y_val)
            if not args.dry_run:
                save_to_model_params(conn, results, scope="league", league_id=lid)
    else:
        # Global model across all leagues
        fixtures = load_fixtures(conn, leagues, args.from_date, args.to_date)
        if len(fixtures) < 100:
            log.error("Not enough fixtures (%d), need at least 100", len(fixtures))
            conn.close()
            sys.exit(1)

        X, y, _ = build_feature_matrix(fixtures)
        if len(X) < 50:
            log.error("Not enough valid samples (%d), need at least 50", len(X))
            conn.close()
            sys.exit(1)

        # Time-based split (not random — avoid leakage)
        split = int(len(X) * (1 - args.val_ratio))
        X_train, X_val = X[:split], X[split:]
        y_train, y_val = y[:split], y[split:]

        results = train_logistic(X_train, y_train, X_val, y_val)

        if not args.dry_run:
            save_to_model_params(conn, results, scope="global", league_id=None)
            log.info("Model saved to model_params table")
        else:
            log.info("[DRY RUN] Model NOT saved")

    conn.close()
    log.info("Done.")


if __name__ == "__main__":
    main()
