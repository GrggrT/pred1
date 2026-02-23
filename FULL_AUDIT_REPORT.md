# FULL AUDIT REPORT — pred1 (Football Value Betting Engine)

**Дата аудита**: 2026-02-21
**Аудитор**: Claude Code (полный read-only аудит)

---

## РАЗДЕЛ 1: Архитектура и структура кода

### 1.1 Карта модулей

#### Дерево проекта (без `.venv`, `__pycache__`, `.git`, `test_*.html`)

```
pred1/
├── .claude/
│   ├── agents/football-predict-optimizer.md
│   └── settings.local.json
├── .dockerignore
├── .env / .env.example
├── .gitignore
├── .python-version
├── AGENTS.md
├── CLAUDE.md
├── CONTEXT.md
├── DEFINITION_OF_DONE.md
├── Dockerfile
├── PROJECT.md
├── README.md
├── REGRESSION_CHECKLIST.md
├── REPORT.md
├── alembic.ini
├── alembic/
│   ├── env.py
│   └── versions/
│       ├── 0001_init.py ... 0029_match_indices_rolling_xg.py (29 миграций)
├── analysis_dixon_coles_stacking_calibration.md
├── app/
│   ├── __init__.py
│   ├── assets/fonts/ (DejaVuSans, NotoEmoji, NotoSans)
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py       → Pydantic Settings, все переменные окружения
│   │   ├── db.py            → AsyncPG + SQLAlchemy session factory
│   │   ├── decimalutils.py  → Decimal helpers (D, q_money, q_prob, q_ev, safe_div)
│   │   ├── http.py          → httpx client с retries для внешних API
│   │   ├── logger.py        → Logging setup
│   │   └── timeutils.py     → UTC time helpers
│   ├── data/
│   │   ├── __init__.py
│   │   ├── mappers.py       → Status normalization (API→internal)
│   │   └── providers/
│   │       ├── __init__.py
│   │       ├── api_football.py  → API-Football client (fixtures, odds, injuries, standings, stats)
│   │       ├── cache.py         → API response cache layer
│   │       ├── deepl.py         → DeepL translation client
│   │       ├── openweather.py   → OpenWeather client (unused in model)
│   │       └── telegram.py      → Telegram bot publishing
│   ├── jobs/
│   │   ├── __init__.py
│   │   ├── sync_data.py          → Stage 1: Fetch fixtures/odds/xG/injuries/standings
│   │   ├── compute_indices.py    → Stage 2: Calculate team form/class/venue indices
│   │   ├── build_predictions.py  → Stage 3: Generate 1X2 + TOTAL predictions
│   │   ├── evaluate_results.py   → Stage 4: Settle bets, compute P&L + metrics
│   │   ├── maintenance.py        → Stage 5: Cleanup + league params refresh
│   │   ├── quality_report.py     → Quality report with calibration, CLV, shadow filters
│   │   ├── rebuild_elo.py        → Force-rebuild Elo ratings
│   │   └── fetch_historical_update.py → Historical data fetching
│   ├── services/
│   │   ├── __init__.py
│   │   ├── elo_ratings.py         → Elo rating system
│   │   ├── poisson.py             → Poisson + Dixon-Coles probability functions
│   │   ├── league_model_params.py → DC rho estimation + power calibration alpha
│   │   ├── api_football_quota.py  → Quota guard for API-Football
│   │   ├── info_report.py         → Info/status report generation
│   │   ├── publishing.py          → Telegram publishing logic
│   │   └── text_image.py          → Image generation for Telegram
│   ├── main.py              → FastAPI app + scheduler + all API endpoints
│   ├── scheduler_runner.py  → Standalone scheduler process
│   └── ui/
│       ├── index.html       → SPA frontend (inline + external assets)
│       ├── ui.css            → Styles
│       └── ui.js             → JavaScript logic
├── docker-compose.yml
├── fix_pending_fixtures.sql   → Dev hotfix script
├── quality_what_if_report.txt → Dev artifact
├── requirements.txt
├── run_fix_pending.py         → Dev hotfix script
├── scripts/
│   ├── __init__.py
│   ├── backfill_current_season.py
│   ├── backtest.py            → Offline backtest simulator
│   ├── bootstrap_from_scratch.sh
│   ├── deprecated/            → Legacy code
│   ├── evaluate_stats.py
│   ├── fetch_historical.py
│   ├── fetch_odds_footballdata.py
│   ├── run_pipeline.py
│   ├── secret_scan.sh
│   ├── start_server.bat / stop_server.bat
│   └── train_model.py        → Logistic regression model trainer
└── tests/
    ├── conftest.py
    └── 23 test files (899 total lines)
```

#### Слои архитектуры

| Слой | Путь | Описание |
|------|------|----------|
| **API Layer** | `app/main.py` | FastAPI endpoints, auth, WebSocket, scheduler |
| **Jobs/Scheduler** | `app/jobs/` | 7 job-модулей + scheduler runner |
| **Business Logic** | `app/services/` | Elo, Poisson, model params, publishing |
| **Data Access** | `app/data/providers/` | API-Football, cache, Telegram, DeepL |
| **Data Mapping** | `app/data/mappers.py` | Status normalization |
| **Core/Config** | `app/core/` | Settings, DB, HTTP, logging, Decimal utils |
| **Models** | (no SQLAlchemy ORM models) | Schema via raw SQL in Alembic migrations |
| **UI** | `app/ui/` | Vanilla HTML/CSS/JS SPA |
| **Scripts** | `scripts/` | Offline training, backtesting, utilities |

