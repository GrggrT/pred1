# Football Predictions Engine — Project Notes

## Архитектура и поток данных
- Источники: API-Football (fixtures/odds/xG + injuries/standings), Postgres.
- Пайплайн (idempotent, цепочка):
  - `sync_data`: upsert fixtures в окне, обновляет odds (и при live режиме пишет `odds_snapshots`), тянет xG/статы для завершённых, поддерживает injuries + league baselines, backfill.
  - `compute_indices`: индексы/фичи для `NS` (история `kickoff < target`, rest_hours, L5/L15 overall, venue).
  - `build_predictions`: вероятности (Poisson/Logistic/Dixon-Coles/Hybrid), EV-фильтр, выбор ставки/`SKIP`, сохраняет `feature_flags` + `prediction_decisions`.
  - `evaluate_results`: settlement, profit/ROI, Elo update, Brier/LogLoss по источнику вероятностей.
- UI: `GET /ui` — “минимально рабочая админка” (Dashboard + Jobs + DB browse). UI статика: `/ui/ui.css`, `/ui/ui.js` (no-store).

## Backtest: pseudo vs true
- `BACKTEST_KIND=pseudo`: использует “текущие” odds (может искажать метрики).
- `BACKTEST_KIND=true`: использует `odds_snapshots` с `fetched_at < kickoff` (корректнее, но требует покрытия снапшотами).
- Готовность true-backtest:
  - `GET /api/v1/snapshots/gaps` — какие будущие `NS` ещё без pre-kickoff snapshot.
  - `GET /api/v1/coverage` — метрики покрытия.

## API (FastAPI)
- Public:
  - `GET /health`
- Admin (`X-Admin-Token`):
  - `GET /api/v1/meta` — build/info (в т.ч. sha256/mtime UI файлов).
  - `GET /health/debug` — uptime, counts, env snapshot (включая snapshot_autofill).
  - `GET /api/v1/freshness` — “актуальность” данных (последний `sync_data` + max timestamps по ключевым таблицам + config summary).
  - `POST /api/v1/run-now?job=...` — ручной запуск джобов (`job=full|sync_data|...`).
  - `GET /api/v1/dashboard?days=...` — KPI для dashboard.
  - `GET /api/v1/picks` / `GET /api/v1/picks/totals` — pending picks (1X2 + totals).
  - `GET /api/v1/bets/history` — история ставок (параметры: `all_time`, `settled_only`, paging через `X-Total-Count`).
  - `GET /api/v1/db/browse?table=...` — DB browser.
  - `GET /api/v1/snapshots/gaps` — мониторинг true-backtest gaps.
  - `GET /api/v1/fixtures/{id}/details` — drill-down карточка матча (включая odds_pre_kickoff + decisions).
  - `GET /api/v1/jobs/status`, `GET /api/v1/jobs/runs` — статус и история запусков.
- Пагинация: часть эндпоинтов отдаёт `X-Total-Count` (history/picks/totals/gaps).

## Схема БД (ключевые таблицы)
- `fixtures`, `teams`, `leagues`
- `odds` — текущие odds по `bookmaker_id`
- `odds_snapshots` — исторические снапшоты odds (true-backtest)
- `match_indices` — вычисленные фичи на матч
- `team_standings` — standings по командам (для `ENABLE_STANDINGS`)
- `predictions`, `predictions_totals` — решения/ставки и totals рынок
- `prediction_decisions` — “почему так решили” + кандидаты/причины
- `job_runs` — история запусков (status + meta JSONB)
- `injuries`, `league_baselines`, `api_cache` — служебные и кэши

## Конфигурация
- Схема БД: Alembic (`alembic upgrade head`)
- `ADMIN_TOKEN` обязателен (админ-эндпоинты и UI требуют токен)
- Snapshot autofill: `SNAPSHOT_AUTOFILL_*` (автотриггер `sync_data` по gaps)
- Scheduler: `SCHEDULER_ENABLED` (в проде включать scheduler только в одном экземпляре)
- Метрики: `WRITE_METRICS_FILE` + `METRICS_OUTPUT_PATH` (по умолчанию запись выключена)
- Ретенция: `JOB_RUNS_RETENTION_DAYS`, `ODDS_SNAPSHOTS_RETENTION_DAYS`, `API_CACHE_MAX_ROWS` (через джоб `maintenance`)

## Notes по UI
- Токен: UI и admin API требуют `ADMIN_TOKEN` / заголовок `X-Admin-Token`; UI сохраняет токен в localStorage.
- Live Picks в UI объединяет 1X2 + totals и группирует по матчу (по `fixture_id`).
- В Dashboard отображается “Обновление: ...” (актуальность данных) и есть кнопка ручного апдейта пайплайна.
- История ставок в UI: последние 10 в карточке + быстрые кнопки “Все ставки/Всё время”; раскрываемая панель (фильтры, пагинация), “Load all (cap)” и “Export CSV”.
- Match details (клик по карточке/строке): причины выбора (decision payload + ключевые фичи) и post‑match сравнение (Brier/LogLoss) для завершённых матчей.
- Model Status в UI показывает API‑Football quota-guard + breakdown по endpoint/league (для последнего `sync_data` с `cache_misses > 0`).
- Docker image не bind-mount’ит исходники: после изменений UI/кода нужен `docker compose up --build -d app scheduler`.

## Ручной прогон цепочки (без cron)
```bash
python - <<'PY'
import asyncio
from app.core.db import SessionLocal, init_db
from app.jobs import sync_data, compute_indices, build_predictions, evaluate_results
async def main():
    await init_db()
    async with SessionLocal() as s:
        await sync_data.run(s)
        await compute_indices.run(s)
        await build_predictions.run(s)
        await evaluate_results.run(s)
asyncio.run(main())
PY
```
