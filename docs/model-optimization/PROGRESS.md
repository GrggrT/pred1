# Progress Log — Model Optimization

## 2026-03-15: Исследование и диагностика

### Выполнено
- Полный аудит 6-ступенчатого пайплайна (sync → evaluate)
- Диагностика draw overestimation +3.7%: 6 корневых причин идентифицированы
- Диагностика CLV ≈ 0: измерение сломано (no opening odds, fair_odds leakage)
- Согласован план с командой: 7 приоритетных действий

### Корневые причины (подтверждены)
1. `fair_draw` feature → stacking копирует bookmaker draw bias
2. DC grid search по ρ → может выбирать положительный ρ без регуляризации
3. Простая нормализация overround → не учитывает неравномерную маржу
4. CLV = closing/initial ≈ 0 потому что оба из одного окна
5. Нет opening odds для реального CLV

---

## 2026-03-15: Phase 1 — Quick Wins (ALL COMPLETED)

### Step 1.1: Удаление fair_draw из stacking — DONE
- `build_predictions.py`: заменён fair_home/draw/away на `fair_delta = fair_home - fair_away`
- `train_stacking.py`: STACKING_FEATURE_NAMES v3 (13 → 11 фичей)
- `generate_training_data.py`: добавлен fair_delta
- `ablation_study.py`: обновлен
- `tests/test_prediction_pipeline.py`: 11 features, deprecated list updated
- Tests: 17 passed

### Step 1.2: Differentiable rho с L2 — DONE
- `dixon_coles.py`: добавлен `_neg_log_likelihood_with_rho()` с joint optimization
- ρ включён в вектор параметров L-BFGS-B с bounds [-0.35, 0.35]
- L2 reg: `nll += rho_l2_alpha * rho²` (default α=0.5)
- Grid search удалён (rho_grid_steps ignored)
- Синтетический тест: rho=0.2062 (с L2 reg), HA=0.1986

### Step 1.3: Shin's method для overround — DONE
- `odds_utils.py`: добавлен `remove_overround_shin()` (Newton-Raphson solver)
- `build_predictions.py`: import + вызов для 1X2 fair odds
- Тест: draw probability снижена на -0.41% vs basic normalization
- Tests: 21 passed

### Step 1.4: Temperature scaling — DONE
- `stacking.py`: StackingModel принимает `temperature`, применяет `logits / T`
- `config.py`: `stacking_temperature: float = 1.2` (STACKING_TEMPERATURE env)
- `build_predictions.py`: передаёт `settings.stacking_temperature` в `load_stacking_model()`
- Tests: 21 passed

### Step 2.1: Opening odds + CLV fix — DONE
- `quality_report.py`:
  - BetRow: добавлено `opening_odd` field
  - CLV formula исправлена: `initial/closing - 1` (positive = got better price)
  - Добавлен `_market_clv_pct()`: `opening/closing - 1` (true line movement)
  - `_fetch_1x2`: opening odds query (earliest snapshot per fixture)
  - `_fetch_totals`: opening odds query для всех вторичных рынков
  - `_summarize`: добавлены `market_clv_avg_pct`, `market_clv_cov`
- `build_predictions.py`:
  - Batch query opening odds (earliest snapshot per fixture) via `DISTINCT ON`
  - Stored in `feature_flags["opening_odds"]` for tracking
- Tests: 21 passed

### Step 2.2: Target leakage audit — DONE
- `train_stacking.py`:
  - `load_training_data()`: добавлен `min_hours_before_kickoff=2.0` parameter
  - SQL filter: `(f.kickoff - p.created_at) >= make_interval(hours => :min_hours)`
  - Предсказания сделанные <2h до kickoff исключены из тренировки
- `generate_training_data.py`:
  - Overround removal заменён на Shin's method (`_remove_overround_shin_float()`)
  - Docstring добавлен о потенциальной утечке через hist_odds closing lines
- Tests: 21 passed

---

## Summary Phase 1

