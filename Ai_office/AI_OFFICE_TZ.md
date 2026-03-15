# ТЗ: AI Office — операционный офис футбольных прогнозов

**Версия**: 1.0
**Дата**: 2026-03-15
**Автор**: Gregory + Claude (архитектор)

---

## 1. Обзор проекта

### 1.1 Что это

Автономный AI-офис из 5 агентов, работающий внутри проекта football-predict-optimizer. Агенты не пишут код — они анализируют данные, создают контент, мониторят систему и фильтруют predictions. Весь ввод-вывод через Telegram бот + админ-панель на сайте.

### 1.2 Цели

1. Автоматический мониторинг здоровья системы 24/7
2. Ежедневный анализ результатов predictions
3. Контекстный скаутинг матчей (травмы, мотивация, дерби) с влиянием на публикацию
4. Генерация контента для Telegram-канала и сайта
5. Новостная лента на сайте — только релевантные события по активным лигам/матчам
6. Еженедельный research новых методов

### 1.3 Принципы

- **$0/месяц** — только бесплатные LLM API (Groq, Gemini)
- **Периметр модели** — агенты работают только с 5 активными лигами и матчами из predictions
- **Скаут не меняет модель** — только фильтрует publication (вариант A)
- **Данные копятся** — scout_reports, news_articles в БД для будущего анализа
- **Два интерфейса** — Telegram бот (мобильность) + админка (полная картина)

### 1.4 Стек

- **LLM**: Groq (Llama 3.3 70B) — primary, Google Gemini (2.0 Flash) — web search + длинный контекст
- **Бот**: python-telegram-bot
- **Парсинг**: feedparser, httpx, BeautifulSoup
- **БД**: PostgreSQL (существующая)
- **Деплой**: Docker-контейнер в существующем docker-compose
- **Язык**: Python (asyncio)

---

## 2. Агенты

### 2.1 🛡️ Мониторщик (Monitor)

**Задача**: Следить за здоровьем системы. Алертить при проблемах. Молчать когда всё ОК.

**Расписание**: Каждые 6 часов (00:00, 06:00, 12:00, 18:00 UTC)

**LLM**: Groq

**Вход**: 6 SQL health checks:

```sql
-- 1. Свежесть sync_data
SELECT job_name, status, started_at,
       EXTRACT(EPOCH FROM now() - started_at)/3600 as hours_ago
FROM job_runs
WHERE job_name = 'sync_data'
ORDER BY started_at DESC LIMIT 1;

-- 2. Predictions для upcoming матчей
SELECT COUNT(*) as upcoming_with_predictions
FROM predictions p
JOIN fixtures f ON f.id = p.fixture_id
WHERE f.status = 'NS' AND f.kickoff BETWEEN now() AND now() + interval '48 hours';

-- 3. Unsettled прошедшие матчи
SELECT COUNT(*) as unsettled
FROM fixtures f
JOIN predictions p ON p.fixture_id = f.id
WHERE f.status = 'FT' AND p.status = 'PENDING'
  AND f.kickoff < now() - interval '3 hours';

-- 4. API quota
SELECT value FROM app_config WHERE key = 'api_football_requests_today';

-- 5. Pinnacle sync
SELECT COUNT(*) as pinnacle_odds_24h
FROM odds
WHERE bookmaker_id = 4 AND updated_at > now() - interval '24 hours';

-- 6. Ошибки за 24ч
SELECT COUNT(*) as errors_24h
FROM job_runs
WHERE status = 'ERROR' AND started_at > now() - interval '24 hours';
```

**Пороги алертов**:

| Check | Порог | Severity |
|-------|-------|----------|
| sync_data > 6ч назад | ⚠️ Warning | medium |
| upcoming_predictions = 0 | 🔴 Critical | high |
| unsettled > 10 | ⚠️ Warning | medium |
| API quota > 80% | ⚠️ Warning | medium |
| pinnacle_24h = 0 | ⚠️ Warning | medium |
| errors_24h > 3 | 🔴 Critical | high |

**Выход**:
- Если всё ОК → ничего не отправлять (silent mode)
- Если проблема → Telegram алерт + запись в `ai_office_reports`

