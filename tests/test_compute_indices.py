"""Tests for compute_indices helper functions."""

from datetime import datetime, timedelta
from decimal import Decimal

from app.jobs.compute_indices import (
    MatchSample,
    _average,
    _coalesce_metric,
    _compute_window,
    _rest_hours,
    _with_fallback,
)
from app.core.decimalutils import D


class TestCoalesceMetric:
    def test_primary_present(self):
        result = _coalesce_metric(1.5, 2)
        assert result == D("1.50")

    def test_primary_none_uses_fallback(self):
        result = _coalesce_metric(None, 3)
        assert result == D("3.00")

    def test_both_none(self):
        assert _coalesce_metric(None, None) is None


class TestAverage:
    def test_empty_returns_none(self):
        assert _average([]) is None

    def test_single_value(self):
        assert _average([D("2.00")]) == D("2.00")

    def test_multiple_values(self):
        result = _average([D("1.00"), D("3.00")])
        assert result == D("2.00")


class TestComputeWindow:
    def test_empty_samples(self):
        f, a = _compute_window([], 5)
        assert f is None
        assert a is None

    def test_returns_average_of_window(self):
        now = datetime(2025, 1, 10)
        samples = [
            MatchSample(kickoff=now - timedelta(days=i), is_home=True,
                        val_for=D("2.00"), val_against=D("1.00"))
            for i in range(1, 6)
        ]
        f, a = _compute_window(samples, 5)
        assert f == D("2.00")
        assert a == D("1.00")

    def test_window_smaller_than_samples(self):
        now = datetime(2025, 1, 10)
        samples = [
            MatchSample(kickoff=now - timedelta(days=i), is_home=True,
                        val_for=D(str(i)), val_against=D("1.00"))
            for i in range(1, 11)
        ]
        f, _ = _compute_window(samples, 3)
        # First 3 samples: val_for = 1, 2, 3 → avg = 2.0
        assert f == D("2.00")


class TestRestHours:
    def test_no_samples(self):
        assert _rest_hours([], datetime(2025, 1, 10)) is None

    def test_computes_hours_from_most_recent(self):
        target = datetime(2025, 1, 10, 18, 0)
        samples = [
            MatchSample(kickoff=datetime(2025, 1, 7, 18, 0), is_home=True,
                        val_for=D("1"), val_against=D("1")),
        ]
        assert _rest_hours(samples, target) == 72  # 3 days

    def test_short_rest(self):
        target = datetime(2025, 1, 3, 20, 0)
        samples = [
            MatchSample(kickoff=datetime(2025, 1, 1, 20, 0), is_home=False,
                        val_for=D("1"), val_against=D("1")),
        ]
        assert _rest_hours(samples, target) == 48  # 2 days


class TestWithFallback:
    def test_value_present(self):
        assert _with_fallback(D("1.50"), D("0")) == D("1.50")

    def test_none_uses_fallback(self):
        assert _with_fallback(None, D("1.30")) == D("1.30")
