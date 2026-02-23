# Changelog

Формат: каждая запись содержит дату, задачу, изменённые файлы и результат тестов.

---

## 2026-02-23 — Backlog cleanup: critical + high items (Task 18)

### 1.1 [CRITICAL] ENABLE_TOTAL_BETS=True
- **Файлы**: `app/core/config.py`
- **Fix**: `enable_total_bets: bool = Field(default=True)` (было False — несогласованность с новыми рынками, которые все True)

### 1.2 [LOW] Удалены obsolete файлы
- **Удалены**: `tests/test_elo_adjustment.py` (ImportError — elo_adjust_factor удалён в Task 15), `app/jobs/build_predictions_legacy.py` (backup от Task 15), `scripts/deprecated/` (legacy scripts)

### 2 [HIGH] Dashboard KPIs — все рынки
- **Файлы**: `app/main.py` — endpoint `/api/v1/dashboard`
- **Fix**: Убран фильтр `pt.market = 'TOTAL'` в UNION, заменён на `pt.selection != 'SKIP'`. Теперь KPIs учитывают ВСЕ рынки (TOTAL, TOTAL_1_5, TOTAL_3_5, BTTS, DOUBLE_CHANCE).
- **Bug fix**: `pt.selection_code` → `pt.selection` (таблица predictions_totals не имеет колонки selection_code)

### 3 [HIGH] Kelly для secondary markets
- **Файлы**: `app/jobs/build_predictions.py`
- **Fix**: Kelly fraction вычисляется в `_process_secondary_markets()` при создании BET decisions. Записывается в decision_payload (JSONB), а не в отдельную колонку. Паттерн аналогичен 1X2 Kelly.

### 4 [HIGH] BTTS + TOTAL → goals correlation group
- **Файлы**: `app/jobs/build_predictions.py`, `tests/test_new_markets.py`
- **Fix**: BTTS group "btts" → "goals", TOTAL group "total" → "goals". Correlation filter теперь объединяет TOTAL 1.5/2.5/3.5 + BTTS → один bet per fixture (max EV). DC остаётся в "double_chance".
- **D028**: Решение — BTTS и TOTAL коррелированы (высокий total → BTTS Yes).

### 5 [HIGH] DC rho grid expansion
- **Файлы**: `app/services/dixon_coles.py`
- **Fix**: rho_grid расширен с `[-0.2, 0.2]` / 41 step до `[-0.35, 0.35]` / 71 step. EPL, Bundesliga, La Liga показывали rho=-0.20 (граница grid).
- **D029**: Если после refit rho по-прежнему на границе (-0.35) — это сигнал о более глубокой проблеме.

### 6 [MEDIUM] History endpoint — уже реализован
- Проверка показала: `/api/v1/bets/history` уже поддерживает market filter (`?market=all|1x2|totals|btts|double_chance|total_1_5|total_3_5`) и UNION с predictions_totals.

**Тесты**: 261 passed, 5 skipped (без регрессий, -2 удалённых obsolete)
**Compile**: `python3 -m compileall -q app` — OK

---

## 2026-02-22 — Diagnostics: VOID rate + DC-xG params (Task 16)

### VOID rate diagnosis
- **Причина**: Все 122 VOID = SKIP (модель не нашла value bet). Не баг.
- **Breakdown для stacking (46 SKIP)**: odds_out_of_range=18, below_threshold=12, negative_ev=11, no_odds=5
- **Config**: VALUE_THRESHOLD=0.05, EPL/Ligue1 override=0.12, MIN_ODD=1.50, MAX_ODD=3.20
- **Рекомендация**: SKIP rate 80% нормален. Рассмотреть расширение odds range и снижение threshold для EPL/Ligue1.

### DC-xG params fix
- **Проблема**: `dc_xg_available=false` при DC_USE_XG=true. fit_dixon_coles не запускался после включения флага.
- **Fix**: Ручной запуск fit_dixon_coles через run-now
- **Результат**: xG params: 114 teams + 6 global (все 6 лиг). DC-xG probs теперь отличаются от DC-goals (до 4.8pp разницы).

