import asyncio
from types import SimpleNamespace

from app.services import publishing


class _Result:
    def __init__(self, row=None):
        self._row = row

    def first(self):
        return self._row


class _FakeSession:
    def __init__(self, *, existing_ok_publication: bool = False, lock_available: bool = True):
        self.existing_ok_publication = existing_ok_publication
        self.lock_available = lock_available
        self.execute_calls = 0
        self.committed = False

    async def execute(self, statement, *_args, **_kwargs):
        self.execute_calls += 1
        sql = str(statement)
        if "pg_try_advisory_xact_lock" in sql:
            return _Result(SimpleNamespace(ok=self.lock_available))
        if self.existing_ok_publication:
            return _Result((1,))
        return _Result(None)

    async def commit(self):
        self.committed = True


def _configure_settings(monkeypatch, *, publish_headline_image: bool = True):
    monkeypatch.setattr(publishing.settings, "telegram_bot_token", "test-bot-token", raising=False)
    monkeypatch.setattr(publishing.settings, "telegram_channel_en", "-1001234567890", raising=False)
    monkeypatch.setattr(publishing.settings, "telegram_channel_uk", "", raising=False)
    monkeypatch.setattr(publishing.settings, "telegram_channel_ru", "", raising=False)
    monkeypatch.setattr(publishing.settings, "telegram_channel_fr", "", raising=False)
    monkeypatch.setattr(publishing.settings, "telegram_channel_de", "", raising=False)
    monkeypatch.setattr(publishing.settings, "telegram_channel_pl", "", raising=False)
    monkeypatch.setattr(publishing.settings, "telegram_channel_pt", "", raising=False)
    monkeypatch.setattr(publishing.settings, "telegram_channel_es", "", raising=False)
    monkeypatch.setattr(publishing.settings, "publish_mode", "manual", raising=False)
    monkeypatch.setattr(publishing.settings, "publish_deepl_fallback", False, raising=False)
    monkeypatch.setattr(publishing.settings, "publish_headline_image", publish_headline_image, raising=False)


def _fixture_data():
    fixture = SimpleNamespace(
        fixture_id=1388515,
        league_name="Premier League",
        home_logo_url=None,
        away_logo_url=None,
        league_logo_url=None,
    )
    pred_total = SimpleNamespace(initial_odd=2.75, confidence=0.462, signal_score=70.0)
    pred_1x2 = SimpleNamespace(initial_odd=1.80, confidence=0.55, signal_score=60.0)
    preview = {
        "mode": "manual",
        "markets": [
            {
                "market": "TOTAL",
                "headline_raw": "x",
                "analysis_raw": "y",
                "quality_level": 0,
                "experimental": False,
                "reasons": [],
            }
        ],
    }
    data = {
        "fixture": fixture,
        "indices": {},
        "decision_1x2": None,
        "pred_1x2": pred_1x2,
        "pred_total": pred_total,
    }
    return preview, data


def _install_common_mocks(monkeypatch):
    preview, data = _fixture_data()

    async def fake_build_preview_internal(_session, _fixture_id):
        return preview, data

    def fake_build_market_text(*_args, **_kwargs):
        return (
            "HOT PREDICTION\nManchester City vs Newcastle\n"
            "21 February 2026 | 12:30 UTC\nRECOMMENDATION\nTotal Under 2.5\n@ 2.75",
            "Model analysis",
        )

    async def fake_fetch_logo_bytes(_url):
        return None

    async def fake_fetch_image_visual_context(_session, _fixture):
        return publishing.ImageVisualContext()

    monkeypatch.setattr(publishing, "_build_preview_internal", fake_build_preview_internal)
    monkeypatch.setattr(publishing, "_build_market_text", fake_build_market_text)
    monkeypatch.setattr(publishing, "_fetch_logo_bytes", fake_fetch_logo_bytes)
    monkeypatch.setattr(publishing, "_fetch_image_visual_context", fake_fetch_image_visual_context)


def test_publish_fixture_uses_html_image(monkeypatch):
    _configure_settings(monkeypatch, publish_headline_image=True)
    _install_common_mocks(monkeypatch)

    records = []
    photo_calls = []
    parts_calls = []

    async def fake_record_publication(*args, **kwargs):
        records.append({"args": args, "kwargs": kwargs})

    def fake_render_headline_image_html(*_args, **_kwargs):
        return b"fake-png"

    async def fake_send_photo(channel_id, image_bytes):
        photo_calls.append((channel_id, image_bytes))
        return 501

    async def fake_send_message_parts(channel_id, parts, reply_to_message_id=None):
        parts_calls.append((channel_id, tuple(parts), reply_to_message_id))
        return [601]

    monkeypatch.setattr(publishing, "_record_publication", fake_record_publication)
    monkeypatch.setattr(publishing, "render_headline_image_html", fake_render_headline_image_html)
    monkeypatch.setattr(publishing, "send_photo", fake_send_photo)
    monkeypatch.setattr(publishing, "send_message_parts", fake_send_message_parts)

    session = _FakeSession(existing_ok_publication=False)
    result = asyncio.run(publishing.publish_fixture(session, 1388515, dry_run=False, force=False))

    assert result["results"][0]["status"] == "ok"
    assert session.committed is True
    assert len(photo_calls) == 1
    assert len(parts_calls) == 1

    payload = records[-1]["kwargs"]["payload"]
    assert payload["headline_image"] is True
    assert payload["headline_image_fallback"] is None


