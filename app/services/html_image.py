from __future__ import annotations

import atexit
import base64
import html
import re
import threading
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from playwright.sync_api import Browser, sync_playwright


_WIDTH = 1280
_MAX_VIEWPORT_H = 2200
_DEFAULT_VIEWPORT_H = 1400
_DEFAULT_STYLE_VARIANT = "pro"
_STYLE_VARIANTS = {"pro", "viral"}

_VARIATION_SELECTOR = "\ufe0f"
_ZERO_WIDTH_JOINER = "\u200d"
_EMOJI_RE = re.compile(r"[\U0001F1E6-\U0001F1FF\U0001F300-\U0001FAFF\u2600-\u27BF]")

_PLAYWRIGHT = None
_BROWSER: Browser | None = None
_BROWSER_LOCK = threading.Lock()


@dataclass
class _CardData:
    title: str
    league: str
    date_line: str
    match_line: str
    home_team: str
    away_team: str
    recommendation_title: str
    recommendation_lines: list[str]
    recommendation_main: str
    recommendation_odd: str
    recommendation_extra: list[str]
    market: str


def _strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "")


def _strip_emojis(text: str) -> str:
    if not text:
        return ""
    cleaned = "".join(ch for ch in text if ch not in (_VARIATION_SELECTOR, _ZERO_WIDTH_JOINER))
    return _EMOJI_RE.sub("", cleaned)


def _normalize_line(line: str) -> str:
    return _strip_emojis(html.unescape(_strip_tags(line))).strip()


def _is_datetime_line(line: str) -> bool:
    if not line:
        return False
    if "UTC" in line.upper():
        return True
    return bool(re.search(r"\b\d{1,2}:\d{2}\b", line))


def _split_match_line(match_line: str) -> tuple[str, str]:
    text = (match_line or "").strip()
    if not text:
        return ("HOME", "AWAY")
    for pattern in (r"\s+vs\.?\s+", r"\s+v\.?\s+"):
        parts = re.split(pattern, text, maxsplit=1, flags=re.IGNORECASE)
        if len(parts) == 2:
            home = parts[0].strip(" -|")
            away = parts[1].strip(" -|")
            return (home or "HOME", away or "AWAY")
    dash_parts = re.split(r"\s*[-–—]\s*", text, maxsplit=1)
    if len(dash_parts) == 2:
        home = dash_parts[0].strip(" -|")
        away = dash_parts[1].strip(" -|")
        return (home or "HOME", away or "AWAY")
    return (text, "OPPONENT")


def _split_recommendation_lines(lines: list[str]) -> tuple[str, str, list[str]]:
    clean = [x.strip() for x in lines if x and x.strip()]
    if not clean:
        return ("Total Under 2.5", "@ 2.10", [])
    odd_idx = next((i for i, line in enumerate(clean) if re.search(r"^\s*@", line)), None)
    if odd_idx is None:
        odd_idx = next((i for i, line in enumerate(clean) if re.search(r"\b\d+(?:[.,]\d+)\b", line)), None)
    main = clean[0]
    odd = ""
    extra: list[str] = []
    for i, line in enumerate(clean[1:], start=1):
        if odd_idx is not None and i == odd_idx and not odd:
            odd = line
            continue
        if not odd and re.search(r"^\s*@", line):
            odd = line
            continue
        extra.append(line)
    if not odd:
        if len(clean) > 1:
            odd = clean[1]
            extra = clean[2:]
        else:
            odd = "@ 2.10"
    return (main, odd, extra)


def _initial_letter(text: str, fallback: str) -> str:
    m = re.search(r"[A-Za-zА-Яа-я0-9]", text or "")
    return (m.group(0).upper() if m else fallback[:1].upper()) or "?"


def _clean_meta_text(value: str | None) -> str:
    return _normalize_line(value or "")


def _normalize_form(value: str | None) -> str:
    text = (value or "").upper()
    letters = "".join(ch for ch in text if ch in {"W", "D", "L"})
    return letters[:8]


def _normalize_style_variant(value: str | None) -> str:
    style = (value or "").strip().lower()
    return style if style in _STYLE_VARIANTS else _DEFAULT_STYLE_VARIANT


def _title_color(title: str | None) -> str:
    text = (title or "").strip().upper()
    if "STRONG" in text or "СИЛЬНЫЙ" in text:
        return "#ffd66b"
    if "TOP" in text or "ТОП" in text:
        return "#59e2a5"
    if "HIGH-CONFIDENCE" in text or "HIGH CONFIDENCE" in text:
        return "#ffbf71"
    if "STANDARD" in text or "СТАНДАРТ" in text:
        return "#9fb7ff"
    if "HOT" in text or "ГОРЯЧ" in text:
        return "#ff9a4a"
    return "#ff9a4a"


def _odds_display(value: str | None) -> str:
    raw = _normalize_line(value or "")
    if not raw:
        return "2.00"
    m = re.search(r"\d+(?:[.,]\d+)?", raw)
    if m:
        return m.group(0).replace(",", ".")
    cleaned = raw.replace("@", "").strip()
    return cleaned or "2.00"


def _odds_font_size_px(display_value: str) -> int:
    compact = re.sub(r"[^0-9.]", "", display_value or "")
    length = len(compact)
    if length <= 4:
        return 64
    if length == 5:
        return 52
    if length == 6:
        return 44
    return 38


def _team_font_size_px(team_name: str | None) -> int:
    text = _normalize_line(team_name or "")
    length = len(text)
    if length <= 13:
        return 56
    if length <= 17:
        return 50
    if length <= 21:
        return 44
    if length <= 25:
        return 40
    return 36


def _signal_title_font_size_px(title: str | None) -> int:
    text = _normalize_line(title or "")
    length = len(text)
    if length <= 12:
        return 35
    if length <= 16:
        return 32
    if length <= 20:
        return 29
    if length <= 24:
        return 26
    return 24


def _text_width_units(text: str | None) -> float:
    src = _normalize_line(text or "")
    if not src:
        return 1.0
    units = 0.0
    for ch in src:
        if ch.isspace():
            units += 0.32
        elif ch in "MW@#%&":
            units += 1.04
        elif ch in "ilIjtfr":
            units += 0.45
        elif ch.isupper():
            units += 0.84
        elif ch.isdigit():
            units += 0.72
        else:
            units += 0.68
    return max(units, 1.0)


def _fit_font_size_px(text: str | None, available_px: int, *, min_px: int, max_px: int) -> int:
    units = _text_width_units(text)
    est = int(available_px / units)
    return max(min_px, min(max_px, est))


def _normalize_probability(value: float | int | None) -> float | None:
    if value is None:
        return None
    try:
        num = float(value)
    except Exception:
        return None
    if num < 0:
        return None
    if num > 1.0 and num <= 100.0:
        num /= 100.0
    if num > 1.0:
        return None
    return max(0.0, min(1.0, num))


