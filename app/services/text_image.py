from __future__ import annotations

import html
import io
import re
import unicodedata
from itertools import zip_longest
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageEnhance, ImageFont


_WIDTH = 1280
_PADDING = 90
_BG_COLOR = (11, 15, 25)
_TEXT_COLOR = (248, 250, 252)
_ACCENT_COLOR = (250, 204, 21)
_TITLE_COLOR = (251, 146, 60)
_BADGE_BG = (30, 41, 59)
_BADGE_TEXT = (226, 232, 240)
_MARKET_BADGE_BG = (250, 204, 21)
_MARKET_BADGE_TEXT = (17, 24, 39)
_SEPARATOR_COLOR = (45, 55, 72)
_LOGO_GAP = 20
_INLINE_LOGO_GAP = 10
_BG_LOGO_OPACITY = 0.08
_BG_LOGO_BRIGHTNESS = 0.4
_EMOJI_CHARS = {
    "🔥",
    "📅",
    "💰",
    "🎯",
    "⚠",
    "📊",
    "⚡",
    "🏆",
    "🏟",
    "⏰",
    "📈",
    "🎲",
    "🤖",
    "🔎",
    "✅",
    "📉",
    "⚪",
    "⛔",
}
_VARIATION_SELECTOR = "\ufe0f"
_ZERO_WIDTH_JOINER = "\u200d"


@dataclass
class _FontSet:
    title: ImageFont.FreeTypeFont
    section: ImageFont.FreeTypeFont
    main: ImageFont.FreeTypeFont
    small: ImageFont.FreeTypeFont
    emoji: ImageFont.FreeTypeFont


def _font_path(filename: str) -> Path | None:
    base = Path(__file__).resolve().parent.parent / "assets" / "fonts"
    candidate = base / filename
    return candidate if candidate.exists() else None


def _load_font(path: Path | None, size: int) -> ImageFont.FreeTypeFont:
    if path:
        try:
            return ImageFont.truetype(str(path), size=size)
        except Exception:
            pass
    return ImageFont.load_default()


def _load_fonts() -> _FontSet:
    regular = _font_path("NotoSans-Regular.ttf") or _font_path("DejaVuSans.ttf")
    bold = _font_path("NotoSans-Bold.ttf") or _font_path("DejaVuSans-Bold.ttf")
    emoji = _font_path("NotoEmoji-Regular.ttf")
    return _FontSet(
        title=_load_font(bold, 72),
        section=_load_font(bold, 54),
        main=_load_font(regular, 44),
        small=_load_font(regular, 38),
        emoji=_load_font(emoji, 44),
    )


def _text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def _line_height(font: ImageFont.FreeTypeFont, emoji_font: ImageFont.FreeTypeFont) -> int:
    bbox = font.getbbox("Hg")
    base = bbox[3] - bbox[1]
    emoji_box = emoji_font.getbbox("🙂")
    emoji_h = emoji_box[3] - emoji_box[1]
    return max(base, emoji_h)


def _center_text_y(y: int, line_height: int, font: ImageFont.FreeTypeFont) -> int:
    bbox = font.getbbox("Hg")
    line_center = y + line_height / 2
    return int(line_center - (bbox[1] + bbox[3]) / 2)


def _is_emoji(ch: str) -> bool:
    if ch in _EMOJI_CHARS:
        return True
    return unicodedata.category(ch) == "So"


def _measure_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, emoji_font: ImageFont.FreeTypeFont) -> int:
    width = 0
    for ch in text:
        if ch in (_VARIATION_SELECTOR, _ZERO_WIDTH_JOINER):
            continue
        fnt = emoji_font if _is_emoji(ch) else font
        width += _text_width(draw, ch, fnt)
    return width


