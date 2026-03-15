#!/usr/bin/env python3
"""
Clear all bot messages from configured Telegram channels.

Usage:
    # Dry-run (just count messages, don't delete):
    python scripts/clear_telegram_channels.py --dry-run

    # Actually delete all messages:
    python scripts/clear_telegram_channels.py

    # Delete messages only from specific channels:
    python scripts/clear_telegram_channels.py --langs ru en

Run inside the Docker container:
    docker compose -f deploy/docker-compose.gcp.yml exec app python scripts/clear_telegram_channels.py
"""

import asyncio
import argparse
import os
import sys
import time

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import httpx
except ImportError:
    print("httpx not found, trying with requests...")
    httpx = None
    import requests


BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

CHANNELS = {
    "en": os.environ.get("TELEGRAM_CHANNEL_EN", ""),
    "uk": os.environ.get("TELEGRAM_CHANNEL_UK", ""),
    "ru": os.environ.get("TELEGRAM_CHANNEL_RU", ""),
    "fr": os.environ.get("TELEGRAM_CHANNEL_FR", ""),
    "de": os.environ.get("TELEGRAM_CHANNEL_DE", ""),
    "pl": os.environ.get("TELEGRAM_CHANNEL_PL", ""),
    "pt": os.environ.get("TELEGRAM_CHANNEL_PT", ""),
    "es": os.environ.get("TELEGRAM_CHANNEL_ES", ""),
}

API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"


def delete_message_sync(chat_id: str, message_id: int) -> bool:
    """Delete a single message. Returns True if deleted."""
    url = f"{API_BASE}/deleteMessage"
    try:
        if httpx:
            r = httpx.post(url, json={"chat_id": chat_id, "message_id": message_id}, timeout=10)
            data = r.json()
        else:
            r = requests.post(url, json={"chat_id": chat_id, "message_id": message_id}, timeout=10)
            data = r.json()
        return data.get("ok", False)
    except Exception as e:
        print(f"  Error deleting msg {message_id}: {e}")
        return False


def get_latest_message_id(chat_id: str) -> int | None:
    """Send a temp message to get the latest message_id, then delete it."""
    url = f"{API_BASE}/sendMessage"
    try:
        if httpx:
            r = httpx.post(url, json={"chat_id": chat_id, "text": "🔄 Cleaning..."}, timeout=10)
            data = r.json()
        else:
            r = requests.post(url, json={"chat_id": chat_id, "text": "🔄 Cleaning..."}, timeout=10)
            data = r.json()

        if data.get("ok"):
            msg_id = data["result"]["message_id"]
            # Delete the temp message too
            delete_message_sync(chat_id, msg_id)
            return msg_id
        else:
            print(f"  Failed to send probe message: {data.get('description', 'unknown error')}")
            return None
    except Exception as e:
        print(f"  Error getting latest message_id: {e}")
        return None


def clear_channel(lang: str, chat_id: str, dry_run: bool = False) -> int:
    """Clear all messages from a channel. Returns count of deleted messages."""
    print(f"\n{'='*50}")
    print(f"Channel: {lang} (chat_id: {chat_id})")
    print(f"{'='*50}")

    # Get the latest message ID
    latest_id = get_latest_message_id(chat_id)
    if latest_id is None:
        print("  Could not determine latest message ID. Skipping.")
        return 0

    print(f"  Latest message ID: {latest_id}")

    if dry_run:
        print(f"  [DRY RUN] Would try to delete messages 1..{latest_id}")
        return 0

    deleted = 0
    failed = 0
    batch_size = 30  # Telegram rate limit: ~30 requests/sec

    # Delete from newest to oldest (more likely to exist)
    for msg_id in range(latest_id, 0, -1):
        ok = delete_message_sync(chat_id, msg_id)
        if ok:
            deleted += 1
            if deleted % 50 == 0:
                print(f"  Deleted {deleted} messages so far... (current: {msg_id})")
        else:
            failed += 1
            # If we've had 200 consecutive failures, probably no more messages
            if failed > 200 and deleted == 0:
                print(f"  No messages found in range. Stopping.")
                break
            if failed > 500:
                print(f"  Too many consecutive failures. Stopping.")
                break

        # Rate limiting: pause every batch_size requests
        if (deleted + failed) % batch_size == 0:
            time.sleep(1.1)

    print(f"  Done: {deleted} deleted, {failed} not found/failed")
    return deleted


def main():
    parser = argparse.ArgumentParser(description="Clear Telegram channel messages")
    parser.add_argument("--dry-run", action="store_true", help="Don't actually delete, just show what would happen")
    parser.add_argument("--force", action="store_true", help="Skip confirmation prompt")
    parser.add_argument("--langs", nargs="*", help="Only clear specific languages (e.g., --langs ru en)")
    args = parser.parse_args()

    if not BOT_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN not set")
        sys.exit(1)

    # Filter channels
    channels = {k: v for k, v in CHANNELS.items() if v}
    if args.langs:
        channels = {k: v for k, v in channels.items() if k in args.langs}

    if not channels:
        print("No channels configured or selected.")
        sys.exit(1)

    print(f"Channels to clear: {', '.join(channels.keys())}")
    if args.dry_run:
        print("[DRY RUN MODE - no messages will be deleted]")
    elif not args.force:
        print("⚠️  WARNING: This will PERMANENTLY delete all bot messages!")
        confirm = input("Type 'YES' to confirm: ")
        if confirm != "YES":
            print("Aborted.")
            sys.exit(0)
    else:
        print("⚠️  FORCE MODE - deleting all bot messages!")

    total_deleted = 0
    for lang, chat_id in channels.items():
        deleted = clear_channel(lang, chat_id, dry_run=args.dry_run)
        total_deleted += deleted

    print(f"\n{'='*50}")
    print(f"Total deleted: {total_deleted}")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