### 1.2 Зависимости

#### requirements.txt (полный)
```
fastapi==0.115.4
uvicorn[standard]==0.32.0
httpx==0.27.2
pydantic==2.9.2
pydantic-settings==2.6.1
SQLAlchemy==2.0.36
asyncpg==0.30.0
alembic==1.13.3
APScheduler==3.10.4
python-dateutil==2.9.0.post0
numpy==2.1.3
pandas==2.2.3
pytest==8.3.3
Pillow==10.4.0
```

#### Зависимости для моделирования
- **numpy==2.1.3** — используется в scripts/train_model.py и scripts/backtest.py
- **pandas==2.2.3** — присутствует, но не используется в model core (только скрипты)
- **scikit-learn** — **НЕ** в requirements.txt, но импортируется **внутри** `scripts/train_model.py` (`from sklearn.linear_model import LogisticRegression`)
- **scipy** — **ОТСУТСТВУЕТ**
- **statsmodels** — **ОТСУТСТВУЕТ**
- **dirichletcal** — **ОТСУТСТВУЕТ**
- **psycopg2** — **НЕ** в requirements.txt, но импортируется внутри скриптов (train_model.py, backtest.py)

**Lock-файл**: ОТСУТСТВУЕТ (нет poetry.lock, requirements.lock, uv.lock)

### 1.3 Конфигурация

#### .env.example (основные переменные, сгруппированные)

**БД и приложение**:
- `APP_ENV`, `APP_MODE`, `DATABASE_URL`, `ADMIN_TOKEN`, `LOG_LEVEL`, `SQL_LOG_LEVEL`

**API-Football**:
- `API_FOOTBALL_KEY`, `API_FOOTBALL_HOST`, `API_FOOTBALL_BASE`
- TTL: `API_FOOTBALL_FIXTURES_TTL_*`, `API_FOOTBALL_ODDS_TTL_*`, `API_FOOTBALL_INJURIES_TTL_*`, `API_FOOTBALL_STANDINGS_TTL_*`, `API_FOOTBALL_FIXTURE_STATS_TTL_*`
- Quota: `API_FOOTBALL_DAILY_LIMIT`, `API_FOOTBALL_GUARD_ENABLED`, `API_FOOTBALL_GUARD_MARGIN`, `API_FOOTBALL_RUN_BUDGET_CACHE_MISSES`

