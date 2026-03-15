#!/usr/bin/env python3
"""
Clear all messages from Telegram channels using Pyrogram (MTProto API).

Requires:
  - API_ID and API_HASH from https://my.telegram.org
  - Phone number for first-time auth (session is saved for reuse)

Usage:
  # Step 1: Auth (interactive - enter phone + code)
  python clear_channels_pyrogram.py --api-id YOUR_ID --api-hash YOUR_HASH

  # Or via env vars:
  export TG_API_ID=12345
  export TG_API_HASH=abcdef123456
  python clear_channels_pyrogram.py
"""

import asyncio
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pyrogram import Client

CHANNELS = {
    "en": int(os.environ.get("TELEGRAM_CHANNEL_EN", "0")),
    "uk": int(os.environ.get("TELEGRAM_CHANNEL_UK", "0")),
    "ru": int(os.environ.get("TELEGRAM_CHANNEL_RU", "0")),
    "fr": int(os.environ.get("TELEGRAM_CHANNEL_FR", "0")),
    "de": int(os.environ.get("TELEGRAM_CHANNEL_DE", "0")),
    "pl": int(os.environ.get("TELEGRAM_CHANNEL_PL", "0")),
    "pt": int(os.environ.get("TELEGRAM_CHANNEL_PT", "0")),
    "es": int(os.environ.get("TELEGRAM_CHANNEL_ES", "0")),
}


async def clear_channel(app: Client, lang: str, chat_id: int) -> int:
    """Delete all messages from a channel. Returns deleted count."""
    print(f"\n{'='*50}")
    print(f"Channel: {lang} (chat_id: {chat_id})")
    print(f"{'='*50}")

    if chat_id == 0:
        print("  Skipping (not configured)")
        return 0

    deleted = 0
    try:
        # Collect all message IDs
        msg_ids = []
        async for msg in app.get_chat_history(chat_id):
            msg_ids.append(msg.id)

        print(f"  Found {len(msg_ids)} messages")

        if not msg_ids:
            print("  Channel is already empty")
            return 0

        # Delete in batches of 100 (Pyrogram/MTProto limit)
        for i in range(0, len(msg_ids), 100):
            batch = msg_ids[i:i + 100]
            result = await app.delete_messages(chat_id, batch)
            # result is True or number of deleted messages
            if isinstance(result, bool):
                deleted += len(batch) if result else 0
            else:
                deleted += result if isinstance(result, int) else len(batch)
            print(f"  Deleted batch {i // 100 + 1}: {len(batch)} messages")
            await asyncio.sleep(0.5)  # Small pause between batches

    except Exception as e:
        print(f"  Error: {e}")

    print(f"  Total deleted: {deleted}")
    return deleted


async def main_async(api_id: int, api_hash: str, langs: list[str] | None):
    channels = {k: v for k, v in CHANNELS.items() if v != 0}
    if langs:
        channels = {k: v for k, v in channels.items() if k in langs}

    if not channels:
        print("No channels to clear.")
        return

    print(f"Channels to clear: {', '.join(channels.keys())}")
    print(f"Starting Pyrogram client (you may need to enter phone + code)...\n")

    # Session file stored in /code for persistence
    session_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "clear_session")

    async with Client(
        session_path,
        api_id=api_id,
        api_hash=api_hash,
    ) as app:
        me = await app.get_me()
        print(f"Logged in as: {me.first_name} (@{me.username or 'N/A'})\n")

        total = 0
        for lang, chat_id in channels.items():
            count = await clear_channel(app, lang, chat_id)
            total += count

        print(f"\n{'='*50}")
        print(f"DONE! Total deleted: {total}")
        print(f"{'='*50}")


def main():
    parser = argparse.ArgumentParser(description="Clear Telegram channels via Pyrogram")
    parser.add_argument("--api-id", type=int, default=int(os.environ.get("TG_API_ID", "0")))
    parser.add_argument("--api-hash", type=str, default=os.environ.get("TG_API_HASH", ""))
    parser.add_argument("--langs", nargs="*", help="Only clear specific languages")
    args = parser.parse_args()

    if not args.api_id or not args.api_hash:
        print("ERROR: Provide --api-id and --api-hash (or set TG_API_ID / TG_API_HASH)")
        print("Get them at: https://my.telegram.org -> API Development Tools")
        sys.exit(1)

    asyncio.run(main_async(args.api_id, args.api_hash, args.langs))


if __name__ == "__main__":
    main()
