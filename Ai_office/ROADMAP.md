# AI Office — Roadmap

**Старт**: 2026-03-15
**Статус**: Фазы 0-5 завершены (все агенты)

---

## Фаза 0: Подготовка (ЗАВЕРШЕНА ✅)

- [x] Интеграция Groq API в проект (provider + HTTP-клиент + кеш)
- [x] AI-обогащение Telegram-постов (enrich_analysis)
- [x] AI-перевод через Groq (замена DeepL)
- [x] GROQ_ENABLED=true в production

**Результат**: Groq работает, инфраструктура LLM готова.

---

## Фаза 1: Инфраструктура + Мониторщик (ЗАВЕРШЕНА ✅)

### 1.1 Миграции БД
- [x] `ai_office_reports` — отчёты всех агентов
- [x] `scout_reports` — вердикты скаута per fixture
- [x] `news_articles` — новости для сайта
- [x] `news_sources` — дедупликация URL

### 1.2 Каркас AI Office
- [x] `app/ai_office/` — структура модулей
- [x] `app/ai_office/config.py` — конфигурация агентов
- [x] `app/ai_office/queries.py` — SQL-запросы health checks
- [x] `app/ai_office/runner.py` — scheduler агентов

### 1.3 Мониторщик
- [x] 6 SQL health checks
- [x] Пороги алертов
- [x] Telegram алерт при проблемах
- [x] Silent mode при норме
- [x] Расписание: каждые 6 часов

### 1.4 Telegram бот (базовый)
- [x] python-telegram-bot integration
- [x] `/status` — ручной запрос health check
- [x] `/help` — список команд
- [x] Авторизация по TELEGRAM_OWNER_ID

### 1.5 Docker
- [x] Service `ai-office` в docker-compose.yml
- [x] Healthcheck
- [x] .env переменные

**Критерий готовности**: `/status` в Telegram возвращает health check ✅

---

## Фаза 2: Аналитик + Контентщик-прогнозы (ЗАВЕРШЕНА ✅)

### 2.1 Аналитик
- [x] SQL: settled predictions за 24ч
- [x] System prompt (раздел 7.2 ТЗ)
- [x] Telegram: утренний отчёт 08:00 UTC
- [x] Сохранение в ai_office_reports
- [x] `/settled` команда в боте

### 2.2 Контентщик (Роль A: прогнозы)
- [x] SQL: upcoming predictions (не заблокированные скаутом)
- [x] System prompt (раздел 7.4 ТЗ)
- [x] Telegram: пост с прогнозами 12:00 UTC
- [x] `/picks` команда в боте

**Критерий готовности**: утренний отчёт приходит автоматически ✅

---

## Фаза 3: Скаут (ЗАВЕРШЕНА ✅)

### 3.1 Gemini API интеграция
- [x] Gemini 2.5 Flash provider (с web search grounding)
- [x] HTTP-клиент в http.py
- [x] Конфиг: GEMINI_API_KEY, GEMINI_MODEL
- [x] Web search grounding (google_search tool)

### 3.2 Скаут-агент
- [x] SQL: upcoming predictions для анализа
- [x] Web search по каждому матчу (травмы, мотивация, дерби)
- [x] System prompt (JSON формат + русский язык)
- [x] Вердикты 🟢/🟡/🔴
- [x] Сохранение в scout_reports
- [x] Расписание: 10:00 UTC

### 3.3 Интеграция с publishing
- [x] Проверка scout_reports.verdict = 'red' → skip publication
- [x] Override через Telegram (`/override fixture_id green/red`)
- [ ] Override через админку (→ Фаза UI)

### 3.4 Telegram
- [x] `/scout` — текущие вердикты
- [x] Автоматический push 10:00 UTC

**Критерий готовности**: `/scout` показывает вердикты, 🔴 блокирует публикацию ✅

---

## Фаза 4: Новости — бэкенд (ЗАВЕРШЕНА ✅)

### 4.1 RSS парсер
- [x] feedparser (уже в requirements.txt)
- [x] Источники: BBC Sport, Guardian Football, ESPN FC
- [x] Дедупликация по URL (news_sources)
- [x] Groq LLM фильтр: только 5 активных лиг

### 4.2 Контентщик (Роль B: новости)
- [x] Фильтрация сырых новостей через Groq (batch)
- [x] Генерация статей (title, summary, body, category) на русском
- [x] Сохранение в news_articles (status=published)
- [x] Расписание: каждые 4 часа
- [x] `/news` + `/news fetch` команды в боте
- [x] Public API: `/api/public/v1/news`

### 4.3 Admin UI — AI Office секция (→ отдельная фаза)
- [ ] Лента отчётов (все агенты)
- [ ] Скаут-панель (вердикты + override)
- [ ] Статистика скаута (accuracy)
- [ ] Модерация новостей (draft/publish/archive)

### 4.4 Public Site — Новости (→ отдельная фаза)
- [ ] Лента карточек (логотипы, цветовые категории)
- [ ] Фильтры: лига + категория
- [ ] Полная статья (markdown → HTML)

**Критерий готовности (бэкенд)**: `/news fetch` парсит RSS и генерирует статьи ✅

---

## Фаза 5: Ресёрчер + полировка (ЗАВЕРШЕНА ✅)

### 5.1 Ресёрчер
- [x] Gemini web search по ключевым словам
- [x] System prompt (раздел 7.5 ТЗ)
- [x] Еженедельный отчёт (воскресенье 10:00 UTC)
- [ ] Файл `reports/research/YYYY-WXX.md` (→ сохраняется в ai_office_reports)
- [x] `/research` команда

### 5.2 Диалоговый режим
- [x] `/ask` — свободные вопросы по данным
- [x] Определение нужного SQL → запрос → Groq → ответ
- [ ] Чат в админке (→ отдельная фаза UI)

### 5.3 Полировка
- [ ] Scout accuracy stats (после 50+ отчётов)
- [x] Error handling / fallbacks
- [x] TTL: удалять reports > 90 дней (daily cron 03:00 UTC)
- [x] Логирование + метрики
- [ ] Нагрузочное тестирование free tier лимитов

**Критерий готовности**: все 5 агентов работают автономно 24/7 ✅

---

## Зависимости между фазами

```
Фаза 0 ──→ Фаза 1 ──→ Фаза 2 ──→ Фаза 3 ──→ Фаза 4 ──→ Фаза 5
(Groq)     (Каркас)   (Аналитик) (Скаут)    (Новости)  (Research)
  ✅                              ↑ Gemini              ↑ Gemini
                                  └──────────────────────┘
```

Gemini нужен для Фазы 3 и 5 (можно интегрировать один раз в Фазе 3).

---

## Риски

| Риск | Mitigation |
|------|-----------|
| Groq/Gemini free tier ограничения | ~10-30 req/day, лимит 14K+ — хватит с запасом |
| LLM галлюцинации в скауте | RED требует конкретный фактор, owner override |
| RSS-источники меняют формат | Мониторщик алертит при 0 новостей > 24ч |
| DB bloat от reports | TTL 90 дней, maintenance job |
