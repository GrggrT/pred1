from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.http import assets_client, request_with_retries
from app.core.logger import get_logger
from app.data.providers.api_football import get_fixture_by_id, get_standings
from app.data.providers.deepl import translate_html
from app.data.providers.telegram import send_message_parts, send_photo
from app.services.ai_enrich import enrich_analysis, translate_text
try:
    from app.services.html_image import render_headline_image_html
except Exception:  # pragma: no cover - startup must survive optional renderer failures
    render_headline_image_html = None
try:
    from app.services.card_gen import render_card as _card_gen_v2_render
    from app.services.card_gen.compat import build_prediction_card as _build_v2_card
except Exception:  # pragma: no cover
    _card_gen_v2_render = None  # type: ignore[assignment]
    _build_v2_card = None  # type: ignore[assignment]
from app.jobs import quality_report

log = get_logger("services.publishing")

_QUALITY_WARN_BRIER = 0.27
_QUALITY_WARN_LOGLOSS = 0.75
_VALUE_STRONG_PCT = 12.0
_VALUE_GOOD_PCT = 6.0
_VALUE_THIN_PCT = 2.0
_SIGNAL_STRONG_PCT = 70.0
_SIGNAL_MED_PCT = 60.0
_STAT_DIFF_MINOR = 0.25
_STAT_DIFF_MAJOR = 0.45
_LOGO_MAX_BYTES = 2 * 1024 * 1024
_IMAGE_THEMES = {"pro", "viral"}

_logo_cache: dict[str, bytes] = {}


def _normalize_image_theme(value: str | None) -> str:
    theme = (value or "").strip().lower()
    return theme if theme in _IMAGE_THEMES else "pro"

_LANG_MONTHS: dict[str, tuple[str, ...]] = {
    "ru": (
        "января",
        "февраля",
        "марта",
        "апреля",
        "мая",
        "июня",
        "июля",
        "августа",
        "сентября",
        "октября",
        "ноября",
        "декабря",
    ),
    "en": (
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ),
    "uk": (
        "січня",
        "лютого",
        "березня",
        "квітня",
        "травня",
        "червня",
        "липня",
        "серпня",
        "вересня",
        "жовтня",
        "листопада",
        "грудня",
    ),
    "fr": (
        "janvier",
        "février",
        "mars",
        "avril",
        "mai",
        "juin",
        "juillet",
        "août",
        "septembre",
        "octobre",
        "novembre",
        "décembre",
    ),
    "de": (
        "Januar",
        "Februar",
        "März",
        "April",
        "Mai",
        "Juni",
        "Juli",
        "August",
        "September",
        "Oktober",
        "November",
        "Dezember",
    ),
    "pl": (
        "stycznia",
        "lutego",
        "marca",
        "kwietnia",
        "maja",
        "czerwca",
        "lipca",
        "sierpnia",
        "września",
        "października",
        "listopada",
        "grudnia",
    ),
    "pt": (
        "janeiro",
        "fevereiro",
        "março",
        "abril",
        "maio",
        "junho",
        "julho",
        "agosto",
        "setembro",
        "outubro",
        "novembro",
        "dezembro",
    ),
    "es": (
        "enero",
        "febrero",
        "marzo",
        "abril",
        "mayo",
        "junio",
        "julio",
        "agosto",
        "septiembre",
        "octubre",
        "noviembre",
        "diciembre",
    ),
}