### Settled stacking predictions
- 2 WIN (+1.70 units), 0 LOSS. Stacking: 2/2 = 100% win rate (слишком мало для выводов).
- 11 PENDING settle на этой/следующей неделе.

**Тесты**: Без изменений (код не менялся)
**Файлы**: `.architect/DIAGNOSTICS_16.md` (новый)

---

## 2026-02-22 — Production monitoring setup (Task 14)

### Part A: Feature_flags verification
- **Проверка**: Все 50+ полей записываются в feature_flags для stacking predictions
- **Статус**: 57 stacking predictions (52 с fair odds, 5 без), все с prob_source="stacking"
- **Замечание**: DC-xG params ещё не сгенерированы (dc_xg_available=false), DC-xG fallbacks на DC-goals

### Part B: Production monitor script
- **Файлы**: `scripts/production_monitor.py` (новый)
- **Функционал**:
  - Загрузка settled stacking predictions с feature_flags
  - Метрики: RPS, Brier, LogLoss (aggregate + per-league)
  - Calibration bins (10 bins by confidence)
  - Base model comparison (Stacking vs DC-only vs Poisson)
  - CLV analysis (closing odds from odds_snapshots)
  - Financial: ROI, win rate, Kelly eligibility
  - Actionable recommendations с порогами
- **Использование**: `docker compose exec scheduler python scripts/production_monitor.py`

### Part C: Quality report RPS fix
- **Файлы**: `app/jobs/quality_report.py`
- **Изменения**:
  - BetRow extended: `feature_flags`, `home_goals`, `away_goals`
  - `_fetch_1x2` SQL: добавлены `p.feature_flags`, `f.home_goals`, `f.away_goals`
  - `_calibration()`: RPS вычисляется из full distribution (feature_flags) + actual outcome (goals)
  - Импорт: `ranked_probability_score` из `app/services/metrics`
  - RPS больше не placeholder 0.0

### Part D: Monitoring checklist
- **Файлы**: `.architect/MONITORING_CHECKLIST.md` (новый)
- **Содержание**: пороги для Kelly/Dirichlet/rollback, команды проверки, decision tree

**Тесты**: 116 passed (без изменений — мониторинг не затрагивает модельный код)

---

## 2026-02-22 — Рефакторинг: единый prediction pipeline (Task 15)

### Цель
Устранение 6+ legacy code paths в build_predictions.py. Единый production pipeline: DC → Stacking → optional Dirichlet, два fallback: DC-only, Poisson baseline.

### Part A: build_predictions.py refactoring
- **Файлы**: `app/jobs/build_predictions.py` (1637→1436 строк, -201)
- **Удалено**:
  - Deprecated imports: `math`, `estimate_power_calibration_alpha`
  - Deprecated constants: `ELO_ADJ_MIN/MAX/K`, `LOGISTIC_FALLBACK_ELO_COEF/XPTS_COEF`, `FINAL_STATUSES`
  - Deprecated functions: `_load_model_params`, `_get_logistic_coefs`, `_get_optimal_alpha`, `_power_scale_1x2`, `elo_adjust_factor`, `logistic_probs_from_features`
  - `_league_baseline_cache`: 5→4 return values (removed calib_alpha), removed `estimate_power_calibration_alpha` call
  - Model selection: removed hybrid, logistic, dixon_coles_probs, power scaling paths
  - Calibration: removed power scaling fallback (Dirichlet only)
  - `adj_factor` removed from feature_flags and log messages
- **Backup**: `app/jobs/build_predictions_legacy.py`

### Part B: Config cleanup
- **Файлы**: `app/core/config.py`
- **Удалено**: `use_logistic_probs`, `use_dixon_coles_probs`, `use_dc_core`, `use_hybrid_probs`, `calib_alpha_overrides_raw`, `hybrid_weights_raw` (6 flags)
- **Defaults changed**: `dc_use_xg=True`, `use_stacking=True`
- **Удалённые properties**: `hybrid_weights`, `calib_alpha_overrides`

