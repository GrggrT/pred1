from __future__ import annotations

import asyncio
import json
from typing import Iterable

from app.core.http import request_with_retries, telegram_client

_TELEGRAM_RETRYABLE_CODES = {429, 500, 502, 503, 504}
_TELEGRAM_MAX_RETRIES = 3
_TELEGRAM_BACKOFF_BASE = 0.6
_TELEGRAM_BACKOFF_CAP = 8.0


def _payload_retry_after(data: dict) -> float | None:
    try:
        raw = (((data or {}).get("parameters") or {}).get("retry_after"))
        if raw is None:
            return None
        retry_after = float(raw)
        return retry_after if retry_after > 0 else None
    except Exception:
        return None


def _backoff_delay(attempt: int, *, retry_after: float | None) -> float:
    delay = min(_TELEGRAM_BACKOFF_CAP, _TELEGRAM_BACKOFF_BASE * (2 ** attempt))
    if retry_after is not None:
        delay = max(delay, retry_after)
    return delay


async def send_message(
    chat_id: int,
    text: str,
    *,
    parse_mode: str = "HTML",
    reply_to_message_id: int | None = None,
) -> int:
    client = telegram_client()
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    if reply_to_message_id is not None:
        payload["reply_parameters"] = {"message_id": reply_to_message_id}
    for attempt in range(_TELEGRAM_MAX_RETRIES + 1):
        resp = await request_with_retries(
            client,
            "POST",
            "/sendMessage",
            json=payload,
        )
        data = resp.json()
        if isinstance(data, dict) and data.get("ok"):
            msg = data.get("result") or {}
            msg_id = msg.get("message_id")
            if not msg_id:
                raise RuntimeError("Telegram sendMessage missing message_id")
            return int(msg_id)

        code = int((data or {}).get("error_code") or 0) if isinstance(data, dict) else 0
        retry_after = _payload_retry_after(data if isinstance(data, dict) else {})
        if code in _TELEGRAM_RETRYABLE_CODES and attempt < _TELEGRAM_MAX_RETRIES:
            await asyncio.sleep(_backoff_delay(attempt, retry_after=retry_after))
            continue
        raise RuntimeError(f"Telegram sendMessage failed: {json.dumps(data)[:500]}")
    raise RuntimeError("Telegram sendMessage failed: exhausted retries")


async def send_message_parts(
    chat_id: int,
    parts: Iterable[str],
    *,
    parse_mode: str = "HTML",
    reply_to_message_id: int | None = None,
) -> list[int]:
    message_ids: list[int] = []
    for part in parts:
        msg_id = await send_message(
            chat_id,
            part,
            parse_mode=parse_mode,
            reply_to_message_id=reply_to_message_id,
        )
        message_ids.append(msg_id)
    return message_ids


async def send_photo(
    chat_id: int,
    image_bytes: bytes,
    *,
    filename: str = "prediction.png",
    caption: str | None = None,
    parse_mode: str = "HTML",
) -> int:
    client = telegram_client()
    data = {"chat_id": str(chat_id)}
    if caption:
        data["caption"] = caption
        data["parse_mode"] = parse_mode
    for attempt in range(_TELEGRAM_MAX_RETRIES + 1):
        resp = await request_with_retries(
            client,
            "POST",
            "/sendPhoto",
            data=data,
            files={"photo": (filename, image_bytes, "image/png")},
        )
        payload = resp.json()
        if isinstance(payload, dict) and payload.get("ok"):
            msg = payload.get("result") or {}
            msg_id = msg.get("message_id")
            if not msg_id:
                raise RuntimeError("Telegram sendPhoto missing message_id")
            return int(msg_id)

        code = int((payload or {}).get("error_code") or 0) if isinstance(payload, dict) else 0
        retry_after = _payload_retry_after(payload if isinstance(payload, dict) else {})
        if code in _TELEGRAM_RETRYABLE_CODES and attempt < _TELEGRAM_MAX_RETRIES:
            await asyncio.sleep(_backoff_delay(attempt, retry_after=retry_after))
            continue
        raise RuntimeError(f"Telegram sendPhoto failed: {json.dumps(payload)[:500]}")
    raise RuntimeError("Telegram sendPhoto failed: exhausted retries")
