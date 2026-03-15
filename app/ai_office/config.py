"""AI Office agent configuration — schedules, thresholds, prompts."""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Monitor thresholds
# ---------------------------------------------------------------------------

MONITOR_THRESHOLDS = {
    "sync_hours_warn": 6,          # sync_data older than N hours → warning
    "upcoming_predictions_min": 1,  # 0 predictions for upcoming → critical
    "unsettled_warn": 10,          # > N unsettled past matches → warning
    "api_cache_24h_warn": 0.8,     # > 80% of daily limit → warning
    "pinnacle_24h_min": 1,         # 0 pinnacle odds in 24h → warning
    "errors_24h_warn": 3,          # > N errors in 24h → critical
}

# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

MONITOR_SYSTEM_PROMPT = (
    "Ты — мониторщик AI-офиса футбольных прогнозов.\n"
    "Следишь за здоровьем системы.\n\n"
    "Входные данные: результаты 6 health checks.\n\n"
    "Правила:\n"
    "- Если ВСЁ в норме → ответь ТОЛЬКО: \"✅ Система в норме\"\n"
    "- Если есть проблема → алерт\n\n"
    "Пороги:\n"
    "1. sync_data > 6 часов → ⚠️ Sync задержка\n"
    "2. upcoming_predictions = 0 при наличии матчей → 🔴 Predictions не генерируются\n"
    "3. unsettled > 10 → ⚠️ Settlement отстаёт\n"
    "4. API quota > 80% → ⚠️ API quota\n"
    "5. pinnacle_24h = 0 → ⚠️ Pinnacle sync остановлен\n"
    "6. errors_24h > 3 → 🔴 Много ошибок\n\n"
    "Формат алерта:\n"
    "🛡️ Мониторинг [время UTC]\n"
    "[Emoji] [Проблема]: [описание, 1 предложение]\n"
    "Рекомендация: [действие]\n\n"
    "Только факты. Не паникуй."
)

HELP_TEXT = (
    "🤖 <b>AI Office — Команды</b>\n\n"
    "/status — Здоровье системы прямо сейчас\n"
    "/help — Список команд\n\n"
    "<i>Фаза 1: Monitor + Status</i>\n"
    "<i>Больше команд появится в следующих фазах.</i>"
)
