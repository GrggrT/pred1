# Definition of Done (DoD)

Этот документ фиксирует минимальные критерии “готово” для трёх сценариев: `live`, `backtest pseudo`, `backtest true`.

## Общие критерии (всегда)
- БД в актуальном состоянии: `alembic upgrade head` выполнен, приложение стартует без ошибок.
- Админ‑доступ работает: `ADMIN_TOKEN` задан, UI/админ‑эндпоинты принимают `X-Admin-Token`.
- Пайплайн не запускается конкурентно (нет гонок между scheduler/manual/multi-worker).
- Тесты/синтаксис зелёные: `python -m compileall -q app`, `pytest -q`.
- В Docker prod‑раскладке API и scheduler разделены: `app` (порт 8000) + `scheduler` (без ports).

## Scenario A — Live

### Конфиг
- `APP_MODE=live`
- `BACKTEST_MODE=false`
- `BACKTEST_KIND=pseudo` (не используется)

### Ожидаемые данные/таблицы
- `fixtures` содержит окно `now-2d..now+7d` по `LEAGUE_IDS/SEASON`
- `odds` обновляются для `NS` и `BOOKMAKER_ID`
- `odds_snapshots` пополняется (live‑снапшоты для true‑backtest, `fetched_at < kickoff`)
- `job_runs` содержит записи запусков (`sync_data/compute_indices/build_predictions/evaluate_results/full`)

### Ожидаемые эндпоинты/метрики
- `GET /health` → `{ok:true}`
- `GET /health/debug` (admin) → есть `env.scheduler_enabled`, счётчики таблиц, uptime (если scheduler включён)
- `GET /api/v1/meta` (admin) → build/info (sha256/mtime UI файлов)
- `GET /api/v1/coverage` (admin) → non-zero coverage по `NS` (odds/indices/predictions) и FT xG coverage (если включено)
- UI (`/ui`) показывает:
  - Dashboard: KPI, Live Picks (1X2+TOTAL), Recent Bets + раскрываемая история ставок
  - System: Jobs/Job Runs, DB browser
- True-backtest мониторинг доступен по API:
  - `GET /api/v1/snapshots/gaps` (admin)
  - `GET /api/v1/coverage` (admin)

## Scenario B — Backtest (pseudo)

### Конфиг
- `BACKTEST_MODE=true`
- `BACKTEST_KIND=pseudo`
- `BACKTEST_CURRENT_DATE=YYYY-MM-DD` (устанавливается на день итерации)

### Ожидания
- Результаты воспроизводимы при одинаковом диапазоне дат и одинаковой БД (в пределах детерминизма внешних данных).
- `odds_snapshots` НЕ должен пополняться (иначе появляются “фейковые” исторические снапшоты).
- Метрики (ROI/Brier/LogLoss) считаются, но могут быть оптимистичны из-за использования “текущих” odds.

## Scenario C — Backtest (true)

### Конфиг
- `BACKTEST_MODE=true`
- `BACKTEST_KIND=true`
- `BACKTEST_CURRENT_DATE=YYYY-MM-DD`

### Ожидания
- `build_predictions` использует pre‑kickoff снапшоты из `odds_snapshots` (где `fetched_at < kickoff`).
- В `GET /api/v1/fixtures/{id}/details` (admin) поле `odds_pre_kickoff` для соответствующих матчей заполнено.
- Готовность данных видна:
  - `GET /api/v1/snapshots/gaps` (admin) — список будущих `NS` без pre‑kickoff snapshot
  - `GET /api/v1/coverage` (admin) — индикаторы покрытия true‑backtest
