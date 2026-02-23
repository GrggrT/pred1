# Текущее состояние проекта

**Последнее обновление**: 2026-02-23
**Текущая фаза**: Все фазы 1-4 завершены, Tasks 10-18 завершены
**Текущая задача**: Мониторинг production, ожидание 50+ settled stacking predictions
**Блокеры**: Нет (ожидание данных)

## Архитектура prediction pipeline (после Task 15)

### Единый production pipeline
```
DC fit (goals + xG) → Poisson baseline → Stacking meta-model → optional Dirichlet → EV calc
```

### Три уровня prediction
1. **Primary**: DC + Stacking (13 features: 3 Poisson + 3 DC-goals + 3 DC-xG + elo_diff + 3 fair odds)
2. **Fallback 1**: DC-only (нет stacking model в DB)
3. **Fallback 2**: Poisson baseline (нет DC params для команды/лиги)

### Рынки (Task 17 + 18)
- **1X2** — основной рынок, predictions таблица
- **TOTAL 2.5/1.5/3.5** — predictions_totals, correlation group "goals"
- **BTTS** — predictions_totals, correlation group "goals" (D028)
- **Double Chance** — predictions_totals, group "double_chance"
- Max 1 bet per fixture из "goals" group (best EV)
- Kelly fraction для всех рынков (в decision_payload)

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

### Task 10-18
- [x] Task 10: Training data pipeline (7308 examples)
- [x] Task 11: Ablation stacking + Dirichlet evaluation
- [x] Task 12: DC-xG integration (quasi-Poisson, rho=0)
- [x] Task 13: Stacking v2 (13 features, DC-xG added)
- [x] Task 14: Production monitoring (production_monitor.py, quality_report RPS fix, checklist)
- [x] Task 15: Рефакторинг — единый pipeline (-201 строк, -6 flags)
- [x] Task 16: Diagnostics — VOID=SKIP (нормально), DC-xG params fixed
- [x] Task 17: Новые рынки — TOTAL 1.5/3.5, BTTS, Double Chance
- [x] Task 18: Backlog cleanup — 7 items resolved, rho grid expanded, dashboard bug fixed

## Ключевые метрики
| Метрика | Значение |
|---------|----------|
| pytest (core) | 261 passed, 5 skipped |
| DC rho grid | [-0.35, 0.35] / 71 steps (D029) |
| Correlation group | "goals" = TOTAL + BTTS (D028) |
| Ablation ΔRPS (Stacking vs Baseline) | -7.0% |
| Stacking v2 val RPS | 0.1887 (13 features) |
| Training data v2 | 7308 examples, 6183 with DC-xG |

## Активные feature flags (production defaults)
- `DC_USE_XG=true` — dual-mode DC fit (goals + xG)
- `USE_STACKING=true` — primary model (ΔRPS -7.0%)
- `ENABLE_REST_ADJUSTMENT=true` — fatigue factor
- `ENABLE_KELLY=false` — ожидает 200+ settled
- `USE_DIRICHLET_CALIB=false` — marginal post-stacking (D021)
- `ENABLE_TOTAL_BETS=true` — Task 18 fix
- `ENABLE_TOTAL_1_5_BETS=true` — Task 17
- `ENABLE_TOTAL_3_5_BETS=true` — Task 17
- `ENABLE_BTTS_BETS=true` — Task 17
- `ENABLE_DOUBLE_CHANCE_BETS=true` — Task 17

## Открытые задачи (BACKLOG)
- [ ] Public site match modal: показать odds для O/U 1.5/3.5, BTTS, DC
- [ ] Centralized markets.py (техдолг — 40+ hardcoded мест)
- [ ] Запустить fit_dixon_coles для проверки новых rho значений
- [ ] Historical standings backfill для улучшения stacking training