### Part C: Pipeline simplification
- **Файлы**: `app/main.py`, `app/jobs/maintenance.py`, `app/jobs/sync_data.py`
- **Изменения**:
  - fit_dixon_coles unconditionally scheduled (removed `if settings.use_dc_core:`)
  - fit_dixon_coles always runs in full pipeline
  - prob_source hardcoded to "stacking"
  - Removed `estimate_power_calibration_alpha` imports

### Part D: Deprecated code cleanup
- **Удалено**: `tests/test_hybrid_probs.py`, `tests/test_logistic_probs.py`
- **Перемещено**: `scripts/train_model.py` → `scripts/deprecated/train_model.py`
- **Обновлено**: `.env.example` — new structure with clear sections (Model/Features/Betting/REMOVED)

### Part E: New tests
- **Файлы**: `tests/test_prediction_pipeline.py` (новый, 17 тестов)
- **Покрытие**: TestFatigueFunction (4), TestSignalScoreComponents (5), TestModelSelection (3), TestStackingFeatureVector (2), TestClampDecimal (3)

### Итоги
- **Строки**: -201 (build_predictions), -6 flags из config
- **Тесты**: 116 passed (было 104, -5 deleted, +17 new)
- **Production path**: DC → Stacking → optional Dirichlet (единственный)
- **Fallback 1**: DC-only (нет stacking model)
- **Fallback 2**: Poisson baseline (нет DC params)

---

## 2026-02-22 — xG integration + stacking v2 retraining (Tasks 12-13)

### Task 12: DC-xG integration
- **Файлы**: `app/services/dixon_coles.py`, `app/core/config.py`, `app/jobs/fit_dixon_coles.py`, `tests/test_dixon_coles.py`, `scripts/generate_training_data.py`, `scripts/ablation_study.py`
- **Изменения**:
  - MatchData extended with optional `home_xg`/`away_xg` fields
  - New quasi-Poisson NLL function for xG mode (rho=0, no grid search, ~20-40x faster)
  - `DC_USE_XG` feature flag in config
  - fit_dixon_coles job: dual-mode (always goals, optionally xG)
  - Ablation config 1x (ID 11): DC-xG standalone ΔRPS=-0.0095 vs DC-goals -0.0085
  - 4 new xG tests (99 total passing)

### Task 13: Stacking v2 with DC-xG features
- **Файлы**: `alembic/versions/0033_dc_param_source.py`, `app/jobs/build_predictions.py`, `scripts/train_stacking.py`, `scripts/ablation_study.py`
- **Изменения**:
  - Migration 0033: `param_source` column in team_strength_params + dc_global_params
  - build_predictions: loads DC-xG params separately, computes DC-xG predictions, passes to stacking
  - Stacking v2: 13 features (added 3 DC-xG, removed dead standings_delta)
  - training_data_v2.json: 7308 samples, 6183 (84.6%) with DC-xG
  - Val RPS v2=0.1887 vs v1=0.1886 (neutral, model saved — DC-xG features have non-zero coefficients)
  - `DC_USE_XG=true` in .env
- **Тесты**: 99 passed

---

## 2026-02-22 — Ablation stacking + Dirichlet evaluation + activation (Task 11)

### Part A: Ablation configs 2,3
- **Файлы**: `scripts/ablation_study.py`
- **Изменения**:
  - Config 2 (DC+Stacking): per-league DC fitting, odds loading from hist_fixtures, stacking predict with trained model
  - Config 3 (DC+Stacking+Dirichlet): full pipeline with Dirichlet post-processing on stacking output
  - Per-league mode for proper DC fitting (not cross-league)

### Part B: Dirichlet on post-stacking evaluation
- **Файлы**: `scripts/ablation_study.py`
- **Изменения**:
  - Dirichlet calibration evaluated on post-stacking probabilities
  - Result: ΔRPS = -0.0018 globally (marginal), inconsistent across leagues
  - Helps La Liga/Portugal, hurts EPL — not activated (D021)

### Part C: USE_STACKING activation
- **Файлы**: `.env.example`
- **Изменения**:
  - `USE_STACKING=true` (proven ΔRPS -7.0% in ablation)
  - `USE_DIRICHLET_CALIB=false` (marginal, not activated — D021)

