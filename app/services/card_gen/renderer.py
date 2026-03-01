"""Card Gen v2 — Orchestrator.

Wires together: models → palette → assets → fonts → Jinja2 → browser → optimizer.

Usage::

    from app.services.card_gen.renderer import render_card

    jpeg = await render_card(PredictionCardData(...))
"""

from __future__ import annotations

import html as html_mod
import logging
import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .models import PredictionCardData, ResultCardData

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Jinja2 environment (singleton)
# ---------------------------------------------------------------------------
_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

_jinja_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "j2"]),
)

# ---------------------------------------------------------------------------
# Constants — ported from html_image.py _build_html
# ---------------------------------------------------------------------------
_DEFAULT_STYLE_VARIANT = "pro"
_STYLE_VARIANTS = {"pro", "viral"}
_DISCLAIMER = (
    "DISCLAIMER: This is an analytical prediction, not a guarantee of results. "
    "The model formulas are proprietary and not disclosed."
)

# ---------------------------------------------------------------------------
# Signal delta rendering — ported from _render_signal_metric_line
# ---------------------------------------------------------------------------
_DELTA_RE = re.compile(r"\(([^()]*(?:\+|-)\d+(?:[.,]\d+)?%\s*[^()]*)\)\s*$")


def _render_signal_line(line: str) -> str:
    """Render a signal metric line with optional delta highlighting.

    Detects patterns like ``(+4.1%)`` at the end and wraps in
    ``<span class='signal-delta'>``.  Ported from ``_render_signal_metric_line``.
    """
    text = line.strip()
    m = _DELTA_RE.search(text)
    if not m:
        return html_mod.escape(text)
    base = text[: m.start()].rstrip()
    delta = m.group(1).strip()
    if not base:
        return f"<span class='signal-delta'>({html_mod.escape(delta)})</span>"
    return (
        f"{html_mod.escape(base)} "
        f"<span class='signal-delta'>({html_mod.escape(delta)})</span>"
    )


# ---------------------------------------------------------------------------
# Probability normalisation — ported from _normalize_probability
# ---------------------------------------------------------------------------

def _normalize_probability(value: float | int | None) -> float | None:
    """Normalise a probability value to 0.0–1.0 range."""
    if value is None:
        return None
    try:
        num = float(value)
    except Exception:
        return None
    if num < 0:
        return None
    if 1.0 < num <= 100.0:
        num /= 100.0
    if num > 1.0:
        return None
    return max(0.0, min(1.0, num))


# ---------------------------------------------------------------------------
# Stat formatting helpers
# ---------------------------------------------------------------------------

def _rank(v: int | None) -> str:
    return f"#{int(v)}" if v is not None else "#-"


def _pts(v: int | None) -> str:
    return f"{int(v)} pts" if v is not None else "\u2014 pts"


def _played(v: int | None) -> str:
    return f"P {int(v)}" if v is not None else "P \u2014"


def _gd(v: int | None) -> str:
    return f"GD {int(v):+d}" if v is not None else "GD \u2014"


# ---------------------------------------------------------------------------
# Common context builder — shared by prediction and result cards
# ---------------------------------------------------------------------------