_LANG_TEXT: dict[str, dict[str, Any]] = {
    "ru": {
        "hot_prediction": "🔥 ГОРЯЧИЙ ПРОГНОЗ 🔥",
        "prediction_label": {
            "hot": "🔥 ГОРЯЧИЙ ПРОГНОЗ",
            "standard": "✅ СТАНДАРТНЫЙ ПРОГНОЗ",
            "cautious": "⚠️ ОСТОРОЖНЫЙ ПРОГНОЗ",
            "experimental": "🧪 EXPERIMENTAL ПРОГНОЗ",
        },
        "prediction_label_variants": {
            "hot": ["🔥 ГОРЯЧИЙ ПРОГНОЗ", "🔥 ТОП-ПРОГНОЗ", "🔥 СИЛЬНЫЙ ПРОГНОЗ", "🔥 ЯРКИЙ ПРОГНОЗ"],
            "standard": ["✅ СТАНДАРТНЫЙ ПРОГНОЗ", "✅ БАЗОВЫЙ ПРОГНОЗ", "✅ ОСНОВНОЙ ПРОГНОЗ", "✅ СТАБИЛЬНЫЙ ПРОГНОЗ"],
            "cautious": ["⚠️ ОСТОРОЖНЫЙ ПРОГНОЗ", "⚠️ АККУРАТНЫЙ ПРОГНОЗ", "⚠️ СДЕРЖАННЫЙ ПРОГНОЗ", "⚠️ УМЕРЕННЫЙ ПРОГНОЗ"],
            "experimental": [
                "🧪 EXPERIMENTAL ПРОГНОЗ",
                "🧪 ЭКСПЕРИМЕНТАЛЬНЫЙ ПРОГНОЗ",
                "🧪 ПРОГНОЗ-ЭКСПЕРИМЕНТ",
                "🧪 ТЕСТОВЫЙ ПРОГНОЗ",
            ],
        },
        "bet_label_by_tier": {
            "hot": "💰 СТАВКА ДНЯ",
            "standard": "💰 РЕКОМЕНДАЦИЯ",
            "cautious": "⚠️ ОСТОРОЖНАЯ СТАВКА",
            "experimental": "🧪 ЭКСПЕРИМЕНТАЛЬНАЯ СТАВКА",
        },
        "bet_of_day": "💰 СТАВКА ДНЯ",
        "model_probability": "Вероятность модели",
        "why": "📊 ПОЧЕМУ ЭТО ЗАЙДЁТ?",
        "why_variants": [
            "📊 ПОЧЕМУ ЭТО ЗАЙДЁТ?",
            "📊 КЛЮЧЕВЫЕ ФАКТОРЫ",
            "📊 КЛЮЧЕВЫЕ АРГУМЕНТЫ",
            "📊 ОСНОВНЫЕ ФАКТОРЫ",
        ],
        "current_form": "⚡ ТЕКУЩАЯ ФОРМА (последние 5 матчей)",
        "team_class": "🏆 КЛАСС КОМАНД (15 матчей)",
        "home_away_stats": "🏟️ ДОМАШНЯЯ/ГОСТЕВАЯ СТАТИСТИКА",
        "fatigue_factor": "⏰ ФАКТОР УСТАЛОСТИ",
        "value_indicators": "📈 VALUE-БЕТ ИНДИКАТОРЫ",
        "value_variants": ["📈 VALUE-БЕТ ИНДИКАТОРЫ", "📈 VALUE-СИГНАЛЫ", "📈 VALUE-ИНДИКАТОРЫ", "📈 VALUE-ОБЗОР"],
        "risks": "⚠️ РИСКИ",
        "risks_variants": ["⚠️ РИСКИ", "⚠️ ЗАМЕЧАНИЯ", "⚠️ ОГРАНИЧЕНИЯ", "⚠️ РИСК-ФАКТОРЫ"],
        "recommendation": "💡 РЕКОМЕНДАЦИЯ",
        "recommendation_variants": ["💡 РЕКОМЕНДАЦИЯ", "💡 ИТОГ", "💡 РЕЗЮМЕ", "💡 ВЫВОД"],
        "disclaimer": "⚠️ ДИСКЛЕЙМЕР: это аналитический прогноз, а не гарантия результата. "
        "Формулы модели являются проприетарными и не раскрываются.",
        "bookmakers_give": "🎲 Букмекеры дают",
        "our_model": "🤖 Наша модель",
        "signal": "📊 Сигнал модели",
        "signal_variants": ["📊 Сигнал модели", "📊 Сила сигнала", "📊 Интенсивность сигнала", "📊 Уровень сигнала"],
        "signal_notes": {"strong": "сильный", "moderate": "умеренный", "weak": "слабый"},
        "edge_short": "перевес",
        "edge_strong": "🔥 Перевес модели: {pct:.1f}%",
        "edge_good": "✅ Перевес модели: {pct:.1f}%",
        "edge_thin": "⚠️ Перевес модели: {pct:.1f}%",
        "edge_none": "⚪ Перевеса нет ({pct:.1f}%)",
        "edge_strong_variants": [
            "🔥 Перевес модели: {pct:.1f}%",
            "🔥 Сильный перевес модели: {pct:.1f}%",
            "🔥 Явный перевес модели: {pct:.1f}%",
            "🔥 Перевес по модели: {pct:.1f}%",
        ],
        "edge_good_variants": [
            "✅ Перевес модели: {pct:.1f}%",
            "✅ Плюс модели: {pct:.1f}%",
            "✅ Перевес по модели: {pct:.1f}%",
            "✅ Небольшой плюс модели: {pct:.1f}%",
        ],
        "edge_thin_variants": [
            "⚠️ Перевес модели: {pct:.1f}%",
            "⚠️ Небольшой перевес: {pct:.1f}%",
            "⚠️ Слабый перевес: {pct:.1f}%",
            "⚠️ Минимальный перевес: {pct:.1f}%",
        ],
        "edge_none_variants": [
            "⚪ Перевеса нет ({pct:.1f}%)",
            "⚪ Перевеса нет: {pct:.1f}%",
            "⚪ Перевеса почти нет ({pct:.1f}%)",
            "⚪ Существенного перевеса нет ({pct:.1f}%)",
        ],
        "value_profile": "✅ Value-профиль",
        "value_profile_variants": ["✅ Value-профиль", "✅ Профиль value", "✅ Value-оценка", "✅ Оценка value"],
        "value_unknown": "⚠️ Value не рассчитан — используйте осторожность.",
        "value_strength": {
            "strong": "сильный value",
            "good": "хороший value",
            "thin": "тонкий value",
            "edge": "value на грани",
            "neg": "value отрицательный",
            "none": "value не оценён",
        },
        "recommend": {
            "strong": "✅ Ставка выглядит очень привлекательно при коэффициенте {odd}.",
            "good": "✅ Ставка выглядит привлекательно при коэффициенте {odd}.",
            "thin": "⚠️ Value небольшой при коэффициенте {odd} — лучше подтвердить доп. факторами.",
            "edge": "⚠️ Value на грани при коэффициенте {odd}.",
            "neg": "⛔ Value отрицательный при коэффициенте {odd} — лучше пропустить.",
        },
        "recommend_variants": {
            "strong": [
                "✅ Ставка выглядит очень привлекательно при коэффициенте {odd}.",
                "✅ Коэффициент {odd} делает ставку очень привлекательной.",
                "✅ Очень хорошая цена при {odd}.",
                "✅ При {odd} ставка выглядит максимально интересно.",
            ],
            "good": [
                "✅ Ставка выглядит привлекательно при коэффициенте {odd}.",
                "✅ При {odd} ставка выглядит интересно.",
                "✅ При {odd} есть ощутимый value.",
                "✅ Коэффициент {odd} всё ещё выглядит достойно.",
            ],
            "thin": [
                "⚠️ Value небольшой при коэффициенте {odd} — лучше подтвердить доп. факторами.",
                "⚠️ При {odd} value небольшой — лучше подтвердить доп. факторами.",
                "⚠️ Небольшой value при {odd} — лучше подтвердить.",
                "⚠️ При {odd} value тонкий — лучше подтвердить.",
            ],
            "edge": [
                "⚠️ Value на грани при коэффициенте {odd}.",
                "⚠️ При {odd} value на грани.",
                "⚠️ Граничный value при {odd}.",
                "⚠️ При {odd} value почти на нуле.",
            ],
            "neg": [
                "⛔ Value отрицательный при коэффициенте {odd} — лучше пропустить.",
                "⛔ При {odd} value отрицательный — лучше пропустить.",
                "⛔ При {odd} value уходит в минус — лучше пропустить.",
                "⛔ Отрицательный value при {odd} — лучше не брать.",
            ],
        },
        "recommend_cautious": {
            "strong": "⚠️ Потенциал высокий при коэффициенте {odd}, но нужна осторожность.",
            "good": "⚠️ Ставка интересна при коэффициенте {odd} — действуйте осторожно.",
            "thin": "⚠️ Слабый value при коэффициенте {odd} — лучше дождаться подтверждений.",
            "edge": "⚠️ Value на грани при коэффициенте {odd}.",
            "neg": "⛔ Value отрицательный при коэффициенте {odd} — лучше пропустить.",
        },
        "recommend_cautious_variants": {
            "strong": [
                "⚠️ Потенциал высокий при коэффициенте {odd}, но нужна осторожность.",
                "⚠️ При {odd} потенциал высокий, но требуется осторожность.",
                "⚠️ При {odd} потенциал высокий — действуйте аккуратно.",
                "⚠️ Высокий потенциал при {odd}, но аккуратнее.",
            ],
            "good": [
                "⚠️ Ставка интересна при коэффициенте {odd} — действуйте осторожно.",
                "⚠️ При {odd} ставка интересна, но действуйте осторожно.",
                "⚠️ При {odd} ставка выглядит неплохо, но осторожно.",
                "⚠️ Есть интерес при {odd}, но нужна осторожность.",
            ],
            "thin": [
                "⚠️ Слабый value при коэффициенте {odd} — лучше дождаться подтверждений.",
                "⚠️ При {odd} value слабый — лучше дождаться подтверждений.",
                "⚠️ При {odd} value тонкий — лучше дождаться подтверждений.",
                "⚠️ Слабый value при {odd} — лучше подождать.",
            ],
            "edge": [
                "⚠️ Value на грани при коэффициенте {odd}.",
                "⚠️ При {odd} value на грани.",
                "⚠️ Граничный value при {odd}.",
                "⚠️ При {odd} value почти на нуле.",
            ],
            "neg": [
                "⛔ Value отрицательный при коэффициенте {odd} — лучше пропустить.",
                "⛔ При {odd} value отрицательный — лучше пропустить.",
                "⛔ При {odd} value уходит в минус — лучше пропустить.",
                "⛔ Отрицательный value при {odd} — лучше не брать.",
            ],
        },
        "line_watch": "📉 Следите за линией — при коэффициенте ниже {odd} value исчезает.",
        "line_watch_variants": [
            "📉 Следите за линией — при коэффициенте ниже {odd} value исчезает.",
            "📉 Если коэффициент упадёт ниже {odd}, value пропадёт.",
            "📉 При падении ниже {odd} value исчезает.",
            "📉 При {odd} и ниже value исчезает.",
        ],
        "no_risks": "✅ Существенных рисков не выявлено",
        "no_risks_variants": [
            "✅ Существенных рисков не выявлено",
            "✅ Риски выглядят минимальными",
            "✅ Критичных рисков не видно",
            "✅ Риски выглядят низкими",
        ],
        "experimental_prefix": "EXPERIMENTAL — ",
        "attack_similar": "Атака команд сопоставима",
        "attack_slight": "Атака чуть сильнее у {team}",
        "attack_strong": "Атака заметно сильнее у {team}",
        "defense_similar": "Оборона на одном уровне",
        "defense_slight": "Оборона чуть надёжнее у {team}",
        "defense_strong": "Оборона заметно надёжнее у {team}",
        "venue_even": "Дом/гость без явного перекоса",
        "venue_slight_home": "Домашний фактор слегка на стороне {team}",
        "venue_slight_away": "Гостевой фактор у {team} слегка лучше",
        "venue_strong_home": "Домашний фактор на стороне {team}",
        "venue_strong_away": "Гостевой фактор у {team} выглядит лучше",
        "rest_even": "Отдых примерно равный",
        "rest_more": "✅ {team} отдыхал больше: {a}ч vs {b}ч",
        "attack_similar_variants": [
            "Атака команд сопоставима",
            "Атака примерно на одном уровне",
            "Уровень атаки сопоставим",
            "Сила атаки примерно равна",
        ],
        "attack_slight_variants": [
            "Атака чуть сильнее у {team}",
            "Атака с небольшим перевесом у {team}",
            "Небольшой перевес в атаке у {team}",
            "Атака немного лучше у {team}",
        ],
        "attack_strong_variants": [
            "Атака заметно сильнее у {team}",
            "Атака ощутимо сильнее у {team}",
            "Сильный перевес в атаке у {team}",
            "Атака существенно сильнее у {team}",
        ],
        "defense_similar_variants": [
            "Оборона на одном уровне",
            "Обороны сопоставимы",
            "Оборона выглядит ровно",
            "Уровень обороны сопоставим",
        ],
        "defense_slight_variants": [
            "Оборона чуть надёжнее у {team}",
            "Оборона с небольшим перевесом у {team}",
            "Небольшой перевес в обороне у {team}",
            "Оборона немного лучше у {team}",
        ],
        "defense_strong_variants": [
            "Оборона заметно надёжнее у {team}",
            "Оборона ощутимо надёжнее у {team}",
            "Сильный перевес в обороне у {team}",
            "Оборона существенно надёжнее у {team}",
        ],
        "venue_even_variants": [
            "Дом/гость без явного перекоса",
            "Дом/гость без явного преимущества",
            "Дом/гость без заметного перекоса",
            "Дом/гость примерно равны",
        ],
        "venue_slight_home_variants": [
            "Домашний фактор слегка на стороне {team}",
            "Лёгкий перевес дома у {team}",
            "Небольшой домашний плюс у {team}",
            "Лёгкий домашний плюс у {team}",
        ],
        "venue_slight_away_variants": [
            "Гостевой фактор у {team} слегка лучше",
            "Лёгкий перевес в гостях у {team}",
            "Небольшой гостевой плюс у {team}",
            "Лёгкий гостевой плюс у {team}",
        ],
        "venue_strong_home_variants": [
            "Домашний фактор на стороне {team}",
            "Сильный домашний фактор у {team}",
            "Домашний фактор явно у {team}",
            "Домашний фактор заметно у {team}",
        ],
        "venue_strong_away_variants": [
            "Гостевой фактор у {team} выглядит лучше",
            "Сильный гостевой фактор у {team}",
            "Гостевой фактор явно у {team}",
            "Гостевой фактор заметно у {team}",
        ],
        "rest_even_variants": ["Отдых примерно равный", "Баланс отдыха примерно равный", "Отдых сопоставим", "Отдых почти равный"],
        "rest_more_variants": [
            "✅ {team} отдыхал больше: {a}ч vs {b}ч",
            "✅ У {team} больше отдыха: {a}ч vs {b}ч",
            "✅ {team} имел больше отдыха: {a}ч vs {b}ч",
            "✅ У {team} преимущество в отдыхе: {a}ч vs {b}ч",
        ],
        "for": "за",
        "against": "против",
        "home": "дома",
        "away": "в гостях",
        "reason_no_report": "нет отчёта качества",
        "reason_no_summary": "нет сводки качества",
        "reason_low_sample": "малый объём ({bets})",
        "reason_clv_zero": "CLV coverage 0%",
        "reason_clv_low": "CLV coverage низкий ({pct})",
        "reason_brier": "Brier {value}",
        "reason_logloss": "LogLoss {value}",
        "selection_home_win": "Победа {team} (П1)",
        "selection_draw": "Ничья (Х)",
        "selection_away_win": "Победа {team} (П2)",
        "selection_over": "Тотал Больше 2.5",
        "selection_under": "Тотал Меньше 2.5",
        "selection_over_1_5": "Тотал Б 1.5",
        "selection_under_1_5": "Тотал М 1.5",
        "selection_over_3_5": "Тотал Б 3.5",
        "selection_under_3_5": "Тотал М 3.5",
        "selection_btts_yes": "Обе забьют — Да",
        "selection_btts_no": "Обе забьют — Нет",
        "selection_dc_1x": "Двойной шанс 1X",
        "selection_dc_x2": "Двойной шанс X2",
        "selection_dc_12": "Двойной шанс 12",
    },
    "en": {
        "hot_prediction": "🔥 HOT PREDICTION 🔥",
        "prediction_label": {
            "hot": "🔥 HOT PREDICTION",
            "standard": "✅ STANDARD PREDICTION",
            "cautious": "⚠️ CAUTIOUS PREDICTION",
            "experimental": "🧪 EXPERIMENTAL PREDICTION",
        },
        "prediction_label_variants": {
            "hot": ["🔥 HOT PREDICTION", "🔥 TOP PREDICTION", "🔥 STRONG PREDICTION", "🔥 HIGH-CONFIDENCE PICK"],
            "standard": [
                "✅ STANDARD PREDICTION",
                "✅ BASELINE PREDICTION",
                "✅ MAIN PREDICTION",
                "✅ STABLE PREDICTION",
            ],
            "cautious": [
                "⚠️ CAUTIOUS PREDICTION",
                "⚠️ CONSERVATIVE PREDICTION",
                "⚠️ MODERATE PREDICTION",
                "⚠️ CAREFUL PREDICTION",
            ],
            "experimental": [
                "🧪 EXPERIMENTAL PREDICTION",
                "🧪 TEST PREDICTION",
                "🧪 BETA PREDICTION",
                "🧪 TRIAL PREDICTION",
            ],
        },
        "bet_label_by_tier": {
            "hot": "💰 BET OF THE DAY",
            "standard": "💰 RECOMMENDATION",
            "cautious": "⚠️ CAUTIOUS BET",
            "experimental": "🧪 EXPERIMENTAL BET",
        },
        "bet_of_day": "💰 BET OF THE DAY",
        "model_probability": "Model probability",
        "why": "📊 WHY WILL THIS HAPPEN?",
        "why_variants": ["📊 WHY WILL THIS HAPPEN?", "📊 KEY FACTORS", "📊 MAIN DRIVERS", "📊 MAIN FACTORS"],
        "current_form": "⚡ CURRENT FORM (last 5 matches)",
        "team_class": "🏆 TEAM CLASS (15 matches)",
        "home_away_stats": "🏟️ HOME/AWAY STATISTICS",
        "fatigue_factor": "⏰ FATIGUE FACTOR",
        "value_indicators": "📈 VALUE BET INDICATORS",
        "value_variants": ["📈 VALUE BET INDICATORS", "📈 VALUE SIGNALS", "📈 VALUE CHECK", "📈 VALUE OVERVIEW"],
        "risks": "⚠️ RISKS",
        "risks_variants": ["⚠️ RISKS", "⚠️ NOTES", "⚠️ CAUTIONS", "⚠️ CAVEATS"],
        "recommendation": "💡 RECOMMENDATION",
        "recommendation_variants": ["💡 RECOMMENDATION", "💡 SUMMARY", "💡 VERDICT", "💡 TAKEAWAY"],
        "disclaimer": "⚠️ DISCLAIMER: This is an analytical prediction, not a guarantee of results. "
        "The model formulas are proprietary and not disclosed.",
        "bookmakers_give": "🎲 Bookmakers give",
        "our_model": "🤖 Our model",
        "signal": "📊 Model signal",
        "signal_variants": ["📊 Model signal", "📊 Signal strength", "📊 Signal intensity", "📊 Signal level"],
        "signal_notes": {"strong": "strong", "moderate": "moderate", "weak": "weak"},
        "edge_short": "edge",
        "edge_strong": "🔥 Model edge: {pct:.1f}%",
        "edge_good": "✅ Model edge: {pct:.1f}%",
        "edge_thin": "⚠️ Model edge: {pct:.1f}%",
        "edge_none": "⚪ No edge ({pct:.1f}%)",
        "edge_strong_variants": [
            "🔥 Model edge: {pct:.1f}%",
            "🔥 Strong model edge: {pct:.1f}%",
            "🔥 Clear model edge: {pct:.1f}%",
            "🔥 Clear edge for the model: {pct:.1f}%",
        ],
        "edge_good_variants": [
            "✅ Model edge: {pct:.1f}%",
            "✅ Model advantage: {pct:.1f}%",
            "✅ Edge in our favor: {pct:.1f}%",
            "✅ Small model edge: {pct:.1f}%",
        ],
        "edge_thin_variants": [
            "⚠️ Model edge: {pct:.1f}%",
            "⚠️ Thin edge: {pct:.1f}%",
            "⚠️ Small edge: {pct:.1f}%",
            "⚠️ Slight edge: {pct:.1f}%",
        ],
        "edge_none_variants": [
            "⚪ No edge ({pct:.1f}%)",
            "⚪ No clear edge: {pct:.1f}%",
            "⚪ Little to no edge ({pct:.1f}%)",
            "⚪ No material edge ({pct:.1f}%)",
        ],
        "value_profile": "✅ Value profile",
        "value_profile_variants": ["✅ Value profile", "✅ Value summary", "✅ Value outlook", "✅ Value check"],
        "value_unknown": "⚠️ Value not calculated — use caution.",
        "value_strength": {
            "strong": "strong value",
            "good": "good value",
            "thin": "thin value",
            "edge": "borderline value",
            "neg": "negative value",
            "none": "value not assessed",
        },
        "recommend": {
            "strong": "✅ The odds look very attractive at {odd}.",
            "good": "✅ The odds look attractive at {odd}.",
            "thin": "⚠️ Small value at {odd} — consider confirming with extra factors.",
            "edge": "⚠️ Borderline value at {odd}.",
            "neg": "⛔ Negative value at {odd} — better to skip.",
        },
        "recommend_variants": {
            "strong": [
                "✅ The odds look very attractive at {odd}.",
                "✅ At {odd}, the value looks very strong.",
                "✅ {odd} is a very attractive price.",
                "✅ Great price at {odd}.",
            ],
            "good": [
                "✅ The odds look attractive at {odd}.",
                "✅ {odd} still offers decent value.",
                "✅ {odd} looks like a solid price.",
                "✅ {odd} still offers value.",
            ],
            "thin": [
                "⚠️ Small value at {odd} — consider confirming with extra factors.",
                "⚠️ At {odd}, the value is thin — look for extra confirmation.",
                "⚠️ Thin value at {odd} — better to confirm.",
                "⚠️ Limited value at {odd} — confirm first.",
            ],
            "edge": [
                "⚠️ Borderline value at {odd}.",
                "⚠️ At {odd}, value is right on the edge.",
                "⚠️ Edge value at {odd}.",
                "⚠️ {odd} is right on the edge for value.",
            ],
            "neg": [
                "⛔ Negative value at {odd} — better to skip.",
                "⛔ At {odd}, value is negative — better to pass.",
                "⛔ {odd} is negative value — better to pass.",
                "⛔ Value turns negative at {odd} — better to skip.",
            ],
        },
        "recommend_cautious": {
            "strong": "⚠️ Solid value at {odd}, but proceed with caution.",
            "good": "⚠️ Interesting value at {odd} — proceed carefully.",
            "thin": "⚠️ Thin value at {odd} — wait for extra confirmation.",
            "edge": "⚠️ Borderline value at {odd}.",
            "neg": "⛔ Negative value at {odd} — better to skip.",
        },
        "recommend_cautious_variants": {
            "strong": [
                "⚠️ Solid value at {odd}, but proceed with caution.",
                "⚠️ At {odd}, value is solid — stay cautious.",
                "⚠️ Good value at {odd}, but be cautious.",
                "⚠️ Strong price at {odd}, but proceed carefully.",
            ],
            "good": [
                "⚠️ Interesting value at {odd} — proceed carefully.",
                "⚠️ {odd} looks interesting, but stay careful.",
                "⚠️ {odd} is interesting, but stay cautious.",
                "⚠️ There is value at {odd}, but be careful.",
            ],
            "thin": [
                "⚠️ Thin value at {odd} — wait for extra confirmation.",
                "⚠️ At {odd}, the value is thin — best to wait for confirmation.",
                "⚠️ Thin value at {odd} — wait for confirmation.",
                "⚠️ Limited value at {odd} — better to wait.",
            ],
            "edge": [
                "⚠️ Borderline value at {odd}.",
                "⚠️ At {odd}, value is borderline.",
                "⚠️ {odd} is borderline value.",
                "⚠️ Edge value at {odd}.",
            ],
            "neg": [
                "⛔ Negative value at {odd} — better to skip.",
                "⛔ At {odd}, value is negative — better to pass.",
                "⛔ Negative value at {odd} — better to pass.",
                "⛔ {odd} gives negative value — better to skip.",
            ],
        },
        "line_watch": "📉 Watch the line — if odds drop below {odd}, the value disappears.",
        "line_watch_variants": [
            "📉 Watch the line — if odds drop below {odd}, the value disappears.",
            "📉 If odds fall below {odd}, the value disappears.",
            "📉 If odds dip below {odd}, the value disappears.",
            "📉 Below {odd}, the value disappears.",
        ],
        "no_risks": "✅ No material risks identified",
        "no_risks_variants": [
            "✅ No material risks identified",
            "✅ Risks look limited",
            "✅ No critical risks spotted",
            "✅ No major risks seen",
        ],
        "experimental_prefix": "EXPERIMENTAL — ",
        "attack_similar": "Attacks are comparable",
        "attack_slight": "Attack is slightly stronger for {team}",
        "attack_strong": "Attack is noticeably stronger for {team}",
        "defense_similar": "Defenses are on the same level",
        "defense_slight": "Defense is slightly stronger for {team}",
        "defense_strong": "Defense is noticeably stronger for {team}",
        "venue_even": "Home/away without a clear bias",
        "venue_slight_home": "Home factor slightly favors {team}",
        "venue_slight_away": "Away factor slightly favors {team}",
        "venue_strong_home": "Home factor favors {team}",
        "venue_strong_away": "Away factor favors {team}",
        "rest_even": "Rest is roughly equal",
        "rest_more": "✅ {team} rested more: {a}h vs {b}h",
        "attack_similar_variants": [
            "Attacks are comparable",
            "Attacking strength looks similar",
            "Attacking output is similar",
            "Attacking levels look even",
        ],
        "attack_slight_variants": [
            "Attack is slightly stronger for {team}",
            "Attack edges slightly to {team}",
            "Small attacking edge for {team}",
            "Slight attacking edge for {team}",
        ],
        "attack_strong_variants": [
            "Attack is noticeably stronger for {team}",
            "Attack advantage is clear for {team}",
            "Clear attacking edge for {team}",
            "Strong attacking edge for {team}",
        ],
        "defense_similar_variants": [
            "Defenses are on the same level",
            "Defensive strength looks similar",
            "Defensive output is similar",
            "Defensive levels look even",
        ],
        "defense_slight_variants": [
            "Defense is slightly stronger for {team}",
            "Defense edges slightly to {team}",
            "Small defensive edge for {team}",
            "Slight defensive edge for {team}",
        ],
        "defense_strong_variants": [
            "Defense is noticeably stronger for {team}",
            "Defense advantage is clear for {team}",
            "Clear defensive edge for {team}",
            "Strong defensive edge for {team}",
        ],
        "venue_even_variants": ["Home/away without a clear bias", "No clear home/away skew", "No clear home/away tilt", "Home/away looks even"],
        "venue_slight_home_variants": [
            "Home factor slightly favors {team}",
            "Slight home edge for {team}",
            "Small home edge for {team}",
            "Light home edge for {team}",
        ],
        "venue_slight_away_variants": [
            "Away factor slightly favors {team}",
            "Slight away edge for {team}",
            "Small away edge for {team}",
            "Light away edge for {team}",
        ],
        "venue_strong_home_variants": [
            "Home factor favors {team}",
            "Strong home edge for {team}",
            "Strong home advantage for {team}",
            "Clear home advantage for {team}",
        ],
        "venue_strong_away_variants": [
            "Away factor favors {team}",
            "Strong away edge for {team}",
            "Strong away advantage for {team}",
            "Clear away advantage for {team}",
        ],
        "rest_even_variants": ["Rest is roughly equal", "Rest levels look similar", "Rest looks balanced", "Rest looks even"],
        "rest_more_variants": [
            "✅ {team} rested more: {a}h vs {b}h",
            "✅ {team} had more rest: {a}h vs {b}h",
            "✅ {team} had the rest edge: {a}h vs {b}h",
            "✅ {team} holds the rest edge: {a}h vs {b}h",
        ],
        "for": "for",
        "against": "against",
        "home": "at home",
        "away": "away",
        "reason_no_report": "no quality report",
        "reason_no_summary": "no quality summary",
        "reason_low_sample": "small sample ({bets})",
        "reason_clv_zero": "CLV coverage 0%",
        "reason_clv_low": "CLV coverage low ({pct})",
        "reason_brier": "Brier {value}",
        "reason_logloss": "LogLoss {value}",
        "selection_home_win": "Win {team} (1)",
        "selection_draw": "Draw (X)",
        "selection_away_win": "Win {team} (2)",
        "selection_over": "Total Over 2.5",
        "selection_under": "Total Under 2.5",
        "selection_over_1_5": "Total Over 1.5",
        "selection_under_1_5": "Total Under 1.5",
        "selection_over_3_5": "Total Over 3.5",
        "selection_under_3_5": "Total Under 3.5",
        "selection_btts_yes": "BTTS — Yes",
        "selection_btts_no": "BTTS — No",
        "selection_dc_1x": "Double Chance 1X",
        "selection_dc_x2": "Double Chance X2",
        "selection_dc_12": "Double Chance 12",
    },
    "uk": {
        "hot_prediction": "🔥 ГАРЯЧИЙ ПРОГНОЗ 🔥",
        "prediction_label": {
            "hot": "🔥 ГАРЯЧИЙ ПРОГНОЗ",
            "standard": "✅ СТАНДАРТНИЙ ПРОГНОЗ",
            "cautious": "⚠️ ОБЕРЕЖНИЙ ПРОГНОЗ",
            "experimental": "🧪 EXPERIMENTAL ПРОГНОЗ",
        },
        "prediction_label_variants": {
            "hot": ["🔥 ГАРЯЧИЙ ПРОГНОЗ", "🔥 ТОП-ПРОГНОЗ", "🔥 СИЛЬНИЙ ПРОГНОЗ", "🔥 ЯСКРАВИЙ ПРОГНОЗ"],
            "standard": ["✅ СТАНДАРТНИЙ ПРОГНОЗ", "✅ БАЗОВИЙ ПРОГНОЗ", "✅ ОСНОВНИЙ ПРОГНОЗ", "✅ СТАБІЛЬНИЙ ПРОГНОЗ"],
            "cautious": ["⚠️ ОБЕРЕЖНИЙ ПРОГНОЗ", "⚠️ АКУРАТНИЙ ПРОГНОЗ", "⚠️ СТРИМАНИЙ ПРОГНОЗ", "⚠️ ПОМІРНИЙ ПРОГНОЗ"],
            "experimental": [
                "🧪 EXPERIMENTAL ПРОГНОЗ",
                "🧪 ЕКСПЕРИМЕНТАЛЬНИЙ ПРОГНОЗ",
                "🧪 ПРОГНОЗ-ЕКСПЕРИМЕНТ",
                "🧪 ТЕСТОВИЙ ПРОГНОЗ",
            ],
        },
        "bet_label_by_tier": {
            "hot": "💰 СТАВКА ДНЯ",
            "standard": "💰 РЕКОМЕНДАЦІЯ",
            "cautious": "⚠️ ОБЕРЕЖНА СТАВКА",
            "experimental": "🧪 ЕКСПЕРИМЕНТАЛЬНА СТАВКА",
        },
        "bet_of_day": "💰 СТАВКА ДНЯ",
        "model_probability": "Ймовірність моделі",
        "why": "📊 ЧОМУ ЦЕ ЗАЙДЕ?",
        "why_variants": [
            "📊 ЧОМУ ЦЕ ЗАЙДЕ?",
            "📊 КЛЮЧОВІ ФАКТОРИ",
            "📊 КЛЮЧОВІ АРГУМЕНТИ",
            "📊 ОСНОВНІ ФАКТОРИ",
        ],
        "current_form": "⚡ ПОТОЧНА ФОРМА (останні 5 матчів)",
        "team_class": "🏆 КЛАС КОМАНД (15 матчів)",
        "home_away_stats": "🏟️ ДОМАШНЯ/ГОСТЬОВА СТАТИСТИКА",
        "fatigue_factor": "⏰ ФАКТОР ВТОМИ",
        "value_indicators": "📈 VALUE-БЕТ ІНДИКАТОРИ",
        "value_variants": ["📈 VALUE-БЕТ ІНДИКАТОРИ", "📈 VALUE-СИГНАЛИ", "📈 VALUE-ІНДИКАТОРИ", "📈 VALUE-ОГЛЯД"],
        "risks": "⚠️ РИЗИКИ",
        "risks_variants": ["⚠️ РИЗИКИ", "⚠️ НОТАТКИ", "⚠️ ОБМЕЖЕННЯ", "⚠️ РИЗИК-ФАКТОРИ"],
        "recommendation": "💡 РЕКОМЕНДАЦІЯ",
        "recommendation_variants": ["💡 РЕКОМЕНДАЦІЯ", "💡 ПІДСУМОК", "💡 РЕЗЮМЕ", "💡 ВИСНОВОК"],
        "disclaimer": "⚠️ ДИСКЛЕЙМЕР: це аналітичний прогноз, а не гарантія результату. "
        "Формули моделі є пропрієтарними і не розкриваються.",
        "bookmakers_give": "🎲 Букмекери дають",
        "our_model": "🤖 Наша модель",
        "signal": "📊 Сигнал моделі",
        "signal_variants": ["📊 Сигнал моделі", "📊 Сила сигналу", "📊 Інтенсивність сигналу", "📊 Рівень сигналу"],
        "signal_notes": {"strong": "сильний", "moderate": "помірний", "weak": "слабкий"},
        "edge_short": "перевага",
        "edge_strong": "🔥 Перевага моделі: {pct:.1f}%",
        "edge_good": "✅ Перевага моделі: {pct:.1f}%",
        "edge_thin": "⚠️ Перевага моделі: {pct:.1f}%",
        "edge_none": "⚪ Переваги немає ({pct:.1f}%)",
        "edge_strong_variants": [
            "🔥 Перевага моделі: {pct:.1f}%",
            "🔥 Сильна перевага моделі: {pct:.1f}%",
            "🔥 Явна перевага моделі: {pct:.1f}%",
            "🔥 Перевага за моделлю: {pct:.1f}%",
        ],
        "edge_good_variants": [
            "✅ Перевага моделі: {pct:.1f}%",
            "✅ Плюс моделі: {pct:.1f}%",
            "✅ Перевага за моделлю: {pct:.1f}%",
            "✅ Невеликий плюс моделі: {pct:.1f}%",
        ],
        "edge_thin_variants": [
            "⚠️ Перевага моделі: {pct:.1f}%",
            "⚠️ Невелика перевага: {pct:.1f}%",
            "⚠️ Слабка перевага: {pct:.1f}%",
            "⚠️ Мінімальна перевага: {pct:.1f}%",
        ],
        "edge_none_variants": [
            "⚪ Переваги немає ({pct:.1f}%)",
            "⚪ Переваги немає: {pct:.1f}%",
            "⚪ Переваги майже немає ({pct:.1f}%)",
            "⚪ Суттєвої переваги немає ({pct:.1f}%)",
        ],
        "value_profile": "✅ Value-профіль",
        "value_profile_variants": ["✅ Value-профіль", "✅ Профіль value", "✅ Value-оцінка", "✅ Оцінка value"],
        "value_unknown": "⚠️ Value не розрахований — використовуйте обережність.",
        "value_strength": {
            "strong": "сильний value",
            "good": "хороший value",
            "thin": "тонкий value",
            "edge": "value на межі",
            "neg": "від'ємний value",
            "none": "value не оцінено",
        },
        "recommend": {
            "strong": "✅ Ставка виглядає дуже привабливо при коефіцієнті {odd}.",
            "good": "✅ Ставка виглядає привабливо при коефіцієнті {odd}.",
            "thin": "⚠️ Value невеликий при коефіцієнті {odd} — краще підтвердити додатковими факторами.",
            "edge": "⚠️ Value на межі при коефіцієнті {odd}.",
            "neg": "⛔ Від'ємний value при коефіцієнті {odd} — краще пропустити.",
        },
        "recommend_variants": {
            "strong": [
                "✅ Ставка виглядає дуже привабливо при коефіцієнті {odd}.",
                "✅ Коефіцієнт {odd} робить ставку дуже привабливою.",
                "✅ Дуже хороша ціна при {odd}.",
                "✅ При {odd} ставка виглядає максимально цікавою.",
            ],
            "good": [
                "✅ Ставка виглядає привабливо при коефіцієнті {odd}.",
                "✅ При {odd} ставка виглядає цікаво.",
                "✅ При {odd} є відчутний value.",
                "✅ Коефіцієнт {odd} виглядає гідно.",
            ],
            "thin": [
                "⚠️ Value невеликий при коефіцієнті {odd} — краще підтвердити додатковими факторами.",
                "⚠️ При {odd} value невеликий — краще підтвердити додатковими факторами.",
                "⚠️ Невеликий value при {odd} — краще підтвердити.",
                "⚠️ При {odd} value тонкий — краще підтвердити.",
            ],
            "edge": [
                "⚠️ Value на межі при коефіцієнті {odd}.",
                "⚠️ При {odd} value на межі.",
                "⚠️ Граничний value при {odd}.",
                "⚠️ При {odd} value майже на нулі.",
            ],
            "neg": [
                "⛔ Від'ємний value при коефіцієнті {odd} — краще пропустити.",
                "⛔ При {odd} value від'ємний — краще пропустити.",
                "⛔ При {odd} value йде в мінус — краще пропустити.",
                "⛔ Від'ємний value при {odd} — краще не брати.",
            ],
        },
        "recommend_cautious": {
            "strong": "⚠️ Високий потенціал при коефіцієнті {odd}, але потрібна обережність.",
            "good": "⚠️ Ставка цікава при коефіцієнті {odd} — дійте обережно.",
            "thin": "⚠️ Слабкий value при коефіцієнті {odd} — краще дочекатися підтвердження.",
            "edge": "⚠️ Value на межі при коефіцієнті {odd}.",
            "neg": "⛔ Від'ємний value при коефіцієнті {odd} — краще пропустити.",
        },
        "recommend_cautious_variants": {
            "strong": [
                "⚠️ Високий потенціал при коефіцієнті {odd}, але потрібна обережність.",
                "⚠️ При {odd} потенціал високий, але потрібна обережність.",
                "⚠️ При {odd} потенціал високий — дійте акуратно.",
                "⚠️ Високий потенціал при {odd}, але обережно.",
            ],
            "good": [
                "⚠️ Ставка цікава при коефіцієнті {odd} — дійте обережно.",
                "⚠️ При {odd} ставка цікава, але дійте обережно.",
                "⚠️ При {odd} ставка непогана, але обережно.",
                "⚠️ Є інтерес при {odd}, але обережно.",
            ],
            "thin": [
                "⚠️ Слабкий value при коефіцієнті {odd} — краще дочекатися підтвердження.",
                "⚠️ При {odd} value слабкий — краще дочекатися підтвердження.",
                "⚠️ При {odd} value тонкий — краще дочекатися підтвердження.",
                "⚠️ Слабкий value при {odd} — краще почекати.",
            ],
            "edge": [
                "⚠️ Value на межі при коефіцієнті {odd}.",
                "⚠️ При {odd} value на межі.",
                "⚠️ Граничний value при {odd}.",
                "⚠️ При {odd} value майже на нулі.",
            ],
            "neg": [
                "⛔ Від'ємний value при коефіцієнті {odd} — краще пропустити.",
                "⛔ При {odd} value від'ємний — краще пропустити.",
                "⛔ При {odd} value йде в мінус — краще пропустити.",
                "⛔ Від'ємний value при {odd} — краще не брати.",
            ],
        },
        "line_watch": "📉 Стежте за лінією — при коефіцієнті нижче {odd} value зникає.",
        "line_watch_variants": [
            "📉 Стежте за лінією — при коефіцієнті нижче {odd} value зникає.",
            "📉 Якщо коефіцієнт впаде нижче {odd}, value зникне.",
            "📉 При падінні нижче {odd} value зникає.",
            "📉 При {odd} і нижче value зникає.",
        ],
        "no_risks": "✅ Суттєвих ризиків не виявлено",
        "no_risks_variants": [
            "✅ Суттєвих ризиків не виявлено",
            "✅ Ризики виглядають мінімальними",
            "✅ Критичних ризиків не видно",
            "✅ Ризики виглядають низькими",
        ],
        "experimental_prefix": "EXPERIMENTAL — ",
        "attack_similar": "Атака команд співставна",
        "attack_slight": "Атака трохи сильніша у {team}",
        "attack_strong": "Атака помітно сильніша у {team}",
        "defense_similar": "Оборона на одному рівні",
        "defense_slight": "Оборона трохи надійніша у {team}",
        "defense_strong": "Оборона помітно надійніша у {team}",
        "venue_even": "Дім/виїзд без явного перекосу",
        "venue_slight_home": "Домашній фактор трохи на боці {team}",
        "venue_slight_away": "Гостьовий фактор у {team} трохи кращий",
        "venue_strong_home": "Домашній фактор на боці {team}",
        "venue_strong_away": "Гостьовий фактор у {team} виглядає краще",
        "rest_even": "Відпочинок приблизно рівний",
        "rest_more": "✅ {team} відпочивав більше: {a}г vs {b}г",
        "attack_similar_variants": [
            "Атака команд співставна",
            "Атакувальна сила схожа",
            "Рівень атаки схожий",
            "Сила атаки приблизно рівна",
        ],
        "attack_slight_variants": [
            "Атака трохи сильніша у {team}",
            "Невеликий перевіс в атаці у {team}",
            "Невелика перевага в атаці у {team}",
            "Атака трохи краща у {team}",
        ],
        "attack_strong_variants": [
            "Атака помітно сильніша у {team}",
            "Відчутна перевага в атаці у {team}",
            "Сильна перевага в атаці у {team}",
            "Атака суттєво сильніша у {team}",
        ],
        "defense_similar_variants": [
            "Оборона на одному рівні",
            "Захист виглядає рівним",
            "Оборона виглядає рівною",
            "Рівень оборони схожий",
        ],
        "defense_slight_variants": [
            "Оборона трохи надійніша у {team}",
            "Невелика перевага в обороні у {team}",
            "Легкий перевіс в обороні у {team}",
            "Оборона трохи краща у {team}",
        ],
        "defense_strong_variants": [
            "Оборона помітно надійніша у {team}",
            "Відчутна перевага в обороні у {team}",
            "Сильна перевага в обороні у {team}",
            "Оборона суттєво надійніша у {team}",
        ],
        "venue_even_variants": [
            "Дім/виїзд без явного перекосу",
            "Немає явної переваги дом/виїзд",
            "Немає помітної переваги дом/виїзд",
            "Дім/виїзд приблизно рівні",
        ],
        "venue_slight_home_variants": [
            "Домашній фактор трохи на боці {team}",
            "Легкий домашній перевіс у {team}",
            "Невеликий домашній плюс у {team}",
            "Легкий домашній плюс у {team}",
        ],
        "venue_slight_away_variants": [
            "Гостьовий фактор у {team} трохи кращий",
            "Легкий гостьовий перевіс у {team}",
            "Невеликий гостьовий плюс у {team}",
            "Легкий гостьовий плюс у {team}",
        ],
        "venue_strong_home_variants": [
            "Домашній фактор на боці {team}",
            "Сильний домашній фактор у {team}",
            "Домашній фактор явно у {team}",
            "Домашній фактор помітно у {team}",
        ],
        "venue_strong_away_variants": [
            "Гостьовий фактор у {team} виглядає краще",
            "Сильний гостьовий фактор у {team}",
            "Гостьовий фактор явно у {team}",
            "Гостьовий фактор помітно у {team}",
        ],
        "rest_even_variants": ["Відпочинок приблизно рівний", "Баланс відпочинку схожий", "Відпочинок схожий", "Відпочинок майже рівний"],
        "rest_more_variants": [
            "✅ {team} відпочивав більше: {a}г vs {b}г",
            "✅ У {team} більше відпочинку: {a}г vs {b}г",
            "✅ {team} мав більше відпочинку: {a}г vs {b}г",
            "✅ У {team} перевага у відпочинку: {a}г vs {b}г",
        ],
        "for": "за",
        "against": "проти",
        "home": "вдома",
        "away": "у гостях",
        "reason_no_report": "немає звіту якості",
        "reason_no_summary": "немає зведення якості",
        "reason_low_sample": "малий обсяг ({bets})",
        "reason_clv_zero": "CLV coverage 0%",
        "reason_clv_low": "CLV coverage низький ({pct})",
        "reason_brier": "Brier {value}",
        "reason_logloss": "LogLoss {value}",
        "selection_home_win": "Перемога {team} (П1)",
        "selection_draw": "Нічия (Х)",
        "selection_away_win": "Перемога {team} (П2)",
        "selection_over": "Тотал Більше 2.5",
        "selection_under": "Тотал Менше 2.5",
        "selection_over_1_5": "Тотал Б 1.5",
        "selection_under_1_5": "Тотал М 1.5",
        "selection_over_3_5": "Тотал Б 3.5",
        "selection_under_3_5": "Тотал М 3.5",
        "selection_btts_yes": "Обидві заб'ють — Так",
        "selection_btts_no": "Обидві заб'ють — Ні",
        "selection_dc_1x": "Подвійний шанс 1X",
        "selection_dc_x2": "Подвійний шанс X2",
        "selection_dc_12": "Подвійний шанс 12",
    },
    "fr": {
        "hot_prediction": "🔥 PRONOSTIC CHAUD 🔥",
        "prediction_label": {
            "hot": "🔥 PRONOSTIC CHAUD",
            "standard": "✅ PRONOSTIC STANDARD",
            "cautious": "⚠️ PRONOSTIC PRUDENT",
            "experimental": "🧪 PRONOSTIC EXPÉRIMENTAL",
        },
        "prediction_label_variants": {
            "hot": ["🔥 PRONOSTIC CHAUD", "🔥 TOP PRONOSTIC", "🔥 PRONOSTIC FORT", "🔥 PRONOSTIC HAUT"],
            "standard": [
                "✅ PRONOSTIC STANDARD",
                "✅ PRONOSTIC DE BASE",
                "✅ PRONOSTIC PRINCIPAL",
                "✅ PRONOSTIC STABLE",
            ],
            "cautious": [
                "⚠️ PRONOSTIC PRUDENT",
                "⚠️ PRONOSTIC PRÉCAUTIONNEUX",
                "⚠️ PRONOSTIC MODÉRÉ",
                "⚠️ PRONOSTIC RÉSERVÉ",
            ],
            "experimental": [
                "🧪 PRONOSTIC EXPÉRIMENTAL",
                "🧪 PRONOSTIC TEST",
                "🧪 PRONOSTIC BÊTA",
                "🧪 PRONOSTIC D’ESSAI",
            ],
        },
        "bet_label_by_tier": {
            "hot": "💰 PARI DU JOUR",
            "standard": "💰 RECOMMANDATION",
            "cautious": "⚠️ PARI PRUDENT",
            "experimental": "🧪 PARI EXPÉRIMENTAL",
        },
        "bet_of_day": "💰 PARI DU JOUR",
        "model_probability": "Probabilité du modèle",
        "why": "📊 POURQUOI ÇA PASSE ?",
        "why_variants": ["📊 POURQUOI ÇA PASSE ?", "📊 FACTEURS CLÉS", "📊 POINTS CLÉS", "📊 PRINCIPAUX FACTEURS"],
        "current_form": "⚡ FORME ACTUELLE (5 derniers matchs)",
        "team_class": "🏆 CLASSE DES ÉQUIPES (15 matchs)",
        "home_away_stats": "🏟️ STATISTIQUES DOM/EXT",
        "fatigue_factor": "⏰ FACTEUR DE FATIGUE",
        "value_indicators": "📈 INDICATEURS DE VALUE BET",
        "value_variants": ["📈 INDICATEURS DE VALUE BET", "📈 SIGNAUX DE VALUE", "📈 BAROMÈTRE VALUE", "📈 APERÇU VALUE"],
        "risks": "⚠️ RISQUES",
        "risks_variants": ["⚠️ RISQUES", "⚠️ POINTS D’ATTENTION", "⚠️ LIMITES", "⚠️ RÉSERVES"],
        "recommendation": "💡 RECOMMANDATION",
        "recommendation_variants": ["💡 RECOMMANDATION", "💡 SYNTHÈSE", "💡 VERDICT", "💡 CONCLUSION"],
        "disclaimer": "⚠️ AVERTISSEMENT : Ceci est une prédiction analytique, sans garantie de résultat. "
        "Les formules du modèle sont propriétaires et ne sont pas divulguées.",
        "bookmakers_give": "🎲 Les bookmakers donnent",
        "our_model": "🤖 Notre modèle",
        "signal": "📊 Signal du modèle",
        "signal_variants": ["📊 Signal du modèle", "📊 Force du signal", "📊 Intensité du signal", "📊 Niveau du signal"],
        "signal_notes": {"strong": "fort", "moderate": "modéré", "weak": "faible"},
        "edge_short": "avantage",
        "edge_strong": "🔥 Avantage du modèle : {pct:.1f}%",
        "edge_good": "✅ Avantage du modèle : {pct:.1f}%",
        "edge_thin": "⚠️ Avantage du modèle : {pct:.1f}%",
        "edge_none": "⚪ Pas d’avantage ({pct:.1f}%)",
        "edge_strong_variants": [
            "🔥 Avantage du modèle : {pct:.1f}%",
            "🔥 Fort avantage du modèle : {pct:.1f}%",
            "🔥 Avantage clair du modèle : {pct:.1f}%",
            "🔥 Avantage net du modèle : {pct:.1f}%",
        ],
        "edge_good_variants": [
            "✅ Avantage du modèle : {pct:.1f}%",
            "✅ Avantage modèle : {pct:.1f}%",
            "✅ Avantage selon le modèle : {pct:.1f}%",
            "✅ Petit avantage du modèle : {pct:.1f}%",
        ],
        "edge_thin_variants": [
            "⚠️ Avantage du modèle : {pct:.1f}%",
            "⚠️ Petit avantage : {pct:.1f}%",
            "⚠️ Léger avantage : {pct:.1f}%",
            "⚠️ Avantage minimal : {pct:.1f}%",
        ],
        "edge_none_variants": [
            "⚪ Pas d’avantage ({pct:.1f}%)",
            "⚪ Pas d’avantage clair : {pct:.1f}%",
            "⚪ Peu d’avantage ({pct:.1f}%)",
            "⚪ Pas d’avantage notable ({pct:.1f}%)",
        ],
        "value_profile": "✅ Profil value",
        "value_profile_variants": ["✅ Profil value", "✅ Profil de value", "✅ Lecture value", "✅ Évaluation value"],
        "value_unknown": "⚠️ Value non calculée — utilisez avec prudence.",
        "value_strength": {
            "strong": "value forte",
            "good": "bonne value",
            "thin": "value faible",
            "edge": "value limite",
            "neg": "value négative",
            "none": "value non évaluée",
        },
        "recommend": {
            "strong": "✅ La cote est très attractive à {odd}.",
            "good": "✅ La cote est attractive à {odd}.",
            "thin": "⚠️ Value faible à {odd} — à confirmer avec des facteurs supplémentaires.",
            "edge": "⚠️ Value limite à {odd}.",
            "neg": "⛔ Value négative à {odd} — mieux vaut passer.",
        },
        "recommend_variants": {
            "strong": [
                "✅ La cote est très attractive à {odd}.",
                "✅ À {odd}, la value est très intéressante.",
                "✅ Très bonne cote à {odd}.",
                "✅ {odd} offre une très bonne value.",
            ],
            "good": [
                "✅ La cote est attractive à {odd}.",
                "✅ À {odd}, la value reste intéressante.",
                "✅ {odd} reste une cote intéressante.",
                "✅ Bonne value à {odd}.",
            ],
            "thin": [
                "⚠️ Value faible à {odd} — à confirmer avec des facteurs supplémentaires.",
                "⚠️ À {odd}, la value est faible — mieux confirmer.",
                "⚠️ Value limitée à {odd} — mieux confirmer.",
                "⚠️ Faible value à {odd} — confirmation recommandée.",
            ],
            "edge": [
                "⚠️ Value limite à {odd}.",
                "⚠️ À {odd}, la value est limite.",
                "⚠️ Value à la limite à {odd}.",
                "⚠️ {odd} est limite pour la value.",
            ],
            "neg": [
                "⛔ Value négative à {odd} — mieux vaut passer.",
                "⛔ À {odd}, la value est négative — mieux passer.",
                "⛔ Value négative à {odd} — mieux passer.",
                "⛔ {odd} donne une value négative — mieux passer.",
            ],
        },
        "recommend_cautious": {
            "strong": "⚠️ Bon potentiel à {odd}, mais prudence.",
            "good": "⚠️ Value intéressante à {odd} — prudence.",
            "thin": "⚠️ Value faible à {odd} — mieux confirmer.",
            "edge": "⚠️ Value limite à {odd}.",
            "neg": "⛔ Value négative à {odd} — mieux passer.",
        },
        "recommend_cautious_variants": {
            "strong": [
                "⚠️ Bon potentiel à {odd}, mais prudence.",
                "⚠️ À {odd}, bon potentiel — prudence.",
                "⚠️ Bon potentiel à {odd}, mais restez prudent.",
                "⚠️ Potentiel élevé à {odd}, mais prudence.",
            ],
            "good": [
                "⚠️ Value intéressante à {odd} — prudence.",
                "⚠️ À {odd}, la value est intéressante — prudence.",
                "⚠️ Value intéressante à {odd}, mais prudence.",
                "⚠️ {odd} est intéressant, mais prudence.",
            ],
            "thin": [
                "⚠️ Value faible à {odd} — mieux confirmer.",
                "⚠️ À {odd}, value faible — mieux confirmer.",
                "⚠️ Value faible à {odd} — confirmation conseillée.",
                "⚠️ Faible value à {odd} — mieux attendre.",
            ],
            "edge": [
                "⚠️ Value limite à {odd}.",
                "⚠️ À {odd}, value limite.",
                "⚠️ {odd} est limite pour la value.",
                "⚠️ Value tout juste limite à {odd}.",
            ],
            "neg": [
                "⛔ Value négative à {odd} — mieux passer.",
                "⛔ À {odd}, value négative — mieux passer.",
                "⛔ Value négative à {odd} — mieux éviter.",
                "⛔ {odd} donne une value négative — mieux passer.",
            ],
        },
        "line_watch": "📉 Surveillez la ligne — en dessous de {odd}, la value disparaît.",
        "line_watch_variants": [
            "📉 Surveillez la ligne — en dessous de {odd}, la value disparaît.",
            "📉 Si la cote passe sous {odd}, la value disparaît.",
            "📉 En dessous de {odd}, la value disparaît.",
            "📉 Sous {odd}, la value disparaît.",
        ],
        "no_risks": "✅ Aucun risque majeur identifié",
        "no_risks_variants": [
            "✅ Aucun risque majeur identifié",
            "✅ Risques limités",
            "✅ Aucun risque critique relevé",
            "✅ Risques faibles",
        ],
        "experimental_prefix": "EXPERIMENTAL — ",
        "attack_similar": "Les attaques sont comparables",
        "attack_slight": "L’attaque est légèrement meilleure pour {team}",
        "attack_strong": "L’attaque est nettement meilleure pour {team}",
        "defense_similar": "Les défenses sont au même niveau",
        "defense_slight": "La défense est légèrement meilleure pour {team}",
        "defense_strong": "La défense est nettement meilleure pour {team}",
        "venue_even": "Domicile/extérieur sans biais clair",
        "venue_slight_home": "L’avantage du domicile favorise légèrement {team}",
        "venue_slight_away": "L’avantage extérieur favorise légèrement {team}",
        "venue_strong_home": "L’avantage du domicile favorise {team}",
        "venue_strong_away": "L’avantage extérieur favorise {team}",
        "rest_even": "Repos à peu près égal",
        "rest_more": "✅ {team} s’est reposé davantage : {a}h vs {b}h",
        "attack_similar_variants": [
            "Les attaques sont comparables",
            "La force offensive est similaire",
            "Force offensive comparable",
            "Niveau offensif comparable",
        ],
        "attack_slight_variants": [
            "L’attaque est légèrement meilleure pour {team}",
            "Petit avantage offensif pour {team}",
            "Légère avance offensive pour {team}",
            "L’attaque est un peu meilleure pour {team}",
        ],
        "attack_strong_variants": [
            "L’attaque est nettement meilleure pour {team}",
            "Avantage offensif clair pour {team}",
            "Net avantage offensif pour {team}",
            "L’attaque est clairement supérieure pour {team}",
        ],
        "defense_similar_variants": [
            "Les défenses sont au même niveau",
            "La défense est comparable",
            "Défenses de niveau similaire",
            "Niveau défensif comparable",
        ],
        "defense_slight_variants": [
            "La défense est légèrement meilleure pour {team}",
            "Petit avantage défensif pour {team}",
            "Légère avance défensive pour {team}",
            "La défense est un peu meilleure pour {team}",
        ],
        "defense_strong_variants": [
            "La défense est nettement meilleure pour {team}",
            "Avantage défensif clair pour {team}",
            "Net avantage défensif pour {team}",
            "La défense est clairement supérieure pour {team}",
        ],
        "venue_even_variants": [
            "Domicile/extérieur sans biais clair",
            "Pas de biais domicile/extérieur",
            "Pas de biais domicile/extérieur notable",
            "Domicile/extérieur assez équilibré",
        ],
        "venue_slight_home_variants": [
            "L’avantage du domicile favorise légèrement {team}",
            "Léger avantage à domicile pour {team}",
            "Petit avantage à domicile pour {team}",
            "Léger avantage domicile pour {team}",
        ],
        "venue_slight_away_variants": [
            "L’avantage extérieur favorise légèrement {team}",
            "Léger avantage à l’extérieur pour {team}",
            "Petit avantage à l’extérieur pour {team}",
            "Léger avantage extérieur pour {team}",
        ],
        "venue_strong_home_variants": [
            "L’avantage du domicile favorise {team}",
            "Fort avantage à domicile pour {team}",
            "Net avantage à domicile pour {team}",
            "Avantage domicile marqué pour {team}",
        ],
        "venue_strong_away_variants": [
            "L’avantage extérieur favorise {team}",
            "Fort avantage à l’extérieur pour {team}",
            "Net avantage à l’extérieur pour {team}",
            "Avantage extérieur marqué pour {team}",
        ],
        "rest_even_variants": ["Repos à peu près égal", "Repos assez équilibré", "Repos équilibré", "Repos similaire"],
        "rest_more_variants": [
            "✅ {team} s’est reposé davantage : {a}h vs {b}h",
            "✅ {team} a plus de repos : {a}h vs {b}h",
            "✅ Avantage repos pour {team} : {a}h vs {b}h",
            "✅ {team} a l’avantage du repos : {a}h vs {b}h",
        ],
        "for": "pour",
        "against": "contre",
        "home": "à domicile",
        "away": "à l’extérieur",
        "reason_no_report": "pas de rapport qualité",
        "reason_no_summary": "pas de synthèse qualité",
        "reason_low_sample": "échantillon faible ({bets})",
        "reason_clv_zero": "CLV coverage 0%",
        "reason_clv_low": "CLV coverage faible ({pct})",
        "reason_brier": "Brier {value}",
        "reason_logloss": "LogLoss {value}",
        "selection_home_win": "Victoire {team} (1)",
        "selection_draw": "Match nul (X)",
        "selection_away_win": "Victoire {team} (2)",
        "selection_over": "Total Plus de 2.5",
        "selection_under": "Total Moins de 2.5",
        "selection_over_1_5": "Total Plus de 1.5",
        "selection_under_1_5": "Total Moins de 1.5",
        "selection_over_3_5": "Total Plus de 3.5",
        "selection_under_3_5": "Total Moins de 3.5",
        "selection_btts_yes": "Les deux marquent — Oui",
        "selection_btts_no": "Les deux marquent — Non",
        "selection_dc_1x": "Double chance 1X",
        "selection_dc_x2": "Double chance X2",
        "selection_dc_12": "Double chance 12",
    },
    "de": {
        "hot_prediction": "🔥 HEISSER TIPP 🔥",
        "prediction_label": {
            "hot": "🔥 HEISSER TIPP",
            "standard": "✅ STANDARD-TIPP",
            "cautious": "⚠️ VORSICHTIGER TIPP",
            "experimental": "🧪 EXPERIMENTELLER TIPP",
        },
        "prediction_label_variants": {
            "hot": ["🔥 HEISSER TIPP", "🔥 TOP-TIPP", "🔥 STARKER TIPP", "🔥 KLARER TIPP"],
            "standard": ["✅ STANDARD-TIPP", "✅ BASIS-TIPP", "✅ HAUPT-TIPP", "✅ STABILER TIPP"],
            "cautious": ["⚠️ VORSICHTIGER TIPP", "⚠️ ZURÜCKHALTENDER TIPP", "⚠️ MODERATER TIPP", "⚠️ BEHUTSAMER TIPP"],
            "experimental": ["🧪 EXPERIMENTELLER TIPP", "🧪 TEST-TIPP", "🧪 BETA-TIPP", "🧪 PROBE-TIPP"],
        },
        "bet_label_by_tier": {
            "hot": "💰 WETT-TIPP DES TAGES",
            "standard": "💰 EMPFEHLUNG",
            "cautious": "⚠️ VORSICHTIGER TIPP",
            "experimental": "🧪 EXPERIMENTELLER TIPP",
        },
        "bet_of_day": "💰 WETT-TIPP DES TAGES",
        "model_probability": "Modellwahrscheinlichkeit",
        "why": "📊 WARUM KLAPPT DAS?",
        "why_variants": ["📊 WARUM KLAPPT DAS?", "📊 SCHLÜSSELFAKTOREN", "📊 HAUPTGRÜNDE", "📊 ZENTRALE FAKTOREN"],
        "current_form": "⚡ AKTUELLE FORM (letzte 5 Spiele)",
        "team_class": "🏆 TEAMKLASSE (15 Spiele)",
        "home_away_stats": "🏟️ HEIM/AUSWÄRTS-STATISTIK",
        "fatigue_factor": "⏰ ERSCHÖPFUNGSFAKTOR",
        "value_indicators": "📈 VALUE-BET INDIKATOREN",
        "value_variants": ["📈 VALUE-BET INDIKATOREN", "📈 VALUE-SIGNALE", "📈 VALUE-CHECK", "📈 VALUE-ÜBERBLICK"],
        "risks": "⚠️ RISIKEN",
        "risks_variants": ["⚠️ RISIKEN", "⚠️ HINWEISE", "⚠️ EINSCHRÄNKUNGEN", "⚠️ RISIKOFAKTOREN"],
        "recommendation": "💡 EMPFEHLUNG",
        "recommendation_variants": ["💡 EMPFEHLUNG", "💡 FAZIT", "💡 KURZFAZIT", "💡 ERGEBNIS"],
        "disclaimer": "⚠️ DISCLAIMER: Dies ist eine analytische Prognose, keine Garantie. "
        "Die Modellformeln sind proprietär und werden nicht offengelegt.",
        "bookmakers_give": "🎲 Buchmacher geben",
        "our_model": "🤖 Unser Modell",
        "signal": "📊 Modellsignal",
        "signal_variants": ["📊 Modellsignal", "📊 Signalstärke", "📊 Signalintensität", "📊 Signalniveau"],
        "signal_notes": {"strong": "stark", "moderate": "moderat", "weak": "schwach"},
        "edge_short": "Vorteil",
        "edge_strong": "🔥 Modellvorteil: {pct:.1f}%",
        "edge_good": "✅ Modellvorteil: {pct:.1f}%",
        "edge_thin": "⚠️ Modellvorteil: {pct:.1f}%",
        "edge_none": "⚪ Kein Vorteil ({pct:.1f}%)",
        "edge_strong_variants": [
            "🔥 Modellvorteil: {pct:.1f}%",
            "🔥 Starker Modellvorteil: {pct:.1f}%",
            "🔥 Klarer Modellvorteil: {pct:.1f}%",
            "🔥 Deutlicher Modellvorteil: {pct:.1f}%",
        ],
        "edge_good_variants": [
            "✅ Modellvorteil: {pct:.1f}%",
            "✅ Vorteil des Modells: {pct:.1f}%",
            "✅ Vorteil laut Modell: {pct:.1f}%",
            "✅ Leichter Modellvorteil: {pct:.1f}%",
        ],
        "edge_thin_variants": [
            "⚠️ Modellvorteil: {pct:.1f}%",
            "⚠️ Kleiner Vorteil: {pct:.1f}%",
            "⚠️ Geringer Vorteil: {pct:.1f}%",
            "⚠️ Minimaler Vorteil: {pct:.1f}%",
        ],
        "edge_none_variants": [
            "⚪ Kein Vorteil ({pct:.1f}%)",
            "⚪ Kein klarer Vorteil: {pct:.1f}%",
            "⚪ Kaum Vorteil ({pct:.1f}%)",
            "⚪ Kein spürbarer Vorteil ({pct:.1f}%)",
        ],
        "value_profile": "✅ Value-Profil",
        "value_profile_variants": ["✅ Value-Profil", "✅ Value-Check", "✅ Value-Einschätzung", "✅ Value-Bewertung"],
        "value_unknown": "⚠️ Value nicht berechnet — Vorsicht.",
        "value_strength": {
            "strong": "starker Value",
            "good": "guter Value",
            "thin": "dünner Value",
            "edge": "grenzwertiger Value",
            "neg": "negativer Value",
            "none": "Value nicht bewertet",
        },
        "recommend": {
            "strong": "✅ Die Quote ist sehr attraktiv bei {odd}.",
            "good": "✅ Die Quote ist attraktiv bei {odd}.",
            "thin": "⚠️ Geringer Value bei {odd} — besser mit zusätzlichen Faktoren bestätigen.",
            "edge": "⚠️ Grenzwertiger Value bei {odd}.",
            "neg": "⛔ Negativer Value bei {odd} — besser auslassen.",
        },
        "recommend_variants": {
            "strong": [
                "✅ Die Quote ist sehr attraktiv bei {odd}.",
                "✅ Bei {odd} wirkt der Value sehr stark.",
                "✅ Sehr gute Quote bei {odd}.",
                "✅ {odd} bietet starken Value.",
            ],
            "good": [
                "✅ Die Quote ist attraktiv bei {odd}.",
                "✅ {odd} bietet noch guten Value.",
                "✅ {odd} sieht weiterhin gut aus.",
                "✅ Gute Quote bei {odd}.",
            ],
            "thin": [
                "⚠️ Geringer Value bei {odd} — besser mit zusätzlichen Faktoren bestätigen.",
                "⚠️ Bei {odd} ist der Value dünn — besser bestätigen.",
                "⚠️ Dünner Value bei {odd} — besser bestätigen.",
                "⚠️ Begrenzter Value bei {odd} — lieber bestätigen.",
            ],
            "edge": [
                "⚠️ Grenzwertiger Value bei {odd}.",
                "⚠️ Bei {odd} ist der Value grenzwertig.",
                "⚠️ Value knapp an der Grenze bei {odd}.",
                "⚠️ {odd} ist grenzwertig für Value.",
            ],
            "neg": [
                "⛔ Negativer Value bei {odd} — besser auslassen.",
                "⛔ Bei {odd} ist der Value negativ — besser auslassen.",
                "⛔ Negativer Value bei {odd} — besser vermeiden.",
                "⛔ {odd} ergibt negativen Value — besser auslassen.",
            ],
        },
        "recommend_cautious": {
            "strong": "⚠️ Guter Wert bei {odd}, aber vorsichtig bleiben.",
            "good": "⚠️ Interessanter Value bei {odd} — vorsichtig.",
            "thin": "⚠️ Dünner Value bei {odd} — besser auf Bestätigung warten.",
            "edge": "⚠️ Grenzwertiger Value bei {odd}.",
            "neg": "⛔ Negativer Value bei {odd} — besser auslassen.",
        },
        "recommend_cautious_variants": {
            "strong": [
                "⚠️ Guter Wert bei {odd}, aber vorsichtig bleiben.",
                "⚠️ Bei {odd} guter Value — bitte vorsichtig.",
                "⚠️ Bei {odd} guter Value, aber vorsichtig.",
                "⚠️ Starker Value bei {odd}, aber vorsichtig.",
            ],
            "good": [
                "⚠️ Interessanter Value bei {odd} — vorsichtig.",
                "⚠️ {odd} wirkt interessant, aber vorsichtig.",
                "⚠️ Interessant bei {odd}, aber vorsichtig.",
                "⚠️ Value bei {odd} vorhanden, aber vorsichtig.",
            ],
            "thin": [
                "⚠️ Dünner Value bei {odd} — besser auf Bestätigung warten.",
                "⚠️ Bei {odd} ist der Value dünn — besser warten.",
                "⚠️ Dünner Value bei {odd} — besser abwarten.",
                "⚠️ Geringer Value bei {odd} — besser warten.",
            ],
            "edge": [
                "⚠️ Grenzwertiger Value bei {odd}.",
                "⚠️ Bei {odd} ist der Value grenzwertig.",
                "⚠️ {odd} ist grenzwertig für Value.",
                "⚠️ Value knapp an der Grenze bei {odd}.",
            ],
            "neg": [
                "⛔ Negativer Value bei {odd} — besser auslassen.",
                "⛔ Bei {odd} ist der Value negativ — besser auslassen.",
                "⛔ Negativer Value bei {odd} — besser verzichten.",
                "⛔ {odd} ergibt negativen Value — besser auslassen.",
            ],
        },
        "line_watch": "📉 Linie beobachten — unter {odd} verschwindet der Value.",
        "line_watch_variants": [
            "📉 Linie beobachten — unter {odd} verschwindet der Value.",
            "📉 Fällt die Quote unter {odd}, verschwindet der Value.",
            "📉 Unter {odd} verschwindet der Value.",
            "📉 Bei {odd} und darunter verschwindet der Value.",
        ],
        "no_risks": "✅ Keine wesentlichen Risiken erkannt",
        "no_risks_variants": [
            "✅ Keine wesentlichen Risiken erkannt",
            "✅ Risiken wirken gering",
            "✅ Keine kritischen Risiken erkennbar",
            "✅ Keine großen Risiken sichtbar",
        ],
        "experimental_prefix": "EXPERIMENTAL — ",
        "attack_similar": "Die Offensiven sind vergleichbar",
        "attack_slight": "Die Offensive ist leicht stärker bei {team}",
        "attack_strong": "Die Offensive ist deutlich stärker bei {team}",
        "defense_similar": "Die Defensiven sind auf gleichem Niveau",
        "defense_slight": "Die Defensive ist leicht stärker bei {team}",
        "defense_strong": "Die Defensive ist deutlich stärker bei {team}",
        "venue_even": "Heim/Auswärts ohne klaren Vorteil",
        "venue_slight_home": "Heimvorteil leicht auf Seite von {team}",
        "venue_slight_away": "Auswärtsfaktor bei {team} leicht besser",
        "venue_strong_home": "Heimvorteil auf Seite von {team}",
        "venue_strong_away": "Auswärtsfaktor bei {team} stärker",
        "rest_even": "Erholung etwa gleich",
        "rest_more": "✅ {team} hatte mehr Ruhe: {a}h vs {b}h",
        "attack_similar_variants": [
            "Die Offensiven sind vergleichbar",
            "Offensivkraft ist ähnlich",
            "Offensivleistung ähnlich",
            "Offensivniveau ähnlich",
        ],
        "attack_slight_variants": [
            "Die Offensive ist leicht stärker bei {team}",
            "Leichter Offensivvorteil für {team}",
            "Kleiner Offensivvorteil bei {team}",
            "Offensive etwas besser bei {team}",
        ],
        "attack_strong_variants": [
            "Die Offensive ist deutlich stärker bei {team}",
            "Klarer Offensivvorteil für {team}",
            "Deutlicher Offensivvorteil für {team}",
            "Offensive klar stärker bei {team}",
        ],
        "defense_similar_variants": [
            "Die Defensiven sind auf gleichem Niveau",
            "Defensivkraft ist ähnlich",
            "Defensivleistung ähnlich",
            "Defensivniveau ähnlich",
        ],
        "defense_slight_variants": [
            "Die Defensive ist leicht stärker bei {team}",
            "Leichter Defensivvorteil für {team}",
            "Kleiner Defensivvorteil für {team}",
            "Defensive etwas besser bei {team}",
        ],
        "defense_strong_variants": [
            "Die Defensive ist deutlich stärker bei {team}",
            "Klarer Defensivvorteil für {team}",
            "Deutlicher Defensivvorteil für {team}",
            "Defensive klar stärker bei {team}",
        ],
        "venue_even_variants": [
            "Heim/Auswärts ohne klaren Vorteil",
            "Kein klarer Heim/Auswärts-Vorteil",
            "Kein deutlicher Heim/Auswärts-Bias",
            "Heim/Auswärts wirkt ausgeglichen",
        ],
        "venue_slight_home_variants": [
            "Heimvorteil leicht auf Seite von {team}",
            "Leichter Heimvorteil für {team}",
            "Kleiner Heimvorteil für {team}",
            "Heimvorteil leicht bei {team}",
        ],
        "venue_slight_away_variants": [
            "Auswärtsfaktor bei {team} leicht besser",
            "Leichter Auswärtsvorteil für {team}",
            "Kleiner Auswärtsvorteil für {team}",
            "Auswärtsvorteil leicht bei {team}",
        ],
        "venue_strong_home_variants": [
            "Heimvorteil auf Seite von {team}",
            "Starker Heimvorteil für {team}",
            "Deutlicher Heimvorteil für {team}",
            "Klarer Heimvorteil für {team}",
        ],
        "venue_strong_away_variants": [
            "Auswärtsfaktor bei {team} stärker",
            "Starker Auswärtsvorteil für {team}",
            "Deutlicher Auswärtsvorteil für {team}",
            "Klarer Auswärtsvorteil für {team}",
        ],
        "rest_even_variants": ["Erholung etwa gleich", "Erholung ist ähnlich", "Erholung wirkt ausgeglichen", "Erholung sieht ähnlich aus"],
        "rest_more_variants": [
            "✅ {team} hatte mehr Ruhe: {a}h vs {b}h",
            "✅ {team} hatte mehr Erholung: {a}h vs {b}h",
            "✅ {team} hatte den Erholungsvorteil: {a}h vs {b}h",
            "✅ Erholungsvorteil bei {team}: {a}h vs {b}h",
        ],
        "for": "für",
        "against": "gegen",
        "home": "zu Hause",
        "away": "auswärts",
        "reason_no_report": "kein Qualitätsbericht",
        "reason_no_summary": "keine Qualitätsübersicht",
        "reason_low_sample": "kleine Stichprobe ({bets})",
        "reason_clv_zero": "CLV coverage 0%",
        "reason_clv_low": "CLV coverage niedrig ({pct})",
        "reason_brier": "Brier {value}",
        "reason_logloss": "LogLoss {value}",
        "selection_home_win": "Sieg {team} (1)",
        "selection_draw": "Unentschieden (X)",
        "selection_away_win": "Sieg {team} (2)",
        "selection_over": "Gesamt Über 2.5",
        "selection_under": "Gesamt Unter 2.5",
        "selection_over_1_5": "Gesamt Über 1.5",
        "selection_under_1_5": "Gesamt Unter 1.5",
        "selection_over_3_5": "Gesamt Über 3.5",
        "selection_under_3_5": "Gesamt Unter 3.5",
        "selection_btts_yes": "Beide treffen — Ja",
        "selection_btts_no": "Beide treffen — Nein",
        "selection_dc_1x": "Doppelte Chance 1X",
        "selection_dc_x2": "Doppelte Chance X2",
        "selection_dc_12": "Doppelte Chance 12",
    },
    "pl": {
        "hot_prediction": "🔥 GORĄCY TYP 🔥",
        "prediction_label": {
            "hot": "🔥 GORĄCY TYP",
            "standard": "✅ STANDARDOWY TYP",
            "cautious": "⚠️ OSTROŻNY TYP",
            "experimental": "🧪 EKSPERYMENTALNY TYP",
        },
        "prediction_label_variants": {
            "hot": ["🔥 GORĄCY TYP", "🔥 TOP TYP", "🔥 MOCNY TYP", "🔥 WYRAŹNY TYP"],
            "standard": ["✅ STANDARDOWY TYP", "✅ BAZOWY TYP", "✅ GŁÓWNY TYP", "✅ STABILNY TYP"],
            "cautious": ["⚠️ OSTROŻNY TYP", "⚠️ UMIARKOWANY TYP", "⚠️ ROZWAŻNY TYP", "⚠️ ZACHOWAWCZY TYP"],
            "experimental": ["🧪 EKSPERYMENTALNY TYP", "🧪 TYP TESTOWY", "🧪 TYP BETA", "🧪 TYP PRÓBNY"],
        },
        "bet_label_by_tier": {
            "hot": "💰 ZAKŁAD DNIA",
            "standard": "💰 REKOMENDACJA",
            "cautious": "⚠️ OSTROŻNY TYP",
            "experimental": "🧪 EKSPERYMENTALNY TYP",
        },
        "bet_of_day": "💰 ZAKŁAD DNIA",
        "model_probability": "Prawdopodobieństwo modelu",
        "why": "📊 DLACZEGO TO WEJDZIE?",
        "why_variants": ["📊 DLACZEGO TO WEJDZIE?", "📊 KLUCZOWE CZYNNIKI", "📊 GŁÓWNE ARGUMENTY", "📊 NAJWAŻNIEJSZE CZYNNIKI"],
        "current_form": "⚡ OBECNA FORMA (ostatnie 5 meczów)",
        "team_class": "🏆 KLASA DRUŻYN (15 meczów)",
        "home_away_stats": "🏟️ STATYSTYKI DOM/WYJAZD",
        "fatigue_factor": "⏰ CZYNNIK ZMĘCZENIA",
        "value_indicators": "📈 WSKAŹNIKI VALUE BET",
        "value_variants": ["📈 WSKAŹNIKI VALUE BET", "📈 SYGNAŁY VALUE", "📈 VALUE-CHECK", "📈 PRZEGLĄD VALUE"],
        "risks": "⚠️ RYZYKA",
        "risks_variants": ["⚠️ RYZYKA", "⚠️ UWAGI", "⚠️ OGRANICZENIA", "⚠️ CZYNNIKI RYZYKA"],
        "recommendation": "💡 REKOMENDACJA",
        "recommendation_variants": ["💡 REKOMENDACJA", "💡 PODSUMOWANIE", "💡 WNIOSKI", "💡 KONKLUZJA"],
        "disclaimer": "⚠️ ZASTRZEŻENIE: To prognoza analityczna, bez gwarancji wyniku. "
        "Formuły modelu są własnościowe i nie są ujawniane.",
        "bookmakers_give": "🎲 Bukmacherzy dają",
        "our_model": "🤖 Nasz model",
        "signal": "📊 Sygnał modelu",
        "signal_variants": ["📊 Sygnał modelu", "📊 Siła sygnału", "📊 Intensywność sygnału", "📊 Poziom sygnału"],
        "signal_notes": {"strong": "mocny", "moderate": "umiarkowany", "weak": "słaby"},
        "edge_short": "przewaga",
        "edge_strong": "🔥 Przewaga modelu: {pct:.1f}%",
        "edge_good": "✅ Przewaga modelu: {pct:.1f}%",
        "edge_thin": "⚠️ Przewaga modelu: {pct:.1f}%",
        "edge_none": "⚪ Brak przewagi ({pct:.1f}%)",
        "edge_strong_variants": [
            "🔥 Przewaga modelu: {pct:.1f}%",
            "🔥 Silna przewaga modelu: {pct:.1f}%",
            "🔥 Wyraźna przewaga modelu: {pct:.1f}%",
            "🔥 Jasna przewaga modelu: {pct:.1f}%",
        ],
        "edge_good_variants": [
            "✅ Przewaga modelu: {pct:.1f}%",
            "✅ Plus modelu: {pct:.1f}%",
            "✅ Przewaga wg modelu: {pct:.1f}%",
            "✅ Niewielki plus modelu: {pct:.1f}%",
        ],
        "edge_thin_variants": [
            "⚠️ Przewaga modelu: {pct:.1f}%",
            "⚠️ Niewielka przewaga: {pct:.1f}%",
            "⚠️ Słaba przewaga: {pct:.1f}%",
            "⚠️ Minimalna przewaga: {pct:.1f}%",
        ],
        "edge_none_variants": [
            "⚪ Brak przewagi ({pct:.1f}%)",
            "⚪ Brak wyraźnej przewagi: {pct:.1f}%",
            "⚪ Prawie brak przewagi ({pct:.1f}%)",
            "⚪ Brak istotnej przewagi ({pct:.1f}%)",
        ],
        "value_profile": "✅ Profil value",
        "value_profile_variants": ["✅ Profil value", "✅ Podsumowanie value", "✅ Ocena value", "✅ Sprawdzenie value"],
        "value_unknown": "⚠️ Value nieobliczony — zachowaj ostrożność.",
        "value_strength": {
            "strong": "mocny value",
            "good": "dobry value",
            "thin": "cienki value",
            "edge": "graniczny value",
            "neg": "ujemny value",
            "none": "value nieoceniony",
        },
        "recommend": {
            "strong": "✅ Kurs wygląda bardzo atrakcyjnie przy {odd}.",
            "good": "✅ Kurs wygląda atrakcyjnie przy {odd}.",
            "thin": "⚠️ Mały value przy {odd} — lepiej potwierdzić dodatkowymi czynnikami.",
            "edge": "⚠️ Graniczny value przy {odd}.",
            "neg": "⛔ Ujemny value przy {odd} — lepiej odpuścić.",
        },
        "recommend_variants": {
            "strong": [
                "✅ Kurs wygląda bardzo atrakcyjnie przy {odd}.",
                "✅ Przy {odd} value wygląda bardzo dobrze.",
                "✅ Bardzo dobra cena przy {odd}.",
                "✅ {odd} daje bardzo dobry value.",
            ],
            "good": [
                "✅ Kurs wygląda atrakcyjnie przy {odd}.",
                "✅ Przy {odd} value wygląda interesująco.",
                "✅ {odd} wygląda nadal solidnie.",
                "✅ Dobra cena przy {odd}.",
            ],
            "thin": [
                "⚠️ Mały value przy {odd} — lepiej potwierdzić dodatkowymi czynnikami.",
                "⚠️ Przy {odd} value jest mały — lepiej potwierdzić.",
                "⚠️ Cienki value przy {odd} — lepiej potwierdzić.",
                "⚠️ Ograniczony value przy {odd} — lepiej potwierdzić.",
            ],
            "edge": [
                "⚠️ Graniczny value przy {odd}.",
                "⚠️ Przy {odd} value jest na granicy.",
                "⚠️ Value na granicy przy {odd}.",
                "⚠️ {odd} jest na granicy value.",
            ],
            "neg": [
                "⛔ Ujemny value przy {odd} — lepiej odpuścić.",
                "⛔ Przy {odd} value jest ujemny — lepiej odpuścić.",
                "⛔ Ujemny value przy {odd} — lepiej pominąć.",
                "⛔ {odd} daje ujemny value — lepiej odpuścić.",
            ],
        },
        "recommend_cautious": {
            "strong": "⚠️ Wysoki potencjał przy {odd}, ale ostrożnie.",
            "good": "⚠️ Value interesujący przy {odd} — ostrożnie.",
            "thin": "⚠️ Słaby value przy {odd} — lepiej poczekać na potwierdzenie.",
            "edge": "⚠️ Value na granicy przy {odd}.",
            "neg": "⛔ Ujemny value przy {odd} — lepiej odpuścić.",
        },
        "recommend_cautious_variants": {
            "strong": [
                "⚠️ Wysoki potencjał przy {odd}, ale ostrożnie.",
                "⚠️ Przy {odd} potencjał jest wysoki, ale ostrożnie.",
                "⚠️ Wysoki potencjał przy {odd}, lecz ostrożnie.",
                "⚠️ Dobry potencjał przy {odd}, ale ostrożnie.",
            ],
            "good": [
                "⚠️ Value interesujący przy {odd} — ostrożnie.",
                "⚠️ Przy {odd} value wygląda interesująco, ale ostrożnie.",
                "⚠️ Interesujący value przy {odd}, ale ostrożnie.",
                "⚠️ Wartość przy {odd} jest ok, ale ostrożnie.",
            ],
            "thin": [
                "⚠️ Słaby value przy {odd} — lepiej poczekać na potwierdzenie.",
                "⚠️ Przy {odd} value jest słaby — lepiej poczekać.",
                "⚠️ Cienki value przy {odd} — lepiej poczekać.",
                "⚠️ Niewielki value przy {odd} — lepiej poczekać.",
            ],
            "edge": [
                "⚠️ Value na granicy przy {odd}.",
                "⚠️ Przy {odd} value jest na granicy.",
                "⚠️ Graniczny value przy {odd}.",
                "⚠️ {odd} jest na granicy value.",
            ],
            "neg": [
                "⛔ Ujemny value przy {odd} — lepiej odpuścić.",
                "⛔ Przy {odd} value jest ujemny — lepiej odpuścić.",
                "⛔ Ujemny value przy {odd} — lepiej pominąć.",
                "⛔ {odd} daje ujemny value — lepiej odpuścić.",
            ],
        },
        "line_watch": "📉 Obserwuj linię — poniżej {odd} value znika.",
        "line_watch_variants": [
            "📉 Obserwuj linię — poniżej {odd} value znika.",
            "📉 Jeśli kurs spadnie poniżej {odd}, value znika.",
            "📉 Poniżej {odd} value znika.",
            "📉 Przy {odd} i niżej value znika.",
        ],
        "no_risks": "✅ Nie wykryto istotnych ryzyk",
        "no_risks_variants": [
            "✅ Nie wykryto istotnych ryzyk",
            "✅ Ryzyka wyglądają na niewielkie",
            "✅ Brak krytycznych ryzyk",
            "✅ Nie widać dużych ryzyk",
        ],
        "experimental_prefix": "EXPERIMENTAL — ",
        "attack_similar": "Ataki są porównywalne",
        "attack_slight": "Atak jest nieco silniejszy u {team}",
        "attack_strong": "Atak jest wyraźnie silniejszy u {team}",
        "defense_similar": "Obrony są na podobnym poziomie",
        "defense_slight": "Obrona jest nieco lepsza u {team}",
        "defense_strong": "Obrona jest wyraźnie lepsza u {team}",
        "venue_even": "Dom/wyjazd bez wyraźnej przewagi",
        "venue_slight_home": "Atut domu lekko po stronie {team}",
        "venue_slight_away": "Atut wyjazdu u {team} nieco lepszy",
        "venue_strong_home": "Atut domu po stronie {team}",
        "venue_strong_away": "Atut wyjazdu u {team} wygląda lepiej",
        "rest_even": "Odpoczynek mniej więcej równy",
        "rest_more": "✅ {team} odpoczywał dłużej: {a}h vs {b}h",
        "attack_similar_variants": ["Ataki są porównywalne", "Siła ataku jest podobna", "Atak wygląda podobnie", "Poziom ataku podobny"],
        "attack_slight_variants": [
            "Atak jest nieco silniejszy u {team}",
            "Lekka przewaga w ataku u {team}",
            "Niewielka przewaga w ataku u {team}",
            "Atak nieco lepszy u {team}",
        ],
        "attack_strong_variants": [
            "Atak jest wyraźnie silniejszy u {team}",
            "Wyraźna przewaga w ataku u {team}",
            "Mocna przewaga w ataku u {team}",
            "Atak zdecydowanie lepszy u {team}",
        ],
        "defense_similar_variants": ["Obrony są na podobnym poziomie", "Siła obrony jest podobna", "Obrona wygląda podobnie", "Poziom obrony podobny"],
        "defense_slight_variants": [
            "Obrona jest nieco lepsza u {team}",
            "Lekka przewaga w obronie u {team}",
            "Niewielka przewaga w obronie u {team}",
            "Obrona nieco lepsza u {team}",
        ],
        "defense_strong_variants": [
            "Obrona jest wyraźnie lepsza u {team}",
            "Wyraźna przewaga w obronie u {team}",
            "Mocna przewaga w obronie u {team}",
            "Obrona zdecydowanie lepsza u {team}",
        ],
        "venue_even_variants": [
            "Dom/wyjazd bez wyraźnej przewagi",
            "Brak wyraźnego atutu dom/wyjazd",
            "Brak wyraźnego przechyłu dom/wyjazd",
            "Dom/wyjazd wygląda wyrównanie",
        ],
        "venue_slight_home_variants": [
            "Atut domu lekko po stronie {team}",
            "Lekki atut domu u {team}",
            "Niewielki atut domu u {team}",
            "Lekki atut własnego boiska u {team}",
        ],
        "venue_slight_away_variants": [
            "Atut wyjazdu u {team} nieco lepszy",
            "Lekki atut wyjazdu u {team}",
            "Niewielki atut wyjazdu u {team}",
            "Lekki atut gry na wyjeździe u {team}",
        ],
        "venue_strong_home_variants": [
            "Atut domu po stronie {team}",
            "Mocny atut domu u {team}",
            "Wyraźny atut domu u {team}",
            "Silny atut własnego boiska u {team}",
        ],
        "venue_strong_away_variants": [
            "Atut wyjazdu u {team} wygląda lepiej",
            "Mocny atut wyjazdu u {team}",
            "Wyraźny atut wyjazdu u {team}",
            "Silny atut gry na wyjeździe u {team}",
        ],
        "rest_even_variants": ["Odpoczynek mniej więcej równy", "Odpoczynek wygląda podobnie", "Odpoczynek jest wyrównany", "Odpoczynek jest podobny"],
        "rest_more_variants": [
            "✅ {team} odpoczywał dłużej: {a}h vs {b}h",
            "✅ {team} miał więcej odpoczynku: {a}h vs {b}h",
            "✅ {team} miał przewagę odpoczynku: {a}h vs {b}h",
            "✅ Przewaga odpoczynku po stronie {team}: {a}h vs {b}h",
        ],
        "for": "za",
        "against": "przeciw",
        "home": "u siebie",
        "away": "na wyjeździe",
        "reason_no_report": "brak raportu jakości",
        "reason_no_summary": "brak podsumowania jakości",
        "reason_low_sample": "mała próbka ({bets})",
        "reason_clv_zero": "CLV coverage 0%",
        "reason_clv_low": "CLV coverage niskie ({pct})",
        "reason_brier": "Brier {value}",
        "reason_logloss": "LogLoss {value}",
        "selection_home_win": "Wygrana {team} (1)",
        "selection_draw": "Remis (X)",
        "selection_away_win": "Wygrana {team} (2)",
        "selection_over": "Total Powyżej 2.5",
        "selection_under": "Total Poniżej 2.5",
        "selection_over_1_5": "Total Powyżej 1.5",
        "selection_under_1_5": "Total Poniżej 1.5",
        "selection_over_3_5": "Total Powyżej 3.5",
        "selection_under_3_5": "Total Poniżej 3.5",
        "selection_btts_yes": "Obie strzelą — Tak",
        "selection_btts_no": "Obie strzelą — Nie",
        "selection_dc_1x": "Podwójna szansa 1X",
        "selection_dc_x2": "Podwójna szansa X2",
        "selection_dc_12": "Podwójna szansa 12",
    },
    "pt": {
        "hot_prediction": "🔥 PALPITE QUENTE 🔥",
        "prediction_label": {
            "hot": "🔥 PALPITE QUENTE",
            "standard": "✅ PALPITE PADRÃO",
            "cautious": "⚠️ PALPITE CAUTELOSO",
            "experimental": "🧪 PALPITE EXPERIMENTAL",
        },
        "prediction_label_variants": {
            "hot": ["🔥 PALPITE QUENTE", "🔥 PALPITE TOP", "🔥 PALPITE FORTE", "🔥 PALPITE EM ALTA"],
            "standard": ["✅ PALPITE PADRÃO", "✅ PALPITE BASE", "✅ PALPITE PRINCIPAL", "✅ PALPITE ESTÁVEL"],
            "cautious": ["⚠️ PALPITE CAUTELOSO", "⚠️ PALPITE PRUDENTE", "⚠️ PALPITE MODERADO", "⚠️ PALPITE CONSERVADOR"],
            "experimental": [
                "🧪 PALPITE EXPERIMENTAL",
                "🧪 PALPITE DE TESTE",
                "🧪 PALPITE BETA",
                "🧪 PALPITE DE ENSAIO",
            ],
        },
        "bet_label_by_tier": {
            "hot": "💰 APOSTA DO DIA",
            "standard": "💰 RECOMENDAÇÃO",
            "cautious": "⚠️ PALPITE CAUTELOSO",
            "experimental": "🧪 PALPITE EXPERIMENTAL",
        },
        "bet_of_day": "💰 APOSTA DO DIA",
        "model_probability": "Probabilidade do modelo",
        "why": "📊 POR QUE ISSO VAI DAR CERTO?",
        "why_variants": ["📊 POR QUE ISSO VAI DAR CERTO?", "📊 FATORES-CHAVE", "📊 PONTOS-CHAVE", "📊 FATORES PRINCIPAIS"],
        "current_form": "⚡ FORMA ATUAL (últimos 5 jogos)",
        "team_class": "🏆 CLASSE DAS EQUIPES (15 jogos)",
        "home_away_stats": "🏟️ ESTATÍSTICAS CASA/FORA",
        "fatigue_factor": "⏰ FATOR DE FADIGA",
        "value_indicators": "📈 INDICADORES DE VALUE BET",
        "value_variants": ["📈 INDICADORES DE VALUE BET", "📈 SINAIS DE VALUE", "📈 CHECK DE VALUE", "📈 VISÃO DE VALUE"],
        "risks": "⚠️ RISCOS",
        "risks_variants": ["⚠️ RISCOS", "⚠️ OBSERVAÇÕES", "⚠️ LIMITAÇÕES", "⚠️ ALERTAS"],
        "recommendation": "💡 RECOMENDAÇÃO",
        "recommendation_variants": ["💡 RECOMENDAÇÃO", "💡 RESUMO", "💡 CONCLUSÃO", "💡 FECHAMENTO"],
        "disclaimer": "⚠️ AVISO: Esta é uma previsão analítica, não uma garantia de resultado. "
        "As fórmulas do modelo são proprietárias e não são divulgadas.",
        "bookmakers_give": "🎲 As casas dão",
        "our_model": "🤖 Nosso modelo",
        "signal": "📊 Sinal do modelo",
        "signal_variants": ["📊 Sinal do modelo", "📊 Força do sinal", "📊 Intensidade do sinal", "📊 Nível do sinal"],
        "signal_notes": {"strong": "forte", "moderate": "moderado", "weak": "fraco"},
        "edge_short": "vantagem",
        "edge_strong": "🔥 Vantagem do modelo: {pct:.1f}%",
        "edge_good": "✅ Vantagem do modelo: {pct:.1f}%",
        "edge_thin": "⚠️ Vantagem do modelo: {pct:.1f}%",
        "edge_none": "⚪ Sem vantagem ({pct:.1f}%)",
        "edge_strong_variants": [
            "🔥 Vantagem do modelo: {pct:.1f}%",
            "🔥 Forte vantagem do modelo: {pct:.1f}%",
            "🔥 Clara vantagem do modelo: {pct:.1f}%",
            "🔥 Vantagem nítida do modelo: {pct:.1f}%",
        ],
        "edge_good_variants": [
            "✅ Vantagem do modelo: {pct:.1f}%",
            "✅ Vantagem a favor do modelo: {pct:.1f}%",
            "✅ Vantagem segundo o modelo: {pct:.1f}%",
            "✅ Leve vantagem do modelo: {pct:.1f}%",
        ],
        "edge_thin_variants": [
            "⚠️ Vantagem do modelo: {pct:.1f}%",
            "⚠️ Pequena vantagem: {pct:.1f}%",
            "⚠️ Vantagem pequena: {pct:.1f}%",
            "⚠️ Vantagem mínima: {pct:.1f}%",
        ],
        "edge_none_variants": [
            "⚪ Sem vantagem ({pct:.1f}%)",
            "⚪ Sem vantagem clara: {pct:.1f}%",
            "⚪ Pouca vantagem ({pct:.1f}%)",
            "⚪ Sem vantagem relevante ({pct:.1f}%)",
        ],
        "value_profile": "✅ Perfil de value",
        "value_profile_variants": ["✅ Perfil de value", "✅ Resumo de value", "✅ Leitura de value", "✅ Avaliação de value"],
        "value_unknown": "⚠️ Value não calculado — use cautela.",
        "value_strength": {
            "strong": "value forte",
            "good": "bom value",
            "thin": "value pequeno",
            "edge": "value no limite",
            "neg": "value negativo",
            "none": "value não avaliado",
        },
        "recommend": {
            "strong": "✅ A odd está muito atraente em {odd}.",
            "good": "✅ A odd está atraente em {odd}.",
            "thin": "⚠️ Value pequeno em {odd} — melhor confirmar com fatores adicionais.",
            "edge": "⚠️ Value no limite em {odd}.",
            "neg": "⛔ Value negativo em {odd} — melhor pular.",
        },
        "recommend_variants": {
            "strong": [
                "✅ A odd está muito atraente em {odd}.",
                "✅ Em {odd}, o value parece muito forte.",
                "✅ Ótima odd em {odd}.",
                "✅ {odd} oferece value muito forte.",
            ],
            "good": [
                "✅ A odd está atraente em {odd}.",
                "✅ Em {odd}, o value ainda é interessante.",
                "✅ {odd} ainda parece bom.",
                "✅ Boa odd em {odd}.",
            ],
            "thin": [
                "⚠️ Value pequeno em {odd} — melhor confirmar com fatores adicionais.",
                "⚠️ Em {odd}, o value é pequeno — melhor confirmar.",
                "⚠️ Value fraco em {odd} — melhor confirmar.",
                "⚠️ Value limitado em {odd} — melhor confirmar.",
            ],
            "edge": [
                "⚠️ Value no limite em {odd}.",
                "⚠️ Em {odd}, o value está no limite.",
                "⚠️ Value no limite em {odd}.",
                "⚠️ {odd} está no limite do value.",
            ],
            "neg": [
                "⛔ Value negativo em {odd} — melhor pular.",
                "⛔ Em {odd}, o value é negativo — melhor pular.",
                "⛔ Value negativo em {odd} — melhor evitar.",
                "⛔ {odd} gera value negativo — melhor pular.",
            ],
        },
        "recommend_cautious": {
            "strong": "⚠️ Bom potencial em {odd}, mas com cautela.",
            "good": "⚠️ Value interessante em {odd} — tenha cautela.",
            "thin": "⚠️ Value fraco em {odd} — melhor esperar confirmação.",
            "edge": "⚠️ Value no limite em {odd}.",
            "neg": "⛔ Value negativo em {odd} — melhor pular.",
        },
        "recommend_cautious_variants": {
            "strong": [
                "⚠️ Bom potencial em {odd}, mas com cautela.",
                "⚠️ Em {odd}, bom potencial — mas cautela.",
                "⚠️ Bom potencial em {odd}, porém cautela.",
                "⚠️ Potencial alto em {odd}, mas cautela.",
            ],
            "good": [
                "⚠️ Value interessante em {odd} — tenha cautela.",
                "⚠️ Em {odd}, o value é interessante — cautela.",
                "⚠️ Value interessante em {odd}, mas cautela.",
                "⚠️ Há value em {odd}, mas cautela.",
            ],
            "thin": [
                "⚠️ Value fraco em {odd} — melhor esperar confirmação.",
                "⚠️ Em {odd}, o value é fraco — melhor esperar.",
                "⚠️ Value pequeno em {odd} — melhor esperar.",
                "⚠️ Value limitado em {odd} — melhor esperar.",
            ],
            "edge": [
                "⚠️ Value no limite em {odd}.",
                "⚠️ Em {odd}, o value está no limite.",
                "⚠️ {odd} está no limite do value.",
                "⚠️ Value no limite em {odd}.",
            ],
            "neg": [
                "⛔ Value negativo em {odd} — melhor pular.",
                "⛔ Em {odd}, o value é negativo — melhor pular.",
                "⛔ Value negativo em {odd} — melhor evitar.",
                "⛔ {odd} gera value negativo — melhor pular.",
            ],
        },
        "line_watch": "📉 Observe a linha — abaixo de {odd}, o value desaparece.",
        "line_watch_variants": [
            "📉 Observe a linha — abaixo de {odd}, o value desaparece.",
            "📉 Se a odd cair abaixo de {odd}, o value desaparece.",
            "📉 Abaixo de {odd}, o value desaparece.",
            "📉 Em {odd} ou menos, o value desaparece.",
        ],
        "no_risks": "✅ Nenhum risco relevante identificado",
        "no_risks_variants": [
            "✅ Nenhum risco relevante identificado",
            "✅ Riscos parecem baixos",
            "✅ Nenhum risco crítico identificado",
            "✅ Sem grandes riscos identificados",
        ],
        "experimental_prefix": "EXPERIMENTAL — ",
        "attack_similar": "Os ataques são comparáveis",
        "attack_slight": "O ataque é ligeiramente melhor para {team}",
        "attack_strong": "O ataque é claramente melhor para {team}",
        "defense_similar": "As defesas estão no mesmo nível",
        "defense_slight": "A defesa é ligeiramente melhor para {team}",
        "defense_strong": "A defesa é claramente melhor para {team}",
        "venue_even": "Casa/fora sem viés claro",
        "venue_slight_home": "Fator casa levemente a favor de {team}",
        "venue_slight_away": "Fator fora ligeiramente melhor para {team}",
        "venue_strong_home": "Fator casa a favor de {team}",
        "venue_strong_away": "Fator fora mais forte para {team}",
        "rest_even": "Descanso aproximadamente igual",
        "rest_more": "✅ {team} descansou mais: {a}h vs {b}h",
        "attack_similar_variants": ["Os ataques são comparáveis", "A força ofensiva é similar", "Força ofensiva parecida", "Nível ofensivo similar"],
        "attack_slight_variants": [
            "O ataque é ligeiramente melhor para {team}",
            "Pequena vantagem ofensiva para {team}",
            "Leve vantagem ofensiva para {team}",
            "Ataque um pouco melhor para {team}",
        ],
        "attack_strong_variants": [
            "O ataque é claramente melhor para {team}",
            "Vantagem ofensiva clara para {team}",
            "Forte vantagem ofensiva para {team}",
            "Ataque muito superior para {team}",
        ],
        "defense_similar_variants": ["As defesas estão no mesmo nível", "A força defensiva é similar", "Força defensiva parecida", "Nível defensivo similar"],
        "defense_slight_variants": [
            "A defesa é ligeiramente melhor para {team}",
            "Pequena vantagem defensiva para {team}",
            "Leve vantagem defensiva para {team}",
            "Defesa um pouco melhor para {team}",
        ],
        "defense_strong_variants": [
            "A defesa é claramente melhor para {team}",
            "Vantagem defensiva clara para {team}",
            "Forte vantagem defensiva para {team}",
            "Defesa muito superior para {team}",
        ],
        "venue_even_variants": ["Casa/fora sem viés claro", "Sem viés claro casa/fora", "Sem tendência clara casa/fora", "Casa/fora equilibrado"],
        "venue_slight_home_variants": [
            "Fator casa levemente a favor de {team}",
            "Leve vantagem em casa para {team}",
            "Pequena vantagem em casa para {team}",
            "Vantagem leve em casa para {team}",
        ],
        "venue_slight_away_variants": [
            "Fator fora ligeiramente melhor para {team}",
            "Leve vantagem fora para {team}",
            "Pequena vantagem fora para {team}",
            "Vantagem leve fora para {team}",
        ],
        "venue_strong_home_variants": [
            "Fator casa a favor de {team}",
            "Forte vantagem em casa para {team}",
            "Vantagem forte em casa para {team}",
            "Vantagem clara em casa para {team}",
        ],
        "venue_strong_away_variants": [
            "Fator fora mais forte para {team}",
            "Forte vantagem fora para {team}",
            "Vantagem forte fora para {team}",
            "Vantagem clara fora para {team}",
        ],
        "rest_even_variants": ["Descanso aproximadamente igual", "Descanso parecido", "Descanso equilibrado", "Descanso similar"],
        "rest_more_variants": [
            "✅ {team} descansou mais: {a}h vs {b}h",
            "✅ {team} teve mais descanso: {a}h vs {b}h",
            "✅ {team} teve vantagem de descanso: {a}h vs {b}h",
            "✅ Vantagem de descanso para {team}: {a}h vs {b}h",
        ],
        "for": "a favor",
        "against": "contra",
        "home": "em casa",
        "away": "fora",
        "reason_no_report": "sem relatório de qualidade",
        "reason_no_summary": "sem resumo de qualidade",
        "reason_low_sample": "amostra pequena ({bets})",
        "reason_clv_zero": "CLV coverage 0%",
        "reason_clv_low": "CLV coverage baixo ({pct})",
        "reason_brier": "Brier {value}",
        "reason_logloss": "LogLoss {value}",
        "selection_home_win": "Vitória {team} (1)",
        "selection_draw": "Empate (X)",
        "selection_away_win": "Vitória {team} (2)",
        "selection_over": "Total Acima de 2.5",
        "selection_under": "Total Abaixo de 2.5",
        "selection_over_1_5": "Total Acima de 1.5",
        "selection_under_1_5": "Total Abaixo de 1.5",
        "selection_over_3_5": "Total Acima de 3.5",
        "selection_under_3_5": "Total Abaixo de 3.5",
        "selection_btts_yes": "Ambas marcam — Sim",
        "selection_btts_no": "Ambas marcam — Não",
        "selection_dc_1x": "Dupla hipótese 1X",
        "selection_dc_x2": "Dupla hipótese X2",
        "selection_dc_12": "Dupla hipótese 12",
    },
    "es": {
        "hot_prediction": "🔥 PRONÓSTICO CALIENTE 🔥",
        "prediction_label": {
            "hot": "🔥 PRONÓSTICO CALIENTE",
            "standard": "✅ PRONÓSTICO ESTÁNDAR",
            "cautious": "⚠️ PRONÓSTICO PRUDENTE",
            "experimental": "🧪 PRONÓSTICO EXPERIMENTAL",
        },
        "prediction_label_variants": {
            "hot": ["🔥 PRONÓSTICO CALIENTE", "🔥 PRONÓSTICO TOP", "🔥 PRONÓSTICO FUERTE", "🔥 PRONÓSTICO DESTACADO"],
            "standard": ["✅ PRONÓSTICO ESTÁNDAR", "✅ PRONÓSTICO BASE", "✅ PRONÓSTICO PRINCIPAL", "✅ PRONÓSTICO ESTABLE"],
            "cautious": [
                "⚠️ PRONÓSTICO PRUDENTE",
                "⚠️ PRONÓSTICO CAUTELOSO",
                "⚠️ PRONÓSTICO MODERADO",
                "⚠️ PRONÓSTICO CONSERVADOR",
            ],
            "experimental": [
                "🧪 PRONÓSTICO EXPERIMENTAL",
                "🧪 PRONÓSTICO DE PRUEBA",
                "🧪 PRONÓSTICO BETA",
                "🧪 PRONÓSTICO DE ENSAYO",
            ],
        },
        "bet_label_by_tier": {
            "hot": "💰 APUESTA DEL DÍA",
            "standard": "💰 RECOMENDACIÓN",
            "cautious": "⚠️ APUESTA PRUDENTE",
            "experimental": "🧪 APUESTA EXPERIMENTAL",
        },
        "bet_of_day": "💰 APUESTA DEL DÍA",
        "model_probability": "Probabilidad del modelo",
        "why": "📊 ¿POR QUÉ ENTRARÁ?",
        "why_variants": ["📊 ¿POR QUÉ ENTRARÁ?", "📊 FACTORES CLAVE", "📊 ARGUMENTOS CLAVE", "📊 FACTORES PRINCIPALES"],
        "current_form": "⚡ FORMA ACTUAL (últimos 5 partidos)",
        "team_class": "🏆 CLASE DE LOS EQUIPOS (15 partidos)",
        "home_away_stats": "🏟️ ESTADÍSTICAS CASA/FUERA",
        "fatigue_factor": "⏰ FACTOR DE FATIGA",
        "value_indicators": "📈 INDICADORES DE VALUE BET",
        "value_variants": ["📈 INDICADORES DE VALUE BET", "📈 SEÑALES DE VALUE", "📈 CHEQUEO DE VALUE", "📈 RESUMEN DE VALUE"],
        "risks": "⚠️ RIESGOS",
        "risks_variants": ["⚠️ RIESGOS", "⚠️ NOTAS", "⚠️ LIMITACIONES", "⚠️ ADVERTENCIAS"],
        "recommendation": "💡 RECOMENDACIÓN",
        "recommendation_variants": ["💡 RECOMENDACIÓN", "💡 RESUMEN", "💡 CONCLUSIÓN", "💡 CIERRE"],
        "disclaimer": "⚠️ DESCARGO: Esto es un pronóstico analítico, no una garantía de resultado. "
        "Las fórmulas del modelo son propietarias y no se revelan.",
        "bookmakers_give": "🎲 Las casas dan",
        "our_model": "🤖 Nuestro modelo",
        "signal": "📊 Señal del modelo",
        "signal_variants": ["📊 Señal del modelo", "📊 Fuerza de la señal", "📊 Intensidad de la señal", "📊 Nivel de la señal"],
        "signal_notes": {"strong": "fuerte", "moderate": "moderada", "weak": "débil"},
        "edge_short": "ventaja",
        "edge_strong": "🔥 Ventaja del modelo: {pct:.1f}%",
        "edge_good": "✅ Ventaja del modelo: {pct:.1f}%",
        "edge_thin": "⚠️ Ventaja del modelo: {pct:.1f}%",
        "edge_none": "⚪ Sin ventaja ({pct:.1f}%)",
        "edge_strong_variants": [
            "🔥 Ventaja del modelo: {pct:.1f}%",
            "🔥 Fuerte ventaja del modelo: {pct:.1f}%",
            "🔥 Clara ventaja del modelo: {pct:.1f}%",
            "🔥 Ventaja nítida del modelo: {pct:.1f}%",
        ],
        "edge_good_variants": [
            "✅ Ventaja del modelo: {pct:.1f}%",
            "✅ Ventaja a favor del modelo: {pct:.1f}%",
            "✅ Ventaja según el modelo: {pct:.1f}%",
            "✅ Ligera ventaja del modelo: {pct:.1f}%",
        ],
        "edge_thin_variants": [
            "⚠️ Ventaja del modelo: {pct:.1f}%",
            "⚠️ Ventaja pequeña: {pct:.1f}%",
            "⚠️ Ventaja menor: {pct:.1f}%",
            "⚠️ Ventaja mínima: {pct:.1f}%",
        ],
        "edge_none_variants": [
            "⚪ Sin ventaja ({pct:.1f}%)",
            "⚪ Sin ventaja clara: {pct:.1f}%",
            "⚪ Poca ventaja ({pct:.1f}%)",
            "⚪ Sin ventaja relevante ({pct:.1f}%)",
        ],
        "value_profile": "✅ Perfil de value",
        "value_profile_variants": ["✅ Perfil de value", "✅ Resumen de value", "✅ Lectura de value", "✅ Evaluación de value"],
        "value_unknown": "⚠️ Value no calculado — use precaución.",
        "value_strength": {
            "strong": "value fuerte",
            "good": "buen value",
            "thin": "value fino",
            "edge": "value al límite",
            "neg": "value negativo",
            "none": "value no evaluado",
        },
        "recommend": {
            "strong": "✅ La cuota se ve muy atractiva en {odd}.",
            "good": "✅ La cuota se ve atractiva en {odd}.",
            "thin": "⚠️ Value pequeño en {odd} — mejor confirmar con factores extra.",
            "edge": "⚠️ Value al límite en {odd}.",
            "neg": "⛔ Value negativo en {odd} — mejor pasar.",
        },
        "recommend_variants": {
            "strong": [
                "✅ La cuota se ve muy atractiva en {odd}.",
                "✅ En {odd}, el value se ve muy fuerte.",
                "✅ Muy buena cuota en {odd}.",
                "✅ {odd} ofrece un value muy fuerte.",
            ],
            "good": [
                "✅ La cuota se ve atractiva en {odd}.",
                "✅ En {odd}, el value sigue siendo interesante.",
                "✅ {odd} aún es una buena cuota.",
                "✅ Buena cuota en {odd}.",
            ],
            "thin": [
                "⚠️ Value pequeño en {odd} — mejor confirmar con factores extra.",
                "⚠️ En {odd}, el value es pequeño — mejor confirmar.",
                "⚠️ Value limitado en {odd} — mejor confirmar.",
                "⚠️ Value débil en {odd} — mejor confirmar.",
            ],
            "edge": [
                "⚠️ Value al límite en {odd}.",
                "⚠️ En {odd}, el value está al límite.",
                "⚠️ Value al límite en {odd}.",
                "⚠️ {odd} está al límite del value.",
            ],
            "neg": [
                "⛔ Value negativo en {odd} — mejor pasar.",
                "⛔ En {odd}, el value es negativo — mejor pasar.",
                "⛔ Value negativo en {odd} — mejor evitar.",
                "⛔ {odd} genera value negativo — mejor pasar.",
            ],
        },
        "recommend_cautious": {
            "strong": "⚠️ Buen potencial en {odd}, pero con cautela.",
            "good": "⚠️ Value interesante en {odd} — con cautela.",
            "thin": "⚠️ Value débil en {odd} — mejor esperar confirmación.",
            "edge": "⚠️ Value al límite en {odd}.",
            "neg": "⛔ Value negativo en {odd} — mejor pasar.",
        },
        "recommend_cautious_variants": {
            "strong": [
                "⚠️ Buen potencial en {odd}, pero con cautela.",
                "⚠️ En {odd}, buen potencial — pero con cautela.",
                "⚠️ Buen potencial en {odd}, aunque con cautela.",
                "⚠️ Potencial alto en {odd}, pero con cautela.",
            ],
            "good": [
                "⚠️ Value interesante en {odd} — con cautela.",
                "⚠️ En {odd}, el value es interesante — con cautela.",
                "⚠️ Value interesante en {odd}, pero con cautela.",
                "⚠️ Hay value en {odd}, pero con cautela.",
            ],
            "thin": [
                "⚠️ Value débil en {odd} — mejor esperar confirmación.",
                "⚠️ En {odd}, el value es débil — mejor esperar.",
                "⚠️ Value pequeño en {odd} — mejor esperar.",
                "⚠️ Value limitado en {odd} — mejor esperar.",
            ],
            "edge": [
                "⚠️ Value al límite en {odd}.",
                "⚠️ En {odd}, el value está al límite.",
                "⚠️ {odd} está al límite del value.",
                "⚠️ Value al límite en {odd}.",
            ],
            "neg": [
                "⛔ Value negativo en {odd} — mejor pasar.",
                "⛔ En {odd}, el value es negativo — mejor pasar.",
                "⛔ Value negativo en {odd} — mejor evitar.",
                "⛔ {odd} genera value negativo — mejor pasar.",
            ],
        },
        "line_watch": "📉 Vigila la línea — por debajo de {odd}, el value desaparece.",
        "line_watch_variants": [
            "📉 Vigila la línea — por debajo de {odd}, el value desaparece.",
            "📉 Si la cuota baja de {odd}, el value desaparece.",
            "📉 Por debajo de {odd}, el value desaparece.",
            "📉 En {odd} o menos, el value desaparece.",
        ],
        "no_risks": "✅ No se detectan riesgos importantes",
        "no_risks_variants": [
            "✅ No se detectan riesgos importantes",
            "✅ Los riesgos parecen bajos",
            "✅ No se ven riesgos críticos",
            "✅ No se aprecian grandes riesgos",
        ],
        "experimental_prefix": "EXPERIMENTAL — ",
        "attack_similar": "Los ataques son comparables",
        "attack_slight": "El ataque es ligeramente mejor para {team}",
        "attack_strong": "El ataque es claramente mejor para {team}",
        "defense_similar": "Las defensas están al mismo nivel",
        "defense_slight": "La defensa es ligeramente mejor para {team}",
        "defense_strong": "La defensa es claramente mejor para {team}",
        "venue_even": "Casa/fuera sin sesgo claro",
        "venue_slight_home": "La ventaja de local favorece ligeramente a {team}",
        "venue_slight_away": "La ventaja de visitante favorece ligeramente a {team}",
        "venue_strong_home": "La ventaja de local favorece a {team}",
        "venue_strong_away": "La ventaja de visitante favorece a {team}",
        "rest_even": "Descanso aproximadamente igual",
        "rest_more": "✅ {team} descansó más: {a}h vs {b}h",
        "attack_similar_variants": [
            "Los ataques son comparables",
            "La fuerza ofensiva es similar",
            "La fuerza ofensiva es pareja",
            "Nivel ofensivo parecido",
        ],
        "attack_slight_variants": [
            "El ataque es ligeramente mejor para {team}",
            "Pequeña ventaja ofensiva para {team}",
            "Ligera ventaja ofensiva para {team}",
            "Ataque algo mejor para {team}",
        ],
        "attack_strong_variants": [
            "El ataque es claramente mejor para {team}",
            "Ventaja ofensiva clara para {team}",
            "Fuerte ventaja ofensiva para {team}",
            "Ataque claramente superior para {team}",
        ],
        "defense_similar_variants": [
            "Las defensas están al mismo nivel",
            "La fuerza defensiva es similar",
            "La fuerza defensiva es pareja",
            "Nivel defensivo parecido",
        ],
        "defense_slight_variants": [
            "La defensa es ligeramente mejor para {team}",
            "Pequeña ventaja defensiva para {team}",
            "Ligera ventaja defensiva para {team}",
            "Defensa algo mejor para {team}",
        ],
        "defense_strong_variants": [
            "La defensa es claramente mejor para {team}",
            "Ventaja defensiva clara para {team}",
            "Fuerte ventaja defensiva para {team}",
            "Defensa claramente superior para {team}",
        ],
        "venue_even_variants": ["Casa/fuera sin sesgo claro", "Sin sesgo claro casa/fuera", "Sin inclinación clara local/visitante", "Casa/fuera equilibrado"],
        "venue_slight_home_variants": [
            "La ventaja de local favorece ligeramente a {team}",
            "Leve ventaja de local para {team}",
            "Pequeña ventaja de local para {team}",
            "Ligera ventaja de local para {team}",
        ],
        "venue_slight_away_variants": [
            "La ventaja de visitante favorece ligeramente a {team}",
            "Leve ventaja de visitante para {team}",
            "Pequeña ventaja de visitante para {team}",
            "Ligera ventaja de visitante para {team}",
        ],
        "venue_strong_home_variants": [
            "La ventaja de local favorece a {team}",
            "Fuerte ventaja de local para {team}",
            "Gran ventaja de local para {team}",
            "Ventaja clara de local para {team}",
        ],
        "venue_strong_away_variants": [
            "La ventaja de visitante favorece a {team}",
            "Fuerte ventaja de visitante para {team}",
            "Gran ventaja de visitante para {team}",
            "Ventaja clara de visitante para {team}",
        ],
        "rest_even_variants": ["Descanso aproximadamente igual", "Descanso similar", "Descanso equilibrado", "Descanso parejo"],
        "rest_more_variants": [
            "✅ {team} descansó más: {a}h vs {b}h",
            "✅ {team} tuvo más descanso: {a}h vs {b}h",
            "✅ {team} tuvo ventaja de descanso: {a}h vs {b}h",
            "✅ Ventaja de descanso para {team}: {a}h vs {b}h",
        ],
        "for": "a favor",
        "against": "en contra",
        "home": "en casa",
        "away": "fuera",
        "reason_no_report": "sin informe de calidad",
        "reason_no_summary": "sin resumen de calidad",
        "reason_low_sample": "muestra pequeña ({bets})",
        "reason_clv_zero": "CLV coverage 0%",
        "reason_clv_low": "CLV coverage bajo ({pct})",
        "reason_brier": "Brier {value}",
        "reason_logloss": "LogLoss {value}",
        "selection_home_win": "Victoria {team} (1)",
        "selection_draw": "Empate (X)",
        "selection_away_win": "Victoria {team} (2)",
        "selection_over": "Total Más de 2.5",
        "selection_under": "Total Menos de 2.5",
        "selection_over_1_5": "Total Más de 1.5",
        "selection_under_1_5": "Total Menos de 1.5",
        "selection_over_3_5": "Total Más de 3.5",
        "selection_under_3_5": "Total Menos de 3.5",
        "selection_btts_yes": "Ambos marcan — Sí",
        "selection_btts_no": "Ambos marcan — No",
        "selection_dc_1x": "Doble oportunidad 1X",
        "selection_dc_x2": "Doble oportunidad X2",
        "selection_dc_12": "Doble oportunidad 12",
    },
}


