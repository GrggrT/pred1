# Diagnostic Report — Task 16

**Дата**: 2026-02-22

## VOID Analysis

### Статистика (30 дней)
| Status | Count |
|--------|-------|
| VOID | 122 |
| PENDING | 11 |
| WIN | 2 |
| LOSS | 2 |

### Причина VOID
**Все 122 VOID = SKIP** (selection_code='SKIP'). Модель не нашла value bet.

Это **не баг**, а нормальное поведение value betting системы.

### VOID breakdown по prob_source
| Source | SKIP | PENDING | WIN | LOSS |
|--------|------|---------|-----|------|
| stacking | 46 | 11 | 2 | 0 |
| logistic | 76 | 0 | 0 | 2 |

### Причины SKIP для stacking predictions (46)
| Причина | Count | Описание |
|---------|-------|----------|
| odds_out_of_range | 18 | odd < 1.50 (фавориты 1.12-1.45) или odd > 3.20 (аутсайдеры 3.25-11.0) |
| below_threshold | 12 | EV > 0, но < effective_threshold (0.07-0.12) |
| negative_ev | 11 | EV < 0 |
| no_odds | 5 | Odds не получены для матча |

### Конфигурация
- `VALUE_THRESHOLD=0.05` (base)
- `LEAGUE_EV_THRESHOLD_OVERRIDES`: EPL(39)=0.12, Ligue1(61)=0.12
- `MIN_ODD=1.50, MAX_ODD=3.20`
- `BOOKMAKER_ID=8`
- Effective threshold варьируется 0.07-0.12 (зависит от signal_score)

### PENDING predictions
Все 11 PENDING — матчи ещё не состоялись (NS):
- 2 матча 22.02 (17:30, 20:00 UTC) — settle сегодня
- 9 матчей 23.02-01.03 — settle на следующей неделе

### Settled predictions
| Fixture | Selection | Status | Profit | Odd | Score | Source |
|---------|-----------|--------|--------|-----|-------|--------|
| 1396440 | HOME_WIN | LOSS | -1.000 | 2.90 | 0-2 | logistic |
| 1379238 | AWAY_WIN | LOSS | -1.000 | 2.55 | 0-0 | logistic |
| 1379232 | HOME_WIN | WIN | +0.750 | 1.75 | 1-0 | stacking |
| 1388508 | HOME_WIN | WIN | +0.950 | 1.95 | 2-1 | stacking |

Stacking: 2W/0L (+1.70 units). Logistic (legacy): 0W/2L (-2.00 units).

### Рекомендации по VOID rate
1. **SKIP rate 80% — нормален** для value betting. Не требует fix.
2. **18 predictions отклонены из-за odds range** — 8 с EV > 0. Рассмотреть расширение MIN_ODD=1.30 для сильных фаворитов.
3. **EPL/Ligue1 threshold 0.12 — агрессивный**. При текущем EV distribution отсекает ~12 дополнительных predictions. Мониторить после накопления 50+ settled.
4. **Odds coverage ~10%** — BOOKMAKER_ID=8 имеет 96 fixtures с odds из 942. Это нормально для API Football — odds приходят постепенно для ближайших матчей.

## DC-xG Status

### До fix
- Migration 0033: **applied** (alembic version = 0033_dc_param_source)
- param_source column: **exists** (text type)
- DC params: goals=114 teams, 6 global. **xG=0** (не запускался)
- DC_USE_XG in env: **True**
- xG data coverage: **100%** для всех 6 лиг (1054 matches with xG)

### Причина
`fit_dixon_coles` job не выполнялся с момента включения DC_USE_XG=true. Job расписание `5 6 * * *` — 06:05 UTC ежедневно. Между включением флага и cron trigger прошло недостаточно времени.

### Fix
Запущен `fit_dixon_coles` вручную через `/api/v1/run-now`.

### После fix
| param_source | team_strength_params | dc_global_params |
|---|---|---|
| goals | 228 | 12 |
| xg | 114 | 6 |

DC-xG params сгенерированы для всех 6 лиг. `build_predictions` теперь использует отдельные DC-xG probs.

Пример из логов (fixture 1391064):
- `p_home_dc: 0.5881` vs `p_home_dc_xg: 0.5887`
- `p_draw_dc: 0.2841` vs `p_draw_dc_xg: 0.2360` (разница 4.8pp!)
- `p_away_dc: 0.1279` vs `p_away_dc_xg: 0.1753` (разница 4.7pp!)
- `dc_xg_available: True`

## Actions Taken
1. SQL диагностика VOID predictions — причина: SKIP (no value)
2. `fit_dixon_coles` запущен вручную — xG params появились
3. `build_predictions` подтверждён — DC-xG probs теперь разные от DC-goals
4. Код не менялся — только данные в БД
