from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logger import get_logger
from app.data.providers.groq import generate_text

log = get_logger("services.ai_enrich")

_LANG_NAMES = {
    "en": "English",
    "uk": "Ukrainian",
    "fr": "French",
    "de": "German",
    "pl": "Polish",
    "pt": "Portuguese",
    "es": "Spanish",
}

# ---------------------------------------------------------------------------
# System prompt: GENERATE analysis from raw data (not rephrase a template)
# ---------------------------------------------------------------------------
_GENERATE_SYSTEM = (
    "Ты — аналитик футбольных ставок. Пишешь для Telegram-канала.\n\n"
    "Получишь данные матча в JSON. Напиши краткий анализ НА РУССКОМ.\n\n"
    "ФОРМАТ (соблюдай ТОЧНО — ровно 4 блока, без лишних секций):\n"
    "📊 <b>Заголовок: Команда1 — Команда2</b>\n"
    "2-3 связных предложения прозой: почему ставка имеет смысл. "
    "Вплети 2-3 ключевые цифры в текст естественно.\n\n"
    "📈 Одна строка: модель vs букмекер, перевес, коэффициент.\n\n"
    "💡 Вывод — одно предложение.\n\n"
    "⚠️ Прогноз носит аналитический характер.\n\n"
    "СТРОГИЕ ПРАВИЛА:\n"
    "- Пиши ПРОЗОЙ — связные предложения, НЕ списки и НЕ пункты\n"
    "- ЗАПРЕЩЕНЫ подзаголовки вроде 'ФОРМА', 'КЛАСС', 'ФАКТОРЫ', 'ДИНАМИКА'\n"
    "- ЗАПРЕЩЕНЫ маркированные списки (•, -, ▸, ▪ и т.д.)\n"
    "- Только 4 эмодзи: 📊 📈 💡 ⚠️ — больше НИКАКИХ\n"
    "- Числа из JSON переноси ТОЧНО — не округляй, не выдумывай\n"
    "- НЕ добавляй информацию, которой нет в данных\n"
    "- Используй <b> </b> для выделения (HTML), НЕ markdown **\n"
    "- Общая длина: 400-800 символов\n"
    "- Пиши как спортивный журналист — живо, ёмко, без воды\n"
    "- ВЕСЬ текст НА РУССКОМ — никаких английских слов\n"
    "  (названия команд оставляй на оригинальном языке)\n"
    "- Ставку называй 'Тотал Меньше/Больше X.X', не 'UNDER/OVER'\n"
    "- Выводи ТОЛЬКО текст анализа, без преамбул и комментариев"
)

# ---------------------------------------------------------------------------
# Fallback: old rephrase system (kept for compatibility)
# ---------------------------------------------------------------------------
_ENRICH_SYSTEM = (
    "Ты - аналитик футбольных ставок. Пиши кратко, по делу, на русском языке.\n"
    "Ты получишь контекст матча и шаблонный текст анализа.\n"
    "Твоя задача: перефразировать текст, сделав его более живым и уникальным.\n\n"
    "Правила:\n"
    "- СОХРАНИ все числа, проценты, коэффициенты и имена команд ТОЧНО\n"
    "- СОХРАНИ эмодзи-маркеры секций и HTML-теги (<b>, </b>) как есть\n"
    "- НЕ добавляй новую информацию, которой нет в данных\n"
    "- НЕ меняй смысл или тональность\n"
    "- Перефразируй только описательные части, оставив данные нетронутыми\n"
    "- Ответ должен содержать ТОЛЬКО перефразированный текст, без пояснений"
)

_TRANSLATE_SYSTEM = (
    "You are a professional sports translator.\n"
    "Translate the following text to {lang_name}.\n\n"
    "Rules:\n"
    "- Preserve ALL HTML tags (<b>, </b>) exactly as they are\n"
    "- Preserve ALL emoji characters exactly as they are\n"
    "- Preserve ALL numbers, percentages, and odds exactly as they are\n"
    "- Preserve team names in their standard form for the target language\n"
    "- Keep the same structure and line breaks\n"
    "- Output ONLY the translated text, no explanations"
)


# ---------------------------------------------------------------------------
# Post-processing: clean up AI output
# ---------------------------------------------------------------------------

