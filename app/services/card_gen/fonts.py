"""Card Gen v2 — Font sizing and CSS embedding.

Adaptive font-size calculations ported exactly from ``html_image.py``,
plus base64-embedded ``@font-face`` CSS generation.

Usage::

    from app.services.card_gen.fonts import (
        compute_team_font_size,
        compute_odds_font_size,
        compute_signal_font_size,
        fit_font_size,
        get_fonts_css,
    )

    size = compute_team_font_size("Arsenal", "Borussia Monchengladbach")  # 40
    css = get_fonts_css()  # "@font-face { ... }"
"""

from __future__ import annotations

import base64
import re
from functools import lru_cache
from pathlib import Path


# ---------------------------------------------------------------------------
# Text normalisation helpers (minimal — same as html_image.py)
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")
_EMOJI_RE = re.compile(r"[\U0001F1E6-\U0001F1FF\U0001F300-\U0001FAFF\u2600-\u27BF]")
_VS = "\ufe0f"
_ZWJ = "\u200d"


def _norm(text: str | None) -> str:
    """Lightweight normalisation: strip tags, emojis, trim."""
    if not text:
        return ""
    t = _TAG_RE.sub("", text)
    t = "".join(ch for ch in t if ch not in (_VS, _ZWJ))
    t = _EMOJI_RE.sub("", t)
    return t.strip()


# ---------------------------------------------------------------------------
# Per-character width estimation — ported from _text_width_units
# ---------------------------------------------------------------------------

_WIDE = frozenset("MW@#%&")
_NARROW = frozenset("ilIjtfr")


def text_width_units(text: str | None) -> float:
    """Estimate proportional text width (unit-less, for relative comparison).

    Ported exactly from html_image.py ``_text_width_units``.
    """
    src = _norm(text)
    if not src:
        return 1.0
    units = 0.0
    for ch in src:
        if ch.isspace():
            units += 0.32
        elif ch in _WIDE:
            units += 1.04
        elif ch in _NARROW:
            units += 0.45
        elif ch.isupper():
            units += 0.84
        elif ch.isdigit():
            units += 0.72
        else:
            units += 0.68
    return max(units, 1.0)


# ---------------------------------------------------------------------------
# Adaptive font size — ported from _*_font_size_px functions
# ---------------------------------------------------------------------------

def compute_team_font_size(home_name: str, away_name: str) -> int:
    """Compute shared font size for both team names (by the longest name).

    Ported from ``_team_font_size_px`` — both sides get the same size
    for visual symmetry.
    """
    longest = max(len(_norm(home_name)), len(_norm(away_name)))
    if longest <= 13:
        return 56
    if longest <= 17:
        return 50
    if longest <= 21:
        return 44
    if longest <= 25:
        return 40
    return 36


def compute_odds_font_size(odds_str: str) -> int:
    """Compute font size for the odds value in the central sphere.

    Ported from ``_odds_font_size_px``.
    """
    compact = re.sub(r"[^0-9.]", "", odds_str or "")
    length = len(compact)
    if length <= 4:
        return 64
    if length == 5:
        return 52
    if length == 6:
        return 44
    return 38


def compute_signal_font_size(title: str | None) -> int:
    """Compute font size for the signal block title.

    Ported from ``_signal_title_font_size_px``.
    """
    length = len(_norm(title))
    if length <= 12:
        return 35
    if length <= 16:
        return 32
    if length <= 20:
        return 29
    if length <= 24:
        return 26
    return 24


def fit_font_size(
    text: str | None,
    available_px: int,
    *,
    min_px: int,
    max_px: int,
) -> int:
    """Fit text into *available_px* by choosing an appropriate font size.

    Ported from ``_fit_font_size_px``.
    """
    units = text_width_units(text)
    est = int(available_px / units)
    return max(min_px, min(max_px, est))


# ---------------------------------------------------------------------------
# Title colour mapping — ported from _title_color
# ---------------------------------------------------------------------------

_TITLE_COLOR_MAP: list[tuple[tuple[str, ...], str]] = [
    (("STRONG", "СИЛЬНЫЙ"), "#ffd66b"),
    (("TOP", "ТОП"), "#59e2a5"),
    (("HIGH-CONFIDENCE", "HIGH CONFIDENCE"), "#ffbf71"),
    (("STANDARD", "СТАНДАРТ"), "#9fb7ff"),
    (("HOT", "ГОРЯЧ"), "#ff9a4a"),
]
_TITLE_COLOR_FALLBACK = "#ff9a4a"


def title_color(title: str | None) -> str:
    """Map prediction title keywords to a hex colour.

    Ported from ``_title_color``.
    """
    text = (title or "").strip().upper()
    for keywords, color in _TITLE_COLOR_MAP:
        for kw in keywords:
            if kw in text:
                return color
    return _TITLE_COLOR_FALLBACK


# ---------------------------------------------------------------------------
# Odds display extraction — ported from _odds_display
# ---------------------------------------------------------------------------

def odds_display(value: str | None) -> str:
    """Extract a clean numeric odds string from raw text.

    Ported from ``_odds_display``.
    """
    raw = _norm(value)
    if not raw:
        return "2.00"
    m = re.search(r"\d+(?:[.,]\d+)?", raw)
    if m:
        return m.group(0).replace(",", ".")
    cleaned = raw.replace("@", "").strip()
    return cleaned or "2.00"


# ---------------------------------------------------------------------------
# Font CSS embedding — ported from _embedded_font_css
# ---------------------------------------------------------------------------

# Default font paths (same as legacy)
_LEGACY_FONT_DIR = Path(__file__).resolve().parent.parent.parent / "assets" / "fonts"
_V2_FONT_DIR = Path(__file__).resolve().parent / "static" / "fonts"

_FONT_SPECS: list[tuple[str, str, int]] = [
    ("PredSans", "NotoSans-Regular.ttf", 400),
    ("PredSans", "NotoSans-Bold.ttf", 700),
]


@lru_cache(maxsize=1)
def get_fonts_css() -> str:
    """Return CSS ``@font-face`` declarations with base64-embedded fonts.

    Looks for font files in ``static/fonts/`` first, then falls back
    to the legacy ``app/assets/fonts/`` directory.

    Ported from ``_embedded_font_css`` — same validation and encoding.
    """
    css_chunks: list[str] = []
    for family, filename, weight in _FONT_SPECS:
        # Try v2 dir first, then legacy
        path = _V2_FONT_DIR / filename
        if not path.exists():
            path = _LEGACY_FONT_DIR / filename
        if not path.exists():
            continue

        data = path.read_bytes()
        # Validate not accidentally HTML
        if b"<html" in data[:512].lower():
            continue

        encoded = base64.b64encode(data).decode("ascii")
        css_chunks.append(
            "@font-face{"
            f"font-family:'{family}';"
            f"font-style:normal;font-weight:{weight};font-display:swap;"
            f"src:url(data:font/ttf;base64,{encoded}) format('truetype');"
            "}"
        )
    return "\n".join(css_chunks)


# ---------------------------------------------------------------------------
# Form helpers
# ---------------------------------------------------------------------------

def normalize_form(value: str | None) -> str:
    """Extract W/D/L characters from a form string, max 8 chars.

    Ported from ``_normalize_form``.
    """
    text = (value or "").upper()
    letters = "".join(ch for ch in text if ch in {"W", "D", "L"})
    return letters[:8]
