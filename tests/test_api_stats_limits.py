import asyncio
from types import SimpleNamespace

from app.main import api_stats


class FakeResult:
    def __init__(self, first_row=None, rows=None):
        self._first_row = first_row
        self._rows = rows or []

    def first(self):
        return self._first_row

    def fetchall(self):
        return list(self._rows)


class FakeSession:
    def __init__(self, results):
        self._results = list(results)
        self.calls = []

    async def execute(self, statement, params=None):
        self.calls.append((statement, params))
        return self._results.pop(0)


def _build_session():
    agg_row = SimpleNamespace(
        total_bets=0,
        wins=0,
        losses=0,
        pending=0,
        profit=0,
        weighted_profit=0,
        weight_sum=0,
        weighted_wins=0,
        strong_signals=0,
        avg_signal=0,
    )
    bin_row = SimpleNamespace(total=0, pnl=0)
    results = [
        FakeResult(first_row=agg_row),
        FakeResult(first_row=bin_row),
        FakeResult(first_row=bin_row),
        FakeResult(first_row=bin_row),
        FakeResult(rows=[]),
    ]
    return FakeSession(results)


def _find_metrics_call(calls):
    for statement, params in calls:
        text = str(statement)
        if "feature_flags" in text and "FROM predictions" in text:
            return text, params
    raise AssertionError("metrics query not found")


def test_api_stats_metrics_limit_applied():
    session = _build_session()
    result = asyncio.run(
        api_stats(
            metrics_limit=10,
            metrics_offset=5,
            metrics_unbounded=False,
            _=None,
            session=session,
        )
    )
    text, params = _find_metrics_call(session.calls)
    assert "LIMIT" in text
    assert params["limit"] == 10
    assert params["offset"] == 5
    assert result["metrics_sample"]["unbounded"] is False
    assert result["metrics_sample"]["limit"] == 10
    assert result["metrics_sample"]["offset"] == 5


def test_api_stats_metrics_unbounded_skips_limit():
    session = _build_session()
    result = asyncio.run(
        api_stats(
            metrics_limit=10,
            metrics_offset=0,
            metrics_unbounded=True,
            _=None,
            session=session,
        )
    )
    text, params = _find_metrics_call(session.calls)
    assert "LIMIT" not in text
    assert params == {}
    assert result["metrics_sample"]["unbounded"] is True
    assert result["metrics_sample"]["limit"] is None
    assert result["metrics_sample"]["offset"] is None
