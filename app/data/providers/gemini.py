"""Gemini provider with Google Search grounding (2.5 Flash default)."""

from __future__ import annotations

import hashlib

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.http import gemini_client, request_with_retries
from app.core.logger import get_logger
from app.data.providers.cache import get_cached_payload, set_cached_payload

log = get_logger("providers.gemini")

_CACHE_TTL_SECONDS = 12 * 3600  # 12 hours (shorter than Groq — web data changes)


def _cache_key(prompt: str, system_prompt: str, model: str, temperature: float) -> str:
    h = hashlib.sha256()
    h.update(prompt.encode("utf-8", errors="ignore"))
    h.update(system_prompt.encode("utf-8", errors="ignore"))
    h.update(model.encode("ascii", errors="ignore"))
    h.update(f"{temperature:.2f}".encode("ascii"))
    return f"gemini:{h.hexdigest()}"


async def generate_with_search(
    session: AsyncSession,
    prompt: str,
    *,
    system_prompt: str = "",
    temperature: float = 0.7,
    max_tokens: int = 2048,
    use_cache: bool = True,
) -> str:
    """Generate text via Gemini API with Google Search grounding.

    Uses the generateContent endpoint with google_search tool enabled,
    allowing the model to search the web for real-time information.
    """
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured")

    model = settings.gemini_model or "gemini-2.5-flash"

    if use_cache:
        key = _cache_key(prompt, system_prompt, model, temperature)
        cached = await get_cached_payload(session, key)
        if isinstance(cached, dict) and cached.get("text"):
            log.debug("gemini_cache_hit key=%s", key[:16])
            return str(cached["text"])

    # Gemini 2.5 uses thinking tokens from maxOutputTokens budget,
    # so ensure we have enough room for both thinking and output
    effective_max = max(max_tokens, 1024)

    # Build request payload
    payload: dict = {
        "contents": [
            {"role": "user", "parts": [{"text": prompt}]},
        ],
        "tools": [{"google_search": {}}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": effective_max,
        },
    }

    if system_prompt:
        payload["systemInstruction"] = {
            "parts": [{"text": system_prompt}],
        }

    # Gemini uses API key as query parameter
    url = f"/models/{model}:generateContent"

    client = gemini_client()
    resp = await request_with_retries(
        client,
        "POST",
        url,
        params={"key": settings.gemini_api_key},
        json=payload,
        retries=2,
        backoff_base=2.0,
        backoff_max=15.0,
    )

    if resp.status_code != 200:
        log.error(
            "gemini_api_error status=%s body=%s",
            resp.status_code,
            resp.text[:500],
        )
        raise RuntimeError(f"Gemini API error: {resp.status_code}")

    data = resp.json()

    # Parse response
    candidates = data.get("candidates")
    if not candidates:
        raise RuntimeError("Gemini API: empty candidates in response")

    content = candidates[0].get("content", {})
    parts = content.get("parts", [])
    finish_reason = candidates[0].get("finishReason", "")

    if not parts:
        log.warning(
            "gemini_empty_parts finish_reason=%s thinking=%s",
            finish_reason,
            data.get("usageMetadata", {}).get("thoughtsTokenCount"),
        )
        raise RuntimeError("Gemini API: empty parts in response")

    # Gemini 2.5 Flash may return multiple text parts (intermediate + final).
    # Take the LAST non-thought text part as the final answer.
    last_text = ""
    for p in parts:
        if p.get("thought"):
            continue
        txt = p.get("text", "")
        if txt:
            last_text = txt

    # If all parts were thought parts, fall back to ANY text part
    if not last_text:
        for p in parts:
            txt = p.get("text", "")
            if txt:
                last_text = txt
                break

    text_out = last_text.strip()

    if not text_out:
        raise RuntimeError("Gemini API: empty text in response")

    if use_cache:
        await set_cached_payload(session, key, {"text": text_out}, _CACHE_TTL_SECONDS)
        log.debug("gemini_cache_set key=%s len=%d", key[:16], len(text_out))

    usage = data.get("usageMetadata", {})
    log.info(
        "gemini_generate model=%s tokens_in=%s tokens_out=%s grounded=%s",
        model,
        usage.get("promptTokenCount"),
        usage.get("candidatesTokenCount"),
        bool(candidates[0].get("groundingMetadata")),
    )
    return text_out