| Step | Change | Expected Impact | Status |
|------|--------|-----------------|--------|
| 1.1 | fair_delta replaces 3 fair_* | -1.5% draw bias, break leakage | DONE |
| 1.2 | Differentiable ρ + L2 | -1.0% draw bias, smoother | DONE |
| 1.3 | Shin's method | -0.4% draw bias, +0.03 CLV | DONE |
| 1.4 | Temperature T=1.2 | -0.05 calib error | DONE |
| 2.1 | Opening odds + CLV fix | Unlock real CLV measurement | DONE |
| 2.2 | Target leakage guard | Cleaner training data | DONE |

---

## Phase 2: Верификация + новые фичи

### Step 2.3: Auto xi tuning — DONE
- `fit_dixon_coles.py`: вызывает `tune_xi()` перед fit (если >= 50 матчей)
- `config.py`: `DC_AUTO_TUNE_XI=true` (env flag)
- Оптимальный xi подбирается walk-forward (70/30 split, grid 0.001-0.012)
- Tests: 21 passed

### Step 2.4-2.6: Новые stacking features — DONE
- **Feature count**: 11 → 15 (stacking v4)
- Новые фичи:
  - `xg_momentum_home` = (xg_l5 - xg_l10) / max(xg_l10, 0.01) — acceleration
  - `xg_momentum_away` — аналогично
  - `rest_advantage` = (home_rest - away_rest) / 24 — normalized rest diff
  - `league_pos_delta` = away_rank - home_rank — standings signal
- Файлы: `build_predictions.py` (query + compute + feature_flags), `train_stacking.py`, `generate_training_data.py`, `ablation_study.py`, `test_prediction_pipeline.py`
- Tests: 21 passed

### Step 2.7: Per-league stacking infrastructure — DONE
- `build_predictions.py`:
  - Stacking model loading moved after `_target_rows()` for per-league support
  - `stacking_model_map: Dict[int, Any]` — per-league models with global fallback
  - `calibrator_map: Dict[int, Any]` — per-league calibrators with global fallback
  - Loop uses `stacking_model_map.get(int(row.league_id))` instead of single global
- `load_stacking_model(session, league_id=lid)` — tries league-specific, falls back to global
- `load_calibrator(session, league_id=lid)` — same fallback pattern
- Tests: 17 pipeline tests passed

### Step 2.8: Dirichlet calibration improvements — DONE
- `scripts/train_calibrator.py`:
  - Added `--reg-mu` parameter for diagonal W regularization towards identity
  - DirichletCalibrator receives both reg_lambda and reg_mu
- Per-league calibration loading in build_predictions.py (same pattern as stacking)
- Tests: 17 pipeline tests passed

### Step 2.9: StandardScaler for stacking features — DONE
- `app/services/stacking.py`:
  - StackingModel accepts optional `scaler_mean` / `scaler_scale` (backward-compatible)
  - `predict()`: applies `(x - mean) / scale` before linear transform if scaler present
  - `load_stacking_model()`: reads scaler params from metadata
  - `save_stacking_model()`: saves scaler params to metadata
- `scripts/train_stacking.py`:
  - StandardScaler fitted on training data, applied to train + val
  - Scaler mean/scale saved with model coefficients
  - Added `--batch-leagues` for training global + per-league in one run
- Backward-compatible: old models without scaler work as before
- Tests: 17 pipeline tests passed

### Pending
- [ ] **2.1 Re-train stacking**: old model expects 13 features → new code sends 15. Must re-train!
  - `python scripts/generate_training_data.py --leagues 39,78,140,135,61`
  - `python scripts/train_stacking.py --from-file results/training_data.json --batch-leagues 39,78,140,135,61`
- [ ] **2.2 Backtest**: BACKTEST_KIND=true на 3 месяца, сравнить метрики
- [ ] **2.8b Train Dirichlet calibrator**: `--prob-source stacking --reg-lambda 0.01 --reg-mu 0.1`
- [ ] **2.9 H2H features**: нет H2H данных в системе
- [ ] **3.1 XGBoost stacking** (Phase 3): `multi:softprob`, max_depth=3-4

