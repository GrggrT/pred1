"""Generate Card Gen v2 snapshot baselines.

Usage::

    # HTML snapshots only (no Playwright needed)
    python scripts/generate_card_gen_snapshots.py

    # HTML + image snapshots (needs Playwright)
    python scripts/generate_card_gen_snapshots.py --images
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TESTS_DIR = ROOT / "tests"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from app.services.card_gen.models import ResultCardData
from app.services.card_gen.renderer import (
    _build_context,
    _build_result_context,
    _jinja_env,
)
from card_gen_snapshot_cases import CASES


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _render_html(case: dict) -> str:
    card_data = case["card_data"]
    if isinstance(card_data, ResultCardData):
        ctx = _build_result_context(card_data)
        template = _jinja_env.get_template("cards/result.html.j2")
    else:
        ctx = _build_context(card_data)
        template = _jinja_env.get_template("cards/prediction.html.j2")
    return template.render(**ctx)


def generate_html(out_dir: Path) -> None:
    """Generate HTML snapshot baselines."""
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_cases = []

    for case in CASES:
        case_id = case["id"]
        html = _render_html(case)
        html_bytes = html.encode("utf-8")
        file_path = out_dir / f"{case_id}.html"
        file_path.write_bytes(html_bytes)
        manifest_cases.append({
            "id": case_id,
            "file": file_path.name,
            "sha256": _sha256(html_bytes),
            "bytes": len(html_bytes),
        })
        print(f"  html: {case_id} -> {file_path.name} ({len(html_bytes)} bytes)")

    manifest = {"version": 1, "cases": manifest_cases}
    manifest_path = out_dir / "html_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"  manifest: {manifest_path}")


async def generate_images(out_dir: Path) -> None:
    """Generate image snapshot baselines (requires Playwright)."""
    from app.services.card_gen.renderer import render_card

    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_cases = []

    for case in CASES:
        case_id = case["id"]
        card_data = case["card_data"]
        jpeg_bytes = await render_card(card_data)
        file_path = out_dir / f"{case_id}.jpg"
        file_path.write_bytes(jpeg_bytes)
        manifest_cases.append({
            "id": case_id,
            "file": file_path.name,
            "sha256": _sha256(jpeg_bytes),
            "bytes": len(jpeg_bytes),
        })
        print(f"  image: {case_id} -> {file_path.name} ({len(jpeg_bytes)} bytes)")

    manifest = {"version": 1, "cases": manifest_cases}
    manifest_path = out_dir / "image_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"  manifest: {manifest_path}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate Card Gen v2 snapshot baselines",
    )
    parser.add_argument(
        "--out-dir",
        default=str(ROOT / "tests" / "snapshots" / "card_gen"),
        help="Output directory for snapshots",
    )
    parser.add_argument(
        "--images",
        action="store_true",
        help="Also generate image snapshots (requires Playwright)",
    )
    args = parser.parse_args()
    out_dir = Path(args.out_dir)

    print("Generating HTML snapshots...")
    generate_html(out_dir)

    if args.images:
        print("\nGenerating image snapshots...")
        asyncio.run(generate_images(out_dir))
    else:
        print("\nSkipping image snapshots (use --images to generate)")

    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