### Part D: Insights and documentation
- **Файлы**: `.architect/DECISIONS.md`, `.architect/BACKLOG.md`, `.architect/STATE.md`
- **Изменения**:
  - D021: Dirichlet on post-stacking — marginal, not activated
  - D022: Fair odds are strongest stacking feature (fair_away +1.46, fair_draw +1.05, fair_home +0.96)
  - BACKLOG: historical standings data, Pinnacle closing line, DC refit caching

**Результаты**: ΔRPS -7.0% for stacking, -7.8% full pipeline (DC+Stacking+Dirichlet)
**Тесты**: 179 passed, 5 skipped (без изменений)

---

## 2026-02-22 — Training data generation + stacking training (Task 10)

### A: Walk-forward training data generator
- **Файлы**: `scripts/generate_training_data.py` (новый)
- **Изменения**:
  - Walk-forward генерация базовых предсказаний для исторических матчей
  - DC refit каждые 30 матчей, Poisson из rolling avg, Elo, rest hours, fair odds
  - Все 11 stacking features + extras (xg, lam, elo raw, rest hours)
  - Результат: 7308 примеров из 6 лиг → `results/training_data.json`

### B: train_stacking.py --from-file
- **Файлы**: `scripts/train_stacking.py`
- **Изменения**:
  - Добавлен `--from-file` для загрузки из JSON (вместо DB)
  - `load_training_data_from_file()` — парсинг JSON с фильтрацией по league_id
  - Убран deprecated `multi_class="multinomial"` (sklearn 1.8+)
  - Результат: val RPS=0.1886, val LogLoss=0.9299, модель сохранена в DB

### C: train_calibrator.py --from-file + --prob-source
- **Файлы**: `scripts/train_calibrator.py`
- **Изменения**:
  - Добавлен `--from-file` для загрузки из JSON
  - Добавлен `--prob-source` (dc/poisson) для выбора вероятностей
  - `load_calibration_data_from_file()` с PROB_SOURCE_MAP
  - Dirichlet на DC probs: ΔLogLoss=+0.4% — не улучшает (DC уже калиброван)

### D: Dependencies + container rebuild
- **Файлы**: `requirements.txt`, `requirements.lock`
- **Изменения**:
  - Добавлен `psycopg2-binary>=2.9.9` (нужен для generate_training_data.py)
  - requirements.lock обновлён через pip-compile
  - Контейнеры пересобраны с новыми зависимостями

**Тесты**: 179 passed, 5 skipped (без изменений — новые скрипты = offline tools)

---

## 2026-02-22 — Инфраструктурная зрелость (фаза 4.1 — 4.5)

### A: Зависимости и воспроизводимость (4.1)
- **Файлы**: `requirements.txt`, `requirements.lock` (новый), `Dockerfile`
- **Изменения**:
  - `Pillow>=10.4.0` добавлен в requirements.txt (был missing, ломал импорт)
  - `requirements.lock` — pin всех 137 зависимостей через pip-compile
  - Dockerfile обновлён: `COPY requirements.lock` + `pip install -r requirements.lock`
  - Комментарий в requirements.txt о lock-файле

### B: Docker health checks (4.3)
- **Файлы**: `docker-compose.yml`
- **Изменения**:
  - `db`: healthcheck via `pg_isready`
  - `app`: healthcheck via Python urllib → `/health`, depends_on `service_healthy`
  - `scheduler`: healthcheck via `python -c 'import app'`, depends_on `service_healthy`

### C: Тесты на непокрытые модули (4.2)
- **Файлы**: `tests/test_compute_indices.py` (новый), `tests/test_league_model_params.py` (новый), `tests/test_model_sanity.py` (новый)
- **Покрытие**:
  - compute_indices helpers: coalesce_metric (3), average (3), compute_window (3), rest_hours (3), with_fallback (2) = 14 тестов
  - league_model_params helpers: clamp_decimal (4), safe_float (5), as_dict (5), outcome_1x2 (4) = 18 тестов
  - Model sanity: probs sum to 1 (3), strong favorite (3), draw reasonable (2), lambda bounds (2), DC sum-to-zero (1) = 11 тестов
  - **Итого: +43 новых теста**

