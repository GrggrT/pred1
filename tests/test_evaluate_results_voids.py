import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace


class _FakeResult:
    def __init__(self, *, rowcount: int = 0):
        self.rowcount = rowcount


class _FakeSession:
    def __init__(self):
        self.calls: list[tuple[str, dict | None]] = []

    async def execute(self, stmt, params=None):
        self.calls.append((str(stmt), dict(params) if params is not None else None))
        return _FakeResult(rowcount=1)

    async def commit(self):
        return None

    async def rollback(self):
        return None


def test_evaluate_results_voids_when_missing_goals(monkeypatch):
    from app.jobs import evaluate_results

    row = SimpleNamespace(
        fixture_id=1,
        selection_code="HOME_WIN",
        initial_odd=Decimal("2.0"),
        confidence=Decimal("0.55"),
        feature_flags={},
        home_goals=None,
        away_goals=None,
        home_team_id=10,
        away_team_id=20,
        kickoff=datetime.now(timezone.utc),
    )

    async def _pending_predictions(_session):
        return [row]

    async def _noop_async(*_args, **_kwargs):
        return 0

    async def _noop_elo(*_args, **_kwargs):
        return {"processed": 0, "batches": 0, "rebuild": False}

    monkeypatch.setattr(evaluate_results, "_pending_predictions", _pending_predictions)
    monkeypatch.setattr(evaluate_results, "apply_elo_from_fixtures", _noop_elo)
    monkeypatch.setattr(evaluate_results, "_settle_totals", _noop_async)
    monkeypatch.setattr(evaluate_results, "_log_roi", _noop_async)
    monkeypatch.setattr(evaluate_results, "_void_cancelled_predictions", _noop_async)
    monkeypatch.setattr(evaluate_results, "_void_cancelled_totals", _noop_async)

    session = _FakeSession()
    asyncio.run(evaluate_results.run(session))

    assert any("UPDATE predictions SET status='VOID'" in sql and "settled_at=now()" in sql for sql, _ in session.calls)


def test_evaluate_results_voids_when_initial_odd_missing(monkeypatch):
    from app.jobs import evaluate_results

    called = {"elo": 0}

    row = SimpleNamespace(
        fixture_id=2,
        selection_code="HOME_WIN",
        initial_odd=None,
        confidence=Decimal("0.55"),
        feature_flags={},
        home_goals=1,
        away_goals=0,
        home_team_id=10,
        away_team_id=20,
        kickoff=datetime.now(timezone.utc),
    )

    async def _pending_predictions(_session):
        return [row]

    async def _noop_elo(*_args, **_kwargs):
        called["elo"] += 1
        return {"processed": 0, "batches": 0, "rebuild": False}

    async def _noop_async(*_args, **_kwargs):
        return 0

    monkeypatch.setattr(evaluate_results, "_pending_predictions", _pending_predictions)
    monkeypatch.setattr(evaluate_results, "apply_elo_from_fixtures", _noop_elo)
    monkeypatch.setattr(evaluate_results, "_settle_totals", _noop_async)
    monkeypatch.setattr(evaluate_results, "_log_roi", _noop_async)
    monkeypatch.setattr(evaluate_results, "_void_cancelled_predictions", _noop_async)
    monkeypatch.setattr(evaluate_results, "_void_cancelled_totals", _noop_async)

    session = _FakeSession()
    asyncio.run(evaluate_results.run(session))

    assert called["elo"] == 1
    assert any("UPDATE predictions SET status='VOID'" in sql and "settled_at=now()" in sql for sql, _ in session.calls)


def test_evaluate_results_sets_settled_at_on_win_loss(monkeypatch):
    from app.jobs import evaluate_results

    row = SimpleNamespace(
        fixture_id=3,
        selection_code="HOME_WIN",
        initial_odd=Decimal("2.0"),
        confidence=Decimal("0.55"),
        feature_flags={},
        home_goals=1,
        away_goals=0,
        home_team_id=10,
        away_team_id=20,
        kickoff=datetime.now(timezone.utc),
    )

    async def _pending_predictions(_session):
        return [row]

    async def _noop_async(*_args, **_kwargs):
        return 0

    async def _noop_elo(*_args, **_kwargs):
        return {"processed": 0, "batches": 0, "rebuild": False}

    monkeypatch.setattr(evaluate_results, "_pending_predictions", _pending_predictions)
    monkeypatch.setattr(evaluate_results, "apply_elo_from_fixtures", _noop_elo)
    monkeypatch.setattr(evaluate_results, "_settle_totals", _noop_async)
    monkeypatch.setattr(evaluate_results, "_log_roi", _noop_async)
    monkeypatch.setattr(evaluate_results, "_void_cancelled_predictions", _noop_async)
    monkeypatch.setattr(evaluate_results, "_void_cancelled_totals", _noop_async)

    session = _FakeSession()
    asyncio.run(evaluate_results.run(session))

    assert any("UPDATE predictions" in sql and "settled_at=now()" in sql and "SET status=:status" in sql for sql, _ in session.calls)
