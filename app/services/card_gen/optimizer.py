"""Card Gen v2 — Telegram output optimizer.

PNG → JPEG conversion with resize and quality tuning.
Target: < 200 KB output for Telegram ``sendPhoto``.

Usage::

    from app.services.card_gen.optimizer import optimize_for_telegram

    jpeg_bytes = optimize_for_telegram(png_bytes)
"""

from __future__ import annotations

import io
import logging

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_MAX_LONG_SIDE = 1280       # max pixel dimension after resize
_BG_COLOR = (4, 9, 24)     # --bg0 (#040918) for RGBA→RGB composite
_JPEG_QUALITY = 82          # Pillow JPEG quality
_TARGET_SIZE = 200_000      # 200 KB target
_MIN_QUALITY = 60           # don't go below this quality


def optimize_for_telegram(png_bytes: bytes) -> bytes:
    """Convert Playwright PNG screenshot to an optimised JPEG for Telegram.

    Steps:
    1. Open PNG with Pillow
    2. Resize if longest side > 1280px (LANCZOS)
    3. Composite RGBA → RGB on dark background
    4. Save as JPEG (quality=82)
    5. If still > 200 KB, reduce quality progressively

    Returns raw JPEG bytes.  Falls through to returning PNG unchanged
    if Pillow is not available.
    """
    try:
        from PIL import Image
    except ImportError:
        log.warning("Pillow not installed — returning raw PNG")
        return png_bytes

    try:
        img = Image.open(io.BytesIO(png_bytes))

        # Step 1: Resize if needed
        w, h = img.size
        long_side = max(w, h)
        if long_side > _MAX_LONG_SIDE:
            ratio = _MAX_LONG_SIDE / long_side
            new_w = max(2, round(w * ratio))
            new_h = max(2, round(h * ratio))
            img = img.resize((new_w, new_h), Image.LANCZOS)

        # Step 2: RGBA → RGB composite on dark background
        if img.mode == "RGBA":
            bg = Image.new("RGB", img.size, _BG_COLOR)
            bg.paste(img, mask=img.split()[3])  # alpha channel as mask
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")

        # Step 3: Save as JPEG with target quality
        quality = _JPEG_QUALITY
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)

        # Step 4: Reduce quality if over target size
        while buf.tell() > _TARGET_SIZE and quality > _MIN_QUALITY:
            quality -= 5
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality, optimize=True)

        result = buf.getvalue()
        log.debug(
            "optimized: %dx%d, quality=%d, size=%d bytes",
            img.size[0], img.size[1], quality, len(result),
        )
        return result

    except Exception:
        log.warning("JPEG optimization failed, returning raw PNG", exc_info=True)
        return png_bytes