**System prompt**: см. раздел 7.1

---

### 2.2 📊 Аналитик (Analyst)

**Задача**: Утренний разбор вчерашних settled predictions. Что сработало, что нет, паттерны.

**Расписание**: Ежедневно 08:00 UTC (только если есть новые settled)

**LLM**: Groq

**Вход**: SQL settled за последние 24ч:

```sql
WITH yesterday AS (
    SELECT 'predictions' as src, p.market, p.selection, p.status,
           p.odd, p.feature_flags, f.home_team_name, f.away_team_name,
           f.home_goals, f.away_goals, l.name as league,
           p.feature_flags->>'p_home' as p_home,
           p.feature_flags->>'p_draw' as p_draw,
           p.feature_flags->>'p_away' as p_away,
           p.feature_flags->>'ev_best' as ev
    FROM predictions p
    JOIN fixtures f ON f.id = p.fixture_id
    JOIN leagues l ON l.id = f.league_id
    WHERE p.settled_at > now() - interval '24 hours'
      AND p.status IN ('WON','LOST')
    UNION ALL
    SELECT 'predictions_totals', pt.market, pt.selection, pt.status,
           pt.initial_odd, pt.feature_flags, f.home_team_name, f.away_team_name,
           f.home_goals, f.away_goals, l.name as league,
           NULL, NULL, NULL,
           pt.feature_flags->>'ev' as ev
    FROM predictions_totals pt
    JOIN fixtures f ON f.id = pt.fixture_id
    JOIN leagues l ON l.id = f.league_id
    WHERE pt.settled_at > now() - interval '24 hours'
      AND pt.status IN ('WON','LOST')
)
SELECT * FROM yesterday ORDER BY league, src;
```

**Выход**:
- Telegram сообщение (краткий отчёт)
- Запись в `ai_office_reports`
- Файл `reports/daily/YYYY-MM-DD.md`

**System prompt**: см. раздел 7.2

---

### 2.3 🔍 Скаут (Scout)

**Задача**: Контекстный анализ завтрашних матчей. Находит то, что модель не видит. Даёт вердикт 🟢/🟡/🔴. Красный вердикт блокирует публикацию prediction.

**Расписание**: Ежедневно 10:00 UTC (если есть upcoming predictions)

**LLM**: Gemini 2.0 Flash (с grounding / web search)

**Вход**: SQL upcoming predictions + web search по командам:

```sql
SELECT p.id as prediction_id, p.market, p.selection, p.odd,
       p.feature_flags->>'ev_best' as ev,
       p.feature_flags->>'signal_score' as signal,
       f.id as fixture_id, f.home_team_name, f.away_team_name,
       f.kickoff, l.name as league
FROM predictions p
JOIN fixtures f ON f.id = p.fixture_id
JOIN leagues l ON l.id = f.league_id
WHERE f.status = 'NS'
  AND f.kickoff BETWEEN now() AND now() + interval '36 hours'
  AND p.selection != 'SKIP'
ORDER BY f.kickoff;
```

**Скаут ищет по каждому матчу**:
1. Ключевые травмы/дисквалификации (особенно вратари, центральные нападающие)
2. Мотивация: борьба за титул/вылет/еврокубки vs ничего не решает
3. Дерби/принципиальное соперничество
4. Смена тренера (< 5 матчей назад)
5. Плотный график (3 матча за 7 дней)

**Вердикт**:
- 🟢 GREEN — контекст поддерживает или нейтрален, публиковать
- 🟡 YELLOW — есть риски, но не критичные, публиковать с пометкой
- 🔴 RED — контекст сильно противоречит ставке, НЕ публиковать

**Выход**:
- Telegram сообщение (список матчей с вердиктами)
- Запись в `scout_reports` (per fixture)
- Админка: скаут-панель с override

**Влияние на систему**:
- `build_predictions` или `publishing` проверяет: есть ли `scout_reports.verdict = 'red'` для fixture → skip publication
- Prediction остаётся в БД (для анализа), но не публикуется
- Владелец может override вердикт через админку или Telegram

**System prompt**: см. раздел 7.3

---