# Common English words that Groq sometimes leaks into Russian text
_EN_RU_REPLACEMENTS: dict[str, str] = {
    "solidity": "надёжность",
    "solid": "надёжную",
    "defense": "оборону",
    "attack": "атаку",
    "performance": "игру",
    "matches": "матчей",
    "goals": "голов",
    "strong": "сильную",
    "weak": "слабую",
    "home advantage": "домашнее преимущество",
    "away form": "гостевую форму",
    "value bet": "ценную ставку",
    "edge": "перевес",
    "bookmaker": "букмекер",
    "disclaimer": "дисклеймер",
    "recommendation": "рекомендация",
    "under": "меньше",
    "over": "больше",
    "total": "тотал",
    "however": "однако",
    "therefore": "следовательно",
    "additionally": "кроме того",
    "furthermore": "более того",
    "meanwhile": "тем временем",
    "overall": "в целом",
    "significant": "значительный",
    "consistently": "стабильно",
    "impressive": "впечатляющую",
    "notably": "заметно",
    "relatively": "относительно",
}


def _fix_markdown_to_html(text: str) -> str:
    """Convert markdown bold (**x**) to HTML bold (<b>x</b>)."""
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"__(.+?)__", r"<b>\1</b>", text)
    # Fix *italic* that shouldn't be there
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"\1", text)
    return text


def _fix_html_tags(text: str) -> str:
    """Ensure HTML bold tags are balanced."""
    # Remove nested <b><b>...</b></b>
    text = re.sub(r"<b>\s*<b>", "<b>", text)
    text = re.sub(r"</b>\s*</b>", "</b>", text)

    # Count open/close
    opens = len(re.findall(r"<b>", text))
    closes = len(re.findall(r"</b>", text))

    if opens > closes:
        # Add missing </b> at end of lines with unclosed <b>
        lines = text.split("\n")
        result = []
        for line in lines:
            line_opens = len(re.findall(r"<b>", line))
            line_closes = len(re.findall(r"</b>", line))
            if line_opens > line_closes:
                line += "</b>" * (line_opens - line_closes)
            result.append(line)
        text = "\n".join(result)
    elif closes > opens:
        # Remove orphan </b>
        diff = closes - opens
        for _ in range(diff):
            text = re.sub(r"</b>", "", text, count=1)

    return text


def _strip_code_artifacts(text: str) -> str:
    """Remove code fences and other LLM artifacts."""
    # Remove ```json ... ``` or ``` ... ```
    text = re.sub(r"```\w*\s*", "", text)
    text = re.sub(r"```", "", text)
    # Remove "Вот анализ:", "Вот текст:", "Конечно,"  etc.
    text = re.sub(
        r"^(Вот\s+(анализ|текст|перефразированный|мой)[^:\n]*:\s*\n?|Конечно[,!]?\s*\n?)",
        "",
        text,
        flags=re.IGNORECASE,
    )
    return text.strip()


def _clean_whitespace(text: str) -> str:
    """Normalize whitespace and blank lines."""
    # Multiple blank lines → one
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Trailing spaces per line
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return text.strip()


def _replace_english_words(text: str, team_names: list[str]) -> str:
    """Replace common English words that leaked into Russian text.

    Skips words that are part of team names or inside <b> tags for names.
    """
    # Build a set of words from team names (case-insensitive)
    team_words: set[str] = set()
    for name in team_names:
        for word in name.split():
            team_words.add(word.lower())

    for en_word, ru_word in _EN_RU_REPLACEMENTS.items():
        # Skip if this word is part of a team name
        if en_word.lower() in team_words:
            continue
        # Case-insensitive word boundary replacement
        pattern = re.compile(r"\b" + re.escape(en_word) + r"\b", re.IGNORECASE)
        text = pattern.sub(ru_word, text)

    return text


def _detect_excessive_latin(text: str, team_names: list[str]) -> float:
    """Return ratio of unexpected Latin words (excluding team names, tags, numbers).

    Returns 0.0-1.0 where higher means more Latin contamination.
    """
    # Strip HTML tags
    clean = re.sub(r"<[^>]+>", "", text)
    # Extract all "words" (alpha sequences)
    words = re.findall(r"[a-zA-Zа-яА-ЯёЁіІїЇєЄґҐ]+", clean)
    if not words:
        return 0.0

    # Build team word set
    team_words: set[str] = set()
    for name in team_names:
        for w in name.split():
            team_words.add(w.lower())
    # Common allowed Latin words in Russian football text
    allowed = {
        "vs", "value", "under", "over", "total", "utc",
        "html", "http", "https", "www", "b",
    }
    allowed.update(team_words)

    latin_count = 0
    cyrillic_count = 0
    for word in words:
        if word.lower() in allowed:
            continue
        is_latin = all("LATIN" in unicodedata.name(c, "") for c in word)
        is_cyrillic = any("CYRILLIC" in unicodedata.name(c, "") for c in word)
        if is_latin:
            latin_count += 1
        elif is_cyrillic:
            cyrillic_count += 1

    total_meaningful = latin_count + cyrillic_count
    if total_meaningful == 0:
        return 0.0
    return latin_count / total_meaningful


