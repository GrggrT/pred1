import asyncio

import httpx

from app.core.http import request_with_retries


def test_request_with_retries_retries_on_500():
    calls = {"count": 0}

    def handler(request):
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(500, request=request)
        return httpx.Response(200, json={"ok": True}, request=request)

    async def _sleep(_delay):
        return None

    async def _run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport, base_url="https://example.com") as client:
            resp = await request_with_retries(
                client,
                "GET",
                "/test",
                retries=1,
                backoff_base=0.0,
                backoff_max=0.0,
                _sleep=_sleep,
            )
            assert resp.status_code == 200

    asyncio.run(_run())
    assert calls["count"] == 2


def test_request_with_retries_respects_retry_after():
    calls = {"count": 0}
    sleeps = []

    def handler(request):
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(429, headers={"Retry-After": "2"}, request=request)
        return httpx.Response(200, request=request)

    async def _sleep(delay):
        sleeps.append(delay)

    async def _run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport, base_url="https://example.com") as client:
            resp = await request_with_retries(
                client,
                "GET",
                "/test",
                retries=1,
                backoff_base=0.0,
                backoff_max=0.0,
                _sleep=_sleep,
            )
            assert resp.status_code == 200

    asyncio.run(_run())
    assert calls["count"] == 2
    assert sleeps and sleeps[0] >= 2.0


def test_request_with_retries_retries_on_request_error():
    calls = {"count": 0}

    def handler(request):
        calls["count"] += 1
        if calls["count"] == 1:
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(200, request=request)

    async def _sleep(_delay):
        return None

    async def _run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport, base_url="https://example.com") as client:
            resp = await request_with_retries(
                client,
                "GET",
                "/test",
                retries=1,
                backoff_base=0.0,
                backoff_max=0.0,
                _sleep=_sleep,
            )
            assert resp.status_code == 200

    asyncio.run(_run())
    assert calls["count"] == 2
