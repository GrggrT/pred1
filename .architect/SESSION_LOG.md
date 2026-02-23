# Лог сессий

Каждая сессия = одно взаимодействие с агентом. Фиксируется: что было задано, что сделано, что осталось.

---

## Сессия 18 — 2026-02-23
**Задание**: Task 18 — Backlog cleanup: critical + high items
**Выполнено**:
1. **[CRITICAL] ENABLE_TOTAL_BETS=True** — config.py default False→True (несогласованность с новыми рынками)
2. **[LOW] Удалены obsolete** — test_elo_adjustment.py, build_predictions_legacy.py, scripts/deprecated/
3. **[HIGH] Dashboard KPIs all markets** — убран фильтр `market='TOTAL'`, исправлена ошибка `selection_code` → `selection` (колонка в predictions_totals)
4. **[HIGH] Kelly для secondary markets** — Kelly fraction в decision_payload для TOTAL/BTTS/DC predictions
5. **[HIGH] BTTS+TOTAL → goals group** — unified correlation group, max 1 bet per fixture из goals-рынков (D028)
6. **[HIGH] DC rho grid** — расширен [-0.2,0.2]/41 → [-0.35,0.35]/71 (D029)
7. **[MEDIUM] History endpoint** — уже реализован (market filter + UNION)

**Bug fix найден**: Dashboard SQL ссылался на `pt.selection_code` — колонка не существует в predictions_totals (правильно: `pt.selection`). Dashboard 500 при наличии settled predictions_totals.

**Результат**: 7 backlog items закрыты, 0 новых багов, docker перестроен
**pytest**: 261 passed, 5 skipped
**Следующий шаг**: Запустить fit_dixon_coles для проверки новых rho значений

---

## Сессия 11 — 2026-02-22
**Задание**: Task 11 — Ablation stacking + Dirichlet evaluation + activation
**Выполнено**:
1. **Part A — Ablation configs 2,3**: Implemented per-league DC fitting, odds loading from hist_fixtures, stacking predict in ablation_study.py. Config 2 (DC+Stacking) and Config 3 (DC+Stacking+Dirichlet) fully functional.
2. **Part B — Dirichlet on post-stacking**: Evaluated Dirichlet calibration on post-stacking probabilities. ΔRPS = -0.0018 globally — marginal. Inconsistent across leagues (helps La Liga/Portugal, hurts EPL). Decision: not activated (D021).
3. **Part C — USE_STACKING=true**: Activated in .env.example based on ablation results (ΔRPS -7.0%).
4. **Part D — Insights recorded**:
   - D021: Dirichlet post-stacking marginal, not activated
   - D022: Fair odds are strongest stacking feature (fair_away +1.46, fair_draw +1.05, fair_home +0.96)
   - BACKLOG: historical standings data, Pinnacle closing line, DC refit caching

**Результат**: Stacking activated, Dirichlet deferred
**Ключевые метрики**: ΔRPS -7.0% stacking, -7.8% full pipeline
**pytest**: 179 passed, 5 skipped (без изменений)
**Следующий шаг**: Ожидание задания от Lead

---

## Сессия 10 — 2026-02-22
**Задание**: Task 10 — Генерация training data + обучение стэкинга и калибратора
**Выполнено**:
1. **generate_training_data.py** — walk-forward генератор, DC refit каждые 30 матчей, Poisson rolling avg, Elo, rest hours, fair odds. Результат: 7308 примеров из 6 лиг (39,61,78,94,135,140), ~95% с DC, ~85% с odds.
2. **train_stacking.py --from-file** — загрузка из JSON, убран deprecated multi_class. Результат: val RPS=0.1886 (vs baseline ~0.206), модель сохранена в model_params.
3. **train_calibrator.py --from-file + --prob-source** — загрузка из JSON с выбором prob source (dc/poisson). Dirichlet на DC probs не улучшает (ΔLogLoss=+0.4%).
4. **psycopg2-binary** добавлен в зависимости, requirements.lock обновлён, контейнеры пересобраны.

**Результат**: Стэкинг обучен, калибрация DC нецелесообразна (DC уже калиброван)
**pytest**: 179 passed, 5 skipped (без изменений)
**Не сделано**: Ablation configs 2,3; активация USE_STACKING в production
**Следующий шаг**: Ablation с конфигами DC+Stacking и DC+Stacking+Dirichlet; решение об активации