@dataclass
class MarketPreview:
    market: str
    headline_raw: str
    analysis_raw: str
    headline: str
    analysis: str
    experimental: bool
    quality_level: int
    reasons: list[str]


@dataclass
class ImageVisualContext:
    league_country: str | None = None
    league_round: str | None = None
    venue_name: str | None = None
    venue_city: str | None = None
    home_rank: int | None = None
    away_rank: int | None = None
    home_points: int | None = None
    away_points: int | None = None
    home_played: int | None = None
    away_played: int | None = None
    home_goal_diff: int | None = None
    away_goal_diff: int | None = None
    home_form: str | None = None
    away_form: str | None = None


def _escape_html(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _protect(value: str) -> str:
    return f"<x>{_escape_html(value)}</x>"


def _strip_protect_tags(text: str) -> str:
    return text.replace("<x>", "").replace("</x>", "")


def _lang_key(lang: str | None) -> str:
    key = (lang or "ru").strip().lower()
    return key if key in _LANG_TEXT else "ru"


def _lang_pack(lang: str | None) -> dict[str, Any]:
    return _LANG_TEXT[_lang_key(lang)]


def _extract_protected_values(text: str) -> list[str]:
    if not text:
        return []
    return [m.group(1) for m in re.finditer(r"<x>(.*?)</x>", text)]


def _prepare_translation_html(text: str) -> str:
    if not text:
        return text
    out = text.replace("\r\n", "\n")
    return out.replace("\n", "<br/>")


def _restore_translated_html(text: str) -> str:
    if not text:
        return text
    out = text.replace("\r\n", "\n")
    out = out.replace("\n", " ")
    out = re.sub(r"<br\s*/?>", "\n", out, flags=re.IGNORECASE)
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r" *\n *", "\n", out)
    return out.strip()