**Publishing (Telegram + DeepL)**:
- `PUBLISH_MODE`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHANNEL_*` (8 языков), `DEEPL_API_KEY`

**Модельные параметры**:
- `LEAGUE_IDS`, `SEASON`, `BOOKMAKER_ID`, `MIN_ODD`, `MAX_ODD`, `VALUE_THRESHOLD`
- Веса: `WEIGHT_SHORT`, `WEIGHT_LONG`, `WEIGHT_VENUE`
- `CALIB_ALPHA_OVERRIDES`, `HYBRID_WEIGHTS`

**Feature flags**:
| Флаг | Default | Что включает/выключает |
|------|---------|----------------------|
| `ENABLE_ELO` | true | Elo-коррекция λ |
| `ENABLE_VENUE` | true | Venue-based form indices |
| `ENABLE_XG` | true | xG stats fetching + usage in indices |
| `ENABLE_FORM` | true | Short-window form in λ calculation |
| `ENABLE_CLASS` | true | Long-window class in λ calculation |
| `ENABLE_INJURIES` | true | Injury penalty on λ |
| `ENABLE_STANDINGS` | true | Standings delta in λ |
| `ENABLE_LEAGUE_BASELINES` | true | League-level baseline caching |
| `ENABLE_TOTAL_BETS` | false | TOTAL (Over/Under 2.5) market |
| `USE_LOGISTIC_PROBS` | false | Logistic probability model |
| `USE_DIXON_COLES_PROBS` | false | Dixon-Coles probability model |
| `USE_HYBRID_PROBS` | false | Hybrid weighted combination |
| `BACKTEST_MODE` | false | Backtest mode |
| `SNAPSHOT_AUTOFILL_ENABLED` | false | Auto-trigger sync for snapshot gaps |

**Per-league controls**:
- `LEAGUE_1X2_ENABLED` — comma-separated league IDs (empty = all)
- `LEAGUE_EV_THRESHOLD_OVERRIDES` — format `39:0.12,61:0.12`
- `VALUE_THRESHOLD_TOTAL` — EV threshold for TOTAL market (default 0.12)

**Scheduler crons**:
- `JOB_SYNC_DATA_CRON=*/5 * * * *`
- `JOB_COMPUTE_INDICES_CRON=1-59/10 * * * *`
- `JOB_BUILD_PREDICTIONS_CRON=3-59/10 * * * *`
- `JOB_EVALUATE_RESULTS_CRON=2-59/5 * * * *`
- `JOB_MAINTENANCE_CRON=30 3 * * *`
- `JOB_QUALITY_REPORT_CRON=30 6,23 * * *`

---

## РАЗДЕЛ 2: Модельное ядро (КРИТИЧЕСКИЙ РАЗДЕЛ)

### 2.1 Текущая модель прогнозирования

**Файл**: `app/jobs/build_predictions.py`, функция `run()` (строка 607)

**Алгоритм генерации вероятностей 1X2 (пошагово)**:

1. **League baseline** (строки 672-674): Загрузка средних xG по лиге: `base_home`, `base_away`, `league_draw_freq`, `dc_rho`, `calib_alpha`
2. **Weighted attack/defense** (строки 676-677): `_weighted_attack()` комбинирует 3 окна формы с весами (short=0.3, long=0.2, venue=0.5)
3. **Lambda расчёт** (строки 679-685):
   ```
   home_att_factor = home_att / base_home
   away_def_factor = away_def / base_home
   lam_home = base_home * home_att_factor * away_def_factor
   ```
   Аналогично для `lam_away`
4. **Elo adjustment** (строки 699-704): `adj_factor = 1 + elo_diff/1600`, clamped [0.75, 1.25]. `lam_home *= adj`, `lam_away /= adj`
5. **Standings delta** (строки 707-715): `±5%` nudge к λ от разницы в очках
6. **Injury penalty** (строки 718-724): До -8% к λ за каждую травму
7. **Poisson probabilities** (строка 767): `match_probs(lam_home, lam_away, k_max=10)` → `p_home_poisson, p_draw_poisson, p_away_poisson`
8. **Dixon-Coles probabilities** (строка 768): `match_probs_dixon_coles(lam_home, lam_away, rho=dc_rho, k_max=10)`
9. **Logistic probabilities** (строки 780-785): Если `USE_LOGISTIC_PROBS` или `USE_HYBRID_PROBS`
10. **Model selection** (строки 786-809): По флагам: hybrid → logistic → dixon_coles → poisson
11. **Power calibration** (строка 815): `_power_scale_1x2(p_home, p_draw, p_away, effective_alpha)`

**Ответ: используется Poisson + опционально Dixon-Coles/Logistic/Hybrid**. По умолчанию — чистый Poisson.

### 2.2 Домашнее преимущество

**Как учитывается**:
- **В λ расчёте**: через venue-специфичные indices (`home_venue_for`, `home_venue_against`) с весом 0.5 — это **per-team** venue advantage, но не явный параметр γ
- **В Elo**: НЕТ home advantage в Elo формуле
- **В Poisson**: домашнее преимущество заложено через league baseline (`avg_home_xg` > `avg_away_xg` в среднем) и venue indices

**Критически**: НЕТ явного глобального параметра γ (home advantage) как в Dixon-Coles. Home advantage «размазан» по venue indices и league baseline.

### 2.3 Расчёт ожидаемых голов (λ, μ)

```
λ_home = base_home_avg × (home_att / base_home) × (away_def / base_home) × elo_adj × standings_adj × injury_adj
λ_away = base_away_avg × (away_att / base_away) × (home_def / base_away) / elo_adj × standings_adj × injury_adj
```

Где `home_att` и `home_def` — **weighted averages** из form/class/venue indices, а **НЕ** латентные параметры атаки/обороны из Dixon-Coles.

**Принципиальное отличие от DC**: НЕТ декомпозиции на `att_i × def_j × γ`. Вместо этого — эмпирические скользящие средние голов/xG, которые НЕ корректируются на силу расписания.

### 2.4 Elo-система

**Файл**: `app/services/elo_ratings.py`

| Параметр | Значение |
|----------|----------|
| Формула | Стандартная: `E = 1/(1+10^((opp-rating)/400))` |
| K-factor | 20 (фиксированный) |
| Начальный рейтинг | 1500 |
| Home advantage в Elo | **НЕТ** |
| Goal-difference adjustment | **НЕТ** |
| Regression to mean между сезонами | **НЕТ** |
| Per-league vs единая шкала | **Единая шкала** (все команды в одной таблице) |
| Инкрементальная обработка | Да, через `fixtures.elo_processed` flag |
| Rebuild | Полный ребилд через `rebuild_elo` job или автоматически при out-of-order fixtures |

### 2.5 Индексы (compute_indices)

**Файл**: `app/jobs/compute_indices.py`

**Вычисляемые индексы**:
| Индекс | Окно | Описание |
|--------|------|----------|
| `home_form_for/against` | L5 | Avg xG/goals scored/conceded, last 5 matches |
| `away_form_for/against` | L5 | Same for away team |
| `home_class_for/against` | L15 | Long-term average, last 15 matches |
| `away_class_for/against` | L15 | Same for away team |
| `home_venue_for/against` | L5 home | Home-only venue form |
| `away_venue_for/against` | L5 away | Away-only venue form |
| `home_rest_hours` | - | Hours since last match |
| `away_rest_hours` | - | Hours since last match |
| `home_xg_l5/l10` | L5/L10 | Rolling xG from hist_fixtures |
| `away_xg_l5/l10` | L5/L10 | Same for away team |

**Временная корректность**: Да, все запросы используют `kickoff < cutoff` (walk-forward safe).

### 2.6 Dixon-Coles

**Реализация τ(ρ) коррекции**: `app/services/poisson.py:50-95` — **РЕАЛИЗОВАНА**

```python
def match_probs_dixon_coles(lam_home, lam_away, rho=D("0.1"), k_max=10):
    # Canonical tau correction for (0,0), (0,1), (1,0), (1,1)
    if i == 0 and j == 0: corr = 1 - lam_h * lam_a * rho
    elif i == 0 and j == 1: corr = 1 + lam_h * rho
    elif i == 1 and j == 0: corr = 1 + lam_a * rho
    elif i == 1 and j == 1: corr = 1 - rho