### 2.4 ✍️ Контентщик (Content Writer)

**Две роли**:
- **A**: Прогнозы для Telegram (ежедневно)
- **B**: Новости для сайта (каждые 4 часа)

#### Роль A: Прогнозы

**Расписание**: Ежедневно 12:00 UTC (после скаута)

**LLM**: Groq

**Вход**: Predictions (не заблокированные скаутом) + скаут-отчёт

```sql
SELECT p.market, p.selection, p.odd,
       p.feature_flags->>'ev_best' as ev,
       f.home_team_name, f.away_team_name,
       f.kickoff, l.name as league,
       sr.report_text as scout_context,
       sr.verdict as scout_verdict
FROM predictions p
JOIN fixtures f ON f.id = p.fixture_id
JOIN leagues l ON l.id = f.league_id
LEFT JOIN scout_reports sr ON sr.fixture_id = f.id
WHERE f.status = 'NS'
  AND f.kickoff BETWEEN now() AND now() + interval '36 hours'
  AND p.selection != 'SKIP'
  AND (sr.verdict IS NULL OR sr.verdict != 'red')
ORDER BY f.kickoff;
```

**Выход**: Telegram-пост, готовый к публикации

#### Роль B: Новости

**Расписание**: Каждые 4 часа (06:00, 10:00, 14:00, 18:00, 22:00 UTC)

**Поток**:
```
1. Python: SELECT upcoming fixtures + team names из БД
2. Python: парсить RSS/сайты по этим командам (feedparser, httpx, bs4)
3. Python: дедупликация (URL уже в news_sources?)
4. LLM (Groq): фильтрация → агрегация → переписывание
5. Python: сохранить в news_articles (status='draft' или 'published')
```

**Источники парсинга** (бесплатные RSS/scraping):

| Источник | Тип | URL паттерн |
|----------|-----|-------------|
| BBC Sport Football | RSS | `feeds.bbci.co.uk/sport/football/rss.xml` |
| The Guardian Football | RSS | `theguardian.com/football/rss` |
| ESPN FC | RSS | `espn.com/espn/rss/soccer/news` |
| Transfermarkt | Scraping | По команде: injuries, transfers |
| Лиговые сайты | RSS/Scraping | premierleague.com, laliga.com, etc. |

**Фильтр релевантности** (КРИТИЧЕСКИЙ):
- Только 5 активных лиг: EPL (39), La Liga (140), Serie A (135), Bundesliga (78), Ligue 1 (61)
- Приоритет 1: команды из upcoming predictions (ближайшие 48ч)
- Приоритет 2: команды из активных лиг (трансферы, травмы)
- Всё остальное — игнорировать

**Визуал новостей**: Логотипы команд из `logo_url` в БД + цветовая карточка по категории:
- Превью матча — синий
- Обзор матча — зелёный
- Травма — красный
- Трансфер — оранжевый
- Турнирная ситуация — фиолетовый

**Без фотографий** — авторские права. Только логотипы команд/лиг (уже есть в БД).

**System prompt**: см. раздел 7.4

---

### 2.5 📚 Ресёрчер (Researcher)

**Задача**: Еженедельный обзор новых публикаций и методов по football prediction.

**Расписание**: Воскресенье 10:00 UTC

**LLM**: Gemini 2.0 Flash (web search)

**Вход**: Текущее описание модели + ключевые слова

**Ключевые слова**:
- Football match prediction models 2025-2026
- Dixon-Coles improvements
- COM-Poisson sports
- Expected goals xG models
- Betting market efficiency
- Stacking ensemble sports prediction
- Closing line value betting

**Выход**:
- Файл `reports/research/YYYY-WXX.md`
- Telegram: краткая выжимка (топ-3 находки)
- Запись в `ai_office_reports`

**System prompt**: см. раздел 7.5

---

## 3. База данных — новые таблицы

### 3.1 ai_office_reports

Все отчёты всех агентов:

```sql
CREATE TABLE ai_office_reports (
    id SERIAL PRIMARY KEY,
    agent VARCHAR(20) NOT NULL,         -- 'monitor', 'analyst', 'scout', 'content', 'researcher'
    report_type VARCHAR(30) NOT NULL,   -- 'health_check', 'daily_analysis', 'scout_report', 
                                        -- 'prediction_post', 'news_batch', 'weekly_research'
    report_text TEXT NOT NULL,           -- Полный текст отчёта
    metadata JSONB DEFAULT '{}',        -- Доп. данные (кол-во матчей, settled, etc.)
    telegram_sent BOOLEAN DEFAULT false, -- Отправлено ли в Telegram
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_aor_agent ON ai_office_reports(agent);
CREATE INDEX idx_aor_created ON ai_office_reports(created_at DESC);
```

### 3.2 scout_reports

Вердикты скаута per fixture:

```sql
CREATE TABLE scout_reports (
    id SERIAL PRIMARY KEY,
    fixture_id INTEGER NOT NULL REFERENCES fixtures(id),
    prediction_id INTEGER,              -- Связь с prediction (если есть)
    verdict VARCHAR(10) NOT NULL,       -- 'green', 'yellow', 'red'
    report_text TEXT NOT NULL,           -- Полный текст анализа
    factors JSONB DEFAULT '{}',         -- {"key_player_missing": true, "derby": false, 
                                        --  "motivation_diff": 1, "manager_change": false,
                                        --  "congested_schedule": false}
    model_selection VARCHAR(30),        -- Что модель предложила
    model_odd NUMERIC(8,3),
    override_verdict VARCHAR(10),       -- Ручной override владельцем (nullable)
    override_reason TEXT,               -- Причина override
    actual_result VARCHAR(30),          -- Заполняется после матча
    scout_correct BOOLEAN,              -- Заполняется после матча
    created_at TIMESTAMPTZ DEFAULT now(),
    
    UNIQUE(fixture_id)                  -- Один отчёт per fixture
);

CREATE INDEX idx_sr_fixture ON scout_reports(fixture_id);
CREATE INDEX idx_sr_verdict ON scout_reports(verdict);
```

### 3.3 news_articles

Новости для сайта:

```sql
CREATE TABLE news_articles (
    id SERIAL PRIMARY KEY,
    title VARCHAR(300) NOT NULL,
    slug VARCHAR(300) NOT NULL UNIQUE,   -- URL: /news/arsenal-saka-injury
    body TEXT NOT NULL,                   -- Markdown
    summary VARCHAR(500),                -- Краткое описание для карточки
    category VARCHAR(20) NOT NULL,       -- 'preview', 'review', 'transfer', 
                                         -- 'injury', 'standings'
    league_id INTEGER REFERENCES leagues(id),  -- Nullable
    fixture_id INTEGER REFERENCES fixtures(id), -- Nullable
    home_team_name VARCHAR(100),         -- Для отображения логотипов
    away_team_name VARCHAR(100),
    sources JSONB DEFAULT '[]',          -- ["https://bbc.com/...", ...]
    status VARCHAR(20) DEFAULT 'draft',  -- 'draft', 'published', 'archived'
    published_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_na_status ON news_articles(status);
CREATE INDEX idx_na_published ON news_articles(published_at DESC);
CREATE INDEX idx_na_league ON news_articles(league_id);
CREATE INDEX idx_na_category ON news_articles(category);
```

### 3.4 news_sources

Дедупликация — какие URL уже обработаны:

```sql
CREATE TABLE news_sources (
    id SERIAL PRIMARY KEY,
    url VARCHAR(1000) NOT NULL UNIQUE,
    source_name VARCHAR(100),            -- 'bbc', 'guardian', 'espn'
    title VARCHAR(500),
    raw_text TEXT,                        -- Сырой текст (для debug)
    processed BOOLEAN DEFAULT false,
    article_id INTEGER REFERENCES news_articles(id),  -- К какой статье привязан
    fetched_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_ns_url ON news_sources(url);
CREATE INDEX idx_ns_processed ON news_sources(processed);
```

---

## 4. Telegram бот

### 4.1 Архитектура

Единый бот, единый token. Все агенты отправляют через него. Владелец управляет через него.

**Библиотека**: `python-telegram-bot` (async)

### 4.2 Команды