---

## Сессия 1 — 2026-02-21
**Задание**: Инициализация рабочего пространства + Hotfixes (1.0.1, 1.0.2, 1.0.3)
**Выполнено**:
1. Создана `.architect/` со всеми файлами (ROLE, STATE, CHANGELOG, SESSION_LOG, BACKLOG, DECISIONS)
2. **1.0.1**: Ротация ключей — TELEGRAM_BOT_TOKEN, DEEPL_API_KEY заменены на плейсхолдеры
3. **1.0.2**: Создан `app/services/odds_utils.py` (remove_overround_basic + remove_overround_binary), 9 тестов, интеграция в build_predictions.py (fair implied probs в feature_flags и decision payloads)
4. **1.0.3**: Удалено 28 файлов мусора, обновлён .gitignore

**Результат**: Все задачи выполнены
**pytest до**: 3 collection errors (PIL), остальное не запускалось
**pytest после**: 40 passed, 4 skipped, 3 collection errors (PIL — предсуществующая)
**compileall**: OK
**Следующий шаг**: Ожидание задания от Lead (фаза 1.1 — Dixon-Coles ядро)

---

## Сессия 2 — 2026-02-21
**Задание**: Dixon-Coles ядро — латентные параметры атаки/обороны (фаза 1.1)
**Выполнено**:
1. **Миграция 0030**: team_strength_params + dc_global_params с индексами
2. **Сервис** `app/services/dixon_coles.py`: fit_dixon_coles (L-BFGS-B + ρ grid search), predict_lambda_mu, tune_xi, tau_value. Vectorized NLL, sum-to-zero через репараметризацию.
3. **Job** `app/jobs/fit_dixon_coles.py`: per-league fitting, UPSERT в обе таблицы, MIN_MATCHES=30
4. **Интеграция**: USE_DC_CORE flag в config, DC-путь в build_predictions.py с кэшированием, legacy-путь нетронут
5. **Регистрация**: job в __init__.py, main.py (scheduler + run-now), .env.example
6. **Зависимости**: scipy>=1.12.0 в requirements.txt
7. **Тесты**: 16 тестов в test_dixon_coles.py (tau, predict, fit — sum-to-zero, strong/weak, HA, time-decay)

**Результат**: Все части реализованы
**pytest до**: 40 passed, 4 skipped
**pytest после**: 56 passed, 4 skipped (+16 DC тестов), fitting за 0.81s на синтетике
**compileall**: OK
**Активация DC core** (для Lead):
```bash
docker compose exec app alembic upgrade head          # миграция 0030
curl -H "X-Admin-Token: dev" -X POST "http://localhost:8000/api/v1/run-now?job=fit_dixon_coles"  # fitting
# В .env: USE_DC_CORE=true → docker compose restart app scheduler
```
**Следующий шаг**: Ожидание задания от Lead (фаза 1.2 — улучшение Elo)

---

## Сессия 3 — 2026-02-21
**Задание**: Улучшение Elo + модуль метрик RPS (фаза 1.2 + 1.3)
**Выполнено**:
1. **Part A — Elo improvements**:
   - `_expected_score()`: добавлен `is_home` + `home_advantage` (ELO_HOME_ADVANTAGE=65)
   - `_goal_diff_multiplier()`: k_eff = K * max(1, ln(|diff|+1))
   - `_detect_season_change()`: gap > 45 дней → регрессия к среднему
   - `_regress_ratings()`: new = 1500 + factor * (old - 1500), ELO_REGRESSION_FACTOR=0.67
   - `apply_elo_from_fixtures()`: интегрирует HA, goal-diff, season regression
   - Config: ELO_HOME_ADVANTAGE, ELO_K_FACTOR, ELO_REGRESSION_FACTOR в config.py + .env.example
2. **Part B — Metrics module**:
   - `app/services/metrics.py`: ranked_probability_score, brier_score, log_loss_score (Decimal-based)
   - evaluate_results.py: удалены inline brier/logloss, импорт из metrics.py, добавлен RPS
   - quality_report.py: `_calibration()` возвращает rps, shadow filters delta включает rps