def _parse_card_data(
    text: str,
    *,
    league_label: str | None,
    market_label: str | None,
    bet_label: str | None,
) -> _CardData:
    clean_league = _normalize_line(league_label or "")
    clean_bet = _normalize_line(bet_label or "")
    lines = [_normalize_line(x) for x in (text or "").splitlines()]
    lines = [x for x in lines if x]
    if clean_league:
        lines = [x for x in lines if x.lower() != clean_league.lower()]
    lines = [x for x in lines if not x.startswith("🎯")]
    if not lines:
        lines = ["HOT PREDICTION"]

    title = lines[0]
    match_idx = next((i for i, line in enumerate(lines[1:], start=1) if " vs " in line.lower()), None)
    date_idx = next(
        (i for i, line in enumerate(lines[1:], start=1) if _is_datetime_line(line)),
        None,
    )
    rec_idx = None
    if clean_bet:
        rec_idx = next((i for i, line in enumerate(lines) if line.lower() == clean_bet.lower()), None)
    if rec_idx is None:
        rec_idx = next(
            (
                i
                for i, line in enumerate(lines)
                if line.upper() in {"BET OF THE DAY", "RECOMMENDATION", "РЕКОМЕНДАЦИЯ"}
            ),
            None,
        )

    match_line = lines[match_idx] if match_idx is not None else (lines[1] if len(lines) > 1 else "")
    home_team, away_team = _split_match_line(match_line)
    date_line = lines[date_idx] if date_idx is not None else ""
    recommendation_title = lines[rec_idx] if rec_idx is not None else (clean_bet or "BET OF THE DAY")

    rec_lines: list[str] = []
    if rec_idx is not None:
        rec_lines = [x for x in lines[rec_idx + 1 :] if x]
    else:
        start = max(match_idx or 0, date_idx or 0) + 1
        rec_lines = [x for x in lines[start:] if x]
    if not rec_lines:
        rec_lines = ["Total Under 2.5", "@ 2.10"]
    rec_main, rec_odd, rec_extra = _split_recommendation_lines(rec_lines)

    market = _normalize_line(market_label or "TOTAL").upper()
    return _CardData(
        title=title,
        league=clean_league,
        date_line=date_line,
        match_line=match_line,
        home_team=home_team,
        away_team=away_team,
        recommendation_title=recommendation_title,
        recommendation_lines=rec_lines,
        recommendation_main=rec_main,
        recommendation_odd=rec_odd,
        recommendation_extra=rec_extra,
        market=market,
    )


def _rgb_to_hex(color: tuple[int, int, int]) -> str:
    return f"#{color[0]:02x}{color[1]:02x}{color[2]:02x}"


def _bytes_to_data_uri(data: bytes | None, fallback_svg: str) -> str:
    if not data:
        return fallback_svg
    mime = "image/png"
    if data[:3] == b"\xff\xd8\xff":
        mime = "image/jpeg"
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        mime = "image/webp"
    elif data[:4] == b"\x89PNG":
        mime = "image/png"
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _fallback_logo_svg(letter: str, bg: str) -> str:
    svg = (
        f"<svg xmlns='http://www.w3.org/2000/svg' width='96' height='96' viewBox='0 0 96 96'>"
        f"<defs><linearGradient id='g' x1='0' y1='0' x2='1' y2='1'>"
        f"<stop offset='0' stop-color='{bg}'/><stop offset='1' stop-color='#1d2948'/></linearGradient></defs>"
        f"<circle cx='48' cy='48' r='44' fill='url(#g)'/>"
        f"<circle cx='48' cy='48' r='43' fill='none' stroke='#9fb3d8' stroke-width='2'/>"
        f"<text x='50%' y='54%' text-anchor='middle' dominant-baseline='middle' "
        f"font-family='Arial' font-size='38' fill='#f8fafc'>{html.escape(letter[:1].upper() or '?')}</text>"
        f"</svg>"
    )
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


@lru_cache(maxsize=1)
def _embedded_font_css() -> str:
    base = Path(__file__).resolve().parent.parent / "assets" / "fonts"
    specs = [
        ("PredSans", "NotoSans-Regular.ttf", 400),
        ("PredSans", "NotoSans-Bold.ttf", 700),
    ]
    css_chunks: list[str] = []
    for family, filename, weight in specs:
        path = base / filename
        if not path.exists():
            continue
        data = path.read_bytes()
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


def _ensure_browser() -> Browser:
    global _PLAYWRIGHT, _BROWSER
    if _BROWSER is not None:
        return _BROWSER
    with _BROWSER_LOCK:
        if _BROWSER is not None:
            return _BROWSER
        _PLAYWRIGHT = sync_playwright().start()
        _BROWSER = _PLAYWRIGHT.chromium.launch(
            headless=True,
            args=[
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--font-render-hinting=none",
            ],
        )
        return _BROWSER


def _shutdown_browser() -> None:
    global _PLAYWRIGHT, _BROWSER
    with _BROWSER_LOCK:
        if _BROWSER is not None:
            try:
                _BROWSER.close()
            except Exception:
                pass
            _BROWSER = None
        if _PLAYWRIGHT is not None:
            try:
                _PLAYWRIGHT.stop()
            except Exception:
                pass
            _PLAYWRIGHT = None


atexit.register(_shutdown_browser)