| Команда | Агент | Что делает |
|---------|-------|-----------|
| `/status` | Мониторщик | Здоровье системы прямо сейчас |
| `/settled` | Аналитик | Разбор последних результатов |
| `/scout` | Скаут | Контекст завтрашних матчей |
| `/picks` | Контентщик | Готовый пост с прогнозами |
| `/news` | Контентщик | Последние новости |
| `/research` | Ресёрчер | Последние находки |
| `/roi` | — (SQL) | Текущий ROI/profit/win_pct |
| `/override [fixture_id] [green/red]` | Скаут | Перезаписать вердикт скаута |
| `/publish [fixture_id]` | Контентщик | Опубликовать prediction вручную |
| `/ask [вопрос]` | Groq | Свободный вопрос по данным |
| `/help` | — | Список команд |

### 4.3 Автоматические сообщения (push)

| Событие | Агент | Когда |
|---------|-------|-------|
| Алерт системы | Мониторщик | При обнаружении проблемы |
| Утренний отчёт | Аналитик | 08:00 если есть settled |
| Скаут-отчёт | Скаут | 10:00 если есть upcoming |
| Прогнозы дня | Контентщик | 12:00 если есть predictions |
| Еженедельный research | Ресёрчер | Воскресенье 10:00 |

### 4.4 Диалоговый режим

Команда `/ask` позволяет задать вопрос в свободной форме. Бот:
1. Определяет какие данные нужны (SQL)
2. Выгружает из БД
3. Отправляет в Groq с контекстом
4. Возвращает ответ

Примеры:
```
/ask Какой ROI по Bundesliga за последний месяц?
/ask Почему вчера проиграли ставку на Arsenal?
/ask Сколько Pinnacle odds накопилось?
```

### 4.5 Авторизация

Бот отвечает только на `TELEGRAM_OWNER_ID` (один пользователь — владелец). Все остальные — игнорировать.

---

## 5. Админ-панель

### 5.1 Новая секция: AI Office (`#ai-office`)

Добавить в существующую admin panel (`/admin`).

### 5.2 Подсекции

#### 5.2.1 Лента отчётов

- Все отчёты всех агентов хронологически
- Фильтр по агенту: All / Аналитик / Скаут / Мониторщик / Ресёрчер
- Каждый отчёт — карточка с текстом, временем, badge агента
- API: `GET /api/v1/ai-office/reports?agent=&limit=&offset=`

#### 5.2.2 Скаут-панель

- Upcoming матчи с predictions + вердикт скаута рядом:
```
Arsenal vs Chelsea  |  1X2: HOME @ 2.10  |  EV +8%  |  🔴 Saka out  [Override →🟢]
Man City vs Wolves  |  1X2: HOME @ 1.45  |  EV +5%  |  🟢 Full squad
```
- Кнопка override — поменять вердикт вручную с указанием причины
- API: `GET /api/v1/ai-office/scout?date=`
- API: `POST /api/v1/ai-office/scout/override` body: `{fixture_id, verdict, reason}`

#### 5.2.3 Статистика скаута

- Accuracy: из N красных — сколько реально проиграли?
- Accuracy: из N зелёных — сколько реально выиграли?
- Таблица по вердиктам:
```
| Verdict | Count | Win% | ROI  |
|---------|-------|------|------|
| 🟢 Green  | 45  | 58%  | +5%  |
| 🟡 Yellow | 12  | 42%  | -8%  |
| 🔴 Red    | 8   | 25%  | -40% |  ← скаут правильно фильтрует!
```
- Доступна после 50+ scout_reports с actual_result
- API: `GET /api/v1/ai-office/scout/accuracy`

#### 5.2.4 Новости (модерация)

- Список news_articles: draft / published
- Preview статьи
- Кнопки: Publish / Archive / Edit
- API: `GET /api/v1/ai-office/news?status=&limit=`
- API: `POST /api/v1/ai-office/news/{id}/publish`
- API: `POST /api/v1/ai-office/news/{id}/archive`

#### 5.2.5 Чат

- Текстовое поле для `/ask`-подобных вопросов
- Ответ агента отображается в чате
- API: `POST /api/v1/ai-office/ask` body: `{question}`

