# AI Office — Changelog

Лог изменений по реализации AI Office. Каждая запись фиксирует что было сделано, какие файлы затронуты, и статус.

---

## 2026-03-15 — Фаза 0: Groq Integration (ЗАВЕРШЕНА ✅)

### Что сделано
Интегрирован Groq API (Llama 3.3 70B) в систему публикации. Две функции:
1. **AI-обогащение** — перефразирует шаблонный текст анализа матчей
2. **AI-перевод** — заменяет DeepL для перевода на 7 языков

### Файлы

| Файл | Действие | Описание |
|------|----------|----------|
| `app/core/config.py` | Изменён | +4 настройки: GROQ_API_KEY, GROQ_API_BASE, GROQ_MODEL, GROQ_ENABLED |
| `app/core/http.py` | Изменён | +HTTP-клиент `groq_client()` с timeout 30s, init/close |
| `app/data/providers/groq.py` | **Новый** | Groq API provider — chat completions, кеш 24ч, retry |
| `app/services/ai_enrich.py` | **Новый** | `enrich_analysis()` + `translate_text()` с graceful fallback |
| `app/services/publishing.py` | Изменён | AI-вызовы в 3 точках после `_build_market_text()` |

### Архитектура
```
Шаблон (ru) → AI-обогащение (Groq) → AI-перевод (Groq) → Telegram
                    ↓ fallback              ↓ fallback
              шаблон как есть          DeepL (если настроен)
```

### Конфигурация
```env
GROQ_API_KEY=gsk_...
GROQ_ENABLED=true
GROQ_MODEL=llama-3.3-70b-versatile
```

### Ветка / Коммит
- Ветка: `feat/groq-integration`
- Коммит: `901bcc7` — feat: integrate Groq API for AI-enriched Telegram analytics and translation

### Статус
- ✅ Код написан и проверен (compileall OK)
- ✅ Docker rebuild + restart
- ✅ GROQ_ENABLED=true в .env
- ⏳ Ожидается первый live publish для проверки

---

## Следующий шаг

**Фаза 1**: Инфраструктура + Мониторщик
- 4 миграции БД (ai_office_reports, scout_reports, news_articles, news_sources)
- Каркас `app/ai_office/`
- Мониторщик: 6 health checks → Telegram
- Базовый Telegram бот с командами
- Docker service `ai-office`