def _build_html(
    data: _CardData,
    *,
    width: int,
    style_variant: str = _DEFAULT_STYLE_VARIANT,
    home_logo_uri: str,
    away_logo_uri: str,
    league_logo_uri: str,
    league_country: str | None = None,
    league_round: str | None = None,
    venue_name: str | None = None,
    venue_city: str | None = None,
    home_rank: int | None = None,
    away_rank: int | None = None,
    home_points: int | None = None,
    away_points: int | None = None,
    home_played: int | None = None,
    away_played: int | None = None,
    home_goal_diff: int | None = None,
    away_goal_diff: int | None = None,
    home_form: str | None = None,
    away_form: str | None = None,
    home_win_prob: float | None = None,
    draw_prob: float | None = None,
    away_win_prob: float | None = None,
    signal_title: str | None = None,
    signal_line_1: str | None = None,
    signal_line_2: str | None = None,
    signal_line_3: str | None = None,
) -> str:
    style_variant_clean = _normalize_style_variant(style_variant)
    canvas_class = f"theme-{style_variant_clean}"
    kicker_label = "MATCHDAY SIGNAL" if style_variant_clean == "viral" else "AI BETTING SIGNAL"
    font_css = _embedded_font_css()

    league_country_clean = _clean_meta_text(league_country)
    league_round_clean = _clean_meta_text(league_round)
    venue_name_clean = _clean_meta_text(venue_name)
    venue_city_clean = _clean_meta_text(venue_city)
    home_form_clean = _normalize_form(home_form)
    away_form_clean = _normalize_form(away_form)

    disclaimer_html = html.escape(
        "DISCLAIMER: This is an analytical prediction, not a guarantee of results. "
        "The model formulas are proprietary and not disclosed."
    )
    date_html = html.escape(data.date_line) if data.date_line else "TBA"
    league_html = html.escape(data.league) if data.league else "League"
    home_team_html = html.escape(data.home_team)
    away_team_html = html.escape(data.away_team)
    pick_main_html = html.escape(data.recommendation_main)
    odds_center_html = html.escape(_odds_display(data.recommendation_odd))
    odds_font_size_px = _odds_font_size_px(_odds_display(data.recommendation_odd))
    title_html = html.escape(data.title)
    title_color = _title_color(data.title)
    signal_rows_raw = [signal_line_1, signal_line_2, signal_line_3]
    def _render_signal_metric_line(line: str) -> str:
        text = line.strip()
        m = re.search(r"\(([^()]*(?:\+|-)\d+(?:[.,]\d+)?%\s*[^()]*)\)\s*$", text)
        if not m:
            return html.escape(text)
        base = text[: m.start()].rstrip()
        delta = m.group(1).strip()
        if not base:
            return f"<span class='signal-delta'>({html.escape(delta)})</span>"
        return f"{html.escape(base)} <span class='signal-delta'>({html.escape(delta)})</span>"

    signal_rows = [
        _render_signal_metric_line(x)
        for x in signal_rows_raw
        if isinstance(x, str) and x.strip()
    ]
    signal_title_html = html.escape((signal_title or "").strip())
    top_inner_width = max(740, int(width) - 108)
    per_top_card_outer = int((top_inner_width - 18) / 2)
    per_top_card_inner = max(240, per_top_card_outer - 28)
    signal_title_space = max(150, per_top_card_inner - 205)

    match_inner_width = max(820, int(width) - 108)
    stage_inner = max(520, match_inner_width - 52)
    side_col_width = max(220, int((stage_inner - 220 - 32) / 2))
    team_name_space = max(180, side_col_width - 18)

    signal_title_size_px = _fit_font_size_px(
        signal_title or "VALUE INDICATORS",
        signal_title_space,
        min_px=18,
        max_px=_signal_title_font_size_px(signal_title),
    )
    home_team_fit_px = _fit_font_size_px(
        data.home_team,
        team_name_space,
        min_px=24,
        max_px=_team_font_size_px(data.home_team),
    )
    away_team_fit_px = _fit_font_size_px(
        data.away_team,
        team_name_space,
        min_px=24,
        max_px=_team_font_size_px(data.away_team),
    )
    # Keep both team names at a single synchronized size for visual symmetry.
    shared_team_font_px = min(home_team_fit_px, away_team_fit_px)
    home_team_font_px = shared_team_font_px
    away_team_font_px = shared_team_font_px
    if signal_rows:
        signal_content_block = (
            "<div class='signal-metrics'>"
            "<div class='signal-header-row'>"
            f"<div class='signal-metrics-title' style='font-size:{signal_title_size_px}px'>{signal_title_html or 'VALUE INDICATORS'}</div>"
            f"<div class='signal-kicker'><span class='kicker-dot'></span>{kicker_label}</div>"
            "</div>"
            + "".join(f"<div class='signal-metric-line'>{line}</div>" for line in signal_rows)
            + "</div>"
        )
    else:
        signal_content_block = (
            "<div class='signal-fallback'>"
            "<div class='signal-header-row'>"
            f"<h1 class='signal-title'>{title_html}</h1>"
            f"<div class='signal-kicker'><span class='kicker-dot'></span>{kicker_label}</div>"
            "</div>"
            "</div>"
        )

    meta_secondary_bits: list[str] = []
    if league_country_clean:
        meta_secondary_bits.append(league_country_clean)
    if league_round_clean:
        meta_secondary_bits.append(league_round_clean)
    meta_secondary_text = " • ".join(meta_secondary_bits) if meta_secondary_bits else ""
    meta_secondary_html = html.escape(meta_secondary_text)
    meta_secondary_block = f"<div class='meta-secondary'>{meta_secondary_html}</div>" if meta_secondary_text else ""

    home_rank_html = f"#{int(home_rank)}" if home_rank is not None else "#-"
    away_rank_html = f"#{int(away_rank)}" if away_rank is not None else "#-"
    home_points_html = f"{int(home_points)} pts" if home_points is not None else "— pts"
    away_points_html = f"{int(away_points)} pts" if away_points is not None else "— pts"
    home_played_html = f"P {int(home_played)}" if home_played is not None else "P —"
    away_played_html = f"P {int(away_played)}" if away_played is not None else "P —"
    home_gd_html = f"GD {int(home_goal_diff):+d}" if home_goal_diff is not None else "GD —"
    away_gd_html = f"GD {int(away_goal_diff):+d}" if away_goal_diff is not None else "GD —"

    def _form_tokens(form: str) -> str:
        if not form:
            return "<span class='form-empty'>NO FORM</span>"
        chunks: list[str] = []
        for ch in form:
            cls = "form-win" if ch == "W" else "form-draw" if ch == "D" else "form-loss"
            chunks.append(f"<span class='form-token {cls}'>{ch}</span>")
        return "".join(chunks)

    home_form_html = _form_tokens(home_form_clean)
    away_form_html = _form_tokens(away_form_clean)

    chance_meter_block = ""
    chance_parts = [
        _normalize_probability(home_win_prob),
        _normalize_probability(draw_prob),
        _normalize_probability(away_win_prob),
    ]
    known_sum = sum(part for part in chance_parts if part is not None)
    missing_count = sum(1 for part in chance_parts if part is None)
    if missing_count and known_sum < 1.0:
        fill = (1.0 - known_sum) / missing_count
        chance_parts = [fill if part is None else part for part in chance_parts]
    if all(part is not None for part in chance_parts):
        total = sum(chance_parts)  # type: ignore[arg-type]
        if total > 0:
            home_share = max(0.0, min(1.0, chance_parts[0] / total))  # type: ignore[index]
            draw_share = max(0.0, min(1.0, chance_parts[1] / total))  # type: ignore[index]
            away_share = max(0.0, min(1.0, chance_parts[2] / total))  # type: ignore[index]
            home_pct_int = int(round(home_share * 100))
            draw_pct_int = int(round(draw_share * 100))
            away_pct_int = max(0, 100 - home_pct_int - draw_pct_int)
            chance_meter_block = (
                "<div class='chance-wrap'>"
                "<div class='chance-labels'>"
                f"<div class='chance-label home'><span class='chance-val'>{home_pct_int}%</span></div>"
                f"<div class='chance-label draw'><span class='chance-val'>{draw_pct_int}%</span></div>"
                f"<div class='chance-label away'><span class='chance-val'>{away_pct_int}%</span></div>"
                "</div>"
                "<div class='chance-bar'>"
                f"<div class='chance-seg home' style='width:{home_share * 100:.3f}%'></div>"
                f"<div class='chance-seg draw' style='width:{draw_share * 100:.3f}%'></div>"
                f"<div class='chance-seg away' style='width:{away_share * 100:.3f}%'></div>"
                "</div>"
                "</div>"
            )

    venue_parts: list[str] = []
    if venue_name_clean:
        venue_parts.append(venue_name_clean)
    if venue_city_clean and venue_city_clean.lower() != (venue_name_clean or "").lower():
        venue_parts.append(venue_city_clean)
    venue_label = " • ".join(venue_parts)
    venue_block = f"<div class='venue-row'>{html.escape(venue_label)}</div>" if venue_label else ""

    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <style>
    {font_css}
    :root {{
      --bg0: #040918;
      --bg1: #081a36;
      --bg2: #071023;
      --ink: #f8fbff;
      --muted: #b9c6e3;
      --accent: #ffd739;
      --title: {title_color};
      --panel-line: #5a76ad;
      --home-rgb: 78, 134, 255;
      --away-rgb: 255, 94, 128;
      --home: rgb(var(--home-rgb));
      --away: rgb(var(--away-rgb));
      --home-glow: rgba(var(--home-rgb), 0.34);
      --away-glow: rgba(var(--away-rgb), 0.34);
      --home-border: rgba(var(--home-rgb), 0.82);
      --away-border: rgba(var(--away-rgb), 0.82);
      --home-soft: rgba(var(--home-rgb), 0.25);
      --away-soft: rgba(var(--away-rgb), 0.25);
      --home-meter: rgba(var(--home-rgb), 0.78);
      --away-meter: rgba(var(--away-rgb), 0.78);
      --logo-shell-size: 124px;
      --odds-shell-size: 170px;
    }}
    * {{ box-sizing: border-box; }}
    html, body {{ margin: 0; padding: 0; }}
    body {{
      background: #040915;
      font-family: "PredSans", "Segoe UI", Arial, sans-serif;
    }}
    #post-canvas {{
      width: {width}px;
      padding: 44px 54px 50px;
      position: relative;
      isolation: isolate;
      overflow: hidden;
      color: var(--ink);
      background:
        radial-gradient(760px 520px at 11% 16%, var(--home-glow) 0%, rgba(0,0,0,0.0) 74%),
        radial-gradient(760px 520px at 89% 18%, var(--away-glow) 0%, rgba(0,0,0,0.0) 74%),
        linear-gradient(154deg, var(--bg0) 0%, var(--bg1) 52%, var(--bg2) 100%);
    }}
    #post-canvas::before {{
      content: "";
      position: absolute;
      inset: 0;
      pointer-events: none;
      background:
        linear-gradient(118deg, rgba(255,255,255,0.06) 0%, rgba(255,255,255,0.00) 33%, rgba(255,255,255,0.04) 59%, rgba(255,255,255,0.00) 84%),
        repeating-linear-gradient(145deg, rgba(255,255,255,0.03) 0px, rgba(255,255,255,0.03) 2px, rgba(255,255,255,0.0) 2px, rgba(255,255,255,0.0) 16px);
      opacity: 0.45;
    }}
    #post-canvas::after {{
      content: "";
      position: absolute;
      inset: 0;
      pointer-events: none;
      background: radial-gradient(1000px 620px at 50% 102%, rgba(0,0,0,0.45), rgba(0,0,0,0.0) 62%);
    }}
    .top {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 18px;
      align-items: stretch;
      position: relative;
      z-index: 2;
    }}
    .signal-card {{
      border-radius: 18px;
      border: 1px solid rgba(141, 166, 211, 0.62);
      background: linear-gradient(180deg, rgba(12, 24, 50, 0.88), rgba(10, 21, 43, 0.76));
      padding: 12px 14px;
      display: flex;
      flex-direction: column;
      justify-content: flex-start;
      align-items: stretch;
      gap: 6px;
      min-height: 132px;
      height: 132px;
      box-shadow:
        inset 0 0 0 1px rgba(255,255,255,0.05),
        0 10px 24px rgba(2, 8, 20, 0.36);
    }}
    .signal-header-row {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      min-height: 38px;
      width: 100%;
    }}
    .signal-kicker {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 6px 12px 7px;
      border-radius: 999px;
      border: 1px solid rgba(152, 173, 214, 0.55);
      background: rgba(13, 26, 52, 0.62);
      color: #d4def4;
      font-size: 17px;
      line-height: 1;
      letter-spacing: 0.75px;
      text-transform: uppercase;
      white-space: nowrap;
      flex-shrink: 0;
    }}
    .kicker-dot {{
      width: 9px;
      height: 9px;
      border-radius: 50%;
      background: var(--accent);
      box-shadow: 0 0 10px rgba(255, 215, 57, 0.85);
    }}
    .signal-title {{
      margin: 0;
      font-size: clamp(34px, 3.8vw, 54px);
      line-height: 0.94;
      letter-spacing: 0.9px;
      text-transform: uppercase;
      color: var(--title);
      font-weight: 800;
      text-wrap: balance;
      text-shadow: 0 4px 13px rgba(3, 8, 21, 0.62);
    }}
    .signal-metrics {{
      width: 100%;
      display: flex;
      flex-direction: column;
      gap: 3px;
    }}
    .signal-metrics-title {{
      margin: 0;
      color: var(--title);
      line-height: 1.08;
      font-weight: 800;
      letter-spacing: 0.5px;
      text-transform: uppercase;
      text-shadow: 0 3px 10px rgba(3, 8, 21, 0.56);
      flex: 1 1 auto;
      min-width: 0;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      padding-bottom: 2px;
    }}
    .signal-metric-line {{
      color: #edf3ff;
      font-size: 22px;
      line-height: 1.2;
      font-weight: 650;
      letter-spacing: 0.15px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      padding-bottom: 3px;
    }}
    .signal-delta {{
      color: #63f1a2;
      font-weight: 800;
      text-shadow: 0 0 10px rgba(44, 221, 136, 0.26);
    }}
    .meta-stack {{
      min-width: 0;
    }}
    .meta-card {{
      border-radius: 18px;
      border: 1px solid rgba(141, 166, 211, 0.62);
      background: linear-gradient(180deg, rgba(12, 24, 50, 0.88), rgba(10, 21, 43, 0.76));
      padding: 12px 14px;
      display: flex;
      flex-direction: column;
      align-items: stretch;
      justify-content: flex-start;
      gap: 6px;
      min-height: 132px;
      height: 132px;
      box-shadow:
        inset 0 0 0 1px rgba(255,255,255,0.05),
        0 10px 24px rgba(2, 8, 20, 0.36);
    }}
    .meta-league {{
      display: flex;
      align-items: center;
      justify-content: flex-end;
      gap: 9px;
      color: var(--title);
      font-size: 35px;
      font-weight: 700;
      line-height: 1;
      letter-spacing: 0.2px;
      min-height: 38px;
    }}
    .meta-date {{
      color: #f1f6ff;
      font-size: 22px;
      line-height: 1.04;
      font-weight: 600;
      letter-spacing: 0.35px;
      text-align: right;
    }}
    .league-logo {{
      width: 34px;
      height: 34px;
      border-radius: 999px;
      border: 1px solid rgba(170, 188, 220, 0.55);
      object-fit: contain;
      background: rgba(9, 19, 40, 0.72);
      box-shadow: 0 0 18px rgba(123, 148, 201, 0.38);
    }}
    .meta-secondary {{
      color: #cfdbf6;
      font-size: 16px;
      line-height: 1;
      letter-spacing: 0.28px;
      text-transform: uppercase;
      text-align: right;
      opacity: 0.96;
    }}
    .match-stage {{
      margin-top: 26px;
      position: relative;
      z-index: 2;
      border-radius: 34px;
      border: 2px solid rgba(90, 118, 173, 0.68);
      background:
        linear-gradient(180deg, rgba(11, 23, 50, 0.90), rgba(8, 17, 38, 0.80)),
        linear-gradient(90deg, var(--home-soft) 0%, rgba(0,0,0,0.0) 50%, var(--away-soft) 100%);
      padding: 24px 26px 24px;
      box-shadow:
        0 24px 46px rgba(0, 0, 0, 0.30),
        inset 0 0 0 1px rgba(255,255,255,0.04);
    }}
    .teams {{
      display: grid;
      grid-template-columns: 1fr 220px 1fr;
      gap: 16px;
      align-items: center;
    }}
    .team {{
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 12px;
      min-width: 0;
      padding-top: calc((var(--odds-shell-size) - var(--logo-shell-size)) / 2);
    }}
    .logo-shell {{
      width: var(--logo-shell-size);
      height: var(--logo-shell-size);
      border-radius: 999px;
      display: grid;
      place-items: center;
      border: 2px solid rgba(166, 188, 226, 0.72);
      background: rgba(7, 16, 36, 0.90);
      box-shadow: 0 10px 24px rgba(4, 9, 22, 0.56);
    }}
    .logo-shell.home {{
      border-color: var(--home-border);
      box-shadow: 0 10px 26px var(--home-soft);
    }}
    .logo-shell.away {{
      border-color: var(--away-border);
      box-shadow: 0 10px 26px var(--away-soft);
    }}
    .logo-shell img {{
      width: 90px;
      height: 90px;
      object-fit: contain;
      display: block;
      filter: drop-shadow(0 3px 8px rgba(0,0,0,0.45));
    }}
    .team-name {{
      margin: 0;
      text-align: center;
      line-height: 1.12;
      font-weight: 700;
      letter-spacing: 0.15px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      max-width: 100%;
      padding: 0 6px 4px;
      text-shadow: 0 2px 8px rgba(0,0,0,0.40);
    }}
    .odds-core {{
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 8px;
    }}
    .odds-shell {{
      position: relative;
      width: var(--odds-shell-size);
      height: var(--odds-shell-size);
      border-radius: 50%;
      display: flex;
      align-items: center;
      justify-content: center;
      border: 2px solid rgba(176, 193, 228, 0.82);
      background:
        radial-gradient(circle at 34% 26%, rgba(255,255,255,0.28), rgba(255,255,255,0.0) 40%),
        radial-gradient(circle at 58% 72%, rgba(130, 154, 214, 0.20), rgba(130, 154, 214, 0.0) 60%),
        radial-gradient(circle at 50% 52%, rgba(40, 57, 93, 0.84) 0%, rgba(11, 23, 50, 0.96) 78%);
      box-shadow:
        inset 0 0 0 1px rgba(255,255,255,0.10),
        0 0 34px rgba(138, 161, 218, 0.34);
      overflow: hidden;
    }}
    .odds-shell::before {{
      content: "";
      position: absolute;
      inset: -16px;
      border-radius: 50%;
      background:
        radial-gradient(circle at 50% 50%, rgba(171, 192, 235, 0.22) 0%, rgba(171, 192, 235, 0.0) 68%);
      pointer-events: none;
    }}
    .odds-shell::after {{
      content: "";
      position: absolute;
      inset: 8px;
      border-radius: 50%;
      border: 1px solid rgba(202, 217, 247, 0.56);
      pointer-events: none;
    }}
    .odds-inner {{
      position: relative;
      z-index: 1;
      width: 100%;
      height: 100%;
      display: grid;
      place-items: center;
      padding: 0 14px;
      text-align: center;
    }}
    .odds-value {{
      margin: 0;
      width: 100%;
      line-height: 1;
      font-weight: 800;
      letter-spacing: 0.6px;
      color: var(--accent);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: clip;
      text-shadow:
        0 2px 10px rgba(0,0,0,0.45),
        0 0 24px rgba(255, 215, 57, 0.30);
    }}
    .odds-label {{
      margin: 0;
      font-size: 14px;
      line-height: 1;
      font-weight: 600;
      letter-spacing: 0.7px;
      text-transform: uppercase;
      color: #d1def5;
      opacity: 0.95;
      z-index: 2;
      position: absolute;
      left: 50%;
      bottom: 17px;
      transform: translateX(-50%);
    }}
    .table-strip {{
      margin-top: 14px;
      display: grid;
      grid-template-columns: 1fr 1fr;
      align-items: center;
      gap: 22px;
      border-top: 1px solid rgba(120, 145, 190, 0.42);
      padding-top: 14px;
    }}
    .table-team {{
      min-width: 0;
      width: 100%;
      border-radius: 14px;
      border: 1px solid rgba(130, 154, 196, 0.45);
      background: rgba(11, 22, 46, 0.56);
      padding: 10px 11px 11px;
    }}
    .table-main {{
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      gap: 10px;
    }}
    .table-rank {{
      font-size: 34px;
      line-height: 1;
      font-weight: 700;
      color: #f8fbff;
      letter-spacing: 0.4px;
    }}
    .table-points {{
      font-size: 30px;
      line-height: 1;
      font-weight: 700;
      color: #f4f8ff;
    }}
    .table-sub {{
      margin-top: 8px;
      display: flex;
      gap: 12px;
      color: #f3f8ff;
      font-size: 21px;
      line-height: 1;
      font-weight: 800;
      letter-spacing: 0.2px;
      text-transform: uppercase;
    }}
    .table-team.away .table-main {{
      flex-direction: row-reverse;
    }}
    .table-team.away .table-sub {{
      justify-content: flex-start;
      flex-direction: row-reverse;
      text-align: right;
    }}
    .form-track {{
      margin-top: 8px;
      display: flex;
      gap: 8px;
      align-items: center;
      flex-wrap: wrap;
    }}
    .table-team.away .form-track {{
      justify-content: flex-start;
      flex-direction: row-reverse;
    }}
    .form-token {{
      width: 34px;
      height: 34px;
      border-radius: 50%;
      display: grid;
      place-items: center;
      font-size: 18px;
      line-height: 1;
      font-weight: 900;
      color: #f8fbff;
      border: 1px solid rgba(255,255,255,0.12);
    }}
    .form-win {{
      background: linear-gradient(180deg, rgba(41, 179, 107, 0.95), rgba(26, 147, 87, 0.95));
    }}
    .form-draw {{
      background: linear-gradient(180deg, rgba(234, 179, 8, 0.95), rgba(202, 138, 4, 0.95));
      color: #111827;
    }}
    .form-loss {{
      background: linear-gradient(180deg, rgba(239, 68, 68, 0.95), rgba(185, 28, 28, 0.95));
    }}
    .form-empty {{
      font-size: 15px;
      line-height: 1;
      font-weight: 600;
      color: #9fb3d7;
      letter-spacing: 0.4px;
    }}
    .chance-wrap {{
      margin-top: 10px;
    }}
    .chance-labels {{
      display: grid;
      grid-template-columns: 1fr 1fr 1fr;
      gap: 10px;
      align-items: center;
      margin-bottom: 6px;
      color: #d6e2fb;
      font-size: 16px;
      line-height: 1;
      font-weight: 700;
      letter-spacing: 0.2px;
      text-transform: uppercase;
      opacity: 0.95;
    }}
    .chance-label.home {{
      text-align: left;
    }}
    .chance-label.draw {{
      text-align: center;
    }}
    .chance-label.away {{
      text-align: right;
    }}
    .chance-bar {{
      height: 8px;
      border-radius: 999px;
      overflow: hidden;
      display: flex;
      border: 1px solid rgba(132, 155, 197, 0.34);
      background: rgba(8, 18, 38, 0.72);
      opacity: 0.88;
    }}
    .chance-seg {{
      height: 100%;
    }}
    .chance-seg.home {{
      background: linear-gradient(90deg, var(--home-meter) 0%, rgba(235, 243, 255, 0.58) 100%);
    }}
    .chance-seg.draw {{
      background: linear-gradient(90deg, rgba(188, 201, 228, 0.84) 0%, rgba(146, 160, 189, 0.82) 100%);
    }}
    .chance-seg.away {{
      background: linear-gradient(90deg, rgba(235, 243, 255, 0.48) 0%, var(--away-meter) 100%);
    }}
    .venue-row {{
      margin-top: 11px;
      padding-bottom: 7px;
      text-align: center;
      color: #f2f7ff;
      font-size: 19px;
      line-height: 1.05;
      letter-spacing: 0.35px;
      font-weight: 700;
      text-transform: uppercase;
      opacity: 0.99;
    }}
    .pick {{
      margin-top: 30px;
      position: relative;
      z-index: 2;
      border-radius: 30px;
      border: 2px solid rgba(255, 215, 57, 0.55);
      background:
        linear-gradient(180deg, rgba(15, 27, 55, 0.92), rgba(10, 19, 41, 0.86)),
        linear-gradient(94deg, var(--home-soft) 0%, rgba(0,0,0,0.0) 38%, var(--away-soft) 100%);
      padding: 22px 24px 22px;
      box-shadow:
        0 22px 44px rgba(0,0,0,0.30),
        inset 0 0 0 1px rgba(255,255,255,0.04);
    }}
    .pick-main {{
      text-align: center;
      font-size: clamp(56px, 4.6vw, 70px);
      line-height: 0.98;
      font-weight: 700;
      color: var(--accent);
      letter-spacing: 0.2px;
      text-shadow: 0 2px 7px rgba(2, 7, 18, 0.50);
    }}
    .pick-disclaimer {{
      margin-top: 10px;
      padding-top: 8px;
      padding-bottom: 16px;
      border-top: 1px solid rgba(128, 150, 192, 0.34);
      text-align: center;
      color: #d9e5f9;
      font-size: 15px;
      line-height: 1.32;
      font-weight: 650;
      letter-spacing: 0.2px;
    }}
    #post-canvas.theme-viral {{
      background:
        radial-gradient(820px 560px at 13% 16%, var(--home-glow) 0%, rgba(0,0,0,0.0) 70%),
        radial-gradient(820px 560px at 87% 16%, var(--away-glow) 0%, rgba(0,0,0,0.0) 70%),
        linear-gradient(132deg, #081427 0%, #0b1a3f 44%, #21102f 100%);
    }}
    #post-canvas.theme-viral::before {{
      background:
        linear-gradient(114deg, rgba(255,255,255,0.08) 0%, rgba(255,255,255,0.00) 28%, rgba(255,255,255,0.06) 58%, rgba(255,255,255,0.00) 86%),
        repeating-linear-gradient(144deg, rgba(255,255,255,0.03) 0px, rgba(255,255,255,0.03) 1px, rgba(255,255,255,0.0) 1px, rgba(255,255,255,0.0) 13px);
      opacity: 0.56;
    }}
    #post-canvas.theme-viral .signal-kicker {{
      border-color: rgba(255, 214, 94, 0.58);
      background: rgba(30, 24, 10, 0.70);
      color: #ffe6a9;
    }}
    #post-canvas.theme-viral .signal-title {{
      text-shadow:
        0 5px 14px rgba(3, 8, 21, 0.62),
        0 0 22px rgba(255, 174, 78, 0.22);
    }}
    #post-canvas.theme-viral .signal-card {{
      border-color: rgba(255, 186, 122, 0.52);
      background: linear-gradient(180deg, rgba(27, 29, 56, 0.90), rgba(14, 23, 50, 0.80));
      box-shadow:
        inset 0 0 0 1px rgba(255,255,255,0.06),
        0 12px 24px rgba(2, 8, 20, 0.40),
        0 0 30px rgba(255, 158, 91, 0.10);
    }}
    #post-canvas.theme-viral .meta-card {{
      border-color: rgba(255, 186, 122, 0.52);
      background: linear-gradient(180deg, rgba(27, 29, 56, 0.90), rgba(14, 23, 50, 0.80));
      box-shadow:
        inset 0 0 0 1px rgba(255,255,255,0.06),
        0 12px 24px rgba(2, 8, 20, 0.40),
        0 0 30px rgba(255, 158, 91, 0.10);
    }}
    #post-canvas.theme-viral .match-stage {{
      border-color: rgba(255, 181, 118, 0.45);
      background:
        linear-gradient(180deg, rgba(16, 28, 60, 0.92), rgba(9, 18, 44, 0.86)),
        linear-gradient(90deg, var(--home-soft) 0%, rgba(0,0,0,0.0) 50%, var(--away-soft) 100%);
      box-shadow:
        0 26px 50px rgba(0, 0, 0, 0.35),
        inset 0 0 0 1px rgba(255,255,255,0.05);
    }}
    #post-canvas.theme-viral .odds-shell {{
      border-color: rgba(255, 198, 128, 0.78);
      box-shadow:
        inset 0 0 0 1px rgba(255,255,255,0.10),
        0 0 30px rgba(255, 164, 80, 0.24);
    }}
    #post-canvas.theme-viral .pick {{
      border-color: rgba(255, 192, 76, 0.84);
      background:
        linear-gradient(180deg, rgba(22, 31, 63, 0.95), rgba(12, 20, 48, 0.89)),
        linear-gradient(102deg, var(--home-soft) 0%, rgba(0,0,0,0.0) 41%, var(--away-soft) 100%);
      box-shadow:
        0 22px 46px rgba(0,0,0,0.36),
        inset 0 0 0 1px rgba(255,255,255,0.06),
        0 0 36px rgba(255, 187, 71, 0.14);
    }}
    #post-canvas.theme-viral .pick-main {{
      font-weight: 700;
    }}
    #post-canvas.theme-viral .pick-disclaimer {{
      color: #c2cee5;
      border-top-color: rgba(159, 177, 214, 0.42);
    }}
  </style>