```

**Оценка ρ**: `app/services/league_model_params.py:61-144` — grid search (401 шагов) по log-likelihood (0,0)/(0,1)/(1,0)/(1,1) частотам. **Per-league, per-season**.

**Что ОТСУТСТВУЕТ**:
- ❌ Латентные параметры att_i/def_i (ядро Dixon-Coles)
- ❌ Мультипликативная факторизация `λ = att_i × def_j × γ`
- ❌ Time-decay ξ
- ❌ Sum-to-zero constraint
- ❌ Identifiability constraint

**Вывод**: Реализована ТОЛЬКО τ(ρ) коррекция из Dixon-Coles (вторичный компонент). Основная структура (латентная атака/оборона) — **ОТСУТСТВУЕТ**.

### 2.7 Стэкинг и ансамбли

**Текущее состояние**:
- Есть 3 модели: Poisson, Dixon-Coles (с ρ-коррекцией), Logistic
- **Hybrid mode** (`USE_HYBRID_PROBS=true`): **линейный пулинг** — взвешенное среднее вероятностей
  - Default weights: `logistic:0.5, poisson:0.3, dixon_coles:0.2`
  - `app/jobs/build_predictions.py:788-800`
- ❌ НЕТ мета-модели (стэкинга)
- ❌ НЕТ обучения на out-of-sample предсказаниях
- ⚠️ Линейный пулинг **доказанно ломает калибровку** (см. research document)

**scripts/train_model.py**: Тренирует LogisticRegression (sklearn) на hist_fixtures с фичами [elo_diff, xpts_diff, xg_diff_l5, home_advantage, form_index]. Сохраняет коэффициенты в `model_params`. Это **НЕ стэкинг** — это отдельная модель.

### 2.8 Калибровка

**Power scaling** (температурная калибровка): `p_i' ∝ p_i^α`
- Реализована в `_power_scale_1x2()` (build_predictions.py:92-110)
- Alpha оптимизируется grid search по logloss: `estimate_power_calibration_alpha()` (league_model_params.py:147-259)
- **Per-league, per-season**, на основе historical prediction_decisions
- Диапазон: α ∈ [0.5, 2.0], 61 шаг

**Что ОТСУТСТВУЕТ**:
- ❌ Platt scaling
- ❌ Isotonic regression
- ❌ Dirichlet calibration
- ❌ Пакет `dirichletcal` не в зависимостях

### 2.9 xG и продвинутые данные

`ENABLE_XG=true` включает:
1. Загрузку xG через `/fixtures/statistics` endpoint API-Football (в sync_data)
2. Использование xG **как fallback для голов** в compute_indices: `COALESCE(home_xg, home_goals)`
3. Rolling xG L5/L10 из hist_fixtures (если доступно)

**xG в модели**: Используется как замена raw goals в скользящих средних. НЕТ отдельных xG-фич в модели прогнозирования.

### 2.10 Odds и рыночные данные

**Использование odds**:
- **НЕ используются как фича** для модели
- Используются для **EV расчёта**: `EV = prob × odd - 1`
- **Market average odds** собираются (средние по всем букмекерам) для market_diff warning
- **CLV (Closing Line Value)** рассчитывается в quality_report через odds_snapshots

**Overround removal**: ❌ **ОТСУТСТВУЕТ**. Implied probability = 1/odd, без коррекции.

**Opening vs Closing**: odds_snapshots хранят историю, CLV считается через последний pre-kickoff snapshot vs initial_odd.

---

## РАЗДЕЛ 3: Value Betting логика

### 3.1 Генерация ставок

**Критерий value bet**: `EV = model_prob × bookmaker_odd - 1 > threshold`

**Thresholds**:
- **1X2 base**: 0.08 (hardcoded `VALUE_THRESHOLD_1X2`)
- **Per-league overrides**: `LEAGUE_EV_THRESHOLD_OVERRIDES` (e.g., EPL=0.12)
- **Signal score adjustment**: low signal → +0.05, high signal → -0.01
- **TOTAL market**: 0.12 (higher due to lower model edge)
- **Odds range**: MIN_ODD=1.50, MAX_ODD=3.20

**Signal score** (0-1): composite of:
- `samples_score` (40%): data availability
- `volatility_score` (30%): xG stability
- `elo_gap_score` (30%): rating difference
- Minus: standings gap, injury uncertainty

**Skip conditions**: signal_score < 0.6 → forced SKIP

**Kelly criterion**: ❌ **ОТСУТСТВУЕТ**. Flat betting (1 unit per bet).

### 3.2 Settlement

- Результат из `fixtures.home_goals / away_goals` (API-Football)
- `WIN`: profit = odd - 1; `LOSS`: profit = -1; `VOID`: profit = 0
- Нет учёта комиссии букмекера (предполагается чистый flat bet)