def _strip_section_headers(text: str) -> str:
    """Remove template-like section headers that make text look robotic.

    E.g. lines like: '⚡ ТЕКУЩАЯ ФОРМА (последние 5 матчей)'
         or:          '🔍 КЛЮЧЕВЫЕ ФАКТОРЫ'
         or:          '<b>ТЕКУЩАЯ ДИНАМИКА</b>'
    """
    # Patterns for section headers — lines that are ALL CAPS Russian + optional
    # parens, or bold-wrapped ALL CAPS headers
    _header_patterns = [
        # ⚡ ЗАГОЛОВОК or ⚡ ЗАГОЛОВОК (пояснение)
        re.compile(
            r"^[^\S\n]*(?:[\U0001F300-\U0001FAFF\u2600-\u27BF\u2700-\u27BF][\s]*)"
            r"(?:<b>)?[А-ЯЁ\s/]{4,}(?:\([^)]*\))?(?:</b>)?[^\S\n]*$",
            re.MULTILINE,
        ),
        # <b>ЗАГОЛОВОК</b> (standalone bold all-caps line)
        re.compile(
            r"^[^\S\n]*<b>[А-ЯЁ\s/]{4,}</b>[^\S\n]*$",
            re.MULTILINE,
        ),
    ]
    for pat in _header_patterns:
        text = pat.sub("", text)
    return text


def _postprocess_russian(text: str, team_names: list[str]) -> tuple[str, bool]:
    """Post-process AI-generated Russian text.

    Returns (cleaned_text, is_valid).
    is_valid=False means too much Latin contamination → should fallback.
    """
    text = _strip_code_artifacts(text)
    text = _strip_section_headers(text)
    text = _fix_markdown_to_html(text)
    text = _fix_html_tags(text)
    text = _replace_english_words(text, team_names)
    text = _clean_whitespace(text)

    # Check Latin contamination after cleanup
    latin_ratio = _detect_excessive_latin(text, team_names)
    if latin_ratio > 0.15:
        log.warning(
            "postprocess_too_much_latin ratio=%.2f team_names=%s",
            latin_ratio, team_names,
        )
        return text, False

    return text, True


def _postprocess_translation(text: str) -> str:
    """Post-process translated text."""
    text = _strip_code_artifacts(text)
    text = _fix_markdown_to_html(text)
    text = _fix_html_tags(text)
    text = _clean_whitespace(text)
    return text


# ---------------------------------------------------------------------------
# Build structured match data for AI generation
# ---------------------------------------------------------------------------
def _safe_float(val: Any, fmt: str = ".2f") -> str | None:
    if val is None:
        return None
    try:
        return format(float(val), fmt)
    except (TypeError, ValueError):
        return None


