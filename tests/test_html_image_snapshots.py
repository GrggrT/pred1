from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from app.services.html_image import render_headline_image_html
from html_snapshot_cases import CASES


MANIFEST_PATH = Path(__file__).resolve().parent / "snapshots" / "html_cards" / "manifest.json"
SNAPSHOT_DIR = MANIFEST_PATH.parent


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_manifest() -> dict[str, dict]:
    if not MANIFEST_PATH.exists():
        pytest.fail(
            f"Snapshot manifest not found: {MANIFEST_PATH}. "
            "Generate baselines with: python scripts/generate_html_snapshots.py"
        )
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    case_map = {}
    for row in manifest.get("cases") or []:
        case_id = str(row.get("id") or "").strip()
        if case_id:
            case_map[case_id] = row
    return case_map


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_html_image_snapshot(case):
    manifest = _load_manifest()
    case_id = case["id"]
    assert case_id in manifest, f"Case '{case_id}' is missing in manifest"

    row = manifest[case_id]
    baseline_file = SNAPSHOT_DIR / str(row["file"])
    assert baseline_file.exists(), f"Baseline PNG not found: {baseline_file}"

    baseline_png = baseline_file.read_bytes()
    baseline_sha = _sha256(baseline_png)
    assert baseline_sha == row["sha256"], f"Manifest hash mismatch for baseline '{case_id}'"

    actual_png = render_headline_image_html(case["text"], **(case.get("kwargs") or {}))
    actual_sha = _sha256(actual_png)

    if actual_sha != baseline_sha:
        actual_dir = SNAPSHOT_DIR / "_actual"
        actual_dir.mkdir(parents=True, exist_ok=True)
        actual_file = actual_dir / f"{case_id}.png"
        actual_file.write_bytes(actual_png)
        pytest.fail(
            f"Snapshot mismatch for '{case_id}'. "
            f"expected={baseline_sha} actual={actual_sha}. "
            f"Wrote actual image to: {actual_file}"
        )
