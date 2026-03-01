"""Card Gen v2 — Python-side team colour extraction.

Replaces the client-side JavaScript canvas palette algorithm from
``html_image.py``.  Uses Pillow for pixel sampling (no extra deps)
with identical HSL normalisation parameters.

Usage::

    from app.services.card_gen.palette import extract_team_color

    rgb = extract_team_color(team_id=42, logo_bytes=b"\\x89PNG...")
    # (220, 53, 69)
"""

from __future__ import annotations

import colorsys
import io
import json
import logging
import math
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants — ported 1:1 from html_image.py JS palette code
# ---------------------------------------------------------------------------
_MAX_SAMPLE_SIDE = 42       # canvas max side in JS
_ALPHA_THRESHOLD = 40       # skip pixels with alpha < 40
_SAT_THRESHOLD = 20         # skip pixels with (max-min) < 20
_WEIGHT_OFFSET = 10         # weight = sat + 10

# HSL clamp ranges (JS: Math.min/max)
_SAT_MIN = 0.36
_SAT_MAX = 0.82
_LIGHT_MIN = 0.42
_LIGHT_MAX = 0.66

# Luminance correction bounds
_LUM_MIN = 95
_LUM_MAX = 210

# Fallback colours (same as JS)
FALLBACK_HOME = (78, 134, 255)
FALLBACK_AWAY = (255, 94, 128)

# Cache file
_CACHE_DIR = Path(__file__).resolve().parent / "cache"
_CACHE_FILE = _CACHE_DIR / "team_palettes.json"

# In-memory cache (populated from JSON on first call)
_mem_cache: dict[int, tuple[int, int, int]] | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rgb_to_hsl(r: int, g: int, b: int) -> tuple[float, float, float]:
    """Convert RGB (0-255) to HSL (h: 0-360, s: 0-1, l: 0-1)."""
    rn, gn, bn = r / 255.0, g / 255.0, b / 255.0
    mx = max(rn, gn, bn)
    mn = min(rn, gn, bn)
    d = mx - mn
    l = (mx + mn) / 2.0
    if d == 0:
        return (0.0, 0.0, l)
    s = d / (1.0 - abs(2.0 * l - 1.0)) if (1.0 - abs(2.0 * l - 1.0)) != 0 else 0.0
    if mx == rn:
        h = ((gn - bn) / d) % 6.0
    elif mx == gn:
        h = (bn - rn) / d + 2.0
    else:
        h = (rn - gn) / d + 4.0
    h *= 60.0
    if h < 0:
        h += 360.0
    return (h, s, l)


def _hsl_to_rgb(h: float, s: float, l: float) -> tuple[int, int, int]:
    """Convert HSL (h: 0-360, s: 0-1, l: 0-1) to RGB (0-255)."""
    c = (1.0 - abs(2.0 * l - 1.0)) * s
    x = c * (1.0 - abs((h / 60.0) % 2.0 - 1.0))
    m = l - c / 2.0
    if h < 60:
        rp, gp, bp = c, x, 0.0
    elif h < 120:
        rp, gp, bp = x, c, 0.0
    elif h < 180:
        rp, gp, bp = 0.0, c, x
    elif h < 240:
        rp, gp, bp = 0.0, x, c
    elif h < 300:
        rp, gp, bp = x, 0.0, c
    else:
        rp, gp, bp = c, 0.0, x
    return (
        round((rp + m) * 255),
        round((gp + m) * 255),
        round((bp + m) * 255),
    )


def _luminance(r: float, g: float, b: float) -> float:
    """Perceived luminance (same formula as JS)."""
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _normalize_accent(
    r: float, g: float, b: float,
    fallback: tuple[int, int, int],
) -> tuple[int, int, int]:
    """HSL normalisation + luminance correction — exact port of JS ``normalizeAccent``."""
    try:
        if not (math.isfinite(r) and math.isfinite(g) and math.isfinite(b)):
            return fallback

        h, s0, l0 = _rgb_to_hsl(int(r), int(g), int(b))
        s = min(_SAT_MAX, max(_SAT_MIN, s0))
        l = min(_LIGHT_MAX, max(_LIGHT_MIN, l0))
        ri, gi, bi = _hsl_to_rgb(h, s, l)

        lum = _luminance(ri, gi, bi)
        if lum < _LUM_MIN:
            gain = _LUM_MIN / max(1, lum)
            ri = min(235, ri * gain)
            gi = min(235, gi * gain)
            bi = min(235, bi * gain)
        elif lum > _LUM_MAX:
            gain = _LUM_MAX / lum
            ri = max(30, ri * gain)
            gi = max(30, gi * gain)
            bi = max(30, bi * gain)

        return (round(ri), round(gi), round(bi))
    except Exception:
        return fallback


