"""Card Gen v2 — Logo and image asset handling.

Converts raw image bytes to base64 data URIs for embedding in HTML,
generates SVG placeholder logos, and detects image MIME types.

All functions are ported 1:1 from ``html_image.py`` internal helpers.

Usage::

    from app.services.card_gen.assets import prepare_logo, placeholder_svg

    uri = prepare_logo(logo_bytes)           # "data:image/png;base64,..."
    svg_uri = placeholder_svg("A", "#4e86ff")  # SVG circle with initial
"""

from __future__ import annotations

import base64
import html as html_mod
import io
import re
from functools import lru_cache


# ---------------------------------------------------------------------------
# MIME detection — ported from _bytes_to_data_uri
# ---------------------------------------------------------------------------

def _detect_mime(data: bytes) -> str:
    """Detect image MIME type from magic bytes."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    # Default to PNG (covers \x89PNG and unknowns)
    return "image/png"


# ---------------------------------------------------------------------------
# Data URI conversion
# ---------------------------------------------------------------------------

def bytes_to_data_uri(data: bytes | None, fallback: str = "") -> str:
    """Convert raw image bytes to a ``data:`` URI.

    If *data* is ``None`` or empty, returns *fallback* (which should be
    a data URI string for a placeholder SVG or empty string).
    """
    if not data:
        return fallback
    mime = _detect_mime(data)
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{encoded}"


# ---------------------------------------------------------------------------
# Logo preparation
# ---------------------------------------------------------------------------

@lru_cache(maxsize=512)
def _prepare_logo_cached(data_hash: int, data: bytes, max_side: int) -> str:
    """Resize logo to *max_side* and return base64 data URI (cached by hash)."""
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(data))
        img = img.convert("RGBA")
        w, h = img.size
        ratio = min(max_side / w, max_side / h, 1.0)
        if ratio < 1.0:
            new_w = max(2, round(w * ratio))
            new_h = max(2, round(h * ratio))
            img = img.resize((new_w, new_h), Image.LANCZOS)

        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        encoded = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/png;base64,{encoded}"
    except Exception:
        # If Pillow fails, fall through to raw encoding
        return bytes_to_data_uri(data)


def prepare_logo(raw_bytes: bytes | None, *, max_side: int = 128) -> str | None:
    """Resize a logo to *max_side*×*max_side* and return a base64 PNG data URI.

    Returns ``None`` if *raw_bytes* is ``None`` or empty.
    Uses an LRU cache keyed by the hash of the input bytes.
    """
    if not raw_bytes:
        return None
    return _prepare_logo_cached(hash(raw_bytes), raw_bytes, max_side)


def prepare_league_logo(raw_bytes: bytes | None) -> str | None:
    """Same as :func:`prepare_logo` but sized for league badges (64×64)."""
    return prepare_logo(raw_bytes, max_side=64)


# ---------------------------------------------------------------------------
# SVG fallback placeholders — ported from _fallback_logo_svg
# ---------------------------------------------------------------------------

def _initial_letter(text: str, fallback: str = "?") -> str:
    """Extract the first Latin/Cyrillic letter or digit."""
    m = re.search(r"[A-Za-zА-Яа-я0-9]", text or "")
    return (m.group(0).upper() if m else fallback[:1].upper()) or "?"


def placeholder_svg(
    initials: str,
    bg_color: str = "#4e86ff",
    *,
    size: int = 96,
) -> str:
    """Generate a circle-with-initial SVG and return as a ``data:`` URI.

    Ported from ``_fallback_logo_svg`` in html_image.py.
    """
    letter = html_mod.escape(initials[:1].upper() or "?")
    r = size // 2 - 4  # circle radius with stroke clearance
    svg = (
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{size}' height='{size}' viewBox='0 0 {size} {size}'>"
        f"<defs><linearGradient id='g' x1='0' y1='0' x2='1' y2='1'>"
        f"<stop offset='0' stop-color='{bg_color}'/><stop offset='1' stop-color='#1d2948'/></linearGradient></defs>"
        f"<circle cx='{size // 2}' cy='{size // 2}' r='{r}' fill='url(#g)'/>"
        f"<circle cx='{size // 2}' cy='{size // 2}' r='{r - 1}' fill='none' stroke='#9fb3d8' stroke-width='2'/>"
        f"<text x='50%' y='54%' text-anchor='middle' dominant-baseline='middle' "
        f"font-family='Arial' font-size='{int(size * 0.4)}' fill='#f8fafc'>{letter}</text>"
        f"</svg>"
    )
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def make_fallback_logo(
    team_name: str,
    color: tuple[int, int, int] = (78, 134, 255),
) -> str:
    """Create a placeholder SVG data URI from team name and colour."""
    letter = _initial_letter(team_name, "?")
    hex_color = f"#{color[0]:02x}{color[1]:02x}{color[2]:02x}"
    return placeholder_svg(letter, hex_color)


def rgb_to_hex(color: tuple[int, int, int]) -> str:
    """Convert an RGB tuple to a ``#RRGGBB`` hex string."""
    return f"#{color[0]:02x}{color[1]:02x}{color[2]:02x}"