---

## 6. Public Site — Новости

### 6.1 Секция "Новости" (`#news`)

Новая секция на public site (`/`).

**Лента**: Карточки новостей хронологически. Каждая карточка:
- Логотипы команд (если привязана к fixture)
- Логотип лиги (если привязана к лиге)
- Цветовая полоса по категории
- Заголовок
- Summary (2-3 предложения)
- Дата
- Категория badge

**Фильтры**: Лига + Категория (превью / обзор / травма / трансфер / турнирная)

**Полная статья**: Клик → развёрнутый текст. Markdown → HTML.

**API** (public, без авторизации):
```
GET /api/public/v1/news?league_id=&category=&limit=&offset=
GET /api/public/v1/news/{slug}
```

### 6.2 Цветовая схема категорий

| Категория | Цвет | CSS variable |
|-----------|------|-------------|
| preview | `#38bdf8` (sky blue) | `--clr-news-preview` |
| review | `#22c55e` (green) | `--clr-news-review` |
| injury | `#f43f5e` (red) | `--clr-news-injury` |
| transfer | `#ff7a1a` (orange) | `--clr-news-transfer` |
| standings | `#a855f7` (purple) | `--clr-news-standings` |

---

## 7. System Prompts

### 7.1 Мониторщик

```
Ты — мониторщик AI-офиса футбольных прогнозов.
Следишь за здоровьем системы.

Входные данные: результаты 6 health checks.

Правила:
- Если ВСЁ в норме → ответь ТОЛЬКО: "✅ Система в норме"
- Если есть проблема → алерт

Пороги:
1. sync_data > 6 часов → ⚠️ Sync задержка
2. upcoming_predictions = 0 при наличии матчей → 🔴 Predictions не генерируются
3. unsettled > 10 → ⚠️ Settlement отстаёт
4. API quota > 80% → ⚠️ API quota
5. pinnacle_24h = 0 → ⚠️ Pinnacle sync остановлен
6. errors_24h > 3 → 🔴 Много ошибок

Формат алерта:
🛡️ Мониторинг [время UTC]
[Emoji] [Проблема]: [описание, 1 предложение]
Рекомендация: [действие]

Только факты. Не паникуй.
```

### 7.2 Аналитик

```
Ты — спортивный аналитик AI-офиса футбольных прогнозов.

Задача: проанализировать вчерашние settled predictions.

Формат:
📊 Дневной отчёт [дата]

Итого: X ставок, Y выиграно, Z проиграно
Profit: +/-N units | ROI: X%

✅ Выигранные:
- [Матч] | [Рынок]: [Selection] @ [Odd] — почему сработало (1 предложение)

❌ Проигранные:
- [Матч] | [Рынок]: [Selection] @ [Odd] — почему не сработало (1 предложение)

💡 Паттерн дня:
[1-2 предложения: общий вывод]

Стиль: конкретно, без воды, с цифрами. Максимум 300 слов.
```

### 7.3 Скаут

```
Ты — скаут AI-офиса футбольных прогнозов.

Задача: для каждого матча найти контекст, который математическая модель
НЕ учитывает. Модель видит: историческую силу команд, xG, Elo, odds.
Модель НЕ видит: травмы, мотивацию, дерби, тренерские изменения, ротацию.

Для каждого матча ищи:
1. Ключевые травмы/дисквалификации (вратари, нападающие)
2. Мотивация (титул, вылет, еврокубки vs nothing to play for)
3. Дерби/принципиальность
4. Смена тренера (< 5 матчей)
5. Плотный график (3 матча за 7 дней)

Вердикт:
🟢 GREEN — контекст поддерживает ставку модели или нейтрален
🟡 YELLOW — есть риски, но не критичные
🔴 RED — контекст сильно противоречит ставке (используй осторожно,
    только при серьёзных факторах вроде отсутствия 3+ ключевых игроков
    или полной демотивации)

Формат:
🔍 Скаут-отчёт [дата]

[Лига]
[Команда1] vs [Команда2] | Ставка: [Selection] @ [Odd]
[Контекст в 2-3 предложениях]
Факторы: травмы=[да/нет], мотивация=[+1/0/-1], дерби=[да/нет]
Вердикт: 🟢/🟡/🔴

ВАЖНО: RED — это не "матч будет сложным". RED = конкретный сильный
фактор, который модель точно не учитывает и который вероятно перевернёт
результат. Если сомневаешься — ставь YELLOW.
```