def _accent_from_pixels(
    pixels: list[tuple[int, int, int, int]],
    fallback: tuple[int, int, int],
) -> tuple[int, int, int]:
    """Saturation-weighted average colour — exact port of JS ``accentFromImage``.

    *pixels* is a list of (R, G, B, A) tuples.
    """
    total = 0.0
    rs = 0.0
    gs = 0.0
    bs = 0.0

    for r, g, b, a in pixels:
        if a < _ALPHA_THRESHOLD:
            continue
        sat = max(r, g, b) - min(r, g, b)
        if sat < _SAT_THRESHOLD:
            continue
        weight = sat + _WEIGHT_OFFSET
        total += weight
        rs += r * weight
        gs += g * weight
        bs += b * weight

    if total <= 0:
        return fallback

    return (rs / total, gs / total, bs / total)  # type: ignore[return-value]
    # NOTE: returns floats intentionally — _normalize_accent handles rounding


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def _load_cache() -> dict[int, tuple[int, int, int]]:
    global _mem_cache
    if _mem_cache is not None:
        return _mem_cache

    _mem_cache = {}
    if _CACHE_FILE.exists():
        try:
            raw = json.loads(_CACHE_FILE.read_text("utf-8"))
            if isinstance(raw, dict):
                for k, v in raw.items():
                    if isinstance(v, list) and len(v) == 3:
                        _mem_cache[int(k)] = (int(v[0]), int(v[1]), int(v[2]))
        except Exception:
            log.warning("palette cache load failed, starting fresh")
    return _mem_cache


def _save_cache() -> None:
    cache = _load_cache()
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        data = {str(k): list(v) for k, v in cache.items()}
        _CACHE_FILE.write_text(json.dumps(data, indent=2), "utf-8")
    except Exception:
        log.warning("palette cache save failed")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_team_color(
    team_id: int,
    logo_bytes: bytes | None,
    *,
    fallback: tuple[int, int, int] = FALLBACK_HOME,
    use_cache: bool = True,
) -> tuple[int, int, int]:
    """Extract the dominant accent colour from a team logo.

    Parameters
    ----------
    team_id:
        Numeric team identifier (used as cache key).
    logo_bytes:
        Raw image bytes (PNG/JPEG/WEBP).  ``None`` returns *fallback*.
    fallback:
        RGB tuple to use when extraction fails.
    use_cache:
        Whether to check/update the JSON cache.

    Returns
    -------
    tuple[int, int, int]
        Normalised RGB colour suitable for card accent.
    """
    # Check cache
    if use_cache:
        cache = _load_cache()
        if team_id in cache:
            return cache[team_id]

    if not logo_bytes:
        return fallback

    try:
        from PIL import Image

        img = Image.open(io.BytesIO(logo_bytes))
        img = img.convert("RGBA")

        # Resize to max 42px side (same as JS canvas)
        w, h = img.size
        ratio = min(_MAX_SAMPLE_SIDE / w, _MAX_SAMPLE_SIDE / h, 1.0)
        if ratio < 1.0:
            new_w = max(2, round(w * ratio))
            new_h = max(2, round(h * ratio))
            img = img.resize((new_w, new_h), Image.LANCZOS)

        pixels = list(img.getdata())  # list of (R, G, B, A) tuples
        raw_rgb = _accent_from_pixels(pixels, fallback)
        result = _normalize_accent(raw_rgb[0], raw_rgb[1], raw_rgb[2], fallback)

        # Update cache
        if use_cache:
            cache = _load_cache()
            cache[team_id] = result
            _save_cache()

        return result

    except Exception:
        log.warning("palette extraction failed for team_id=%s", team_id, exc_info=True)
        return fallback


def clear_cache() -> None:
    """Remove all entries from the in-memory and disk palette cache."""
    global _mem_cache
    _mem_cache = {}
    try:
        if _CACHE_FILE.exists():
            _CACHE_FILE.unlink()
    except Exception:
        pass