def _normalize_translated_text(text: str, protected: list[str]) -> str:
    if not text:
        return text
    out = text.replace("\r\n", "\n")
    out = re.sub(r"\n\s*:", ":", out)
    for value in protected:
        if not value:
            continue
        escaped = re.escape(value)
        out = re.sub(rf"{escaped}\n\s*:", f"{value}:", out)
        out = re.sub(rf"(?<=\\w){escaped}", f" {value}", out)
        out = re.sub(rf"{escaped}(?=\\w)", f"{value} ", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\s+vs\s+", " vs ", out, flags=re.IGNORECASE)
    return out


def _strip_image_probability_line(text: str) -> str:
    if not text:
        return text
    lines = []
    for line in text.splitlines():
        if line.lstrip().startswith("🎯"):
            continue
        lines.append(line)
    return "\n".join(lines)


async def _fetch_logo_bytes(url: str | None) -> bytes | None:
    if not url:
        return None
    key = url.strip()
    if not key:
        return None
    cached = _logo_cache.get(key)
    if cached:
        return cached
    client = assets_client()
    try:
        resp = await request_with_retries(
            client,
            "GET",
            key,
            retries=2,
            backoff_base=0.4,
            backoff_max=2.0,
        )
        if resp.status_code < 200 or resp.status_code >= 300:
            await resp.aclose()
            return None
        data = await resp.aread()
        await resp.aclose()
        if not data or len(data) > _LOGO_MAX_BYTES:
            return None
        _logo_cache[key] = data
        return data
    except Exception:
        log.exception("logo_fetch_failed url=%s", key)
        return None


def _to_int_or_none(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except Exception:
        return None


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _payload_dict(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _extract_1x2_chances(payload: Any) -> tuple[float | None, float | None, float | None]:
    data = _payload_dict(payload)
    if not data:
        return None, None, None
    candidates = data.get("candidates")
    if not isinstance(candidates, list):
        return None, None, None

    probs: dict[str, float] = {}
    for item in candidates:
        if not isinstance(item, dict):
            continue
        selection = str(item.get("selection") or "").strip().upper()
        if selection not in {"HOME_WIN", "DRAW", "AWAY_WIN"}:
            continue
        try:
            value = float(item.get("prob"))
        except Exception:
            continue
        if value < 0:
            continue
        probs[selection] = value

    return probs.get("HOME_WIN"), probs.get("DRAW"), probs.get("AWAY_WIN")


def _extract_standing_row(payload: dict, team_id: int) -> dict | None:
    response = payload.get("response") or []
    if not isinstance(response, list):
        return None
    for item in response:
        league = (item or {}).get("league") if isinstance(item, dict) else None
        standings = (league or {}).get("standings") if isinstance(league, dict) else None
        if not isinstance(standings, list):
            continue
        for group in standings:
            if not isinstance(group, list):
                continue
            for row in group:
                if not isinstance(row, dict):
                    continue
                team = row.get("team") or {}
                if _to_int_or_none(team.get("id")) == int(team_id):
                    return row
    return None


async def _fetch_image_visual_context(session: AsyncSession, fixture: Any) -> ImageVisualContext:
    ctx = ImageVisualContext()

    fixture_id = _to_int_or_none(getattr(fixture, "id", None))
    league_id = _to_int_or_none(getattr(fixture, "league_id", None))
    season = _to_int_or_none(getattr(fixture, "season", None)) or _to_int_or_none(getattr(settings, "season", None))
    home_team_id = _to_int_or_none(getattr(fixture, "home_team_id", None))
    away_team_id = _to_int_or_none(getattr(fixture, "away_team_id", None))

    if fixture_id:
        try:
            fixture_payload = await get_fixture_by_id(
                session,
                int(fixture_id),
                metric_league_id=int(league_id) if league_id is not None else None,
            )
            response = fixture_payload.get("response") or []
            item = response[0] if isinstance(response, list) and response else {}
            fx = (item or {}).get("fixture") if isinstance(item, dict) else None
            lg = (item or {}).get("league") if isinstance(item, dict) else None
            venue = (fx or {}).get("venue") if isinstance(fx, dict) else None

            if isinstance(lg, dict):
                ctx.league_country = _clean_text(lg.get("country"))
                ctx.league_round = _clean_text(lg.get("round"))
            if isinstance(venue, dict):
                ctx.venue_name = _clean_text(venue.get("name"))
                ctx.venue_city = _clean_text(venue.get("city"))
        except Exception:
            log.exception("image_visual_fixture_context_failed fixture=%s", fixture_id)

    if league_id and season and home_team_id and away_team_id:
        try:
            standings_payload = await get_standings(session, int(league_id), int(season))
            home_row = _extract_standing_row(standings_payload, int(home_team_id))
            away_row = _extract_standing_row(standings_payload, int(away_team_id))

            if isinstance(home_row, dict):
                all_stats = home_row.get("all") or {}
                ctx.home_rank = _to_int_or_none(home_row.get("rank"))
                ctx.home_points = _to_int_or_none(home_row.get("points"))
                ctx.home_goal_diff = _to_int_or_none(home_row.get("goalsDiff"))
                ctx.home_form = _clean_text(home_row.get("form"))
                if isinstance(all_stats, dict):
                    ctx.home_played = _to_int_or_none(all_stats.get("played"))

            if isinstance(away_row, dict):
                all_stats = away_row.get("all") or {}
                ctx.away_rank = _to_int_or_none(away_row.get("rank"))
                ctx.away_points = _to_int_or_none(away_row.get("points"))
                ctx.away_goal_diff = _to_int_or_none(away_row.get("goalsDiff"))
                ctx.away_form = _clean_text(away_row.get("form"))
                if isinstance(all_stats, dict):
                    ctx.away_played = _to_int_or_none(all_stats.get("played"))
        except Exception:
            log.exception("image_visual_standings_context_failed fixture=%s league=%s", fixture_id, league_id)

    return ctx


def _translate_reason(reason: str, lang: str | None) -> str:
    pack = _lang_pack(lang)
    raw = (reason or "").strip()
    if not raw:
        return raw
    if raw == "нет отчёта качества":
        return pack["reason_no_report"]
    if raw == "нет сводки качества":
        return pack["reason_no_summary"]
    if raw == "CLV coverage 0%":
        return pack["reason_clv_zero"]
    match = re.search(r"\(([^)]+)\)", raw)
    if raw.startswith("малый объём"):
        bets = match.group(1) if match else raw
        return pack["reason_low_sample"].format(bets=bets)
    if raw.startswith("CLV coverage низкий"):
        pct = match.group(1) if match else raw
        return pack["reason_clv_low"].format(pct=pct)
    m_brier = re.search(r"Brier\s+([0-9.]+)", raw)
    if m_brier:
        return pack["reason_brier"].format(value=m_brier.group(1))
    m_logloss = re.search(r"LogLoss\s+([0-9.]+)", raw)
    if m_logloss:
        return pack["reason_logloss"].format(value=m_logloss.group(1))
    return raw


def _translate_reasons(reasons: list[str], lang: str | None) -> list[str]:
    return [_translate_reason(reason, lang) for reason in reasons or [] if reason]


def _fmt_float(value: Any, digits: int = 2) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):.{digits}f}"
    except Exception:
        return "—"


def _fmt_percent(value: Any, digits: int = 1) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value) * 100:.{digits}f}%"
    except Exception:
        return "—"


