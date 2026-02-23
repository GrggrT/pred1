import asyncio

import pytest

from app.data.providers import telegram


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def test_send_message_retries_on_429(monkeypatch):
    calls = []
    sleeps = []
    payloads = [
        {"ok": False, "error_code": 429, "description": "Too Many Requests", "parameters": {"retry_after": 0}},
        {"ok": True, "result": {"message_id": 101}},
    ]

    async def fake_request_with_retries(*_args, **_kwargs):
        calls.append(1)
        return _Resp(payloads.pop(0))

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(telegram, "telegram_client", lambda: object())
    monkeypatch.setattr(telegram, "request_with_retries", fake_request_with_retries)
    monkeypatch.setattr(telegram.asyncio, "sleep", fake_sleep)

    result = asyncio.run(telegram.send_message(-1001, "hello"))

    assert result == 101
    assert len(calls) == 2
    assert len(sleeps) == 1


def test_send_photo_retries_on_500(monkeypatch):
    calls = []
    sleeps = []
    payloads = [
        {"ok": False, "error_code": 500, "description": "Internal error"},
        {"ok": True, "result": {"message_id": 202}},
    ]

    async def fake_request_with_retries(*_args, **_kwargs):
        calls.append(1)
        return _Resp(payloads.pop(0))

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(telegram, "telegram_client", lambda: object())
    monkeypatch.setattr(telegram, "request_with_retries", fake_request_with_retries)
    monkeypatch.setattr(telegram.asyncio, "sleep", fake_sleep)

    result = asyncio.run(telegram.send_photo(-1001, b"png"))

    assert result == 202
    assert len(calls) == 2
    assert len(sleeps) == 1


def test_send_message_raises_on_non_retryable(monkeypatch):
    calls = []
    sleeps = []

    async def fake_request_with_retries(*_args, **_kwargs):
        calls.append(1)
        return _Resp({"ok": False, "error_code": 400, "description": "Bad Request"})

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(telegram, "telegram_client", lambda: object())
    monkeypatch.setattr(telegram, "request_with_retries", fake_request_with_retries)
    monkeypatch.setattr(telegram.asyncio, "sleep", fake_sleep)

    with pytest.raises(RuntimeError):
        asyncio.run(telegram.send_message(-1001, "hello"))

    assert len(calls) == 1
    assert len(sleeps) == 0
