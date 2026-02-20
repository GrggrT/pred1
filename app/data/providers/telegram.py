from __future__ import annotations

import json
from typing import Iterable

from app.core.http import request_with_retries, telegram_client


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
    resp = await request_with_retries(
        client,
        "POST",
        "/sendMessage",
        json=payload,
    )
    data = resp.json()
    if not isinstance(data, dict) or not data.get("ok"):
        raise RuntimeError(f"Telegram sendMessage failed: {json.dumps(data)[:500]}")
    msg = data.get("result") or {}
    msg_id = msg.get("message_id")
    if not msg_id:
        raise RuntimeError("Telegram sendMessage missing message_id")
    return int(msg_id)


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
    resp = await request_with_retries(
        client,
        "POST",
        "/sendPhoto",
        data=data,
        files={"photo": (filename, image_bytes, "image/png")},
    )
    payload = resp.json()
    if not isinstance(payload, dict) or not payload.get("ok"):
        raise RuntimeError(f"Telegram sendPhoto failed: {json.dumps(payload)[:500]}")
    msg = payload.get("result") or {}
    msg_id = msg.get("message_id")
    if not msg_id:
        raise RuntimeError("Telegram sendPhoto missing message_id")
    return int(msg_id)