---

## РАЗДЕЛ 4: Data Pipeline

### 4.1 Ingestion (sync_data)

**API-Football endpoints**:
| Endpoint | Данные | TTL |
|----------|--------|-----|
| `/fixtures` | Fixtures (results, status, goals) | 180s recent / 86400s historical |
| `/odds` (by date/fixture) | Odds от bookmaker_id | 120s |
| `/fixtures/statistics` | xG stats | 43200s |
| `/injuries` | Player injuries | 10800s |
| `/standings` | League standings | 43200s |

**Не используются**: lineups, player stats, transfers, events.

**Cron**: `*/5 * * * *` (каждые 5 минут)

### 4.2 Схема БД

**Таблицы** (из 29 миграций):

| Таблица | Назначение |
|---------|------------|
| `fixtures` | Матчи (id, league_id, season, kickoff, home/away goals, xG, status, has_odds, stats_*, elo_processed) |
| `teams` | Команды (id, name, league_id, code, logo_url) |
| `leagues` | Лиги (id, name, country, active, logo_url) |
| `odds` | Текущие odds по bookmaker_id (1X2 + O/U 2.5 + market averages) |
| `odds_snapshots` | Исторические снимки odds (для true-backtest) |
| `match_indices` | Вычисленные фичи (form/class/venue for/against, rest_hours, rolling xG) |
| `predictions` | 1X2 predictions (selection, confidence, odd, EV, status, profit, signal_score, feature_flags JSONB) |
| `predictions_totals` | TOTAL market predictions |
| `prediction_decisions` | Detailed decision payloads (candidates, reasons) |
| `prediction_publications` | Telegram publication tracking |
| `team_elo_ratings` | Current Elo ratings per team |
| `team_standings` | League standings (rank, points, GD, form) |
| `injuries` | Player injuries with fingerprint dedup |
| `league_baselines` | Per-league/season/date averages + dc_rho + calib_alpha |
| `model_params` | Trained model coefficients (scope, league_id, param_name, param_value) |
| `job_runs` | Job execution history (status, meta JSONB) |
| `api_cache` | External API response cache |
| `hist_fixtures` | Historical fixtures (for training/backtest) |
| `hist_odds` | Historical odds (for backtest) |
| `hist_statistics` | Historical statistics |

**НЕТ таблицы для** att/def snapshots (потому что DC ядро не реализовано).

### 4.3 Jobs и Scheduler

| Job | Cron | Зависит от |
|-----|------|------------|
| `sync_data` | `*/5 * * * *` | - (external API) |
| `compute_indices` | `1-59/10 * * * *` | sync_data (needs fixtures) |
| `build_predictions` | `3-59/10 * * * *` | compute_indices (needs match_indices) |
| `evaluate_results` | `2-59/5 * * * *` | fixtures (finished matches) |
| `maintenance` | `30 3 * * *` | - |
| `quality_report` | `30 6,23 * * *` | predictions (settled) |
| `rebuild_elo` | manual only | fixtures |

**Pipeline**: sync_data → compute_indices → build_predictions → evaluate_results (sequential via `full_pipeline` trigger).

**Обработка ошибок**: Advisory locks (PostgreSQL), per-job asyncio.Lock, try/catch с logging. Quota guard для API-Football.

---

## РАЗДЕЛ 5: Оценка качества и бэктестинг

### 5.1 Метрики

| Метрика | Где считается | Proper? |
|---------|--------------|---------|
| **Brier score** | `evaluate_results.py:25` (per-prediction), `quality_report.py:258-290` (aggregate) | ✅ Yes |
| **Log loss** | `evaluate_results.py:29` (per-prediction), `quality_report.py:268` | ✅ Yes |
| **ROI** | `evaluate_results.py:207-224`, `quality_report.py:149-150` | N/A (financial) |
| **Win rate** | `quality_report.py:148` | N/A |
| **CLV** | `quality_report.py:105-110` (closing_odd vs initial_odd) | N/A |
| **RPS** | ❌ **НЕ реализован** | - |

**Quality Report** разрезы:
- By league
- By odds bucket (1.0-1.49, 1.5-1.99, 2.0-2.99, 3.0-4.99, 5.0+)
- By time-to-match (<6h, 6-12h, 12-24h, 1-3d, 3-7d, 7d+)
- Calibration bins (10 bins by probability)
- Shadow filters (what-if exclusions)

### 5.2 Бэктестинг

**В приложении** (`BACKTEST_MODE=true`):
- `pseudo`: использует текущие odds → оптимистичные метрики
- `true`: использует pre-kickoff odds_snapshots → корректнее

**Standalone** (`scripts/backtest.py`):
- Walk-forward: Elo и rolling features обновляются хронологически
- Данные из hist_fixtures + hist_odds
- Metrics: ROI, hit rate, Brier, LogLoss per league/market
- Поддержка --compare (old vs new model)

**Ablation testing**: ❌ **ОТСУТСТВУЕТ** в формализованном виде. Можно сравнить old vs new через `--compare`.

### 5.3 Risk signals в quality_report

Shadow filters (предопределённые what-if сценарии):
- `exclude_league_39` — Exclude Premier League
- `exclude_odds_2_0_2_99` — Exclude mid-range odds
- `exclude_league_94_140` — Exclude Primeira + La Liga (for totals)