def _common_context(card: PredictionCardData | ResultCardData) -> dict:
    """Build context variables shared between prediction and result cards."""
    from .assets import (
        make_fallback_logo,
        prepare_league_logo,
        prepare_logo,
    )
    from .fonts import (
        compute_team_font_size,
        fit_font_size,
        get_fonts_css,
        normalize_form,
        title_color,
    )
    from .palette import (
        FALLBACK_AWAY,
        FALLBACK_HOME,
        extract_team_color,
    )

    width = max(800, int(card.width))
    theme_name = card.theme.strip().lower()
    if theme_name not in _STYLE_VARIANTS:
        theme_name = _DEFAULT_STYLE_VARIANT

    # ── Palette ───────────────────────────────────────────────
    home_id = hash(card.home.name) & 0x7FFFFFFF
    away_id = hash(card.away.name) & 0x7FFFFFFF
    home_color = extract_team_color(
        home_id, card.home.logo_bytes, fallback=FALLBACK_HOME,
    )
    away_color = extract_team_color(
        away_id, card.away.logo_bytes, fallback=FALLBACK_AWAY,
    )
    home_rgb = f"{home_color[0]}, {home_color[1]}, {home_color[2]}"
    away_rgb = f"{away_color[0]}, {away_color[1]}, {away_color[2]}"

    # ── Logos ─────────────────────────────────────────────────
    home_fallback = make_fallback_logo(card.home.name, home_color)
    away_fallback = make_fallback_logo(card.away.name, away_color)
    league_fallback = make_fallback_logo(card.league or "L", (63, 91, 149))

    home_logo_uri = prepare_logo(card.home.logo_bytes) or home_fallback
    away_logo_uri = prepare_logo(card.away.logo_bytes) or away_fallback
    league_logo_uri = prepare_league_logo(card.league_logo_bytes) or league_fallback

    # ── Fonts CSS ─────────────────────────────────────────────
    fonts_css = get_fonts_css()

    # ── Title color ───────────────────────────────────────────
    title_text = getattr(card, "title", None)
    tc = title_color(title_text)

    # ── League / meta ─────────────────────────────────────────
    league_display = html_mod.escape((card.league or "League").strip())
    date_display = html_mod.escape((card.date_line or "TBA").strip())

    meta_parts: list[str] = []
    if card.league_country and card.league_country.strip():
        meta_parts.append(card.league_country.strip())
    if card.league_round and card.league_round.strip():
        meta_parts.append(card.league_round.strip())
    meta_secondary = html_mod.escape(" \u2022 ".join(meta_parts)) if meta_parts else ""

    # ── Team names + font sizes ───────────────────────────────
    home_name = html_mod.escape(card.home.name)
    away_name = html_mod.escape(card.away.name)

    match_inner_width = max(820, width - 108)
    stage_inner = max(520, match_inner_width - 52)
    side_col_width = max(220, int((stage_inner - 220 - 32) / 2))
    team_name_space = max(180, side_col_width - 18)

    home_max = compute_team_font_size(card.home.name, card.home.name)
    away_max = compute_team_font_size(card.away.name, card.away.name)
    home_fit = fit_font_size(card.home.name, team_name_space, min_px=24, max_px=home_max)
    away_fit = fit_font_size(card.away.name, team_name_space, min_px=24, max_px=away_max)
    team_font_size = min(home_fit, away_fit)

    # ── Form ──────────────────────────────────────────────────
    home_form = normalize_form(card.home.form)
    away_form = normalize_form(card.away.form)

    # ── Venue ─────────────────────────────────────────────────
    venue_parts: list[str] = []
    vn = (card.venue_name or "").strip()
    vc = (card.venue_city or "").strip()
    if vn:
        venue_parts.append(vn)
    if vc and vc.lower() != vn.lower():
        venue_parts.append(vc)
    venue_label = html_mod.escape(" \u2022 ".join(venue_parts)) if venue_parts else ""

    disclaimer = html_mod.escape(_DISCLAIMER)

    return {
        "fonts_css": fonts_css,
        "theme_name": theme_name,
        "width": width,
        "title_color": tc,
        "home_rgb": home_rgb,
        "away_rgb": away_rgb,
        "home_logo_uri": home_logo_uri,
        "away_logo_uri": away_logo_uri,
        "league_logo_uri": league_logo_uri,
        "league_display": league_display,
        "date_display": date_display,
        "meta_secondary": meta_secondary,
        "home_name": home_name,
        "away_name": away_name,
        "team_font_size": team_font_size,
        "home_rank": _rank(card.home.rank),
        "away_rank": _rank(card.away.rank),
        "home_points": _pts(card.home.points),
        "away_points": _pts(card.away.points),
        "home_played": _played(card.home.played),
        "away_played": _played(card.away.played),
        "home_gd": _gd(card.home.goal_diff),
        "away_gd": _gd(card.away.goal_diff),
        "home_form": home_form,
        "away_form": away_form,
        "disclaimer": disclaimer,
        "venue_label": venue_label,
    }