3. **Тесты**: 6 Elo + 13 metrics = +19 новых тестов

**Результат**: Все задачи выполнены
**pytest до**: 56 passed, 4 skipped
**pytest после**: 75 passed, 4 skipped (+19 тестов)
**compileall**: OK
**Активация** (для Lead):
- Elo improvements активны автоматически (default: HA=65, K=20, regression=0.67)
- Для перекалибровки Elo с новыми параметрами нужен force_recompute:
```bash
docker compose exec scheduler python -c "
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from app.services.elo_ratings import apply_elo_from_fixtures
from app.core.config import settings
async def rebuild():
    engine = create_async_engine(settings.database_url)
    S = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with S() as s:
        r = await apply_elo_from_fixtures(s, league_ids=settings.league_ids, force_recompute=True)
        await s.commit()
        print(r)
    await engine.dispose()
asyncio.run(rebuild())
"
```
- RPS метрики появятся в output evaluate_results автоматически при наличии p_home/p_draw/p_away в feature_flags
**Следующий шаг**: Ожидание задания от Lead (фаза 2 — стэкинг)

---

## Сессия 4 — 2026-02-21
**Задание**: Стэкинг — мета-модель вместо линейного пулинга (фаза 2.1 + 2.2)
**Выполнено**:
1. **Сервис**: `app/services/stacking.py` — StackingModel (numpy softmax inference), load/save через model_params metadata JSONB
2. **Training скрипт**: `scripts/train_stacking.py` — walk-forward Variant A (OOS predictions из feature_flags), LogisticRegression multinomial, chronological 80/20 split, RPS/Brier/LogLoss eval
3. **Интеграция**: build_predictions.py — USE_STACKING path (приоритет над hybrid), base model probs в feature_flags (p_home/draw/away_poisson, p_home/draw/away_dc)
4. **Config**: USE_STACKING=false, scikit-learn>=1.4.0
5. **Тесты**: 6 stacking тестов (predict sum=1, probs in (0,1), base agreement, missing features, feature order, decimal)

**Результат**: Все задачи выполнены
**pytest до**: 75 passed, 4 skipped
**pytest после**: 90 passed, 5 skipped (+15 тестов/skipped)
**compileall**: OK
**train_stacking.py --help**: OK
**Активация** (для Lead):
1. Накопить settled predictions с base model probs в feature_flags (build_predictions уже записывает p_home/draw/away_poisson и p_home/draw/away_dc)
2. Обучить мета-модель:
```bash
docker compose exec scheduler python scripts/train_stacking.py --min-samples 100 --dry-run
# Если метрики удовлетворительны:
docker compose exec scheduler python scripts/train_stacking.py --min-samples 100
```
3. Включить: `USE_STACKING=true` в .env → `docker compose restart app scheduler`
**Следующий шаг**: Ожидание задания от Lead (фаза 2.3 — Dirichlet калибровка)

---

## Сессия 5 — 2026-02-21
**Задание**: Dirichlet калибровка (фаза 2.3 + 2.4)
**Выполнено**:
1. **Сервис**: `app/services/calibration.py` — DirichletCalibrator: log(p)→W@log(p)+b→softmax, L-BFGS-B с аналитическим градиентом, L2 reg (off-diagonal + optional diagonal)
2. **Training скрипт**: `scripts/train_calibrator.py` — chronological split, before/after metrics comparison, safety check (не сохраняет если ухудшает logloss)
3. **Интеграция**: build_predictions.py — USE_DIRICHLET_CALIB path (заменяет power scaling), pre_calib probs + calibration_method в feature_flags
4. **Config**: USE_DIRICHLET_CALIB=false
5. **Тесты**: 9 calibration тестов

**Результат**: Все задачи выполнены. **Фаза 2 полностью завершена.**
**pytest до**: 90 passed, 5 skipped
**pytest после**: 99 passed, 5 skipped (+9 тестов)
**compileall**: OK
**train_calibrator.py --help**: OK
**Активация** (для Lead):
1. Накопить ~200+ predictions с p_home/p_draw/p_away в feature_flags
2. Обучить калибратор:
```bash
docker compose exec scheduler python scripts/train_calibrator.py --min-samples 200 --dry-run
# Если before/after показывает улучшение:
docker compose exec scheduler python scripts/train_calibrator.py --min-samples 200
```
3. Включить: `USE_DIRICHLET_CALIB=true` в .env → restart

