from __future__ import annotations

import hashlib

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.http import deepl_client, request_with_retries
from app.data.providers.cache import get_cached_payload, set_cached_payload

_CACHE_TTL_SECONDS = 30 * 24 * 3600

_LANG_MAP = {
    "en": "EN",
    "uk": "UK",
    "ru": "RU",
    "fr": "FR",
    "de": "DE",
    "pl": "PL",
    "pt": "PT-PT",
    "es": "ES",
}


def _cache_key(text: str, target_lang: str) -> str:
    h = hashlib.sha256()
    h.update(text.encode("utf-8", errors="ignore"))
    h.update(target_lang.encode("ascii", errors="ignore"))
    return f"deepl:{h.hexdigest()}"


async def translate_html(session: AsyncSession, text: str, target_lang: str, source_lang: str = "RU") -> str:
    if not text:
        return text
    if target_lang.lower() == source_lang.lower():
        return text
    if not settings.deepl_api_key:
        raise RuntimeError("DEEPL_API_KEY is not configured")

    target = _LANG_MAP.get(target_lang.lower(), target_lang.upper())
    source = _LANG_MAP.get(source_lang.lower(), source_lang.upper())
    cache_key = _cache_key(text, target)
    cached = await get_cached_payload(session, cache_key)
    if isinstance(cached, dict) and cached.get("text"):
        return str(cached["text"])

    client = deepl_client()
    resp = await request_with_retries(
        client,
        "POST",
        "/translate",
        data={
            "auth_key": settings.deepl_api_key,
            "text": text,
            "source_lang": source,
            "target_lang": target,
            "tag_handling": "html",
            "ignore_tags": "x",
            "tag_handling_version": "v2",
            "preserve_formatting": "1",
        },
    )
    data = resp.json()
    translations = data.get("translations") if isinstance(data, dict) else None
    if not translations:
        raise RuntimeError("DeepL translate failed: empty response")
    out = translations[0].get("text")
    if not out:
        raise RuntimeError("DeepL translate failed: missing text")

    await set_cached_payload(session, cache_key, {"text": out}, _CACHE_TTL_SECONDS)
    return str(out)