# ---------------------------------------------------------------------------
# Prediction card context builder
# ---------------------------------------------------------------------------

def _build_context(card: PredictionCardData) -> dict:
    """Build the full Jinja2 template context from a PredictionCardData."""
    from .fonts import (
        compute_odds_font_size,
        compute_signal_font_size,
        fit_font_size,
        odds_display,
    )

    ctx = _common_context(card)
    width = ctx["width"]
    theme_name = ctx["theme_name"]

    # ── Signal block ──────────────────────────────────────────
    kicker_label = (
        "MATCHDAY SIGNAL" if theme_name == "viral" else "AI BETTING SIGNAL"
    )
    signal_rows_raw = [s for s in (card.signal_lines or []) if isinstance(s, str) and s.strip()]
    signal_rows = [_render_signal_line(s) for s in signal_rows_raw]

    card_title = html_mod.escape((card.title or "HOT PREDICTION").strip())

    # Signal title font size — layout-aware computation
    top_inner_width = max(740, width - 108)
    per_top_card_outer = int((top_inner_width - 18) / 2)
    per_top_card_inner = max(240, per_top_card_outer - 28)
    signal_title_space = max(150, per_top_card_inner - 205)

    signal_title_text = (card.signal_title or "VALUE INDICATORS").strip()
    signal_title_display = html_mod.escape(signal_title_text)
    signal_title_font_size = fit_font_size(
        signal_title_text,
        signal_title_space,
        min_px=18,
        max_px=compute_signal_font_size(signal_title_text),
    )

    # ── Odds ──────────────────────────────────────────────────
    odd_str = str(card.odd) if card.odd is not None else "2.00"
    odds_disp = odds_display(odd_str)
    odds_font_size = compute_odds_font_size(odds_disp)

    # ── Chance meter ──────────────────────────────────────────
    has_chance = False
    home_pct = draw_pct = away_pct = 0
    home_share = draw_share = away_share = 0.0

    chance_parts = [
        _normalize_probability(card.home_win_prob),
        _normalize_probability(card.draw_prob),
        _normalize_probability(card.away_win_prob),
    ]
    known_sum = sum(p for p in chance_parts if p is not None)
    missing_count = sum(1 for p in chance_parts if p is None)
    if missing_count and known_sum < 1.0:
        fill = (1.0 - known_sum) / missing_count
        chance_parts = [fill if p is None else p for p in chance_parts]

    if all(p is not None for p in chance_parts):
        total = sum(chance_parts)  # type: ignore[arg-type]
        if total > 0:
            has_chance = True
            home_share = max(0.0, min(1.0, chance_parts[0] / total))  # type: ignore[index]
            draw_share = max(0.0, min(1.0, chance_parts[1] / total))  # type: ignore[index]
            away_share = max(0.0, min(1.0, chance_parts[2] / total))  # type: ignore[index]
            home_pct = int(round(home_share * 100))
            draw_pct = int(round(draw_share * 100))
            away_pct = max(0, 100 - home_pct - draw_pct)

    # ── Pick ──────────────────────────────────────────────────
    pick_display = html_mod.escape(
        (card.pick_display or card.pick or "BET").strip()
    )

    # ── Assemble prediction-specific context ──────────────────
    ctx.update({
        "kicker_label": kicker_label,
        "signal_title_display": signal_title_display,
        "signal_title_font_size": signal_title_font_size,
        "signal_rows": signal_rows,
        "card_title": card_title,
        "odds_display": odds_disp,
        "odds_font_size": odds_font_size,
        "has_chance": has_chance,
        "home_pct": home_pct,
        "draw_pct": draw_pct,
        "away_pct": away_pct,
        "home_share": home_share,
        "draw_share": draw_share,
        "away_share": away_share,
        "pick_display": pick_display,
    })
    return ctx