### D: Дедупликация кода (4.5)
- **Файлы**: `app/services/math_utils.py` (новый), `scripts/backtest.py`, `scripts/train_model.py`, `scripts/ablation_study.py`
- **Изменения**:
  - `math_utils.py`: shared float-based `elo_expected`, `poisson_pmf`, `match_probs_poisson`, `power_scale`
  - backtest.py: удалены `_elo_expected`, `_poisson_pmf`, `_match_probs`, `_power_scale` — импорт из math_utils
  - train_model.py: удалены `_elo_expected`, `_poisson_pmf`, `_match_probs_from_lambda` — импорт из math_utils
  - ablation_study.py: удалены `_elo_expected`, `_poisson_pmf`, `_match_probs_poisson` — импорт из math_utils

### E: CI/CD (4.4)
- **Файлы**: `.github/workflows/ci.yml` (новый)
- **Изменения**: GitHub Actions workflow — Python 3.12, Postgres 16, compile check, alembic migrate, pytest, docker build

### Тесты
- `python -m compileall -q app scripts` — OK
- `pytest -q` — 179 passed, 5 skipped (было 136 passed, +43 теста)

---

## 2026-02-22 — Production activation + Rest hours + Kelly (фаза 3.2 + 3.4)

### Part A: DC Production activation
- **Файлы**: `.env.example`, `app/main.py`
- **Изменения**:
  - `USE_DC_CORE=true` (default, proven ΔRPS -2.8% in ablation)
  - `fit_dixon_coles.run()` добавлен в `_run_pipeline` между compute_indices и build_predictions (conditional на `settings.use_dc_core`)

### Part B: Rest hours fatigue adjustment
- **Файлы**: `app/jobs/build_predictions.py`, `app/core/config.py`, `.env.example`
- **Изменения**:
  - `_fatigue_factor(rest_hours)` — piecewise linear: <72h→0.90-0.95, 72-120h→0.95-1.00, 120-192h→1.00-1.02, >=192h→1.00
  - Применяется к lam_home/lam_away после injuries, перед EV calc
  - `home_fatigue`/`away_fatigue` в feature_flags
  - Config: `ENABLE_REST_ADJUSTMENT=true` (default)

### Part C: Kelly criterion
- **Файлы**: `app/services/kelly.py` (новый), `tests/test_kelly.py` (новый), `app/jobs/build_predictions.py`, `app/core/config.py`, `.env.example`
- **Изменения**:
  - `kelly_fraction(model_prob, odds, fraction, max_fraction)` → Decimal
  - `kelly_stake(bankroll, model_prob, odds, ...)` → Decimal
  - Quarter Kelly (0.25) default, max_fraction=0.05 cap
  - `kelly_fraction` записывается в feature_flags (если ENABLE_KELLY=true)
  - Config: `ENABLE_KELLY=false` (default — включать после calibration quality proof на 200+ settled)

### Ablation: Config 1b (DC + rest hours)
- **Файлы**: `scripts/ablation_study.py`
- **Изменения**:
  - Config 10 ("1b"): DC Core + fatigue adjustment from computed rest hours
  - `_fatigue_factor()` float-версия, rest hours computed из дат матчей
  - CONFIG_ALIASES для парсинга "1b" → 10
  - EPL ablation result: ΔRPS=-0.0067 (vs baseline), +0.0003 vs DC Core (neutral)

### Тесты
- **Файлы**: `tests/test_kelly.py` (11 тестов)
- **Покрытие**: kelly_fraction (8 тестов: no_edge, positive_edge, max_cap, quarter<full, odds_one, prob_zero, negative_prob, breakeven), kelly_stake (3 тестов: bankroll, tiny_edge, rounding)
- `python -m compileall -q app scripts` — OK
- `pytest -q` — 136 passed, 5 skipped (было 125 passed, +11 kelly тестов)

---

## 2026-02-21 — Ablation framework (фаза 3.1)

