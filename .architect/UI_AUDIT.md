# UI Audit Report

**Дата**: 2026-02-22
**Аудитор**: UI Agent

---

## 1. Файловая структура

### Три UI-контура (изолированные):

| Файл | Строк | Размер | Назначение |
|------|-------|--------|-----------|
| `app/ui/index.html` | 342 | 19 KB | Legacy admin UI — HTML |
| `app/ui/ui.js` | 6,496 | 320 KB | Legacy admin UI — вся логика |
| `app/ui/ui.css` | 2,751 | 53 KB | Legacy admin UI — стили |
| `app/public_site/index.html` | 234 | — | Public site — HTML |
| `app/public_site/public.js` | 492 | — | Public site — логика |
| `app/public_site/public.css` | 683 | — | Public site — стили |
| `app/admin/index.html` | 230 | — | New admin panel — HTML |
| `app/admin/admin.js` | 1,403 | — | New admin panel — логика |
| `app/admin/admin.css` | 849 | — | New admin panel — стили |
| `app/shared/tokens.css` | 56 | — | Shared CSS design tokens |
| `app/main.py` | ~4,500+ | — | Все API endpoints + static file serving |

**Итого:**
- Legacy UI: 9,589 строк (392 KB)
- New Public Site: 1,409 строк
- New Admin Panel: 2,482 строк
- Shared: 56 строк

---

## 2. Архитектура

### Framework / Подход
- **Vanilla JS** (IIFE pattern) — без фреймворков/зависимостей
- **Три изолированных SPA** на hash-based routing
- Каждый со своими CSS/JS, общие только дизайн-токены

### API Communication
- **REST API** через `fetch()`
- Admin API: заголовок `X-Admin-Token`
- Public API: без авторизации, rate-limited (60 req/min per IP)
- Pagination через `X-Total-Count` header

### Навигация
- **Legacy UI** (`/ui`): 3 секции — Dashboard, Info, System
- **Public site** (`/`): 4 секции — Главная, Матчи, Аналитика, О проекте
- **New admin** (`/admin`): 5 секций — Операции, Матчи, Публикации, Качество, Система

### Auth
- Token вводится через login form
- Хранится в `localStorage`
- Проверяется через `GET /api/v1/meta` (или `/health/debug` в legacy)
- 403 ответ → автоматический re-auth

---

## 3. Секции

### 3.1 Legacy UI — Dashboard (`#dashboard`)
- **KPI Cards**: profit, ROI, profit-factor, total-bets, period-days
- **Live Picks**: predictions (1x2 + totals) с фильтрами по рынку/лиге
- **Recent Bets**: история за 7/30/90 дней
- **Quality Report**: калибровка, сигналы, CLV
- **API**: `/dashboard`, `/freshness`, `/picks`, `/picks/totals`, `/quality_report`, `/bets/history`

### 3.2 Legacy UI — Info (`#info`)
- **Два таба**: Picks | Stats
- **Фильтры**: date range, match limit, search, only upcoming
- **Info Markets**: BTTS, O/U 1.5, O/U 2.5, O/U 3.5
- **API**: `/info/fixtures`, `/info/stats`

### 3.3 Legacy UI — System (`#system`)
- **Jobs**: статус + запуск 8 джобов
- **DB Browser**: выбор таблицы + pagination
- **Model Status**: ELO, API quota, league parameters
- **API**: `/jobs/status`, `/jobs/runs`, `/model/status`, `/db/browse`

### 3.4 New Admin — Операции (`#operations`)
- **Health Strip**: 6 индикаторов (sync, predictions, odds, standings, API quota, ELO)
- **KPI Grid**: 6 карточек + risk metrics (max win/loss, profit factor)
- **Action Rail**: 5 кнопок запуска джобов (с confirm для опасных)
- **Priority Feed**: топ-10 combined 1X2+Тоталы по EV
- **API**: `/dashboard`, `/freshness`, `/model/status`, `/picks`, `/picks/totals`

### 3.5 New Admin — Матчи (`#admin-matches`)
- **Фильтры**: лига, рынок (1x2/totals/all), статус, поиск по команде
- **Таблица**: 10 колонок (дата, матч, лига, рынок, pick, odd, EV, signal, status, profit)
- **Pagination**: с окном вокруг текущей страницы
- **API**: `/bets/history`