---

## РАЗДЕЛ 6: UI и API

### 6.1 Frontend

**Стек**: Vanilla HTML + CSS + JavaScript (SPA в одном index.html + ui.css + ui.js)

**Страницы**:
| Раздел | Экран | Описание |
|--------|-------|----------|
| 📊 Обзор | Главная | Dashboard KPIs, recent activity |
| 📊 Обзор | Live Picks | Current predictions (1X2 + TOTAL) |
| 📈 Анализ | История ставок | ROI analysis, paginated history |
| 📈 Анализ | Графики | Charts and analytics |
| ⚙️ Управление | Задания | Job control, status monitoring |
| ⚙️ Управление | База данных | DB browser |
| ⚙️ Управление | Компоненты | UI component library |

**WebSocket**: Да, для real-time job status updates.

### 6.2 API endpoints

Основные (все под `X-Admin-Token`):
- `GET /health` (public), `GET /health/debug`
- `GET /api/v1/meta`, `GET /api/v1/freshness`
- `POST /api/v1/run-now?job=...` (rate-limited)
- `GET /api/v1/dashboard?days=...`
- `GET /api/v1/picks`, `GET /api/v1/picks/totals`
- `GET /api/v1/bets/history`
- `GET /api/v1/db/browse?table=...`
- `GET /api/v1/snapshots/gaps`
- `GET /api/v1/fixtures/{id}/details`
- `GET /api/v1/jobs/status`, `GET /api/v1/jobs/runs`
- `GET /api/v1/quality_report`
- `GET /api/v1/coverage`

**Rate limiting**: `RUN_NOW_MIN_INTERVAL_SECONDS=3`, `RUN_NOW_MAX_PER_MINUTE=20` (on `/api/v1/run-now`)

---

## РАЗДЕЛ 7: Тесты и качество кода

### 7.1 Тесты

**Количество**: 23 тестовых файла, ~899 строк total

**Покрытие по модулям**:

| Тестовый файл | Что тестирует |
|--------------|---------------|
| `test_poisson.py` | Poisson PMF, match_probs |
| `test_dixon_coles_probs.py` | Dixon-Coles tau correction |
| `test_elo_adjustment.py` | Elo adjust factor bounds |
| `test_ev_selection.py` | Best EV selection logic |
| `test_logistic_probs.py` | Logistic probability model |
| `test_hybrid_probs.py` | Hybrid weighted probabilities |
| `test_status_mapping.py` | Status normalization |
| `test_totals_settlement.py` | Over/Under settlement |
| `test_evaluate_results_voids.py` | Void/cancel handling |
| `test_api_*.py` (8 files) | API endpoint tests |
| `test_sync_data_*.py` (3 files) | Sync data logic |
| `test_http_retries.py` | HTTP retry logic |

**НЕ покрыты тестами**:
- `compute_indices` (основная логика)
- `build_predictions` (integration)
- `league_model_params` (rho/alpha estimation)
- `maintenance` job
- `quality_report` job
- `publishing` service
- Model accuracy/sanity tests

### 7.2 Качество кода

- **Линтер/форматтер**: ❌ Не обнаружено (нет ruff.toml, pyproject.toml с black/ruff, .flake8)
- **Type hints**: Частично — основные функции имеют аннотации (особенно в core/), но не везде
- **Docstrings**: Минимально — есть в ключевых функциях (elo, league_model_params), отсутствуют в большинстве
- **TODO/FIXME/HACK**: ❌ **Ни одного** в Python-файлах app/

### 7.3 Мусор и технический долг

**Артефакты в корне** (21 файл):
```
test_advanced_filters.html
test_all_api_endpoints_report.html
test_analytics_improved.html
test_code_analysis_complete.html
test_code_cleanup_analysis.html
test_component_library.html
test_dashboard.html
test_dashboard_improved.html
test_fresh_build_complete.html
test_history_improved.html
test_history_roi_fixed.html
test_jobs_predictions_system_complete.html
test_live_control_fixed.html
test_navigation_emergency_fix.html
test_navigation_fix.html
test_new_navigation.html
test_real_data_only.html
test_realtime_updates.html
test_smart_notifications.html
test_ui_business_logic_integration_complete.html
test_ui_fix_complete.html
test_ui_jobs_debug.html
test_unified_dashboard.html
test_websocket_integration.html
```

**Dev hotfix scripts**:
- `fix_pending_fixtures.sql`
- `run_fix_pending.py`
- `quality_what_if_report.txt`
- `fetch_historical.log`

**Deprecated code**: `scripts/deprecated/` — старые jobs и domain модули (корректно вынесены)

**Дублирование**:
- Poisson PMF реализована 3 раза: `app/services/poisson.py`, `scripts/train_model.py`, `scripts/backtest.py`
- Elo logic дублирована: `app/services/elo_ratings.py` vs `scripts/train_model.py` vs `scripts/backtest.py`

---

## РАЗДЕЛ 8: DevOps и безопасность

### 8.1 Docker