### Ablation study скрипт
- **Файлы**: `scripts/ablation_study.py` (новый)
- **Изменения**:
  - Walk-forward evaluation с 4 конфигурациями (0: Baseline, 1: DC Core, 2: DC+Stacking placeholder, 3: Full Pipeline placeholder)
  - Config 0: rolling xG L5 + Elo adjustment → Poisson (идентично backtest.py baseline)
  - Config 1: Dixon-Coles с periodic refit (каждые 50 матчей), fallback на baseline при нехватке данных
  - Proper scoring: RPS, Brier, LogLoss per match → aggregate
  - Comparison table с ΔRPS vs baseline
  - JSON output в results/
  - Helper functions: `compute_outcome`, `matches_to_dc_input`, `load_finished_matches`

### Тесты
- **Файлы**: `tests/test_ablation.py` (новый, 20 тестов)
- **Покрытие**: compute_outcome (4), matches_to_dc_input (3), scoring functions (7), aggregation (2), comparison table ΔRPS (2), walk_forward baseline (1), walk_forward DC (1)
- `python -m compileall -q scripts/ablation_study.py` — OK
- `pytest -q` — 125 passed, 5 skipped (было 105 passed, +20 тестов)

### Инфраструктура
- **Файлы**: `results/.gitkeep` (новый), `.gitignore`
- **Изменения**: Директория results/ для хранения JSON output, `results/*.json` в .gitignore

---

## 2026-02-21 — Dirichlet калибровка (фаза 2.3 + 2.4)

### Сервис калибровки
- **Файлы**: `app/services/calibration.py` (новый)
- **Изменения**:
  - `DirichletCalibrator` — log(p) → W@log(p)+b → softmax, L-BFGS-B с аналитическим градиентом
  - L2 reg: `reg_lambda` (off-diagonal), `reg_mu` (diagonal towards 1)
  - `fit(probs, labels)` — обучение, min 30 samples (fallback → identity)
  - `calibrate(probs)` / `calibrate_single(p_h, p_d, p_a)` — inference
  - `to_dict()` / `from_dict()` — serialization
  - `load_calibrator(session, league_id)` / `save_calibrator(...)` — DB via model_params metadata JSONB

### Training скрипт
- **Файлы**: `scripts/train_calibrator.py` (новый)
- **Изменения**:
  - Загрузка p_home/p_draw/p_away из feature_flags settled predictions
  - Chronological 80/20 split, before/after comparison table
  - Safety: не сохраняет если logloss ухудшился (--force для override)
  - --dry-run, --league-id, --reg-lambda, --min-samples

### Интеграция в build_predictions
- **Файлы**: `app/jobs/build_predictions.py`
- **Изменения**:
  - `USE_DIRICHLET_CALIB` path: заменяет power scaling, fallback на power если нет калибратора
  - Pre-calibration probs записываются в feature_flags (p_home/draw/away_pre_calib)
  - `calibration_method` в feature_flags ("dirichlet" / "power" / "none")
  - Калибратор загружается один раз per batch

### Конфигурация
- **Файлы**: `app/core/config.py`, `.env.example`
- **Изменения**: `USE_DIRICHLET_CALIB=false` (default)

### Тесты
- **Файлы**: `tests/test_calibration.py` (новый, 9 тестов)
- **Покрытие**: valid W (1), sum=1 (1), probs∈(0,1) (1), bias correction (1), serialization roundtrip (1), Decimal interface (1), high reg → identity (1), unfitted raises (1), small sample identity (1)
- `python -m compileall -q app` — OK
- `pytest -q` — 99 passed, 5 skipped (было 90 passed, +9 тестов)

---

## 2026-02-21 — Стэкинг: мета-модель вместо линейного пулинга (фаза 2.1 + 2.2)

### Сервис стэкинга
- **Файлы**: `app/services/stacking.py` (новый)
- **Изменения**:
  - `StackingModel` — inference-only класс (softmax regression), без sklearn в app/
  - `predict(features)` → (p_home, p_draw, p_away) Decimal, sum ≈ 1.0, clamped to [0.0001, 0.9998]
  - `load_stacking_model(session, league_id)` — загрузка из model_params (metadata JSONB), fallback global→per-league
  - `save_stacking_model(session, ...)` — сохранение коэффициентов/intercept/feature_names

