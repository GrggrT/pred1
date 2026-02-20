import asyncio
from decimal import Decimal
from types import SimpleNamespace

import app.main as main


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


def test_api_elo_without_team_id_returns_rows():
    rows = [SimpleNamespace(team_id=1, rating=Decimal("1500.5"), name="Team A")]
    session = FakeSession([FakeResult(rows=rows)])
    result = asyncio.run(main.api_elo(team_id=None, limit=5, _=None, session=session))

    assert result["count"] == 1
    assert isinstance(result["rows"], list)
    assert result["rows"][0]["team_id"] == 1
    assert result["rows"][0]["name"] == "Team A"
    assert result["rows"][0]["rating"] == float(Decimal("1500.5"))


def test_api_elo_with_team_id_returns_rating(monkeypatch):
    async def _fake_get_team_rating(session, team_id):
        return Decimal("1550.25")

    monkeypatch.setattr(main, "get_team_rating", _fake_get_team_rating)

    session = FakeSession([])
    result = asyncio.run(main.api_elo(team_id=42, limit=5, _=None, session=session))

    assert result == {"team_id": 42, "rating": 1550.25}
