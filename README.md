## Football Value Betting Engine (MVP)

FastAPI + Postgres pipeline for fixtures/odds ingestion, indices, predictions, settlement and monitoring UI.

### Quick start (Docker)
1. `cp .env.example .env`
2. Заполнить минимум в `.env`:
   - `DATABASE_URL` (для docker-compose уже подходит значение по умолчанию)
   - `ADMIN_TOKEN` (обязателен)
   - `API_FOOTBALL_KEY`, `SEASON`, `LEAGUE_IDS`, `BOOKMAKER_ID`
   - `LOG_LEVEL` (опционально), `SQL_LOG_LEVEL` (опционально)
   - Примеры `LEAGUE_IDS` (API‑Football): `39` EPL, `78` Bundesliga, `140` La Liga, `135` Serie A, `61` Ligue 1, `94` Primeira Liga, `253` MLS, `71` Brazil Serie A
3. Поднять сервисы: `docker compose up --build -d`
4. Применить миграции: `docker compose exec -T app alembic upgrade head`
5. Открыть UI: `http://localhost:8000/ui` и ввести `ADMIN_TOKEN` (сохранится в localStorage)

### Bootstrap from scratch (чистая БД)
Скрипт полностью пересоздаёт базу и прогоняет все миграции. **Осторожно:** база с тем же именем будет удалена.
```bash
./scripts/bootstrap_from_scratch.sh fc_mvp_audit
```
После выполнения `\dt` должен показывать `teams`, `fixtures`, `predictions`, `match_indices`, `api_cache`.

### Безопасность .env
- Не коммитьте `.env`; используйте `.env.example` как шаблон.
- Если `.env` когда-либо попадал в репозиторий/общий доступ — ротируйте ключи.

### Публикации (Telegram + DeepL)
Для ручных публикаций в Telegram:
- `TELEGRAM_BOT_TOKEN` — токен бота
- `TELEGRAM_CHANNEL_*` — ID каналов по языкам (EN/UK/RU/FR/DE/PL/PT/ES)
- `DEEPL_API_KEY` — ключ DeepL для переводов
- `PUBLISH_MODE` — `manual` (пока вручную), в `auto` режим можно переключать позже

UI: в карточке матча есть превью Telegram (RU) и кнопки Send/Force.

UI сейчас — “минимально рабочая админка”:
- Dashboard: KPI (profit/ROI/bets/period), Live Picks (1X2+TOTAL, сгруппировано по матчу + дата последнего обновления + ручной апдейт), Recent Bets (последние 10) + история ставок (пагинация, “всё время”, CSV export).
- Quality Report: ROI/CLV/калибровка по лигам, диапазонам odds и времени до матча (обновляется по cron `JOB_QUALITY_REPORT_CRON`, кэш `QUALITY_REPORT_CACHE_TTL_SECONDS`, есть ручной refresh).
- Quality Report signals: авто‑индикаторы риска (малый объём, низкий CLV coverage, отрицательный CLV, плохая калибровка при достаточной выборке).
- System: управление jobs (run/status/recent runs), Model Status (Elo + параметры лиг + API‑Football quota + кнопка rebuild), и DB browser (по таблицам, paging, copy JSON).

Drill‑down матча (почему ставка выбрана + post‑match сравнение прогноз/факт): клик по карточке Live Picks или по строке в истории ставок → открывается `Match details`.

Важно: docker image не bind-mount’ит исходники. После изменений в коде/UI нужно пересобрать контейнер: `docker compose up --build -d app scheduler`.
UI-статика отдаётся с `Cache-Control: no-store`, поэтому после rebuild достаточно просто обновить страницу в браузере.

### Local dev (venv)
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export ADMIN_TOKEN=dev
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

### Основные эндпоинты
- Public:
  - `GET /health`
- Admin (`X-Admin-Token: <ADMIN_TOKEN>`):
  - `GET /api/v1/meta` — build/info (в т.ч. sha256 UI файлов).
  - `GET /health/debug`
  - `GET /api/v1/freshness` — “актуальность” данных (последний успешный `sync_data` + max timestamps по ключевым таблицам + config summary).
  - `GET /api/v1/model/status` — статус модели (Elo processed/unprocessed + per-league draw/rho/alpha + sample counts).
  - `POST /api/v1/run-now?job=full|sync_data|compute_indices|build_predictions|evaluate_results|maintenance|rebuild_elo`
  - `GET /api/v1/dashboard?days=...`
  - `GET /api/v1/quality_report` (`?refresh=1` для пересчёта)
  - `GET /api/v1/publish/preview?fixture_id=...` — Telegram preview (RU)
  - `POST /api/v1/publish` — ручная публикация (fixture_id)
  - `GET /api/v1/publish/history?fixture_id=...` — история публикаций
  - `GET /api/v1/picks` / `GET /api/v1/picks/totals`
  - `GET /api/v1/bets/history` (пагинация через `X-Total-Count`)
  - `GET /api/v1/db/browse?table=...`
  - `GET /api/v1/snapshots/gaps` (true-backtest coverage)
  - `GET /api/v1/standings` (team standings table)