</head>
<body>
  <div id="post-canvas" class="{canvas_class}">
    <header class="top">
      <div class="signal-card">
        {signal_content_block}
      </div>
      <div class="meta-stack">
        <div class="meta-card">
          <div class="meta-league">
            <img class="league-logo" src="{league_logo_uri}" alt="league logo" />
            <span>{league_html}</span>
          </div>
          <div class="meta-date">{date_html}</div>
          {meta_secondary_block}
        </div>
      </div>
    </header>

    <section class="match-stage">
      <div class="teams">
        <div class="team">
          <div class="logo-shell home"><img class="team-logo home-logo" src="{home_logo_uri}" alt="home logo" /></div>
          <p class="team-name" style="font-size:{home_team_font_px}px">{home_team_html}</p>
        </div>
        <div class="odds-core">
          <div class="odds-shell">
            <div class="odds-inner">
              <div class="odds-value" style="font-size:{odds_font_size_px}px">{odds_center_html}</div>
              <div class="odds-label">ODDS</div>
            </div>
          </div>
        </div>
        <div class="team">
          <div class="logo-shell away"><img class="team-logo away-logo" src="{away_logo_uri}" alt="away logo" /></div>
          <p class="team-name" style="font-size:{away_team_font_px}px">{away_team_html}</p>
        </div>
      </div>
      <div class="table-strip">
        <div class="table-team home">
          <div class="table-main">
            <span class="table-rank">{home_rank_html}</span>
            <span class="table-points">{home_points_html}</span>
          </div>
          <div class="table-sub">
            <span>{home_played_html}</span>
            <span>{home_gd_html}</span>
          </div>
          <div class="form-track">{home_form_html}</div>
        </div>
        <div class="table-team away">
          <div class="table-main">
            <span class="table-rank">{away_rank_html}</span>
            <span class="table-points">{away_points_html}</span>
          </div>
          <div class="table-sub">
            <span>{away_played_html}</span>
            <span>{away_gd_html}</span>
          </div>
          <div class="form-track">{away_form_html}</div>
        </div>
      </div>
      {chance_meter_block}
      {venue_block}
    </section>

    <section class="pick">
      <div class="pick-main">{pick_main_html}</div>
      <div class="pick-disclaimer">{disclaimer_html}</div>
    </section>
  </div>
  <script>
    (() => {{
      const root = document.documentElement;
      const canvas = document.getElementById("post-canvas");
      const homeLogo = document.querySelector(".team-logo.home-logo");
      const awayLogo = document.querySelector(".team-logo.away-logo");
      const fallbackHome = [78, 134, 255];
      const fallbackAway = [255, 94, 128];

      const setReady = () => {{
        if (canvas) {{
          canvas.dataset.paletteReady = "1";
        }}
      }};

      const setPalette = (name, rgb) => {{
        const safe = [
          Math.max(0, Math.min(255, Math.round(rgb[0] || 0))),
          Math.max(0, Math.min(255, Math.round(rgb[1] || 0))),
          Math.max(0, Math.min(255, Math.round(rgb[2] || 0))),
        ];
        root.style.setProperty(`--${{name}}-rgb`, `${{safe[0]}}, ${{safe[1]}}, ${{safe[2]}}`);
      }};

      const waitImage = (img) =>
        new Promise((resolve) => {{
          if (!img) {{
            resolve();
            return;
          }}
          if (img.complete && img.naturalWidth > 0 && img.naturalHeight > 0) {{
            resolve();
            return;
          }}
          let done = false;
          const finish = () => {{
            if (done) return;
            done = true;
            resolve();
          }};
          img.addEventListener("load", finish, {{ once: true }});
          img.addEventListener("error", finish, {{ once: true }});
          setTimeout(finish, 900);
        }});

      const rgbToHsl = (r, g, b) => {{
        const rn = r / 255;
        const gn = g / 255;
        const bn = b / 255;
        const max = Math.max(rn, gn, bn);
        const min = Math.min(rn, gn, bn);
        const d = max - min;
        let h = 0;
        const l = (max + min) / 2;
        const s = d === 0 ? 0 : d / (1 - Math.abs(2 * l - 1));
        if (d !== 0) {{
          if (max === rn) h = ((gn - bn) / d) % 6;
          else if (max === gn) h = (bn - rn) / d + 2;
          else h = (rn - gn) / d + 4;
          h *= 60;
          if (h < 0) h += 360;
        }}
        return [h, s, l];
      }};

      const hslToRgb = (h, s, l) => {{
        const c = (1 - Math.abs(2 * l - 1)) * s;
        const x = c * (1 - Math.abs((h / 60) % 2 - 1));
        const m = l - c / 2;
        let rp = 0;
        let gp = 0;
        let bp = 0;
        if (h < 60) {{
          rp = c; gp = x; bp = 0;
        }} else if (h < 120) {{
          rp = x; gp = c; bp = 0;
        }} else if (h < 180) {{
          rp = 0; gp = c; bp = x;
        }} else if (h < 240) {{
          rp = 0; gp = x; bp = c;
        }} else if (h < 300) {{
          rp = x; gp = 0; bp = c;
        }} else {{
          rp = c; gp = 0; bp = x;
        }}
        return [
          Math.round((rp + m) * 255),
          Math.round((gp + m) * 255),
          Math.round((bp + m) * 255),
        ];
      }};

      const normalizeAccent = (rgb, fallback) => {{
        try {{
          let r = Number(rgb?.[0]);
          let g = Number(rgb?.[1]);
          let b = Number(rgb?.[2]);
          if (!Number.isFinite(r) || !Number.isFinite(g) || !Number.isFinite(b)) {{
            return fallback;
          }}
          const [h0, s0, l0] = rgbToHsl(r, g, b);
          const h = h0;
          const s = Math.min(0.82, Math.max(0.36, s0));
          const l = Math.min(0.66, Math.max(0.42, l0));
          [r, g, b] = hslToRgb(h, s, l);
          const lum = 0.2126 * r + 0.7152 * g + 0.0722 * b;
          if (lum < 95) {{
            const gain = 95 / Math.max(1, lum);
            r = Math.min(235, r * gain);
            g = Math.min(235, g * gain);
            b = Math.min(235, b * gain);
          }} else if (lum > 210) {{
            const gain = 210 / lum;
            r = Math.max(30, r * gain);
            g = Math.max(30, g * gain);
            b = Math.max(30, b * gain);
          }}
          return [r, g, b];
        }} catch (_err) {{
          return fallback;
        }}
      }};

      const accentFromImage = (img, fallback) => {{
        try {{
          if (!img || img.naturalWidth <= 0 || img.naturalHeight <= 0) return fallback;
          const c = document.createElement("canvas");
          const maxSide = 42;
          const ratio = Math.min(maxSide / img.naturalWidth, maxSide / img.naturalHeight, 1);
          c.width = Math.max(2, Math.round(img.naturalWidth * ratio));
          c.height = Math.max(2, Math.round(img.naturalHeight * ratio));
          const ctx = c.getContext("2d", {{ willReadFrequently: true }});
          if (!ctx) return fallback;
          ctx.clearRect(0, 0, c.width, c.height);
          ctx.drawImage(img, 0, 0, c.width, c.height);
          const data = ctx.getImageData(0, 0, c.width, c.height).data;
          let total = 0;
          let rs = 0;
          let gs = 0;
          let bs = 0;
          for (let i = 0; i < data.length; i += 4) {{
            const r = data[i];
            const g = data[i + 1];
            const b = data[i + 2];
            const a = data[i + 3];
            if (a < 40) continue;
            const max = Math.max(r, g, b);
            const min = Math.min(r, g, b);
            const sat = max - min;
            if (sat < 20) continue;
            const weight = sat + 10;
            total += weight;
            rs += r * weight;
            gs += g * weight;
            bs += b * weight;
          }}
          if (total <= 0) return fallback;
          const r = rs / total;
          const g = gs / total;
          const b = bs / total;
          return [r, g, b];
        }} catch (_err) {{
          return fallback;
        }}
      }};

      Promise.all([waitImage(homeLogo), waitImage(awayLogo)])
        .then(() => {{
          setPalette("home", normalizeAccent(accentFromImage(homeLogo, fallbackHome), fallbackHome));
          setPalette("away", normalizeAccent(accentFromImage(awayLogo, fallbackAway), fallbackAway));
          setReady();
        }})
        .catch(() => {{
          setPalette("home", fallbackHome);
          setPalette("away", fallbackAway);
          setReady();
        }});
    }})();
  </script>
