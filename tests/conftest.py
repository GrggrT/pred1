import os
import socket
import sys
from pathlib import Path
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("SCHEDULER_ENABLED", "false")
os.environ.setdefault("SNAPSHOT_AUTOFILL_ENABLED", "false")
os.environ.setdefault("ADMIN_TOKEN", "test")


def _db_is_reachable(database_url: str) -> bool:
    try:
        u = urlparse(database_url)
        host = u.hostname
        port = int(u.port or 5432)
        if not host:
            return False
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except Exception:
        return False


@pytest.fixture()
def api_client():
    from app.core.config import settings

    if not (settings.admin_token or "").strip():
        pytest.skip("ADMIN_TOKEN is not configured for API tests")

    if not _db_is_reachable(settings.database_url):
        pytest.skip("DB is not reachable for API tests")

    from app.main import app

    client = TestClient(app)
    client.headers.update({"X-Admin-Token": settings.admin_token})
    try:
        yield client
    finally:
        client.close()