**Полный pipeline (фаза 2 завершена)**:
```
Base models (DC/Poisson/Elo/Logistic) → Stacking (USE_STACKING) → Dirichlet (USE_DIRICHLET_CALIB) → EV calc
```
Каждый компонент — отдельный флаг, каждый — с fallback.
**Следующий шаг**: Ожидание задания от Lead (фаза 3 — продвинутые фичи + backtest)

---

## Сессия 6 — 2026-02-21
**Задание**: Ablation framework (фаза 3.1)
**Выполнено**:
1. **Скрипт**: `scripts/ablation_study.py` — walk-forward evaluation с 4 конфигурациями
   - Config 0: Baseline (rolling xG L5 + Elo adj → Poisson) — идентично backtest.py
   - Config 1: DC Core — periodic refit (каждые 50 матчей), fallback на baseline
   - Config 2, 3: placeholder (используют baseline)
   - Proper scoring: RPS, Brier, LogLoss per match
   - Comparison table с ΔRPS
   - JSON output в results/
2. **Helpers**: `compute_outcome`, `matches_to_dc_input`, `load_finished_matches`, `_match_probs_dc`
3. **Тесты**: 20 тестов в test_ablation.py (scoring, helpers, walk_forward с синтетикой)
4. **Инфраструктура**: `results/.gitkeep`, `.gitignore` обновлён

**Результат**: Все задачи выполнены
**pytest до**: 105 passed, 5 skipped
**pytest после**: 125 passed, 5 skipped (+20 тестов)
**compileall**: OK
**Запуск ablation** (для Lead):
```bash
docker compose exec scheduler python scripts/ablation_study.py --configs 0,1 --warmup 50
# Результаты в results/ablation_latest.json
# Per-league: --leagues 39
# Custom dates: --from-date 2023-01-01 --to-date 2025-12-31
```
**Следующий шаг**: Ожидание задания от Lead (фаза 3.2 — контекстные фичи или расширение ablation на configs 2,3)

---

## Сессия 7 — 2026-02-22
**Задание**: Диагностика данных + первый прогон ablation (между фазой 3.1 и 3.2)
**Выполнено**:
1. **Data inventory** — SQL-запросы к production и historical таблицам:
   - Production: 3104 FT fixtures, 6 лиг, 2 сезона (2024-2025)
   - Historical: 7604 FT fixtures, 6 лиг, 4 сезона (2022-2025)
   - 132 команды, Elo avg=1465
   - Odds: 209 fixtures (production), 6705 (historical)
   - 34 settled predictions (23L, 11W, 3301 VOID)
2. **DC fitting** — запущен на production данных, все 6 лиг:
   - EPL: 20 teams, 210 matches, HA=0.376, rho=-0.20, 3.1s
   - Ligue 1: 18 teams, 154 matches, HA=0.438, rho=+0.20, 1.2s
   - Bundesliga: 18 teams, 136 matches, HA=0.396, rho=-0.20, 3.2s
   - Liga Portugal: 18 teams, 152 matches, HA=0.160, rho=-0.07, 3.0s
   - Serie A: 20 teams, 187 matches, HA=0.102, rho=+0.04, 2.3s
   - La Liga: 20 teams, 182 matches, HA=0.280, rho=-0.20, 3.9s
3. **Ablation per-league** — configs 0 vs 1, hist_fixtures:
   - EPL: ΔRPS=-0.0070 (DC лучше)
   - Ligue 1: ΔRPS=-0.0037 (DC лучше)
   - Bundesliga: ΔRPS=+0.0007 (neutral)
   - Liga Portugal: ΔRPS=-0.0111 (DC значительно лучше)
   - Serie A: ΔRPS=-0.0052 (DC лучше)
   - La Liga: ΔRPS=-0.0100 (DC значительно лучше)
   - Средневзвешенное: ΔRPS=-0.0058 (DC лучше на ~2.8%)
4. **DATA_INVENTORY.md** создан с полными данными

**Результат**: DC Core доказуемо лучше baseline в 5 из 6 лиг
**Код не изменён**: только диагностика и запуск существующих скриптов
**Рекомендация**: Включить USE_DC_CORE=true для production
**Следующий шаг**: Ожидание задания от Lead

