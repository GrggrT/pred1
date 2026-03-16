# Model Optimization Roadmap

**Старт**: 2026-03-15
**Цель**: CLV > 3%, calibration error < 0.06, draw bias < 1%

## Текущие метрики (baseline)
| Метрика | Значение | Цель |
|---------|----------|------|
| CLV | ≈ 0% | > 3% |
| Calibration error | 0.14 | < 0.06 |
| Draw overestimation | +3.7% | < 1% |

---

## Phase 1: Устранение draw leakage и DC bias — COMPLETED

### 1.1 Убрать `fair_draw` из stacking [DONE]
- **Файлы**: `build_predictions.py`, `train_stacking.py`, `generate_training_data.py`, `ablation_study.py`, `test_prediction_pipeline.py`
- **Суть**: 3 фичи `fair_home/draw/away` → 1 фича `fair_delta = fair_home - fair_away`
- **Результат**: Feature count 13 → 11. Draw leakage loop разорван.

### 1.2 Дифференцируемый ρ с L2 в Dixon-Coles [DONE]
- **Файл**: `app/services/dixon_coles.py`
- **Суть**: ρ включён в L-BFGS-B + `loss += 0.5·ρ²`, bounds [-0.35, 0.35]
- **Результат**: Grid search удалён. Синтетический тест: ρ=0.2062 (регуляризированный).

### 1.3 Shin's method для overround removal [DONE]
- **Файлы**: `odds_utils.py`, `build_predictions.py`, `generate_training_data.py`
- **Результат**: Draw probability -0.41% vs basic normalization.

### 1.4 Temperature Scaling [DONE]
- **Файлы**: `stacking.py`, `config.py`, `build_predictions.py`
- **Суть**: `probs = softmax(logits / T)`, T=1.2 (env: STACKING_TEMPERATURE)
- **Результат**: Softens overconfident predictions.

### 1.5 Opening odds + CLV fix [DONE]
- **Файлы**: `quality_report.py`, `build_predictions.py`
- **Суть**: Opening odds (earliest snapshot) per fixture, CLV formula fixed, market CLV added.
- **Результат**: `initial/closing - 1` (positive = value), `opening/closing - 1` (true line movement).

### 1.6 Target leakage guard [DONE]
- **Файлы**: `train_stacking.py`, `generate_training_data.py`
- **Суть**: Predictions <2h before kickoff excluded; Shin's method in training data generator.

---

## Phase 2: Верификация + новые фичи

### 2.1 Re-train stacking model [NOT STARTED — BLOCKER]
- Переобучить stacking с 15 features на production data
- **Критично**: без переобучки старая модель ожидает 13 features, новый код посылает 15
- Скрипт: `python scripts/train_stacking.py --from-file results/training_data.json`
- Перед этим: `python scripts/generate_training_data.py --leagues 39,78,140,135,61`

### 2.2 Backtest и сравнение метрик [NOT STARTED]
- BACKTEST_KIND=true на 3 месяца
- Сравнить: RPS, Brier, LogLoss, ROI, draw prediction rate, market CLV
- Зафиксировать метрики в PROGRESS.md

### 2.3 Auto xi tuning в DC pipeline [DONE]
- **Файл**: `app/jobs/fit_dixon_coles.py`
- **Суть**: Вызов `tune_xi()` перед fit (walk-forward, 70/30 split, grid 0.001-0.012)
- **Config**: `DC_AUTO_TUNE_XI=true` (env flag)

### 2.4-2.6 Новые stacking features (11 → 15) [DONE]
- `xg_momentum_home` = (xG_L5 - xG_L10) / max(xG_L10, 0.01)
- `xg_momentum_away` = аналогично
- `rest_advantage` = (home_rest - away_rest) / 24
- `league_pos_delta` = away_rank - home_rank
- **Файлы**: `build_predictions.py`, `train_stacking.py`, `generate_training_data.py`, `ablation_study.py`, `test_prediction_pipeline.py`

### 2.7 Per-league stacking models [DONE — infra]
- **Файл**: `build_predictions.py`
- **Суть**: Загрузка per-league stacking model с fallback на global
- `load_stacking_model(session, league_id=lid)` для каждой лиги в fixture set
- Тренировка: `python scripts/train_stacking.py --from-file ... --league-id 39`
- Аналогично для Dirichlet calibrator: per-league → global fallback

### 2.8 Dirichlet calibration (tuned) [DONE — infra]
- **Файлы**: `build_predictions.py`, `scripts/train_calibrator.py`
- Per-league calibration loading (same pattern as stacking)
- `--reg-mu` добавлен в train_calibrator.py для диагональной регуляризации
- Тренировка: `python scripts/train_calibrator.py --from-file ... --prob-source stacking --reg-lambda 0.01 --reg-mu 0.1`
- Включение: `USE_DIRICHLET_CALIB=true` (env)

### 2.9 StandardScaler для stacking features [DONE]
- **Файлы**: `stacking.py`, `train_stacking.py`
- **Суть**: elo_diff (-500..+500) vs probabilities (0..1) — StandardScaler нормализует
- Scaler mean/scale сохраняются с моделью, backward-compatible
- **Бонус**: `--batch-leagues 39,78,140,135,61` — тренировка global + per-league за один запуск

---

## Phase 3: Нелинейный стэкинг
**Срок**: После стабилизации Phase 2

### 3.1 XGBoost вместо softmax [DONE]
- **Файлы**: `stacking.py`, `train_stacking.py`
- `XGBoostStackingModel` class: `multi:softprob`, serialized as JSON in model_params
- `--model-type xgboost`, `--n-estimators`, `--max-depth`, `--learning-rate` CLI args
- Walk-forward validation (chronological split)

### 3.2 xG-based ELO [DONE]
- **Файлы**: `elo_ratings.py`, `config.py`
- `ELO_USE_XG_DIFF=true` — использует xG difference вместо goal difference для K-factor multiplier
- Query расширен: `SELECT ... home_xg, away_xg FROM fixtures`
- Backward-compatible: если xG=NULL или flag=false, используются реальные голы

### 3.3 Dynamic EV thresholds по лигам [NOT STARTED]
- `LEAGUE_EV_THRESHOLD_OVERRIDES` уже существует в config
- Подобрать оптимальные пороги на историческом backtest

### 3.4 H2H features [DONE]
- **Файлы**: `build_predictions.py`, `generate_training_data.py`, `train_stacking.py`, `ablation_study.py`, `test_prediction_pipeline.py`
- `h2h_draw_rate` = доля ничьих в последних 10 H2H (из fixtures)
- `h2h_goals_avg` = средний тотал в последних 10 H2H
- Feature vector v5: 15 → 17 features
- Pre-fetch в build_predictions, walk-forward в generate_training_data

---

## Правила работы
1. После КАЖДОГО изменения: `pytest -q` + syntax check
2. Бэктест на 3 мес. данных (BACKTEST_KIND=true) перед деплоем
3. Не менять больше 1 компонента за раз (изолировать влияние)
4. Фиксировать метрики до/после в PROGRESS.md