### Training скрипт
- **Файлы**: `scripts/train_stacking.py` (новый)
- **Изменения**:
  - Загрузка settled predictions из predictions + feature_flags
  - Walk-forward Variant A: OOS предсказания уже хранятся в feature_flags
  - Chronological split 80/20, LogisticRegression (multinomial, L2, lbfgs)
  - Evaluation: RPS, Brier, LogLoss на validation
  - Feature importance report, --dry-run опция
  - Feature vector: p_home/draw/away_poisson, p_home/draw/away_dc, elo_diff, standings_delta, fair_home/draw/away

### Интеграция в build_predictions
- **Файлы**: `app/jobs/build_predictions.py`
- **Изменения**:
  - `USE_STACKING` path: загрузка stacking_model один раз per batch, predict() per fixture
  - Stacking приоритетнее hybrid mode; warning если оба включены
  - Base model probs записываются в feature_flags: p_home/draw/away, p_home/draw/away_poisson, p_home/draw/away_dc
  - Логирование logistic probs enabled при USE_STACKING

### Конфигурация
- **Файлы**: `app/core/config.py`, `.env.example`
- **Изменения**: `USE_STACKING=false` (default), `scikit-learn>=1.4.0` в requirements.txt

### Тесты
- **Файлы**: `tests/test_stacking.py` (новый, 6 тестов)
- **Покрытие**: predict sum=1 (1), probs in (0,1) (1), base agreement (1), missing features (1), feature order (1), decimal return (1)
- `python -m compileall -q app` — OK
- `pytest -q` — 90 passed, 5 skipped (было 75 passed, +15 тестов включая skipped)

---

## 2026-02-21 — Улучшение Elo + модуль метрик RPS (фаза 1.2 + 1.3)

### Улучшение Elo (Part A)
- **Файлы**: `app/services/elo_ratings.py` (переписан), `app/core/config.py`
- **Изменения**:
  - `_expected_score()` — добавлены параметры `is_home`, `home_advantage` (default 65)
  - `_goal_diff_multiplier()` — новая функция: k_eff = K * max(1, ln(|diff| + 1))
  - `_detect_season_change()` — детекция границы сезона (gap > 45 дней)
  - `_regress_ratings()` — регрессия к среднему: new = 1500 + factor * (old - 1500)
  - `apply_elo_from_fixtures()` — интегрирует все три улучшения в batch-processing
  - `update_elo_rating()` — принимает is_home, home_advantage, goal_diff_mult
  - Config: `ELO_HOME_ADVANTAGE=65`, `ELO_K_FACTOR=20`, `ELO_REGRESSION_FACTOR=0.67`

### Модуль метрик (Part B)
- **Файлы**: `app/services/metrics.py` (новый)
- **Изменения**:
  - `ranked_probability_score(probs, outcome_index)` — RPS для упорядоченных 1X2 исходов
  - `brier_score(prob, outcome)` — Brier score
  - `log_loss_score(prob, outcome)` — Log-loss с clamping

### Интеграция в evaluate_results
- **Файлы**: `app/jobs/evaluate_results.py`
- **Изменения**:
  - Удалены inline brier_score/log_loss, импорт из metrics.py
  - Добавлен RPS в metrics accumulation (из feature_flags p_home/p_draw/p_away)
  - RPS в logging и output dict

### Интеграция в quality_report
- **Файлы**: `app/jobs/quality_report.py`
- **Изменения**:
  - `_calibration()` возвращает `rps` (placeholder, т.к. BetRow не несёт full distribution)
  - Shadow filters delta включает `rps`

### Конфигурация
- **Файлы**: `.env.example`
- **Изменения**: ELO_HOME_ADVANTAGE, ELO_K_FACTOR, ELO_REGRESSION_FACTOR

