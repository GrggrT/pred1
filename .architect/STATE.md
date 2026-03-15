# Текущее состояние проекта

**Последнее обновление**: 2026-03-13
**Текущая фаза**: Tasks 10-22 завершены, Strategic Roadmap validated
**Текущая задача**: Task 22 завершён — Roadmap validation
**Блокеры**: Нет (ожидание накопления данных для v3 retrain и Pinnacle calibration)

## Архитектура prediction pipeline (после Task 22)

### Единый production pipeline
```
DC fit (goals + xG) → Poisson baseline → Stacking meta-model → optional Dirichlet → EV calc
                                          (v2, 13 features)
```

### Три уровня prediction
1. **Primary**: DC + Stacking (13 features: 3 Poisson + 3 DC-goals + 3 DC-xG + elo_diff + 3 fair odds)
2. **Fallback 1**: DC-only (нет stacking model в DB)
3. **Fallback 2**: Poisson baseline (нет DC params для команды/лиги)

### Новые компоненты (Roadmap, Task 22)
- **Historical standings**: LATERAL join для standings_rank_diff, standings_pts_diff
- **Opening odds**: opening_home/draw/away для odds_movement вычисления
- **Pinnacle sync**: dual-bookmaker fetching (primary + Pinnacle bookmaker_id=4)
- **Dual CLV**: soft book (1xBet) + Pinnacle (sharp benchmark)
- **V3 features**: computed in build_predictions, accumulating (Market, Performance, Context)
- **Market timing**: bonus reduces EV threshold for high-disagreement, low-movement opportunities

### Рынки (Task 17 + 18)
- **1X2** — основной рынок, predictions таблица
- **TOTAL 2.5/1.5/3.5** — predictions_totals, correlation group "goals"
- **BTTS** — predictions_totals, correlation group "goals" (D028)
- **Double Chance** — predictions_totals, group "double_chance"
- Max 1 bet per fixture из "goals" group (best EV)
- Kelly fraction для всех рынков (в decision_payload)

## Production Metrics (report #001, 2026-03-01)

