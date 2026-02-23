import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from app import main


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, *_args, **_kwargs):
        return _FakeResult(self._rows)


def test_api_publish_metrics_aggregates_rates(monkeypatch):
    monkeypatch.setattr(main.settings, "publish_html_fallback_alert_pct", 20)
    monkeypatch.setattr(main.settings, "publish_metrics_window_hours", 24)

    now = datetime.now(timezone.utc)
    rows = [
        SimpleNamespace(
            status="published",
            created_at=now,
            payload={"html_attempted": True, "html_render_failed": False, "render_time_ms": 900},
        ),
        SimpleNamespace(
            status="published",
            created_at=now,
            payload={
                "html_attempted": True,
                "html_render_failed": True,
                "headline_image_fallback": "html_render_failed",
                "render_time_ms": 1200,
            },
        ),
        SimpleNamespace(status="send_failed", created_at=now, payload={"html_attempted": False}),
        SimpleNamespace(status="render_failed", created_at=now, payload={"reason": "html_render_failed"}),
    ]

    session = _FakeSession(rows)
    result = asyncio.run(main.api_publish_metrics(hours=24, _=None, session=session))

    assert result["rows_total"] == 4
    assert result["status_counts"]["published"] == 2
    assert result["status_counts"]["send_failed"] == 1
    assert result["status_counts"]["render_failed"] == 1

    # html_attempts=2, html_failures=1, fallback=1
    assert result["html_fail_rate"] == 0.5
    assert result["html_fallback_rate"] == 0.5

    # telegram_attempts=3 (2 published + 1 send_failed), failures=1
    assert result["telegram_fail_rate"] == round(1 / 3, 4)

    # render times collected from html attempts only
    assert result["render_time_ms"]["samples"] == 2
    assert result["render_time_ms"]["avg"] == 1050.0

    # threshold 20%, fallback 50% -> alert triggered
    assert result["alert"]["triggered"] is True