# ---------------------------------------------------------------------------
# Result card context builder
# ---------------------------------------------------------------------------

def _build_result_context(card: ResultCardData) -> dict:
    """Build the full Jinja2 template context from a ResultCardData."""
    ctx = _common_context(card)

    # ── Result status ─────────────────────────────────────────
    status = card.status.strip().upper()
    is_win = status == "WIN"
    result_status = "win" if is_win else "loss"
    result_status_display = html_mod.escape(
        "\u0412\u042b\u0418\u0413\u0420\u042b\u0428" if is_win else "\u041f\u0420\u041e\u0418\u0413\u0420\u042b\u0428"
    )

    # ── Profit display ────────────────────────────────────────
    profit = card.profit
    if profit >= 0:
        result_profit_display = html_mod.escape(f"+{profit:.2f}u")
    else:
        result_profit_display = html_mod.escape(f"{profit:.2f}u")

    # ── Result main text (combined status + profit) ───────────
    if is_win:
        raw_main = f"\u0412\u042b\u0418\u0413\u0420\u042b\u0428 +{profit:.2f}u"
    else:
        raw_main = f"\u041f\u0420\u041e\u0418\u0413\u0420\u042b\u0428 {profit:.2f}u"
    result_main_text = html_mod.escape(raw_main)

    # ── Original pick info ────────────────────────────────────
    pick_parts: list[str] = []
    pick_text = (card.pick_display or card.pick or "").strip()
    if pick_text:
        pick_parts.append(pick_text)
    if card.odd is not None:
        pick_parts.append(f"@ {card.odd:.2f}")
    original_pick_display = html_mod.escape(" ".join(pick_parts)) if pick_parts else ""

    # ── Score ─────────────────────────────────────────────────
    home_goals = int(card.home_goals)
    away_goals = int(card.away_goals)

    # ── Assemble result-specific context ──────────────────────
    ctx.update({
        "result_status": result_status,
        "result_status_display": result_status_display,
        "result_profit_display": result_profit_display,
        "result_main_text": result_main_text,
        "original_pick_display": original_pick_display,
        "home_goals": home_goals,
        "away_goals": away_goals,
    })
    return ctx


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def render_card(
    card_data: PredictionCardData | ResultCardData,
) -> bytes:
    """Render a card image and return optimised JPEG bytes.

    Pipeline: data → palette → assets → fonts → Jinja2 → browser → optimizer.

    Parameters
    ----------
    card_data:
        A ``PredictionCardData`` or ``ResultCardData`` instance.

    Returns
    -------
    bytes
        JPEG image bytes optimised for Telegram (< 200 KB target).

    Raises
    ------
    RuntimeError
        If the browser screenshot fails.
    """
    from .browser import screenshot
    from .optimizer import optimize_for_telegram

    # Select template
    if isinstance(card_data, ResultCardData):
        template_name = "cards/result.html.j2"
    else:
        template_name = "cards/prediction.html.j2"

    # Build context
    if isinstance(card_data, ResultCardData):
        ctx = _build_result_context(card_data)
    else:
        ctx = _build_context(card_data)

    # Render HTML
    template = _jinja_env.get_template(template_name)
    html_doc = template.render(**ctx)

    # Screenshot
    png_bytes = await screenshot(html_doc, width=ctx["width"])

    # Optimize
    jpeg_bytes = optimize_for_telegram(png_bytes)

    log.info(
        "card rendered: %s theme=%s, png=%d bytes → jpeg=%d bytes",
        card_data.card_type,
        ctx["theme_name"],
        len(png_bytes),
        len(jpeg_bytes),
    )

    return jpeg_bytes