### 3.6 New Admin — Публикации (`#publishing`)
- **Ready List**: NS + PENDING матчи с кнопками Preview / Dry Run / Publish
- **History**: загружается per-fixture через Preview
- **Metrics**: status counts, rendering stats, alerts
- **Confirm Dialog**: для реальной публикации
- **API**: `/picks`, `/publish/metrics`, `/publish/post_preview`, `/publish/history`, `/publish`

### 3.7 New Admin — Качество (`#quality`)
- **Quality Report**: по рынкам (1X2, Тоталы) — summary KPIs, calibration bins, by-league, by-odds-bucket
- **League Breakdown**: 7-column таблица за 90 дней
- **Overall Stats**: 11 KPIs + prob_source metrics + signal bins
- **API**: `/quality_report`, `/stats/combined/leagues`, `/stats`

### 3.8 New Admin — Система (`#system`)
- **Jobs**: 9 джобов со статусом, run кнопками
- **Recent Runs**: таблица последних 15 запусков
- **Model Status**: config, ELO, API quota, league params table
- **Audit Log**: ручные запуски + ошибки
- **DB Browser**: 12 таблиц
- **API**: `/jobs/status`, `/jobs/runs`, `/model/status`, `/db/browse`

### 3.9 Public Site — Главная (`#home`)
- **Hero**: brand + tagline + CTA
- **Stats Strip**: 4 KPI (ROI, Win Rate, Ставок, Прибыль) за 90 дней
- **Upcoming Matches**: топ-8 карточек
- **Leagues**: grid с логотипами, клик → фильтр матчей
- **API**: `/api/public/v1/stats`, `/matches`, `/leagues`

### 3.10 Public Site — Матчи (`#matches`)
- **Filter**: dropdown по лигам
- **Card Grid**: responsive, status badges (NS/Live/FT), score display
- **Pagination**: up to 7 pages
- **API**: `/api/public/v1/matches`

### 3.11 Public Site — Аналитика (`#analytics`)
- **Summary**: 4 KPI cards
- **ROI Chart**: canvas-based line chart с gradient fill
- **League Breakdown**: per-league ROI/Win Rate/Profit таблица
- **Results Table**: последние 50 settled bets
- **API**: `/api/public/v1/stats`, `/results`

### 3.12 Public Site — О проекте (`#about`)
- Статический контент: методология, лиги, дисклеймер

---

## 4. API Endpoints

### 4.1 Public API (без авторизации, rate-limited)

| Method | URL | Returns | UI |
|--------|-----|---------|-----|
| GET | `/api/public/v1/leagues` | `[{id, name, country, logo_url}]` | Public Site |
| GET | `/api/public/v1/stats?days=90` | `{period_days, total_bets, wins, losses, win_rate, roi, total_profit}` | Public Site |
| GET | `/api/public/v1/matches?league_id=&limit=&offset=&days_ahead=` | `[{fixture_id, kickoff, home, away, logos, league, fixture_status, score, pick, odd, confidence, ev}]` | Public Site |
| GET | `/api/public/v1/matches/{id}` | Single match + odds detail | Public Site |
| GET | `/api/public/v1/results?league_id=&days=&limit=&offset=` | `[{fixture_id, kickoff, home, away, league, score, pick, odd, status, profit}]` | Public Site |
| GET | `/api/public/v1/standings?league_id=` | `[{team_id, team_name, rank, points, played, ...}]` | Public Site |

### 4.2 Admin API (требует X-Admin-Token)

