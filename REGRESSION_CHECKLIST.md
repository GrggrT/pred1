# Regression Checklist

## Быстрый (локально, venv)
- `python -m compileall -q app`
- `pytest -q`

## Docker (если прод‑режим через compose)
- `docker compose up --build -d`
- `docker compose exec -T app alembic upgrade head`
- `docker compose exec -T app python -m compileall -q app`
- `docker compose ps` (видны `app` и `scheduler`)

## Dry-run пайплайна (без cron)
- `docker compose exec -T app python -c "import asyncio; from app.core.db import SessionLocal, init_db; from app.jobs import sync_data, compute_indices, build_predictions, evaluate_results; async def main():\n    await init_db()\n    async with SessionLocal() as s:\n      await sync_data.run(s)\n      await compute_indices.run(s)\n      await build_predictions.run(s)\n      await evaluate_results.run(s)\n  asyncio.run(main())"`

## Smoke API/UI
- `curl http://localhost:8000/health`
- `curl -H 'X-Admin-Token: <token>' http://localhost:8000/health/debug`
- `curl -H 'X-Admin-Token: <token>' http://localhost:8000/api/v1/meta`
- Открыть `http://localhost:8000/ui` и проверить:
  - Dashboard: KPI обновляются, Live Picks грузятся (1X2+TOTAL), Recent Bets показываются
  - History: “Открыть историю” → таблица с пагинацией; “Load all (max 5000)” и “Export CSV” работают
  - System: Jobs/Job Runs обновляются; DB browser (Browse + prev/next + Copy JSON)
  - UI статика отдаётся: `GET /ui/ui.css`, `GET /ui/ui.js` (Cache-Control: no-store)