### 7.4 Контентщик (прогнозы)

```
Ты — контент-менеджер AI-офиса футбольных прогнозов.

Задача: написать Telegram-пост с прогнозами на сегодня.

Входные данные: прогнозы модели + скаут-отчёт.

Формат:
⚽ Прогнозы на [дата]

🏆 [Лига]
[Время] [Команда1] — [Команда2]
📌 [Ставка]: [Selection] @ [Odd]
📊 EV: [X]% | Уверенность: [H/M/L]
💬 [1-2 предложения с учётом скаут-контекста]

---
[Следующий матч]

📈 Серия: [W/L] | ROI 30д: [X]%

⚠️ Прогнозы основаны на математической модели. Ставки — ваша ответственность.

Стиль: информативный, уверенный но не агрессивный. Не обещать выигрыш.
```

### 7.4b Контентщик (новости)

```
Ты — редактор новостной ленты AI-офиса футбольных прогнозов.

Входные данные: сырые новости (заголовки + snippets) из разных источников
по командам из активных прогнозов.

Задача:
1. Отфильтровать нерелевантное (слухи, lifestyle, не наши лиги)
2. Сгруппировать по событию (5 статей про одну травму → 1 заметка)
3. Написать 3-5 заметок СВОИМИ СЛОВАМИ (не копипаст)

Формат каждой заметки:
{
  "title": "Краткий информативный заголовок",
  "summary": "2-3 предложения — суть",
  "body": "Полный текст 150-300 слов. Markdown.",
  "category": "preview|review|injury|transfer|standings",
  "teams": ["Arsenal", "Chelsea"],
  "league": "Premier League"
}

ВАЖНО:
- НЕ копировать текст источников. Переписывать своими словами.
- НЕ включать контент вне 5 лиг (EPL, La Liga, Serie A, Bundesliga, Ligue 1)
- НЕ включать lifestyle/gossip
- Приоритет: матчи с predictions > общие новости лиги
- Всегда указывать категорию
```

### 7.5 Ресёрчер

```
Ты — исследователь AI-офиса футбольных прогнозов.

Текущая модель: Dixon-Coles + xG + 13-feature stacking meta-model.
Рынки: 1X2, TOTAL 2.5, Double Chance.
Лиги: EPL, La Liga, Serie A, Bundesliga, Ligue 1.
Текущие проблемы: CLV ≈ 0, calibration error 0.14, Draw overestimation +3.7%.

Задача: найти свежие (2025-2026) публикации и идеи по темам:
1. Football match prediction models
2. Dixon-Coles improvements
3. Betting market efficiency / CLV
4. Expected goals (xG) models
5. Calibration methods for probability models

Формат:
📚 Weekly Research [неделя]

[Для каждой находки (макс 5)]:
📄 [Название/Источник/URL]
💡 Идея: [1-2 предложения]
🎯 Применимость: Высокая / Средняя / Низкая
📝 Действие: [что конкретно сделать в нашей системе]

Только реально применимое. Не теоретические упражнения.
```

---

## 8. Файловая структура

```
scripts/ai_office/
├── __init__.py
├── config.py              # API keys, расписание, chat IDs, DB URL
├── runner.py              # Главный: schedule + event loop
├── db.py                  # SQL-запросы к PostgreSQL (asyncpg)
├── llm.py                 # Обёртки Groq + Gemini API
├── telegram_bot.py        # python-telegram-bot: commands + handlers
├── news_parser.py         # RSS + scraping (feedparser, httpx, bs4)
├── agents/
│   ├── __init__.py
│   ├── monitor.py
│   ├── analyst.py
│   ├── scout.py
│   ├── content.py         # Роли A (прогнозы) + B (новости)
│   └── researcher.py
└── prompts/
    ├── monitor.txt
    ├── analyst.txt
    ├── scout.txt
    ├── content_picks.txt
    ├── content_news.txt
    └── researcher.txt

reports/
├── daily/                 # YYYY-MM-DD.md
├── scout/                 # YYYY-MM-DD.md
├── research/              # YYYY-WXX.md
└── news/                  # Drafts before DB
```