**Dockerfile**:
```dockerfile
FROM python:3.12-slim
WORKDIR /code
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**docker-compose.yml**:
```yaml
services:
  db:
    image: postgres:16
    restart: unless-stopped
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: fc_mvp
    ports: ["5432:5432"]
    volumes: [pgdata:/var/lib/postgresql/data]

  app:
    build: .
    restart: unless-stopped
    env_file: .env
    depends_on: [db]
    ports: ["8000:8000"]
    environment:
      SCHEDULER_ENABLED: "false"
      SNAPSHOT_AUTOFILL_ENABLED: "false"
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000

  scheduler:
    build: .
    restart: unless-stopped
    env_file: .env
    depends_on: [db]
    environment:
      SCHEDULER_ENABLED: "true"
    command: python -m app.scheduler_runner

volumes:
  pgdata:
```

**3 сервиса**: db (Postgres 16), app (API), scheduler (jobs)

**Health checks**: ❌ **ОТСУТСТВУЮТ** в docker-compose.yml

### 8.2 Миграции

**29 Alembic миграций**:

| ID | Описание |
|----|----------|
| 0001 | init |
| 0002 | team_fatigue_raw |
| 0003 | mvp_schema |
| 0004 | predictions_pk_id |
| 0005 | mvp_v22_schema |
| 0006 | fix_schema_alignment |
| 0007 | team_elo_ratings |
| 0008 | totals_market |
| 0009 | add_signal_score |
| 0010 | feature_flags |
| 0011 | market_avg_odds |
| 0012 | injuries_league_baselines |
| 0013 | injuries_fingerprint |
| 0014 | totals_settlement |
| 0015 | history_indexes |
| 0016 | job_runs |
| 0017 | history_sort_expr_indexes |
| 0018 | odds_snapshots |
| 0019 | prediction_decisions |
| 0020 | team_standings |
| 0021 | fixture_stats_retries |
| 0022 | retention_indexes |
| 0023 | predictions_settled_at |
| 0024 | backfill_predictions_settled_at |
| 0025 | elo_processed_and_league_model_params |
| 0026 | prediction_publications |
| 0027 | league_logo_url |
| 0028 | model_params |
| 0029 | match_indices_rolling_xg |

**Ручные SQL-фиксы**: `fix_pending_fixtures.sql`, `run_fix_pending.py` (в корне)

### 8.3 Безопасность

- **Аутентификация**: `ADMIN_TOKEN` → header `X-Admin-Token` (простой bearer)
- **CORS**: ❌ Не настроен (нет CORS middleware в main.py первых 150 строках)
- **Rate limiting**: Только на `/api/v1/run-now` (min interval + max per minute)

**⚠️ КРИТИЧНО — Хардкоженные секреты в .env.example**:
```
TELEGRAM_BOT_TOKEN=6462645627:AAEGtIvfiEeV6n3ZuCTeCux3HfQQdr69vNs
DEEPL_API_KEY=8f34b9b3-a664-42ad-8e3c-c1a4e9ec748d:fx
```
Это **реальные API ключи** в `.env.example`, который **зачекинен в git**. Критическая уязвимость.

---

## РАЗДЕЛ 9: Документация проекта

### 9.1 .md файлы

Все следующие файлы присутствуют и содержательны:
- **CLAUDE.md** — инструкции для Claude Code (подробные, актуальные)
- **PROJECT.md** — архитектурные заметки, поток данных, схема БД
- **AGENTS.md** — guidelines для разработки (build, test, style)
- **DEFINITION_OF_DONE.md** — критерии готовности для live/backtest pseudo/backtest true
- **REGRESSION_CHECKLIST.md** — чек-лист регресса (компиляция, pytest, docker, API smoke)
- **REPORT.md** — аудит/report по текущему состоянию (stages 0-4)
- **analysis_dixon_coles_stacking_calibration.md** — **242-строчный** глубокий теоретический документ, описывающий дорожную карту от baseline к production-grade DC+stacking+Dirichlet system

### 9.2 Комментарии в коде

- **Математические комментарии**: Минимальны. В `poisson.py` — краткий комментарий "Canonical Dixon-Coles low-score correlation adjustment (tau)". В `league_model_params.py` — docstring с формулами τ.
- **Ссылки на литературу**: ❌ Нет ссылок на Dixon & Coles 1997 или другие работы в коде. Литература обсуждается только в `analysis_dixon_coles_stacking_calibration.md`.

### 9.3 .claude/ директория

- `.claude/agents/football-predict-optimizer.md` — описание специализированного агента
- `.claude/settings.local.json` — локальные настройки Claude Code

---

## РАЗДЕЛ 10: Gap-анализ относительно дорожной карты

| Компонент дорожной карты | Статус | Детали |
|---|---|---|
| **Латентная атака/оборона (att/def)** | ❌ Отсутствует | Используются эмпирические скользящие средние xG/goals, НЕ латентные параметры. Нет декомпозиции att_i × def_j. Нет strength-of-schedule correction. |
| **Домашнее преимущество (γ)** | 🟡 Частично | Косвенно через venue indices и league baseline (avg_home_xg > avg_away_xg). Нет явного параметра γ. |
| **τ(ρ) коррекция для низких счетов** | ✅ Реализовано | `poisson.py:50-95` — canonical tau correction. ρ оценивается per-league grid search в `league_model_params.py`. |
| **Time-decay (ξ)** | ❌ Отсутствует | Нет экспоненциального затухания. Используются фиксированные окна (L5/L15). |
| **Poisson-модель голов** | ✅ Реализовано | `poisson.py:10-47` — double Poisson через PMF grid (k_max=10). |
| **Стэкинг (мета-модель)** | ❌ Отсутствует | Есть linear pooling (hybrid mode), НЕТ мета-модели на out-of-sample предсказаниях. |
| **Dirichlet calibration** | ❌ Отсутствует | Используется power scaling (temperature). `dirichletcal` не в зависимостях. |
| **Walk-forward backtest** | 🟡 Частично | В compute_indices — walk-forward safe (cutoff < kickoff). В scripts/backtest.py — хронологический. Но нет формализованного walk-forward framework с temporal cross-validation. |
| **Proper scoring rules (RPS, log loss, Brier)** | 🟡 Частично | Brier и LogLoss реализованы. RPS — отсутствует. |
| **xG-интеграция в модель** | 🟡 Частично | xG используется как fallback для goals в rolling averages. Не как отдельная фича в прогнозировании. |
| **Odds как benchmark (CLV)** | ✅ Реализовано | CLV рассчитывается в quality_report через odds_snapshots (closing vs initial). |
| **Odds как фича модели** | ❌ Отсутствует | Odds не подаются в модель как feature. Используются только для EV фильтра. |
| **H2H features (shrinked)** | ❌ Отсутствует | Нет head-to-head данных в модели. |
| **Fatigue × importance features** | 🟡 Частично | `rest_hours` рассчитывается в compute_indices, но НЕ используется в build_predictions. Importance proxy — нет. |
| **Ablation sequence** | ❌ Отсутствует | Нет формализованной системы ablation testing. |

---

## Критические находки (Топ-5)

### 1. 🔴 Отсутствие ядра Dixon-Coles (att/def латентные параметры)

**Проблема**: Основная структурная инновация Dixon-Coles — декомпозиция λ = att_i × def_j × γ — **полностью отсутствует**. Модель использует эмпирические скользящие средние, которые страдают от strength-of-schedule confounding (описано в research document). Реализована только τ(ρ) коррекция — **вторичный** компонент DC.

**Воздействие**: Это **главный архитектурный дефект**, который ограничивает предсказательную способность. Результаты против слабых команд завышают форму без дисконтирования.

**Рекомендация**: Реализовать полное DC ядро (att_i/def_i per team, sum-to-zero constraint, γ home advantage, time-decay ξ).

### 2. 🔴 Хардкоженные API ключи в .env.example (закоммичены в git)

**Проблема**: `.env.example` содержит **реальные** Telegram Bot Token и DeepL API Key:
```
TELEGRAM_BOT_TOKEN=6462645627:AAEGtIvfiEeV6n3ZuCTeCux3HfQQdr69vNs
DEEPL_API_KEY=8f34b9b3-a664-42ad-8e3c-c1a4e9ec748d:fx
```

**Воздействие**: Компрометация API ключей. Если репозиторий станет публичным, ключи будут немедленно украдены.

**Рекомендация**: Немедленно ротировать ключи и заменить на плейсхолдеры в .env.example.

### 3. 🟡 Линейный пулинг в hybrid mode нарушает калибровку

**Проблема**: `USE_HYBRID_PROBS=true` использует линейное среднее вероятностей, что **математически доказано** нарушает калибровку (research document, раздел "Линейный пулинг ломает калибровку"). Power scaling после пулинга лишь частично компенсирует.

**Рекомендация**: Заменить линейный пулинг на стэкинг (мета-модель) или Dirichlet calibration.

### 4. 🟡 Отсутствие overround removal

**Проблема**: Implied probability рассчитывается как `1/odd` без удаления overround. Это систематически завышает implied probabilities и может привести к ложному обнаружению value.

**Рекомендация**: Реализовать overround removal (basic normalization или Shin method).

### 5. 🟡 Хрупкость ML pipeline: sklearn не в requirements.txt

**Проблема**: `scripts/train_model.py` импортирует `sklearn.linear_model.LogisticRegression`, но `scikit-learn` **отсутствует** в `requirements.txt`. Также `psycopg2` используется в скриптах, но не в зависимостях. Docker-образ не сможет запустить обучение.

**Рекомендация**: Добавить `scikit-learn` и `psycopg2-binary` в requirements.txt (или отдельный requirements-scripts.txt).

---

## Дополнительные замечания

### Что хорошо реализовано
- Robust pipeline architecture (5 stages, advisory locks, idempotent)
- Walk-forward safe indices (cutoff < kickoff)
- Odds snapshots for true-backtest
- Signal score system (data quality filter)
- Per-league baseline caching with ρ and α estimation
- Comprehensive quality report with CLV, calibration bins, shadow filters
- Feature flags for granular control
- API quota guard with budget management

### Что стоит улучшить (помимо топ-5)
- Нет health checks в Docker Compose
- rest_hours рассчитывается, но не используется в build_predictions
- Нет CORS middleware
- 21 тестовый HTML-файл в корне проекта (dev artifacts)
- Нет lock-файла для reproducible builds
- Нет CI/CD pipeline (нет .github/workflows)
- SQLAlchemy ORM модели отсутствуют (весь SQL — raw text)
