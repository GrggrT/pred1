import asyncio
from types import SimpleNamespace

from app.main import api_dashboard


class FakeResult:
    def __init__(self, first_row=None):
        self._first_row = first_row

    def first(self):
        return self._first_row


class FakeSession:
    def __init__(self, results):
        self._results = list(results)
        self.calls = []

    async def execute(self, statement, params=None):
        self.calls.append((statement, params))
        return self._results.pop(0)


def _build_session(max_loss, total_profit):
    current = SimpleNamespace(
        total_bets=1,
        wins=1,
        total_profit=total_profit,
        avg_profit=total_profit,
        max_win=total_profit,
        max_loss=max_loss,
        active_leagues=1,
    )
    prev = SimpleNamespace(
        total_bets=1,
        wins=1,
        total_profit=0,
    )
    return FakeSession([FakeResult(first_row=current), FakeResult(first_row=prev)])


def test_profit_factor_no_losses():
    session = _build_session(max_loss=None, total_profit=0)
    result = asyncio.run(api_dashboard(days=30, _=None, session=session))
    risk = result["risk_metrics"]
    assert risk["profit_factor"] is None
    assert risk["profit_factor_note"] == "no_losses"


def test_profit_factor_zero_denominator():
    session = _build_session(max_loss=-10, total_profit=10)
    result = asyncio.run(api_dashboard(days=30, _=None, session=session))
    risk = result["risk_metrics"]
    assert risk["profit_factor"] is None
    assert risk["profit_factor_note"] == "zero_denominator"