| Method | URL | Returns | UI |
|--------|-----|---------|-----|
| GET | `/api/v1/meta` | App metadata | Auth validation |
| GET | `/api/v1/dashboard?days=` | `{kpis.{key}.{value,trend,format}, risk_metrics}` | Admin Operations |
| GET | `/api/v1/freshness` | `{sync_data.last_ok, max.*, config}` | Admin Operations |
| GET | `/api/v1/model/status` | `{elo.*, api_football.*, config, leagues[]}` | Admin Operations + System |
| GET | `/api/v1/picks?sort=&limit=` | `[{fixture_id, home, away, pick, odd, ev, signal_score, ...}]` | Admin Operations + Publishing |
| GET | `/api/v1/picks/totals?sort=&limit=` | `[{fixture_id, pick, odd, ev, ...}]` | Admin Operations |
| GET | `/api/v1/bets/history?market=&status=&limit=&offset=` | `[{fixture_id, kickoff, league, pick, odd, ev, status, profit, signal_score}]` | Admin Matches, Legacy |
| GET | `/api/v1/quality_report` | `{cached, report.{1x2,total}.{summary,calibration,by_league,by_odds_bucket}}` | Admin Quality |
| GET | `/api/v1/stats` | `{total_bets, wins, losses, roi, win_rate, avg_brier, avg_log_loss, prob_source_metrics[], bins[]}` | Admin Quality |
| GET | `/api/v1/stats/totals` | Totals-specific stats | Legacy |
| GET | `/api/v1/stats/combined/leagues` | `[{league_id, league_name, bets, wins, losses, roi, win_rate, total_profit}]` | Admin Quality |
| GET | `/api/v1/stats/combined/window` | Window-based stats | Legacy |
| GET | `/api/v1/market-stats` | `{data[market].{total_bets, wins, losses, roi, ...}}` | Legacy |
| GET | `/api/v1/jobs/status` | `{jobs.{name}.status}` | Admin System |
| GET | `/api/v1/jobs/runs?limit=` | `[{id, job_name, status, triggered_by, started_at, duration_seconds, error}]` | Admin System |
| GET | `/api/v1/db/browse?table=&limit=` | `{rows[], columns}` | Admin System |
| GET | `/api/v1/fixtures/{id}/details` | `{fixture, prediction_1x2, odds, match_indices, decisions}` | Admin Modal |
| GET | `/api/v1/publish/preview?fixture_id=` | Preview data | Admin Publishing |
| GET | `/api/v1/publish/post_preview?fixture_id=` | Post preview with HTML | Admin Publishing |
| POST | `/api/v1/publish` | Publish result | Admin Publishing |
| GET | `/api/v1/publish/history?fixture_id=` | `[{id, fixture_id, market, status, created_at, published_at}]` | Admin Publishing |
| GET | `/api/v1/publish/metrics` | `{rows_total, status_counts, render_time_ms, alert}` | Admin Publishing |
| POST | `/api/v1/run-now?job=` | `{ok, started, skipped}` | Admin System |
| GET | `/api/v1/info/fixtures` | Info market fixtures | Legacy |
| GET | `/api/v1/info/stats` | Info market stats | Legacy |
| GET | `/api/v1/coverage` | Coverage metrics | Legacy |
| GET | `/api/v1/elo` | ELO ratings | Legacy |
| GET | `/api/v1/standings` | League standings | Legacy |
| GET | `/api/v1/league_baselines` | League DC params | Legacy |
| GET | `/api/v1/snapshots/gaps` | Snapshot coverage gaps | Legacy |

### 4.3 Особые endpoints

| Вопрос | Ответ |
|--------|-------|
| Есть `/api/v1/market-stats`? | **Да** — возвращает breakdown по всем secondary markets |
| Есть per-market breakdown? | **Да** — через `market-stats` и `bets/history?market=` |
| Есть calibration data? | **Да** — в `quality_report` (bins) и `stats` (avg_brier, avg_log_loss) |
| Есть BTTS endpoint? | **Нет отдельного** — BTTS только через info markets (`/info/fixtures`) |
| Есть standings public? | **Да** — `/api/public/v1/standings` |

---

## 5. Info Markets (текущее состояние)

### INFO_MARKETS массив (Legacy UI, `ui.js:4503-4508`):
```javascript
const INFO_MARKETS = [
  { id: 'INFO_BTTS', label: 'Обе забьют (BTTS)', selections: ['BTTS_YES', 'BTTS_NO'] },
  { id: 'INFO_OU_1_5', label: 'Тотал 1.5 (O/U)', selections: ['OVER_1_5', 'UNDER_1_5'] },
  { id: 'INFO_OU_2_5', label: 'Тотал 2.5 (O/U)', selections: ['OVER_2_5', 'UNDER_2_5'] },
  { id: 'INFO_OU_3_5', label: 'Тотал 3.5 (O/U)', selections: ['OVER_3_5', 'UNDER_3_5'] },
]
```

