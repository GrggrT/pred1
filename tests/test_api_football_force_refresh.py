import asyncio

import httpx

import app.data.providers.api_football as api_football


class DummySession:
    pass


def test_api_get_uses_cache_by_default(monkeypatch):
    called = {"requests": 0}

    async def fake_get_cached(_session, _key):
        return {"cached": True}

    async def fake_set_cached(_session, _key, _payload, _ttl):
        return None

    async def fake_request_with_retries(*_args, **_kwargs):
        called["requests"] += 1
        raise AssertionError("request_with_retries should not be called")

    monkeypatch.setattr(api_football, "get_cached", fake_get_cached)
    monkeypatch.setattr(api_football, "set_cached", fake_set_cached)
    monkeypatch.setattr(api_football, "request_with_retries", fake_request_with_retries)
    monkeypatch.setattr(api_football, "api_football_client", lambda: object())

    result = asyncio.run(api_football.api_get(DummySession(), "/test", {}, ttl_seconds=1))
    assert result == {"cached": True}
    assert called["requests"] == 0


def test_api_get_force_refresh_bypasses_cache(monkeypatch):
    called = {"requests": 0, "payload": None}

    async def fake_get_cached(_session, _key):
        return {"cached": True}

    async def fake_set_cached(_session, _key, payload, _ttl):
        called["payload"] = payload

    async def fake_request_with_retries(_client, _method, _url, *_args, **_kwargs):
        called["requests"] += 1
        req = httpx.Request("GET", "https://example.com/test")
        return httpx.Response(200, json={"fresh": True}, request=req)

    monkeypatch.setattr(api_football, "get_cached", fake_get_cached)
    monkeypatch.setattr(api_football, "set_cached", fake_set_cached)
    monkeypatch.setattr(api_football, "request_with_retries", fake_request_with_retries)
    monkeypatch.setattr(api_football, "api_football_client", lambda: object())

    token = api_football.set_force_refresh(True)
    try:
        result = asyncio.run(api_football.api_get(DummySession(), "/test", {}, ttl_seconds=1))
    finally:
        api_football.reset_force_refresh(token)

    assert result == {"fresh": True}
    assert called["requests"] == 1
    assert called["payload"] == {"fresh": True}
