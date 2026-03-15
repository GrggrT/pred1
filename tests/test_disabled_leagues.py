"""Tests for DISABLED_PREDICTION_LEAGUES config and prediction pipeline skip logic.

Task 21: Primeira Liga (ID 94) disabled for prediction generation.
"""
import pytest
from unittest.mock import patch
from decimal import Decimal

from app.core.config import Settings


class TestDisabledPredictionLeaguesConfig:
    """Test the disabled_prediction_leagues property parsing."""

    def test_empty_string_returns_empty_set(self):
        with patch.dict("os.environ", {
            "DATABASE_URL": "postgresql+asyncpg://x:x@localhost/test",
            "DISABLED_PREDICTION_LEAGUES": "",
        }, clear=False):
            s = Settings()
            assert s.disabled_prediction_leagues == set()

    def test_single_league(self):
        with patch.dict("os.environ", {
            "DATABASE_URL": "postgresql+asyncpg://x:x@localhost/test",
            "DISABLED_PREDICTION_LEAGUES": "94",
        }, clear=False):
            s = Settings()
            assert s.disabled_prediction_leagues == {94}

    def test_multiple_leagues(self):
        with patch.dict("os.environ", {
            "DATABASE_URL": "postgresql+asyncpg://x:x@localhost/test",
            "DISABLED_PREDICTION_LEAGUES": "94,253,71",
        }, clear=False):
            s = Settings()
            assert s.disabled_prediction_leagues == {94, 253, 71}

    def test_whitespace_handling(self):
        with patch.dict("os.environ", {
            "DATABASE_URL": "postgresql+asyncpg://x:x@localhost/test",
            "DISABLED_PREDICTION_LEAGUES": " 94 , 253 , ",
        }, clear=False):
            s = Settings()
            assert s.disabled_prediction_leagues == {94, 253}

    def test_disabled_league_check(self):
        with patch.dict("os.environ", {
            "DATABASE_URL": "postgresql+asyncpg://x:x@localhost/test",
            "DISABLED_PREDICTION_LEAGUES": "94",
        }, clear=False):
            s = Settings()
            assert 94 in s.disabled_prediction_leagues
            assert 39 not in s.disabled_prediction_leagues


class TestFallbackChainNoLogistic:
    """Verify the prediction pipeline has no logistic fallback."""

    def test_prob_source_values_in_code(self):
        """build_predictions.py should only use stacking/dc/poisson_fallback as prob_source."""
        import ast
        import pathlib

        src = pathlib.Path("app/jobs/build_predictions.py").read_text()
        tree = ast.parse(src)

        prob_sources = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "prob_source":
                        if isinstance(node.value, ast.Constant):
                            prob_sources.append(node.value.value)

        assert "logistic" not in prob_sources, (
            f"Found prob_source='logistic' in build_predictions.py. "
            f"Actual sources: {prob_sources}"
        )
        # Verify expected sources exist
        assert "stacking" in prob_sources
        assert "dc" in prob_sources
        assert "poisson_fallback" in prob_sources