def _fmt_percent100(value: Any, digits: int = 1) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):.{digits}f}%"
    except Exception:
        return "—"


def _plain_indicator_text(value: str | None) -> str:
    text = str(value or "").strip()
    text = re.sub(r"^[^\wА-Яа-я0-9]+", "", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def _split_message(text: str, max_len: int = 3900) -> list[str]:
    if len(text) <= max_len:
        return [text]
    parts: list[str] = []
    current = ""
    paragraphs = text.split("\n\n")
    for para in paragraphs:
        chunk = para.strip("\n")
        if not chunk:
            continue
        candidate = f"{current}\n\n{chunk}" if current else chunk
        if len(candidate) <= max_len:
            current = candidate
            continue
        if current:
            parts.append(current)
            current = ""
        if len(chunk) <= max_len:
            current = chunk
            continue
        lines = chunk.split("\n")
        line_buf = ""
        for line in lines:
            cand = f"{line_buf}\n{line}" if line_buf else line
            if len(cand) <= max_len:
                line_buf = cand
                continue
            if line_buf:
                parts.append(line_buf)
                line_buf = ""
            if len(line) <= max_len:
                line_buf = line
                continue
            for i in range(0, len(line), max_len):
                parts.append(line[i : i + max_len])
        if line_buf:
            current = line_buf
    if current:
        parts.append(current)
    return [p for p in parts if p]


_SELECTION_LABEL_MAP = {
    "OVER_2_5": "selection_over",
    "UNDER_2_5": "selection_under",
    "OVER_1_5": "selection_over_1_5",
    "UNDER_1_5": "selection_under_1_5",
    "OVER_3_5": "selection_over_3_5",
    "UNDER_3_5": "selection_under_3_5",
    "BTTS_YES": "selection_btts_yes",
    "BTTS_NO": "selection_btts_no",
    "DC_1X": "selection_dc_1x",
    "DC_X2": "selection_dc_x2",
    "DC_12": "selection_dc_12",
}


def _selection_label(selection: str, market: str, lang: str | None) -> str:
    pack = _lang_pack(lang)
    if market == "1X2":
        return {"HOME_WIN": "1", "DRAW": "X", "AWAY_WIN": "2"}.get(selection, selection)
    pack_key = _SELECTION_LABEL_MAP.get(selection)
    if pack_key and pack_key in pack:
        return pack[pack_key]
    return selection


def _extract_selection(pred: Any) -> str:
    return str(getattr(pred, "selection_code", "") or getattr(pred, "selection", "") or "").strip()


def _is_skip_selection(selection: str) -> bool:
    return selection.strip().upper() == "SKIP"


def _format_kickoff(kickoff: Any, lang: str | None) -> str:
    if not kickoff:
        return "—"
    if getattr(kickoff, "tzinfo", None) is None:
        kickoff = kickoff.replace(tzinfo=timezone.utc)
    dt = kickoff.astimezone(timezone.utc)
    months = _LANG_MONTHS.get(_lang_key(lang), _LANG_MONTHS["ru"])
    month = months[dt.month - 1] if 1 <= dt.month <= 12 else ""
    return f"{dt.day} {month} {dt.year} | {dt:%H:%M} UTC"


def _selection_phrase(selection: str, market: str, home: str, away: str, lang: str | None) -> str:
    pack = _lang_pack(lang)
    if market == "1X2":
        if selection == "HOME_WIN":
            return pack["selection_home_win"].format(team=home)
        if selection == "DRAW":
            return pack["selection_draw"]
        if selection == "AWAY_WIN":
            return pack["selection_away_win"].format(team=away)
    pack_key = _SELECTION_LABEL_MAP.get(selection)
    if pack_key and pack_key in pack:
        return pack[pack_key]
    return selection


def _fmt_value(ev: float | None) -> str:
    if ev is None:
        return "—"
    return f"{ev * 100:+.1f}%"


def _prediction_tier(ev: float | None, signal: Any, experimental: bool) -> str:
    if experimental:
        return "experimental"
    ev_pct = ev * 100 if ev is not None else None
    try:
        signal_pct = float(signal) * 100 if signal is not None else None
    except Exception:
        signal_pct = None
    if (ev_pct is not None and ev_pct >= _VALUE_STRONG_PCT) or (
        signal_pct is not None and signal_pct >= _SIGNAL_STRONG_PCT
    ):
        return "hot"
    if (ev_pct is not None and ev_pct >= _VALUE_GOOD_PCT) or (
        signal_pct is not None and signal_pct >= _SIGNAL_MED_PCT
    ):
        return "standard"
    return "cautious"


def _prediction_label(pack: dict[str, Any], tier: str, seed: str) -> str:
    variant_map = pack.get("prediction_label_variants") or {}
    if isinstance(variant_map, dict):
        variants = variant_map.get(tier)
        if isinstance(variants, list) and variants:
            return _variant_from_list(variants, pack.get("hot_prediction", "HOT PREDICTION"), f"{seed}:title:{tier}")
    labels = pack.get("prediction_label") or {}
    if isinstance(labels, dict):
        label = labels.get(tier)
        if label:
            return label
    return pack.get("hot_prediction", "HOT PREDICTION")


def _variant_text(pack: dict[str, Any], key: str, default: str, seed: str) -> str:
    variants = pack.get(key)
    if isinstance(variants, list) and variants:
        idx = int(hashlib.sha256(seed.encode("utf-8", errors="ignore")).hexdigest(), 16) % len(variants)
        return variants[idx]
    if isinstance(variants, str) and variants:
        return variants
    return default


def _variant_from_list(variants: list[str] | None, default: str, seed: str) -> str:
    if isinstance(variants, list) and variants:
        idx = int(hashlib.sha256(seed.encode("utf-8", errors="ignore")).hexdigest(), 16) % len(variants)
        return variants[idx]
    return default


def _bet_label(pack: dict[str, Any], tier: str) -> str:
    labels = pack.get("bet_label_by_tier") or {}
    if isinstance(labels, dict):
        label = labels.get(tier)
        if label:
            return label
    return pack.get("bet_of_day", "BET OF THE DAY")


def _value_strength(ev: float | None, lang: str | None) -> str:
    pack = _lang_pack(lang)
    if ev is None:
        return pack["value_strength"]["none"]
    pct = ev * 100
    if pct >= _VALUE_STRONG_PCT:
        return pack["value_strength"]["strong"]
    if pct >= _VALUE_GOOD_PCT:
        return pack["value_strength"]["good"]
    if pct >= _VALUE_THIN_PCT:
        return pack["value_strength"]["thin"]
    if pct > 0:
        return pack["value_strength"]["edge"]
    return pack["value_strength"]["neg"]


def _recommendation_line(
    ev: float | None, odd: Decimal | None, lang: str | None, tier: str, seed: str
) -> str | None:
    pack = _lang_pack(lang)
    recommend = pack.get("recommend") or {}
    recommend_variants = pack.get("recommend_variants") or {}
    if tier in {"cautious", "experimental"}:
        recommend = pack.get("recommend_cautious") or recommend
        recommend_variants = pack.get("recommend_cautious_variants") or recommend_variants
    if ev is None or odd is None:
        return pack["value_unknown"]
    try:
        pct = ev * 100
    except Exception:
        return pack["value_unknown"]
    odd_val = _fmt_float(odd, 2)
    kind = "neg"
    if pct >= _VALUE_STRONG_PCT:
        kind = "strong"
    elif pct >= _VALUE_GOOD_PCT:
        kind = "good"
    elif pct >= _VALUE_THIN_PCT:
        kind = "thin"
    elif pct > 0:
        kind = "edge"
    fallback = recommend.get(kind, pack["recommend"].get(kind, pack["value_unknown"]))
    variants = recommend_variants.get(kind) if isinstance(recommend_variants, dict) else None
    template = _variant_from_list(variants, fallback, f"{seed}:recommend:{kind}")
    return template.format(odd=odd_val)


def _signal_line(signal: Any, lang: str | None, seed: str) -> str | None:
    if signal is None:
        return None
    try:
        pct = float(signal) * 100
    except Exception:
        return None
    pack = _lang_pack(lang)
    label = _variant_text(pack, "signal_variants", pack["signal"], f"{seed}:signal")
    note = (
        pack["signal_notes"]["strong"]
        if pct >= _SIGNAL_STRONG_PCT
        else pack["signal_notes"]["moderate"]
        if pct >= _SIGNAL_MED_PCT
        else pack["signal_notes"]["weak"]
    )
    return f"{label}: {pct:.1f}% ({note})"


def _edge_line(edge: float | None, lang: str | None, seed: str) -> str | None:
    if edge is None:
        return None
    pct = edge * 100
    pack = _lang_pack(lang)
    if pct >= 5:
        template = _variant_text(pack, "edge_strong_variants", pack["edge_strong"], f"{seed}:edge:strong")
        return template.format(pct=pct)
    if pct >= 2:
        template = _variant_text(pack, "edge_good_variants", pack["edge_good"], f"{seed}:edge:good")
        return template.format(pct=pct)
    if pct > 0:
        template = _variant_text(pack, "edge_thin_variants", pack["edge_thin"], f"{seed}:edge:thin")
        return template.format(pct=pct)
    template = _variant_text(pack, "edge_none_variants", pack["edge_none"], f"{seed}:edge:none")
    return template.format(pct=pct)


def _comment_attack(home_for: Any, away_for: Any, home: str, away: str, lang: str | None) -> str | None:
    if home_for is None or away_for is None:
        return None
    try:
        diff = float(home_for) - float(away_for)
    except Exception:
        return None
    pack = _lang_pack(lang)
    seed_base = f"attack:{home}:{away}:{home_for}:{away_for}:{lang}"
    if abs(diff) < _STAT_DIFF_MINOR:
        return _variant_text(pack, "attack_similar_variants", pack["attack_similar"], seed_base)
    if abs(diff) < _STAT_DIFF_MAJOR:
        team = home if diff > 0 else away
        text = _variant_text(pack, "attack_slight_variants", pack["attack_slight"], seed_base)
        return text.format(team=team)
    team = home if diff > 0 else away
    text = _variant_text(pack, "attack_strong_variants", pack["attack_strong"], seed_base)
    return text.format(team=team)


def _comment_defense(home_against: Any, away_against: Any, home: str, away: str, lang: str | None) -> str | None:
    if home_against is None or away_against is None:
        return None
    try:
        diff = float(away_against) - float(home_against)
    except Exception:
        return None
    pack = _lang_pack(lang)
    seed_base = f"defense:{home}:{away}:{home_against}:{away_against}:{lang}"
    if abs(diff) < _STAT_DIFF_MINOR:
        return _variant_text(pack, "defense_similar_variants", pack["defense_similar"], seed_base)
    if abs(diff) < _STAT_DIFF_MAJOR:
        team = home if diff > 0 else away
        text = _variant_text(pack, "defense_slight_variants", pack["defense_slight"], seed_base)
        return text.format(team=team)
    team = home if diff > 0 else away
    text = _variant_text(pack, "defense_strong_variants", pack["defense_strong"], seed_base)
    return text.format(team=team)


def _comment_venue(home_for: Any, away_for: Any, home: str, away: str, lang: str | None) -> str | None:
    if home_for is None or away_for is None:
        return None
    try:
        diff = float(home_for) - float(away_for)
    except Exception:
        return None
    pack = _lang_pack(lang)
    seed_base = f"venue:{home}:{away}:{home_for}:{away_for}:{lang}"
    if abs(diff) < _STAT_DIFF_MINOR:
        return _variant_text(pack, "venue_even_variants", pack["venue_even"], seed_base)
    if abs(diff) < _STAT_DIFF_MAJOR:
        if diff > 0:
            text = _variant_text(pack, "venue_slight_home_variants", pack["venue_slight_home"], seed_base)
            return text.format(team=home)
        text = _variant_text(pack, "venue_slight_away_variants", pack["venue_slight_away"], seed_base)
        return text.format(team=away)
    if diff > 0:
        text = _variant_text(pack, "venue_strong_home_variants", pack["venue_strong_home"], seed_base)
        return text.format(team=home)
    text = _variant_text(pack, "venue_strong_away_variants", pack["venue_strong_away"], seed_base)
    return text.format(team=away)


def _comment_rest(home_rest: Any, away_rest: Any, home: str, away: str, lang: str | None) -> str | None:
    if home_rest is None or away_rest is None:
        return None
    try:
        diff = int(home_rest) - int(away_rest)
    except Exception:
        return None
    pack = _lang_pack(lang)
    seed_base = f"rest:{home}:{away}:{home_rest}:{away_rest}:{lang}"
    if abs(diff) < 6:
        return _variant_text(pack, "rest_even_variants", pack["rest_even"], seed_base)
    if diff > 0:
        text = _variant_text(pack, "rest_more_variants", pack["rest_more"], seed_base)
        return text.format(team=home, a=int(home_rest), b=int(away_rest))
    text = _variant_text(pack, "rest_more_variants", pack["rest_more"], seed_base)
    return text.format(team=away, a=int(away_rest), b=int(home_rest))


def _market_key(market: str) -> str:
    return "1x2" if market == "1X2" else "total"


def _quality_from_report(report: dict | None, market: str) -> tuple[int, list[str]]:
    if not report:
        return 1, ["нет отчёта качества"]
    key = _market_key(market)
    bucket = report.get(key) if isinstance(report, dict) else None
    summary = bucket.get("summary") if isinstance(bucket, dict) else None
    calibration = bucket.get("calibration") if isinstance(bucket, dict) else None
    if not summary:
        return 1, ["нет сводки качества"]
    bets = int(summary.get("bets") or 0)
    clv_cov_pct = float(summary.get("clv_cov_pct") or 0.0)
    reasons: list[str] = []
    level = 0
    if bets < 50:
        reasons.append(f"малый объём ({bets})")
        level = max(level, 1)
    if bets > 0 and clv_cov_pct == 0:
        reasons.append("CLV coverage 0%")
        level = max(level, 1)
    elif 0 < clv_cov_pct < 30:
        reasons.append(f"CLV coverage низкий ({_fmt_percent100(clv_cov_pct, 1)})")
        level = max(level, 1 if clv_cov_pct >= 10 else 2)
    if bets >= 100 and calibration:
        brier = float(calibration.get("brier") or 0.0)
        logloss = float(calibration.get("logloss") or 0.0)
        if brier > _QUALITY_WARN_BRIER:
            reasons.append(f"Brier {brier:.3f}")
            level = max(level, 1)
        if logloss > _QUALITY_WARN_LOGLOSS:
            reasons.append(f"LogLoss {logloss:.3f}")
            level = max(level, 1)
    return level, reasons


def _calc_implied_prob(odd: Decimal | None) -> float | None:
    if odd is None:
        return None
    try:
        o = float(odd)
        if o <= 0:
            return None
        return 1 / o
    except Exception:
        return None


def _calc_ev(prob: Decimal | None, odd: Decimal | None) -> float | None:
    if prob is None or odd is None:
        return None
    try:
        return float(Decimal(prob) * Decimal(odd) - Decimal(1))
    except Exception:
        return None


async def _fetch_fixture_data(session: AsyncSession, fixture_id: int) -> dict:
    fixture_row = (
        await session.execute(
            text(
                """
                SELECT
                  f.id,
                  f.league_id,
                  f.season,
                  f.kickoff,
                  f.status,
                  f.home_team_id,
                  f.away_team_id,
                  l.name AS league_name,
                  l.logo_url AS league_logo_url,
                  th.name AS home_name,
                  th.logo_url AS home_logo_url,
                  ta.name AS away_name,
                  ta.logo_url AS away_logo_url
                FROM fixtures f
                JOIN teams th ON th.id=f.home_team_id
                JOIN teams ta ON ta.id=f.away_team_id
                LEFT JOIN leagues l ON l.id=f.league_id
                WHERE f.id=:fid
                """
            ),
            {"fid": fixture_id},
        )
    ).first()
    if not fixture_row:
        raise ValueError("fixture not found")

    pred_row = (
        await session.execute(
            text(
                """
                SELECT selection_code, confidence, initial_odd, value_index, signal_score
                FROM predictions
                WHERE fixture_id=:fid
                """
            ),
            {"fid": fixture_id},
        )
    ).first()

    totals_row = (
        await session.execute(
            text(
                """
                SELECT selection, confidence, initial_odd, value_index
                FROM predictions_totals
                WHERE fixture_id=:fid AND market='TOTAL'
                """
            ),
            {"fid": fixture_id},
        )
    ).first()

    indices_row = (
        await session.execute(
            text(
                """
                SELECT
                  home_form_for, home_form_against,
                  away_form_for, away_form_against,
                  home_class_for, home_class_against,
                  away_class_for, away_class_against,
                  home_venue_for, home_venue_against,
                  away_venue_for, away_venue_against,
                  home_rest_hours, away_rest_hours
                FROM match_indices
                WHERE fixture_id=:fid
                """
            ),
            {"fid": fixture_id},
        )
    ).first()

    decision_1x2_row = (
        await session.execute(
            text(
                """
                SELECT payload
                FROM prediction_decisions
                WHERE fixture_id=:fid AND market='1X2'
                ORDER BY updated_at DESC
                LIMIT 1
                """
            ),
            {"fid": fixture_id},
        )
    ).first()

    return {
        "fixture": fixture_row,
        "pred_1x2": pred_row,
        "pred_total": totals_row,
        "indices": indices_row,
        "decision_1x2": getattr(decision_1x2_row, "payload", None) if decision_1x2_row else None,
    }


def _build_market_text(
    fixture: Any,
    pred: Any,
    indices: Any,
    market: str,
    experimental: bool,
    reasons: list[str],
    lang: str | None,
) -> tuple[str, str]:
    pack = _lang_pack(lang)
    league_name = str(getattr(fixture, "league_name", "") or "")
    home_name = str(getattr(fixture, "home_name", "") or "")
    away_name = str(getattr(fixture, "away_name", "") or "")
    league_tag = _protect(league_name)
    home_tag = _protect(home_name)
    away_tag = _protect(away_name)
    kickoff = getattr(fixture, "kickoff", None)
    kickoff_str = _format_kickoff(kickoff, lang)

    selection = _extract_selection(pred)
    selection_label = _protect(_selection_label(selection, market, lang))
    selection_phrase = _selection_phrase(selection, market, home_tag, away_tag, lang) or selection_label
    odd = getattr(pred, "initial_odd", None)
    prob = getattr(pred, "confidence", None)
    ev = _calc_ev(prob, odd)
    implied = _calc_implied_prob(odd)
    edge = (float(prob) - implied) if prob is not None and implied is not None else None
    signal = getattr(pred, "signal_score", None)

    experimental_tag = "⚠️ EXPERIMENTAL" if experimental else ""
    tier = _prediction_tier(ev, signal, experimental)
    seed_base = f"{getattr(fixture, 'id', '')}:{market}:{tier}:{lang}"
    title_label = _prediction_label(pack, tier, seed_base)
    bet_label = _bet_label(pack, tier)
    why_title = _variant_text(pack, "why_variants", pack["why"], f"{seed_base}:why")
    value_title = _variant_text(pack, "value_variants", pack["value_indicators"], f"{seed_base}:value")
    risks_title = _variant_text(pack, "risks_variants", pack["risks"], f"{seed_base}:risks")
    recommendation_title = _variant_text(pack, "recommendation_variants", pack["recommendation"], f"{seed_base}:rec")
    value_profile_label = _variant_text(
        pack, "value_profile_variants", pack["value_profile"], f"{seed_base}:value_profile"
    )

    headline_lines = [
        f"<b>{title_label}</b>",
        f"{league_tag}",
        f"{home_tag} vs {away_tag}",
        f"📅 {_escape_html(kickoff_str)}",
        "",
        f"<b>{bet_label}</b>",
        f"{selection_phrase}",
        f"@ {_protect(_fmt_float(odd, 2))}",
        f"🎯 {pack['model_probability']}: {_fmt_percent(prob, 1)} | Value: {_fmt_value(ev)} {experimental_tag}".strip(),
    ]
    headline = "\n".join(line for line in headline_lines if line is not None)

    model_line = f"{pack['our_model']}: {_fmt_percent(prob, 1)}"
    if edge is not None:
        model_line = f"{model_line} ({edge * 100:+.1f}% {pack['edge_short']})"

    def _fmt_stats(team: str, value_for: Any, value_against: Any) -> str:
        return (
            f"{team}: {_fmt_float(value_for, 2)} {pack['for']} / "
            f"{_fmt_float(value_against, 2)} {pack['against']}"
        )

    def _fmt_venue(team: str, label: str, value_for: Any, value_against: Any) -> str:
        return (
            f"{team} {label}: {_fmt_float(value_for, 2)} {pack['for']} / "
            f"{_fmt_float(value_against, 2)} {pack['against']}"
        )

    translated_reasons = _translate_reasons(reasons, lang)
    risk_line = _variant_text(pack, "no_risks_variants", pack["no_risks"], f"{seed_base}:no_risks")
    if experimental:
        if translated_reasons:
            risk_line = pack["experimental_prefix"] + "; ".join(translated_reasons)
        else:
            risk_line = pack["experimental_prefix"].strip()

    compact = tier in {"cautious", "experimental"}
    if compact:
        analysis_lines = [
            value_title,
            f"{pack['bookmakers_give']}: {_fmt_percent(implied, 1)}",
            model_line,
            _edge_line(edge, lang, seed_base),
            _signal_line(signal, lang, seed_base),
            "",
            risks_title,
            risk_line,
            "",
            recommendation_title,
            f"{value_profile_label}: {_value_strength(ev, lang)}",
            _recommendation_line(ev, odd, lang, tier, seed_base),
        ]
    else:
        analysis_lines = [
            f"<b>{why_title}</b>",
            pack["current_form"],
            _fmt_stats(home_tag, getattr(indices, "home_form_for", None), getattr(indices, "home_form_against", None)),
            _fmt_stats(away_tag, getattr(indices, "away_form_for", None), getattr(indices, "away_form_against", None)),
            _comment_defense(
                getattr(indices, "home_form_against", None),
                getattr(indices, "away_form_against", None),
                home_tag,
                away_tag,
                lang,
            ),
            "",
            pack["team_class"],
            _fmt_stats(home_tag, getattr(indices, "home_class_for", None), getattr(indices, "home_class_against", None)),
            _fmt_stats(away_tag, getattr(indices, "away_class_for", None), getattr(indices, "away_class_against", None)),
            _comment_attack(
                getattr(indices, "home_class_for", None),
                getattr(indices, "away_class_for", None),
                home_tag,
                away_tag,
                lang,
            ),
            "",
            pack["home_away_stats"],
            _fmt_venue(
                home_tag,
                pack["home"],
                getattr(indices, "home_venue_for", None),
                getattr(indices, "home_venue_against", None),
            ),
            _fmt_venue(
                away_tag,
                pack["away"],
                getattr(indices, "away_venue_for", None),
                getattr(indices, "away_venue_against", None),
            ),
            _comment_venue(
                getattr(indices, "home_venue_for", None),
                getattr(indices, "away_venue_for", None),
                home_tag,
                away_tag,
                lang,
            ),
            "",
            pack["fatigue_factor"],
            _comment_rest(
                getattr(indices, "home_rest_hours", None),
                getattr(indices, "away_rest_hours", None),
                home_tag,
                away_tag,
                lang,
            ),
            "",
            value_title,
            f"{pack['bookmakers_give']}: {_fmt_percent(implied, 1)}",
            model_line,
            _edge_line(edge, lang, seed_base),
            _signal_line(signal, lang, seed_base),
            "",
            risks_title,
            risk_line,
            "",
            recommendation_title,
            f"{value_profile_label}: {_value_strength(ev, lang)}",
            _recommendation_line(ev, odd, lang, tier, seed_base),
        ]
    if not compact and prob is not None:
        try:
            fair_odd = 1 / float(prob) if float(prob) > 0 else None
        except Exception:
            fair_odd = None
        if fair_odd:
            line_watch = _variant_text(pack, "line_watch_variants", pack["line_watch"], f"{seed_base}:line_watch")
            analysis_lines.append(line_watch.format(odd=f"{fair_odd:.2f}"))
    analysis_lines.extend(["", pack["disclaimer"]])
    analysis = "\n".join(line for line in analysis_lines if line is not None)
    return headline, analysis


async def _build_preview_internal(session: AsyncSession, fixture_id: int) -> tuple[dict, dict]:
    data = await _fetch_fixture_data(session, fixture_id)
    fixture = data["fixture"]
    indices = data["indices"]

    cached_report = await quality_report.get_cached(session)
    markets: list[MarketPreview] = []

    for market, pred in (("1X2", data["pred_1x2"]), ("TOTAL", data["pred_total"])):
        if not pred:
            markets.append(
                MarketPreview(
                    market=market,
                    headline_raw="",
                    analysis_raw="",
                    headline="",
                    analysis="",
                    experimental=True,
                    quality_level=2,
                    reasons=["нет данных для прогноза"],
                )
            )
            continue
        selection = _extract_selection(pred)
        if _is_skip_selection(selection):
            markets.append(
                MarketPreview(
                    market=market,
                    headline_raw="",
                    analysis_raw="",
                    headline="",
                    analysis="",
                    experimental=False,
                    quality_level=0,
                    reasons=["SKIP: не публикуется"],
                )
            )
            continue
        level, reasons = _quality_from_report(cached_report, market)
        experimental = level > 0
        headline_raw, analysis_raw = _build_market_text(
            fixture,
            pred,
            indices,
            market,
            experimental,
            reasons,
            "ru",
        )
        if settings.groq_enabled:
            analysis_raw = await enrich_analysis(
                session, analysis_raw, fixture, pred, indices, market,
            )
        markets.append(
            MarketPreview(
                market=market,
                headline_raw=headline_raw,
                analysis_raw=analysis_raw,
                headline=_strip_protect_tags(headline_raw),
                analysis=_strip_protect_tags(analysis_raw),
                experimental=experimental,
                quality_level=level,
                reasons=reasons,
            )
        )

    preview = {
        "fixture_id": int(fixture_id),
        "mode": (settings.publish_mode or "manual").strip().lower(),
        "markets": [m.__dict__ for m in markets],
    }
    return preview, data


async def build_preview(session: AsyncSession, fixture_id: int) -> dict:
    preview, _ = await _build_preview_internal(session, fixture_id)
    return preview


def _preview_language(lang: str | None) -> str:
    key = (lang or "").strip().lower()
    if key in _LANG_TEXT:
        return key
    channels = settings.telegram_channels
    if "ru" in channels:
        return "ru"
    if channels:
        for item in channels.keys():
            if item in _LANG_TEXT:
                return item
    return "ru"


async def build_post_preview(
    session: AsyncSession,
    fixture_id: int,
    *,
    image_theme: str | None = None,
    lang: str | None = None,
) -> dict:
    preview, data = await _build_preview_internal(session, fixture_id)
    image_theme_norm = _normalize_image_theme(image_theme)
    mode = preview.get("mode") or "manual"
    lang_key = _preview_language(lang)

    fixture = data["fixture"]
    indices = data["indices"]
    home_win_prob, draw_prob, away_win_prob = _extract_1x2_chances(data.get("decision_1x2"))
    pred_by_market = {"1X2": data.get("pred_1x2"), "TOTAL": data.get("pred_total")}

    home_logo_bytes: bytes | None = None
    away_logo_bytes: bytes | None = None
    league_logo_bytes: bytes | None = None
    image_visual_context = ImageVisualContext()
    if settings.publish_headline_image:
        home_logo_bytes = await _fetch_logo_bytes(getattr(fixture, "home_logo_url", None))
        away_logo_bytes = await _fetch_logo_bytes(getattr(fixture, "away_logo_url", None))
        league_logo_bytes = await _fetch_logo_bytes(getattr(fixture, "league_logo_url", None))
        image_visual_context = await _fetch_image_visual_context(session, fixture)

    posts: list[dict[str, Any]] = []
    for market in preview.get("markets", []):
        market_name = str(market.get("market") or "").strip() or "UNKNOWN"
        headline_raw_preview = str(market.get("headline_raw") or "").strip()
        analysis_raw_preview = str(market.get("analysis_raw") or "").strip()
        quality_level = int(market.get("quality_level") or 0)
        experimental = bool(market.get("experimental"))
        reasons = list(market.get("reasons") or [])

        if not headline_raw_preview or not analysis_raw_preview:
            posts.append(
                {
                    "market": market_name,
                    "lang": lang_key,
                    "status": "unavailable",
                    "reason": "no_data",
                    "publish_allowed": False,
                    "experimental": experimental,
                    "quality_level": quality_level,
                    "reasons": reasons,
                    "headline": "",
                    "analysis": "",
                    "headline_parts": [],
                    "analysis_parts": [],
                    "uses_image": False,
                    "image_data_url": None,
                    "image_fallback_reason": None,
                    "render_time_ms": None,
                    "messages": [],
                }
            )
            continue

        pred = pred_by_market.get(market_name)
        if not pred:
            posts.append(
                {
                    "market": market_name,
                    "lang": lang_key,
                    "status": "unavailable",
                    "reason": "no_pred",
                    "publish_allowed": False,
                    "experimental": experimental,
                    "quality_level": quality_level,
                    "reasons": reasons,
                    "headline": "",
                    "analysis": "",
                    "headline_parts": [],
                    "analysis_parts": [],
                    "uses_image": False,
                    "image_data_url": None,
                    "image_fallback_reason": None,
                    "render_time_ms": None,
                    "messages": [],
                }
            )
            continue

        odd = getattr(pred, "initial_odd", None)
        prob = getattr(pred, "confidence", None)
        signal = getattr(pred, "signal_score", None)
        implied_prob = _calc_implied_prob(odd)
        model_edge = (float(prob) - implied_prob) if prob is not None and implied_prob is not None else None
        ev = _calc_ev(prob, odd)
        tier = _prediction_tier(ev, signal, experimental)

        local_lang = lang_key if lang_key in _LANG_TEXT else "ru"
        pack = _lang_pack(local_lang)
        bet_label = _bet_label(pack, tier)
        indicator_title = _plain_indicator_text(
            _variant_text(
                pack,
                "value_variants",
                pack.get("value_indicators", "VALUE INDICATORS"),
                f"{fixture_id}:{market_name}:{local_lang}:signal_title",
            )
        ) or "VALUE INDICATORS"
        bookmakers_label = _plain_indicator_text(pack.get("bookmakers_give", "Bookmakers give")) or "Bookmakers give"
        model_label = _plain_indicator_text(pack.get("our_model", "Our model")) or "Our model"
        edge_suffix = _plain_indicator_text(pack.get("edge_short", "edge")) or "edge"
        indicator_line_1 = f"{bookmakers_label}: {_fmt_percent(implied_prob, 1)}"
        indicator_line_2 = f"{model_label}: {_fmt_percent(prob, 1)}"
        if model_edge is not None:
            indicator_line_2 = f"{indicator_line_2} ({model_edge * 100:+.1f}% {edge_suffix})"
        indicator_line_3 = None

        use_deepl = bool(
            settings.publish_deepl_fallback
            and settings.deepl_api_key
            and lang_key not in _LANG_TEXT
            and lang_key != "ru"
        )
        headline_raw, analysis_raw = _build_market_text(
            fixture,
            pred,
            indices,
            market_name,
            experimental,
            reasons,
            local_lang,
        )
        # AI enrichment (Russian source text only)
        if settings.groq_enabled and local_lang == "ru":
            analysis_raw = await enrich_analysis(
                session, analysis_raw, fixture, pred, indices, market_name,
            )

        # Translation: Groq (if enabled) or DeepL (fallback)
        protected: list[str] = []
        if settings.groq_enabled and lang_key != local_lang:
            headline_raw = await translate_text(session, headline_raw, lang_key, local_lang)
            analysis_raw = await translate_text(session, analysis_raw, lang_key, local_lang)
        elif use_deepl:
            protected = _extract_protected_values(f"{headline_raw}\n{analysis_raw}")
            headline_payload = _prepare_translation_html(headline_raw)
            analysis_payload = _prepare_translation_html(analysis_raw)
            headline_raw = await translate_html(session, headline_payload, lang_key)
            analysis_raw = await translate_html(session, analysis_payload, lang_key)
            headline_raw = _restore_translated_html(headline_raw)
            analysis_raw = _restore_translated_html(analysis_raw)

        headline = _strip_protect_tags(headline_raw)
        analysis = _strip_protect_tags(analysis_raw)
        if use_deepl and not settings.groq_enabled:
            headline = _normalize_translated_text(headline, protected)
            analysis = _normalize_translated_text(analysis, protected)

        headline_parts = _split_message(headline)
        analysis_parts = _split_message(analysis)
        uses_image = False
        image_data_url: str | None = None
        image_fallback_reason: str | None = None
        render_time_ms: int | None = None

        if settings.publish_headline_image:
            image_text = _strip_image_probability_line(headline)
            common_image_kwargs = {
                "home_logo": home_logo_bytes,
                "away_logo": away_logo_bytes,
                "league_logo": league_logo_bytes,
                "league_label": str(getattr(fixture, "league_name", "") or ""),
                "market_label": (
                    "1X2" if market_name == "1X2" else "TOTAL" if market_name == "TOTAL" else market_name
                ),
                "bet_label": bet_label,
            }
            html_image_kwargs = {
                **common_image_kwargs,
                "style_variant": image_theme_norm,
                "league_country": image_visual_context.league_country,
                "league_round": image_visual_context.league_round,
                "venue_name": image_visual_context.venue_name,
                "venue_city": image_visual_context.venue_city,
                "home_rank": image_visual_context.home_rank,
                "away_rank": image_visual_context.away_rank,
                "home_points": image_visual_context.home_points,
                "away_points": image_visual_context.away_points,
                "home_played": image_visual_context.home_played,
                "away_played": image_visual_context.away_played,
                "home_goal_diff": image_visual_context.home_goal_diff,
                "away_goal_diff": image_visual_context.away_goal_diff,
                "home_form": image_visual_context.home_form,
                "away_form": image_visual_context.away_form,
                "home_win_prob": home_win_prob,
                "draw_prob": draw_prob,
                "away_win_prob": away_win_prob,
                "signal_title": indicator_title,
                "signal_line_1": indicator_line_1,
                "signal_line_2": indicator_line_2,
                "signal_line_3": indicator_line_3,
            }
            if settings.card_gen_v2 and _card_gen_v2_render is not None and _build_v2_card is not None:
                render_started = time.perf_counter()
                try:
                    v2_card = _build_v2_card(
                        fixture=fixture,
                        image_visual_context=image_visual_context,
                        image_text=image_text,
                        html_image_kwargs=html_image_kwargs,
                        home_win_prob=home_win_prob,
                        draw_prob=draw_prob,
                        away_win_prob=away_win_prob,
                        indicator_title=indicator_title,
                        indicator_lines=[indicator_line_1, indicator_line_2, indicator_line_3],
                    )
                    image_bytes = await _card_gen_v2_render(v2_card)
                    render_time_ms = int((time.perf_counter() - render_started) * 1000)
                    encoded = base64.b64encode(image_bytes).decode("ascii")
                    image_data_url = f"data:image/jpeg;base64,{encoded}"
                    uses_image = True
                except Exception:
                    render_time_ms = int((time.perf_counter() - render_started) * 1000)
                    image_fallback_reason = "card_gen_v2_failed"
                    log.exception(
                        "card_gen_v2_preview_failed fixture=%s market=%s lang=%s",
                        fixture_id,
                        market_name,
                        lang_key,
                    )
            elif render_headline_image_html is not None:
                render_started = time.perf_counter()
                try:
                    image_bytes = await asyncio.to_thread(
                        render_headline_image_html,
                        image_text,
                        **html_image_kwargs,
                    )
                    render_time_ms = int((time.perf_counter() - render_started) * 1000)
                    encoded = base64.b64encode(image_bytes).decode("ascii")
                    image_data_url = f"data:image/png;base64,{encoded}"
                    uses_image = True
                except Exception:
                    render_time_ms = int((time.perf_counter() - render_started) * 1000)
                    image_fallback_reason = "html_render_failed"
                    log.exception(
                        "post_preview_html_render_failed fixture=%s market=%s lang=%s",
                        fixture_id,
                        market_name,
                        lang_key,
                    )
            else:
                image_fallback_reason = "html_renderer_unavailable"

        messages: list[dict[str, Any]] = []
        order = 1
        if uses_image:
            messages.append(
                {
                    "order": order,
                    "type": "image",
                    "section": "headline",
                    "text": None,
                }
            )
            order += 1
            for part in analysis_parts:
                messages.append(
                    {
                        "order": order,
                        "type": "text",
                        "section": "analysis",
                        "text": part,
                    }
                )
                order += 1
        else:
            for part in headline_parts:
                messages.append(
                    {
                        "order": order,
                        "type": "text",
                        "section": "headline",
                        "text": part,
                    }
                )
                order += 1
            for part in analysis_parts:
                messages.append(
                    {
                        "order": order,
                        "type": "text",
                        "section": "analysis",
                        "text": part,
                    }
                )
                order += 1

        publish_allowed = not (mode == "auto" and quality_level >= 2)
        status = "ready" if publish_allowed else "blocked"
        reason = None if publish_allowed else "quality_risk"
        posts.append(
            {
                "market": market_name,
                "lang": lang_key,
                "status": status,
                "reason": reason,
                "publish_allowed": publish_allowed,
                "experimental": experimental,
                "quality_level": quality_level,
                "reasons": reasons,
                "headline": headline,
                "analysis": analysis,
                "headline_parts": headline_parts,
                "analysis_parts": analysis_parts,
                "uses_image": uses_image,
                "image_data_url": image_data_url,
                "image_fallback_reason": image_fallback_reason,
                "render_time_ms": render_time_ms,
                "messages": messages,
            }
        )

    return {
        "fixture_id": int(fixture_id),
        "mode": mode,
        "lang": lang_key,
        "image_theme": image_theme_norm,
        "image_enabled": bool(settings.publish_headline_image),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "posts": posts,
    }


async def _record_publication(
    session: AsyncSession,
    fixture_id: int,
    market: str,
    language: str,
    channel_id: int,
    status: str,
    *,
    experimental: bool,
    headline_message_id: int | None = None,
    analysis_message_id: int | None = None,
    content_hash: str | None = None,
    idempotency_key: str | None = None,
    payload: dict | None = None,
    error: str | None = None,
) -> None:
    published_at = datetime.now(timezone.utc) if status in {"ok", "published"} else None
    payload_json = None
    if payload is not None:
        if isinstance(payload, str):
            payload_json = payload
        else:
            payload_json = json.dumps(payload, ensure_ascii=False)
    await session.execute(
        text(
            """
            INSERT INTO prediction_publications(
              fixture_id, market, language, channel_id, status,
              experimental, headline_message_id, analysis_message_id,
              content_hash, idempotency_key, payload, error, published_at
            )
            VALUES(
              :fid, :market, :lang, :cid, :status,
              :exp, :mid_head, :mid_analysis,
              :hash, :idempotency_key, CAST(:payload AS jsonb), :error,
              :published_at
            )
            """
        ),
        {
            "fid": fixture_id,
            "market": market,
            "lang": language,
            "cid": channel_id,
            "status": status,
            "exp": bool(experimental),
            "mid_head": headline_message_id,
            "mid_analysis": analysis_message_id,
            "hash": content_hash,
            "idempotency_key": idempotency_key,
            "payload": payload_json,
            "error": error,
            "published_at": published_at,
        },
    )


def _hash_content(headline: str, analysis: str) -> str:
    h = hashlib.sha256()
    h.update(headline.encode("utf-8", errors="ignore"))
    h.update(analysis.encode("utf-8", errors="ignore"))
    return h.hexdigest()


def _build_idempotency_key(
    fixture_id: int,
    market: str,
    language: str,
    channel_id: int,
    content_hash: str,
) -> str:
    payload = f"{fixture_id}:{market}:{language}:{channel_id}:{content_hash}"
    return hashlib.sha256(payload.encode("utf-8", errors="ignore")).hexdigest()


def _publish_reservation_key(fixture_id: int) -> int:
    digest = hashlib.blake2b(f"pred1:publish:{int(fixture_id)}".encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") & 0x7FFF_FFFF_FFFF_FFFF


async def _try_publish_reservation(session: AsyncSession, fixture_id: int) -> bool:
    try:
        row = (
            await session.execute(
                text("SELECT pg_try_advisory_xact_lock(:k) AS ok"),
                {"k": _publish_reservation_key(int(fixture_id))},
            )
        ).first()
        if row is None:
            return False
        if hasattr(row, "ok"):
            return bool(row.ok)
        try:
            return bool(row[0])
        except Exception:
            return False
    except Exception:
        log.exception("publish_reservation_lock_failed fixture=%s", fixture_id)
        return False


async def publish_fixture(
    session: AsyncSession,
    fixture_id: int,
    *,
    force: bool = False,
    dry_run: bool = False,
    image_theme: str | None = None,
) -> dict:
    if not settings.telegram_bot_token and not dry_run:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")
    channels = settings.telegram_channels
    if not channels:
        raise RuntimeError("No TELEGRAM_CHANNEL_* configured")

    if not await _try_publish_reservation(session, int(fixture_id)):
        return {
            "fixture_id": fixture_id,
            "mode": (settings.publish_mode or "manual").strip().lower(),
            "dry_run": dry_run,
            "image_theme": _normalize_image_theme(image_theme),
            "reservation_locked": True,
            "results": [
                {
                    "market": "*",
                    "lang": "*",
                    "status": "skipped",
                    "reason": "publish_locked",
                }
            ],
        }

    preview, data = await _build_preview_internal(session, fixture_id)
    mode = preview.get("mode") or "manual"
    image_theme_norm = _normalize_image_theme(image_theme)
    results: list[dict] = []
    fixture = data["fixture"]
    indices = data["indices"]
    home_win_prob, draw_prob, away_win_prob = _extract_1x2_chances(data.get("decision_1x2"))
    pred_by_market = {"1X2": data["pred_1x2"], "TOTAL": data["pred_total"]}
    home_logo_bytes: bytes | None = None
    away_logo_bytes: bytes | None = None
    league_logo_bytes: bytes | None = None
    image_visual_context = ImageVisualContext()
    if settings.publish_headline_image and not dry_run:
        home_logo_bytes = await _fetch_logo_bytes(getattr(fixture, "home_logo_url", None))
        away_logo_bytes = await _fetch_logo_bytes(getattr(fixture, "away_logo_url", None))
        league_logo_bytes = await _fetch_logo_bytes(getattr(fixture, "league_logo_url", None))
        image_visual_context = await _fetch_image_visual_context(session, fixture)

    for market in preview.get("markets", []):
        if not market.get("headline_raw") or not market.get("analysis_raw"):
            results.append({"market": market.get("market"), "status": "skipped", "reason": "no_data"})
            continue
        pred = pred_by_market.get(market.get("market"))
        if not pred:
            results.append({"market": market.get("market"), "status": "skipped", "reason": "no_pred"})
            continue
        quality_level = int(market.get("quality_level") or 0)
        experimental = bool(market.get("experimental"))
        reasons = market.get("reasons") or []
        odd = getattr(pred, "initial_odd", None)
        prob = getattr(pred, "confidence", None)
        signal = getattr(pred, "signal_score", None)
        ev = _calc_ev(prob, odd)
        implied_prob = _calc_implied_prob(odd)
        model_edge = (float(prob) - implied_prob) if prob is not None and implied_prob is not None else None
        tier = _prediction_tier(ev, signal, experimental)

        for lang, channel_id in channels.items():
            existing = (
                await session.execute(
                    text(
                        """
                        SELECT id FROM prediction_publications
                        WHERE fixture_id=:fid AND market=:market AND language=:lang AND status IN ('ok', 'published')
                        ORDER BY created_at DESC
                        LIMIT 1
                        """
                    ),
                    {"fid": fixture_id, "market": market["market"], "lang": lang},
                )
            ).first()
            if existing and not force:
                await _record_publication(
                    session,
                    fixture_id,
                    market["market"],
                    lang,
                    channel_id,
                    "skipped",
                    experimental=experimental,
                    payload={"reason": "already_published"},
                )
                results.append({"market": market["market"], "lang": lang, "status": "skipped", "reason": "already_published"})
                continue

            if mode == "auto" and quality_level >= 2 and not force:
                await _record_publication(
                    session,
                    fixture_id,
                    market["market"],
                    lang,
                    channel_id,
                    "skipped",
                    experimental=experimental,
                    payload={"reason": "quality_risk", "reasons": reasons},
                )
                results.append({"market": market["market"], "lang": lang, "status": "skipped", "reason": "quality_risk"})
                continue

            lang_key = (lang or "ru").strip().lower()
            local_lang = lang_key if lang_key in _LANG_TEXT else "ru"
            pack = _lang_pack(local_lang)
            bet_label = _bet_label(pack, tier)
            indicator_title = _plain_indicator_text(
                _variant_text(
                    pack,
                    "value_variants",
                    pack.get("value_indicators", "VALUE INDICATORS"),
                    f"{fixture_id}:{market['market']}:{local_lang}:signal_title",
                )
            ) or "VALUE INDICATORS"
            bookmakers_label = _plain_indicator_text(pack.get("bookmakers_give", "Bookmakers give")) or "Bookmakers give"
            model_label = _plain_indicator_text(pack.get("our_model", "Our model")) or "Our model"
            edge_suffix = _plain_indicator_text(pack.get("edge_short", "edge")) or "edge"
            indicator_line_1 = f"{bookmakers_label}: {_fmt_percent(implied_prob, 1)}"
            indicator_line_2 = f"{model_label}: {_fmt_percent(prob, 1)}"
            if model_edge is not None:
                indicator_line_2 = f"{indicator_line_2} ({model_edge * 100:+.1f}% {edge_suffix})"
            indicator_line_3 = None
            use_deepl = bool(
                settings.publish_deepl_fallback
                and settings.deepl_api_key
                and lang_key not in _LANG_TEXT
                and lang_key != "ru"
            )
            headline_raw, analysis_raw = _build_market_text(
                fixture,
                pred,
                indices,
                market["market"],
                experimental,
                reasons,
                local_lang,
            )
            # AI enrichment (Russian source text only)
            if settings.groq_enabled and local_lang == "ru":
                analysis_raw = await enrich_analysis(
                    session, analysis_raw, fixture, pred, indices, market["market"],
                )

            # Translation: Groq (if enabled) or DeepL (fallback)
            protected: list[str] = []
            if settings.groq_enabled and lang_key != local_lang:
                headline_raw = await translate_text(session, headline_raw, lang_key, local_lang)
                analysis_raw = await translate_text(session, analysis_raw, lang_key, local_lang)
            elif use_deepl:
                protected = _extract_protected_values(f"{headline_raw}\n{analysis_raw}")
                headline_payload = _prepare_translation_html(headline_raw)
                analysis_payload = _prepare_translation_html(analysis_raw)
                headline_raw = await translate_html(session, headline_payload, lang_key)
                analysis_raw = await translate_html(session, analysis_payload, lang_key)
                headline_raw = _restore_translated_html(headline_raw)
                analysis_raw = _restore_translated_html(analysis_raw)

            headline = _strip_protect_tags(headline_raw)
            analysis = _strip_protect_tags(analysis_raw)
            if use_deepl and not settings.groq_enabled:
                headline = _normalize_translated_text(headline, protected)
                analysis = _normalize_translated_text(analysis, protected)

            content_hash = _hash_content(headline, analysis)
            idempotency_key = None
            if not force:
                idempotency_key = _build_idempotency_key(
                    int(fixture_id),
                    str(market["market"]),
                    str(lang),
                    int(channel_id),
                    content_hash,
                )

            if dry_run:
                await _record_publication(
                    session,
                    fixture_id,
                    market["market"],
                    lang,
                    channel_id,
                    "dry_run",
                    experimental=experimental,
                    content_hash=content_hash,
                    idempotency_key=idempotency_key,
                    payload={
                        "dry_run": True,
                        "headline": headline,
                        "analysis": analysis,
                        "image_theme": image_theme_norm,
                    },
                )
                results.append({"market": market["market"], "lang": lang, "status": "dry_run"})
                continue

            try:
                if not force:
                    existing_idempotent = (
                        await session.execute(
                            text(
                                """
                                SELECT id FROM prediction_publications
                                WHERE idempotency_key=:key AND status IN ('ok', 'published')
                                ORDER BY created_at DESC
                                LIMIT 1
                                """
                            ),
                            {"key": idempotency_key},
                        )
                    ).first()
                    if existing_idempotent:
                        await _record_publication(
                            session,
                            fixture_id,
                            market["market"],
                            lang,
                            channel_id,
                            "skipped",
                            experimental=experimental,
                            content_hash=content_hash,
                            idempotency_key=idempotency_key,
                            payload={"reason": "idempotent_duplicate"},
                        )
                        results.append(
                            {
                                "market": market["market"],
                                "lang": lang,
                                "status": "skipped",
                                "reason": "idempotent_duplicate",
                            }
                        )
                        continue

                headline_parts = _split_message(headline)
                analysis_parts = _split_message(analysis)
                headline_ids: list[int]
                analysis_ids: list[int]
                used_headline_image = False
                image_fallback_reason: str | None = None
                html_attempted = False
                html_render_failed = False
                render_time_ms: int | None = None
                if settings.publish_headline_image:
                    html_attempted = True
                    image_text = _strip_image_probability_line(headline)
                    common_image_kwargs = {
                        "home_logo": home_logo_bytes,
                        "away_logo": away_logo_bytes,
                        "league_logo": league_logo_bytes,
                        "league_label": str(getattr(fixture, "league_name", "") or ""),
                        "market_label": (
                            "1X2" if market["market"] == "1X2" else "TOTAL" if market["market"] == "TOTAL" else str(market["market"])
                        ),
                        "bet_label": bet_label,
                    }
                    html_image_kwargs = {
                        **common_image_kwargs,
                        "style_variant": image_theme_norm,
                        "league_country": image_visual_context.league_country,
                        "league_round": image_visual_context.league_round,
                        "venue_name": image_visual_context.venue_name,
                        "venue_city": image_visual_context.venue_city,
                        "home_rank": image_visual_context.home_rank,
                        "away_rank": image_visual_context.away_rank,
                        "home_points": image_visual_context.home_points,
                        "away_points": image_visual_context.away_points,
                        "home_played": image_visual_context.home_played,
                        "away_played": image_visual_context.away_played,
                        "home_goal_diff": image_visual_context.home_goal_diff,
                        "away_goal_diff": image_visual_context.away_goal_diff,
                        "home_form": image_visual_context.home_form,
                        "away_form": image_visual_context.away_form,
                        "home_win_prob": home_win_prob,
                        "draw_prob": draw_prob,
                        "away_win_prob": away_win_prob,
                        "signal_title": indicator_title,
                        "signal_line_1": indicator_line_1,
                        "signal_line_2": indicator_line_2,
                        "signal_line_3": indicator_line_3,
                    }
                    if settings.card_gen_v2 and _card_gen_v2_render is not None and _build_v2_card is not None:
                        render_started = time.perf_counter()
                        try:
                            v2_card = _build_v2_card(
                                fixture=fixture,
                                image_visual_context=image_visual_context,
                                image_text=image_text,
                                html_image_kwargs=html_image_kwargs,
                                home_win_prob=home_win_prob,
                                draw_prob=draw_prob,
                                away_win_prob=away_win_prob,
                                indicator_title=indicator_title,
                                indicator_lines=[indicator_line_1, indicator_line_2, indicator_line_3],
                            )
                            image_bytes = await _card_gen_v2_render(v2_card)
                            render_time_ms = int((time.perf_counter() - render_started) * 1000)
                            # Caption mode: image + text in ONE Telegram message
                            caption_text = "\n".join(analysis_parts)
                            if len(caption_text) <= 1024:
                                photo_id = await send_photo(
                                    channel_id, image_bytes, caption=caption_text,
                                )
                                headline_ids = [photo_id]
                                analysis_ids = []
                            else:
                                # Caption too long — send photo with truncated
                                # caption, then remainder as reply
                                photo_id = await send_photo(
                                    channel_id, image_bytes,
                                    caption=caption_text[:1024],
                                )
                                headline_ids = [photo_id]
                                remainder = caption_text[1024:]
                                analysis_ids = await send_message_parts(
                                    channel_id,
                                    [remainder],
                                    reply_to_message_id=photo_id,
                                )
                            used_headline_image = True
                        except Exception:
                            render_time_ms = int((time.perf_counter() - render_started) * 1000)
                            html_render_failed = True
                            log.exception(
                                "card_gen_v2_failed fixture=%s market=%s lang=%s fallback=text",
                                fixture_id,
                                market["market"],
                                lang,
                            )
                            image_fallback_reason = "card_gen_v2_failed"
                            await _record_publication(
                                session,
                                fixture_id,
                                market["market"],
                                lang,
                                channel_id,
                                "render_failed",
                                experimental=experimental,
                                content_hash=content_hash,
                                idempotency_key=idempotency_key,
                                payload={
                                    "reason": "card_gen_v2_failed",
                                    "headline_image": False,
                                    "headline_image_fallback": image_fallback_reason,
                                    "html_attempted": True,
                                    "html_render_failed": True,
                                    "render_time_ms": render_time_ms,
                                    "image_theme": image_theme_norm,
                                },
                            )
                    elif render_headline_image_html is not None:
                        render_started = time.perf_counter()
                        try:
                            image_bytes = await asyncio.to_thread(
                                render_headline_image_html,
                                image_text,
                                **html_image_kwargs,
                            )
                            render_time_ms = int((time.perf_counter() - render_started) * 1000)
                            # Caption mode: image + text in ONE Telegram message
                            caption_text = "\n".join(analysis_parts)
                            if len(caption_text) <= 1024:
                                photo_id = await send_photo(
                                    channel_id, image_bytes, caption=caption_text,
                                )
                                headline_ids = [photo_id]
                                analysis_ids = []
                            else:
                                photo_id = await send_photo(
                                    channel_id, image_bytes,
                                    caption=caption_text[:1024],
                                )
                                headline_ids = [photo_id]
                                remainder = caption_text[1024:]
                                analysis_ids = await send_message_parts(
                                    channel_id,
                                    [remainder],
                                    reply_to_message_id=photo_id,
                                )
                            used_headline_image = True
                        except Exception:
                            render_time_ms = int((time.perf_counter() - render_started) * 1000)
                            html_render_failed = True
                            log.exception(
                                "headline_image_html_failed fixture=%s market=%s lang=%s fallback=text",
                                fixture_id,
                                market["market"],
                                lang,
                            )
                            image_fallback_reason = "html_render_failed"
                            await _record_publication(
                                session,
                                fixture_id,
                                market["market"],
                                lang,
                                channel_id,
                                "render_failed",
                                experimental=experimental,
                                content_hash=content_hash,
                                idempotency_key=idempotency_key,
                                payload={
                                    "reason": "html_render_failed",
                                    "headline_image": False,
                                    "headline_image_fallback": image_fallback_reason,
                                    "html_attempted": True,
                                    "html_render_failed": True,
                                    "render_time_ms": render_time_ms,
                                    "image_theme": image_theme_norm,
                                },
                            )
                    else:
                        html_render_failed = True
                        log.warning(
                            "headline_image_html_unavailable fixture=%s market=%s lang=%s fallback=text",
                            fixture_id,
                            market["market"],
                            lang,
                        )
                        image_fallback_reason = "html_renderer_unavailable"
                        await _record_publication(
                            session,
                            fixture_id,
                            market["market"],
                            lang,
                            channel_id,
                            "render_failed",
                            experimental=experimental,
                            content_hash=content_hash,
                            idempotency_key=idempotency_key,
                            payload={
                                "reason": "html_renderer_unavailable",
                                "headline_image": False,
                                "headline_image_fallback": image_fallback_reason,
                                "html_attempted": True,
                                "html_render_failed": True,
                                "render_time_ms": render_time_ms,
                                "image_theme": image_theme_norm,
                            },
                        )

                if not used_headline_image:
                    headline_ids = await send_message_parts(channel_id, headline_parts)
                    analysis_ids = await send_message_parts(channel_id, analysis_parts)

                await _record_publication(
                    session,
                    fixture_id,
                    market["market"],
                    lang,
                    channel_id,
                    "published",
                    experimental=experimental,
                    headline_message_id=headline_ids[0] if headline_ids else None,
                    analysis_message_id=analysis_ids[0] if analysis_ids else None,
                    content_hash=content_hash,
                    idempotency_key=idempotency_key,
                    payload={
                        "headline": headline,
                        "analysis": analysis,
                        "headline_ids": headline_ids,
                        "analysis_ids": analysis_ids,
                        "headline_image": used_headline_image,
                        "headline_image_fallback": image_fallback_reason,
                        "html_attempted": html_attempted,
                        "html_render_failed": html_render_failed,
                        "render_time_ms": render_time_ms,
                        "image_theme": image_theme_norm,
                    },
                )
                results.append({"market": market["market"], "lang": lang, "status": "ok"})
            except Exception as exc:
                log.exception("publish_failed fixture=%s market=%s lang=%s", fixture_id, market["market"], lang)
                try:
                    await session.rollback()
                except Exception:
                    pass
                try:
                    await _record_publication(
                        session,
                        fixture_id,
                        market["market"],
                        lang,
                        channel_id,
                        "send_failed",
                        experimental=experimental,
                        content_hash=content_hash,
                        idempotency_key=idempotency_key,
                        payload={"reason": "send_failed"},
                        error=str(exc),
                    )
                except Exception:
                    log.exception("record_failed_publication_error fixture=%s", fixture_id)
                    try:
                        await session.rollback()
                    except Exception:
                        pass
                results.append({"market": market["market"], "lang": lang, "status": "failed", "error": str(exc)})

    try:
        await session.commit()
    except Exception:
        log.exception("publish_final_commit_failed fixture=%s", fixture_id)
        try:
            await session.rollback()
        except Exception:
            pass
    return {
        "fixture_id": fixture_id,
        "mode": mode,
        "dry_run": dry_run,
        "image_theme": image_theme_norm,
        "results": results,
    }
