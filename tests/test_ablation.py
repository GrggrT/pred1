"""Tests for scripts/ablation_study.py helper functions."""

from __future__ import annotations

import json
import math
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.ablation_study import (
    _brier,
    _logloss,
    _rps,
    aggregate_metrics,
    build_comparison_table,
    compute_outcome,
    matches_to_dc_input,
    walk_forward_evaluate,
)


# ---------------------------------------------------------------------------
# compute_outcome
# ---------------------------------------------------------------------------

class TestComputeOutcome:
    def test_home_win(self):
        assert compute_outcome(3, 1) == 0

    def test_draw(self):
        assert compute_outcome(1, 1) == 1

    def test_away_win(self):
        assert compute_outcome(0, 2) == 2

    def test_zero_zero(self):
        assert compute_outcome(0, 0) == 1


# ---------------------------------------------------------------------------
# matches_to_dc_input
# ---------------------------------------------------------------------------

class TestMatchesToDcInput:
    def test_basic_conversion(self):
        from datetime import date
        matches = [
            {
                "home_team_id": 10,
                "away_team_id": 20,
                "goals_home": 2,
                "goals_away": 1,
                "match_date": date(2024, 1, 15),
            },
            {
                "home_team_id": 30,
                "away_team_id": 40,
                "goals_home": 0,
                "goals_away": 0,
                "match_date": date(2024, 2, 1),
            },
        ]
        result = matches_to_dc_input(matches)
        assert len(result) == 2
        assert result[0].home_id == 10
        assert result[0].away_id == 20
        assert result[0].home_goals == 2
        assert result[0].away_goals == 1
        assert result[0].date == date(2024, 1, 15)

    def test_skips_none_goals(self):
        matches = [
            {
                "home_team_id": 10,
                "away_team_id": 20,
                "goals_home": None,
                "goals_away": None,
                "match_date": "2024-01-15",
            },
        ]
        result = matches_to_dc_input(matches)
        assert len(result) == 0

    def test_string_date(self):
        from datetime import date
        matches = [
            {
                "home_team_id": 10,
                "away_team_id": 20,
                "goals_home": 1,
                "goals_away": 0,
                "match_date": "2024-03-10",
            },
        ]
        result = matches_to_dc_input(matches)
        assert len(result) == 1
        assert result[0].date == date(2024, 3, 10)


# ---------------------------------------------------------------------------
# Scoring functions
# ---------------------------------------------------------------------------

class TestScoring:
    def test_rps_perfect_prediction(self):
        """Perfect prediction for home win: p_h=1, outcome=0 → RPS=0."""
        assert _rps(1.0, 0.0, 0.0, 0) == pytest.approx(0.0)

    def test_rps_worst_prediction(self):
        """Predict away win with certainty, actual is home win."""
        rps = _rps(0.0, 0.0, 1.0, 0)
        assert rps > 0.5  # bad prediction

    def test_rps_uniform(self):
        """Uniform prediction: RPS should be moderate."""
        rps = _rps(1/3, 1/3, 1/3, 0)
        assert 0.0 < rps < 0.5

    def test_brier_perfect(self):
        assert _brier(1.0, 0.0, 0.0, 0) == pytest.approx(0.0)

    def test_brier_worst(self):
        assert _brier(0.0, 0.0, 1.0, 0) == pytest.approx(2.0)

    def test_logloss_perfect(self):
        """Near-perfect prediction should have low logloss."""
        ll = _logloss(0.99, 0.005, 0.005, 0)
        assert ll < 0.02

    def test_logloss_bad(self):
        """Bad prediction should have high logloss."""
        ll = _logloss(0.01, 0.01, 0.98, 0)
        assert ll > 3.0


# ---------------------------------------------------------------------------
# aggregate_metrics / empty results
# ---------------------------------------------------------------------------

class TestAggregation:
    def test_empty_results(self):
        m = aggregate_metrics([])
        assert m["n"] == 0
        assert m["rps"] is None

    def test_single_result(self):
        results = [{"rps": 0.25, "brier": 0.5, "logloss": 1.0}]
        m = aggregate_metrics(results)
        assert m["n"] == 1
        assert m["rps"] == pytest.approx(0.25)
        assert m["brier"] == pytest.approx(0.5)
        assert m["logloss"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# build_comparison_table — ΔRPS
# ---------------------------------------------------------------------------

class TestComparisonTable:
    def test_delta_rps_baseline_zero(self):
        """Baseline should have delta_rps = 0."""
        all_metrics = {
            0: {"n": 100, "rps": 0.220, "brier": 0.600, "logloss": 1.100},
            1: {"n": 100, "rps": 0.210, "brier": 0.580, "logloss": 1.050},
        }
        rows = build_comparison_table(all_metrics)
        assert len(rows) == 2
        # Baseline delta = 0
        assert rows[0]["delta_rps"] == pytest.approx(0.0)
        # DC should be negative (improvement)
        assert rows[1]["delta_rps"] == pytest.approx(-0.010)

    def test_delta_rps_no_baseline(self):
        """Without baseline, delta_rps should be None."""
        all_metrics = {
            1: {"n": 100, "rps": 0.210, "brier": 0.580, "logloss": 1.050},
        }
        rows = build_comparison_table(all_metrics)
        assert rows[0]["delta_rps"] is None


# ---------------------------------------------------------------------------
# walk_forward_evaluate with synthetic data
# ---------------------------------------------------------------------------

class TestWalkForward:
    @staticmethod
    def _make_fixtures(n: int = 100) -> list[dict]:
        """Generate synthetic fixtures for testing walk-forward."""
        import random
        from datetime import date, timedelta

        random.seed(42)
        fixtures = []
        base_date = date(2023, 1, 1)
        teams = list(range(1, 21))  # 20 teams

        for i in range(n):
            h = teams[i % len(teams)]
            a = teams[(i + 3) % len(teams)]
            if h == a:
                a = teams[(i + 5) % len(teams)]
            gh = random.randint(0, 4)
            ga = random.randint(0, 3)
            fixtures.append({
                "fixture_id": 1000 + i,
                "league_id": 39,
                "season": 2023,
                "home_team_id": h,
                "away_team_id": a,
                "match_date": base_date + timedelta(days=i * 3),
                "goals_home": gh,
                "goals_away": ga,
                "xg_home": gh + random.uniform(-0.5, 0.5),
                "xg_away": ga + random.uniform(-0.5, 0.5),
            })
        return fixtures

    def test_baseline_returns_results(self):
        fixtures = self._make_fixtures(80)
        results = walk_forward_evaluate(fixtures, config_id=0, warmup=20)
        assert len(results) == 60  # 80 - 20 warmup
        for r in results:
            assert 0.0 <= r["p_h"] <= 1.0
            assert 0.0 <= r["p_d"] <= 1.0
            assert 0.0 <= r["p_a"] <= 1.0
            assert r["p_h"] + r["p_d"] + r["p_a"] == pytest.approx(1.0, abs=0.01)
            assert r["rps"] >= 0.0
            assert r["outcome"] in (0, 1, 2)

    def test_config_1_returns_results(self):
        """DC config should produce results (with fallback to baseline for early matches)."""
        fixtures = self._make_fixtures(120)
        results = walk_forward_evaluate(fixtures, config_id=1, warmup=40)
        assert len(results) > 0
        for r in results:
            assert r["p_h"] + r["p_d"] + r["p_a"] == pytest.approx(1.0, abs=0.01)
