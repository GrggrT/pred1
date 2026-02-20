import json
import hashlib
from datetime import datetime, timedelta, timezone
from contextvars import ContextVar
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.http import api_football_client, request_with_retries
from app.core.config import settings

_api_metrics: ContextVar[dict] = ContextVar("api_football_metrics", default={})
_api_budget: ContextVar[dict | None] = ContextVar("api_football_budget", default=None)
_api_force_refresh: ContextVar[bool] = ContextVar("api_football_force_refresh", default=False)
_PAGE_PARAM_ERROR_NEEDLES = (
    "page field do not exist",
    "page field does not exist",
)
_PAGE_PARAM_UNSUPPORTED: set[str] = set()


class ApiFootballBudgetExceeded(RuntimeError):
    def __init__(self, *, limit: int, used: int, endpoint: str | None = None, league_id: int | None = None):
        msg = f"API-Football run budget exceeded: used={used} limit={limit}"
        if endpoint:
            msg += f" endpoint={endpoint}"
        if league_id is not None:
            msg += f" league_id={league_id}"
        super().__init__(msg)
        self.limit = int(limit)
        self.used = int(used)
        self.endpoint = endpoint
        self.league_id = league_id


def set_run_cache_miss_budget(limit_cache_misses: int | None) -> None:
    limit = int(limit_cache_misses or 0)
    if limit > 0:
        _api_budget.set({"cache_misses_limit": limit, "cache_misses_used": 0})
    else:
        _api_budget.set(None)


def set_force_refresh(enabled: bool):
    return _api_force_refresh.set(bool(enabled))


def reset_force_refresh(token) -> None:
    _api_force_refresh.reset(token)


def get_force_refresh() -> bool:
    return bool(_api_force_refresh.get())


def _consume_cache_miss_budget(*, endpoint: str | None = None, league_id: int | None = None) -> None:
    budget = _api_budget.get()
    if not isinstance(budget, dict):
        return
    limit = int(budget.get("cache_misses_limit") or 0)
    used = int(budget.get("cache_misses_used") or 0)
    if limit <= 0:
        return
    if used >= limit:
        raise ApiFootballBudgetExceeded(limit=limit, used=used, endpoint=endpoint, league_id=league_id)
    budget["cache_misses_used"] = used + 1
    _api_budget.set(budget)


def reset_api_metrics() -> None:
    _api_metrics.set(
        {
            "requests": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "errors": 0,
            "status": {},
            "by_endpoint": {},
            "by_league": {},
            "by_endpoint_league": {},
        }
    )


def get_api_metrics() -> dict:
    out = dict(_api_metrics.get() or {})
    budget = _api_budget.get()
    if isinstance(budget, dict):
        limit = int(budget.get("cache_misses_limit") or 0)
        used = int(budget.get("cache_misses_used") or 0)
        if limit > 0:
            out["budget"] = {
                "cache_misses_limit": limit,
                "cache_misses_used": used,
                "cache_misses_remaining": max(0, limit - used),
                "exhausted": bool(used >= limit),
            }
    return out


def _inc_metric(
    status_code: int | None = None,
    *,
    cache_hit: bool | None = None,
    error: bool = False,
    endpoint: str | None = None,
    league_id: int | None = None,
) -> None:
    cur = _api_metrics.get() or {}
    if not cur:
        # Not tracking for this context.
        return

    def _bump(bucket: dict) -> None:
        if cache_hit is True:
            bucket["requests"] = int(bucket.get("requests", 0)) + 1
            bucket["cache_hits"] = int(bucket.get("cache_hits", 0)) + 1
        elif cache_hit is False:
            bucket["requests"] = int(bucket.get("requests", 0)) + 1
            bucket["cache_misses"] = int(bucket.get("cache_misses", 0)) + 1
        if error:
            bucket["errors"] = int(bucket.get("errors", 0)) + 1
        if status_code is not None:
            st = bucket.get("status") or {}
            st[str(int(status_code))] = int(st.get(str(int(status_code)), 0)) + 1
            bucket["status"] = st

    def _ensure(container: dict, key: str) -> dict:
        row = container.get(key)
        if isinstance(row, dict):
            return row
        row = {"requests": 0, "cache_hits": 0, "cache_misses": 0, "errors": 0, "status": {}}
        container[key] = row
        return row

    _bump(cur)

    endpoint_key = (endpoint or "").strip() or None
    league_key = None
    if league_id is not None:
        try:
            league_key = str(int(league_id))
        except Exception:
            league_key = None

    if endpoint_key:
        by_ep = cur.get("by_endpoint")
        if not isinstance(by_ep, dict):
            by_ep = {}
            cur["by_endpoint"] = by_ep
        _bump(_ensure(by_ep, endpoint_key))

    if league_key:
        by_lg = cur.get("by_league")
        if not isinstance(by_lg, dict):
            by_lg = {}
            cur["by_league"] = by_lg
        _bump(_ensure(by_lg, league_key))

    if endpoint_key and league_key:
        by_ep_lg = cur.get("by_endpoint_league")
        if not isinstance(by_ep_lg, dict):
            by_ep_lg = {}
            cur["by_endpoint_league"] = by_ep_lg
        ep_map = by_ep_lg.get(endpoint_key)
        if not isinstance(ep_map, dict):
            ep_map = {}
            by_ep_lg[endpoint_key] = ep_map
        _bump(_ensure(ep_map, league_key))

    _api_metrics.set(cur)


