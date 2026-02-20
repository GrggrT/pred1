import asyncio
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx

from .config import settings

_DEFAULT_RETRY_STATUSES = {429, 500, 502, 503, 504}
_api_football_client: httpx.AsyncClient | None = None
_openweather_client: httpx.AsyncClient | None = None
_telegram_client: httpx.AsyncClient | None = None
_deepl_client: httpx.AsyncClient | None = None
_assets_client: httpx.AsyncClient | None = None
_telegram_token: str | None = None
_deepl_base: str | None = None


def _http_limits() -> httpx.Limits:
    return httpx.Limits(max_connections=20, max_keepalive_connections=10)


def api_football_client() -> httpx.AsyncClient:
    global _api_football_client
    if _api_football_client is None or _api_football_client.is_closed:
        _api_football_client = httpx.AsyncClient(
            base_url=settings.api_football_base,
            headers={
                "x-apisports-key": settings.api_football_key,
                "x-rapidapi-host": settings.api_football_host,
            },
            timeout=httpx.Timeout(20.0),
            limits=_http_limits(),
        )
    return _api_football_client


def openweather_client() -> httpx.AsyncClient:
    global _openweather_client
    if _openweather_client is None or _openweather_client.is_closed:
        _openweather_client = httpx.AsyncClient(
            base_url=settings.openweather_base,
            timeout=httpx.Timeout(15.0),
            limits=_http_limits(),
        )
    return _openweather_client


def telegram_client() -> httpx.AsyncClient:
    global _telegram_client, _telegram_token
    token = (settings.telegram_bot_token or "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")
    if _telegram_client is None or _telegram_client.is_closed or _telegram_token != token:
        _telegram_token = token
        _telegram_client = httpx.AsyncClient(
            base_url=f"https://api.telegram.org/bot{token}",
            timeout=httpx.Timeout(20.0),
            limits=_http_limits(),
        )
    return _telegram_client


def deepl_client() -> httpx.AsyncClient:
    global _deepl_client, _deepl_base
    base = (settings.deepl_api_base or "").strip() or "https://api-free.deepl.com/v2"
    if _deepl_client is None or _deepl_client.is_closed or _deepl_base != base:
        _deepl_base = base
        _deepl_client = httpx.AsyncClient(
            base_url=base,
            timeout=httpx.Timeout(20.0),
            limits=_http_limits(),
        )
    return _deepl_client


def assets_client() -> httpx.AsyncClient:
    global _assets_client
    if _assets_client is None or _assets_client.is_closed:
        _assets_client = httpx.AsyncClient(
            timeout=httpx.Timeout(15.0),
            limits=_http_limits(),
            follow_redirects=True,
        )
    return _assets_client


async def init_http_clients() -> None:
    api_football_client()
    openweather_client()
    if settings.telegram_bot_token:
        telegram_client()
    if settings.deepl_api_key:
        deepl_client()


async def close_http_clients() -> None:
    global _api_football_client, _openweather_client, _telegram_client, _deepl_client, _assets_client, _telegram_token, _deepl_base
    if _api_football_client is not None and not _api_football_client.is_closed:
        await _api_football_client.aclose()
    if _openweather_client is not None and not _openweather_client.is_closed:
        await _openweather_client.aclose()
    if _telegram_client is not None and not _telegram_client.is_closed:
        await _telegram_client.aclose()
    if _deepl_client is not None and not _deepl_client.is_closed:
        await _deepl_client.aclose()
    if _assets_client is not None and not _assets_client.is_closed:
        await _assets_client.aclose()
    _api_football_client = None
    _openweather_client = None
    _telegram_client = None
    _deepl_client = None
    _assets_client = None
    _telegram_token = None
    _deepl_base = None


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (dt - datetime.now(timezone.utc)).total_seconds())
    except Exception:
        return None


def _backoff_delay(attempt: int, base: float, cap: float, retry_after: float | None) -> float:
    delay = min(cap, base * (2 ** attempt))
    if retry_after is not None:
        delay = max(delay, retry_after)
    return delay


async def request_with_retries(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    params: dict | None = None,
    retries: int = 3,
    backoff_base: float = 0.5,
    backoff_max: float = 8.0,
    retry_statuses: set[int] | None = None,
    retry_exceptions: tuple[type[BaseException], ...] = (httpx.RequestError,),
    _sleep=asyncio.sleep,
    **kwargs,
) -> httpx.Response:
    statuses = retry_statuses or _DEFAULT_RETRY_STATUSES
    for attempt in range(retries + 1):
        try:
            response = await client.request(method, url, params=params, **kwargs)
        except retry_exceptions:
            if attempt >= retries:
                raise
            await _sleep(_backoff_delay(attempt, backoff_base, backoff_max, None))
            continue

        if response.status_code in statuses:
            if attempt >= retries:
                return response
            retry_after = _parse_retry_after(response.headers.get("Retry-After"))
            await response.aclose()
            await _sleep(_backoff_delay(attempt, backoff_base, backoff_max, retry_after))
            continue
        return response

    raise RuntimeError("request_with_retries: exhausted retries")