---

## 9. Docker

### docker-compose.yml (добавить service):

```yaml
ai-office:
  build: .
  command: python scripts/ai_office/runner.py
  environment:
    - GROQ_API_KEY=${GROQ_API_KEY}
    - GEMINI_API_KEY=${GEMINI_API_KEY}
    - TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
    - TELEGRAM_OWNER_ID=${TELEGRAM_OWNER_ID}
    - TELEGRAM_CHAT_ID=${TELEGRAM_CHAT_ID}
    - DATABASE_URL=postgresql://postgres:${POSTGRES_PASSWORD}@db:5432/fc_mvp
  depends_on:
    - db
  restart: unless-stopped
  healthcheck:
    test: ["CMD", "python", "-c", "import scripts.ai_office; print('ok')"]
    interval: 60s
    timeout: 10s
    retries: 3
```

### .env (новые переменные):

```bash
# AI Office
GROQ_API_KEY=gsk_...          # Бесплатный ключ с console.groq.com
GEMINI_API_KEY=AI...           # Бесплатный ключ с aistudio.google.com
TELEGRAM_OWNER_ID=123456789    # Твой Telegram user ID
```

---

## 10. Стоимость и лимиты

| Ресурс | Использование | Free tier | Запас |
|--------|--------------|-----------|-------|
| Groq API | ~10 req/day | 14,400 req/day | 99.9% |
| Gemini API | ~3 req/day | 1,500 req/day | 99.8% |
| Telegram Bot API | ~20 msg/day | Unlimited | ∞ |
| RSS/Scraping | ~30 req/day | Unlimited | ∞ |
| PostgreSQL | Existing DB | Existing | ∞ |
| Docker | +1 container | Existing server | ∞ |

**Итого: $0/месяц**

---

## 11. Порядок реализации

### Фаза 1 (неделя 1): Инфраструктура + Мониторщик
- Миграции (4 таблицы)
- `scripts/ai_office/` — каркас (config, db, llm, telegram_bot)
- Мониторщик: health checks → Telegram алерт
- Docker service
- **Тест**: `/status` в Telegram возвращает health check

### Фаза 2 (неделя 2): Аналитик + Контентщик (прогнозы)
- Аналитик: settled analysis → Telegram
- Контентщик (роль A): predictions → Telegram пост
- **Тест**: утренний отчёт приходит автоматически

### Фаза 3 (неделя 3): Скаут
- Gemini integration
- Скаут: web search → вердикты → scout_reports
- Интеграция с publishing: red → skip
- **Тест**: `/scout` показывает вердикты, 🔴 блокирует публикацию

### Фаза 4 (неделя 4): Новости + Админка
- Парсер (feedparser, httpx, bs4)
- Контентщик (роль B): сырые новости → статьи
- news_articles → public site
- Админка: AI Office секция
- **Тест**: новости появляются на сайте

### Фаза 5 (неделя 5): Ресёрчер + полировка
- Ресёрчер: weekly search → report
- `/ask` — свободные вопросы
- Scout accuracy stats (когда данные накопятся)
- Edge cases, error handling, fallbacks

---

## 12. Риски и mitigation

| Риск | Вероятность | Mitigation |
|------|------------|-----------|
| Groq/Gemini меняет free tier | Средняя | Fallback на другой провайдер (OpenRouter, Together AI) |
| LLM галлюцинирует в скаут-отчёте | Высокая | RED вердикт требует конкретный фактор, owner override |
| RSS-источник меняет формат | Средняя | Мониторщик алертит при 0 новостей > 24ч |
| Скаут блокирует слишком много | Средняя | Track red rate; если > 30% — пересмотреть prompt |
| Контентщик копирует текст | Низкая | Prompt явно запрещает; periodic manual review |
| DB bloat от reports | Низкая | TTL: удалять reports > 90 дней |