def _make_key(url: str, params: dict, cache_tag: str | None = None) -> str:
    raw = url + "|" + (cache_tag or "") + "|" + json.dumps(params, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _errors_empty(errors_obj) -> bool:
    if errors_obj is None:
        return True
    if isinstance(errors_obj, dict):
        return len(errors_obj) == 0
    if isinstance(errors_obj, list):
        return len(errors_obj) == 0
    if isinstance(errors_obj, str):
        return errors_obj.strip() == ""
    return False


def _payload_has_errors(payload) -> bool:
    if not isinstance(payload, dict):
        return False
    return not _errors_empty(payload.get("errors"))


def _is_page_param_error(exc: BaseException) -> bool:
    msg = str(exc or "").lower()
    return any(needle in msg for needle in _PAGE_PARAM_ERROR_NEEDLES)


async def get_cached(session: AsyncSession, cache_key: str):
    q = text(
        """
        SELECT payload FROM api_cache
        WHERE cache_key=:k AND expires_at > now()
    """
    )
    res = await session.execute(q, {"k": cache_key})
    row = res.first()
    payload = row[0] if row else None
    # Avoid poisoning the cache with API-Football quota/validation errors.
    if payload is not None and _payload_has_errors(payload):
        try:
            await session.execute(text("DELETE FROM api_cache WHERE cache_key=:k"), {"k": cache_key})
        except Exception:
            pass
        return None
    return payload


async def set_cached(session: AsyncSession, cache_key: str, payload: dict, ttl_seconds: int):
    expires = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
    q = text(
        """
        INSERT INTO api_cache(cache_key, payload, expires_at)
        VALUES(:k, CAST(:p AS jsonb), :e)
        ON CONFLICT (cache_key)
        DO UPDATE SET payload=CAST(:p AS jsonb), expires_at=:e
    """
    )
    await session.execute(
        q,
        {
            "k": cache_key,
            "p": json.dumps(payload, ensure_ascii=False),
            "e": expires,
        },
    )


async def api_get(
    session: AsyncSession,
    url: str,
    params: dict,
    ttl_seconds: int,
    *,
    cache_tag: str | None = None,
    metric_league_id: int | None = None,
):
    endpoint = (url or "").strip() or None
    league_id = metric_league_id
    try:
        if league_id is None and params and "league" in params and params.get("league") is not None:
            league_id = int(params.get("league"))
    except Exception:
        league_id = None
    key = _make_key(url, params, cache_tag=cache_tag)
    if not get_force_refresh():
        cached = await get_cached(session, key)
        if cached is not None:
            _inc_metric(cache_hit=True, endpoint=endpoint, league_id=league_id)
            return cached
    _consume_cache_miss_budget(endpoint=endpoint, league_id=league_id)
    _inc_metric(cache_hit=False, endpoint=endpoint, league_id=league_id)

    client = api_football_client()
    try:
        r = await request_with_retries(client, "GET", url, params=params)
        _inc_metric(status_code=r.status_code, endpoint=endpoint, league_id=league_id)
        r.raise_for_status()
        data = r.json()
        if _payload_has_errors(data):
            raise RuntimeError(f"API-Football returned errors for {url}: {data.get('errors')}")
    except Exception:
        _inc_metric(error=True, endpoint=endpoint, league_id=league_id)
        raise

    await set_cached(session, key, data, ttl_seconds)
    return data


async def api_get_all_pages(
    session: AsyncSession,
    url: str,
    params: dict,
    ttl_seconds: int,
    *,
    cache_tag: str | None = None,
    max_pages: int = 20,
    metric_league_id: int | None = None,
) -> dict:
    endpoint_key = (url or "").strip()
    if endpoint_key and endpoint_key in _PAGE_PARAM_UNSUPPORTED:
        return await api_get(
            session,
            url,
            params,
            ttl_seconds=ttl_seconds,
            cache_tag=cache_tag,
            metric_league_id=metric_league_id,
        )
    merged: dict | None = None
    page = 1
    while True:
        page_params = dict(params)
        page_params["page"] = page
        try:
            data = await api_get(
                session,
                url,
                page_params,
                ttl_seconds=ttl_seconds,
                cache_tag=cache_tag,
                metric_league_id=metric_league_id,
            )
        except RuntimeError as exc:
            if page == 1 and _is_page_param_error(exc):
                if endpoint_key:
                    _PAGE_PARAM_UNSUPPORTED.add(endpoint_key)
                return await api_get(
                    session,
                    url,
                    params,
                    ttl_seconds=ttl_seconds,
                    cache_tag=cache_tag,
                    metric_league_id=metric_league_id,
                )
            raise
        if merged is None:
            merged = data
            merged["response"] = list(data.get("response") or [])
        else:
            merged["response"].extend(list(data.get("response") or []))

        paging = data.get("paging") or {}
        total = int(paging.get("total") or 1)
        if page >= total:
            break
        page += 1
        if page > max_pages:
            break
    return merged or {"response": []}


async def get_fixtures(session: AsyncSession, league_id: int, season: int,
                       date_from: datetime, date_to: datetime):
    # Fixtures status/score change around kickoff; use shorter TTL for recent ranges to avoid stale NS/PENDING.
    today = datetime.now(timezone.utc).date()
    is_recent = date_to.date() >= (today - timedelta(days=1))
    ttl_seconds = (
        int(settings.api_football_fixtures_ttl_recent_seconds or 600)
        if is_recent
        else int(settings.api_football_fixtures_ttl_historical_seconds or 24 * 3600)
    )
    params = {
        "league": league_id,
        "season": season,
        "from": date_from.date().isoformat(),
        "to": date_to.date().isoformat(),
        "timezone": "UTC",
    }
    cache_tag = "fixtures_recent_v2" if is_recent else "fixtures_historical_v1"
    # Note: API-Football is paginated; fetch all pages for completeness.
    return await api_get_all_pages(
        session,
        "/fixtures",
        params,
        ttl_seconds=ttl_seconds,
        cache_tag=cache_tag,
        max_pages=20,
        metric_league_id=int(league_id),
    )


async def get_team_last_matches(session: AsyncSession, team_id: int, season: int, n: int = 15):
    params = {"team": team_id, "season": season, "last": n, "timezone": "UTC"}
    return await api_get(session, "/fixtures", params, ttl_seconds=12 * 3600)


async def get_lineups(session: AsyncSession, fixture_id: int):
    params = {"fixture": fixture_id}
    return await api_get(session, "/fixtures/lineups", params, ttl_seconds=7 * 24 * 3600)


async def get_fixtures_by_season(session: AsyncSession, league_id: int, season: int):
    params = {"league": league_id, "season": season, "timezone": "UTC"}
    return await api_get_all_pages(
        session,
        "/fixtures",
        params,
        ttl_seconds=24 * 3600,
        cache_tag="fixtures_season_v1",
        max_pages=50,
        metric_league_id=int(league_id),
    )


async def get_odds_by_season(session: AsyncSession, league_id: int, season: int, bookmaker_ids: list[int]):
    bookmaker_param = ",".join(str(b) for b in bookmaker_ids)
    params = {"league": league_id, "season": season, "bookmaker": bookmaker_param}
    ttl_seconds = int(settings.api_football_odds_season_ttl_seconds or 6 * 3600)
    return await api_get(
        session,
        "/odds",
        params,
        ttl_seconds=ttl_seconds,
        cache_tag="odds_season_v2",
        metric_league_id=int(league_id),
    )


async def get_odds_by_fixture(session: AsyncSession, fixture_id: int, bookmaker_ids: list[int], *, metric_league_id: int | None = None):
    params = {"fixture": fixture_id}
    if bookmaker_ids:
        params["bookmaker"] = ",".join(str(b) for b in bookmaker_ids)
    ttl_seconds = int(settings.api_football_odds_ttl_seconds or 300)
    return await api_get(
        session,
        "/odds",
        params,
        ttl_seconds=ttl_seconds,
        cache_tag="odds_fixture_v2",
        metric_league_id=metric_league_id,
    )


async def get_odds_by_date(session: AsyncSession, date_iso: str, bookmaker_ids: list[int]):
    params = {"date": date_iso, "timezone": "UTC"}
    if bookmaker_ids:
        params["bookmaker"] = ",".join(str(b) for b in bookmaker_ids)
    ttl_seconds = int(settings.api_football_odds_ttl_seconds or 300)
    return await api_get(session, "/odds", params, ttl_seconds=ttl_seconds, cache_tag="odds_date_v2")


async def get_odds_by_date_paged(
    session: AsyncSession,
    date_iso: str,
    bookmaker_ids: list[int],
    *,
    league_id: int | None = None,
    season: int | None = None,
    stop_fixture_ids: set[int] | None = None,
    max_pages: int = 10,
):
    params = {"date": date_iso, "timezone": "UTC"}
    if league_id is not None:
        params["league"] = int(league_id)
    if season is not None:
        params["season"] = int(season)
    if bookmaker_ids:
        params["bookmaker"] = ",".join(str(b) for b in bookmaker_ids)
    ttl_seconds = int(settings.api_football_odds_ttl_seconds or 300)

    target = {int(x) for x in (stop_fixture_ids or set()) if x}
    if not target:
        return await api_get(
            session,
            "/odds",
            params,
            ttl_seconds=ttl_seconds,
            cache_tag="odds_date_v2",
            metric_league_id=int(league_id) if league_id is not None else None,
        )

    merged: dict | None = None
    found: set[int] = set()
    page = 1
    while True:
        page_params = dict(params)
        page_params["page"] = page
        data = await api_get(
            session,
            "/odds",
            page_params,
            ttl_seconds=ttl_seconds,
            cache_tag="odds_date_v2",
            metric_league_id=int(league_id) if league_id is not None else None,
        )
        if merged is None:
            merged = data
            merged["response"] = list(data.get("response") or [])
        else:
            merged["response"].extend(list(data.get("response") or []))

        for entry in data.get("response") or []:
            fx = entry.get("fixture") or {}
            fid = fx.get("id")
            if fid is not None:
                try:
                    found.add(int(fid))
                except Exception:
                    pass

        if target.issubset(found):
            break

        paging = data.get("paging") or {}
        total = int(paging.get("total") or 1)
        if page >= total:
            break
        page += 1
        if page > max_pages:
            break
    return merged or {"response": []}


async def get_fixture_statistics(session: AsyncSession, fixture_id: int, *, metric_league_id: int | None = None):
    params = {"fixture": fixture_id}
    ttl_seconds = int(getattr(settings, "api_football_fixture_stats_ttl_seconds", 12 * 3600) or 12 * 3600)
    return await api_get(
        session,
        "/fixtures/statistics",
        params,
        ttl_seconds=ttl_seconds,
        metric_league_id=metric_league_id,
    )


async def get_injuries(session: AsyncSession, team_id: int, *, metric_league_id: int | None = None):
    params = {"team": team_id}
    season = int(getattr(settings, "season", 0) or 0)
    if season:
        params["season"] = season
    ttl_seconds = int(getattr(settings, "api_football_injuries_ttl_seconds", 3 * 3600) or 3 * 3600)
    return await api_get(session, "/injuries", params, ttl_seconds=ttl_seconds, metric_league_id=metric_league_id)


async def get_standings(session: AsyncSession, league_id: int, season: int):
    params = {"league": league_id, "season": season}
    ttl_seconds = int(getattr(settings, "api_football_standings_ttl_seconds", 12 * 3600) or 12 * 3600)
    return await api_get(session, "/standings", params, ttl_seconds=ttl_seconds, metric_league_id=int(league_id))