---

## Сессия 8 — 2026-02-22
**Задание**: Production activation + Rest hours fatigue + Kelly criterion (фаза 3.2 + 3.4)
**Выполнено**:
1. **Part A — DC production activation**:
   - `USE_DC_CORE=true` в .env.example (proven ΔRPS -2.8%)
   - `fit_dixon_coles` добавлен в `_run_pipeline` (main.py) — conditional на `use_dc_core`
   - Graceful degradation: DC fallback на legacy если params не загружены
2. **Part B — Rest hours fatigue adjustment**:
   - `_fatigue_factor(rest_hours)` в build_predictions.py: piecewise linear [0.90, 1.02]
   - Применяется к lam_home/lam_away, записывается home_fatigue/away_fatigue в feature_flags
   - Config: `ENABLE_REST_ADJUSTMENT=true` (default)
3. **Part C — Kelly criterion**:
   - `app/services/kelly.py`: kelly_fraction + kelly_stake (Decimal-based)
   - Quarter Kelly (0.25), max_fraction=0.05 cap
   - kelly_fraction → feature_flags (если ENABLE_KELLY=true)
   - Config: `ENABLE_KELLY=false` (default — включать после 200+ settled с доказанной calibration)
4. **Ablation config 1b (DC + rest)**:
   - Config 10 в ablation_study.py, alias "1b"
   - EPL: ΔRPS=-0.0067 (vs baseline), +0.0003 vs DC Core alone — **neutral effect**
   - Rest hours более полезен в production (точные данные из match_indices) чем в ablation (вычисление из дат)
5. **Тесты**: 11 kelly тестов (8 fraction + 3 stake)

**Результат**: Все три части реализованы, ablation проведён
**pytest до**: 125 passed, 5 skipped
**pytest после**: 136 passed, 5 skipped (+11 kelly тестов)
**compileall**: OK
**Активация** (для Lead):
- DC Core: уже active по default (USE_DC_CORE=true)
- Rest adjustment: уже active по default (ENABLE_REST_ADJUSTMENT=true)
- Kelly: **выключен по default** (ENABLE_KELLY=false). Включать только после:
  1. Накопления 200+ settled predictions
  2. Подтверждения calibration quality
  3. `ENABLE_KELLY=true` в .env → restart
**Следующий шаг**: Ожидание задания от Lead

---

## Сессия 9 — 2026-02-22
**Задание**: Инфраструктурная зрелость (фаза 4.1 — 4.5)
**Выполнено**:
1. **A — Зависимости (4.1)**:
   - `Pillow>=10.4.0` добавлен в requirements.txt
   - `requirements.lock` создан (pip-compile, 137 pinned deps)
   - Dockerfile обновлён для `requirements.lock`
2. **B — Docker health checks (4.3)**:
   - db: `pg_isready`, app: urllib→`/health`, scheduler: `import app`
   - `depends_on: condition: service_healthy` для app/scheduler
3. **C — Тесты (4.2)**:
   - `test_compute_indices.py`: 14 тестов (coalesce_metric, average, compute_window, rest_hours, with_fallback)
   - `test_league_model_params.py`: 18 тестов (clamp_decimal, safe_float, as_dict, outcome_1x2)
   - `test_model_sanity.py`: 11 тестов (sum-to-one, favorites, draw, lambda bounds, DC sum-to-zero)
4. **D — Дедупликация (4.5)**:
   - `app/services/math_utils.py`: canonical float Poisson/Elo (elo_expected, poisson_pmf, match_probs_poisson, power_scale)
   - backtest.py, train_model.py, ablation_study.py: удалены дубликаты, импорт из math_utils
5. **E — CI/CD (4.4)**:
   - `.github/workflows/ci.yml`: Python 3.12 + Postgres 16 + pytest + docker build

**Результат**: Фаза 4 полностью завершена
**pytest до**: 136 passed, 5 skipped
**pytest после**: 179 passed, 5 skipped (+43 теста)
**compileall**: OK
**BACKLOG**: Закрыты пункты Pillow и дублирование Poisson/Elo
**Следующий шаг**: Ожидание задания от Lead