def _wrap_line(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    emoji_font: ImageFont.FreeTypeFont,
    max_width: int,
) -> list[str]:
    if not text:
        return [""]
    words = text.split()
    if not words:
        return [text]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if _measure_text(draw, candidate, font, emoji_font) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _style_for_line(line: str, idx: int, fonts: _FontSet) -> tuple[ImageFont.FreeTypeFont, tuple[int, int, int]]:
    stripped = line.strip()
    if idx == 0 or "ГОРЯЧИЙ ПРОГНОЗ" in stripped:
        return fonts.title, _TITLE_COLOR
    if stripped.startswith("💰") or stripped.startswith("📊") or stripped.startswith("⚠️"):
        return fonts.section, _ACCENT_COLOR
    if stripped.startswith("🎯"):
        return fonts.small, _ACCENT_COLOR
    if stripped.startswith("📅"):
        return fonts.small, _TEXT_COLOR
    if " vs " in stripped:
        return fonts.section, _TEXT_COLOR
    return fonts.main, _TEXT_COLOR


def _draw_mixed_line(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    text: str,
    font: ImageFont.FreeTypeFont,
    color: tuple[int, int, int],
    emoji_font: ImageFont.FreeTypeFont,
) -> None:
    cursor = x
    for ch in text:
        if ch in (_VARIATION_SELECTOR, _ZERO_WIDTH_JOINER):
            continue
        fnt = emoji_font if _is_emoji(ch) else font
        draw.text((cursor, y), ch, font=fnt, fill=color)
        cursor += _text_width(draw, ch, fnt)


def _strip_emojis(text: str) -> str:
    if not text:
        return ""
    return "".join(
        ch
        for ch in text
        if ch not in (_VARIATION_SELECTOR, _ZERO_WIDTH_JOINER)
        and ch not in _EMOJI_CHARS
        and unicodedata.category(ch) != "So"
    )


def _prepare_league_background(data: bytes | None, width: int, height: int) -> Image.Image | None:
    if not data:
        return None
    try:
        img = Image.open(io.BytesIO(data)).convert("RGBA")
    except Exception:
        return None
    max_dim = max(width, height) * 1.4
    scale = max_dim / max(img.width, img.height)
    new_size = (max(1, int(img.width * scale)), max(1, int(img.height * scale)))
    img = img.resize(new_size, Image.Resampling.LANCZOS)
    img = ImageEnhance.Brightness(img).enhance(_BG_LOGO_BRIGHTNESS)
    alpha = img.split()[-1]
    alpha = alpha.point(lambda a: int(a * _BG_LOGO_OPACITY))
    img.putalpha(alpha)
    img = img.rotate(-20, expand=True, resample=Image.Resampling.BICUBIC)
    return img


def _truncate_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> str:
    if _text_width(draw, text, font) <= max_width:
        return text
    truncated = text
    while truncated and _text_width(draw, f"{truncated}...", font) > max_width:
        truncated = truncated[:-1]
    return f"{truncated}..." if truncated else ""


def _draw_badge(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    text: str,
    font: ImageFont.FreeTypeFont,
    bg: tuple[int, int, int],
    fg: tuple[int, int, int],
    *,
    pad_x: int = 12,
    pad_y: int = 6,
    radius: int = 12,
) -> tuple[int, int]:
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    width = text_w + pad_x * 2
    height = text_h + pad_y * 2
    draw.rounded_rectangle((x, y, x + width, y + height), radius=radius, fill=bg)
    draw.text((x + pad_x - bbox[0], y + pad_y - bbox[1]), text, font=font, fill=fg)
    return width, height


def _badge_size(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    *,
    pad_x: int = 12,
    pad_y: int = 6,
) -> tuple[int, int]:
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    return text_w + pad_x * 2, text_h + pad_y * 2