### Как отображаются:
- **Legacy UI**: Полноценная секция Info с двумя табами (Picks / Stats)
  - Picks: карточки матчей с outcome grid (prob, implied odds, value tier)
  - Stats: статистика по info markets
- **New Admin**: **Не отображаются** — нет секции Info, только 1X2 + Totals
- **Public Site**: **Не отображаются** — только 1X2 predictions

### Что видно в Legacy:
- Probability для каждого selection
- Implied odds (из букмекерских коэффициентов)
- Value tier (strong/lean/edge/close)
- Decision block с обоснованием

---

## 6. Покрытие рынков

| Рынок | Backend (EV+decision) | Backend (info only) | Legacy UI | New Admin | Public Site |
|-------|----------------------|---------------------|-----------|-----------|-------------|
| 1X2 | **YES** `/picks` | — | **YES** Live Picks | **YES** Ops + Matches | **YES** Cards |
| Total 2.5 | **YES** `/picks/totals` | **YES** `/info/fixtures` | **YES** Live Picks | **YES** Ops + Matches | Labels only |
| Total 1.5 | No | **YES** `/info/fixtures` | **YES** Info tab | No | Labels only |
| Total 3.5 | No | **YES** `/info/fixtures` | **YES** Info tab | No | Labels only |
| BTTS | No | **YES** `/info/fixtures` | **YES** Info tab | No | Labels only |
| Double Chance | No | No | No | No | Labels only |

**Примечание**: PICK_LABELS в new admin и public site содержат лейблы для BTTS, DC, O/U 1.5/3.5, но эти рынки **не отображаются** — лейблы подготовлены для будущего использования.

---

## 7. Dashboard / KPI

### New Admin Operations:
| KPI | Источник | Период |
|-----|---------|--------|
| Прибыль | `dashboard.kpis.total_profit` | 30 дней |
| ROI | `dashboard.kpis.roi` | 30 дней |
| Win Rate | `dashboard.kpis.win_rate` | 30 дней |
| Ставок | `dashboard.kpis.total_bets` | 30 дней |
| Ср. профит | `dashboard.kpis.avg_bet` | 30 дней |
| Лиги | `dashboard.kpis.active_leagues` | 30 дней |
| Макс. выигрыш | `dashboard.risk_metrics.max_win` | 30 дней |
| Макс. проигрыш | `dashboard.risk_metrics.max_loss` | 30 дней |
| Profit Factor | `dashboard.risk_metrics.profit_factor` | 30 дней |

- Фильтрация по периоду: **Нет** (фиксировано 30 дней)
- Рынки: **1X2 + Totals combined** в priority feed, KPIs — по всем settled

### Public Site:
| KPI | Источник | Период |
|-----|---------|--------|
| ROI | `stats.roi` | 90 дней |
| Win Rate | `stats.win_rate` | 90 дней |
| Ставок | `stats.total_bets` | 90 дней |
| Прибыль | `stats.total_profit` | 90 дней |

---

## 8. Live Picks / Текущие ставки

### New Admin Operations — Priority Feed:
- **Группировка**: combined 1X2 + Тоталы, sorted by EV descending, top 10
- **Карточка**: league logo + name + market badge, team logos + names, pick badge + odd + EV% + signal%, kickoff date
- **Рынки**: 1X2 и Тоталы (market badge "1X2" / "Тотал")
- **Показывается**: pick, odd, EV%, signal score%

### New Admin Matches — Table:
- **Группировка**: по дате (kickoff_desc), с фильтрами
- **Рынки**: 1x2, totals, или all (combined)
- **Показывается**: все поля включая signal_score, feature_flags доступны через modal

### Public Site — Match Cards:
- **Группировка**: chronological, with league filter
- **Карточка**: league logo, team logos + names, score (if finished), kickoff, status badge (NS/Live/FT), pick badge, odd, EV%
- **Рынки**: только 1X2 (public API не возвращает totals)
- **Скрыто**: signal_score, feature_flags, market_diff, prob_source

