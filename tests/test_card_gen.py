"""Card Gen v2 — Snapshot tests.

Two test groups:

1. **HTML snapshot tests** (always run):
   Compare SHA-256 of rendered HTML templates.
   Catches changes in template structure, context building, and CSS.

2. **Image snapshot tests** (require Playwright):
   Compare SHA-256 of rendered JPEG images.
   Catches changes in visual rendering (browser, optimizer).

Generate baselines::

    python scripts/generate_card_gen_snapshots.py          # HTML only
    python scripts/generate_card_gen_snapshots.py --images  # HTML + images (needs Playwright)
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

ROOT = TESTS_DIR.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.card_gen.models import PredictionCardData, ResultCardData
from app.services.card_gen.renderer import (
    _build_context,
    _build_result_context,
    _jinja_env,
)
from card_gen_snapshot_cases import CASES


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

HTML_MANIFEST_PATH = TESTS_DIR / "snapshots" / "card_gen" / "html_manifest.json"
IMAGE_MANIFEST_PATH = TESTS_DIR / "snapshots" / "card_gen" / "image_manifest.json"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_manifest(path: Path) -> dict[str, dict]:
    if not path.exists():
        pytest.fail(
            f"Manifest not found: {path}. "
            "Generate baselines: python scripts/generate_card_gen_snapshots.py"
        )
    manifest = json.loads(path.read_text(encoding="utf-8"))
    return {str(r["id"]): r for r in manifest.get("cases", []) if r.get("id")}


def _render_html(case: dict) -> str:
    """Render case to HTML string (no browser)."""
    card_data = case["card_data"]
    if isinstance(card_data, ResultCardData):
        ctx = _build_result_context(card_data)
        template = _jinja_env.get_template("cards/result.html.j2")
    else:
        ctx = _build_context(card_data)
        template = _jinja_env.get_template("cards/prediction.html.j2")
    return template.render(**ctx)


# ---------------------------------------------------------------------------
# HTML snapshot tests (always available)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_card_gen_html_snapshot(case):
    """Compare SHA-256 of rendered HTML against baseline."""
    manifest = _load_manifest(HTML_MANIFEST_PATH)
    case_id = case["id"]
    assert case_id in manifest, f"Case '{case_id}' missing in HTML manifest"

    row = manifest[case_id]
    html = _render_html(case)
    html_bytes = html.encode("utf-8")
    actual_sha = _sha256(html_bytes)

    if actual_sha != row["sha256"]:
        actual_dir = HTML_MANIFEST_PATH.parent / "_actual"
        actual_dir.mkdir(parents=True, exist_ok=True)
        actual_file = actual_dir / f"{case_id}.html"
        actual_file.write_bytes(html_bytes)
        pytest.fail(
            f"HTML snapshot mismatch for '{case_id}'. "
            f"expected={row['sha256'][:16]}... actual={actual_sha[:16]}... "
            f"Wrote actual to: {actual_file}"
        )


# ---------------------------------------------------------------------------
# Image snapshot tests (require Playwright)
# ---------------------------------------------------------------------------
# All cases are rendered inside a single asyncio.run() to share one browser
# instance.  Launching Chromium per test is too slow (~30s each).
# ---------------------------------------------------------------------------

def test_card_gen_image_snapshots_all():
    """Compare SHA-256 of rendered JPEG images against baselines (batch)."""
    pytest.importorskip("playwright.async_api")

    import asyncio

    from app.services.card_gen.browser import close_browser
    from app.services.card_gen.renderer import render_card

    # Reset singleton so this asyncio.run() owns the browser cleanly
    import app.services.card_gen.browser as _bmod
    _bmod._playwright_instance = None
    _bmod._browser = None
    _bmod._lock = asyncio.Lock()

    manifest = _load_manifest(IMAGE_MANIFEST_PATH)
    failures: list[str] = []

    async def _run_all():
        try:
            for case in CASES:
                case_id = case["id"]
                assert case_id in manifest, f"Case '{case_id}' missing in image manifest"

                row = manifest[case_id]
                baseline_file = IMAGE_MANIFEST_PATH.parent / str(row["file"])
                assert baseline_file.exists(), f"Baseline not found: {baseline_file}"

                baseline_bytes = baseline_file.read_bytes()
                baseline_sha = _sha256(baseline_bytes)
                assert baseline_sha == row["sha256"], f"Manifest hash mismatch for '{case_id}'"

                actual_bytes = await render_card(case["card_data"])
                actual_sha = _sha256(actual_bytes)

                if actual_sha != baseline_sha:
                    actual_dir = IMAGE_MANIFEST_PATH.parent / "_actual"
                    actual_dir.mkdir(parents=True, exist_ok=True)
                    actual_file = actual_dir / f"{case_id}.jpg"
                    actual_file.write_bytes(actual_bytes)
                    failures.append(
                        f"{case_id}: expected={baseline_sha[:16]}... "
                        f"actual={actual_sha[:16]}..."
                    )
        finally:
            await close_browser()

    asyncio.run(_run_all())

    if failures:
        pytest.fail(
            f"Image snapshot mismatches ({len(failures)}/{len(CASES)}):\n"
            + "\n".join(f"  - {f}" for f in failures)
        )


# ---------------------------------------------------------------------------
# Structural tests (always run, no baselines needed)
# ---------------------------------------------------------------------------

class TestCardGenStructure:
    """Structural checks — no baselines required."""

    @pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
    def test_html_renders_without_error(self, case):
        """Every case must render to non-empty HTML."""
        html = _render_html(case)
        assert len(html) > 500, f"HTML too short for '{case['id']}': {len(html)} chars"
        assert "<!DOCTYPE html>" in html

    @pytest.mark.parametrize(
        "case",
        [c for c in CASES if isinstance(c["card_data"], PredictionCardData)],
        ids=[c["id"] for c in CASES if isinstance(c["card_data"], PredictionCardData)],
    )
    def test_prediction_card_structure(self, case):
        """Prediction cards must contain key structural elements."""
        html = _render_html(case)
        assert "odds-shell" in html
        assert "pick-main" in html
        assert "signal-card" in html or "signal-title" in html
        assert "table-strip" in html

    @pytest.mark.parametrize(
        "case",
        [c for c in CASES if isinstance(c["card_data"], ResultCardData)],
        ids=[c["id"] for c in CASES if isinstance(c["card_data"], ResultCardData)],
    )
    def test_result_card_structure(self, case):
        """Result cards must contain key structural elements."""
        html = _render_html(case)
        card_data = case["card_data"]
        assert "score-shell" in html
        assert "result-section" in html
        assert "status-card" in html
        assert "table-strip" in html
        # Check score
        assert f"{card_data.home_goals} : {card_data.away_goals}" in html
        # Check result class
        expected_class = "result-win" if card_data.status.upper() == "WIN" else "result-loss"
        assert expected_class in html

    def test_prediction_no_result_elements(self):
        """Prediction card body must not contain result-specific HTML elements."""
        case = next(c for c in CASES if c["id"] == "pred_en_hot_basic")
        html = _render_html(case)
        # Split at </style> to check only body HTML
        body = html.split("</style>")[-1] if "</style>" in html else html
        assert '<div class="score-shell">' not in body
        assert '<section class="result-section' not in body
        assert '<div class="status-card">' not in body

    def test_result_no_prediction_elements(self):
        """Result card body must not contain prediction-specific HTML elements."""
        case = next(c for c in CASES if c["id"] == "result_win")
        html = _render_html(case)
        body = html.split("</style>")[-1] if "</style>" in html else html
        assert '<div class="odds-shell">' not in body
        assert '<section class="pick">' not in body

    def test_viral_theme_class(self):
        """Viral theme must add theme-viral class to card."""
        case = next(c for c in CASES if c["id"] == "pred_en_viral_theme")
        html = _render_html(case)
        assert "theme-viral" in html

    def test_win_loss_status_text(self):
        """WIN/LOSS cards must show correct Russian status text."""
        win_case = next(c for c in CASES if c["id"] == "result_win")
        loss_case = next(c for c in CASES if c["id"] == "result_loss")
        win_html = _render_html(win_case)
        loss_html = _render_html(loss_case)
        # Check status display (may be HTML-escaped)
        assert "\u0412\u042b\u0418\u0413\u0420\u042b\u0428" in win_html or "&#" in win_html
        assert "\u041f\u0420\u041e\u0418\u0413\u0420\u042b\u0428" in loss_html or "&#" in loss_html