def _build_match_data(
    fixture: Any,
    pred: Any,
    indices: Any,
    market: str,
) -> dict:
    """Build structured JSON data for AI to generate analysis."""
    home = str(getattr(fixture, "home_name", "Home") or "Home")
    away = str(getattr(fixture, "away_name", "Away") or "Away")
    league = str(getattr(fixture, "league_name", "") or "")

    sel = getattr(pred, "selection_code", None) or getattr(pred, "selection", None)
    odd = getattr(pred, "initial_odd", None)
    prob = getattr(pred, "confidence", None)

    # Calculate derived values
    implied = (1.0 / float(odd)) if odd and float(odd) > 0 else None
    edge = (float(prob) - implied) if prob is not None and implied is not None else None
    ev = (float(prob) * float(odd) - 1) if prob is not None and odd is not None else None
    fair_odd = (1.0 / float(prob)) if prob and float(prob) > 0 else None

    data: dict[str, Any] = {
        "match": f"{home} vs {away}",
        "home": home,
        "away": away,
        "league": league,
        "market": market,
        "selection": str(sel) if sel else None,
        "odd": _safe_float(odd, ".2f"),
        "model_prob_pct": _safe_float(float(prob) * 100, ".1f") if prob else None,
        "bookmaker_prob_pct": _safe_float(implied * 100, ".1f") if implied else None,
        "edge_pct": _safe_float(edge * 100, "+.1f") if edge else None,
        "ev_pct": _safe_float(ev * 100, "+.1f") if ev else None,
        "fair_odd": _safe_float(fair_odd, ".2f") if fair_odd else None,
    }

    # Form (last 5 matches)
    if indices:
        form = {}
        hff = _safe_float(getattr(indices, "home_form_for", None))
        hfa = _safe_float(getattr(indices, "home_form_against", None))
        aff = _safe_float(getattr(indices, "away_form_for", None))
        afa = _safe_float(getattr(indices, "away_form_against", None))
        if hff and hfa:
            form["home_last5"] = {"goals_for": hff, "goals_against": hfa}
        if aff and afa:
            form["away_last5"] = {"goals_for": aff, "goals_against": afa}
        if form:
            data["form_last5"] = form

        # Class (15 matches)
        cls = {}
        hcf = _safe_float(getattr(indices, "home_class_for", None))
        hca = _safe_float(getattr(indices, "home_class_against", None))
        acf = _safe_float(getattr(indices, "away_class_for", None))
        aca = _safe_float(getattr(indices, "away_class_against", None))
        if hcf and hca:
            cls["home_15m"] = {"goals_for": hcf, "goals_against": hca}
        if acf and aca:
            cls["away_15m"] = {"goals_for": acf, "goals_against": aca}
        if cls:
            data["class_15matches"] = cls

        # Venue stats
        venue = {}
        hvf = _safe_float(getattr(indices, "home_venue_for", None))
        hva = _safe_float(getattr(indices, "home_venue_against", None))
        avf = _safe_float(getattr(indices, "away_venue_for", None))
        ava = _safe_float(getattr(indices, "away_venue_against", None))
        if hvf and hva:
            venue["home_at_home"] = {"goals_for": hvf, "goals_against": hva}
        if avf and ava:
            venue["away_away"] = {"goals_for": avf, "goals_against": ava}
        if venue:
            data["venue_stats"] = venue

        # Rest hours
        hr = getattr(indices, "home_rest_hours", None)
        ar = getattr(indices, "away_rest_hours", None)
        if hr is not None and ar is not None:
            data["rest_hours"] = {"home": int(hr), "away": int(ar)}

    # Remove None values for cleaner JSON
    return {k: v for k, v in data.items() if v is not None}


def _build_match_context(
    fixture: Any,
    pred: Any,
    indices: Any,
    market: str,
) -> str:
    """Legacy context builder (kept for fallback rephrase mode)."""
    home = str(getattr(fixture, "home_name", "Home") or "Home")
    away = str(getattr(fixture, "away_name", "Away") or "Away")
    league = str(getattr(fixture, "league_name", "") or "")

    lines = [
        f"Матч: {home} vs {away}",
        f"Лига: {league}",
        f"Рынок: {market}",
    ]

    sel = getattr(pred, "selection_code", None) or getattr(pred, "selection", None)
    if sel:
        lines.append(f"Выбор: {sel}")
    odd = getattr(pred, "initial_odd", None)
    prob = getattr(pred, "confidence", None)
    if odd is not None:
        lines.append(f"Коэффициент: {odd}")
    if prob is not None:
        lines.append(f"Вероятность модели: {float(prob) * 100:.1f}%")

    if indices:
        stats = []
        for attr, label in [
            ("home_form_for", f"{home} форма забитых"),
            ("home_form_against", f"{home} форма пропущенных"),
            ("away_form_for", f"{away} форма забитых"),
            ("away_form_against", f"{away} форма пропущенных"),
        ]:
            val = getattr(indices, attr, None)
            if val is not None:
                stats.append(f"{label}: {val:.2f}")

        hr = getattr(indices, "home_rest_hours", None)
        ar = getattr(indices, "away_rest_hours", None)
        if hr is not None and ar is not None:
            stats.append(f"Отдых: {home} {hr}ч, {away} {ar}ч")

        if stats:
            lines.append("\nСтатистика:")
            lines.extend(stats)

    return "\n".join(lines)