**Стэкинг (51 settled, report #002)**:
- RPS: 0.1943 (backtest: 0.196)
- ROI: +0.8% (n=51)
- Stacking vs DC-only: -6.2% RPS
- Calibration error: 0.1433 (> 0.05, Kelly NOT activated)
- CLV: 0.000 (Pinnacle tracking now enabled)

**Active leagues**: 39 (EPL), 78 (Bundesliga), 140 (La Liga), 135 (Serie A), 61 (Ligue 1)
**Disabled leagues**: 94 (Primeira Liga) — ROI -57.5%, predictions disabled, sync continues
**Active markets**: 1X2, TOTAL 2.5, Double Chance
**Disabled markets**: TOTAL 1.5, TOTAL 3.5, BTTS
**Odds range**: 1.30 — 2.50
**Fallback chain**: stacking → dc_only → poisson_fallback (NO logistic)
**Next checkpoint**: Pinnacle calibrator training (after 50+ Pinnacle odds accumulate)

## Прогресс по фазам

### Фаза 1: Dixon-Coles ядро + hotfixes
- [x] 1.0.1-1.0.3 Hotfixes (ключи, overround, очистка)
- [x] 1.1 Dixon-Coles ядро
- [x] 1.2 Улучшение Elo
- [x] 1.3 Метрика RPS + Brier + LogLoss

### Фаза 2: Стэкинг + Dirichlet калибровка
- [x] 2.1-2.4 Стэкинг + Dirichlet (полностью завершены)

### Фаза 3: Продвинутые фичи + backtest
- [x] 3.1-3.5 Ablation, Rest hours, xG, Kelly, DC activation

### Фаза 4: Инфраструктурная зрелость
- [x] 4.1-4.5 Dependencies, tests, Docker, CI, dedup

### Task 10-22
- [x] Task 10: Training data pipeline (7308 examples)
- [x] Task 11: Ablation stacking + Dirichlet evaluation
- [x] Task 12: DC-xG integration (quasi-Poisson, rho=0)
- [x] Task 13: Stacking v2 (13 features, DC-xG added)
- [x] Task 14: Production monitoring (production_monitor.py, quality_report RPS fix, checklist)
- [x] Task 15: Рефакторинг — единый pipeline (-201 строк, -6 flags)
- [x] Task 16: Diagnostics — VOID=SKIP (нормально), DC-xG params fixed
- [x] Task 17: Новые рынки — TOTAL 1.5/3.5, BTTS, Double Chance
- [x] Task 18: Backlog cleanup — 7 items resolved, rho grid expanded, dashboard bug fixed
- [x] Task 19: Production monitoring — report #001, SQL analysis, recommendations
- [x] Task 20: Post-monitoring config — markets reduced, odds range narrowed, docs updated
- [x] Task 21: Logistic removal + Primeira Liga disabled
- [x] Task 22: Roadmap validation — CMP-DC FAILED, standings PASSED, Pinnacle sync enabled

### Strategic Roadmap (validated Task 22)
- [x] Phase 1.1: Historical standings backfill (21,135 records)
- [x] Phase 1.2: Opening odds pipeline
- [x] Phase 1.3: Pinnacle dual-bookmaker sync
- [ ] Phase 1.4: xG context enrichment (blocked by data source)
- [x] Phase 2: CMP-DC implementation (code complete, **DISABLED** after ablation — D033)
- [x] Phase 3: Feature engineering 13→30 (code complete, accumulating data)
- [ ] Phase 4: Pinnacle calibration (code complete, awaiting data)
- [ ] Phase 5: Bet selection (Kelly + Pinnacle — awaiting calibration)

## Ключевые метрики
| Метрика | Значение |
|---------|----------|
| pytest (core) | 300 passed, 21 pre-existing failures |
| DC rho grid | [-0.35, 0.35] / 71 steps (D029) |
| CMP-DC ablation | FAILED: 0/6 leagues, ΔRPS +0.00042 (D033) |
| Stacking v2 val RPS | 0.1886 (13 features) |
| Stacking v3 | Deferred — 0 predictions with v3 features (D034) |
| Training data v2 | 7308 examples, 6183 with DC-xG |
| Historical standings | 21,135 records, 6 leagues, 2 seasons |
| Pinnacle odds | Accumulating (sync enabled) |

## Активные feature flags (production defaults)
- `DC_USE_XG=true` — dual-mode DC fit (goals + xG)
- `DC_USE_CMP=false` — **DISABLED** after ablation (D033)
- `USE_STACKING=true` — primary model (ΔRPS -7.0%)
- `ENABLE_REST_ADJUSTMENT=true` — fatigue factor
- `ENABLE_KELLY=false` — ожидает Pinnacle calibration
- `USE_DIRICHLET_CALIB=false` — marginal post-stacking (D021)
- `USE_PINNACLE_CALIB=false` — ожидает training (50+ Pinnacle odds)
- `SYNC_PINNACLE_ODDS=true` — dual-bookmaker fetching enabled
- `ENABLE_TOTAL_BETS=true` — Task 18 fix
- `ENABLE_TOTAL_1_5_BETS=false` — Task 20 (disabled after production report #001)
- `ENABLE_TOTAL_3_5_BETS=false` — Task 20 (disabled after production report #001)
- `ENABLE_BTTS_BETS=false` — Task 20 (disabled after production report #001)
- `ENABLE_DOUBLE_CHANCE_BETS=true` — Task 17

## Открытые задачи (BACKLOG)
- [ ] Train Pinnacle calibrator (после 50+ Pinnacle odds накопятся)
- [ ] Retrain stacking v3 (после 100+ predictions с v3 features)
- [ ] Activate Kelly criterion (после Pinnacle calibration)
- [ ] Public site match modal: показать odds для O/U 1.5/3.5, BTTS, DC
- [ ] Centralized markets.py (техдолг — 40+ hardcoded мест)
