import asyncio

import app.jobs.sync_data as sync_data


class DummySession:
    async def execute(self, *_args, **_kwargs):
        class Result:
            def fetchall(self):
                return []
        return Result()

    async def commit(self):
        return None

    async def rollback(self):
        return None


def test_force_refresh_skips_freshness_check(monkeypatch):
    async def fake_get_fixtures(*_args, **_kwargs):
        return {"response": []}

    async def fake_select_ns_fixtures(*_args, **_kwargs):
        return [(1, None, None), (2, None, None)]

    called = {"filter": False, "refresh": None}

    async def fake_filter_missing_odds_dynamic(*_args, **_kwargs):
        called["filter"] = True
        return [], set()

    async def fake_refresh_odds(_session, fixture_ids):
        called["refresh"] = list(fixture_ids)
        return set(fixture_ids), 0

    async def fake_backfill_snapshots(*_args, **_kwargs):
        return 0

    async def fake_sync_stats(*_args, **_kwargs):
        return 0, 0

    async def fake_sync_standings(*_args, **_kwargs):
        return 0

    async def fake_fetch_injuries(*_args, **_kwargs):
        return None

    async def fake_cleanup_injuries(*_args, **_kwargs):
        return None

    async def fake_compute_baselines(*_args, **_kwargs):
        return None

    async def fake_quota_guard(*_args, **_kwargs):
        return {"blocked": False}

    monkeypatch.setattr(sync_data, "get_fixtures", fake_get_fixtures)
    monkeypatch.setattr(sync_data, "_select_ns_fixtures", fake_select_ns_fixtures)
    monkeypatch.setattr(sync_data, "_filter_missing_odds_dynamic", fake_filter_missing_odds_dynamic)
    monkeypatch.setattr(sync_data, "_refresh_odds", fake_refresh_odds)
    monkeypatch.setattr(sync_data, "_backfill_snapshots_from_odds", fake_backfill_snapshots)
    monkeypatch.setattr(sync_data, "_sync_stats", fake_sync_stats)
    monkeypatch.setattr(sync_data, "_sync_standings", fake_sync_standings)
    monkeypatch.setattr(sync_data, "_fetch_injuries", fake_fetch_injuries)
    monkeypatch.setattr(sync_data, "_cleanup_injuries", fake_cleanup_injuries)
    monkeypatch.setattr(sync_data, "_compute_league_baselines", fake_compute_baselines)
    monkeypatch.setattr(sync_data, "quota_guard_decision", fake_quota_guard)

    monkeypatch.setattr(sync_data.settings, "api_football_key", "test")
    monkeypatch.setattr(sync_data.settings, "league_ids_raw", "39")
    monkeypatch.setattr(sync_data.settings, "season", 2025)
    monkeypatch.setattr(sync_data.settings, "enable_xg", False)
    monkeypatch.setattr(sync_data.settings, "enable_standings", False)
    monkeypatch.setattr(sync_data.settings, "enable_injuries", False)
    monkeypatch.setattr(sync_data.settings, "enable_league_baselines", False)

    asyncio.run(sync_data.run(DummySession(), force_refresh=True))

    assert called["filter"] is False
    assert set(called["refresh"] or []) == {1, 2}
