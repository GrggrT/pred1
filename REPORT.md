# Report / Audit Summary (current state)

## Краткий вывод
- Архитектура пайплайна устойчивая (джобы + Postgres + FastAPI UI), но для корректного backtest важно иметь покрытие историческими снапшотами odds.
- Схема и эволюция БД приведены к одному источнику истины (Alembic); добавлены таблицы наблюдаемости и true-backtest данных.
- Админ-эндпоинты закрыты токеном; UI поддерживает ввод и сохранение `ADMIN_TOKEN`.

## Что добавлено / улучшено
- **Безопасность**
  - `ADMIN_TOKEN` обязателен; админ-эндпоинты требуют `X-Admin-Token`.
  - `/health` остаётся публичным, `/health/debug` закрыт.
- **Миграции и схема**
  - Источник истины по схеме — Alembic (`alembic/versions/`).
  - Добавлены ключевые таблицы:
    - `job_runs` — история запусков джобов (status + meta JSONB).
    - `odds_snapshots` — снапшоты odds для true-backtest (fetched_at < kickoff).
    - `prediction_decisions` — объяснения решений/кандидаты (drill-down доступен через API).
- **True-backtest готовность**
  - Мониторинг “gaps” (будущие `NS`, у которых нет pre-kickoff snapshot) через `GET /api/v1/snapshots/gaps`.
  - Метрика coverage дополнилась индикаторами “сколько матчей реально пригодны для true-backtest”.
- **Автоматизация**
  - `snapshot_autofill` (APScheduler) автоматически триггерит `sync_data` при обнаружении “gaps” в заданном окне:
    - cooldown (минимальный интервал),
    - urgent-режим,
    - trigger-before (базовый порог),
    - динамическое ускорение (уменьшает trigger-before, если gaps много).
- **UI и аналитика**
  - UI приведён к “минимально рабочей админке” (Dashboard + Jobs + DB browse).
  - Dashboard: KPI, Live Picks (1X2+TOTAL в одном списке, сгруппировано по матчу), Recent Bets + раскрываемая панель истории ставок.
  - История ставок: `all_time` и `settled_only` для согласования с KPI; есть “Load all (cap)” и “Export CSV”.
  - System: управление jobs (run/status/recent runs) и DB browser (paging, table filter, copy JSON).
  - Добавлен `/api/v1/meta` и отображение build/info в хедере UI (удобно проверять, что контейнер пересобран).

## Как запустить и проверить
1. `cp .env.example .env` и заполнить минимум: `DATABASE_URL`, `ADMIN_TOKEN`, `API_FOOTBALL_KEY`, `SEASON`, `LEAGUE_IDS`, `BOOKMAKER_ID`.
2. Docker: `docker compose up --build -d`
3. Применить миграции: `docker compose exec -T app alembic upgrade head`
4. Проверка:
   - `curl http://localhost:8000/health`
   - `curl -H 'X-Admin-Token: <token>' http://localhost:8000/health/debug`
   - UI: `http://localhost:8000/ui` (ввести admin token)

## Статус проверок
- `.venv/bin/python -m compileall -q app` проходит.
- `.venv/bin/pytest -q` проходит (есть `skipped`; критические true-backtest кейсы лучше покрыть отдельно).

## Stage 0 (baseline) — критерии и регресс
- Критерии готовности по сценариям: `DEFINITION_OF_DONE.md`
- Чек-лист регресса: `REGRESSION_CHECKLIST.md`

## Stage 1 (P0) — критические фиксы
- **Scheduler без гонок**
  - Scheduler вынесен в отдельный сервис (`docker-compose.yml`), в веб-процессе по умолчанию выключен.
  - Реальные джобы теперь `await`-ятся в scheduler-пути (а не запускаются “в фоне”), добавлена защита от конкурентного запуска через advisory lock (Postgres).
- **Математика**
  - Elo-коррекция ограничена и не может сделать `λ` отрицательным/нулевым; добавлены тесты на экстремальные `elo_diff`.
- **Alembic/Settings**
  - Миграции не требуют API-ключей: Alembic использует только `DATABASE_URL`.
  - Обязательность `ADMIN_TOKEN`/`API_FOOTBALL_KEY` перенесена в runtime-валидацию (и в конкретные джобы).
- **Чистота workspace**
  - `evaluate_results` не пишет файлы в репозиторий по умолчанию; запись JSON метрик включается флагом.

## Stage 2 (P1) — надёжность данных/рост
- Добавлен джоб `maintenance` (api_cache/job_runs/odds_snapshots cleanup) и настройки ретенции в `.env.example`.
- Standings стали “живой” фичей: `sync_data` заполняет `team_standings`, `build_predictions` учитывает это в `signal_score`.
- xG загрузка получила ретраи/backoff: `stats_downloaded` ставится только при наличии xG, добавлены попытки/таймстампы.

## Stage 3 (P2) — чистка и унификация
- Легаси-джобы и экспериментальные доменные модули вынесены в `scripts/deprecated/` (чтобы прод-пайплайн был “однозначным”).
- Переименованы/унифицированы cron-настройки для `sync_data` (с совместимостью со старым env).
- Зафиксирована версия Python 3.12 (Docker + локально).

## Stage 4 (P3) — наблюдаемость и UX
- **Логи**: поддержка `LOG_LEVEL` и отдельный `SQL_LOG_LEVEL`.
- **Job runs**: статус/длительность и meta JSONB (API-метрики внешнего провайдера, длительности стадий пайплайна).
- **UI**:
  - Sidebar + 2 секции: Dashboard и System; авто‑refresh только активного раздела.
  - Live Picks: объединение 1X2 + totals, группировка по матчу.
  - История ставок: пагинация + “Load all (cap)” + CSV export.
  - DB browser: table filter, paging, copy JSON.
  - UI вынесен в статические файлы: `/ui/ui.css`, `/ui/ui.js` (Cache-Control: no-store).

## Заметки по UI “История ставок”
- UI по умолчанию показывает последние N дней (селект “Период” на dashboard).
- Чтобы видеть всю историю: включить “All time” в панели истории ставок.
