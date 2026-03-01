# Public Site — Full Technical Audit

> Документ для разработчика. Содержит всё необходимое для пересборки публичного сайта с нуля.

---

## Оглавление

1. [Архитектура и инфраструктура](#1-архитектура-и-инфраструктура)
2. [Public API — 6 эндпоинтов](#2-public-api--6-эндпоинтов)
3. [База данных — схема](#3-база-данных--схема)
4. [Frontend — HTML/CSS/JS](#4-frontend--htmlcssjs)
5. [Дизайн-токены](#5-дизайн-токены)
6. [Бизнес-логика и формулы](#6-бизнес-логика-и-формулы)

---

## 1. Архитектура и инфраструктура

### Стек

| Компонент | Технология |
|-----------|-----------|
| Backend | Python 3.12, FastAPI 0.115, Uvicorn |
| Database | PostgreSQL 16, SQLAlchemy 2.0 + asyncpg |
| Frontend | Vanilla JS (IIFE), CSS Custom Properties, Canvas API |
| Deploy | Docker Compose (3 сервиса: `db`, `app`, `scheduler`) |
| Fonts | Google Fonts: Exo 2 (400,600,700,800), Onest (400,500,600,700) |

### Docker-сервисы

- **`app`** — API-сервер на порту `8000`, `SCHEDULER_ENABLED=false`
- **`scheduler`** — фоновые задачи (cron), без порта
- **`db`** — PostgreSQL 16, volume `pgdata`, БД `fc_mvp`

### Раздача статики

Нет `StaticFiles` mount. Каждый файл обслуживается отдельным `FileResponse` route:

| URL | Файл | Cache-Control |
|-----|------|---------------|
| `GET /` | `app/public_site/index.html` | `no-store` |
| `GET /public.css` | `app/public_site/public.css` | `no-store` |
| `GET /public.js` | `app/public_site/public.js` | `no-store` |
| `GET /shared/tokens.css` | `app/shared/tokens.css` | `no-store` |

### CORS

```
allow_origins=["*"]
allow_methods=["GET"]
allow_headers=["*"]
expose_headers=["X-Total-Count"]
```

### Content Security Policy (для `/`, `/public*`, `/shared*`)

```
default-src 'self';
script-src 'self';
style-src 'self' 'unsafe-inline' https://fonts.googleapis.com;
font-src 'self' https://fonts.gstatic.com;
img-src 'self' https://media.api-sports.io data:;
connect-src 'self';
frame-ancestors 'none'
```

Также: `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`.

### Rate Limiting

- **Метод**: In-memory per-IP sliding window
- **Лимит**: 60 запросов / 60 секунд на IP
- **Ответ при превышении**: HTTP 429, `Retry-After: 60`
- **Применяется ко**: всем 6 `/api/public/v1/*` эндпоинтам
- **Не применяется к**: HTML/CSS/JS статике

### Аутентификация

Публичный сайт — **без аутентификации**. Никаких токенов, cookies, сессий.

### STATS_EPOCH

```python
STATS_EPOCH = datetime(2026, 2, 23, tzinfo=timezone.utc)
```

Фиксированная дата начала production baseline. Все статистические эндпоинты (`/stats`, `/results`) используют `max(cutoff, STATS_EPOCH)` — данные ДО этой даты не показываются.

### Ключевые настройки (из `.env`)

| Переменная | По умолчанию | Влияние на публичный сайт |
|-----------|-------------|--------------------------|
| `LEAGUE_IDS` | `39,78,140,135` | Какие лиги отображаются |
| `SEASON` | Авто (2025 для фев 2026) | Сезон для турнирных таблиц |
| `BOOKMAKER_ID` | `1` | Чьи коэффициенты показывать |
| `STALE_NS_HIDE_HOURS` | `6` | Скрывать NS матчи старше N часов |
| `DATABASE_URL` | `postgresql+asyncpg://...` | Строка подключения к БД |

---

## 2. Public API — 6 эндпоинтов

### Сводная таблица

| # | Endpoint | Params | Cache | Pagination | STATS_EPOCH |
|---|----------|--------|-------|------------|-------------|
| 1 | `GET /api/public/v1/leagues` | нет | нет | нет | нет |
| 2 | `GET /api/public/v1/stats` | `days` | нет | нет | **да** |
| 3 | `GET /api/public/v1/matches` | `league_id`, `days_ahead`, `limit`, `offset` | `max-age=120` | `X-Total-Count` | нет |
| 4 | `GET /api/public/v1/matches/{fixture_id}` | нет | нет | нет | нет |
| 5 | `GET /api/public/v1/results` | `league_id`, `days`, `limit`, `offset` | `max-age=120` | `X-Total-Count` | **да** |
| 6 | `GET /api/public/v1/standings` | `league_id` (обяз.), `season`, `limit`, `offset` | `max-age=300` | `X-Total-Count` | нет |

---

### 2.1 GET `/api/public/v1/leagues`

**Параметры**: нет

**SQL** (основной):
```sql
SELECT DISTINCT l.id, l.name, l.country, l.logo_url
FROM leagues l
WHERE l.id IN ({league_ids из настроек})
ORDER BY l.name
```

**Fallback** (если таблица `leagues` пуста): запрос к `fixtures JOIN leagues`.

**Ответ**:
```json
[
  {
    "id": 39,
    "name": "Premier League",
    "country": "England",
    "logo_url": "https://media.api-sports.io/football/leagues/39.png"
  }
]
```

---

### 2.2 GET `/api/public/v1/stats`

**Параметры**:

| Имя | Тип | Default | Constraints |
|-----|-----|---------|-------------|
| `days` | int | 90 | 1..365 |

**Логика**: `cutoff = max(utcnow() - timedelta(days), STATS_EPOCH)`

**SQL**:
```sql
WITH combined AS (
  SELECT p.status, p.profit FROM predictions p
  WHERE p.selection_code != 'SKIP' AND p.status IN ('WIN','LOSS')
    AND p.settled_at >= :cutoff
  UNION ALL
  SELECT COALESCE(pt.status,'PENDING'), pt.profit FROM predictions_totals pt
  WHERE COALESCE(pt.status,'PENDING') IN ('WIN','LOSS')
    AND pt.settled_at >= :cutoff
)
SELECT COUNT(*) AS total_bets,
       COUNT(*) FILTER (WHERE status='WIN') AS wins,
       COUNT(*) FILTER (WHERE status='LOSS') AS losses,
       COALESCE(SUM(profit),0) AS total_profit
FROM combined
```

**Ответ**:
```json
{
  "period_days": 90,
  "total_bets": 42,
  "wins": 25,
  "losses": 17,
  "win_rate": 59.5,
  "roi": 8.3,
  "total_profit": 3.49
}
```

**Формулы**:
- `win_rate = (wins / settled) * 100` (1 decimal)
- `roi = (total_profit / settled) * 100` (1 decimal)
- `total_profit` = SUM(profit), 2 decimals

---

### 2.3 GET `/api/public/v1/matches`

**Параметры**:

| Имя | Тип | Default | Constraints |
|-----|-----|---------|-------------|
| `league_id` | int? | null | опционально |
| `days_ahead` | int | 7 | 1..30 |
| `limit` | int | 20 | 1..100 |
| `offset` | int | 0 | >= 0 |

**Логика**:
- Окно: `utcnow() - 8 часов` до `utcnow() + days_ahead`
- Исключаются завершённые: `FT, AET, PEN, CANC, ABD, AWD, WO`
- Скрываются stale NS: kickoff > `stale_ns_hide_hours` назад
- Только PENDING прогнозы: `COALESCE(status, 'PENDING') = 'PENDING'`
- Исключаются SKIP для 1X2
- UNION ALL: `predictions` (1X2) + `predictions_totals` (доп. рынки)
- **Один fixture может быть несколько раз** (по одной записи на каждый рынок)

**Ответ** (массив):
```json
[
  {
    "fixture_id": 1234567,
    "kickoff": "2026-02-25T15:00:00+00:00",
    "home": "Arsenal",
    "away": "Chelsea",
    "home_logo_url": "https://media.api-sports.io/football/teams/42.png",
    "away_logo_url": "https://media.api-sports.io/football/teams/49.png",
    "league_id": 39,
    "league": "Premier League",
    "league_logo_url": "https://media.api-sports.io/football/leagues/39.png",
    "fixture_status": "NS",
    "score": null,
    "market": "1X2",
    "pick": "HOME_WIN",
    "odd": 1.85,
    "confidence": 0.5832,
    "ev": 0.0789
  }
]
```

**EV** считается в Python: `Decimal(confidence) * Decimal(initial_odd) - 1`, округляется до 4 знаков.

**Headers**: `X-Total-Count`, `Cache-Control: public, max-age=120`

---

### 2.4 GET `/api/public/v1/matches/{fixture_id}`

**Параметры**: `fixture_id` (path, int)

**SQL (fixture)**:
```sql
SELECT f.id, f.kickoff, f.status, f.home_goals, f.away_goals,
       th.name AS home, ta.name AS away,
       th.logo_url AS home_logo_url, ta.logo_url AS away_logo_url,
       f.league_id, l.name AS league, l.logo_url AS league_logo_url,
       o.home_win AS odds_home, o.draw AS odds_draw, o.away_win AS odds_away,
       o.over_2_5 AS odds_over, o.under_2_5 AS odds_under
FROM fixtures f
JOIN teams th ON th.id = f.home_team_id
JOIN teams ta ON ta.id = f.away_team_id
LEFT JOIN leagues l ON l.id = f.league_id
LEFT JOIN odds o ON o.fixture_id = f.id AND o.bookmaker_id = :bid
WHERE f.id = :fid
```

**SQL (predictions)**: UNION ALL of `predictions` + `predictions_totals` для fixture_id.

**Ответ**:
```json
{
  "fixture_id": 1234567,
  "kickoff": "2026-02-25T15:00:00+00:00",
  "status": "NS",
  "home": "Arsenal",
  "away": "Chelsea",
  "home_logo_url": "...",
  "away_logo_url": "...",
  "league_id": 39,
  "league": "Premier League",
  "league_logo_url": "...",
  "score": null,
  "prediction": {
    "market": "1X2",
    "pick": "HOME_WIN",
    "odd": 1.85,
    "confidence": 0.5832,
    "ev": 0.0789,
    "status": "PENDING",
    "profit": null
  },
  "predictions": [
    { "market": "1X2", "pick": "HOME_WIN", "odd": 1.85, "confidence": 0.5832, "ev": 0.0789, "status": "PENDING", "profit": null },
    { "market": "TOTAL", "pick": "OVER_2_5", "odd": 1.90, "confidence": 0.56, "ev": 0.064, "status": "PENDING", "profit": null }
  ],
  "odds": {
    "home_win": 1.85,
    "draw": 3.50,
    "away_win": 4.20,
    "over_2_5": 1.90,
    "under_2_5": 1.95
  }
}
```

- `prediction` (singular) = первый элемент или `null`
- `predictions` (plural) = полный массив или `[]`
- `odds` = `null` если `odds_home` is null
- HTTP 404 если fixture не найден

---

### 2.5 GET `/api/public/v1/results`

**Параметры**:

| Имя | Тип | Default | Constraints |
|-----|-----|---------|-------------|
| `league_id` | int? | null | опционально |
| `days` | int | 30 | 1..365 |
| `limit` | int | 50 | 1..200 |
| `offset` | int | 0 | >= 0 |

**Логика**: `cutoff = max(utcnow() - timedelta(days), STATS_EPOCH)`. Только WIN/LOSS.

**Ответ** (массив):
```json
[
  {
    "fixture_id": 1234567,
    "kickoff": "2026-02-24T15:00:00+00:00",
    "home": "Arsenal",
    "away": "Chelsea",
    "home_logo_url": "...",
    "away_logo_url": "...",
    "league_id": 39,
    "league": "Premier League",
    "league_logo_url": "...",
    "score": "2-1",
    "market": "1X2",
    "pick": "HOME_WIN",
    "odd": 1.85,
    "ev": 0.0789,
    "status": "WIN",
    "profit": 0.85
  }
]
```

**Отличие от `/matches`**: нет поля `confidence`, есть `status` (WIN/LOSS) и `profit`.

**Headers**: `X-Total-Count`, `Cache-Control: public, max-age=120`

---

### 2.6 GET `/api/public/v1/standings`

**Параметры**:

| Имя | Тип | Default | Constraints |
|-----|-----|---------|-------------|
| `league_id` | int | **обязательный** | — |
| `season` | int? | auto (settings.season) | — |
| `limit` | int | 50 | 1..100 |
| `offset` | int | 0 | >= 0 |

**SQL**:
```sql
SELECT ts.team_id, t.name AS team_name, t.logo_url AS team_logo_url,
       ts.rank, ts.points, ts.played, ts.goals_for, ts.goals_against,
       ts.goal_diff, ts.form
FROM team_standings ts
JOIN teams t ON t.id = ts.team_id
WHERE ts.season = :s AND ts.league_id = :lid
ORDER BY ts.rank ASC NULLS LAST, ts.points DESC NULLS LAST
LIMIT :limit OFFSET :offset
```

**Ответ** (массив):
```json
[
  {
    "team_id": 42,
    "team_name": "Arsenal",
    "team_logo_url": "...",
    "rank": 1,
    "points": 65,
    "played": 27,
    "goals_for": 58,
    "goals_against": 22,
    "goal_diff": 36,
    "form": "WWDWW"
  }
]
```

**Headers**: `X-Total-Count`, `Cache-Control: public, max-age=300`

---

## 3. База данных — схема

### Таблицы, используемые публичным API

#### `fixtures`

| Колонка | Тип | Nullable | Описание |
|---------|-----|----------|----------|
| `id` | BIGINT | NOT NULL | **PK**. API Football fixture ID |
| `league_id` | INTEGER | YES | ID лиги |
| `season` | INTEGER | YES | Сезон |
| `kickoff` | TIMESTAMPTZ | NOT NULL | Время начала матча |
| `home_team_id` | BIGINT | YES | FK → teams.id |
| `away_team_id` | BIGINT | YES | FK → teams.id |
| `status` | VARCHAR(20) | YES | Статус матча (см. ниже) |
| `home_goals` | INTEGER | YES | Голы хозяев |
| `away_goals` | INTEGER | YES | Голы гостей |
| `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` |

**Индексы**: `(league_id, season)`, `(home_team_id, kickoff)`, `(away_team_id, kickoff)`, `(kickoff)`, `(league_id, kickoff)`.

**Статусы fixture**:
- Finished: `FT`, `AET`, `PEN`
- Not Started: `NS`
- Cancelled: `CANC`, `ABD`, `AWD`, `WO`
- Postponed: `PST`
- Live: `1H`, `HT`, `2H`, `ET`, `BT`, `P`, `LIVE`, `INT`
- Unknown: `UNK`, `TBD`

#### `predictions` (рынок 1X2)

| Колонка | Тип | Nullable | Описание |
|---------|-----|----------|----------|
| `id` | SERIAL | NOT NULL | **PK** |
| `fixture_id` | BIGINT | YES | **UNIQUE**. FK → fixtures.id |
| `selection_code` | VARCHAR(20) | NOT NULL | `HOME_WIN`, `DRAW`, `AWAY_WIN`, `SKIP` |
| `confidence` | NUMERIC(6,4) | YES | Вероятность модели |
| `initial_odd` | NUMERIC(8,3) | YES | Коэффициент букмекера |
| `status` | VARCHAR(20) | NOT NULL | `PENDING`, `WIN`, `LOSS`, `VOID` |
| `profit` | NUMERIC(10,3) | YES | P&L: WIN → `odd - 1`, LOSS → `-1` |
| `settled_at` | TIMESTAMPTZ | YES | Когда рассчитана ставка |
| `signal_score` | NUMERIC(6,3) | YES | Сила сигнала |
| `feature_flags` | JSONB | YES | Метаданные модели |
| `created_at` | TIMESTAMPTZ | NOT NULL | `now()` |

#### `predictions_totals` (доп. рынки)

| Колонка | Тип | Nullable | Описание |
|---------|-----|----------|----------|
| `fixture_id` | BIGINT | NOT NULL | **PK part 1**. FK → fixtures.id |
| `market` | VARCHAR(20) | NOT NULL | **PK part 2**. Тип рынка |
| `selection` | VARCHAR(20) | YES | Выбор (НЕ `selection_code`!) |
| `confidence` | NUMERIC(6,4) | YES | Вероятность |
| `initial_odd` | NUMERIC(8,3) | YES | Коэффициент |
| `status` | VARCHAR(20) | YES | `PENDING`/`WIN`/`LOSS` |
| `profit` | NUMERIC(10,3) | YES | P&L |
| `settled_at` | TIMESTAMPTZ | YES | Дата расчёта |
| `created_at` | TIMESTAMPTZ | YES | `now()` |

**Рынки** (market):
- `TOTAL` — Over/Under 2.5
- `TOTAL_1_5` — Over/Under 1.5
- `TOTAL_3_5` — Over/Under 3.5
- `BTTS` — Обе забьют
- `DOUBLE_CHANCE` — Двойной шанс

**Selections по рынкам**:
- TOTAL: `OVER_2_5`, `UNDER_2_5`
- TOTAL_1_5: `OVER_1_5`, `UNDER_1_5`
- TOTAL_3_5: `OVER_3_5`, `UNDER_3_5`
- BTTS: `BTTS_YES`, `BTTS_NO`
- DOUBLE_CHANCE: `DC_1X`, `DC_X2`, `DC_12`

#### `teams`

| Колонка | Тип | Nullable | Описание |
|---------|-----|----------|----------|
| `id` | BIGINT | NOT NULL | **PK** |
| `name` | VARCHAR(100) | YES | Название команды |
| `logo_url` | VARCHAR(255) | YES | URL лого |

#### `leagues`

| Колонка | Тип | Nullable | Описание |
|---------|-----|----------|----------|
| `id` | INTEGER | NOT NULL | **PK** |
| `name` | VARCHAR(100) | YES | Название лиги |
| `country` | VARCHAR(50) | YES | Страна |
| `logo_url` | VARCHAR(255) | YES | URL лого |

#### `odds` (текущие коэффициенты)

| Колонка | Тип | Nullable | Описание |
|---------|-----|----------|----------|
| `fixture_id` | BIGINT | NOT NULL | **PK part 1** |
| `bookmaker_id` | INTEGER | NOT NULL | **PK part 2** |
| `home_win` | NUMERIC(8,3) | YES | 1X2: Победа дома |
| `draw` | NUMERIC(8,3) | YES | 1X2: Ничья |
| `away_win` | NUMERIC(8,3) | YES | 1X2: Победа гостей |
| `over_2_5` | NUMERIC(8,3) | YES | Тотал больше 2.5 |
| `under_2_5` | NUMERIC(8,3) | YES | Тотал меньше 2.5 |

#### `team_standings`

| Колонка | Тип | Nullable | Описание |
|---------|-----|----------|----------|
| `team_id` | BIGINT | NOT NULL | **PK part 1** |
| `league_id` | INTEGER | NOT NULL | **PK part 2** |
| `season` | INTEGER | NOT NULL | **PK part 3** |
| `rank` | INTEGER | YES | Позиция |
| `points` | INTEGER | YES | Очки |
| `played` | INTEGER | YES | Игры |
| `goals_for` | INTEGER | YES | Забито |
| `goals_against` | INTEGER | YES | Пропущено |
| `goal_diff` | INTEGER | YES | Разница мячей |
| `form` | TEXT | YES | Строка формы: `"WWDLW"` |

### Связи между таблицами

```
fixtures.home_team_id → teams.id
fixtures.away_team_id → teams.id
fixtures.league_id    → leagues.id
predictions.fixture_id → fixtures.id (UNIQUE)
predictions_totals.fixture_id → fixtures.id
odds.fixture_id → fixtures.id
team_standings.(team_id) → teams.id
```

---

## 4. Frontend — HTML/CSS/JS

### 4.1 HTML-структура (`index.html`)

#### Meta и SEO
- Язык: русский (`og:locale=ru_RU`)
- Title: "Football Value Betting -- Prognozi na futbol"
- JSON-LD: `@type: WebSite`
- Open Graph + Twitter Card
- Favicon: inline SVG (футбольный мяч)

#### Внешние зависимости
- Google Fonts: Exo 2, Onest
- **Никаких JS-библиотек** — всё на чистом JS

#### Секции (6 штук)

| ID | Название | Описание |
|----|----------|----------|
| `#home` | Главная | Hero, стата (ROI/WR/bets/profit), upcoming, results, leagues |
| `#matches` | Матчи | Фильтр по лигам, карточки матчей, пагинация |
| `#standings` | Таблицы | Выбор лиги, турнирная таблица |
| `#analytics` | Аналитика | Период (7/30/90/365), ROI/profit графики (Canvas), breakdown по лигам, таблица результатов с сортировкой, CSV export |
| `#league` | Деталь лиги | Таблица + матчи + результаты конкретной лиги |
| `#about` | О проекте | Описание, методология, лиги, дисклеймер |

#### UI-компоненты

- **Header**: fixed, blur backdrop, лого FVB, навигация, mobile hamburger
- **Bottom Navigation**: 5 табов с SVG иконками (mobile-only)
- **Match Modal**: overlay dialog, trap focus, share URL, spinner loading
- **Toast notifications**: slide-in/slide-out, auto-remove 3.1s
- **Scroll-to-top button**: появляется после 600px скролла

#### Все ID элементов

```
conn-dot, pub-nav, mobile-toggle, pub-main,
home, stat-roi, stat-winrate, stat-bets, stat-profit,
home-upcoming, home-results, home-leagues,
matches, filter-league, matches-list, matches-pagination,
standings, standings-league, standings-table-wrap,
analytics, pub-period-bar, an-roi, an-roi-label, an-winrate, an-bets, an-profit,
roi-chart, profit-chart, league-breakdown,
export-results-csv, results-table, results-tbody,
league, league-back, league-header, league-standings-wrap, league-matches, league-results,
about, about-stats, about-leagues, about-bets, about-roi, about-winrate,
pub-match-modal, pub-modal-title, pub-modal-share-btn, pub-modal-close-btn, pub-modal-body,
pub-bottom-nav, scroll-top-btn, pub-toasts
```

---

### 4.2 CSS (`public.css`)

#### Архитектура
Чистый CSS, тёмная тема, CSS custom properties из `tokens.css`. Без фреймворков.

#### Фон страницы
Тройной градиент на `--bg-0`:
1. Зелёное свечение (top-right): `rgba(182, 243, 61, 0.08)`
2. Голубое свечение (top-left): `rgba(56, 189, 248, 0.08)`
3. Вертикальный gradient

#### Анимации (8 keyframes)

| Имя | Описание | Длительность |
|-----|----------|-------------|
| `pub-loading-bar` | Полоса загрузки | 1.8s infinite |
| `pub-shimmer` | Skeleton shimmer | 1.5s infinite |
| `pubToastIn` | Toast slide-in | 0.3s |
| `pubToastOut` | Toast slide-out | 0.3s (delay 2.7s) |
| `pubFadeIn` | Fade+slide-up секции | 0.3s |
| `pubCardIn` | Staggered карточки | 0.35s |
| `pub-pulse` | Live badge pulse | 1.5s infinite |
| `pub-spin` | Modal spinner | 0.7s infinite |

Staggered delays: карточки матчей 0-0.24s (шаг 0.04s), лиги 0-0.15s (шаг 0.03s).

#### Layout

| Компонент | Layout |
|-----------|--------|
| Container | `max-width: 1200px`, centered |
| Match grid | `repeat(auto-fill, minmax(340px, 1fr))` |
| League grid | `repeat(auto-fill, minmax(200px, 1fr))` |
| Stats strip | `repeat(4, 1fr)` |
| Header | Flexbox, height 64px, fixed |

#### Responsive breakpoints

| Breakpoint | Изменения |
|-----------|-----------|
| `≤1024px` | Match grid: 2 колонки |
| `≤900px` | Stats strip: 2 колонки, league grid: 2 колонки |
| `≤768px` | Mobile nav, 1 колонка матчей, bottom nav visible, footer +80px padding |
| `≤480px` | Compact stats, full-width toast/modal |

Также: `@media print` (скрытие UI, белый фон), `prefers-reduced-motion` (отключение анимаций).

#### Ключевые паттерны

- **Header**: `backdrop-filter: blur(20px)`, z-index 100
- **Match cards**: hover border, active `scale(0.98)`, result badge 45deg rotation
- **Modal**: overlay `backdrop-filter: blur(4px)`, z-index 200, max-width 600px
- **Tables**: sortable headers с unicode arrows, scroll fade gradient
- **Connection dot**: 6x6px, зелёный/красный glow
- **Scrollbar**: тонкий, тёмный

---

### 4.3 JavaScript (`public.js`)

#### Архитектура
IIFE, strict mode, vanilla JS. Все состояние — module-scoped переменные.

#### Константы

```javascript
const API = '/api/public/v1';

const PICK_LABELS = {
  HOME_WIN: 'Дома', DRAW: 'Ничья', AWAY_WIN: 'Гости',
  OVER_2_5: 'Больше 2.5', UNDER_2_5: 'Меньше 2.5',
  OVER_1_5: 'Больше 1.5', UNDER_1_5: 'Меньше 1.5',
  OVER_3_5: 'Больше 3.5', UNDER_3_5: 'Меньше 3.5',
  BTTS_YES: 'Обе забьют — Да', BTTS_NO: 'Обе забьют — Нет',
  DC_1X: 'Двойной шанс 1X', DC_X2: 'Двойной шанс X2', DC_12: 'Двойной шанс 12'
};

const MARKET_SHORT = {
  '1X2': '1X2', 'TOTAL': 'T2.5', 'TOTAL_1_5': 'T1.5',
  'TOTAL_3_5': 'T3.5', 'BTTS': 'ОЗ', 'DOUBLE_CHANCE': 'ДШ'
};
```

#### Состояние (state)

| Переменная | Тип | Сохраняется | localStorage key |
|-----------|-----|------------|-----------------|
| `matchesState.league` | string | Да | `fvb_league` |
| `pubDays` | number (90) | Да | `fvb_days` |
| `_favoriteLeagues` | array | Да | `fvb_fav_leagues` |
| `_resultsSort` | {col, dir} | Да | `fvb_resultsSort` |
| `leaguesCache` | array/null | Нет | — |
| `_resultsCache` | array/null | Нет | — |
| `currentLeagueId` | string/null | Нет | — |
| `_navController` | AbortController | Нет | — |

#### API Layer

```javascript
async function api(path, params = {}, opts = {}) {
  // GET fetch to `API + path` with query params
  // Returns { data, total } where total is from X-Total-Count header
}
```

#### Все API вызовы из JS

| Где вызывается | Endpoint | Параметры |
|---------------|----------|-----------|
| `loadHome()` | `/stats` | `{days: 90}` |
| `loadHome()` | `/matches` | `{limit: 8}` |
| `loadHome()` | `/results` | `{days: 30, limit: 6}` |
| `loadHome()` | `/leagues` | — |
| `fetchMatches()` | `/matches` | `{limit: 20, offset, days_ahead: 14, league_id?}` |
| `loadAnalytics()` | `/stats` | `{days: pubDays}` |
| `loadAnalytics()` | `/results` | `{days: pubDays, limit: 200}` |
| `fetchStandings(id)` | `/standings` | `{league_id}` |
| `openMatchModal(id)` | `/matches/{id}` | — |
| `loadLeagueDetail()` | `/standings` | `{league_id}` |
| `loadLeagueDetail()` | `/matches` | `{league_id, limit: 10}` |
| `loadLeagueDetail()` | `/results` | `{league_id, days: 90, limit: 30}` |
| `loadAbout()` | `/stats` | `{days: 90}` |
| `loadAbout()` | `/leagues` | — |
| Connection ping | `HEAD /leagues` | каждые 45s |

#### Роутер (SPA)

Секции: `['home', 'matches', 'standings', 'analytics', 'league', 'about']`

```javascript
function navigate(hash) {
  // Abort in-flight requests
  // Toggle active class on sections/nav/bottom tabs
  // Call corresponding loader: loadHome, loadMatches, loadStandings, loadAnalytics, loadLeagueDetail, loadAbout
}
```

Навигация по `window.hashchange`. Deep link: `?fixture=123` открывает модалку.

#### Графики (Canvas API)

Два графика — ROI и Profit, рисуются вручную на Canvas:
- DPR scaling для ретины
- 5 горизонтальных grid lines
- Нулевая линия (dashed)
- Градиентная заливка под линией
- Точка на конце
- Tooltip при наведении (threshold 20px)
- Кнопка скачивания PNG
- Padding: top 30, bottom 30, left 50, right 20

**Данные для ROI chart**: results sorted by kickoff ASC, cumulative `ROI = (cumProfit / (i + 1)) * 100`

**Данные для Profit chart**: results sorted by kickoff ASC, cumulative sum of profit. Зелёная линия если profit >= 0, красная если < 0.

#### Event Listeners

Единый delegated click handler на `document` обрабатывает:
- Sortable headers (`th[data-sort]`)
- Retry buttons (`[data-retry]`)
- CSV export (`#export-results-csv`)
- Chart download (`[data-download-chart]`)
- Period buttons (`[data-pub-days]`)
- Mobile toggle (`#mobile-toggle`)
- Favorite league (`[data-fav-league]`)
- League cards (`.pub-league-card`)
- Match cards (`.pub-match-card[data-fixture-id]`)
- Modal share/close
- Pagination buttons

Keyboard: Escape закрывает модалку, Tab — focus trap.

#### Утилиты

| Функция | Назначение |
|---------|-----------|
| `esc(s)` | HTML entity escape |
| `el(id)` | `document.getElementById` |
| `_guardBtn(btn, ms=2000)` | Защита от двойного клика |
| `formatDate(iso)` | "DD мес HH:MM" (русский) |
| `logoImg(url, alt, size)` | `<img>` с lazy loading и fallback placeholder |
| `toast(msg, type)` | Тост-уведомление (3.1s) |
| `animateValue(el, end, opts)` | Анимация счётчика (0→end, 600ms, cubic ease-out) |
| `errorHtml(msg, retryAction)` | HTML ошибки с кнопкой retry |
| `emptyHtml(icon, text, sub)` | HTML пустого состояния |
| `skeletonCards(n)` | Skeleton loading карточки |
| `exportResultsCsv()` | Экспорт в CSV с BOM для Excel |

#### Hardcoded значения

| Значение | Описание |
|---------|----------|
| 20 | Матчей на странице |
| 8 | Upcoming на главной |
| 6 | Результатов на главной |
| 14 | days_ahead для матчей |
| 200 | Лимит результатов для аналитики |
| 50 | Видимых строк в таблице |
| 600px | Порог появления scroll-top |
| 45000ms | Интервал connection ping |
| 250ms | Debounce смены периода |
| 0.10 | EV >= 10% → класс "strong" |

---

## 5. Дизайн-токены (`shared/tokens.css`)

### Цвета

| Токен | Значение | Назначение |
|-------|---------|-----------|
| `--bg-0` | `#0b0f14` | Базовый фон |
| `--bg-1` | `#101826` | Фон +1 |
| `--bg-2` | `#141e2b` | Фон +2 |
| `--surface-1` | `rgba(14, 22, 34, 0.92)` | Карточки |
| `--surface-2` | `rgba(18, 28, 42, 0.96)` | Elevated |
| `--surface-3` | `rgba(20, 33, 49, 0.98)` | Max elevation |
| `--text-primary` | `#e6ebf2` | Основной текст |
| `--text-secondary` | `#b8c3d6` | Вторичный текст |
| `--text-muted` | `#8a97ad` | Приглушённый |
| `--accent-primary` | `#b6f33d` | Lime green — бренд, позитив |
| `--accent-secondary` | `#38bdf8` | Sky blue — ссылки |
| `--accent-warning` | `#ff7a1a` | Orange — предупреждения |
| `--accent-danger` | `#f43f5e` | Rose — ошибки, проигрыши |
| `--accent-success` | `#22c55e` | Green — выигрыши |

### Границы

| Токен | Значение |
|-------|---------|
| `--border-color` | `#1f2b3c` |
| `--border-soft` | `#263548` |
| `--border-glow` | `rgba(182, 243, 61, 0.35)` |

### Скругления

| Токен | Значение |
|-------|---------|
| `--radius-sm` | `8px` |
| `--radius-md` | `12px` |
| `--radius-lg` | `18px` |

### Отступы

| Токен | Значение |
|-------|---------|
| `--spacing-xs` | `6px` |
| `--spacing-sm` | `10px` |
| `--spacing-md` | `14px` |
| `--spacing-lg` | `20px` |
| `--spacing-xl` | `28px` |

### Типографика

| Токен | Значение |
|-------|---------|
| `--font-size-xs` | `12px` |
| `--font-size-sm` | `13px` |
| `--font-size-md` | `14px` |
| `--font-size-lg` | `16px` |
| `--font-size-xl` | `20px` |
| `--font-size-2xl` | `28px` |
| `--font-size-3xl` | `36px` |
| `--font-display` | `"Exo 2", "Onest", sans-serif` |
| `--font-body` | `"Onest", "Exo 2", sans-serif` |

### Тени

| Токен | Значение |
|-------|---------|
| `--shadow-soft` | `0 10px 25px rgba(6, 10, 18, 0.45)` |
| `--shadow-card` | `0 16px 36px rgba(2, 6, 12, 0.5)` |
| `--glow-primary` | `0 0 25px rgba(182, 243, 61, 0.2)` |

---

## 6. Бизнес-логика и формулы

### Расчёт ставок

| Метрика | Формула |
|---------|---------|
| **EV (Expected Value)** | `confidence * initial_odd - 1` |
| **Profit (WIN)** | `odd - 1` (на единицу ставки) |
| **Profit (LOSS)** | `-1` |
| **ROI** | `(total_profit / total_bets) * 100` |
| **Win Rate** | `(wins / total_bets) * 100` |

### Отображение EV на фронте

- `ev * 100` → процент
- `ev >= 0.10` → класс `strong` (зелёный, жирный)
- `ev >= 0.0` → класс `positive`
- Иначе → обычный

### Отображение profit

- `profit > 0.05` → зелёный с `+` префиксом
- `profit < -0.05` → красный
- Иначе → нейтральный

### Графики

**ROI chart**: Результаты сортируются по kickoff ASC. Для каждой точки `i`: `ROI_i = (cumProfit_i / (i + 1)) * 100`.

**Profit chart**: Кумулятивная сумма profit. Цвет линии: зелёный если итог >= 0, красный если < 0.

### Score display

- Если `home_goals` и `away_goals` есть → `"{home_goals}-{away_goals}"`
- Иначе → `null` (на фронте отображается как "VS")

### Form display (турнирная таблица)

Строка из символов `W`/`D`/`L` → цветные точки:
- `W` → зелёная
- `D` → жёлтая/нейтральная
- `L` → красная