def test_publish_fixture_reservation_lock_skip(monkeypatch):
    _configure_settings(monkeypatch, publish_headline_image=True)
    _install_common_mocks(monkeypatch)

    session = _FakeSession(existing_ok_publication=False, lock_available=False)
    result = asyncio.run(publishing.publish_fixture(session, 1388515, dry_run=False, force=False))

    assert result["reservation_locked"] is True
    assert result["results"][0]["status"] == "skipped"
    assert result["results"][0]["reason"] == "publish_locked"
    assert session.committed is False


def test_publish_fixture_html_failure_falls_back_to_text(monkeypatch):
    _configure_settings(monkeypatch, publish_headline_image=True)
    _install_common_mocks(monkeypatch)

    records = []
    photo_calls = []
    parts_calls = []

    async def fake_record_publication(*args, **kwargs):
        records.append({"args": args, "kwargs": kwargs})

    def fake_render_headline_image_html(*_args, **_kwargs):
        raise RuntimeError("renderer boom")

    async def fake_send_photo(channel_id, image_bytes):
        photo_calls.append((channel_id, image_bytes))
        return 777

    async def fake_send_message_parts(channel_id, parts, reply_to_message_id=None):
        parts_calls.append((channel_id, tuple(parts), reply_to_message_id))
        return [888]

    monkeypatch.setattr(publishing, "_record_publication", fake_record_publication)
    monkeypatch.setattr(publishing, "render_headline_image_html", fake_render_headline_image_html)
    monkeypatch.setattr(publishing, "send_photo", fake_send_photo)
    monkeypatch.setattr(publishing, "send_message_parts", fake_send_message_parts)

    session = _FakeSession(existing_ok_publication=False)
    result = asyncio.run(publishing.publish_fixture(session, 1388515, dry_run=False, force=False))

    assert result["results"][0]["status"] == "ok"
    assert session.committed is True
    assert len(photo_calls) == 0
    assert len(parts_calls) == 2

    payload = records[-1]["kwargs"]["payload"]
    assert payload["headline_image"] is False
    assert payload["headline_image_fallback"] == "html_render_failed"


def test_publish_fixture_skips_when_already_published(monkeypatch):
    _configure_settings(monkeypatch, publish_headline_image=True)
    _install_common_mocks(monkeypatch)

    records = []
    photo_calls = []
    parts_calls = []

    async def fake_record_publication(*args, **kwargs):
        records.append({"args": args, "kwargs": kwargs})

    def fake_render_headline_image_html(*_args, **_kwargs):
        return b"unused"

    async def fake_send_photo(channel_id, image_bytes):
        photo_calls.append((channel_id, image_bytes))
        return 901

    async def fake_send_message_parts(channel_id, parts, reply_to_message_id=None):
        parts_calls.append((channel_id, tuple(parts), reply_to_message_id))
        return [902]

    monkeypatch.setattr(publishing, "_record_publication", fake_record_publication)
    monkeypatch.setattr(publishing, "render_headline_image_html", fake_render_headline_image_html)
    monkeypatch.setattr(publishing, "send_photo", fake_send_photo)
    monkeypatch.setattr(publishing, "send_message_parts", fake_send_message_parts)

    session = _FakeSession(existing_ok_publication=True)
    result = asyncio.run(publishing.publish_fixture(session, 1388515, dry_run=False, force=False))

    assert result["results"][0]["status"] == "skipped"
    assert result["results"][0]["reason"] == "already_published"
    assert session.committed is True
    assert len(photo_calls) == 0
    assert len(parts_calls) == 0

    # _record_publication signature: (..., status, *, payload=...)
    assert records[-1]["args"][5] == "skipped"
    assert records[-1]["kwargs"]["payload"]["reason"] == "already_published"


def test_build_post_preview_includes_image_and_messages(monkeypatch):
    _configure_settings(monkeypatch, publish_headline_image=True)
    _install_common_mocks(monkeypatch)

    def fake_render_headline_image_html(*_args, **_kwargs):
        return b"fake-png"

    monkeypatch.setattr(publishing, "render_headline_image_html", fake_render_headline_image_html)

    session = _FakeSession(existing_ok_publication=False, lock_available=True)
    result = asyncio.run(publishing.build_post_preview(session, 1388515, image_theme="pro", lang="ru"))

    assert result["fixture_id"] == 1388515
    assert result["lang"] == "ru"
    assert result["image_theme"] == "pro"
    assert result["posts"]
    post = result["posts"][0]
    assert post["uses_image"] is True
    assert isinstance(post["image_data_url"], str)
    assert post["image_data_url"].startswith("data:image/png;base64,")
    assert post["messages"][0]["type"] == "image"
    assert any(msg["type"] == "text" for msg in post["messages"])
