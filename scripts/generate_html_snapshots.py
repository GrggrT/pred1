from __future__ import annotations

import argparse
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

from app.services.html_image import render_headline_image_html
from html_snapshot_cases import CASES


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate HTML card snapshot baselines")
    parser.add_argument("--out-dir", default=str(ROOT / "tests" / "snapshots" / "html_cards"))
    parser.add_argument("--manifest", default=str(ROOT / "tests" / "snapshots" / "html_cards" / "manifest.json"))
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    manifest_path = Path(args.manifest)
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest_cases = []
    for case in CASES:
        case_id = case["id"]
        text = case["text"]
        kwargs = dict(case.get("kwargs") or {})
        png = render_headline_image_html(text, **kwargs)
        file_path = out_dir / f"{case_id}.png"
        file_path.write_bytes(png)
        manifest_cases.append(
            {
                "id": case_id,
                "file": file_path.name,
                "sha256": sha256_bytes(png),
                "bytes": len(png),
            }
        )
        print(f"generated {case_id} -> {file_path.name} ({len(png)} bytes)")

    manifest = {
        "version": 1,
        "cases": manifest_cases,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"manifest written: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
