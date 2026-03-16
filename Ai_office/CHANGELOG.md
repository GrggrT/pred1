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

## 2026-03-15 — Фазы 1-3: Каркас + Мониторщик + Аналитик + Контентщик + Скаут (ЗАВЕРШЕНЫ ✅)

### Что сделано
Полный каркас AI Office с 4 агентами:
1. **Monitor** — 6 health checks, алерты при проблемах, cron */6h
2. **Analyst** — отчёт по settled predictions за 24ч, cron 08:00 UTC
3. **Scout** — Gemini 2.5 Flash + web search grounding, вердикты GREEN/YELLOW/RED, cron 10:00 UTC
4. **Content Writer** — Telegram-пост с прогнозами, фильтрует RED из scout, cron 12:00 UTC

### Ключевые коммиты
- `7cf836e` — Phase 1: Infrastructure + Monitor
- `96811a0` — Phase 2: Analyst + Content Writer
- `bde8b5b` — Phase 3: Scout agent
- `5aa3389` — fix: switch Gemini to 2.5 Flash
- `a942ca0` — fix: scout reports in Russian, skip already-analyzed

---

## 2026-03-15 — Фаза 4: Новости (бэкенд) (ЗАВЕРШЕНА ✅)

### Что сделано
RSS-парсер + LLM-фильтрация и генерация новостей:
1. **RSS fetcher** — feedparser, 3 источника (BBC, Guardian, ESPN)
2. **LLM фильтр** — Groq отбирает релевантные новости для 5 лиг
3. **LLM генератор** — Groq генерирует статьи на русском с категоризацией
4. **Telegram** — `/news` (последние), `/news fetch` (запуск пайплайна)
5. **API** — `/api/public/v1/news` (публичный endpoint)

### Файлы

| Файл | Действие | Описание |
|------|----------|----------|
| `app/ai_office/agents/news.py` | **Новый** | Агент новостей: RSS → filter → generate → DB → Telegram |
| `app/core/config.py` | Изменён | +`ai_office_news_cron`, `ai_office_news_feeds` |
| `app/ai_office/config.py` | Изменён | +`NEWS_FILTER_PROMPT`, `NEWS_WRITER_PROMPT`, обновлён `HELP_TEXT` |
| `app/ai_office/queries.py` | Изменён | +6 функций для news_sources/news_articles |
| `app/ai_office/telegram_bot.py` | Изменён | +`/news` команда (show + fetch) |
| `app/ai_office/runner.py` | Изменён | +news cron job (*/4h) |
| `app/main.py` | Изменён | +`/api/public/v1/news` публичный endpoint |

### Архитектура
```
RSS (BBC, Guardian, ESPN) → feedparser → news_sources (dedup)
    → Groq filter (batch) → Groq writer (per article)
    → news_articles (published) → Telegram digest + API
```

### Конфигурация
```env
AI_OFFICE_NEWS_CRON=0 */4 * * *
AI_OFFICE_NEWS_FEEDS=https://feeds.bbci.co.uk/sport/football/rss.xml,...
```

### Статус
- ✅ Код написан и проверен (compileall OK)
- ✅ Docker build OK, 5 cron jobs registered
- ✅ Deployed to GCP
- ⏳ Ожидается первый `/news fetch` для проверки

---

## 2026-03-16 — Фаза 5: Ресёрчер + полировка (ЗАВЕРШЕНА ✅)

### Что сделано
1. **Researcher agent** — Gemini web search по 7 ключевым словам, еженедельный отчёт
2. **`/ask` command** — свободные вопросы по данным через Groq (DB context → LLM → ответ)
3. **`/research` command** — ручной запуск ресёрчера
4. **TTL cleanup** — автоматическое удаление reports, scout_reports, news > 90 дней (daily 03:00 UTC)
5. **7 cron jobs** — monitor, analyst, scout, content, news, researcher, cleanup

### Файлы

| Файл | Действие | Описание |
|------|----------|----------|
| `app/ai_office/agents/researcher.py` | **Новый** | Researcher agent: Gemini + web search → weekly report |
| `app/ai_office/config.py` | Изменён | +RESEARCHER_SYSTEM_PROMPT, ASK_SYSTEM_PROMPT, RESEARCH_KEYWORDS, обновлён HELP_TEXT |
| `app/ai_office/queries.py` | Изменён | +fetch_ask_context, cleanup_old_reports/scout/news |
| `app/ai_office/telegram_bot.py` | Изменён | +/research, /ask commands (10 handlers total) |
| `app/ai_office/runner.py` | Изменён | +researcher cron, +cleanup cron (7 jobs total) |
| `app/core/config.py` | Изменён | +ai_office_researcher_cron |

### Архитектура
```
6 агентов + 1 cleanup:
├── Monitor      (Groq, */6h)     — health checks
├── Analyst      (Groq, 08:00)    — daily report
├── Scout        (Gemini, 10:00)  — match context
├── Content      (Groq, 12:00)    — picks post
├── News         (Groq, */4h)     — RSS → articles
├── Researcher   (Gemini, Sun 10:00) — weekly research
└── Cleanup      (SQL, 03:00)     — TTL 90 days
```

### Telegram Commands (10 total)
/start, /status, /settled, /picks, /scout, /override, /news, /research, /ask, /help

---

## Оставшиеся задачи (отложены)
- Admin UI секция AI Office (лента отчётов, скаут-панель, модерация новостей)
- Public site новостная лента (карточки, фильтры, полная статья)
- Scout accuracy stats (нужно 50+ отчётов)
- Чат в админке (/ask через веб-интерфейс)