async def enrich_analysis(
    session: AsyncSession,
    template_analysis: str,
    fixture: Any,
    pred: Any,
    indices: Any,
    market: str,
) -> str:
    """Generate analysis text from raw data using AI.

    Primary mode: Groq generates original text from structured JSON data.
    Fallback: returns original template_analysis on any failure.
    """
    if not settings.groq_enabled or not settings.groq_api_key:
        return template_analysis

    try:
        # Build structured data for AI generation
        match_data = _build_match_data(fixture, pred, indices, market)
        prompt = (
            "Данные матча:\n"
            f"```json\n{json.dumps(match_data, ensure_ascii=False, indent=2)}\n```\n\n"
            "Напиши анализ этого матча по формату из инструкции."
        )
        result = await generate_text(
            session,
            prompt,
            system_prompt=_GENERATE_SYSTEM,
            temperature=0.7,
            max_tokens=1024,
            use_cache=False,  # Each analysis should be unique
        )

        # Validate: must be 200-2000 chars, must contain some key numbers
        home = str(getattr(fixture, "home_name", "") or "")
        away = str(getattr(fixture, "away_name", "") or "")
        team_names = [n for n in [home, away] if n]

        if result and 200 <= len(result) <= 2000:
            # Post-process: fix markdown, HTML, English leaks
            result, is_valid = _postprocess_russian(result, team_names)

            if not is_valid:
                log.warning(
                    "ai_generate_latin_contamination fixture=%s market=%s len=%d",
                    getattr(fixture, "id", "?"), market, len(result),
                )
                # Fall through to fallback
            else:
                # Sanity check: result should mention at least one team name
                has_team = (
                    (home and (home.lower() in result.lower() or home.split()[-1].lower() in result.lower()))
                    or (away and (away.lower() in result.lower() or away.split()[-1].lower() in result.lower()))
                )
                if has_team:
                    log.info(
                        "ai_generate_ok fixture=%s market=%s len=%d",
                        getattr(fixture, "id", "?"), market, len(result),
                    )
                    return result

                log.warning(
                    "ai_generate_no_team_name fixture=%s market=%s len=%d",
                    getattr(fixture, "id", "?"), market, len(result),
                )
        else:
            log.warning(
                "ai_generate_length_bad fixture=%s market=%s len=%d",
                getattr(fixture, "id", "?"), market,
                len(result) if result else 0,
            )

        # Fallback: try legacy rephrase mode
        return await _fallback_rephrase(session, template_analysis, fixture, pred, indices, market)

    except Exception:
        log.exception("ai_generate_failed fixture=%s market=%s", getattr(fixture, "id", "?"), market)
        return template_analysis


async def _fallback_rephrase(
    session: AsyncSession,
    template_analysis: str,
    fixture: Any,
    pred: Any,
    indices: Any,
    market: str,
) -> str:
    """Legacy rephrase mode as fallback."""
    try:
        context = _build_match_context(fixture, pred, indices, market)
        prompt = (
            f"Контекст матча:\n{context}\n\n"
            f"Исходный текст анализа:\n{template_analysis}\n\n"
            "Перефразируй текст анализа, сохраняя все данные, числа и структуру."
        )
        result = await generate_text(
            session,
            prompt,
            system_prompt=_ENRICH_SYSTEM,
            temperature=0.7,
            max_tokens=1024,
        )
        if result and 0.5 * len(template_analysis) <= len(result) <= 2.0 * len(template_analysis):
            log.info(
                "ai_rephrase_fallback_ok fixture=%s market=%s len_in=%d len_out=%d",
                getattr(fixture, "id", "?"), market,
                len(template_analysis), len(result),
            )
            return result
    except Exception:
        log.exception("ai_rephrase_fallback_failed fixture=%s market=%s", getattr(fixture, "id", "?"), market)

    return template_analysis


async def translate_text(
    session: AsyncSession,
    text: str,
    target_lang: str,
    source_lang: str = "ru",
) -> str:
    """Translate text to target language via Groq.

    Returns translated text, or original text on any failure.
    """
    if not text:
        return text
    if target_lang.lower() == source_lang.lower():
        return text
    if not settings.groq_enabled or not settings.groq_api_key:
        return text

    lang_name = _LANG_NAMES.get(target_lang.lower(), target_lang)
    system_prompt = _TRANSLATE_SYSTEM.format(lang_name=lang_name)

    try:
        result = await generate_text(
            session,
            text,
            system_prompt=system_prompt,
            temperature=0.3,
            max_tokens=1536,
        )
        if result:
            result = _postprocess_translation(result)
            log.info(
                "ai_translate_ok lang=%s len_in=%d len_out=%d",
                target_lang, len(text), len(result),
            )
            return result
        log.warning("ai_translate_empty lang=%s", target_lang)
        return text
    except Exception:
        log.exception("ai_translate_failed lang=%s", target_lang)
        return text