---

## 9. History / История ставок

### New Admin Matches:
- **Фильтры**: лига, рынок (1x2/totals/all), статус (PENDING/WIN/LOSS), поиск по команде
- **Pagination**: да, с page window
- **Export CSV**: **Нет** (есть в legacy)
- **API**: `/bets/history` с params

### Public Site Analytics:
- **Фильтры**: **Нет** (фиксировано 90 дней, limit 200)
- **Per-league breakdown**: **Да** (client-side group by league)
- **Pagination**: **Нет** (max 50 в таблице, 200 для chart)
- **Export**: **Нет**

### Legacy UI:
- **Фильтры**: рынок, статус, team search, sort, period
- **Pagination**: да
- **Export CSV**: **Да** (через `downloadTextFile()`)
- **All-time load**: до 5000 записей

---

## 10. Stats / Аналитика

### New Admin Quality:
- **Quality Report**: per-market (1X2, Total) с calibration bins, by-league, by-odds-bucket
- **League Breakdown**: ROI/WR/Profit per league за 90 дней
- **Overall Stats**: Brier, LogLoss, prob_source metrics, signal bins
- **Calibration**: bins с avg_prob vs win_rate и deviation

### Public Site Analytics:
- **ROI Chart**: canvas, cumulative ROI line
- **League Breakdown**: client-side computed from results
- **Results Table**: last 50 settled

### Legacy UI Dashboard:
- **Quality Report**: signal indicators, CLV, per-market breakdown
- **Calibration**: through quality report bins

---

## 11. System / Jobs

### New Admin System:
- **9 джобов**: sync_data, compute_indices, build_predictions, evaluate_results, quality_report, maintenance, rebuild_elo, fit_dixon_coles, snapshot_autofill
- **Status**: OK/Error/Running badges, time ago, duration
- **Run buttons**: per-job, с confirm для dangerous (full, rebuild_elo, maintenance)
- **Recent runs**: table of 15, with trigger source and errors
- **Audit log**: manual triggers and failures highlighted
- **DB Browser**: 12 таблиц, 30 rows per page
- **Model status**: config, ELO stats, API quota, league params

---

## 12. Стилизация и UX

### Цветовая схема (shared tokens):
- **Background**: `#0b0f14` (navy), `#101826`, `#141e2b`
- **Primary accent**: `#b6f33d` (lime green) — для положительных значений, кнопок, выделений
- **Secondary accent**: `#38bdf8` (sky blue) — для ссылок, secondary actions
- **Danger**: `#f43f5e` — для ошибок, отрицательных значений
- **Success**: `#22c55e` — для wins
- **Warning**: `#ff7a1a` — для предупреждений
- **Text**: `#e6ebf2` (primary), `#b8c3d6` (secondary), `#8a97ad` (muted)

### Fonts:
- **Display**: Exo 2 (headings, KPIs, brand)
- **Body**: Onest (text, tables, labels)

### Responsive:
| Breakpoint | Public Site | New Admin | Legacy |
|-----------|-------------|-----------|--------|
| 1024px | Match grid → 2 cols | — | — |
| 900px | — | Sidebar → overlay | Team layout responsive |
| 768px | Mobile layout, 1 col | — | Full mobile layout |
| 600px | — | KPI → 2 cols, filters stack | — |
| 480px | Small phone tweaks | — | — |

### Размеры:
- Legacy: 6,496 строк JS — **монолитный**, сложно поддерживать
- New admin: 1,403 строк JS — **компактный**, хорошо структурирован
- Public: 492 строки JS — **минимальный**, только необходимое

---

## 13. Технический долг

### Дублирование:
- **PICK_LABELS** — определён в 3 файлах: `ui.js`, `admin.js`, `public.js` (с небольшими различиями в формулировках)
- **JOB_LABELS** — в `ui.js` и `admin.js` (идентичные)
- **Утилиты** `esc()`, `formatDate()`, `logoImg()` — продублированы в каждом JS файле
- **Color variables** — определены в `tokens.css`, но legacy `ui.css` имеет свою копию