def _load_logo_image(data: bytes | None, size: int) -> Image.Image | None:
    if not data:
        return None
    try:
        img = Image.open(io.BytesIO(data)).convert("RGBA")
    except Exception:
        return None
    img.thumbnail((size, size), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    x = (size - img.width) // 2
    y = (size - img.height) // 2
    canvas.paste(img, (x, y), img)
    return canvas


def render_headline_image(
    text: str,
    width: int = _WIDTH,
    *,
    home_logo: bytes | None = None,
    away_logo: bytes | None = None,
    league_logo: bytes | None = None,
    league_label: str | None = None,
    market_label: str | None = None,
    bet_label: str | None = None,
) -> bytes:
    cleaned_raw = re.sub(r"<[^>]+>", "", text or "")
    cleaned_raw = html.unescape(cleaned_raw)
    cleaned = _strip_emojis(cleaned_raw)
    clean_bet_label = _strip_emojis(re.sub(r"<[^>]+>", "", bet_label or "")).strip()
    clean_league_label = _strip_emojis(re.sub(r"<[^>]+>", "", league_label or "")).strip()
    fonts = _load_fonts()
    padding = _PADDING
    max_width = width - padding * 2
    base_height = _line_height(fonts.main, fonts.emoji)
    draw = ImageDraw.Draw(Image.new("RGB", (width, 10), color=_BG_COLOR))
    logo_size = int(_line_height(fonts.section, fonts.emoji) * 1.05)
    home_logo_img = _load_logo_image(home_logo, logo_size)
    away_logo_img = _load_logo_image(away_logo, logo_size)
    has_logo = bool(home_logo_img or away_logo_img)
    layout: list[tuple[str, ImageFont.FreeTypeFont, tuple[int, int, int], int, int, bool, tuple[str, str, int] | None]] = []
    raw_lines = cleaned_raw.splitlines()
    clean_lines = cleaned.splitlines()
    for idx, (raw, line) in enumerate(zip_longest(raw_lines, clean_lines, fillvalue="")):
        if line.strip() == "":
            layout.append(("", fonts.main, _TEXT_COLOR, int(base_height * 0.8), 0, False, None))
            continue
        if clean_league_label and line.strip().lower() == clean_league_label.lower():
            continue
        font, color = _style_for_line(raw, idx, fonts)
        is_vs_line = " vs " in line.lower() and has_logo
        line_gap = 12
        if idx == 0:
            line_gap = 22
        elif is_vs_line:
            line_gap = 18
        elif raw.strip().startswith("📅"):
            line_gap = 16
        if is_vs_line and home_logo_img and away_logo_img:
            parts = re.split(r"\s+vs\s+", line, flags=re.IGNORECASE, maxsplit=1)
            if len(parts) == 2:
                home_text = parts[0].strip()
                away_text = parts[1].strip()
                if home_text and away_text:
                    if layout and layout[-1][0] != "":
                        layout.append(("", fonts.main, _TEXT_COLOR, int(base_height * 0.9), 0, False, None))
                    logo_gap = _INLINE_LOGO_GAP
                    vs_text = "vs"
                    total_width = (
                        logo_size
                        + logo_gap
                        + _measure_text(draw, home_text, font, fonts.emoji)
                        + logo_gap
                        + _measure_text(draw, vs_text, font, fonts.emoji)
                        + logo_gap
                        + _measure_text(draw, away_text, font, fonts.emoji)
                        + logo_gap
                        + logo_size
                    )
                    if total_width <= max_width:
                        layout.append(
                            (
                                f"{home_text} vs {away_text}",
                                font,
                                color,
                                line_gap,
                                0,
                                False,
                                (home_text, away_text, logo_gap),
                            )
                        )
                        continue
        line_max_width = max_width
        x_offset = 0
        if is_vs_line:
            line_max_width = max(50, max_width - 2 * (logo_size + _LOGO_GAP))
            x_offset = logo_size + _LOGO_GAP
        for w_idx, wrapped in enumerate(_wrap_line(draw, line, font, fonts.emoji, line_max_width)):
            anchor = bool(is_vs_line and w_idx == 0)
            layout.append((wrapped, font, color, line_gap, x_offset, anchor, None))

    total_height = padding * 2
    for line, font, _, gap, _, _, _ in layout:
        total_height += _line_height(font, fonts.emoji)
        total_height += gap
    total_height = max(total_height, padding * 2 + base_height)

    image = Image.new("RGB", (width, total_height), color=_BG_COLOR)
    draw = ImageDraw.Draw(image)
    bg_logo = _prepare_league_background(league_logo, width, total_height)
    if bg_logo is not None:
        x = int((width - bg_logo.width) / 2)
        y = int((total_height - bg_logo.height) / 2)
        image.paste(bg_logo, (x, y), bg_logo)
    y = padding
    logo_y = None
    logo_line_height = None
    separator_drawn = False
    bet_line_y = None
    bet_line_height = None
    has_bet_line = bool(clean_bet_label)
    inline_vs_used = False
    for line, font, color, gap, x_offset, anchor, vs_inline in layout:
        line_height = _line_height(font, fonts.emoji)
        is_blank = not line
        is_bet = bool(clean_bet_label) and line.strip().lower() == clean_bet_label.lower()
        if is_blank and not separator_drawn and has_bet_line:
            sep_y = int(y + line_height / 2)
            draw.line((padding, sep_y, width - padding, sep_y), fill=_SEPARATOR_COLOR, width=2)
            separator_drawn = True
        if vs_inline:
            inline_vs_used = True
            home_text, away_text, gap = vs_inline
            x = padding
            text_y = _center_text_y(y, line_height, font)
            line_center = y + line_height / 2
            y_pos = int(line_center - logo_size / 2)
            if home_logo_img:
                image.paste(home_logo_img, (int(x), y_pos), home_logo_img)
            x += logo_size + gap
            _draw_mixed_line(draw, x, text_y, home_text, font, color, fonts.emoji)
            x += _measure_text(draw, home_text, font, fonts.emoji) + gap
            _draw_mixed_line(draw, x, text_y, "vs", font, color, fonts.emoji)
            x += _measure_text(draw, "vs", font, fonts.emoji) + gap
            _draw_mixed_line(draw, x, text_y, away_text, font, color, fonts.emoji)
            x += _measure_text(draw, away_text, font, fonts.emoji) + gap
            if away_logo_img:
                image.paste(away_logo_img, (int(x), y_pos), away_logo_img)
        elif line:
            text_y = _center_text_y(y, line_height, font)
            _draw_mixed_line(draw, padding + x_offset, text_y, line, font, color, fonts.emoji)
        if anchor and logo_y is None:
            logo_y = y
            logo_line_height = _line_height(font, fonts.emoji)
        if is_bet and bet_line_y is None:
            bet_line_y = y
            bet_line_height = line_height
        y += line_height + gap

    if has_logo and not inline_vs_used and logo_y is not None and logo_line_height is not None:
        y_pos = int(logo_y + (logo_line_height - logo_size) / 2)
        if home_logo_img:
            image.paste(home_logo_img, (padding, y_pos), home_logo_img)
        if away_logo_img:
            image.paste(away_logo_img, (width - padding - logo_size, y_pos), away_logo_img)

    if clean_league_label:
        badge_font = fonts.small
        max_badge_width = int(width * 0.45)
        badge_text = _truncate_text(draw, clean_league_label, badge_font, max_badge_width)
        badge_w, badge_h = _badge_size(draw, badge_text, badge_font, pad_x=12, pad_y=6)
        badge_x = width - padding - badge_w
        badge_y = max(10, padding - badge_h - 8)
        _draw_badge(
            draw,
            badge_x,
            badge_y,
            badge_text,
            badge_font,
            _BADGE_BG,
            _BADGE_TEXT,
            pad_x=12,
            pad_y=6,
            radius=12,
        )

    if market_label:
        market_text = market_label.strip().upper()
        badge_font = fonts.small
        market_w, market_h = _badge_size(draw, market_text, badge_font, pad_x=12, pad_y=6)
        market_x = width - padding - market_w
        if bet_line_y is not None and bet_line_height is not None:
            market_y = int(bet_line_y + (bet_line_height - market_h) / 2)
        else:
            market_y = max(10, padding + 10)
        _draw_badge(
            draw,
            market_x,
            market_y,
            market_text,
            badge_font,
            _MARKET_BADGE_BG,
            _MARKET_BADGE_TEXT,
            pad_x=12,
            pad_y=6,
            radius=12,
        )

    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()
