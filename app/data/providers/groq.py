from __future__ import annotations

import hashlib

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.http import groq_client, request_with_retries
from app.core.logger import get_logger
from app.data.providers.cache import get_cached_payload, set_cached_payload

log = get_logger("providers.groq")

_CACHE_TTL_SECONDS = 24 * 3600  # 24 hours


def _cache_key(prompt: str, system_prompt: str, model: str, temperature: float) -> str:
    h = hashlib.sha256()
    h.update(prompt.encode("utf-8", errors="ignore"))
    h.update(system_prompt.encode("utf-8", errors="ignore"))
    h.update(model.encode("ascii", errors="ignore"))
    h.update(f"{temperature:.2f}".encode("ascii"))
    return f"groq:{h.hexdigest()}"


async def generate_text(
    session: AsyncSession,
    prompt: str,
    *,
    system_prompt: str = "",
    temperature: float = 0.7,
    max_tokens: int = 512,
    use_cache: bool = True,
) -> str:
    """Generate text via Groq API (OpenAI-compatible chat completions)."""
    if not settings.groq_api_key:
        raise RuntimeError("GROQ_API_KEY is not configured")
    if not settings.groq_enabled:
        raise RuntimeError("GROQ_ENABLED is not set to true")

    model = settings.groq_model or "llama-3.3-70b-versatile"

    if use_cache:
        key = _cache_key(prompt, system_prompt, model, temperature)
        cached = await get_cached_payload(session, key)
        if isinstance(cached, dict) and cached.get("text"):
            log.debug("groq_cache_hit key=%s", key[:16])
            return str(cached["text"])

    messages: list[dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_completion_tokens": max_tokens,
    }

    client = groq_client()
    resp = await request_with_retries(
        client,
        "POST",
        "/chat/completions",
        json=payload,
        retries=2,
        backoff_base=1.0,
        backoff_max=10.0,
    )

    if resp.status_code != 200:
        log.error("groq_api_error status=%s body=%s", resp.status_code, resp.text[:500])
        raise RuntimeError(f"Groq API error: {resp.status_code}")

    data = resp.json()
    choices = data.get("choices")
    if not choices:
        raise RuntimeError("Groq API: empty choices in response")

    text_out = choices[0].get("message", {}).get("content", "").strip()
    if not text_out:
        raise RuntimeError("Groq API: empty content in response")

    if use_cache:
        await set_cached_payload(session, key, {"text": text_out}, _CACHE_TTL_SECONDS)
        log.debug("groq_cache_set key=%s len=%d", key[:16], len(text_out))

    log.info(
        "groq_generate model=%s tokens_in=%s tokens_out=%s",
        model,
        data.get("usage", {}).get("prompt_tokens"),
        data.get("usage", {}).get("completion_tokens"),
    )
    return text_out