### Хардкоженные значения:
- Dashboard период: 30 дней (admin), 90 дней (public) — не настраивается из UI
- Pagination limits: 20 (public matches), 50 (admin matches), 30 (DB browser)
- Auto-refresh: 30 секунд (admin), нет (public)
- Rate limit: 60 req/min per IP (бэкенд)

### Dead code / отсутствующий функционал:
- **PICK_LABELS** в admin/public содержат DC_1X/DC_X2/DC_12 и BTTS_YES/BTTS_NO, но эти рынки не отображаются нигде кроме legacy
- **New Admin не использует**: `/api/v1/info/fixtures`, `/api/v1/info/stats`, `/api/v1/market-stats`, `/api/v1/coverage`, `/api/v1/elo`, `/api/v1/standings`, `/api/v1/snapshots/gaps`
- **Public Site не использует**: standings endpoint, match detail endpoint
- Legacy UI: 6,496 строк — вероятно содержит неиспользуемый код (нужен отдельный аудит)

### Accessibility:
- **Legacy UI**: хорошая — ARIA labels, roles, live regions, focus management, keyboard nav
- **New Admin**: средняя — есть `aria-label` на key elements, keyboard shortcuts, focus-visible стили
- **Public Site**: базовая — `aria-label` на фильтрах, но нет keyboard nav между карточками

### Performance:
- **Legacy**: 320 KB JS в одном файле — тяжёлый initial load
- **New Admin**: 1,403 строк — лёгкий, с section caching (TTL)
- **Public Site**: 492 строки + canvas chart — минимальный
- **Шрифты**: Google Fonts (Exo 2 + Onest) — 2 дополнительных HTTP запроса

### Безопасность:
- Token в localStorage — стандартная практика для admin panels
- XSS protection через `esc()` во всех трёх UI
- CSP headers на public и admin пути
- Public API не утекает internal fields (проверено тестами)

---

## 14. Рекомендации для обновления

### Для поддержки новых рынков:
1. **New Admin**: добавить Info Markets секцию (или интегрировать в Quality)
   - Использовать `/api/v1/info/fixtures` + `/api/v1/info/stats`
   - Отображать BTTS, O/U 1.5/2.5/3.5 с probabilities и value
   - Сложность: **Medium** (2-3 часа)

2. **Public Site**: добавить market filter в Matches
   - Потребуется public API endpoint для totals matches
   - Сложность: **Medium** (2-3 часа)

3. **Market-stats dashboard**: использовать `/api/v1/market-stats` для per-market breakdown
   - Можно добавить в Quality или Operations
   - Сложность: **Low** (1-2 часа)

### Для мониторинг-дашборда:
1. Использовать `/api/v1/coverage` для отображения data coverage
2. Добавить API quota трекинг (уже есть в health strip)
3. Job scheduling view (cron expressions из config)
4. Сложность: **Low-Medium** (2-4 часа)

### Общие улучшения:
1. **Period selector** для Dashboard KPIs (7/30/90 дней)
2. **CSV Export** в new admin (перенести из legacy)
3. **Standings page** на public site (endpoint уже есть)
4. **WebSocket** для real-time job status (endpoint нужно создать)
5. **Legacy deprecation**: redirect `/ui` → `/admin` после стабилизации

---

## 15. Сводка

| Метрика | Legacy UI | New Admin | Public Site |
|---------|-----------|-----------|-------------|
| Строк JS | 6,496 | 1,403 | 492 |
| Секций | 3 | 5 | 4 |
| API endpoints | 18+ | 15+ | 6 |
| Info Markets | 4 (BTTS, O/U 1.5/2.5/3.5) | 0 | 0 |
| Responsive breakpoints | 5 | 2 | 3 |
| State persistence | localStorage | localStorage (token only) | In-memory |
| Auto-refresh | Нет | 30с + section caching | Нет |
| Keyboard shortcuts | Нет | Да (1-5, R, ?, Esc) | Нет |
| CSV Export | Да | Нет | Нет |
| Accessibility | Хорошая | Средняя | Базовая |
| E2E Tests | Нет | 37 тестов | 37 тестов (shared) |
