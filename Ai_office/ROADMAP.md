# AI Office — Roadmap

**Старт**: 2026-03-15
**Статус**: Фаза 0 завершена, Фаза 1 в очереди

---

## Фаза 0: Подготовка (ЗАВЕРШЕНА ✅)

- [x] Интеграция Groq API в проект (provider + HTTP-клиент + кеш)
- [x] AI-обогащение Telegram-постов (enrich_analysis)
- [x] AI-перевод через Groq (замена DeepL)
- [x] GROQ_ENABLED=true в production

**Результат**: Groq работает, инфраструктура LLM готова.

---

## Фаза 1: Инфраструктура + Мониторщик (~1 неделя)

### 1.1 Миграции БД
- [ ] `ai_office_reports` — отчёты всех агентов
- [ ] `scout_reports` — вердикты скаута per fixture
- [ ] `news_articles` — новости для сайта
- [ ] `news_sources` — дедупликация URL

### 1.2 Каркас AI Office
- [ ] `app/ai_office/` — структура модулей
- [ ] `app/ai_office/config.py` — конфигурация агентов
- [ ] `app/ai_office/db.py` — SQL-запросы health checks
- [ ] `app/ai_office/llm.py` — обёртки Groq + Gemini
- [ ] `app/ai_office/runner.py` — scheduler агентов

### 1.3 Мониторщик
- [ ] 6 SQL health checks
- [ ] Пороги алертов
- [ ] Telegram алерт при проблемах
- [ ] Silent mode при норме
- [ ] Расписание: каждые 6 часов

### 1.4 Telegram бот (базовый)
- [ ] python-telegram-bot integration
- [ ] `/status` — ручной запрос health check
- [ ] `/help` — список команд
- [ ] Авторизация по TELEGRAM_OWNER_ID

### 1.5 Docker
- [ ] Service `ai-office` в docker-compose.yml
- [ ] Healthcheck
- [ ] .env переменные

**Критерий готовности**: `/status` в Telegram возвращает health check

---

## Фаза 2: Аналитик + Контентщик-прогнозы (~1 неделя)

### 2.1 Аналитик
- [ ] SQL: settled predictions за 24ч
- [ ] System prompt (раздел 7.2 ТЗ)
- [ ] Telegram: утренний отчёт 08:00 UTC
- [ ] Сохранение в ai_office_reports
- [ ] Файл `reports/daily/YYYY-MM-DD.md`
- [ ] `/settled` команда в боте

### 2.2 Контентщик (Роль A: прогнозы)
- [ ] SQL: upcoming predictions (не заблокированные скаутом)
- [ ] System prompt (раздел 7.4 ТЗ)
- [ ] Telegram: пост с прогнозами 12:00 UTC
- [ ] `/picks` команда в боте

**Критерий готовности**: утренний отчёт приходит автоматически

---

## Фаза 3: Скаут (~1 неделя)

### 3.1 Gemini API интеграция
- [ ] Gemini provider (аналог groq.py)
- [ ] HTTP-клиент в http.py
- [ ] Конфиг: GEMINI_API_KEY
- [ ] Web search grounding

### 3.2 Скаут-агент
- [ ] SQL: upcoming predictions для анализа
- [ ] Web search по каждому матчу (травмы, мотивация, дерби)
- [ ] System prompt (раздел 7.3 ТЗ)
- [ ] Вердикты 🟢/🟡/🔴
- [ ] Сохранение в scout_reports
- [ ] Расписание: 10:00 UTC

### 3.3 Интеграция с publishing
- [ ] Проверка scout_reports.verdict = 'red' → skip publication
- [ ] Override через Telegram (`/override fixture_id green/red`)
- [ ] Override через админку

### 3.4 Telegram
- [ ] `/scout` — текущие вердикты
- [ ] Автоматический push 10:00 UTC

**Критерий готовности**: `/scout` показывает вердикты, 🔴 блокирует публикацию

---

## Фаза 4: Новости + Админка (~1.5 недели)

### 4.1 RSS парсер
- [ ] feedparser + httpx + bs4
- [ ] Источники: BBC, Guardian, ESPN, лиговые сайты
- [ ] Дедупликация по URL (news_sources)
- [ ] Фильтр: только 5 активных лиг

### 4.2 Контентщик (Роль B: новости)
- [ ] Фильтрация + агрегация сырых новостей через Groq
- [ ] Генерация статей (title, summary, body, category)
- [ ] Сохранение в news_articles (status=draft)
- [ ] Расписание: каждые 4 часа

### 4.3 Admin UI — AI Office секция
- [ ] Лента отчётов (все агенты)
- [ ] Скаут-панель (вердикты + override)
- [ ] Статистика скаута (accuracy)
- [ ] Модерация новостей (draft/publish/archive)
- [ ] API endpoints

### 4.4 Public Site — Новости
- [ ] Лента карточек (логотипы, цветовые категории)
- [ ] Фильтры: лига + категория
- [ ] Полная статья (markdown → HTML)
- [ ] Public API: `/api/public/v1/news`

**Критерий готовности**: новости появляются на сайте, модерация в админке

---

## Фаза 5: Ресёрчер + полировка (~1 неделя)

### 5.1 Ресёрчер
- [ ] Gemini web search по ключевым словам
- [ ] System prompt (раздел 7.5 ТЗ)
- [ ] Еженедельный отчёт (воскресенье 10:00 UTC)
- [ ] Файл `reports/research/YYYY-WXX.md`
- [ ] `/research` команда

### 5.2 Диалоговый режим
- [ ] `/ask` — свободные вопросы по данным
- [ ] Определение нужного SQL → запрос → Groq → ответ
- [ ] Чат в админке

### 5.3 Полировка
- [ ] Scout accuracy stats (после 50+ отчётов)
- [ ] Error handling / fallbacks
- [ ] TTL: удалять reports > 90 дней
- [ ] Логирование + метрики
- [ ] Нагрузочное тестирование free tier лимитов

**Критерий готовности**: все 5 агентов работают автономно 24/7

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
