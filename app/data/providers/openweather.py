from sqlalchemy.ext.asyncio import AsyncSession  # noqa: F401
from app.core.http import openweather_client, request_with_retries
from app.core.config import settings


def _has_coords(lat, lon):
    return lat is not None and lon is not None


async def get_weather(lat: float, lon: float):
    if not settings.openweather_key or not _has_coords(lat, lon):
        return None
    client = openweather_client()
    r = await request_with_retries(
        client,
        "GET",
        "/weather",
        params={
            "lat": lat,
            "lon": lon,
            "appid": settings.openweather_key,
            "units": "metric",
        },
        retries=2,
        backoff_base=0.5,
        backoff_max=4.0,
    )
    r.raise_for_status()
    return r.json()