`/api/v1/run-now` имеет простой rate-limit (см. `RUN_NOW_*` в `.env.example`) и поддерживает аудит через заголовок `X-Admin-Actor`.
`/api/v1/stats` по умолчанию ограничивает выборку для brier/logloss; используйте `metrics_unbounded=true` только для контролируемых прогонов.

### True-backtest (odds snapshots)
В режиме `BACKTEST_KIND=true` `build_predictions` использует `odds_snapshots` (снапшоты до kickoff), а не “текущие” odds.
Покрытие снапшотами видно в UI:
- карточка **Coverage**
- таблица **Snapshot Gaps (true-backtest)** (какие `NS` матчи ещё без pre-kickoff snapshot)

Автозаполнение (опционально): см. переменные `SNAPSHOT_AUTOFILL_*` в `.env.example`.

### API‑Football quota / caching
UI читает данные из Postgres. Внешние запросы к API‑Football делает только джоб `sync_data` (scheduler).
Сырые ответы кешируются в Postgres (`api_cache`) по ключу запроса + TTL (см. `API_FOOTBALL_*_TTL_SECONDS` в `.env.example`).

Важно: API‑Football иногда возвращает HTTP 200 с полем `errors` (например, при quota limit). Такие ответы:
- не сохраняются в `api_cache` (и чистятся джобом `maintenance`, если уже попали туда)
- увеличивают `errors` в `api_football` метриках
- при quota limit включается quota-guard: `sync_data` помечается как `skipped` и дальше пропускается до reset (UTC midnight), чтобы не спамить фейлами

Как понять, сколько реально было HTTP вызовов (cache miss):
```bash
docker compose exec -T db psql -U postgres -d fc_mvp -c \
  "select started_at, meta->'result'->'api_football' from job_runs where job_name='sync_data' order by started_at desc limit 5;"
```
`cache_misses` ≈ внешние HTTP запросы; `requests = cache_hits + cache_misses`.

Если вы упираетесь в лимит ~7500/day и хотите добавить больше лиг — используйте “LOW‑QUOTA profile” из `.env.example`:
- Вариант A: уменьшает quota без отключения входных данных модели (через cron + TTL).
- Вариант B: агрессивнее экономит quota, но может отключать тяжёлые источники (`ENABLE_XG=false`, `ENABLE_INJURIES=false`) и уменьшать покрытие odds.

Quota-guard настраивается переменными:
- `API_FOOTBALL_DAILY_LIMIT` (по умолчанию 7500)
- `API_FOOTBALL_GUARD_ENABLED=true|false`
- `API_FOOTBALL_GUARD_MARGIN` (headroom по `cache_misses` перед блокировкой)
- `API_FOOTBALL_RUN_BUDGET_CACHE_MISSES` (опционально: cap `cache_misses` на один запуск `sync_data`, чтобы не “съесть” квоту одним прогоном)

UI → System → Model Status показывает:
- `blocked/reset_at` (quota-guard)
- usage за сегодня/24h
- breakdown по endpoint/league для последнего `sync_data` с реальными внешними вызовами (`cache_misses > 0`)

Проверка “насколько свежие данные”:
- в UI (Dashboard → Live Picks) есть строка “Обновление: ...”
- API: `GET /api/v1/freshness`

### Maintenance / retention
В пайплайне есть джоб `maintenance` (cron `JOB_MAINTENANCE_CRON`), который:
- удаляет просроченный `api_cache` (и опционально ограничивает размер `API_CACHE_MAX_ROWS`)
- чистит `job_runs` старше `JOB_RUNS_RETENTION_DAYS` (по `finished_at`)
- опционально чистит `odds_snapshots` старше `ODDS_SNAPSHOTS_RETENTION_DAYS` (0 = хранить бессрочно)

### Prod run mode (scheduler)
В проде важно исключить гонки и дубли scheduler:
- В `docker-compose.yml` scheduler вынесен в отдельный сервис `scheduler` (без ports); API сервис `app` стартует с `SCHEDULER_ENABLED=false`.
- Если запускаете **несколько воркеров/реплик API** вне compose — включайте scheduler только в одном экземпляре (`SCHEDULER_ENABLED=true`), остальные `false`.

### Ручной прогон пайплайна (без cron)
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

### Проверки
- `python -m compileall -q app`
- `pytest -q`
- `./scripts/secret_scan.sh` (поиск потенциальных секретов, без `.env`)

### Критерии готовности
- `DEFINITION_OF_DONE.md`
- `REGRESSION_CHECKLIST.md`

### Метрики (файл)
По умолчанию `evaluate_results` не пишет файлы в workspace. Если нужно сохранить JSON метрик:
- `WRITE_METRICS_FILE=true`
- `METRICS_OUTPUT_PATH=/tmp/metrics_eval.json`