</body>
</html>"""


def render_headline_image_html(
    text: str,
    width: int = _WIDTH,
    *,
    style_variant: str = _DEFAULT_STYLE_VARIANT,
    home_logo: bytes | None = None,
    away_logo: bytes | None = None,
    league_logo: bytes | None = None,  # kept for API compatibility
    league_label: str | None = None,
    market_label: str | None = None,
    bet_label: str | None = None,
    league_country: str | None = None,
    league_round: str | None = None,
    venue_name: str | None = None,
    venue_city: str | None = None,
    home_rank: int | None = None,
    away_rank: int | None = None,
    home_points: int | None = None,
    away_points: int | None = None,
    home_played: int | None = None,
    away_played: int | None = None,
    home_goal_diff: int | None = None,
    away_goal_diff: int | None = None,
    home_form: str | None = None,
    away_form: str | None = None,
    home_win_prob: float | None = None,
    draw_prob: float | None = None,
    away_win_prob: float | None = None,
    signal_title: str | None = None,
    signal_line_1: str | None = None,
    signal_line_2: str | None = None,
    signal_line_3: str | None = None,
) -> bytes:
    data = _parse_card_data(
        text,
        league_label=league_label,
        market_label=market_label,
        bet_label=bet_label,
    )
    home_default_color = (78, 134, 255)
    away_default_color = (255, 94, 128)
    home_fallback = _fallback_logo_svg(_initial_letter(data.home_team, "H"), _rgb_to_hex(home_default_color))
    away_fallback = _fallback_logo_svg(_initial_letter(data.away_team, "A"), _rgb_to_hex(away_default_color))
    league_fallback = _fallback_logo_svg(_initial_letter(data.league, "L"), "#3f5b95")
    home_logo_uri = _bytes_to_data_uri(home_logo, home_fallback)
    away_logo_uri = _bytes_to_data_uri(away_logo, away_fallback)
    league_logo_uri = _bytes_to_data_uri(league_logo, league_fallback)
    html_doc = _build_html(
        data,
        width=max(800, int(width)),
        style_variant=_normalize_style_variant(style_variant),
        home_logo_uri=home_logo_uri,
        away_logo_uri=away_logo_uri,
        league_logo_uri=league_logo_uri,
        league_country=league_country,
        league_round=league_round,
        venue_name=venue_name,
        venue_city=venue_city,
        home_rank=home_rank,
        away_rank=away_rank,
        home_points=home_points,
        away_points=away_points,
        home_played=home_played,
        away_played=away_played,
        home_goal_diff=home_goal_diff,
        away_goal_diff=away_goal_diff,
        home_form=home_form,
        away_form=away_form,
        home_win_prob=home_win_prob,
        draw_prob=draw_prob,
        away_win_prob=away_win_prob,
        signal_title=signal_title,
        signal_line_1=signal_line_1,
        signal_line_2=signal_line_2,
        signal_line_3=signal_line_3,
    )

    browser = _ensure_browser()
    context = browser.new_context(
        viewport={"width": max(800, int(width)), "height": _DEFAULT_VIEWPORT_H},
        device_scale_factor=2,
        color_scheme="dark",
    )
    page = context.new_page()
    try:
        page.set_content(html_doc, wait_until="domcontentloaded")
        try:
            page.wait_for_function(
                "() => document.getElementById('post-canvas')?.dataset.paletteReady === '1'",
                timeout=2500,
            )
        except Exception:
            pass
        element = page.locator("#post-canvas")
        box = element.bounding_box()
        if box and box.get("height", 0) > _DEFAULT_VIEWPORT_H:
            h = min(_MAX_VIEWPORT_H, int(box["height"]) + 48)
            page.set_viewport_size({"width": max(800, int(width)), "height": h})
        png = element.screenshot(type="png")
        return png
    finally:
        context.close()
