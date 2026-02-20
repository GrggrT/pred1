from __future__ import annotations

from datetime import datetime, timezone
import os
from typing import Optional


def utcnow() -> datetime:
    """Return aware UTC datetime."""
    bt = os.getenv("BACKTEST_CURRENT_DATE")
    if bt:
        try:
            return datetime.fromisoformat(bt).replace(tzinfo=timezone.utc)
        except Exception:
            return datetime.now(timezone.utc)
    return datetime.now(timezone.utc)


def ensure_aware_utc(value: datetime) -> datetime:
    """
    Ensure datetime is timezone-aware in UTC.

    - If naive, assume it is UTC and attach tzinfo.
    - If aware, convert to UTC.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def to_utc(value: Optional[datetime]) -> Optional[datetime]:
    """Convert datetime to aware UTC; pass None through."""
    if value is None:
        return None
    return ensure_aware_utc(value)