### Тесты
- **Файлы**: `tests/test_elo_ratings.py` (новый, 6 тестов), `tests/test_metrics.py` (новый, 13 тестов)
- **Покрытие Elo**: expected_score (3), goal_diff_multiplier (2), season_change (1)
- **Покрытие metrics**: RPS (7), Brier (3), LogLoss (3)
- `python -m compileall -q app` — OK
- `pytest -q` — 75 passed, 4 skipped (было 56 passed, +19 тестов)

---

## 2026-02-21 — Dixon-Coles ядро (фаза 1.1)

### Alembic-миграция 0030
- **Файлы**: `alembic/versions/0030_dixon_coles_params.py` (новый)
- **Изменения**: Создание таблиц `team_strength_params` и `dc_global_params` с индексами

### Сервис Dixon-Coles
- **Файлы**: `app/services/dixon_coles.py` (новый)
- **Изменения**:
  - `fit_dixon_coles()` — L-BFGS-B оптимизация с grid search для ρ, vectorized NLL
  - `predict_lambda_mu()` — вычисление λ/μ из DC-параметров
  - `tune_xi()` — walk-forward validation для подбора xi
  - `tau_value()` — float-версия τ-коррекции для scipy
  - Sum-to-zero constraint через репараметризацию (N-1 свободных параметров)

### Job fit_dixon_coles
- **Файлы**: `app/jobs/fit_dixon_coles.py` (новый), `app/jobs/__init__.py`
- **Изменения**: Per-league fitting с UPSERT в team_strength_params и dc_global_params

### Интеграция в build_predictions
- **Файлы**: `app/jobs/build_predictions.py`, `app/core/config.py`, `app/main.py`
- **Изменения**:
  - `USE_DC_CORE` feature flag (default: false)
  - DC-путь: загрузка att/def из БД, `predict_lambda_mu`, dc_rho из dc_global_params
  - Legacy-путь (rolling averages + Elo adj) остаётся нетронутым при USE_DC_CORE=false
  - `dc_core` flag в feature_flags для отслеживания
  - DC job зарегистрирован в scheduler и run-now

### Конфигурация
- **Файлы**: `requirements.txt`, `.env.example`
- **Изменения**: scipy>=1.12.0, JOB_FIT_DIXON_COLES_CRON, USE_DC_CORE

### Тесты
- **Файлы**: `tests/test_dixon_coles.py` (новый, 16 тестов)
- **Покрытие**: tau_value (6), predict_lambda_mu (3), fit_dixon_coles (7)
- `python -m compileall -q app` — OK
- `pytest -q` — 56 passed, 4 skipped (было 40 passed)

---

## 2026-02-21 — Hotfixes: очистка, overround removal, ротация ключей

### 1.0.1 Ротация ключей
- **Файлы**: `.env.example`
- **Изменения**: Заменены реальные секреты на плейсхолдеры:
  - `TELEGRAM_BOT_TOKEN` (был реальный токен бота)
  - `DEEPL_API_KEY` (был реальный UUID-ключ)

### 1.0.2 Overround removal
- **Файлы**: `app/services/odds_utils.py` (новый), `tests/test_odds_utils.py` (новый), `app/jobs/build_predictions.py`
- **Изменения**:
  - Создан `odds_utils.py` с `remove_overround_basic()` (1X2) и `remove_overround_binary()` (Over/Under)
  - 9 тестов в `test_odds_utils.py`
  - Интегрированы fair implied probabilities в `build_predictions.py`:
    - 1X2: `fair_home/draw/away` в feature_flags
    - TOTAL: `fair_over/under` в decision payloads (оба пути: main + skip)

### 1.0.3 Очистка репозитория
- **Файлы**: удалены 24x `test_*.html`, `fix_pending_fixtures.sql`, `run_fix_pending.py`, `quality_what_if_report.txt`, `fetch_historical.log`; обновлён `.gitignore`
- **Изменения**: добавлены паттерны `test_*.html`, `*.log`, `quality_what_if_report.txt` в `.gitignore`

### Тесты
- `python -m compileall -q app` — OK
- `pytest -q` — 40 passed, 4 skipped (3 collection errors из-за отсутствия Pillow — предсуществующая проблема)
